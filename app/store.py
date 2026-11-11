"""观测与审查决定的追加式存储。

观测（observation）一旦写入不修改、不删除；审查决定（quarantine/restore）
同样是追加记录。当前视图由目录模块在读取时重新计算，因此隔离某份观测
不会抹掉它，只会让它不再参与当前状态。存储用 JSONL 文件，进程重启后
重新加载；观测标识由内容决定，重复采集同一批次不会产生重复观测。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from app.registry import load_batch, normalize_batch, observation_id


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        """directory 为 None 时是纯内存存储（用于演示与测试）。"""
        self.directory = Path(directory) if directory is not None else None
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.observations_path = self.directory / "observations.jsonl"
            self.decisions_path = self.directory / "decisions.jsonl"
        else:
            self.observations_path = None
            self.decisions_path = None
        self._observations = {}
        self._decisions = []
        self._load()

    def _load(self):
        if self.observations_path is None:
            return
        if self.observations_path.exists():
            for line in self.observations_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    obs = json.loads(line)
                    self._observations[obs["id"]] = obs
        if self.decisions_path.exists():
            for line in self.decisions_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._decisions.append(json.loads(line))

    # ------------------------------------------------------------------ 观测

    def ingest_batch(self, batch=None, path=None):
        """采集一批观测。返回 (新增观测数, 本批次观测标识列表)。"""
        if batch is None:
            batch = load_batch(path)
        elif isinstance(batch, dict) and "records" not in batch:
            batch = normalize_batch(batch)
        ingested_at = _utcnow()
        ids, added = [], 0
        new_lines = []
        for seq, record in enumerate(batch["records"]):
            oid = observation_id(
                batch["registry"], batch["source"], batch["batch_id"],
                batch["observed_at"], record,
            )
            ids.append(oid)
            if oid in self._observations:
                continue  # 重复获取/重启重放：同一观测只保留一份
            obs = {
                "id": oid,
                "registry": batch["registry"],
                "source": batch["source"],
                "batch_id": batch["batch_id"],
                "observed_at": batch["observed_at"],
                "complete": batch["complete"],
                "package": batch["package"],
                "batch_revision": batch["revision"],
                "seq": seq,
                "record": record,
                "ingested_at": ingested_at,
            }
            self._observations[oid] = obs
            new_lines.append(json.dumps(obs, ensure_ascii=False, sort_keys=True))
            added += 1
        if new_lines and self.observations_path is not None:
            with self.observations_path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(new_lines) + "\n")
        return added, ids

    def observations(self, registry=None):
        """按 (观测时间, 批次, 批内序号) 排序返回观测。"""
        items = list(self._observations.values())
        if registry is not None:
            items = [o for o in items if o["registry"] == registry]
        items.sort(key=lambda o: (o["observed_at"], o.get("batch_id") or "", o["seq"]))
        return items

    def get_observation(self, observation_id):
        return self._observations.get(observation_id)

    def resolve_observation_id(self, prefix):
        """支持用标识前缀定位观测（类似 git 短哈希）。"""
        if prefix in self._observations:
            return prefix
        matches = [oid for oid in self._observations if oid.startswith(prefix)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"没有匹配的观测: {prefix}")
        raise ValueError(f"前缀不唯一，匹配到 {len(matches)} 份观测: {prefix}")

    # ------------------------------------------------------------------ 决定

    def _record_decision(self, action, observation_id, reason, decided_by):
        oid = self.resolve_observation_id(observation_id)
        decision = {
            "observation_id": oid,
            "action": action,
            "reason": reason or "",
            "decided_by": decided_by or "auditor",
            "decided_at": _utcnow(),
        }
        self._decisions.append(decision)
        if self.decisions_path is not None:
            with self.decisions_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(decision, ensure_ascii=False, sort_keys=True) + "\n")
        return decision

    def quarantine(self, observation_id, reason="", decided_by="auditor"):
        """确认某份来源声明无效：观测保留，但当前视图不再采用它。"""
        return self._record_decision("quarantine", observation_id, reason, decided_by)

    def restore(self, observation_id, reason="", decided_by="auditor"):
        """撤销隔离：该观测重新参与当前视图的计算。"""
        return self._record_decision("restore", observation_id, reason, decided_by)

    def quarantined_ids(self):
        """当前处于隔离状态的观测标识集合（同一观测以最新决定为准）。"""
        state = {}
        for d in self._decisions:
            state[d["observation_id"]] = d["action"]
        return {oid for oid, action in state.items() if action == "quarantine"}

    def decisions(self):
        return list(self._decisions)

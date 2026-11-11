"""登记源快照的读取与规范化。

输入是一批观测（batch）：某个登记源在某个时间点对某个包的一次观测。
每条记录可声明：新版本(release)、撤回(withdraw)、弃用(deprecate)、
重签(resign)或制品替换(replace_artifact)。旧格式用 status 字段的记录
会被映射为对应事件（active->release、withdrawn->withdraw、deprecated->deprecate）。

本模块只做结构校验与规范化，不安装依赖，也不执行包内任何代码。
"""

import hashlib
import json
from pathlib import Path

EVENTS = ("release", "withdraw", "deprecate", "resign", "replace_artifact")

_STATUS_TO_EVENT = {
    "active": "release",
    "withdrawn": "withdraw",
    "deprecated": "deprecate",
}

_HEX64 = set("0123456789abcdef")


def parse_snapshot(path):
    """读取并校验单份快照，返回原始字典（保持既有行为）。"""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not value.get("registry") or not isinstance(value.get("releases"), list):
        raise ValueError("登记源快照结构无效")
    for release in value["releases"]:
        if not release.get("name") or not release.get("version"):
            raise ValueError("软件版本缺少身份")
    return value


def normalize_record(record, batch):
    """把一条来源记录规范化为内部事件格式。"""
    if not record.get("name") or not record.get("version"):
        raise ValueError("软件版本缺少身份")
    event = record.get("event")
    status = record.get("status")
    if event is None and status is not None:
        try:
            event = _STATUS_TO_EVENT[status]
        except KeyError:
            raise ValueError(f"未知状态: {status!r}") from None
    if event is None:
        # 快照中列出的版本即“该版本可用”的声明。
        event = "release"
    if event not in EVENTS:
        raise ValueError(f"未知事件类型: {event!r}")

    revision = record.get("revision")
    if revision is not None and not isinstance(revision, int):
        raise ValueError("元数据修订号必须是整数")

    digest = record.get("artifact_sha256")
    if digest is not None:
        digest = str(digest).lower()
        if len(digest) != 64 or not set(digest) <= _HEX64:
            raise ValueError(f"制品摘要不是有效的 sha256: {digest!r}")

    length = record.get("artifact_length")
    if length is not None and (not isinstance(length, int) or length < 0):
        raise ValueError("制品长度必须是非负整数")

    signature = record.get("signature")
    if signature is not None and not isinstance(signature, dict):
        raise ValueError("签名证明必须是对象")

    return {
        "name": record["name"],
        "version": record["version"],
        "event": event,
        "revision": revision,
        "artifact_sha256": digest,
        "artifact_length": length,
        "download_url": record.get("download_url"),
        "signature": signature,
        "reason": record.get("reason"),
    }


def normalize_batch(value, origin="<memory>"):
    """校验并规范化一批观测。origin 仅用于错误提示。"""
    if not isinstance(value, dict) or not value.get("registry"):
        raise ValueError(f"{origin}: 登记源快照结构无效")
    if not isinstance(value.get("releases"), list):
        raise ValueError(f"{origin}: 登记源快照结构无效")
    if not value.get("observed_at"):
        raise ValueError(f"{origin}: 批次缺少观测时间 observed_at")
    records = [normalize_record(r, value) for r in value["releases"]]
    return {
        "registry": value["registry"],
        # 采集来源（端点/镜像/缓存）。默认同登记源；不同来源可为同一登记源供数。
        "source": value.get("source") or value["registry"],
        "batch_id": value.get("batch_id"),
        "observed_at": value["observed_at"],
        # complete=false 表示来源尚未采全：未出现的版本不得视为已撤回。
        "complete": bool(value.get("complete", False)),
        "package": value.get("package"),
        "revision": value.get("revision"),
        "records": records,
    }


def load_batch(path):
    """从文件读取一批观测并规范化。"""
    path = Path(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    return normalize_batch(value, origin=str(path))


def observation_id(registry, source, batch_id, observed_at, record):
    """观测的确定性标识：同一批次的同一记录重复采集得到同一标识。

    采集进程重启或重复获取同一份快照不会产生新的观测。
    """
    envelope = {
        "registry": registry,
        "source": source,
        "batch_id": batch_id,
        "observed_at": observed_at,
        "record": record,
    }
    blob = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

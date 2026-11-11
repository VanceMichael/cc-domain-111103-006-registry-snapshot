"""版本目录：把观测折叠成当前视图，并保留得出状态的依据。

核心规则
--------
* 观测按 (观测时间, 批次, 批内序号) 顺序应用；被隔离的观测保留在存储与
  历史中，但不参与当前状态。
* 来源提供的元数据修订号（revision）是同一版本事实的先后依据：修订号更低
  的观测是陈旧观测（例如晚到的缓存），不得回滚已应用的状态。
* 制品替换、重签、撤回、弃用都是显式事件。同一版本出现不同制品摘要，
  而没有 replace_artifact 声明、也没有更高修订号时，视为可信来源互相
  矛盾：版本进入待复核(pending_review)，不按最后到达覆盖。
* 下载地址变化本身不等于制品变化：摘要不变就只更新地址。
* complete=true 的批次是来源对某包的完整枚举：已知版本未出现则视为该来源
  已不再提供；complete=false 的批次不做任何缺席推断。
* 目录键是 (登记源, 包名)：不同登记源的同名记录绝不合并。
"""

from app.versions import latest as latest_version, version_key


class VersionState:
    """单个 (登记源, 包名, 版本) 的折叠状态。"""

    def __init__(self):
        self.availability = "unknown"          # unknown | available | withdrawn
        self.availability_by = None            # 奠定可用性状态的观测标识或批次标记
        self.availability_rev = None           # 该观测的来源修订号
        self.deprecated = False
        self.deprecation_reason = None
        self.artifact = None                   # {sha256,length,url,since,observation_id,revision}
        self.superseded_artifacts = []         # 被替换下去的制品（含替换者）
        self.signature = None                  # 当前签名证明
        self.attestations = []                 # 全部签名证明，各自保留观测时间与来源
        self.revision = None                   # 已应用的最高来源修订号
        self.pending_review = False
        self.conflicts = []
        self.history = []                      # 得出状态的全部依据
        self.updated_at = None

    # ------------------------------------------------------------- 工具
    def _note(self, obs, effect, detail):
        entry = {"effect": effect, "detail": detail}
        if obs is not None:
            entry.update({
                "observation": obs["id"],
                "source": obs["source"],
                "observed_at": obs["observed_at"],
                "event": obs["record"]["event"],
            })
        self.history.append(entry)

    def _touch(self, obs):
        at = obs["observed_at"]
        if self.updated_at is None or at > self.updated_at:
            self.updated_at = at

    def _bump_revision(self, rev):
        if rev is not None and (self.revision is None or rev > self.revision):
            self.revision = rev

    def _resolve_conflicts_by(self, rev):
        """更高修订号的新证据可以了结此前的矛盾。"""
        if rev is None or not self.conflicts:
            return
        remaining = []
        for conflict in self.conflicts:
            revs = conflict["revisions"]
            if all(r is not None and r < rev for r in revs):
                self._note(None, "resolved",
                           f"修订号 {rev} 的新证据了结了矛盾: {conflict['detail']}")
            else:
                remaining.append(conflict)
        self.conflicts = remaining
        self.pending_review = bool(self.conflicts)

    def _hold(self, obs, field, other_ref, other_rev, detail):
        """可信来源互相矛盾：不采用后到观测，版本进入待复核。"""
        conflict = {
            "field": field,
            "observations": [other_ref, obs["id"]],
            "revisions": [other_rev, obs["record"]["revision"]],
            "detail": detail,
        }
        self.conflicts.append(conflict)
        self.pending_review = True
        self._note(obs, "held", f"待复核：{detail}")

    # ------------------------------------------------------------- 事件
    def apply(self, obs):
        rec = obs["record"]
        rev = rec["revision"]
        if rev is not None and self.revision is not None and rev < self.revision:
            self._note(obs, "stale",
                       f"修订号 {rev} 低于当前 {self.revision}，观测已陈旧，不采用")
            return
        handler = {
            "release": self._apply_release,
            "withdraw": self._apply_withdraw,
            "deprecate": self._apply_deprecate,
            "resign": self._apply_resign,
            "replace_artifact": self._apply_replace,
        }[rec["event"]]
        handler(obs)
        self._resolve_conflicts_by(rev)

    def _apply_release(self, obs):
        rec, oid, rev = obs["record"], obs["id"], obs["record"]["revision"]
        # 可用性矛盾：同一修订号下，一处说可用、一处说撤回
        if (self.availability == "withdrawn" and rev is not None
                and self.revision is not None and rev == self.revision):
            self._hold(obs, "availability", self.availability_by,
                       self.availability_rev, "同一修订号下可用性声明互相矛盾")
            return
        # 制品矛盾：摘要变了，却没有替换声明、也没有更高修订号
        new_sha = rec["artifact_sha256"]
        if new_sha and self.artifact and self.artifact["sha256"] != new_sha:
            higher = rev is not None and self.revision is not None and rev > self.revision
            if not higher:
                self._hold(obs, "artifact_sha256", self.artifact["observation_id"],
                           self.artifact.get("revision"),
                           "同一版本出现不同制品摘要，且无替换声明")
                return
            self._note(obs, "applied",
                       "更高修订号改变了制品摘要（未经替换声明），已记录")
        was_withdrawn = self.availability == "withdrawn"
        self.availability = "available"
        self.availability_by = oid
        self.availability_rev = rev
        if new_sha:
            url = rec["download_url"]
            if self.artifact and self.artifact["sha256"] == new_sha:
                if url and url != self.artifact.get("url"):
                    # 下载地址变化本身不等于制品变化
                    self.artifact["url"] = url
                    self._note(obs, "applied", "下载地址变化，制品摘要不变，不视为制品变化")
                else:
                    self._note(obs, "applied", "再次确认制品摘要")
            else:
                self.artifact = {"sha256": new_sha, "length": rec["artifact_length"],
                                 "url": url, "since": obs["observed_at"],
                                 "observation_id": oid, "revision": rev}
                self._note(obs, "applied", "登记制品")
        elif not self.artifact:
            self._note(obs, "applied", "仅元数据声明，无制品摘要")
        if was_withdrawn:
            self._note(obs, "applied", "撤回后重新发布，可用性恢复")
        self._apply_signature(obs)
        self._bump_revision(rev)
        self._touch(obs)

    def _apply_withdraw(self, obs):
        rec, oid, rev = obs["record"], obs["id"], obs["record"]["revision"]
        if (self.availability == "available" and rev is not None
                and self.revision is not None and rev == self.revision):
            self._hold(obs, "availability", self.availability_by,
                       self.availability_rev, "同一修订号下撤回与可用声明互相矛盾")
            return
        self.availability = "withdrawn"
        self.availability_by = oid
        self.availability_rev = rev
        reason = rec.get("reason")
        self._note(obs, "applied", f"版本被撤回{('：' + reason) if reason else ''}")
        self._bump_revision(rev)
        self._touch(obs)

    def _apply_deprecate(self, obs):
        rec, rev = obs["record"], obs["record"]["revision"]
        self.deprecated = True
        self.deprecation_reason = rec.get("reason")
        if self.availability == "unknown":
            self.availability = "available"
            self.availability_by = obs["id"]
            self.availability_rev = rev
        self._note(obs, "applied",
                   f"版本被弃用{('：' + rec['reason']) if rec.get('reason') else ''}（仍可用）")
        self._bump_revision(rev)
        self._touch(obs)

    def _apply_resign(self, obs):
        rec, rev = obs["record"], obs["record"]["revision"]
        new_sha = rec["artifact_sha256"]
        if new_sha and self.artifact and self.artifact["sha256"] != new_sha:
            self._hold(obs, "artifact_sha256", self.artifact["observation_id"],
                       self.artifact.get("revision"), "重签声明不得改变制品摘要")
            return
        self._apply_signature(obs, resigned=True)
        self._bump_revision(rev)
        self._touch(obs)

    def _apply_replace(self, obs):
        rec, oid, rev = obs["record"], obs["id"], obs["record"]["revision"]
        new_sha = rec["artifact_sha256"]
        if not new_sha:
            self._note(obs, "held", "制品替换声明缺少新摘要")
            return
        if self.artifact and self.artifact["sha256"] == new_sha:
            self._note(obs, "applied", "替换声明与当前制品一致，无需变更")
        else:
            if self.artifact:
                self.superseded_artifacts.append(
                    {**self.artifact, "replaced_by": oid})
            self.artifact = {"sha256": new_sha, "length": rec["artifact_length"],
                             "url": rec["download_url"], "since": obs["observed_at"],
                             "observation_id": oid, "revision": rev}
            self._note(obs, "applied", "制品被替换，旧制品转入历史")
        if self.availability == "unknown":
            self.availability = "available"
            self.availability_by = oid
            self.availability_rev = rev
        self._apply_signature(obs)
        self._bump_revision(rev)
        self._touch(obs)

    def _apply_signature(self, obs, resigned=False):
        sig = obs["record"]["signature"]
        if not sig:
            return
        attestation = {"signature": sig, "observation": obs["id"],
                       "source": obs["source"], "observed_at": obs["observed_at"]}
        if self.signature and self.signature != sig:
            self._note(obs, "applied",
                       "签名证明更新" if resigned else "签名证明随记录更新")
        self.signature = sig
        self.attestations.append(attestation)

    def mark_absent(self, batch_key, observed_at, batch_revision):
        """完整批次中未出现：来源已不再提供该版本。"""
        if self.availability == "withdrawn":
            return
        if (batch_revision is not None and self.revision is not None
                and self.revision > batch_revision):
            self._note(None, "stale",
                       f"完整批次 {batch_key} 修订号较旧，缺席不影响当前状态")
            return
        self.availability = "withdrawn"
        self.availability_by = f"batch:{batch_key}"
        self.availability_rev = batch_revision
        self._note(None, "absent",
                   f"未出现在完整批次 {batch_key}（{observed_at}），来源不再提供")

    # ------------------------------------------------------------- 视图
    def status(self):
        if self.pending_review:
            return "pending_review"
        if self.availability == "withdrawn":
            return "withdrawn"
        if self.availability == "available":
            return "deprecated" if self.deprecated else "active"
        return "unknown"

    def view(self):
        return {
            "status": self.status(),
            "availability": self.availability,
            "deprecated": self.deprecated,
            "deprecation_reason": self.deprecation_reason,
            "artifact": self.artifact,
            "superseded_artifacts": list(self.superseded_artifacts),
            "signature": self.signature,
            "attestations": list(self.attestations),
            "revision": self.revision,
            "pending_review": self.pending_review,
            "conflicts": list(self.conflicts),
            "updated_at": self.updated_at,
        }


class Catalog:
    """由存储中的观测计算出的目录。读取时重建，隔离决定即刻生效。"""

    def __init__(self, store):
        self.store = store
        self.quarantined = store.quarantined_ids()
        self._versions = {}   # (registry, name) -> {version: VersionState}
        self._build()

    # ------------------------------------------------------------- 折叠
    def _state(self, registry, name, version):
        versions = self._versions.setdefault((registry, name), {})
        if version not in versions:
            versions[version] = VersionState()
        return versions[version]

    def _build(self):
        batches = {}
        for obs in self.store.observations():
            key = (obs["registry"],
                   obs.get("batch_id") or f'{obs["source"]}|{obs["observed_at"]}')
            batches.setdefault(key, []).append(obs)
        for (registry, batch_key), obs_list in batches.items():
            for obs in obs_list:
                rec = obs["record"]
                state = self._state(registry, rec["name"], rec["version"])
                if obs["id"] in self.quarantined:
                    state._note(obs, "quarantined", "观测被审查隔离，不参与当前状态")
                    continue
                state.apply(obs)
            if obs_list[0]["complete"]:
                self._apply_absence(registry, batch_key, obs_list)

    def _apply_absence(self, registry, batch_key, obs_list):
        first = obs_list[0]
        package = first["package"]
        listed = {}
        for obs in obs_list:  # 被隔离的记录仍算“出现过”，避免隔离引发误撤回
            listed.setdefault(obs["record"]["name"], set()).add(obs["record"]["version"])
        scope = [package] if package else list(listed)
        for name in scope:
            versions = self._versions.get((registry, name), {})
            present = listed.get(name, set())
            for version, state in versions.items():
                if version not in present:
                    state.mark_absent(batch_key, first["observed_at"],
                                      first.get("batch_revision"))

    # ------------------------------------------------------------- 查询
    def packages(self):
        """全部已知 (登记源, 包名)。不同登记源的同名记录各自独立。"""
        return sorted(self._versions)

    def registries(self):
        return sorted({registry for registry, _ in self._versions})

    def package_view(self, registry, name):
        versions = self._versions.get((registry, name))
        if versions is None:
            raise KeyError(f"未知包: {registry}/{name}")
        available = [v for v, s in versions.items() if s.availability == "available"]
        return {
            "registry": registry,
            "name": name,
            "latest_available": latest_version(available),
            "versions": {v: versions[v].view()
                         for v in sorted(versions, key=version_key)},
        }

    def version_view(self, registry, name, version):
        versions = self._versions.get((registry, name))
        if not versions or version not in versions:
            raise KeyError(f"未知版本: {registry}/{name} {version}")
        view = versions[version].view()
        view.update({"registry": registry, "name": name, "version": version})
        return view

    def explain(self, registry, name, version):
        """当前状态连同得出状态的全部依据。"""
        view = self.version_view(registry, name, version)
        state = self._versions[(registry, name)][version]
        view["basis"] = [
            {**entry, "observation": _short(entry.get("observation"))}
            for entry in state.history
        ]
        view["quarantined_observations"] = sorted(
            oid for oid in self.quarantined
            if any(e.get("observation") == oid for e in state.history))
        return view

    def replay(self, registry=None, name=None):
        """按登记源重放历史观测（观测时间顺序），隔离状态一并标出。"""
        result = []
        for obs in self.store.observations(registry=registry):
            rec = obs["record"]
            if name is not None and rec["name"] != name:
                continue
            result.append({
                "id": obs["id"],
                "registry": obs["registry"],
                "source": obs["source"],
                "batch_id": obs["batch_id"],
                "observed_at": obs["observed_at"],
                "complete": obs["complete"],
                "name": rec["name"],
                "version": rec["version"],
                "event": rec["event"],
                "revision": rec["revision"],
                "quarantined": obs["id"] in self.quarantined,
            })
        return result


def _short(oid, length=12):
    if oid is None:
        return None
    return oid if len(oid) <= length else oid[:length]

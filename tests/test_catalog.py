"""目录状态的场景验证。

覆盖审计要求的四类验证：撤回早于缓存更新、制品被替换、跨命名空间重名、
不完整源批次；以及幂等采集、版本号排序、可信来源矛盾进入待复核、
隔离/恢复、下载地址变化、重签与弃用。所有输入均为合成数据。
"""

import tempfile
import unittest
from pathlib import Path

from app.artifacts import check_cache, verify_artifact
from app.catalog import Catalog
from app.store import Store

SCEN = Path("fixtures/scenarios")
ARTIFACTS = Path("fixtures/artifacts")

BUILD1_SHA = "5f0531dcd86b1b7cf9bbeae6b494bd0c6f670dd96618b45ed662535d143e76b9"
BUILD2_SHA = "c86799539ce89ca7b1b30832e2bca70223efb26624d5ab9316b3d113fee654e5"
V200_SHA = "b" * 64
MIRROR_SHA = "c" * 64

R1, LIB_A = "registry-one", "synthetic:lib-a"


def ingest(store, *names):
    for name in names:
        store.ingest_batch(path=SCEN / name)


def memory_catalog(*names):
    store = Store(None)
    ingest(store, *names)
    return store, Catalog(store)


class WithdrawalBeatsStaleCacheTests(unittest.TestCase):
    """撤回早于缓存更新：晚到的陈旧缓存观测不得把已撤回版本翻回可用。"""

    def test_withdrawal_stands(self):
        _, catalog = memory_catalog(
            "r1-batch-01-complete.json", "r1-batch-02-withdraw.json",
            "r1-batch-03-cache-stale.json")
        view = catalog.version_view(R1, LIB_A, "2.0.0")
        self.assertEqual(view["status"], "withdrawn")
        self.assertEqual(view["availability"], "withdrawn")
        self.assertEqual(view["revision"], 7)

    def test_stale_cache_recorded_in_basis(self):
        _, catalog = memory_catalog(
            "r1-batch-01-complete.json", "r1-batch-02-withdraw.json",
            "r1-batch-03-cache-stale.json")
        basis = catalog.explain(R1, LIB_A, "2.0.0")["basis"]
        stale = [e for e in basis if e["effect"] == "stale"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["source"], "cache-mirror")
        self.assertIn("陈旧", stale[0]["detail"])

    def test_other_version_unaffected(self):
        _, catalog = memory_catalog(
            "r1-batch-01-complete.json", "r1-batch-02-withdraw.json",
            "r1-batch-03-cache-stale.json")
        view = catalog.version_view(R1, LIB_A, "1.9.0")
        self.assertEqual(view["status"], "active")
        self.assertEqual(view["artifact"]["sha256"], BUILD1_SHA)


class ArtifactReplacementTests(unittest.TestCase):
    """制品被替换：当前制品更新，旧制品转入历史，缓存中的旧文件判为过期。"""

    def setUp(self):
        _, self.catalog = memory_catalog(
            "r1-batch-01-complete.json", "r1-batch-02-withdraw.json",
            "r1-batch-04-replace.json")
        self.view = self.catalog.version_view(R1, LIB_A, "1.9.0")

    def test_current_artifact_updated(self):
        self.assertEqual(self.view["artifact"]["sha256"], BUILD2_SHA)
        self.assertEqual(self.view["artifact"]["length"], 79)

    def test_old_artifact_kept_in_history(self):
        superseded = self.view["superseded_artifacts"]
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0]["sha256"], BUILD1_SHA)
        self.assertIn("replaced_by", superseded[0])

    def test_cached_old_file_is_stale(self):
        ok, result = check_cache(ARTIFACTS / "lib-a-1.9.0.build1.bin",
                                 self.view["artifact"])
        self.assertFalse(ok)
        self.assertEqual(result["verdict"], "stale")
        self.assertFalse(result["digest_ok"])

    def test_cached_new_file_is_current(self):
        ok, result = check_cache(ARTIFACTS / "lib-a-1.9.0.build2.bin",
                                 self.view["artifact"])
        self.assertTrue(ok)
        self.assertEqual(result["verdict"], "current")

    def test_verify_by_length_and_digest_only(self):
        good = verify_artifact(ARTIFACTS / "lib-a-1.9.0.build2.bin",
                               expected_sha256=BUILD2_SHA, expected_length=79)
        self.assertTrue(good["ok"])
        bad_length = verify_artifact(ARTIFACTS / "lib-a-1.9.0.build2.bin",
                                     expected_sha256=BUILD2_SHA, expected_length=70)
        self.assertFalse(bad_length["ok"])
        self.assertFalse(bad_length["length_ok"])


class CrossNamespaceTests(unittest.TestCase):
    """跨命名空间重名：不同登记源的同名记录各自独立，绝不合并。"""

    def test_same_name_kept_separate(self):
        _, catalog = memory_catalog(
            "r1-batch-01-complete.json", "r1-batch-02-withdraw.json",
            "r2-batch-01.json")
        self.assertIn((R1, LIB_A), catalog.packages())
        self.assertIn(("registry-two", LIB_A), catalog.packages())
        one = catalog.version_view(R1, LIB_A, "2.0.0")
        two = catalog.version_view("registry-two", LIB_A, "2.0.0")
        self.assertEqual(one["status"], "withdrawn")
        self.assertEqual(two["status"], "active")
        self.assertNotEqual(one["artifact"]["sha256"], two["artifact"]["sha256"])

    def test_replay_scoped_by_registry(self):
        _, catalog = memory_catalog("r1-batch-01-complete.json", "r2-batch-01.json")
        two = catalog.replay(registry="registry-two")
        self.assertEqual(len(two), 2)
        self.assertTrue(all(e["registry"] == "registry-two" for e in two))


class IncompleteBatchTests(unittest.TestCase):
    """不完整源批次：未出现的版本不得当成已撤回；完整批次才可以。"""

    def test_incomplete_batch_does_not_withdraw_absent_version(self):
        _, catalog = memory_catalog(
            "r1-libb-01-complete.json", "r1-libb-02-incomplete.json")
        view = catalog.version_view(R1, "synthetic:lib-b", "3.2.0")
        self.assertEqual(view["status"], "active")

    def test_complete_batch_withdraws_absent_version(self):
        _, catalog = memory_catalog(
            "r1-libb-01-complete.json", "r1-libb-02-incomplete.json",
            "r1-libb-03-complete.json")
        view = catalog.version_view(R1, "synthetic:lib-b", "3.2.0")
        self.assertEqual(view["status"], "withdrawn")
        basis = catalog.explain(R1, "synthetic:lib-b", "3.2.0")["basis"]
        self.assertTrue(any(e["effect"] == "absent" for e in basis))

    def test_listed_version_kept(self):
        _, catalog = memory_catalog(
            "r1-libb-01-complete.json", "r1-libb-03-complete.json")
        view = catalog.version_view(R1, "synthetic:lib-b", "3.1.0")
        self.assertEqual(view["status"], "active")


class IdempotentIngestTests(unittest.TestCase):
    """采集进程重启和重复获取不能制造多份同一观测。"""

    def test_repeated_ingest_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            added, _ = store.ingest_batch(path=SCEN / "r1-batch-01-complete.json")
            self.assertEqual(added, 2)
            added, _ = store.ingest_batch(path=SCEN / "r1-batch-01-complete.json")
            self.assertEqual(added, 0)
            self.assertEqual(len(store.observations()), 2)

            before = Catalog(store).package_view(R1, LIB_A)
            restarted = Store(tmp)  # 模拟进程重启后重新加载
            self.assertEqual(len(restarted.observations()), 2)
            added, _ = restarted.ingest_batch(path=SCEN / "r1-batch-01-complete.json")
            self.assertEqual(added, 0)
            after = Catalog(restarted).package_view(R1, LIB_A)
            self.assertEqual(before, after)


class VersionOrderingTests(unittest.TestCase):
    """版本号不能按字典序决定新旧：1.10.0 比 1.9.0 新。"""

    def test_latest_available_not_lexicographic(self):
        _, catalog = memory_catalog(
            "r1-batch-01-complete.json", "r1-batch-02-withdraw.json",
            "r1-batch-08-newversion.json")
        view = catalog.package_view(R1, LIB_A)
        # 字典序下 "1.10.0" < "1.9.0"，正确结果必须是 1.10.0
        self.assertLess("1.10.0", "1.9.0")  # 确认字典序确实会给出错误答案
        self.assertEqual(view["latest_available"], "1.10.0")


class ContradictionTests(unittest.TestCase):
    """两份可信来源互相矛盾：进入待复核，不按最后到达覆盖。"""

    FILES = ("r1-batch-01-complete.json", "r1-batch-04-replace.json",
             "r1-batch-05-mirror-conflict.json")

    def _mirror_obs_id(self, store):
        for obs in store.observations():
            if obs["source"] == "mirror-eu":
                return obs["id"]
        self.fail("缺少镜像观测")

    def test_pending_review_not_last_write_wins(self):
        _, catalog = memory_catalog(*self.FILES)
        view = catalog.version_view(R1, LIB_A, "1.9.0")
        self.assertEqual(view["status"], "pending_review")
        self.assertTrue(view["pending_review"])
        # 后到的镜像声明不得覆盖先建立的当前制品
        self.assertEqual(view["artifact"]["sha256"], BUILD2_SHA)
        self.assertEqual(view["conflicts"][0]["field"], "artifact_sha256")

    def test_quarantine_resolves_and_keeps_observation(self):
        store, _ = memory_catalog(*self.FILES)
        mirror_id = self._mirror_obs_id(store)
        store.quarantine(mirror_id, reason="镜像声明与主源矛盾，确认无效")
        catalog = Catalog(store)
        view = catalog.version_view(R1, LIB_A, "1.9.0")
        self.assertEqual(view["status"], "active")
        self.assertEqual(view["artifact"]["sha256"], BUILD2_SHA)
        # 原观测保留：存储可查、重放可见且标记隔离
        self.assertIsNotNone(store.get_observation(mirror_id))
        replay = {e["id"]: e for e in catalog.replay(registry=R1)}
        self.assertTrue(replay[mirror_id]["quarantined"])
        explained = catalog.explain(R1, LIB_A, "1.9.0")
        self.assertIn(mirror_id, explained["quarantined_observations"])

    def test_restore_returns_to_pending(self):
        store, _ = memory_catalog(*self.FILES)
        mirror_id = self._mirror_obs_id(store)
        store.quarantine(mirror_id, reason="暂时隔离")
        store.restore(mirror_id, reason="复核后重新纳入")
        view = Catalog(store).version_view(R1, LIB_A, "1.9.0")
        self.assertEqual(view["status"], "pending_review")

    def test_quarantine_other_side_adopts_mirror(self):
        store, _ = memory_catalog(*self.FILES)
        mirror_id = self._mirror_obs_id(store)
        replace_id = None
        for obs in store.observations():
            if obs["record"]["event"] == "replace_artifact":
                replace_id = obs["id"]
        store.quarantine(replace_id, reason="主源替换声明被确认无效")
        view = Catalog(store).version_view(R1, LIB_A, "1.9.0")
        self.assertEqual(view["status"], "active")
        self.assertEqual(view["artifact"]["sha256"], MIRROR_SHA)
        self.assertIsNotNone(store.get_observation(mirror_id))

    def test_new_evidence_resolves_conflict(self):
        store, _ = memory_catalog(*self.FILES)
        self.assertEqual(Catalog(store).version_view(R1, LIB_A, "1.9.0")["status"],
                         "pending_review")
        ingest(store, "r1-batch-06-confirm.json")  # 主源更高修订号确认替换
        view = Catalog(store).version_view(R1, LIB_A, "1.9.0")
        self.assertEqual(view["status"], "active")
        self.assertEqual(view["artifact"]["sha256"], BUILD2_SHA)
        self.assertEqual(view["revision"], 9)


class UrlChangeTests(unittest.TestCase):
    """下载地址变化本身不等于制品变化。"""

    def test_url_change_keeps_artifact(self):
        _, catalog = memory_catalog(
            "r1-batch-01-complete.json", "r1-batch-04-replace.json",
            "r1-batch-07-resign-url.json")
        view = catalog.version_view(R1, LIB_A, "1.9.0")
        self.assertEqual(view["artifact"]["sha256"], BUILD2_SHA)
        self.assertEqual(view["artifact"]["url"],
                         "https://cdn.example.invalid/lib-a/1.9.0")
        self.assertFalse(view["pending_review"])
        basis = catalog.explain(R1, LIB_A, "1.9.0")["basis"]
        self.assertTrue(any("下载地址变化" in e["detail"] for e in basis))


class ResignTests(unittest.TestCase):
    """重签：签名证明更新，制品摘要不变，各次证明保留观测时间与来源。"""

    def test_resign_updates_signature_only(self):
        _, catalog = memory_catalog(
            "r1-batch-01-complete.json", "r1-batch-04-replace.json",
            "r1-batch-07-resign-url.json")
        view = catalog.version_view(R1, LIB_A, "1.9.0")
        self.assertEqual(view["artifact"]["sha256"], BUILD2_SHA)
        self.assertEqual(view["signature"]["value"], "sig-1b")
        attestations = view["attestations"]
        self.assertEqual(len(attestations), 2)
        self.assertEqual(attestations[0]["signature"]["value"], "sig-1a")
        self.assertEqual(attestations[0]["observed_at"], "2026-11-10T10:00:00Z")
        self.assertEqual(attestations[1]["signature"]["value"], "sig-1b")
        self.assertEqual(attestations[1]["source"], "primary")


class DeprecateTests(unittest.TestCase):
    def test_deprecated_but_available(self):
        _, catalog = memory_catalog(
            "r1-batch-08-newversion.json", "r1-batch-09-deprecate.json")
        view = catalog.version_view(R1, LIB_A, "1.10.0")
        self.assertEqual(view["status"], "deprecated")
        self.assertEqual(view["availability"], "available")
        self.assertEqual(view["deprecation_reason"], "不再维护，请迁移到 2.x")


class ReplayTests(unittest.TestCase):
    def test_replay_in_observation_order(self):
        _, catalog = memory_catalog(
            "r1-batch-01-complete.json", "r1-batch-02-withdraw.json",
            "r2-batch-01.json")
        entries = catalog.replay(registry=R1)
        self.assertEqual(len(entries), 4)
        ordered = [e["observed_at"] for e in entries]
        self.assertEqual(ordered, sorted(ordered))
        self.assertEqual(entries[0]["event"], "release")
        self.assertEqual(entries[-1]["event"], "withdraw")


if __name__ == "__main__":
    unittest.main()

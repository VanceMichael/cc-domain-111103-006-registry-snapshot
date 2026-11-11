"""本地演示入口：读取 fixtures 中的合成快照并展示目录当前视图。

只读取来源材料，不写入 runtime 存储，不安装或执行任何包代码。
"""

from app.catalog import Catalog
from app.registry import parse_snapshot
from app.store import Store

SNAPSHOTS = ("fixtures/snapshot-a.json", "fixtures/snapshot-b.json")


def main():
    print([(r["version"], r["status"])
           for r in parse_snapshot("fixtures/snapshot-a.json")["releases"]])
    store = Store(None)  # 纯内存：演示不落盘
    for path in SNAPSHOTS:
        store.ingest_batch(path=path)
    catalog = Catalog(store)
    for registry, name in catalog.packages():
        view = catalog.package_view(registry, name)
        summary = {v: s["status"] for v, s in view["versions"].items()}
        print(f"{registry}  {name}  最新可用={view['latest_available']}  {summary}")


if __name__ == "__main__":
    main()

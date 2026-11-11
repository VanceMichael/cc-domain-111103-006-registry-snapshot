"""命令行入口：采集、重放、查询、解释与审查操作。

示例：
    python3 -m app.cli ingest fixtures/scenarios/r1-batch-01-complete.json
    python3 -m app.cli replay --registry registry-one
    python3 -m app.cli status --registry registry-one --name synthetic:lib-a
    python3 -m app.cli explain --registry registry-one --name synthetic:lib-a --version 2.0.0
    python3 -m app.cli quarantine <观测标识前缀> --reason "来源声明无效"
    python3 -m app.cli restore <观测标识前缀> --reason "新证据恢复"
    python3 -m app.cli verify-artifact 文件 --registry R --name N --version V
"""

import argparse
import json
import sys

from app.artifacts import check_cache, verify_artifact
from app.catalog import Catalog
from app.store import Store


def _print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False))


def _cmd_ingest(store, args):
    total_added = 0
    for path in args.files:
        added, ids = store.ingest_batch(path=path)
        total_added += added
        print(f"{path}: 新增 {added} 份观测（批次共 {len(ids)} 条记录）")
    print(f"合计新增 {total_added} 份观测；重复采集不会产生重复观测")


def _cmd_replay(store, args):
    catalog = Catalog(store)
    entries = catalog.replay(registry=args.registry, name=args.name)
    for e in entries:
        mark = " [已隔离]" if e["quarantined"] else ""
        rev = f" rev={e['revision']}" if e["revision"] is not None else ""
        print(f"{e['observed_at']}  {e['source']:<14} {e['name']} {e['version']} "
              f"{e['event']}{rev}  ({e['id'][:12]}){mark}")
    if not entries:
        print("没有匹配的观测")


def _cmd_status(store, args):
    catalog = Catalog(store)
    if args.version:
        _print_json(catalog.version_view(args.registry, args.name, args.version))
    else:
        _print_json(catalog.package_view(args.registry, args.name))


def _cmd_explain(store, args):
    catalog = Catalog(store)
    _print_json(catalog.explain(args.registry, args.name, args.version))


def _cmd_quarantine(store, args):
    decision = store.quarantine(args.observation, reason=args.reason,
                                decided_by=args.by)
    print(f"已隔离 {decision['observation_id'][:12]}：{decision['reason']}"
          "（原观测保留，当前视图不再采用）")


def _cmd_restore(store, args):
    decision = store.restore(args.observation, reason=args.reason,
                             decided_by=args.by)
    print(f"已恢复 {decision['observation_id'][:12]}：{decision['reason']}")


def _cmd_verify(store, args):
    if args.registry and args.name and args.version:
        catalog = Catalog(store)
        artifact = catalog.version_view(args.registry, args.name,
                                        args.version)["artifact"]
        ok, result = check_cache(args.file, artifact)
        _print_json(result)
        sys.exit(0 if ok else 1)
    else:
        result = verify_artifact(args.file, expected_sha256=args.sha256,
                                 expected_length=args.length)
        _print_json(result)
        sys.exit(0 if result["ok"] else 1)


def _cmd_packages(store, args):
    catalog = Catalog(store)
    for registry, name in catalog.packages():
        view = catalog.package_view(registry, name)
        summary = {v: s["status"] for v, s in view["versions"].items()}
        print(f"{registry}  {name}  最新可用={view['latest_available']}  {summary}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    parser.add_argument("--store", default="runtime",
                        help="观测存储目录（默认 runtime/）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="采集一批观测（可重复执行，幂等）")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("replay", help="按登记源重放历史观测")
    p.add_argument("--registry")
    p.add_argument("--name")
    p.set_defaults(func=_cmd_replay)

    p = sub.add_parser("status", help="查找当前状态")
    p.add_argument("--registry", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--version")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("explain", help="解释状态依据")
    p.add_argument("--registry", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--version", required=True)
    p.set_defaults(func=_cmd_explain)

    p = sub.add_parser("quarantine", help="确认某份来源声明无效（观测保留）")
    p.add_argument("observation")
    p.add_argument("--reason", default="")
    p.add_argument("--by", default="auditor")
    p.set_defaults(func=_cmd_quarantine)

    p = sub.add_parser("restore", help="撤销隔离，观测重新参与当前视图")
    p.add_argument("observation")
    p.add_argument("--reason", default="")
    p.add_argument("--by", default="auditor")
    p.set_defaults(func=_cmd_restore)

    p = sub.add_parser("verify-artifact",
                       help="只按长度和摘要校验制品文件，不执行包代码")
    p.add_argument("file")
    p.add_argument("--sha256")
    p.add_argument("--length", type=int)
    p.add_argument("--registry")
    p.add_argument("--name")
    p.add_argument("--version")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("packages", help="列出全部已知 (登记源, 包名)")
    p.set_defaults(func=_cmd_packages)

    args = parser.parse_args(argv)
    store = Store(args.store)
    try:
        args.func(store, args)
    except KeyError as exc:
        print(f"未找到: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

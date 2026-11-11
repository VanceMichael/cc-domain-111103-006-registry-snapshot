import json
from pathlib import Path

def parse_snapshot(path):
    value=json.loads(Path(path).read_text(encoding="utf-8"))
    if not value.get("registry") or not isinstance(value.get("releases"),list): raise ValueError("登记源快照结构无效")
    for release in value["releases"]:
        if not release.get("name") or not release.get("version"): raise ValueError("软件版本缺少身份")
    return value

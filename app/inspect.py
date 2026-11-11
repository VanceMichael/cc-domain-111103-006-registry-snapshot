from pathlib import Path
from app.registry import parse_snapshot
print([(r["version"],r["status"]) for r in parse_snapshot("fixtures/snapshot-a.json")["releases"]])

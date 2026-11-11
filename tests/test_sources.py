import json
import unittest
from pathlib import Path
from app.registry import parse_snapshot
class DomainTests(unittest.TestCase):
    def test_source_identity(self): self.assertEqual(parse_snapshot("fixtures/snapshot-a.json")["registry"],"registry-one")
    def test_releases(self): self.assertEqual(len(parse_snapshot("fixtures/snapshot-a.json")["releases"]),2)
    def test_incomplete(self): self.assertFalse(parse_snapshot("fixtures/snapshot-b.json")["complete"])

class FixtureTests(unittest.TestCase):
    def test_fixture_directory(self): self.assertTrue(Path("fixtures").is_dir())

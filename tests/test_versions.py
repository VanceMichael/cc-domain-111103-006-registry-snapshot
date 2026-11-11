"""版本号比较器的单元测试：绝不退化为整串字典序。"""

import unittest

from app.versions import compare, latest, version_key


class VersionKeyTests(unittest.TestCase):
    def test_numeric_segments_compare_by_value(self):
        self.assertGreater(compare("1.10.0", "1.9.0"), 0)
        self.assertLess(compare("1.9.0", "1.10.0"), 0)
        self.assertGreater(compare("2.0.0", "1.10.0"), 0)

    def test_lexicographic_order_would_differ(self):
        # 字典序下 "1.10.0" < "1.9.0"，比较器必须给出相反结论
        self.assertLess("1.10.0", "1.9.0")
        self.assertGreater(compare("1.10.0", "1.9.0"), 0)

    def test_prerelease_before_release(self):
        self.assertLess(compare("1.0.0-alpha", "1.0.0"), 0)
        self.assertLess(compare("1.0.0-alpha", "1.0.0-beta"), 0)
        self.assertLess(compare("1.0.0-alpha.1", "1.0.0-alpha.2"), 0)

    def test_equal_versions(self):
        self.assertEqual(compare("1.0.0", "1.0.0"), 0)
        self.assertEqual(compare("v1.2.3", "1.2.3"), 0)

    def test_latest(self):
        self.assertEqual(latest(["1.9.0", "1.10.0", "2.0.0"]), "2.0.0")
        self.assertEqual(latest(["1.10.0", "1.9.0"]), "1.10.0")
        self.assertIsNone(latest([]))

    def test_empty_version_rejected(self):
        with self.assertRaises(ValueError):
            version_key("")
        with self.assertRaises(ValueError):
            version_key("   ")


if __name__ == "__main__":
    unittest.main()

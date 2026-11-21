"""Tests for the helpers that read backups and expand package patterns.

Run from the repo folder:  python -m unittest -v
"""
import os
import tempfile
import unittest

# The module looks up adb when it is imported. Any existing file satisfies that;
# the tests never run adb.
os.environ["ADB"] = os.path.abspath(__file__)

import android_debloater as ad  # noqa: E402


class ParseSettingsTest(unittest.TestCase):
    def test_multiline_value_is_joined(self):
        lines = ["a=1", "b=first", "second line", "c=null"]
        self.assertEqual(ad.parse_settings_lines(lines),
                         {"a": "1", "b": "first\nsecond line", "c": None})

    def test_line_after_null_is_dropped(self):
        self.assertEqual(ad.parse_settings_lines(["c=null", "stray"]), {"c": None})


class ParseBackupTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backup.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("=== DEVICE ===\n"
                        "serial: ABC\n"
                        "manufacturer: samsung\n"
                        "model: SM-X\n\n"
                        "=== NAMESPACE: system ===\nx=1\n\n"
                        "=== NAMESPACE: secure ===\n\n"
                        "=== NAMESPACE: global ===\nk=v\n\n"
                        "=== PACKAGES: installed ===\ncom.a\ncom.b\n\n"
                        "=== PACKAGES: disabled ===\ncom.b\n\n")
            device, settings, packages = ad.parse_backup(path)
        self.assertEqual(device, {"serial": "ABC", "manufacturer": "samsung", "model": "SM-X"})
        self.assertEqual(settings["system"], {"x": "1"})
        self.assertEqual(settings["secure"], {})
        self.assertEqual(settings["global"], {"k": "v"})
        self.assertEqual(packages, {"installed": {"com.a", "com.b"}, "disabled": {"com.b"}})


class ExpandPackagesTest(unittest.TestCase):
    def test_wildcard_is_expanded_and_duplicates_dropped(self):
        installed = {"com.x.one", "com.x.two", "com.other"}
        self.assertEqual(
            ad.expand_packages(["com.x.*", "com.x.one", "com.gone.*"], installed),
            ["com.x.one", "com.x.two", "com.gone.*"])


if __name__ == "__main__":
    unittest.main()

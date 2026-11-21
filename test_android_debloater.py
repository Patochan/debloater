"""Tests for the helpers that read backups and expand package patterns.

Run from the repo folder:  python -m unittest -v
"""
import contextlib
import io
import os
import tempfile
import unittest
import unittest.mock

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


class DisableAdbTest(unittest.TestCase):
    def _order(self, over_network):
        """Setting keys in the order disable_adb writes them."""
        sent = []

        def fake_adb(args):
            sent.append(args[-2])  # "settings put global <key> 0"
            return 0, "", ""

        with unittest.mock.patch.object(ad, "run_adb", side_effect=fake_adb), \
                contextlib.redirect_stdout(io.StringIO()):
            ad.disable_adb(over_network)
        return sent

    def test_usb_turns_adb_off_last(self):
        self.assertEqual(self._order(False), [
            "development_settings_enabled", "adb_wifi_enabled", "adb_enabled"])

    def test_wifi_turns_usb_debugging_off_before_wireless(self):
        self.assertEqual(self._order(True), [
            "development_settings_enabled", "adb_enabled", "adb_wifi_enabled"])

    def test_network_serial(self):
        self.assertTrue(ad.is_network_serial("192.168.1.5:5555"))
        self.assertTrue(ad.is_network_serial("adb-XYZ._adb-tls-connect._tcp"))
        self.assertFalse(ad.is_network_serial("ABC123"))


class PrivacySettingsTest(unittest.TestCase):
    def test_reads_each_namespace_once_and_skips_matching_values(self):
        calls = []

        def fake_adb(args):
            calls.append(args)
            return 0, "a=1\nb=2", ""

        with unittest.mock.patch.object(ad, "run_adb", side_effect=fake_adb), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            ad.apply_privacy_settings([("global", "a", "1"), ("global", "b", "3")], apply=False)
        self.assertEqual(calls, [["shell", "settings", "list", "global"]])
        self.assertIn("[OK]   global/a = 1 (already set)", out.getvalue())
        self.assertIn("[DRY]  global/b: 2 -> 3", out.getvalue())


class ExpandPackagesTest(unittest.TestCase):
    def test_wildcard_is_expanded_and_duplicates_dropped(self):
        installed = {"com.x.one", "com.x.two", "com.other"}
        self.assertEqual(
            ad.expand_packages(["com.x.*", "com.x.one", "com.gone.*"], installed),
            ["com.x.one", "com.x.two", "com.gone.*"])


if __name__ == "__main__":
    unittest.main()

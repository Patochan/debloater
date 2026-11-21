"""Tests for the helpers that read backups and expand package patterns.

Run from the repo folder:  python -m unittest -v
"""
import argparse
import contextlib
import io
import os
import shlex
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


class ManagedSettingsTest(unittest.TestCase):
    def test_location_setting_is_restorable(self):
        # --restore only reverts settings listed here, so the location option must be included
        self.assertIn(("secure", "location_mode"), ad.managed_settings())


class ParseSideloadedTest(unittest.TestCase):
    def test_play_store_apps_are_left_out(self):
        out = ("package:com.a  installer=com.android.vending\n"
               "package:com.b  installer=org.fdroid.fdroid\n"
               "package:com.c  installer=null\n")
        self.assertEqual(ad.parse_sideloaded(out),
                         [("com.b", "org.fdroid.fdroid"), ("com.c", "unknown")])


class TeeStreamTest(unittest.TestCase):
    def test_output_goes_to_console_and_report(self):
        console, report = io.StringIO(), io.StringIO()
        tee = ad.TeeStream(console, report)
        tee.write("[OK] done\n")
        self.assertEqual(console.getvalue(), "[OK] done\n")
        self.assertEqual(report.getvalue(), "[OK] done\n")


class PackageListTest(unittest.TestCase):
    def test_names_are_split_and_checked(self):
        self.assertEqual(ad.package_list("com.a.b, com.c"), ["com.a.b", "com.c"])

    def test_shell_characters_are_rejected(self):
        # Names are sent to the phone's shell, so anything but a package name is refused
        for bad in ["com.a;reboot", "com.a b", "nodots", "com.a$(id)"]:
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                ad.package_list(bad)


class PermissionListTest(unittest.TestCase):
    def test_short_names_get_the_android_prefix(self):
        self.assertEqual(ad.permission_list("com.a:CAMERA, com.b:android.permission.RECORD_AUDIO"),
                         [("com.a", "android.permission.CAMERA"),
                          ("com.b", "android.permission.RECORD_AUDIO")])

    def test_bad_items_are_rejected(self):
        for bad in ["com.a", "com.a:", "com.a:CAM;ERA", "bad pkg:CAMERA", "com.a:CAMERA$(id)"]:
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                ad.permission_list(bad)


class BlockedUidsTest(unittest.TestCase):
    def test_parses_list_output(self):
        with unittest.mock.patch.object(ad, "run_adb", return_value=(0, "Restrict background blacklisted UIDs: 10206 10300 ", "")):
            self.assertEqual(ad.blocked_background_uids(), ["10206", "10300"])
        with unittest.mock.patch.object(ad, "run_adb", return_value=(0, "Restrict background blacklisted UIDs: none", "")):
            self.assertEqual(ad.blocked_background_uids(), ["none"])  # 'none' never equals a uid


class AuditParseTest(unittest.TestCase):
    def test_sensitive_app_ops_are_found(self):
        out = ("Uid mode: COARSE_LOCATION: foreground\n"
               "FINE_LOCATION: foreground\n"
               "CAMERA: ignore\n"
               "SYSTEM_ALERT_WINDOW: allow; time=+1d4h ago; duration=+6s\n"
               "SOME_OTHER_OP: allow\n"
               "RECORD_AUDIO: allow\n"
               "RECORD_AUDIO: allow; time=+1d ago\n")
        self.assertEqual(ad.parse_sensitive_access(out),
                         ["approximate location", "precise location", "draw over other apps",
                          "microphone"])

    def test_device_admins_are_read_from_their_section(self):
        out = ("Enabled Device Admins (User 0):\n"
               "  ComponentInfo{com.example.mdm/.AdminReceiver}\n"
               "Default admin policy size limit: -1\n"
               "  ComponentInfo{com.later/.Ignored}\n")
        with unittest.mock.patch.object(ad, "run_adb", return_value=(0, out, "")):
            self.assertEqual(ad.device_admins(), ["com.example.mdm"])


class ExpandPackagesTest(unittest.TestCase):
    def test_wildcard_is_expanded_and_duplicates_dropped(self):
        installed = {"com.x.one", "com.x.two", "com.other"}
        self.assertEqual(
            ad.expand_packages(["com.x.*", "com.x.one", "com.gone.*"], installed),
            ["com.x.one", "com.x.two", "com.gone.*"])


if __name__ == "__main__":
    unittest.main()


class PrivateDnsSupportTest(unittest.TestCase):
    def test_android_9_and_newer_is_supported(self):
        self.assertTrue(ad.private_dns_supported("28"))
        self.assertTrue(ad.private_dns_supported("37"))

    def test_older_or_unknown_sdk_is_skipped(self):
        self.assertFalse(ad.private_dns_supported("19"))
        self.assertFalse(ad.private_dns_supported("27"))
        self.assertFalse(ad.private_dns_supported(""))


class ResolvePrivateDnsTest(unittest.TestCase):
    def test_default_when_nothing_given(self):
        self.assertEqual(ad.resolve_private_dns(None, False), ad.DEFAULT_PRIVATE_DNS)

    def test_explicit_host_is_used(self):
        self.assertEqual(ad.resolve_private_dns("one.one.one.one", False), "one.one.one.one")

    def test_no_private_dns_flag_disables(self):
        self.assertIsNone(ad.resolve_private_dns(None, True))

    def test_empty_value_disables(self):
        self.assertIsNone(ad.resolve_private_dns("", False))


class FakeAdb:
    """Stand-in for run_adb: a phone with settings in memory (refuse=True: writes are ignored)."""

    def __init__(self, settings, refuse=False):
        self.settings = {ns: dict(v) for ns, v in settings.items()}
        self.refuse = refuse
        self.calls = []

    def __call__(self, args):
        self.calls.append(" ".join(args))
        if args[:3] == ["shell", "settings", "list"]:
            body = self.settings.get(args[3], {})
            return 0, "\n".join(f"{k}={'null' if v is None else v}" for k, v in body.items()), ""
        if args[:3] == ["shell", "settings", "get"] and len(args) == 5:
            value = self.settings.get(args[3], {}).get(args[4])
            return 0, "null" if value is None else value, ""
        if len(args) == 2 and args[0] == "shell" and args[1].startswith("settings "):
            if not self.refuse:
                parts = shlex.split(args[1])
                if parts[1] == "put":
                    self.settings.setdefault(parts[2], {})[parts[3]] = parts[4]
                elif parts[1] == "delete":
                    self.settings.get(parts[2], {}).pop(parts[3], None)
            return 0, "", ""
        return 0, "", ""


class RestoreSettingsTest(unittest.TestCase):
    def _restore(self, backup, phone, refuse=False):
        fake = FakeAdb(phone, refuse=refuse)
        with unittest.mock.patch.object(ad, "run_adb", side_effect=fake), \
                contextlib.redirect_stdout(io.StringIO()):
            result = ad.restore_settings(
                {"system": {}, "secure": {}, "global": {}, **backup}, restore_all=False, apply=True)
        return fake, result

    def test_user_setting_that_is_not_managed_is_never_deleted(self):
        fake, _ = self._restore({}, {"secure": {"my_own_setting": "5"}})
        self.assertFalse(any("delete" in c and "my_own_setting" in c for c in fake.calls))
        self.assertEqual(fake.settings["secure"], {"my_own_setting": "5"})

    def test_managed_setting_that_was_not_in_backup_is_deleted(self):
        fake, _ = self._restore({}, {"global": {"private_dns_mode": "hostname"}})
        self.assertNotIn("private_dns_mode", fake.settings["global"])

    def test_write_that_does_not_stick_is_reported_as_failed(self):
        _, (changed, failed) = self._restore(
            {"global": {"multi_cb": "1"}}, {"global": {"multi_cb": "0"}}, refuse=True)
        self.assertEqual(failed, 1)


class SensitiveAccessManyTest(unittest.TestCase):
    def test_each_app_gets_its_own_block(self):
        out = ("Uid mode: CAMERA: allow\n----- next app -----\n"
               "CAMERA: ignore\n----- next app -----\n")
        with unittest.mock.patch.object(ad, "run_adb", return_value=(0, out, "")) as run:
            result = ad.sensitive_access_many(["com.a.one", "com.b.two"])
        self.assertEqual(result, {"com.a.one": ["camera"], "com.b.two": []})
        self.assertIn("cmd appops get com.a.one", run.call_args[0][0][1])

    def test_invalid_names_are_not_sent_to_the_shell(self):
        with unittest.mock.patch.object(ad, "run_adb", return_value=(0, "", "")) as run:
            result = ad.sensitive_access_many(["bad name; rm -rf /"])
        run.assert_not_called()
        self.assertEqual(result, {})


class ExistingSettingsTest(unittest.TestCase):
    def test_keeps_only_keys_that_exist(self):
        fake = FakeAdb({"global": {"a": "1", "b": None}})
        with unittest.mock.patch.object(ad, "run_adb", side_effect=fake):
            kept = ad.existing_settings([("global", "a", "0"), ("global", "b", "0"),
                                         ("global", "c", "0")])
        self.assertEqual(kept, [("global", "a", "0")])
        self.assertEqual(len([c for c in fake.calls if "settings list" in c]), 1)


class UndoPrivateDnsTest(unittest.TestCase):
    def test_previous_values_are_put_back_and_unset_ones_deleted(self):
        fake = FakeAdb({"global": {"private_dns_mode": "hostname",
                                   "private_dns_specifier": "dns.quad9.net"}})
        with unittest.mock.patch.object(ad, "run_adb", side_effect=fake), \
                contextlib.redirect_stdout(io.StringIO()):
            ad.undo_private_dns(("opportunistic", None))
        self.assertEqual(fake.settings["global"]["private_dns_mode"], "opportunistic")
        self.assertNotIn("private_dns_specifier", fake.settings["global"])


class PruneReportsTest(unittest.TestCase):
    def test_only_the_oldest_reports_are_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(22):
                open(os.path.join(tmp, f"report_20260101_0000{i:02d}.txt"), "w").close()
            open(os.path.join(tmp, "settings_backup_x_20260101_000000.txt"), "w").close()
            with unittest.mock.patch.object(ad, "LOG_DIR", tmp):
                removed = ad.prune_reports()
            left = sorted(os.listdir(tmp))
        self.assertEqual(removed, 2)
        self.assertEqual(sum(n.startswith("report_") for n in left), ad.REPORTS_TO_KEEP)
        self.assertIn("settings_backup_x_20260101_000000.txt", left)
        self.assertNotIn("report_20260101_000000.txt", left)

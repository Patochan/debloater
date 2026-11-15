#!/usr/bin/env python3
"""Generic Android privacy debloater via ADB.

Detects the manufacturer/model of the connected device and applies generic
(Google/AOSP) as well as vendor-specific settings and package lists.
By default the script runs as a dry run and changes nothing.

Usage:
    python android_debloater.py                       # dry run: only show changes
    python android_debloater.py --run                 # actually apply changes
    python android_debloater.py -s SERIAL             # specific device
    python android_debloater.py --run --private-dns dns.quad9.net
    python android_debloater.py --run --disable-agps --include-optional

Restore from a backup (also a dry run by default):
    python android_debloater.py --restore logs/settings_backup_....txt
    python android_debloater.py --restore logs/settings_backup_....txt --run
    python android_debloater.py --restore logs/settings_backup_....txt --all --run
"""
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Privacy settings (namespace, key, value) applied to all devices
COMMON_SETTINGS = [
    # Disable Google usage & diagnostics
    ("global", "multi_cb", "0"),
    # Disable background Wi-Fi and Bluetooth scanning (even when turned off)
    ("global", "wifi_scan_always_enabled", "0"),
    ("global", "ble_scan_always_enabled", "0"),
    # Hide notification content on the lock screen
    ("secure", "lock_screen_allow_private_notifications", "0"),
    # Play Protect: don't upload unknown APKs to Google (scanning stays on)
    ("global", "upload_apk_enable", "0"),
    # No Wi-Fi network rating/recommendations by Google Play Services
    ("global", "network_recommendations_enabled", "0"),
    # Show a notice when an app reads the clipboard
    ("secure", "clipboard_show_access_notifications", "1"),
    # Don't turn Wi-Fi on automatically near saved networks (uses location scans)
    ("global", "wifi_wakeup_enabled", "0"),
    # No "Send feedback" to Google in the app crash dialog
    ("secure", "send_action_app_error", "0"),
    ("global", "send_action_app_error", "0"),
    # Remove the bug report shortcut from the power menu (reports contain a lot of data)
    ("global", "bug_report", "0"),
]

# Battery runtime settings, only applied with --battery (trade-offs, not privacy)
BATTERY_SETTINGS = [
    # Don't keep mobile data connected while on Wi-Fi (developer option)
    ("global", "mobile_data_always_on", "0"),
    # Adaptive Battery: limit background activity of rarely used apps
    ("global", "adaptive_battery_management_enabled", "1"),
]

# Packages to remove (--user 0) on all devices
COMMON_PACKAGES = [
    # Google SafetyCore (client-side scanning)
    "com.google.android.safetycore",
    # Google telemetry / feedback / partner setup
    "com.google.android.feedback",
    "com.google.mainline.telemetry",
    "com.google.android.partnersetup",
    # Preinstalled Facebook services (run even without the Facebook app, e.g. Samsung/Xiaomi)
    "com.facebook.services",
    "com.facebook.system",
    "com.facebook.appmanager",
]

# Vendor-specific additions. Key = ro.product.manufacturer (lowercase).
# "optional_packages" are only removed with --include-optional.
# "marker_packages": if none of them is installed, the device most likely runs a
# custom ROM (e.g. LineageOS) and the vendor profile is skipped.
VENDOR_PROFILES = {
    "samsung": {
        "marker_packages": ["com.sec.android.app.launcher", "com.samsung.android.honeyboard"],
        "settings": [
            # Disable Samsung diagnostic data
            ("system", "samsung_errorlog_agree", "0"),
            # Disable continuous scanning for nearby devices
            ("system", "nearby_scanning_enabled", "0"),
        ],
        "packages": [
            "com.samsung.android.rubin.app",       # Customization Service (user profiling)
            "com.samsung.android.dqagent",         # Device Quality Agent (telemetry)
            "com.samsung.android.app.spage",       # Samsung Free / news feed
            "com.samsung.android.mapsagent",       # Samsung app recommendations / tracking
            "com.aura.oobe.samsung",               # AppCloud (installs bloatware unasked)
            "com.sec.android.diagmonagent",        # DiagMonAgent (error reports to Samsung)
        ],
        "optional_packages": [
            "com.samsung.android.bixby.agent",     # Bixby (if only Google Assistant is used)
            "com.microsoft.appmanager",            # Link to Windows (SMS/contacts/mic access)
        ],
    },
    "xiaomi": {
        "marker_packages": ["com.miui.home", "com.miui.securitycenter"],
        "settings": [],
        "packages": [
            "com.miui.analytics",                  # MIUI Analytics (telemetry)
            "com.miui.msa.global",                 # MIUI System Ads
            "com.miui.daemon",                     # MiuiDaemon (usage statistics)
        ],
        "optional_packages": [],
    },
}
# Manufacturer aliases that share a profile
VENDOR_ALIASES = {"redmi": "xiaomi", "poco": "xiaomi"}

# For --restore: settings/packages changed by earlier versions or options of the
# script, in addition to the lists above.
EXTRA_MANAGED_SETTINGS = [
    ("system", "min_refresh_rate"),
    ("global", "private_dns_mode"),
    ("global", "private_dns_specifier"),
    ("global", "assisted_gps_enabled"),
    ("secure", "limit_ad_tracking"),
    ("system", "marketing_info_agree"),
]
EXTRA_MANAGED_PACKAGES = [
    "com.google.android.contactkeys",
    "com.samsung.android.app.omcagent",
]

# ---------------------------------------------------------------------------
# ADB helpers
# ---------------------------------------------------------------------------


def find_adb() -> str:
    """Locate adb: ADB environment variable, PATH, then common SDK locations."""
    env_adb = os.environ.get("ADB")
    if env_adb and os.path.isfile(env_adb):
        return env_adb
    on_path = shutil.which("adb")
    if on_path:
        return on_path

    exe = "adb.exe" if os.name == "nt" else "adb"
    home = os.path.expanduser("~")
    candidates = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            candidates.append(os.path.join(os.environ[var], "platform-tools", exe))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        candidates += [
            os.path.join(local, "Android", "Sdk", "platform-tools", exe),
            os.path.join("C:\\", "platform-tools", exe),
            os.path.join(home, "platform-tools", exe),
            os.path.join(home, "Downloads", "platform-tools", exe),
        ]
    else:
        candidates += [
            os.path.join(home, "Android", "Sdk", "platform-tools", exe),
            os.path.join(home, "Library", "Android", "sdk", "platform-tools", exe),
        ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    print("[ERROR] adb not found.")
    print("  Install the Android Platform-Tools or set the ADB environment variable,")
    print("  e.g.:  set ADB=C:\\path\\to\\platform-tools\\adb.exe")
    sys.exit(1)


ADB = find_adb()
SERIAL: str | None = None  # set after device selection
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def run_adb(args: list[str]) -> tuple[int, str, str]:
    """Run an ADB command (for the selected device): (return code, stdout, stderr)."""
    cmd = [ADB]
    if SERIAL:
        cmd += ["-s", SERIAL]
    try:
        result = subprocess.run(
            cmd + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as e:
        return 1, "", str(e)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def getprop(name: str) -> str:
    _, out, _ = run_adb(["shell", "getprop", name])
    return out.strip()


def get_setting(namespace: str, key: str) -> str | None:
    """Read a setting; None if it doesn't exist."""
    code, out, _ = run_adb(["shell", "settings", "get", namespace, key])
    if code != 0 or out in ("", "null"):
        return None
    return out


def list_packages(*flags: str) -> set[str]:
    """pm list packages [flags]; e.g. -u (incl. uninstalled for the user), -d (disabled)."""
    code, out, err = run_adb(["shell", "pm", "list", "packages", *flags])
    if code != 0 or (not out and not flags):
        print(f"[ERROR] Could not read package list: {err}")
        sys.exit(1)
    return {line.replace("package:", "").strip() for line in out.splitlines() if line.strip()}


def get_installed_packages() -> set[str]:
    return list_packages()


# ---------------------------------------------------------------------------
# Device selection & detection
# ---------------------------------------------------------------------------


def select_device(requested: str | None) -> str:
    """Select an authorized device and return its serial number."""
    print(f"[INFO] Using adb: {ADB}")
    code, out, err = run_adb(["devices", "-l"])
    if code != 0:
        print(f"[ERROR] Could not start adb: {err}")
        sys.exit(1)

    ready, other = [], []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        info = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        (ready if state == "device" else other).append((serial, state, info))

    for serial, state, _ in other:
        hint = " (accept the debugging prompt on the device?)" if state == "unauthorized" else ""
        print(f"[INFO] Ignoring {serial}: state '{state}'{hint}")

    if requested:
        if any(s == requested for s, _, _ in ready):
            return requested
        print(f"[ERROR] Device '{requested}' is not connected/authorized.")
        sys.exit(1)

    if not ready:
        print("[ERROR] No authorized Android device found. Is USB debugging enabled?")
        sys.exit(1)
    if len(ready) == 1:
        return ready[0][0]

    print("\nMultiple devices connected:")
    for i, (serial, _, info) in enumerate(ready, 1):
        print(f"  [{i}] {serial}  {info.get('model', '')}")
    while True:
        choice = input("Select device (number): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(ready):
            return ready[int(choice) - 1][0]


def detect_device() -> dict[str, str]:
    """Read manufacturer, model and Android version of the device."""
    manufacturer = getprop("ro.product.manufacturer") or "unknown"
    info = {
        "serial": SERIAL or "",
        "manufacturer": manufacturer,
        "brand": getprop("ro.product.brand"),
        "model": getprop("ro.product.model") or "unknown",
        "device": getprop("ro.product.device"),
        "name": getprop("ro.product.marketname")
        or getprop("ro.product.vendor.marketname")
        or getprop("ro.config.marketing_name")
        or "",
        "android": getprop("ro.build.version.release"),
        "sdk": getprop("ro.build.version.sdk"),
        "security_patch": getprop("ro.build.version.security_patch"),
        "verified_boot": getprop("ro.boot.verifiedbootstate"),
    }
    vendor = manufacturer.lower()
    info["profile"] = VENDOR_ALIASES.get(vendor, vendor)
    return info


def safe_filename_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "unknown"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def backup_settings(dev: dict[str, str]) -> None:
    """Back up all settings (read-only, also runs in dry-run mode)."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(LOG_DIR, exist_ok=True)
    filename = os.path.join(
        LOG_DIR,
        f"settings_backup_{safe_filename_part(dev['manufacturer'])}"
        f"_{safe_filename_part(dev['model'])}_{timestamp}.txt",
    )
    with open(filename, "w", encoding="utf-8") as f:
        f.write("=== DEVICE ===\n")
        for key, value in dev.items():
            f.write(f"{key}: {value}\n")
        f.write("\n")
        for namespace in ("system", "secure", "global"):
            code, out, err = run_adb(["shell", "settings", "list", namespace])
            if code != 0:
                print(f"[ERROR] Backup of '{namespace}' failed: {err}")
                sys.exit(1)
            f.write(f"=== NAMESPACE: {namespace} ===\n{out}\n\n")
        f.write("=== PACKAGES: installed ===\n")
        f.write("\n".join(sorted(list_packages())) + "\n\n")
        f.write("=== PACKAGES: disabled ===\n")
        f.write("\n".join(sorted(list_packages("-d"))) + "\n\n")
    print(f"[OK] Backup of current settings saved to: {filename}")


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

SECTION_RE = re.compile(r"^=== (.+) ===$")
SETTING_RE = re.compile(r"^([A-Za-z0-9_.:\-]+)=(.*)$")


def parse_settings_lines(lines: list[str]) -> dict[str, str | None]:
    """Parse 'key=value' lines; lines without a key belong to the previous value (multi-line)."""
    result: dict[str, str | None] = {}
    last = None
    for line in lines:
        m = SETTING_RE.match(line)
        if m:
            last = m.group(1)
            result[last] = m.group(2)
        elif last is not None and result[last] is not None:
            result[last] += "\n" + line
    # "null" means: the setting exists but has no value
    return {k: (None if v == "null" else v) for k, v in result.items()}


def parse_backup(path: str):
    """Read a backup file: (device info, {namespace: settings}, packages or None)."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    sections: dict[str, list[str]] = {}
    current = None
    for line in lines:
        m = SECTION_RE.match(line)
        if m:
            current = m.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    device = {}
    # "GERÄT" is the section name used by older (German) versions of the script
    for line in sections.get("DEVICE", sections.get("GERÄT", [])):
        if ": " in line:
            k, v = line.split(": ", 1)
            device[k] = v

    settings = {}
    for ns in ("system", "secure", "global"):
        body = sections.get(f"NAMESPACE: {ns}", [])
        while body and not body[-1]:  # strip blank lines between sections
            body.pop()
        settings[ns] = parse_settings_lines(body)

    packages = None
    if "PACKAGES: installed" in sections:
        packages = {
            "installed": {p for p in sections["PACKAGES: installed"] if p.strip()},
            "disabled": {p for p in sections.get("PACKAGES: disabled", []) if p.strip()},
        }
    return device, settings, packages


def managed_settings() -> set[tuple[str, str]]:
    keys = {(ns, k) for ns, k, _ in COMMON_SETTINGS + BATTERY_SETTINGS}
    for profile in VENDOR_PROFILES.values():
        keys |= {(ns, k) for ns, k, _ in profile["settings"]}
    return keys | set(EXTRA_MANAGED_SETTINGS)


def managed_packages() -> set[str]:
    pkgs = set(COMMON_PACKAGES) | set(EXTRA_MANAGED_PACKAGES)
    for profile in VENDOR_PROFILES.values():
        pkgs |= set(profile["packages"]) | set(profile.get("optional_packages", []))
    return pkgs


def short(value: str | None, limit: int = 70) -> str:
    if value is None:
        return "not set"
    value = value.replace("\n", "\\n")
    return value if len(value) <= limit else value[:limit] + "..."


def restore_backup(path: str, apply: bool, restore_all: bool, force: bool) -> None:
    if not os.path.isfile(path):
        print(f"[ERROR] Backup file not found: {path}")
        sys.exit(1)
    device, backup, backup_pkgs = parse_backup(path)

    serial = device.get("serial", "")
    print(f"\n[INFO] Backup: {os.path.basename(path)}")
    print(f"[INFO] Backup device: {device.get('manufacturer', '?')} {device.get('model', '?')} "
          f"(serial {serial or '?'})")
    if serial and serial != SERIAL and not force:
        print(f"[ERROR] Backup is from a different device ({serial}, connected: {SERIAL}).")
        print("  Use --force to restore anyway.")
        sys.exit(1)
    mode = "ALL settings" if restore_all else "only settings managed by the script"
    print(f"[INFO] Scope: {mode}")

    # --- Settings ---
    print("\n--- Restoring settings ---")
    managed = managed_settings()
    changed = failed = 0
    for ns in ("system", "secure", "global"):
        code, out, err = run_adb(["shell", "settings", "list", ns])
        if code != 0:
            print(f"[ERROR] Could not read current settings ({ns}): {err}")
            sys.exit(1)
        current = parse_settings_lines(out.splitlines())
        old = backup[ns]

        if restore_all:
            keys = set(old) | {k for (n, k) in managed if n == ns}
        else:
            keys = {k for (n, k) in managed if n == ns}

        for key in sorted(keys):
            in_backup = key in old and old[key] is not None
            target = old.get(key)
            now = current.get(key)
            if in_backup and target == now:
                continue
            if not in_backup:
                # Only delete keys the script itself may have created
                if now is None or (ns, key) not in managed:
                    continue
                desc = f"{ns}/{key}: {short(now)} -> (delete, was not set)"
                cmd = f"settings delete {ns} {shlex.quote(key)}"
            else:
                desc = f"{ns}/{key}: {short(now)} -> {short(target)}"
                cmd = f"settings put {ns} {shlex.quote(key)} {shlex.quote(target)}"

            changed += 1
            if not apply:
                print(f"[DRY]  {desc}")
                continue
            code, _, err = run_adb(["shell", cmd])
            if code == 0 and not err:
                print(f"[SET]  {desc}")
            else:
                failed += 1
                print(f"[WARN] Could not restore {ns}/{key}: {err}")

    if changed == 0:
        print("[OK]   All settings already match the backup.")

    # --- Packages ---
    print("\n--- Restoring packages ---")
    installed = list_packages()
    on_system = list_packages("-u")      # incl. system packages uninstalled for user 0
    disabled = list_packages("-d")

    if backup_pkgs is None:
        print("[INFO] Backup has no package list (older version); "
              "checking the packages managed by the script.")
        candidates = managed_packages()
        was_disabled: set[str] = set()
    else:
        candidates = backup_pkgs["installed"] if restore_all else (
            backup_pkgs["installed"] & managed_packages())
        was_disabled = backup_pkgs["disabled"]

    pkg_changes = 0
    for pkg in sorted(candidates):
        if pkg not in installed:
            if pkg not in on_system:
                if backup_pkgs is not None:
                    print(f"[SKIP] {pkg} no longer on the device (reinstall from Play Store)")
                continue
            pkg_changes += 1
            if not apply:
                print(f"[DRY]  {pkg} would be restored")
                continue
            code, out, err = run_adb(["shell", "pm", "install-existing", "--user", "0", pkg])
            if code == 0 and "installed" in out.lower():
                print(f"[ADD]  {pkg} restored")
            else:
                failed += 1
                print(f"[ERR]  {pkg}: {err or out}")
        elif pkg in disabled and pkg not in was_disabled:
            pkg_changes += 1
            if not apply:
                print(f"[DRY]  {pkg} would be enabled")
                continue
            code, out, err = run_adb(["shell", "pm", "enable", "--user", "0", pkg])
            if code == 0:
                print(f"[ENA]  {pkg} enabled")
            else:
                failed += 1
                print(f"[ERR]  {pkg}: {err or out}")

    if pkg_changes == 0:
        print("[OK]   No packages to restore.")

    if not apply:
        print(f"\nDry run: {changed} setting(s), {pkg_changes} package(s) would be "
              "restored. To apply: --run")
    else:
        print(f"\nRestore finished ({failed} error(s)). A reboot is recommended.")


def apply_privacy_settings(settings: list[tuple[str, str, str]], apply: bool) -> None:
    print("\n--- Privacy settings ---")
    for namespace, key, value in settings:
        current = get_setting(namespace, key)
        if current == value:
            print(f"[OK]   {namespace}/{key} = {value} (already set)")
            continue
        change = f"{namespace}/{key}: {current if current is not None else 'not set'} -> {value}"
        if not apply:
            print(f"[DRY]  {change}")
            continue
        code, _, err = run_adb(["shell", "settings", "put", namespace, key, value])
        if code == 0:
            print(f"[SET]  {change}")
        else:
            print(f"[WARN] Could not set {namespace}/{key}: {err}")


def remove_packages(packages: list[str], installed: set[str], disabled: set[str],
                    apply: bool) -> None:
    print("\n--- Removing packages (--user 0) ---")
    for pkg in packages:
        if pkg not in installed:
            print(f"[SKIP] {pkg} (not installed)")
            continue
        if pkg in disabled:
            # Disabled packages are still listed as installed; uninstalling was
            # already refused on an earlier run, so there is nothing left to do.
            print(f"[OK]   {pkg} (already disabled)")
            continue
        if not apply:
            print(f"[DRY]  {pkg} would be removed/disabled")
            continue

        code, out, err = run_adb(["shell", "pm", "uninstall", "-k", "--user", "0", pkg])
        if code == 0 and "Success" in out:
            print(f"[DEL]  {pkg} removed")
            installed.discard(pkg)
        else:
            # Fallback: some system packages can only be disabled, not uninstalled
            code_dis, _, err_dis = run_adb(["shell", "pm", "disable-user", "--user", "0", pkg])
            if code_dis == 0:
                print(f"[DIS]  {pkg} disabled (uninstall not allowed)")
                disabled.add(pkg)
            else:
                print(f"[ERR]  {pkg} failed: {err or out or err_dis}")


def manual_recommendations(dev: dict[str, str], installed: set[str],
                           private_dns_set: bool) -> list[str]:
    """Check things that can't (or shouldn't) be changed via ADB."""
    tips: list[str] = []
    samsung = dev["profile"] == "samsung" and not dev["custom_rom"]

    # Security patch level
    try:
        patch = datetime.strptime(dev["security_patch"], "%Y-%m-%d")
        age_days = (datetime.now() - patch).days
        if age_days > 90:
            tips.append(
                f"Security patch from {dev['security_patch']} is {age_days} days old. "
                "Install updates; if the device no longer gets any, stop using it for "
                "sensitive data (banking, passwords)."
            )
    except ValueError:
        tips.append("Could not determine the security patch level; check it manually.")

    # Unlocked bootloader
    if dev["verified_boot"] == "orange":
        tips.append(
            "Bootloader is unlocked (Verified Boot: orange). Anyone who gets hold of the "
            "device can flash software. Use a strong PIN/password and don't leave the "
            "device unattended."
        )

    # Screen timeout
    timeout = get_setting("system", "screen_off_timeout")
    if timeout and timeout.isdigit() and int(timeout) > 120000:
        tips.append(
            f"Screen timeout is {int(timeout) // 60000} minutes; the device stays unlocked "
            "and readable that long. Set it to 2 minutes or less: "
            "Settings > Display > Screen timeout."
        )

    # Device name is visible nearby via Bluetooth/hotspot/Quick Share
    names = {n for n in (get_setting("global", "device_name"),
                         get_setting("secure", "bluetooth_name")) if n}
    if names:
        shown = ", ".join(f'"{n}"' for n in sorted(names))
        tips.append(
            f"Device name {shown} is visible to nearby devices (Bluetooth, hotspot, "
            "Quick Share). If it contains your name, choose a neutral one: "
            "Settings > About phone > Edit."
        )

    # Apps with access to all notifications
    listeners_raw = get_setting("secure", "enabled_notification_listeners") or ""
    listeners = sorted({c.split("/")[0] for c in listeners_raw.split(":") if "/" in c})
    if listeners:
        tips.append(
            f"{len(listeners)} app(s) can read ALL notifications. Revoke access for the ones "
            "you no longer need (Settings > Notifications > Notification access):\n"
            + "\n".join(f"        - {p}" for p in listeners)
        )

    # Advertising ID
    tips.append(
        "Delete the advertising ID: Settings > Google > All services > Ads > "
        "Delete advertising ID."
    )

    # Face unlock
    if "facelock" in (get_setting("secure", "lockscreen.options") or ""):
        tips.append(
            "Face unlock is enabled. It only uses the camera (2D) and is less secure than "
            "fingerprint + PIN. Consider turning it off: "
            "Settings > Security > Biometrics > Face recognition."
        )

    # Samsung Find My Mobile / SmartThings Find: encrypt offline location
    if (samsung and get_setting("secure", "fmm_offline_find_support") == "Y"
            and get_setting("secure", "offline_finding_e2e") == "0"):
        tips.append(
            "Offline finding is on, but the offline location doesn't seem to be encrypted. "
            "Turn it on: Settings > Security > Find My Mobile > Offline finding > "
            "Encrypt offline location."
        )

    # SMS/MMS backup even though Google backup is off
    if get_setting("secure", "mms_backup_enabled") == "1":
        tips.append(
            "SMS/MMS backup is on. Check where your messages are backed up to "
            "(Google Messages > Settings > Backup, or Samsung Cloud)."
        )

    # Optional packages that are still installed
    if samsung and "com.samsung.android.bixby.agent" in installed:
        tips.append(
            "Bixby is installed and has location, sensor and call log permissions among "
            "others. If unused: remove it with --include-optional."
        )
    if "com.microsoft.appmanager" in installed:
        tips.append(
            "Link to Windows is installed (pre-granted SMS, contacts, camera and microphone "
            "permissions). If unused: remove it with --include-optional or turn it off in "
            "Settings > Connected devices > Link to Windows."
        )

    # Private DNS
    if not private_dns_set and (get_setting("global", "private_dns_mode")
                                or "opportunistic") != "hostname":
        tips.append(
            "No fixed Private DNS provider. Set one with --private-dns dns.quad9.net "
            "or manually: Settings > Connections > More connection settings > Private DNS."
        )

    # USB debugging (necessarily on while the script runs)
    tips.append(
        "After debloating, turn USB debugging and Developer options off again: "
        "Settings > Developer options > switch at the top."
    )
    return tips


def refresh_rate_fix() -> tuple[str, str, str] | None:
    """If the minimum refresh rate is forced above 60 Hz, return a setting to allow 60 Hz."""
    try:
        if float(get_setting("system", "min_refresh_rate") or 0) > 60:
            return ("system", "min_refresh_rate", "60.0")
    except ValueError:
        pass
    return None


def battery_recommendations(dev: dict[str, str], battery_flag: bool) -> list[str]:
    """Check settings that affect battery lifespan and runtime."""
    tips: list[str] = []
    samsung = dev["profile"] == "samsung" and not dev["custom_rom"]

    # --- Battery lifespan: charge limit and heat ---
    if samsung:
        if get_setting("global", "protect_battery") == "0":
            tips.append(
                "Battery protection is off, so the battery sits at 100% for hours. Turn it on "
                "(biggest lifespan gain): Settings > Battery > Battery protection > Maximum "
                "(limit 80-85%)."
            )
        if get_setting("system", "wireless_fast_charging") == "1":
            tips.append(
                "Fast wireless charging is on. Wireless charging already produces a lot of heat; "
                "turning this off reduces battery wear: Settings > Battery > Charging settings."
            )
        if "1" in (get_setting("system", "adaptive_fast_charging"),
                   get_setting("system", "super_fast_charging")):
            tips.append(
                "Wired fast charging is on. If you charge overnight or have time, turning it off "
                "means less heat and less wear: Settings > Battery > Charging settings."
            )
    elif get_setting("secure", "charge_optimization_mode") is not None:
        tips.append(
            "This system supports charging optimization. Limit charging to 80% if offered: "
            "Settings > Battery > Charging optimization > Limit to 80%."
        )
    elif dev["custom_rom"]:
        tips.append(
            "Custom ROM: check for a charge limit (LineageOS-based ROMs: Settings > Battery > "
            "Charging control) and limit charging to 80%."
        )

    # --- Runtime: display ---
    if refresh_rate_fix() and not battery_flag:
        tips.append(
            "The screen is forced to its peak refresh rate all the time (min_refresh_rate > 60). "
            "Allowing 60 Hz saves a lot of battery: use --battery, or turn off "
            "Developer options > Force peak refresh rate."
        )
    # refresh_rate_mode: 0 = Standard (60 Hz), 2 = Adaptive (verified on Galaxy S21 FE and S25+)
    if samsung and (get_setting("secure", "refresh_rate_mode") or "0") != "0":
        tips.append(
            "Motion smoothness is set to adaptive/high (up to 120 Hz). Standard (60 Hz) saves "
            "noticeable battery: Settings > Display > Motion smoothness > Standard."
        )
    if samsung and get_setting("system", "aod_mode") == "1" \
            and get_setting("system", "aod_tap_to_show_mode") == "0":
        tips.append(
            "Always On Display is shown permanently. 'Tap to show' or off saves battery: "
            "Settings > Lock screen and AOD > Always On Display."
        )
    if get_setting("system", "screen_brightness_mode") == "0":
        tips.append(
            "Adaptive brightness is off. Turning it on usually saves battery, since the screen "
            "is the biggest consumer: Settings > Display > Adaptive brightness."
        )

    # --- Runtime: radios and background ---
    if get_setting("global", "mobile_data_always_on") == "1" and not battery_flag:
        tips.append(
            "Mobile data stays connected while on Wi-Fi (mobile_data_always_on=1). "
            "Use --battery to turn it off."
        )
    if get_setting("global", "adaptive_battery_management_enabled") == "0" and not battery_flag:
        tips.append("Adaptive Battery is off. Use --battery to turn it on.")
    modes = get_setting("global", "preferred_network_mode") or ""
    if any(m.strip().isdigit() and int(m) >= 23 for m in modes.split(",")):
        tips.append(
            "5G is enabled. Where 5G coverage is weak, the modem keeps searching and drains the "
            "battery; switching to LTE/4G helps: Settings > Connections > Mobile networks > "
            "Network mode."
        )
    return tips


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    global SERIAL
    parser = argparse.ArgumentParser(
        description="Android privacy debloater via ADB (default: dry run, nothing is changed)"
    )
    parser.add_argument("-s", "--serial", default=os.environ.get("ANDROID_SERIAL"),
                        help="serial number of the device (if several are connected)")
    parser.add_argument("--run", action="store_true",
                        help="actually apply changes (without it: dry run only)")
    parser.add_argument("--private-dns", metavar="HOST",
                        help="set a fixed Private DNS provider, e.g. dns.quad9.net")
    parser.add_argument("--disable-agps", action="store_true",
                        help="turn off Assisted GPS (sends cell tower info to Google; "
                             "first GPS fix gets slower)")
    parser.add_argument("--include-optional", action="store_true",
                        help="also remove optional packages (e.g. Bixby, Link to Windows)")
    parser.add_argument("--battery", action="store_true",
                        help="also apply battery runtime settings (mobile data not kept "
                             "connected on Wi-Fi, Adaptive Battery on, no forced peak refresh rate)")
    parser.add_argument("--restore", metavar="FILE",
                        help="restore settings/packages from a backup file")
    parser.add_argument("--all", action="store_true",
                        help="with --restore: restore all differing settings and packages, "
                             "not only the ones managed by the script")
    parser.add_argument("--force", action="store_true",
                        help="with --restore: allow a backup from a different device")
    args = parser.parse_args()
    apply = args.run

    SERIAL = select_device(args.serial)
    dev = detect_device()

    label = dev["name"] or dev["model"]
    print(f"[OK] Device: {dev['manufacturer']} {label} ({dev['model']}), "
          f"Android {dev['android']} (SDK {dev['sdk']}), serial {SERIAL}")
    if not apply:
        print("[INFO] DRY RUN: nothing will be changed. Use --run to apply.")

    backup_settings(dev)

    if args.restore:
        restore_backup(args.restore, apply, args.all, args.force)
        return

    installed = get_installed_packages()

    empty_profile = {"settings": [], "packages": [], "optional_packages": []}
    profile = VENDOR_PROFILES.get(dev["profile"])
    dev["custom_rom"] = bool(
        profile and not any(p in installed for p in profile.get("marker_packages", []))
    )
    if dev["custom_rom"]:
        print(f"[INFO] No {dev['manufacturer']} system apps found: probably a custom ROM. "
              "Skipping the vendor profile, generic rules only.")
        profile = empty_profile
    elif profile:
        print(f"[INFO] Vendor profile: {dev['profile']}")
    else:
        print(f"[INFO] No vendor profile for '{dev['manufacturer']}', generic rules only.")
        profile = empty_profile

    settings = COMMON_SETTINGS + profile["settings"]
    if args.private_dns:
        settings += [("global", "private_dns_mode", "hostname"),
                     ("global", "private_dns_specifier", args.private_dns)]
    if args.disable_agps:
        settings.append(("global", "assisted_gps_enabled", "0"))
    if args.battery:
        settings += BATTERY_SETTINGS
        fix = refresh_rate_fix()
        if fix:
            settings.append(fix)

    packages = COMMON_PACKAGES + profile["packages"]
    if args.include_optional:
        packages += profile.get("optional_packages", [])

    disabled = list_packages("-d")

    apply_privacy_settings(settings, apply)
    remove_packages(packages, installed, disabled, apply)

    print("\n--- Manual recommendations ---")
    # Disabled packages don't run, so they don't need a "still installed" tip
    tips = manual_recommendations(dev, installed - disabled, bool(args.private_dns))
    for i, tip in enumerate(tips, 1):
        print(f"  {i}. {tip}")

    print("\n--- Battery recommendations ---")
    battery_tips = battery_recommendations(dev, args.battery)
    if not battery_tips:
        print("  No battery issues found.")
    for i, tip in enumerate(battery_tips, 1):
        print(f"  {i}. {tip}")

    if apply:
        print("\nDone. A reboot of the device is recommended.")
    else:
        print("\nDry run finished, nothing was changed. To apply: --run")


if __name__ == "__main__":
    main()

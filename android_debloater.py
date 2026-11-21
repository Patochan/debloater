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
import time
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
    # No Wi-Fi network rating/recommendations by Google Play Services.
    # -1 = forced off; with 0 the system turns it back on while a provider (GMS) exists.
    ("global", "network_recommendations_enabled", "-1"),
    # Show a notice when an app reads the clipboard
    ("secure", "clipboard_show_access_notifications", "1"),
    # Don't turn Wi-Fi on automatically near saved networks (uses location scans)
    ("global", "wifi_wakeup_enabled", "0"),
    # No "Send feedback" to Google in the app crash dialog
    ("secure", "send_action_app_error", "0"),
    ("global", "send_action_app_error", "0"),
    # Remove the bug report shortcut from the power menu (reports contain a lot of data)
    ("global", "bug_report", "0"),
    # Wi-Fi: a new random MAC address per network connection, so networks can't track the phone's hardware address
    ("global", "non_persistent_mac_randomization_force_enabled", "1"),
]

# Battery runtime settings, only applied with --battery (trade-offs, not privacy)
BATTERY_SETTINGS = [
    # Don't keep mobile data connected while on Wi-Fi (developer option)
    ("global", "mobile_data_always_on", "0"),
    # Adaptive Battery: limit background activity of rarely used apps
    ("global", "adaptive_battery_management_enabled", "1"),
]

# AI features, only changed with --ai (all devices)
AI_SETTINGS = [
    # Smart replies/actions suggested in notifications
    ("global", "smart_replies_enabled", "0"),
    # Assistant may not read text or take screenshots of the current screen
    ("secure", "assist_structure_enabled", "0"),
    ("secure", "assist_screenshot_enabled", "0"),
    # Don't start the assistant by long-pressing home or swiping from a corner
    ("secure", "assist_long_press_home_enabled", "0"),
    ("secure", "assist_touch_gesture_enabled", "0"),
]
AI_PACKAGES = [
    "com.google.android.apps.bard",                # Gemini
    "com.google.android.aicore",                   # AICore (on-device Gemini Nano)
    "com.google.android.apps.aiwallpapers",        # AI wallpapers
    "com.google.android.as",                       # Android System Intelligence
    "com.google.android.as.oss",                   # Private Compute Services (ASI network bridge)
]
# Apps the user installed themselves: only reported, never removed
AI_USER_APPS = {
    "com.openai.chatgpt": "ChatGPT",
    "com.microsoft.copilot": "Microsoft Copilot",
    "com.anthropic.claude": "Claude",
    "ai.perplexity.app.android": "Perplexity",
    "com.deepseek.chat": "DeepSeek",
}

# Cloud features, only changed with --cloud (all devices)
CLOUD_SETTINGS = [
    # Don't automatically restore app data from the cloud when an app is reinstalled
    ("secure", "backup_auto_restore", "0"),
]
CLOUD_USER_APPS = {
    "com.google.android.apps.photos": "Google Photos",
    "com.google.android.apps.docs": "Google Drive",
    "com.dropbox.android": "Dropbox",
    "com.microsoft.skydrive": "OneDrive",
    "com.nextcloud.client": "Nextcloud",
    "mega.privacy.android.app": "MEGA",
    "me.proton.android.drive": "Proton Drive",
}

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

# Location settings, only changed with --location-device-only (trade-off: less accurate location)
LOCATION_SETTINGS = [
    # 1 = device GPS only, no Google network location (3 = high accuracy, uses Google's services)
    ("secure", "location_mode", "1"),
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
        # --ai: Galaxy AI. Settings are only written if the key exists on the device.
        "ai_settings": [
            ("secure", "noti_intelligence_summarize_content", "0"),   # notification summaries
            ("secure", "noti_intelligence_priority_content", "0"),    # AI notification priority
            ("system", "call_transcript", "0"),                       # call transcripts
            ("global", "translate_during_calls", "0"),                # live call translation
            ("system", "voip_translator_enable", "0"),                # translation in VoIP calls
        ],
        "ai_packages": [
            "com.samsung.android.aicore",          # Samsung AI Core (Galaxy AI)
            "com.samsung.android.smartsuggestions",  # Smart suggestions (reads notifications)
            "com.samsung.android.app.interpreter",   # Interpreter (live translation)
            "com.samsung.android.callassistant",     # Call Assist / Bixby Text Call
            "com.samsung.android.visionintelligence",  # Bixby Vision
            "com.samsung.android.bixbyvision.framework",
            "com.samsung.android.vision.model",      # vision AI models
            "com.samsung.android.bixby.agent",       # Bixby
            "com.samsung.android.bixby.wakeup",      # "Hi Bixby" voice wake-up
            "com.samsung.android.bixby.ondevice.*",  # Bixby on-device language packs
        ],
        # --cloud: settings only written if the key exists on the device
        "cloud_settings": [
            ("system", "mcf_continuity", "0"),                  # continue apps on other devices
            ("secure", "ltw_clipboard_sync_state", "0"),        # clipboard sync with Windows
            ("secure", "samsungflow_clipboard_sync_state", "0"),  # clipboard sync via Samsung Flow
        ],
        "cloud_packages": [
            "com.samsung.android.scloud",          # Samsung Cloud (backup/sync)
            "com.microsoft.skydrive",              # OneDrive (preinstalled on Samsung)
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
# Short labels shown next to package names in the output (details: README).
# Keys ending in "." match every package with that prefix.
PACKAGE_LABELS = {
    "com.google.android.safetycore": "SafetyCore: scans content on the device",
    "com.google.android.feedback": "Google Feedback: crash reports to Google",
    "com.google.mainline.telemetry": "Google system update telemetry",
    "com.google.android.partnersetup": "Google Partner Setup: partner IDs",
    "com.facebook.services": "Facebook Services: background data",
    "com.facebook.system": "Facebook App Installer",
    "com.facebook.appmanager": "Facebook App Manager",
    "com.samsung.android.rubin.app": "Customization Service: usage profiling",
    "com.samsung.android.dqagent": "Device Quality Agent: telemetry",
    "com.samsung.android.app.spage": "Samsung Free: news/ads feed",
    "com.samsung.android.mapsagent": "Samsung location agent",
    "com.aura.oobe.samsung": "AppCloud: installs promoted apps",
    "com.sec.android.diagmonagent": "DiagMonAgent: error reports to Samsung",
    "com.samsung.android.bixby.agent": "Bixby voice assistant",
    "com.microsoft.appmanager": "Link to Windows",
    "com.miui.analytics": "MIUI Analytics: telemetry",
    "com.miui.msa.global": "MIUI System Ads",
    "com.miui.daemon": "MiuiDaemon: usage statistics",
    "com.google.android.apps.bard": "Gemini",
    "com.google.android.aicore": "AICore: on-device Gemini Nano",
    "com.google.android.apps.aiwallpapers": "AI wallpapers",
    "com.google.android.as": "Android System Intelligence",
    "com.google.android.as.oss": "Private Compute Services",
    "com.samsung.android.aicore": "Samsung AI Core (Galaxy AI)",
    "com.samsung.android.smartsuggestions": "Smart suggestions: reads notifications",
    "com.samsung.android.app.interpreter": "Interpreter: live translation",
    "com.samsung.android.callassistant": "Call Assist / Text Call",
    "com.samsung.android.visionintelligence": "Bixby Vision",
    "com.samsung.android.bixbyvision.framework": "Bixby Vision framework",
    "com.samsung.android.vision.model": "on-device vision AI models",
    "com.samsung.android.bixby.wakeup": "\"Hi Bixby\" voice wake-up",
    "com.samsung.android.bixby.ondevice.": "Bixby language pack",
    "com.samsung.android.scloud": "Samsung Cloud",
    "com.microsoft.skydrive": "OneDrive",
    "com.google.android.contactkeys": "Android System Key Verifier",
    "com.samsung.android.app.omcagent": "carrier/region app installer",
}


def pkg_name(pkg: str) -> str:
    """Package name with its short label, e.g. 'com.x.y (What it is)'."""
    label = PACKAGE_LABELS.get(pkg) or next(
        (v for k, v in PACKAGE_LABELS.items() if k.endswith(".") and pkg.startswith(k)), None)
    return f"{pkg} ({label})" if label else pkg


# Manufacturer aliases that share a profile
VENDOR_ALIASES = {"redmi": "xiaomi", "poco": "xiaomi"}

# For --restore: settings/packages changed by earlier versions or options of the
# script, in addition to the lists above.
EXTRA_MANAGED_SETTINGS = [
    ("system", "min_refresh_rate"),
    ("system", "screen_off_timeout"),
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
# Console colors
# ---------------------------------------------------------------------------

# ANSI color per status tag at the start of a line
TAG_COLORS = {
    "OK": "32",                                   # green
    "DRY": "33",                                  # yellow
    "SET": "36", "DEL": "36", "DIS": "36",        # cyan: something was changed
    "ADD": "36", "ENA": "36",
    "WARN": "38;5;208",                           # orange
    "ERR": "1;31", "ERROR": "1;31",               # bold red
    "SKIP": "90", "INFO": "90",                   # gray
}
TAG_RE = re.compile(r"^(\s*)\[([A-Z]+)\]")
SECTION_RE_OUT = re.compile(r"^--- .+ ---$")


def enable_windows_ansi() -> bool:
    """Turn on ANSI escape code support in the Windows console (no-op elsewhere)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


class ColorStream:
    """Wraps stdout and colors the status tag and section headings of each line."""

    def __init__(self, stream):
        self._stream = stream
        self._line_start = True

    def write(self, text: str) -> int:
        for part in text.splitlines(keepends=True):
            if self._line_start:
                part = self._colorize(part)
            self._stream.write(part)
            self._line_start = part.endswith("\n")
        return len(text)

    @staticmethod
    def _colorize(line: str) -> str:
        body = line.rstrip("\n")
        newline = line[len(body):]
        if SECTION_RE_OUT.match(body):
            return f"\033[1m{body}\033[0m{newline}"
        m = TAG_RE.match(body)
        if m and m.group(2) in TAG_COLORS:
            indent, tag, rest = m.group(1), body[len(m.group(1)):m.end()], body[m.end():]
            return f"{indent}\033[{TAG_COLORS[m.group(2)]}m{tag}\033[0m{rest}{newline}"
        return line

    def __getattr__(self, name):
        return getattr(self._stream, name)


def setup_colors(disabled: bool) -> None:
    """Color the output if it goes to a terminal and colors aren't disabled."""
    if disabled or os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return
    if enable_windows_ansi():
        sys.stdout = ColorStream(sys.stdout)


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


def is_network_serial(serial: str) -> bool:
    """True for an IP:PORT address or a Wireless debugging (mDNS) name."""
    return ":" in serial or "._adb-tls-connect" in serial


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
        try:
            choice = input("Select device (number): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[ERROR] No device chosen. Use -s SERIAL to pick one.")
            sys.exit(1)
        if choice.isdigit() and 1 <= int(choice) <= len(ready):
            return ready[int(choice) - 1][0]


def pair_device(hostport: str, pairing_code: str | None) -> None:
    """Pair with a device via Android 11+ Wireless debugging (adb pair)."""
    if not pairing_code:
        if not sys.stdin.isatty():
            print("[ERROR] --pair needs --pair-code when there is no interactive terminal.")
            sys.exit(1)
        try:
            pairing_code = input("Pairing code (6 digits, shown under "
                                 "'Pair device with pairing code'): ")
        except (EOFError, KeyboardInterrupt):
            # Windows reports NUL as a terminal, so input can still end here
            print("\n[ERROR] No pairing code entered. Use --pair-code CODE.")
            sys.exit(1)
    code, out, err = run_adb(["pair", hostport, pairing_code.strip()])
    if code == 0 and "Successfully paired" in out:
        print(f"[OK]   Paired with {hostport}")
    else:
        print(f"[ERROR] Pairing with {hostport} failed: {err or out}")
        print("  Check that the IP/port and code match the 'Pair device with pairing code' "
              "dialog, which must stay open while pairing.")
        sys.exit(1)


def find_connect_endpoint(host: str) -> str | None:
    """Find the Wireless debugging connect address (IP:PORT) of a host via adb mDNS."""
    for _ in range(5):  # the service shows up a few seconds after pairing
        _, out, _ = run_adb(["mdns", "services"])
        for line in out.splitlines():
            if "_adb-tls-connect._tcp" in line:
                endpoint = line.split()[-1]
                if endpoint.split(":")[0] == host:
                    return endpoint
        time.sleep(1)
    return None


def connect_device(hostport: str) -> str:
    """Connect to a device over the network (adb connect); return its adb serial."""
    if ":" not in hostport:
        hostport += ":5555"
    _, out, err = run_adb(["connect", hostport])
    if "connected to" in out:  # also matches "already connected to"
        print(f"[OK]   Connected to {hostport} over the network")
        return hostport
    print(f"[ERROR] Could not connect to {hostport}: {err or out}")
    print("  Check that the phone is on the same network and network debugging is on "
          "(--tcpip over USB, or Wireless debugging + --pair).")
    sys.exit(1)


def switch_to_tcpip(port: int) -> str:
    """Switch the USB-connected device to network adb and connect to it; return new serial."""
    print("\n--- Switching adb to the network ---")
    _, out, _ = run_adb(["shell", "ip -f inet addr show wlan0"])
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
    if not m:
        print("[ERROR] The device has no Wi-Fi IP address. Connect it to Wi-Fi first.")
        sys.exit(1)
    ip = m.group(1)
    code, out, err = run_adb(["tcpip", str(port)])
    if code != 0:
        print(f"[ERROR] Could not switch adb to network mode: {err or out}")
        sys.exit(1)
    print(f"[SET]  adb listens on {ip}:{port} (until the next reboot)")
    time.sleep(2)  # adb restarts on the device
    serial = connect_device(f"{ip}:{port}")
    print(f"[INFO] You can unplug the USB cable now. Next time: --connect {ip}:{port}")
    return serial


def detect_device() -> dict[str, str]:
    """Read manufacturer, model and Android version of the device."""
    manufacturer = getprop("ro.product.manufacturer") or "unknown"
    info = {
        # Hardware serial, not the adb address: stays the same over USB and network
        "serial": getprop("ro.serialno") or SERIAL or "",
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
        elif last is not None and result[last] != "null":
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
    keys = {(ns, k) for ns, k, _ in
            COMMON_SETTINGS + BATTERY_SETTINGS + AI_SETTINGS + CLOUD_SETTINGS
            + LOCATION_SETTINGS}
    for profile in VENDOR_PROFILES.values():
        for group in ("settings", "ai_settings", "cloud_settings"):
            keys |= {(ns, k) for ns, k, _ in profile.get(group, [])}
    return keys | set(EXTRA_MANAGED_SETTINGS)


def managed_packages(installed: set[str] | None = None) -> set[str]:
    pkgs = set(COMMON_PACKAGES) | set(EXTRA_MANAGED_PACKAGES) | set(AI_PACKAGES)
    for profile in VENDOR_PROFILES.values():
        for group in ("packages", "optional_packages", "ai_packages", "cloud_packages"):
            pkgs |= set(profile.get(group, []))
    # Expand prefix patterns like "com.example.*" against the given package list
    return set(expand_packages(sorted(pkgs), installed or set()))


def expand_packages(packages: list[str], installed: set[str]) -> list[str]:
    """Replace patterns ending in ".*" by the matching installed packages; drop duplicates."""
    result: list[str] = []
    for pkg in packages:
        if pkg.endswith(".*"):
            matches = sorted(p for p in installed if p.startswith(pkg[:-1]))
            result += matches or [pkg]  # keep the pattern so it's reported as not installed
        else:
            result.append(pkg)
    return list(dict.fromkeys(result))


def short(value: str | None, limit: int = 70) -> str:
    if value is None:
        return "not set"
    value = value.replace("\n", "\\n")
    return value if len(value) <= limit else value[:limit] + "..."


def restore_settings(backup: dict, restore_all: bool, apply: bool) -> tuple[int, int]:
    """Restore settings, notification access and Google backup. Returns (changes, errors)."""
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

        keys.discard("enabled_notification_listeners")  # restored separately below
        keys.discard("backup_enabled")                   # restored via bmgr below
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

    # Notification access revoked with --revoke-notifications: grant it back via the
    # notification service (writing the setting alone isn't reliably picked up)
    old_listeners = [c for c in (backup["secure"].get("enabled_notification_listeners") or "")
                     .split(":") if "/" in c]
    current_listeners = notification_listeners()
    for comp in old_listeners:
        if comp in current_listeners:
            continue
        changed += 1
        desc = f"{comp.split('/')[0]}: notification access"
        if not apply:
            print(f"[DRY]  {desc} would be granted again")
            continue
        ok, msg = set_listener_access(comp, allow=True)
        if ok:
            print(f"[SET]  {desc} granted again")
        else:
            failed += 1
            print(f"[WARN] {desc} could not be granted: {msg}")

    # Google backup turned off with --cloud: turn it on again via the backup manager
    if backup["secure"].get("backup_enabled") == "1" and not google_backup_enabled():
        changed += 1
        if not apply:
            print("[DRY]  Google backup would be turned on again")
        elif set_google_backup(True):
            print("[SET]  Google backup turned on again")
        else:
            failed += 1
            print("[WARN] Google backup could not be turned on again")

    if changed == 0:
        print("[OK]   All settings already match the backup.")

    return changed, failed


def restore_packages(backup_pkgs, restore_all: bool, apply: bool) -> tuple[int, int]:
    """Reinstall or re-enable packages from the backup. Returns (changes, errors)."""
    failed = 0
    print("\n--- Restoring packages ---")
    installed = list_packages()
    on_system = list_packages("-u")      # incl. system packages uninstalled for user 0
    disabled = list_packages("-d")

    if backup_pkgs is None:
        print("[INFO] Backup has no package list (older version); "
              "checking the packages managed by the script.")
        candidates = managed_packages(on_system)
        was_disabled: set[str] = set()
    else:
        candidates = backup_pkgs["installed"] if restore_all else (
            backup_pkgs["installed"] & managed_packages(backup_pkgs["installed"]))
        was_disabled = backup_pkgs["disabled"]

    pkg_changes = 0
    for pkg in sorted(candidates):
        if pkg not in installed:
            if pkg not in on_system:
                if backup_pkgs is not None:
                    print(f"[SKIP] {pkg_name(pkg)} no longer on the device (reinstall from Play Store)")
                continue
            pkg_changes += 1
            if not apply:
                print(f"[DRY]  {pkg_name(pkg)} would be restored")
                continue
            code, out, err = run_adb(["shell", "pm", "install-existing", "--user", "0", pkg])
            if code == 0 and "installed" in out.lower():
                print(f"[ADD]  {pkg_name(pkg)} restored")
            else:
                failed += 1
                print(f"[ERR]  {pkg_name(pkg)}: {err or out}")
        elif pkg in disabled and pkg not in was_disabled:
            pkg_changes += 1
            if not apply:
                print(f"[DRY]  {pkg_name(pkg)} would be enabled")
                continue
            code, out, err = run_adb(["shell", "pm", "enable", "--user", "0", pkg])
            if code == 0:
                print(f"[ENA]  {pkg_name(pkg)} enabled")
            else:
                failed += 1
                print(f"[ERR]  {pkg_name(pkg)}: {err or out}")

    if pkg_changes == 0:
        print("[OK]   No packages to restore.")

    return pkg_changes, failed


def restore_backup(path: str, apply: bool, restore_all: bool, force: bool,
                   device_serial: str) -> None:
    if not os.path.isfile(path):
        print(f"[ERROR] Backup file not found: {path}")
        sys.exit(1)
    device, backup, backup_pkgs = parse_backup(path)

    serial = device.get("serial", "")
    print(f"\n[INFO] Backup: {os.path.basename(path)}")
    print(f"[INFO] Backup device: {device.get('manufacturer', '?')} {device.get('model', '?')} "
          f"(serial {serial or '?'})")
    if serial and serial not in (device_serial, SERIAL) and not force:
        print(f"[ERROR] Backup is from a different device ({serial}, "
              f"connected: {device_serial}).")
        print("  Use --force to restore anyway.")
        sys.exit(1)
    mode = "ALL settings" if restore_all else "only settings managed by the script"
    print(f"[INFO] Scope: {mode}")
    changed, failed = restore_settings(backup, restore_all, apply)
    pkg_changes, pkg_failed = restore_packages(backup_pkgs, restore_all, apply)
    failed += pkg_failed

    if not apply:
        print(f"\nDry run: {changed} setting(s), {pkg_changes} package(s) would be "
              "restored. To apply: --run")
    else:
        print(f"\nRestore finished ({failed} error(s)). A reboot of the device is recommended.")


def apply_privacy_settings(settings: list[tuple[str, str, str]], apply: bool) -> None:
    print("\n--- Privacy settings ---")
    # One read per namespace instead of one adb call per setting (read-back below stays live)
    before = {}
    for ns in {s[0] for s in settings}:
        code, out, err = run_adb(["shell", "settings", "list", ns])
        if code != 0:
            print(f"[ERROR] Could not read current settings ({ns}): {err}")
            sys.exit(1)
        before[ns] = parse_settings_lines(out.splitlines())
    for namespace, key, value in settings:
        current = before[namespace].get(key)
        if current == value:
            print(f"[OK]   {namespace}/{key} = {value} (already set)")
            continue
        change = f"{namespace}/{key}: {current if current is not None else 'not set'} -> {value}"
        if not apply:
            print(f"[DRY]  {change}")
            continue
        code, _, err = run_adb(
            ["shell", f"settings put {namespace} {shlex.quote(key)} {shlex.quote(value)}"])
        if code != 0:
            print(f"[WARN] Could not set {namespace}/{key}: {err}")
            continue
        # Read back: some settings are silently reset by the system
        now = get_setting(namespace, key)
        if now == value:
            print(f"[SET]  {change}")
        else:
            print(f"[WARN] {namespace}/{key} was reset by the system "
                  f"(set {value}, now {now if now is not None else 'not set'})")


def notification_listeners() -> list[str]:
    """Components (package/class) that currently have notification access."""
    raw = get_setting("secure", "enabled_notification_listeners") or ""
    return [c for c in raw.split(":") if "/" in c]


def set_listener_access(component: str, allow: bool) -> tuple[bool, str]:
    """Grant or revoke notification access for one component and check the result."""
    action = "allow_listener" if allow else "disallow_listener"
    # Quote the component: names like "...Manager$ListenerService" contain "$",
    # which the device shell would otherwise expand to an empty variable
    code, out, err = run_adb(["shell", f"cmd notification {action} {shlex.quote(component)}"])
    if code != 0:
        return False, err or out
    for _ in range(5):  # the setting is updated asynchronously
        if (component in notification_listeners()) == allow:
            return True, ""
        time.sleep(1)
    return False, err or out


def revoke_notification_access(packages: list[str], apply: bool) -> None:
    print("\n--- Revoking notification access ---")
    current = notification_listeners()
    for pkg in packages:
        components = [c for c in current if c.split("/")[0] == pkg]
        if not components:
            print(f"[SKIP] {pkg} (has no notification access)")
            continue
        for comp in components:
            if not apply:
                print(f"[DRY]  {pkg}: notification access would be revoked")
                continue
            ok, msg = set_listener_access(comp, allow=False)
            if ok:
                print(f"[SET]  {pkg}: notification access revoked")
            else:
                print(f"[WARN] {pkg}: could not revoke notification access {msg}")


def ask_reboot() -> None:
    """Ask whether to reboot the device (default: no) and reboot it via adb."""
    if not sys.stdin.isatty():
        print("[INFO] No interactive terminal: not rebooting. Reboot the device by hand.")
        return
    try:
        answer = input("\nReboot the device now? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
        print()
    if answer not in ("y", "yes"):
        print("[INFO] Not rebooting. Reboot the device later so all changes take effect.")
        return
    code, out, err = run_adb(["reboot"])
    if code == 0:
        print("[OK]   Device is rebooting.")
    else:
        print(f"[WARN] Reboot failed: {err or out}. Reboot the device by hand.")


def disable_adb(over_network: bool) -> None:
    """Turn off Developer options, wireless and USB debugging. Must be the very last step."""
    print("\n--- Turning off USB debugging ---")
    labels = {"development_settings_enabled": "Developer options off",
              "adb_wifi_enabled": "Wireless debugging off",
              "adb_enabled": "USB debugging off"}
    # Over Wi-Fi, turning off wireless debugging cuts the connection, so USB debugging goes first
    order = ["development_settings_enabled", "adb_wifi_enabled", "adb_enabled"]
    if over_network:
        order = ["development_settings_enabled", "adb_enabled", "adb_wifi_enabled"]
    for key in order:
        # The connection can drop on the last step, so a failure there may just mean that
        code, _, err = run_adb(["shell", "settings", "put", "global", key, "0"])
        if code == 0:
            print(f"[SET]  {labels[key]}")
        else:
            print(f"[WARN] {labels[key]}: not confirmed ({err or 'the connection ended'})")


def existing_settings(settings: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Keep only settings whose key exists on the device (avoids creating stray keys)."""
    return [s for s in settings if get_setting(s[0], s[1]) is not None]


def google_backup_enabled() -> bool:
    _, out, _ = run_adb(["shell", "bmgr", "enabled"])
    return "enabled" in out and "disabled" not in out


def set_google_backup(enable: bool) -> bool:
    """Turn Google (Android) backup on/off via the backup manager and check the result."""
    run_adb(["shell", "bmgr", "enable", "true" if enable else "false"])
    return google_backup_enabled() == enable


def apply_google_backup_off(apply: bool) -> None:
    print("\n--- Google backup ---")
    if not google_backup_enabled():
        print("[OK]   Google backup is off (already set)")
    elif not apply:
        print("[DRY]  Google backup: on -> off")
    elif set_google_backup(False):
        print("[SET]  Google backup: on -> off")
    else:
        print("[WARN] Google backup could not be turned off")


def ai_recommendations(dev: dict[str, str], installed: set[str]) -> list[str]:
    """AI features that can't (or shouldn't) be changed via ADB. Only shown with --ai."""
    tips: list[str] = []
    samsung = dev["samsung"]

    assistant = (get_setting("secure", "assistant") or "").split("/")[0]
    if assistant:
        path = ("Settings > Apps > Choose default apps > Digital assistant app" if samsung
                else "Settings > Apps > Default apps > Digital assistant app")
        tips.append(
            f"Default digital assistant is {assistant} (Google Assistant/Gemini). "
            f"If AI assistants aren't wanted, set it to None: {path}."
        )
    if samsung:
        tips.append(
            "Galaxy AI: turn on 'Process data only on device' so no data goes to Samsung/Google "
            "AI servers, and turn off the features you don't use: Settings > Galaxy AI "
            "(older One UI: Settings > Advanced features > Advanced intelligence)."
        )
    if "com.google.android.googlequicksearchbox" in installed:
        path = ("Settings > Display > Navigation bar > Circle to Search" if samsung
                else "Settings > System > Navigation mode")
        tips.append(f"Circle to Search (Google AI search): turn it off if unused: {path}.")
    if (get_setting("secure", "default_input_method") or "").startswith(
            "com.google.android.inputmethod.latin"):
        tips.append(
            "Gboard is the keyboard. It has AI writing features (e.g. Smart Compose, "
            "proofreading); turn them off in the Gboard settings if they aren't wanted."
        )
    user_apps = [name for pkg, name in AI_USER_APPS.items() if pkg in installed]
    if user_apps:
        tips.append(
            f"AI apps installed by the user: {', '.join(user_apps)}. They aren't removed "
            "automatically; uninstall them if they aren't wanted."
        )
    return tips


def cloud_recommendations(dev: dict[str, str], installed: set[str]) -> list[str]:
    """Cloud features that can't (or shouldn't) be changed via ADB. Only shown with --cloud."""
    tips: list[str] = []
    samsung = dev["samsung"]

    if get_setting("global", "master_sync_status") == "1":
        _, out, _ = run_adb(["shell", "dumpsys", "account"])
        types = sorted(set(re.findall(r"Account \{name=[^,]*, type=([^}]+)\}", out)))
        path = ("Settings > Accounts and backup > Manage accounts > Auto sync data" if samsung
                else "Settings > Passwords & accounts > Automatically sync app data")
        accounts = f" ({len(types)} account type(s): {', '.join(types)})" if types else ""
        tips.append(
            f"Auto-sync is on{accounts}: contacts, calendars, mail etc. are synced to the cloud. "
            f"Turn it off if not needed (mail then no longer arrives automatically): {path}."
        )
    cloud_apps = [name for pkg, name in CLOUD_USER_APPS.items() if pkg in installed]
    if cloud_apps:
        tips.append(
            f"Cloud storage apps installed: {', '.join(cloud_apps)}. Check their backup/sync "
            "settings (e.g. Google Photos > Settings > Backup) or uninstall them if unwanted."
        )
    if samsung and "com.samsung.android.samsungpass" in installed:
        tips.append(
            "Samsung Pass is installed; it stores passwords and passkeys in the Samsung account "
            "cloud. If it isn't used: Settings > Security and privacy > Samsung Pass."
        )
    return tips


PRIVATE_DNS_RE = re.compile(
    r"PrivateDnsServerName: (\S+)(?: ValidatedPrivateDnsAddresses: \[([^\]]*)\])?")


def verify_private_dns(host: str, timeout: int = 15) -> None:
    """Check that Android actually reached the Private DNS provider on a connected network."""
    print("\n--- Checking Private DNS ---")
    if get_setting("global", "private_dns_mode") != "hostname" \
            or get_setting("global", "private_dns_specifier") != host:
        print(f"[SKIP] Private DNS is not set to {host} yet (use --run to set it)")
        return

    deadline = time.monotonic() + timeout
    while True:
        _, out, _ = run_adb(["shell", "dumpsys", "connectivity"])
        found = [(name, addrs) for name, addrs in PRIVATE_DNS_RE.findall(out) if name == host]
        validated = sorted({a.strip() for _, addrs in found for a in addrs.split(",") if a.strip()})
        if validated:
            print(f"[OK]   {host} is in use and validated ({', '.join(validated)})")
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(3)  # validation takes a few seconds after the setting changes

    if not found:
        print("[WARN] No connected network uses Private DNS right now. "
              "Connect to Wi-Fi or mobile data and run the script again to check.")
    else:
        print(f"[WARN] {host} could not be reached. In this mode DNS lookups fail, so the "
              "device may have no internet. Check the hostname, or restore a backup "
              "(--restore) to undo it.")


def remove_packages(packages: list[str], installed: set[str], disabled: set[str],
                    apply: bool) -> None:
    print("\n--- Removing packages (--user 0) ---")
    for pkg in packages:
        if pkg not in installed:
            print(f"[SKIP] {pkg_name(pkg)} - not installed")
            continue
        if pkg in disabled:
            # Disabled packages are still listed as installed; uninstalling was
            # already refused on an earlier run, so there is nothing left to do.
            print(f"[OK]   {pkg_name(pkg)} - already disabled")
            continue
        if not apply:
            print(f"[DRY]  {pkg_name(pkg)} would be removed/disabled")
            continue

        code, out, err = run_adb(["shell", "pm", "uninstall", "-k", "--user", "0", pkg])
        if code == 0 and "Success" in out:
            print(f"[DEL]  {pkg_name(pkg)} removed")
            installed.discard(pkg)
        else:
            # Fallback: some system packages can only be disabled, not uninstalled
            code_dis, _, err_dis = run_adb(["shell", "pm", "disable-user", "--user", "0", pkg])
            if code_dis == 0:
                print(f"[DIS]  {pkg_name(pkg)} disabled - uninstall not allowed")
                disabled.add(pkg)
            else:
                print(f"[ERR]  {pkg_name(pkg)} failed: {err or out or err_dis}")


def manual_recommendations(dev: dict[str, str], installed: set[str],
                           private_dns_set: bool, timeout_set: bool = False,
                           adb_off: bool = False) -> list[str]:
    """Check things that can't (or shouldn't) be changed via ADB."""
    tips: list[str] = []
    samsung = dev["samsung"]

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
    if not timeout_set and timeout and timeout.isdigit() and int(timeout) > 120000:
        tips.append(
            f"Screen timeout is {int(timeout) // 60000} minutes; the device stays unlocked "
            "and readable that long. Set it to 2 minutes or less with --screen-timeout 60, "
            "or: Settings > Display > Screen timeout."
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
            "you no longer need with --revoke-notifications PKG,PKG or in "
            "Settings > Notifications > Notification access:\n"
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
        path = ("Settings > Connections > More connection settings > Private DNS" if samsung
                else "Settings > Network & internet > Private DNS")
        tips.append(
            "No fixed Private DNS provider. Set one with --private-dns dns.quad9.net "
            f"or manually: {path}."
        )

    # USB debugging (necessarily on while the script runs)
    if not adb_off:
        tips.append(
            "After debloating, turn USB debugging and Developer options off again with "
            "--disable-adb, or: Settings > Developer options > switch at the top."
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
    samsung = dev["samsung"]

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
    parser.add_argument("--connect", metavar="HOST[:PORT]",
                        help="connect to the device over the network (adb connect); "
                             "default port 5555")
    parser.add_argument("--pair", metavar="HOST:PORT",
                        help="pair via Android 11+ Wireless debugging first (adb pair), "
                             "then connect")
    parser.add_argument("--pair-code", metavar="CODE",
                        help="6-digit pairing code for --pair (asked for if missing)")
    parser.add_argument("--tcpip", nargs="?", const=5555, type=int, metavar="PORT",
                        help="switch the USB-connected device to network adb (default port "
                             "5555) and continue over Wi-Fi")
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
    parser.add_argument("--ai", "--AI", dest="ai", action="store_true",
                        help="check AI features (assistant, Gemini, Galaxy AI ...) and, with "
                             "--run, turn them off / remove them")
    parser.add_argument("--cloud", "--CLOUD", dest="cloud", action="store_true",
                        help="check cloud features (backup, sync, cloud apps) and, with --run, "
                             "turn them off / remove them")
    parser.add_argument("--location-device-only", action="store_true",
                        help="location from the device's GPS only, no Google network location "
                             "(less accurate)")
    parser.add_argument("--screen-timeout", type=int, metavar="SECONDS",
                        help="set the screen timeout, e.g. 60")
    parser.add_argument("--revoke-notifications", metavar="PKG[,PKG...]",
                        help="revoke notification access for these apps "
                             "(package names, comma-separated)")
    parser.add_argument("--disable-adb", action="store_true",
                        help="with --run: turn off USB debugging and Developer options as the "
                             "last step (ends the adb connection)")
    parser.add_argument("--no-color", action="store_true",
                        help="disable colored output (also off when NO_COLOR is set or "
                             "output is redirected)")
    parser.add_argument("--restore", metavar="FILE",
                        help="restore settings/packages from a backup file")
    parser.add_argument("--all", action="store_true",
                        help="with --restore: restore all differing settings and packages, "
                             "not only the ones managed by the script")
    parser.add_argument("--force", action="store_true",
                        help="with --restore: allow a backup from a different device")
    args = parser.parse_args()
    setup_colors(args.no_color)
    if args.screen_timeout is not None and args.screen_timeout < 10:
        parser.error("--screen-timeout must be at least 10 seconds")
    apply = args.run

    if args.pair:
        pair_device(args.pair, args.pair_code)
        if not args.connect:
            host = args.pair.rsplit(":", 1)[0]
            args.connect = find_connect_endpoint(host)
            if not args.connect:
                print(f"[ERROR] Could not find the connect address of {host}. Add "
                      "--connect IP:PORT (shown at the top of the Wireless debugging screen).")
                sys.exit(1)
    if args.connect:
        args.serial = connect_device(args.connect)

    SERIAL = select_device(args.serial)
    if args.tcpip:
        if is_network_serial(SERIAL):
            print("[INFO] Already connected over the network, --tcpip is not needed.")
        else:
            SERIAL = switch_to_tcpip(args.tcpip)
    dev = detect_device()

    label = dev["name"] or dev["model"]
    print(f"[OK] Device: {dev['manufacturer']} {label} ({dev['model']}), "
          f"Android {dev['android']} (SDK {dev['sdk']}), serial {SERIAL}")
    if not apply:
        print("[INFO] DRY RUN: nothing will be changed. Use --run to apply.")

    backup_settings(dev)

    if args.restore:
        restore_backup(args.restore, apply, args.all, args.force, dev["serial"])
        if apply:
            ask_reboot()
        return

    installed = list_packages()

    empty_profile = {"settings": [], "packages": [], "optional_packages": []}
    profile = VENDOR_PROFILES.get(dev["profile"])
    dev["custom_rom"] = bool(
        profile and not any(p in installed for p in profile.get("marker_packages", []))
    )
    dev["samsung"] = dev["profile"] == "samsung" and not dev["custom_rom"]
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
    if args.screen_timeout:
        settings.append(("system", "screen_off_timeout", str(args.screen_timeout * 1000)))
    if args.ai:
        settings += AI_SETTINGS + existing_settings(profile.get("ai_settings", []))
    if args.cloud:
        settings += CLOUD_SETTINGS + existing_settings(profile.get("cloud_settings", []))
    if args.location_device_only:
        settings += LOCATION_SETTINGS
    if args.battery:
        settings += BATTERY_SETTINGS
        fix = refresh_rate_fix()
        if fix:
            settings.append(fix)

    packages = COMMON_PACKAGES + profile["packages"]
    if args.include_optional:
        packages += profile.get("optional_packages", [])
    if args.ai:
        packages += AI_PACKAGES + profile.get("ai_packages", [])
    if args.cloud:
        packages += profile.get("cloud_packages", [])
    packages = expand_packages(packages, installed)

    disabled = list_packages("-d")

    apply_privacy_settings(settings, apply)
    if args.cloud:
        apply_google_backup_off(apply)
    if args.private_dns:
        verify_private_dns(args.private_dns)
    if args.revoke_notifications:
        revoke_notification_access(
            [p.strip() for p in args.revoke_notifications.split(",") if p.strip()], apply)
    remove_packages(packages, installed, disabled, apply)

    print("\n--- Manual recommendations ---")
    # Disabled packages don't run, so they don't need a "still installed" tip
    tips = manual_recommendations(dev, installed - disabled, bool(args.private_dns),
                                  timeout_set=bool(args.screen_timeout),
                                  adb_off=args.disable_adb)
    for i, tip in enumerate(tips, 1):
        print(f"  {i}. {tip}")

    print("\n--- Battery recommendations ---")
    battery_tips = battery_recommendations(dev, args.battery)
    if not battery_tips:
        print("  No battery issues found.")
    for i, tip in enumerate(battery_tips, 1):
        print(f"  {i}. {tip}")

    active = installed - disabled
    for flag, title, func in ((args.ai, "AI", ai_recommendations),
                              (args.cloud, "Cloud", cloud_recommendations)):
        if not flag:
            continue
        print(f"\n--- {title} recommendations ---")
        extra_tips = func(dev, active)
        if not extra_tips:
            print(f"  No further {title} features found.")
        for i, tip in enumerate(extra_tips, 1):
            print(f"  {i}. {tip}")

    if apply:
        print("\nDone. A reboot of the device is recommended.")
        if args.disable_adb:
            disable_adb(is_network_serial(SERIAL))
            print("[INFO] adb can't reach the device anymore, so reboot it by hand.")
        else:
            ask_reboot()
    else:
        if args.disable_adb:
            print("\n[DRY]  USB debugging and Developer options would be turned off at the end")
        print("\nDry run finished, nothing was changed. To apply: --run")


if __name__ == "__main__":
    main()

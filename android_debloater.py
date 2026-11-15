#!/usr/bin/env python3
"""Generischer Android Privacy-Debloater via ADB.

Erkennt Hersteller/Modell des verbundenen Geräts und wendet allgemeine
(Google/AOSP) sowie herstellerspezifische Einstellungen und Paketlisten an.
Standardmäßig läuft das Skript als Dry-Run und verändert nichts.

Aufruf:
    python android_debloater.py                       # Dry-Run: nur anzeigen
    python android_debloater.py --run                 # Änderungen wirklich anwenden
    python android_debloater.py -s SERIAL             # bestimmtes Gerät
    python android_debloater.py --run --private-dns dns.quad9.net
    python android_debloater.py --run --disable-agps --include-optional

Wiederherstellen aus einem Backup (ebenfalls standardmäßig Dry-Run):
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
# Konfiguration
# ---------------------------------------------------------------------------

# Privacy-Einstellungen (Namespace, Key, Wert), gültig für alle Geräte
COMMON_SETTINGS = [
    # Google Nutzungs- & Diagnosedaten deaktivieren
    ("global", "multi_cb", "0"),
    # WLAN- und Bluetooth-Suche im Hintergrund (auch wenn deaktiviert) abschalten
    ("global", "wifi_scan_always_enabled", "0"),
    ("global", "ble_scan_always_enabled", "0"),
    # Keine Nachrichteninhalte auf dem Sperrbildschirm
    ("secure", "lock_screen_allow_private_notifications", "0"),
    # Play Protect: unbekannte APKs nicht an Google hochladen (Scan bleibt aktiv)
    ("global", "upload_apk_enable", "0"),
    # Keine WLAN-Bewertung/Empfehlungen durch Google Play-Dienste
    ("global", "network_recommendations_enabled", "0"),
    # Hinweis anzeigen, wenn eine App die Zwischenablage liest
    ("secure", "clipboard_show_access_notifications", "1"),
    # WLAN nicht automatisch in der Nähe gespeicherter Netze einschalten (nutzt Standortscans)
    ("global", "wifi_wakeup_enabled", "0"),
    # Kein "Feedback senden" an Google im App-Absturz-Dialog
    ("secure", "send_action_app_error", "0"),
    ("global", "send_action_app_error", "0"),
    # Fehlerbericht-Verknüpfung im Ein/Aus-Menü abschalten (Berichte enthalten viele Daten)
    ("global", "bug_report", "0"),
]

# Zu entfernende Pakete (--user 0), gültig für alle Geräte
COMMON_PACKAGES = [
    # Google SafetyCore & Key Verifier (Client-Side Scanning / Kontakt-Verifizierung)
    "com.google.android.safetycore",
    # Google Telemetrie / Feedback / Partner-Setup
    "com.google.android.feedback",
    "com.google.mainline.telemetry",
    "com.google.android.partnersetup",
    # Vorinstallierte Facebook-Dienste (laufen auch ohne Facebook-App, u.a. Samsung/Xiaomi)
    "com.facebook.services",
    "com.facebook.system",
    "com.facebook.appmanager",
]

# Herstellerspezifische Ergänzungen. Schlüssel = ro.product.manufacturer (klein).
# "optional_packages" werden nur mit --include-optional entfernt.
# "marker_packages": Ist keines davon installiert, läuft vermutlich eine Custom-ROM
# (z.B. LineageOS) und das Herstellerprofil wird übersprungen.
VENDOR_PROFILES = {
    "samsung": {
        "marker_packages": ["com.sec.android.app.launcher", "com.samsung.android.honeyboard"],
        "settings": [
            # Samsung Diagnosedaten deaktivieren
            ("system", "samsung_errorlog_agree", "0"),
            # Dauerhafte Suche nach Geräten in der Nähe abschalten
            ("system", "nearby_scanning_enabled", "0"),
        ],
        "packages": [
            "com.samsung.android.rubin.app",       # Customization Service (Nutzerprofilierung)
            "com.samsung.android.dqagent",         # Device Quality Agent (Telemetrie)
            "com.samsung.android.app.spage",       # Samsung Free / News-Feed
            "com.samsung.android.mapsagent",       # Samsung App-Empfehlungen / Tracking
            "com.aura.oobe.samsung",               # AppCloud (installiert ungefragt Bloatware)
            "com.sec.android.diagmonagent",        # DiagMonAgent (Fehlerberichte an Samsung)
        ],
        "optional_packages": [
            "com.samsung.android.bixby.agent",     # Bixby (falls nur Google Assistant genutzt)
            "com.microsoft.appmanager",            # Link zu Windows (Zugriff auf SMS/Kontakte/Mikro)
        ],
    },
    "xiaomi": {
        "marker_packages": ["com.miui.home", "com.miui.securitycenter"],
        "settings": [],
        "packages": [
            "com.miui.analytics",                  # MIUI Analytics (Telemetrie)
            "com.miui.msa.global",                 # MIUI System Ads
            "com.miui.daemon",                     # MiuiDaemon (Nutzungsstatistiken)
        ],
        "optional_packages": [],
    },
}
# Alias-Hersteller, die dasselbe Profil nutzen
VENDOR_ALIASES = {"redmi": "xiaomi", "poco": "xiaomi"}

# Für --restore: Einstellungen/Pakete, die frühere Versionen oder Optionen des
# Skripts verändert haben, zusätzlich zu den Listen oben.
EXTRA_MANAGED_SETTINGS = [
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
# ADB-Hilfsfunktionen
# ---------------------------------------------------------------------------


def find_adb() -> str:
    """Sucht adb: Umgebungsvariable ADB, PATH, dann übliche SDK-Pfade."""
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

    print("[FEHLER] adb wurde nicht gefunden.")
    print("  Installiere die Android Platform-Tools oder setze die Umgebungsvariable ADB,")
    print("  z.B.:  set ADB=C:\\pfad\\zu\\platform-tools\\adb.exe")
    sys.exit(1)


ADB = find_adb()
SERIAL: str | None = None  # wird nach der Geräteauswahl gesetzt
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def run_adb(args: list[str]) -> tuple[int, str, str]:
    """Führt einen ADB-Befehl (für das gewählte Gerät) aus: (Returncode, stdout, stderr)."""
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
    """Liest eine Einstellung; None, wenn sie nicht existiert."""
    code, out, _ = run_adb(["shell", "settings", "get", namespace, key])
    if code != 0 or out in ("", "null"):
        return None
    return out


def list_packages(*flags: str) -> set[str]:
    """pm list packages [flags]; z.B. -u (inkl. für den Nutzer deinstallierte), -d (deaktiviert)."""
    code, out, err = run_adb(["shell", "pm", "list", "packages", *flags])
    if code != 0 or (not out and not flags):
        print(f"[FEHLER] Paketliste konnte nicht gelesen werden: {err}")
        sys.exit(1)
    return {line.replace("package:", "").strip() for line in out.splitlines() if line.strip()}


def get_installed_packages() -> set[str]:
    return list_packages()


# ---------------------------------------------------------------------------
# Geräteauswahl & -erkennung
# ---------------------------------------------------------------------------


def select_device(requested: str | None) -> str:
    """Wählt ein autorisiertes Gerät aus und gibt dessen Seriennummer zurück."""
    print(f"[INFO] Verwende adb: {ADB}")
    code, out, err = run_adb(["devices", "-l"])
    if code != 0:
        print(f"[FEHLER] adb konnte nicht gestartet werden: {err}")
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
        hint = " (Debugging-Dialog auf dem Gerät bestätigen?)" if state == "unauthorized" else ""
        print(f"[INFO] Ignoriere {serial}: Status '{state}'{hint}")

    if requested:
        if any(s == requested for s, _, _ in ready):
            return requested
        print(f"[FEHLER] Gerät '{requested}' ist nicht verbunden/autorisiert.")
        sys.exit(1)

    if not ready:
        print("[FEHLER] Kein autorisiertes Android-Gerät gefunden. USB-Debugging aktiv?")
        sys.exit(1)
    if len(ready) == 1:
        return ready[0][0]

    print("\nMehrere Geräte verbunden:")
    for i, (serial, _, info) in enumerate(ready, 1):
        print(f"  [{i}] {serial}  {info.get('model', '')}")
    while True:
        choice = input("Gerät wählen (Nummer): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(ready):
            return ready[int(choice) - 1][0]


def detect_device() -> dict[str, str]:
    """Liest Hersteller, Modell und Android-Version des Geräts aus."""
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
# Aktionen
# ---------------------------------------------------------------------------


def backup_settings(dev: dict[str, str]) -> None:
    """Sichert alle Settings (nur lesend, läuft auch im Dry-Run)."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(LOG_DIR, exist_ok=True)
    filename = os.path.join(
        LOG_DIR,
        f"settings_backup_{safe_filename_part(dev['manufacturer'])}"
        f"_{safe_filename_part(dev['model'])}_{timestamp}.txt",
    )
    with open(filename, "w", encoding="utf-8") as f:
        f.write("=== GERÄT ===\n")
        for key, value in dev.items():
            f.write(f"{key}: {value}\n")
        f.write("\n")
        for namespace in ("system", "secure", "global"):
            code, out, err = run_adb(["shell", "settings", "list", namespace])
            if code != 0:
                print(f"[FEHLER] Backup von '{namespace}' fehlgeschlagen: {err}")
                sys.exit(1)
            f.write(f"=== NAMESPACE: {namespace} ===\n{out}\n\n")
        f.write("=== PACKAGES: installed ===\n")
        f.write("\n".join(sorted(list_packages())) + "\n\n")
        f.write("=== PACKAGES: disabled ===\n")
        f.write("\n".join(sorted(list_packages("-d"))) + "\n\n")
    print(f"[OK] Backup der aktuellen Settings gespeichert in: {filename}")


# ---------------------------------------------------------------------------
# Wiederherstellung
# ---------------------------------------------------------------------------

SECTION_RE = re.compile(r"^=== (.+) ===$")
SETTING_RE = re.compile(r"^([A-Za-z0-9_.:\-]+)=(.*)$")


def parse_settings_lines(lines: list[str]) -> dict[str, str | None]:
    """Parst 'key=value'-Zeilen; Zeilen ohne Key gehören zum Wert davor (mehrzeilig)."""
    result: dict[str, str | None] = {}
    last = None
    for line in lines:
        m = SETTING_RE.match(line)
        if m:
            last = m.group(1)
            result[last] = m.group(2)
        elif last is not None and result[last] is not None:
            result[last] += "\n" + line
    # "null" bedeutet: Einstellung existiert, hat aber keinen Wert
    return {k: (None if v == "null" else v) for k, v in result.items()}


def parse_backup(path: str):
    """Liest eine Backup-Datei: (Geräteinfo, {namespace: settings}, Pakete oder None)."""
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
    for line in sections.get("GERÄT", []):
        if ": " in line:
            k, v = line.split(": ", 1)
            device[k] = v

    settings = {}
    for ns in ("system", "secure", "global"):
        body = sections.get(f"NAMESPACE: {ns}", [])
        while body and not body[-1]:  # Leerzeilen zwischen Abschnitten entfernen
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
    keys = {(ns, k) for ns, k, _ in COMMON_SETTINGS}
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
        return "nicht gesetzt"
    value = value.replace("\n", "\\n")
    return value if len(value) <= limit else value[:limit] + "..."


def restore_backup(path: str, apply: bool, restore_all: bool, force: bool) -> None:
    if not os.path.isfile(path):
        print(f"[FEHLER] Backup-Datei nicht gefunden: {path}")
        sys.exit(1)
    device, backup, backup_pkgs = parse_backup(path)

    serial = device.get("serial", "")
    print(f"\n[INFO] Backup: {os.path.basename(path)}")
    print(f"[INFO] Backup-Gerät: {device.get('manufacturer', '?')} {device.get('model', '?')} "
          f"(Serial {serial or '?'})")
    if serial and serial != SERIAL and not force:
        print(f"[FEHLER] Backup stammt von einem anderen Gerät ({serial}, verbunden: {SERIAL}).")
        print("  Mit --force trotzdem wiederherstellen.")
        sys.exit(1)
    mode = "ALLE Einstellungen" if restore_all else "nur vom Skript verwaltete Einstellungen"
    print(f"[INFO] Umfang: {mode}")

    # --- Einstellungen ---
    print("\n--- Einstellungen wiederherstellen ---")
    managed = managed_settings()
    changed = failed = 0
    for ns in ("system", "secure", "global"):
        code, out, err = run_adb(["shell", "settings", "list", ns])
        if code != 0:
            print(f"[FEHLER] Aktuelle Einstellungen ({ns}) nicht lesbar: {err}")
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
                # Nur Keys löschen, die das Skript selbst angelegt haben könnte
                if now is None or (ns, key) not in managed:
                    continue
                desc = f"{ns}/{key}: {short(now)} -> (löschen, war nicht gesetzt)"
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
                print(f"[WARN] {ns}/{key} nicht wiederherstellbar: {err}")

    if changed == 0:
        print("[OK]   Alle Einstellungen entsprechen bereits dem Backup.")

    # --- Pakete ---
    print("\n--- Pakete wiederherstellen ---")
    installed = list_packages()
    on_system = list_packages("-u")      # inkl. für Nutzer 0 deinstallierter Systempakete
    disabled = list_packages("-d")

    if backup_pkgs is None:
        print("[INFO] Backup enthält keine Paketliste (ältere Version); "
              "prüfe die vom Skript verwalteten Pakete.")
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
                    print(f"[SKIP] {pkg} nicht mehr auf dem Gerät (aus Play Store neu installieren)")
                continue
            pkg_changes += 1
            if not apply:
                print(f"[DRY]  {pkg} würde wiederhergestellt")
                continue
            code, out, err = run_adb(["shell", "pm", "install-existing", "--user", "0", pkg])
            if code == 0 and "installed" in out.lower():
                print(f"[ADD]  {pkg} wiederhergestellt")
            else:
                failed += 1
                print(f"[ERR]  {pkg}: {err or out}")
        elif pkg in disabled and pkg not in was_disabled:
            pkg_changes += 1
            if not apply:
                print(f"[DRY]  {pkg} würde aktiviert")
                continue
            code, out, err = run_adb(["shell", "pm", "enable", "--user", "0", pkg])
            if code == 0:
                print(f"[ENA]  {pkg} aktiviert")
            else:
                failed += 1
                print(f"[ERR]  {pkg}: {err or out}")

    if pkg_changes == 0:
        print("[OK]   Keine Pakete wiederherzustellen.")

    if not apply:
        print(f"\nDry-Run: {changed} Einstellung(en), {pkg_changes} Paket(e) würden "
              "wiederhergestellt. Zum Anwenden: --run")
    else:
        print(f"\nWiederherstellung beendet ({failed} Fehler). Neustart empfohlen.")


def apply_privacy_settings(settings: list[tuple[str, str, str]], apply: bool) -> None:
    print("\n--- Privacy-Einstellungen ---")
    for namespace, key, value in settings:
        current = get_setting(namespace, key)
        if current == value:
            print(f"[OK]   {namespace}/{key} = {value} (bereits gesetzt)")
            continue
        change = f"{namespace}/{key}: {current if current is not None else 'nicht gesetzt'} -> {value}"
        if not apply:
            print(f"[DRY]  {change}")
            continue
        code, _, err = run_adb(["shell", "settings", "put", namespace, key, value])
        if code == 0:
            print(f"[SET]  {change}")
        else:
            print(f"[WARN] Konnte {namespace}/{key} nicht setzen: {err}")


def remove_packages(packages: list[str], installed: set[str], apply: bool) -> None:
    print("\n--- Pakete entfernen (--user 0) ---")
    for pkg in packages:
        if pkg not in installed:
            print(f"[SKIP] {pkg} (nicht installiert)")
            continue
        if not apply:
            print(f"[DRY]  {pkg} würde entfernt")
            continue

        code, out, err = run_adb(["shell", "pm", "uninstall", "-k", "--user", "0", pkg])
        if code == 0 and "Success" in out:
            print(f"[DEL]  {pkg} erfolgreich entfernt")
            installed.discard(pkg)
        else:
            # Fallback: Manche Systempakete lassen sich nur deaktivieren statt deinstallieren
            code_dis, _, err_dis = run_adb(["shell", "pm", "disable-user", "--user", "0", pkg])
            if code_dis == 0:
                print(f"[DIS]  {pkg} deaktiviert (Uninstall nicht erlaubt)")
            else:
                print(f"[ERR]  {pkg} fehlgeschlagen: {err or out or err_dis}")


def manual_recommendations(dev: dict[str, str], installed: set[str],
                           private_dns_set: bool) -> list[str]:
    """Prüft Punkte, die sich nicht (sinnvoll) per ADB setzen lassen."""
    tips: list[str] = []
    samsung = dev["profile"] == "samsung" and not dev["custom_rom"]

    # Sicherheitspatch-Stand
    try:
        patch = datetime.strptime(dev["security_patch"], "%Y-%m-%d")
        age_days = (datetime.now() - patch).days
        if age_days > 90:
            tips.append(
                f"Sicherheitspatch vom {dev['security_patch']} ist {age_days} Tage alt. "
                "Updates installieren; erhält das Gerät keine mehr, nicht mehr für "
                "sensible Daten (Banking, Passwörter) nutzen."
            )
    except ValueError:
        tips.append("Sicherheitspatch-Stand konnte nicht ermittelt werden; manuell prüfen.")

    # Entsperrter Bootloader
    if dev["verified_boot"] == "orange":
        tips.append(
            "Bootloader ist entsperrt (Verified Boot: orange). Wer das Gerät in die Hände "
            "bekommt, kann Software aufspielen. Starke PIN/Passwort verwenden und "
            "das Gerät nicht unbeaufsichtigt lassen."
        )

    # Bildschirm-Timeout
    timeout = get_setting("system", "screen_off_timeout")
    if timeout and timeout.isdigit() and int(timeout) > 120000:
        tips.append(
            f"Bildschirm-Timeout ist {int(timeout) // 60000} Minuten; so lange bleibt das "
            "Gerät entsperrt und lesbar. Auf 2 Minuten oder weniger stellen: "
            "Einstellungen > Display > Bildschirm-Timeout."
        )

    # Gerätename wird per Bluetooth/Hotspot/Quick Share in der Umgebung sichtbar
    names = {n for n in (get_setting("global", "device_name"),
                         get_setting("secure", "bluetooth_name")) if n}
    if names:
        shown = ", ".join(f'"{n}"' for n in sorted(names))
        tips.append(
            f"Gerätename {shown} ist für Geräte in der Nähe sichtbar (Bluetooth, Hotspot, "
            "Quick Share). Falls er deinen Namen enthält, einen neutralen Namen wählen: "
            "Einstellungen > Telefoninfo > Bearbeiten."
        )

    # Apps mit Zugriff auf alle Benachrichtigungen
    listeners_raw = get_setting("secure", "enabled_notification_listeners") or ""
    listeners = sorted({c.split("/")[0] for c in listeners_raw.split(":") if "/" in c})
    if listeners:
        tips.append(
            f"{len(listeners)} App(s) können ALLE Benachrichtigungen lesen. Nicht mehr "
            "benötigte entziehen (Einstellungen > Benachrichtigungen > Zugriff auf "
            "Benachrichtigungen):\n" + "\n".join(f"        - {p}" for p in listeners)
        )

    # Werbe-ID
    tips.append(
        "Werbe-ID löschen: Einstellungen > Google > Alle Dienste > Werbung > Werbe-ID löschen."
    )

    # Gesichtserkennung
    if "facelock" in (get_setting("secure", "lockscreen.options") or ""):
        tips.append(
            "Gesichtserkennung ist aktiv. Sie arbeitet nur mit der Kamera (2D) und ist "
            "unsicherer als Fingerabdruck + PIN. Ggf. deaktivieren: "
            "Einstellungen > Sicherheit > Biometrie > Gesichtserkennung."
        )

    # Samsung Find My Mobile / SmartThings Find: Offline-Standort verschlüsseln
    if (samsung and get_setting("secure", "fmm_offline_find_support") == "Y"
            and get_setting("secure", "offline_finding_e2e") == "0"):
        tips.append(
            "Offline-Finden ist aktiv, aber der Offline-Standort scheint nicht "
            "verschlüsselt zu sein. Aktivieren: Einstellungen > Sicherheit > Mobilgerät "
            "finden > Offline-Finden > Offline-Standort verschlüsseln."
        )

    # SMS/MMS-Backup trotz deaktiviertem Google-Backup
    if get_setting("secure", "mms_backup_enabled") == "1":
        tips.append(
            "SMS/MMS-Backup ist aktiv. Prüfen, wohin Nachrichten gesichert werden "
            "(Google Messages > Einstellungen > Backup bzw. Samsung Cloud)."
        )

    # Optionale Pakete, die noch installiert sind
    if samsung and "com.samsung.android.bixby.agent" in installed:
        tips.append(
            "Bixby ist installiert und hat u.a. Standort-, Sensor- und Anrufliste-Rechte. "
            "Falls ungenutzt: mit --include-optional entfernen."
        )
    if "com.microsoft.appmanager" in installed:
        tips.append(
            "Link zu Windows ist installiert (vorab-Rechte für SMS, Kontakte, Kamera, "
            "Mikrofon). Falls ungenutzt: mit --include-optional entfernen oder "
            "Einstellungen > Verbundene Geräte > Link zu Windows ausschalten."
        )

    # Private DNS
    if not private_dns_set and (get_setting("global", "private_dns_mode")
                                or "opportunistic") != "hostname":
        tips.append(
            "Kein fester Private-DNS-Anbieter. Mit --private-dns dns.quad9.net setzen "
            "oder manuell: Einstellungen > Verbindungen > Weitere > Privates DNS."
        )

    # USB-Debugging (ist während der Skriptausführung zwangsläufig an)
    tips.append(
        "Nach dem Debloaten USB-Debugging und Entwickleroptionen wieder ausschalten: "
        "Einstellungen > Entwickleroptionen > Schalter oben aus."
    )
    return tips


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    global SERIAL
    parser = argparse.ArgumentParser(
        description="Android Privacy-Debloater via ADB (Standard: Dry-Run, nichts wird verändert)"
    )
    parser.add_argument("-s", "--serial", default=os.environ.get("ANDROID_SERIAL"),
                        help="Seriennummer des Geräts (bei mehreren Geräten)")
    parser.add_argument("--run", action="store_true",
                        help="Änderungen wirklich anwenden (ohne: nur Dry-Run)")
    parser.add_argument("--private-dns", metavar="HOST",
                        help="Privates DNS fest setzen, z.B. dns.quad9.net")
    parser.add_argument("--disable-agps", action="store_true",
                        help="Assisted GPS abschalten (sendet Funkzellen-Infos an Google; "
                             "erster GPS-Fix wird langsamer)")
    parser.add_argument("--include-optional", action="store_true",
                        help="Auch optionale Pakete entfernen (z.B. Bixby, Link zu Windows)")
    parser.add_argument("--restore", metavar="DATEI",
                        help="Einstellungen/Pakete aus einer Backup-Datei wiederherstellen")
    parser.add_argument("--all", action="store_true",
                        help="Mit --restore: alle abweichenden Einstellungen und Pakete "
                             "wiederherstellen, nicht nur die vom Skript verwalteten")
    parser.add_argument("--force", action="store_true",
                        help="Mit --restore: auch Backups eines anderen Geräts zulassen")
    args = parser.parse_args()
    apply = args.run

    SERIAL = select_device(args.serial)
    dev = detect_device()

    label = dev["name"] or dev["model"]
    print(f"[OK] Gerät: {dev['manufacturer']} {label} ({dev['model']}), "
          f"Android {dev['android']} (SDK {dev['sdk']}), Serial {SERIAL}")
    if not apply:
        print("[INFO] DRY-RUN: es wird nichts verändert. Mit --run anwenden.")

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
        print(f"[INFO] Keine {dev['manufacturer']}-Systemapps gefunden: vermutlich Custom-ROM. "
              "Herstellerprofil übersprungen, nur allgemeine Regeln.")
        profile = empty_profile
    elif profile:
        print(f"[INFO] Herstellerprofil: {dev['profile']}")
    else:
        print(f"[INFO] Kein Herstellerprofil für '{dev['manufacturer']}', nur allgemeine Regeln.")
        profile = empty_profile

    settings = COMMON_SETTINGS + profile["settings"]
    if args.private_dns:
        settings += [("global", "private_dns_mode", "hostname"),
                     ("global", "private_dns_specifier", args.private_dns)]
    if args.disable_agps:
        settings.append(("global", "assisted_gps_enabled", "0"))

    packages = COMMON_PACKAGES + profile["packages"]
    if args.include_optional:
        packages += profile.get("optional_packages", [])

    apply_privacy_settings(settings, apply)
    remove_packages(packages, installed, apply)

    print("\n--- Manuelle Empfehlungen ---")
    tips = manual_recommendations(dev, installed, bool(args.private_dns))
    for i, tip in enumerate(tips, 1):
        print(f"  {i}. {tip}")

    if apply:
        print("\nFertig. Ein Neustart des Geräts wird empfohlen.")
    else:
        print("\nDry-Run beendet, es wurde nichts verändert. Zum Anwenden: --run")


if __name__ == "__main__":
    main()

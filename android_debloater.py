#!/usr/bin/env python3
"""Generischer Android Privacy-Debloater via ADB.

Erkennt Hersteller/Modell des verbundenen Geräts und wendet allgemeine
(Google/AOSP) sowie herstellerspezifische Einstellungen und Paketlisten an.

Aufruf:
    python android_debloater.py               # Gerät automatisch wählen
    python android_debloater.py -s SERIAL     # bestimmtes Gerät
    python android_debloater.py --dry-run     # nur anzeigen, nichts ändern
"""
import argparse
import os
import re
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
    # Personalisierte Werbung / Werbe-ID einschränken
    ("secure", "limit_ad_tracking", "1"),
    # Google Play Protect App-Überprüfung deaktivieren (optional, falls gewünscht)
    # ("global", "package_verifier_user_consent", "-1"),
]

# Zu entfernende Pakete (--user 0), gültig für alle Geräte
COMMON_PACKAGES = [
    # Google SafetyCore & Key Verifier (Client-Side Scanning / Kontakt-Verifizierung)
    "com.google.android.safetycore",
    "com.google.android.contactkeys",
    # Google Telemetrie / Feedback / Partner-Setup
    "com.google.android.feedback",
    "com.google.mainline.telemetry",
    "com.google.android.partnersetup",
]

# Herstellerspezifische Ergänzungen. Schlüssel = ro.product.manufacturer (klein).
# Neue Hersteller einfach hier ergänzen.
VENDOR_PROFILES = {
    "samsung": {
        "settings": [
            # Samsung Diagnosedaten & Marketing deaktivieren
            ("system", "samsung_errorlog_agree", "0"),
            ("system", "marketing_info_agree", "0"),
        ],
        "packages": [
            "com.samsung.android.rubin.app",       # Customization Service (Nutzerprofilierung)
            "com.samsung.android.dqagent",         # Device Quality Agent (Telemetrie)
            "com.samsung.android.app.spage",       # Samsung Free / News-Feed
            "com.samsung.android.mapsagent",       # Samsung App-Empfehlungen / Tracking
            "com.samsung.android.app.omcagent",    # Carrier/Region App-Installer
            "com.aura.oobe.samsung",               # AppCloud (installiert ungefragt Bloatware)
            "com.sec.android.diagmonagent",        # DiagMonAgent (Fehlerberichte an Samsung)
        ],
    },
    "xiaomi": {
        "settings": [],
        "packages": [
            "com.miui.analytics",                  # MIUI Analytics (Telemetrie)
            "com.miui.msa.global",                 # MIUI System Ads
            "com.miui.daemon",                     # MiuiDaemon (Nutzungsstatistiken)
        ],
    },
}
# Alias-Hersteller, die dasselbe Profil nutzen
VENDOR_ALIASES = {"redmi": "xiaomi", "poco": "xiaomi"}

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
    }
    vendor = manufacturer.lower()
    info["profile"] = VENDOR_ALIASES.get(vendor, vendor)
    return info


def safe_filename_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "unknown"


# ---------------------------------------------------------------------------
# Aktionen
# ---------------------------------------------------------------------------


LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def backup_settings(dev: dict[str, str]) -> None:
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
    print(f"[OK] Backup der aktuellen Settings gespeichert in: {filename}")


def apply_privacy_settings(settings: list[tuple[str, str, str]], dry_run: bool) -> None:
    print("\n--- Setze Privacy-Einstellungen ---")
    for namespace, key, value in settings:
        if dry_run:
            print(f"[DRY]  {namespace}/{key} -> {value}")
            continue
        code, _, err = run_adb(["shell", "settings", "put", namespace, key, value])
        if code == 0:
            print(f"[SET]  {namespace}/{key} -> {value}")
        else:
            print(f"[WARN] Konnte {namespace}/{key} nicht setzen: {err}")


def remove_packages(packages: list[str], dry_run: bool) -> None:
    print("\n--- Entferne Pakete (--user 0) ---")
    code, installed_raw, err = run_adb(["shell", "pm", "list", "packages"])
    if code != 0 or not installed_raw:
        print(f"[FEHLER] Paketliste konnte nicht gelesen werden: {err}")
        sys.exit(1)
    installed = {line.replace("package:", "").strip() for line in installed_raw.splitlines()}

    for pkg in packages:
        if pkg not in installed:
            print(f"[SKIP] {pkg} (nicht installiert)")
            continue
        if dry_run:
            print(f"[DRY]  {pkg} würde entfernt")
            continue

        code, out, err = run_adb(["shell", "pm", "uninstall", "-k", "--user", "0", pkg])
        if code == 0 and "Success" in out:
            print(f"[DEL]  {pkg} erfolgreich entfernt")
        else:
            # Fallback: Manche Systempakete lassen sich nur deaktivieren statt deinstallieren
            code_dis, _, err_dis = run_adb(["shell", "pm", "disable-user", "--user", "0", pkg])
            if code_dis == 0:
                print(f"[DIS]  {pkg} deaktiviert (Uninstall nicht erlaubt)")
            else:
                print(f"[ERR]  {pkg} fehlgeschlagen: {err or out or err_dis}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    global SERIAL
    parser = argparse.ArgumentParser(description="Android Privacy-Debloater via ADB")
    parser.add_argument("-s", "--serial", default=os.environ.get("ANDROID_SERIAL"),
                        help="Seriennummer des Geräts (bei mehreren Geräten)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur anzeigen, was geändert würde")
    args = parser.parse_args()

    SERIAL = select_device(args.serial)
    dev = detect_device()

    label = dev["name"] or dev["model"]
    print(f"[OK] Gerät: {dev['manufacturer']} {label} ({dev['model']}), "
          f"Android {dev['android']} (SDK {dev['sdk']}), Serial {SERIAL}")

    profile = VENDOR_PROFILES.get(dev["profile"])
    if profile:
        print(f"[INFO] Herstellerprofil: {dev['profile']}")
    else:
        print(f"[INFO] Kein Herstellerprofil für '{dev['manufacturer']}', nur allgemeine Regeln.")
        profile = {"settings": [], "packages": []}

    settings = COMMON_SETTINGS + profile["settings"]
    packages = COMMON_PACKAGES + profile["packages"]

    backup_settings(dev)
    apply_privacy_settings(settings, args.dry_run)
    remove_packages(packages, args.dry_run)
    if args.dry_run:
        print("\nDry-Run beendet, es wurde nichts verändert.")
    else:
        print("\nFertig. Ein Neustart des Geräts wird empfohlen.")


if __name__ == "__main__":
    main()

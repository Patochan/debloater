# Android Privacy Debloater

A Python script that makes Android phones and tablets more private over ADB, with no root needed. It detects the connected device and applies privacy settings. It also removes telemetry and tracking apps, and lists the steps you have to do by hand on the device.

- Works on any Android device, with extra rules for **Samsung** and **Xiaomi/Redmi/POCO**
- **Dry run by default**: nothing changes on the device until you pass `--run`
- Backs up all settings before every run, and can **restore** from those backups

## Requirements

- **Python 3.10+**
- **Android Platform-Tools** (`adb`). The script finds `adb` automatically, in this order:
  1. The `ADB` environment variable (full path to `adb`/`adb.exe`)
  2. Your `PATH`
  3. `ANDROID_HOME` / `ANDROID_SDK_ROOT`
  4. Common install locations, such as `%LOCALAPPDATA%\Android\Sdk\platform-tools` and `C:\platform-tools`
- **USB debugging** turned on for the device (Developer options → USB debugging), and the computer's debugging prompt accepted on the device.

## Quick start

```bash
# 1. Dry run: show what would change (nothing is modified)
python android_debloater.py

# 2. Apply the changes
python android_debloater.py --run
```

Read the dry-run output first. Every setting is shown as `current -> new`, and settings that already have the right value are marked `[OK]`.

## Options

| Option | Description |
|---|---|
| `--run` | Apply the changes. Without it, the script only does a dry run. |
| `-s`, `--serial SERIAL` | Choose a device by serial number. Defaults to `ANDROID_SERIAL`. If several devices are connected and none is chosen, the script asks which one to use. |
| `--private-dns HOST` | Set a fixed Private DNS provider, e.g. `dns.quad9.net`. |
| `--disable-agps` | Turn off Assisted GPS, which sends cell tower info to Google. The trade-off is a slower first GPS fix. |
| `--include-optional` | Also remove optional packages, e.g. Bixby and Link to Windows on Samsung. |
| `--battery` | Also apply battery runtime settings (see [Battery](#battery)). |
| `--no-color` | Turn off colored output. Colors are also off when `NO_COLOR` is set or the output is redirected to a file or pipe. |
| `--restore FILE` | Restore settings and packages from a backup file (see [Backup & restore](#backup--restore)). |
| `--all` | With `--restore`: restore every setting and package that differs from the backup, not only the ones the script manages. |
| `--force` | With `--restore`: allow a backup taken on a different device. |

### Examples

```bash
python android_debloater.py -s <SERIAL>                              # dry run on one specific device
python android_debloater.py --run --private-dns dns.quad9.net        # apply + encrypted DNS
python android_debloater.py --run --disable-agps --include-optional  # apply all privacy options

python android_debloater.py --battery                                # dry run incl. battery settings
python android_debloater.py --run --battery                          # apply privacy + battery settings

python android_debloater.py --restore logs/<backup>.txt              # restore: dry run
python android_debloater.py --restore logs/<backup>.txt --run        # restore: apply
```

## What the script changes

### Settings (all devices)

| Setting | Value | Purpose |
|---|---|---|
| `global/multi_cb` | 0 | Turn off Google usage & diagnostics |
| `global/wifi_scan_always_enabled` | 0 | No Wi-Fi scanning while Wi-Fi is off |
| `global/ble_scan_always_enabled` | 0 | No Bluetooth scanning while Bluetooth is off |
| `secure/lock_screen_allow_private_notifications` | 0 | Hide notification content on the lock screen |
| `global/upload_apk_enable` | 0 | Play Protect keeps scanning but no longer uploads unknown APKs to Google |
| `global/network_recommendations_enabled` | 0 | No Wi-Fi network rating by Google Play Services |
| `secure/clipboard_show_access_notifications` | 1 | Show a notice when an app reads the clipboard |
| `global/wifi_wakeup_enabled` | 0 | Don't turn Wi-Fi back on automatically near saved networks |
| `secure` + `global/send_action_app_error` | 0 | No "Send feedback" to Google in the app crash dialog |
| `global/bug_report` | 0 | Remove the bug report shortcut from the power menu |

**Samsung only:** `system/samsung_errorlog_agree=0` (no error reports to Samsung) and `system/nearby_scanning_enabled=0` (no constant scanning for nearby devices).

### Packages

Packages are removed for the current user only (`pm uninstall -k --user 0`). They stay on the system partition and can be restored. If a package can't be uninstalled, the script disables it instead.

| Profile | Packages |
|---|---|
| All devices | `com.google.android.safetycore`, `com.google.android.feedback`, `com.google.mainline.telemetry`, `com.google.android.partnersetup`, `com.facebook.services`, `com.facebook.system`, `com.facebook.appmanager` |
| Samsung | `com.samsung.android.rubin.app`, `com.samsung.android.dqagent`, `com.samsung.android.app.spage`, `com.samsung.android.mapsagent`, `com.aura.oobe.samsung`, `com.sec.android.diagmonagent` |
| Samsung (optional) | `com.samsung.android.bixby.agent`, `com.microsoft.appmanager` |
| Xiaomi | `com.miui.analytics`, `com.miui.msa.global`, `com.miui.daemon` |

Packages that aren't installed are skipped.

### Vendor profiles and custom ROMs

The vendor profile is chosen from `ro.product.manufacturer`. If none of the vendor's own system apps are installed (for example on LineageOS), the script assumes a custom ROM, skips the vendor profile and applies only the generic rules.

## Manual recommendations

Some changes can't, or shouldn't, be made over ADB. At the end of each run, the script checks the device and lists only the points that apply to it:

- Security patch older than 90 days
- Unlocked bootloader
- Screen timeout longer than 2 minutes
- Device name visible to nearby devices (Bluetooth, hotspot, Quick Share)
- Apps that can read all notifications
- Deleting the advertising ID
- Face unlock that uses only the camera (2D), which is weaker than fingerprint plus PIN
- Samsung: offline location for Find My Mobile isn't encrypted
- SMS/MMS backup is turned on
- Bixby or Link to Windows is still installed
- No Private DNS provider set
- Turning USB debugging off again afterwards

## Battery

### Settings applied with `--battery`

| Setting | Value | Purpose |
|---|---|---|
| `global/mobile_data_always_on` | 0 | Don't keep mobile data connected while on Wi-Fi |
| `global/adaptive_battery_management_enabled` | 1 | Adaptive Battery: limit background activity of rarely used apps |
| `system/min_refresh_rate` | 60.0 | Only changed if the screen is forced to its peak refresh rate (e.g. 120 Hz) all the time |

The battery settings are applied together with the privacy settings:

```bash
python android_debloater.py --battery                                # dry run: show what would change
python android_debloater.py --run --battery                          # apply
python android_debloater.py --run --battery --private-dns dns.quad9.net --include-optional   # everything
```

Example dry run on a tablet whose screen was forced to 120 Hz:

```
--- Privacy settings ---
...
[DRY]  global/mobile_data_always_on: 1 -> 0
[DRY]  global/adaptive_battery_management_enabled: not set -> 1
[DRY]  system/min_refresh_rate: 120.0 -> 60.0
```

Settings that already have the right value are shown as `[OK] ... (already set)`, so on many devices `--battery` changes only one or two settings, or none.

### Battery recommendations

Every run also checks battery lifespan and runtime and lists what applies to the device:

- **Lifespan:**
  - Samsung battery protection (charge limit) is off
  - Fast wireless or wired charging is on (more heat, more wear)
  - Charging optimization (limit to 80%) is available
  - Custom ROMs: check for a charge limit
- **Runtime:**
  - Screen forced to its peak refresh rate
  - Samsung motion smoothness set to adaptive/120 Hz
  - Always On Display shown permanently
  - Adaptive brightness off
  - Mobile data kept connected while on Wi-Fi
  - Adaptive Battery off
  - 5G enabled (drains the battery where coverage is weak)

The recommendations are shown on every run, with or without `--battery`. Example from a Samsung phone:

```
--- Battery recommendations ---
  1. Battery protection is off, so the battery sits at 100% for hours. Turn it on (biggest lifespan gain): Settings > Battery > Battery protection > Maximum (limit 80-85%).
  2. Fast wireless charging is on. Wireless charging already produces a lot of heat; turning this off reduces battery wear: Settings > Battery > Charging settings.
  3. Wired fast charging is on. If you charge overnight or have time, turning it off means less heat and less wear: Settings > Battery > Charging settings.
  4. Motion smoothness is set to adaptive/high (up to 120 Hz). Standard (60 Hz) saves noticeable battery: Settings > Display > Motion smoothness > Standard.
  5. 5G is enabled. Where 5G coverage is weak, the modem keeps searching and drains the battery; switching to LTE/4G helps: Settings > Connections > Mobile networks > Network mode.
```

To undo the battery settings, restore an earlier backup (see [Restoring](#restoring)); the battery settings are part of the default restore scope.

Charge limits are not set automatically: the values of Samsung's `protect_battery` differ between One UI versions, and a wrong value could switch on the wrong mode.

## Backup & restore

Every run, including dry runs and restores, first writes a backup to `logs/`:

```
logs/settings_backup_<manufacturer>_<model>_<timestamp>.txt
```

The file contains device info, every `system`, `secure` and `global` setting, and the lists of installed and disabled packages. `logs/` is excluded from git because the backups contain device-specific and personal data.

### Restoring

Like the debloat itself, a restore is a dry run unless you add `--run`, and it takes a fresh backup first.

**1. Find the backup to restore.** Backup names include the model and a timestamp, so sorting by name puts the oldest backup of each device first:

```bash
ls logs/                 # Linux / macOS / Git Bash
dir logs                 # Windows (cmd / PowerShell)
```

To undo the script completely, use the device's **oldest** backup, taken before the first `--run`.

**2. Preview what would change (dry run):**

```bash
python android_debloater.py --restore logs/settings_backup_samsung_SM-S936B_20261115_130405.txt
```

```
[INFO] Backup: settings_backup_samsung_SM-S936B_20261115_130405.txt
[INFO] Backup device: samsung SM-S936B (serial <SERIAL>)
[INFO] Scope: only settings managed by the script

--- Restoring settings ---
[DRY]  system/marketing_info_agree: 0 -> (delete, was not set)
[DRY]  secure/lock_screen_allow_private_notifications: 0 -> 1
[DRY]  global/upload_apk_enable: 0 -> 1
[DRY]  global/wifi_scan_always_enabled: 0 -> 1

--- Restoring packages ---
[DRY]  com.samsung.android.dqagent would be restored
[DRY]  com.samsung.android.rubin.app would be restored

Dry run: 4 setting(s), 2 package(s) would be restored. To apply: --run
```

**3. Apply it:**

```bash
python android_debloater.py --restore logs/settings_backup_samsung_SM-S936B_20261115_130405.txt --run
```

**More examples:**

```bash
# Restore every setting and package that differs from the backup, not only the script's
python android_debloater.py --restore logs/<backup>.txt --all          # check the dry run first!
python android_debloater.py --restore logs/<backup>.txt --all --run

# Several devices connected: choose the one to restore
python android_debloater.py -s <SERIAL> --restore logs/<backup>.txt --run

# Apply a backup from a different device (e.g. same model, new phone)
python android_debloater.py --restore logs/<backup>.txt --force --run
```

**How it works:**

- **Default scope:** only the settings and packages the script manages, including keys set by earlier script versions. Keys the script created that weren't in the backup are deleted.
- **`--all`:** restores every value that differs from the backup. Use it with care: it also includes counters and timestamps the system updates by itself.
- **Packages** are reinstalled with `pm install-existing` and turned back on with `pm enable`. Backups from older script versions have no package list; for those, only the packages the script manages are restored.
- A backup from a different device is refused unless you pass `--force`.
- Settings the system protects can't be written over ADB; they're reported as `[WARN]` and skipped.

## Adding a vendor

Add an entry to `VENDOR_PROFILES` in `android_debloater.py`, using the lowercase `ro.product.manufacturer` as the key:

```python
"oneplus": {
    "marker_packages": ["com.oneplus.launcher"],   # used to detect a custom ROM
    "settings": [("global", "some_key", "0")],
    "packages": ["com.example.telemetry"],
    "optional_packages": [],
},
```

Brands that share an entry go in `VENDOR_ALIASES`, e.g. `"redmi": "xiaomi"`.

## After running

- Restart the device.
- Work through the manual recommendations.
- **Turn off USB debugging and Developer options again.**

## Disclaimer

Use at your own risk. Removing system packages can break features that depend on them. Always check the dry-run output first, and keep the backups so you can restore.

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
| `--connect HOST[:PORT]` | Connect to the device over the network (`adb connect`, default port 5555). See [ADB over the network](#adb-over-the-network). |
| `--pair HOST:PORT` | Pair via Android 11+ Wireless debugging (`adb pair`), then connect. |
| `--pair-code CODE` | 6-digit pairing code for `--pair`; asked for if missing. |
| `--tcpip [PORT]` | Switch a USB-connected device to network adb (default port 5555) and continue over Wi-Fi. |
| `-s`, `--serial SERIAL` | Choose a device by serial number. Defaults to `ANDROID_SERIAL`. If several devices are connected and none is chosen, the script asks which one to use. |
| `--private-dns HOST` | Set a fixed Private DNS provider, e.g. `dns.quad9.net`, and check that it works (see [Private DNS](#private-dns)). |
| `--disable-agps` | Turn off Assisted GPS, which sends cell tower info to Google. The trade-off is a slower first GPS fix. |
| `--include-optional` | Also remove optional packages, e.g. Bixby and Link to Windows on Samsung. |
| `--battery` | Also apply battery runtime settings (see [Battery](#battery)). |
| `--ai` (or `--AI`) | Check AI features and list them; with `--run`, turn them off and remove AI system apps (see [AI and cloud features](#ai-and-cloud-features)). |
| `--cloud` | Check cloud features (backup, sync, cloud apps); with `--run`, turn them off and remove cloud system apps (see [AI and cloud features](#ai-and-cloud-features)). |
| `--revoke-permissions PKG:PERM[,...]` | Take a permission (location, camera, microphone, contacts ...) away from an app. Not undone by `--restore`; see [App permissions](#app-permissions---revoke-permissions). |
| `--background-off PKG[,PKG...]` | Stop these apps from running in the background (Android `appops` `RUN_ANY_IN_BACKGROUND`). Not undone by `--restore`; see [Background activity](#background-activity---background-off). |
| `--report` | Also save the whole output of the run as plain text in `logs/report_<timestamp>.txt`. Implies `--no-color`. |
| `--audit` | Report only: list apps installed outside the Play Store, with the installer that put them on the phone. Changes nothing. |
| `--location-device-only` | Location from the phone's GPS only, no Google network location (`secure/location_mode` 3 → 1). Trade-off: less accurate location. |
| `--screen-timeout SECONDS` | Set the screen timeout, e.g. `60`. Minimum 10 seconds. |
| `--revoke-notifications PKG[,PKG...]` | Revoke notification access for these apps (package names from the manual recommendations). Restore grants it back. |
| `--disable-adb` | With `--run`: turn off USB debugging and Developer options as the very last step. This ends the adb connection; turn USB debugging on again on the device to run the script later. |
| `--no-color` | Turn off colored output. Colors are also off when `NO_COLOR` is set or the output is redirected to a file or pipe. |
| `--restore FILE` | Restore settings and packages from a backup file (see [Backup & restore](#backup--restore)). |
| `--all` | With `--restore`: restore every setting and package that differs from the backup, not only the ones the script manages. |
| `--force` | With `--restore`: allow a backup taken on a different device. |

### Examples

```bash
python android_debloater.py -s <SERIAL>                              # dry run on one specific device
python android_debloater.py --run --private-dns dns.quad9.net        # apply + encrypted DNS
python android_debloater.py --run --disable-agps --include-optional  # apply all privacy options

python android_debloater.py --run --screen-timeout 60 --revoke-notifications com.xiaomi.wearable --disable-adb

python android_debloater.py --battery                                # dry run incl. battery settings
python android_debloater.py --run --battery                          # apply privacy + battery settings

python android_debloater.py --ai --cloud                             # dry run: list AI and cloud features
python android_debloater.py --run --ai --cloud                       # turn off AI and cloud features

python android_debloater.py --restore logs/<backup>.txt              # restore: dry run
python android_debloater.py --restore logs/<backup>.txt --run        # restore: apply
```

## ADB over the network

The script can also work over Wi-Fi instead of USB. The phone and the computer must be on the same network. There are three ways:

**1. Wireless debugging (Android 11+, no cable needed):**

1. On the phone: Developer options → **Wireless debugging** → on → **Pair device with pairing code**. Keep this dialog open.
2. Run the script with the IP address and port shown in the dialog:

   ```bash
   python android_debloater.py --pair 192.168.1.50:37123                    # asks for the 6-digit code
   python android_debloater.py --pair 192.168.1.50:37123 --pair-code 482913 # code on the command line
   ```

   After pairing, the script finds the connect address automatically. If it can't, add the IP address and port shown at the top of the Wireless debugging screen: `--connect 192.168.1.50:41235`.

3. Pairing is only needed once. Next time, while Wireless debugging is on:

   ```bash
   python android_debloater.py --connect 192.168.1.50:41235
   ```

   The port changes each time Wireless debugging is turned on; check the Wireless debugging screen.

**2. Switch from USB to Wi-Fi (`--tcpip`, all Android versions):**

With the phone connected by USB:

```bash
python android_debloater.py --tcpip              # port 5555
python android_debloater.py --tcpip 5556 --run   # other port, and apply changes
```

The script finds the phone's Wi-Fi IP, switches adb to network mode and continues over Wi-Fi. After that, the USB cable can be unplugged. Network mode lasts until the next reboot; until then, connect again with:

```bash
python android_debloater.py --connect 192.168.1.50
```

**3. Network debugging already on:** just use `--connect HOST[:PORT]`.

All other options work the same over the network, e.g. `--connect 192.168.1.50 --run --ai --cloud`.

**Security:** while network debugging is on, anyone on the same network can try to connect. They would still have to be allowed on the phone ("Allow debugging?"). Turn it off when you're done: `--disable-adb` turns off USB and wireless debugging, and `--tcpip` mode also ends with the next reboot.

Backups contain the phone's hardware serial number, so a backup taken over USB can be restored over the network and vice versa.

## Output

Every line starts with a status tag. In a terminal, the tags and the `--- section ---` headings are colored:

| Tag | Color | Meaning |
|---|---|---|
| `[OK]` | 🟢 green | Already in the desired state, nothing to do |
| `[DRY]` | 🟡 yellow | Would be changed (dry run) |
| `[SET]` | 🔵 cyan | Setting changed |
| `[DEL]` | 🔵 cyan | Package removed |
| `[DIS]` | 🔵 cyan | Package disabled (uninstall not allowed) |
| `[ADD]` | 🔵 cyan | Package restored |
| `[ENA]` | 🔵 cyan | Package enabled again |
| `[WARN]` | 🟠 orange | Something couldn't be changed; the script continues |
| `[ERR]`, `[ERROR]` | 🔴 bold red | Failed; `[ERROR]` stops the script |
| `[SKIP]` | ⚪ gray | Package not installed |
| `[INFO]` | ⚪ gray | Information |

Colors work in Windows Terminal, PowerShell 7, Windows PowerShell 5.1 and Linux/macOS terminals. They are turned off automatically when the output is redirected, so log files contain plain text:

```bash
python android_debloater.py --no-color                  # turn colors off for one run
python android_debloater.py > dry-run.txt               # redirected: plain text, no colors
```

To turn colors off permanently, set the `NO_COLOR` environment variable:

```powershell
$env:NO_COLOR = "1"                                      # PowerShell, current session
[Environment]::SetEnvironmentVariable("NO_COLOR", "1", "User")   # PowerShell, permanent
```

```bash
export NO_COLOR=1                                        # Linux / macOS / Git Bash
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
| `global/network_recommendations_enabled` | -1 | No Wi-Fi network rating by Google Play Services (`-1` = forced off; Android resets `0` back to `1` while Play Services is installed) |
| `secure/clipboard_show_access_notifications` | 1 | Show a notice when an app reads the clipboard |
| `global/wifi_wakeup_enabled` | 0 | Don't turn Wi-Fi back on automatically near saved networks |
| `secure` + `global/send_action_app_error` | 0 | No "Send feedback" to Google in the app crash dialog |
| `global/bug_report` | 0 | Remove the bug report shortcut from the power menu |
| `global/non_persistent_mac_randomization_force_enabled` | 1 | Wi-Fi uses a new random MAC address for each network connection, so networks can't follow the phone by its hardware address. Checked on Android 17 (the key exists there); effect on individual routers not tested |

**Samsung only:** `system/samsung_errorlog_agree=0` (no error reports to Samsung) and `system/nearby_scanning_enabled=0` (no constant scanning for nearby devices).

### Packages

Packages are removed for the current user only (`pm uninstall -k --user 0`). They stay on the system partition and can be restored. If a package can't be uninstalled, the script disables it instead.

Packages that aren't installed are skipped. In the output, each package is shown with a short label, e.g. `com.samsung.android.rubin.app (Customization Service: usage profiling)`.

The tables below explain every package the script can remove: what it is, why it's removed, and what stops working afterwards. None of them is needed for calls, messages, the camera, Wi-Fi, mobile data or the Play Store.

**Why a package is removed:**
- **Telemetry:** sends usage, diagnostic or error data to the manufacturer or Google
- **Profiling:** builds a profile of how you use the phone, e.g. for personalized content or ads
- **Ads / bloat:** shows ads or installs promoted apps
- **Unwanted service:** runs in the background for a feature many people don't use
- **AI / cloud:** only with `--ai` / `--cloud`, for people who don't want these features at all

#### All devices

| Package | What it is | Why it's removed | What stops working |
|---|---|---|---|
| `com.google.android.safetycore` | Android System SafetyCore: classifies images on the device, e.g. for sensitive content warnings | Installed silently by Google in 2025 without asking; scans content on the phone | Sensitive content warnings in Google Messages |
| `com.google.android.feedback` | Google Feedback: sends crash reports and feedback to Google | Telemetry | "Send feedback" in crash dialogs |
| `com.google.mainline.telemetry` | Telemetry for Google Play system updates | Telemetry | Nothing noticeable (often only disabled, not removed) |
| `com.google.android.partnersetup` | Google Partner Setup: passes manufacturer/carrier IDs to Google apps | Used to attribute Google search and app usage to partners | Nothing noticeable |
| `com.facebook.services` | Facebook Services, preinstalled by the manufacturer | Runs in the background and contacts Facebook even if no Facebook app is used | Nothing; Facebook, Instagram and WhatsApp from the Play Store keep working |
| `com.facebook.system` | Facebook App Installer | Can install and update Facebook apps outside the Play Store | Same as above |
| `com.facebook.appmanager` | Facebook App Manager | Background updater and data collection for Facebook apps | Same as above |

#### Samsung

| Package | What it is | Why it's removed | What stops working |
|---|---|---|---|
| `com.samsung.android.rubin.app` | Customization Service | Profiling: analyzes app usage, location and habits for personalized content and ads | Personalized suggestions in Samsung apps |
| `com.samsung.android.dqagent` | Device Quality Agent | Telemetry: collects device quality and usage data for Samsung | Nothing noticeable |
| `com.samsung.android.app.spage` | Samsung Free (news and game feed left of the home screen) | Ads / bloat | The feed panel on the home screen |
| `com.samsung.android.mapsagent` | Samsung location agent for location-based suggestions (sparsely documented) | Unwanted service: location-based tracking for suggestions | Location-based suggestions in Samsung apps |
| `com.aura.oobe.samsung` | AppCloud (by ironSource) | Ads / bloat: installs promoted apps during setup and system updates | Nothing |
| `com.sec.android.diagmonagent` | DiagMonAgent | Telemetry: sends diagnostic and error reports to Samsung | Automatic error reports to Samsung |

**Optional** (only with `--include-optional`):

| Package | What it is | Why it's removed | What stops working |
|---|---|---|---|
| `com.samsung.android.bixby.agent` | Bixby voice assistant | Unwanted service for people who use Google Assistant or no assistant; has broad permissions (location, call log, sensors) | Bixby; set the side key to the power menu |
| `com.microsoft.appmanager` | Link to Windows (Phone Link) | Has pre-granted access to SMS, contacts, camera and microphone | The connection to Windows PCs (Phone Link) |

#### Xiaomi (MIUI / HyperOS only, skipped on custom ROMs)

| Package | What it is | Why it's removed | What stops working |
|---|---|---|---|
| `com.miui.analytics` | MIUI Analytics | Telemetry: sends usage statistics to Xiaomi | Nothing noticeable |
| `com.miui.msa.global` | MIUI System Ads (MSA) | Ads: shows ads in system apps | Ads in system apps (fewer ads) |
| `com.miui.daemon` | MiuiDaemon | Telemetry: collects device and usage statistics | Nothing noticeable |

#### AI (only with `--ai`)

| Package | What it is | Why it's removed | What stops working |
|---|---|---|---|
| `com.google.android.apps.bard` | Gemini app | AI assistant | Gemini |
| `com.google.android.aicore` | AICore: runs Google's on-device AI model (Gemini Nano) | AI runtime for other apps | On-device AI features, e.g. summaries and AI replies |
| `com.google.android.apps.aiwallpapers` | AI wallpaper generator | AI | AI wallpapers |
| `com.google.android.as` | Android System Intelligence | On-device AI that analyzes screen content, notifications and app usage | Smart text selection, Live Caption, app suggestions, smart replies |
| `com.google.android.as.oss` | Private Compute Services | Network connection for Android System Intelligence | Nothing without Android System Intelligence |
| `com.samsung.android.aicore` | Samsung AI Core | AI runtime for Galaxy AI | Galaxy AI features that run on the device |
| `com.samsung.android.smartsuggestions` | Smart suggestions | AI suggestions based on usage; reads all notifications | App and action suggestions |
| `com.samsung.android.app.interpreter` | Interpreter | Galaxy AI live translation of conversations | Interpreter |
| `com.samsung.android.callassistant` | Call Assist / Bixby Text Call | AI that answers and transcribes calls | Call Assist, Text Call |
| `com.samsung.android.visionintelligence` | Bixby Vision | AI image recognition in camera and gallery | Bixby Vision |
| `com.samsung.android.bixbyvision.framework` | Bixby Vision framework | Part of Bixby Vision | Same as above |
| `com.samsung.android.vision.model` | On-device vision AI models | AI models for image features | Some AI image features in Gallery/Camera |
| `com.samsung.android.bixby.agent` | Bixby voice assistant | AI assistant | Bixby |
| `com.samsung.android.bixby.wakeup` | "Hi Bixby" voice wake-up | Listens for the wake word | Voice wake-up |
| `com.samsung.android.bixby.ondevice.*` | Bixby on-device language packs (e.g. `.dede`, `.enus`) | Speech recognition for Bixby | Nothing without Bixby |

#### Cloud (only with `--cloud`, Samsung)

| Package | What it is | Why it's removed | What stops working |
|---|---|---|---|
| `com.samsung.android.scloud` | Samsung Cloud | Backup and sync of Samsung data (contacts, calendar, notes, settings) to Samsung's servers | Samsung Cloud backup and sync; the Samsung account itself keeps working |
| `com.microsoft.skydrive` | OneDrive (preinstalled) | Cloud storage, e.g. for Gallery sync | OneDrive and Gallery sync to OneDrive |

Earlier versions of the script also removed `com.google.android.contactkeys` (Android System Key Verifier) and `com.samsung.android.app.omcagent` (carrier/region app installer). They're no longer removed, but a restore still brings them back.

### Vendor profiles and custom ROMs

The vendor profile is chosen from `ro.product.manufacturer`. If none of the vendor's own system apps are installed (for example on LineageOS), the script assumes a custom ROM, skips the vendor profile and applies only the generic rules.

## Report (`--report`)

`--report` saves a copy of everything the run prints to `logs/report_<timestamp>.txt`, in plain text without colors. Use it to hand a client a record of what was changed on the device. It is saved for dry runs too, so you can keep the "before" report.

```bash
python android_debloater.py --run --ai --cloud --report
```

## App permissions (`--revoke-permissions`)

`--revoke-permissions PKG:PERM[,PKG:PERM...]` takes a runtime permission away from an app, e.g. its location or camera access. Give the permission as a short name (`CAMERA`, `ACCESS_FINE_LOCATION`, `RECORD_AUDIO`, `READ_CONTACTS`) or the full name. Apps that don't have the permission are skipped. The app keeps working; if it needs the permission later, Android asks again. Package names and permission names are checked before they are sent to the phone.

```bash
python android_debloater.py --revoke-permissions com.example.app:CAMERA,com.example.app:ACCESS_FINE_LOCATION
```

**Undo:** this is not part of `--restore`. Grant the permission back:

```bash
adb shell pm grant --user 0 com.example.app android.permission.CAMERA
```

Tested on the phone with a browser's location permission: revoked, then granted back. The grant-back restores the permission but may not restore the exact internal flags Android shows for it.

## Background activity (`--background-off`)

`--background-off PKG[,PKG...]` stops the named apps from starting or running when they are not open. Use it for apps that keep running for no reason, e.g. after the audit found them. Apps you open still work normally. Package names are checked before they are sent to the phone (only letters, digits, `_` and `.`).

```bash
python android_debloater.py --background-off com.example.app,com.other.app
```

**Undo:** this is not part of `--restore`. Run for each app:

```bash
adb shell cmd appops set com.example.app RUN_ANY_IN_BACKGROUND default
```

## Audit (`--audit`)

`--audit` lists third-party apps that were not installed from the Play Store, with the installer shown in brackets. Apps from F-Droid, Aptoide, a file manager or Samsung's Galaxy Store appear too, and so do apps whose installer is unknown. It only reports: review the list and uninstall what isn't wanted, e.g. with `adb uninstall` or in the phone's app list.

```
--- Apps installed outside the Play Store ---
[INFO] org.fdroid.fdroid (installed by org.fdroid.fdroid)
[INFO] cm.aptoide.pt (installed by com.android.packageinstaller)
```

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

## AI and cloud features

AI and cloud features are **only checked and changed when you ask for it** with `--ai` and/or `--cloud`. Without these flags, the script doesn't touch them.

### Typical workflow

**1. Check what's enabled (dry run, nothing changes):**

```bash
python android_debloater.py --ai --cloud
```

**2. Turn off only what the client doesn't want:**

```bash
python android_debloater.py --run --ai                 # AI features only
python android_debloater.py --run --cloud              # cloud features only
python android_debloater.py --run --ai --cloud         # both
```

**3. Combine with the other options, e.g. for a client who wants no AI, no cloud and maximum privacy:**

```bash
python android_debloater.py --run --ai --cloud --include-optional --private-dns dns.quad9.net --disable-adb
```

**4. Several phones connected:** run it once per phone with `-s`:

```bash
python android_debloater.py -s <SERIAL> --run --ai --cloud
```

**5. Undo:** restore the backup taken before the run (see [Restoring](#restoring)). This also brings back removed AI/cloud apps and turns Google backup on again:

```bash
python android_debloater.py --restore logs/<backup>.txt --run
```

### Changed with `--ai`

| Setting / package | Purpose |
|---|---|
| `global/smart_replies_enabled=0` | No AI reply/action suggestions in notifications |
| `secure/assist_structure_enabled=0`, `secure/assist_screenshot_enabled=0` | The assistant can't read text or take screenshots of the screen |
| `secure/assist_long_press_home_enabled=0`, `secure/assist_touch_gesture_enabled=0` | Don't start the assistant by long-pressing home or swiping from a corner |
| Gemini, AICore, AI wallpapers, Android System Intelligence, Private Compute Services | Google AI apps and services are removed |
| **Samsung:** notification summaries, AI notification priority, call transcripts, live call translation | Galaxy AI settings off (only if the setting exists on the device) |
| **Samsung:** Samsung AI Core, Smart suggestions, Interpreter, Call Assist, Bixby, Bixby Vision, Bixby voice wake-up and language packs | Galaxy AI / Bixby apps are removed |

### Changed with `--cloud`

| Setting / package | Purpose |
|---|---|
| Google backup (via `bmgr`) | Turn off Android's backup to Google Drive |
| `secure/backup_auto_restore=0` | Don't restore app data from the cloud when an app is reinstalled |
| **Samsung:** `mcf_continuity`, clipboard sync with Windows / Samsung Flow | Continuity and clipboard sync off (only if the setting exists) |
| **Samsung:** Samsung Cloud, OneDrive | Preinstalled cloud apps are removed |

### Recommendations

Some AI and cloud features can't be changed reliably via ADB, or belong to apps the user installed. The script lists them instead:

- **AI:** default digital assistant (Google Assistant/Gemini), Galaxy AI "Process data only on device", Circle to Search, Gboard AI writing features, AI apps installed by the user (e.g. ChatGPT, Copilot, Claude, Perplexity)
- **Cloud:** auto-sync and the account types it syncs, cloud storage apps (Google Photos, Drive, Dropbox, OneDrive, Nextcloud, ...), Samsung Pass

Apps the user installed themselves (e.g. ChatGPT, Google Photos) are **never removed** automatically.

### Example output

Dry run with `--ai --cloud` on a Samsung Galaxy S21 FE (privacy settings already applied earlier; unchanged lines shortened). Packages marked `already disabled` had been turned off by hand before:

```
--- Privacy settings ---
...
[DRY]  global/smart_replies_enabled: 1 -> 0
[DRY]  secure/assist_structure_enabled: not set -> 0
[DRY]  secure/assist_screenshot_enabled: not set -> 0
[DRY]  secure/assist_long_press_home_enabled: not set -> 0
[DRY]  secure/assist_touch_gesture_enabled: not set -> 0
[DRY]  secure/backup_auto_restore: not set -> 0
[OK]   system/mcf_continuity = 0 (already set)
[OK]   secure/ltw_clipboard_sync_state = 0 (already set)
[OK]   secure/samsungflow_clipboard_sync_state = 0 (already set)

--- Google backup ---
[OK]   Google backup is off (already set)

--- Removing packages (--user 0) ---
...
[OK]   com.google.android.apps.bard (Gemini) - already disabled
[SKIP] com.google.android.aicore (AICore: on-device Gemini Nano) - not installed
[DRY]  com.google.android.as (Android System Intelligence) would be removed/disabled
[DRY]  com.google.android.as.oss (Private Compute Services) would be removed/disabled
[DRY]  com.samsung.android.smartsuggestions (Smart suggestions: reads notifications) would be removed/disabled
[DRY]  com.samsung.android.callassistant (Call Assist / Text Call) would be removed/disabled
[DRY]  com.samsung.android.visionintelligence (Bixby Vision) would be removed/disabled
[DRY]  com.samsung.android.bixbyvision.framework (Bixby Vision framework) would be removed/disabled
[OK]   com.samsung.android.bixby.agent (Bixby voice assistant) - already disabled
[DRY]  com.samsung.android.bixby.wakeup ("Hi Bixby" voice wake-up) would be removed/disabled
[DRY]  com.samsung.android.scloud (Samsung Cloud) would be removed/disabled
[OK]   com.microsoft.skydrive (OneDrive) - already disabled

--- AI recommendations ---
  1. Default digital assistant is com.google.android.googlequicksearchbox (Google Assistant/Gemini). If AI assistants aren't wanted, set it to None: Settings > Apps > Choose default apps > Digital assistant app.
  2. Galaxy AI: turn on 'Process data only on device' so no data goes to Samsung/Google AI servers, and turn off the features you don't use: Settings > Galaxy AI (older One UI: Settings > Advanced features > Advanced intelligence).
  3. Circle to Search (Google AI search): turn it off if unused: Settings > Display > Navigation bar > Circle to Search.

--- Cloud recommendations ---
  1. Auto-sync is on (2 account type(s): com.google, nextcloud): contacts, calendars, mail etc. are synced to the cloud. Turn it off if not needed (mail then no longer arrives automatically): Settings > Accounts and backup > Manage accounts > Auto sync data.
  2. Cloud storage apps installed: Google Photos, Google Drive, Nextcloud. Check their backup/sync settings (e.g. Google Photos > Settings > Backup) or uninstall them if unwanted.
  3. Samsung Pass is installed; it stores passwords and passkeys in the Samsung account cloud. If it isn't used: Settings > Security and privacy > Samsung Pass.
```

On a phone with a user-installed AI app, the AI recommendations also list it, e.g.:

```
  4. AI apps installed by the user: ChatGPT. They aren't removed automatically; uninstall them if they aren't wanted.
```

## Private DNS

`--private-dns HOST` sets a fixed Private DNS provider (DNS over TLS). DNS lookups are then encrypted, so your mobile carrier and Wi-Fi owners can't see which domains you look up. Common providers:

| Provider | Hostname |
|---|---|
| Quad9 | `dns.quad9.net` |
| Cloudflare | `one.one.one.one` |
| Mullvad | `dns.mullvad.net` |
| AdGuard (blocks ads) | `dns.adguard-dns.com` |

```bash
python android_debloater.py --private-dns dns.quad9.net           # dry run
python android_debloater.py --run --private-dns dns.quad9.net     # set it and check it
```

### Check after setting

With a fixed provider, Android sends DNS lookups only to that provider. If it can't be reached, for example because of a typo in the hostname, websites stop loading. So after setting it, the script checks that Android actually reached the provider. It waits up to 15 seconds, because Android needs a few seconds to validate a new provider.

| Result | Output |
|---|---|
| Provider reached and validated | `[OK]   dns.quad9.net is in use and validated (149.112.112.112, 9.9.9.9)` |
| Not set yet (dry run) | `[SKIP] Private DNS is not set to dns.quad9.net yet (use --run to set it)` |
| Provider not reachable | `[WARN] dns.quad9.net could not be reached. In this mode DNS lookups fail, so the device may have no internet. Check the hostname, or restore a backup (--restore) to undo it.` |
| No network connected | `[WARN] No connected network uses Private DNS right now. Connect to Wi-Fi or mobile data and run the script again to check.` |

Example run:

```
--- Privacy settings ---
...
[SET]  global/private_dns_mode: not set -> hostname
[SET]  global/private_dns_specifier: not set -> dns.quad9.net

--- Checking Private DNS ---
[OK]   dns.quad9.net is in use and validated (149.112.112.112, 9.9.9.9)
```

### Check manually

The same check with adb:

```bash
adb shell settings get global private_dns_mode          # hostname
adb shell settings get global private_dns_specifier     # dns.quad9.net
adb shell "dumpsys connectivity | grep -oE 'UsePrivateDns: \w+|PrivateDnsServerName: \S+|ValidatedPrivateDnsAddresses: \[[^]]*\]'"
```

```
UsePrivateDns: true
PrivateDnsServerName: dns.quad9.net
ValidatedPrivateDnsAddresses: [9.9.9.9,149.112.112.112]
```

`ValidatedPrivateDnsAddresses` is the important part. If it's missing, Android couldn't reach the provider.

On the device, the setting is under:

- **Samsung:** Settings → Connections → More connection settings → Private DNS
- **Stock Android / custom ROMs:** Settings → Network & internet → Private DNS

### Undo

Restore an earlier backup (see [Restoring](#restoring)), or set Private DNS back to Android's default, "Automatic":

```bash
adb shell settings put global private_dns_mode opportunistic
```

## Location

`--location-device-only` sets `secure/location_mode` to `1`: the phone uses only its own GPS. Android's default (`3`, high accuracy) also uses Google's network location, which sends nearby Wi-Fi networks and cell towers to Google. The trade-off: the first GPS fix is slower and the position is less accurate indoors. Maps and other apps still work. The change is part of the restore scope, so `--restore` brings the old value back.

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
[DRY]  com.samsung.android.dqagent (Device Quality Agent: telemetry) would be restored
[DRY]  com.samsung.android.rubin.app (Customization Service: usage profiling) would be restored

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

- **Restart the device.** After a `--run` or a restore with `--run`, the script asks:

  ```
  Reboot the device now? [y/N]
  ```

  Enter `y` to reboot it with `adb reboot`. Just pressing Enter (the default) or any other answer skips it. The question isn't asked in a dry run, when the script can't read input from an interactive terminal (e.g. in an automated job), or with `--disable-adb` (adb can't reach the device anymore then, so reboot it by hand).

  **Examples:**

  ```bash
  python android_debloater.py --run                                  # asks at the end
  python android_debloater.py --restore logs/<backup>.txt --run      # asks after restoring
  python android_debloater.py --run --disable-adb                    # doesn't ask, reboot by hand
  python android_debloater.py                                        # dry run: doesn't ask
  ```

  Answering `y`:

  ```
  Done. A reboot of the device is recommended.

  Reboot the device now? [y/N] y
  [OK]   Device is rebooting.
  ```

  Pressing Enter (default: no):

  ```
  Done. A reboot of the device is recommended.

  Reboot the device now? [y/N]
  [INFO] Not rebooting. Reboot the device later so all changes take effect.
  ```

  With `--disable-adb`:

  ```
  Done. A reboot of the device is recommended.

  --- Turning off USB debugging ---
  [SET]  Developer options off
  [SET]  USB debugging off - the adb connection ends now
  [INFO] adb can't reach the device anymore, so reboot it by hand.
  ```

  In an automated job (no interactive terminal):

  ```
  [INFO] No interactive terminal: not rebooting. Reboot the device by hand.
  ```
- Work through the manual recommendations.
- **Turn off USB debugging and Developer options again** (or use `--disable-adb`).

## Disclaimer

Use at your own risk. Removing system packages can break features that depend on them. Always check the dry-run output first, and keep the backups so you can restore.

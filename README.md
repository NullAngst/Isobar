# Isobar

Isobar is a weather and radar app for Linux, Windows, macOS and Android, for people who want the forecast, live radar, warnings and SPC outlooks in one clean window, without paying for RadarOmega or digging through the NWS website.

Type in a city, ZIP code or `lat, lon` (or tap "Use my location" on Android) and you get current conditions, an hourly chart, a 10-day forecast, active alerts with their hail size, wind and damage threat, the local forecast discussion and hazardous weather outlook, a radar map with storm reports, storm cells and hurricane cones, and the Storm Prediction Center outlooks for days 1 through 8.

## What's in it

- **Now**: active alerts, current conditions, how it compares with this time yesterday, when rain or snow starts or stops in the next 3 hours, NWS forecast text, today's SPC risk, the next 12 hours as a chart, then wind, humidity, pressure trend, visibility, UV and air quality.
- **Warning strip**: while a warning covers your place, a strip under the top bar shows it on every view, radar included, with a countdown once it's within 3 hours of expiring.
- **Forecast**: the hourly chart (24 hours, 48 hours or 7 days), the 10-day list you can expand for detail, and an hour-by-hour table.
- **Radar**: reflectivity (national mosaic or a single radar), velocity, rotation (storm-relative velocity), echo tops, radar-estimated rainfall (1, 24, 48, 72 hours), future radar (HRRR, with the real valid time on every frame), a GOES East satellite loop (infrared, GeoColor, water vapor), and model wind and temperature maps. Loops, play and step with space and the arrow keys.
- **Pointer readout**: on the desktop, the reflectivity under the mouse in dBZ, and how far and which way that spot is from your place.
- **Radar sites**: every NEXRAD site is a dot on the map. Click one to see just that radar, click it again (or "Back to all radars") to return to the national mosaic. Velocity and rotation start on your nearest radar.
- **Radar overlays**: warnings, watches, advisories, SPC mesoscale discussions, storm reports, NEXRAD storm cells (tornado vortex signatures, mesocyclones and hail, with where each cell is headed in the next 30 minutes), NHC hurricane cones, the SPC day 1 outlook, labels on warning shapes (TO, SV, FF) and county lines. Chips over the timeline show which overlays are on, and turn red if one failed to load. It retries on its own.
- **Storm reports**: the NWS local storm reports from the last 3, 6, 12, 24 or 48 hours, as dots colored by kind (tornado, hail, wind, flood, winter, other). Click one for the size, place, time and the spotter's remark.
- **Reflectivity color modes**: IEM default, NWS classic, smooth, soft, color-blind safe and grayscale, plus a cutoff to hide weak echoes and ground clutter.
- **Outlooks**: SPC days 1 to 8, with categorical, tornado, wind, hail and any-severe maps, hatched significant areas, and the risk at your exact location.
- **Alerts**: everything active for your location, most dangerous first, with the impact tags the NWS attaches (tornado radar indicated or observed, hail size, peak gust, damage threat), Tornado Emergency and PDS called out, full text, and a button that jumps to the polygon on the radar. If an SPC mesoscale discussion covers your spot, it shows here too, and as a banner on Now. Those often come an hour or so before a watch. If the NWS stops answering, Alerts says so and how old the list is, instead of looking like a quiet sky.
- **Discussion**: your NWS office's Area Forecast Discussion, cleaned up for reading, with whatever changed since the previous issuance marked in the margin, and its Hazardous Weather Outlook on the "Hazards" tab.
- **Notifications** on the desktop and on Android, for new warnings at your place and at any saved place you ring the bell on (or watches and mesoscale discussions too, if you want them). An update to a warning you already got stays quiet, unless it gets worse (a tornado warning turning into a tornado emergency pings you again). Tornado warnings, extreme wind warnings, emergencies and PDS warnings make a sound and stay on screen. Quiet hours let only those through. If the NWS can't be reached for 15 minutes, you get one notice saying so.
- **Tray**: on the desktop, the tray icon and its tooltip show your temperature and active warnings, and the icon gets a red dot while a warning is up. Optional: keep running in the tray when the window closes, and start at login hidden in the tray.
- **Keyboard**: `1` to `6` jump between views, `/` jumps to search, `r` refreshes, `?` lists every shortcut, and on the radar space plays or pauses and the arrow keys step frames.
- **Map tile cache**: radar frames, county lines and base maps are kept on disk, each until it goes stale, so zooming back out or reopening the app doesn't download them again. Details under "The map cache".
- Dark, light, midnight, verdigris, Kawaii Light and Kawaii Dark themes, or follow the system. US or metric units, 12 or 24 hour clock, saved places.
- **Honest timestamps**: "Updated" in the top bar is when Open-Meteo last confirmed the forecast. If it stops answering, that turns into "Forecast from 45 min ago" in red. The radar time turns red if the newest scan is more than 15 minutes old.

## Download and run

Grab the archive for your system from the [Releases](https://github.com/NullAngst/Isobar/releases) page.

On Windows, and with the Linux `.tar.gz`, KEEP THE `Isobar` PROGRAM INSIDE ITS FOLDER. It loads everything else from the `_internal` folder next to it, so if you move the program out on its own it won't start. Put the whole folder wherever you like and make a shortcut or symlink to the program instead.

### Linux

Prerequisites: a desktop session (X11 or Wayland) and a few system libraries that Qt WebEngine expects the OS to provide. Most desktops already have them.

I run Kubuntu, so in my case that's:

```
sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libasound2t64
```

On older Ubuntu and Debian the last one is `libasound2`. On openSUSE it's `sudo zypper install libxcb-cursor0 libxkbcommon-x11-0 mozilla-nss libXcomposite1 libXdamage1 libXrandr2 libXtst6 libasound2`, or whichever names your distro uses for the same libraries.

Optional, for alert notifications that stay on screen and make a sound: `notify-send` (package `libnotify-bin` on Ubuntu, `libnotify-tools` on openSUSE) and `canberra-gtk-play` (`gnome-session-canberra` or `libcanberra-gtk3-module`, depending on the distro). Without them Isobar falls back to the tray's own notifications.

There are two Linux downloads with the same app inside. The AppImage is one file you mark executable and run. The `.tar.gz` is a folder you unpack. Pick the AppImage unless you have a reason not to.

#### AppImage

The AppImage also needs FUSE to mount itself, which most desktops already have. If it says it can't mount, install it. In my case that's `sudo apt install fuse3`, on openSUSE `sudo zypper install fuse3`, or whichever package gives you `fusermount3` or `fusermount`.

1. Download `Isobar-<version>-linux-x86_64.AppImage`.
2. Move it somewhere permanent: `mkdir -p ~/Applications && mv Isobar-*-linux-x86_64.AppImage ~/Applications/` since that's where AppImage tools look by default.
3. Make it executable: `chmod +x ~/Applications/Isobar-*-linux-x86_64.AppImage`
4. Run it: `~/Applications/Isobar-*-linux-x86_64.AppImage`
4.5. Optional: give it a menu entry with Gear Lever, AppImageLauncher, or whichever integration tool you use. The icon and desktop file are inside the AppImage, so they pick it up on their own.

No FUSE and don't want to install it? Run it with `--appimage-extract-and-run`. It unpacks to a temp folder on every launch, so it starts slower, but it works.

To update, download the new AppImage and delete the old one. Or let AppImageUpdate (or Gear Lever) do it, since the AppImage knows where its releases live and only downloads the parts that changed. Your settings live outside the AppImage either way, so they stay.

#### Folder (.tar.gz)

1. Download `Isobar-<version>-linux-x86_64.tar.gz`.
2. Extract it: `tar -xzf Isobar-*-linux-x86_64.tar.gz`
3. Move the folder somewhere permanent: `mv Isobar ~/.local/share/` since that's where per-user apps usually live.
4. Run it: `~/.local/share/Isobar/Isobar`
4.5. Optional: link it onto your PATH with `ln -s ~/.local/share/Isobar/Isobar ~/.local/bin/isobar`

With either download, if the window opens blank or it exits right away, add `--no-sandbox` to the command. Some setups (containers, hardened kernels, some Flatpak-style sandboxes) block Chromium's own sandbox. If it's a VM or the GPU driver is the problem, add `--software-gl`.

### Windows

1. Download `Isobar-<version>-windows-x86_64.zip`.
2. Right click it, choose Extract All, and pick a folder like `C:\Program Files\Isobar` or whichever you prefer.
3. Run `Isobar.exe`.

The build isn't code signed, so SmartScreen will warn you the first time. Click "More info", then "Run anyway".

### macOS (Apple Silicon)

1. Download `Isobar-<version>-macos-arm64.zip` and double click it.
2. Drag `Isobar.app` into Applications.
3. Clear the quarantine flag, since the app isn't signed or notarized: `xattr -dr com.apple.quarantine /Applications/Isobar.app`
4. Open it from Launchpad or Applications.

Intel Macs aren't built by the workflow. Run it from source instead (below).

### Android

Android 7.0 or newer, on a 64-bit phone or tablet (anything from about 2017 on).

1. On the phone, open the [Releases](https://github.com/NullAngst/Isobar/releases) page and download `Isobar-<version>-android.apk`.
2. Open the downloaded file.
3. If Android asks, allow your browser (or whichever app opened it) to install unknown apps, then go back and tap Install.

Play Protect may warn that it doesn't recognize the developer, since the app isn't from the Play Store. "Install anyway" is under "More details". Updates install over the top the same way, and your settings stay.

What's different on Android:

- The whole UI is the same, laid out for a phone: the view buttons move to a bottom bar, and the radar and outlook panels fold up into one button until you tap them.
- Alert notifications come from a background check about every 15 minutes, which is the shortest interval Android allows apps without a permanent notification. That's slower than the desktop's 2 minutes, so keep WEA alerts turned on in your phone's settings too. Tapping a notification opens Alerts.
- Notifications use three channels you can tune in Android's settings: "Tornado warnings and emergencies" (loud, vibrates), "Warnings" and "Watches and other alerts". On first launch Android 13 and later ask whether Isobar may post notifications.
- "Use my location" is at the top of the search list, and "Follow my location" in Settings moves your place to wherever the phone is each time you open the app. Both use coarse location (about a kilometer) and only while the app is open. Isobar never asks for background location.
- The back button closes whatever is open (Settings, search, a map panel), then goes back to Now, then leaves the app.
- With gesture navigation on a phone with rounded screen corners, the bottom bar pulls in at the ends so the corners don't cut off Now and Settings.
- Permissions: internet access, notifications, and coarse location when you use it. The app targets Android 17, so Android doesn't hand it the "Nearby devices" (local network) permission it gives older apps automatically. Isobar talks to its own built-in server on the phone and to the weather services online, and neither counts as your local network.
- Everything is stored inside the app's private storage, and uninstalling removes it all, settings included.

## Optional: a map key for the street maps

Out of the box, the radar and outlook maps sit on Esri satellite imagery with place names on top. That needs no key and works right away.

If you'd rather have plain dark, light or street maps (or "Match theme", which follows the app's light or dark look), those come from CARTO, and CARTO requires a free API key. Without one those styles still load, but every tile is stamped "API KEY REQUIRED", so Isobar falls back to satellite until a key is set.

1. Go to [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey/) and request a key. No account needed, they email it straight back.
2. In Isobar, open Settings and find Maps.
3. Paste the key into "CARTO API key" and press Enter.

The radar and outlook maps reload with the key right away. The key is saved in plain text in `settings.json` (see below), which is fine, since it's sent in every tile URL anyway and grants nothing but basemap tiles.

Then pick the style under Settings, Maps, Map style, or in the radar's Layers panel. A wrong key looks exactly like no key: you still get the watermark. If it's still stamped after you paste it, check the key for a stray space or a missing character.

## Run from source

Prerequisites: Python 3.10 or newer and `git`.

1. Clone it: `git clone https://github.com/NullAngst/Isobar.git`
2. Go in: `cd Isobar`
3. Make a virtual environment: `python3 -m venv .venv`
4. Activate it: `source .venv/bin/activate` (on Windows, `.venv\Scripts\activate`)
5. Install the two dependencies: `pip install -r requirements.txt`
6. Run it: `python run.py`

If Qt WebEngine won't install or won't start on your machine, `python run.py --browser` serves the same UI to your default browser instead. You lose the tray and notifications, everything else works.

To run the tests: `python -m unittest discover -s tests -t .` from the repo root. They need only `requests`, not Qt, and they don't touch the network or your real settings.

## Command line options

| Option | What it does |
| --- | --- |
| `--browser` | Open the UI in your default browser instead of the app window |
| `--hidden` | Start in the tray without showing the window. "Start at login" uses this. |
| `--port N` | Use a different local port (default 47130, or a random one if that's taken) |
| `--no-sandbox` | Turn off Chromium's sandbox, for setups that block it |
| `--software-gl` | Render without the GPU, for VMs and broken drivers |
| `--debug` | Verbose logs, plus a web inspector on port 9222. Anything running on your machine can attach to that inspector while it's open, so use it for troubleshooting only. |
| `--version` | Print the version and exit |

## The map cache

Every map tile Isobar shows gets saved to disk with an expiry. Until it expires, zooming or panning back to it, switching views, or restarting the app loads it from disk instead of the network. Once it expires, the next time that spot is on screen it downloads fresh and replaces the old copy.

How long a tile stays good depends on what it is:

| Tile | Kept for | Why |
| --- | --- | --- |
| Radar mosaic and single-radar scans | 24 hours | Each frame is one timestamped scan, which never changes. New scans have new addresses, so the loop picks them up on its own. |
| County lines | 30 days | They don't move. |
| Base maps (CARTO, Esri) | 7 days | Roads and labels change slowly. |
| Future radar (HRRR) | 24 hours | Frames name their model run, so a frame never changes. A new run gets new addresses. |
| Echo tops loop | 10 minutes | Its frames are "N minutes ago" layers that all change together, so the loop only rebuilds every 10 minutes. |
| Rainfall | 4 minutes | These addresses always mean "the latest", so the picture behind them changes. |

If the network drops, Isobar shows an expired tile rather than a blank one.

The cache tops out around 6,000 tiles and drops the oldest past that. Settings, under Maps, shows how big it is and has a Clear button.

The satellite loop is one image per frame from NESDIS rather than tiles. Those come from NESDIS without the headers a page needs to store them, so they lean on the regular browser cache instead.

The cache is tied to the app's local address, which is why Isobar now always uses port 47130. If something else already has that port, Isobar picks a random one and the cache starts empty for that run. Same thing if you pass a different `--port`.

## Going easy on the free services

Everything Isobar shows comes from services that are free because the people running them are generous. The app is built to ask them for as little as possible:

- **Every request identifies itself** as Isobar, with a link to this repo, which the NWS and OpenStreetMap ask for.
- **Every answer is cached** for as long as it can stay useful: forecasts and the rain timing 10 minutes, alerts 1 minute, SPC outlooks 10 minutes, mesoscale discussions and storm cells 2 minutes, storm reports 5 minutes, hurricane cones 10 minutes, NWS text forecasts 30 minutes, forecast discussions and hazardous weather outlooks a day once issued, search results and NWS grid points a day, place names for typed coordinates 30 days.
- **Storm reports are asked for nationwide**, in one of five fixed windows, and trimmed to the map on your end. Every pan and zoom reuses the same cached answer instead of asking again for each map view.
- **Long-lived answers are kept on disk**, so a restart doesn't refetch them.
- **Expired answers are revalidated, not refetched.** If a server sent an ETag or Last-Modified date, Isobar asks "has this changed?" and an unchanged answer comes back as a tiny "not modified".
- **Identical requests share one call.** If five parts of the app want the same thing at once, the server gets asked once, and that holds when the call fails too.
- **Failing services get left alone.** After an error, timeout or "slow down", Isobar backs off that service, honoring any Retry-After it sends, starting at 20 seconds and doubling up to 15 minutes. While it waits, you see the last good answer if there is one, and the UI says how old it is. Backoff is kept per service path, so the NWS forecast endpoint failing never holds up NWS alerts. Plain 500 errors from the NWS API don't trigger backoff at all, since it throws them often for one grid point and answers fine on the next try.
- **Nothing refreshes while you aren't looking.** The forecast refreshes every 10 minutes, and only while the window is visible. Alerts refresh every 2 minutes, also while the window is hidden in the tray, so the tray icon stays honest. Notifications check alerts every 2 minutes on the desktop and about every 15 on Android.
- **Radar loops only download what's new.** A refresh keeps the frames already loaded and adds the newest scan. Frames load one or two at a time, the one on screen first, and zooming reloads the rest in turn instead of all at once.
- **Map requests are shaped to reuse answers.** Watch and warning boxes snap to a coarse grid, so panning around lands on the same few areas. The wind and temperature maps ask Open-Meteo for at most 48 points over a padded area and reuse it while you pan inside it.
- **Typed coordinates aren't looked up per keystroke.** The place name for "34.58, -83.33" is fetched once, when you pick it, and those lookups are spaced at least a second apart per OpenStreetMap Nominatim's usage policy.

## Security

Isobar runs a small web server on your machine to show its UI, so it's locked down:

- **It only listens on 127.0.0.1**, so nothing else on your network can reach it.
- **Every API call needs a random token** made fresh each launch. It moves out of the address bar as soon as the page loads.
- **Requests from other websites are refused.** The server checks the Host header, which stops DNS rebinding, and turns away any request whose Origin isn't Isobar itself.
- **A strict Content-Security-Policy** limits the page to its own scripts. The only outside hosts it may load from are the radar, map tile and NESDIS satellite servers. Text from weather services is always escaped before display.
- **Tile servers are sent no Referer header**, so they don't learn the local address or the token.
- **Links only ever open in your browser, and only web links** (http and https). Anything else, like a file path or a custom protocol, is dropped.
- **The tile proxy fallback only fetches images from the Iowa Environmental Mesonet.** It refuses redirects and caps response sizes.
- **Settings are validated before they're saved**: choices have to be one of the known answers, numbers are clamped to sane ranges, and the settings file is readable only by you, since it can hold your CARTO key.
- **Map popups for storm reports, storm cells, hurricane cones and mesoscale discussions are built as page elements, not HTML strings**, so text in a report can never run as code.
- **One copy per user.** Starting Isobar again raises the window that's already running, so there's never a second set of notifications. The check uses a local socket only your user can reach.
- **The GitHub workflow builds with read-only access.** Only the release job, which uploads the finished files, can write to the repo, and the Android job can sign its provenance attestation. The actions are referenced by major version (`actions/checkout@v4`), not pinned to commits, so they pick up fixes without manual bumps. The tradeoff is that a bad release pushed under one of those tags upstream would run in the build.
- **The APK carries a signed SLSA build provenance attestation**, which proves it was built by this repo's workflow from a given commit. Check it with `gh attestation verify Isobar-<version>-android.apk --repo NullAngst/Isobar` on any machine that has the GitHub CLI, or with the `.sigstore.json` bundle attached next to the APK and any Sigstore verifier.

## Where it keeps things

Settings (units, theme, saved places, radar choices) live in one JSON file. The cache folder holds the map tiles (under `webengine/storage`), web data, and `notify-seen.json`, the list of alerts you've already been notified about. It's safe to delete; the worst case is a repeat notification for an alert that's still active.

"Start at login" writes the system's own entry: `~/.config/autostart/io.github.nullangst.isobar.desktop` on Linux, an `Isobar` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` on Windows, and `~/Library/LaunchAgents/io.github.nullangst.isobar.plist` on macOS. Turning the setting off removes it.

- Linux: `~/.config/isobar/settings.json` and `~/.cache/isobar/`
- Windows: `%APPDATA%\Isobar\settings.json` and `%LOCALAPPDATA%\Isobar\Cache\`
- macOS: `~/Library/Application Support/Isobar/settings.json` and `~/Library/Caches/Isobar/`
- Android: inside the app's private storage. Clearing the app's storage in Android settings resets it.

## Making a release

The workflow in `.github/workflows/build.yml` builds Linux (as a folder and an AppImage), Windows and macOS on GitHub's runners with PyInstaller, builds and signs the Android APK with Gradle, attests the APK, then uploads all of it to an existing release. It only builds when you run it by hand. Pushes to `main` and pull requests run the checks (tests, syntax and shellcheck) and nothing else, and pushing a tag does nothing at all.

The Android build needs a signing key in the repo's secrets first. That's a one-time setup, covered in "Signing the Android app". Until it's done, the Android job fails with a message pointing there, and the desktop builds still go through.

1. Bump the version in `isobar/__init__.py`. The macOS bundle and the Android build both read it from there, and the release job refuses a tag that doesn't match it.
2. Add an entry to `changes.md`.
3. Commit and push, or upload the changed files through the web UI.
4. Go to Releases, then "Draft a new release".
5. Under "Choose a tag", type a new tag like `v1.5.0` and pick "Create new tag on publish". Give the release a title and notes.
6. Click "Publish release". Nothing builds yet, which is expected.
7. Go to Actions and click "Build and release" in the list on the left.
8. Click "Run workflow". Under "Use workflow from", switch to the Tags tab and pick the tag you just made. Click the green "Run workflow".
9. Wait. The checks run first, then all four builds in parallel, roughly 10 to 15 minutes in all. If the checks fail, nothing builds.
10. Refresh the release page. The `.AppImage` and its `.zsync` update file, the `.tar.gz`, two `.zip` files, the `.apk` and its `.apk.sigstore.json` attestation are attached.

If the release job fails right away, read its message. It says whether you ran it on a branch instead of a tag, whether no release exists for that tag yet, or whether the tag doesn't match the version in `isobar/__init__.py`. Running it again on the same tag replaces the files that are already attached, so a rerun after a failed build is safe.

For a test build without a release, run the workflow on `main` instead of a tag. The builds show up as artifacts at the bottom of that run's page, and the release job fails with the "not a tag" message, which is expected.

### Building the AppImage yourself

The workflow does this on its own. To do it on your machine, from a source checkout with the virtual environment active:

1. Install PyInstaller: `pip install pyinstaller`
2. Build the app folder: `pyinstaller --noconfirm --clean isobar.spec`
3. Wrap it: `bash packaging/appimage/build-appimage.sh v1.5.0` (the last part is only the label in the file name)

The AppImage and its `.zsync` land in the repo root. The script downloads `appimagetool` from its "continuous" release the first time, so it always gets the current one. The tradeoff is that nothing checks that download against a known hash. If you already have `appimagetool`, point the script at it with `APPIMAGETOOL=/path/to/appimagetool` and it skips the download.

If you upload the repo through the web UI and the `.github` folder doesn't come along (some file pickers hide dot folders), use "Add file", then "Create new file", type `.github/workflows/build.yml` as the name, and paste the file in.

## Signing the Android app

Android only installs signed APKs, and it only installs an update if it's signed with the same key as the version already on the phone. So you make one key, keep it forever, and hand it to GitHub as secrets.

BACK UP THE KEYSTORE FILE AND ITS PASSWORD SOMEWHERE SAFE. If you lose either one, you can never publish an update that installs over the existing app. Everyone, you included, would have to uninstall and lose their settings to move to a build signed with a new key.

Prerequisites: `keytool`, which comes with any Java JDK. I use openSUSE, so in my case that's `sudo zypper install java-21-openjdk-headless`. On other systems install whichever OpenJDK package your package manager has.

1. Make the key: `keytool -genkeypair -v -keystore isobar-release.jks -alias isobar -keyalg RSA -keysize 4096 -validity 10000`
2. Answer the prompts. Pick a strong password. The name and organization questions can be anything, since they only show up in the certificate.
3. Turn the keystore into text GitHub can store: `base64 -w0 isobar-release.jks > isobar-release.jks.b64`
4. In the repo on GitHub, go to Settings, then "Secrets and variables", then Actions.
5. Click "New repository secret" and add these four:
   - `ANDROID_KEYSTORE_BASE64`: the entire contents of `isobar-release.jks.b64`
   - `ANDROID_KEYSTORE_PASSWORD`: the password you picked
   - `ANDROID_KEY_ALIAS`: `isobar`
   - `ANDROID_KEY_PASSWORD`: the same password again, since keytool's default keystore type uses one password for both
6. Delete `isobar-release.jks.b64`, since it's the key in plain text: `rm isobar-release.jks.b64`
7. Move `isobar-release.jks` into your backups. Don't put it in the repo. The `.gitignore` blocks `*.jks` files, but the web UI upload doesn't read `.gitignore`, so be careful what you drag in.

The workflow decodes the key onto the runner, signs the APK, checks the signature with `apksigner`, and deletes the key file when the job ends.

### Building the APK yourself

You'll need the Android SDK with the Android 17 (API 37) platform (Android Studio installs it), JDK 17 or newer, and Python 3.12 on your PATH, since Chaquopy compiles the Python side with a matching Python. The Gradle wrapper downloads Gradle 9.6 on its own the first time.

1. Go into the Android project: `cd android`
2. Build a debug APK, signed with Android's throwaway debug key: `./gradlew assembleDebug`
3. Install it on a phone with USB debugging on: `adb install -r app/build/outputs/apk/debug/app-debug.apk`

A debug build and a release build have different signatures, so one won't install over the other. Uninstall first when you switch.

## Where the data comes from, and the limits

- **Forecast, hourly data, air quality and the model wind/temperature maps**: [Open-Meteo](https://open-meteo.com/). Works worldwide. Free for non-commercial use with a daily request cap that one person won't come close to.
- **Alerts, observations, forecast text, discussions**: the [National Weather Service API](https://www.weather.gov/documentation/services-web-api). US only.
- **Radar, rainfall estimates, HRRR future radar, NEXRAD storm cells, county lines**: the [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/). US only. IEM is a university service run as a public good, so please don't point a hundred copies of this at it from one office.
- **Satellite loop**: GOES East from [NOAA NESDIS](https://www.nesdis.noaa.gov/imagery/interactive-maps). If that service doesn't answer, Isobar shows IEM's latest single satellite image instead.
- **Hurricane cones and tracks**: the National Hurricane Center, through NOAA's tropical map service.
- **Warning and watch polygons on the map**: NOAA's watch/warning/advisory map service.
- **Mesoscale discussions**: NOAA's SPC mesoscale discussion map service. Isobar shows the outline and links to the full text on spc.noaa.gov.
- **Storm reports**: the NWS local storm reports, through the [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/). Reports are preliminary and often get corrected later.
- **Outlooks**: the [Storm Prediction Center](https://www.spc.noaa.gov/). US only.
- **Base maps**: Esri satellite imagery by default. CARTO and OpenStreetMap street maps if you add your own free key (see "Optional: a map key for the street maps"); the free tier allows 5 million tile requests a month, which one person panning a radar map won't get near.

Things you should know before relying on it:

- The Android app is a thin shell: Python runs inside it through [Chaquopy](https://chaquo.com/chaquopy/) and serves the same UI to a WebView. That makes it bigger than a native app would be (Python itself is in there), and the first launch after an install or update takes a few seconds while it unpacks.
- Outside the US you get the forecast and the model maps. Radar, alerts, outlooks, storm reports and discussions are blank.
- Storm cells are what each radar's own algorithm flagged. A tornado vortex signature is a radar signature, not a confirmed tornado, and the arrows are a straight-line guess at where a cell will be in 30 minutes.
- The satellite loop is GOES East only. It covers the lower 48, but the West Coast is seen at a slant.
- The custom color modes only apply to reflectivity. Velocity, rotation, echo tops, rainfall, future radar and satellite use the source colors and the legend shows them.
- The Wind and Temperature maps are model output from Open-Meteo, not station observations. They can be off by a few degrees or a few mph, and they don't show storm-scale gusts.
- Single-radar products update as fast as IEM processes them, usually within a few minutes of the scan. That's fine for watching weather. It isn't a replacement for a Level II viewer like GR2Analyst if you're tracking a tornado in real time.
- If any of these services go down or change their formats, the matching part of the app goes blank until it's fixed. The rest keeps working.
- The download is big: the AppImage is about 180 MB, and the folder is about 440 MB unpacked. Almost all of that is Qt WebEngine (a full Chromium engine). That's the price of a real map and chart renderer in a desktop window. If that bothers you, run from source with `--browser`.
- I beg you not to treat this as your only warning source. Have a NOAA weather radio or WEA alerts on your phone too. On the desktop Isobar only notifies you while it's running, and on Android the background check can run late when the phone is saving battery.

## License

GPL-3.0. See [LICENSE](LICENSE). Leaflet is BSD-2-Clause and IBM Plex Sans is under the SIL Open Font License; their license files are in `isobar/web/vendor/leaflet/` and `isobar/web/fonts/`.

Once it's installed and you've searched your town, Isobar sits in your tray (or in your phone's background) showing the current conditions, the radar loop and any warnings for your spot, and pings you when a new warning drops. Tinker with the themes, palettes and overlays as you see fit.

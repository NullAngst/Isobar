# Isobar

Isobar is a desktop weather and radar app for people who want the forecast, live radar, warnings and SPC outlooks in one clean window, without paying for RadarOmega or digging through the NWS website.

Type in a city, ZIP code or `lat, lon` and you get current conditions, an hourly chart, a 10-day forecast, active alerts, the local forecast discussion, a radar map and the Storm Prediction Center outlooks for days 1 through 8.

## What's in it

- **Now**: current conditions, NWS forecast text, today's SPC risk, wind, humidity, pressure trend, visibility, UV, air quality, the next 24 hours as a chart, and a 10-day list you can expand for detail.
- **Hourly**: 24 hours, 48 hours or 7 days as a chart plus a full table.
- **Radar**: reflectivity (national mosaic or a single radar), velocity, rotation (storm-relative velocity), echo tops, radar-estimated rainfall (1, 24, 48, 72 hours), future radar (HRRR), satellite (infrared, visible, water vapor), and model wind and temperature maps. Loops, play and step with space and the arrow keys.
- **Radar sites**: every NEXRAD site is a dot on the map. Click one to see just that radar, click it again (or "Back to all radars") to return to the national mosaic. Velocity and rotation start on your nearest radar.
- **Radar overlays**: warnings, watches, advisories, the SPC day 1 outlook and county lines.
- **Reflectivity color modes**: IEM default, NWS classic, smooth, soft, color-blind safe and grayscale, plus a cutoff to hide weak echoes and ground clutter.
- **Outlooks**: SPC days 1 to 8, with categorical, tornado, wind, hail and any-severe maps, hatched significant areas, and the risk at your exact location.
- **Alerts**: everything active for your location, full text, and a button that jumps to the polygon on the radar.
- **Discussion**: your NWS office's Area Forecast Discussion, cleaned up for reading.
- **Desktop notifications** for new warnings at your saved location, and an optional tray mode so it keeps watching with the window closed.
- Dark, light, midnight or follow-the-system themes, US or metric units, 12 or 24 hour clock, saved places.

## Download and run

Grab the archive for your system from the [Releases](https://github.com/NullAngst/Isobar/releases) page.

On Linux and Windows, KEEP THE `Isobar` PROGRAM INSIDE ITS FOLDER. It loads everything else from the `_internal` folder next to it, so if you move the program out on its own it won't start. Put the whole folder wherever you like and make a shortcut or symlink to the program instead.

### Linux

Prerequisites: a desktop session (X11 or Wayland) and a few system libraries that Qt WebEngine expects the OS to provide. Most desktops already have them.

I run openSUSE, so in my case that's:

```
sudo zypper install libxcb-cursor0 libxkbcommon-x11-0 mozilla-nss libXcomposite1 libXdamage1 libXrandr2 libXtst6 libasound2
```

On Debian or Ubuntu it's `sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libasound2`, or whichever names your distro uses for the same libraries.

1. Download `Isobar-<version>-linux-x86_64.tar.gz`.
2. Extract it: `tar -xzf Isobar-*-linux-x86_64.tar.gz`
3. Move the folder somewhere permanent: `mv Isobar ~/.local/share/` since that's where per-user apps usually live.
4. Run it: `~/.local/share/Isobar/Isobar`
4.5. Optional: link it onto your PATH with `ln -s ~/.local/share/Isobar/Isobar ~/.local/bin/isobar`

If the window opens blank or it exits right away, try `Isobar --no-sandbox`. Some setups (containers, hardened kernels, some Flatpak-style sandboxes) block Chromium's own sandbox. If it's a VM or the GPU driver is the problem, add `--software-gl`.

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

## Set up the map key

The dark, light and street base maps come from CARTO, and CARTO now requires a free API key. Without one the maps still load, but every tile is stamped "API KEY REQUIRED".

1. Go to [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey/) and request a key. No account needed, they email it straight back.
2. In Isobar, open Settings and find Maps.
3. Paste the key into "CARTO API key" and press Enter.

The radar and outlook maps reload with the key right away. The key is saved in plain text in `settings.json` (see below), which is fine, since it's sent in every tile URL anyway and grants nothing but basemap tiles.

A wrong key looks exactly like no key: you still get the watermark. If it's still stamped after you paste it, check the key for a stray space or a missing character. If you'd rather skip CARTO entirely, set Map style to Satellite, which comes from Esri and needs no key.

## Run from source

Prerequisites: Python 3.10 or newer and `git`.

1. Clone it: `git clone https://github.com/NullAngst/Isobar.git`
2. Go in: `cd Isobar`
3. Make a virtual environment: `python3 -m venv .venv`
4. Activate it: `source .venv/bin/activate` (on Windows, `.venv\Scripts\activate`)
5. Install the two dependencies: `pip install -r requirements.txt`
6. Run it: `python run.py`

If Qt WebEngine won't install or won't start on your machine, `python run.py --browser` serves the same UI to your default browser instead. You lose the tray and notifications, everything else works.

## Command line options

| Option | What it does |
| --- | --- |
| `--browser` | Open the UI in your default browser instead of the app window |
| `--port N` | Use a fixed local port instead of a random one |
| `--no-sandbox` | Turn off Chromium's sandbox, for setups that block it |
| `--software-gl` | Render without the GPU, for VMs and broken drivers |
| `--debug` | Verbose logs, plus a web inspector on port 9222 |
| `--version` | Print the version and exit |

## Where it keeps things

Settings (units, theme, saved places, radar choices) live in one JSON file. The cache holds map tiles and web data and is safe to delete.

- Linux: `~/.config/isobar/settings.json` and `~/.cache/isobar/`
- Windows: `%APPDATA%\Isobar\settings.json` and `%LOCALAPPDATA%\Isobar\Cache\`
- macOS: `~/Library/Application Support/Isobar/settings.json` and `~/Library/Caches/Isobar/`

## Building releases

The workflow in `.github/workflows/build.yml` builds Linux, Windows and macOS on GitHub's runners with PyInstaller, then attaches the archives to the release. I do this from the GitHub web UI, so that's what these steps use.

1. Bump the version in `isobar/__init__.py` and `CFBundleShortVersionString` in `isobar.spec`, since both show up in the app.
2. Commit and push, or upload the changed files through the web UI.
3. Go to Releases, then "Draft a new release".
4. Make a new tag like `v1.0.1` and give the release a title.
5. Click "Publish release".
6. Wait. All three builds run in parallel and take roughly 10 to 15 minutes. Progress is on the Actions tab.
7. Refresh the release page. The `.tar.gz` and two `.zip` files are attached.

For a test build without making a release, go to Actions, pick "Build and release", and click "Run workflow". The archives show up as artifacts at the bottom of that run's page.

If you upload the repo through the web UI and the `.github` folder doesn't come along (some file pickers hide dot folders), use "Add file", then "Create new file", type `.github/workflows/build.yml` as the name, and paste the file in.

## Where the data comes from, and the limits

- **Forecast, hourly data, air quality and the model wind/temperature maps**: [Open-Meteo](https://open-meteo.com/). Works worldwide. Free for non-commercial use with a daily request cap that one person won't come close to.
- **Alerts, observations, forecast text, discussions**: the [National Weather Service API](https://www.weather.gov/documentation/services-web-api). US only.
- **Radar, satellite, rainfall estimates, HRRR future radar, county lines**: the [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/). US only. IEM is a university service run as a public good, so please don't point a hundred copies of this at it from one office.
- **Warning and watch polygons on the map**: NOAA's watch/warning/advisory map service.
- **Outlooks**: the [Storm Prediction Center](https://www.spc.noaa.gov/). US only.
- **Base maps**: CARTO and OpenStreetMap, plus Esri for satellite imagery. CARTO needs your own free key (see "Set up the map key"); the free tier allows 5 million tile requests a month, which one person panning a radar map won't get near.

Things you should know before relying on it:

- Outside the US you get the forecast and the model maps. Radar, alerts, outlooks and discussions are blank.
- The custom color modes only apply to reflectivity. Velocity, rotation, echo tops, rainfall, future radar and satellite use the source colors and the legend shows them.
- The Wind and Temperature maps are model output from Open-Meteo, not station observations. They can be off by a few degrees or a few mph, and they don't show storm-scale gusts.
- Single-radar products update as fast as IEM processes them, usually within a few minutes of the scan. That's fine for watching weather. It isn't a replacement for a Level II viewer like GR2Analyst if you're tracking a tornado in real time.
- If any of these services go down or change their formats, the matching part of the app goes blank until it's fixed. The rest keeps working.
- The download is big, about 440 MB unpacked on Linux. Almost all of that is Qt WebEngine (a full Chromium engine). That's the price of a real map and chart renderer in a desktop window. If that bothers you, run from source with `--browser`.
- I beg you not to treat this as your only warning source. Have a NOAA weather radio or WEA alerts on your phone too. Isobar only notifies you while it's running.

## License

GPL-3.0. See [LICENSE](LICENSE). Leaflet is BSD-2-Clause and IBM Plex Sans is under the SIL Open Font License; their license files are in `isobar/web/vendor/leaflet/` and `isobar/web/fonts/`.

Once it's installed and you've searched your town, Isobar sits there showing the current conditions, the radar loop and any warnings for your spot, and pings you when a new warning drops. Tinker with the themes, palettes and overlays as you see fit.

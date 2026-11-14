# Maintenance

A checklist for keeping Isobar healthy, meant to be gone through every couple of months, and any time part of the app goes blank. Most of what breaks in a weather app is an upstream service changing something, so most of this is checking those.

## Every couple of months

### 1. Run the checks

From a source checkout:

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `python -m unittest discover -s tests -t .`
4. `python run.py` and click through every view with a US location. Look for anything blank.

The tests use canned answers and never touch the network, so they pass even when an upstream service has changed. Step 4 is the one that catches that.

### 2. Check each upstream service

Open Isobar on a US location with weather going on (or anywhere, if the sky is quiet) and confirm each of these shows something. If one is blank, the matching function in `isobar/sources.py` is where to look, and `python run.py --debug` logs every upstream error.

| What | Where it comes from | Function |
| --- | --- | --- |
| Forecast, hourly, 10 day | api.open-meteo.com/v1/forecast | `open_meteo` |
| Rain timing line | Open-Meteo `minutely_15` | `nowcast` |
| Air quality | air-quality-api.open-meteo.com | `air_quality` |
| Alerts | api.weather.gov/alerts/active | `nws_alerts` |
| NWS text forecast, office, radar | api.weather.gov/points | `nws_point` |
| Discussion and hazards | api.weather.gov/products | `nws_discussion` |
| SPC outlooks | spc.noaa.gov/products/outlook/*.lyr.geojson | `spc_outlook` |
| Mesoscale discussions | mapservices.weather.noaa.gov spc_mesoscale_discussion | `spc_mcds` |
| Warning shapes | mapservices.weather.noaa.gov watch_warn_adv | `wwa` |
| Storm reports | mesonet.agron.iastate.edu/geojson/lsr.geojson | `storm_reports` |
| Storm cells | mesonet.agron.iastate.edu/geojson/nexrad_attr.py | `storm_attrs` |
| HRRR run time | mesonet.agron.iastate.edu/data/gis/images/4326/hrrr/refd_1080.json | `hrrr_run` |
| Satellite loop | satellitemaps.nesdis.noaa.gov ABI*_Last_24hr ImageServer | `sat_info` |
| Hurricane cones | mapservices.weather.noaa.gov NHC_tropical_weather_summary layers 6 and 7 | `tropical` |
| Radar tiles | mesonet*.agron.iastate.edu tile.py | `radar.js` |

Things that have changed before, or are likely to:

- **SPC intensity files** have been renamed once (sig* to cig*). `_spc_urls` tries both. If hatching disappears, check the file names on spc.noaa.gov/products/outlook.
- **NOAA map service layer numbers** (the hurricane cone is layer 7, the track layer 6) can shift when NOAA republishes a service. Open the service URL with `?f=pjson` in a browser and check the layer list.
- **NESDIS service names** (ABI13_Last_24hr, ABIGC_Last_24hr, ABI10_Last_24hr) are in `SAT_LAYERS`. The service directory is at satellitemaps.nesdis.noaa.gov/arcgis/rest/services.
- **IEM storm attribute field names** are read case-insensitively, but if cells stop showing, compare the GeoJSON's property names with `storm_attrs`.
- **Hurricane cones** only show during an active storm, so test them in season (June to November) or check the service returns an empty list without an error.

### 3. Update the dependencies

1. `pip install --upgrade PySide6 requests pyinstaller` in the virtual environment, then step 1 again.
2. Leaflet lives in `isobar/web/vendor/leaflet/`. Check leafletjs.com for a new 1.x release, replace `leaflet.js` and `leaflet.css`, and click through the radar and outlooks. `radar.js` reaches into a few Leaflet internals (`_tiles`, `_tileZoom`) for the pointer readout, so test that after an upgrade.
3. Android: in `android/build.gradle.kts` and `android/app/build.gradle.kts`, check for newer versions of the Android Gradle plugin, Chaquopy, androidx.appcompat, androidx.core, androidx.work and androidx.activity. The Chaquopy Python version (3.12) has to match the `python-version` in the workflow's Android job.
4. GitHub Actions: the workflow uses major version tags (`actions/checkout@v4` and so on). When GitHub announces a new major version or deprecates a Node runtime, bump the number. The Actions tab shows deprecation warnings on every run, so a glance at the last run tells you.
5. `appimagetool` is downloaded from its continuous release on every build, so it updates itself.

### 4. Check Android against the current OS

1. Install the latest APK on a phone with the newest Android you have.
2. Confirm the notification permission prompt appears on a fresh install, and that a test alert (pick a place with an active advisory and set Notify me to "All alerts") arrives within about 15 minutes.
3. Confirm "Use my location" works and asks only for approximate location.
4. When Google raises the minimum target SDK for new installs, raise `compileSdk` and `targetSdk` in `android/app/build.gradle.kts`.

### 5. Prune and look at sizes

1. The AppImage should stay around 180 MB. If it jumps, `isobar.spec` probably started pulling in a Qt module it doesn't need. Add its prefix to `DROP_MODULES`.
2. On your own machine, Settings, Maps shows the tile cache size. It caps itself at about 6,000 tiles.

## When something breaks between check-ups

1. Run with `python run.py --debug` (or the AppImage with `--debug`) and watch the log. Upstream failures log as "upstream error" or "backing off".
2. A service backing off clears itself; the UI shows the last good answer with its age in the meantime.
3. If one service changed its format, fix the parser in `sources.py`, add a test with a canned copy of the new answer in `tests/test_sources.py`, and make a release.

## Making a release after maintenance

Bump `__version__` in `isobar/__init__.py`, add an entry to `changes.md`, then follow "Making a release" in the README.

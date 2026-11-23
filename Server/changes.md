# Server changes

2026.11.23 - Build fixes

* Geocoding image: the geocoder's .deb is now unpacked into a scratch folder and only its two programs are copied out. Unpacking it over / replaced Ubuntu's /lib link with the package's own lib folder (it ships a systemd unit there), which removed every system library and broke the build.
* Relay image: the build now installs a compiler and the eccodes library before installing the Python packages, and removes the compiler afterwards. The untagged base moved to Python 3.15, and pygrib (which pyIEM needs) had no prebuilt wheel for it yet, so it has to compile.

Reasons for changes: both images failed their first build in GitHub Actions.

2026.11.20 - First release

* The relay: one Python service (aiohttp) in front of everything, with keys from keys.txt reloaded every 10 seconds, silence for anything that isn't an Isobar app with a working key, a per-key rate limit, and a per-address wrong-key limit.
* Alerts: the national NWS snapshot polled once a minute with conditional requests, filtered per point by the relay using NWS zone shapes, with IEM's current warnings as the backup after 5 minutes of NWS failures, expired alerts dropped even from a stale copy, and the snapshot saved to disk across restarts.
* Alert shapes for the map, with zone-only alerts drawn as the union of their zones.
* NWWS-OI ingest with nwws-oi-receiver and pyIEM, off until credentials are set.
* Forecasts from a self-hosted Open-Meteo: HRRR, NBM and GFS synced by one worker each, stitched by the relay, never the public API. GFS syncs both its 0.11 and 0.25 degree grids, and GEFS rain chances sync for where NBM doesn't reach.
* Air quality from CAMS global, synced into the same Open-Meteo.
* Place search from Open-Meteo's geocoder in its own container.
* Station observations from the NWS with IEM as the backup.
* MRMS fallback radar: GDAL renders reflectivity in IEM's own colors to XYZ tiles, zoom 3 to 8, every 2 minutes, keeping 12 frames.
* /v1/status with every dataset's age, source, last error and the forecast models' reach.
* Images built by GitHub Actions on workflow_dispatch against a release tag, pushed to GHCR as latest and the version.

Reasons for changes: upstream weather endpoints keep going down, and the app could only say "Can't connect". A server someone runs for their friends turns an outage into slightly old data, and its own Open-Meteo means it doesn't lean on the public API to do it.

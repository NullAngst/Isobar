# Server changes

2026.11.23 - Geocoder crash fix

* Geocoding image: tzdata is now installed. The geocoder looks up the GMT time zone when it starts and crashes without the zone files, which bare Ubuntu doesn't have. That was the exit 132 (x86) and 133 (ARM) in the build's smoke test. The earlier .deb-based image had the same bug; it just never got started during a build.
* Geocoding image: the build now starts the geocoder once and fails if it crashes. With no database yet it exits with 1, which counts as a pass.
* Workflow: the separate geocoder smoke test is gone, since the Dockerfile check covers it and handles the no-database exit correctly.

Reasons for changes: the v1.8.0 image build failed the geocoder smoke test on both CPUs. Reproduced with Open-Meteo's own x86 binary by hiding /usr/share/zoneinfo.

2026.11.23 - ARM support

* Every image the stack runs now comes in x86 and ARM (linux/amd64 and linux/arm64), under the same `latest` tag. Docker picks the right one on pull.
* The image workflow builds each image twice, on a normal runner and on GitHub's ARM runner, then joins the two into one multi-arch tag with `docker buildx imagetools create`. No emulation.
* Geocoding image: the geocoder is now compiled from source in Swift's official image with the same build command Open-Meteo's release uses, then copied onto plain Ubuntu 22.04. Open-Meteo only ships the geocoder as an x86 .deb, so there was nothing to unpack on ARM. The final image checks the program's libraries again on the system that runs it.
* Relay and radar images needed no changes: every compiled Python package the relay uses has ARM wheels except pygrib, which already compiles on both, and GDAL's image is multi-arch.
* Version 1.8.0, since the image workflow checks the tag against the version.
* Hosting guide: the CPU requirement now covers ARM. AVX2 only applies on x86.

Reasons for changes: the stack wouldn't pull on an ARM server ("no matching manifest for linux/arm64/v8"). Open-Meteo's own image already ships for ARM, so the three Isobar images were the only thing missing.

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

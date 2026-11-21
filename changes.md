# Changes

2026.11.20 - Isobar Server support / Radar fix

* Settings, Servers: add one or more Isobar Servers (label, address, key, on or off), reorder them by dragging on the desktop or with the arrows on Android, and Test each one. Test tells apart "the server is down" from "the server is up but isn't answering this key", since a server never says "wrong key".
* Forecasts, place search, air quality, alerts, observations and the map's warning shapes walk the server list in order before going direct: 5 seconds per server, a fresh answer wins, a stale one is held while the next server is tried, three failures in a row put a server in a 5 minute cooldown, a 429 is waited out, and a key that isn't answering is skipped until it's edited. With no servers set, Isobar makes exactly the same requests as 1.6.0.
* "Go direct when every server fails", on by default. Off makes Isobar use servers only. If nothing fresh comes back, the freshest copy seen stays on screen with its age.
* Every panel that came from a server says so: "Updated 3 min ago, via VPS" or "direct", in red once it's past the source's stale threshold. Alerts get a banner when a server's alert feed is stale ("Alert feed unavailable since 9:10 PM. New warnings may be missing.") or coming from its IEM backup.
* Radar falls back to MRMS reflectivity from the first server that runs radar when IEM's radar fails (a frame with no tile after 6 seconds, two failed frames in a row, or a newest scan over 15 minutes old), shows "Radar: MRMS via <server>" while switched, and tries IEM again every 5 minutes. The MRMS tiles use IEM's own colors, so the palette and cutoff settings still work.
* Notifications recognize the same alert by its VTEC event when its id changes, so switching between the NWS feed, a server's backup feed or NWWS-OI doesn't ping twice.
* Fixed: every radar product drew nothing. IEM moved its tiles off mesonet1 to mesonet3 onto mesonet.agron.iastate.edu, and the old hosts answer with a redirect that carries no CORS header, which broke both the direct load and the proxy retry. Tiles now load from the one host.
* Every radar product (not only reflectivity) now retries a failed tile through Isobar's proxy, and the proxy follows redirects that stay on agron.iastate.edu, so the next host move slows radar down instead of breaking it.
* A radar frame where most tiles failed is marked failed instead of loaded: the timeline says so, "Radar tiles failed to load" shows next to it, and the console names the URL and why.
* The Isobar Server itself lives under Server/, with its own README, maintenance.md and changes.md, and its images build with their own workflow.

Reasons for changes: upstream weather endpoints keep going down, and the app could only say "Can't connect". Servers turn an outage into slightly old data. The radar fix ships with this release because the MRMS fallback switches on failed frames, and Leaflet's load event couldn't tell a failed frame from a loaded one. Note that a server's forecast stitches NOAA's own models, so its numbers can differ a little from the direct Open-Meteo numbers; the source line says which you're looking at.

2026.11.15 - Android widgets / Network reliability

* Home screen widgets for the Android app: a small one (temperature, conditions, place, the most serious warning or watch) and a wide one that adds high and low, feels like, rain chance, last update time and the next five hours. Tapping opens the app; tapping the alert line opens Alerts.
* Widget settings, under Settings, Widgets on Android: the widget's place (same as the app, or any starred place), its theme (same as the app, matching the phone, or any app theme), a solid, see-through or no background, and whether to show alerts, details and the hours row.
* Widgets refresh after each background alert check (about every 15 minutes), whenever the app loads the forecast or a widget setting changes, and when Android asks (every 30 minutes at most). The last data is kept so Android can redraw them without starting Python.
* Weather icons for the widgets as Android vector drawables, matching the app's icon set.
* Weather data requests (JSON and text) open a fresh connection every time instead of reusing a kept-open one. Map tiles still reuse connections.
* Separate timeouts: 10 seconds to connect, 15 seconds of silence while waiting for data.
* A request that times out, loses its connection, or stops partway through the body is retried once on a fresh connection before it counts as a failure. Only a request that fails twice in a row starts a backoff.
* Fixed: a connection that stalled partway through a response raised a raw error that skipped the stale-copy fallback, so the UI showed an error even when a recent copy was available.
* Fixed: long checkbox labels in the settings drawer dropped below their checkbox since 1.5.0.

Reasons for changes: after 1.5.0 the Android app showed "Forecast unavailable" with a ReadTimeout from Open-Meteo for a while, even though Open-Meteo answered the PC and the phone's browser instantly. The likely cause is kept-open connections that died while the phone slept or switched networks; 1.5.0 opened two Open-Meteo connections per refresh, leaving more of them to go stale. Fresh connections plus one retry make a single stall invisible. Widgets were asked for, for the phone.

2026.11.14 - Alerts overhaul / Radar performance / Android notifications / Release workflow

* The 1.4.1 changes that had been uploaded to the repo root (stacked-polygon popups on the radar, the radar product panel remembering whether it's open) are now in `isobar/` where the build picks them up. The stray root copies are gone.
* Backoff is now kept per service path (host plus first path segment), so a failing NWS forecast endpoint no longer blocks NWS alerts. Plain 500s from the NWS API no longer trigger backoff at all; 429 and 503 still do.
* Alerts are requested for any point inside the NWS coverage area even when the NWS /points lookup fails, instead of the Alerts view claiming there's no coverage.
* Alerts and the forecast carry the time they were last confirmed upstream. Alerts show a notice when they failed to load or are more than 5 minutes old, and the top bar says "Forecast from N min ago" in red when Open-Meteo stops answering.
* Alert requests ask for status=actual, so test messages never notify.
* Alerts carry the NWS impact tags (tornado radar indicated or observed, hail size, peak gust, damage threat) and flag Tornado Emergency, Flash Flood Emergency and PDS. They sort most dangerous first.
* Notifications: tiers (critical, high, normal), a sound and a notification that stays up for tornado and extreme wind warnings, emergencies and PDS warnings, quiet hours that only critical alerts get through, notifications for saved places with the bell on, a single "can't reach the NWS" notice after 15 minutes of failures, and an update that gets worse notifies again.
* What's already been notified is saved to disk (notify-seen.json in the cache folder), so a restart doesn't repeat every active warning.
* Only one copy of the desktop app runs per user. A second launch raises the first window.
* Without a tray, notifications go through notify-send when it's installed, instead of only flashing the task bar.
* New --hidden option and a "Start at login, hidden in the tray" setting, using each system's own autostart (Linux .desktop file, Windows Run key, macOS LaunchAgent).
* Window title and tray tooltip show the place, temperature and active warnings. The tray icon gets a yellow or red dot for watches and warnings.
* A warning strip under the top bar on every view, with an "expires in N min" countdown for alerts within 3 hours of expiring. Countdowns also show on banners and alert cards.
* Refreshes no longer jump the Now, Forecast, Alerts or Discussion views back to the top, and opened 10-day rows stay open.
* SPC risk on Now, the 10-day list and the outlook day tabs is matched by date, so the hour or two after midnight no longer shows yesterday's outlook as today.
* Watch and warning queries are split at the antimeridian.
* The JSON cache has a byte cap (48 MB of response bodies).
* CIG1 intensity areas are labeled "Intensity level 1", like CIG2 and CIG3.
* Radar refreshes keep frames already loaded and only add the newest scan. Frames load one or two at a time, the one on screen first, and zooming reloads the other frames in turn instead of all at once. Play waits briefly for a frame that's still loading.
* The wind and temperature maps ask Open-Meteo for at most 48 points per request, down from 99.
* Radar overlays (warnings, watches, advisories, discussions, reports, storm cells, tropical, outlook) show as chips above the timeline. A failed one turns red, the layers button gets a red dot, and it retries after 30 seconds.
* New overlays: NEXRAD storm cells from IEM (TVS, mesocyclone, hail, with 30 minute motion arrows), NHC hurricane cones and tracks, and optional TO/SV/FF labels on warning shapes.
* Future radar (HRRR) names its model run, so every frame shows its real valid time and starts on the frame closest to now.
* Satellite is now a loop from NOAA NESDIS's 24 hour GOES East archive (infrared, GeoColor, water vapor), falling back to IEM's latest image.
* Pointer readout on the radar: dBZ under the mouse and the distance and direction from your place.
* The radar's newest-frame time turns red when the scan is more than 15 minutes old.
* Now shows when rain or snow starts or stops in the next 3 hours (Open-Meteo 15 minute data) and how the temperature compares with the same hour yesterday.
* The forecast discussion marks paragraphs that are new or changed since the previous issuance, with a toggle.
* Android: background alert checks every 15 minutes with three notification channels, a notification permission prompt on Android 13 and later, tapping a notification opens Alerts, "Use my location" and "Follow my location" with coarse location only.
* Themes: verdigris, Kawaii Light and Kawaii Dark.
* Keyboard: "?" opens a list of shortcuts, "r" refreshes. The settings drawer and the shortcuts dialog keep Tab inside them and give focus back when they close.
* Release workflow: builds only run by hand (workflow_dispatch). Pushes to main and pull requests run the checks: tests, Python compile, JS parse and shellcheck. The release job refuses a branch, a missing release or a tag that doesn't match the version, and uploads with `gh release upload --clobber` instead of a third-party action.
* The Android APK gets a signed SLSA build provenance attestation, attached next to it as a .sigstore.json bundle.
* AppRun uses APPDIR from the AppImage runtime instead of `readlink -f`. Both packaging scripts follow the shell conventions (bash, printf, command checks) and pass shellcheck.
* Added maintenance.md and this file.

Reasons for changes: a code review found that the 1.4.1 work never shipped, that one NWS forecast error could silence alerts for up to 15 minutes, and that a failed /points lookup told US users they had no alert coverage. The rest is the feature, performance and UI work from that review, plus bringing the release flow in line with how releases are made.

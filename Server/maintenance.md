# Isobar Server maintenance

A checklist for keeping a server healthy, meant to be gone through every couple of months, and any time an app reports the server as stale or unreachable. Most of what breaks is an upstream service or image changing something, so most of this is checking those. Commands assume you're in the stack's folder (`/opt/isobar` in my case).

## Every couple of months

### 1. Look at the status

```
curl -s -A "Isobar/check" -H "Authorization: Bearer <key>" https://weather.example.net/v1/status | python3 -m json.tool
```

Check:

- `datasets.alerts.fetched_at` is within the last couple of minutes and `stale` is false.
- `datasets.alerts.zones_loaded` is true. False means the NWS zone shapefiles haven't downloaded; points still get alerts through the NWS /points lookup, but marine zones and the map's zone shapes need the files.
- `datasets.forecast.models`: each model's `horizon_hours` should be roughly HRRR 15 to 48, NBM 200 or more, GFS 300 or more. A number that keeps shrinking over a day means that model's sync worker stalled.
- `capabilities` lists radar (unless you turned it off), air_quality, and nwws if you set credentials.
- `upstreams` is empty. Anything listed there is failing right now, with when it started and the last error.

### 2. Read the log for state changes

```
docker compose logs --since 168h isobar-relay | grep -E "failing since|recovered|NWWS-OI|zone shapes|keys.txt"
```

The relay logs one line when an upstream starts failing and one when it recovers, so a week of outages fits on a screen. Every ten minutes it also logs how many NWS alert polls came back 304 Not Modified; most of them should, which means the conditional requests are working.

### 3. Update the images

```
docker compose pull
docker compose up -d
docker compose ps
```

Every service should be running (om-elevation exits after its one sync; "exited (0)" is right for it). Then step 1 again.

### 4. Check Open-Meteo after an update

The Open-Meteo image runs untagged, so an update can change the sync command, the stored variable names or the storage format.

1. `docker compose logs --since 1h om-sync-nbm om-sync-gfs om-sync-hrrr om-sync-gefs om-sync-cams | grep -iE "error|warn"` should be quiet.
2. `docker compose exec open-meteo-api ./openmeteo-api sync --help` lists the sync options. Compare with the commands in compose.yml (`--past-days`, `--repeat-interval`, `--concurrent`).
3. Ask the local API for the fields Isobar uses, from inside the stack:

   ```
   docker compose exec isobar-relay python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://open-meteo-api:8080/v1/forecast?latitude=39&longitude=-98&models=ncep_nbm_conus&current=temperature_2m,weather_code,wind_gusts_10m,pressure_msl&hourly=precipitation_probability,visibility,uv_index&forecast_days=2')); print(json.dumps(d['current']))"
   ```

   Change `models=` to `ncep_gfs_global` and `ncep_hrrr_conus` (or whichever `SHORT_RANGE_MODEL` you run) and try again. A field that's null for every model needs its stored variable added to `OM_VARIABLES` in .env. The stored names are in Open-Meteo's source (`Sources/App/Gfs/GfsDomainVariable.swift`, `Sources/App/Nbm/NbmVariable.swift`).
4. If the API itself won't start after an update, roll it back while you look into it: change `image: ghcr.io/open-meteo/open-meteo` to a dated tag from https://github.com/open-meteo/open-meteo/pkgs/container/open-meteo, `docker compose up -d`, and switch back once upstream is fixed.

### 5. HRRR and RRFS

NOAA is replacing HRRR with RRFS, and Open-Meteo has said its HRRR feed ends. Check `datasets.forecast.models` in the status: if the short-range model's `horizon_hours` is null or below 0 for more than a few hours, set `SHORT_RANGE_MODEL=ncep_rrfs_conus` in .env and `docker compose up -d`. Then remove the old model's files to get the disk back: `docker compose run --rm --entrypoint sh om-sync-hrrr -c "rm -rf /app/data/ncep_hrrr_conus"`.

### 6. Disk

```
docker system df -v | grep -E "VOLUME|isobar-weather"
df -h
```

Log the om-data volume's size weekly for the first month, then every check-up. Past 80% of the disk, either move om-data to a bigger SSD (Server/README.md, "Disk, models and tinkering") or drop variables you don't need from OM_VARIABLES.

### 7. Radar

1. `curl -s -A "Isobar/check" -H "Authorization: Bearer <key>" https://weather.example.net/v1/radar/frames` should list up to 12 frames, the newest no more than 4 minutes old.
2. `docker compose logs --since 1h mrms-radar | tail` shows "frame ... ready in Ns". If N is near or over 110 seconds, set `RADAR_ZOOMS=3-7`.
3. If frames stop and the log says it can't list the bucket, check NOAA's layout hasn't moved: `aws s3 ls --no-sign-request s3://noaa-mrms-pds/CONUS/ReflectivityAtLowestAltitude_00.50/` (or open https://noaa-mrms-pds.s3.amazonaws.com/?list-type=2&prefix=CONUS/&delimiter=/ in a browser). The path is `PRODUCT` near the top of `Server/radar/mrms.sh`.
4. The colors come from IEM's N0Q table (`Server/radar/n0q-colors.txt`, the same table as `IEM_N0Q` in the app's `ramps.js`). If IEM ever changes its reflectivity colors, regenerate both together, or the app can't repaint the fallback radar with the user's palette.

### 8. Place search

`docker compose logs --since 168h geocoding | tail` should show it serving. The image builds the geocoder from source, since Open-Meteo only publishes an x86 package, so a new geocoder release comes with the next image build; on its first start it downloads the matching database and checks it before swapping it in, so expect a few minutes of place search failing over to the app's direct geocoder after an update.

### 9. NWS sources the relay reads

| What | Where | Relay file |
| --- | --- | --- |
| Active alerts | api.weather.gov/alerts/active?status=actual | alerts.py |
| Backup alerts | mesonet.agron.iastate.edu/data/gis/shape/4326/us/current_ww.zip | alerts.py |
| Zone shapes | weather.gov/gis/Counties, PublicZones, FireZones, MarineZones | zones.py |
| Point lookups, stations, observations | api.weather.gov/points, /stations | obs.py |
| Backup observations | mesonet.agron.iastate.edu/api/1/currents.json | obs.py |
| NWWS-OI | nwws-oi.weather.gov, XMPP port 5222 | nwws.py |

The zone shapefile pages list the file in effect and sometimes next season's file with its future date; zones.py picks the newest file whose date has arrived. If `zones_loaded` stays false, open those pages and check the file names still look like `c_16ap26.zip`, `z_18mr25.zip`, `fz18mr25.zip`, `mz18mr25.zip`.

### 10. Keys

Read through `keys.txt`. Remove anyone who no longer uses the server. Grep the log for "wrong key" and "wrong-key limit reached": a burst from one address is someone guessing; steady wrong keys from a friend's usual address means their app has an old key.

## When something breaks between check-ups

1. `docker compose ps` for anything restarting or unhealthy.
2. `docker compose logs --since 2h <service>` for the one that is.
3. The relay keeps serving the last good copy of everything while an upstream is down, marked stale, and the apps move to their next server or go direct. Nothing needs restarting for that to work.
4. If a fix needs a change to the relay itself, see "Making a release" in the main README; the server images build with their own workflow from the same tag.

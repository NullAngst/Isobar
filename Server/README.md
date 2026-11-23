# Isobar Server

Isobar Server keeps weather data flowing to the Isobar apps you choose to share it with, from a box you run, so a provider outage shows up as slightly old data instead of "Can't connect".

It's optional. Isobar works without one, talking to the weather services directly. A server is for when those services go down, or when you'd rather your friends' apps lean on a box you run.

## What it does

A server is one Docker Compose stack:

- **Forecasts** (current conditions, hourly and 10-day) from its own copy of [Open-Meteo](https://open-meteo.com/), fed by NOAA's HRRR, National Blend of Models and GFS. The server never calls the public Open-Meteo API.
- **Place search** from its own copy of Open-Meteo's geocoder.
- **Air quality** from CAMS, synced into the same Open-Meteo.
- **Watches, warnings and advisories** for the whole country, polled from the NWS once a minute and kept in memory, so a thousand users cost the NWS one request a minute. When the NWS feed goes down, the last copy keeps serving, then IEM's warning list takes over.
- **Station observations** from the NWS, with IEM as the backup.
- **Fallback radar** built from NOAA MRMS, used only when IEM's radar fails.
- **NWWS-OI**, optional: warnings pushed over the NWS's own wire within seconds of issue, once the NWS gives you credentials.

What stays direct from the app, server or no server: primary radar from IEM, SPC outlooks, mesoscale discussions, storm reports, hurricane cones, satellite and base maps.

Access is a key per person. No key, no data. Anything that isn't an Isobar app with a working key gets no answer at all, as if the server were down.

## Prerequisites

- A Linux box with Docker and Docker Compose. Portainer is optional.
- A 64-bit CPU, x86 or ARM. Every image in the stack is built for both, and Docker pulls the right one. `uname -m` prints `x86_64` or `aarch64`; anything else (32-bit ARM, for one) won't run it.
- On x86, a CPU with AVX2, since Open-Meteo's x86 image is compiled for Skylake-era chips. Most x86 CPUs from 2013 on have it; `grep -c avx2 /proc/cpuinfo` prints a number above 0 if yours does. ARM has no such requirement.
- 16 GB of RAM to be comfortable (8 GB is the floor).
- An SSD with 50 GB free to start and room to grow to 150 GB. Put it on its own SSD if you can, not your file server's data disks.
- A domain or subdomain you control, like `weather.example.net`.
- An HTTPS reverse proxy: NPMPlus, Caddy, a Cloudflare Tunnel, or whichever you already run.
- An email address for the User-Agent the NWS requires.

The images are big. The relay carries IEM's pyIEM library for reading NWS products, and the geocoder downloads a place database of a couple of gigabytes on its first start.

## Setting it up

1. Make a folder for it and copy `compose.yml`, `.env.example` and `keys.txt.example` from this `Server/` folder into it. I use `/opt/isobar`, so in my case that's:

   ```
   sudo mkdir -p /opt/isobar && sudo chown "$USER" /opt/isobar
   cd /opt/isobar
   curl -fLO https://raw.githubusercontent.com/NullAngst/Isobar/main/Server/compose.yml
   curl -fL -o .env https://raw.githubusercontent.com/NullAngst/Isobar/main/Server/.env.example
   curl -fL -o keys.txt https://raw.githubusercontent.com/NullAngst/Isobar/main/Server/keys.txt.example
   ```

   Downloading them through the browser works just as well; rename `.env.example` to `.env` and `keys.txt.example` to `keys.txt`.

2. Make one key per person with `openssl rand -hex 32` (or whichever random generator you prefer), and put each in `keys.txt` under a `#name` line:

   ```
   #friend1
   3f9c0e5a...the rest of the 64 characters

   #friend2
   a71b44d0...
   ```

   The name only shows up in the server's log. The placeholder keys in the example are too short to work, so a file left as it is won't start a server.

3. Lock the key file down: `chmod 600 keys.txt`

4. Fill in `.env`: `CONTACT_EMAIL` and `SERVER_LABEL`. Everything else has a working default, and every setting is explained in the file.

5. Start the stack: `docker compose up -d`

   Or in Portainer: Stacks, Add stack, paste `compose.yml` into the web editor, add `CONTACT_EMAIL` and `SERVER_LABEL` under Environment variables, and add `KEYS_PATH` set to the absolute path of your key file, like `/opt/isobar/keys.txt`, since Portainer doesn't run from that folder. Deploy.

5.5. The first model downloads take a while, an hour or more depending on your connection. Until the first one finishes, `/v1/status` shows forecasts as warming up, and the app moves on to its next source for forecasts. Alerts and observations work within a minute.

6. Point your proxy at `RELAY_PORT` (8787 by default) on the box, with HTTPS on. In NPMPlus that's a new proxy host for `weather.example.net`, forwarding to the box's address and port 8787, with a certificate and Force SSL on. Or whichever proxy you use, the same way.

7. Check it from outside, from any other machine:

   ```
   curl -A "Isobar/check" https://weather.example.net/v1/ping
   ```

   That returns the server's name. Then the same call to `/v1/status` with your key:

   ```
   curl -A "Isobar/check" -H "Authorization: Bearer <key>" https://weather.example.net/v1/status
   ```

   That returns every dataset's age. Plain `curl https://weather.example.net/v1/ping`, without `-A`, should hang and get nothing back. That is correct: anything that isn't Isobar gets silence.

8. Give each friend the address and their own key. They add both in Isobar under Settings, Servers, and tap Test.

NEVER PUT THE RELAY ON THE INTERNET WITHOUT HTTPS. The key travels in a header, and plain HTTP hands it to anyone in between.

Also don't open `RELAY_PORT` to the internet on the box itself. Only your proxy should reach it, since the relay trusts the proxy's headers for whether a request came over HTTPS and from which address. If the proxy runs on another machine, firewall the port to that machine. I run UFW, so in my case that's `sudo ufw allow from 192.168.1.20 to any port 8787 proto tcp`, with the proxy's address in place of mine.

## Keys

Add, replace or remove a line in `keys.txt` and it takes effect within 10 seconds. No restart. Every other key keeps working.

- **Someone new:** add a `#name` line and a new key, and send them the address and key.
- **Someone lost their key or posted it publicly:** replace that one line with a new key. Only that person has to update their app.
- **Someone you're cutting off:** delete their two lines.

If an edit leaves the file unreadable, the relay keeps the last good list and says so in its log. If you remove every key, every request gets silence until you add one back. At startup, a missing file or one with no usable keys stops the relay with a message saying why.

The log names whose key each request used, never the key itself: `docker compose logs isobar-relay`.

## Updating

```
docker compose pull
docker compose up -d
```

In Portainer: open the stack and use "Pull and redeploy". Your data, keys and settings stay. Every image runs untagged and tracks the newest release; maintenance.md covers what to check every couple of months.

## Turning on NWWS-OI

NWWS-OI streams NWS products the moment they're issued, about 10 seconds behind, on a path separate from the NWS website and API. With it on, a new tornado warning shows up within seconds instead of within a minute, and keeps coming if api.weather.gov goes down.

1. Request an account from the NWS. The request page is https://www.weather.gov/nwws/nwws_oi_request; as of this writing it says to email NWWS.Issue@noaa.gov with your name, company and address. Approval can take 10 days or more, so do it on day one. Everything else works without it.
2. When the user ID and password arrive, put them in `.env` as `NWWS_USER` and `NWWS_PASS`.
3. `docker compose up -d`

The relay log says "NWWS-OI connected", and `/v1/status` lists `nwws` under capabilities. If the connection drops, the relay logs it and reconnects; the minute-by-minute NWS poll keeps running either way and corrects anything NWWS-OI got wrong.

## Turning radar off

The fallback radar builds a set of tiles every 2 minutes, and serves them when IEM's radar is down, which is exactly when everyone checks radar. A box with a weak CPU or weak upload can leave that to another server in your friends' lists.

1. Set `ENABLE_RADAR=false` in `.env`.
2. `docker compose up -d`

The radar container idles, and `/v1/status` stops listing radar, so the apps look elsewhere for it.

If radar stays on but the log says rendering takes longer than the 2 minute MRMS cycle, set `RADAR_ZOOMS=3-7` instead. Zoom 8 is the most detail MRMS has anyway (about 1 km a pixel).

## Dropping non-Isobar traffic at the proxy

The relay already gives anything that isn't Isobar no answer, holding the connection for 30 seconds first. Your proxy can drop it before it ever reaches the relay. This is optional.

In NPMPlus: open the proxy host, go to the Advanced tab, and paste this into Custom Nginx Configuration:

```
if ($http_user_agent !~ "^Isobar/") { return 444; }
```

Code 444 tells nginx to close the connection without sending anything. In plain nginx it goes in the `server` block for the relay. Caddy, Traefik or whichever proxy you use have their own ways to match a header and abort.

## What the silence does and doesn't do

- The Isobar User-Agent is public, since Isobar is open source. The silence hides the server from scanners and casual bots. It does not stop someone who's looking for it on purpose. The key is the lock.
- The connection itself still opens. A truly dead server never completes a connection; the relay only sees the User-Agent after TLS and the request headers arrive. The silence starts there.
- Behind Cloudflare, a silent origin shows the caller Cloudflare's own timeout page once Cloudflare gives up. That page says nothing about Isobar, and your address stays hidden.
- A request so malformed it isn't HTTP at all gets a plain 400 from the relay's web server before the relay sees it. Behind a proxy those never get through.
- The per-address wrong-key limit trusts the proxy's headers for the caller's address (Cloudflare's, then nginx's). That's one more reason the port should only be reachable by your proxy.

## Public or private

The software is the same either way. Post your address and a key somewhere public and you have a public server. Hand them to five friends and you have a private one. Anyone with a key can pass it on, so a key works like a house key: one key per person means a key that gets around costs you one line in `keys.txt` and costs that one person a new key.

## Settings

Every setting lives in `.env` (or the stack's environment variables in Portainer), with comments. The ones you're most likely to touch:

| Setting | Default | What it does |
| --- | --- | --- |
| `CONTACT_EMAIL` | none, required | Your address, sent in the User-Agent the NWS requires |
| `SERVER_LABEL` | Isobar Server | The name the app's Test button shows |
| `KEYS_PATH` | ./keys.txt | The key file on the host. An absolute path in Portainer. |
| `KEYS_FILE` | /config/keys.txt | Where the relay reads it inside the container |
| `RELAY_PORT` | 8787 | Host port your proxy forwards to |
| `REQUIRE_HTTPS` | true | Silence for anything that didn't arrive over HTTPS. false only for a LAN-only server. |
| `ENABLE_RADAR` | true | The MRMS fallback radar |
| `RADAR_ZOOMS` | 3-8 | Radar tile zoom levels |
| `NWWS_USER`, `NWWS_PASS` | empty | NWWS-OI credentials. Empty means off. |
| `RATE_LIMIT_PER_KEY` | 300 | Requests per minute per key, with bursts allowed |
| `AUTH_FAIL_LIMIT` | 20 | Wrong-key attempts per address in 10 minutes before an hour of silence |
| `SILENT_HOLD_SECONDS` | 30 | How long a silent connection is held before it closes |
| `SILENT_HOLD_MAX` | 200 | Most silent connections held at once; past it they close at once, still without a byte |
| `SHORT_RANGE_MODEL` | ncep_hrrr_conus | The model for current conditions and the next 48 hours |

One person running Isobar on a phone and a desktop with the same key stays well under the rate limit. Tune it from the log if you need to.

## Disk, models and tinkering

Open-Meteo keeps two days of past data and the full forecast for each model. Watch the volume for the first week before deciding how big a disk it needs: `docker system df -v` shows the `isobar-weather_om-data` volume's size.

To put the forecast data on its own SSD, replace the `om-data` volume with a folder on that disk: in `compose.yml`, change each `om-data:/app/data` line to `/mnt/ssd/openmeteo:/app/data` (or wherever yours is) and `docker compose up -d`.

**HRRR to RRFS.** NOAA is replacing HRRR with RRFS. If `/v1/status` shows the short-range model's `horizon_hours` stuck at or below 0 for hours, HRRR stopped updating. Set `SHORT_RANGE_MODEL=ncep_rrfs_conus` in `.env` and `docker compose up -d`. The sync worker and the relay both follow that one setting. Until then, NBM covers the gap on its own.

**Which models do what.** The relay asks the local Open-Meteo for each model by name and stitches the answers: HRRR first for current conditions and the next 48 hours, NBM for days 1 to 11, GFS for days 12 to 16 and anywhere outside the lower 48 (Alaska, Hawaii, Puerto Rico). A field one model leaves empty is filled from the next. Forecast numbers from a server can differ a little from Isobar's direct numbers, since the public Open-Meteo blends its own set of models. The app always says where each forecast came from.

**Variables.** `OM_VARIABLES` in `.env` lists what the sync workers download. The default covers every field Isobar asks for. If a field comes back empty, add the underlying variable Open-Meteo stores for it.

**Rough edges to know about.** The Open-Meteo image runs untagged, so it tracks the latest release. An upstream change can break sync or the storage format without warning. When that happens the relay answers 503 for forecasts, apps move to their next source, and maintenance.md says what to check.

## Licenses

- Open-Meteo is AGPL-3.0. Running it unmodified, as this stack does, is fine. If you ever patch it, the patched source has to be offered to the people using it. Its forecast data is CC BY 4.0.
- The geocoder's code is AGPL-3.0, and its API data is CC BY-NC 4.0, which forbids commercial use. That fits a server for friends. If you run one commercially, the place search part needs a license of its own.
- MRMS radar is NOAA's and free to use, but you may not imply NOAA endorses the product or present modified data as original NOAA data. The app labels the fallback layer "Derived from NOAA MRMS".
- pyIEM, which reads NWS products, is MIT-licensed, from the Iowa Environmental Mesonet.

## When something's wrong

- **The relay won't start.** `docker compose logs isobar-relay` says why. The usual two: `CONTACT_EMAIL` isn't set, or `keys.txt` is missing or holds no usable keys.
- **The log says keys.txt "is a directory".** Docker made a folder where the file should be, because `KEYS_PATH` pointed at a file that didn't exist yet. `docker compose down`, remove that folder, put the real `keys.txt` there, and start again.
- **Forecasts stay "warming up".** `docker compose logs om-sync-nbm` (and `om-sync-gfs`, `om-sync-hrrr`) shows the download. A sync worker that errors on every attempt usually means an Open-Meteo image change; see maintenance.md.
- **Place search fails.** The geocoder downloads its database on its first start; `docker compose logs geocoding` shows how far along it is.
- **A friend's app says "key not answering".** Their key isn't in `keys.txt`, or has a typo. Check the log for "wrong key", fix the line or send them the right key. Their app tries again as soon as they edit the key.

Now the server is running, syncing forecasts, polling alerts every minute, and every Isobar app with a key is pulling from it. From there, tinker with the limits and models as you see fit.

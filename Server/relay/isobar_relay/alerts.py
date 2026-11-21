"""The national alert snapshot, and alerts for a point or a map view from it.

One upstream call a minute covers every user: the relay polls the NWS active
alerts feed (conditionally, so an unchanged feed costs a 304), keeps the whole
country in memory, and answers each user's point by filtering it, the same
way the NWS point filter works:

1. Find the zones holding the point (counties, forecast, fire and marine zones).
2. Return every alert whose polygon holds the point, plus every alert with no
   polygon that names one of those zones.

When the NWS feed fails, the last good snapshot keeps serving, marked stale
after 5 minutes. Past that, the relay switches to IEM's current warnings file,
which carries warnings, watches and advisories (VTEC products) but not every
statement. With NWWS-OI credentials set, new warnings land in the snapshot
within seconds of issue, and the next NWS poll corrects whatever they got wrong.

Expired alerts drop even from a stale copy. A stale copy can still show a
warning that ended, but never one issued after the outage began, which is why
every stale answer says so.
"""

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone

from . import datasets
from .upstream import UpstreamError

log = logging.getLogger("isobar_relay.alerts")

NWS_ACTIVE = "https://api.weather.gov/alerts/active"
IEM_CURRENT = "https://mesonet.agron.iastate.edu/data/gis/shape/4326/us/current_ww.zip"
NWS_HEADERS = {"Accept": "application/geo+json"}
POLL_SECONDS = 60
NWWS_GRACE = 10 * 60          # an NWWS alert the NWS feed still lacks after this is dropped
VTEC_RE = re.compile(r"/[OTEX]\.([A-Z]{3})\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.")


def parse_time(value):
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def ends_at(props):
    """When an alert stops being active: its end time, or its expiry when it has none."""
    return parse_time(props.get("ends")) or parse_time(props.get("expires"))


def expired(props, now):
    end = ends_at(props)
    return end is not None and end <= now


def vtec_keys(props):
    """Event keys like KJAN.TO.W.0130 from an alert's VTEC strings."""
    out = []
    for line in ((props.get("parameters") or {}).get("VTEC") or []):
        match = VTEC_RE.search(str(line))
        if match:
            out.append(f"{match.group(2)}.{match.group(3)}.{match.group(4)}.{match.group(5)}")
    return out


def ugcs_of(props):
    return [str(code) for code in ((props.get("geocode") or {}).get("UGC") or []) if code]


class Snapshot:
    """Every active alert in the country, with indexes for point and map queries."""

    def __init__(self, features, fetched_at, source, version=0):
        from shapely import STRtree
        from shapely.geometry import shape

        self.features = features
        self.fetched_at = fetched_at
        self.source = source
        self.version = version
        self.polygons = []          # (feature index, shapely geometry)
        self.by_ugc = {}            # UGC -> [feature index] for alerts without a polygon
        for i, feat in enumerate(features):
            geometry = feat.get("geometry")
            if geometry:
                try:
                    geom = shape(geometry)
                except (ValueError, TypeError, AttributeError, KeyError):
                    geom = None
                if geom is not None and not geom.is_empty:
                    self.polygons.append((i, geom))
                    continue
            for code in ugcs_of(feat.get("properties") or {}):
                self.by_ugc.setdefault(code, []).append(i)
        self.tree = STRtree([g for _, g in self.polygons]) if self.polygons else None

    def at_point(self, lat, lon, zones, now=None):
        from shapely import Point

        now = time.time() if now is None else now
        hits = set()
        if self.tree is not None:
            point = Point(lon, lat)
            for k in self.tree.query(point, predicate="intersects"):
                hits.add(self.polygons[int(k)][0])
        for code in zones or ():
            hits.update(self.by_ugc.get(code, ()))
        return [self.features[i] for i in sorted(hits)
                if not expired(self.features[i].get("properties") or {}, now)]


def map_properties(props):
    return {
        "id": props.get("id"),
        "event": props.get("event"),
        "severity": props.get("severity"),
        "headline": props.get("headline"),
        "senderName": props.get("senderName"),
        "sent": props.get("sent"),
        "expires": props.get("expires"),
        "ends": props.get("ends"),
        "parameters": {"VTEC": (props.get("parameters") or {}).get("VTEC") or []},
    }


# ---------------------------------------------------------------- IEM fallback

def _iem_time(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{12}", text):
        return None
    return datetime.strptime(text, "%Y%m%d%H%M").replace(tzinfo=timezone.utc).isoformat()


def iem_features(rows, now=None):
    """NWS-shaped alerts from IEM's current_ww rows (dicts with the shapefile's
    fields plus a shapely "geometry"). Rows are grouped per event: the storm
    polygon (GTYPE P) becomes the geometry, county and zone rows (GTYPE C)
    become the UGC list."""
    from pyiem.nws.vtec import get_ps_string
    from shapely.geometry import mapping

    now = time.time() if now is None else now
    events = {}
    for row in rows:
        get = lambda key: row.get(key) if row.get(key) is not None else row.get(key.lower())  # noqa: E731
        wfo = str(get("WFO") or "").strip()
        phen = str(get("PHENOM") or "").strip()
        sig = str(get("SIG") or "").strip()
        etn = str(get("ETN") or "").strip()
        if not (wfo and phen and sig and etn):
            continue
        key = (wfo, phen, sig, etn.zfill(4))
        event = events.setdefault(key, {"ugcs": [], "polygon": None, "rows": []})
        event["rows"].append(row)
        gtype = str(get("GTYPE") or "").strip().upper()
        if gtype == "P" and row.get("geometry") is not None:
            event["polygon"] = row["geometry"]
        else:
            ugc = str(get("NWS_UGC") or "").strip()
            if ugc and ugc not in event["ugcs"]:
                event["ugcs"].append(ugc)
    out = []
    for (wfo, phen, sig, etn), event in events.items():
        first = event["rows"][0]
        get = lambda key, r=first: r.get(key) if r.get(key) is not None else r.get(key.lower())  # noqa: E731
        expires = max((_iem_time(r.get("EXPIRED") or r.get("expired")) or "" for r in event["rows"]), default="") or None
        if expires and parse_time(expires) <= now:
            continue
        issued = _iem_time(get("ISSUED")) or _iem_time(get("INIT_ISS"))
        name = get_ps_string(phen, sig) or f"{phen}.{sig}"
        office = wfo if len(wfo) == 4 else f"K{wfo}"
        ident = f"iem-{office}-{phen}.{sig}-{etn}"
        severity = {"W": "Severe", "A": "Moderate", "Y": "Minor"}.get(sig, "Unknown")
        if phen in ("TO", "EW") and sig == "W":
            severity = "Extreme"
        vtec = f"/O.CON.{office}.{phen}.{sig}.{etn}.000000T0000Z-000000T0000Z/"
        out.append({
            "id": ident,
            "type": "Feature",
            "geometry": mapping(event["polygon"]) if event["polygon"] is not None else None,
            "properties": {
                "id": ident,
                "event": name,
                "headline": f"{name} from NWS {wfo}",
                "severity": severity,
                "urgency": "Unknown",
                "certainty": "Unknown",
                "status": "Actual",
                "messageType": "Alert",
                "sent": issued,
                "effective": issued,
                "onset": issued,
                "expires": expires,
                "ends": expires,
                "senderName": f"NWS {wfo}",
                "areaDesc": ", ".join(event["ugcs"]),
                "description": ("The NWS alert feed is down, so this comes from the Iowa Environmental "
                                "Mesonet's list of active warnings, which carries the event and area but "
                                "not the full text."),
                "instruction": None,
                "geocode": {"UGC": event["ugcs"]},
                "parameters": {"VTEC": [vtec]},
                "references": [],
            },
        })
    return out


def read_iem_zip(body):
    """Rows from IEM's current_ww.zip: the shapefile's fields plus a shapely geometry."""
    import geopandas

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "current_ww.zip")
        with open(path, "wb") as fh:
            fh.write(body)
        frame = geopandas.read_file(path)
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(row._asdict())
    return rows


# ---------------------------------------------------------------- the snapshot keeper

class Alerts:
    def __init__(self, upstream, tracker, data_dir):
        self.upstream = upstream
        self.tracker = tracker
        self.path = os.path.join(data_dir, "alerts.json")
        self.snapshot = None
        # The last copy from a real feed: (features, fetched_at, source).
        self.base = None
        self.etag = None
        self.modified = None
        self.iem_etag = None
        self.iem_modified = None
        self.overlay = {}           # VTEC key -> (feature, added time), from NWWS-OI
        self.cancelled = {}         # VTEC key -> time NWWS-OI said it ended
        self.polls = 0
        self.unchanged = 0
        self.version = 0
        self._map_cache = {}
        self._shape_cache = {}
        self.zones = None           # set by the app

    # ------------------------------------------------------------ persistence

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                saved = json.load(fh)
            features = list(saved["features"])
            fetched = float(saved["fetched_at"])
            source = str(saved.get("source") or "nws")
        except (OSError, ValueError, KeyError, TypeError):
            return
        self.base = (features, fetched, source)
        self._rebuild(save=False)
        log.info("alerts: loaded the saved snapshot from %s (%s alerts)", datasets.iso(fetched), len(features))

    def _save(self):
        features, fetched, source = self.base
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"fetched_at": fetched, "source": source, "features": features}, fh)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("alerts: could not save the snapshot: %s", exc)

    def _rebuild(self, save=True):
        """The snapshot: the last feed copy, minus what NWWS-OI says ended, plus
        what NWWS-OI added that the feed doesn't have yet."""
        features, fetched, source = self.base
        present = set()
        kept = []
        for feat in features:
            keys = vtec_keys(feat.get("properties") or {})
            present.update(keys)
            if keys and all(k in self.cancelled for k in keys):
                continue
            kept.append(feat)
        extra = [feature for key, (feature, _) in self.overlay.items() if key not in present]
        self.version += 1
        self.snapshot = Snapshot(kept + extra, fetched, source, self.version)
        self._map_cache.clear()
        self.tracker.ok(datasets.ALERTS, source, fetched)
        if save:
            self._save()

    def _confirm(self, now):
        """The feed answered 304: the copy is still current as of now."""
        features, _, source = self.base
        self.base = (features, now, source)
        self.snapshot.fetched_at = now
        self.tracker.ok(datasets.ALERTS, source, now)

    def _prune_overlay(self, now, nws_checked):
        """Drop NWWS-OI additions the feed now carries, that expired, or that
        the NWS feed still lacks well after they arrived (the feed is the source
        of truth). Returns True if anything was dropped."""
        features = self.base[0] if self.base else []
        present = set()
        for feat in features:
            present.update(vtec_keys(feat.get("properties") or {}))
        dropped = False
        for key in list(self.overlay):
            feature, added = self.overlay[key]
            stale = nws_checked and now - added > NWWS_GRACE
            if key in present or expired(feature.get("properties") or {}, now) or stale:
                del self.overlay[key]
                dropped = True
        for key in list(self.cancelled):
            if (nws_checked and key not in present) or now - self.cancelled[key] > NWWS_GRACE:
                del self.cancelled[key]
                dropped = True
        return dropped

    # ------------------------------------------------------------ polling

    async def run(self):
        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001  (the loop must outlive any one bad answer)
                log.exception("alerts poll crashed")
            await asyncio.sleep(POLL_SECONDS)

    def stale(self, now=None):
        snap = self.snapshot
        return snap is None or datasets.is_stale(datasets.ALERTS, snap.fetched_at, now)

    async def poll_once(self, now=None):
        now = time.time() if now is None else now
        nws_copy = self.base is not None and self.base[2] == "nws"
        try:
            answer = await self.upstream.get(NWS_ACTIVE, params={"status": "actual"}, headers=NWS_HEADERS,
                                             etag=self.etag if nws_copy else None,
                                             modified=self.modified if nws_copy else None,
                                             timeout=45, name="NWS alerts")
        except UpstreamError as exc:
            self.tracker.fail(datasets.ALERTS, exc)
            if self.stale(now):
                await self._poll_iem(now)
            return
        self.polls += 1
        if answer.not_modified and nws_copy:
            self.unchanged += 1
            if self._prune_overlay(now, nws_checked=True):
                self.base = (self.base[0], now, "nws")
                self._rebuild(save=False)
            else:
                self._confirm(now)
        else:
            try:
                data = json.loads(answer.body)
                features = [f for f in data.get("features") or [] if isinstance(f, dict)]
            except (ValueError, AttributeError) as exc:
                self.upstream.health.fail("NWS alerts", f"bad JSON: {exc}")
                self.tracker.fail(datasets.ALERTS, exc)
                if self.stale(now):
                    await self._poll_iem(now)
                return
            if self.base is not None and self.base[2] != "nws":
                log.warning("alerts: the NWS feed is back; IEM fallback off")
            self.base = (features, now, "nws")
            self.etag, self.modified = answer.etag, answer.modified
            self._prune_overlay(now, nws_checked=True)
            self._rebuild(save=True)
        if self.polls >= 10:
            log.info("NWS alerts: %s of the last %s polls came back 304 Not Modified", self.unchanged, self.polls)
            self.polls = self.unchanged = 0

    async def _poll_iem(self, now):
        iem_copy = self.base is not None and self.base[2] == "iem"
        try:
            answer = await self.upstream.get(IEM_CURRENT, etag=self.iem_etag if iem_copy else None,
                                             modified=self.iem_modified if iem_copy else None,
                                             timeout=60, name="IEM warnings")
        except UpstreamError as exc:
            log.info("IEM warnings: %s", exc)
            return
        if answer.not_modified and iem_copy:
            self._confirm(now)
            return
        try:
            rows = await asyncio.to_thread(read_iem_zip, answer.body)
            features = iem_features(rows, now)
        except Exception as exc:  # noqa: BLE001
            self.upstream.health.fail("IEM warnings", f"could not read: {exc}")
            return
        self.iem_etag, self.iem_modified = answer.etag, answer.modified
        if not iem_copy:
            log.warning("alerts: serving IEM's current warnings while the NWS feed is down")
        self.base = (features, now, "iem")
        self._prune_overlay(now, nws_checked=False)
        self._rebuild(save=True)

    # ------------------------------------------------------------ NWWS-OI

    def nwws_update(self, add, remove):
        """Called by the NWWS-OI ingester with new or updated alert features
        (keyed by VTEC event) and the keys of events that ended. Keeps the
        copy's fetch time: NWWS-OI adds to the copy, it doesn't make it fresher."""
        now = time.time()
        for key in remove:
            self.overlay.pop(key, None)
            self.cancelled[key] = now
        for key, feature in add.items():
            self.cancelled.pop(key, None)
            self.overlay[key] = (feature, now)
        if self.base is not None:
            self._rebuild(save=False)

    # ------------------------------------------------------------ queries

    def for_point(self, lat, lon, zones, now=None):
        return self.snapshot.at_point(lat, lon, zones, now)

    def for_map(self, west, south, east, north, now=None):
        """GeoJSON bytes of every active alert drawn inside the box. Alerts
        without their own polygon are drawn as the union of their zones."""
        from shapely import box
        from shapely.geometry import mapping

        now = time.time() if now is None else now
        snap = self.snapshot
        key = (snap.version, round(west, 1), round(south, 1), round(east, 1), round(north, 1), int(now // 60))
        hit = self._map_cache.get(key)
        if hit is not None:
            return hit
        view = box(west, south, east, north)
        out = []
        drawn = set()
        if snap.tree is not None:
            for k in snap.tree.query(view, predicate="intersects"):
                i, _ = snap.polygons[int(k)]
                props = snap.features[i].get("properties") or {}
                if not expired(props, now):
                    drawn.add(i)
                    out.append({"type": "Feature", "geometry": snap.features[i]["geometry"],
                                "properties": map_properties(props)})
        zones = self.zones
        if zones is not None and zones.ready:
            for i, feat in enumerate(snap.features):
                if i in drawn or feat.get("geometry"):
                    continue
                props = feat.get("properties") or {}
                codes = tuple(sorted(ugcs_of(props)))
                if not codes or expired(props, now) or not zones.near(codes, west, south, east, north):
                    continue
                shape = self._shape_cache.get(codes)
                if shape is None:
                    shape = zones.shape_of(codes)
                    if shape is None:
                        continue
                    if len(self._shape_cache) > 4000:
                        self._shape_cache.clear()
                    self._shape_cache[codes] = shape
                if not shape.intersects(view):
                    continue
                item = {"type": "Feature", "geometry": mapping(shape), "properties": map_properties(props)}
                item["properties"]["zoneShape"] = True
                out.append(item)
        result = json.dumps({"type": "FeatureCollection", "features": out}, separators=(",", ":")).encode("utf-8")
        if len(self._map_cache) > 200:
            self._map_cache.clear()
        self._map_cache[key] = result
        return result

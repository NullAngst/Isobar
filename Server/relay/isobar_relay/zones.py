"""NWS zone shapes: which county, forecast, fire and marine zones hold a point.

The relay filters its national alert snapshot by point itself, instead of
asking the NWS once per user. Alerts without a polygon name the zones they
cover (UGC codes like GAC139 or GAZ054), so the relay needs to know which
zones contain each point. Those shapes come from the NWS GIS shapefiles,
checked monthly. The pages list the file in effect and sometimes the next one
with its future date, so the newest file whose date has arrived wins.
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import urljoin

log = logging.getLogger("isobar_relay.zones")

MONTHS = {"ja": 1, "fe": 2, "mr": 3, "ap": 4, "my": 5, "jn": 6, "jl": 7, "au": 8, "se": 9, "oc": 10, "no": 11, "de": 12}

# name: (page listing the files, file name prefix)
SOURCES = {
    "county": ("https://www.weather.gov/gis/Counties", "c"),
    "public": ("https://www.weather.gov/gis/PublicZones", "z"),
    "fire": ("https://www.weather.gov/gis/FireZones", "fz"),
    "marine": ("https://www.weather.gov/gis/MarineZones", "mz"),
    "offshore": ("https://www.weather.gov/gis/MarineZones", "oz"),
    "highseas": ("https://www.weather.gov/gis/MarineZones", "hz"),
}
REFRESH_EVERY = 30 * 86400
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+\.zip)["']""", re.IGNORECASE)


def file_date(name, prefix):
    """The valid date in a file name like c_16ap26.zip or mz18mr25.zip, or None."""
    match = re.fullmatch(rf"{prefix}_?(\d{{2}})([a-z]{{2}})(\d{{2}})\.zip", name.lower())
    if not match:
        return None
    month = MONTHS.get(match.group(2))
    if not month:
        return None
    try:
        return date(2000 + int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def pick_file(page_html, page_url, prefix, today=None):
    """The URL of the newest file in effect today, from a GIS page's links."""
    today = today or datetime.now(timezone.utc).date()
    found = []
    for href in HREF_RE.findall(page_html):
        url = urljoin(page_url, href)
        when = file_date(url.rsplit("/", 1)[-1], prefix)
        if when:
            found.append((when, url))
    if not found:
        return None
    current = [f for f in found if f[0] <= today]
    return max(current)[1] if current else min(found)[1]


def ugc_for(kind, props):
    """The UGC code a shapefile row stands for."""
    get = lambda key: str(props.get(key) or props.get(key.lower()) or props.get(key.title()) or "").strip()  # noqa: E731
    if kind == "county":
        state, fips = get("STATE"), get("FIPS")
        return f"{state}C{fips[-3:]}" if state and len(fips) >= 3 else None
    if kind in ("public", "fire"):
        state, zone = get("STATE"), get("ZONE")
        return f"{state}Z{zone.zfill(3)}" if state and zone else None
    code = get("ID")
    return code or None


class Zones:
    def __init__(self, data_dir, upstream):
        self.dir = os.path.join(data_dir, "zones")
        self.upstream = upstream
        self.tree = None
        self.codes = []
        self.geoms = []
        self.by_code = {}
        self.bounds = {}
        self.loaded_at = None
        self._point_cache = {}

    @property
    def ready(self):
        return self.tree is not None

    def _manifest_path(self):
        return os.path.join(self.dir, "manifest.json")

    def _manifest(self):
        try:
            with open(self._manifest_path(), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    async def run(self):
        """Load what's on disk, then keep the files current."""
        os.makedirs(self.dir, exist_ok=True)
        manifest = self._manifest()
        if manifest.get("files"):
            await self._load(manifest)
        while True:
            manifest = self._manifest()
            due = time.time() - manifest.get("checked", 0) >= REFRESH_EVERY
            if due or not self.ready:
                try:
                    changed = await self.refresh(manifest)
                    if changed or not self.ready:
                        await self._load(self._manifest())
                except Exception as exc:  # noqa: BLE001  (a failed refresh must never stop the relay)
                    self.upstream.health.fail("NWS zone shapes", exc)
            await asyncio.sleep(3600 if self.ready else 600)

    async def refresh(self, manifest):
        """Download any zone file that changed. Returns True if one did."""
        files = dict(manifest.get("files") or {})
        pages = {}
        changed = False
        for kind, (page, prefix) in SOURCES.items():
            if page not in pages:
                pages[page] = (await self.upstream.get(page, timeout=60, name="NWS zone shapes")).body.decode(
                    "utf-8", "replace")
            url = pick_file(pages[page], page, prefix)
            if not url:
                raise RuntimeError(f"no {prefix} zone file listed on {page}")
            target = os.path.join(self.dir, f"{kind}.zip")
            if files.get(kind) == url and os.path.exists(target):
                continue
            answer = await self.upstream.get(url, timeout=600, name="NWS zone shapes", limit=512 * 1024 * 1024)
            tmp = target + ".part"
            with open(tmp, "wb") as fh:
                fh.write(answer.body)
            os.replace(tmp, target)
            files[kind] = url
            changed = True
            log.info("zone shapes: downloaded %s", url)
        manifest = {"files": files, "checked": time.time()}
        tmp = self._manifest_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        os.replace(tmp, self._manifest_path())
        return changed

    async def _load(self, manifest):
        try:
            loaded = await asyncio.to_thread(self._read_files, manifest)
        except Exception as exc:  # noqa: BLE001
            log.error("zone shapes could not be read: %s", exc)
            return
        self.tree, self.codes, self.geoms, self.by_code, self.bounds = loaded
        self.loaded_at = time.time()
        self._point_cache.clear()
        log.info("zone shapes loaded: %s zones", len(self.codes))

    def _read_files(self, manifest):
        import geopandas  # heavy; only needed here
        from shapely import STRtree

        codes, geoms = [], []
        for kind in manifest.get("files") or {}:
            path = os.path.join(self.dir, f"{kind}.zip")
            if not os.path.exists(path):
                continue
            frame = geopandas.read_file(path)
            # The NWS files are plain lat/lon (NAD83, close enough to WGS84 here).
            if frame.crs is not None and not frame.crs.is_geographic:
                frame = frame.to_crs(4326)
            for row in frame.itertuples(index=False):
                props = row._asdict()
                geom = props.pop("geometry", None)
                code = ugc_for(kind, props)
                if code and geom is not None and not geom.is_empty:
                    codes.append(code)
                    geoms.append(geom)
        if not codes:
            raise RuntimeError("no zones in the downloaded files")
        by_code = {}
        bounds = {}
        for code, geom in zip(codes, geoms):
            by_code.setdefault(code, []).append(geom)
            x0, y0, x1, y1 = geom.bounds
            if code in bounds:
                a0, b0, a1, b1 = bounds[code]
                bounds[code] = (min(a0, x0), min(b0, y0), max(a1, x1), max(b1, y1))
            else:
                bounds[code] = (x0, y0, x1, y1)
        return STRtree(geoms), codes, geoms, by_code, bounds

    def zones_at(self, lat, lon):
        """UGC codes of every zone holding the point (cached per ~1 km)."""
        if not self.ready:
            return None
        key = (round(lat, 2), round(lon, 2))
        hit = self._point_cache.get(key)
        if hit is not None:
            return hit
        from shapely import Point

        point = Point(lon, lat)
        found = set()
        for i in self.tree.query(point, predicate="intersects"):
            found.add(self.codes[int(i)])
        if len(self._point_cache) > 50000:
            self._point_cache.clear()
        self._point_cache[key] = frozenset(found)
        return self._point_cache[key]

    def near(self, codes, west, south, east, north):
        """Whether any of these zones' bounding boxes touch the box. A cheap
        check before building a zone shape for the map."""
        for code in codes:
            b = self.bounds.get(code)
            if b and b[0] <= east and b[2] >= west and b[1] <= north and b[3] >= south:
                return True
        return False

    def shape_of(self, codes, tolerance=0.01):
        """One simplified shape covering these zones, for drawing on a map."""
        if not self.ready:
            return None
        from shapely import union_all

        parts = [g for code in codes for g in self.by_code.get(code, [])]
        if not parts:
            return None
        merged = union_all(parts)
        return merged.simplify(tolerance, preserve_topology=True)

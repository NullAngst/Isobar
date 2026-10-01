"""Local HTTP server: serves the web UI and a small JSON API.

Bound to 127.0.0.1 on a random port. API calls need the per-session token,
and the Host header must be the loopback address, which stops other sites
in a browser from poking at it through DNS rebinding.
"""

import json
import logging
import mimetypes
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, settings, sources
from .net import FetchError, get_bytes
from .paths import web_dir

log = logging.getLogger("isobar.server")

TILE_HOSTS = {
    "mesonet.agron.iastate.edu",
    "mesonet1.agron.iastate.edu",
    "mesonet2.agron.iastate.edu",
    "mesonet3.agron.iastate.edu",
}

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")


def _float(qs, key, lo=None, hi=None):
    try:
        value = float(qs[key][0])
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"missing or bad '{key}'") from exc
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError(f"'{key}' out of range")
    return value


def _str(qs, key, default=None):
    values = qs.get(key)
    return values[0] if values else default


class Handler(BaseHTTPRequestHandler):
    server_version = f"Isobar/{__version__}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ plumbing

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status=200):
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _error(self, status, message):
        self._json({"error": message}, status)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _token_ok(self, qs):
        token = self.headers.get("X-Isobar-Token") or _str(qs, "t")
        return token is not None and secrets.compare_digest(token, self.server.token)

    def _body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    # ------------------------------------------------------------ routing

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        if not self._host_ok():
            return self._error(403, "bad host")
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        path = url.path

        if not (path.startswith("/api/") or path.startswith("/proxy/")):
            if method != "GET":
                return self._error(405, "method not allowed")
            return self._static(path)

        if not self._token_ok(qs):
            return self._error(401, "bad token")

        route = self.ROUTES.get((method, path))
        if route is None:
            return self._error(404, "no such endpoint")
        try:
            route(self, qs)
        except ValueError as exc:
            self._error(400, str(exc))
        except FetchError as exc:
            log.info("upstream error: %s", exc)
            self._error(502, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.exception("handler crashed")
            self._error(500, f"{exc.__class__.__name__}: {exc}")

    def _static(self, path):
        root = web_dir().resolve()
        rel = path.lstrip("/") or "index.html"
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            return self._error(404, "not found")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return self._error(404, "not found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        cache = "no-cache" if target.suffix in (".html", ".js", ".css") else "max-age=86400"
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": cache})

    # ------------------------------------------------------------ endpoints

    def api_version(self, qs):
        self._json({"version": __version__})

    def api_settings_get(self, qs):
        self._json(settings.load())

    def api_settings_post(self, qs):
        patch = self._body_json()
        if not isinstance(patch, dict):
            raise ValueError("settings must be an object")
        self._json(settings.save(patch))

    def api_geocode(self, qs):
        self._json({"results": sources.geocode(_str(qs, "q", ""))})

    def api_reverse(self, qs):
        lat, lon = _float(qs, "lat", -90, 90), _float(qs, "lon", -180, 180)
        self._json({"name": sources.reverse(lat, lon)})

    def api_weather(self, qs):
        lat, lon = _float(qs, "lat", -90, 90), _float(qs, "lon", -180, 180)
        self._json(sources.weather_bundle(lat, lon))

    def api_alerts(self, qs):
        lat, lon = _float(qs, "lat", -90, 90), _float(qs, "lon", -180, 180)
        self._json({"alerts": sources.nws_alerts(lat, lon)})

    def api_discussion(self, qs):
        self._json({"discussion": sources.nws_discussion(_str(qs, "office", ""))})

    def api_spc(self, qs):
        day = int(_float(qs, "day", 1, 8))
        kind = _str(qs, "kind", "cat") if day <= 3 else "prob"
        self._json(sources.spc_outlook(day, kind))

    def api_spc_point(self, qs):
        lat, lon = _float(qs, "lat", -90, 90), _float(qs, "lon", -180, 180)
        self._json({"days": sources.spc_point(lat, lon)})

    def api_wwa(self, qs):
        bbox = [_float(qs, k) for k in ("s", "w", "n", "e")]
        zoom = _float(qs, "z", 0, 20)
        self._json(sources.wwa(_str(qs, "sig", "W"), bbox, zoom))

    def api_radar_sites(self, qs):
        self._json({"sites": sources.radar_sites()})

    def api_radar_frames(self, qs):
        frames = sources.radar_frames(
            _str(qs, "site", ""), _str(qs, "product", ""), _float(qs, "minutes", 10, 180)
        )
        self._json({"frames": frames})

    def api_grid(self, qs):
        bbox = [_float(qs, k) for k in ("s", "w", "n", "e")]
        nx, ny = _float(qs, "nx", 3, 20), _float(qs, "ny", 3, 20)
        self._json(sources.model_grid(bbox, nx, ny))

    def api_open(self, qs):
        url = str(self._body_json().get("url") or "")
        if not url.startswith(("https://", "http://")):
            raise ValueError("only http(s) links can be opened")
        threading.Thread(target=self.server.open_url, args=(url,), daemon=True).start()
        self._json({"ok": True})

    def proxy_tile(self, qs):
        target = _str(qs, "u", "")
        parsed = urlparse(target)
        if parsed.scheme != "https" or parsed.hostname not in TILE_HOSTS:
            raise ValueError("tile host not allowed")
        body, ctype = get_bytes(target, ttl=120 if "-0/" in target or "/cache/" in target else 3600)
        self._send(200, body, ctype, {"Cache-Control": "max-age=60", "Access-Control-Allow-Origin": "*"})

    ROUTES = {
        ("GET", "/api/version"): api_version,
        ("GET", "/api/settings"): api_settings_get,
        ("POST", "/api/settings"): api_settings_post,
        ("GET", "/api/geocode"): api_geocode,
        ("GET", "/api/reverse"): api_reverse,
        ("GET", "/api/weather"): api_weather,
        ("GET", "/api/alerts"): api_alerts,
        ("GET", "/api/discussion"): api_discussion,
        ("GET", "/api/spc"): api_spc,
        ("GET", "/api/spc/point"): api_spc_point,
        ("GET", "/api/wwa"): api_wwa,
        ("GET", "/api/radar/sites"): api_radar_sites,
        ("GET", "/api/radar/frames"): api_radar_frames,
        ("GET", "/api/grid"): api_grid,
        ("POST", "/api/open"): api_open,
        ("GET", "/proxy/tile"): proxy_tile,
    }


# Browsers key stored data by origin, and the port is part of the origin.
# Using the same port every launch keeps the map tile cache across restarts.
PREFERRED_PORT = 47130


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port, so it stays off there.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, port=0, open_url=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.token = secrets.token_urlsafe(24)
        self.open_url = open_url or webbrowser.open

    def handle_error(self, request, client_address):
        # The UI drops tile requests all the time while panning; that is not an error.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"

    @classmethod
    def create(cls, port=0, open_url=None):
        """Bind the given port, or the preferred one, or any free one if that's taken."""
        if port:
            return cls(port, open_url)
        try:
            return cls(PREFERRED_PORT, open_url)
        except OSError:
            log.info("port %s busy, using a random port; the map cache won't carry over", PREFERRED_PORT)
            return cls(0, open_url)

    def start(self):
        thread = threading.Thread(target=self.serve_forever, name="isobar-http", daemon=True)
        thread.start()
        return thread

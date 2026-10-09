"""Local HTTP server: serves the web UI and a small JSON API.

Bound to 127.0.0.1 only (port 47130 when free). Every API call needs the
per-session token in the X-Isobar-Token header; only the tile proxy takes it
as a query parameter, since an image tag can't send headers. The Host header
must be the loopback address, which defeats DNS rebinding, and requests that
carry an Origin header must come from this server's own origin. Every
response carries a Content-Security-Policy that limits the page to its own
scripts and the map and radar tile hosts.
"""

import json
import logging
import re
import mimetypes
import os
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

MAP_HOSTS = "https://*.agron.iastate.edu https://*.basemaps.cartocdn.com https://server.arcgisonline.com"
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    # Leaflet positions everything with inline style attributes.
    "style-src 'self' 'unsafe-inline'",
    f"img-src 'self' data: blob: {MAP_HOSTS}",
    f"connect-src 'self' {MAP_HOSTS}",
    "font-src 'self'",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
])
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    # Tile servers get no Referer at all, so neither the local address nor the token leaks.
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}
TOKEN_IN_URL = re.compile(r"([?&]t=)[^&\s]+")
MAX_BODY = 256_000

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
        log.debug("%s %s", self.address_string(), TOKEN_IN_URL.sub(r"\1***", fmt % args))

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
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

    def _token_ok(self, qs, allow_query=False):
        token = self.headers.get("X-Isobar-Token")
        if token is None and allow_query:
            token = _str(qs, "t")
        return token is not None and secrets.compare_digest(token, self.server.token)

    def _origin_ok(self):
        # Browsers send Origin on cross-site and on all POST requests. Anything
        # that isn't this server is turned away, token or not.
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        return origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _read_body(self):
        """Read the request body up front, before any check can turn the request
        away. On a kept-alive connection an unread body would otherwise be parsed
        as the start of the next request. Returns False if it can't be read."""
        self._body = b""
        if self.headers.get("Transfer-Encoding"):
            return False  # chunked bodies aren't supported; the connection closes
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return False
        if length < 0 or length > MAX_BODY:
            return False
        if length:
            self._body = self.rfile.read(length)
        return True

    def _body_json(self):
        try:
            return json.loads(self._body or b"{}")
        except ValueError as exc:
            raise ValueError("body is not JSON") from exc

    # ------------------------------------------------------------ routing

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        if method == "POST" and not self._read_body():
            self.close_connection = True
            return self._error(413, "body missing a length or too large")
        if not self._host_ok():
            return self._error(403, "bad host")
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        path = url.path

        if not (path.startswith("/api/") or path.startswith("/proxy/")):
            if method != "GET":
                return self._error(405, "method not allowed")
            return self._static(path)

        if not self._origin_ok():
            return self._error(403, "bad origin")
        if not self._token_ok(qs, allow_query=path.startswith("/proxy/")):
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
        except Exception:  # noqa: BLE001
            log.exception("handler crashed")
            self._error(500, "internal error, see the log")

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
        # The Android build sets ISOBAR_PLATFORM so the UI can hide desktop-only settings.
        self._json({"version": __version__, "platform": os.environ.get("ISOBAR_PLATFORM", "desktop")})

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
        self._json({"alerts": sources.nws_alerts(lat, lon), "mcds": sources.mcds_here(lat, lon)})

    def api_discussion(self, qs):
        product = _str(qs, "product", "AFD").upper()
        self._json({"discussion": sources.nws_discussion(_str(qs, "office", ""), product)})

    def api_mcd(self, qs):
        lat = _float(qs, "lat", -90, 90) if "lat" in qs else None
        lon = _float(qs, "lon", -180, 180) if "lon" in qs else None
        self._json(sources.spc_mcds(lat, lon))

    def api_reports(self, qs):
        hours = int(_float(qs, "hours", 1, 48))
        self._json(sources.storm_reports(hours))

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
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or len(url) > 2048 or any(c in url for c in "\r\n\t "):
            raise ValueError("only plain http(s) links can be opened")
        threading.Thread(target=self.server.open_url, args=(url,), daemon=True).start()
        self._json({"ok": True})

    def proxy_tile(self, qs):
        target = _str(qs, "u", "")
        parsed = urlparse(target)
        if (len(target) > 1024 or parsed.scheme != "https" or parsed.hostname not in TILE_HOSTS
                or parsed.port not in (None, 443) or parsed.username or parsed.password):
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
        ("GET", "/api/spc/mcd"): api_mcd,
        ("GET", "/api/reports"): api_reports,
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

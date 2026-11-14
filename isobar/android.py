"""Android entry point.

The Android app (android/ in the repo) embeds Python with Chaquopy, calls
start() once, and points a WebView at the URL it returns. Everything else is
the same server and web UI the desktop app uses.

What's left out on Android: the Qt window and the tray, which live in app.py
and are never imported here. Alert notifications come from check_alerts(),
which the app's background job (AlertWorker.kt) calls every 15 minutes, using
the same rules as the desktop tray (notify.py).
"""

import json
import logging
import os
import threading

_server = None
_lock = threading.Lock()
log = logging.getLogger("isobar.android")


def _open_url(url):
    """Open a link in the phone's browser (or whatever app handles it)."""
    # These imports only exist inside Chaquopy.
    from java import jclass

    Python = jclass("com.chaquo.python.Python")
    Intent = jclass("android.content.Intent")
    Uri = jclass("android.net.Uri")
    context = Python.getPlatform().getApplication()
    intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    try:
        context.startActivity(intent)
    except Exception as exc:  # no app can open it
        log.warning("could not open %s: %s", url, exc)


def _setup(files_dir, cache_dir):
    # paths.py follows the XDG variables on anything that isn't Windows or
    # macOS, so pointing them at the app's private storage is enough.
    os.environ["XDG_CONFIG_HOME"] = os.path.join(files_dir, "config")
    os.environ["XDG_CACHE_HOME"] = cache_dir
    os.environ["ISOBAR_PLATFORM"] = "android"
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")


_watcher = None
# Separate from _lock, so a slow alert check never holds up the UI starting.
_check_lock = threading.Lock()


def check_alerts(files_dir, cache_dir):
    """Run one alert check and return the notices as a JSON list of
    {"title", "body", "tier", "channel"}. Called from the background job, which may run
    in a process where the UI never started."""
    global _watcher
    _setup(files_dir, cache_dir)
    from . import settings
    from .notify import AlertWatcher, collect
    from .paths import cache_dir as cache_path

    with _check_lock:
        if _watcher is None:
            _watcher = AlertWatcher(path=str(cache_path() / "notify-seen.json"))
        prefs = settings.load()
        try:
            notices = collect(_watcher, prefs)
        except Exception as exc:  # noqa: BLE001
            log.warning("alert check failed: %s", exc)
            notices = []
    loud = prefs.get("critical_sound", True)
    return json.dumps([
        {"title": n["title"], "body": n["body"], "tier": n["tier"], "channel": channel_for(n["tier"], loud)}
        for n in notices
    ])


def channel_for(tier, loud=True):
    """Android notification channel for a tier: the loud critical one (if the
    user wants it), the warnings one, or the quieter one for everything else."""
    if tier == "critical":
        return "critical" if loud else "alerts"
    return "alerts" if tier == "high" else "updates"


def start(files_dir, cache_dir):
    """Start the local server if it isn't running yet, and return its URL.

    Safe to call again (the activity gets recreated on theme changes and
    after Android reclaims it); the running server is reused.
    """
    global _server
    with _lock:
        if _server is not None:
            return _server.url

        _setup(files_dir, cache_dir)

        from .net import prune_disk
        from .server import Server

        _server = Server.create(open_url=_open_url)
        _server.start()
        threading.Thread(target=prune_disk, name="isobar-prune", daemon=True).start()
        log.info("serving %s", _server.url)
        return _server.url

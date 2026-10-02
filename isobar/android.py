"""Android entry point.

The Android app (android/ in the repo) embeds Python with Chaquopy, calls
start() once, and points a WebView at the URL it returns. Everything else is
the same server and web UI the desktop app uses.

What's left out on Android: the Qt window, the tray, and desktop alert
notifications. Those live in app.py, which is never imported here.
"""

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


def start(files_dir, cache_dir):
    """Start the local server if it isn't running yet, and return its URL.

    Safe to call again (the activity gets recreated on theme changes and
    after Android reclaims it); the running server is reused.
    """
    global _server
    with _lock:
        if _server is not None:
            return _server.url

        # paths.py follows the XDG variables on anything that isn't Windows or
        # macOS, so pointing them at the app's private storage is enough.
        os.environ["XDG_CONFIG_HOME"] = os.path.join(files_dir, "config")
        os.environ["XDG_CACHE_HOME"] = cache_dir
        os.environ["ISOBAR_PLATFORM"] = "android"
        logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

        from .net import prune_disk
        from .server import Server

        _server = Server.create(open_url=_open_url)
        _server.start()
        threading.Thread(target=prune_disk, name="isobar-prune", daemon=True).start()
        log.info("serving %s", _server.url)
        return _server.url

"""Desktop shell.

Starts the local server, then shows the UI in a Qt WebEngine window.
With --browser, or when Qt WebEngine is missing, the UI opens in the
default browser instead.
"""

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser

from . import APP_NAME, __version__, settings, sources
from .paths import cache_dir, icon_path
from .server import Server

log = logging.getLogger("isobar")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="isobar", description=f"{APP_NAME} weather and radar")
    p.add_argument("--browser", action="store_true", help="open the UI in your default browser instead of a window")
    p.add_argument("--port", type=int, default=0, help="local port for the UI server (default: 47130, or random if busy)")
    p.add_argument("--no-sandbox", action="store_true", help="disable the Chromium sandbox (needed on some Linux setups)")
    p.add_argument("--software-gl", action="store_true", help="render without the GPU (VMs, broken drivers)")
    p.add_argument("--debug", action="store_true", help="verbose logging and web inspector port 9222")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p.parse_args(argv)


def run_browser(server):
    print(f"{APP_NAME} is running at {server.url}")
    print("Press Ctrl+C to stop.")
    webbrowser.open(server.url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def _is_warning_worthy(alert, mode):
    if mode == "all":
        return True
    event = (alert.get("event") or "").lower()
    return event.endswith("warning") or alert.get("severity") in ("Extreme",)


def run_qt(server, args):
    if args.no_sandbox:
        os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if args.software_gl:
        flags += " --disable-gpu --disable-gpu-compositing"
    if args.debug:
        os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags.strip()

    from PySide6.QtCore import QObject, QSettings, Qt, QTimer, QUrl, Signal
    from PySide6.QtGui import QAction, QDesktopServices, QIcon
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon

    if args.software_gl:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    icon = QIcon(str(icon_path()))
    app.setWindowIcon(icon)

    local_hosts = ("127.0.0.1", "localhost")

    class ExternalPage(QWebEnginePage):
        """Catches target=_blank windows and hands the link to the OS."""

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            QDesktopServices.openUrl(url)
            self.deleteLater()
            return False

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if not is_main_frame or url.host() in local_hosts or url.scheme() in ("data", "blob", "about"):
                return True
            QDesktopServices.openUrl(url)
            return False

        def createWindow(self, window_type):
            return ExternalPage(self.profile(), self)

        def javaScriptConsoleMessage(self, level, message, line, source):
            log.debug("js: %s (%s:%s)", message, source, line)

    profile_dir = cache_dir() / "webengine"
    profile = QWebEngineProfile(APP_NAME, app)
    profile.setPersistentStoragePath(str(profile_dir / "storage"))
    profile.setCachePath(str(profile_dir / "http"))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    profile.setHttpCacheMaximumSize(256 * 1024 * 1024)

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(APP_NAME)
            self.view = QWebEngineView(self)
            self.page = Page(profile, self.view)
            self.view.setPage(self.page)
            self.setCentralWidget(self.view)
            self.qsettings = QSettings(APP_NAME, APP_NAME)
            geometry = self.qsettings.value("geometry")
            if geometry is not None:
                self.restoreGeometry(geometry)
            else:
                self.resize(1320, 860)
            self.view.load(QUrl(server.url))
            self.quitting = False

        def closeEvent(self, event):
            self.qsettings.setValue("geometry", self.saveGeometry())
            if not self.quitting and tray is not None and settings.load().get("tray_on_close"):
                self.hide()
                event.ignore()
                return
            event.accept()
            app.quit()

        def go(self, view):
            self.showNormal()
            self.raise_()
            self.activateWindow()
            self.page.runJavaScript(f"window.isobar && window.isobar.go('{view}')")

    window = Window()

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(icon, app)
        tray.setToolTip(APP_NAME)
        menu = QMenu()
        show_action = QAction("Show Isobar", menu)
        show_action.triggered.connect(lambda: window.go("now"))
        alerts_action = QAction("Alerts", menu)
        alerts_action.triggered.connect(lambda: window.go("alerts"))
        quit_action = QAction("Quit", menu)

        def really_quit():
            window.quitting = True
            window.close()
            app.quit()

        quit_action.triggered.connect(really_quit)
        menu.addAction(show_action)
        menu.addAction(alerts_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: window.go("now") if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        tray.messageClicked.connect(lambda: window.go("alerts"))
        tray.show()

    class Notifier(QObject):
        found = Signal(str, str)

    notifier = Notifier()
    seen = set()
    busy = threading.Lock()

    def show_alert(title, body):
        if tray is not None:
            tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Warning, 20000)
        else:
            QApplication.alert(window, 0)

    notifier.found.connect(show_alert)

    def poll_alerts():
        if not busy.acquire(blocking=False):
            return
        try:
            prefs = settings.load()
            loc = prefs.get("location")
            mode = prefs.get("notify", "warnings")
            if mode == "off" or not loc:
                return
            for alert in sources.nws_alerts(loc["lat"], loc["lon"]):
                if alert["id"] in seen:
                    continue
                seen.add(alert["id"])
                if _is_warning_worthy(alert, mode):
                    notifier.found.emit(alert["event"] or "Weather alert", alert.get("headline") or loc.get("name", ""))
        except Exception as exc:  # noqa: BLE001
            log.info("alert poll failed: %s", exc)
        finally:
            busy.release()

    timer = QTimer(app)
    timer.timeout.connect(lambda: threading.Thread(target=poll_alerts, daemon=True).start())
    timer.start(120_000)
    QTimer.singleShot(8000, lambda: threading.Thread(target=poll_alerts, daemon=True).start())

    window.show()
    return app.exec()


def main(argv=None):
    # Windowed builds have no console; give print() somewhere to go.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    server = Server.create(port=args.port)
    server.start()

    if args.browser:
        run_browser(server)
        return

    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except ImportError as exc:
        print(f"Qt WebEngine is not available ({exc}). Opening in your browser instead.")
        run_browser(server)
        return

    sys.exit(run_qt(server, args))

"""Desktop shell.

Starts the local server, then shows the UI in a Qt WebEngine window.
With --browser, or when Qt WebEngine is missing, the UI opens in the
default browser instead.

Only one copy runs per user. Starting Isobar again raises the running window
(or does nothing, with --hidden), so there's never a second set of
notifications or a second tile cache.
"""

import argparse
import getpass
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

from . import APP_NAME, __version__, settings
from .net import prune_disk
from .notify import AlertWatcher, collect
from .paths import cache_dir, icon_path
from .server import Server

log = logging.getLogger("isobar")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="isobar", description=f"{APP_NAME} weather and radar")
    p.add_argument("--browser", action="store_true", help="open the UI in your default browser instead of a window")
    p.add_argument("--hidden", action="store_true", help="start in the tray without showing the window (used at login)")
    p.add_argument("--port", type=int, default=0, help="local port for the UI server (default: 47130, or random if busy)")
    p.add_argument("--no-sandbox", action="store_true", help="disable the Chromium sandbox (needed on some Linux setups)")
    p.add_argument("--software-gl", action="store_true", help="render without the GPU (VMs, broken drivers)")
    p.add_argument("--debug", action="store_true", help="verbose logging and web inspector port 9222")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p.parse_args(argv)


def start_server(port):
    server = Server.create(port=port)
    server.start()
    threading.Thread(target=prune_disk, name="isobar-prune", daemon=True).start()
    return server


def run_browser(server):
    print(f"{APP_NAME} is running at {server.url}")
    print("Press Ctrl+C to stop.")
    webbrowser.open(server.url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------- notifications outside Qt

def _instance_name():
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001  (no user name on some systems)
        user = str(os.getuid()) if hasattr(os, "getuid") else "user"
    return f"io.github.nullangst.isobar-{user}"


def play_alarm():
    """A short alert sound through whatever the system already has."""
    if sys.platform.startswith("win"):
        try:
            import winsound  # noqa: PLC0415

            winsound.MessageBeep(winsound.MB_ICONHAND)
            return True
        except Exception:  # noqa: BLE001
            return False
    if sys.platform == "darwin":
        if shutil.which("afplay"):
            subprocess.Popen(["afplay", "/System/Library/Sounds/Sosumi.aiff"])  # noqa: S603,S607
            return True
        return False
    tool = shutil.which("canberra-gtk-play")
    if tool:
        subprocess.Popen([tool, "-i", "dialog-warning", "-d", "Isobar"])  # noqa: S603
        return True
    tool = shutil.which("paplay") or shutil.which("pw-play")
    sound = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
    if tool and os.path.isfile(sound):
        subprocess.Popen([tool, sound])  # noqa: S603
        return True
    return False


def notify_send(title, body, critical):
    """Desktop notification through libnotify's notify-send, when it's there.
    Critical ones stay on screen until dismissed."""
    tool = shutil.which("notify-send")
    if not tool:
        return False
    cmd = [tool, "--app-name", APP_NAME, "--icon", str(icon_path())]
    cmd += ["--urgency", "critical"] if critical else ["--urgency", "normal"]
    cmd += ["--", title, body or ""]
    try:
        subprocess.Popen(cmd)  # noqa: S603
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- Qt

def run_qt(args):
    if args.no_sandbox:
        os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if args.software_gl:
        flags += " --disable-gpu --disable-gpu-compositing"
    if args.debug:
        os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags.strip()

    from PySide6.QtCore import QObject, QSettings, Qt, QTimer, QUrl, Signal
    from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon

    if args.software_gl:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    # Matches the .desktop file the AppImage ships, so Wayland docks and task
    # bars can pair the window with its icon.
    app.setDesktopFileName("io.github.nullangst.isobar")

    # One copy per user. A second launch asks the first to show itself.
    name = _instance_name()
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(700):
        probe.write(b"hidden\n" if args.hidden else b"show\n")
        probe.waitForBytesWritten(700)
        probe.disconnectFromServer()
        print(f"{APP_NAME} is already running.")
        return 0
    QLocalServer.removeServer(name)  # a socket file left by a crash
    instance = QLocalServer(app)
    if not instance.listen(name):
        log.info("single-instance socket unavailable: %s", instance.errorString())

    server = start_server(args.port)

    base_icon = QIcon(str(icon_path()))
    app.setWindowIcon(base_icon)

    def badge_icon(color):
        pixmap = base_icon.pixmap(64, 64)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#ffffff"))
        painter.setBrush(QColor(color))
        painter.drawEllipse(38, 38, 24, 24)
        painter.end()
        return QIcon(pixmap)

    icons = {
        "none": base_icon,
        "watch": badge_icon("#e8d23a"),
        "warning": badge_icon("#ef4b4b"),
        "critical": badge_icon("#ff1f1f"),
    }

    local_hosts = ("127.0.0.1", "localhost")
    local_port = server.server_address[1]

    def open_outside(url):
        # Only web links leave the app. file:, smb:, custom protocol handlers and
        # the like are dropped, so a bad link can't launch something on the system.
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        else:
            log.info("refused to open %s link", url.scheme())

    def is_ours(url):
        return url.scheme() == "http" and url.host() in local_hosts and url.port() == local_port

    class ExternalPage(QWebEnginePage):
        """Catches target=_blank windows and hands the link to the OS."""

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            open_outside(url)
            self.deleteLater()
            return False

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if not is_main_frame or is_ours(url) or url.scheme() in ("data", "blob", "about"):
                return True
            open_outside(url)
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
            if view in ("now", "forecast", "radar", "outlooks", "alerts", "discussion"):
                self.page.runJavaScript(f"window.isobar && window.isobar.go('{view}')")

    window = Window()

    def on_instance():
        sock = instance.nextPendingConnection()
        if sock is None:
            return

        def read():
            message = bytes(sock.readAll()).decode("ascii", "replace").strip()
            if message != "hidden":
                window.go("now")
            sock.disconnectFromServer()

        sock.readyRead.connect(read)

    instance.newConnection.connect(on_instance)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(base_icon, app)
        tray.setToolTip(APP_NAME)
        menu = QMenu()
        show_action = QAction("Show Isobar", menu)
        show_action.triggered.connect(lambda: window.go("now"))
        alerts_action = QAction("Alerts", menu)
        alerts_action.triggered.connect(lambda: window.go("alerts"))
        radar_action = QAction("Radar", menu)
        radar_action.triggered.connect(lambda: window.go("radar"))
        quit_action = QAction("Quit", menu)

        def really_quit():
            window.quitting = True
            window.close()
            app.quit()

        quit_action.triggered.connect(really_quit)
        menu.addAction(show_action)
        menu.addAction(alerts_action)
        menu.addAction(radar_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: window.go("now") if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        tray.messageClicked.connect(lambda: window.go("alerts"))
        tray.show()
    server.has_tray = tray is not None

    class Bridge(QObject):
        notice = Signal(str, str, str)
        status = Signal(str, str, str)

    bridge = Bridge()

    def show_notice(title, body, tier):
        critical = tier == "critical"
        if critical and settings.load().get("critical_sound", True):
            if not play_alarm():
                QApplication.beep()
        # Critical alerts go through notify-send where it exists, since only
        # that can ask for a notification that stays until dismissed.
        if critical and sys.platform.startswith("linux") and notify_send(title, body, True):
            return
        if tray is not None:
            icon = QSystemTrayIcon.MessageIcon.Critical if critical else QSystemTrayIcon.MessageIcon.Warning
            tray.showMessage(title, body, icon, 0 if critical else 20000)
        elif not notify_send(title, body, critical):
            # No tray and no notify-send (GNOME without the AppIndicator
            # extension, say): flash the task bar entry at least.
            QApplication.alert(window, 0)

    def apply_status(title, tooltip, level):
        window.setWindowTitle(title or APP_NAME)
        if tray is not None:
            tray.setToolTip(tooltip or APP_NAME)
            tray.setIcon(icons.get(level, base_icon))

    bridge.notice.connect(show_notice)
    bridge.status.connect(apply_status)
    server.on_status = lambda s: bridge.status.emit(s["title"], s["tooltip"], s["level"])

    watcher = AlertWatcher(path=str(cache_dir() / "notify-seen.json"))
    busy = threading.Lock()

    def poll_alerts():
        if not busy.acquire(blocking=False):
            return
        try:
            for notice in collect(watcher, settings.load()):
                bridge.notice.emit(notice["title"], notice["body"], notice["tier"])
        except Exception as exc:  # noqa: BLE001
            log.info("alert poll failed: %s", exc)
        finally:
            busy.release()

    timer = QTimer(app)
    timer.timeout.connect(lambda: threading.Thread(target=poll_alerts, daemon=True).start())
    timer.start(120_000)
    QTimer.singleShot(8000, lambda: threading.Thread(target=poll_alerts, daemon=True).start())

    if args.hidden and tray is not None:
        log.info("started hidden in the tray")
    else:
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

    if args.browser:
        run_browser(start_server(args.port))
        return

    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except ImportError as exc:
        print(f"Qt WebEngine is not available ({exc}). Opening in your browser instead.")
        run_browser(start_server(args.port))
        return

    sys.exit(run_qt(args))

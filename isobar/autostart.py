"""Start Isobar at login, hidden in the tray, so alert notifications keep coming.

Each system has its own standard place for this, and Isobar uses that rather
than running anything of its own:

- Linux (and other freedesktop systems): a .desktop file in ~/.config/autostart
- Windows: a value under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
- macOS: a LaunchAgent plist in ~/Library/LaunchAgents

Inside an AppImage the command points at the AppImage file itself ($APPIMAGE),
not at the temporary folder it was mounted on.
"""

import os
import plistlib
import sys
from pathlib import Path

APP_ID = "io.github.nullangst.isobar"


def command():
    """The command line that starts this copy of Isobar hidden."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return [appimage, "--hidden"]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--hidden"]
    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0].endswith(".py") else None
    if script and os.path.isfile(script):
        return [sys.executable, script, "--hidden"]
    return [sys.executable, "-m", "isobar", "--hidden"]


def _desktop_quote(arg):
    # Desktop Entry spec: quote, and escape ", `, $ and \ inside quotes.
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def _linux_file():
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "autostart" / f"{APP_ID}.desktop"


def _mac_file():
    return Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"


def _set_linux(on):
    path = _linux_file()
    if not on:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(_desktop_quote(a) for a in command())
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Isobar\n"
        "Comment=Weather alerts in the tray\n"
        f"Exec={exec_line}\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )


def _set_windows(on):
    import winreg  # noqa: PLC0415  (Windows only)

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            value = " ".join(f'"{a}"' if " " in a else a for a in command())
            winreg.SetValueEx(key, "Isobar", 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, "Isobar")
            except FileNotFoundError:
                pass


def _set_mac(on):
    path = _mac_file()
    if not on:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump({"Label": APP_ID, "ProgramArguments": command(), "RunAtLoad": True}, fh)


def apply(on):
    """Turn start-at-login on or off. Raises OSError if the system refused."""
    if sys.platform.startswith("win"):
        _set_windows(bool(on))
    elif sys.platform == "darwin":
        _set_mac(bool(on))
    else:
        _set_linux(bool(on))


def enabled():
    if sys.platform.startswith("win"):
        import winreg  # noqa: PLC0415

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, "Isobar")
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return _mac_file().exists()
    return _linux_file().exists()

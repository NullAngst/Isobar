"""Where things live: bundled web files, config, and cache."""

import os
import sys
from pathlib import Path


def resource_dir() -> Path:
    """Folder holding the isobar package files, frozen or not."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "isobar"
    return Path(__file__).resolve().parent


def web_dir() -> Path:
    return resource_dir() / "web"


def icon_path() -> Path:
    return resource_dir() / "web" / "icon.png"


def _platform_dir(kind: str) -> Path:
    home = Path.home()
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA" if kind == "cache" else "APPDATA")
        return Path(root or home / "AppData" / "Roaming") / "Isobar" / ("Cache" if kind == "cache" else "")
    if sys.platform == "darwin":
        if kind == "cache":
            return home / "Library" / "Caches" / "Isobar"
        return home / "Library" / "Application Support" / "Isobar"
    if kind == "cache":
        return Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache") / "isobar"
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "isobar"


def config_dir() -> Path:
    path = _platform_dir("config")
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = _platform_dir("cache")
    path.mkdir(parents=True, exist_ok=True)
    return path

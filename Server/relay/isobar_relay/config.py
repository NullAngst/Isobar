"""Settings, read once from the environment (compose fills it from .env)."""

import os
import re
from dataclasses import dataclass

from . import REPO_URL, __version__


class ConfigError(Exception):
    """A setting the relay can't start without is missing or wrong."""


def _bool(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, not {raw!r}")


def _int(name, default, lo, hi):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, not {raw!r}") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}, got {value}")
    return value


def _str(name, default=""):
    return (os.environ.get(name) or default).strip()


EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class Config:
    contact_email: str
    server_label: str
    keys_file: str
    require_https: bool
    enable_radar: bool
    nwws_user: str
    nwws_pass: str
    rate_limit: int
    auth_fail_limit: int
    silent_hold_seconds: int
    silent_hold_max: int
    # Internal addresses on the stack network. Not meant to be changed, but
    # they're settings so the tests (and an unusual setup) can point elsewhere.
    open_meteo_url: str
    geocoder_url: str
    tiles_dir: str
    data_dir: str
    listen_port: int
    short_model: str
    nbm_model: str
    gfs_model: str
    air_model: str

    @property
    def nwws_enabled(self):
        return bool(self.nwws_user and self.nwws_pass)

    @property
    def upstream_agent(self):
        # The NWS asks for a unique User-Agent with a way to reach the operator.
        return f"IsobarServer/{__version__} ({REPO_URL}, {self.contact_email})"


def load():
    email = _str("CONTACT_EMAIL")
    if not email:
        raise ConfigError(
            "CONTACT_EMAIL is not set. The NWS requires a contact address in every request's "
            "User-Agent. Put yours in .env (or the stack's environment) and start again.")
    if not EMAIL_RE.fullmatch(email):
        raise ConfigError(f"CONTACT_EMAIL {email!r} does not look like an email address.")
    return Config(
        contact_email=email,
        server_label=_str("SERVER_LABEL", "Isobar Server")[:60],
        keys_file=_str("KEYS_FILE", "/config/keys.txt"),
        require_https=_bool("REQUIRE_HTTPS", True),
        enable_radar=_bool("ENABLE_RADAR", True),
        nwws_user=_str("NWWS_USER"),
        nwws_pass=_str("NWWS_PASS"),
        rate_limit=_int("RATE_LIMIT_PER_KEY", 300, 10, 100000),
        auth_fail_limit=_int("AUTH_FAIL_LIMIT", 20, 1, 100000),
        silent_hold_seconds=_int("SILENT_HOLD_SECONDS", 30, 0, 600),
        silent_hold_max=_int("SILENT_HOLD_MAX", 200, 0, 100000),
        open_meteo_url=_str("OPEN_METEO_URL", "http://open-meteo-api:8080").rstrip("/"),
        geocoder_url=_str("GEOCODER_URL", "http://geocoding:8080").rstrip("/"),
        tiles_dir=_str("TILES_DIR", "/tiles"),
        data_dir=_str("DATA_DIR", "/data"),
        listen_port=_int("LISTEN_PORT", 8787, 1, 65535),
        short_model=_str("SHORT_RANGE_MODEL", "ncep_hrrr_conus"),
        nbm_model=_str("NBM_MODEL", "ncep_nbm_conus"),
        gfs_model=_str("GFS_MODEL", "ncep_gfs_global"),
        air_model=_str("AIR_QUALITY_MODEL", "cams_global"),
    )

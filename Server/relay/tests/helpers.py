"""Shared setup for the relay tests: a config pointing at a throwaway folder."""

import os
import tempfile

from isobar_relay.config import Config

KEY_A = "a" * 40
KEY_B = "b" * 40


def make_config(**changes):
    tmp = tempfile.mkdtemp(prefix="isobar-relay-test-")
    keys = os.path.join(tmp, "keys.txt")
    with open(keys, "w", encoding="utf-8") as fh:
        fh.write(f"#alice\n{KEY_A}\n\n#bob\n{KEY_B}\n")
    values = dict(
        contact_email="ops@example.net",
        server_label="Test Server",
        keys_file=keys,
        require_https=True,
        enable_radar=False,
        nwws_user="",
        nwws_pass="",
        rate_limit=300,
        auth_fail_limit=20,
        silent_hold_seconds=1,
        silent_hold_max=200,
        open_meteo_url="http://127.0.0.1:9",
        geocoder_url="http://127.0.0.1:9",
        tiles_dir=os.path.join(tmp, "tiles"),
        data_dir=os.path.join(tmp, "data"),
        listen_port=0,
        short_model="ncep_hrrr_conus",
        nbm_model="ncep_nbm_conus",
        gfs_model="ncep_gfs_global",
        air_model="cams_global",
    )
    values.update(changes)
    os.makedirs(values["data_dir"], exist_ok=True)
    os.makedirs(values["tiles_dir"], exist_ok=True)
    return Config(**values)

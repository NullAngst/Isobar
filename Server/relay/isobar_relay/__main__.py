"""python -m isobar_relay: check the settings and key file, then serve."""

import logging
import os
import sys

from aiohttp import web

from . import config as config_mod
from .app import Relay
from .keys import KeyRing, KeysError


def main():
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Library chatter (slixmpp's XMPP traffic, aiohttp's internals) stays out of the operator's way.
    for noisy in ("slixmpp", "aiohttp.access", "nwws_receiver", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log = logging.getLogger("isobar_relay")
    try:
        cfg = config_mod.load()
    except config_mod.ConfigError as exc:
        log.error("Not starting: %s", exc)
        return 2
    keys = KeyRing(cfg.keys_file)
    try:
        keys.load_initial()
    except KeysError as exc:
        log.error("Not starting: %s", exc)
        return 2
    os.makedirs(cfg.data_dir, exist_ok=True)
    relay = Relay(cfg, keys)
    web.run_app(relay.app(), host="0.0.0.0", port=cfg.listen_port, access_log=None, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())

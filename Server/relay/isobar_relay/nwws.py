"""NWWS-OI: NWS text products pushed over XMPP within seconds of issue.

Off until NWWS_USER and NWWS_PASS are set. The receiver is nwws-oi-receiver
(slixmpp underneath), and IEM's pyIEM parses each product's VTEC lines,
zones, polygon and impact tags. New and updated warnings go straight into the
alert snapshot; the minute-by-minute NWS feed poll stays the source of truth
and corrects them.

A dropped connection is logged and the receiver started over, without
touching the NWS feed poll, which runs on its own.
"""

import asyncio
import logging
import time
from datetime import timezone

log = logging.getLogger("isobar_relay.nwws")

RECONNECT_AFTER = 90        # seconds without a live connection before starting over
SEVERITY = {"W": "Severe", "A": "Moderate", "Y": "Minor", "S": "Minor"}
DROP_ACTIONS = {"CAN", "EXP", "UPG"}


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _tag(value):
    return [str(value)] if value not in (None, "") else None


def features_from_product(prod, now=None):
    """({VTEC key: NWS-shaped feature}, [VTEC keys that ended]) from a pyIEM product."""
    from pyiem.nws.vtec import get_ps_string
    from shapely.geometry import mapping

    now = time.time() if now is None else now
    add, remove = {}, []
    valid = getattr(prod, "valid", None)
    for seg in getattr(prod, "segments", None) or []:
        for v in seg.vtec or []:
            if v.status != "O":
                continue  # test and experimental products never count
            key = f"{v.office4}.{v.phenomena}.{v.significance}.{int(v.etn):04d}"
            if v.action in DROP_ACTIONS:
                remove.append(key)
                continue
            end = v.endts or seg.ugcexpire
            if end is not None and end.timestamp() <= now:
                remove.append(key)
                continue
            event = get_ps_string(v.phenomena, v.significance) or f"{v.phenomena}.{v.significance}"
            severity = SEVERITY.get(v.significance, "Unknown")
            if v.phenomena in ("TO", "EW") and v.significance == "W":
                severity = "Extreme"
            params = {"VTEC": [str(v)]}
            for name, value in (
                ("tornadoDetection", seg.tornadotag),
                ("maxHailSize", seg.hailtag),
                ("maxWindGust", f"{seg.windtag} {seg.windtagunits or 'MPH'}" if seg.windtag else None),
                ("tornadoDamageThreat", seg.damagetag if v.phenomena == "TO" else None),
                ("thunderstormDamageThreat", seg.damagetag if v.phenomena == "SV" else None),
                ("flashFloodDamageThreat", seg.damagetag if v.phenomena == "FF" else None),
            ):
                tag = _tag(value)
                if tag:
                    params[name] = tag
            ident = f"nwws-{key}-{prod.get_product_id()}"
            begin = v.begints or valid
            add[key] = {
                "id": ident,
                "type": "Feature",
                "geometry": mapping(seg.sbw) if seg.sbw is not None else None,
                "properties": {
                    "id": ident,
                    "event": event,
                    "headline": f"{event} issued by NWS {v.office}",
                    "severity": severity,
                    "urgency": "Immediate" if v.significance == "W" else "Expected",
                    "certainty": "Observed" if seg.tornadotag == "OBSERVED" else "Likely",
                    "status": "Actual",
                    "messageType": "Alert" if v.action == "NEW" else "Update",
                    "sent": _iso(valid),
                    "effective": _iso(valid),
                    "onset": _iso(begin),
                    "expires": _iso(end),
                    "ends": _iso(end),
                    "senderName": f"NWS {v.office}",
                    "areaDesc": ", ".join(str(u) for u in seg.ugcs),
                    "description": seg.unixtext,
                    "instruction": None,
                    "geocode": {"UGC": [str(u) for u in seg.ugcs]},
                    "parameters": params,
                    "references": [],
                },
            }
    # A CAN for some counties next to a CON for the rest is a partial cancel:
    # the event goes on, with the CON segment's area.
    return add, [key for key in dict.fromkeys(remove) if key not in add]


def parse_text(text, now=None):
    from pyiem.nws.products import parser

    # Empty providers keep pyIEM from reaching for IEM's own database.
    prod = parser(text, ugc_provider={}, nwsli_provider={})
    return features_from_product(prod, now)


class Ingester:
    def __init__(self, config, alerts, health):
        self.cfg = config
        self.alerts = alerts
        self.health = health
        self.connected = False
        self.last_product = None
        self.products = 0

    async def run(self):
        from nwws_receiver import WxWire, WxWireConfig

        while True:
            client = WxWire(WxWireConfig(username=self.cfg.nwws_user, password=self.cfg.nwws_pass))
            reader = asyncio.create_task(self._read(client))
            try:
                await client.start()
                await self._watch(client)
            except Exception as exc:  # noqa: BLE001  (never let the receiver take down the relay)
                self.health.fail("NWWS-OI", exc)
            finally:
                self.connected = False
                reader.cancel()
                try:
                    await client.stop(reason="reconnecting")
                except Exception:  # noqa: BLE001
                    pass
            log.warning("NWWS-OI connection dropped; reconnecting in 15 seconds")
            await asyncio.sleep(15)

    async def _watch(self, client):
        lost = None
        while True:
            await asyncio.sleep(5)
            if client.is_client_connected():
                if not self.connected:
                    log.info("NWWS-OI connected")
                    self.health.ok("NWWS-OI")
                self.connected = True
                lost = None
                continue
            self.connected = False
            lost = lost or time.time()
            if time.time() - lost > RECONNECT_AFTER:
                self.health.fail("NWWS-OI", "not connected")
                return

    async def _read(self, client):
        async for message in client:
            self.last_product = time.time()
            self.products += 1
            text = getattr(message, "noaaport", "") or ""
            if "/O." not in text:
                continue  # no VTEC: nothing for the alert snapshot
            try:
                add, remove = await asyncio.to_thread(parse_text, text)
            except Exception as exc:  # noqa: BLE001  (one odd product must not stop the stream)
                log.info("NWWS-OI: could not parse %s: %s", getattr(message, "awipsid", "?"), exc)
                continue
            if add or remove:
                self.alerts.nwws_update(add, remove)
                log.info("NWWS-OI: %s added or updated, %s ended (%s)", len(add), len(remove),
                         getattr(message, "awipsid", "?"))

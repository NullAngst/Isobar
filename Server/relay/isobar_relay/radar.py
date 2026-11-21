"""MRMS fallback radar tiles, built by the mrms-radar container.

That container writes one folder of XYZ tiles per frame (zoom 3 to 8) and an
index.json listing the frames, on a volume the relay reads but never writes.
The app only uses these when IEM's radar fails.
"""

import json
import os
import re
import struct
import time
import zlib

from . import datasets

FRAME_RE = re.compile(r"\d{8}-\d{6}")
MIN_ZOOM, MAX_ZOOM = 3, 8
# A frame list older than this means the radar container stalled.
INDEX_STALE = 15 * 60


def _blank_png(size=256):
    """A fully transparent PNG, for tiles outside the radar's coverage."""
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * size for _ in range(size))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


BLANK = _blank_png()


class Radar:
    def __init__(self, tiles_dir, enabled):
        self.dir = tiles_dir
        self.enabled = enabled
        self._index = None
        self._mtime = None

    def index(self):
        path = os.path.join(self.dir, "index.json")
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return None
        if mtime != self._mtime:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                return self._index
            frames = [f for f in data.get("frames") or []
                      if isinstance(f, dict) and FRAME_RE.fullmatch(str(f.get("id") or ""))]
            self._index = {"frames": frames, "updated": data.get("updated"), "mtime": mtime}
            self._mtime = mtime
        return self._index

    def newest_time(self):
        index = self.index()
        if not index or not index["frames"]:
            return None
        return index["mtime"]

    def available(self, now=None):
        if not self.enabled:
            return False
        newest = self.newest_time()
        now = time.time() if now is None else now
        return newest is not None and now - newest < INDEX_STALE

    def frames(self):
        index = self.index() or {"frames": []}
        return [{"id": f["id"], "time": f.get("time")} for f in index["frames"]]

    def tile(self, frame, z, x, y):
        """PNG bytes for a tile, BLANK where the frame has no tile there, or
        None if the request doesn't name a real frame and tile."""
        if not FRAME_RE.fullmatch(frame):
            return None
        try:
            z, x, y = int(z), int(x), int(y)
        except ValueError:
            return None
        if not MIN_ZOOM <= z <= MAX_ZOOM or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
            return None
        if frame not in {f["id"] for f in (self.index() or {"frames": []})["frames"]}:
            return None
        path = os.path.join(self.dir, frame, str(z), str(x), f"{y}.png")
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return BLANK
        except OSError:
            return None

    def describe(self):
        index = self.index()
        frames = (index or {}).get("frames") or []
        return {
            "frames": len(frames),
            "newest": frames[-1].get("time") if frames else None,
            "fetched_at": datasets.iso(self.newest_time()),
        }

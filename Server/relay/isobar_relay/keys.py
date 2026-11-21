"""Access keys, one per person, from a plain text file.

The format:

    #friend1
    3f9c...64 hex characters...

A # line names the key on the next non-blank line. The name only goes in
the log. The file is checked every few seconds and reloaded when it changes,
so adding, replacing or removing a line takes effect without a restart.

Keys are compared by their SHA-256 digests with hmac.compare_digest, against
every key in the list, so how long a check takes says nothing about which
key, if any, was close.
"""

import hashlib
import hmac
import logging
import os

log = logging.getLogger("isobar_relay.keys")

MIN_KEY_LENGTH = 16


class KeysError(Exception):
    """The key file can't be used to start the relay."""


def _digest(key):
    return hashlib.sha256(key.encode("utf-8")).digest()


def parse(text):
    """Turn the file's text into ({digest: label}, [warnings])."""
    keys = {}
    warnings = []
    label = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            label = line[1:].strip() or None
            continue
        name = label or f"unlabeled-{number}"
        label = None
        if len(line) < MIN_KEY_LENGTH:
            warnings.append(f"line {number} ({name}): key shorter than {MIN_KEY_LENGTH} characters, skipped")
            continue
        digest = _digest(line)
        if digest in keys:
            warnings.append(f"line {number} ({name}): same key as {keys[digest]}, which keeps it")
            continue
        keys[digest] = name
    return keys, warnings


class KeyRing:
    def __init__(self, path):
        self.path = path
        self._keys = {}
        self._signature = None

    def _stat(self):
        st = os.stat(self.path)
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _read(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def load_initial(self):
        """Load at startup. Refuses to go on with no usable keys, so a server
        never comes up open by accident."""
        try:
            signature = self._stat()
            text = self._read()
        except FileNotFoundError as exc:
            raise KeysError(
                f"The key file {self.path} does not exist. Copy keys.txt.example to keys.txt, "
                "add a key per person, and mount it into the relay (see Server/README.md).") from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise KeysError(f"The key file {self.path} can't be read: {exc}") from exc
        keys, warnings = parse(text)
        for warning in warnings:
            log.warning("keys.txt %s", warning)
        if not keys:
            raise KeysError(
                f"The key file {self.path} holds no usable keys. Each key needs its own line and at "
                f"least {MIN_KEY_LENGTH} characters. Make one with: openssl rand -hex 32")
        self._keys = keys
        self._signature = signature
        log.info("loaded %s keys: %s", len(keys), ", ".join(sorted(keys.values())))

    def reload_if_changed(self):
        """Called every few seconds. A file that can't be read keeps the last
        good list. A file with every key removed rejects every request."""
        try:
            signature = self._stat()
        except OSError as exc:
            if self._signature is not None:
                log.error("keys.txt can't be read (%s); keeping the last good list", exc)
                self._signature = None
            return False
        if signature == self._signature:
            return False
        try:
            text = self._read()
        except (OSError, UnicodeDecodeError) as exc:
            log.error("keys.txt changed but can't be read (%s); keeping the last good list", exc)
            return False
        keys, warnings = parse(text)
        for warning in warnings:
            log.warning("keys.txt %s", warning)
        old = {label: digest for digest, label in self._keys.items()}
        new = {label: digest for digest, label in keys.items()}
        self._keys = keys
        self._signature = signature
        if not keys:
            log.error("keys.txt now holds no usable keys; every request gets no answer until one is added")
        else:
            added = sorted(set(new) - set(old))
            removed = sorted(set(old) - set(new))
            replaced = sorted(label for label in set(new) & set(old) if new[label] != old[label])
            log.info("keys.txt reloaded: %s keys (added %s, replaced %s, removed %s)", len(keys),
                     ", ".join(added) or "none", ", ".join(replaced) or "none", ", ".join(removed) or "none")
        return True

    def check(self, key):
        """The label for a working key, or None."""
        if not key:
            return None
        digest = _digest(key)
        found = None
        for known, label in self._keys.items():
            if hmac.compare_digest(digest, known) and found is None:
                found = label
        return found

    def __len__(self):
        return len(self._keys)

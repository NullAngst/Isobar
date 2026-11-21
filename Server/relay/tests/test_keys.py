"""keys.txt: format rules, startup refusal and live reloads."""

import os
import tempfile
import time
import unittest

from isobar_relay.keys import KeyRing, KeysError, parse

GOOD = "0123456789abcdef0123"


class Format(unittest.TestCase):
    def test_labels_blank_lines_and_trimming(self):
        keys, warnings = parse(f"#friend1\n\n   {GOOD}1  \n#friend2\n{GOOD}2\n")
        self.assertEqual(sorted(keys.values()), ["friend1", "friend2"])
        self.assertEqual(warnings, [])

    def test_unlabeled_short_and_duplicate(self):
        text = f"{GOOD}1\n#short\nabc\n#dupe\n{GOOD}1\n"
        keys, warnings = parse(text)
        self.assertEqual(list(keys.values()), ["unlabeled-1"])
        self.assertTrue(any("shorter" in w for w in warnings))
        self.assertTrue(any("same key as unlabeled-1" in w for w in warnings))


class Ring(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "keys.txt")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)
        # Make sure the change shows up even on filesystems with coarse timestamps.
        stamp = time.time() + len(text)
        os.utime(self.path, (stamp, stamp))

    def test_missing_or_empty_refuses_to_start(self):
        with self.assertRaises(KeysError) as ctx:
            KeyRing(self.path).load_initial()
        self.assertIn("does not exist", str(ctx.exception))
        self.write("#nobody\nshort\n")
        with self.assertRaises(KeysError) as ctx:
            KeyRing(self.path).load_initial()
        self.assertIn("no usable keys", str(ctx.exception))

    def test_replace_one_line_without_restart(self):
        self.write(f"#a\n{GOOD}A\n#b\n{GOOD}B\n")
        ring = KeyRing(self.path)
        ring.load_initial()
        self.assertEqual(ring.check(f"{GOOD}A"), "a")
        self.write(f"#a\n{GOOD}NEW\n#b\n{GOOD}B\n")
        self.assertTrue(ring.reload_if_changed())
        self.assertIsNone(ring.check(f"{GOOD}A"))
        self.assertEqual(ring.check(f"{GOOD}NEW"), "a")
        self.assertEqual(ring.check(f"{GOOD}B"), "b")

    def test_unreadable_keeps_last_list_and_empty_rejects_all(self):
        self.write(f"#a\n{GOOD}A\n")
        ring = KeyRing(self.path)
        ring.load_initial()
        with open(self.path, "wb") as fh:
            fh.write(b"\xff\xfe\x00 not utf-8 \xc3")
        os.utime(self.path, (time.time() + 99, time.time() + 99))
        ring.reload_if_changed()
        self.assertEqual(ring.check(f"{GOOD}A"), "a")
        self.write("# everyone removed\n")
        ring.reload_if_changed()
        self.assertIsNone(ring.check(f"{GOOD}A"))
        self.assertEqual(len(ring), 0)

    def test_wrong_and_empty_keys(self):
        self.write(f"#a\n{GOOD}A\n")
        ring = KeyRing(self.path)
        ring.load_initial()
        self.assertIsNone(ring.check(""))
        self.assertIsNone(ring.check(None))
        self.assertIsNone(ring.check(f"{GOOD}a"))


if __name__ == "__main__":
    unittest.main()

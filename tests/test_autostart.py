import os
import sys
import unittest
from unittest import mock

from tests import helpers  # noqa: F401
from isobar import autostart


@unittest.skipUnless(sys.platform.startswith("linux"), "the .desktop file is the Linux path")
class LinuxAutostart(unittest.TestCase):
    def test_desktop_file_round_trip(self):
        with mock.patch.dict(os.environ, {"APPIMAGE": "/home/z/Apps/Isobar $weird.AppImage"}):
            autostart.apply(True)
        path = autostart._linux_file()
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn('Exec="/home/z/Apps/Isobar \\$weird.AppImage" "--hidden"', text)
        self.assertTrue(autostart.enabled())
        autostart.apply(False)
        self.assertFalse(path.exists())
        autostart.apply(False)  # already off is fine


if __name__ == "__main__":
    unittest.main()

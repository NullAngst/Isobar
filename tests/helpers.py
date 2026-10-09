"""Shared test setup: point config and cache at a throwaway folder before anything reads them."""

import os
import tempfile

TMP = tempfile.mkdtemp(prefix="isobar-test-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "cache")

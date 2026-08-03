"""Isolate application data for the entire test process."""

import os
import tempfile
from pathlib import Path

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="wnacg-tests-"))
os.environ["WNACG_DATA_DIR"] = str(_TEST_DATA_DIR)

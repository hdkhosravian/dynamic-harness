"""Every test here drives the real `claude` binary, which costs money: skipped unless HARNESS_E2E=1."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))  # harness_core, even when tests/conftest.py is not loaded


@pytest.fixture(autouse=True)
def _opt_in():
    if os.environ.get("HARNESS_E2E") != "1":
        pytest.skip("set HARNESS_E2E=1 to run: these tests drive the real claude binary and the run costs money")

#!/usr/bin/python3 -I
"""research.check: see harness_core.research.check (runs under harness-run and the gate oracle, in a sandbox)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from harness_core import checklib, research  # noqa: E402

checklib.main(research.check)

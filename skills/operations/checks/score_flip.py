#!/usr/bin/python3 -I
"""See harness_core.operations.score_flip (runs under harness-run and the gate oracle, in a sandbox)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from harness_core import checklib, operations  # noqa: E402

checklib.main(operations.score_flip)

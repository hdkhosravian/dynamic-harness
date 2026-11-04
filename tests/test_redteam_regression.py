"""Red-team corpora as regression gates: recall may not drop, and no legitimate call may be denied."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _measure(script):
    spec = importlib.util.spec_from_file_location(script, HERE / f"{script}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.measure()


@pytest.mark.parametrize("script,min_hit", [("redteam_measure", 133), ("redteam2_measure", 188)])
def test_redteam_recall_and_false_denies(script, min_hit):
    r = _measure(script)
    assert not r["errors"], r["errors"]
    assert r["deny_hit"] >= min_hit, [m["id"] for m in r["misses"]]
    assert not r["false_denies"], [f["id"] for f in r["false_denies"]]

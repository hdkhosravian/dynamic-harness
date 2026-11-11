"""One scripted run per pack, from a request to a signed result, with the real `claude` binary."""
from __future__ import annotations

import os

import pytest

import driver


@pytest.mark.parametrize("pack", ["finance", "model", "operations", "research"])
def test_pack(pack, tmp_path, record_property):
    stage, exc, v = "PREFLIGHT", None, None
    try:
        claude, lc = driver.preflight(os.environ)  # fails, not skips, when claude or loop-contract is missing
        ws = driver.make_workspace(tmp_path, pack)
        env = driver.build_env(tmp_path, lc, os.environ)
        stage = "SETUP"
        driver.setup_folder(claude, pack, ws, env, lc)
        stage = "RUN"
        v = driver.converse(claude, pack, ws, env, lc)
    except BaseException as e:
        exc = e
        raise
    finally:  # one line for every outcome, so a later count sees a failure line and not a missing line
        state = driver.result_state(stage, exc, v)
        record_property("e2e_state", state)
        print(f"\nE2E_RESULT pack={pack} state={state}")
    assert driver.passes(v), f"{pack}: {state}"

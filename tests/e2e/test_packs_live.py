"""One scripted run per pack, from a request to a signed result, with the real `claude` binary."""
from __future__ import annotations

import os

import pytest

import driver


@pytest.mark.parametrize("pack", ["finance", "model", "operations", "research"])
def test_pack(pack, tmp_path, record_property):
    claude, lc = driver.preflight(os.environ)  # fails, not skips, when claude or loop-contract is missing
    ws = driver.make_workspace(tmp_path, pack)
    env = driver.build_env(tmp_path, lc, os.environ)
    driver.setup_folder(claude, ws, env, lc)
    v = driver.converse(claude, pack, ws, env, lc)
    state = v.get("state") or v["reason"]
    record_property("e2e_state", state)
    print(f"\nE2E_RESULT pack={pack} receipt_verifies={v['ok']} state={state}")  # a later task counts "Checked" here
    assert driver.passes(v), f"{pack}: {state}"

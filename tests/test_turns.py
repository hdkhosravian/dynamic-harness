from __future__ import annotations

import json

import pytest

from harness_core import canon
from harness_core.errors import Denied, SchemaError, TamperError
from harness_core.turns import Turns


@pytest.fixture
def turns(env):
    return Turns(env["data"])


def test_append_writes_row_with_seq_and_text_sha(turns, env):
    t = turns.append({"turn_id": "t1", "text_norm": "drop unit two"})
    assert t["seq"] == 1 and t["text_sha"] == canon.sha256_bytes(b"drop unit two") and t["ts"]
    t2 = turns.append({"turn_id": "t2", "text_norm": "x", "typed_command": "/harness:setup a"})
    assert t2["seq"] == 2
    rows = [json.loads(x) for x in (env["data"] / "turns.jsonl").read_text().splitlines()]
    assert [r["turn_id"] for r in rows] == ["t1", "t2"]
    assert turns.latest()["turn_id"] == "t2"


def test_append_rejects_bad_rows(turns):
    with pytest.raises(SchemaError):
        turns.append({"turn_id": "t1"})  # no text_norm
    with pytest.raises(SchemaError):
        turns.append({"turn_id": "t1", "text_norm": "x", "bogus": 1})
    turns.append({"turn_id": "t1", "text_norm": "x"})
    with pytest.raises(SchemaError):
        turns.append({"turn_id": "t1", "text_norm": "dup"})


def test_cite_existence_ordering_and_match(turns):
    turns.append({"turn_id": "t1", "text_norm": "hello"})
    turns.append({"turn_id": "t2", "text_norm": "please drop Invoices and Payroll"})
    assert turns.cite("t2")["turn_id"] == "t2"
    with pytest.raises(Denied):
        turns.cite("nope")
    with pytest.raises(Denied):
        turns.cite("t1", after_seq=1)  # must come strictly after
    assert turns.cite("t2", after_seq=1)["seq"] == 2
    assert turns.cite("t2", must_match=["payroll"])
    with pytest.raises(Denied):
        turns.cite("t2", must_match=["Taxes"])
    with pytest.raises(Denied):
        turns.cite("t2", must_match=["voice"])  # substring inside a word is not a match
    with pytest.raises(Denied):
        turns.cite("t2", must_match=[])


def test_cite_detects_text_norm_edit(turns, env):
    turns.append({"turn_id": "t1", "text_norm": "keep everything"})
    p = env["data"] / "turns.jsonl"
    p.write_text(p.read_text().replace("keep everything", "drop everything"))
    with pytest.raises(TamperError):
        turns.cite("t1", must_match=["drop"])


def test_nonce_one_use_and_latest_only(turns):
    turns.append({"turn_id": "t1", "text_norm": "x", "typed_command": "/harness:config set a=1"})
    assert not turns.nonce_ok("/harness:config", ["set", "a=2"])
    assert turns.nonce_ok("/harness:config", ["set", "a=1"])
    assert not turns.nonce_ok("/harness:config", ["set", "a=1"])  # consumed
    turns.append({"turn_id": "t2", "text_norm": "y", "typed_command": "/harness:off"})
    turns.append({"turn_id": "t3", "text_norm": "z"})
    assert not turns.nonce_ok("/harness:off", [])  # not the latest turn


def test_nonce_string_args(turns):
    turns.append({"turn_id": "t1", "text_norm": "x", "typed_command": "/harness:off"})
    assert turns.nonce_ok("/harness:off", "")

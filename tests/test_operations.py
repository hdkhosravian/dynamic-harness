"""Operations pack: doc.check and decide.score_flip (build phase P5)."""
from __future__ import annotations

import json

import pytest

from harness_core import packs
from harness_core.errors import HarnessError
from test_budget import log_of
from test_p3_hooks import CARD, compiled, ctx_of, h  # noqa: F401  (fixture h)
from test_run import run_req

DOC = dict(CARD, task_type="document", deliverable={"kind": "document", "description": "proposal for the client"},
           oracle={"type": "doc.check", "executable": True, "criteria": ["every required item"]})
DEC = dict(CARD, task_type="decision_support", deliverable={"kind": "decision_memo", "description": "vendor choice"},
           oracle={"type": "decide.score_flip", "executable": True, "criteria": ["scores recomputed"]})


def _profile(h, **prof):
    (h["data"] / "business").mkdir(exist_ok=True)
    (h["data"] / "business" / "profile.json").write_text(json.dumps(prof))


def test_a_complete_document_passes(h):
    job = compiled(h, card=DOC, text="draft the proposal for the client")
    _profile(h, footer="Karimi Trading, Tehran", do_not_contact=["Rahimi Co"])
    p, log = log_of(h, job)
    (p.work / "proposal.md").write_text("Dear client,\nPrice and delivery date are below.\n\nKarimi Trading, Tehran\n")
    res = run_req(h, check="doc.check", inputs=(), period=None, document="proposal.md",
                  required=["price", "delivery date"])
    assert res["result"] == "pass", res["notes"]


def test_placeholders_missing_items_footer_and_blocked_recipients_fail(h):
    job = compiled(h, card=DOC, text="draft the proposal for the client")
    _profile(h, footer="Karimi Trading, Tehran", do_not_contact=["Rahimi Co"])
    p, log = log_of(h, job)
    (p.work / "proposal.md").write_text("Dear Rahimi Co,\nPrice: [amount]. Signed {{name}}.\n")
    res = run_req(h, check="doc.check", inputs=(), period=None, document="proposal.md",
                  required=["price", "delivery date"])
    joined = " | ".join(res["notes"])
    assert res["result"] == "fail"
    for want in ("delivery date", "[amount]", "{{name}}", "footer", "do-not-contact"):
        assert want in joined


DECISION = {"criteria": [{"id": "price", "weight": 5, "source": "owner"}, {"id": "quality", "weight": 4,
                                                                            "source": "owner"}],
            "options": [{"id": "vendor_a", "scores": {"price": 9, "quality": 5}},
                        {"id": "vendor_b", "scores": {"price": 6, "quality": 8}},
                        {"id": "vendor_c", "scores": {"price": 10, "quality": 1}}],
            "constraints": [{"criterion": "quality", "min": 3}]}


def test_scores_constraints_and_the_flip_test(h):
    compiled(h, card=DEC, text="which vendor should we pick for packaging?")
    res = run_req(h, check="decide.score_flip", inputs=(), period=None, **DECISION)
    v = res["values"]
    assert res["result"] == "pass" and v["eligible"] == 2 and v["winner"] == "vendor_a"
    assert v["flips"] == ["price -20% -> a tie"]  # a: (36+20)/8 = 7.0, b: (24+32)/8 = 7.0
    assert any("vendor_c breaks a constraint" in n for n in res["notes"])


def test_weights_not_from_the_owner_are_noted(h):
    compiled(h, card=DEC, text="which vendor should we pick for packaging?")
    d = dict(DECISION, criteria=[{"id": "price", "weight": 5}, {"id": "quality", "weight": 4, "source": "owner"}])
    res = run_req(h, check="decide.score_flip", inputs=(), period=None, **d)
    assert any("weights not set by you: price" in n for n in res["notes"])


def test_two_packs_cannot_register_one_check(h, tmp_path, monkeypatch):
    import shutil
    root = tmp_path / "plugin"
    shutil.copytree(packs._root(ctx_of(h)), root / "skills")
    m = json.loads((root / "skills" / "operations" / "manifest.json").read_text())
    m["checks"]["finance.reconcile"] = m["checks"]["doc.check"]
    (root / "skills" / "operations" / "manifest.json").write_text(json.dumps(m))
    c = ctx_of(h)
    c.plugin_root = root
    with pytest.raises(HarnessError, match="already registered"):
        packs.manifests(c)

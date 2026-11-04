"""Business layer: the owner's typed command, confirmed definitions, money caps, estimates (build phase P5)."""
from __future__ import annotations

import json
import re

import pytest

from harness_core import budget, business, contact, estimate, hookmain, session
from harness_core.errors import Denied
from test_budget import blocked, log_of
from test_p3_hooks import SID, compiled, ctx_of, h, post, say  # noqa: F401  (fixture h)


def typed(h, line, pid):
    code, out = say(h, line, pid)
    assert code == 0 and out["decision"] == "block", out
    return out["reason"]


def confirm(h, cmd, shown, pid):
    code = re.search(r"confirm (\d{6})", shown).group(1)
    return typed(h, f"/dynamic-harness:{cmd} confirm {code}", pid)


def test_show_is_answered_by_the_hook_and_never_reaches_the_model(h):
    assert "nothing is set yet" in typed(h, "/dynamic-harness:business show", "b1")


def test_set_needs_the_code_and_then_records_a_baseline(h):
    shown = typed(h, "/dynamic-harness:business set currency irt", "b1")
    assert "Toman (IRT)" in shown and business.profile(ctx_of(h)) == {}
    assert "Done." in confirm(h, "business", shown, "b2")
    c = ctx_of(h)
    assert business.profile(c)["currency"] == "IRT"
    rec = session.recorded_hashes(h["data"] / "config_log.jsonl")
    assert rec["business"] == session.current_hashes(c)["business"]
    # a new session starts clean: the owner's change is the baseline, not tamper
    assert hookmain.main("SessionStart", {"session_id": "s2", "cwd": str(h["ws"]), "source": "startup",
                                          "permission_mode": "default"}, h["env"])[0] == 0
    status = json.loads((h["data"] / "sessions" / "s2" / "status.json").read_text())
    assert status["ok"] is True, status


def test_a_code_works_once_and_only_in_its_session(h):
    shown = typed(h, "/dynamic-harness:business set caps.task 500000", "b1")
    code = re.search(r"confirm (\d{6})", shown).group(1)
    other = hookmain.main("UserPromptSubmit", {"session_id": "other", "prompt_id": "x1", "cwd": str(h["ws"]),
                                               "prompt": f"/dynamic-harness:business confirm {code}"}, h["env"])
    assert "another session" in other[1]["reason"]
    assert "does not match" in typed(h, f"/dynamic-harness:business confirm {code}", "b3") or \
           "Nothing is waiting" in typed(h, f"/dynamic-harness:business confirm {code}", "b4")
    assert business.profile(ctx_of(h)) == {}


def test_bad_values_are_refused_with_what_is_expected(h):
    assert "3-letter code" in typed(h, "/dynamic-harness:business set currency toman", "b1")
    assert "positive number" in typed(h, "/dynamic-harness:business set caps.task -5", "b2")
    assert "unknown field" in typed(h, "/dynamic-harness:business set colour blue", "b3")


def test_an_out_of_band_edit_is_never_re_baselined(h):
    (h["data"] / "business").mkdir(exist_ok=True)
    (h["data"] / "business" / "profile.json").write_text('{"currency": "USD"}')
    assert "changed outside the harness" in typed(h, "/dynamic-harness:business set currency IRT", "b1")


def test_lists_and_protected_terms(h):
    shown = typed(h, "/dynamic-harness:business add protected_terms 'Rahimi Co'", "b1")
    confirm(h, "business", shown, "b2")
    assert ctx_of(h).protected_terms == ["Rahimi Co"]
    shown = typed(h, "/dynamic-harness:business add own_accounts 0101-22", "b3")
    confirm(h, "business", shown, "b4")
    assert business.profile(ctx_of(h))["own_accounts"] == ["0101-22"]
    assert "Rahimi Co" in typed(h, "/dynamic-harness:business show", "b5")


METRIC = {"metric_id": "revenue", "inclusions": "sales invoices minus returns", "exclusions": "VAT",
          "unit": "amount", "currency": "irt"}


def test_a_definition_is_written_only_after_the_owner_confirms_the_rendered_sentence(h):
    compiled(h)
    c = ctx_of(h)
    out = contact.apply({"tool": "contact", "type": "definition_confirm", "metric": METRIC}, c, {"session_id": SID})
    assert out["text"] == ("I'll count revenue as sales invoices minus returns, excluding VAT, in Toman. "
                           "Is that right?")
    with pytest.raises(Denied):  # no reply yet
        contact.apply({"tool": "contact", "type": "definition_confirm", "accept": "latest"}, c, {"session_id": SID})
    say(h, "yes", "r1")
    res = contact.apply({"tool": "contact", "type": "definition_confirm", "accept": "latest"}, ctx_of(h),
                        {"session_id": SID})
    entry = business.dictionary(ctx_of(h))["revenue"]
    assert res["defined"] == "revenue" and entry["version"] == 1 and entry["approved_by"] == "owner"
    assert entry["sentence"] == out["text"]


def test_a_changed_definition_gets_a_new_version(h):
    compiled(h)
    for i, (m, reply) in enumerate(((METRIC, "yes"), (dict(METRIC, exclusions="VAT and shipping"), "ok"))):
        contact.apply({"tool": "contact", "type": "definition_confirm", "metric": m}, ctx_of(h), {"session_id": SID})
        say(h, reply, f"r{i}")
        contact.apply({"tool": "contact", "type": "definition_confirm", "accept": "latest"}, ctx_of(h),
                      {"session_id": SID})
    e = business.dictionary(ctx_of(h))["revenue"]
    assert e["version"] == 2 and e["previous_version"] == 1


def test_a_non_approval_does_not_define(h):
    compiled(h)
    contact.apply({"tool": "contact", "type": "definition_confirm", "metric": METRIC}, ctx_of(h), {"session_id": SID})
    say(h, "no, include VAT", "r1")
    with pytest.raises(Denied):
        contact.apply({"tool": "contact", "type": "definition_confirm", "accept": "latest"}, ctx_of(h),
                      {"session_id": SID})
    assert business.dictionary(ctx_of(h)) == {}


def _profile(h, **prof):
    (h["data"] / "business").mkdir(exist_ok=True)
    (h["data"] / "business" / "profile.json").write_text(json.dumps(prof))


def _transcript(tmp_path, tokens_in, tokens_out):
    tr = tmp_path / "t.jsonl"
    tr.write_text(json.dumps({"type": "assistant", "timestamp": "2099-01-01T00:00:00Z",
                              "message": {"usage": {"input_tokens": tokens_in, "output_tokens": tokens_out}}}) + "\n")
    return tr


def test_the_task_cap_converts_dollar_prices_with_the_owners_rate(h, tmp_path):
    job = compiled(h)
    _profile(h, currency="IRT", caps={"task": 500000, "currency": "IRT"}, fx={"USD": 100000})
    tr = _transcript(tmp_path, 300000, 20000)  # 4.5 + 1.5 USD = 6 USD = 600,000 Toman
    evt = {"session_id": SID, "tool_name": "Read", "tool_input": {"file_path": str(h["ws"] / "orders.csv")},
           "tool_response": {}, "cwd": str(h["ws"]), "transcript_path": str(tr)}
    assert blocked(hookmain.main("PostToolUse", evt, h["env"]))
    p, log = log_of(h, job)
    assert budget.latest_stop(log) == "STOPPED_COST"


def test_without_a_rate_the_money_cap_stays_off(h, tmp_path):
    job = compiled(h)
    _profile(h, currency="IRT", caps={"task": 1, "currency": "IRT"})
    tr = _transcript(tmp_path, 300000, 20000)
    evt = {"session_id": SID, "tool_name": "Read", "tool_input": {"file_path": str(h["ws"] / "orders.csv")},
           "tool_response": {}, "cwd": str(h["ws"]), "transcript_path": str(tr)}
    assert not blocked(hookmain.main("PostToolUse", evt, h["env"]))
    p, log = log_of(h, job)
    assert budget.latest_stop(log) is None


def test_the_monthly_cap_counts_other_tasks_this_month(h, tmp_path):
    import time
    job = compiled(h)
    _profile(h, currency="USD", caps={"task": 100, "month": 10, "currency": "USD"})
    (h["data"] / "records").mkdir(exist_ok=True)
    (h["data"] / "records" / "older.jsonl").write_text(json.dumps(
        {"state": "DONE", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "cost": {"money": 9.5}}) + "\n")
    tr = _transcript(tmp_path, 100000, 0)  # 1.5 USD: 9.5 + 1.5 > 10
    evt = {"session_id": SID, "tool_name": "Read", "tool_input": {"file_path": str(h["ws"] / "orders.csv")},
           "tool_response": {}, "cwd": str(h["ws"]), "transcript_path": str(tr)}
    out = hookmain.main("PostToolUse", evt, h["env"])
    assert blocked(out) and "Continue?" in out[1]["systemMessage"]
    p, log = log_of(h, job)
    assert "monthly" in [e for e in log.events() if e["type"] == "STOPPED"][-1]["payload"]["reason"]


def test_an_estimate_needs_three_past_tasks(h):
    c = ctx_of(h)
    assert estimate.estimate(c, "T1") is None
    (h["data"] / "records").mkdir(exist_ok=True)
    for i, mins in enumerate((10, 20, 30)):
        (h["data"] / "records" / f"j{i}.jsonl").write_text(json.dumps(
            {"state": "DONE", "tier": "T1", "duration_s": mins * 60, "cost": {"money": 1000 * (i + 1)}}) + "\n")
    est = estimate.estimate(c, "T1")
    assert est["minutes"] == (14, 26) and est["money"] == (1400.0, 2600.0) and est["samples"] == 3


def test_the_opening_line_carries_the_estimate(h):
    (h["data"] / "records").mkdir(exist_ok=True)
    for i in range(3):
        (h["data"] / "records" / f"j{i}.jsonl").write_text(json.dumps(
            {"state": "DONE", "tier": "T1", "duration_s": 1200}) + "\n")
    job = compiled(h)
    p, log = log_of(h, job)
    assert "Similar tasks took about 14 to 26 minutes." in (p.run / "opening.txt").read_text()

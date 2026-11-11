"""State words, question marks and the stop gate come from the locale packs (build phase P1, task T2).

New names (``gate.lexicon``, ``stopgate.asks``) are used inside the tests only, so that a missing name fails that
test and does not hide the others. Non-ASCII characters are written as \\u escapes; the fixture files hold them as they
are."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core import admin, gate, locales, sessionstate, store, stopgate
from harness_core.events import EventLog
from harness_core.paths import job_paths
from harness_core.safety import Ctx
from test_business import typed
from test_locales import pack, put, set_profile
from test_p3_hooks import SID, compiled, ctx_of, h, say, st  # noqa: F401  (fixture h)
from test_probe_words import spy, write_probe
from test_stopgate import stop

ROOT = Path(__file__).resolve().parents[1]
OLD_LEX = json.loads((Path(__file__).resolve().parent / "fixtures" / "state_lexicon_0_4_0.json").read_text("utf-8"))
TERMS = [t for v in OLD_LEX["terms"].values() for t in v]  # the 0.4.0 flat list: en, then fa
LINES = [t for v in OLD_LEX["template_lines"].values() for t in v]
LEGACY = {"version": 1, "terms": {"en": ["audited", "checked"], "fa": ["\u062a\u0633\u062a"]},
          "template_lines": {"en": ["The audited line."]}}


def write_legacy(h, content=LEGACY):
    d = h["data"] / "control"
    d.mkdir(parents=True, exist_ok=True)
    (d / "state_lexicon.json").write_text(json.dumps(content), "utf-8")


# --- 2. pinned results of the present code (these pass before and after the change) --------------------------------

# {text: the hits}; recorded from stopgate.lexicon_hits(None, text) at 2de23b1
LEXICON_PINNED = [
    ('The figures are verified.', ['verified']),
    ('All checked', ['checked']),
    ('\u062a\u0623\u06cc\u06cc\u062f \u0634\u062f', ['\u062a\u0623\u06cc\u06cc\u062f \u0634\u062f']),
    ('\u0628\u0631\u0631\u0633\u06cc\u200c\u0634\u062f\u0647', ['\u0628\u0631\u0631\u0633\u06cc\u0634\u062f\u0647']),
    ('\u0628\u0631\u0631\u0633\u06cc \u0634\u062f', ['\u0628\u0631\u0631\u0633\u06cc \u0634\u062f']),
    ('Looks good \u2713', ['\u2713']),
    ('Done \u2714 and confirmed', ['confirmed', '\u2714']),
    ('Arithmetic checked; assumptions not.', []),
    ('\u0645\u062d\u0627\u0633\u0628\u0627\u062a \u0628\u0631\u0631\u0633\u06cc \u0634\u062f\u061b \u0641\u0631\u0636\u200c\u0647\u0627 \u0646\u0647.', []),
    ('Nothing special here', []),
    ('line one\nChecked\nplain', ['checked']),
    ('unverifiable claim', []),
    ('', []),
]


@pytest.mark.parametrize("text,hits", LEXICON_PINNED, ids=[f"l{i:02d}" for i in range(len(LEXICON_PINNED))])
def test_the_state_word_hits_are_pinned(text, hits):
    assert stopgate.lexicon_hits(None, text) == hits


def test_the_pinned_texts_cover_the_cases_of_the_brief():
    assert len(LEXICON_PINNED) >= 8
    assert any(hits for _, hits in LEXICON_PINNED) and any(not hits for _, hits in LEXICON_PINNED)


def test_a_term_in_an_allowed_line_is_no_hit():
    assert stopgate.lexicon_hits(None, "Arithmetic checked; assumptions not.\nverified", allowed=["verified"]) == []
    assert stopgate.lexicon_hits(None, "Arithmetic checked; assumptions not.\nverified") == ["verified"]
    assert stopgate.lexicon_hits(None, "all verified", allowed=["verified"]) == ["verified"]  # the whole line only


# --- 8. gate.lexicon ------------------------------------------------------------------------------------------------


def test_without_a_data_folder_the_lexicon_is_the_0_4_0_flat_lists_in_order(tmp_path):
    for ctx in (None, Ctx(workspace=tmp_path, data_root=None)):
        got = gate.lexicon(ctx)
        assert got == {"terms": TERMS, "template_lines": LINES}
    assert len(TERMS) == 23 and len(LINES) == 2


def test_the_profile_list_does_not_hide_the_state_words_of_a_language(h):
    set_profile(h, locales=["en"])
    assert gate.lexicon(ctx_of(h)) == {"terms": TERMS, "template_lines": LINES}  # the 16 Persian terms stay
    assert len(gate.lexicon(ctx_of(h))["terms"]) == 23


def test_a_live_pack_the_list_does_not_name_adds_its_terms_and_lines_without_repeats(h):
    put(h, "xx.json", pack("xx", state_lexicon={"terms": ["blorped", "checked"], "template_lines": ["Blorp line.", LINES[0]]}))
    set_profile(h, locales=["en"])
    assert gate.lexicon(ctx_of(h)) == {"terms": TERMS + ["blorped"], "template_lines": LINES + ["Blorp line."]}
    assert stopgate.lexicon_hits(ctx_of(h), "it was blorped") == ["blorped"]
    assert stopgate.lexicon_hits(ctx_of(h), "Blorp line.") == []


def test_a_hand_placed_live_lexicon_file_adds_after_the_pack_entries(h):
    write_legacy(h)
    c = ctx_of(h)
    assert gate.lexicon(c) == {"terms": TERMS + ["audited", "\u062a\u0633\u062a"],
                               "template_lines": LINES + ["The audited line."]}
    assert stopgate.lexicon_hits(c, "all audited") == ["audited"]
    assert stopgate.lexicon_hits(c, "The audited line.") == []
    put(h, "xx.json", pack("xx", state_lexicon={"terms": ["blorped"]}))
    assert gate.lexicon(ctx_of(h))["terms"] == TERMS + ["blorped", "audited", "\u062a\u0633\u062a"]  # packs first


def test_a_live_lexicon_that_is_not_a_file_is_ignored(h):
    (h["data"] / "control" / "state_lexicon.json").mkdir(parents=True)
    assert gate.lexicon(ctx_of(h)) == {"terms": TERMS, "template_lines": LINES}


def test_lexicon_accepts_a_job_paths_object_and_the_gate_file_has_two_keys(h):
    job = compiled(h)
    paths = job_paths(h["data"], h["ws"], job)
    assert gate.lexicon(paths) == {"terms": TERMS, "template_lines": LINES}
    out = gate._lexicon(paths)
    assert out == paths.run / "gate" / "lexicon.json"
    assert json.loads(out.read_text("utf-8")) == {"terms": TERMS, "template_lines": LINES}
    write_legacy(h)
    got = json.loads(gate._lexicon(paths).read_text("utf-8"))
    assert set(got) == {"terms", "template_lines"}
    assert got["terms"] == TERMS + ["audited", "\u062a\u0633\u062a"]
    assert got["template_lines"] == LINES + ["The audited line."]


def test_the_shipped_state_lexicon_file_is_gone_and_the_admin_command_does_not_know_it(h):
    assert not (ROOT / "control" / "defaults" / "state_lexicon.json").exists()
    assert "state_lexicon.json" not in admin.FIXED and "state_lexicon.json" not in admin.EDITABLE
    shown = typed(h, "/dynamic-harness:config show state_lexicon.json", "c1")
    assert shown.startswith("No control file 'state_lexicon.json'") and "fixed in v1" not in shown


def test_no_code_under_lib_names_state_lexicon_json_except_gate_py():
    named = sorted(p.name for p in (ROOT / "lib").rglob("*.py") if "state_lexicon.json" in p.read_text("utf-8"))
    assert named == ["gate.py"]


def test_the_text_of_stopgate_py_names_neither_probe_json_nor_the_old_regex():
    text = (ROOT / "lib" / "harness_core" / "stopgate.py").read_text("utf-8")
    assert "probe.json" not in text and "_ASKS" not in text and "\u061f" not in text
    assert not hasattr(stopgate, "_ASKS")


# --- 10. question marks ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("msg,asked", [
    ("Which week do you mean?", True),
    ("Which week do you mean\u061f", True),
    ("Which week?   ", True),
    ("Which week?\t\n", True),
    ("Which week?\nI will wait.", True),         # a mark at the end of a middle line
    ("First line.\nWhich week\u061f\nthanks", True),
    ("?", True),
    ("Which week do you mean", False),
    ("What? Nothing more.", False),              # a mark in the middle of a line
    ("", False),
    (None, False),
])
def test_asks_is_true_when_a_line_ends_with_a_mark_of_an_installed_pack(h, msg, asked):
    assert stopgate.asks(None, msg) is asked
    assert stopgate.asks(ctx_of(h), msg) is asked


def test_asks_with_no_mark_gives_false_and_does_not_fail(monkeypatch):
    for view in ({}, {"question_marks": []}, {"words": {}}):
        monkeypatch.setattr(locales, "view", lambda ctx=None, every=False, v=view: v)
        assert stopgate.asks(None, "Which week?") is False
        assert stopgate.asks(None, None) is False


def test_asks_takes_its_marks_from_the_view_and_escapes_them(monkeypatch):
    monkeypatch.setattr(locales, "view", lambda ctx=None, every=False: {"question_marks": ["."]})
    assert stopgate.asks(None, "Which week.") is True and stopgate.asks(None, "Which week") is False
    assert stopgate.asks(None, "Which week?") is False  # "." is a mark here, not "any character"
    assert stopgate.asks(None, "a.b") is False


# --- 10b. the stop path ---------------------------------------------------------------------------------------------


MONEY_TEXT = "Paris. That is 400 {}."


def test_a_money_word_of_the_owner_blocks_a_quick_answer_that_names_a_figure(h):
    write_probe(h, {"money_words": ["zloty"]})
    say(h, "What is the capital of France?", "p1")
    assert stop(h, MONEY_TEXT.format("zloty"))[0] == 2
    assert st(h)["pending_card"]["floor"] == "T1"
    assert st(h)["pending_card"]["reason"] == "money in the answer: ['zloty']"


def test_a_money_word_of_a_live_pack_blocks_only_while_the_pack_is_active(h):
    put(h, "xx.json", pack("xx", words={"money_words": ["zloty"]}))
    say(h, "What is the capital of France?", "p1")
    assert stop(h, MONEY_TEXT.format("zloty"))[0] == 2
    assert st(h)["pending_card"]["reason"] == "money in the answer: ['zloty']"
    sessionstate.update(ctx_of(h), SID, pending_card=None)
    set_profile(h, locales=["en"])
    assert stop(h, MONEY_TEXT.format("zloty")) == (0, None)
    assert st(h)["pending_card"] is None


def test_a_region_money_word_is_matched_only_when_the_region_pack_is_active(h):
    say(h, "What is the capital of France?", "p1")
    assert stop(h, MONEY_TEXT.format("toman"))[0] == 2
    assert st(h)["pending_card"]["reason"] == "money in the answer: ['toman']"
    sessionstate.update(ctx_of(h), SID, pending_card=None)
    set_profile(h, locales=["fa"])
    assert stop(h, MONEY_TEXT.format("toman")) == (0, None)


@pytest.mark.parametrize("mark,asked", [("?", True), ("\u061f", True), ("\u2e2e", True), (".", False)])
def test_the_marks_of_the_installed_packs_end_a_question_in_the_final_message_of_a_task(h, mark, asked):
    put(h, "xx.json", pack("xx", question_marks=["\u2e2e"]))
    job = compiled(h)
    out = stop(h, "Which week do you mean" + mark)
    if not asked:
        assert out[0] == 2
        return
    assert out == (0, None)
    p = job_paths(h["data"], h["ws"], job)
    rows = store.read_json(p.run, EventLog(p.run, p.heads), "contacts.json")["rows"]
    assert rows[-1]["type"] == "question" and st(h)["pending_contact_id"] == rows[-1]["contact_id"]


# --- 11. one read ---------------------------------------------------------------------------------------------------


def test_lexicon_makes_one_view_call_and_lexicon_hits_asks_it_once(h, monkeypatch):
    views = spy(monkeypatch, locales, "view")
    gate.lexicon(ctx_of(h))
    assert len(views) == 1 and (views[0][1].get("every") is True or views[0][0][1:] == (True,))
    del views[:]
    lex = spy(monkeypatch, gate, "lexicon")
    assert stopgate.lexicon_hits(ctx_of(h), "all verified") == ["verified"]
    assert len(lex) == 1 and len(views) == 1

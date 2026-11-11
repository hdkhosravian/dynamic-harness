"""Digit, letter and format folds no longer name one language in code (build phase P1, task T2b).

- digits fold by their Unicode decimal value, every format character (category Cf) folds away or to a space, both in
  code, for every script;
- letter folds are the ``fold.letters`` of the locale packs, read from every installed pack: by ``probe`` and the stop
  gate in the hook, and by pack checks through the request (``locale.fold``), which the replay of the gate gets back
  byte for byte.

Names that the task adds (the function of ``localize`` that takes the fold of a request) are not used here: the tests
go through ``checklib.main`` in a subprocess and through ``fold_name``. Non-ASCII characters are written as \\u
escapes or ``chr()``, so that a reader sees which character it is."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

import pytest

from harness_core import locales, oracle, probe, protected, run, stopgate
from harness_core import localize as L
from harness_core.ledger import Outputs
from harness_core.safety import Ctx
from test_budget import log_of
from test_locales import NINE, pack, put, set_profile
from test_p3_hooks import SID, ctx_of, h  # noqa: F401  (fixture h)
from test_run import job, run_req  # noqa: F401  (fixture job)

ROOT = Path(__file__).resolve().parents[1]
PY = "/usr/bin/python3"

YO, YE, EH = "ё", "е", "э"  # Cyrillic yo, ie, e: a pair of another script, and a third letter
YO_WORD = "объём"   # written with yo
YE_WORD = "объем"   # the same word written with ie
LEAD_YO = "чёрт"         # a question lead written with yo
LEAD_YE = "черт"         # ... with ie
STATE_YO = "блёрп"  # a state word written with yo
STATE_YE = "блерп"  # ... with ie

DECIMALS = [chr(i) for i in range(0x20000) if unicodedata.decimal(chr(i), None) is not None]
DECIMAL_VALUES = "".join(str(unicodedata.decimal(c)) for c in DECIMALS)
CF = [chr(i) for i in range(0x110000) if unicodedata.category(chr(i)) == "Cf"]

BLOCKS = [("arabic-indic", "٧"), ("extended-arabic-indic", "۷"), ("devanagari", "७"),
          ("fullwidth", "７"), ("mathematical-bold", chr(0x1D7D5))]  # each is the digit seven
FORMATS = [("zwnj", "‌"), ("zwj", "‍"), ("lrm", "‎"), ("soft-hyphen", "­"), ("bom", "﻿"),
           ("zwsp", "​"), ("word-joiner", "⁠"), ("alm", "؜"), ("lre", "‪")]


def live(h, pid, **extra):
    put(h, f"{pid}.json", pack(pid, **extra))
    return ctx_of(h)


def spy_views(monkeypatch):
    calls, real = [], locales.view
    monkeypatch.setattr(locales, "view", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def shipped_copy(tmp_path, monkeypatch, fold=True):
    """The shipped control/defaults copied; ``protected.DEFAULTS_DIR`` points at it; fa.json without ``fold`` unless
    ``fold``."""
    d = tmp_path / "defaults"
    shutil.copytree(protected.DEFAULTS_DIR, d)
    f = d / "locales" / "fa.json"
    fa = json.loads(f.read_text("utf-8"))
    if not fold:
        fa.pop("fold", None)
    f.write_text(json.dumps(fa, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(protected, "DEFAULTS_DIR", d)


def other_root(tmp_path, **extra):
    """A second data folder in the same process, with one live pack ``xx``."""
    d = tmp_path / "second"
    (d / "control" / "locales").mkdir(parents=True)
    (d / "control" / "locales" / "xx.json").write_text(json.dumps(pack("xx", **extra)), "utf-8")
    return Ctx(workspace=tmp_path, data_root=d)


# --- 4. digits fold by Unicode property, in code -------------------------------------------------------------------------


@pytest.mark.parametrize("name,d", BLOCKS, ids=[n for n, _ in BLOCKS])
def test_digits_of_every_block_fold_in_localize_digits(name, d):
    assert L.digits(d) == "7" and L.digits("a" + d + d + "b") == "a77b"


@pytest.mark.parametrize("name,d", BLOCKS, ids=[n for n, _ in BLOCKS])
def test_digits_of_every_block_fold_in_fold_name(name, d):
    assert L.fold_name(d) == "7" and L.fold_name("Total " + d + d) == "total 77"


@pytest.mark.parametrize("name,d", BLOCKS, ids=[n for n, _ in BLOCKS])
def test_digits_of_every_block_fold_in_the_probe(name, d):
    assert probe.run("pay " + d + d + " now", None)["number_tokens"] == ["77"]


def test_every_decimal_character_folds_to_its_ascii_digit():
    assert len(DECIMALS) > 600  # 650 at Unicode 13: the test would pass for a short table otherwise
    assert L.digits("".join(DECIMALS)) == DECIMAL_VALUES
    assert L.fold_name("".join(DECIMALS)) == DECIMAL_VALUES
    assert probe.run("n " + "".join(DECIMALS) + " n", None)["number_tokens"] == [DECIMAL_VALUES]


def test_the_arabic_decimal_separator_is_a_point_in_digits_and_is_left_alone_by_the_probe():
    assert L.digits("۱۲٫۵") == "12.5"
    assert probe.run("x ۱۲٫۵ y", None)["number_tokens"] == ["12", "5"]  # as pinned at 406bffd


def test_a_character_that_has_no_decimal_value_is_not_a_digit():
    for c in ("²", "①", "½", "٬", "٪"):  # superscript, circled, fraction, group, percent
        assert unicodedata.decimal(c, None) is None
        assert L.digits(c) == c


# --- 5. format characters ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name,ch", FORMATS, ids=[n for n, _ in FORMATS])
def test_the_probe_removes_a_format_character_inside_a_word(name, ch):
    assert probe.run("de" + ch + "lete it", None)["irreversible_classes"] == ["destructive"]


@pytest.mark.parametrize("name,ch", FORMATS, ids=[n for n, _ in FORMATS])
def test_fold_name_turns_a_format_character_into_a_space(name, ch):
    assert unicodedata.category(ch) == "Cf"
    assert L.fold_name("a" + ch + "b") == "a b"


def test_every_format_character_folds_the_same_way():
    assert len(CF) > 100  # 161 at Unicode 13
    assert probe.run("de" + "".join(CF) + "lete it", None)["irreversible_classes"] == ["destructive"]
    assert L.fold_name("a" + "".join(CF) + "b") == "a b"
    assert L.fold_name(" " + "".join(CF) + " ") == ""


def test_fold_name_still_drops_marks_collapses_white_space_and_casefolds():
    assert L.fold_name(" Aَ‌  ßB\t\n") == "a ssb"


# recorded from stopgate.lexicon_hits(None, text) at 406bffd: the stop gate's fold removes every Cf character without a
# space, folds digits and kaf/yeh, casefolds. The hits are the folded text; no text here holds a letter that only
# localize folded at 406bffd.
GATE_PINNED = [
    ("The figures are ver­ified.", ["verified"]),
    ("all ver​ified", ["verified"]),
    ("﻿checked", ["checked"]),
    ("che‍cked and done", ["checked"]),
    ("CHECKED", ["checked"]),
    ("conf⁠irmed", ["confirmed"]),
    ("‪checked‬", ["checked"]),
    ("veri‎fied", ["verified"]),
    ("che‏cked", ["checked"]),
    ("بررسی‏شده", ["بررسیشده"]),
    ("بررسی​شده", ["بررسیشده"]),
    ("بررسي شد", ["بررسی شد"]),
    ("بررسی­شد", []),
    ("ch⁣ecked\nplain\nver؜ified", ["checked", "verified"]),
    ("che cked", []),
    ("تطبيق داده شد", ["تطبیق داده شد"]),
    ("صحت‌سنجی شد", ["صحتسنجی شد"]),
]


@pytest.mark.parametrize("text,hits", GATE_PINNED, ids=[f"g{i:02d}" for i in range(len(GATE_PINNED))])
def test_the_stop_gate_fold_gives_the_results_of_406bffd(text, hits):
    assert stopgate.lexicon_hits(None, text) == hits


def test_the_stop_gate_pinned_inputs_are_enough():
    assert len(GATE_PINNED) >= 10 and any(hits for _, hits in GATE_PINNED) and any(not hits for _, hits in GATE_PINNED)


# --- 6. letters are data, hook side -----------------------------------------------------------------------------------


@pytest.mark.parametrize("src,dst", sorted(NINE.items()), ids=[f"u{ord(k):04x}" for k in sorted(NINE)])
def test_the_shipped_pairs_reach_the_probe_though_the_profile_names_another_pack(h, src, dst):
    word = "ز" + dst + "ز"                 # a word of a live pack, written with the target letter
    live(h, "xx", words={"money_words": [word]})
    set_profile(h, locales=["xx"])                   # fa is installed, not active: fold applies for every pack
    c = ctx_of(h)
    assert probe.run("the " + word, c)["money_tokens"] == [word]
    assert probe.run("the ز" + src + "ز", c)["money_tokens"] == [word]


def test_a_verb_written_with_a_letter_form_only_localize_folded_is_the_same_request():
    plain = probe.run("پاک کن", None)           # pak kon, with alef
    for src in ("أ", "آ", "إ"):                           # alef with hamza above, madda, hamza below
        got = probe.run("پ" + src + "ک کن", None)
        assert got["irreversible_classes"] == ["destructive"] and got == dict(plain, request_sha=got["request_sha"])
    assert plain["irreversible_classes"] == ["destructive"]


def test_a_live_pair_of_another_script_matches_a_word_written_with_either_letter(h):
    c = live(h, "xx", words={"money_words": [YO_WORD]})
    assert probe.run("the " + YO_WORD, c)["money_tokens"] == [YO_WORD]
    assert probe.run("the " + YE_WORD, c)["money_tokens"] == []              # without the pair: the exact spelling
    c = live(h, "xx", words={"money_words": [YO_WORD]}, fold={"letters": {YO: YE}})
    assert probe.run("the " + YO_WORD, c)["money_tokens"] == [YO_WORD]
    assert probe.run("the " + YE_WORD, c)["money_tokens"] == [YO_WORD]       # no restart, no cache
    live(h, "xx", words={"money_words": [YE_WORD]}, fold={"letters": {YO: YE}})  # the list holds the other letter
    assert probe.run("the " + YO_WORD, ctx_of(h))["money_tokens"] == [YE_WORD]
    live(h, "xx", words={"money_words": [YO_WORD]})                          # the pair is deleted again
    assert probe.run("the " + YE_WORD, ctx_of(h))["money_tokens"] == []


def test_a_live_pair_reaches_a_question_lead_and_a_question_mark(h):
    live(h, "xx", words={"question_lead": [LEAD_YO]}, question_marks=[YO])
    c = ctx_of(h)
    assert probe.run(LEAD_YO + " sky blue", c)["question_only"] is True
    assert probe.run(LEAD_YE + " sky blue", c)["question_only"] is False
    assert probe.run("sky blue" + YE, c)["question_only"] is False
    live(h, "xx", words={"question_lead": [LEAD_YO]}, question_marks=[YO], fold={"letters": {YO: YE}})
    c = ctx_of(h)
    assert probe.run(LEAD_YE + " sky blue", c)["question_only"] is True
    assert probe.run("sky blue" + YE, c)["question_only"] is True


def test_a_live_pair_reaches_the_state_word_check(h):
    c = live(h, "xx", state_lexicon={"terms": [STATE_YO]})
    assert len(stopgate.lexicon_hits(c, "it was " + STATE_YO)) == 1
    assert stopgate.lexicon_hits(c, "it was " + STATE_YE) == []
    c = live(h, "xx", state_lexicon={"terms": [STATE_YO]}, fold={"letters": {YO: YE}})
    assert len(stopgate.lexicon_hits(c, "it was " + STATE_YE)) == 1
    assert len(stopgate.lexicon_hits(c, "it was " + STATE_YO)) == 1
    live(h, "xx", state_lexicon={"terms": [STATE_YE]}, fold={"letters": {YO: YE}})
    assert len(stopgate.lexicon_hits(ctx_of(h), "it was " + STATE_YO)) == 1


@pytest.mark.parametrize("src", sorted(NINE), ids=[f"u{ord(k):04x}" for k in sorted(NINE)])
def test_without_fold_in_fa_json_the_letter_is_not_folded_by_the_probe_nor_the_stop_gate(tmp_path, monkeypatch, src):
    shipped_copy(tmp_path, monkeypatch, fold=False)
    f = tmp_path / "defaults" / "locales" / "fa.json"
    fa = json.loads(f.read_text("utf-8"))
    word = "ز" + NINE[src] + "ز"
    fa["words"]["money_words"].append(word)
    fa["state_lexicon"]["terms"].append(word)
    f.write_text(json.dumps(fa, ensure_ascii=False), "utf-8")
    assert probe.run("the " + word, None)["money_tokens"] == [word]
    assert probe.run("the ز" + src + "ز", None)["money_tokens"] == []
    assert stopgate.lexicon_hits(None, "it was ز" + src + "ز") == []
    assert len(stopgate.lexicon_hits(None, "it was " + word)) == 1


def test_a_pair_that_the_copy_holds_is_the_pair_the_probe_uses(tmp_path, monkeypatch):
    shipped_copy(tmp_path, monkeypatch, fold=False)
    f = tmp_path / "defaults" / "locales" / "fa.json"
    fa = json.loads(f.read_text("utf-8"))
    fa["fold"] = {"letters": {YO: YE}}
    fa["words"]["money_words"].append(YO_WORD)
    f.write_text(json.dumps(fa, ensure_ascii=False), "utf-8")
    assert probe.run("the " + YE_WORD, None)["money_tokens"] == [YO_WORD]
    assert probe.run("the كالا", None)["money_tokens"] == []  # the pairs of fa.json are gone


def test_a_pair_across_two_packs_is_applied_once_and_not_resolved(h):
    put(h, "xx.json", pack("xx", fold={"letters": {YO: YE}}, words={"money_words": ["объэм"]}))
    put(h, "yy.json", pack("yy", fold={"letters": {YE: EH}}))
    c = ctx_of(h)
    assert locales.view(c, every=True)["fold"]["letters"][YO] == YE
    assert probe.run("the " + YE_WORD, c)["money_tokens"] == ["объэм"]   # ie -> e
    assert probe.run("the " + YO_WORD, c)["money_tokens"] == []                                    # yo -> ie, and stops


@pytest.mark.parametrize("order", ["ab", "ba", "aba", "bab"])
def test_two_contexts_in_one_process_each_use_their_own_packs(h, tmp_path, order):
    a = live(h, "xx", words={"money_words": [YO_WORD]}, state_lexicon={"terms": [STATE_YO]},
             fold={"letters": {YO: YE}})
    b = other_root(tmp_path, words={"money_words": [YO_WORD]}, state_lexicon={"terms": [STATE_YO]})
    ctx = {"a": a, "b": b}
    for which in order:
        want = which == "a"
        assert probe.run("the " + YE_WORD, ctx[which])["money_tokens"] == ([YO_WORD] if want else []), order
        assert len(stopgate.lexicon_hits(ctx[which], "it was " + STATE_YE)) == (1 if want else 0), order
    assert probe.run("the " + YE_WORD, None)["money_tokens"] == []  # and no context at all has no live pair


# --- 7. letters are data, checks ----------------------------------------------------------------------------------------

CHECK = '''
import json, sys
sys.path.insert(0, %r)
from harness_core import checklib, locales, localize
calls = []
real = locales.view
locales.view = lambda *a, **k: calls.append(1) or real(*a, **k)

def fn(req):
    out = [localize.fold_name(t) for t in req.get("texts", [])]
    for _ in range(int(req.get("repeat", 0))):
        localize.fold_name("x")
    return "pass", {"out": out, "views": len(calls)}, []

checklib.main(fn)
''' % str(ROOT / "lib")


def check_values(tmp_path, request, env=None):
    """Run a check that folds ``request['texts']`` the way a pack check does: ``/usr/bin/python3 -I``, stdin is the
    request, a clean environment."""
    script = tmp_path / "check_fold.py"
    script.write_text(CHECK, "utf-8")
    p = subprocess.run([PY, "-I", str(script)], input=json.dumps(request).encode("utf-8"), env=env or {},
                       cwd=str(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["values"]


def test_the_request_of_a_check_holds_the_fold_of_every_installed_pack(h, job):
    live(h, "xx", fold={"letters": {YO: YE}})
    set_profile(h, locales=["en"])  # xx and fa are not named: the fold is the one of every pack
    sent = []

    def runner(argv, cwd, stdin, **kw):
        sent.append(json.loads(stdin.decode("utf-8")))
        return 0, b'{"result": "pass", "values": {}, "notes": []}\n', b""
    req = {"tool": "run", "check_id": "finance.reconcile", "unit": "u1", "attempt": 1, "inputs": ["sales.csv"],
           "args": {"period": {"month": "1402-12"}}}
    assert run.apply(req, ctx_of(h), {"session_id": SID}, runner=runner)["result"] == "pass"
    want = locales.view(ctx_of(h), every=True)["fold"]
    assert want == {"letters": dict(NINE, **{YO: YE})}
    assert sent[0]["locale"] == {"fold": want}
    assert sent[0]["business"] and sent[0]["check_id"] == "finance.reconcile"  # the other keys are still there


def test_the_request_holds_an_empty_fold_when_no_pack_has_one(h, job, tmp_path, monkeypatch):
    shipped_copy(tmp_path, monkeypatch, fold=False)
    sent = []

    def runner(argv, cwd, stdin, **kw):
        sent.append(json.loads(stdin.decode("utf-8")))
        return 0, b'{"result": "pass", "values": {}, "notes": []}\n', b""
    req = {"tool": "run", "check_id": "finance.reconcile", "unit": "u1", "attempt": 1, "inputs": ["sales.csv"]}
    run.apply(req, ctx_of(h), {"session_id": SID}, runner=runner)
    assert sent[0]["locale"] == {"fold": {}}


def test_checklib_main_hands_the_fold_of_the_request_to_fold_name(tmp_path):
    texts = [YO, "ك", "ي", "A" + YO]
    got = check_values(tmp_path, {"texts": texts, "locale": {"fold": {"letters": {YO: YE}}}})["out"]
    # the table of the request is the table: the pair of the request, and not the nine pairs of the shipped packs
    assert got == [YE, "ك", "ي", "a" + YE]
    got = check_values(tmp_path, {"texts": texts, "locale": {"fold": {"letters": dict(NINE, **{YO: YE})}}})["out"]
    assert got == [YE, "ک", "ی", "a" + YE]


def test_a_request_with_an_empty_letters_table_folds_no_letter(tmp_path):
    texts = [YO, "ك", "ي", "أ"]
    assert check_values(tmp_path, {"texts": texts, "locale": {"fold": {"letters": {}}}})["out"] == texts
    assert check_values(tmp_path, {"texts": texts, "locale": {"fold": {}}})["out"] == texts


def test_a_request_with_a_fold_still_folds_digits_and_format_characters(tmp_path):
    texts = ["۱۲" + "‌" + "３", chr(0x1D7D5) + "­" + "٧"]
    got = check_values(tmp_path, {"texts": texts, "locale": {"fold": {"letters": {}}}})["out"]
    assert got == ["12 3", "7 7"]


def test_a_request_without_locale_uses_the_nine_pairs_of_the_shipped_packs(tmp_path):
    sources, targets = sorted(NINE), [NINE[k] for k in sorted(NINE)]
    assert check_values(tmp_path, {"texts": sources})["out"] == targets
    assert L.fold_name("".join(sources)) == "".join(targets)  # and so does a direct call in this process


def test_that_fallback_works_under_python_I_with_an_empty_environment(tmp_path):
    sources, targets = sorted(NINE), [NINE[k] for k in sorted(NINE)]
    got = check_values(tmp_path, {"texts": sources + [YO]}, env={})["out"]
    assert got == targets + [YO]  # a live pair is not a shipped pair: the sandbox cannot read the data root


def test_a_fold_name_that_got_a_fold_reads_no_pack_and_the_fallback_reads_once(tmp_path):
    with_fold = check_values(tmp_path, {"texts": ["a", YO], "repeat": 50, "locale": {"fold": {"letters": {YO: YE}}}})
    assert with_fold["views"] == 0 and with_fold["out"] == ["a", YE]
    without = check_values(tmp_path, {"texts": ["a", YO] * 5, "repeat": 50})
    assert without["views"] == 1
    assert check_values(tmp_path, {"texts": [], "repeat": 0})["views"] in (0, 1)  # no call, no (or one) read


def test_the_frozen_channel_the_replay_gets_the_fold_the_first_run_had(h, job, tmp_path):
    (h["ws"] / "objem.csv").write_text("date,description,объём\n1402/12/01,invoice 1,1000\n"
                                       "1402/12/05,invoice 2,2500\n1402/12/29,invoice 3,500\n", encoding="utf-8")
    col = YE_WORD  # the column of the file is written with yo, the question names it with ie
    live(h, "xx", fold={"letters": {YO: YE}})
    first = run_req(h, inputs=("objem.csv",), amount_col=col)
    assert first["result"] == "pass" and first["values"]["total"] == 4000 and first["values"]["rows_used"] == 3
    (h["data"] / "control" / "locales" / "xx.json").unlink()   # the live pack is gone: the data root changed
    p, log = log_of(h, job)
    row = Outputs(p, log).rows()[-1]
    assert oracle.replay(p.run, row, time.time() + 240) is None  # the same result and the same values
    gone = run_req(h, attempt=2, inputs=("objem.csv",), amount_col=col)
    assert gone["result"] == "error"   # control: without the pair the same question is not answered at all


# --- 8. calls ------------------------------------------------------------------------------------------------------------


def test_the_hook_side_calls_make_no_more_view_calls_than_at_406bffd(h, monkeypatch):
    live(h, "xx", fold={"letters": {YO: YE}}, question_marks=[YO])
    calls = spy_views(monkeypatch)
    c = ctx_of(h)
    for name, call in (("probe.run", lambda: probe.run("please send the invoice to Sara?", c)),
                       ("probe.words", lambda: probe.words(c)),
                       ("lexicon_hits", lambda: stopgate.lexicon_hits(c, "all checked\nverified")),
                       ("asks", lambda: stopgate.asks(c, "done?"))):
        del calls[:]
        call()
        assert len(calls) == 1, name


def test_a_second_call_reads_the_packs_again_so_a_pair_written_between_two_calls_is_seen(h):
    live(h, "xx", words={"money_words": [YO_WORD]})
    assert probe.run("the " + YE_WORD, ctx_of(h))["money_tokens"] == []
    live(h, "xx", words={"money_words": [YO_WORD]}, fold={"letters": {YO: YE}})
    assert probe.run("the " + YE_WORD, ctx_of(h))["money_tokens"] == [YO_WORD]

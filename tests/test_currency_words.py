"""Currency words: one table in the locale packs, one matcher in the code (build phase P1, task T4b).

``words.currency_words.<CODE>`` lists the words and signs that name one currency; ``localize.word_pattern`` decides
what a match is; ``localize.currency_of`` / ``names_currency``, ``claims.claim_type``, ``probe.run`` and the stop gate
all use it. The mechanism is proved with data that no shipped file holds: the live pack ``t-words``, the codes ``QDA``
and ``QDC`` and the words ``zorb``, the yen sign and a Cyrillic abbreviation. The shipped words appear only where a test
pins 0.4.0 behaviour or the shipped data itself.

New names (``word_pattern``, ``use_currency_words``, ``names_currency``, the ``words=`` parameters) are reached inside
the test bodies, so that a missing name fails one test and never the collection. A test that changes the process table
of ``localize`` uses the ``fresh_table`` fixture, which puts every module attribute back."""
from __future__ import annotations

import json
import re
import string
import subprocess
import time
import unicodedata
from pathlib import Path

import pytest

from harness_core import (business, claims, lanes, locales, model_cmd, oracle, probe, sessionstate, store, stopgate, subagent,
                          templates)
from harness_core import localize as L
from harness_core.errors import Denied, LocaleError
from harness_core.events import EventLog
from harness_core.ledger import Outputs
from harness_core.paths import job_paths
from test_budget import log_of
from test_currencies import PY, ROOT, amounts, check, csv_of, pair, sent_requests, spy_views, without_ir
from test_locales import pack, put, set_profile
from test_model import sim  # noqa: F401  (fixture sim: a compiled simulation with assumptions)
from test_p3_hooks import SID, _fetched, compiled, ctx_of, h, say  # noqa: F401  (fixture h)
from test_probe_words import FIX, write_probe
from test_run import job, run_req  # noqa: F401  (fixture job: sales.csv in the workspace, a compiled task, unit u1)

YEN = "\u5186"                 # a CJK character that is a letter (Lo)
HRN = "\u0433\u0440\u043d"     # a Cyrillic abbreviation of three letters
FA_IRT, FA_IRR, FA_EUR = "\u062a\u0648\u0645\u0627\u0646", "\u0631\u06cc\u0627\u0644", "\u06cc\u0648\u0631\u0648"
FA_IRT_ADJ, FA_IRR_ADJ = FA_IRT + "\u06cc", FA_IRR + "\u06cc"          # the two adjective forms
FA_USD, RIAL_SIGN = "\u062f\u0644\u0627\u0631", "\ufdfc"
ARABIC_YEH, PERSIAN_YEH = "\u064a", "\u06cc"

WORDS = {"QDA": ["zorb", YEN], "QDC": [HRN]}
PAIR = pair("QDA", "QDC", 100)
T_WORDS = pack("t-words", words={"currency_words": WORDS}, currency_pairs=[PAIR])
OLD = json.loads((FIX / "probe_0_4_0.json").read_text("utf-8"))


@pytest.fixture
def fresh_table():
    """The process table of ``localize`` is "not read yet" in the test; every module attribute is put back after it."""
    before = dict(vars(L))
    if hasattr(L, "use_currency_words"):   # before the task there is no table to reset
        L.use_currency_words({})   # shows which attribute holds the table
        for k in [k for k, v in vars(L).items() if k not in before or v is not before[k]]:
            setattr(L, k, None)
    yield
    for k in [k for k in vars(L) if k not in before]:
        delattr(L, k)
    for k, v in before.items():
        setattr(L, k, v)


def live(h, *packs):
    for p in packs:
        put(h, p["id"] + ".json", p)
    return ctx_of(h)


def cw_of(c):
    return locales.view(c, every=True)["words"]["currency_words"]


# --- 1. the shipped data is exactly R2 ------------------------------------------------------------------------------

EN_0_4_0 = ["$", "\u20ac", "\u00a3", "usd", "eur", "dollar", "dollars", "euro", "euros", "price", "prices", "cost",
            "costs", "revenue", "profit", "invoice", "invoices", "payment", "payments", "salary", "budget", "discount",
            "debt", "owe", "owed"]
FA_0_4_0 = [FA_USD, FA_EUR, "\u0642\u06cc\u0645\u062a", "\u0647\u0632\u06cc\u0646\u0647", "\u062f\u0631\u0622\u0645\u062f",
            "\u0633\u0648\u062f", "\u0641\u0627\u06a9\u062a\u0648\u0631", "\u067e\u0631\u062f\u0627\u062e\u062a",
            "\u062d\u0642\u0648\u0642", "\u0628\u0648\u062f\u062c\u0647", "\u062a\u062e\u0641\u06cc\u0641",
            "\u0628\u062f\u0647\u06cc", "\u0637\u0644\u0628", "\u0628\u062f\u0647\u06a9\u0627\u0631"]
IR_0_4_0 = [RIAL_SIGN, "toman", "tomans", "rial", "rials", "irr", "irt", FA_IRT, FA_IRR]
CURRENCY_WORDS = {
    "en": {"USD": ["usd"], "EUR": ["eur", "euro", "euros", "\u20ac"]},
    "fa": {"EUR": [FA_EUR]},
    "ir": {"IRT": ["toman", "tomans", "irt", FA_IRT, FA_IRT_ADJ], "IRR": ["rial", "rials", "irr", FA_IRR, FA_IRR_ADJ]},
}
MOVED = {w for t in CURRENCY_WORDS.values() for ws in t.values() for w in ws}
MONEY_WORDS = {"en": [w for w in EN_0_4_0 if w not in MOVED], "fa": [w for w in FA_0_4_0 if w not in MOVED],
               "ir": [w for w in IR_0_4_0 if w not in MOVED]}


def shipped_words(pid):
    return locales.installed()[pid]["words"]


def test_the_literal_0_4_0_lists_of_this_file_are_the_0_4_0_words():
    assert sorted(EN_0_4_0 + FA_0_4_0 + IR_0_4_0) == sorted(OLD["money_words"]) and len(OLD["money_words"]) == 48


@pytest.mark.parametrize("pid", ["en", "fa", "ir"])
def test_a_shipped_pack_holds_exactly_the_currency_words_of_the_brief(pid):
    assert shipped_words(pid)["currency_words"] == CURRENCY_WORDS[pid]


@pytest.mark.parametrize("pid", ["en", "fa", "ir"])
def test_a_shipped_money_words_list_is_the_0_4_0_list_minus_the_moved_words_in_the_same_order(pid):
    assert shipped_words(pid)["money_words"] == MONEY_WORDS[pid]


def test_every_0_4_0_word_is_in_exactly_one_place_and_the_two_adjective_forms_are_new():
    places = [w for pid in ("en", "fa", "ir") for w in shipped_words(pid)["money_words"]]
    places += [w for pid in ("en", "fa", "ir") for ws in shipped_words(pid)["currency_words"].values() for w in ws]
    assert sorted(places) == sorted(OLD["money_words"] + [FA_IRT_ADJ, FA_IRR_ADJ]) and len(places) == 50
    assert len(set(places)) == 50   # no word twice: moved, not copied


def test_the_other_shipped_codes_get_no_word_and_no_pack_lists_them_as_a_key():
    keys = {c for pid in ("en", "fa", "ir") for c in shipped_words(pid)["currency_words"]}
    words = {w.casefold() for pid in ("en", "fa", "ir") for ws in shipped_words(pid)["currency_words"].values() for w in ws}
    assert keys == {"USD", "EUR", "IRT", "IRR"} and not {"gbp", "aed", "try"} & words


# --- 2. pack load ---------------------------------------------------------------------------------------------------


def test_a_live_pack_with_currency_words_loads_keeps_them_and_needs_no_currencies_entry(h):
    put(h, "t-words.json", T_WORDS)
    got = locales.installed(ctx_of(h))["t-words"]
    assert got["words"]["currency_words"] == WORDS and "currencies" not in got   # QDA has no entry under currencies


def test_the_currency_words_of_live_packs_merge_with_the_shipped_lists_by_the_list_rule(h):
    c = live(h, pack("t-words", words={"currency_words": {"QDA": ["zorb", YEN], "EUR": ["zorbeur"]}}),
             pack("t-more", words={"currency_words": {"QDA": [YEN, "qdw"]}}),
             pack("en", words={"currency_words": {"USD": ["bucks"]}}))   # a live file of a shipped id adds to it
    got = cw_of(c)
    assert got["EUR"] == CURRENCY_WORDS["en"]["EUR"] + [FA_EUR, "zorbeur"]   # en, fa, then the live pack
    assert got["USD"] == ["usd", "bucks"] and sorted(got["QDA"]) == sorted([YEN, "qdw", "zorb"])   # no word twice
    assert got["IRT"] == CURRENCY_WORDS["ir"]["IRT"]


GOOD_WORDS = {"QDA": ["zorb", YEN]}
# words in which every character is a control, format, separator or mark: nothing to see, nothing to match
INVISIBLE = [("control", "\u0001"), ("soft-hyphen", "\u00ad"), ("space", " "), ("tab", "\t"),
             ("no-break-space", "\u00a0"), ("zero-width-space", "\u200b"), ("zwnj-and-zwj", "\u200c\u200d"), ("combining-acute", "\u0301"), ("space-and-zero-width-space", " \u200b")]


def unassigned():
    """A code point that the running interpreter reports as Cn: the first one in the Currency Symbols block, else the
    first one after it (a newer Unicode knows more signs; the hooks run on Unicode 13)."""
    cp = next(c for c in list(range(0x20A0, 0x20D0)) + list(range(0x20D0, 0x110000))
              if unicodedata.category(chr(c)) == "Cn")
    assert unicodedata.category(chr(cp)) == "Cn"
    return chr(cp)


# a character that no table of this interpreter knows, and a private-use one, are visible: a sign added to Unicode
# later is Cn on an older Python, and a pack for that region must load
SIGNS_NOT_IN_TABLES = [("unassigned", unassigned()), ("private-use", "\ue000")]
# words that have a visible character, however much invisible one around it
VISIBLE = [("inner-space", "zorb fund"), ("zwnj-inside", "zo\u200crb"), ("one-sign", YEN), ("padded", " zorb "),
           ("zwsp-around", "\u200bzorb\u200b")]
BAD_WORDS = [
    ("code-lowercase", {"qda": ["zorb"]}),
    ("code-two-letters", {"QD": ["zorb"]}),
    ("code-four-letters", {"QDAA": ["zorb"]}),
    ("code-with-a-digit", {"QD1": ["zorb"]}),
    ("empty-word", {"QDA": ["zorb", ""]}),
    ("only-an-empty-word", {"QDA": [""]}),
    ("value-a-string", {"QDA": "zorb"}),
    ("value-an-object", {"QDA": {"w": "zorb"}}),
    ("value-a-number", {"QDA": 5}),
    ("value-null", {"QDA": None}),
    ("entry-a-number", {"QDA": ["zorb", 5]}),
    ("entry-null", {"QDA": [None]}),
    ("entry-a-list", {"QDA": [["zorb"]]}),
    ("the-table-a-list", ["zorb"]),
    ("the-table-a-string", "zorb"),
] + [(f"no-visible-character-{n}", {"QDA": ["zorb", w]}) for n, w in INVISIBLE] + \
    [(f"only-no-visible-character-{n}", {"QDA": [w]}) for n, w in INVISIBLE[:2]]


@pytest.mark.parametrize("case", BAD_WORDS, ids=[c[0] for c in BAD_WORDS])
def test_a_bad_currency_words_key_is_refused_with_the_file_and_the_key_path(h, case):
    _, bad = case
    put(h, "xx.json", pack("xx", words={"currency_words": GOOD_WORDS}))
    assert locales.installed(ctx_of(h))["xx"]["words"]["currency_words"] == GOOD_WORDS   # control: it loads
    put(h, "xx.json", pack("xx", words={"currency_words": bad}))
    ctx = ctx_of(h)
    with pytest.raises(LocaleError) as e:
        locales.installed(ctx)
    f = Path(ctx.data_root) / "control" / "locales" / "xx.json"
    assert str(e.value).startswith(f"locale pack {f}: ") and "words.currency_words" in str(e.value)


@pytest.mark.parametrize("word", [w for _, w in VISIBLE], ids=[n for n, _ in VISIBLE])
def test_a_word_with_a_visible_character_is_accepted_at_pack_load(h, word):
    put(h, "xx.json", pack("xx", words={"currency_words": {"QDA": [word, "zorb"]}}))
    assert locales.installed(ctx_of(h))["xx"]["words"]["currency_words"] == {"QDA": [word, "zorb"]}


def test_the_signs_of_the_tests_are_of_the_categories_they_are_named_for():
    assert [unicodedata.category(w) for _, w in SIGNS_NOT_IN_TABLES] == ["Cn", "Co"]
    assert [unicodedata.category(c) for c in INVISIBLE[0][1] + INVISIBLE[1][1]] == ["Cc", "Cf"]


@pytest.mark.parametrize("sign", [w for _, w in SIGNS_NOT_IN_TABLES], ids=[n for n, _ in SIGNS_NOT_IN_TABLES])
def test_a_word_of_a_character_that_no_unicode_table_knows_is_accepted_at_pack_load(h, sign):
    put(h, "xx.json", pack("xx", words={"currency_words": {"QDA": [sign]}}))
    assert locales.installed(ctx_of(h))["xx"]["words"]["currency_words"] == {"QDA": [sign]}


@pytest.mark.parametrize("sign", [w for _, w in SIGNS_NOT_IN_TABLES], ids=[n for n, _ in SIGNS_NOT_IN_TABLES])
def test_a_word_of_a_character_that_no_unicode_table_knows_is_accepted_in_a_request(tmp_path, sign):
    marker = tmp_path / "ran"
    got = values(tmp_path, {"labels": ["amount (" + sign + ")", "amount"], "marker": str(marker),
                            "locale": {"fold": {}, "currency_words": {"QDA": [sign]}}})
    assert marker.exists() and got["codes"] == ["QDA", None]


@pytest.mark.parametrize("sign", [w for _, w in SIGNS_NOT_IN_TABLES], ids=[n for n, _ in SIGNS_NOT_IN_TABLES])
def test_a_sign_that_no_unicode_table_knows_is_a_currency_word_for_the_matcher(sign):
    table = {"QDA": [sign], "QDC": ["zorb"]}
    assert [L.currency_of(x, words=table) for x in ("amount (" + sign + ")", "5000" + sign, sign + "5000", "amount",
                                                     "amount (zorb)")] == ["QDA", "QDA", "QDA", None, "QDC"]
    assert [L.names_currency(x, table) for x in ("paid " + sign + "500", "paid 500", "")] == [True, False, False]


@pytest.mark.parametrize("sign", [w for _, w in SIGNS_NOT_IN_TABLES], ids=[n for n, _ in SIGNS_NOT_IN_TABLES])
def test_a_sign_that_no_unicode_table_knows_is_money_for_the_probe_of_a_live_pack(h, sign):
    c = live(h, pack("t-words", words={"currency_words": {"QDA": [sign]}}))
    assert money("it came to 3000" + sign, c) == [sign] and money("it came to " + sign + "3000", c) == [sign]
    assert money("it came to 3000", c) == [] and money("please summarise the 5 meeting notes", c) == []


@pytest.mark.parametrize("junk", [w for _, w in INVISIBLE], ids=[n for n, _ in INVISIBLE])
def test_a_pack_with_a_word_of_no_visible_character_gives_the_probe_and_the_stop_gate_no_money_token(h, junk):
    put(h, "xx.json", pack("xx", words={"currency_words": {"QDA": ["zorb", junk]}}))
    c = ctx_of(h)
    for call in (lambda: probe.run("please summarise the 5 meeting notes", c), lambda: probe.words(c)):
        with pytest.raises(LocaleError) as e:     # the pack does not load: the path stops, as for any broken pack
            call()
        assert "xx.json" in str(e.value) and "words.currency_words" in str(e.value)
    say(h, "What is the capital of France?", "q1")   # the hook fails closed: a card is pending
    assert sessionstate.load(ctx_of(h), SID).get("pending_card")
    out = stopgate.on_stop({"session_id": SID, "last_assistant_message": "The 5 meeting notes."}, ctx_of(h))
    assert out["decision"] == "block" and "names money" not in out["reason"]


# --- 3. word_pattern ------------------------------------------------------------------------------------------------

BOUNDARY = [
    # (word, text, matches): a digit or an underscore may touch the word, a letter of any script may not
    ("zorb", "zorb", True), ("zorb", "a zorb b", True), ("zorb", "(zorb)", True), ("zorb", "5000zorb", True),
    ("zorb", "zorb5000", True), ("zorb", "5zorb7", True), ("zorb", "amount_zorb", True), ("zorb", "zorb_total", True),
    ("zorb", "_zorb_", True), (YEN, "3000" + YEN, True), (HRN, "50" + HRN, True), (HRN, HRN + "_x", True),
    ("zorb", "xzorb", False), ("zorb", "zorbx", False), ("zorb", "zorbs", False), ("zorb", "izorb", False),
    ("zorb", "\u00e9zorb", False), ("zorb", "zorb\u00e9", False),                      # an accented Latin letter
    ("zorb", "\u0627zorb", False), ("zorb", "zorb\u0627", False),                      # an Arabic letter
    (HRN, "\u0430" + HRN, False), (HRN, HRN + "\u0430", False),                        # a Cyrillic letter
    (YEN, "\u65e5" + YEN, False), (YEN, YEN + "\u65e5", False),                        # a CJK letter
    ("zorb", "5\u00e9zorb", False),                                                     # a letter after a digit
    # a word that starts with a sign is matched anywhere, also inside a longer token
    ("\u20ac", "10\u20ac", True), ("\u20ac", "abc\u20acdef", True), ("\u20ac", "x\u20ac", True),
    ("\u00a4kr", "abc\u00a4krx", True), ("\u00a4kr", "5\u00a4kr", True), ("\u00a4kr", "kr", False),
    # the word is a literal, never a pattern
    ("a.b", "a.b", True), ("a.b", "axb", False), ("a+b", "a+b", True), ("a+b", "aab", False),
    ("$z", "x$z", True), ("(z)", "x(z)y", True),
]


@pytest.mark.parametrize("word,text,matches", BOUNDARY, ids=[f"b{i:02d}" for i in range(len(BOUNDARY))])
def test_a_word_matches_where_no_letter_touches_it(word, text, matches):
    pat = L.word_pattern(word)
    assert isinstance(pat, str)
    assert bool(re.search(pat, text)) is matches, (pat, text)


# --- 4. currency_of and names_currency with a table -----------------------------------------------------------------


def codes(labels, words):
    return [L.currency_of(x, words=words) for x in labels]


def test_a_label_names_the_code_of_the_word_it_holds_and_none_names_no_code():
    assert codes(["Amount (zorb)", "total " + HRN, "3000" + YEN, "Amount", "amount (qux)"], WORDS) == \
        ["QDA", "QDC", "QDA", None, None]


@pytest.mark.parametrize("word,label", [("zorb", "AMOUNT (ZORB)"), ("Zorb", "amount (zorb)"), ("ZORB", "Amount (Zorb)"),
                                        ("zorb" + ARABIC_YEH, "amount (zorb" + PERSIAN_YEH + ")"),
                                        ("zorb" + PERSIAN_YEH, "amount (zorb" + ARABIC_YEH + ")"),
                                        ("zorb", "\u0663\u0660\u0660\u0660zorb"),            # Arabic-Indic digits
                                        ("zorb", "amount\u200czorb"), ("zorb", "amount\u200bzorb")])
def test_the_label_and_the_word_go_through_the_same_fold(word, label):
    assert L.currency_of(label, words={"QDA": [word]}) == "QDA"


def test_a_label_with_a_digit_or_an_underscore_against_the_word_names_the_code():
    assert codes(["5000zorb", "amount_zorb", "zorb_5000", "zorb5"], WORDS) == ["QDA"] * 4
    assert codes(["xzorb", "zorbx", "zorbs"], WORDS) == [None] * 3


AMBIGUOUS = {"QDA": ["zorb", "shared"], "QDC": [HRN, "Shared"]}   # "shared" is listed under two codes (after the fold)


def test_a_word_under_two_codes_names_no_code_but_another_word_of_the_label_still_does():
    assert codes(["shared", "Amount (shared)", "shared total"], AMBIGUOUS) == [None] * 3
    assert codes(["shared zorb", "zorb shared", "zorb"], AMBIGUOUS) == ["QDA"] * 3
    assert codes(["shared " + HRN, HRN + " shared"], AMBIGUOUS) == ["QDC"] * 2


def test_a_word_listed_twice_under_one_code_is_not_ambiguous():
    assert L.currency_of("zorb", words={"QDA": ["zorb", "ZORB"]}) == "QDA"


def test_two_codes_in_one_label_the_earliest_wins_in_both_orders():
    assert codes(["zorb " + HRN, "total zorb in " + HRN, HRN + " zorb", HRN + " then zorb 5000"], WORDS) == \
        ["QDA", "QDA", "QDC", "QDC"]
    assert codes(["zorb " + HRN], {"QDC": [HRN], "QDA": ["zorb"]}) == ["QDA"]   # the order of the table does not count


def test_two_words_at_the_same_position_the_longest_wins():
    shorter_first = {"QDA": ["zorb"], "QDC": ["zorb fund"]}
    longer_first = {"QDC": ["zorb fund"], "QDA": ["zorb"]}
    for table in (shorter_first, longer_first):
        assert L.currency_of("zorb fund", words=table) == "QDC"
        assert L.currency_of("zorb funds", words=table) == "QDA"          # "zorb fund" is not a word of this label
        assert L.currency_of("my zorb fund", words=table) == "QDC"
    assert L.currency_of("5\u20ac", words={"QDA": ["\u20ac"], "QDC": ["\u20acuro"]}) == "QDA"
    assert L.currency_of("5\u20acuro", words={"QDA": ["\u20ac"], "QDC": ["\u20acuro"]}) == "QDC"


@pytest.mark.parametrize("label,words", [(None, WORDS), ("", WORDS), ("   ", WORDS), ("zorb", {}), ("zorb", {"QDA": []}),
                                         ("", {}), (None, {})])
def test_none_the_empty_label_and_an_empty_table_give_none(label, words):
    assert L.currency_of(label, words=words) is None


def test_names_currency_is_true_when_any_word_matches_a_word_under_two_codes_included():
    assert L.names_currency("paid 500 zorb", WORDS) is True
    assert L.names_currency("paid 500 shared", AMBIGUOUS) is True and L.currency_of("shared", words=AMBIGUOUS) is None
    assert L.names_currency("paid 500" + YEN, WORDS) is True
    assert L.names_currency("paid 500 xzorb", WORDS) is False
    assert [L.names_currency(x, WORDS) for x in (None, "", "   ")] == [False] * 3
    assert L.names_currency("zorb", {}) is False and L.names_currency("zorb", {"QDA": []}) is False


@pytest.mark.parametrize("junk", [w for _, w in INVISIBLE], ids=[n for n, _ in INVISIBLE])
def test_a_word_with_no_visible_character_in_a_table_given_to_a_call_names_nothing(junk):
    labels = ["Amount", "total 500", "amount (zorb)", "x", "5000", "\u0627\u06cc\u0646", ""]
    assert [L.currency_of(x, words={"QDA": [junk]}) for x in labels] == [None] * len(labels)
    assert [L.names_currency(x, {"QDA": [junk]}) for x in labels] == [False] * len(labels)
    both = {"QDA": [junk], "QDC": ["zorb"]}                     # a real word under another code still wins
    assert [L.currency_of(x, words=both) for x in ("Amount", "amount (zorb)", "5000zorb")] == [None, "QDC", "QDC"]
    assert [L.names_currency(x, both) for x in ("paid 500", "paid 500 zorb")] == [False, True]


# --- 5. the shipped forms: 0.4.0 pins, then the documented differences ----------------------------------------------

SHIPPED_FORMS = [
    ("\u0645\u0628\u0644\u063a (\u0631\u064a\u0627\u0644)", "IRR"),    # test_localize: Arabic yeh
    ("Amount (Toman)", "IRT"), ("Amount", None),                       # test_localize
    ("Amount (IRT)", "IRT"), ("amount irr", "IRR"), ("AMOUNT (TOMAN)", "IRT"), ("Total in Rials", "IRR"),
    ("Total in tomans", "IRT"), ("Amount (Rial)", "IRR"), ("price in IRT", "IRT"),
    ("\u0645\u0628\u0644\u063a (" + FA_IRT + ")", "IRT"), ("\u0645\u0628\u0644\u063a \u0628\u0647 " + FA_IRR, "IRR"),
    ("\u0645\u0628\u0644\u063a(" + FA_IRT + ")", "IRT"),
    ("\u0645\u0628\u0644\u063a \u0631\u064a\u0627\u0644", "IRR"),      # Arabic yeh in the word
    ("\u0645\u0628\u0644\u063a \u0631\u06cc\u0627\u0644\u200c\u0647\u0627", "IRR"),   # the plural, ZWNJ then a suffix
    ("\u0645\u0628\u0644\u063a " + FA_IRT_ADJ, "IRT"), ("\u0645\u0628\u0644\u063a " + FA_IRR_ADJ, "IRR"),
    ("\u0645\u0628\u0644\u063a \u06f1\u06f2 " + FA_IRT, "IRT"),
    ("Toman / Rial", "IRT"),                                           # both units named: 0.4.0 took the Toman first
    ("Total", None), ("Rialto", None), ("irrigation", None), ("Tomanova", None), ("\u0645\u0628\u0644\u063a", None),
]


@pytest.mark.parametrize("label,code", SHIPPED_FORMS, ids=[f"f{i:02d}" for i in range(len(SHIPPED_FORMS))])
def test_the_shipped_forms_of_0_4_0_name_the_same_code(fresh_table, label, code):
    assert L.currency_of(label) == code


@pytest.mark.parametrize("label,code", [("5000rial", "IRR"), ("5000toman", "IRT"), ("amount_irt", "IRT"), ("irr_total", "IRR"),
                                        ("Rial / Toman", "IRR"), ("\u0631\u06cc\u0627\u0644 \u062a\u0648\u0645\u0627\u0646", "IRR"),
                                        ("Amount (USD)", "USD"), ("Total in euros", "EUR"), ("Amount (\u20ac)", "EUR"),
                                        ("10\u20ac", "EUR"), ("\u0645\u0628\u0644\u063a " + FA_EUR, "EUR")])
def test_the_documented_differences_from_0_4_0_digit_touching_earliest_wins_and_the_other_shipped_words(fresh_table,
                                                                                                         label, code):
    assert L.currency_of(label) == code


# --- 6. the process table -------------------------------------------------------------------------------------------


def test_many_calls_read_the_packs_at_most_once(fresh_table, monkeypatch):
    L.fold_name("warm")   # the letter table is read once per process too, and is not under test here
    calls = spy_views(monkeypatch)
    got = [(L.currency_of("Amount (Toman)"), L.names_currency("paid 40 euros"), L.currency_of("Amount")) for _ in range(60)]
    assert got == [("IRT", True, None)] * 60
    assert len(calls) <= 1


def test_use_currency_words_replaces_the_table_and_the_packs_are_not_read(fresh_table, monkeypatch):
    L.fold_name("warm")
    calls = spy_views(monkeypatch)
    L.use_currency_words(WORDS)
    assert L.currency_of("amount (zorb)") == "QDA" and L.currency_of("Amount (Toman)") is None
    assert L.names_currency("500 zorb") is True and L.names_currency("500 euros") is False
    L.use_currency_words({"QDC": ["qdw"]})   # replaced, not added to
    assert L.currency_of("amount (zorb)") is None and L.currency_of("amount (qdw)") == "QDC"
    L.use_currency_words({})
    assert L.currency_of("amount (qdw)") is None and L.names_currency("qdw") is False
    assert calls == []


def test_a_table_given_to_a_call_does_not_change_the_table_of_the_process(fresh_table):
    L.use_currency_words(WORDS)
    assert L.currency_of("amount (qdw)", words={"QDC": ["qdw"]}) == "QDC"
    assert L.names_currency("500 qdw", {"QDC": ["qdw"]}) is True
    assert L.currency_of("amount (zorb)", words={}) is None          # {} is a table, it is not "no table"
    assert L.currency_of("amount (zorb)") == "QDA" and L.currency_of("amount (qdw)") is None
    assert L.names_currency("500 zorb") is True and L.names_currency("500 qdw") is False


# --- 7. the request -------------------------------------------------------------------------------------------------


def test_the_request_holds_the_currency_words_of_every_active_pack(h, job):
    c = live(h, T_WORDS)
    sent = sent_requests(h, c)
    assert sent[0]["locale"]["currency_words"] == cw_of(c) != {}
    assert sent[0]["locale"]["currency_words"]["QDA"] == WORDS["QDA"] and "fold" in sent[0]["locale"]
    assert sent[0]["locale"]["currency_pairs"][-1] == PAIR


def test_a_profile_list_that_leaves_a_pack_out_does_not_send_its_words_but_still_sends_its_pairs(h, job):
    live(h, T_WORDS)
    set_profile(h, locales=["en"])
    assert locales.active(ctx_of(h)) == ["en"]
    sent = sent_requests(h, ctx_of(h))[0]["locale"]
    assert sent["currency_words"] == CURRENCY_WORDS["en"]       # the words follow the active packs only
    assert PAIR in sent["currency_pairs"]                       # the pairs follow every installed pack
    set_profile(h, locales=["t-words"])
    sent = sent_requests(h, ctx_of(h))[0]["locale"]
    assert sent["currency_words"]["QDA"] == WORDS["QDA"] and sent["currency_words"]["USD"] == ["usd"]
    assert "IRT" not in sent["currency_words"]                  # the pack ir is not named


def test_the_view_is_read_once_for_the_three_keys(h, job, monkeypatch):
    c = live(h, T_WORDS)
    calls = spy_views(monkeypatch)
    sent = sent_requests(h, c)[0]["locale"]
    assert set(sent) >= {"fold", "currency_pairs", "currency_words"} and sent["currency_words"]["QDA"] == WORDS["QDA"]
    assert len(calls) == 1


# --- 8. the check process -------------------------------------------------------------------------------------------

CHECK = '''
import json, sys
sys.path.insert(0, %r)
from pathlib import Path
from harness_core import checklib, locales, localize
views = []
real_view = locales.view
locales.view = lambda *a, **k: views.append(1) or real_view(*a, **k)

def fn(req):
    if "marker" in req:
        Path(req["marker"]).write_text("ran")
    return "pass", {"codes": [localize.currency_of(x) for x in req.get("labels", [])],
                    "names": [localize.names_currency(x) for x in req.get("texts", [])],
                    "views": len(views)}, []

checklib.main(fn)
''' % str(ROOT / "lib")


def process(tmp_path, request, env=None):
    """A check that reads ``labels`` and ``texts``: ``/usr/bin/python3 -I``, the request on stdin, an empty environment."""
    script = tmp_path / "check_words.py"
    script.write_text(CHECK, "utf-8")
    return subprocess.run([PY, "-I", str(script)], input=json.dumps(request).encode("utf-8"),
                          env={} if env is None else env, cwd=str(tmp_path), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=120)


def values(tmp_path, request, env=None):
    p = process(tmp_path, request, env)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["values"]


def test_a_request_with_currency_words_detects_a_made_up_word_and_reads_no_pack(tmp_path):
    got = values(tmp_path, {"labels": ["Amount (zorb)", "3000" + YEN, "amount " + HRN, "Amount"] * 15,
                            "texts": ["paid 500 zorb", "paid 500"],
                            "locale": {"fold": {}, "currency_words": WORDS}})
    assert got["codes"] == ["QDA", "QDA", "QDC", None] * 15 and got["names"] == [True, False]
    assert got["views"] == 0


def test_the_words_of_the_request_are_the_whole_table_a_shipped_word_is_not_added_to_them(tmp_path):
    got = values(tmp_path, {"labels": ["Amount (Toman)", "Amount (USD)", "Amount (zorb)"], "texts": ["40 euros"],
                            "locale": {"fold": {}, "currency_words": {}}})
    assert got["codes"] == [None, None, None] and got["names"] == [False] and got["views"] == 0
    got = values(tmp_path, {"labels": ["Amount (Toman)", "Amount (zorb)"],
                            "locale": {"fold": {}, "currency_words": WORDS}})
    assert got["codes"] == [None, "QDA"]


@pytest.mark.parametrize("locale", [{"fold": {"letters": {}}}, {"fold": {}, "currency_pairs": []}, None],
                         ids=["only-fold", "pairs-only", "no-locale"])
def test_a_request_without_the_key_detects_the_regional_words_as_in_0_4_0_under_python_I(tmp_path, locale):
    req = {"labels": ["Amount (Toman)", "Amount (Rial)", "\u0645\u0628\u0644\u063a (" + FA_IRR + ")", "Amount", "zorb"]}
    if locale is not None:
        req["locale"] = locale
    assert values(tmp_path, req)["codes"] == ["IRT", "IRR", "IRR", None, None]   # an old record replays as before


@pytest.mark.parametrize("locale", [{"fold": {"letters": {}}}, None], ids=["only-fold", "no-locale"])
def test_a_request_without_the_key_detects_the_other_shipped_words_too(tmp_path, locale):
    req = {"labels": ["Amount (USD)", "Amount (euros)", "5000toman", "amount_irt"], "texts": ["40 euros"]}
    if locale is not None:
        req["locale"] = locale
    got = values(tmp_path, req)
    assert got["codes"] == ["USD", "EUR", "IRT", "IRT"] and got["names"] == [True]


def test_the_shipped_words_are_read_once_per_process(tmp_path):
    got = values(tmp_path, {"labels": ["Amount (Toman)"] * 50, "texts": ["paid 40 euros"] * 50,
                            "locale": {"fold": {"letters": {}}}})
    assert got["codes"] == ["IRT"] * 50 and got["names"] == [True] * 50 and got["views"] == 1


def pack_check(h, header="amount (zorb)", args=None, attempt=1):
    """The finance check on the file ``amounts`` with ``header`` for its amount column."""
    return check(h, args or {}, amounts(1).replace(",amount\n", f",{header}\n"), attempt)


def test_the_frozen_channel_the_replay_gets_the_words_the_first_run_had(h, job):
    c = live(h, T_WORDS)
    business.set_field(c, "currency", "QDC")
    first = pack_check(h)
    assert first["result"] == "pass" and first["values"]["total"] == 400000000, first["notes"]   # QDA read, QDC shown
    (h["data"] / "control" / "locales" / "t-words.json").unlink()   # the live pack is gone: the data root changed
    p, log = log_of(h, job)
    row = Outputs(p, log).rows()[-1]
    assert oracle.replay(p.run, row, time.time() + 240) is None   # the same result and the same values
    gone = pack_check(h, attempt=2)
    assert gone["result"] == "pass" and gone["values"]["total"] == 4000000   # control: the word is not a word any more


# --- 9. the finance check through run.apply -------------------------------------------------------------------------


def test_a_header_that_names_a_live_word_is_converted_to_the_profile_currency_with_the_note(h, job):
    c = live(h, T_WORDS)
    business.set_field(c, "currency", "QDC")
    res = pack_check(h)
    assert res["result"] == "pass", res["notes"]
    v = res["values"]
    assert v["total"] == 400000000 and v["unit"] == "QDC" and v["rows_used"] == 3
    assert v["total_other_unit"] == 4000000
    assert res["notes"] == ["amounts were in QDA and are shown in QDC"]
    res = pack_check(h, "amount (3000" + YEN + ")")    # the sign touches nothing but a digit
    assert res["result"] == "pass" and res["values"]["total"] == 400000000


def test_a_header_that_names_a_code_with_no_relation_to_the_profile_currency_ends_in_an_error(h, job):
    c = live(h, T_WORDS, pack("t-other", currency_pairs=[pair("QKY", "QYN", 1000)]))
    business.set_field(c, "currency", "QKY")
    res = pack_check(h)
    assert res["result"] == "error" and "total" not in res["values"]
    assert "cannot convert QDA to QKY" in " ".join(res["notes"])


def test_a_header_that_names_a_code_and_no_profile_currency_shows_that_code(h, job):
    live(h, T_WORDS)
    assert business.profile(ctx_of(h)).get("currency") is None
    res = pack_check(h)
    assert res["result"] == "pass" and res["values"]["unit"] == "QDA" and res["values"]["total"] == 4000000
    assert res["notes"] == []


def test_a_shipped_word_in_the_header_and_no_profile_currency_shows_its_code(h, job):
    assert business.profile(ctx_of(h)).get("currency") is None
    res = pack_check(h, "Amount (USD)")
    assert res["result"] == "pass" and res["values"]["unit"] == "USD" and res["values"]["total"] == 4000000


def test_a_header_that_names_no_currency_is_read_in_the_profile_currency_as_today(h, job):
    business.set_field(ctx_of(h), "currency", "QDC")
    res = pack_check(h, "Amount")
    assert res["result"] == "pass" and res["values"]["unit"] == "QDC" and res["values"]["total"] == 4000000
    assert res["notes"] == []


# --- 10. validation of the request ----------------------------------------------------------------------------------

OK_PAIR = pair("QDA", "QDC", 100)
BAD_PAIR_LISTS = [
    ("entry-a-number", [5]), ("entry-a-string", ["QDA"]), ("entry-null", [None]), ("entry-a-list", [["QDA", "QDC", 100]]),
    ("from-missing", [{"to": "QDC", "factor": 100}]), ("to-missing", [{"from": "QDA", "factor": 100}]),
    ("factor-missing", [{"from": "QDA", "to": "QDC"}]),
    ("factor-0", [pair("QDA", "QDC", 0)]), ("factor-1", [pair("QDA", "QDC", 1)]),
    ("factor-negative", [pair("QDA", "QDC", -3)]), ("factor-float", [pair("QDA", "QDC", 2.5)]),
    ("factor-a-whole-float", [pair("QDA", "QDC", 100.0)]), ("factor-string", [pair("QDA", "QDC", "100")]),
    ("factor-boolean", [pair("QDA", "QDC", True)]), ("factor-null", [pair("QDA", "QDC", None)]),
    ("from-lowercase", [pair("qda", "QDC", 100)]), ("from-two-letters", [pair("QD", "QDC", 100)]),
    ("to-four-letters", [pair("QDA", "QDCC", 100)]), ("to-with-a-digit", [pair("QDA", "QD1", 100)]),
    ("from-a-number", [pair(5, "QDC", 100)]),
    ("the-same-code-twice", [pair("QDA", "QDA", 100)]),
    ("the-same-two-codes-in-two-entries", [OK_PAIR, pair("QDA", "QDC", 7)]),
    ("the-same-two-codes-reversed", [OK_PAIR, pair("QDC", "QDA", 100)]),
    ("a-bad-entry-after-a-good-one", [OK_PAIR, pair("QKY", "QKY", 100)]),
]
BAD_WORD_TABLES = [
    ("table-null", None), ("table-a-list", ["zorb"]), ("table-a-string", "zorb"), ("table-a-number", 7),
    ("code-lowercase", {"qda": ["zorb"]}), ("code-two-letters", {"QD": ["zorb"]}),
    ("code-four-letters", {"QDAA": ["zorb"]}), ("code-with-a-digit", {"QD1": ["zorb"]}),
    ("value-a-string", {"QDA": "zorb"}), ("value-an-object", {"QDA": {"w": "zorb"}}), ("value-a-number", {"QDA": 5}),
    ("value-null", {"QDA": None}),
    ("entry-a-number", {"QDA": [5]}), ("entry-null", {"QDA": [None]}), ("entry-a-list", {"QDA": [["zorb"]]}),
    ("empty-word", {"QDA": [""]}), ("empty-word-after-a-good-one", {"QDA": ["zorb", ""]}),
    ("a-bad-code-after-a-good-one", {"QDA": ["zorb"], "qdc": ["x"]}),
] + [(f"no-visible-character-{n}", {"QDA": ["zorb", w]}) for n, w in INVISIBLE] + \
    [(f"only-no-visible-character-{n}", {"QDA": [w]}) for n, w in INVISIBLE[:2]]


def refused(tmp_path, key, value):
    marker = tmp_path / "ran"
    p = process(tmp_path, {"labels": [], "marker": str(marker), "locale": {"fold": {}, key: value}})
    assert p.returncode == 3 and p.stdout == b"" and b"check error" in p.stderr, p.stderr
    assert f"locale.{key}".encode() in p.stderr and not marker.exists()   # it names the key; the check did not run
    return p


@pytest.mark.parametrize("case", BAD_PAIR_LISTS, ids=[c[0] for c in BAD_PAIR_LISTS])
def test_a_request_with_bad_currency_pairs_ends_with_an_error_exit_that_names_the_key_and_runs_no_check(tmp_path, case):
    refused(tmp_path, "currency_pairs", case[1])


@pytest.mark.parametrize("case", BAD_WORD_TABLES, ids=[c[0] for c in BAD_WORD_TABLES])
def test_a_request_with_bad_currency_words_ends_with_an_error_exit_that_names_the_key_and_runs_no_check(tmp_path, case):
    refused(tmp_path, "currency_words", case[1])


@pytest.mark.parametrize("value", [{}, "abc", 7, None], ids=["object", "string", "number", "null"])
def test_the_message_for_pairs_that_are_not_a_list_stays(tmp_path, value):
    assert b"locale.currency_pairs is not a list" in refused(tmp_path, "currency_pairs", value).stderr


@pytest.mark.parametrize("word", [w for _, w in VISIBLE], ids=[n for n, _ in VISIBLE])
def test_a_request_with_a_word_that_has_a_visible_character_runs_the_check(tmp_path, word):
    marker = tmp_path / "ran"
    got = values(tmp_path, {"labels": ["amount (zorb)"], "marker": str(marker),
                            "locale": {"fold": {}, "currency_words": {"QDA": [word, "zorb"]}}})
    assert marker.exists() and got["codes"] == ["QDA"]


def test_a_request_with_valid_pairs_and_words_runs_the_check(tmp_path):
    marker = tmp_path / "ran"
    got = values(tmp_path, {"labels": ["amount (zorb)"], "marker": str(marker),
                            "locale": {"fold": {}, "currency_pairs": [OK_PAIR, pair("QKY", "QYN", 1000)],
                                       "currency_words": WORDS}})
    assert marker.read_text("utf-8") == "ran" and got["codes"] == ["QDA"]


@pytest.mark.parametrize("key,value", [("currency_pairs", []), ("currency_words", {}), ("currency_words", {"QDA": []})])
def test_an_empty_table_is_valid(tmp_path, key, value):
    marker = tmp_path / "ran"
    values(tmp_path, {"marker": str(marker), "locale": {"fold": {}, key: value}})
    assert marker.exists()


def test_a_locale_object_with_no_keys_stays_an_error_exit(tmp_path):
    marker = tmp_path / "ran"
    p = process(tmp_path, {"marker": str(marker), "locale": {}})
    assert p.returncode == 3 and p.stdout == b"" and not marker.exists()


# --- 11. claim_type and the sub-agent hook --------------------------------------------------------------------------

SIGNS = ["\u00a3", "\u00a5", "\u20b9", "\u20a9", "\u20aa", "\u20ab", "\u20bd", "\u20ba", RIAL_SIGN, "\u00a2"]   # none is $ or the euro


def test_the_signs_of_the_tests_are_currency_symbols_of_unicode_and_not_the_ones_of_0_4_0():
    assert all(unicodedata.category(s) == "Sc" for s in SIGNS) and "$" not in SIGNS and "\u20ac" not in SIGNS


def test_a_quantity_and_a_live_word_is_a_price_claim_the_word_alone_is_not():
    assert claims.claim_type("paid 500 zorb to the vendor", WORDS) == "price"
    assert claims.claim_type("paid 3000" + YEN + " to the vendor", WORDS) == "price"
    assert claims.claim_type("paid 500 " + HRN, WORDS) == "price"
    assert claims.claim_type("the zorb is a unit", WORDS) == "default"          # no quantity
    assert claims.claim_type("paid 5 zorb to the vendor", WORDS) == "default"   # 5 is not a quantity (one digit)
    assert claims.claim_type("paid 500 xzorb to the vendor", WORDS) == "default"
    assert claims.claim_type("paid 500 zorb to the vendor", {}) == "default"
    assert claims.claim_type("paid 500 zorb to the vendor") == "default"        # the shipped table does not know it


def test_a_word_under_two_codes_still_makes_a_price_claim():
    assert claims.claim_type("paid 500 shared", AMBIGUOUS) == "price"
    assert claims.claim_type("the shared is a unit", AMBIGUOUS) == "default"


@pytest.mark.parametrize("sign", SIGNS)
def test_a_quantity_and_any_currency_sign_of_unicode_is_a_price_claim(sign):
    assert claims.claim_type(f"paid {sign}1,500 for lunch") == "price"
    assert claims.claim_type(f"paid 1,500 {sign}") == "price"
    assert claims.claim_type(f"paid {sign}1,500 for lunch", {}) == "price"
    assert claims.claim_type(f"the {sign} sign") == "default"                  # no quantity


@pytest.mark.parametrize("text", ["growth was 15% in May", "see section \u00a7 12 for 1,500 units",
                                  "a 20 + 30 = 50 sum", "text of 1,500 \u00b0 turns"])
def test_a_quantity_and_a_character_that_is_not_a_currency_sign_is_no_price_claim(text):
    assert claims.claim_type(text) == "default"


@pytest.mark.parametrize("text", ["europe 50", "material 10", "trial 20 and serial 30"])
def test_a_currency_word_inside_a_longer_word_is_no_longer_a_price_claim(text):
    assert claims.claim_type(text) == "default"


@pytest.mark.parametrize("text,words,want", [
    ("the price of zorb", WORDS, "price"), ("the tariff changed", WORDS, "price"), ("the fee", {}, "price"),
    ("a law on zorb", WORDS, "law"), ("the decree sets a fee of 500 zorb", WORDS, "law"),
    ("the act was amended in 2019", {}, "law"), ("the statute says 30 days", WORDS, "law"),
])
def test_a_price_word_and_a_law_word_behave_as_today(text, words, want):
    assert claims.claim_type(text, words) == want


T_0_4_0 = [
    ("The ministry set the price at 40 EUR", "price"), ("paid 2,500,000 toman to the supplier", "price"),
    ("\u067e\u0631\u062f\u0627\u062e\u062a \u06f2\u06f5\u06f0\u06f0\u06f0\u06f0\u06f0 " + FA_IRT, "price"),
    ("balance 1500 USD", "price"), ("the ticket is \u20ac50", "price"), ("it sold for $5.50 each", "price"),
    ("Total 1,200 rials", "price"), (FA_IRR + " \u06f5\u06f0\u06f0\u06f0 \u0628\u0648\u062f", "price"),
    ("40 euros in total", "price"), ("reached 12,000 EUR", "price"), ("\u06f2\u06f5\u06f0\u06f0\u06f0" + FA_IRR, "price"),
    ("5000toman", "price"), ("amount_usd 5000", "price"),
    ("\u0642\u06cc\u0645\u062a \u0641\u0648\u0644\u0627\u062f", "price"),
    ("we sold 300 units", "default"), ("the company has 150 employees", "default"), ("growth was 15%", "default"),
    ("the meeting is on 2024/03/19", "default"), ("paid 5 toman", "default"), ("toman", "default"), ("", "default"),
    ("the act was amended in 2019", "law"), ("\u0642\u0627\u0646\u0648\u0646 \u0645\u0627\u0644\u06cc\u0627\u062a \u0627\u0635\u0644\u0627\u062d \u0634\u062f", "law"),
    ("the decree sets a fee of 500 dollars", "law"),
]


@pytest.mark.parametrize("text,want", T_0_4_0, ids=[f"t{i:02d}" for i in range(len(T_0_4_0))])
def test_the_texts_of_0_4_0_keep_their_claim_type(fresh_table, text, want):
    assert claims.claim_type(text) == want


ROW = {"claim": "the vendor was paid 500 zorb", "quote": "The vendor was paid 500 zorb in May",
       "url": "https://example.com/a", "snapshot_sha": "ab" * 32, "date": "2026-05"}


def test_claim_row_passes_the_table_on_to_claim_type():
    fetched = {"ts": "2026-05-01T00:00:00Z", "status": "OK"}
    assert claims.claim_row(ROW, fetched, "a1", words=WORDS)["claim_type"] == "price"
    assert claims.claim_row(ROW, fetched, "a1", WORDS)["claim_type"] == "price"
    assert claims.claim_row(ROW, fetched, "a1")["claim_type"] == "default"
    assert claims.claim_row(ROW, fetched, "a1", words={})["claim_type"] == "default"


PAGE = "The vendor was paid 500 zorb in May, and the second invoice was paid 700 zorb in June."
URL = "https://news.example.ir/a"
QUOTES = [("paid 500 zorb", "The vendor was paid 500 zorb in May"),
          ("paid 700 zorb", "the second invoice was paid 700 zorb in June")]


def rows_of(sha):
    return [{"claim": c, "quote": q, "date": "2026-05", "url": URL, "snapshot_sha": sha} for c, q in QUOTES]


def fire(h, message, agent_id="a1"):
    subagent.on_subagent_stop({"agent_type": lanes.RESEARCHER, "agent_id": agent_id, "last_assistant_message": message},
                              ctx_of(h))


def stop(h, locales_=None):
    """A researcher's SubagentStop with two claims about money in the made-up currency; the claim types it wrote."""
    job = compiled(h)
    sha = _fetched(h, job, URL, PAGE)
    if locales_:
        set_profile(h, locales=locales_)
    fire(h, json.dumps(rows_of(sha)))
    p = job_paths(h["data"], h["ws"], job)
    return [r["claim_type"] for r in store.read_jsonl(p.run, EventLog(p.run, p.heads), "claims.jsonl")]


def test_the_hook_writes_a_price_claim_for_a_word_of_a_live_pack(h):
    live(h, T_WORDS)
    assert stop(h) == ["price", "price"]


def test_the_hook_writes_a_default_claim_when_the_profile_leaves_the_pack_out(h):
    live(h, T_WORDS)
    assert stop(h, ["en"]) == ["default", "default"]


def test_the_hook_writes_a_default_claim_when_no_pack_holds_the_word(h):
    assert stop(h) == ["default", "default"]


def test_the_hook_reads_the_view_once_per_event_and_only_when_it_has_rows_to_write(h, monkeypatch):
    live(h, T_WORDS)
    job = compiled(h)
    sha = _fetched(h, job, URL, PAGE)
    calls = spy_views(monkeypatch)
    fire(h, json.dumps(rows_of(sha)))                                 # two rows to write
    assert len(calls) == 1
    del calls[:]
    fire(h, "[]", "a2")                                               # no row
    fire(h, json.dumps([dict(r, quote="not on any page at all") for r in rows_of(sha)]), "a3")   # no row accepted
    assert calls == []


# --- 12. the probe and the stop gate --------------------------------------------------------------------------------


def money(text, ctx=None):
    return probe.run(text, ctx)["money_tokens"]


@pytest.mark.parametrize("text,hits", [
    ("it came to 3000" + YEN, [YEN]), ("it came to " + YEN + "3000", [YEN]), ("it came to 3000zorb", ["zorb"]),
    ("it came to 3000 zorb", ["zorb"]), ("it came to zorb_3000", ["zorb"]), ("it came to 50" + HRN, [HRN]),
    ("it came to 3000zorbs", []), ("it came to 3000xzorb", []), ("it came to 3000" + YEN + "x", []),
    ("it came to 3000\u65e5" + YEN, []),
])
def test_a_live_currency_word_is_money_where_no_letter_touches_it(h, text, hits):
    c = live(h, T_WORDS)
    assert money(text, c) == hits
    assert probe.run(text, c)["quantitative"] is True


def test_a_live_currency_word_is_not_money_when_the_profile_leaves_the_pack_out(h):
    live(h, T_WORDS)
    set_profile(h, locales=["en"])
    assert money("it came to 3000zorb", ctx_of(h)) == []


@pytest.mark.parametrize("text,hits", [("send 5000toman", ["toman"]), ("amount_usd 5", ["usd"]),
                                       ("\u06f5\u06f0\u06f0 " + FA_IRR_ADJ, [FA_IRR_ADJ]),
                                       ("\u06f5\u06f0\u06f0" + FA_IRT, [FA_IRT]), ("3000EUR", ["eur"])])
def test_the_shipped_currency_words_are_money_where_a_digit_or_an_underscore_touches_them(text, hits):
    assert money(text) == hits


@pytest.mark.parametrize("text,hits", [("10\u20ac", ["\u20ac"]), ("zorb5000 3000" + YEN, []), ("40 euros", ["euros"]),
                                       ("a Toman", ["toman"]), ("the europe 5", []), ("tomanova 5", []),
                                       ("3dollars", []), ("invoice5 due", []), ("pay 5 dollars", ["dollars"])])
def test_the_shipped_money_words_keep_the_0_4_0_results_of_the_probe(text, hits):
    assert money(text) == hits


def quick_answer(h, msg):
    """What the stop gate says to ``msg`` after a request that is a quick question (and no card pending)."""
    say(h, "What is the capital of France?", "q1")
    assert sessionstate.load(ctx_of(h), SID).get("pending_card") is None   # the request itself raised nothing
    return stopgate.on_stop({"session_id": SID, "last_assistant_message": msg}, ctx_of(h))


@pytest.mark.parametrize("msg,word", [("It came to 3000" + YEN + ".", YEN), ("It came to 3000zorb.", "zorb"),
                                      ("Es ist 3000 zorb wert.", "zorb"), ("It came to zorb_3000.", "zorb")])
def test_the_stop_gate_blocks_a_quick_answer_that_holds_a_live_currency_word_touching_a_digit(h, msg, word):
    live(h, T_WORDS)
    out = quick_answer(h, msg)
    assert out["decision"] == "block" and "names money" in out["reason"] and f"({word})" in out["reason"]


def test_the_stop_gate_does_not_block_a_word_that_a_letter_touches(h):
    live(h, T_WORDS)
    assert quick_answer(h, "It came to 3000zorbs.") == {}


@pytest.mark.parametrize("msg,blocks", [("It came to 3000zorb.", True), ("Paris is the capital of France.", False)])
def test_the_stop_gate_reads_the_view_once_per_event_and_no_other_pack(h, monkeypatch, msg, blocks):
    live(h, T_WORDS)
    say(h, "What is the capital of France?", "q1")
    monkeypatch.setattr(stopgate, "lexicon_hits", lambda ctx, text: [])   # its own state-word read is not under test
    views, installed, active = spy_views(monkeypatch), [], []
    real_installed, real_active = locales.installed, locales.active
    monkeypatch.setattr(locales, "installed", lambda *a, **k: installed.append(1) or real_installed(*a, **k))
    monkeypatch.setattr(locales, "active", lambda *a, **k: active.append(1) or real_active(*a, **k))
    out = stopgate.on_stop({"session_id": SID, "last_assistant_message": msg}, ctx_of(h))
    assert bool(out) is blocks and (not blocks or "(zorb)" in out["reason"])
    assert (len(views), len(installed), len(active)) == (1, 1, 0)


def test_the_stop_gate_lets_the_same_answer_pass_when_no_active_pack_holds_the_word(h):
    live(h, T_WORDS)
    set_profile(h, locales=["en"])
    assert quick_answer(h, "It came to 3000zorb.") == {}


def test_the_stop_gate_blocks_a_shipped_word_touching_a_digit(h):
    out = quick_answer(h, "It came to 5000toman.")
    assert out["decision"] == "block" and "names money" in out["reason"] and "toman" in out["reason"]


def test_words_has_the_eleven_keys_and_money_words_in_the_order_pack_lists_currency_words_owner(h):
    c = live(h, pack("t-words", words={"money_words": ["plainw"], "currency_words": WORDS}))
    write_probe(h, {"money_words": ["ownw", "zorb", "plainw", "usd"]})
    got = probe.words(c)
    view = locales.view(c, every=True)["words"]
    listed = [w for ws in view["currency_words"].values() for w in ws]
    assert set(got) == {"verbs", "verbs_anywhere", "imperative_lead", "imperative_join", "question_lead", "money_words",
                        "quantity_words", "decision_keywords", "continuation", "file_extensions", "question_marks"}
    assert got["money_words"] == list(dict.fromkeys(view["money_words"] + listed + ["ownw"]))
    assert got["money_words"][-1] == "ownw" and "plainw" in view["money_words"]
    assert got["money_words"].index("plainw") < got["money_words"].index("usd") < got["money_words"].index("zorb")
    assert got["money_words"].count("usd") == 1 and got["money_words"].count("zorb") == 1


def test_the_shipped_money_words_run_in_the_order_pack_lists_then_currency_words():
    got = probe.words(None)["money_words"]
    packs_first = MONEY_WORDS["en"] + MONEY_WORDS["fa"] + MONEY_WORDS["ir"]
    flat = ["usd", "eur", "euro", "euros", "\u20ac", FA_EUR, "toman", "tomans", "irt", FA_IRT, FA_IRT_ADJ, "rial", "rials",
            "irr", FA_IRR, FA_IRR_ADJ]
    assert got == packs_first + flat


def test_an_owner_money_word_still_matches_by_the_old_rule_and_a_currency_word_by_the_new_one(h):
    c = live(h, T_WORDS)
    write_probe(h, {"money_words": ["zloty", "zorb"]})
    assert money("the zloty arrived", c) == ["zloty"]
    assert money("the 3000zloty arrived", c) == []                   # an owner word: whole words, a digit is a letter
    assert money("the 3000zorb arrived", c) == ["zorb"]              # a pack currency word, repeated by the owner
    assert probe.words(c)["money_words"].count("zorb") == 1
    assert money("a zloty and 3000zorb", c) == ["zloty", "zorb"]


# --- 13. the magnitude note -----------------------------------------------------------------------------------------

MAG = "date,amount\n" + "".join(f"1402/11/{d:02d},1000\n" for d in (1, 2, 3)) + \
      "".join(f"1402/12/{d:02d},10000\n" for d in (1, 2, 3))   # a ten times jump from one month to the next
NEUTRAL = "the total is 10.0 times the prior period's: check for a missing zero or amounts in another currency"


def magnitude(h, job, unit=None):
    """(the result, the note codes of the run) for ``MAG`` with the amount header ``amount (unit)``."""
    (h["ws"] / "sales.csv").write_text(MAG.replace("amount", f"amount ({unit})" if unit else "amount", 1), encoding="utf-8")
    res = run_req(h)
    p, log = log_of(h, job)
    return res, Outputs(p, log).rows()[-1]["note_codes"]


def mag_codes(codes_):
    return [c for c in codes_ if c and c["code"].startswith("magnitude")]


def test_a_unit_with_no_pair_gets_the_neutral_magnitude_note(h, job):
    live(h, pack("t-no-pair", words={"currency_words": {"QKY": ["zorbk"]}}))
    res, codes_ = magnitude(h, job, "zorbk")
    got = mag_codes(codes_)
    assert res["result"] == "fail" and [c["code"] for c in got] == ["magnitude"] and set(got[0]["params"]) == {"ratio"}
    joined = " | ".join(res["notes"])
    assert NEUTRAL in res["notes"] and "QKY" not in joined and "mix-up" not in joined


def test_a_header_with_no_unit_gets_the_neutral_magnitude_note(h, job):
    res, codes_ = magnitude(h, job)
    assert res["result"] == "fail" and [c["code"] for c in mag_codes(codes_)] == ["magnitude"] and NEUTRAL in res["notes"]


@pytest.mark.parametrize("unit", ["zorb", "grn"])
def test_a_live_pair_gives_the_units_note_with_from_and_other_as_to_whichever_unit_the_result_is_in(h, job, unit):
    live(h, pack("t-words", words={"currency_words": {"QDA": ["zorb"], "QDC": ["grn"]}}, currency_pairs=[PAIR]))
    res, codes_ = magnitude(h, job, unit)
    got = mag_codes(codes_)
    assert res["result"] == "fail" and [c["code"] for c in got] == ["magnitude_units"]
    assert got[0]["params"]["unit"] == "QDA" and got[0]["params"]["other"] == "QDC" and "ratio" in got[0]["params"]
    assert "the total is 10.0 times the prior period's: check for a QDA/QDC mix-up or a missing zero" in res["notes"]


@pytest.mark.parametrize("unit", ["Toman", "Rial"])
def test_the_shipped_pair_gives_the_units_note_in_codes(h, job, unit):
    res, codes_ = magnitude(h, job, unit)
    got = mag_codes(codes_)
    assert [c["code"] for c in got] == ["magnitude_units"] and (got[0]["params"]["unit"], got[0]["params"]["other"]) == \
        ("IRT", "IRR")
    assert "the total is 10.0 times the prior period's: check for a IRT/IRR mix-up or a missing zero" in res["notes"]
    assert "Toman/Rial" not in " | ".join(res["notes"])


TEMPLATES = sorted((ROOT / "templates").glob("*.json"))
EN_MAGNITUDE = {"note.magnitude": "the total is {ratio} times the prior period's: check for a missing zero or amounts in another currency",
                "note.magnitude_units": "the total is {ratio} times the prior period's: check for a {unit}/{other} "
                                        "mix-up or a missing zero"}
FA_MAGNITUDE = {
    "note.magnitude": "\u062c\u0645\u0639 \u0627\u06cc\u0646 \u062f\u0648\u0631\u0647 {ratio} \u0628\u0631\u0627\u0628\u0631 \u062f\u0648\u0631\u0647\u0654 \u0642\u0628\u0644 \u0627\u0633\u062a: \u0634\u0627\u06cc\u062f \u06cc\u06a9 \u0635\u0641\u0631 \u062c\u0627 \u0627\u0641\u062a\u0627\u062f\u0647 \u06cc\u0627 \u0645\u0628\u0644\u063a\u200c\u0647\u0627 \u0628\u0647 \u0627\u0631\u0632 \u062f\u06cc\u06af\u0631\u06cc \u0628\u0627\u0634\u0646\u062f",
    "note.magnitude_units": "\u062c\u0645\u0639 \u0627\u06cc\u0646 \u062f\u0648\u0631\u0647 {ratio} \u0628\u0631\u0627\u0628\u0631 \u062f\u0648\u0631\u0647\u0654 \u0642\u0628\u0644 \u0627\u0633\u062a: \u0634\u0627\u06cc\u062f {unit} \u0648 {other} \u062c\u0627\u0628\u0647\u200c\u062c\u0627 \u0634\u062f\u0647 \u06cc\u0627 \u06cc\u06a9 \u0635\u0641\u0631 \u062c\u0627 \u0627\u0641\u062a\u0627\u062f\u0647 \u0628\u0627\u0634\u062f",
}


def fields(text):
    return {n for _, n, _, _ in string.Formatter().parse(text) if n}


def test_the_english_templates_hold_both_magnitude_notes_with_the_texts_of_the_brief():
    en = json.loads((ROOT / "templates" / "en.json").read_text("utf-8"))
    assert {k: en.get(k) for k in EN_MAGNITUDE} == EN_MAGNITUDE


def test_the_persian_templates_hold_both_magnitude_notes_with_the_texts_of_the_brief():
    fa = json.loads((ROOT / "templates" / "fa.json").read_text("utf-8"))
    assert {k: fa.get(k) for k in FA_MAGNITUDE} == FA_MAGNITUDE


@pytest.mark.parametrize("f", TEMPLATES, ids=[f.stem for f in TEMPLATES])
def test_every_template_file_holds_both_magnitude_notes_with_the_placeholders_of_the_english_one(f):
    t = json.loads(f.read_text("utf-8"))
    assert {k: fields(t[k]) for k in EN_MAGNITUDE} == {"note.magnitude": {"ratio"},
                                                       "note.magnitude_units": {"ratio", "unit", "other"}}


@pytest.mark.parametrize("f", TEMPLATES, ids=[f.stem for f in TEMPLATES])
def test_no_template_text_names_a_currency_by_a_shipped_word_or_says_toman_rial(f):
    words = {w for t in CURRENCY_WORDS.values() for ws in t.values() for w in ws}
    for key, text in json.loads(f.read_text("utf-8")).items():
        if not isinstance(text, str):
            continue
        folded = text.casefold()
        assert "toman/rial" not in folded, key
        for w in words:
            assert not re.search(r"(?<![^\W\d_])" + re.escape(w) + r"(?![^\W\d_])", folded), (key, w)


def test_the_shipped_templates_pass_the_lint_with_the_two_magnitude_notes():
    from harness_core import lint_text
    assert lint_text.lint() == []


def test_the_note_is_rendered_in_a_non_base_language_with_the_codes_and_the_translated_digits_of_the_ratio(h):
    c = ctx_of(h)
    entry = {"code": "magnitude_units", "params": {"ratio": "10.5", "unit": "QDA", "other": "QDC"}}
    digits = locales.view(c, every=True)["render"]["fa"]["digits"]
    ratio = "10.5".translate(str.maketrans("0123456789", digits))
    want = FA_MAGNITUDE["note.magnitude_units"].format(ratio=ratio, unit="QDA", other="QDC")
    assert "QDA" in want and "QDC" in want and "10" not in want
    assert templates.note(entry, "english text", c, "fa") == want
    plain = {"code": "magnitude", "params": {"ratio": "10.5"}}
    assert templates.note(plain, "english text", c, "fa") == FA_MAGNITUDE["note.magnitude"].format(ratio=ratio)
    assert templates.note(entry, "english text", c, "en") == "english text"


@pytest.mark.parametrize("value,code", [("50000 toman", "IRT"), ("50,000 " + FA_IRR, "IRR"), ("1,000 rials", "IRR")])
def test_harness_normalize_names_the_currency_of_an_amount_with_the_shipped_words_and_no_environment(tmp_path, value,
                                                                                                    code):
    p = subprocess.run([PY, "-I", str(ROOT / "bin" / "harness-normalize"), "--amount", value], env={},
                       cwd=str(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    assert json.loads(p.stdout.decode("utf-8"))["currency"] == code


# --- 14. no code path needs a shipped regional entry ----------------------------------------------------------------


def test_without_the_regional_pack_the_regional_words_name_no_currency_and_the_notes_are_neutral(h, job, tmp_path,
                                                                                               monkeypatch, fresh_table):
    without_ir(tmp_path, monkeypatch)
    assert [L.currency_of(x) for x in ("Amount (Toman)", "Amount (IRT)", "Amount (Rial)", FA_IRR, "5000toman")] == [None] * 5
    assert L.currency_of("Amount (USD)") == "USD" and L.currency_of("Amount (euros)") == "EUR"   # the other packs stay
    assert L.names_currency("paid 5000 toman") is False and L.names_currency("paid 40 euros") is True
    assert money("it came to 5000toman") == []
    for sign in ("$", "\u00a3", "\u00a5"):
        assert claims.claim_type(f"paid {sign}1,500 for lunch") == "price"     # the shipped sign needs no regional entry
    assert claims.claim_type("paid 2,500,000 toman to the supplier") == "default"
    res, codes_ = magnitude(h, job, "Toman")
    assert res["result"] == "fail" and [c["code"] for c in mag_codes(codes_)] == ["magnitude"] and NEUTRAL in res["notes"]


# --- 16. messages ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("unit", ["bad unit!", "u" * 21, "a;b"])
def test_a_bad_unit_of_harness_model_is_refused_with_the_text_that_names_no_currency(h, sim, unit):
    with pytest.raises(Denied) as e:
        model_cmd.apply({"tool": "model", "sub": "assume", "id": "x", "value": 1, "unit": unit, "period": "once",
                         "source": "owner"}, ctx_of(h), {"session_id": SID})
    assert str(e.value) == "--unit is a short unit name (a currency code, ratio, percent, index)"

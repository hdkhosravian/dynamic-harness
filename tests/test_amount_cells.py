"""Amount cells: a currency mark in a cell is read against the column's unit (build phase P1, task T4c).

``localize.parse_amount(value, unit=None, words=None, pairs=None)`` reads a cell that is a number and at most one
currency mark. The mark counts only when, folded, it equals a word of ``words.currency_words``; the result is the
number when the caller names no unit or the mark names the unit, the number converted by the pair that relates
the mark's code to the unit, and None for anything else. ``finance.aggregate`` passes the column's unit.

The mechanism is proved with data that no shipped file holds: the codes ``QDA``, ``QDC`` and ``QDX``, and the words
``zorb``, a yen sign, a Cyrillic abbreviation, a generic currency sign, ``zb.`` and ``new zorb`` (a test below reads the
shipped files and checks it). The shipped IRT/IRR/USD/EUR facts appear only in the tests that pin 0.4.0 behaviour or the
shipped data (items 10, 12, 14). Non-ASCII characters are \\u escapes.

Pair shape: ``{"from": F, "to": T, "factor": n}`` is ``1 F = n T``. A cell in ``T`` under the column unit ``F`` is
divided by ``n``; a cell in ``F`` under the column unit ``T`` is multiplied by it."""

from __future__ import annotations

import csv
import json
import subprocess
import time
from decimal import Decimal
from pathlib import Path

import pytest

from harness_core import business
from harness_core import localize as L
from harness_core.errors import Denied
from test_currencies import check, csv_of, pair
from test_locales import pack, put
from test_p3_hooks import ctx_of, h  # noqa: F401  (fixture h)
from test_run import job  # noqa: F401  (fixture job: sales.csv in the workspace, a compiled task, unit u1)

ROOT = Path(__file__).resolve().parents[1]
PY = "/usr/bin/python3"
ZWNJ = "‌"
YEN = "円"  # a CJK character that is a letter (Lo)
HRN = "грн"  # a Cyrillic abbreviation of three letters
SIGN = "¤"  # the generic currency sign (Sc)
FA_IRT, FA_IRR, FA_EUR = "تومان", "ریال", "یورو"
FA_IRR_ADJ = FA_IRR + "ی"
ARABIC_YEH_RIAL = "ريال"  # the word for a rial with the Arabic yeh
EURO_SIGN = "€"

WORDS = {"QDA": ["zorb", YEN, SIGN, "zb.", "new zorb", "twin"], "QDC": [HRN, "twin"], "QDX": ["xyl"]}
PAIRS = [pair("QDA", "QDC", 100)]  # 1 QDA = 100 QDC
PAIRS_ODD = [pair("QDA", "QDC", 7)]  # 1 QDA = 7 QDC
PAIRS_REVERSED = [pair("QDC", "QDA", 100)]  # 1 QDC = 100 QDA
PAIRS_X = PAIRS + [pair("QDX", "QDA", 7)]  # QDX is related to QDA, never to QDC
PAIRS_CHAIN = PAIRS + [pair("QDC", "QDX", 5)]  # QDA - QDC - QDX: no direct pair between QDA and QDX
D = Decimal


def table(rows):
    """pytest params from ``{id: args}``."""
    return [pytest.param(*(a if isinstance(a, tuple) else (a,)), id=i) for i, a in rows.items()]


def same(got, want):
    """``got`` is None when ``want`` is None, else the Decimal that ``want`` (a string) spells."""
    return got is None if want is None else isinstance(got, Decimal) and got == D(want)


def cell(value, unit=None, pairs=PAIRS):
    return L.parse_amount(value, unit=unit, words=WORDS, pairs=pairs)


@pytest.fixture
def shipped(monkeypatch):
    """The process tables of ``localize`` are "not read yet": the shipped packs answer."""
    for name in ("_currency_words", "_pairs", "_fold"):
        monkeypatch.setattr(L, name, None)


# --- 0. the made-up data is made up ---------------------------------------------------------------------------------

MADE_UP = ["QDA", "QDC", "QDX", "zorb", YEN, HRN, SIGN, "zb.", "new zorb", "twin", "xyl"]


def test_no_shipped_file_holds_a_code_word_or_sign_of_the_mechanism_tests():
    files = sorted((ROOT / "control" / "defaults").rglob("*.json"))
    assert len(files) >= 4
    for f in files:
        text = json.dumps(json.loads(f.read_text("utf-8")), ensure_ascii=False).casefold()
        raw = f.read_text("utf-8").casefold()
        for needle in MADE_UP:
            assert needle.casefold() not in text and needle.casefold() not in raw, (f.name, needle)


# --- 1. no mark: what 0.4.0 gives (computed at e23c0a9, written as literals; green before and after) --------------

NO_MARK = {
    "int": (5, "5"),
    "int-negative": (-7, "-7"),
    "decimal": (D("1.50"), "1.50"),
    "float": (2.5, "2.5"),
    "float-large": (1e20, "1E+20"),
    "bool-true": (True, None),
    "bool-false": (False, None),
    "none": (None, None),
    "plain": ("1000", "1000"),
    "spaces-around": ("  1000  ", "1000"),
    "persian-digits": ("۱۲۳۴", "1234"),
    "arabic-indic-digits": ("١٢٣", "123"),
    "comma-groups": ("1,234,567", "1234567"),
    "arabic-thousands-separator": ("1٬234٬567", "1234567"),
    "arabic-comma-groups": ("1،234", "1234"),
    "persian-digits-with-separator": ("۱٬۵۰۰", "1500"),
    "decimal-point": ("12.5", "12.5"),
    "arabic-decimal-separator": ("1٫5", "1.5"),
    "groups-and-decimal": ("1,234.50", "1234.50"),
    "parentheses": ("(1,000)", "-1000"),
    "trailing-minus": ("1,000-", "-1000"),
    "leading-minus": ("-1000", "-1000"),
    "leading-plus": ("+1000", "1000"),
    "space-inside": ("1 000", "1000"),
    "zwnj-inside": ("1" + ZWNJ + "000", "1000"),
    "nbsp-inside": ("1 000", "1000"),
    "decimal-comma": ("1,5", None),
    "bad-group": ("12,50", None),
    "empty": ("", None),
    "letters-only": ("abc", None),
    "blank": ("   ", None),
    "parentheses-around-minus": ("(-5)", "5"),
    "double-minus": ("--5", None),
    "minus-both-sides": ("-5-", None),
    "parentheses-and-trailing-minus": ("(5)-", None),
    "dot-without-digit": ("5.", None),
    "dot-first": (".5", None),
    "exponent": ("1e3", None),
    "percent": ("12%", None),
    "two-numbers": ("12 34 5.6", "12345.6"),
    "minus-then-space": ("- 5", "-5"),
    "minus-inside-parentheses": ("(5-)", "-5"),
    "zero": ("0", "0"),
    "leading-zeros": ("007", "7"),
    "plus-and-trailing-minus": ("+5-", "-5"),
    "lone-minus": ("-", None),
    "empty-parentheses": ("()", None),
    "space-and-decimal-comma": ("1 234,567", None),
}


def shown(got):
    return None if got is None else str(got)


@pytest.mark.parametrize("value, want", table(NO_MARK))
def test_a_cell_with_no_mark_is_read_as_in_0_4_0_with_one_argument(value, want):
    assert shown(L.parse_amount(value)) == want


@pytest.mark.parametrize("value, want", table(NO_MARK))
def test_a_cell_with_no_mark_is_read_the_same_with_a_unit_a_word_table_and_pairs(value, want):
    for kw in (
        {"unit": "QDA"},
        {"unit": None},
        {"unit": "QDC", "words": WORDS, "pairs": PAIRS},
        {"unit": "QDA", "words": {}, "pairs": []},
    ):
        assert shown(L.parse_amount(value, **kw)) == want, kw


# --- 2. a mark, no unit: the number ---------------------------------------------------------------------------------

POSITIONS = {
    "after": "100{m}",
    "after-space": "100 {m}",
    "before": "{m}100",
    "before-space": "{m} 100",
    "after-zwnj": "100" + ZWNJ + "{m}",
    "before-zwnj": "{m}" + ZWNJ + "100",
}
MARKS = {
    "letters": "zorb",
    "other-script": HRN,
    "sign": SIGN,
    "cjk": YEN,
    "dot": "zb.",
    "space": "new zorb",
    "shared": "twin",
    "other-code": "xyl",
}


def positions(*marks):
    return [pytest.param(POSITIONS[p].format(m=MARKS[m]), id=f"{m}-{p}") for m in marks for p in POSITIONS]


@pytest.mark.parametrize("value", positions(*MARKS))
def test_a_mark_before_or_after_the_number_with_or_without_white_space_leaves_the_number_when_no_unit_is_given(value):
    assert same(cell(value), "100")


@pytest.mark.parametrize(
    "value, want",
    table(
        {
            "upper-case": ("100 ZORB", "100"),
            "title-case": ("100 Zorb", "100"),
            "upper-case-other-script": ("100 ГРН", "100"),
            "upper-case-with-space": ("100 NEW ZORB", "100"),
            "two-spaces-inside-the-word": ("100 new  zorb", "100"),
            "groups": ("1,234.5 zorb", "1234.5"),
            "groups-mark-before": ("zorb 1,234.5", "1234.5"),
            "persian-digits": ("۱۲۳ zorb", "123"),
            "decimal": ("0.25" + SIGN, "0.25"),
            "dot-word-after-decimal": ("2.5zb.", "2.5"),
            "dot-word-before-decimal": ("zb.2.5", "2.5"),
            "spaces-in-the-number": ("1 000 zorb", "1000"),
            "zero": ("0 zorb", "0"),
            "padded": ("  100 zorb  ", "100"),
        }
    ),
)
def test_the_mark_and_the_number_are_split_before_white_space_goes_and_the_mark_is_folded(value, want):
    assert same(cell(value), want)


def test_the_word_table_of_the_call_is_the_whole_table_a_shipped_word_is_not_added_to_it(shipped):
    assert same(L.parse_amount("100 zorb", words={"QDA": ["zorb"]}), "100")
    assert L.parse_amount("100 zorb", words={"QDA": ["other"]}) is None
    assert L.parse_amount("100 zorb", words={}) is None  # {} is a table, it is not "no table"
    assert L.parse_amount("100 toman", words={}) is None  # a shipped word is not added to the table given


# --- 3. a mark equal to the unit: the number ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, unit",
    table(
        {
            "word-after": ("100 zorb", "QDA"),
            "word-before": ("zorb 100", "QDA"),
            "word-glued": ("100zorb", "QDA"),
            "sign": (SIGN + "100", "QDA"),
            "cjk": ("100" + YEN, "QDA"),
            "dot-word": ("100 zb.", "QDA"),
            "word-with-space": ("100 new zorb", "QDA"),
            "upper-case": ("100 ZORB", "QDA"),
            "other-code-word": ("100 " + HRN, "QDC"),
            "other-code-upper-case": ("100 ГРН", "QDC"),
            "third-code": ("100 xyl", "QDX"),
        }
    ),
)
def test_a_mark_that_names_the_unit_leaves_the_number_even_where_a_pair_could_convert_it(value, unit):
    assert same(cell(value, unit), "100")
    assert same(cell(value, unit, pairs=PAIRS_REVERSED), "100")
    assert same(cell(value, unit, pairs=[]), "100")


# --- 4. a mark related to the unit: the number converted ------------------------------------------------------------
# PAIRS: 1 QDA = 100 QDC. A cell in QDC under a QDA column is divided by 100; a cell in QDA under a QDC column is
# multiplied by 100. The conversion is ``to_unit(number, code of the mark, unit)``.

CONVERTED = table(
    {
        "to-unit-under-from-unit": ("500 " + HRN, "QDA", PAIRS, "5"),  # 500 / 100
        "to-unit-under-from-unit-before": (HRN + " 500", "QDA", PAIRS, "5"),
        "from-unit-under-to-unit": ("5 zorb", "QDC", PAIRS, "500"),  # 5 * 100
        "from-unit-under-to-unit-before": ("zorb 5", "QDC", PAIRS, "500"),
        "fraction": ("5 " + HRN, "QDA", PAIRS, "0.05"),  # 5 / 100
        "fraction-glued": ("5" + HRN, "QDA", PAIRS, "0.05"),
        "one-from-unit": ("1 zorb", "QDC", PAIRS, "100"),
        "decimal-number": ("2.5 zorb", "QDC", PAIRS, "250"),  # 2.5 * 100
        "groups": ("1,250,000 " + HRN, "QDA", PAIRS, "12500"),  # 1250000 / 100
        "dot-word": ("500 zb.", "QDC", PAIRS, "50000"),  # 500 * 100
        "word-with-space": ("3 new zorb", "QDC", PAIRS, "300"),  # 3 * 100
        "sign": (SIGN + "7", "QDC", PAIRS, "700"),  # 7 * 100
        "cjk": ("7" + YEN, "QDC", PAIRS, "700"),
        "upper-case": ("500 ГРН", "QDA", PAIRS, "5"),
        "reversed-pair-from-unit": ("500 zorb", "QDC", PAIRS_REVERSED, "5"),  # 1 QDC = 100 QDA: 500 / 100
        "reversed-pair-to-unit": ("5 " + HRN, "QDA", PAIRS_REVERSED, "500"),  # 5 * 100
        "factor-seven-down": ("14 " + HRN, "QDA", PAIRS_ODD, "2"),  # 14 / 7
        "factor-seven-up": ("2 zorb", "QDC", PAIRS_ODD, "14"),  # 2 * 7
        "pair-among-others": ("700 zorb", "QDX", PAIRS_X, "100"),  # 1 QDX = 7 QDA: 700 / 7
        "negative-parentheses": ("(500 " + HRN + ")", "QDA", PAIRS, "-5"),
        "negative-trailing": ("500 " + HRN + "-", "QDA", PAIRS, "-5"),
        "negative-leading": ("-500 " + HRN, "QDA", PAIRS, "-5"),
        "negative-sign-after-mark": (HRN + "-500", "QDA", PAIRS, "-5"),
        "zero": ("0 zorb", "QDC", PAIRS, "0"),
    }
)


@pytest.mark.parametrize("value, unit, pairs, want", CONVERTED)
def test_a_mark_that_a_pair_relates_to_the_unit_converts_the_number(value, unit, pairs, want):
    assert same(cell(value, unit, pairs), want)


def test_the_pairs_of_the_call_are_the_whole_table_and_with_none_the_process_table_answers(shipped, monkeypatch):
    assert cell("500 " + HRN, "QDA", pairs=[]) is None
    monkeypatch.setattr(L, "_pairs", PAIRS)
    assert same(L.parse_amount("500 " + HRN, unit="QDA", words=WORDS), "5")
    assert L.parse_amount("500 " + HRN, unit="QDA", words=WORDS, pairs=[]) is None


# --- 5. a mark with no relation to the unit: None -------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, unit, pairs",
    table(
        {
            "no-pair-at-all": ("100 zorb", "QDX", PAIRS),
            "other-direction": ("100 " + HRN, "QDX", PAIRS),
            "the-third-code-mark": ("100 xyl", "QDA", PAIRS),
            "the-third-code-mark-under-the-other": ("100 xyl", "QDC", PAIRS),
            "unit-is-in-no-table": ("100 zorb", "QDZ", PAIRS),
            "empty-pairs": ("100 zorb", "QDC", []),
            "empty-pairs-other-direction": ("100 " + HRN, "QDA", []),
            "a-chain-is-not-a-relation": ("100 zorb", "QDX", PAIRS_CHAIN),
            "a-chain-is-not-a-relation-reversed": ("100 xyl", "QDA", PAIRS_CHAIN),
            "sign": (SIGN + "100", "QDX", PAIRS),
            "negative": ("(100 zorb)", "QDX", PAIRS),
            "unit-in-another-letter-case": ("100 zorb", "qda", PAIRS),
        }
    ),
)
def test_a_mark_that_names_a_currency_with_no_relation_to_the_unit_makes_the_cell_unreadable(value, unit, pairs):
    assert cell(value, unit, pairs) is None


# --- 6. no word of the table: None ----------------------------------------------------------------------------------

NOT_A_WORD = table(
    {
        "the-word-and-a-letter": "100 zorbx",
        "a-letter-and-the-word": "100 xzorb",
        "the-word-glued-to-a-letter": "100zorbx",
        "a-plural-the-table-does-not-list": "100 zorbs",
        "a-listed-word-and-a-dot": "100 zorb.",
        "a-dotted-word-without-its-dot": "100 zb",
        "a-dotted-word-and-two-dots": "100 zb..",
        "a-prefix-of-a-word-with-a-space": "100 new",
        "a-sign-and-a-letter": "100" + SIGN + "x",
        "a-cjk-word-and-a-letter": "100" + YEN + "x",
        "a-cyrillic-word-and-a-letter": "100 " + HRN + "a",
        "a-word-inside-a-longer-mark": "100 pay zorb",
        "a-word-and-a-second-word": "100 zorb apples",
        "a-second-word-and-a-word": "100 apples zorb",
        "the-word-twice-after": "100 zorb zorb",
        "the-word-twice-before": "zorb zorb 100",
        "two-words-of-two-codes": "100 zorb " + HRN,
        "an-unlisted-word": "100 apples",
        "an-unlisted-word-before": "apples 100",
        "a-word-and-a-second-number": "100 zorb 5",
        "a-word-between-two-numbers": "5 zorb 100",
        "a-word-and-a-minus-inside": "100 zo-rb",
        "a-word-only": "zorb",
        "a-sign-only": SIGN,
        "two-signs": SIGN + SIGN + "100",
    }
)


@pytest.mark.parametrize("value", NOT_A_WORD)
def test_text_beside_the_number_that_is_no_word_of_the_table_makes_the_cell_unreadable(value):
    for unit in (None, "QDA", "QDC"):
        assert cell(value, unit) is None, unit


def test_a_mark_is_compared_whole_never_searched_for_a_word_inside_it():
    words = {"QDA": ["usd"]}  # 100 usdt holds no mark of usd (the same shape as 100 usdt of a shipped word)
    for value in ("100 usdt", "100 xusd", "100 usd1x", "100 my usd", "100 usd usd", "100 usd."):
        assert L.parse_amount(value, words=words) is None, value
        assert L.parse_amount(value, unit="QDA", words=words, pairs=[]) is None, value
    assert same(L.parse_amount("100 usd", words=words), "100")


# --- 7. a word under two codes --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["100 twin", "twin 100", "100twin", "100 TWIN", "(100 twin)"],
    ids=["after", "before", "glued", "upper-case", "negative"],
)
def test_a_word_listed_under_two_codes_reads_in_a_unit_that_is_one_of_them_and_when_no_unit_is_given(value):
    sign = -1 if value.startswith("(") else 1
    for unit in (None, "QDA", "QDC"):
        assert same(cell(value, unit), str(100 * sign)), unit
        assert same(cell(value, unit, pairs=[]), str(100 * sign)), unit


@pytest.mark.parametrize(
    "pairs",
    [PAIRS, PAIRS_X, PAIRS_CHAIN, [], PAIRS_REVERSED],
    ids=["no-pair", "pair-to-one", "chain", "none", "reversed"],
)
def test_a_word_listed_under_two_codes_in_a_third_unit_is_unreadable_also_when_a_pair_relates_the_unit(pairs):
    assert cell("100 twin", "QDX", pairs) is None
    assert cell("100 twin", "QDZ", pairs) is None


def test_a_pair_that_would_convert_a_one_code_mark_does_not_convert_a_two_code_mark():
    assert same(cell("700 zorb", "QDX", PAIRS_X), "100")  # control: zorb names QDA alone, 1 QDX = 7 QDA
    assert cell("700 twin", "QDX", PAIRS_X) is None


# --- 8. two marks: None ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    table(
        {
            "same-word": "zorb 100 zorb",
            "same-word-glued": "zorb100zorb",
            "same-code-two-words": "zorb 100 zb.",
            "same-code-word-and-sign": SIGN + "100 zorb",
            "same-code-signs": SIGN + "100" + SIGN,
            "two-codes": "zorb 100 " + HRN,
            "two-codes-reversed": HRN + " 100 zorb",
            "word-and-shared": "twin 100 zorb",
            "shared-twice": "twin 100 twin",
            "negative": "(zorb 100 zorb)",
            "trailing-minus": "zorb 100 zorb-",
            "dotted-word-twice": "zb. 100 zb.",
        }
    ),
)
def test_a_mark_on_both_sides_of_the_number_makes_the_cell_unreadable_also_when_both_name_the_same_code(value):
    for unit in (None, "QDA", "QDC", "QDX"):
        assert cell(value, unit) is None, unit


# --- 9. the negative forms with a mark ------------------------------------------------------------------------------

NEGATIVE = {
    "parentheses-mark-after": "(100 {m})",
    "parentheses-mark-after-glued": "(100{m})",
    "parentheses-mark-before": "({m} 100)",
    "parentheses-mark-before-glued": "({m}100)",
    "trailing-minus-mark-after": "100 {m}-",
    "trailing-minus-mark-after-glued": "100{m}-",
    "trailing-minus-mark-before": "{m} 100-",
    "trailing-minus-mark-before-glued": "{m}100-",
    "leading-minus-mark-after": "-100 {m}",
    "leading-minus-mark-after-glued": "-100{m}",
    "sign-directly-before-the-number": "{m}-100",
}
NEGATIVE_MARKS = {"letters": "zorb", "sign": SIGN, "other-script": HRN}


def negatives(rows):
    return [pytest.param(t.format(m=NEGATIVE_MARKS[m]), id=f"{m}-{i}") for m in NEGATIVE_MARKS for i, t in rows.items()]


@pytest.mark.parametrize("value", negatives(NEGATIVE))
def test_each_negative_form_holds_around_a_cell_with_a_mark(value):
    assert same(cell(value), "-100")


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(NEGATIVE[k].format(m="zorb"), id=k)
        for k in (
            "parentheses-mark-after",
            "trailing-minus-mark-before",
            "leading-minus-mark-after",
            "sign-directly-before-the-number",
        )
    ],
)
def test_each_negative_form_with_a_mark_of_the_unit_is_the_negative_number(value):
    assert same(cell(value, "QDA"), "-100")


def test_a_leading_plus_with_a_mark_is_the_number():
    assert same(cell("+100 zorb"), "100") and same(cell("+100 zorb", "QDA"), "100")


@pytest.mark.parametrize(
    "value",
    table(
        {
            "minus-both-ends": "-100 zorb-",
            "double-leading-minus": "--100 zorb",
            "double-minus-before-the-number": "zorb--100",
            "parentheses-and-trailing-minus": "(100 zorb)-",
            "parentheses-and-trailing-minus-mark-before": "(zorb 100)-",
            "unclosed-parenthesis": "(100 zorb",
            "a-mark-and-a-lone-minus": "zorb -",
            "a-mark-and-no-number": "(zorb)",
        }
    ),
)
def test_a_cell_that_is_unreadable_today_because_of_where_its_sign_stands_stays_unreadable_with_a_mark(value):
    assert cell(value) is None and cell(value, "QDA") is None


# --- 9b. the sign of a cell with a mark: outer and inner places (ruling of the controller, brief R2 item 6) -----------
# A cell with a mark has two places for a sign: outer (around the whole cell) and inner (on the number part, read by
# the grammar of 0.4.0). One negative form in one place is negative; a negative form in both places is None; a leading
# plus changes nothing.

SIGN_NEGATIVE = {
    "outer-leading-minus-mark-before": "-W 100",
    "outer-leading-minus-mark-before-glued": "-W100",
    "outer-parentheses-mark-before": "(W 100)",
    "outer-trailing-minus-mark-before": "W 100-",
    "outer-trailing-minus-mark-after": "100 W-",
    "outer-parentheses-mark-after": "(100 W)",
    "outer-leading-minus-mark-after": "-100 W",
    "inner-leading-minus-spaced": "W -100",
    "inner-leading-minus-glued": "W-100",
    "inner-parentheses": "W (100)",
    "inner-trailing-minus": "100- W",
    "inner-parentheses-mark-after": "(100) W",
    # second ruling: a plus is no negative form, so the parentheses are the one form
    "outer-parentheses-and-outer-plus": "(+W 100)",
    "outer-parentheses-and-inner-plus": "(W +100)",
    "outer-parentheses-and-inner-plus-mark-after": "(+100 W)",
}
SIGN_POSITIVE = {
    "outer-plus-mark-before": "+W 100",
    "inner-plus-mark-before": "W +100",
    "outer-plus-mark-after": "+100 W",
}
SIGN_NONE = {
    "outer-minus-and-trailing-minus": "-W 100-",
    "outer-minus-and-inner-minus": "-W -100",
    "outer-parentheses-and-inner-minus": "(W -100)",
    "outer-parentheses-and-inner-minus-mark-after": "(-100 W)",
    "outer-minus-and-outer-trailing-minus": "-100 W-",
    "outer-parentheses-and-inner-parentheses": "(W (100))",
    # second ruling: at most one negative form, wherever the forms stand
    "parentheses-and-leading-minus": "(-W 100)",
    "parentheses-and-trailing-minus-mark-before": "(W 100-)",
    "parentheses-and-trailing-minus-mark-after": "(100 W-)",
    "parentheses-and-minus-after-the-number": "(100- W)",
    "mark-and-parentheses-around-a-negative-number": "W (-100)",
    "parentheses-around-a-negative-number-and-mark": "(-100) W",
    "mark-and-parentheses-around-a-number-with-trailing-minus": "W (100-)",
    "parentheses-around-a-number-with-trailing-minus-and-mark": "(100-) W",
    "mark-and-minus-on-both-sides-of-the-number": "W -100-",
    "minus-on-both-sides-of-the-number-and-mark": "-100- W",
}
SIGN_MARKS = {"letters": ("zorb", "QDA"), "sign": (SIGN, "QDA"), "other-script": (HRN, "QDC")}


def sign_cells(rows):
    return [pytest.param(t.replace("W", m), id=f"{k}-{i}") for k, (m, _) in SIGN_MARKS.items() for i, t in rows.items()]


@pytest.mark.parametrize("value", sign_cells(SIGN_NEGATIVE))
def test_one_negative_form_in_one_place_of_a_cell_with_a_mark_makes_the_number_negative(value):
    assert same(cell(value), "-100")


@pytest.mark.parametrize("value", sign_cells(SIGN_POSITIVE))
def test_a_leading_plus_in_either_place_of_a_cell_with_a_mark_changes_nothing(value):
    assert same(cell(value), "100")


@pytest.mark.parametrize("value", sign_cells(SIGN_NONE))
def test_a_negative_form_in_both_places_of_a_cell_with_a_mark_is_unreadable_never_a_double_negation(value):
    assert cell(value) is None


SIGN_SUBSET = (
    "outer-parentheses-mark-before",
    "inner-leading-minus-spaced",
    "inner-trailing-minus",
    "outer-plus-mark-before",
)


@pytest.mark.parametrize("name", SIGN_SUBSET)
def test_the_sign_table_holds_for_a_mark_that_names_the_unit(name):
    t = {**SIGN_NEGATIVE, **SIGN_POSITIVE}[name]
    assert same(cell(t.replace("W", "zorb"), "QDA"), "100" if name in SIGN_POSITIVE else "-100")


@pytest.mark.parametrize("name", ["outer-minus-and-inner-minus", "outer-parentheses-and-inner-parentheses"])
def test_a_negative_form_in_both_places_is_unreadable_also_for_a_mark_that_names_the_unit(name):
    assert cell(SIGN_NONE[name].replace("W", "zorb"), "QDA") is None


# a cell in QDC under a QDA column: 500 QDC = 5 QDA, the sign applies to the converted number; 5 QDA under QDC = 500
SIGN_CONVERTED = table(
    {
        "outer-parentheses": ("(500 W)", "QDA", "-5"),
        "outer-leading-minus-mark-before": ("-W 500", "QDA", "-5"),
        "inner-leading-minus": ("W -500", "QDA", "-5"),
        "inner-parentheses": ("W (500)", "QDA", "-5"),
        "inner-trailing-minus": ("500- W", "QDA", "-5"),
        "outer-plus": ("+500 W", "QDA", "5"),
        "both-places-outer-and-inner": ("-W -500", "QDA", None),
        "both-places-parentheses-and-minus": ("(-500 W)", "QDA", None),
    }
)


@pytest.mark.parametrize("value, unit, want", SIGN_CONVERTED)
def test_the_sign_of_a_cell_in_the_related_unit_applies_to_the_converted_number(value, unit, want):
    assert same(cell(value.replace("W", HRN), unit), want)


@pytest.mark.parametrize(
    "value, want",
    [("(5 W)", "-500"), ("W -5", "-500"), ("5- W", "-500"), ("+5 W", "500"), ("-W -5", None)],
    ids=["outer", "inner", "inner-trailing", "plus", "both"],
)
def test_the_sign_of_a_cell_in_the_from_unit_applies_to_the_converted_number(value, want):
    assert same(cell(value.replace("W", "zorb"), "QDC"), want)


# the cells of 0.4.0 that hold no mark are not touched by the ruling (computed at e23c0a9; a double negation stays)
NO_MARK_SIGNS = table(
    {
        "parentheses-around-minus": ("(-100)", "100"),
        "minus-both-ends": ("-100-", None),
        "parentheses-and-trailing-minus": ("(100)-", None),
        "plus": ("+100", "100"),
        "parentheses-around-plus": ("(+100)", "-100"),
        "parentheses-around-trailing-minus": ("(100-)", "-100"),
        "plus-and-trailing-minus": ("+100-", "-100"),
        "minus-then-plus": ("-+100", None),
        "minus-then-parentheses": ("-(100)", None),
    }
)


@pytest.mark.parametrize("value, want", NO_MARK_SIGNS)
def test_a_cell_with_no_mark_keeps_the_sign_it_has_in_0_4_0_with_one_argument(value, want):
    assert shown(L.parse_amount(value)) == want


@pytest.mark.parametrize("value, want", NO_MARK_SIGNS)
def test_a_cell_with_no_mark_keeps_the_sign_it_has_in_0_4_0_with_a_unit_a_word_table_and_pairs(value, want):
    assert shown(L.parse_amount(value, unit="QDA", words=WORDS, pairs=PAIRS)) == want


@pytest.mark.parametrize(
    "value",
    ["(-100) rials", "(100-) rials"],
    ids=["parentheses-around-a-negative", "parentheses-around-a-trailing-minus"],
)
@pytest.mark.parametrize("unit", [None, "IRR"], ids=["no-unit", "own-unit"])
def test_two_negative_forms_in_a_cell_with_a_shipped_word_make_it_unreadable_unlike_0_4_0(shipped, value, unit):
    # 0.4.0 gave 100 for (-100) rials and -100 for (100-) rials (the strip left the number part to the old grammar)
    assert L.parse_amount(value, unit=unit) is None


# --- 9c. the read of one cell takes time linear in its length (ruling: item 7) ---------------------------------------

N = 20000


def linear_cells():
    """(id, cell, result) of shapes that make a backtracking split quadratic. ``x`` is no word of the table; the
    result follows from the rule: text beside a number that is no word, or a mark on both sides, is unreadable."""
    for end in ("x", "zorb"):
        yield f"digits-{end}", end + "1" * N + "x", None  # a mark on both sides
        yield f"digits-both-sides-{end}", "1" * N + end + "1" * N + "x", None  # digits on both sides of the text
        yield f"spaces-{end}", end + " " * N + "x", None  # no number at all
        yield f"nested-signs-{end}", end + "-(" * N + "x", None
    # cells that do reach the word lookup and are numbers
    yield "word-then-digits", "zorb" + "1" * N, D("1" * N)
    yield "digits-then-word", "1" * N + " zorb", D("1" * N)
    yield "word-spaces-digit", "zorb" + " " * N + "1", D(1)
    yield "digit-spaces-word", "1" + " " * N + "zorb", D(1)
    yield "word-then-negative", "zorb-1" + " " * N + "1", D(-11)  # white space inside a number goes


def test_a_cell_of_twenty_thousand_characters_is_read_in_linear_time():
    cells = list(linear_cells())
    t0 = time.perf_counter()
    got = [(i, L.parse_amount(c, None, WORDS)) for i, c, _ in cells]
    spent = time.perf_counter() - t0
    assert [(i, r) for i, r in got] == [(i, want) for i, _, want in cells]
    # a linear read takes milliseconds; the quadratic split took seconds per shape. 2 s leaves a slow machine room.
    assert spent < 2.0, f"{spent:.2f} s"


# --- 9d. a new word table or letter table is read at once (ruling: item 8) -------------------------------------------

FRESH = """
import gc, json, sys
sys.path.insert(0, %r)
from harness_core import localize as L

out = {}
L.use_fold({})
L.use_currency_words({"QDA": ["zorb"]})
out["first"] = [str(L.parse_amount("5 zorb", unit="QDA")), str(L.parse_amount("5 zorb", unit="QDC", pairs=[]))]
L.use_currency_words({"QDC": ["zorb"], "QDA": ["blip"]})
out["table_changed"] = [str(L.parse_amount("5 zorb", unit="QDA", pairs=[])), str(L.parse_amount("5 zorb", unit="QDC")),
                        str(L.parse_amount("5 blip", unit="QDA"))]
L.use_currency_words({"QDA": ["zorb"]})
out["table_back"] = str(L.parse_amount("5 zorb", unit="QDA"))

word = "ab\u00e9"
L.use_currency_words({"QDA": [word]})
L.use_fold({})
out["fold_a"] = [str(L.parse_amount("5 abe", unit="QDA", pairs=[])), str(L.parse_amount("5 " + word, unit="QDA"))]
L.use_fold({"letters": {"\u00e9": "e"}})
out["fold_b"] = [str(L.parse_amount("5 abe", unit="QDA", pairs=[])), str(L.parse_amount("5 " + word, unit="QDA"))]
L.use_fold({})
out["fold_a_again"] = str(L.parse_amount("5 abe", unit="QDA", pairs=[]))

rows, before = [], "none"
for i in range(60):
    word = "w" + "".join(chr(97 + int(d)) for d in str(i))   # letters only: a digit in the mark would be a number
    table = {"QDA": [word]}   # a new object each round; the one before is unreferenced
    rows.append([str(L.parse_amount("5 " + word, unit="QDA", words=table, pairs=[])),
                 str(L.parse_amount("5 " + before, unit="QDA", words=table, pairs=[]))])
    before, table = word, None
    gc.collect()
out["objects"] = rows
print(json.dumps(out))
""" % str(ROOT / "lib")


def test_a_new_word_table_a_new_letter_table_and_a_new_words_argument_each_give_their_own_result(tmp_path):
    script = tmp_path / "check_fresh.py"
    script.write_text(FRESH, "utf-8")
    p = subprocess.run(
        [PY, "-I", str(script)], env={}, cwd=str(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120
    )
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    got = json.loads(p.stdout.decode("utf-8"))
    assert got["first"] == ["5", "None"]  # zorb names QDA; under QDC no pair relates it
    assert got["table_changed"] == ["None", "5", "5"]  # zorb now names QDC; blip names QDA
    assert got["table_back"] == "5"
    assert got["fold_a"] == ["None", "5"]  # the word keeps its accent; the cell without it is no word
    assert got["fold_b"] == ["5", "5"]  # both fold to abe
    assert got["fold_a_again"] == "None"
    assert got["objects"] == [["5", "None"]] * 60


# --- 10. the shipped packs: the 0.4.0 pins, and the same rule for the shipped words ----------------------------------

SIX_WORDS_NO_UNIT = table(
    {  # the figures of e23c0a9 (tests/test_localize.py and t4-facts.md section Q2)
        "toman": ("1000 Toman", "1000"),
        "toman-glued": ("1000toman", "1000"),
        "irt": ("1,000 IRT", "1000"),
        "rials": ("100 rials", "100"),
        "rial": ("100 rial", "100"),
        "irr": ("100 irr", "100"),
        "persian-rial": ("50,000 " + FA_IRR, "50000"),
        "persian-toman-persian-digits": ("۱٬۵۰۰ " + FA_IRT, "1500"),
        "upper-case": ("100 TOMAN", "100"),
        "title-case": ("100 Rials", "100"),
        "upper-irr": ("100 IRR", "100"),
        "space-in-the-number": ("1 000 toman", "1000"),
        "zwnj-around": ("1" + ZWNJ + "000" + ZWNJ + FA_IRT, "1000"),
        "zwnj-before-the-word": ("100 " + ZWNJ + FA_IRT, "100"),
        "leading-minus": ("-100 toman", "-100"),
        "leading-plus": ("+100 toman", "100"),
        "parentheses": ("(100 rials)", "-100"),
        "parentheses-groups": ("(1,000 toman)", "-1000"),
        "trailing-minus": ("100 rials-", "-100"),
        "no-number": ("toman", None),
        "dollar-sign": ("$100", None),
        "not-a-currency-word": ("100 apples", None),
        "word-and-a-letter": ("100 irrx", None),
        "a-letter-and-the-word": ("100 dirt", None),
        "the-word-inside-trial": ("100 trial", None),
        "percent-sign": ("12٪", None),
        "two-words": ("100 toman rials", None),
        "decimal-comma-and-word": ("1,5 toman", None),
        "bad-group-and-word": ("12,50 toman", None),
        "minus-both-ends": ("-100 rials-", None),
        "parentheses-and-trailing-minus": ("(100 rials)-", None),
        "double-minus": ("--100 toman", None),
    }
)


@pytest.mark.parametrize("value, want", SIX_WORDS_NO_UNIT)
def test_a_cell_with_a_word_of_the_six_of_0_4_0_gives_its_0_4_0_figure_with_one_argument(shipped, value, want):
    assert shown(L.parse_amount(value)) == want


@pytest.mark.parametrize("value, want", SIX_WORDS_NO_UNIT)
def test_a_cell_with_a_word_of_the_six_of_0_4_0_gives_its_0_4_0_figure_when_the_unit_is_none(shipped, value, want):
    assert shown(L.parse_amount(value, unit=None)) == want


@pytest.mark.parametrize(
    "value, unit, want",
    table(
        {
            # a Rial word under a Toman column is divided by 10 (0.4.0 read the cell as it stands: 10 times too large)
            "rials-under-toman": ("100 rials", "IRT", "10"),
            "rial-under-toman": ("100 rial", "IRT", "10"),
            "irr-under-toman": ("100 irr", "IRT", "10"),
            "persian-rial-under-toman": ("50,000 " + FA_IRR, "IRT", "5000"),
            "irr-groups-under-toman": ("1,000 IRR", "IRT", "100"),
            "persian-rial-adjective-under-toman": ("100 " + FA_IRR_ADJ, "IRT", "10"),
            "rials-before-the-number": ("IRR 100", "IRT", "10"),
            "negative-rials-under-toman": ("(100 rials)", "IRT", "-10"),
            "arabic-yeh-rial-under-toman": ("50,000 " + ARABIC_YEH_RIAL, "IRT", "5000"),
            # a Toman word under a Rial column is multiplied by 10 (0.4.0: 10 times too small)
            "toman-under-rial": ("100 toman", "IRR", "1000"),
            "tomans-under-rial": ("100 tomans", "IRR", "1000"),
            "persian-toman-under-rial": ("100 " + FA_IRT, "IRR", "1000"),
            "persian-toman-digits-under-rial": ("۱٬۵۰۰ " + FA_IRT, "IRR", "15000"),
            "irt-under-rial": ("1,000 IRT", "IRR", "10000"),
            "toman-before-the-number": ("toman 100", "IRR", "1000"),
            "negative-toman-under-rial": ("100 toman-", "IRR", "-1000"),
            # the column's own unit
            "toman-under-toman": ("100 toman", "IRT", "100"),
            "irt-under-toman": ("100 IRT", "IRT", "100"),
            "persian-toman-under-toman": ("100 " + FA_IRT, "IRT", "100"),
            "rials-under-rial": ("100 rials", "IRR", "100"),
            "persian-rial-under-rial": ("100 " + FA_IRR, "IRR", "100"),
            "usd-under-usd": ("100 USD", "USD", "100"),
            "euros-under-eur": ("100 euros", "EUR", "100"),
            "persian-euro-under-eur": ("100 " + FA_EUR, "EUR", "100"),
            # a word of another shipped currency, no relation
            "usd-under-toman": ("100 usd", "IRT", None),
            "usd-under-rial": ("100 usd", "IRR", None),
            "eur-under-toman": ("100 EUR", "IRT", None),
            "euros-under-rial": ("100 euros", "IRR", None),
            "persian-euro-under-toman": ("100 " + FA_EUR, "IRT", None),
            "usd-under-eur": ("100 usd", "EUR", None),
            "eur-under-usd": ("100 eur", "USD", None),
            "toman-under-usd": ("100 toman", "USD", None),
            "rials-under-eur": ("100 rials", "EUR", None),
            # the one shipped sign
            "euro-sign-under-eur": (EURO_SIGN + "100", "EUR", "100"),
            "euro-sign-after-under-eur": ("100" + EURO_SIGN, "EUR", "100"),
            "euro-sign-under-usd": (EURO_SIGN + "100", "USD", None),
            "euro-sign-under-toman": (EURO_SIGN + "100", "IRT", None),
            "euro-sign-under-rial": ("100 " + EURO_SIGN, "IRR", None),
            # no word, no mark
            "dollar-sign-is-no-shipped-word": ("$100", "USD", None),
            "dollar-sign-under-toman": ("$100", "IRT", None),
            "text-is-no-word": ("100 apples", "IRT", None),
            "the-word-and-a-letter": ("100 irrx", "IRR", None),
            "two-marks": ("irt 100 irr", "IRT", None),
            "two-marks-of-one-code": ("rials 100 irr", "IRR", None),
        }
    ),
)
def test_a_shipped_word_is_read_against_the_unit_of_the_column(shipped, value, unit, want):
    assert same(L.parse_amount(value, unit=unit), want)


@pytest.mark.parametrize(
    "value, want",
    table(
        {
            # 0.4.0 gave None for every one of these: the strip knew six words and read them at the end only
            "word-before": ("IRT 100", "100"),
            "word-before-spaced": ("toman 100", "100"),
            "usd": ("100 USD", "100"),
            "usd-before": ("usd 100", "100"),
            "euros": ("100 euros", "100"),
            "eur": ("100 EUR", "100"),
            "euro-sign": (EURO_SIGN + "100", "100"),
            "euro-sign-after": ("100 " + EURO_SIGN, "100"),
            "persian-euro": ("100 " + FA_EUR, "100"),
            "plural-toman": ("100 tomans", "100"),
            "persian-rial-adjective": ("100 " + FA_IRR_ADJ, "100"),
            # 0.4.0: None for both (the strip knew the word at the end only); the sign table of item 9 reads them now
            "inner-trailing-minus-before-the-word": ("100- rials", "-100"),
            "inner-parentheses-before-the-word": ("(1,000) rials", "-1000"),
            "arabic-yeh-rial": ("50,000 " + ARABIC_YEH_RIAL, "50000"),  # the mark is folded as the table is
            "negative-before": ("(usd 100)", "-100"),
        }
    ),
)
def test_every_word_of_the_shipped_table_is_read_in_a_cell_when_no_unit_is_given(shipped, value, want):
    assert same(L.parse_amount(value), want)


# --- 11. aggregate in a sandboxed check, made-up codes in the request -------------------------------------------------

AGG = """
import sys
sys.path.insert(0, %r)
from harness_core import checklib, finance

def fn(req):
    agg = finance.aggregate(req["path"], req.get("args", {}), req.get("profile", {}))
    out = {k: None if agg[k] is None else str(agg[k]) for k in ("total", "grand", "partition", "file_total")}
    out.update(unparsable=agg["stats"]["unparsable"], rows_used=agg["stats"]["rows_used"], unit_in=agg["unit_in"],
               unit=agg["unit"], transfers=agg["stats"]["transfers"])
    return "pass", out, []

checklib.main(fn)
""" % str(ROOT / "lib")

LIVE = {"fold": {}, "currency_pairs": PAIRS, "currency_words": WORDS}


def aggregate_process(tmp_path, header, cells, locale=LIVE, profile=None, args=None):
    """``finance.aggregate`` on a CSV of date, description and one amount column, in ``/usr/bin/python3 -I`` with an
    empty environment and the request on stdin. ``cells`` are the amounts; a ``("total", amount)`` is a total row."""
    path = tmp_path / "cells.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "description", header])
        for i, c in enumerate(cells, 1):
            w.writerow(["", "total", c[1]] if isinstance(c, tuple) else [f"1402/12/{i:02d}", f"invoice {i}", c])
    script = tmp_path / "check_aggregate.py"
    script.write_text(AGG, "utf-8")
    req = {"path": str(path), "profile": profile or {}, "args": args or {}}
    if locale is not None:
        req["locale"] = locale
    return subprocess.run(
        [PY, "-I", str(script)],
        input=json.dumps(req).encode("utf-8"),
        env={},
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )


def aggregated(tmp_path, *a, **kw):
    p = aggregate_process(tmp_path, *a, **kw)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["values"]


def totals(v):
    return {k: None if v[k] is None else D(v[k]) for k in ("total", "grand", "partition", "file_total")}


# A column in QDA (the header names it): 1000, 500 in the column's unit, 30000 QDC (= 300 QDA), 70 of an unrelated
# currency (unreadable), (100 QDA) = -100, and a total row. 1000 + 500 + 300 - 100 = 1700.
ROWS = ["1000", "500 zorb", "30000 " + HRN, "70 xyl", "(100 zorb)"]


def test_a_cell_in_the_unit_with_a_mark_a_cell_in_the_related_unit_and_a_cell_in_an_unrelated_currency(tmp_path):
    v = aggregated(tmp_path, "amount (zorb)", ROWS + [("total", "1700 zorb")])
    assert totals(v) == {"total": 1700, "grand": 1700, "partition": 1700, "file_total": 1700}
    assert v["unparsable"] == 1 and v["rows_used"] == 4 and v["unit_in"] == "QDA" and v["unit"] == "QDA"


def test_the_total_row_of_the_file_is_read_the_way_a_row_is(tmp_path):
    for total in ("1700", "1700 zorb", "170000 " + HRN, SIGN + "1700"):  # 170000 QDC is 1700 QDA
        v = aggregated(tmp_path, "amount (zorb)", ROWS + [("total", total)])
        assert totals(v)["file_total"] == 1700 and totals(v)["total"] == 1700, total
    v = aggregated(tmp_path, "amount (zorb)", ROWS + [("total", "1700 xyl")])  # unreadable: it is no total row
    assert v["file_total"] is None and totals(v)["total"] == 1700


def test_the_column_is_converted_into_the_profile_currency_after_each_cell_is_read_in_the_column_unit(tmp_path):
    # the column is QDA, the profile currency QDC: every cell goes QDA -> QDC (x 100) once it is in QDA
    v = aggregated(tmp_path, "amount (zorb)", ROWS + [("total", "170000 " + HRN)], profile={"currency": "QDC"})
    assert totals(v) == {"total": 170000, "grand": 170000, "partition": 170000, "file_total": 170000}
    assert v["unparsable"] == 1 and v["unit_in"] == "QDA" and v["unit"] == "QDC"


def test_a_column_in_the_to_unit_reads_a_from_unit_cell_multiplied(tmp_path):
    # 1000 QDC, 2 QDA = 200 QDC, 300 QDC: 1500
    v = aggregated(tmp_path, "amount (" + HRN + ")", ["1000", "2 zorb", "300 " + HRN, "9 xyl"], profile={})
    assert totals(v)["total"] == 1500 and totals(v)["grand"] == 1500 and totals(v)["partition"] == 1500
    assert v["unparsable"] == 1 and v["unit_in"] == "QDC"


def test_a_cell_with_a_mark_in_a_column_whose_header_names_no_currency_reads_in_the_unit_of_the_profile(tmp_path):
    # the header names no unit: the unit is the profile currency QDA; 400 QDC is 4 QDA
    v = aggregated(tmp_path, "amount", ["1000", "400 " + HRN, "50 zorb", "9 xyl"], profile={"currency": "QDA"})
    assert totals(v)["total"] == 1054 and v["unparsable"] == 1 and v["unit_in"] == "QDA"
    assert totals(v)["grand"] == totals(v)["partition"] == 1054


def test_only_plain_cells_give_the_sum_as_before(tmp_path):
    v = aggregated(tmp_path, "amount (zorb)", ["1,000", "(250)", "300.5"])
    assert totals(v)["total"] == D("1050.5") and v["unparsable"] == 0 and totals(v)["grand"] == totals(v)["partition"]


def test_a_word_and_a_pair_that_only_a_live_pack_holds_change_the_figure_of_the_finance_check(h, job):
    """The channel of a real check: a live pack under the data root, ``run.apply``, the sandbox."""
    t_cells = pack("t-cells", words={"currency_words": WORDS}, currency_pairs=PAIRS)
    put(h, "t-cells.json", t_cells)
    text = csv_of(
        list(zip(("1402/12/01", "1402/12/05", "1402/12/10", "1402/12/20", "1402/12/29"), ROWS)), header="amount (zorb)"
    )
    res = check(h, {}, text)
    assert res["values"]["total"] == 1700 and res["values"]["rows_left_out_unparsable"] == 1
    assert res["values"]["unit"] == "QDA" and res["values"]["rows_used"] == 4
    assert not any("do not add up" in n for n in res["notes"]) and res["result"] == "fail"
    assert any("1 rows with an amount that could not be read" in n for n in res["notes"])


# --- 12. the same shape with the shipped packs ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header, cells, total, unparsable",
    [
        # 0.4.0 gave 3600: the Rial cell was added as 100 Toman, 10 times too large, with no note
        pytest.param("amount (Toman)", ["1000", "2500", "100 rials"], "3510", 0, id="toman-column-one-rial-cell"),
        # 0.4.0: 3600 as well, with the Persian header and word
        pytest.param(
            "مبلغ (" + FA_IRT + ")",
            ["1000", "2500", "100 " + FA_IRR],
            "3510",
            0,
            id="persian-toman-column-one-rial-cell",
        ),
        # 0.4.0 added 100 to 5000 + 2000 and gave 7100: the Toman cell was read as Rial, 10 times too small
        pytest.param("amount (Rial)", ["5000", "100 toman", "2000 rials"], "8000", 0, id="rial-column-one-toman-cell"),
        # 0.4.0 left the three marked cells unreadable: total 1000, 3 rows left out
        pytest.param(
            "amount (EUR)", ["1,000", EURO_SIGN + "100", "50 EUR", "25 euros"], "1175", 0, id="euro-column-own-marks"
        ),
        # 0.4.0: 1110 (the rials cell as Toman, 10 times too large) and the same 3 unreadable cells
        pytest.param(
            "amount (Toman)",
            ["1000", "100 usd", "100 euros", "$100", "100 toman", "10 rials"],
            "1101",
            3,
            id="toman-column-other-currencies",
        ),
    ],
)
def test_the_shipped_packs_read_a_mark_against_the_unit_of_the_column(tmp_path, header, cells, total, unparsable):
    v = aggregated(tmp_path, header, cells, locale=None)
    assert D(v["total"]) == D(total) and v["unparsable"] == unparsable
    assert D(v["grand"]) == D(v["partition"]) == D(total)


def test_the_total_row_of_a_toman_column_in_rials_is_converted_by_the_shipped_pair(tmp_path):
    # 1000 + 2500 + 10 (100 rials) = 3510 Toman; the file's own total row says 35100 rials = 3510 Toman (0.4.0: 35100)
    v = aggregated(tmp_path, "amount (Toman)", ["1000", "2500", "100 rials", ("total", "35100 rials")], locale=None)
    assert totals(v)["total"] == 3510 and totals(v)["file_total"] == 3510


def test_a_rial_column_under_a_toman_profile_is_converted_after_the_cells_are_read(tmp_path):
    # 1000 rials, 100 toman (= 1000 rials), 500: 2500 rials = 250 Toman
    v = aggregated(tmp_path, "amount (Rial)", ["1000", "100 toman", "500"], locale=None, profile={"currency": "IRT"})
    assert totals(v)["total"] == 250 and v["unit_in"] == "IRR" and v["unit"] == "IRT"


# --- 13. a header that names a currency with no relation to the target ------------------------------------------------

M6_HEADER = "Sales amount (zorb) net"


@pytest.mark.parametrize(
    "cells",
    [["100", "200 zorb"], ["abc", "none of these is a number"], []],
    ids=["readable-rows", "unreadable-rows", "no-rows"],
)
def test_a_header_that_names_a_currency_with_no_relation_to_the_target_ends_the_check_with_a_clear_text(
    tmp_path, cells
):
    p = aggregate_process(tmp_path, M6_HEADER, cells, profile={"currency": "QDX"})  # QDA is not related to QDX
    err = p.stderr.decode("utf-8", "replace")
    assert p.returncode == 3 and p.stdout == b"" and "check error" in err, err
    assert M6_HEADER in err and "QDA" in err and "QDX" in err, err


def test_the_text_of_the_refusal_is_the_same_when_the_target_comes_from_the_args(tmp_path):
    p = aggregate_process(tmp_path, M6_HEADER, ["100"], args={"currency": "QDX"})
    err = p.stderr.decode("utf-8", "replace")
    assert p.returncode == 3 and p.stdout == b"" and M6_HEADER in err and "QDA" in err and "QDX" in err, err


def test_a_header_that_names_a_currency_the_target_relates_to_or_equals_is_not_refused(tmp_path):
    for profile in ({"currency": "QDA"}, {"currency": "QDC"}, {}):
        assert aggregate_process(tmp_path, M6_HEADER, ["100"], profile=profile).returncode == 0, profile


def test_the_refusal_comes_with_the_shipped_packs_as_well(tmp_path):
    header = "Total sales (USD) before tax"
    p = aggregate_process(tmp_path, header, ["abc", "100"], locale=None, profile={"currency": "IRT"})
    err = p.stderr.decode("utf-8", "replace")
    assert p.returncode == 3 and p.stdout == b"" and "check error" in err, err
    assert header in err and "USD" in err and "IRT" in err, err


def test_the_refusal_with_the_shipped_packs_when_the_header_is_a_regional_unit_and_the_target_is_not(tmp_path):
    header = "Amount (Toman) in the ledger"
    p = aggregate_process(tmp_path, header, ["100"], locale=None, profile={"currency": "EUR"})
    err = p.stderr.decode("utf-8", "replace")
    assert p.returncode == 3 and p.stdout == b"" and header in err and "IRT" in err and "EUR" in err, err


# --- 14. the callers with one argument -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, raw, want",
    [
        pytest.param("caps.task", "1,500 toman", 1500, id="toman"),
        pytest.param("caps.task", "2000 rials", 2000, id="rials"),
        pytest.param("fx.USD", "700000 irt", 700000, id="irt"),
        pytest.param("caps.month", "50,000 " + FA_IRR, 50000, id="persian"),
        pytest.param("caps.task", "1,500", 1500, id="plain"),
        pytest.param("caps.task", "12.5 toman", 12.5, id="decimal"),
    ],
)
def test_a_money_field_of_the_profile_reads_a_word_of_the_six_as_in_0_4_0(shipped, field, raw, want):
    assert business.parse_value(field, raw) == want


@pytest.mark.parametrize(
    "field, raw",
    [("caps.month", "100 apples"), ("caps.task", "usdx"), ("caps.task", "-100 toman"), ("caps.task", "0 toman")],
    ids=["text", "word-only", "negative", "zero"],
)
def test_a_money_field_still_refuses_what_it_refused(shipped, field, raw):
    with pytest.raises(Denied) as e:
        business.parse_value(field, raw)
    assert str(e.value) == f"{field} is a positive number"


def test_a_money_field_reads_every_word_of_the_table_the_same_way(shipped):
    # R4: no unit is given, so the number is the amount (0.4.0 refused "100 usd")
    assert business.parse_value("caps.month", "100 usd") == 100
    assert business.parse_value("caps.task", EURO_SIGN + "250") == 250


def normalize(tmp_path, value):
    p = subprocess.run(
        [PY, "-I", str(ROOT / "bin" / "harness-normalize"), "--amount", value],
        env={},
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return json.loads(p.stdout.decode("utf-8"))


@pytest.mark.parametrize(
    "value, want",
    [
        pytest.param("50000 toman", {"amount": "50000", "currency": "IRT"}, id="toman"),
        pytest.param("100 rials", {"amount": "100", "currency": "IRR"}, id="rials"),
        pytest.param("1,000 rials", {"amount": "1000", "currency": "IRR"}, id="rials-groups"),
        pytest.param("50,000 " + FA_IRR, {"amount": "50000", "currency": "IRR"}, id="persian-rial"),
        pytest.param("1000", {"amount": "1000", "currency": None}, id="plain"),
        pytest.param("$100", {"error": "not an amount"}, id="dollar-sign"),
        pytest.param("100 apples", {"error": "not an amount"}, id="text"),
        pytest.param("toman", {"error": "not an amount"}, id="word-only"),
    ],
)
def test_harness_normalize_amount_gives_what_it_gave_in_0_4_0_for_a_word_of_the_six(tmp_path, value, want):
    assert normalize(tmp_path, value) == want


@pytest.mark.parametrize(
    "value, want",
    [
        pytest.param("100 USD", {"amount": "100", "currency": "USD"}, id="usd"),
        pytest.param("100 euros", {"amount": "100", "currency": "EUR"}, id="euros"),
        pytest.param(EURO_SIGN + "100", {"amount": "100", "currency": "EUR"}, id="euro-sign"),
        pytest.param("100 " + FA_EUR, {"amount": "100", "currency": "EUR"}, id="persian-euro"),
    ],
)
def test_harness_normalize_amount_gives_the_amount_and_the_code_for_a_word_of_another_shipped_currency(
    tmp_path, value, want
):
    assert normalize(tmp_path, value) == want


# --- 15. no table work for a plain cell ------------------------------------------------------------------------------

PLAIN = ["1,234", "1000", "(1,000)", "1,000-", "-5", "12.5", "1 000", "۱۲۳", "1,5", "", None, True, 7, D("3.5"), 2.5]


@pytest.fixture
def table_reads(monkeypatch):
    """The calls of ``localize._codes``: the one reader of the word table in ``parse_amount`` (a helper that reads the
    table another way would not be seen by this spy)."""
    calls, real = [], L._codes
    monkeypatch.setattr(L, "_codes", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def test_a_plain_cell_does_not_read_the_word_table(shipped, table_reads):
    for value in PLAIN:
        L.parse_amount(value)
    assert table_reads == []


def test_a_plain_cell_does_not_read_the_word_table_with_a_unit_words_or_pairs(shipped, table_reads):
    for value in PLAIN:
        L.parse_amount(value, unit="QDA")
        L.parse_amount(value, unit="QDA", words=WORDS, pairs=PAIRS)
        L.parse_amount(value, words=WORDS)
        L.parse_amount(value, unit=None, pairs=PAIRS)
    assert table_reads == []


@pytest.mark.parametrize(
    "kw", [{"unit": None}, {"unit": "QDA"}, {"unit": "QDC", "pairs": PAIRS}], ids=["no-unit", "unit", "unit-pairs"]
)
def test_a_cell_with_a_mark_does_read_the_word_table_which_shows_that_the_spy_sees_it(shipped, table_reads, kw):
    L.parse_amount("100 zorb", words=WORDS, **kw)
    assert len(table_reads) >= 1

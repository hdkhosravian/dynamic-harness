"""Currency names and the relations between coded units are pack data (build phase P1, task T4a).

The mechanism is proved with data that no shipped file holds: the live packs ``t-minor``, ``t-scale`` and ``t-odd`` and
their made-up codes. The shipped IRT/IRR facts appear only where the brief names them (the 0.4.0 controls). Names that
the task adds are reached inside the test bodies, so that a missing name fails one test and never the collection.

Every test that is not marked ``once`` runs twice: with the shipped packs, and with a copy of them without ``ir.json``
(``defaults`` below). It shows that no code path needs a shipped regional entry. A test marked ``once`` pins a
0.4.0 fact that lives in ``ir.json`` or makes its own copy of the defaults. Non-ASCII characters are \\u escapes."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from decimal import Decimal
from pathlib import Path

import pytest

from harness_core import (business, compile as compile_, contact, gate, locales, oracle, probe, protected, run,
                          stopgate, templates)
from harness_core import localize as L
from harness_core.errors import Denied, LocaleError
from harness_core.ledger import Outputs
from test_budget import blocked, log_of
from test_business import confirm, typed
from test_locales import pack, put, set_profile
from test_p3_hooks import SID, compiled, ctx_of, h, say  # noqa: F401  (fixture h)
from test_run import job  # noqa: F401  (fixture job: sales.csv in the workspace, a compiled task, unit u1)

ROOT = Path(__file__).resolve().parents[1]
PY = "/usr/bin/python3"

LA, LB, LC = "ql1", "ql2", "ql3"   # made-up language codes: no shipped pack holds them
FA_USD, FA_EUR, FA_GBP, FA_AED, FA_TRY = ("دلار", "یورو", "پوند",
                                          "درهم", "لیر")
FA_IRT, FA_IRR = "تومان", "ریال"


def pair(frm, to, factor):
    return {"from": frm, "to": to, "factor": factor}


def cur(code="QDA", **body):
    return {code: dict({"decimals": 2, "names": {"en": "quidas"}}, **body)}


MINOR_PAIR, SCALE_PAIR, ODD_PAIR = pair("QDA", "QDC", 100), pair("QKY", "QYN", 1000), pair("QQA", "QQB", 7)
MINOR = pack("t-minor", currencies=cur("QDA"), currency_pairs=[MINOR_PAIR])
SCALE = pack("t-scale", currencies={"QYN": {"decimals": 0, "names": {LA: "qynla", LB: "qynlb"}}},
             currency_pairs=[SCALE_PAIR])
ODD = pack("t-odd", currency_pairs=[ODD_PAIR])
PAIRS = [pytest.param("QDA", "QDC", 100, id="minor"), pytest.param("QKY", "QYN", 1000, id="scale"),
         pytest.param("QQA", "QQB", 7, id="odd")]
LIVE_PAIRS = [MINOR_PAIR, ODD_PAIR, SCALE_PAIR]   # the merge order of the three packs: t-minor, t-odd, t-scale
UNRELATED = [("QDA", "QKY"), ("QQB", "QDC"), ("QYN", "QQA")]   # each code is in a pair, never in the same pair
WARNING = "your spending limits are in the old currency and will be removed; set them again"


def once(fn):
    """The test is not run again with a copy of the shipped packs without ``ir.json``."""
    fn._once = True
    return fn


def pytest_generate_tests(metafunc):
    if "defaults" in metafunc.fixturenames and not getattr(metafunc.function, "_once", False):
        metafunc.parametrize("defaults", ["shipped", "no-ir"], indirect=True)


def without_ir(tmp_path, monkeypatch, name="defaults-no-ir"):
    """A copy of the shipped defaults without ``ir.json``; ``protected.DEFAULTS_DIR`` points at it."""
    d = tmp_path / name
    shutil.copytree(protected.DEFAULTS_DIR, d)
    (d / "locales" / "ir.json").unlink()
    monkeypatch.setattr(protected, "DEFAULTS_DIR", d)
    return d


@pytest.fixture(autouse=True)
def defaults(request, tmp_path, monkeypatch):
    if getattr(request, "param", "shipped") == "no-ir":
        if "h" in request.fixturenames:
            request.getfixturevalue("h")   # the plugin data is set up with the shipped packs, then the copy takes over
        without_ir(tmp_path, monkeypatch)
    return getattr(request, "param", "shipped")


def shipped_pairs():
    """The ``currency_pairs`` of the shipped packs in merge order (the base pack, then the others sorted)."""
    d = protected.DEFAULTS_DIR / "locales"
    ids = sorted(f.name[:-len(".json")] for f in d.glob("*.json"))
    return [e for i in ["en"] + [x for x in ids if x != "en"]
            for e in json.loads((d / f"{i}.json").read_text("utf-8")).get("currency_pairs", [])]


def live3(h):
    for p in (MINOR, ODD, SCALE):
        put(h, p["id"] + ".json", p)
    return ctx_of(h)


def spy_views(monkeypatch):
    calls, real = [], locales.view
    monkeypatch.setattr(locales, "view", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def csv_of(rows, header="amount"):
    return f"date,description,{header}\n" + "".join(f"{d},invoice {i},{a}\n" for i, (d, a) in enumerate(rows, 1))


def amounts(m):
    """Three rows in the month 1402-12 that add up to 4,000,000 times ``m`` (in the unit of the file)."""
    return csv_of(list(zip(("1402/12/01", "1402/12/05", "1402/12/29"), (1000000 * m, 2500000 * m, 500000 * m))))


def check(h, args, text, attempt=1):
    """The finance check, through ``run.apply`` and the real sandbox: the header of the amount column names no unit."""
    (h["ws"] / "amounts.csv").write_text(text, encoding="utf-8")
    req = {"tool": "run", "check_id": "finance.reconcile", "unit": "u1", "attempt": attempt, "inputs": ["amounts.csv"],
           "args": dict({"period": {"month": "1402-12"}}, **args)}
    return run.apply(req, ctx_of(h), {"session_id": SID})


def sent_requests(h, ctx=None):
    """What ``run.apply`` writes to the stdin of the check, for one run."""
    sent = []

    def runner(argv, cwd, stdin, **kw):
        sent.append(json.loads(stdin.decode("utf-8")))
        return 0, b'{"result": "pass", "values": {}, "notes": []}\n', b""
    req = {"tool": "run", "check_id": "finance.reconcile", "unit": "u1", "attempt": 1, "inputs": ["sales.csv"],
           "args": {"period": {"month": "1402-12"}}}
    assert run.apply(req, ctx or ctx_of(h), {"session_id": SID}, runner=runner)["result"] == "pass"
    return sent


# --- 1. the pack keys are checked at load ---------------------------------------------------------------------------

GOOD_PAIRS = [pair("QDA", "QDC", 10), pair("QQA", "QQB", 2)]


def with_pairs(*entries):
    return {"currencies": cur(), "currency_pairs": [GOOD_PAIRS[0], *entries]}


def without(entry, key):
    return {k: v for k, v in entry.items() if k != key}


# (id, the keys of the pack, the texts that the error holds beside the file path)
BAD_PACKS = [
    ("code-lowercase", {"currencies": cur("qda"), "currency_pairs": GOOD_PAIRS}, ["currencies", "qda"]),
    ("code-two-letters", {"currencies": cur("QD"), "currency_pairs": GOOD_PAIRS}, ["currencies", "QD"]),
    ("code-four-letters", {"currencies": cur("QDAA"), "currency_pairs": GOOD_PAIRS}, ["currencies", "QDAA"]),
    ("code-with-a-digit", {"currencies": cur("QD1"), "currency_pairs": GOOD_PAIRS}, ["currencies", "QD1"]),
    ("decimals-negative", {"currencies": cur(decimals=-1)}, ["currencies.QDA.decimals"]),
    ("decimals-19", {"currencies": cur(decimals=19)}, ["currencies.QDA.decimals"]),
    ("decimals-boolean", {"currencies": cur(decimals=True)}, ["currencies.QDA.decimals"]),
    ("decimals-string", {"currencies": cur(decimals="2")}, ["currencies.QDA.decimals"]),
    ("decimals-float", {"currencies": cur(decimals=2.5)}, ["currencies.QDA.decimals"]),
    ("name-empty", {"currencies": cur(names={"en": ""})}, ["currencies.QDA.names"]),
    ("name-a-number", {"currencies": cur(names={"en": 5})}, ["currencies.QDA.names"]),
    ("name-language-upper-case", {"currencies": cur(names={"EN": "quidas"})}, ["currencies.QDA.names", "EN"]),
    ("name-language-with-a-space", {"currencies": cur(names={"q l": "quidas"})}, ["currencies.QDA.names", "q l"]),
    ("name-language-33-characters", {"currencies": cur(names={"q" * 33: "quidas"})},
     ["currencies.QDA.names", "q" * 33]),
    ("currency-with-an-extra-key", {"currencies": cur(symbol="q")}, ["currencies.QDA"]),
    ("from-lowercase", {"currency_pairs": with_pairs(pair("qqa", "QQB", 2))["currency_pairs"]}, ["currency_pairs[1]"]),
    ("from-four-letters", {"currency_pairs": with_pairs(pair("QQAA", "QQB", 2))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("from-with-a-digit", {"currency_pairs": with_pairs(pair("QQ1", "QQB", 2))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("to-lowercase", {"currency_pairs": with_pairs(pair("QQA", "qqb", 2))["currency_pairs"]}, ["currency_pairs[1]"]),
    ("to-two-letters", {"currency_pairs": with_pairs(pair("QQA", "QQ", 2))["currency_pairs"]}, ["currency_pairs[1]"]),
    ("from-missing", {"currency_pairs": with_pairs(without(GOOD_PAIRS[1], "from"))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("to-missing", {"currency_pairs": with_pairs(without(GOOD_PAIRS[1], "to"))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("factor-missing", {"currency_pairs": with_pairs(without(GOOD_PAIRS[1], "factor"))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("extra-key", {"currency_pairs": with_pairs(dict(GOOD_PAIRS[1], note="x"))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("from-equals-to", {"currency_pairs": with_pairs(pair("QQA", "QQA", 2))["currency_pairs"]}, ["currency_pairs[1]"]),
    ("factor-1", {"currency_pairs": with_pairs(pair("QQA", "QQB", 1))["currency_pairs"]}, ["currency_pairs[1]"]),
    ("factor-0", {"currency_pairs": with_pairs(pair("QQA", "QQB", 0))["currency_pairs"]}, ["currency_pairs[1]"]),
    ("factor-negative", {"currency_pairs": with_pairs(pair("QQA", "QQB", -3))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("factor-boolean", {"currency_pairs": with_pairs(pair("QQA", "QQB", True))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("factor-float", {"currency_pairs": with_pairs(pair("QQA", "QQB", 2.5))["currency_pairs"]}, ["currency_pairs[1]"]),
    ("factor-a-whole-float", {"currency_pairs": with_pairs(pair("QQA", "QQB", 10.0))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("factor-string", {"currency_pairs": with_pairs(pair("QQA", "QQB", "10"))["currency_pairs"]},
     ["currency_pairs[1]"]),
    ("the-same-two-codes-twice", {"currency_pairs": GOOD_PAIRS + [pair("QQA", "QQB", 2)]}, ["currency_pairs[2]"]),
    ("the-same-two-codes-twice-with-another-factor", {"currency_pairs": GOOD_PAIRS + [pair("QQA", "QQB", 3)]},
     ["currency_pairs[2]"]),
    ("the-same-two-codes-reversed", {"currency_pairs": GOOD_PAIRS + [pair("QQB", "QQA", 2)]}, ["currency_pairs[2]"]),
    ("the-first-entry-reversed-later", {"currency_pairs": GOOD_PAIRS + [pair("QDC", "QDA", 5)]},
     ["currency_pairs[2]"]),
]


@pytest.mark.parametrize("case", BAD_PACKS, ids=[c[0] for c in BAD_PACKS])
def test_a_bad_currency_key_is_refused_with_the_file_and_the_key_path(h, case):
    _, keys, texts = case
    good = {"currencies": cur(), "currency_pairs": GOOD_PAIRS}
    put(h, "xx.json", pack("xx", **good))
    assert locales.installed(ctx_of(h))["xx"]["currency_pairs"] == GOOD_PAIRS   # control: without the fault it loads
    put(h, "xx.json", pack("xx", **dict(good, **keys)))
    ctx = ctx_of(h)
    with pytest.raises(LocaleError) as e:
        locales.installed(ctx)
    f = Path(ctx.data_root) / "control" / "locales" / "xx.json"
    assert str(e.value).startswith(f"locale pack {f}: ")
    for text in texts:
        assert text in str(e.value), text


def test_a_pack_with_valid_currency_keys_loads_and_keeps_them(h):
    currencies = {"QDA": {"decimals": 0, "names": {"en": "quidas", LA: "qd"}}, "QDB": {"decimals": 18}, "QDC": {},
                  "QDD": {"names": {LB: "qdd"}}}
    pairs = [pair("QDA", "QDC", 2), pair("QZA", "QZB", 10 ** 12)]   # a code needs no entry in currencies
    put(h, "xx.json", pack("xx", currencies=currencies, currency_pairs=pairs))
    got = locales.installed(ctx_of(h))["xx"]
    assert got["currencies"] == currencies and got["currency_pairs"] == pairs


def test_a_pack_with_the_other_key_only_loads(h):
    put(h, "xx.json", pack("xx", currency_pairs=[pair("QQA", "QQB", 7)]))
    put(h, "yy.json", pack("yy", currencies=cur()))
    got = locales.installed(ctx_of(h))
    assert "currencies" not in got["xx"] and got["xx"]["currency_pairs"] == [ODD_PAIR]
    assert "currency_pairs" not in got["yy"] and got["yy"]["currencies"] == cur()


def test_the_shipped_packs_are_checked_by_the_same_rules(tmp_path, monkeypatch):
    d = tmp_path / "defaults-bad"
    shutil.copytree(protected.DEFAULTS_DIR, d)
    f = d / "locales" / "en.json"
    en = json.loads(f.read_text("utf-8"))
    en["currency_pairs"] = [pair("QQA", "QQB", 1)]
    f.write_text(json.dumps(en), "utf-8")
    monkeypatch.setattr(protected, "DEFAULTS_DIR", d)
    with pytest.raises(LocaleError) as e:
        locales.installed()
    assert str(e.value).startswith(f"locale pack {f}: ") and "currency_pairs[0]" in str(e.value)


# --- 2. the shipped content is exactly the entries of R2 ------------------------------------------------------------

EN_NAMES = {"USD": "US dollars", "EUR": "euros", "GBP": "pounds", "AED": "dirhams", "TRY": "lira"}
FA_NAMES = {"USD": FA_USD, "EUR": FA_EUR, "GBP": FA_GBP, "AED": FA_AED, "TRY": FA_TRY}


def test_en_holds_five_currencies_with_two_decimals_and_their_english_names():
    pk = locales.installed()["en"]
    assert pk["currencies"] == {c: {"decimals": 2, "names": {"en": n}} for c, n in EN_NAMES.items()}
    assert pk.get("currency_pairs", []) == []


def test_fa_holds_the_same_five_currencies_with_their_persian_names_only():
    pk = locales.installed()["fa"]
    assert pk["currencies"] == {c: {"names": {"fa": n}} for c, n in FA_NAMES.items()}
    assert pk.get("currency_pairs", []) == []


@once
def test_ir_holds_the_two_regional_currencies_and_the_one_relation_between_them():
    pk = locales.installed()["ir"]
    assert pk["currencies"] == {"IRT": {"decimals": 0, "names": {"en": "Toman", "fa": FA_IRT}},
                                "IRR": {"decimals": 0, "names": {"en": "Rial", "fa": FA_IRR}}}
    assert pk["currency_pairs"] == [pair("IRT", "IRR", 10)]


@once
def test_the_merged_shipped_view_holds_seven_codes_and_one_pair():
    v = locales.view(None, every=True)
    assert sorted(v["currencies"]) == ["AED", "EUR", "GBP", "IRR", "IRT", "TRY", "USD"]
    assert v["currencies"]["USD"] == {"decimals": 2, "names": {"en": "US dollars", "fa": FA_USD}}
    assert v["currency_pairs"] == [pair("IRT", "IRR", 10)]


# --- 3. merge -------------------------------------------------------------------------------------------------------


def test_the_names_of_one_code_from_two_packs_merge_and_the_first_pack_wins_a_clash(h):
    put(h, "t-minor.json", MINOR)   # sorts before t-names: its decimals and its English name win
    put(h, "t-names.json", pack("t-names", currencies=cur(decimals=3, names={"en": "other", LA: "qda-la"})))
    c = ctx_of(h)
    want = {"decimals": 2, "names": {"en": "quidas", LA: "qda-la"}}
    assert locales.view(c)["currencies"]["QDA"] == want
    assert locales.view(c, every=True)["currencies"]["QDA"] == want


def test_a_live_pack_adds_a_name_to_a_shipped_currency_and_keeps_the_shipped_ones(h):
    put(h, "t-names.json", pack("t-names", currencies={"USD": {"names": {LA: "usd-la"}}}))
    usd = locales.view(ctx_of(h), every=True)["currencies"]["USD"]
    assert usd["names"] == {"en": "US dollars", "fa": FA_USD, LA: "usd-la"} and usd["decimals"] == 2


def test_the_first_entry_of_the_merge_order_is_the_relation_for_two_codes(h):
    # no profile list: the base pack, then the others sorted by id, so t-early comes before t-late
    put(h, "t-late.json", pack("t-late", currency_pairs=[pair("QDC", "QDA", 5)]))
    put(h, "t-early.json", pack("t-early", currency_pairs=[pair("QDA", "QDC", 100)]))
    pairs = locales.view(ctx_of(h), every=True)["currency_pairs"]
    ours = [e for e in pairs if "QDA" in (e["from"], e["to"])]
    assert ours[0] == pair("QDA", "QDC", 100)   # t-early wins: its id sorts first
    assert L.to_unit(Decimal(1), "QDA", "QDC", pairs=pairs) == 100
    assert L.to_unit(Decimal(300), "QDC", "QDA", pairs=pairs) == 3


def test_the_pack_the_profile_names_wins_whatever_the_ids_are(h):
    put(h, "t-late.json", pack("t-late", currency_pairs=[pair("QDC", "QDA", 5)]))
    put(h, "t-early.json", pack("t-early", currency_pairs=[pair("QDA", "QDC", 100)]))
    set_profile(h, locales=["t-late"])
    c = ctx_of(h)
    assert locales.active(c) == ["t-late", "en"]
    pairs = locales.view(c, every=True)["currency_pairs"]   # t-early is installed and not named: its entry comes later
    assert [e for e in pairs if "QDA" in (e["from"], e["to"])][0] == pair("QDC", "QDA", 5)
    assert L.to_unit(Decimal(10), "QDA", "QDC", pairs=pairs) == 2    # 1 QDC = 5 QDA, so 10 QDA are 2 QDC
    assert L.to_unit(Decimal(10), "QDC", "QDA", pairs=pairs) == 50


@pytest.mark.parametrize("split", [True, False], ids=["two-packs", "one-pack"])
def test_a_to_b_and_b_to_c_make_no_a_to_c(h, split):
    ab, bc = pair("QRA", "QRB", 3), pair("QRB", "QRC", 4)
    if split:
        put(h, "t-x1.json", pack("t-x1", currency_pairs=[ab]))
        put(h, "t-x2.json", pack("t-x2", currency_pairs=[bc]))
    else:
        put(h, "t-x1.json", pack("t-x1", currency_pairs=[ab, bc]))
    pairs = locales.view(ctx_of(h), every=True)["currency_pairs"]
    assert L.to_unit(Decimal(5), "QRA", "QRB", pairs=pairs) == 15
    assert L.to_unit(Decimal(8), "QRC", "QRB", pairs=pairs) == 2
    for a, b in (("QRA", "QRC"), ("QRC", "QRA")):
        with pytest.raises(ValueError) as e:
            L.to_unit(Decimal(5), a, b, pairs=pairs)
        assert str(e.value) == f"cannot convert {a} to {b}"


# --- 4. templates.currency ------------------------------------------------------------------------------------------


def test_a_live_name_comes_in_the_asked_language(h):
    c = live3(h)
    assert templates.currency("QDA", "en", c) == "quidas"
    assert templates.currency("QYN", LA, c) == "qynla" and templates.currency("QYN", LB, c) == "qynlb"


def test_a_currency_with_no_name_in_the_asked_language_is_its_code_and_no_other_language_fills_in(h):
    c = live3(h)
    assert templates.currency("QYN", "en", c) == "QYN"   # t-scale names it in two other languages only
    assert templates.currency("QYN", LC, c) == "QYN"
    assert templates.currency("QDA", LA, c) == "QDA"      # t-minor names it in en only
    for code in ("USD", "EUR"):                            # a shipped code in a language no pack names it in
        assert templates.currency(code, LC, c) == code
        assert templates.currency(code, LA, c) == code


def test_none_and_the_empty_string_are_the_empty_string(h):
    c = live3(h)
    for lang in ("en", "fa", LA):
        assert templates.currency(None, lang, c) == "" and templates.currency("", lang, c) == ""
        assert templates.currency(None, lang, None) == "" and templates.currency("", lang) == ""


def test_without_a_context_only_the_shipped_packs_are_read(h):
    live3(h)
    assert templates.currency("QDA", "en", None) == "QDA"
    assert templates.currency("USD", "en", None) == "US dollars" and templates.currency("USD", "fa", None) == FA_USD
    assert templates.currency("USD", "en") == "US dollars"


def test_a_profile_list_that_leaves_the_pack_out_does_not_hide_its_names(h):
    live3(h)
    set_profile(h, locales=["en"])
    c = ctx_of(h)
    assert locales.active(c) == ["en"]
    assert templates.currency("QDA", "en", c) == "quidas" and templates.currency("QYN", LB, c) == "qynlb"


@pytest.mark.parametrize("exc", [LocaleError("a pack is broken"), OSError("a folder cannot be read")],
                         ids=["harness-error", "os-error"])
def test_when_the_live_view_cannot_be_read_the_shipped_packs_answer(h, monkeypatch, exc):
    c = live3(h)
    real = locales.view

    def broken(ctx=None, **k):
        if ctx is not None:
            raise exc
        return real(ctx, **k)
    monkeypatch.setattr(locales, "view", broken)
    assert templates.currency("USD", "en", c) == "US dollars" and templates.currency("USD", "fa", c) == FA_USD
    assert templates.currency("QDA", "en", c) == "QDA"   # the live name is out of reach: the code


def test_a_broken_live_pack_gives_the_shipped_answer(h):
    live3(h)
    put(h, "broken.json", b"{")
    c = ctx_of(h)
    with pytest.raises(LocaleError):
        locales.view(c, every=True)
    assert templates.currency("USD", "en", c) == "US dollars" and templates.currency("QDA", "en", c) == "QDA"


def test_any_other_error_of_the_view_is_not_swallowed(h, monkeypatch):
    c = live3(h)
    real = locales.view

    def broken(ctx=None, **k):
        if ctx is not None:
            raise RuntimeError("a defect, not a bad file")
        return real(ctx, **k)
    monkeypatch.setattr(locales, "view", broken)
    with pytest.raises(RuntimeError):
        templates.currency("QDA", "en", c)


def test_currency_reads_the_view_of_every_installed_pack_once_per_call_and_no_cache_hides_a_change(h, monkeypatch):
    c = live3(h)
    set_profile(h, locales=["en"])
    calls = spy_views(monkeypatch)
    real = locales.view
    seen = []
    monkeypatch.setattr(locales, "view", lambda *a, **k: seen.append(k.get("every")) or real(*a, **k))
    assert templates.currency("QDA", "en", c) == "quidas"
    assert seen == [True] and len(calls) == 1
    put(h, "t-minor.json", dict(MINOR, currencies=cur(names={"en": "changed"})))
    assert templates.currency("QDA", "en", ctx_of(h)) == "changed"   # a second call reads the packs again
    assert len(calls) == 2


@pytest.mark.parametrize("code,en,fa", [("USD", "US dollars", FA_USD), ("EUR", "euros", FA_EUR),
                                        ("GBP", "pounds", FA_GBP), ("AED", "dirhams", FA_AED),
                                        ("TRY", "lira", FA_TRY)])
def test_the_five_shipped_currencies_keep_their_0_4_0_names(h, code, en, fa):
    c = ctx_of(h)
    assert templates.currency(code, "en", c) == en and templates.currency(code, "fa", c) == fa


@once
@pytest.mark.parametrize("code,en,fa", [("IRT", "Toman", FA_IRT), ("IRR", "Rial", FA_IRR)])
def test_the_two_regional_currencies_keep_their_0_4_0_names(h, code, en, fa):
    c = ctx_of(h)
    assert templates.currency(code, "en", c) == en and templates.currency(code, "fa", c) == fa


def test_the_tables_of_the_code_are_gone():
    assert not hasattr(business, "CURRENCIES") and not hasattr(templates, "CURRENCY_FA")


@pytest.mark.parametrize("order", [("business", "templates"), ("templates", "business")])
def test_either_module_can_be_imported_first(order):
    code = "import sys; sys.path.insert(0, %r); import harness_core.%s, harness_core.%s" % (str(ROOT / "lib"), *order)
    p = subprocess.run([PY, "-I", "-c", code], env={}, capture_output=True, timeout=120)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")


# --- 4b. the names reach the owner's text ---------------------------------------------------------------------------

METRIC = {"metric_id": "revenue", "inclusions": "sales invoices minus returns", "exclusions": "VAT", "unit": "amount",
          "currency": "qda"}


def test_a_definition_sentence_names_the_live_currency_in_the_owners_language(h):
    c = live3(h)
    compiled(h)
    assert contact.definition_text(dict(METRIC, currency="QDA"), c) == (
        "I'll count revenue as sales invoices minus returns, excluding VAT, in quidas. Is that right?")
    business.set_field(c, "language", "fa")
    sentence = contact.definition_text(dict(METRIC, currency="QDA"), ctx_of(h))   # no Persian name: the code
    assert "QDA" in sentence and "quidas" not in sentence


def test_the_settings_text_shows_the_name_in_the_base_language_and_the_code(h):
    c = live3(h)
    for code, shown in (("QDA", "quidas (QDA)"), ("QYN", "QYN (QYN)"), ("QZZ", "QZZ (QZZ)"),
                        ("USD", "US dollars (USD)")):
        business.set_field(c, "currency", code)
        assert f"- Your currency: {shown}" in business.describe(ctx_of(h))


@pytest.mark.parametrize("raw,shown", [("qda", "quidas (QDA)"), ("QYN", "QYN (QYN)"), ("qzz", "QZZ (QZZ)"),
                                       ("usd", "US dollars (USD)")])
def test_the_owner_sentence_names_the_currency_through_the_same_function(h, raw, shown):
    live3(h)
    assert f"Set your currency to {shown}." in typed(h, f"/dynamic-harness:business set currency {raw}", "n1")


@once
def test_the_two_regional_currencies_read_as_in_0_4_0_in_the_settings_text(h):
    business.set_field(ctx_of(h), "currency", "IRT")
    assert "- Your currency: Toman (IRT)" in business.describe(ctx_of(h))
    assert "Set your currency to Rial (IRR)." in typed(h, "/dynamic-harness:business set currency irr", "n1")


def test_the_answer_header_names_the_live_currency(h):
    live3(h)
    (h["ws"] / "sales.csv").write_text("date,description,amount\n1402/12/01,invoice 1,1000\n1402/12/05,invoice 2,2500\n"
                                       "1402/12/29,invoice 3,500\n", encoding="utf-8")
    compiled(h)
    business.set_field(ctx_of(h), "currency", "QDA")
    contact.apply({"tool": "contact", "type": "definition_confirm", "metric": dict(METRIC, currency="QDA")},
                  ctx_of(h), {"session_id": SID})
    say(h, "yes", "def-yes")
    contact.apply({"tool": "contact", "type": "definition_confirm", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    res = check(h, {"metric_id": "revenue"}, (h["ws"] / "sales.csv").read_text("utf-8"))
    from harness_core import answer
    out = answer.apply({"tool": "answer", "sub": "header", "unit": "u1", "output": res["sha256"]}, ctx_of(h),
                       {"session_id": SID})
    assert out["header"].splitlines()[0].startswith("4,000 quidas: revenue")


def test_the_opening_estimate_names_the_live_currency(h, monkeypatch):
    c = live3(h)
    business.set_field(c, "currency", "QDA")
    from harness_core import estimate
    monkeypatch.setattr(estimate, "estimate", lambda ctx, tier: {"minutes": (2, 5), "money": (1.5, 3.0), "samples": 3})
    line = compile_._opening(ctx_of(h), {"deliverable": {"description": "the sales report"}}, "T1", "no-job")
    assert " and roughly 1.5 to 3.0 quidas" in line


def test_the_budget_stop_names_the_live_currency(h):
    live3(h)
    compiled(h)
    (h["data"] / "business").mkdir(exist_ok=True)
    (h["data"] / "business" / "profile.json").write_text(json.dumps({"caps": {"task": 1.0, "currency": "QDA"}}))
    (h["data"] / "control").mkdir(exist_ok=True)
    prices = {"version": 1, "currency": "QDA", "as_of": "2026-11-01", "per_mtok": {"input": 3.0, "output": 15.0}}
    (h["data"] / "control" / "prices.json").write_text(json.dumps(prices))
    tr = h["ws"].parent / "t.jsonl"
    tr.write_text(json.dumps({"type": "assistant", "timestamp": "2099-01-01T00:00:00Z",
                              "message": {"usage": {"input_tokens": 200000, "output_tokens": 40000}}}) + "\n")
    evt = {"session_id": SID, "tool_name": "Read", "tool_input": {"file_path": str(h["ws"] / "orders.csv")},
           "tool_response": {}, "cwd": str(h["ws"]), "transcript_path": str(tr)}
    from harness_core import hookmain
    res = hookmain.main("PostToolUse", evt, h["env"])
    assert blocked(res) and " quidas more" in json.dumps(res[1], ensure_ascii=False)


# --- 5. conversion --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("frm,to,f", PAIRS)
def test_a_decimal_is_multiplied_by_the_factor_from_the_from_side_and_divided_on_the_to_side(frm, to, f):
    table = [pair(frm, to, f)]
    up = L.to_unit(Decimal("2.5"), frm, to, pairs=table)
    assert isinstance(up, Decimal) and up == Decimal("2.5") * f
    down = L.to_unit(Decimal(5 * f), to, frm, pairs=table)
    assert isinstance(down, Decimal) and down == Decimal(5)
    assert L.to_unit(Decimal(f) * 7, to, frm, pairs=table) == Decimal(7)
    assert L.to_unit(Decimal("0"), frm, to, pairs=table) == 0
    assert L.to_unit(Decimal("-3"), frm, to, pairs=table) == -3 * f


def test_the_literal_figures_of_the_three_live_pairs():
    t = LIVE_PAIRS
    assert L.to_unit(Decimal("2.5"), "QDA", "QDC", pairs=t) == Decimal("250")
    assert L.to_unit(Decimal("250"), "QDC", "QDA", pairs=t) == Decimal("2.5")
    assert L.to_unit(Decimal("2.5"), "QKY", "QYN", pairs=t) == Decimal("2500")
    assert L.to_unit(Decimal("2500"), "QYN", "QKY", pairs=t) == Decimal("2.5")
    assert L.to_unit(Decimal("3.5"), "QQA", "QQB", pairs=t) == Decimal("24.5")
    assert L.to_unit(Decimal("24.5"), "QQB", "QQA", pairs=t) == Decimal("3.5")


def test_a_division_by_a_factor_that_is_not_a_power_of_ten_gives_what_decimal_division_gives():
    got = L.to_unit(Decimal(1), "QQB", "QQA", pairs=[ODD_PAIR])
    assert isinstance(got, Decimal) and got == Decimal("0.1428571428571428571428571429") and got == Decimal(1) / 7


@pytest.mark.parametrize("frm,to,f", PAIRS)
def test_an_int_stays_an_int_when_it_is_multiplied(frm, to, f):
    got = L.to_unit(5, frm, to, pairs=[pair(frm, to, f)])
    assert got == 5 * f and type(got) is int


@pytest.mark.parametrize("frm,to,f", PAIRS)
def test_an_int_divided_gives_what_the_slash_gives(frm, to, f):
    table = [pair(frm, to, f)]
    for n in (7 * f, 36 * f + 1):
        got = L.to_unit(n, to, frm, pairs=table)
        assert got == n / f and type(got) is float


@pytest.mark.parametrize("frm,to,f", PAIRS)
def test_equal_codes_and_a_missing_code_return_the_amount_itself(frm, to, f):
    table = [pair(frm, to, f)]
    amount = Decimal("12.50")
    for unit, target in ((frm, frm), (to, to), (None, to), (frm, None), (None, None), ("QZZ", "QZZ")):
        assert L.to_unit(amount, unit, target, pairs=table) is amount


@pytest.mark.parametrize("unit,target", [("QDA", "QYN"), ("QYN", "QDA"), ("QQA", "QDC"), ("QQB", "QKY"), ("QDA", "QZZ"),
                                         ("QZZ", "QDA")])
def test_unrelated_codes_raise_with_the_pinned_text(unit, target):
    for amount in (Decimal(1), 1):
        with pytest.raises(ValueError) as e:
            L.to_unit(amount, unit, target, pairs=LIVE_PAIRS)
        assert str(e.value) == f"cannot convert {unit} to {target}"


def test_the_pairs_given_are_the_whole_table_no_shipped_pair_is_added():
    with pytest.raises(ValueError) as e:
        L.to_unit(Decimal(1), "IRT", "IRR", pairs=LIVE_PAIRS)
    assert str(e.value) == "cannot convert IRT to IRR"
    with pytest.raises(ValueError):
        L.to_unit(Decimal(1), "QDA", "QDC", pairs=[])
    assert L.to_unit(Decimal(1), "QDA", "QDA", pairs=[]) == 1


def test_the_first_entry_that_holds_both_codes_is_the_relation_in_either_direction():
    table = [pair("QDC", "QDA", 5), pair("QDA", "QDC", 100), pair("QDA", "QQB", 3)]
    assert L.to_unit(Decimal(10), "QDA", "QDC", pairs=table) == Decimal(2)
    assert L.to_unit(Decimal(10), "QDC", "QDA", pairs=table) == Decimal(50)
    assert L.to_unit(Decimal(10), "QDA", "QQB", pairs=table) == Decimal(30)   # another partner of QDA: its own entry


def test_the_docstring_of_to_unit_and_the_module_docstring_name_no_currency_relation():
    assert "= 10" not in (L.__doc__ or "")
    assert not any(w in (L.to_unit.__doc__ or "") for w in ("Rial", "Toman", "IRR", "IRT"))


# --- 6. the finance check -------------------------------------------------------------------------------------------


def expected_figures(frm, to, f, down):
    """(unit of the file, unit of the profile, total, total in the other unit) for 4,000,000 (x7 for the odd pair)."""
    s = 4000000 * (7 if f == 7 else 1)
    return (to, frm, s // f, s) if down else (frm, to, s * f, s)


@pytest.mark.parametrize("down", [False, True], ids=["from-to-to", "to-to-from"])
@pytest.mark.parametrize("frm,to,f", PAIRS)
def test_the_check_converts_across_a_live_pair_in_the_sandbox(h, job, frm, to, f, down):
    c = live3(h)
    src, target, total, other = expected_figures(frm, to, f, down)
    business.set_field(c, "currency", target)
    res = check(h, {"unit": src}, amounts(7 if f == 7 else 1))
    assert res["result"] == "pass", res["notes"]
    v = res["values"]
    assert v["total"] == total and v["unit"] == target
    assert v["total_other_unit"] == other and v["total_other_unit_millions"] == other // 1000000
    assert v["rows_used"] == 3
    assert res["notes"] == [f"amounts were in {src} and are shown in {target}"]


@once
@pytest.mark.parametrize("src,target,total", [("IRR", "IRT", 400000), ("IRT", "IRR", 40000000)])
def test_the_check_converts_the_shipped_pair_as_in_0_4_0(h, job, src, target, total):
    business.set_field(ctx_of(h), "currency", target)
    res = check(h, {"unit": src}, amounts(1))
    assert res["result"] == "pass", res["notes"]
    assert res["values"]["total"] == total and res["values"]["unit"] == target
    assert res["values"]["total_other_unit"] == 4000000 and res["values"]["total_other_unit_millions"] == 4


@pytest.mark.parametrize("first,expect", [(pair("QRB", "QRA", 3), "down"), (pair("QRA", "QRB", 3), "up")])
def test_the_other_unit_is_the_partner_in_the_first_entry_that_holds_the_unit(h, job, first, expect):
    put(h, "t-first.json", pack("t-first", currency_pairs=[first]))
    put(h, "t-second.json", pack("t-second", currency_pairs=[pair("QRA", "QRC", 4)]))
    business.set_field(ctx_of(h), "currency", "QRA")
    res = check(h, {}, amounts(3))   # 12,000,000 in QRA; the unit of the file is the profile currency
    assert res["result"] == "pass", res["notes"]
    v = res["values"]
    assert v["total"] == 12000000 and v["unit"] == "QRA"
    assert v["total_other_unit"] == (4000000 if expect == "down" else 36000000)   # QRB by the first entry, never QRC
    assert v["total_other_unit_millions"] == (4 if expect == "down" else 36)


def test_a_pair_of_a_pack_the_profile_does_not_name_still_converts_in_the_check(h, job):
    c = live3(h)
    business.change_list(c, "locales", "en", True)
    assert locales.active(ctx_of(h)) == ["en"]
    business.set_field(ctx_of(h), "currency", "QDA")
    res = check(h, {"unit": "QDC"}, amounts(1))
    assert res["result"] == "pass" and res["values"]["total"] == 40000 and res["values"]["total_other_unit"] == 4000000


def test_a_currency_that_no_pack_names_passes_the_amounts_through_and_has_no_other_unit(h, job):
    live3(h)
    business.set_field(ctx_of(h), "currency", "QZZ")
    res = check(h, {}, amounts(1))
    assert res["result"] == "pass", res["notes"]
    assert res["values"]["total"] == 4000000 and res["values"]["unit"] == "QZZ" and res["notes"] == []
    assert "total_other_unit" not in res["values"] and "total_other_unit_millions" not in res["values"]


# --- 7. the request -------------------------------------------------------------------------------------------------


def test_the_request_holds_the_pairs_of_every_installed_pack(h, job):
    c = live3(h)
    sent = sent_requests(h, c)
    assert sent[0]["locale"]["currency_pairs"] == shipped_pairs() + LIVE_PAIRS
    assert sent[0]["locale"]["fold"] == locales.view(c, every=True)["fold"]
    assert sent[0]["business"] and sent[0]["check_id"] == "finance.reconcile"


def test_a_profile_list_that_leaves_a_pack_out_still_sends_its_pairs_after_those_of_the_active_packs(h, job):
    live3(h)
    set_profile(h, locales=["t-scale"])
    assert locales.active(ctx_of(h)) == ["t-scale", "en"]
    sent = sent_requests(h, ctx_of(h))
    assert sent[0]["locale"]["currency_pairs"] == [SCALE_PAIR] + shipped_pairs() + [MINOR_PAIR, ODD_PAIR]


def test_the_view_is_read_once_for_both_keys(h, job, monkeypatch):
    c = live3(h)
    calls = spy_views(monkeypatch)
    sent = sent_requests(h, c)
    assert sent[0]["locale"]["currency_pairs"] == shipped_pairs() + LIVE_PAIRS and "fold" in sent[0]["locale"]
    assert len(calls) == 1


@once
def test_the_key_is_always_sent_an_empty_list_means_no_relation(h, job, tmp_path, monkeypatch):
    without_ir(tmp_path, monkeypatch)
    assert shipped_pairs() == []
    sent = sent_requests(h)
    assert sent[0]["locale"]["currency_pairs"] == [] and "fold" in sent[0]["locale"]


# --- 8. the check process -------------------------------------------------------------------------------------------

CONVERT = '''
import json, sys
sys.path.insert(0, %r)
from decimal import Decimal
from harness_core import checklib, locales, localize
views, reads = [], []
real_view, real_installed = locales.view, locales.installed
locales.view = lambda *a, **k: views.append(1) or real_view(*a, **k)
locales.installed = lambda *a, **k: reads.append(1) or real_installed(*a, **k)

def fn(req):
    if "use" in req:
        localize.use_pairs(req["use"])
    out = []
    for amount, unit, target, *rest in req["convert"]:
        try:
            out.append({"v": str(localize.to_unit(Decimal(amount), unit, target, *rest))})
        except ValueError as e:
            out.append({"err": str(e)})
    return "pass", {"out": out, "views": len(views), "reads": len(reads)}, []

checklib.main(fn)
''' % str(ROOT / "lib")


def process(tmp_path, request, env=None):
    """Run a check that converts ``request['convert']``: ``/usr/bin/python3 -I``, stdin is the request, an empty
    environment. The values hold the results (a Decimal as text, or the text of the ValueError) and the pack reads."""
    script = tmp_path / "check_convert.py"
    script.write_text(CONVERT, "utf-8")
    p = subprocess.run([PY, "-I", str(script)], input=json.dumps(request).encode("utf-8"),
                       env={} if env is None else env, cwd=str(tmp_path), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=120)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["values"]


def results(values):
    return [Decimal(x["v"]) if "v" in x else x["err"] for x in values["out"]]


def test_a_request_with_pairs_converts_with_them_and_reads_no_pack(tmp_path):
    req = {"convert": [["2.5", "QDA", "QDC"], ["250", "QDC", "QDA"], ["3.5", "QQA", "QQB"], ["14", "QQB", "QQA"],
                       ["2.5", "QKY", "QYN"], ["2500", "QYN", "QKY"], ["1", "QDA", "QYN"]],
           "locale": {"fold": {}, "currency_pairs": LIVE_PAIRS}}
    got = process(tmp_path, req)
    assert results(got) == [Decimal(250), Decimal("2.5"), Decimal("24.5"), Decimal(2), Decimal(2500), Decimal("2.5"),
                            "cannot convert QDA to QYN"]
    assert got["views"] == 0 and got["reads"] == 0


def test_the_pairs_of_the_request_are_the_whole_table_a_live_pair_is_not_added_to_the_shipped_ones(tmp_path):
    got = process(tmp_path, {"convert": [["1", "QDA", "QDC"], ["1", "QQA", "QQB"]],
                             "locale": {"fold": {}, "currency_pairs": [MINOR_PAIR]}})
    assert results(got) == [Decimal(100), "cannot convert QQA to QQB"]


@once
def test_a_request_with_an_empty_list_has_no_relation_even_for_a_shipped_pair(tmp_path):
    got = process(tmp_path, {"convert": [["1", "IRT", "IRR"], ["10", "IRR", "IRT"]],
                             "locale": {"fold": {}, "currency_pairs": []}})
    assert results(got) == ["cannot convert IRT to IRR", "cannot convert IRR to IRT"]
    assert got["views"] == 0 and got["reads"] == 0


@once
def test_a_request_with_only_the_pairs_of_other_packs_has_no_shipped_pair_either(tmp_path):
    got = process(tmp_path, {"convert": [["1", "IRT", "IRR"]], "locale": {"fold": {}, "currency_pairs": LIVE_PAIRS}})
    assert results(got) == ["cannot convert IRT to IRR"]


@once
@pytest.mark.parametrize("locale", [{"fold": {"letters": {}}}, None], ids=["only-fold", "no-locale"])
def test_a_request_without_pairs_converts_with_the_shipped_pairs_under_python_I_and_no_environment(tmp_path, locale):
    req = {"convert": [["5", "IRT", "IRR"], ["500", "IRR", "IRT"], ["1", "QDA", "QDC"]]}
    if locale is not None:
        req["locale"] = locale
    got = process(tmp_path, req, env={})
    # the record of 0.4.0 replays: the shipped relation, and no live pair (the sandbox cannot read the data root)
    assert results(got) == [Decimal(50), Decimal(50), "cannot convert QDA to QDC"]


@pytest.mark.parametrize("locale", [{"fold": {"letters": {}}}, None], ids=["only-fold", "no-locale"])
def test_the_shipped_pairs_are_read_once_per_process(tmp_path, locale):
    req = {"convert": [["1", "QDA", "QDC"]] * 50}   # unrelated codes: the table is needed for every one of them
    if locale is not None:
        req["locale"] = locale
    got = process(tmp_path, req)
    assert results(got) == ["cannot convert QDA to QDC"] * 50 and got["reads"] == 1


def test_use_pairs_sets_the_table_of_the_process_and_pairs_given_override_it(tmp_path):
    req = {"use": [ODD_PAIR],
           "convert": [["2", "QQA", "QQB"], ["1", "QDA", "QDC"], ["1", "QDA", "QDC", [MINOR_PAIR]],
                       ["2", "QQA", "QQB", []]]}
    assert results(process(tmp_path, req)) == [Decimal(14), "cannot convert QDA to QDC", Decimal(100),
                                               "cannot convert QQA to QQB"]


def test_a_request_with_pairs_and_no_fold_is_an_error_exit(tmp_path):
    script = tmp_path / "check_convert.py"
    script.write_text(CONVERT, "utf-8")
    p = subprocess.run([PY, "-I", str(script)], env={}, cwd=str(tmp_path), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=120,
                       input=json.dumps({"convert": [], "locale": {"currency_pairs": []}}).encode("utf-8"))
    assert p.returncode == 3 and p.stdout == b"" and b"check error" in p.stderr


def test_the_frozen_channel_the_replay_gets_the_pairs_the_first_run_had(h, job):
    c = live3(h)
    business.set_field(c, "currency", "QDA")
    first = check(h, {"unit": "QDC"}, amounts(1))
    assert first["result"] == "pass" and first["values"]["total"] == 40000
    for name in ("t-minor", "t-odd", "t-scale"):
        (h["data"] / "control" / "locales" / f"{name}.json").unlink()   # the live packs are gone: the data root changed
    p, log = log_of(h, job)
    row = Outputs(p, log).rows()[-1]
    assert oracle.replay(p.run, row, time.time() + 240) is None   # the same result and the same values
    gone = check(h, {"unit": "QDC"}, amounts(1), attempt=2)
    assert gone["result"] == "error"   # control: without the pair the same question is not answered at all


# --- 9. the owner's settings ----------------------------------------------------------------------------------------

LIMITS = {"caps.task": 7000000, "caps.month": 21000000, "thresholds.money_at_stake_high": 14000000, "fx.USD": 700000}


def set_limits(c, currency):
    business.set_field(c, "currency", currency)
    for field, v in LIMITS.items():
        business.set_field(c, field, v)


@pytest.mark.parametrize("down", [False, True], ids=["from-to-to", "to-to-from"])
@pytest.mark.parametrize("frm,to,f", PAIRS)
def test_a_currency_change_across_a_live_pair_converts_limits_threshold_and_rate(h, frm, to, f, down):
    live3(h)
    src, dst = (to, frm) if down else (frm, to)
    set_limits(ctx_of(h), src)
    business.set_field(ctx_of(h), "currency", dst)
    prof = business.profile(ctx_of(h))
    k = (lambda v: v // f) if down else (lambda v: v * f)
    assert prof["currency"] == dst
    assert prof["caps"] == {"task": k(7000000), "month": k(21000000), "currency": dst}
    assert prof["thresholds"] == {"money_at_stake_high": k(14000000)}
    assert prof["fx"]["USD"] == k(700000) and sorted(prof["fx"]) == ["USD", "as_of"]


@pytest.mark.parametrize("src,dst", UNRELATED)
def test_unrelated_codes_drop_the_limits_threshold_and_rate(h, src, dst):
    live3(h)
    set_limits(ctx_of(h), src)
    business.set_field(ctx_of(h), "currency", dst)
    prof = business.profile(ctx_of(h))
    assert prof["currency"] == dst and prof["caps"] == {"currency": dst}
    assert "money_at_stake_high" not in prof["thresholds"] and "USD" not in prof["fx"]


def test_no_old_currency_drops_the_limits_even_when_the_new_code_has_a_pair(h):
    live3(h)
    c = ctx_of(h)
    for field, v in LIMITS.items():
        business.set_field(c, field, v)
    business.set_field(ctx_of(h), "currency", "QDC")
    prof = business.profile(ctx_of(h))
    assert prof["currency"] == "QDC" and prof["caps"] == {"currency": "QDC"}
    assert "money_at_stake_high" not in prof["thresholds"] and "USD" not in prof["fx"]


@pytest.mark.parametrize("code", ["QDA", "QYN"])
def test_writing_the_code_that_is_already_set_keeps_everything(h, code):
    live3(h)
    set_limits(ctx_of(h), code)
    before = business.profile(ctx_of(h))
    business.set_field(ctx_of(h), "currency", code)
    assert business.profile(ctx_of(h)) == before and before["caps"] == {"task": 7000000, "month": 21000000,
                                                                       "currency": code}


@pytest.mark.parametrize("a,b,f", [("QDA", "QDC", 100), ("QQA", "QQB", 7)])
def test_a_round_trip_across_a_pair_gives_back_the_figures(h, a, b, f):
    live3(h)
    set_limits(ctx_of(h), a)
    business.set_field(ctx_of(h), "currency", b)
    business.set_field(ctx_of(h), "currency", a)
    prof = business.profile(ctx_of(h))
    assert prof["caps"] == {"task": 7000000, "month": 21000000, "currency": a}
    assert prof["thresholds"] == {"money_at_stake_high": 14000000} and prof["fx"]["USD"] == 700000


@pytest.mark.parametrize("old,new,drops", [
    ("QDA", "QDC", False), ("QDC", "QDA", False), ("QKY", "QYN", False), ("QQB", "QQA", False),
    ("QDA", "QKY", True), ("QQB", "QDC", True), ("QDA", "QDA", False), (None, "QDC", True),
    ("QZZ", "QZZ", False), ("QZZ", "QDA", True), ("QDA", "QZZ", True)])
def test_the_warning_appears_exactly_when_the_limits_would_be_dropped(h, old, new, drops):
    live3(h)
    c = ctx_of(h)
    if old:
        business.set_field(c, "currency", old)
    business.set_field(ctx_of(h), "caps.task", 7000000)
    shown = typed(h, f"/dynamic-harness:business set currency {new.lower()}", "w1")
    assert (WARNING in shown) is drops
    business.set_field(ctx_of(h), "currency", new)   # and set_field really drops them when the warning was shown
    assert ("task" not in business.profile(ctx_of(h))["caps"]) is drops


def test_the_owner_command_converts_across_a_live_pair_and_shows_no_warning(h):
    live3(h)
    business.set_field(ctx_of(h), "currency", "QQA")
    business.set_field(ctx_of(h), "caps.task", 700000)
    shown = typed(h, "/dynamic-harness:business set currency qqb", "c1")
    assert "Set your currency to QQB (QQB)." in shown and WARNING not in shown
    confirm(h, "business", shown, "c2")
    assert business.profile(ctx_of(h))["caps"] == {"task": 4900000, "currency": "QQB"}


@once
def test_the_pinned_conversion_of_the_regional_pair_does_not_depend_on_the_profile_list(h):
    business.change_list(ctx_of(h), "locales", "en", True)
    assert locales.active(ctx_of(h)) == ["en"]
    set_limits(ctx_of(h), "IRT")
    business.set_field(ctx_of(h), "currency", "IRR")
    prof = business.profile(ctx_of(h))
    assert prof["caps"] == {"task": 70000000, "month": 210000000, "currency": "IRR"}
    assert prof["thresholds"] == {"money_at_stake_high": 140000000} and prof["fx"]["USD"] == 7000000


# --- 10. text for the owner -----------------------------------------------------------------------------------------


def test_a_currency_that_is_not_three_letters_is_refused_with_the_new_example():
    with pytest.raises(Denied) as e:
        business.parse_value("currency", "toman")
    assert str(e.value) == "currency is a 3-letter code, for example USD, EUR or JPY"


def test_the_hint_for_a_missing_currency_has_a_placeholder_and_no_code(h):
    shown = typed(h, "/dynamic-harness:business set caps.task 500000", "t1")
    assert "- Note: spending limits apply only once your currency is set (business set currency <code>)" in shown


# --- 11. no code path needs a shipped regional entry ----------------------------------------------------------------

NOIR = '''
import json, sys
sys.path.insert(0, %r)
from decimal import Decimal
from pathlib import Path
from harness_core import localize, protected
protected.DEFAULTS_DIR = Path(sys.argv[1])
try:
    localize.to_unit(Decimal(1), "IRT", "IRR")
    out = "converted"
except ValueError as e:
    out = str(e)
print(json.dumps(out))
''' % str(ROOT / "lib")


@once
def test_without_the_regional_pack_nothing_converts_and_the_names_are_the_codes(tmp_path, monkeypatch):
    d = without_ir(tmp_path, monkeypatch)
    assert templates.currency("IRT", "en") == "IRT" and templates.currency("IRR", "fa") == "IRR"
    assert templates.currency("USD", "en") == "US dollars"   # the other packs are still there
    script = tmp_path / "noir.py"
    script.write_text(NOIR, "utf-8")
    p = subprocess.run([PY, "-I", str(script), str(d)], env={}, cwd=str(tmp_path), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=120)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    assert json.loads(p.stdout.decode("utf-8")) == "cannot convert IRT to IRR"


# --- 12. EVERY ------------------------------------------------------------------------------------------------------


def test_the_view_adds_the_currency_keys_of_a_pack_the_profile_does_not_name(h):
    live3(h)
    set_profile(h, locales=["en"])
    c = ctx_of(h)
    default, every = locales.view(c), locales.view(c, every=True)
    assert "QDA" not in default["currencies"] and "currency_pairs" not in default
    assert every["currencies"]["QDA"] == {"decimals": 2, "names": {"en": "quidas"}}
    assert every["currencies"]["QYN"] == {"decimals": 0, "names": {LA: "qynla", LB: "qynlb"}}
    assert every["currency_pairs"][-3:] == LIVE_PAIRS
    assert every["words"] == default["words"]   # and nothing else of the packs that are not named


def test_a_profile_list_that_leaves_the_pack_out_still_converts_the_limits_and_shows_no_warning(h):
    live3(h)
    business.change_list(ctx_of(h), "locales", "en", True)
    assert locales.active(ctx_of(h)) == ["en"]
    set_limits(ctx_of(h), "QQA")
    assert WARNING not in typed(h, "/dynamic-harness:business set currency qqb", "e1")
    business.set_field(ctx_of(h), "currency", "QQB")
    prof = business.profile(ctx_of(h))
    assert prof["caps"] == {"task": 49000000, "month": 147000000, "currency": "QQB"}
    assert prof["thresholds"] == {"money_at_stake_high": 98000000} and prof["fx"]["USD"] == 4900000


@once
def test_a_profile_list_with_the_base_pack_only_loses_no_shipped_relation(h):
    business.change_list(ctx_of(h), "locales", "en", True)
    c = ctx_of(h)
    assert locales.view(c).get("currency_pairs", []) == []   # ir is not active ...
    assert locales.view(c, every=True)["currency_pairs"] == [pair("IRT", "IRR", 10)]   # ... its relation still applies


@once
def test_the_finance_check_converts_the_regional_pair_when_the_profile_names_the_base_pack_only(h, job):
    business.change_list(ctx_of(h), "locales", "en", True)
    business.set_field(ctx_of(h), "currency", "IRT")
    res = check(h, {"unit": "IRR"}, amounts(1))
    assert res["result"] == "pass" and res["values"]["total"] == 400000 and res["values"]["total_other_unit"] == 4000000


# --- 13. a currency with no entry anywhere --------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "fa", LA])
def test_a_currency_that_no_pack_names_is_its_code_in_every_language(h, lang):
    assert templates.currency("QZZ", lang, live3(h)) == "QZZ"


def test_a_currency_that_no_pack_names_has_no_relation():
    pairs = locales.view(None, every=True).get("currency_pairs", []) + LIVE_PAIRS
    assert L.to_unit(Decimal(5), "QZZ", "QZZ", pairs=pairs) == 5
    for a, b in (("QZZ", "QDA"), ("QDA", "QZZ"), ("QZZ", "QYN")):
        with pytest.raises(ValueError) as e:
            L.to_unit(Decimal(5), a, b, pairs=pairs)
        assert str(e.value) == f"cannot convert {a} to {b}"


def test_the_same_code_again_keeps_the_limits_of_a_currency_that_no_pack_names(h):
    live3(h)
    set_limits(ctx_of(h), "QZZ")
    before = business.profile(ctx_of(h))
    business.set_field(ctx_of(h), "currency", "QZZ")
    assert business.profile(ctx_of(h)) == before and before["caps"] == {"task": 7000000, "month": 21000000,
                                                                       "currency": "QZZ"}


@pytest.mark.parametrize("src,dst", [("QZZ", "QDA"), ("QDA", "QZZ"), ("QZZ", "QZY")])
def test_a_change_to_or_from_a_currency_that_no_pack_names_drops_them(h, src, dst):
    live3(h)
    set_limits(ctx_of(h), src)
    shown = typed(h, f"/dynamic-harness:business set currency {dst}", "z1")
    assert WARNING in shown
    business.set_field(ctx_of(h), "currency", dst)
    prof = business.profile(ctx_of(h))
    assert prof["caps"] == {"currency": dst} and "money_at_stake_high" not in prof["thresholds"]
    assert "USD" not in prof["fx"]


# --- 14. view reads -------------------------------------------------------------------------------------------------


def test_the_hook_side_calls_make_the_same_view_calls_with_currency_data_in_the_packs(h, monkeypatch):
    put(h, "xx.json", pack("xx", currencies=cur("QDA"), currency_pairs=[MINOR_PAIR], question_marks=["⸮"]))
    calls = spy_views(monkeypatch)
    c = ctx_of(h)
    for name, call, n in (("probe.run", lambda: probe.run("please send the invoice to Sara?", c), 1),
                          ("probe.words", lambda: probe.words(c), 1),
                          ("gate.lexicon", lambda: gate.lexicon(c), 1),
                          ("lexicon_hits", lambda: stopgate.lexicon_hits(c, "all checked\nverified"), 1),
                          ("asks", lambda: stopgate.asks(c, "done?"), 1)):
        del calls[:]
        call()
        assert len(calls) == n, name
    del calls[:]
    out = stopgate._t0({}, c, SID, {}, "The invoice is 20 dollars.")
    assert out["decision"] == "block" and len(calls) == 2


def test_the_templates_calls_that_the_existing_pins_count_make_no_currency_read(h, monkeypatch):
    c = live3(h)
    say(h, "فروش", "v1")
    calls = spy_views(monkeypatch)
    templates.render("stopped_budget", c, lang="fa", done=1, total=2, more="x")
    templates.language(c)
    templates.state_line("Done, not fully checked: lexicon; known mode; x", c, "fa")
    assert len(calls) == 3   # one read per public call, as test_user_text pins: none of them asks for a currency name
    del calls[:]
    templates.currency("QDA", "en", c)
    assert len(calls) == 1   # and the name of a currency is one read of its own

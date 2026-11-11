"""User-facing text: the template lint, Persian rendering, the localised state line (build phase P5)."""
from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from harness_core import lint_text, stopgate, templates
from test_budget import blocked, log_of, set_cap
from test_p3_hooks import ROOT, SID, compiled, ctx_of, h, post, say  # noqa: F401  (fixture h)


def test_the_shipped_templates_pass_the_lint():
    assert lint_text.lint() == []
    r = subprocess.run([sys.executable, "-I", str(ROOT / "bin" / "harness-lint-user-text")], capture_output=True)
    assert r.returncode == 0


def test_the_lint_finds_internal_terms_missing_keys_and_arabic_letters(monkeypatch):
    real = templates.load

    def fake(lang):
        t = dict(real(lang))
        if lang == "en":
            t["refused"] = "The gate refused this card at tier T2."
        else:
            t.pop("handoff")
            t["disconnect"] = "ارتباط قطع شد — كارتان ذخيره شد."
        return t
    monkeypatch.setattr(templates, "load", fake)
    found = "\n".join(lint_text.lint())
    for want in ("'gate'", "'card'", "'T2'", "fa: missing handoff", "Arabic", "dash"):
        assert want in found


def test_model_hints_are_not_owner_text():
    assert lint_text.INTERNAL.search(templates.load("en")["model.pending_card"])  # it names harness-compile
    assert "model.pending_card" not in templates.load("fa")


def _farsi(h):
    (h["data"] / "business").mkdir(exist_ok=True)
    (h["data"] / "business" / "profile.json").write_text(json.dumps({"language": "fa", "currency": "IRT"}))


def test_persian_numbers_and_state_lines(h):
    _farsi(h)
    c = ctx_of(h)
    assert templates.render("state.partly", c, done=9, total=14) == "بخشی انجام شد: ۹ از ۱۴"
    assert templates.state_line("Checked", c) == "بررسی‌شده"
    assert templates.state_line("Done, not fully checked: numbers_untraced", c) == \
        "انجام شد، ولی کامل بررسی نشد: برخی عددها به هیچ بررسی‌ای نمی‌رسند"
    assert templates.state_line("Partly done: 2 of 3", c) == "بخشی انجام شد: ۲ از ۳"


def test_the_language_follows_the_owners_words_without_a_profile(h):
    say(h, "فروش اسفند چقدر بود؟", "fa1")
    assert templates.language(ctx_of(h)) == "fa"
    say(h, "How much did we sell in March?", "en1")
    assert templates.language(ctx_of(h)) == "en"


def test_a_persian_owner_gets_a_persian_opening_and_budget_stop(h):
    _farsi(h)
    job = compiled(h, text="سفارش‌های این هفته چند تا بود؟")
    p, log = log_of(h, job)
    assert (p.run / "opening.txt").read_text().startswith("این را آماده می‌کنم:")
    set_cap(h, job, 1)
    post(h, "Read", file_path=str(h["ws"] / "orders.csv"))
    res = post(h, "Read", file_path=str(h["ws"] / "orders.csv"))
    assert blocked(res) and res[1]["systemMessage"].startswith("از ۱ مورد، ۰ مورد را انجام دادم")


def test_the_persian_state_line_is_allowed_in_the_final_message(h):
    _farsi(h)
    c = ctx_of(h)
    line = templates.state_line("Checked", c)
    assert stopgate.lexicon_hits(c, line) != []  # a state word on its own line...
    assert stopgate.lexicon_hits(c, line, allowed=[line]) == []  # ...is fine only as the rendered state line


def test_the_plan_the_owner_sees_is_in_their_language(h):
    from harness_core import contact
    from test_p3_hooks import CARD
    _farsi(h)
    compiled(h, card=dict(CARD, task_type="investigation", oracle={"type": "research.check", "executable": True,
                                                                   "criteria": ["x"]}), text="قیمت فولاد بالا رفت؟")
    plan = {"hypotheses": ["قیمت بالا رفت"], "queries": ["قیمت فولاد"], "urls": ["https://a.example.com/p"],
            "page_budget": 2, "stop_rule": "دو منبع", "load_bearing": []}
    out = contact.apply({"tool": "contact", "type": "plan", "plan": plan}, ctx_of(h), {"session_id": SID})
    assert out["text"].startswith("پیش از شروع، برنامهٔ جست‌وجو این است") and "حداکثر ۲ صفحه" in out["text"]
    assert Path(templates.ROOT / "fa.json").is_file()


# --- P1 task 3: the language list and the rendering data come from files ------------------------------------------

import shutil  # noqa: E402

import pytest  # noqa: E402

from harness_core import business, locales, protected  # noqa: E402
import os  # noqa: E402

from harness_core.errors import Denied, LocaleError  # noqa: E402
from test_locales import live_dir, pack, put, set_profile  # noqa: E402

SHIPPED_TEMPLATES = ROOT / "templates"
FA_MORE = " قدم دیگر"  # " more steps" in Persian (templates/fa.json more.calls)
EL_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
EL_RENDER = {"el": {"digits": EL_DIGITS, "group": "·", "joiner": " / ", "script": ["ͰϿ"],
                    "direction": "ltr", "lint": [{"chars": "ς", "message": "final sigma"}]}}
EL_TEXT = {"state.checked": "OK-el", "state.not_fully": "NF: {what}", "state.partly": "P {done}/{total}",
           "more.calls": "{n} calls", "reason.lexicon": "R-LEX", "reason.known_mode": "R-KM",
           "note.left_out_undated": "gone: {n}", "note.sources_disagree": "{x}|{b}|{y}|{gap}"}


def template_dir(tmp_path, monkeypatch, **files):
    """A copy of the shipped template files plus ``name=dict`` extra files; ``templates.ROOT`` points at it."""
    d = tmp_path / "templates"
    shutil.copytree(SHIPPED_TEMPLATES, d)
    for name, table in files.items():
        (d / f"{name}.json").write_text(json.dumps(table), "utf-8")
    monkeypatch.setattr(templates, "ROOT", d)
    return d


def shipped_with(tmp_path, monkeypatch, pid, extra, name="defaults"):
    """The shipped locale packs plus one more shipped pack ``pid`` (what the lint reads: no context)."""
    d = tmp_path / name
    shutil.copytree(protected.DEFAULTS_DIR, d)
    (d / "locales" / f"{pid}.json").write_text(json.dumps(pack(pid, **extra)), "utf-8")
    monkeypatch.setattr(protected, "DEFAULTS_DIR", d)


def count_views(monkeypatch):
    calls = []
    real = locales.view
    monkeypatch.setattr(locales, "view", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


# 1. the language list

def test_langs_are_the_stems_of_the_json_files_with_the_base_first(tmp_path, monkeypatch):
    assert templates.langs() == ["en", "fa"]
    assert not hasattr(templates, "LANGS")
    d = tmp_path / "t"
    d.mkdir()
    monkeypatch.setattr(templates, "ROOT", d)
    for name in ("zz", "0b", "fa", "xx", "en", "aa", "a" * 32):
        (d / f"{name}.json").write_text("{}")
    for name in ("Bad.json", "a.b.json", "x y.json", "a" * 33 + ".json", "café.json", ".json", "yy.txt",
                 "yy.json.bak", "UP.JSON", "noext"):
        (d / name).write_text("{}")
    assert templates.langs() == ["en", "0b", "aa", "a" * 32, "fa", "xx", "zz"]
    (d / "en.json").unlink()
    assert templates.langs() == ["0b", "aa", "a" * 32, "fa", "xx", "zz"]  # no base file: sorted only
    (d / "new.json").write_text("{}")
    assert "new" in templates.langs()  # the folder is read at each call
    monkeypatch.setattr(templates, "ROOT", tmp_path / "missing")
    assert templates.langs() == []


# 2. what the present code writes, pinned as literals (recorded at f2b8dac)

@pytest.mark.parametrize("value,fa,en", [
    (1234567, "۱٬۲۳۴٬۵۶۷", "1234567"),
    (1234.5, "۱٬۲۳۴٫۵", "1234.5"),
    (Decimal("1234567.25"), "۱٬۲۳۴٬۵۶۷٫۲۵", "1234567.25"),
    (True, "True", "True"),
    (0, "۰", "0"),
    (12, "۱۲", "12"),
])
def test_numbers_are_written_with_the_digits_of_the_language(value, fa, en):
    assert templates.render("more.calls", lang="fa", n=value) == fa + FA_MORE
    assert templates.render("more.calls", lang="en", n=value) == en + " more steps"


def test_two_numbers_in_one_sentence_and_a_string_value_left_alone():
    assert templates.render("opening.estimate", lang="fa", lo=1.5, hi=Decimal("20000.75"), money="") == \
        "کارهای مشابه حدود " \
        "۱٫۵ تا ۲۰٬۰۰۰٫۷۵ " \
        "دقیقه طول کشیده‌اند."
    assert templates.render("more.calls", lang="fa", n="12") == "12" + FA_MORE  # strings are left alone


STATE_FA = "انجام شد، ولی کامل " \
           "بررسی نشد: "
R_NUMBERS = "برخی عددها به هیچ " \
            "بررسی‌ای نمی‌رسند"
R_LEXICON = "متن، بررسی را خودش " \
            "تأیید کرده است"
R_KNOWN = "همهٔ بخش‌ها بررسی " \
          "اجرایی نداشتند"
R_NOEXEC = "بررسی اجرایی نداشت"
SEMI = "؛ "


@pytest.mark.parametrize("state,want", [
    ("Partly done: 2 of 5", "بخشی انجام شد: ۲ از ۵"),
    ("Done, not fully checked: numbers_untraced", STATE_FA + R_NUMBERS),
    ("Done, not fully checked: lexicon", STATE_FA + R_LEXICON),
    ("Done, not fully checked: known mode", STATE_FA + R_KNOWN),
    ("Done, not fully checked: no executable check", STATE_FA + R_NOEXEC),
    ("Done, not fully checked: lexicon; known mode", STATE_FA + R_LEXICON + SEMI + R_KNOWN),
    ("Done, not fully checked: something else", STATE_FA + "something else"),
    ("Done, not fully checked: lexicon; something else; no executable check",
     STATE_FA + R_LEXICON + SEMI + "something else" + SEMI + R_NOEXEC),
    ("Odd state", "Odd state"),
    ("", ""),
])
def test_the_persian_state_line_pinned(state, want):
    assert templates.state_line(state, lang="fa") == want


def test_the_english_state_line_is_the_state():
    for s in ("Checked", "Partly done: 2 of 5", "Done, not fully checked: lexicon; known mode"):
        assert templates.state_line(s, lang="en") == s


def test_check_notes_in_persian_pinned():
    def entry(code, **params):
        return {"code": code, "params": params}
    assert templates.note(entry("left_out_undated", n="12"), "EN", lang="fa") == \
        "کنار گذاشته شد: ۱۲ " \
        "ردیف بدون تاریخ"
    got = templates.note(entry("sources_disagree", x="10", b="B7", y="20", gap="5"), "EN", lang="fa")
    assert got == "منبع‌ها با هم " \
        "نمی‌خوانند: این فایل " \
        "۱۰ را نشان می‌دهد و B7 " \
        "عدد ۲۰ را؛ اختلاف ۵ " \
        "توضیحی ندارد"  # b keeps its digit; the others change
    assert templates.note(entry("left_out_undated", n=12), "EN", lang="fa") == \
        templates.note(entry("left_out_undated", n="12"), "EN", lang="fa")
    assert templates.note(entry("left_out_undated", n="12"), "EN", lang="en") == "EN"
    assert templates.note(entry("no_such_code", n="12"), "EN", lang="fa") == "EN"


# 3. language()

def test_the_profile_language_wins_when_a_template_file_exists(h):
    set_profile(h, language="fa")
    say(h, "How much did we sell in March?", "t1")
    assert templates.language(ctx_of(h)) == "fa"
    set_profile(h, language="en")
    say(h, "فروش اسفن چقدر بود؟", "t2")
    assert templates.language(ctx_of(h)) == "en"


def test_a_profile_language_without_a_template_file_is_ignored(h):
    set_profile(h, language="zz")
    assert templates.language(ctx_of(h)) == "en"
    say(h, "فروش اسفن چقدر بود؟", "t1")
    assert templates.language(ctx_of(h)) == "fa"


@pytest.mark.parametrize("text,want", [
    ("فروش اسفن", "fa"),
    ("abc ابجد", "fa"),       # 4 Arabic-script letters against 3 Latin
    ("abcde ابج", "en"),           # 3 against 5
    ("ab اب", "en"),                    # a tie goes to the base language
    ("12 34 ... ?", "en"),                        # no letters at all
    ("۱۲۳ x", "fa"),               # Persian digits fall in the Arabic block: 3 against 1
])
def test_the_language_of_a_turn_follows_the_letter_counts(h, text, want):
    say(h, text, "t1")
    assert templates.language(ctx_of(h)) == want


def test_no_turn_gives_the_base_language(h):
    assert templates.language(ctx_of(h)) == "en"
    assert templates.language(None) == "en"


def test_a_broken_live_pack_never_breaks_the_language_or_a_number(h):
    say(h, "فروش اسفن", "t1")
    put(h, "broken.json", b"{")
    c = ctx_of(h)
    assert templates.language(c) == "fa"
    assert templates.render("more.calls", c, n=1234) == "۱٬۲۳۴" + FA_MORE
    assert templates.state_line("Done, not fully checked: lexicon; known mode", c) == \
        STATE_FA + R_LEXICON + SEMI + R_KNOWN
    assert templates.note({"code": "left_out_undated", "params": {"n": "12"}}, "EN", c, "fa").endswith(
        "بدون تاریخ")


def test_the_view_is_read_once_per_public_call(h, monkeypatch):
    c = ctx_of(h)
    say(h, "فروش", "t1")
    calls = count_views(monkeypatch)
    templates.render("stopped_budget", c, lang="fa", done=1, total=2, more="x")
    assert len(calls) == 1
    calls.clear()
    templates.language(c)
    assert len(calls) == 1
    calls.clear()
    templates.state_line("Done, not fully checked: lexicon; known mode; x", c, "fa")
    assert len(calls) == 1
    calls.clear()
    templates.note({"code": "left_out_undated", "params": {"n": "12"}}, "EN", c, "fa")
    assert len(calls) == 1


# 4. a new language with no change in the code

def test_a_new_language_needs_two_data_files_and_no_code(h, tmp_path, monkeypatch):
    template_dir(tmp_path, monkeypatch, el=EL_TEXT)
    say(h, "Γειά σου κόσμε", "g1")
    put(h, "el.json", pack("el", render=EL_RENDER))
    c = ctx_of(h)
    assert templates.langs() == ["en", "el", "fa"]
    assert templates.language(c) == "el"                                   # a turn in its script
    assert templates.render("more.calls", c, n=1234567) == "₁·₂₃₄·₅₆₇ calls"
    assert templates.render("more.calls", c, n=12.5) == "₁₂.₅ calls"   # decimal: the default mark
    assert templates.render("more.calls", c, n="12") == "12 calls"
    assert templates.state_line("Checked", c, "el") == "OK-el"
    assert templates.state_line("Partly done: 2 of 5", c, "el") == "P ₂/₅"
    assert templates.state_line("Done, not fully checked: lexicon; Known--Mode!; odd one", c, "el") == \
        "NF: R-LEX / R-KM / odd one"
    entry = {"code": "sources_disagree", "params": {"x": "10", "b": "B7", "y": "20", "gap": "5"}}
    assert templates.note(entry, "EN", c, "el") == "₁₀|B7|₂₀|₅"
    assert business.parse_value("language", "EL") == "el"
    say(h, "\u03b1\u0627", "g2")  # one letter of each script, no Latin: el and fa tie, the first in langs() order wins
    assert templates.language(c) == "el"
    say(h, "\u03b1\u03b2\u0627 x", "g3")  # el 2, fa 1, en 1
    assert templates.language(c) == "el"
    say(h, "\u03b1\u0627\u0628", "g4")  # el 1, fa 2
    assert templates.language(c) == "fa"
    # a changed pack is seen by the next call in the same process
    put(h, "el.json", pack("el", render={"el": dict(EL_RENDER["el"], digits="ABCDEFGHIJ", joiner=" + ")}))
    assert templates.render("more.calls", ctx_of(h), lang="el", n=12) == "BC calls"
    assert templates.state_line("Done, not fully checked: lexicon; known mode", ctx_of(h), "el") == "NF: R-LEX + R-KM"


def test_a_new_language_is_checked_by_the_lint(tmp_path, monkeypatch):
    en = json.loads((SHIPPED_TEMPLATES / "en.json").read_text("utf-8"))
    template_dir(tmp_path, monkeypatch, el=dict(en, handoff="Hello ς there"))
    shipped_with(tmp_path, monkeypatch, "el", {"render": EL_RENDER})
    assert lint_text.lint() == ["el:handoff: final sigma"]


# 5. numbers

def test_a_language_with_only_a_group_mark_keeps_ascii_digits(h, tmp_path, monkeypatch):
    template_dir(tmp_path, monkeypatch, qq={"x": "<{n}>"})
    put(h, "qq.json", pack("qq", render={"qq": {"group": "'"}}))
    c = ctx_of(h)
    assert templates.render("x", c, lang="qq", n=1234567) == "<1'234'567>"
    assert templates.render("x", c, lang="qq", n=1234.5) == "<1'234.5>"
    assert templates.render("x", c, lang="qq", n=Decimal("1234.25")) == "<1'234.25>"
    assert templates.render("x", c, lang="qq", n=True) == "<True>"


def test_decimal_only_and_digits_only_languages(h, tmp_path, monkeypatch):
    template_dir(tmp_path, monkeypatch, qq={"x": "<{n}>"}, rr={"x": "<{n}>"})
    put(h, "qq.json", pack("qq", render={"qq": {"decimal": ","}}))
    put(h, "rr.json", pack("rr", render={"rr": {"digits": "abcdefghij"}}))
    c = ctx_of(h)
    assert templates.render("x", c, lang="qq", n=12.5) == "<12,5>"
    assert templates.render("x", c, lang="rr", n=1234.5) == "<b,cde.f>"
    assert templates.render("x", c, lang="rr", n="12") == "<12>"


def test_a_language_with_no_number_key_returns_the_value_unchanged(h, tmp_path, monkeypatch):
    template_dir(tmp_path, monkeypatch, qq={"x": "<{n}>"}, ss={"x": "<{n}>"})
    put(h, "qq.json", pack("qq", render={"qq": {"joiner": "|", "direction": "ltr"}}))  # ss has no pack at all
    c = ctx_of(h)
    for lang in ("qq", "ss"):
        assert templates.render("x", c, lang=lang, n=1234567.5) == "<1234567.5>"
        assert templates.render("x", c, lang=lang, n=Decimal("1234567.5")) == "<1234567.5>"
    assert templates.render("more.calls", c, lang="en", n=1234567.5) == "1234567.5 more steps"


def test_without_a_joiner_the_reasons_are_joined_with_a_semicolon(h, tmp_path, monkeypatch):
    template_dir(tmp_path, monkeypatch, qq={"state.not_fully": "{what}", "reason.lexicon": "L"})
    put(h, "qq.json", pack("qq", render={"qq": {"group": "'"}}))
    assert templates.state_line("Done, not fully checked: lexicon; other", ctx_of(h), "qq") == "L; other"


# 6. lint

def _fa_with(monkeypatch, **changes):
    real = templates.load
    monkeypatch.setattr(templates, "load", lambda lang: dict(real(lang), **changes) if lang == "fa" else real(lang))


@pytest.mark.parametrize("ch,msg", [
    ("ي", "Arabic ي or ك instead of Persian ی or ک"),
    ("ك", "Arabic ي or ك instead of Persian ی or ک"),
    ("–", "a dash in Persian prose"),
    ("—", "a dash in Persian prose"),
])
def test_each_forbidden_persian_character_gives_its_message(monkeypatch, ch, msg):
    _fa_with(monkeypatch, handoff="سلام " + ch + " دنیا")
    assert lint_text.lint() == [f"fa:handoff: {msg}"]


@pytest.mark.parametrize("text,bad", [
    ("Hello {n} \u0633\u0644\u0627\u0645", True),
    ("\u03b1\u03b2 {n} \u0633\u0644\u0627\u0645", True),          # any left-to-right letter, not only A-Z
    ("{n} Hello \u0633\u0644\u0627\u0645", True),                  # placeholders are removed first
    ("{n} \u0633\u0644\u0627\u0645 Hello", False),
    ("  \u0633\u0644\u0627\u0645 {n} Hello", False),
    ("123 {n} \u0633\u0644\u0627\u0645", False),
])
def test_a_right_to_left_text_must_not_start_with_a_left_to_right_letter(monkeypatch, text, bad):
    _fa_with(monkeypatch, **{"more.calls": text})
    want = ["fa:more.calls: starts with a left-to-right letter (renders left to right)"] if bad else []
    assert lint_text.lint() == want


def test_the_same_text_in_a_left_to_right_language_is_fine(tmp_path, monkeypatch):
    fa = json.loads((SHIPPED_TEMPLATES / "fa.json").read_text("utf-8"))
    template_dir(tmp_path, monkeypatch, el=dict(fa, handoff="Hello world"))
    shipped_with(tmp_path, monkeypatch, "el", {"render": {"el": {"direction": "ltr"}}})
    assert lint_text.lint() == []
    shipped_with(tmp_path, monkeypatch, "el", {"render": {"el": {"direction": "rtl"}}}, name="rtl")
    assert lint_text.lint() == ["el:handoff: starts with a left-to-right letter (renders left to right)"]


def test_a_language_without_render_data_gets_only_the_general_rules(tmp_path, monkeypatch):
    en = json.loads((SHIPPED_TEMPLATES / "en.json").read_text("utf-8"))
    xx = dict(en)
    xx["refused"] = "The gate refused this."
    xx["more.calls"] = "{m} more"
    xx["opening.T0"] = "A ي — ك x"
    del xx["handoff"]
    template_dir(tmp_path, monkeypatch, xx=xx)
    assert sorted(lint_text.lint()) == sorted([
        "xx:refused: internal term 'gate'",
        "xx:more.calls: placeholders ['m'] differ from en ['n']",
        "xx: missing handoff"])


# 7. business

def test_the_language_field_accepts_the_languages_of_the_template_files():
    assert business.parse_value("language", "fa") == "fa"
    assert business.parse_value("language", " EN ") == "en"
    with pytest.raises(Denied) as e:
        business.parse_value("language", "zz")
    assert str(e.value) == "language is one of: en, fa"


# 10. import order

@pytest.mark.parametrize("first", ["business", "templates", "locales"])
def test_each_module_imports_first_in_a_fresh_process(first):
    code = f"import sys; sys.path.insert(0, {str(ROOT / 'lib')!r}); import harness_core.{first}"
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- P1 task 3, fix round 1 ------------------------------------------------------------------------------------------

PERSIAN_TURN = "\u0641\u0631\u0648\u0634 \u0627\u0633\u0641\u0646"
EN_HANDOFF = "I've paused this task so you can answer when ready. Reply here to continue."


def broken_shipped(tmp_path, monkeypatch, stem="fa"):
    """The shipped locale packs with one of them broken (what the code reads when it has no context)."""
    d = tmp_path / "defaults-broken"
    shutil.copytree(protected.DEFAULTS_DIR, d)
    (d / "locales" / f"{stem}.json").write_text("{")
    monkeypatch.setattr(protected, "DEFAULTS_DIR", d)


# D1. the profile list `locales` picks words and patterns, not how a language is written

def test_the_profile_list_does_not_hide_how_a_language_is_written(h):
    set_profile(h, language="fa", locales=["ir"])
    c = ctx_of(h)
    assert templates.render("more.calls", c, n=1234) == "\u06f1\u066c\u06f2\u06f3\u06f4" + FA_MORE
    assert templates.state_line("Done, not fully checked: lexicon; known mode", c) == \
        STATE_FA + R_LEXICON + SEMI + R_KNOWN
    set_profile(h, locales=["ir"])  # no profile language: the owner's turn decides
    say(h, PERSIAN_TURN, "d1")
    assert templates.language(ctx_of(h)) == "fa"


def test_a_live_pack_not_named_in_the_list_still_supplies_its_render_data(h, tmp_path, monkeypatch):
    template_dir(tmp_path, monkeypatch, el=EL_TEXT)
    say(h, "\u0393\u03b5\u03b9\u03ac \u03c3\u03bf\u03c5", "d2")
    put(h, "el.json", pack("el", render=EL_RENDER))
    set_profile(h, locales=["fa"])
    c = ctx_of(h)
    assert locales.active(c) == ["fa", "en"]  # el is installed but not active
    assert templates.render("more.calls", c, lang="el", n=12) == "\u2081\u2082 calls"
    assert templates.state_line("Done, not fully checked: lexicon; known mode", c, "el") == "NF: R-LEX / R-KM"
    assert templates.language(c) == "el"


# F1. the live-pack folder cannot be read

@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every folder")
def test_an_unreadable_live_pack_folder_never_stops_a_message(h):
    say(h, PERSIAN_TURN, "f1")
    d = live_dir(h)
    c = ctx_of(h)
    d.chmod(0)
    try:
        assert templates.render("more.calls", c, lang="fa", n=1234) == "\u06f1\u066c\u06f2\u06f3\u06f4" + FA_MORE
        assert templates.state_line("Checked", c, "fa") == "\u0628\u0631\u0631\u0633\u06cc\u200c\u0634\u062f\u0647"
        assert templates.state_line("Done, not fully checked: lexicon; known mode", c) == \
            STATE_FA + R_LEXICON + SEMI + R_KNOWN  # the language comes from the turn, through the shipped packs
    finally:
        d.chmod(0o755)


# F2. a call that needs no pack data reads no pack

def test_a_call_that_needs_no_pack_data_makes_no_view_call(h, monkeypatch):
    c = ctx_of(h)
    calls = count_views(monkeypatch)
    assert templates.render("handoff", lang="en") == EN_HANDOFF
    assert templates.render("handoff", c, lang="en") == EN_HANDOFF
    assert templates.render("handoff", c, lang="fa") == templates.load("fa")["handoff"]
    assert templates.language(c) == "en"  # no turn yet
    set_profile(h, language="fa")
    assert templates.language(ctx_of(h)) == "fa"  # the profile decides
    assert calls == []


def test_a_message_without_values_is_written_when_a_shipped_pack_is_broken(tmp_path, monkeypatch):
    broken_shipped(tmp_path, monkeypatch)
    assert templates.render("handoff", lang="en") == EN_HANDOFF
    assert templates.language(None) == "en"  # no turn: nothing to count


# F3. one read when no language is given

def test_the_view_is_read_once_when_no_language_is_given(h, monkeypatch):
    say(h, PERSIAN_TURN, "f3")
    c = ctx_of(h)
    calls = count_views(monkeypatch)
    assert templates.render("more.calls", c, n=1) == "\u06f1" + FA_MORE
    assert len(calls) == 1
    calls.clear()
    assert templates.state_line("Done, not fully checked: lexicon; known mode", c) == \
        STATE_FA + R_LEXICON + SEMI + R_KNOWN
    assert len(calls) == 1
    calls.clear()
    assert templates.note({"code": "left_out_undated", "params": {"n": "12"}}, "EN", c).endswith(
        "\u06f1\u06f2 \u0631\u062f\u06cc\u0641 \u0628\u062f\u0648\u0646 \u062a\u0627\u0631\u06cc\u062e")
    assert len(calls) == 1


# F4. a broken shipped pack is not swallowed

def test_a_broken_shipped_pack_is_an_error_when_pack_data_is_needed(h, tmp_path, monkeypatch):
    broken_shipped(tmp_path, monkeypatch)
    for call in (lambda: templates.render("more.calls", n=1),
                 lambda: templates.render("more.calls", ctx_of(h), lang="fa", n=1),
                 lambda: templates.state_line("Checked", lang="fa"),
                 lambda: templates.note({"code": "left_out_undated", "params": {"n": "1"}}, "EN", lang="fa")):
        with pytest.raises(LocaleError) as e:
            call()
        assert "fa.json" in str(e.value)


def test_only_a_harness_or_os_error_sends_the_message_to_the_shipped_packs(h, monkeypatch):
    real = locales.view

    def broken(ctx=None, **k):  # the live view fails, the shipped one would still work
        if ctx is not None:
            raise RuntimeError("a defect, not a bad file")
        return real(ctx, **k)
    monkeypatch.setattr(locales, "view", broken)
    with pytest.raises(RuntimeError):
        templates.render("more.calls", ctx_of(h), lang="fa", n=1)

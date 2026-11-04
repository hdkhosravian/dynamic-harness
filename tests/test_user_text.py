"""User-facing text: the template lint, Persian rendering, the localised state line (build phase P5)."""
from __future__ import annotations

import json
import subprocess
import sys
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

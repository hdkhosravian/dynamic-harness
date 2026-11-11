"""Matching words come from the locale packs (build phase P1, task T2): pack content, pinned probe results,
``probe.words``, the owner's ``probe.json``, join words as data, a new language without code, one read per call.

New names (``probe.words``, ``locales.EVERY``, ``gate.lexicon``, ``stopgate.asks``) are used inside the tests only,
so that a missing name fails that test and does not hide the others. Non-ASCII characters are written as \\u
escapes; the two fixture files hold the characters as they are."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from harness_core import locales, probe, protected
from harness_core.errors import HarnessError, LocaleError
from harness_core.safety import Ctx
from test_business import confirm, typed
from test_locales import pack, put, set_profile
from test_p3_hooks import ctx_of, h  # noqa: F401  (fixture h)

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures"
OLD = json.loads((FIX / "probe_0_4_0.json").read_text("utf-8"))  # control/defaults/probe.json at 2de23b1
OLD_LEX = json.loads((FIX / "state_lexicon_0_4_0.json").read_text("utf-8"))

CLASSES = ["external_message", "money", "destructive", "automation_create", "local_reversible"]
DECISIONS = ["financial", "tax", "legal", "employment", "medical", "safety"]
LIST_KEYS = ["imperative_lead", "question_lead", "money_words", "quantity_words", "continuation"]
SYMBOLS = ("\u20ac", "\u00a3", "\u2713", "\u2714")  # the four non-ASCII entries that the en pack keeps
IR_MONEY = ["\ufdfc", "toman", "tomans", "rial", "rials", "irr", "irt", "\u062a\u0648\u0645\u0627\u0646",
            "\u0631\u06cc\u0627\u0644"]
WORD_KEYS = {"verbs", "verbs_anywhere", "imperative_lead", "imperative_join", "question_lead", "money_words",
             "quantity_words", "decision_keywords", "continuation", "file_extensions", "question_marks"}
SHIPPED_PROBE = {
    "version": 1,
    "note": "Your own extra words. The shipped words are in the locale packs. A word you add here is matched in "
            "addition to them.",
    "verbs": {c: [] for c in CLASSES},
    "imperative_lead": [], "question_lead": [], "money_words": [], "quantity_words": [],
    "decision_keywords": {c: [] for c in DECISIONS},
    "continuation": [],
    "file_extensions": OLD["file_extensions"],
}


def split(entries, ir=()):
    """The placement rule of the brief: ``ir`` entries to the region pack, the other ASCII entries (and the four
    symbols) to en, the rest to fa; the order of the 0.4.0 file is kept."""
    plain = [w for w in entries if w not in ir]
    return ([w for w in plain if w.isascii() or w in SYMBOLS], [w for w in plain if not (w.isascii() or w in SYMBOLS)],
            [w for w in entries if w in ir])


def words_of(pid):
    return locales.installed()[pid].get("words", {})


def write_probe(h, content):
    d = h["data"] / "control"
    d.mkdir(parents=True, exist_ok=True)
    (d / "probe.json").write_text(content if isinstance(content, str) else json.dumps(content), "utf-8")


# --- 1. pack content -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", LIST_KEYS)
def test_each_shipped_list_is_split_between_the_packs_by_the_placement_rule(key):
    en, fa, ir = split(OLD[key], IR_MONEY if key == "money_words" else ())
    assert words_of("en").get(key, []) == en
    assert words_of("fa").get(key, []) == fa
    assert words_of("ir").get(key, []) == ir
    union = words_of("en").get(key, []) + words_of("fa").get(key, []) + words_of("ir").get(key, [])
    assert sorted(union) == sorted(OLD[key])  # nothing lost, nothing added, nothing twice


@pytest.mark.parametrize("cls", CLASSES)
def test_verbs_and_verbs_anywhere_of_a_class_make_up_the_0_4_0_list(cls):
    en, fa, _ = split(OLD["verbs"][cls])
    assert words_of("en").get("verbs", {}).get(cls) == en
    assert words_of("fa").get("verbs_anywhere", {}).get(cls) == fa
    both = (words_of("en").get("verbs", {}).get(cls, []) + words_of("fa").get("verbs_anywhere", {}).get(cls, []))
    assert sorted(both) == sorted(OLD["verbs"][cls])


@pytest.mark.parametrize("cls", DECISIONS)
def test_decision_keywords_of_a_class_are_split_between_en_and_fa(cls):
    en, fa, _ = split(OLD["decision_keywords"][cls])
    assert words_of("en").get("decision_keywords", {}).get(cls) == en
    assert words_of("fa").get("decision_keywords", {}).get(cls) == fa


def test_the_packs_hold_exactly_the_keys_of_the_brief():
    assert set(words_of("en")) == {"verbs", "imperative_lead", "imperative_join", "question_lead", "money_words",
                                   "quantity_words", "decision_keywords", "continuation"}
    assert set(words_of("fa")) == {"verbs_anywhere", "question_lead", "money_words", "quantity_words",
                                   "decision_keywords", "continuation"}
    assert list(words_of("en")["verbs"]) == CLASSES and list(words_of("fa")["verbs_anywhere"]) == CLASSES
    assert list(words_of("en")["decision_keywords"]) == DECISIONS  # the order of the 0.4.0 file
    assert set(words_of("fa")["decision_keywords"]) == set(DECISIONS)


def test_the_region_pack_ir_holds_the_nine_money_words_and_nothing_else_under_words():
    assert words_of("ir") == {"money_words": IR_MONEY}


def test_the_list_lengths_are_the_ones_of_the_brief():
    en, fa = words_of("en"), words_of("fa")
    assert [len(en[k]) for k in LIST_KEYS] == [13, 21, 25, 12, 6]
    assert [len(fa[k]) for k in LIST_KEYS if k != "imperative_lead"] == [11, 14, 10, 4]
    assert "imperative_lead" not in fa


def test_en_imperative_join_and_both_question_marks_are_as_in_the_brief():
    got = locales.installed()
    assert words_of("en")["imperative_join"] == ["and", "then", "also", "please"]
    assert got["en"]["question_marks"] == ["?"]
    assert got["fa"]["question_marks"] == ["\u061f"]


def test_the_state_lexicon_of_0_4_0_is_split_by_language():
    got = locales.installed()
    assert got["en"]["state_lexicon"] == {"terms": OLD_LEX["terms"]["en"], "template_lines": OLD_LEX["template_lines"]["en"]}
    assert got["fa"]["state_lexicon"] == {"terms": OLD_LEX["terms"]["fa"], "template_lines": OLD_LEX["template_lines"]["fa"]}
    assert "state_lexicon" not in got["ir"]
    assert (len(got["en"]["state_lexicon"]["terms"]), len(got["fa"]["state_lexicon"]["terms"])) == (7, 16)


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)


def _entries(pack_):
    return list(_strings([pack_.get("words", {}), pack_.get("state_lexicon", {}), pack_.get("question_marks", [])]))


def test_every_en_entry_is_ascii_or_one_of_four_symbols_and_no_fa_entry_has_an_ascii_letter():
    got = locales.installed()
    assert [w for w in _entries(got["en"]) if not (w.isascii() or w in SYMBOLS)] == []
    assert len(_entries(got["en"])) > 100  # the check ran over real entries
    assert [w for w in _entries(got["fa"]) if re.search(r"[A-Za-z]", w)] == []
    assert len(_entries(got["fa"])) > 100


def test_the_shipped_probe_json_is_the_owners_empty_lists():
    shipped = json.loads((ROOT / "control" / "defaults" / "probe.json").read_text("utf-8"))
    assert shipped == SHIPPED_PROBE
    assert len(shipped["file_extensions"]) == 16


# --- 2. pinned results of the present code (these pass before and after the change) --------------------------------

DEFAULT = {"schema": "harness/probe@1", "question_only": False, "action_verbs": [], "irreversible_classes": [],
           "write_targets": [], "files_named": [], "connectors": [], "write_connectors": [], "money_tokens": [],
           "number_tokens": [], "quantitative": False, "data_classes": [], "decision_hits": {}, "continuation": False,
           "floor_tier": "T0"}
FILE_TEXTS = ("summarise customers.csv", "update report.md with the new totals", "edit notes.txt")
# {text: the fields that differ from DEFAULT}; recorded from probe.run(text, None) at 2de23b1 (a Ctx for file names)
PINNED = [
    ('send the invoice to Sara',
     {'request_sha': 'b69689f76e815a35fb054cd4c999513757922cfe503d79fe837ef69fcc820836',
      'action_verbs': ['send'],
      'irreversible_classes': ['external_message'],
      'money_tokens': ['invoice'],
      'quantitative': True,
      'floor_tier': 'T2'}),
    ('could you email Sara the total',
     {'request_sha': '1cafd47a7a619575d40ce622f2c910f2d2dab4db3ae51e2507466b520ce6c7e0',
      'action_verbs': ['email'],
      'irreversible_classes': ['external_message'],
      'quantitative': True,
      'floor_tier': 'T2'}),
    ('I want you to delete the old reports and then send the summary',
     {'request_sha': '048b287daa2ae333922ada2e38a175416be6568c3a386b4e4dfd2aea330ebc3e',
      'action_verbs': ['delete', 'send'],
      'irreversible_classes': ['destructive', 'external_message'],
      'floor_tier': 'T2'}),
    ('We reviewed it. Cancel the order',
     {'request_sha': '52a0d001ea3db0fc6275972030e1fa50e50255f52af9d265d8a1e10b9d7c49d6',
      'action_verbs': ['cancel', 'order'],
      'irreversible_classes': ['destructive'],
      'floor_tier': 'T2'}),
    ('did we send the reminders last week?',
     {'request_sha': 'a0efb8c8c57903d4b0283410f10f29c1a47e437f43a8ca8ba801e10c57c61213',
      'question_only': True,
      'action_verbs': ['send']}),
    ('when did we send the box',
     {'request_sha': 'c8e75031e5cb01c8c6d8eb7d0a71c318caa06c714cfb5d5c397abd7a07820d9d',
      'question_only': True,
      'action_verbs': ['send']}),
    ('remind me every day to check stock',
     {'request_sha': 'bc8d90e0f98c8214a0492b30ed9539d3b52a0425a32913d7f01afdc656959f2f',
      'action_verbs': ['every day', 'remind'],
      'irreversible_classes': ['automation_create'],
      'decision_hits': {'financial': ['stock']},
      'floor_tier': 'T2'}),
    ('\u0627\u06cc\u0646 \u0641\u0627\u06a9\u062a\u0648\u0631 \u0631\u0627 \u0628\u0631\u0627\u06cc \u0645\u0634\u062a\u0631\u06cc \u0627\u06cc\u0645\u06cc\u0644 \u06a9\u0646',
     {'request_sha': '1b6ce7be6108b34b1793c7980bcd11a42ba51e5882e5e40c0acfa8afb9413fe0',
      'action_verbs': ['\u0627\u06cc\u0645\u06cc\u0644 \u06a9\u0646'],
      'irreversible_classes': ['external_message'],
      'money_tokens': ['\u0641\u0627\u06a9\u062a\u0648\u0631'],
      'quantitative': True,
      'floor_tier': 'T2'}),
    ('\u0644\u0637\u0641\u0627 \u0627\u06cc\u0646 \u0641\u0627\u06a9\u062a\u0648\u0631 \u0631\u0627 \u0627\u06cc\u0645\u06cc\u0644 \u06a9\u0646 \u0648 \u0628\u0639\u062f \u067e\u0627\u06a9 \u06a9\u0646',
     {'request_sha': '1d7b5cf5626318ae54f5c151c3f8ca7dac90074ea15b788fdf6c6dcd2ced00de',
      'action_verbs': ['\u0627\u06cc\u0645\u06cc\u0644 \u06a9\u0646', '\u067e\u0627\u06a9 \u06a9\u0646'],
      'irreversible_classes': ['destructive', 'external_message'],
      'money_tokens': ['\u0641\u0627\u06a9\u062a\u0648\u0631'],
      'quantitative': True,
      'floor_tier': 'T2'}),
    ('\u0686\u0631\u0627 \u0622\u0633\u0645\u0627\u0646 \u0622\u0628\u06cc \u0627\u0633\u062a\u061f',
     {'request_sha': '35bcaab0c146f0fdc89ec8e468ab8465274e67edb891bc25b2f5a254ecd93743',
      'question_only': True}),
    ('what is the weather like',
     {'request_sha': '437d0981b8c2022c311b61e48e16036028698ba0a58d795768cce7d1e13f0d84',
      'question_only': True}),
    ('who owns this account',
     {'request_sha': '5227d5d9f1390dbf1ea2e67b8b15b85e55b6abde8ef318af5ebb9abe5042df09',
      'question_only': True}),
    ('the total is $5 for the order',
     {'request_sha': '91f93c1cfc8c29574222894bd8d98d5e98126d9e3e520ca22ba2d1d23730c12e',
      'action_verbs': ['order'],
      'money_tokens': ['$'],
      'number_tokens': ['5'],
      'quantitative': True}),
    ('payment was 40 euros',
     {'request_sha': 'd0c04750f5a012b90841088583c3dede0b2b3d4555cfa9dc563c1462458bc886',
      'money_tokens': ['euros', 'payment'],
      'number_tokens': ['40'],
      'quantitative': True}),
    ('\u0642\u06cc\u0645\u062a \u0627\u06cc\u0646 \u06a9\u0627\u0644\u0627 \u0686\u0642\u062f\u0631 \u0627\u0633\u062a',
     {'request_sha': '662802bac77d2207c6059b40ee7f0cd5de9d9d815e4a0fc8b24ba0be907e059b',
      'money_tokens': ['\u0642\u06cc\u0645\u062a'],
      'quantitative': True}),
    ('\u067e\u0631\u062f\u0627\u062e\u062a \u0634\u062f \u06f2\u06f5\u06f0\u06f0\u06f0 \u062a\u0648\u0645\u0627\u0646',
     {'request_sha': '5a3c804694bcdb3c5dbbbfacfb6008c928bc6600fc53269657a4248fe8322aa4',
      'money_tokens': ['\u062a\u0648\u0645\u0627\u0646', '\u067e\u0631\u062f\u0627\u062e\u062a'],
      'number_tokens': ['25000'],
      'quantitative': True}),
    ('we owe 3 million toman',
     {'request_sha': '2f972912775a86e1c0815f70a8e3385c92c22b6e1ae332ee51eab594824804da',
      'money_tokens': ['owe', 'toman'],
      'number_tokens': ['3'],
      'quantitative': True}),
    ('price in rial 5000',
     {'request_sha': '5fb62207eb6493fa995da1d837306b8bd4fa08c3e77f59d60d0ea9fed3642360',
      'money_tokens': ['price', 'rial'],
      'number_tokens': ['5000'],
      'quantitative': True}),
    ('how many orders came in and what is the average',
     {'request_sha': '36be95ea8fb265dc87f9ae64adb4d136f02decf975dfb5549482ede7aea1c62e',
      'question_only': True,
      'quantitative': True}),
    ('\u0645\u062c\u0645\u0648\u0639 \u0641\u0631\u0648\u0634 \u0648 \u0645\u06cc\u0627\u0646\u06af\u06cc\u0646',
     {'request_sha': '8da18d03a732354e5c7411df3b25db3e627b43801209a4b9827fde4d438ea3a9',
      'quantitative': True}),
    ('should I take this loan to invest',
     {'request_sha': 'ae9f66ec6ec6f6ed88760bb252908c06c0c30b404e5e363fa67870732f95aacb',
      'question_only': True,
      'decision_hits': {'financial': ['invest', 'loan']}}),
    ('what is the tax on a contract',
     {'request_sha': 'd10e561ba49234d1c68729d5f3588defda5b5541ef120121e4a327393a21a5f9',
      'question_only': True,
      'decision_hits': {'tax': ['tax'], 'legal': ['contract']}}),
    ('\u0642\u0631\u0627\u0631\u062f\u0627\u062f \u0648 \u0645\u0627\u0644\u06cc\u0627\u062a \u0634\u0631\u06a9\u062a',
     {'request_sha': '6b1b4b97b6f64181062595856103c85fe6a619134ddee41ca09dccf43466f21e',
      'decision_hits': {'tax': ['\u0645\u0627\u0644\u06cc\u0627\u062a'], 'legal': ['\u0642\u0631\u0627\u0631\u062f\u0627\u062f']}}),
    ('continue',
     {'request_sha': 'e256ee8e7aff6957a781d8328f0f68e26996564c81fa458da59fbca2305138ad',
      'continuation': True}),
    ('\u0627\u062f\u0627\u0645\u0647 \u0628\u062f\u0647',
     {'request_sha': '8dec7895d6e935e751b4781a83afe7a8249a098e10be8520200078f870453d49',
      'continuation': True}),
    ('keep going with the report',
     {'request_sha': '21cde1058833fb551dd0875caef45e9e6c8b518f675d8dce481dda5faa82bbca',
      'continuation': True}),
    ('\u062f\u0631 \u06f3 \u0645\u0627\u0647 \u06af\u0630\u0634\u062a\u0647 \u06f1\u06f2\u066b\u06f5 \u0645\u0634\u062a\u0631\u06cc',
     {'request_sha': 'cc0fc53fc8ddca66d72f7fd931006fb3d245239dec18fd0406536eeb112089ac',
      'number_tokens': ['12', '3', '5'],
      'quantitative': True}),
    ('total \u0663 and \u0664',
     {'request_sha': 'c94c86194799068b80bfd8187b1fa14e7548cc4aee1db3f0ee5c3c153a0ee348',
      'number_tokens': ['3', '4'],
      'quantitative': True}),
    ('',
     {'request_sha': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'}),
    ('   ',
     {'request_sha': '0aad7da77d2ed59c396c99a74e49f3a4524dcdbcb5163251b1433d640247aeb4'}),
    ('Please PAY the supplier and ship it',
     {'request_sha': '05b7b430c98979c77a3ebe8454d69a2002ed7b44be45d4c8d700e3f7a7a41e5a',
      'action_verbs': ['pay'],
      'irreversible_classes': ['money'],
      'floor_tier': 'T2'}),
    ('write the draft then save it',
     {'request_sha': 'ece8fd8e78618ad431f6b25c4b19126e4d662723e9d0c8b82d7545f6284010a0',
      'action_verbs': ['draft', 'save', 'write']}),
    ('hello there?',
     {'request_sha': '6c737d30ab8a0b22593530ee92ffea56001e1e5f611ae382caaa136cb6a583e0',
      'question_only': True}),
    ('\u0627\u06cc\u0645\u06cc\u0644 \u0628\u0632\u0646\u061f',
     {'request_sha': 'db24fbfdd07ab3eeffbf311332c7370c4e2c9cc102e885c3c33639417b263084',
      'action_verbs': ['\u0627\u06cc\u0645\u06cc\u0644 \u0628\u0632\u0646'],
      'irreversible_classes': ['external_message'],
      'floor_tier': 'T2'}),
    ('forward this message. now delete it',
     {'request_sha': '5d0134d6ed4545087dfd41ea9679ccfd16084d0df16924387df2247b9becc305',
      'action_verbs': ['delete', 'forward', 'message'],
      'irreversible_classes': ['destructive', 'external_message'],
      'floor_tier': 'T2'}),
    ('cost \ufdfc 100 and \xa350 or \u20ac5',
     {'request_sha': '520f5806e1580a481d5025cbebf07fbcfac5fbe05685b0bab4aaefc844bc1a37',
      'money_tokens': ['cost', '\xa3', '\u20ac', '\ufdfc'],
      'number_tokens': ['100', '5', '50'],
      'quantitative': True}),
    ('buy 5 shares of stock',
     {'request_sha': 'e6d9504348044e10cc9e0f596f438718d725a799b7cae66b74a198c7208371bc',
      'action_verbs': ['buy'],
      'irreversible_classes': ['money'],
      'number_tokens': ['5'],
      'quantitative': True,
      'decision_hits': {'financial': ['shares', 'stock']},
      'floor_tier': 'T2'}),
    ('are we hiring a lawyer for the lawsuit',
     {'request_sha': 'beb485301a1e26d4b9e6c23726d53f235b0caaf630468450e193fe92925861f3',
      'question_only': True,
      'decision_hits': {'legal': ['lawsuit'], 'employment': ['hiring']}}),
    ('summarise customers.csv',
     {'request_sha': 'f7d75d15c0abb457040a9b7914971b6eab02c661b9273f9cb6a224e7ae512034',
      'files_named': ['customers.csv'],
      'quantitative': True}),
    ('update report.md with the new totals',
     {'request_sha': '25dd83bdcdac9104368bab12e9dc62f6a5f8fe883f7095b16217c1669d3577eb',
      'action_verbs': ['update'],
      'write_targets': ['report.md'],
      'files_named': ['report.md'],
      'floor_tier': 'T1'}),
    ('edit notes.txt',
     {'request_sha': '969d53327e1c82489806465adde8e201e74f769eda1d7fdac05ca0e9d579af36',
      'action_verbs': ['edit'],
      'write_targets': ['notes.txt'],
      'files_named': ['notes.txt'],
      'floor_tier': 'T1'}),
]


@pytest.mark.parametrize("text,over", PINNED, ids=[f"p{i:02d}" for i in range(len(PINNED))])
def test_the_probe_result_is_pinned(tmp_path, text, over):
    ctx = Ctx(workspace=tmp_path, data_root=None) if text in FILE_TEXTS else None
    assert probe.run(text, ctx) == dict(DEFAULT, **over)


def test_the_pinned_corpus_covers_what_the_brief_asks():
    assert len(PINNED) >= 25 and len({t for t, _ in PINNED}) == len(PINNED)


@pytest.mark.parametrize("text,prefilter", [
    ("send the invoice to Sara", "card-needed"),
    ("\u0686\u0631\u0627 \u0622\u0633\u0645\u0627\u0646 \u0622\u0628\u06cc \u0627\u0633\u062a\u061f", "T0-fast"),
])
def test_harness_probe_still_prints_the_pinned_result_with_no_data_folder(tmp_path, text, prefilter):
    run = subprocess.run(["/usr/bin/python3", "-I", str(ROOT / "bin" / "harness-probe"), text], cwd=str(tmp_path),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    assert json.loads(run.stdout.decode("utf-8")) == dict(DEFAULT, **dict(PINNED)[text], prefilter=prefilter)


# --- 3. probe.words ------------------------------------------------------------------------------------------------


def test_words_has_exactly_the_eleven_keys():
    assert set(probe.words(None)) == WORD_KEYS


def test_without_a_data_folder_the_lists_are_the_0_4_0_lists(tmp_path):
    for ctx in (None, Ctx(workspace=tmp_path, data_root=None)):
        got = probe.words(ctx)
        for key in LIST_KEYS:
            assert sorted(got[key]) == sorted(OLD[key]), key
        for cls in CLASSES:
            assert sorted(got["verbs"][cls] + got["verbs_anywhere"][cls]) == sorted(OLD["verbs"][cls]), cls
        assert {c: sorted(v) for c, v in got["decision_keywords"].items()} == \
            {c: sorted(v) for c, v in OLD["decision_keywords"].items()}
        assert got["file_extensions"] == OLD["file_extensions"]
        assert got["imperative_join"] == ["and", "then", "also", "please"]
        assert got["question_marks"] == ["?", "\u061f"]
        assert got["money_words"] == sum(split(OLD["money_words"], IR_MONEY), [])  # en, fa, then ir


def test_verbs_and_verbs_anywhere_always_hold_the_five_classes():
    got = probe.words(None)
    assert list(got["verbs"]) == CLASSES and list(got["verbs_anywhere"]) == CLASSES
    assert got["verbs"]["money"][0] == "pay" and got["verbs_anywhere"]["money"][0] == "\u067e\u0631\u062f\u0627\u062e\u062a \u06a9\u0646"


def test_profile_en_only_leaves_out_every_persian_word_and_toman(h):
    set_profile(h, locales=["en"])
    got = probe.words(ctx_of(h))
    flat = [w for k, v in got.items() if k != "question_marks" for w in _strings(v)]
    assert [w for w in flat if re.search("[\u0600-\u06ff]", w)] == []
    assert "toman" not in got["money_words"] and "invoice" in got["money_words"]
    assert all(got["verbs_anywhere"][c] == [] for c in CLASSES)
    assert got["question_marks"] == ["?", "\u061f"]  # a mark ends a question whatever the profile names


def test_profile_ir_with_language_fa_keeps_the_persian_verbs_as_anywhere_verbs(h):
    set_profile(h, locales=["ir"], language="fa")
    got = probe.words(ctx_of(h))
    for cls in CLASSES:
        assert got["verbs_anywhere"][cls] == split(OLD["verbs"][cls])[1], cls
        assert got["verbs"][cls] == split(OLD["verbs"][cls])[0], cls
    assert "toman" in got["money_words"]
    set_profile(h, locales=["ir"])  # no profile language: the pack fa is not active
    assert probe.words(ctx_of(h))["verbs_anywhere"]["money"] == []


def test_an_active_live_pack_adds_its_words(h):
    put(h, "xx.json", pack("xx", words={"money_words": ["zloty"], "verbs": {"money": ["xpay"]},
                                       "imperative_lead": ["xlead"], "decision_keywords": {"zz_class": ["blorp"]}}))
    got = probe.words(ctx_of(h))
    assert "zloty" in got["money_words"] and "xpay" in got["verbs"]["money"] and "xlead" in got["imperative_lead"]
    assert got["decision_keywords"]["zz_class"] == ["blorp"]
    assert probe.run("a blorp", ctx_of(h))["decision_hits"] == {"zz_class": ["blorp"]}
    set_profile(h, locales=["fa"])  # xx is not named: its words are not active
    got = probe.words(ctx_of(h))
    assert "zloty" not in got["money_words"] and "zz_class" not in got["decision_keywords"]
    set_profile(h, locales=["xx"])
    got = probe.words(ctx_of(h))
    assert got["money_words"][0] == "zloty" and "toman" not in got["money_words"]


# --- 4. the owner's probe.json ---------------------------------------------------------------------------------------


def test_a_live_file_with_one_list_adds_to_the_packs_and_loses_nothing(h):
    write_probe(h, {"money_words": ["zloty"]})
    c = ctx_of(h)
    got = probe.run("the zloty invoice", c)
    assert got["money_tokens"] == ["invoice", "zloty"] and got["quantitative"] is True
    words = probe.words(c)
    assert set(words) == WORD_KEYS and all(isinstance(words[k], list) for k in LIST_KEYS + ["imperative_join"])
    assert words["money_words"][-1] == "zloty" and words["money_words"][:3] == ["$", "\u20ac", "\u00a3"]
    assert words["verbs"]["money"][0] == "pay"


def test_the_owner_list_follows_the_pack_list_and_repeats_are_dropped_first_kept(h):
    write_probe(h, {"money_words": ["invoice", "zloty", "zloty", "krona"]})
    money = probe.words(ctx_of(h))["money_words"]
    assert money.count("invoice") == 1 and money.count("zloty") == 1
    assert money.index("invoice") < money.index("zloty") < money.index("krona")
    assert money[-2:] == ["zloty", "krona"]


def test_the_admin_path_writes_only_the_owners_list_into_the_live_file(h):
    shown = typed(h, "/dynamic-harness:config show probe.json", "c0")
    assert json.loads(shown) == SHIPPED_PROBE  # a fresh data folder shows the owner's empty lists
    shown = typed(h, "/dynamic-harness:config set probe.json money_words '[\"zloty\"]'", "c1")
    assert 'from [] to ["zloty"]' in shown
    assert "Done." in confirm(h, "config", shown, "c2")
    live = json.loads((h["data"] / "control" / "probe.json").read_text("utf-8"))
    assert live == dict(SHIPPED_PROBE, money_words=["zloty"])
    got = probe.run("the zloty invoice in toman", ctx_of(h))
    assert got["money_tokens"] == ["invoice", "toman", "zloty"]


def test_a_live_file_that_is_a_full_copy_of_0_4_0_gives_the_pinned_results(h):
    write_probe(h, (FIX / "probe_0_4_0.json").read_text("utf-8"))
    c = ctx_of(h)
    bad = [t for t, over in PINNED if probe.run(t, c) != dict(DEFAULT, **over)]
    assert bad == []
    assert probe.words(c) == probe.words(None)  # every owner entry repeats a pack entry: nothing moves


def test_an_owner_verb_that_no_pack_lists_is_matched_anywhere_also_in_a_question(h):
    write_probe(h, {"verbs": {"external_message": ["ship"]}})
    c = ctx_of(h)
    got = probe.run("when did we ship the box?", c)
    assert got["irreversible_classes"] == ["external_message"] and got["question_only"] is False
    assert got["action_verbs"] == ["ship"] and got["floor_tier"] == "T2"
    words = probe.words(c)
    assert words["verbs_anywhere"]["external_message"][-1] == "ship" and "ship" not in words["verbs"]["external_message"]
    assert probe.run("when did we send the box?", c)["irreversible_classes"] == []  # a pack verb stays by position


def test_an_owner_verb_that_a_pack_lists_in_another_class_is_matched_by_position_only(h):
    write_probe(h, {"verbs": {"destructive": ["send"]}})
    c = ctx_of(h)
    words = probe.words(c)
    assert words["verbs"]["destructive"][-1] == "send" and "send" not in words["verbs_anywhere"]["destructive"]
    asked = probe.run("did we send the report?", c)
    assert asked["irreversible_classes"] == [] and asked["question_only"] is True and asked["action_verbs"] == ["send"]
    assert probe.run("please send the report", c)["irreversible_classes"] == ["destructive", "external_message"]


def test_a_pack_verb_is_found_after_probe_norm_when_the_owner_writes_it_in_capitals(h):
    write_probe(h, {"verbs": {"destructive": ["SEND"]}})
    words = probe.words(ctx_of(h))
    assert words["verbs"]["destructive"][-1] == "SEND" and "SEND" not in words["verbs_anywhere"]["destructive"]


def test_an_owner_class_name_that_only_probe_json_holds_is_kept(h):
    write_probe(h, {"verbs": {"zz_cls": ["blorp"]}, "decision_keywords": {"zz_dec": ["glorp"]}})
    words = probe.words(ctx_of(h))
    assert words["verbs_anywhere"]["zz_cls"] == ["blorp"] and words["verbs"].get("zz_cls", []) == []
    assert set(CLASSES) <= set(words["verbs"]) and set(CLASSES) <= set(words["verbs_anywhere"])
    assert set(words["decision_keywords"]) == set(DECISIONS) | {"zz_dec"}
    assert words["decision_keywords"]["zz_dec"] == ["glorp"]
    assert probe.run("a blorp", ctx_of(h))["irreversible_classes"] == ["zz_cls"]


@pytest.mark.parametrize("live,names", [
    ({"money_words": "x"}, ["money_words"]),
    ({"question_lead": {"a": ["b"]}}, ["question_lead"]),
    ({"continuation": ["ok", 3]}, ["continuation"]),
    ({"verbs": []}, ["verbs"]),
    ({"verbs": {"money": [1]}}, ["verbs.money"]),
    ({"verbs": {"money": "pay"}}, ["verbs.money"]),
    ({"imperative_join": "plus"}, ["imperative_join"]),
    ({"decision_keywords": ["tax"]}, ["decision_keywords"]),
    ({"decision_keywords": {"tax": "vat"}}, ["decision_keywords"]),
], ids=["str", "dict", "item-int", "verbs-list", "verbs-class-int", "verbs-class-str", "join-str", "decisions-list",
        "decisions-class-str"])
def test_a_live_value_of_the_wrong_kind_is_an_error_that_names_the_file_and_the_key(h, live, names):
    write_probe(h, dict(live))
    with pytest.raises(HarnessError) as e:
        probe.words(ctx_of(h))
    assert "probe.json" in str(e.value) and all(n in str(e.value) for n in names), str(e.value)
    with pytest.raises(HarnessError):
        probe.run("pay the invoice", ctx_of(h))


def test_unknown_keys_of_a_live_file_are_ignored_and_keys_it_lacks_count_as_empty(h):
    write_probe(h, {"version": 7, "note": "mine", "colour": "red", "continuation": ["carry on"]})
    c = ctx_of(h)
    assert probe.words(c)["continuation"][-1] == "carry on"
    assert probe.run("carry on please", c)["continuation"] is True
    assert probe.run("send it", c)["irreversible_classes"] == ["external_message"]


def test_a_live_file_list_of_file_extensions_replaces_the_shipped_list(h):
    write_probe(h, {"file_extensions": ["zzz"], "money_words": []})
    c = ctx_of(h)
    assert probe.words(c)["file_extensions"] == ["zzz"]
    got = probe.run("open data.zzz and report.csv", c)
    assert got["files_named"] == ["data.zzz"]
    assert probe.run("open report.csv", Ctx(workspace=h["ws"], data_root=None))["files_named"] == ["report.csv"]


def test_a_live_file_without_file_extensions_uses_the_shipped_list(h):
    write_probe(h, {"money_words": ["zloty"]})
    c = ctx_of(h)
    assert probe.words(c)["file_extensions"] == OLD["file_extensions"]
    assert probe.run("open report.csv", c)["files_named"] == ["report.csv"]


@pytest.mark.parametrize("raw", ["[]", '"x"', "5", "null"], ids=["list", "string", "number", "null"])
def test_a_live_probe_json_that_is_not_an_object_is_an_error_that_names_the_file(h, raw):
    write_probe(h, raw)
    with pytest.raises(HarnessError) as e:
        probe.words(ctx_of(h))
    assert "probe.json" in str(e.value)


def test_a_live_file_with_an_empty_extension_list_replaces_the_shipped_list(h):
    write_probe(h, {"file_extensions": []})
    c = ctx_of(h)
    assert probe.words(c)["file_extensions"] == []
    assert probe.run("see report.csv", c)["files_named"] == []  # the owner asked for no file detection


def test_a_join_word_in_a_live_probe_json_makes_the_verb_after_it_an_instruction(h):
    assert probe.run(JOINED, ctx_of(h))["irreversible_classes"] == []
    write_probe(h, {"imperative_join": ["plus"]})
    c = ctx_of(h)
    assert probe.words(c)["imperative_join"] == ["and", "then", "also", "please", "plus"]
    assert probe.run(JOINED, c)["irreversible_classes"] == ["destructive"]


def test_a_question_mark_that_probe_norm_folds_to_nothing_is_not_a_mark(h):
    from harness_core import stopgate
    put(h, "xx.json", pack("xx", question_marks=["\u200c", "\u2e2e"]))
    c = ctx_of(h)
    assert probe.run("hello world", c)["question_only"] is False
    assert stopgate.asks(c, "hello world") is False
    assert probe.run("hello world\u2e2e", c)["question_only"] is True
    assert probe.run("hello world?", c)["question_only"] is True


def test_a_mark_of_a_live_pack_is_compared_after_probe_norm(h):
    put(h, "qq.json", pack("qq", question_marks=["Q"]))
    c = ctx_of(h)
    assert probe.run("sky blueQ", c)["question_only"] is True
    assert probe.run("sky blue", c)["question_only"] is False


# --- 5. join words are data --------------------------------------------------------------------------------------------

JOINED = "tell me the total plus delete the file"


def test_a_join_word_of_a_live_pack_makes_the_verb_after_it_an_instruction(h):
    c = ctx_of(h)
    assert probe.run(JOINED, c)["irreversible_classes"] == []
    put(h, "xx.json", pack("xx", words={"imperative_join": ["plus"]}))
    got = probe.run(JOINED, ctx_of(h))
    assert got["irreversible_classes"] == ["destructive"] and got["action_verbs"] == ["delete", "file"]
    assert "plus" in probe.words(ctx_of(h))["imperative_join"]
    (h["data"] / "control" / "locales" / "xx.json").unlink()
    assert probe.run(JOINED, ctx_of(h))["irreversible_classes"] == []


def test_the_text_of_probe_py_holds_no_join_word_no_first_letter_branch_and_no_question_mark():
    text = (ROOT / "lib" / "harness_core" / "probe.py").read_text("utf-8")
    assert "and|then|also|please" not in text
    assert "[a-z]" not in text
    assert '"?"' not in text and "'?'" not in text and "\u061f" not in text


# --- 5b. empty lists in a view: the edges of the position rule -------------------------------------------------------


def fake_view(monkeypatch, words, marks=()):
    full = {"verbs": {}, "verbs_anywhere": {}, "imperative_lead": [], "imperative_join": [], "question_lead": [],
            "money_words": [], "quantity_words": [], "decision_keywords": {}, "continuation": []}
    full.update(words)
    monkeypatch.setattr(locales, "view", lambda ctx=None, every=False: {"words": full, "question_marks": list(marks)})


@pytest.mark.parametrize("lead,join,text,found", [
    ([], [], "delete it", True),
    ([], [], "look. delete it", True),
    ([], [], "look\ndelete it", True),
    ([], [], "look: delete it", True),
    ([], [], "please delete it", False),
    ([], [], "and delete it", False),
    (["please"], [], "please delete it", True),
    (["please"], [], "please please delete it", True),
    (["please"], [], "and please delete it", False),
    ([], ["plus"], "a plus delete it", True),
    ([], ["plus"], "a plus please delete it", False),
    (["please"], ["plus"], "a plus please delete it", True),
    (["please"], ["plus"], "a plus pleased delete it", False),
    ([], ["plus"], "a surplus delete it", False),
])
def test_the_position_rule_follows_the_lead_and_join_lists_of_the_view(monkeypatch, lead, join, text, found):
    fake_view(monkeypatch, {"verbs": {"destructive": ["delete"]}, "imperative_lead": lead, "imperative_join": join})
    assert probe.run(text, None)["irreversible_classes"] == (["destructive"] if found else [])


@pytest.mark.parametrize("marks,text,asked", [
    ([], "sky blue?", False),
    ([], "quoz sky", True),
    ([], "sky blue\u061f", False),
    (["?"], "sky blue?", True),
    (["?"], "sky blue?   ", True),
    (["?"], "sky blue\u061f", False),
    (["?", "\u061f"], "sky blue\u061f", True),
    (["\u2e2e"], "sky blue\u2e2e", True),
    (["?"], "sky ? blue", False),
    (["Q"], "sky blueQ", True),                # the mark is compared after probe._norm: "Q" folds to "q"
    (["\u0643"], "sky blue\u0643", True),      # Arabic kaf folds to the Persian kaf on both sides
    (["\u200c"], "sky blue", False),           # a mark that _norm folds to nothing is not a mark
    (["\u200c", "?"], "sky blue?", True),
])
def test_a_question_ends_with_a_mark_of_the_view_or_starts_with_a_question_lead(monkeypatch, marks, text, asked):
    fake_view(monkeypatch, {"question_lead": ["quoz"]}, marks)
    assert probe.run(text, None)["question_only"] is asked


# --- 6. a new language needs a pack and no code --------------------------------------------------------------------

XX = {"verbs_anywhere": {"money": ["zorp it"]}, "question_lead": ["quoz"]}


def test_a_live_pack_with_made_up_words_works_with_no_code_change(h):
    from harness_core import gate, stopgate
    texts = {"verb": "the cat said zorp it and left", "lead": "quoz sky blue", "mark": "sky blue\u2e2e"}
    before = {k: probe.run(t, ctx_of(h)) for k, t in texts.items()}
    assert before["verb"]["irreversible_classes"] == [] and before["lead"]["question_only"] is False
    assert before["mark"]["question_only"] is False
    assert stopgate.lexicon_hits(ctx_of(h), "it was blorped") == []
    assert stopgate.asks(ctx_of(h), "sky blue\u2e2e") is False
    put(h, "xx.json", pack("xx", words=XX, question_marks=["\u2e2e"], state_lexicon={"terms": ["blorped"]}))
    c = ctx_of(h)
    got = probe.run(texts["verb"], c)
    assert got["irreversible_classes"] == ["money"] and got["action_verbs"] == ["zorp it"] and got["floor_tier"] == "T2"
    asked = probe.run("did the cat zorp it?", c)
    assert asked["irreversible_classes"] == ["money"] and asked["question_only"] is False  # an anywhere verb
    assert probe.run(texts["lead"], c)["question_only"] is True
    assert probe.run(texts["mark"], c)["question_only"] is True
    assert stopgate.lexicon_hits(c, "it was blorped") == ["blorped"]
    assert "blorped" in gate.lexicon(c)["terms"]
    assert stopgate.asks(c, "sky blue\u2e2e") is True


def test_the_mark_and_the_state_words_of_a_pack_the_profile_does_not_name_still_apply(h):
    from harness_core import stopgate
    put(h, "xx.json", pack("xx", words=XX, question_marks=["\u2e2e"], state_lexicon={"terms": ["blorped"]}))
    set_profile(h, locales=["en"])
    c = ctx_of(h)
    assert probe.run("the cat said zorp it and left", c)["irreversible_classes"] == []  # its words are not active
    assert probe.run("quoz sky blue", c)["question_only"] is False
    assert probe.run("sky blue\u2e2e", c)["question_only"] is True  # how a question ends: every installed pack
    assert stopgate.lexicon_hits(c, "it was blorped") == ["blorped"]
    assert stopgate.asks(c, "sky blue\u2e2e") is True


# --- 11. one read ---------------------------------------------------------------------------------------------------


def spy(monkeypatch, module, name):
    real, log = getattr(module, name), []
    monkeypatch.setattr(module, name, lambda *a, **k: log.append((a, k)) or real(*a, **k))
    return log


def test_words_makes_one_view_call_and_one_read_of_probe_json(h, monkeypatch):
    views, controls = spy(monkeypatch, locales, "view"), []
    real = protected.load_control
    monkeypatch.setattr(protected, "load_control", lambda name, ctx=None: controls.append(name) or real(name, ctx))
    for ctx in (ctx_of(h), None):
        del views[:], controls[:]
        probe.words(ctx)
        assert len(views) == 1 and (views[0][1].get("every") is True or views[0][0][1:] == (True,))
        assert controls == ["probe.json"]


def test_run_makes_one_view_call_and_reads_nothing_else(h, monkeypatch):
    views, controls = spy(monkeypatch, locales, "view"), []
    real = protected.load_control
    monkeypatch.setattr(protected, "load_control", lambda name, ctx=None: controls.append(name) or real(name, ctx))
    probe.run("please send the invoice for 20 dollars to Sara?", ctx_of(h))
    assert len(views) == 1 and controls == ["probe.json"]


def test_a_broken_live_pack_stops_the_probe(h):
    put(h, "broken.json", b"{")
    for call in (lambda: probe.run("hello", ctx_of(h)), lambda: probe.words(ctx_of(h))):
        with pytest.raises(LocaleError) as e:
            call()
        assert "broken.json" in str(e.value)

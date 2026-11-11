"""Locale packs (harness/locale@1): loader, merge, active set, owner command (build phase P1, task 1).

``harness_core.locales`` is imported inside the fixtures so that, before the module exists, every test fails at its
first line with ModuleNotFoundError and for no other reason."""
from __future__ import annotations

import copy
import json
import os
import re
import sys

import pytest

from harness_core import business, hookmain, session
from harness_core.errors import Denied
from test_business import confirm, typed
from test_p3_hooks import ctx_of, h  # noqa: F401  (fixture h)

SCHEMA = "harness/locale@1"
SHIPPED = ["en", "fa", "ir"]


@pytest.fixture
def L():
    import harness_core.locales as locales
    return locales


@pytest.fixture
def LocaleError(L):
    from harness_core.errors import LocaleError as cls
    return cls


def pack(pid, **extra):
    return dict({"schema": SCHEMA, "id": pid}, **extra)


def live_dir(h):
    d = h["data"] / "control" / "locales"
    d.mkdir(parents=True, exist_ok=True)
    return d


def put(h, name, content):
    """Write one live pack file: a dict becomes JSON, str or bytes are written as they are."""
    f = live_dir(h) / name
    if isinstance(content, dict):
        content = json.dumps(content)
    f.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return f


def holds(got, want):
    """``got`` holds everything ``want`` holds: later tasks add words to the shipped packs, so tests compare parts."""
    if isinstance(want, dict):
        return isinstance(got, dict) and all(k in got and holds(got[k], v) for k, v in want.items())
    if isinstance(want, list):
        return isinstance(got, list) and all(x in got for x in want)
    return got == want


def set_profile(h, **prof):
    (h["data"] / "business").mkdir(exist_ok=True)
    (h["data"] / "business" / "profile.json").write_text(json.dumps(prof))


# --- 1. shipped packs ------------------------------------------------------------------------------------------


def test_the_shipped_packs_are_en_fa_ir(L):
    got = L.installed()
    assert sorted(got) == SHIPPED
    for pid, p in got.items():
        assert p["schema"] == SCHEMA and p["id"] == pid


def test_the_shipped_packs_are_installed_in_a_set_up_data_folder(h, L):
    got = L.installed(ctx_of(h))
    assert sorted(got) == SHIPPED
    assert [got[i]["name"] for i in SHIPPED] == ["English", "Persian", "Iran"]


def test_the_base_pack_is_en(L):
    assert L.BASE == "en" and L.MAX_BYTES == 256 * 1024


# --- 2-4. which packs are active ------------------------------------------------------------------------------


def test_without_a_context_or_a_profile_every_pack_is_active(h, L):
    assert L.active() == SHIPPED
    assert L.active(None) == SHIPPED
    assert L.active(ctx_of(h)) == SHIPPED  # no profile file at all
    set_profile(h)
    assert L.active(ctx_of(h)) == SHIPPED  # an empty profile


@pytest.mark.parametrize("chosen,expected", [
    (["fa"], ["fa", "en"]),
    (["ir", "fa"], ["ir", "fa", "en"]),
    (["en"], ["en"]),
    (["fa", "fa"], ["fa", "en"]),
    (["en", "ir"], ["en", "ir"]),  # the base pack keeps the place the owner gave it
    (["ir", "en", "ir"], ["ir", "en"]),
])
def test_the_profile_list_picks_the_packs_in_its_order(h, L, chosen, expected):
    set_profile(h, locales=chosen)
    assert L.active(ctx_of(h)) == expected


@pytest.mark.parametrize("bad", [[], ["zz"], ["fa", "zz"], "fa", [1], ["fa", 1], ["fa", None], {"fa": 1}, None, 7])
def test_any_other_profile_value_falls_back_to_every_pack_base_first(h, L, bad):
    set_profile(h, locales=bad)
    assert L.active(ctx_of(h)) == SHIPPED


def test_the_fall_back_sorts_the_other_packs_by_id(h, L):
    for stem in ("zz", "aa", "mid"):
        put(h, f"{stem}.json", pack(stem))
    assert L.active(ctx_of(h)) == ["en", "aa", "fa", "ir", "mid", "zz"]


def test_a_profile_that_is_not_an_object_does_not_stop_the_loader(h, L):
    (h["data"] / "business").mkdir(exist_ok=True)
    (h["data"] / "business" / "profile.json").write_text("[1, 2]")
    assert L.active(ctx_of(h)) == SHIPPED


# --- 5-6. live packs ---------------------------------------------------------------------------------------------


def test_a_live_pack_with_a_new_id_is_installed_and_active(h, L):
    put(h, "xx.json", pack("xx", words={"money_words": ["zloty"]}))
    c = ctx_of(h)
    assert sorted(L.installed(c)) == SHIPPED + ["xx"]
    assert L.active(c) == ["en", "fa", "ir", "xx"]
    assert "zloty" in L.view(c)["words"]["money_words"]
    assert sorted(L.installed()) == SHIPPED  # no context: shipped packs only
    set_profile(h, locales=["xx"])
    assert L.active(ctx_of(h)) == ["xx", "en"]
    assert L.view(ctx_of(h))["words"]["money_words"][0] == "zloty"  # the first active pack comes first


def test_a_live_pack_with_a_shipped_id_adds_and_never_replaces(h, L):
    shipped = copy.deepcopy(L.installed()["fa"])
    put(h, "fa.json", pack("fa", name="Other", words={"money_words": ["zloty"]}))
    got = L.installed(ctx_of(h))["fa"]
    assert got["name"] == "Persian"  # the shipped value wins over the live one
    assert "zloty" in got["words"]["money_words"]
    assert got["schema"] == SCHEMA and got["id"] == "fa"
    assert holds(got, shipped)  # nothing of the shipped pack is lost
    assert sorted(L.installed(ctx_of(h))) == SHIPPED


def test_a_live_pack_with_the_base_id_cannot_remove_the_base(h, L):
    put(h, "en.json", pack("en", name="Other", words={"money_words": ["y"]}))
    c = ctx_of(h)
    assert L.installed(c)["en"]["name"] == "English" and L.active(c)[0] == "en"
    assert L.installed(c)["en"]["words"]["money_words"][-1] == "y"  # added after what the shipped pack holds
    assert holds(L.installed(c)["en"], L.installed()["en"])  # nothing of the shipped base pack is lost
    assert L.installed(c)["en"]["words"]["money_words"][:2] == L.installed()["en"]["words"]["money_words"][:2]
    set_profile(h, locales=["fa"])
    assert L.active(ctx_of(h)) == ["fa", "en"]


def test_a_missing_live_directory_means_no_live_pack(h, L):
    assert not (h["data"] / "control" / "locales").exists()
    assert sorted(L.installed(ctx_of(h))) == SHIPPED


# --- 7. invalid packs ---------------------------------------------------------------------------------------------


def _raw(content):
    return lambda h, L: content


BAD_CONTENT = [
    ("broken.json", b"{"),
    ("empty.json", b""),
    ("latin.json", b'{"schema": "harness/locale@1", "id": "latin", "name": "caf\xe9"}'),
    ("arr.json", b"[]"),
    ("str.json", b'"xx"'),
    ("noschema.json", {"id": "noschema"}),
    ("noid.json", {"schema": SCHEMA}),
    ("wrongschema.json", {"schema": "harness/locale@2", "id": "wrongschema"}),
    ("extra.json", {"schema": SCHEMA, "id": "extra", "wrods": {}}),
    ("badtype.json", {"schema": SCHEMA, "id": "badtype", "name": 5}),
    ("badwords.json", {"schema": SCHEMA, "id": "badwords", "words": ["a"]}),
    ("idtype.json", {"schema": SCHEMA, "id": 7}),
    ("xx.json", {"schema": SCHEMA, "id": "yy"}),  # id differs from the stem
    ("Has Space.json", {"schema": SCHEMA, "id": "Has Space"}),
    ("UPPER.json", {"schema": SCHEMA, "id": "UPPER"}),
    ("upper.json", {"schema": SCHEMA, "id": "Upper"}),
    ("dot.dot.json", {"schema": SCHEMA, "id": "dot.dot"}),
    ("a" * 33 + ".json", {"schema": SCHEMA, "id": "a" * 33}),
    ("caf\u00e9.json", {"schema": SCHEMA, "id": "caf\u00e9"}),
]


@pytest.mark.parametrize("name,content", BAD_CONTENT, ids=[n for n, _ in BAD_CONTENT])
def test_an_invalid_live_pack_is_refused_and_named(h, L, LocaleError, name, content):
    put(h, name, content)
    with pytest.raises(LocaleError) as e:
        L.installed(ctx_of(h))
    assert name in str(e.value)


def test_a_symbolic_link_is_refused_even_when_it_points_at_a_valid_pack(h, L, LocaleError, tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(pack("lnk")))
    os.symlink(target, live_dir(h) / "lnk.json")
    with pytest.raises(LocaleError) as e:
        L.installed(ctx_of(h))
    assert "lnk.json" in str(e.value)


def test_a_dangling_symbolic_link_is_refused(h, L, LocaleError, tmp_path):
    os.symlink(tmp_path / "nowhere", live_dir(h) / "gone.json")
    with pytest.raises(LocaleError) as e:
        L.installed(ctx_of(h))
    assert "gone.json" in str(e.value)


def test_a_directory_named_like_a_pack_is_refused(h, L, LocaleError):
    (live_dir(h) / "dir.json").mkdir()
    with pytest.raises(LocaleError) as e:
        L.installed(ctx_of(h))
    assert "dir.json" in str(e.value)


def _sized(pid, size):
    head = '{"schema": "harness/locale@1", "id": "%s", "name": "' % pid
    tail = '"}'
    return (head + "x" * (size - len(head) - len(tail)) + tail).encode("utf-8")


def _deep(pid, opener, closer, leaf, depth):
    head = '{"schema": "harness/locale@1", "id": "%s", "words": ' % pid
    return (head + opener * depth + leaf + closer * depth + "}").encode("utf-8")


@pytest.mark.parametrize("name,data", [
    ("deep.json", _deep("deep", '{"a": ', "}", "1", 5000)),
    ("arrays.json", b"[" * 100000),
    ("wordarr.json", _deep("wordarr", "[", "]", "1", 5000)),
], ids=["objects", "unclosed-arrays", "arrays"])
def test_a_pack_nested_too_deep_is_a_locale_error_that_names_the_file(h, L, LocaleError, name, data):
    assert len(data) < L.MAX_BYTES
    put(h, name, data)
    set_profile(h, locales=["fa"])
    for fn in (L.installed, L.active, L.view):
        with pytest.raises(LocaleError) as e:
            fn(ctx_of(h))
        assert name in str(e.value)


def _nested(pid, shape, depth):
    """A pack with ``words.w`` nested ``depth`` levels (arrays, or arrays that hold one object each)."""
    inner = {"arrays": "[" * depth + "1" + "]" * depth,
             "arrays_of_objects": '[{"a": ' * depth + "1" + "}]" * depth}[shape]
    return ('{"schema": "harness/locale@1", "id": "%s", "words": {"w": %s}}' % (pid, inner)).encode("utf-8")


# The parser stops near the recursion limit; merging (copy and compare) needs more frames per level and stops
# earlier. Measured on 3.9 at limits 500, 1000 and 2000: arrays merge-fail from 0.5 and parse-fail from 0.95 of the
# limit, arrays of objects from 0.25 and 0.5. A depth of 0.7 and 0.35 of the limit is inside the window of each.
LIMIT = sys.getrecursionlimit()
MID = [("arrays", int(LIMIT * 0.7)), ("arrays_of_objects", int(LIMIT * 0.35))]


@pytest.mark.parametrize("pid", ["xx", "fa"], ids=["new-id", "shipped-id"])
@pytest.mark.parametrize("shape,depth", MID, ids=[m[0] for m in MID])
def test_a_deep_pack_that_parses_is_refused_by_the_schema_naming_the_file(h, L, LocaleError, shape, depth, pid):
    data = _nested(pid, shape, depth)
    json.loads(data)  # precondition: the parser accepts it
    with pytest.raises(LocaleError):  # precondition: merging the pack as it is would fail ...
        L.merge([("a", json.loads(data)), ("b", {})])
    put(h, pid + ".json", data)  # ... but the closed schema refuses the unknown key before `_pack` merges: a valid pack is shallow
    set_profile(h, locales=["fa"])
    for fn in (L.installed, L.active, L.view):
        with pytest.raises(LocaleError) as e:
            fn(ctx_of(h))
        assert pid + ".json" in str(e.value) and "unexpected property 'w'" in str(e.value)


@pytest.mark.parametrize("kind", ["objects", "arrays"])
def test_merge_of_two_in_memory_packs_too_deep_is_a_locale_error(L, LocaleError, kind):
    def deep():
        d = {} if kind == "objects" else []
        for _ in range(2 * LIMIT):
            d = {"w": d} if kind == "objects" else [d]
        return {"words": d}
    with pytest.raises(LocaleError) as e:
        L.merge([("first_pack", deep()), ("second_pack", deep())])
    assert "first_pack" in str(e.value) and "second_pack" in str(e.value)


def test_a_pack_one_byte_over_the_limit_is_refused(h, L, LocaleError):
    put(h, "big.json", _sized("big", L.MAX_BYTES + 1))
    with pytest.raises(LocaleError) as e:
        L.installed(ctx_of(h))
    assert "big.json" in str(e.value)


def test_a_pack_of_exactly_the_limit_is_accepted(h, L):
    data = _sized("edge", L.MAX_BYTES)
    assert len(data) == L.MAX_BYTES
    put(h, "edge.json", data)
    assert "edge" in L.installed(ctx_of(h))


def test_a_pack_stem_of_32_characters_is_accepted(h, L):
    stem = "a-b_c" + "9" * 27
    assert len(stem) == 32
    put(h, stem + ".json", pack(stem))
    assert stem in L.installed(ctx_of(h))


def test_an_invalid_pack_stops_active_and_view_too(h, L, LocaleError):
    put(h, "broken.json", b"{")
    set_profile(h, locales=["fa"])
    for fn in (L.active, L.view):
        with pytest.raises(LocaleError) as e:
            fn(ctx_of(h))
        assert "broken.json" in str(e.value)


def test_a_shipped_pack_is_validated_like_a_live_one(L, LocaleError, tmp_path, monkeypatch):
    from harness_core import protected
    d = tmp_path / "defaults"
    (d / "locales").mkdir(parents=True)
    (d / "locales" / "en.json").write_text(json.dumps(pack("en")))
    (d / "locales" / "fa.json").write_text(json.dumps(pack("zz")))
    monkeypatch.setattr(protected, "DEFAULTS_DIR", d)
    with pytest.raises(LocaleError) as e:
        L.installed()
    assert "fa.json" in str(e.value)


def test_installed_needs_the_base_pack_among_the_shipped_packs(L, LocaleError, tmp_path, monkeypatch):
    from harness_core import protected
    d = tmp_path / "defaults"
    (d / "locales").mkdir(parents=True)
    (d / "locales" / "fa.json").write_text(json.dumps(pack("fa")))
    monkeypatch.setattr(protected, "DEFAULTS_DIR", d)
    with pytest.raises(LocaleError) as e:
        L.installed()
    assert L.BASE in str(e.value)


# --- 7b. render data (task P1-T3) ---------------------------------------------------------------------------------


FA_RENDER = {"digits": "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9", "group": "\u066c",
             "decimal": "\u066b", "joiner": "\u061b ", "script": ["\u0600\u06ff"], "direction": "rtl",
             "lint": [{"chars": "\u064a\u0643", "message": "Arabic \u064a or \u0643 instead of Persian \u06cc or \u06a9"},
                      {"chars": "\u2013\u2014", "message": "a dash in Persian prose"}]}


def test_the_shipped_en_and_fa_packs_hold_the_render_values(L):
    got = L.installed()
    assert got["en"]["render"] == {"en": {"script": ["AZ", "az"], "direction": "ltr"}}
    assert got["fa"]["render"] == {"fa": FA_RENDER}
    assert "render" not in got["ir"]
    assert L.view()["render"] == {"en": {"script": ["AZ", "az"], "direction": "ltr"}, "fa": FA_RENDER}


GOOD = {"digits": "abcdefghij", "group": ".", "decimal": ",", "joiner": "; ", "script": ["az"], "direction": "ltr",
        "lint": [{"chars": "q", "message": "no q"}]}
BAD_RENDER = [
    ("digits-9", {"digits": "abcdefghi"}), ("digits-11", {"digits": "abcdefghijk"}), ("digits-empty", {"digits": ""}),
    ("group-2", {"group": ".."}), ("group-empty", {"group": ""}),
    ("decimal-2", {"decimal": ",,"}), ("decimal-empty", {"decimal": ""}),
    ("joiner-empty", {"joiner": ""}), ("joiner-9", {"joiner": "123456789"}),
    ("script-1", {"script": ["a"]}), ("script-3", {"script": ["abc"]}), ("script-empty-entry", {"script": [""]}),
    ("script-reversed", {"script": ["za"]}), ("script-second-bad", {"script": ["az", "ba"]}),
    ("chars-empty", {"lint": [{"chars": "", "message": "m"}]}),
    ("message-empty", {"lint": [{"chars": "q", "message": ""}]}),
    ("direction", {"direction": "up"}),
    ("unknown-key", {"colour": "red"}),
]


def _accepted(h, L, lang_render, lang="xx"):
    put(h, "xx.json", pack("xx", render={lang: lang_render}))
    return L.view(ctx_of(h))["render"][lang]


@pytest.mark.parametrize("name,change", BAD_RENDER, ids=[n for n, _ in BAD_RENDER])
def test_a_render_value_that_breaks_a_rule_is_refused_naming_the_file(h, L, LocaleError, name, change):
    assert _accepted(h, L, GOOD) == GOOD  # the same pack without the fault loads
    put(h, "xx.json", pack("xx", render={"xx": dict(GOOD, **change)}))
    with pytest.raises(LocaleError) as e:
        L.installed(ctx_of(h))
    assert "xx.json" in str(e.value)


@pytest.mark.parametrize("code", ["XX", "x y", "", "a" * 33, "caf\u00e9", "x.y"])
def test_a_language_code_outside_the_pack_id_form_is_refused_naming_the_file(h, L, LocaleError, code):
    assert _accepted(h, L, GOOD) == GOOD
    put(h, "xx.json", pack("xx", render={code: GOOD}))
    with pytest.raises(LocaleError) as e:
        L.installed(ctx_of(h))
    assert "xx.json" in str(e.value)


def test_render_must_be_an_object_of_objects(h, L, LocaleError):
    assert _accepted(h, L, GOOD) == GOOD
    for bad in ([], {"xx": []}, {"xx": "ltr"}):
        put(h, "xx.json", pack("xx", render=bad))
        with pytest.raises(LocaleError) as e:
            L.installed(ctx_of(h))
        assert "xx.json" in str(e.value)


def test_the_limits_of_the_render_rules_are_inclusive_and_count_characters(h, L):
    edge = {"digits": "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9", "group": "\u066c",
            "decimal": "\u066b", "joiner": "12345678", "script": ["aa", "\u0600\u06ff"], "direction": "rtl",
            "lint": [{"chars": "\u064a", "message": "m"}]}
    assert _accepted(h, L, edge, lang="a" * 32) == edge
    assert _accepted(h, L, {"joiner": "x"}) == {"joiner": "x"}
    assert _accepted(h, L, {}) == {}


def test_a_live_render_adds_to_a_shipped_pack_and_never_replaces(h, L):
    put(h, "fa.json", pack("fa", render={"fa": {"joiner": "|", "script": ["\u0750\u077f"], "direction": "ltr",
                                                 "lint": [{"chars": "z", "message": "no z"}]}}))
    got = L.installed(ctx_of(h))["fa"]["render"]["fa"]
    assert got["joiner"] == "\u061b " and got["direction"] == "rtl"  # the first scalar wins: the shipped one
    assert got["script"] == ["\u0600\u06ff", "\u0750\u077f"]
    assert got["lint"][:2] == FA_RENDER["lint"] and got["lint"][2] == {"chars": "z", "message": "no z"}


# --- 8. entries that are not packs --------------------------------------------------------------------------------


def test_live_entries_that_do_not_end_with_json_are_ignored(h, L, tmp_path):
    d = live_dir(h)
    (d / "notes.txt").write_text("not json at all")
    (d / "xx.json.bak").write_text("{")
    (d / "README").write_bytes(b"\xff\xfe")
    (d / "UPPER.JSON").write_text("{")  # the ending is compared as written
    (d / "subdir").mkdir()
    (d / "subdir" / "inner.json").write_text("{")
    os.symlink(tmp_path / "nowhere", d / "link.txt")
    assert sorted(L.installed(ctx_of(h))) == SHIPPED
    assert L.active(ctx_of(h)) == SHIPPED


# --- 9. merge -----------------------------------------------------------------------------------------------------


def test_merge_recurses_into_objects(L):
    a = ("a", {"words": {"x": ["1"], "y": "s", "deep": {"p": [1]}}, "only_a": True})
    b = ("b", {"words": {"x": ["2"], "z": 5, "deep": {"q": [2], "p": [3]}}, "only_b": [0]})
    assert L.merge([a, b]) == {"words": {"x": ["1", "2"], "y": "s", "z": 5, "deep": {"p": [1, 3], "q": [2]}},
                               "only_a": True, "only_b": [0]}


def test_merge_keeps_list_order_and_drops_repeats(L):
    got = L.merge([("a", {"l": [1, 2]}), ("b", {"l": [2, 3, 3]}), ("c", {"l": [4, 1]})])
    assert got == {"l": [1, 2, 3, 4]}
    objs = L.merge([("a", {"l": [{"k": 1}, {"k": 2}]}), ("b", {"l": [{"k": 2}, {"k": 3}, {"k": 1, "j": 0}]})])
    assert objs == {"l": [{"k": 1}, {"k": 2}, {"k": 3}, {"k": 1, "j": 0}]}


def test_merge_follows_the_order_of_the_packs(L):
    p, q = ("p", {"l": ["p"]}), ("q", {"l": ["q"], "s": "q"})
    assert L.merge([p, q]) == {"l": ["p", "q"], "s": "q"}
    assert L.merge([q, p]) == {"l": ["q", "p"], "s": "q"}


def test_merge_first_scalar_wins_even_when_it_is_falsy(L):
    a = ("a", {"s": "", "n": 0, "b": False, "z": None, "t": "a"})
    b = ("b", {"s": "b", "n": 2, "b": True, "z": "x", "t": 1, "late": 0})
    assert L.merge([a, b]) == {"s": "", "n": 0, "b": False, "z": None, "t": "a", "late": 0}


def test_merge_keeps_the_first_list_as_written_and_skips_what_is_already_there(L):
    assert L.merge([("a", {"l": [1, 1]})]) == {"l": [1, 1]}
    assert L.merge([("a", {"l": [1, 1]}), ("b", {"l": [1, 2, 2]})]) == {"l": [1, 1, 2]}
    objs = [{"k": 1}, {"k": 1}]
    assert L.merge([("a", {"l": objs}), ("b", {"l": [{"k": 1}, {"k": 2}, {"k": 2}]})]) == \
        {"l": [{"k": 1}, {"k": 1}, {"k": 2}]}


def test_merge_of_one_pack_is_an_equal_copy(L):
    src = {"w": {"l": [{"k": [1]}]}, "s": 1}
    got = L.merge([("a", src)])
    assert got == src


@pytest.mark.parametrize("a,b", [
    ({"words": {"money_words": ["x"]}}, {"words": {"money_words": {"k": 1}}}),
    ({"words": {"money_words": {"k": 1}}}, {"words": {"money_words": ["x"]}}),
    ({"words": {"money_words": ["x"]}}, {"words": {"money_words": "x"}}),
    ({"words": {"money_words": "x"}}, {"words": {"money_words": ["x"]}}),
    ({"words": {"money_words": {"k": 1}}}, {"words": {"money_words": 3}}),
    ({"words": {"money_words": None}}, {"words": {"money_words": ["x"]}}),
])
def test_merge_names_the_key_and_both_packs_on_a_kind_conflict(L, LocaleError, a, b):
    with pytest.raises(LocaleError) as e:
        L.merge([("first_pack", a), ("second_pack", b)])
    msg = str(e.value)
    assert "words.money_words" in msg and "first_pack" in msg and "second_pack" in msg


def test_merge_finds_a_conflict_in_a_later_pack_and_at_the_top_level(L, LocaleError):
    with pytest.raises(LocaleError) as e:
        L.merge([("p1", {"k": [1]}), ("p2", {"j": 1}), ("p3", {"k": 1})])
    assert "k" in str(e.value) and "p3" in str(e.value)
    with pytest.raises(LocaleError):
        L.merge([("p1", {"k": {"a": 1}}), ("p2", {"k": [1]})])


def test_merge_does_not_change_its_inputs_or_share_their_lists(L):
    a = {"w": {"l": [{"k": 1}], "s": "a"}, "t": [1]}
    b = {"w": {"l": [{"k": 2}], "s": "b"}, "u": {"deep": [9]}}
    a0, b0 = copy.deepcopy(a), copy.deepcopy(b)
    got = L.merge([("a", a), ("b", b)])
    assert a == a0 and b == b0
    got["w"]["l"].append("added")
    got["t"].append(2)
    got["u"]["deep"].append(10)
    got["w"]["l"][0]["k"] = 99
    assert a == a0 and b == b0


# --- 10. no stale result ------------------------------------------------------------------------------------------


def test_a_pack_written_after_the_first_call_is_seen_by_the_second(h, L):
    c = ctx_of(h)
    assert sorted(L.installed(c)) == SHIPPED and L.active(c) == SHIPPED
    put(h, "xx.json", pack("xx", words={"money_words": ["one"]}))
    assert sorted(L.installed(c)) == SHIPPED + ["xx"] and L.active(c)[-1] == "xx"
    assert L.view(c)["words"]["money_words"][-1:] == ["one"]  # xx is the last active pack
    put(h, "xx.json", pack("xx", words={"money_words": ["one", "two"]}))
    assert L.view(c)["words"]["money_words"][-2:] == ["one", "two"]
    (live_dir(h) / "xx.json").unlink()
    assert sorted(L.installed(c)) == SHIPPED


def test_a_profile_change_changes_active_in_the_same_process(h, L):
    c = ctx_of(h)
    assert L.active(c) == SHIPPED
    set_profile(h, locales=["ir"])
    assert L.active(c) == ["ir", "en"]
    set_profile(h, locales=["fa", "ir"])
    assert L.active(c) == ["fa", "ir", "en"]
    set_profile(h)
    assert L.active(c) == SHIPPED


# --- 11. view -------------------------------------------------------------------------------------------------------


def test_view_has_none_of_the_pack_header_keys(h, L):
    put(h, "xx.json", pack("xx", name="Extra", words={"money_words": ["zloty"]}))
    for got in (L.view(), L.view(ctx_of(h))):
        assert not {"schema", "id", "name"} & set(got)
    assert "zloty" in L.view(ctx_of(h))["words"]["money_words"] and "zloty" not in json.dumps(L.view())


def test_view_merges_the_active_packs_in_the_order_of_active(h, L):
    put(h, "xx.json", pack("xx", words={"money_words": ["w-xx"]}, render={"zz": {"joiner": "from xx"}}))
    put(h, "fa.json", pack("fa", words={"money_words": ["w-fa"]}, render={"zz": {"joiner": "from fa"}}))
    set_profile(h, locales=["xx", "fa"])
    assert L.view(ctx_of(h))["render"]["zz"]["joiner"] == "from xx"
    assert [w for w in L.view(ctx_of(h))["words"]["money_words"] if w.startswith("w-")] == ["w-xx", "w-fa"]
    set_profile(h, locales=["fa", "xx"])
    assert L.view(ctx_of(h))["render"]["zz"]["joiner"] == "from fa"
    assert [w for w in L.view(ctx_of(h))["words"]["money_words"] if w.startswith("w-")] == ["w-fa", "w-xx"]


def test_view_leaves_out_a_pack_that_is_not_active(h, L):
    put(h, "xx.json", pack("xx", words={"money_words": ["only-in-xx"]}))
    set_profile(h, locales=["fa"])
    assert "only-in-xx" not in json.dumps(L.view(ctx_of(h)))
    set_profile(h, locales=["xx"])
    assert "only-in-xx" in json.dumps(L.view(ctx_of(h)))


# --- 11b. every installed pack (P1-T3 fix round 1) ----------------------------------------------------------------


def test_view_every_adds_the_installed_packs_the_profile_list_leaves_out(h, L):
    set_profile(h, locales=["ir"])
    c = ctx_of(h)
    assert L.active(c) == ["ir", "en"]
    assert set(L.view(c)["render"]) == {"en"}
    got = L.view(c, every=True)["render"]
    assert got["fa"] == FA_RENDER and got["en"] == {"script": ["AZ", "az"], "direction": "ltr"}
    assert L.view(None, every=True) == L.view(None)  # without a profile every pack is active already


def test_view_every_takes_the_active_packs_first_then_the_others_sorted(h, L):
    def lang(label, **render):  # render and state_lexicon are two of the keys that every installed pack supplies
        return dict(render=dict({"zz": {"joiner": label}}, **render), state_lexicon={"terms": [label]})

    def mine(view):
        return [t for t in view["state_lexicon"]["terms"] if t in ("xx", "aa", "fa")]
    put(h, "xx.json", pack("xx", **lang("xx")))
    put(h, "aa.json", pack("aa", **lang("aa", zz_only_aa={"joiner": "a"})))
    put(h, "fa.json", pack("fa", **lang("fa", zz_only_aa={"joiner": "f"})))
    set_profile(h, locales=["xx"])
    c = ctx_of(h)
    assert L.active(c) == ["xx", "en"]
    assert mine(L.view(c)) == ["xx"] and "zz_only_aa" not in L.view(c)["render"]  # the default is unchanged
    got = L.view(c, every=True)
    assert got["render"]["zz"]["joiner"] == "xx"   # an active pack wins
    assert mine(got) == ["xx", "aa", "fa"]         # then aa, fa, ir by id
    assert got["render"]["zz_only_aa"]["joiner"] == "a"  # of the others, the first by id wins
    set_profile(h, locales=["fa", "xx"])
    assert L.view(ctx_of(h), every=True)["render"]["zz"]["joiner"] == "fa"
    assert L.view(ctx_of(h), every=True)["render"]["zz_only_aa"]["joiner"] == "f"  # fa is active now


def test_view_every_still_refuses_an_invalid_pack(h, L, LocaleError):
    put(h, "broken.json", b"{")
    set_profile(h, locales=["fa"])
    with pytest.raises(LocaleError) as e:
        L.view(ctx_of(h), every=True)
    assert "broken.json" in str(e.value)


# --- 12. hash layers --------------------------------------------------------------------------------------------


def test_a_live_pack_changes_the_control_hash_only(h, L):
    before = session.current_hashes(ctx_of(h))
    put(h, "xx.json", pack("xx"))
    assert "xx" in L.installed(ctx_of(h))
    after = session.current_hashes(ctx_of(h))
    assert after["control"] != before["control"]
    assert after["business"] == before["business"] and after["plugin"] == before["plugin"]
    put(h, "xx.json", pack("xx", name="Changed"))
    assert session.current_hashes(ctx_of(h))["control"] not in (before["control"], after["control"])


def test_a_new_session_stops_on_a_live_pack_nobody_approved(h, L):
    put(h, "xx.json", pack("xx"))
    assert "xx" in L.installed(ctx_of(h))
    assert hookmain.main("SessionStart", {"session_id": "s2", "cwd": str(h["ws"]), "source": "startup",
                                          "permission_mode": "default"}, h["env"])[0] == 2
    status = json.loads((h["data"] / "sessions" / "s2" / "status.json").read_text())
    assert status["ok"] is False and any("control" in r for r in status["reasons"]), status


# --- 13. the owner's command --------------------------------------------------------------------------------------


def test_the_owner_adds_a_pack_with_the_code_and_it_becomes_the_baseline(h, L):
    shown = typed(h, "/dynamic-harness:business add locales fa", "b1")
    assert "locale packs" in shown and business.profile(ctx_of(h)) == {}
    assert "Done." in confirm(h, "business", shown, "b2")
    c = ctx_of(h)
    assert business.profile(c)["locales"] == ["fa"]
    rec = session.recorded_hashes(h["data"] / "config_log.jsonl")
    assert rec["business"] == session.current_hashes(c)["business"]
    assert L.active(c) == ["fa", "en"]
    assert hookmain.main("SessionStart", {"session_id": "s2", "cwd": str(h["ws"]), "source": "startup",
                                          "permission_mode": "default"}, h["env"])[0] == 0
    status = json.loads((h["data"] / "sessions" / "s2" / "status.json").read_text())
    assert status["ok"] is True, status


def test_two_packs_keep_the_order_they_were_added_in(h, L):
    for i, pid in enumerate(("ir", "fa")):
        confirm(h, "business", typed(h, f"/dynamic-harness:business add locales {pid}", f"a{i}"), f"c{i}")
    assert business.profile(ctx_of(h))["locales"] == ["ir", "fa"]
    assert L.active(ctx_of(h)) == ["ir", "fa", "en"]


def test_an_unknown_pack_is_refused_when_the_owner_types_it(h, L):
    shown = typed(h, "/dynamic-harness:business add locales zz", "b1")
    assert "unknown locale pack 'zz'; installed: en, fa, ir" in shown
    assert not re.search(r"confirm \d{6}", shown), shown  # no code is offered
    assert "Nothing is waiting" in typed(h, "/dynamic-harness:business confirm 000000", "b2")
    assert business.profile(ctx_of(h)) == {} and not (h["data"] / "business" / "profile.json").exists()


def test_the_check_at_confirm_time_refuses_too(h, L):
    with pytest.raises(Denied) as e:
        business.change_list(ctx_of(h), "locales", "zz", True)
    assert "unknown locale pack 'zz'; installed: en, fa, ir" in str(e.value)
    assert not (h["data"] / "business" / "profile.json").exists()


def test_the_refusal_lists_the_live_packs_too_and_a_live_pack_can_be_added(h, L):
    put(h, "xx.json", pack("xx"))
    with pytest.raises(Denied) as e:
        business.change_list(ctx_of(h), "locales", "zz", True)
    assert "unknown locale pack 'zz'; installed: en, fa, ir, xx" in str(e.value)
    assert not (h["data"] / "business" / "profile.json").exists()
    business.change_list(ctx_of(h), "locales", "xx", True)
    assert business.profile(ctx_of(h))["locales"] == ["xx"] and L.active(ctx_of(h)) == ["xx", "en"]


def test_an_unknown_pack_leaves_the_profile_as_it_was(h, L):
    confirm(h, "business", typed(h, "/dynamic-harness:business add locales fa", "b1"), "b2")
    path = h["data"] / "business" / "profile.json"
    before = path.read_bytes()
    with pytest.raises(Denied):
        business.change_list(ctx_of(h), "locales", "zz", True)
    assert path.read_bytes() == before and business.profile(ctx_of(h))["locales"] == ["fa"]


def test_the_owner_removes_an_id_whose_pack_is_gone(h, L):
    put(h, "xx.json", pack("xx"))
    confirm(h, "business", typed(h, "/dynamic-harness:business add locales xx", "b1"), "b2")
    (live_dir(h) / "xx.json").unlink()
    assert L.active(ctx_of(h)) == SHIPPED  # the list names a pack that is not installed: every pack applies
    assert "Done." in confirm(h, "business", typed(h, "/dynamic-harness:business remove locales xx", "b3"), "b4")
    assert business.profile(ctx_of(h))["locales"] == []


def test_the_owner_removes_a_pack(h, L):
    confirm(h, "business", typed(h, "/dynamic-harness:business add locales fa", "b1"), "b2")
    confirm(h, "business", typed(h, "/dynamic-harness:business add locales ir", "b3"), "b4")
    assert "Done." in confirm(h, "business", typed(h, "/dynamic-harness:business remove locales fa", "b5"), "b6")
    assert business.profile(ctx_of(h))["locales"] == ["ir"] and L.active(ctx_of(h)) == ["ir", "en"]
    confirm(h, "business", typed(h, "/dynamic-harness:business remove locales ir", "b7"), "b8")
    assert L.active(ctx_of(h)) == SHIPPED  # an empty list is the 0.4.0 behaviour again


def test_business_show_lists_the_packs_when_they_are_set(h, L):
    assert "locale" not in typed(h, "/dynamic-harness:business show", "b0")
    confirm(h, "business", typed(h, "/dynamic-harness:business add locales ir", "b1"), "b2")
    confirm(h, "business", typed(h, "/dynamic-harness:business add locales fa", "b3"), "b4")
    shown = typed(h, "/dynamic-harness:business show", "b5")
    assert "- The active locale packs (ids of installed packs): ir, fa" in shown


def test_the_list_is_named_in_list_fields():
    assert business.LIST_FIELDS["locales"] == "the active locale packs (ids of installed packs)"


# --- 14. matching words, state words and question marks (task P1-T2) ----------------------------------------------

GOOD_WORDS = {"money_words": ["a"]}
# (id, a pack part that loads, the same part with one fault)
BAD_PARTS = [
    ("words-key", {"words": GOOD_WORDS}, {"words": {"zz_probe": ["a"]}}),
    ("verbs-class", {"words": {"verbs": {"money": ["a"]}}}, {"words": {"verbs": {"zz_class": ["a"]}}}),
    ("verbs-anywhere-class", {"words": {"verbs_anywhere": {"money": ["a"]}}},
     {"words": {"verbs_anywhere": {"zz_class": ["a"]}}}),
    ("entry-int", {"words": GOOD_WORDS}, {"words": {"money_words": ["a", 1]}}),
    ("entry-null", {"words": GOOD_WORDS}, {"words": {"quantity_words": [None]}}),
    ("verb-entry-int", {"words": {"verbs": {"money": ["a"]}}}, {"words": {"verbs": {"money": [1]}}}),
    ("anywhere-entry-list", {"words": {"verbs_anywhere": {"money": ["a"]}}},
     {"words": {"verbs_anywhere": {"money": [["a"]]}}}),
    ("decision-entry-int", {"words": {"decision_keywords": {"tax": ["a"]}}},
     {"words": {"decision_keywords": {"tax": [2]}}}),
    ("mark-entry-int", {"question_marks": ["?"]}, {"question_marks": [1]}),
    ("term-entry-int", {"state_lexicon": {"terms": ["a"]}}, {"state_lexicon": {"terms": [1]}}),
    ("line-entry-int", {"state_lexicon": {"template_lines": ["a"]}}, {"state_lexicon": {"template_lines": [1]}}),
    ("list-is-string", {"words": GOOD_WORDS}, {"words": {"money_words": "a"}}),
    ("verbs-is-list", {"words": {"verbs": {"money": ["a"]}}}, {"words": {"verbs": ["a"]}}),
    ("verbs-class-is-string", {"words": {"verbs": {"money": ["a"]}}}, {"words": {"verbs": {"money": "a"}}}),
    ("decision-class-is-string", {"words": {"decision_keywords": {"tax": ["a"]}}},
     {"words": {"decision_keywords": {"tax": "a"}}}),
    ("marks-is-string", {"question_marks": ["?"]}, {"question_marks": "?"}),
    ("state-lexicon-is-list", {"state_lexicon": {"terms": ["a"]}}, {"state_lexicon": ["a"]}),
    ("empty-word", {"words": GOOD_WORDS}, {"words": {"money_words": ["a", ""]}}),
    ("empty-join", {"words": {"imperative_join": ["a"]}}, {"words": {"imperative_join": [""]}}),
    ("empty-verb", {"words": {"verbs": {"money": ["a"]}}}, {"words": {"verbs": {"money": [""]}}}),
    ("empty-anywhere-verb", {"words": {"verbs_anywhere": {"money": ["a"]}}},
     {"words": {"verbs_anywhere": {"money": [""]}}}),
    ("empty-decision-word", {"words": {"decision_keywords": {"zz_class": ["a"]}}},
     {"words": {"decision_keywords": {"zz_class": [""]}}}),
    ("empty-term", {"state_lexicon": {"terms": ["a"]}}, {"state_lexicon": {"terms": ["a", ""]}}),
    ("empty-line", {"state_lexicon": {"template_lines": ["a"]}}, {"state_lexicon": {"template_lines": [""]}}),
    ("mark-of-2-characters", {"question_marks": ["?"]}, {"question_marks": ["?", "ab"]}),
    ("mark-empty", {"question_marks": ["?"]}, {"question_marks": [""]}),
    ("state-lexicon-key", {"state_lexicon": {"terms": ["a"]}}, {"state_lexicon": {"terms": ["a"], "zz": []}}),
]


@pytest.mark.parametrize("name,good,bad", BAD_PARTS, ids=[b[0] for b in BAD_PARTS])
def test_a_pack_part_that_breaks_a_rule_is_refused_naming_the_file(h, L, LocaleError, name, good, bad):
    put(h, "xx.json", pack("xx", **good))
    assert "xx" in L.installed(ctx_of(h))  # the same part without the fault loads
    put(h, "xx.json", pack("xx", **bad))
    with pytest.raises(LocaleError) as e:
        L.installed(ctx_of(h))
    assert "xx.json" in str(e.value)


def test_any_class_name_is_accepted_under_decision_keywords_and_the_five_under_verbs(h, L):
    classes = ["external_message", "money", "destructive", "automation_create", "local_reversible"]
    put(h, "xx.json", pack("xx", words={"decision_keywords": {"zz_class": ["blorp"], "Any Name": ["glorp"]},
                                       "verbs": {c: [c + " it"] for c in classes},
                                       "verbs_anywhere": {c: [c + " now"] for c in classes}}))
    got = L.view(ctx_of(h))["words"]
    assert got["decision_keywords"]["zz_class"] == ["blorp"] and got["decision_keywords"]["Any Name"] == ["glorp"]
    assert all(c + " it" in got["verbs"][c] and c + " now" in got["verbs_anywhere"][c] for c in classes)


def test_a_pack_can_hold_every_new_key_and_a_mark_counts_characters_not_bytes(h, L):
    words = {"verbs": {"money": ["a"]}, "verbs_anywhere": {"money": ["b"]}, "imperative_lead": ["c"],
             "imperative_join": ["d"], "question_lead": ["e"], "money_words": ["f"], "quantity_words": ["g"],
             "decision_keywords": {"tax": ["h"]}, "continuation": ["i"]}
    put(h, "xx.json", pack("xx", words=words, state_lexicon={"terms": ["t"], "template_lines": ["l"]},
                           question_marks=["\u2e2e", "\u061f"]))
    got = L.installed(ctx_of(h))["xx"]
    assert got["words"] == words and got["question_marks"] == ["\u2e2e", "\u061f"]
    assert got["state_lexicon"] == {"terms": ["t"], "template_lines": ["l"]}


# --- 14b. EVERY and view(ctx, every=True) --------------------------------------------------------------------------


def test_every_names_the_keys_that_apply_for_every_installed_pack(L):
    assert L.EVERY == ("render", "state_lexicon", "question_marks")


def test_view_every_adds_only_the_every_keys_of_a_pack_the_profile_does_not_name(h, L):
    put(h, "xx.json", pack("xx", words={"money_words": ["zloty"]}, render={"xx": {"joiner": "|"}},
                           state_lexicon={"terms": ["blorped"], "template_lines": ["Blorp."]},
                           question_marks=["\u2e2e"]))
    set_profile(h, locales=["fa"])
    c = ctx_of(h)
    assert L.active(c) == ["fa", "en"]
    default, every = L.view(c), L.view(c, every=True)
    assert "xx" not in default["render"] and "blorped" not in default["state_lexicon"]["terms"]
    assert "\u2e2e" not in default["question_marks"]
    assert every["render"]["xx"] == {"joiner": "|"}
    assert every["state_lexicon"]["terms"][-1] == "blorped" and every["state_lexicon"]["template_lines"][-1] == "Blorp."
    assert every["question_marks"][-1] == "\u2e2e"
    assert every["words"] == default["words"]  # none of the words of xx, none of the words of ir
    assert "zloty" not in json.dumps(every["words"]) and "toman" not in json.dumps(every["words"])


def test_view_every_keeps_the_whole_content_of_the_active_packs(h, L):
    put(h, "xx.json", pack("xx", words={"money_words": ["zloty"]}))
    set_profile(h, locales=["xx"])
    c = ctx_of(h)
    assert "zloty" in L.view(c, every=True)["words"]["money_words"]
    assert "toman" not in L.view(c, every=True)["words"]["money_words"]
    assert L.view(None, every=True)["state_lexicon"]["terms"][0] == "checked"


def _plain(pid, **more):
    return dict({"schema": SCHEMA, "id": pid, "name": pid}, **more)


def test_a_kind_conflict_under_words_with_a_pack_that_is_not_active_breaks_no_view_call(h, L, LocaleError, monkeypatch):
    # No two valid packs can disagree on the kind of a words key (the schema fixes it), so the packs are handed over
    # as they would be if they could: en is active, xx is not.
    packs = {"en": _plain("en", words={"money_words": ["a"]}), "xx": _plain("xx", words={"money_words": {"k": 1}})}
    monkeypatch.setattr(L, "installed", lambda ctx=None: packs)
    set_profile(h, locales=["en"])
    c = ctx_of(h)
    assert L.active(c) == ["en"]
    assert L.view(c)["words"]["money_words"] == ["a"]
    assert L.view(c, every=True)["words"]["money_words"] == ["a"]
    set_profile(h, locales=["xx"])  # both active: the conflict is still an error
    with pytest.raises(LocaleError) as e:
        L.view(ctx_of(h))
    assert "words.money_words" in str(e.value)


# --- 14c. the pack of the profile language is active (R7 item 3) ---------------------------------------------------


def _fa_holder(h):
    put(h, "zz.json", pack("zz", render={"fa": {"joiner": "|"}}))


@pytest.mark.parametrize("prof,live_zz,expected", [
    (dict(locales=["ir"], language="fa"), False, ["ir", "fa", "en"]),
    (dict(locales=["ir"], language="fa"), True, ["ir", "fa", "zz", "en"]),
    (dict(locales=["en", "ir"], language="fa"), False, ["en", "ir", "fa"]),
    (dict(locales=["ir"]), False, ["ir", "en"]),
    (dict(locales=["ir"]), True, ["ir", "en"]),
    (dict(locales=["fa"], language="fa"), True, ["fa", "zz", "en"]),
    (dict(locales=["ir", "fa"], language="fa"), False, ["ir", "fa", "en"]),
    (dict(locales=["ir"], language="en"), False, ["ir", "en"]),
    (dict(locales=["en"], language="en"), False, ["en"]),
    (dict(locales=["ir"], language="de"), False, ["ir", "en"]),     # no pack holds render.de
    (dict(locales=["ir"], language=""), False, ["ir", "en"]),
    (dict(locales=["ir"], language=7), False, ["ir", "en"]),
    (dict(locales=["ir"], language=["fa"]), False, ["ir", "en"]),
    (dict(language="fa"), False, SHIPPED),                           # no list in force: every pack is active
    (dict(language="fa"), True, SHIPPED + ["zz"]),
    (dict(locales=[], language="fa"), False, SHIPPED),
    (dict(locales=["nope"], language="fa"), False, SHIPPED),
])
def test_the_pack_of_the_profile_language_is_active_with_the_list_that_leaves_it_out(h, L, prof, live_zz, expected):
    if live_zz:
        _fa_holder(h)
    set_profile(h, **prof)
    assert L.active(ctx_of(h)) == expected


def test_the_words_of_the_profile_language_pack_are_in_the_view(h, L):
    pay = "\u067e\u0631\u062f\u0627\u062e\u062a \u06a9\u0646"
    set_profile(h, locales=["ir"], language="fa")
    words = L.view(ctx_of(h))["words"]
    assert pay in words["verbs_anywhere"]["money"] and "toman" in words["money_words"] and "pay" in words["verbs"]["money"]
    set_profile(h, locales=["ir"])
    words = L.view(ctx_of(h))["words"]
    assert "verbs_anywhere" not in words and "toman" in words["money_words"] and "pay" in words["verbs"]["money"]
    set_profile(h, locales=["en"], language="fa")
    assert pay in L.view(ctx_of(h))["words"]["verbs_anywhere"]["money"]


def test_the_profile_language_rule_follows_a_profile_change_in_the_same_process(h, L):
    c = ctx_of(h)
    set_profile(h, locales=["ir"])
    assert L.active(c) == ["ir", "en"]
    set_profile(h, locales=["ir"], language="fa")
    assert L.active(c) == ["ir", "fa", "en"]
    set_profile(h, locales=["ir"], language="de")
    assert L.active(c) == ["ir", "en"]


def test_the_packs_added_for_the_profile_language_are_sorted_by_id(h, L):
    put(h, "bb.json", pack("bb", render={"fa": {"joiner": "|"}}))  # written first, sorts after aa
    put(h, "aa.json", pack("aa", render={"fa": {"joiner": "|"}}))
    set_profile(h, locales=["ir"], language="fa")
    assert L.active(ctx_of(h)) == ["ir", "aa", "bb", "fa", "en"]  # not the order in which the packs are installed

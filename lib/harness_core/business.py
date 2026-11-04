"""Business layer (spec §Business governance; §Business questions "Metric dictionary", "Sources"; build phase P5).

Files under ``${CLAUDE_PLUGIN_DATA}/business/`` (read-denied to the model, write-denied to every tool):

- ``profile.json``: currency and unit, calendar, fiscal year start, work week, jurisdiction, language,
  money caps, thresholds, footers, do-not-contact list, retention;
- ``metric_dictionary.json``: ``{metrics: {metric_id: {inclusions, exclusions, unit, currency,
  source_precedence, date_anchor, version, approved_by, approved_at, sentence}}}``;
- ``source_registry.json``: ``{sources: {source_id: {role, as_of, coverage_min, coverage_max, unit, sha256}}}``;
- ``protected_terms.json``: ``{terms: [...]}`` (the query guard's list).

``snapshot(ctx)`` is what pack checks see. Writers live in ``business_cmd`` (the owner's typed command) and
``contact`` (a definition the owner confirmed).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .errors import Denied
from .safeio import atomic_write, safe_open

FILES = {"profile": "profile.json", "dictionary": "metric_dictionary.json", "sources": "source_registry.json",
         "protected_terms": "protected_terms.json"}


def _load(ctx, name) -> dict:
    root = Path(ctx.data_root) / "business"
    f = root / name
    if not f.exists():
        return {}
    try:
        v = json.loads(safe_open(f, [root]))
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def profile(ctx) -> dict:
    return _load(ctx, FILES["profile"])


def dictionary(ctx) -> dict:
    return _load(ctx, FILES["dictionary"]).get("metrics") or {}


def sources(ctx) -> dict:
    return _load(ctx, FILES["sources"]).get("sources") or {}


def snapshot(ctx) -> dict:
    """The business facts a pack check may use (never protected terms: checks need none)."""
    return {"profile": profile(ctx), "dictionary": dictionary(ctx), "sources": sources(ctx)}


# --- writes (only from hooks: the owner's typed command, or a definition the owner confirmed) -------------------
CURRENCIES = {"IRT": "Toman", "IRR": "Rial", "USD": "US dollars", "EUR": "euros", "GBP": "pounds", "AED": "dirhams",
              "TRY": "lira"}
PROFILE_FIELDS = {
    "currency": "your currency", "calendar": "your calendar", "fiscal_year_start": "the first day of your fiscal year",
    "work_week": "your work week", "jurisdiction": "your jurisdiction", "language": "the language I write to you in",
    "caps.task": "the spending limit per task", "caps.month": "the spending limit per month",
    "thresholds.money_at_stake_high": "the amount above which a task counts as high risk",
    "thresholds.recipient_count_high": "the number of recipients above which a task counts as high risk",
    "footer": "the footer your documents must carry", "retention_years": "how many years decision records are kept",
    "fx.USD": "the US dollar rate in your currency", "owner_name": "your name",
}
LIST_FIELDS = {"own_accounts": "your own bank accounts (transfers between them are not revenue)",
               "do_not_contact": "the do-not-contact list", "protected_terms": "names that never go into a web search"}


def parse_value(field, raw: str, profile_calendar=None):
    """The typed value for a profile field, or Denied with what is expected."""
    raw = raw.strip()
    if field == "currency":
        v = raw.upper()
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise Denied("currency is a 3-letter code, for example IRT (Toman), IRR (Rial) or USD")
        return v
    if field == "calendar":
        if raw.lower() not in ("jalali", "gregorian"):
            raise Denied("calendar is jalali or gregorian")
        return raw.lower()
    if field == "language":
        if raw.lower() not in ("en", "fa"):
            raise Denied("language is en or fa")
        return raw.lower()
    if field == "fiscal_year_start":
        from .localize import digits
        m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", digits(raw))
        mo, day = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        # each month's shortest length in the calendar, so the date exists every year (Esfand 29, February 28)
        days = ([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31] if profile_calendar == "gregorian"
                else [31] * 6 + [30] * 5 + [29])
        cap = days[mo - 1] if 1 <= mo <= 12 else 0
        if not 1 <= day <= cap:
            raise Denied("fiscal_year_start is MM-DD in your calendar, for example 01-01 (Farvardin 1), and must "
                         "exist every year")
        return f"{mo:02d}-{day:02d}"
    if field in ("caps.task", "caps.month", "thresholds.money_at_stake_high", "fx.USD"):
        from .localize import parse_amount
        v = parse_amount(raw)
        if v is None or v <= 0:
            raise Denied(f"{field} is a positive number")
        return int(v) if v == v.to_integral_value() else float(v)
    if field in ("thresholds.recipient_count_high", "retention_years"):
        if not re.fullmatch(r"\d{1,6}", raw) or int(raw) < 1:
            raise Denied(f"{field} is a whole number of at least 1")
        if field == "retention_years" and int(raw) > 30:
            raise Denied("retention_years is at most 30")
        return int(raw)
    if field in PROFILE_FIELDS:
        if not raw or len(raw) > 300 or any(ord(c) < 32 for c in raw):
            raise Denied(f"{field} is one line of at most 300 characters")
        return raw
    raise Denied(f"unknown field {field!r}; fields: {', '.join(sorted(PROFILE_FIELDS))}")


def _write(ctx, name, obj) -> None:
    root = Path(ctx.data_root) / "business"
    root.mkdir(parents=True, exist_ok=True)
    if (root / name).is_symlink():
        raise Denied(f"business/{name} is a symlink; refusing to write through it")
    atomic_write(root / name, (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def guard(ctx, layers=("business",)) -> None:
    """Before a change: each layer must still equal its recorded hash, so a re-baseline never blesses an edit
    made outside the harness (that stays STOPPED_TAMPER until an operator looks at it)."""
    from . import session
    log = Path(ctx.data_root) / "config_log.jsonl"
    if not log.is_file():
        raise Denied("this folder is not set up: type /dynamic-harness:setup first")
    rec, cur = session.recorded_hashes(log), session.current_hashes(ctx)
    bad = [k for k in layers if rec.get(k) != cur.get(k)]
    if bad:
        raise Denied(f"the {', '.join(bad)} layer changed outside the harness; nothing was changed")


def baseline(ctx, why, layers=("business",)) -> None:
    """Record the new layer hashes, so SessionStart compares against the owner's change, not the old state."""
    from . import session
    cur = session.current_hashes(ctx)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "hashes": {k: cur[k] for k in layers},
           "by": why}
    with open(Path(ctx.data_root) / "config_log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def set_field(ctx, field, value) -> None:
    guard(ctx)
    prof = profile(ctx)
    head, _, tail = field.partition(".")
    if tail:
        sub = dict(prof.get(head) or {})
        sub[tail] = value
        if head == "caps":
            sub["currency"] = prof.get("currency") or sub.get("currency")
        if head == "fx":
            sub["as_of"] = time.strftime("%Y-%m-%d")
        prof[head] = sub
    else:
        old = prof.get("currency")
        prof[field] = value
        if field == "currency" and isinstance(prof.get("caps"), dict) and old != value:
            caps = dict(prof["caps"], currency=value)
            for k in ("task", "month"):  # a limit keeps its worth: Toman <-> Rial converts, anything else is dropped
                if isinstance(caps.get(k), (int, float)):
                    if {old, value} == {"IRT", "IRR"}:
                        caps[k] = caps[k] * 10 if value == "IRR" else caps[k] / 10
                    else:
                        caps.pop(k)
            prof["caps"] = caps
    prof["schema"] = "harness/business_profile@1"
    _write(ctx, FILES["profile"], prof)
    baseline(ctx, f"business set {field}")


def change_list(ctx, field, value, add: bool) -> None:
    guard(ctx)
    if field not in LIST_FIELDS:
        raise Denied(f"unknown list {field!r}; lists: {', '.join(sorted(LIST_FIELDS))}")
    value = value.strip()
    if not value or len(value) > 200:
        raise Denied("a list entry is 1 to 200 characters")
    if field == "protected_terms":
        doc = _load(ctx, FILES["protected_terms"])
        items = list(doc.get("terms") or [])
    else:
        prof = profile(ctx)
        items = list(prof.get(field) or [])
    if add and value not in items:
        items.append(value)
    elif not add:
        if value not in items:
            raise Denied(f"{value!r} is not in {field}")
        items.remove(value)
    if field == "protected_terms":
        _write(ctx, FILES["protected_terms"], {"schema": "harness/protected_terms@1", "terms": items})
    else:
        _write(ctx, FILES["profile"], dict(prof, **{field: items}, schema="harness/business_profile@1"))
    baseline(ctx, f"business {'add' if add else 'remove'} {field}")


METRIC = {"type": "object", "additionalProperties": False,
          "required": ["metric_id", "inclusions", "exclusions", "unit", "currency"],
          "properties": {"metric_id": {"type": "string"}, "inclusions": {"type": "string"},
                         "exclusions": {"type": "string"}, "unit": {"type": "string"},
                         "currency": {"type": "string"}, "date_anchor": {"type": "string"},
                         "source_precedence": {"type": "array", "items": {"type": "string"}}}}


def define_metric(ctx, metric, sentence, turn_id) -> dict:
    """A dictionary entry the owner confirmed; a changed definition bumps the version (figures are restated)."""
    guard(ctx)
    d = _load(ctx, FILES["dictionary"])
    metrics = dict(d.get("metrics") or {})
    old = metrics.get(metric["metric_id"])
    same = old and all(old.get(k) == metric.get(k) for k in METRIC["properties"])
    entry = dict(metric, version=(old or {}).get("version", 0) + (0 if same else 1), approved_by="owner",
                 approved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), turn_id=turn_id, sentence=sentence,
                 previous_version=(old or {}).get("version") if old and not same else (old or {}).get("previous_version"))
    metrics[metric["metric_id"]] = entry
    _write(ctx, FILES["dictionary"], {"schema": "harness/metric_dictionary@1", "metrics": metrics})
    baseline(ctx, f"metric {metric['metric_id']} v{entry['version']} confirmed by the owner")
    return entry


def remove_metric(ctx, metric_id) -> None:
    guard(ctx)
    d = _load(ctx, FILES["dictionary"])
    metrics = dict(d.get("metrics") or {})
    if metric_id not in metrics:
        raise Denied(f"no metric {metric_id!r}")
    del metrics[metric_id]
    _write(ctx, FILES["dictionary"], {"schema": "harness/metric_dictionary@1", "metrics": metrics})
    baseline(ctx, f"metric {metric_id} removed by the owner")


def register_source(ctx, source_id, facts) -> dict:
    """Code-derived facts about a source (role, as_of, coverage, unit, sha256), from a recorded check output."""
    guard(ctx)
    d = _load(ctx, FILES["sources"])
    src = dict(d.get("sources") or {})
    src[source_id] = dict(src.get(source_id) or {}, **facts)
    _write(ctx, FILES["sources"], {"schema": "harness/source_registry@1", "sources": src})
    baseline(ctx, f"source {source_id} registered")
    return src[source_id]


def describe(ctx) -> list:
    """The business layer in plain words, for /dynamic-harness:business show."""
    prof = profile(ctx)
    out = ["Your business settings:"]
    for f, label in PROFILE_FIELDS.items():
        head, _, tail = f.partition(".")
        v = (prof.get(head) or {}).get(tail) if tail else prof.get(f)
        if v is not None:
            if f == "currency":
                v = f"{CURRENCIES.get(v, v)} ({v})"
            out.append(f"- {label[0].upper() + label[1:]}: {v}")
    for f, label in LIST_FIELDS.items():
        items = (_load(ctx, FILES["protected_terms"]).get("terms") if f == "protected_terms" else prof.get(f)) or []
        if items:
            out.append(f"- {label[0].upper() + label[1:]}: {', '.join(map(str, items))}")
    mets = dictionary(ctx)
    if mets:
        out.append("Metric definitions you confirmed:")
        out += [f"- {m} (version {e.get('version')}): {e.get('sentence') or e.get('inclusions')}"
                for m, e in sorted(mets.items())]
    if len(out) == 1:
        out.append("- nothing is set yet.")
    return out

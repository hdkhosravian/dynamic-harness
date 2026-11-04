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
from pathlib import Path

from .safeio import safe_open

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

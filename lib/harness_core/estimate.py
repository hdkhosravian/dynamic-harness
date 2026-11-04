"""Cost and time estimate for the opening line (spec §Business governance "Cost": "T1/T2 tasks open with an
estimated range"; §Detailed plan ``harness-estimate``: the tier median from past records, ±30%; build phase P5).

Past tasks are the latest decision-record row of each task that ended DONE at the same tier. With fewer than
``MIN_SAMPLES`` of them there is no estimate: a guess presented as a range would be invented.
"""
from __future__ import annotations

import glob
import json
import statistics
from pathlib import Path

MIN_SAMPLES = 3
SPREAD = 0.3


def _rows(ctx):
    for f in glob.glob(str(Path(ctx.data_root) / "records" / "*.jsonl")):
        try:
            rows = [json.loads(x) for x in Path(f).read_text("utf-8").splitlines() if x.strip()]
        except (OSError, ValueError):
            continue
        if rows:
            yield rows[-1]


def estimate(ctx, tier):
    """``{minutes: (lo, hi), money: (lo, hi) | None, samples}`` or None."""
    done = [r for r in _rows(ctx) if r.get("state") == "DONE" and r.get("tier") == tier]
    mins = [r["duration_s"] / 60 for r in done if isinstance(r.get("duration_s"), (int, float))]
    if len(mins) < MIN_SAMPLES:
        return None
    m = statistics.median(mins)
    money = [(r.get("cost") or {}).get("money") for r in done]
    money = [x for x in money if isinstance(x, (int, float))]
    mm = statistics.median(money) if len(money) >= MIN_SAMPLES else None
    rng = (lambda v, places: (round(v * (1 - SPREAD), places), round(v * (1 + SPREAD), places)))
    return {"minutes": tuple(max(1, int(x)) for x in rng(m, 0)), "samples": len(mins),
            "money": rng(mm, 2) if mm is not None else None}

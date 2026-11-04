#!/usr/bin/env python3
"""Release tool (operator): re-hash every pack manifest's entries and approve every shipped SKILL.md (R-5).

Run before a release, after reviewing the changed files: python3 tools/release_hashes.py <release label>.
A skill whose hash is unchanged keeps its approver; a new or changed one gets the release label."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from harness_core import packs  # noqa: E402


def main(label):
    for m in sorted(ROOT.glob("skills/*/manifest.json")):
        data = packs.hash_entries(m.parent)
        m.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    f = ROOT / "control" / "defaults" / "approved_procedures.json"
    cur = json.loads(f.read_text("utf-8"))
    old = {p["path"]: p for p in cur["procedures"]}
    procs = []
    for s in sorted(ROOT.glob("skills/*/SKILL.md")):
        rel = s.relative_to(ROOT).as_posix()
        sha = hashlib.sha256(s.read_bytes()).hexdigest()
        prev = old.get(rel)
        procs.append(prev if prev and prev["sha256"] == sha else {"path": rel, "sha256": sha, "approver": label})
    f.write_text(json.dumps(dict(cur, procedures=procs), indent=2) + "\n", "utf-8")
    print(f"{len(procs)} procedures approved; manifests re-hashed")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "release 0.4.0")

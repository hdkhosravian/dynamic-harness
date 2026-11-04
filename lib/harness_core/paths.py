from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobPaths:
    data_root: Path
    workspace: Path
    job: str
    run: Path
    work: Path
    requests: Path
    heads: Path
    keys: Path


def job_paths(data_root, workspace, job: str) -> JobPaths:
    data_root, workspace = Path(data_root), Path(workspace)
    loops = workspace / ".claude" / "loops" / job
    return JobPaths(
        data_root=data_root,
        workspace=workspace,
        job=job,
        run=data_root / "runs" / job,
        work=loops / "work",
        requests=loops / "requests",
        heads=data_root / "heads" / job,
        keys=data_root / "keys",
    )

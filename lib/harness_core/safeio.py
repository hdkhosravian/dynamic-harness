from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from .errors import TamperError


def _under(path: Path, roots) -> bool:
    for r in roots:
        try:
            path.relative_to(Path(r).resolve())
            return True
        except ValueError:
            continue
    return False


def safe_open(path, allowed_roots) -> bytes:
    """Read a regular, single-link, non-symlink file whose real path is under allowed_roots."""
    path = Path(path)
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:  # ELOOP on symlink, ENOENT, ...
        raise TamperError(f"cannot open safely: {path}: {e}") from e
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise TamperError(f"not a regular file: {path}")
        if st.st_nlink != 1:
            raise TamperError(f"hard-linked file: {path}")
        real = Path(os.path.realpath(path))
        # bind the realpath check to the opened inode, so a swap after open is caught
        rst = os.stat(real)
        if (rst.st_dev, rst.st_ino) != (st.st_dev, st.st_ino) or not _under(real, allowed_roots):
            raise TamperError(f"path outside allowed roots or swapped: {path}")
        chunks = []
        while True:
            b = os.read(fd, 1 << 16)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def append_bytes(path, data: bytes) -> None:
    """Append to a file without following a symlink at the final component; fsync before return."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

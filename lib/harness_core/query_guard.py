"""Query guard for outbound queries and URLs (spec §Hooks lanes row, §Business investigations).

Percent-decodes (repeatedly, so double-encoding cannot hide data), then rejects any
segment longer than 64 characters or with Shannon entropy above 4.0 bits/char
(measured on the decoded text, so Persian passes), any run of 4+ digits (any
script), any protected term, and any owner-file token.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from urllib.parse import unquote, unquote_plus, urlsplit

from .errors import Denied

MAX_SEGMENT = 64
MAX_ENTROPY = 4.0
DIGIT_RUN = re.compile(r"\d{4,}")  # \d is Unicode-aware: Persian and Arabic-Indic digits count
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_FOLD = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه"})
_STRIP = re.compile("[‌‍ـً-ٰٟ]")  # ZWNJ, ZWJ, tatweel, harakat
_SEP = re.compile(r"[\s\-_+./,;:|]+")


def entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _decode(s: str, plus: bool = False) -> str:
    f = unquote_plus if plus else unquote
    for _ in range(4):
        d = f(s)
        if d == s:
            break
        s = d
    return s


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFC", s).casefold().translate(_FOLD)
    return _SEP.sub(" ", _STRIP.sub("", s)).strip()


def segments(text: str):
    """Decoded segments: host labels, path segments, query keys/values, fragment; or words."""
    if not _URL.match(text):
        return [w for w in _decode(text).split() if w]
    u = urlsplit(text)
    segs = [lab for lab in (u.hostname or "").split(".") if lab]
    segs += [_decode(p) for p in u.path.split("/") if p]
    for pair in re.split(r"[&;]", u.query):
        segs += [_decode(x, plus=True) for x in pair.split("=", 1) if x]
    if u.fragment:
        segs.append(_decode(u.fragment))
    return segs


def check(text_or_url: str, protected_terms=(), owner_tokens=()) -> None:
    if not isinstance(text_or_url, str):
        raise Denied("query guard: non-text query")
    segs = segments(text_or_url)
    for s in segs:
        if len(s) > MAX_SEGMENT:
            raise Denied(f"query guard: segment longer than {MAX_SEGMENT} chars")
        if entropy(s) > MAX_ENTROPY:
            raise Denied("query guard: high-entropy segment (possible encoded data)")
    whole = " ".join(segs)
    if DIGIT_RUN.search(whole):
        raise Denied("query guard: digit run (possible identifier or amount)")
    norm = normalise(whole)
    for term in protected_terms or ():
        t = normalise(term)
        if t and t in norm:
            raise Denied("query guard: contains a protected term")
    words = set(norm.split())
    for tok in owner_tokens or ():
        if normalise(tok) in words:
            raise Denied("query guard: contains a token from the owner's files")

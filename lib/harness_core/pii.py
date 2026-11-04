"""Personal-data scanner (spec §Business governance "Personal data"; build phase P3).

``scan(text_or_path) -> [tag]`` finds personal data by pattern (in text) and by column name (a CSV-like
header line). It returns tags only, sorted; the matched values never leave this module. ``redact(text)``
replaces each match with ``[tag]`` (used for ``text_norm`` in turns and for logged corrections).

Tags: email, phone, national_id (Iranian code, checksum-checked), iban (IR Sheba / any IBAN, mod-97),
card_number (Luhn), salary and person_name (column names only). Persian and Arabic-Indic digits are
read as ASCII. A file is read with O_NOFOLLOW, regular files only, at most ``MAX_BYTES``; anything
unreadable gives no tags.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

MAX_BYTES = 64 * 1024
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# anchored at a token start and bounded (RFC 5321 local part <= 64): linear time on long runs of letters
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,8}")
# Iranian mobile (09xx xxx xxxx, +98 9xx…), or a generic international number with 10-14 digits
_PHONE = re.compile(r"(?<![\d.])(?:\+98[\s-]?|0098[\s-]?|0)9\d{2}[\s-]?\d{3}[\s-]?\d{4}(?![\d.])"
                    r"|(?<![\w.])\+\d{1,3}(?:[\s-]?\d{2,4}){3,4}(?![\d.])")
_TEN = re.compile(r"(?<![\d.])\d{10}(?![\d.])")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30}\b")
_CARD = re.compile(r"(?<![\d.])(?:\d[\s-]?){15}\d(?![\d.])")

COLUMNS = {
    "email": ("email", "e-mail", "mail", "ایمیل", "پست الکترونیک"),
    "phone": ("phone", "mobile", "tel", "telephone", "cell", "تلفن", "موبایل", "همراه", "شماره تماس"),
    "national_id": ("national id", "national_id", "nid", "ssn", "کد ملی", "کدملی", "شماره ملی"),
    "iban": ("iban", "sheba", "شبا", "شماره شبا"),
    "card_number": ("card number", "card_no", "شماره کارت"),
    "salary": ("salary", "wage", "wages", "payroll", "حقوق", "دستمزد", "حقوق ماهانه"),
    "person_name": ("name", "full name", "customer name", "first name", "last name", "employee",
                    "نام", "نام و نام خانوادگی", "نام مشتری", "نام خانوادگی"),
}


def _national_id_ok(s: str) -> bool:
    if len(set(s)) == 1:
        return False
    check = sum(int(s[i]) * (10 - i) for i in range(9)) % 11
    return int(s[9]) == (check if check < 2 else 11 - check)


def _iban_ok(s: str) -> bool:
    s = s.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1
    except ValueError:
        return False


def _luhn_ok(s: str) -> bool:
    d = [int(c) for c in s if c.isdigit()]
    if len(d) != 16 or len(set(d)) == 1:
        return False
    total = 0
    for i, v in enumerate(reversed(d)):
        if i % 2:
            v *= 2
            v -= 9 if v > 9 else 0
        total += v
    return total % 10 == 0


def _matches(text: str):
    """(tag, start, end) for every pattern match that passes its check."""
    t = text.translate(_DIGITS)
    for m in _EMAIL.finditer(t):
        yield "email", m.start(), m.end()
    for m in _IBAN.finditer(t.upper()):
        if _iban_ok(m.group()):
            yield "iban", m.start(), m.end()
    for m in _CARD.finditer(t):
        if _luhn_ok(m.group()):
            yield "card_number", m.start(), m.end()
    for m in _TEN.finditer(t):
        if _national_id_ok(m.group()):
            yield "national_id", m.start(), m.end()
    for m in _PHONE.finditer(t):
        yield "phone", m.start(), m.end()


def _header_tags(line: str) -> set:
    cells = [c.strip().strip('"').casefold() for c in re.split(r"[,;\t|]", line)]
    out = set()
    for tag, names in COLUMNS.items():
        if any(c == n.casefold() or c.endswith(" " + n.casefold()) for c in cells for n in names):
            out.add(tag)
    return out


def _read(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        return os.read(fd, MAX_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""
    finally:
        os.close(fd)


def scan(source) -> list:
    """Tags found in a text (str) or a file (Path)."""
    if isinstance(source, Path):
        text = _read(source)
        first = text.splitlines()[0] if text else ""
        tags = _header_tags(first)
    else:
        text, tags = str(source), set()
    tags |= {tag for tag, _, _ in _matches(text)}
    return sorted(tags)


def redact(text: str) -> str:
    """``text`` with every pattern match replaced by ``[tag]``; overlapping matches keep the first."""
    spans = sorted(_matches(text), key=lambda x: (x[1], -x[2]))
    out, pos = [], 0
    for tag, a, b in spans:  # offsets match text: the digit map is one character to one character
        if a < pos:
            continue
        out.append(text[pos:a])
        out.append(f"[{tag}]")
        pos = b
    out.append(text[pos:])
    return "".join(out)

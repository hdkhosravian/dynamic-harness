"""Deterministic localisation (spec §User-facing templates and localisation "Localisation and normalisation";
build phase P5). Stdlib only; pack checks import it inside their sandbox.

- ``fold_name``: one key for names written with Arabic or Persian ی/ي and ک/ك, ZWNJ, diacritics, digits.
- Jalali <-> Gregorian (the 33-year arithmetic of jalaali-js, valid for Jalali years -61..3177), leap Esfand.
- ``parse_date``: ISO and Jalali ``YYYY/MM/DD`` (Persian or Latin digits); a 4-digit year below 1700 is Jalali.
- ``parse_amount``: digits in any script, thousands separators, ``(1,000)`` and trailing minus as negatives.
- ``currency_of``: Toman or Rial from a column header or label; ``to_unit`` converts (1 Toman = 10 Rial).
- ``period``: a month, a fiscal year (profile ``fiscal_year_start``, default Farvardin 1) or a date range, as
  Gregorian bounds plus labels in both calendars.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from decimal import Decimal, InvalidOperation

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")
_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ؤ": "و"})
_FORMAT = {"‌", "‍", "‎", "‏", "؜", "﻿"}
BREAKS = (-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456,
          3178)
JALALI_MONTHS = ("Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar", "Mehr", "Aban", "Azar", "Dey",
                 "Bahman", "Esfand")
JALALI_MONTHS_FA = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن",
                    "اسفند")


def digits(text) -> str:
    return unicodedata.normalize("NFC", str(text)).translate(_DIGITS)


def fold_name(text) -> str:
    """Matching key: ی/ي, ک/ك and alef forms unified, ZWNJ and diacritics removed, digits ASCII, casefolded."""
    t = digits(text).translate(_LETTERS)
    t = "".join(" " if ch in _FORMAT else ch for ch in t if unicodedata.category(ch) != "Mn")
    return " ".join(t.split()).casefold()


# --- Jalali calendar (jalaali-js) --------------------------------------------------------------------------------
def _div(a, b):
    return int(a / b)


def _mod(a, b):
    return a - _div(a, b) * b


def _jal_cal(jy):
    if jy < BREAKS[0] or jy >= BREAKS[-1]:
        raise ValueError(f"Jalali year {jy} is out of range")
    gy, leap_j, jp, jump = jy + 621, -14, BREAKS[0], 0
    for jm in BREAKS[1:]:
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    return (4 if leap == -1 else leap), gy, march


def _g2d(gy, gm, gd):
    d = _div((gy + _div(gm - 8, 6) + 100100) * 1461, 4) + _div(153 * _mod(gm + 9, 12) + 2, 5) + gd - 34840408
    return d - _div(_div(gy + 100100 + _div(gm - 8, 6), 100) * 3, 4) + 752


def _d2g(jdn):
    j = 4 * jdn + 139361631
    j = j + _div(_div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908
    i = _div(_mod(j, 1461), 4) * 5 + 308
    gd = _div(_mod(i, 153), 5) + 1
    gm = _mod(_div(i, 153), 12) + 1
    return _div(j, 1461) - 100100 + _div(8 - gm, 6), gm, gd


def is_leap_jalali(jy) -> bool:
    return _jal_cal(jy)[0] == 0


def month_length(jy, jm) -> int:
    if not 1 <= jm <= 12:
        raise ValueError(f"Jalali month {jm} is out of range")
    return 31 if jm <= 6 else 30 if jm <= 11 or is_leap_jalali(jy) else 29


def to_gregorian(jy, jm, jd) -> dt.date:
    if not 1 <= jd <= month_length(jy, jm):
        raise ValueError(f"{jy}/{jm}/{jd} is not a Jalali date")
    _, gy, march = _jal_cal(jy)
    jdn = _g2d(gy, 3, march) + (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1
    return dt.date(*_d2g(jdn))


def to_jalali(d: dt.date) -> tuple:
    jdn = _g2d(d.year, d.month, d.day)
    gy = _d2g(jdn)[0]
    jy = gy - 621
    leap, _, march = _jal_cal(jy)
    k = jdn - _g2d(gy, 3, march)
    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if leap == 1:
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


def jalali_str(d: dt.date) -> str:
    return "%04d/%02d/%02d" % to_jalali(d)


# --- parsing -----------------------------------------------------------------------------------------------------
_DATE = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T\s].*)?$")
_DMY = re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*$")


def parse_date(value):
    """A Gregorian ``date`` or None. A year below 1700 is read as Jalali; D/M/Y only with a 4-digit year."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    s = digits(value)
    m = _DATE.match(s)
    try:
        if m:
            y, mo, d = map(int, m.groups())
            return to_gregorian(y, mo, d) if y < 1700 else dt.date(y, mo, d)
        m = _DMY.match(s)
        if m:
            d, mo, y = map(int, m.groups())
            return to_gregorian(y, mo, d) if y < 1700 else dt.date(y, mo, d)
    except ValueError:
        return None
    return None


def parse_amount(value):
    """``Decimal`` or None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    s = digits(value).strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.endswith("-") and not s.startswith("-"):
        neg, s = True, s[:-1]
    s = re.sub(r"[,٬،\s‌]", "", s)
    s = re.sub(r"(?i)(ریال|تومان|toman|rials?|irr|irt)$", "", s)
    if not re.fullmatch(r"[-+]?\d+(\.\d+)?", s):
        return None
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if neg else v


_RIAL = re.compile(r"(?i)ریال|\brials?\b|\birr\b")
_TOMAN = re.compile(r"(?i)تومان|\btomans?\b|\birt\b")


def currency_of(label):
    """``"IRR"`` (Rial), ``"IRT"`` (Toman) or None, from a header or label."""
    t = fold_name(label or "")
    if _TOMAN.search(t):
        return "IRT"
    if _RIAL.search(t):
        return "IRR"
    return None


def to_unit(amount: Decimal, unit, target):
    """Convert between Rial and Toman; other units pass through only when equal."""
    if unit == target or unit is None or target is None:
        return amount
    if (unit, target) == ("IRR", "IRT"):
        return amount / 10
    if (unit, target) == ("IRT", "IRR"):
        return amount * 10
    raise ValueError(f"cannot convert {unit} to {target}")


def _fy_start(profile) -> tuple:
    s = (profile or {}).get("fiscal_year_start") or "01-01"
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", digits(s))
    return (int(m.group(1)), int(m.group(2))) if m else (1, 1)


def period(spec, profile=None) -> dict:
    """``{start, end (inclusive), label, label_gregorian}`` for ``{"month": "1402-12"}`` (Jalali when the year is
    below 1700), ``{"fiscal_year": 1402}`` or ``{"from": date, "to": date}``."""
    profile = profile or {}
    if not isinstance(spec, dict):
        raise ValueError("period must be an object")
    if "month" in spec:
        y, m = (int(x) for x in digits(spec["month"]).split("-"))
        if y < 1700:
            start, end = to_gregorian(y, m, 1), to_gregorian(y, m, month_length(y, m))
            label = f"{JALALI_MONTHS[m - 1]} {y}"
        else:
            start = dt.date(y, m, 1)
            end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
            label = start.strftime("%B %Y")
    elif "fiscal_year" in spec:
        y = int(digits(spec["fiscal_year"]))
        fm, fd = _fy_start(profile)
        if y < 1700:
            start = to_gregorian(y, fm, fd)
            end = to_gregorian(y + 1, fm, fd) - dt.timedelta(days=1)
            label = f"fiscal year {y}"
        else:
            start = dt.date(y, fm, fd)
            end = dt.date(y + 1, fm, fd) - dt.timedelta(days=1)
            label = f"fiscal year {y}"
    elif "from" in spec and "to" in spec:
        start, end = parse_date(spec["from"]), parse_date(spec["to"])
        if start is None or end is None or end < start:
            raise ValueError("period from/to must be dates with from <= to")
        label = f"{jalali_str(start)} to {jalali_str(end)}"
    else:
        raise ValueError("period needs month, fiscal_year, or from and to")
    return {"start": start, "end": end, "label": label,
            "label_gregorian": f"{start.isoformat()} to {end.isoformat()}",
            "label_jalali": f"{jalali_str(start)} to {jalali_str(end)}"}


def label_fa(label: str) -> str:
    """A period label for Persian text: Jalali month names in Persian, "to" as «تا», "fiscal year" as «سال مالی»."""
    out = str(label or "")
    for en, fa in zip(JALALI_MONTHS, JALALI_MONTHS_FA):
        out = re.sub(rf"\b{en}\b", fa, out)
    out = out.replace(" to ", " تا ").replace("fiscal year", "سال مالی")
    return out.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

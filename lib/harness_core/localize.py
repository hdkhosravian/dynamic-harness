"""Deterministic localisation (spec §User-facing templates and localisation "Localisation and normalisation";
build phase P5). Stdlib only; pack checks import it inside their sandbox.

- ``fold_name``: one key for names written with Arabic or Persian ی/ي and ک/ك, ZWNJ, diacritics, digits.
- Jalali <-> Gregorian (the 33-year arithmetic of jalaali-js, valid for Jalali years -61..3177), leap Esfand.
- ``parse_date``: ISO and Jalali ``YYYY/MM/DD`` (Persian or Latin digits); a 4-digit year below 1700 is Jalali.
- ``parse_amount``: digits in any script, thousands separators, ``(1,000)`` and trailing minus as negatives; at most
  one currency mark (a word of ``currency_words``) beside the number, read against the column's unit.
- ``currency_of``: the currency code a column header or label names (``words.currency_words`` of the locale packs,
  matched by ``word_pattern``); ``to_unit`` converts between two related units (``currency_pairs``).
- ``period``: a month, a fiscal year (profile ``fiscal_year_start``, default Farvardin 1) or a date range, as
  Gregorian bounds plus labels in both calendars.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from decimal import Decimal, InvalidOperation

_DECIMAL = re.compile(r"(?![0-9])\d")  # \d of a str pattern: every character that has a Unicode decimal value
_fold = None  # the letter table of fold_name: the request's (use_fold), else the shipped packs'
_pairs = None  # the currency_pairs of to_unit: the request's (use_pairs), else the shipped packs'
_currency_words = None  # the words of currency_of: the request's (use_currency_words), else the shipped packs'
_codes_last = [None, None, None]  # the last result of _codes: (word table, letter table, result)
BREAKS = (-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456,
          3178)
JALALI_MONTHS = ("Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar", "Mehr", "Aban", "Azar", "Dey",
                 "Bahman", "Esfand")
JALALI_MONTHS_FA = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن",
                    "اسفند")


def decimals(text: str) -> str:
    """Every character that has a Unicode decimal value as that ASCII digit, for every script."""
    return text if text.isascii() else _DECIMAL.sub(lambda m: str(unicodedata.decimal(m.group())), text)


def digits(text) -> str:
    return decimals(unicodedata.normalize("NFC", str(text))).replace("\u066b", ".")  # the Arabic decimal separator


def use_fold(fold: dict) -> None:
    """The ``locale.fold`` of a check request: from now on ``fold_name`` in this process folds letters with it."""
    global _fold
    _fold = str.maketrans(fold.get("letters", {}))


def _uncover(t: str) -> str:
    """The step of ``fold_name`` that can uncover a character: a format character (``Cf``) a space, a mark (``Mn``)
    removed. ``locales.check_currency_words`` checks a word in this form, the form the table holds."""
    return "".join(" " if unicodedata.category(ch) == "Cf" else ch for ch in t if unicodedata.category(ch) != "Mn")

def fold_name(text) -> str:
    """Matching key: letters unified by the ``fold.letters`` of the locale packs, format characters a space,
    diacritics removed, digits ASCII, casefolded."""
    global _fold
    if _fold is None:  # not in a check that got a fold: the shipped packs, read once per process
        from . import locales
        _fold = str.maketrans(locales.view(None, every=True).get("fold", {}).get("letters", {}))
    t = digits(text)
    if not t.isascii():
        t = _uncover(t.translate(_fold))
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


_NUM = r"[0-9.,٬،+\-()\s‌]"  # after digits(): every character the number grammar reads
_PLAIN = re.compile(_NUM + "*")
_RUN = re.compile(_NUM + "*")  # a run of number characters at one end of a cell: matched forward, linear
_WS = re.compile(r"[\s‌]")
_SIGN = re.compile(r"[-+]")

def _ends(s):
    """The runs of number characters at the start and at the end of ``s``, linear. The number takes no dot that no
    digit follows: a dot next to the rest is the rest's (``zb.``). ``locales.check_currency_words`` uses the same."""
    return _RUN.match(s).group().rstrip("."), _RUN.match(s[::-1]).group()[::-1].lstrip(".")

def _forms(t):
    """(negative forms, rest): parentheses around ``t``, then a leading sign (a ``+`` is no form), then a trailing
    minus."""
    n, t = 0, t.strip()
    if t.startswith("(") and t.endswith(")"):
        n, t = 1, t[1:-1].strip()
    if t[:1] in ("-", "+"):
        n, t = n + (t[0] == "-"), t[1:].strip()
    if t.endswith("-"):
        n, t = n + 1, t[:-1]
    return n, t

def parse_amount(value, unit=None, words=None, pairs=None):
    """``Decimal`` in ``unit`` or None. A cell is a number and at most one currency mark before or after it: a mark
    equal (folded, whole) to a word of ``words`` (else the table of the process) gives the number when ``unit`` is
    None or one of the word's codes, the number converted by ``pairs`` (``to_unit``) when the word names one code
    related to ``unit``, else None. A cell with a mark holds at most one negative form (around the cell, around the
    number, a minus before or after either); two or more are None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    s = digits(value)
    if _PLAIN.fullmatch(s):  # no mark: no word table is read
        return _number(s)
    n, t = _forms(s)
    a, b = _ends(t)
    mark, a, b = t[len(a):len(t) - len(b)], _WS.sub("", a), _WS.sub("", b)
    if bool(a) == bool(b):  # no number, or a number on both sides of the mark
        return None
    if len(_SIGN.findall(s)) - len(_SIGN.findall(mark)) > 1:  # at most one sign character outside the mark
        return None
    m, rest = _forms(a or b)
    v = _number(rest) if n + m < 2 and not re.search(r"[-+()]", rest) else None
    cs = _codes(words).get(fold_name(mark)) if v is not None else None
    if not cs:
        return None
    v = -v if n + m else v
    if unit is None or unit in cs:
        return v
    c = next(iter(cs))
    return to_unit(v, c, unit, pairs) if len(cs) == 1 and pair_of(c, unit, pairs) else None

def _number(s):
    """The number grammar: digits, group marks, a decimal point, ``(1,000)``, a trailing or leading minus."""
    s = s.strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.endswith("-") and not s.startswith("-"):
        neg, s = True, s[:-1]
    s = _WS.sub("", s)
    if re.search(r"[,٬،]", s) and not re.fullmatch(r"[-+]?\d{1,3}([,٬،]\d{3})+(\.\d+)?", s):
        return None  # "1,5" is a decimal comma, not 15: unreadable is reported, a wrong number is not
    s = re.sub(r"[,٬،]", "", s)
    if not re.fullmatch(r"[-+]?\d+(\.\d+)?", s):
        return None
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if neg else v


def word_pattern(word: str) -> str:
    """Regex source for one normalised currency word: it matches where no letter touches it (a digit or ``_`` may:
    ``3000<word>``, ``amount_<word>``); a word that starts with a sign matches anywhere."""
    w = re.escape(word)
    return rf"(?<![^\W\d_]){w}(?![^\W\d_])" if re.match(r"\w", word) else w


def use_currency_words(table: dict) -> None:
    """The ``locale.currency_words`` of a check request: from now on ``currency_of`` in this process matches with it."""
    global _currency_words
    _currency_words = table


def _codes(words) -> dict:
    """Folded word -> the codes that list it, from ``words`` (else the table of the process)."""
    global _currency_words
    if words is None:
        if _currency_words is None:  # not in a check that got words: the shipped packs, read once per process
            from . import locales
            _currency_words = locales.view(None, every=True).get("words", {}).get("currency_words", {})
        words = _currency_words
    if _codes_last[0] is words and _codes_last[1] is _fold:  # the same two table objects: the same result
        return _codes_last[2]
    out = {}
    for code, ws in words.items():
        for w in {fold_name(x) for x in ws} - {""}:  # a word that folds to nothing would match every label
            out.setdefault(w, set()).add(code)
    _codes_last[:] = words, _fold, out  # the objects are held, so no id is reused; _fold as fold_name read it
    return out


def currency_of(label, words=None):
    """The currency code ``label`` (a header or label) names, or None. A word listed under two codes names none; of
    the other words the earliest match wins, at the same position the longest."""
    t = fold_name(label or "")
    hits = [(m.start(), -len(w), c) for w, cs in _codes(words).items() if len(cs) == 1
            for c in cs for m in [re.search(word_pattern(w), t)] if m]
    return min(hits)[2] if hits else None


def names_currency(text, words=None) -> bool:
    """Whether ``text`` holds a word of a currency, also one listed under two codes."""
    t = fold_name(text or "")
    return any(re.search(word_pattern(w), t) for w in _codes(words))


def use_pairs(pairs: list) -> None:
    """The ``locale.currency_pairs`` of a check request: from now on ``to_unit`` in this process converts with it."""
    global _pairs
    _pairs = pairs


def pair_of(unit, target=None, pairs=None):
    """The relation of two units: the first entry of ``pairs`` (else the table of the process) that holds ``unit`` and
    ``target`` in either direction, or with ``target`` None the first that holds ``unit``; None when there is none."""
    global _pairs
    if pairs is None:
        if _pairs is None:  # not in a check that got pairs: the shipped packs, read once per process
            from . import locales
            _pairs = locales.view(None, every=True).get("currency_pairs", [])
        pairs = _pairs
    for e in pairs:
        if unit in (e["from"], e["to"]) and (target is None or target in (e["from"], e["to"])):
            return e
    return None


def to_unit(amount: Decimal, unit, target, pairs=None):
    """Convert between two related units (1 ``from`` = ``factor`` ``to``, see ``pair_of``); other units pass through
    only when equal."""
    if unit == target or unit is None or target is None:
        return amount
    e = pair_of(unit, target, pairs)
    if e is None:
        raise ValueError(f"cannot convert {unit} to {target}")
    return amount * e["factor"] if e["from"] == unit else amount / e["factor"]


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
        cal = "gregorian" if (profile or {}).get("calendar") == "gregorian" else "jalali"
        if (profile or {}).get("fiscal_year_start") and (y < 1700) != (cal == "jalali"):
            # the start day is a date in the owner's calendar: it cannot be read in the other one
            raise ValueError(f"your fiscal year starts on {profile['fiscal_year_start']} in the {cal} calendar, so "
                             f"number the fiscal year in that calendar too (not {y})")
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


def prior_period(spec, profile=None) -> dict:
    """The period before ``spec``: the previous month, the previous fiscal year, or an equal-length window."""
    if "month" in spec:
        y, m = (int(x) for x in digits(spec["month"]).split("-"))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        return period({"month": f"{y}-{m:02d}"}, profile)
    if "fiscal_year" in spec:
        return period({"fiscal_year": int(digits(spec["fiscal_year"])) - 1}, profile)
    cur = period(spec, profile)
    span = cur["end"] - cur["start"]
    end = cur["start"] - dt.timedelta(days=1)
    return period({"from": (end - span).isoformat(), "to": end.isoformat()}, profile)

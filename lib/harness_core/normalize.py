"""Money/quantity tokeniser shared with the gate (vendored copy of loop-contract's lc_normalize.py).

Stdlib only. The Stop hook uses it so a final message and fold_ledger's --numbers-trace agree on what a
"number" is; tests/test_stopgate.py checks the two copies match.

    normalize(text)        Persian/Arabic-Indic digits -> ASCII, ٫ -> '.', NFC
    quantity_tokens(text)  [(canonical_str, Decimal)]: >= 2 digits or a decimal point; dates, times, IDs and
                           version strings excluded; percentages kept
    to_decimal(value)      Decimal for a JSON number or numeric string, else None
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")
_THOUSANDS = ",٬،"  # ASCII comma, U+066C Arabic thousands separator, U+060C Arabic comma

# Date/time shapes are blanked before tokenising (Gregorian and Jalali 13xx/14xx alike).
_DATEISH = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"     # YYYY-MM-DD, YYYY/MM/DD, 1403/08/14
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{4}"    # DD/MM/YYYY
    r"|\b1[34]\d\d/\d{1,2}\b"          # Jalali year/month
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"   # times
)
_NUM = re.compile(r"(?<![A-Za-z\d.])(\d{1,3}(?:[" + _THOUSANDS + r"]\d{3})+|\d+)(\.\d+)?(?!\.?\d)")
_ID_PREFIX = re.compile(r"(#|(?<![A-Za-z])id[:\s]*|[A-Za-z]-)$", re.IGNORECASE)


def normalize(text):
    return unicodedata.normalize("NFC", str(text)).translate(_DIGITS)


def quantity_tokens(text):
    t = _DATEISH.sub(lambda m: " " * len(m.group()), normalize(text))
    out = []
    for m in _NUM.finditer(t):
        if _ID_PREFIX.search(t[:m.start()]):
            continue
        whole = re.sub("[" + _THOUSANDS + "]", "", m.group(1))
        frac = m.group(2) or ""
        if len(whole) < 2 and not frac:
            continue
        out.append((whole + frac, Decimal(whole + frac)))
    return out


def to_decimal(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, str):
        s = re.sub("[" + _THOUSANDS + r"\s]", "", normalize(value)).rstrip("%")
        try:
            return Decimal(s) if re.fullmatch(r"-?\d+(\.\d+)?", s) else None
        except InvalidOperation:
            return None
    return None

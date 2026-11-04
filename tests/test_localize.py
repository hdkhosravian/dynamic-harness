"""Localisation: digits, letter variants, Jalali calendar, amounts, currency, periods (build phase P5)."""
from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

import pytest

from harness_core import localize as L
from harness_core import tables


@pytest.mark.parametrize("j, g", [((1403, 1, 1), (2024, 3, 20)), ((1402, 12, 29), (2024, 3, 19)),
                                  ((1403, 12, 30), (2025, 3, 20)), ((1399, 12, 30), (2021, 3, 20)),
                                  ((1405, 8, 13), (2026, 11, 4)), ((1300, 1, 1), (1921, 3, 21))])
def test_known_jalali_dates(j, g):
    assert L.to_gregorian(*j) == dt.date(*g) and L.to_jalali(dt.date(*g)) == j


def test_leap_esfand():
    assert L.is_leap_jalali(1403) and L.is_leap_jalali(1399) and not L.is_leap_jalali(1402)
    assert L.month_length(1403, 12) == 30 and L.month_length(1402, 12) == 29
    with pytest.raises(ValueError):
        L.to_gregorian(1402, 12, 30)
    assert L.parse_date("1402/12/30") is None and L.parse_date("1403/12/30") == dt.date(2025, 3, 20)


def test_round_trip_over_a_century():
    rnd = random.Random(7)
    for _ in range(5000):
        d = dt.date(1950, 1, 1) + dt.timedelta(days=rnd.randint(0, 40000))
        assert L.to_gregorian(*L.to_jalali(d)) == d


def test_persian_digits_and_letter_variants():
    assert L.parse_date("۱۴۰۲/۱۲/۲۹") == dt.date(2024, 3, 19)
    assert L.fold_name("علي‌رضا كريمي") == L.fold_name("علی رضا کریمی")
    assert L.fold_name("Karimi ") == L.fold_name("karimi")


@pytest.mark.parametrize("raw, want", [("1,250,000", 1250000), ("۱٬۲۵۰٬۰۰۰", 1250000), ("(1,000)", -1000),
                                       ("1,000-", -1000), ("12.5", Decimal("12.5")), ("50,000 ریال", 50000),
                                       ("abc", None), ("", None), (None, None), (True, None)])
def test_amounts(raw, want):
    assert L.parse_amount(raw) == (Decimal(want) if want is not None else None)


def test_currency_from_headers_and_conversion():
    assert L.currency_of("مبلغ (ريال)") == "IRR" and L.currency_of("Amount (Toman)") == "IRT"
    assert L.currency_of("Amount") is None
    assert L.to_unit(Decimal(500000), "IRR", "IRT") == 50000 and L.to_unit(Decimal(5), "IRT", "IRR") == 50


def test_periods_in_both_calendars():
    p = L.period({"month": "1402-12"})
    assert (p["start"], p["end"]) == (dt.date(2024, 2, 20), dt.date(2024, 3, 19)) and p["label"] == "Esfand 1402"
    leap = L.period({"month": "1403-12"})
    assert leap["end"] == dt.date(2025, 3, 20)
    fy = L.period({"fiscal_year": 1403}, {"fiscal_year_start": "04-01"})
    assert fy["start"] == L.to_gregorian(1403, 4, 1) and fy["end"] == L.to_gregorian(1404, 4, 1) - dt.timedelta(days=1)
    with pytest.raises(ValueError):
        L.period({"from": "2024-03-01", "to": "2024-02-01"})


def test_xlsx_reader_refuses_dtds(tmp_path):
    import zipfile
    f = tmp_path / "x.xlsx"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><worksheet/>')
    with pytest.raises(ValueError, match="DTD"):
        tables.read(f)


def test_xlsx_reader_reads_strings_numbers_and_dates(tmp_path):
    import zipfile
    f = tmp_path / "x.xlsx"
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}><si><t>date</t></si><si><t>amount</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row r="2"><c r="A2"><v>45371</v></c><c r="B2"><v>1500</v></c></row>'
                   '</sheetData></worksheet>')
    headers, rows = tables.read(f)
    assert headers == ["date", "amount"] and rows == [[45371, 1500]]
    assert tables.excel_date(45371) == dt.date(2024, 3, 20)

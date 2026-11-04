"""Read an owner's table (CSV/TSV, or XLSX with the standard library only) for pack checks (build phase P5).

``read(path) -> (headers, rows)``: rows are lists of cell values (str, int, float or None) aligned with the
headers. XLSX: the first sheet; shared and inline strings, numbers and booleans; formulas are read as their
cached values (a cell whose formula has no cached value is None). Date cells come back as Excel serial numbers;
``excel_date`` converts one.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
MAX_ROWS = 200_000
_REF = re.compile(r"([A-Z]+)(\d+)")


def excel_date(serial):
    """Excel 1900-system serial (int/float) -> date, or None."""
    if isinstance(serial, bool) or not isinstance(serial, (int, float)) or not 1 <= serial < 2958466:
        return None
    return dt.date(1899, 12, 30) + dt.timedelta(days=int(serial))


def _col(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


MAX_MEMBER = 200 * 1024 * 1024


def _xml(z, name):
    """A workbook part, parsed without DTDs: defusedxml is not in the standard library, so any DOCTYPE (the
    only way to declare entities, external or nested) is refused, and members are size-capped (zip bombs)."""
    if z.getinfo(name).file_size > MAX_MEMBER:
        raise ValueError(f"{name} is larger than {MAX_MEMBER // (1024 * 1024)} MB uncompressed")
    raw = z.read(name)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError(f"{name} declares a DTD; refused")
    return ET.fromstring(raw)


def _xlsx(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in _xml(z, "xl/sharedStrings.xml").findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheet = "xl/worksheets/sheet1.xml"
        if sheet not in names:
            cands = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            if not cands:
                raise ValueError("the workbook has no worksheet")
            sheet = cands[0]
        root = _xml(z, sheet)
    rows = []
    for r in root.iter(f"{{{NS['m']}}}row"):
        cells = {}
        for c in r.findall("m:c", NS):
            m = _REF.match(c.get("r", ""))
            idx = _col(m.group(1)) if m else len(cells)
            t, v = c.get("t"), c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            elif t == "b" and v is not None:
                val = v.text == "1"
            elif t == "str" and v is not None:
                val = v.text
            elif v is not None and v.text is not None:
                f = float(v.text)
                val = int(f) if f.is_integer() and "." not in v.text and "E" not in v.text.upper() else f
            else:
                val = None
            cells[idx] = val
        if cells:
            rows.append([cells.get(i) for i in range(max(cells) + 1)])
        if len(rows) > MAX_ROWS:
            raise ValueError(f"more than {MAX_ROWS} rows")
    return rows


def read(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] == b"PK":
        rows = _xlsx(data)
    else:
        text = data.decode("utf-8-sig", errors="replace")
        first = text.splitlines()[0] if text else ""
        dialect = "excel-tab" if "\t" in first and "," not in first else "excel"
        rows = list(csv.reader(io.StringIO(text), dialect))
        if len(rows) > MAX_ROWS:
            raise ValueError(f"more than {MAX_ROWS} rows")
    rows = [r for r in rows if any(x not in (None, "") for x in r)]
    if not rows:
        return [], []
    headers = [str(h if h is not None else "").strip() for h in rows[0]]
    body = [list(r) + [None] * (len(headers) - len(r)) for r in rows[1:]]
    return headers, body

"""A small XLSX writer, standard library only (build phase P5): the rows behind an answer and the forecast
workbook. ``tables.read`` reads what it writes.

``write(path, sheets)`` where each sheet is ``{"name": str, "rows": [[cell, ...], ...], "protect": bool,
"widths": [int, ...]}`` and a cell is ``None``, a str, an int or float, a bool, or ``{"f": formula, "v": cached,
"style": "input" | "header" | "check"}``. Strings are inline strings; formulas keep their cached value and the
workbook asks Excel to recalculate on open. ``input`` cells are coloured and stay editable on a protected
sheet; every other cell of a protected sheet is locked.
"""
from __future__ import annotations

import zipfile
from xml.sax.saxutils import escape

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
STYLES = {None: 0, "header": 1, "input": 2, "check": 3}
_STYLES_XML = (
    f'<styleSheet xmlns="{MAIN}">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE2EFDA"/><bgColor indexed="64"/></patternFill></fill>'
    '</fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="2" borderId="0" xfId="0" applyFill="1" applyProtection="1">'
    '<protection locked="0"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')


def col(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def ref(row: int, column: int) -> str:
    """Zero-based (row, column) -> A1."""
    return f"{col(column)}{row + 1}"


def _cell(r, c, v) -> str:
    style = STYLES.get(v.get("style")) if isinstance(v, dict) else 0
    s = f' s="{style}"' if style else ""
    at = ref(r, c)
    if isinstance(v, dict) and "f" in v:
        cached = v.get("v")
        if isinstance(cached, bool):
            return f'<c r="{at}" t="b"{s}><f>{escape(v["f"])}</f><v>{int(cached)}</v></c>'
        if isinstance(cached, (int, float)):
            return f'<c r="{at}"{s}><f>{escape(v["f"])}</f><v>{cached}</v></c>'
        return f'<c r="{at}"{s}><f>{escape(v["f"])}</f></c>'
    if isinstance(v, dict):
        v = v.get("v")
    if v is None:
        return f'<c r="{at}"{s}/>' if s else ""
    if isinstance(v, bool):
        return f'<c r="{at}" t="b"{s}><v>{int(v)}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{at}"{s}><v>{v}</v></c>'
    text = escape(str(v)).replace("\n", "&#10;")
    return f'<c r="{at}" t="inlineStr"{s}><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet(sheet) -> str:
    rows = []
    for r, row in enumerate(sheet.get("rows") or []):
        cells = "".join(_cell(r, c, v) for c, v in enumerate(row))
        rows.append(f'<row r="{r + 1}">{cells}</row>')
    widths = sheet.get("widths") or []
    cols = ("<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                               for i, w in enumerate(widths)) + "</cols>") if widths else ""
    protect = '<sheetProtection sheet="1" objects="1" scenarios="1"/>' if sheet.get("protect") else ""
    return f'<worksheet xmlns="{MAIN}">{cols}<sheetData>{"".join(rows)}</sheetData>{protect}</worksheet>'


def build(sheets) -> dict:
    """``{part name: bytes}`` of the workbook."""
    names = [s["name"][:31] for s in sheets]
    if len(set(names)) != len(names):
        raise ValueError("sheet names must be unique")
    over = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
                   f'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1))
    parts = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/'
            f'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{over}</Types>'),
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        "xl/workbook.xml": (
            f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>'
            + "".join(f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1))
            + '</sheets><calcPr fullCalcOnLoad="1"/></workbook>'),
        "xl/_rels/workbook.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                      for i in range(1, len(sheets) + 1))
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="{REL}/styles" Target="styles.xml"/></Relationships>'),
        "xl/styles.xml": _STYLES_XML,
    }
    for i, s in enumerate(sheets, 1):
        parts[f"xl/worksheets/sheet{i}.xml"] = _sheet(s)
    return {k: ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + v).encode("utf-8")
            for k, v in parts.items()}


def to_bytes(sheets) -> bytes:
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in build(sheets).items():  # [Content_Types].xml first, as Office expects
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # fixed: same model, same bytes
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


def sheet_xml(data: bytes, index: int = 1) -> bytes:
    """Raw XML of worksheet ``index`` (for the workbook lint, which reads formulas, not values)."""
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read(f"xl/worksheets/sheet{index}.xml")

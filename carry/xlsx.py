"""Minimal .xlsx reader (standard library only): one sheet as rows of cell values.

Handles shared strings, inline strings and numbers, which is all the yield workbooks use.
Cells come back as str, float or None, placed by their column letter so gaps stay aligned.
Dates stay as Excel serial numbers; see `excel_date`.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, timedelta
from typing import Optional, Union
from xml.etree import ElementTree

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
Cell = Optional[Union[str, float]]


def _column_index(ref: str) -> int:
    index = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        index = index * 26 + ord(ch) - 64
    return index - 1


def _text(node) -> str:
    return "".join(t.text or "" for t in node.iter(f"{{{NS['m']}}}t"))


def sheet_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        root = ElementTree.fromstring(book.read("xl/workbook.xml"))
    return [s.get("name") for s in root.iterfind("m:sheets/m:sheet", NS)]


def read_sheet(data: bytes, name: str) -> list[list[Cell]]:
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        workbook = ElementTree.fromstring(book.read("xl/workbook.xml"))
        rel_id = next(s.get(REL_NS) for s in workbook.iterfind("m:sheets/m:sheet", NS) if s.get("name") == name)
        rels = ElementTree.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == rel_id)
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            strings = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in strings.iterfind("m:si", NS)]
        sheet = ElementTree.fromstring(book.read(path))
    rows = []
    for row in sheet.iterfind("m:sheetData/m:row", NS):
        cells: list[Cell] = []
        for c in row.iterfind("m:c", NS):
            col = _column_index(c.get("r"))
            kind = c.get("t")
            v = c.find("m:v", NS)
            if kind == "inlineStr":
                value: Cell = _text(c)
            elif v is None or v.text is None:
                value = None
            elif kind == "s":
                value = shared[int(v.text)]
            elif kind in ("str", "e", "b"):
                value = v.text
            else:
                value = float(v.text)
            cells.extend([None] * (col - len(cells)))
            cells.append(value)
        rows.append(cells)
    return rows


def excel_date(serial: float) -> date:
    """Excel's 1900 date system (serial 1 = 1900-01-01, with its 1900 leap-year bug)."""
    return date(1899, 12, 30) + timedelta(days=int(serial))

"""Tests for carry.yields, carry.xlsx and scripts/fetch_yields.py, on synthetic files (no network)."""

import io
import zipfile
from datetime import date, datetime, timezone

from carry.macro import Observation, Series
from carry.xlsx import excel_date, read_sheet, sheet_names
from carry.yields import (
    load_currency,
    parse_boe_sheet,
    parse_boe_zip,
    parse_dated_csv,
    parse_mof,
    parse_rbnz_sheet,
    parse_snb,
    spread_at,
    to_series,
)
from scripts import fetch_yields


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    """A minimal workbook: strings go to sharedStrings, numbers stay numeric."""
    strings: list[str] = []

    def cell(ref, value):
        if value is None:
            return ""
        if isinstance(value, str):
            if value not in strings:
                strings.append(value)
            return f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>'
        return f'<c r="{ref}"><v>{value}</v></c>'

    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        entries, rels = [], []
        for i, (name, rows) in enumerate(sheets.items(), start=1):
            body = "".join(
                f'<row r="{r}">' + "".join(cell(f"{chr(65 + c)}{r}", v) for c, v in enumerate(row)) + "</row>"
                for r, row in enumerate(rows, start=1)
            )
            z.writestr(f"xl/worksheets/sheet{i}.xml", f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>")
            entries.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
            rels.append(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>')
        z.writestr("xl/workbook.xml", f"<workbook {ns} {rns}><sheets>{''.join(entries)}</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", f"<Relationships>{''.join(rels)}</Relationships>")
        sst = "".join(f"<si><t>{s}</t></si>" for s in strings)
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}>{sst}</sst>")
    return buf.getvalue()


def test_xlsx_reader_keeps_columns_aligned():
    data = make_xlsx({"info": [["x"]], "4. spot curve": [["years:", 0.5, 2.0], [45000, None, 3.5]]})
    assert sheet_names(data) == ["info", "4. spot curve"]
    assert read_sheet(data, "4. spot curve") == [["years:", 0.5, 2.0], [45000.0, None, 3.5]]
    assert excel_date(45000) == date(2023, 3, 15)


def test_dated_csv_sources():
    fred = "observation_date,DGS2\n2026-11-06,4.5\n2026-11-09,.\n"
    bundesbank = '﻿"",BBSIS.X,BBSIS.X_FLAGS\n"",Term structure,\nunit,Prozent,\n2026-11-09,3.00,\n2026-11-10,.,No value\n'
    valet = '"TERMS AND CONDITIONS"\n"OBSERVATIONS"\n"date","BD.CDN.2YR.DQ.YLD"\n"2026-11-09","3.13"\n'
    dbnomics = "period,AU 2 year\n2013-05-20,NA\n2013-09-02,2.9\n"
    assert parse_dated_csv(fred) == {date(2026, 11, 6): 4.5}
    assert parse_dated_csv(bundesbank) == {date(2026, 11, 9): 3.0}
    assert parse_dated_csv(valet) == {date(2026, 11, 9): 3.13}
    assert parse_dated_csv(dbnomics) == {date(2013, 9, 2): 2.9}
    snb = '﻿"CubeId";"rendeiduebd"\n\n"Date";"D0";"D1";"Value"\n"2009-01-01";"CHF";"2J";\n"2026-10-30";"CHF";"2J";"0.238"\n'
    assert parse_snb(snb) == {date(2026, 10, 30): 0.238}


def test_mof_finds_2y_column_and_skips_noise():
    text = (
        "Interest Rate,,,,(Unit : %)\nDate,1Y,2Y,3Y\n1974/9/24,10.3,9.362,8.83\n"
        "2026/11/2,1.671,1.88,1.982\n2026/11/3,-,-,-\n\"  ��If you cannot download\"\n"
    )
    assert parse_mof(text) == {date(1974, 9, 24): 9.362, date(2026, 11, 2): 1.88}


def test_boe_zip_reads_2y_and_skips_old_workbooks():
    sheet = [[None, "UK nominal spot curve"], ["years:", 0.5, 1.0, 1.5, 2.0], [None], [46000, 4.0, 4.1, 4.2, 4.3], [46001, None, None, None, None]]
    assert parse_boe_sheet(sheet) == {excel_date(46000): 4.3}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("GLC Nominal daily data_2025 to present.xlsx", make_xlsx({"4. spot curve": sheet}))
        z.writestr("GLC Nominal daily data_1979 to 1984.xlsx", b"not a workbook")  # skipped, never opened
    assert parse_boe_zip(buf.getvalue()) == {excel_date(46000): 4.3}


def test_rbnz_sheet_by_series_id():
    rows = [["Series Id", "INM.DG101.NZ.C", "INM.DG102.NZ.C"], [46000, 3.1, 3.4], [46001, 3.2, None]]
    assert parse_rbnz_sheet(rows) == {excel_date(46000): 3.4}


def test_jpy_month_file_overrides_history(tmp_path):
    (tmp_path / "JPY_all.csv").write_text("Date,1Y,2Y\n2026/10/30,1.6,1.875\n2026/11/2,1.6,1.0\n")
    (tmp_path / "JPY_month.csv").write_text("Date,1Y,2Y\n2026/11/2,1.671,1.88\n")
    assert load_currency("JPY", tmp_path) == {date(2026, 10, 30): 1.875, date(2026, 11, 2): 1.88}
    assert load_currency("NZD", tmp_path) == {}  # manual file not there


def test_spread_known_next_day_and_rejects_stale():
    us = to_series({date(2026, 11, 6): 4.5, date(2026, 11, 9): 4.56})
    jp = to_series({date(2026, 11, 6): 1.8})
    assert us.observations[0].known_from == utc(2026, 11, 7)
    assert spread_at(us, jp, utc(2026, 11, 6, 23)) is None  # nothing known yet
    assert spread_at(us, jp, utc(2026, 11, 10)) == 4.56 - 1.8
    assert spread_at(us, jp, utc(2026, 11, 13)) is None  # JPY last known 6 days earlier
    empty = Series([])
    assert spread_at(us, empty, utc(2026, 11, 10)) is None
    assert isinstance(us.observations[0], Observation)


def test_fetch_yields_downloads_and_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch_yields, "SOURCES", {"CAD": [("CAD.csv", "u1")], "AUD": [("AUD.csv", "u2")]})
    monkeypatch.setattr(fetch_yields, "CURRENCIES", ("CAD", "AUD", "NZD"))

    def fake_fetch(url, out):
        if url == "u2":
            raise OSError("offline")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('"OBSERVATIONS"\n"date","v"\n"2026-11-06","3.12"\n"2026-11-09","3.13"\n')
        return 10

    try:
        fetch_yields.main(["--out-dir", str(tmp_path)], fetch=fake_fetch)
    except SystemExit as exc:
        assert "AUD.csv" in str(exc)
    out = capsys.readouterr().out
    assert "CAD  2026-11-06 .. 2026-11-09" in out and "AUD  no data" in out and "hb2-daily-close.xlsx" in out

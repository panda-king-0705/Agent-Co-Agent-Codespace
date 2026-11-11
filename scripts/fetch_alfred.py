"""Download ALFRED vintages into data/macro/alfred/ and report how much release history each has.

Usage:
    python -m scripts.fetch_alfred [SERIES_ID ...] [--out-dir data/macro/alfred] [--report-only]

With no series ids it fetches CANDIDATES, the series being considered for the fundamental
score (6 categories x 8 economies). Needs FRED_API_KEY in the environment or .env.

For each series it writes <ID>.csv (every revision row) and <ID>.json (FRED's metadata),
then prints: frequency, last period, first vintage, number of real releases (backfill
excluded), first and last release, and the median days from period start to release.
--report-only skips the download and reports on files already present.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

from carry.alfred import AlfredError, Vintages, api_key, fetch_series_info, fetch_vintage_rows, load_rows, write_rows

# Candidates per economy: inflation, employment, GDP, trade, business, sentiment.
# Many are OECD copies whose ALFRED history starts around 2014; the report shows which hold up.
CANDIDATES = {
    "USD": ["CPIAUCSL", "PAYEMS", "UNRATE", "GDPC1", "BOPGSTB", "INDPRO", "UMCSENT"],
    "EUR": ["CP0000EZ19M086NEST", "LRHUTTTTEZM156S", "CLVMNACSCAB1GQEA19", "XTNTVA01EZM664S", "PRINTO01EZM661S", "CSCICP03EZM665S"],
    "GBP": ["GBRCPIALLMINMEI", "LRHUTTTTGBM156S", "NGDPRSAXDCGBQ", "XTNTVA01GBM664S", "GBRPROINDMISMEI", "CSCICP03GBM665S"],
    "JPY": ["JPNCPIALLMINMEI", "LRHUTTTTJPM156S", "JPNRGDPEXP", "XTNTVA01JPM664S", "JPNPROINDMISMEI", "CSCICP03JPM665S"],
    "CHF": ["CHECPIALLMINMEI", "LRHUTTTTCHQ156S", "CLVMNACSCAB1GQCH", "XTNTVA01CHM664S", "PRINTO01CHQ661S", "CSCICP03CHM665S"],
    "CAD": ["CANCPIALLMINMEI", "LRHUTTTTCAM156S", "NAEXKP01CAQ189S", "XTNTVA01CAM664S", "CANPROINDMISMEI", "CSCICP03CAM665S"],
    "AUD": ["AUSCPIALLQINMEI", "LRHUTTTTAUM156S", "NAEXKP01AUQ189S", "XTNTVA01AUM664S", "PRINTO01AUQ661S", "CSCICP03AUM665S"],
    "NZD": ["NZLCPIALLQINMEI", "LRHUTTTTNZQ156S", "NAEXKP01NZQ189S", "XTNTVA01NZM664S", "PRINTO01NZQ661S", "CSCICP03NZM665S"],
}


def report_line(series_id: str, out_dir: Path) -> str:
    csv_path, info_path = out_dir / f"{series_id}.csv", out_dir / f"{series_id}.json"
    if not csv_path.exists():
        return f"{series_id:<20} missing"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    revisions = load_rows(csv_path)
    vintages = Vintages(revisions)
    releases = vintages.releases("level")
    first_vintage = min((r.start for r in revisions), default=None)
    lags = [(r.released - r.period).days for r in releases]
    span = f"{releases[0].released} .. {releases[-1].released}" if releases else "-"
    return (
        f"{series_id:<20} {info.get('frequency_short', '?'):<2} last {info.get('observation_end', '?'):<10} "
        f"vintages from {first_vintage}  releases {len(releases):>4}  {span:<24} "
        f"lag {median(lags) if lags else '-':>5}d  {info.get('title', '')[:60]}"
    )


def main(argv: list[str] | None = None, fetch_rows=fetch_vintage_rows, fetch_info=fetch_series_info) -> None:
    parser = argparse.ArgumentParser(description="Download ALFRED vintages and report release coverage.")
    parser.add_argument("series", nargs="*")
    parser.add_argument("--out-dir", default="data/macro/alfred")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    ids = args.series or [s for group in CANDIDATES.values() for s in group]

    failed = []
    if not args.report_only:
        key = api_key()
        for series_id in ids:
            try:
                info = fetch_info(series_id, key)
                rows = fetch_rows(series_id, key)
            except AlfredError as exc:
                failed.append(series_id)
                print(f"{series_id}: FAILED ({exc})", flush=True)
                continue
            write_rows(rows, out_dir / f"{series_id}.csv")
            (out_dir / f"{series_id}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
            print(f"{series_id}: {len(rows):,} revision rows", flush=True)

    for series_id in ids:
        print(report_line(series_id, out_dir))
    if failed:
        print(f"{len(failed)} series failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()

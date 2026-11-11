"""Download the free macro inputs of the carry strategy into data/macro/ (git ignores data/).

Usage:
    python -m scripts.fetch_macro [--out-dir data/macro] [--start-year 2009]

Downloads, without keys or accounts:
- FRED daily series as CSV: VIXCLS (VIX close), T10Y2Y (US 10-year minus 2-year), DGS2 (US 2-year).
- BIS central bank policy rates, daily, for the 8 economies (WS_CBPOL, data only).
- CFTC Commitments of Traders, legacy futures-only annual files (deacot<year>.zip), one per year.
  The current year's file is replaced on every run; earlier years are skipped when present.

S&P 500, WTI oil and copper come from OANDA instead (daily CFD candles):
    python -m scripts.fetch_candles --instruments SPX500_USD,WTICO_USD,XCU_USD --granularity D --start 2009-01-01 --end 2026-11-01 --out-dir data/macro

Each file is written to a temporary name and renamed when complete.
"""

from __future__ import annotations

import argparse
import urllib.request
from datetime import date
from pathlib import Path

FRED_SERIES = ("VIXCLS", "T10Y2Y", "DGS2")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
BIS_URL = (
    "https://stats.bis.org/api/v1/data/WS_CBPOL/D.US+XM+GB+JP+CH+CA+AU+NZ"
    "?format=csv&detail=dataonly&startPeriod={start}-01-01"
)
COT_URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"
USER_AGENT = "agentco-fx-research/1.0"
TIMEOUT_SECONDS = 300


def download(url: str, out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, open(tmp, "wb") as f:
        size = 0
        while chunk := response.read(1 << 16):
            f.write(chunk)
            size += len(chunk)
    tmp.replace(out)
    return size


def main(argv: list[str] | None = None, fetch=download, today: date | None = None) -> None:
    parser = argparse.ArgumentParser(description="Download FRED, BIS and CFTC data for the carry strategy.")
    parser.add_argument("--out-dir", default="data/macro")
    parser.add_argument("--start-year", type=int, default=2009)
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    this_year = (today or date.today()).year

    jobs = [(FRED_URL.format(series=s), out_dir / f"fred_{s}.csv", True) for s in FRED_SERIES]
    jobs.append((BIS_URL.format(start=args.start_year), out_dir / "bis_cbpol.csv", True))
    for year in range(args.start_year, this_year + 1):
        jobs.append((COT_URL.format(year=year), out_dir / "cot" / f"deacot{year}.zip", year == this_year))

    failed = []
    for url, out, refresh in jobs:
        if out.exists() and not refresh:
            print(f"{out}: present, skipped", flush=True)
            continue
        try:
            print(f"{out}: {fetch(url, out):,} bytes", flush=True)
        except Exception as exc:  # report and carry on with the rest
            failed.append(out.name)
            print(f"{out}: FAILED ({type(exc).__name__}: {exc})", flush=True)
    if failed:
        raise SystemExit(f"{len(failed)} download(s) failed: {', '.join(failed)}. Rerun to retry.")
    print("done")


if __name__ == "__main__":
    main()

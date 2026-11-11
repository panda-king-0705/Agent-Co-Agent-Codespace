"""Download daily 2-year government bond yields into data/macro/yields/ and report coverage.

Usage:
    python -m scripts.fetch_yields [--out-dir data/macro/yields] [--report-only]

No keys needed. NZD comes from a file the RBNZ only serves to browsers: download it by hand
into <out-dir>/manual/ (carry.yields.MANUAL names the file and the page it is on).
Every file is replaced on each run.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from carry.yields import MANUAL, SOURCES, load_currency
from scripts.fetch_macro import download

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")


def coverage(currency: str, out_dir: Path) -> str:
    try:
        values = load_currency(currency, out_dir)
    except Exception as exc:  # a malformed file: report it, keep going
        return f"{currency}  unreadable ({type(exc).__name__}: {exc})"
    if not values:
        hint = f"  (download {MANUAL[currency][0]} by hand into {out_dir / 'manual'})" if currency in MANUAL else ""
        return f"{currency}  no data{hint}"
    days = sorted(values)
    recent = [d for d in days if d >= date(2010, 1, 1)]
    gap = max(((b - a).days for a, b in zip(recent, recent[1:])), default=0)
    return f"{currency}  {days[0]} .. {days[-1]}  {len(recent):>5} days since 2010  longest gap {gap:>3}d  last {values[days[-1]]:.3f}%"


def main(argv: list[str] | None = None, fetch=download) -> None:
    parser = argparse.ArgumentParser(description="Download 2-year government bond yields.")
    parser.add_argument("--out-dir", default="data/macro/yields")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)

    failed = []
    if not args.report_only:
        for currency, files in SOURCES.items():
            for name, url in files:
                try:
                    print(f"{name}: {fetch(url, out_dir / name):,} bytes", flush=True)
                except Exception as exc:  # report and carry on with the rest
                    failed.append(name)
                    print(f"{name}: FAILED ({type(exc).__name__}: {exc})", flush=True)

    for currency in CURRENCIES:
        print(coverage(currency, out_dir))
    if failed:
        raise SystemExit(f"{len(failed)} download(s) failed: {', '.join(failed)}. Rerun to retry.")


if __name__ == "__main__":
    main()

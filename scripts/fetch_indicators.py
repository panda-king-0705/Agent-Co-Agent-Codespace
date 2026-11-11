"""Download the fundamental-score indicators (carry.indicators.CATALOG) and report coverage.

Usage:
    python -m scripts.fetch_indicators [--data-dir data/macro] [--report-only]

US series come from ALFRED into <data-dir>/alfred/; the rest from the FRED API, BIS,
Eurostat and the Bank of Canada into <data-dir>/indicators/. Needs FRED_API_KEY.
The report shows, per economy and category, the first and last period, the number of
releases, and when the latest one became usable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from carry.alfred import AlfredError, api_key, fetch_latest_csv, fetch_vintage_rows, write_rows
from carry.indicators import CATALOG, load_indicator
from scripts.fetch_macro import download


def report(data_dir: Path) -> list[str]:
    lines = []
    for ind in CATALOG:
        try:
            series = load_indicator(ind, data_dir)
        except FileNotFoundError:
            lines.append(f"{ind.name:<16} missing")
            continue
        except Exception as exc:  # a malformed download: report it, keep going
            lines.append(f"{ind.name:<16} unreadable ({type(exc).__name__}: {exc})")
            continue
        obs = sorted(series.observations, key=lambda o: o.date)
        if not obs:
            lines.append(f"{ind.name:<16} no releases")
            continue
        latest = series.observations[-1]
        lines.append(
            f"{ind.name:<16} {ind.source:<8} {ind.freq} {obs[0].date} .. {obs[-1].date}  "
            f"releases {len(obs):>4}  latest usable {latest.known_from:%Y-%m-%d %H:%M}Z  {ind.key}"
        )
    return lines


def main(argv: list[str] | None = None, fetch=download, fetch_rows=fetch_vintage_rows, fetch_latest=fetch_latest_csv) -> None:
    parser = argparse.ArgumentParser(description="Download fundamental-score indicators and report coverage.")
    parser.add_argument("--data-dir", default="data/macro")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)

    failed = []
    if not args.report_only:
        key = None
        for ind in CATALOG:
            try:
                if ind.source == "alfred":
                    key = key or api_key()
                    rows = fetch_rows(ind.key, key)
                    write_rows(rows, data_dir / "alfred" / ind.filename)
                    print(f"{ind.name}: {len(rows):,} revision rows", flush=True)
                elif ind.source == "fred":
                    key = key or api_key()
                    out = data_dir / "indicators" / ind.filename
                    out.parent.mkdir(parents=True, exist_ok=True)
                    text = fetch_latest(ind.key, key)
                    out.write_text(text, encoding="utf-8")
                    print(f"{ind.name}: {len(text):,} bytes", flush=True)
                else:
                    size = fetch(ind.url, data_dir / "indicators" / ind.filename)
                    print(f"{ind.name}: {size:,} bytes", flush=True)
            except (AlfredError, OSError) as exc:
                failed.append(ind.name)
                print(f"{ind.name}: FAILED ({type(exc).__name__}: {exc})", flush=True)

    for line in report(data_dir):
        print(line)
    if failed:
        raise SystemExit(f"{len(failed)} download(s) failed: {', '.join(failed)}. Rerun to retry.")


if __name__ == "__main__":
    main()

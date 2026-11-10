"""Download completed OANDA practice candles into the backtest CSV format.

Usage:
    python -m scripts.fetch_candles --start 2026-05-01 --end 2026-11-01 --out data/eurusd_m1.csv

Credentials come only from OANDA_API_TOKEN and OANDA_ACCOUNT_ID in the environment.
Practice host only. Output columns: time (ISO UTC), open, high, low, close (mid), spread_pips
(ask minus bid at the bar open), which is what `python -m scripts.backtest` reads.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from fx_scalper.oanda import Candle, OandaPracticeBroker
from fx_scalper.settings import load_settings

FIELDS = ["time", "open", "high", "low", "close", "spread_pips"]


def parse_utc(text: str) -> datetime:
    """Parse a date or datetime; a value without a zone is taken as UTC."""
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def write_candles(candles: Iterable[Candle], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for c in candles:
            writer.writerow(
                {
                    "time": c.time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "open": f"{c.open:.5f}",
                    "high": f"{c.high:.5f}",
                    "low": f"{c.low:.5f}",
                    "close": f"{c.close:.5f}",
                    "spread_pips": "" if c.spread_pips is None else f"{c.spread_pips:.2f}",
                }
            )
            count += 1
    return count


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Download OANDA practice candles to CSV.")
    parser.add_argument("--start", required=True, help="UTC start, e.g. 2026-05-01")
    parser.add_argument("--end", required=True, help="UTC end (exclusive), e.g. 2026-11-01")
    parser.add_argument("--out", default="data/eurusd_m1.csv")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--granularity", default="M1")
    args = parser.parse_args(argv)

    broker = OandaPracticeBroker(load_settings())
    candles = broker.get_candles(args.instrument, args.granularity, parse_utc(args.start), parse_utc(args.end))
    written = write_candles(candles, args.out)
    if candles:
        print(f"wrote {written} candles to {args.out} ({candles[0].time:%Y-%m-%d} to {candles[-1].time:%Y-%m-%d %H:%M} UTC)")
    else:
        print(f"no candles returned; wrote an empty file to {args.out}")


if __name__ == "__main__":
    main()

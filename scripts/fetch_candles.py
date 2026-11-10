"""Download completed OANDA practice candles into the backtest CSV format.

Usage:
    # one instrument
    python -m scripts.fetch_candles --start 2026-05-01 --end 2026-11-01 --out data/eurusd_m1.csv

    # many instruments, one file each: data/<INSTRUMENT>_<GRANULARITY>.csv
    python -m scripts.fetch_candles --instruments majors28 --granularity H1 --start 2010-01-01 --end 2026-11-01 --skip-existing

Credentials come only from OANDA_API_TOKEN and OANDA_ACCOUNT_ID in the environment.
Practice host only. Output columns: time (ISO UTC), open, high, low, close (mid), spread_pips
(ask minus bid at the bar open, in the instrument's own pips: 0.01 for JPY-quoted pairs,
0.0001 otherwise), which is what `python -m scripts.backtest` reads.

Each file is written to a temporary name first and renamed when complete, so an interrupted
download never leaves a partial file that --skip-existing would mistake for a finished one.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from fx_scalper.oanda import Candle, OandaPracticeBroker
from fx_scalper.settings import load_settings

FIELDS = ["time", "open", "high", "low", "close", "spread_pips"]

# The 28 pairs made from USD, EUR, GBP, JPY, CHF, CAD, AUD and NZD, in OANDA's naming.
MAJORS_28 = [
    "EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "USD_CAD", "AUD_USD", "NZD_USD",
    "EUR_GBP", "EUR_JPY", "EUR_CHF", "EUR_AUD", "EUR_CAD", "EUR_NZD",
    "GBP_JPY", "GBP_CHF", "GBP_AUD", "GBP_CAD", "GBP_NZD",
    "AUD_JPY", "AUD_CHF", "AUD_CAD", "AUD_NZD",
    "CAD_JPY", "CAD_CHF", "CHF_JPY",
    "NZD_JPY", "NZD_CHF", "NZD_CAD",
]


def pip_size(instrument: str) -> float:
    """OANDA pip location: 0.01 for JPY-quoted pairs, 0.0001 for the other majors and crosses."""
    return 0.01 if instrument.upper().endswith("_JPY") else 0.0001


def parse_utc(text: str) -> datetime:
    """Parse a date or datetime; a value without a zone is taken as UTC."""
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def parse_instruments(text: str) -> list[str]:
    if text.strip().lower() == "majors28":
        return list(MAJORS_28)
    names = [part.strip().upper() for part in text.split(",") if part.strip()]
    if not names:
        raise ValueError("no instruments given")
    return names


def output_path(out_dir: str | Path, instrument: str, granularity: str) -> Path:
    return Path(out_dir) / f"{instrument}_{granularity}.csv"


def write_candles(candles: Iterable[Candle], path: str | Path) -> int:
    """Write candles to `path` atomically (temporary file, then rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    count = 0
    with partial.open("w", newline="", encoding="utf-8") as stream:
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
    os.replace(partial, path)
    return count


def fetch_one(broker, instrument: str, granularity: str, start: datetime, end: datetime, out: Path) -> int:
    candles = broker.get_candles(instrument, granularity, start, end, pip_size=pip_size(instrument))
    written = write_candles(candles, out)
    if candles:
        print(f"{instrument}: wrote {written} candles to {out} "
              f"({candles[0].time:%Y-%m-%d} to {candles[-1].time:%Y-%m-%d %H:%M} UTC)", flush=True)
    else:
        print(f"{instrument}: no candles returned; wrote an empty file to {out}", flush=True)
    return written


def main(argv: list[str] | None = None, broker=None) -> None:
    parser = argparse.ArgumentParser(description="Download OANDA practice candles to CSV.")
    parser.add_argument("--start", required=True, help="UTC start, e.g. 2010-01-01")
    parser.add_argument("--end", required=True, help="UTC end (exclusive), e.g. 2026-11-01")
    parser.add_argument("--granularity", default="M1", help="OANDA granularity, e.g. M1, H1, H4, D")
    parser.add_argument("--instrument", default="EUR_USD", help="single instrument (used with --out)")
    parser.add_argument("--out", default="data/eurusd_m1.csv", help="output file for --instrument")
    parser.add_argument("--instruments", help='comma list (e.g. "EUR_USD,USD_JPY") or "majors28"')
    parser.add_argument("--out-dir", default="data", help="folder for --instruments files")
    parser.add_argument("--skip-existing", action="store_true", help="skip instruments whose file already exists")
    args = parser.parse_args(argv)

    start, end = parse_utc(args.start), parse_utc(args.end)
    broker = broker or OandaPracticeBroker(load_settings())

    if not args.instruments:
        fetch_one(broker, args.instrument.upper(), args.granularity, start, end, Path(args.out))
        return

    instruments = parse_instruments(args.instruments)
    failed = []
    for i, instrument in enumerate(instruments, 1):
        out = output_path(args.out_dir, instrument, args.granularity)
        if args.skip_existing and out.exists():
            print(f"[{i}/{len(instruments)}] {instrument}: {out} exists, skipped", flush=True)
            continue
        print(f"[{i}/{len(instruments)}] {instrument}: downloading {args.granularity}...", flush=True)
        try:
            fetch_one(broker, instrument, args.granularity, start, end, out)
        except Exception as exc:  # keep going; report at the end
            failed.append(instrument)
            print(f"{instrument}: FAILED ({type(exc).__name__}: {exc})", flush=True)
    if failed:
        print(f"done with {len(failed)} failure(s): {', '.join(failed)}. Rerun with --skip-existing to retry them.")
        raise SystemExit(1)
    print(f"done: {len(instruments)} instrument(s) in {args.out_dir}")


if __name__ == "__main__":
    main()

"""Run the carry strategy backtest on local data and write the results.

Usage:
    python -m scripts.run_backtest [--start 2010-01-01] [--end 2026-11-01]
        [--score-threshold 3] [--stop-atr 1.5] [--trail atr|swing] [--entry-hours 1|4]
        [--out logs/backtest/baseline]

--entry-hours 1 (default) enters and exits on 1H bars with the 4H trend; 4 enters and exits
on 4H bars with the daily trend (stop, target and trail then use the daily ATR).

Reads data/<PAIR>_H1.csv and data/macro/ (see the README for the download scripts).
Writes trades.csv, gates.csv (every candidate pair per day, which gates passed),
equity.csv and summary.txt to --out, and prints the summary against the pass bar:
at least 100 trades, at least +0.2R per trade, maximum drawdown no worse than 10%.
"""

from __future__ import annotations

import argparse
import time

from carry.backtest import Backtest, Params, utc_day, write_result
from carry.strategy import MarketData, Strategy, red_events


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="Backtest the carry strategy.")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default="2026-11-01")
    parser.add_argument("--candles-dir", default="data")
    parser.add_argument("--macro-dir", default="data/macro")
    parser.add_argument("--score-threshold", type=int, default=3)
    parser.add_argument("--stop-atr", type=float, default=1.5)
    parser.add_argument("--trail", choices=("atr", "swing"), default="atr")
    parser.add_argument("--entry-hours", type=int, choices=(1, 4), default=1)
    parser.add_argument("--out", default="logs/backtest/baseline")
    args = parser.parse_args(argv)

    began = time.time()
    data = MarketData.load(args.macro_dir)
    params = Params(score_threshold=args.score_threshold, stop_atr=args.stop_atr, trail=args.trail, entry_hours=args.entry_hours)
    backtest = Backtest(Strategy(data, params.score_threshold), args.candles_dir, params, red_events(args.macro_dir))
    result = backtest.run(utc_day(args.start), utc_day(args.end))
    summary = write_result(result, args.out)
    print(f"{args.start} .. {args.end}  threshold {params.score_threshold}  stop {params.stop_atr} ATR  trail {params.trail}  entries {params.entry_hours}H")
    print(
        f"trades {summary['trades']}  win rate {summary['win_rate']:.1%}  avg {summary['avg_r']:+.3f}R  "
        f"total {summary['total_r']:+.1f}R  max drawdown {summary['max_drawdown']:.1%}  "
        f"final equity {summary['final_equity']:.3f}  pass bar: {'PASS' if summary['passes'] else 'FAIL'}"
    )
    print(f"results in {args.out} ({time.time() - began:.0f}s)")
    return summary


if __name__ == "__main__":
    main()

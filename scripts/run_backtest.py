"""Run the carry strategy backtest on local data and write the results.

Usage:
    python -m scripts.run_backtest [--start 2010-01-01] [--end 2026-11-01]
        [--score-threshold 3] [--stop-atr 1.5] [--trail atr|swing] [--entry-hours 1|4]
        [--blackout-hours 6] [--rank-by policy|yield2y] [--min-rate-gap 0] [--split 2018-07-01]
        [--out logs/backtest/baseline]

--entry-hours 1 (default) enters and exits on 1H bars with the 4H trend; 4 enters and exits
on 4H bars with the daily trend (stop, target and trail then use the daily ATR).
--rank-by yield2y ranks currencies by 2-year yields instead of policy rates (policy rate where a
yield is missing); --min-rate-gap only enters pairs whose ranked levels differ by at least that
many percentage points. --split also prints (and writes to summary.txt) the summary for trades
entered before and from that date, to keep a tuning period apart from a holdout period.
--blackout-hours 0 turns the news blackout off: entries are allowed near red events and trades stay open through them.

Reads data/<PAIR>_H1.csv and data/macro/ (see the README for the download scripts).
Writes trades.csv, gates.csv (every candidate pair per day, which gates passed),
equity.csv and summary.txt to --out, and prints the summary against the pass bar:
at least 100 trades, at least +0.2R per trade, maximum drawdown no worse than 10%.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from carry.backtest import Backtest, Params, utc_day, write_result
from carry.strategy import MarketData, Strategy, red_events


def line(s: dict) -> str:
    return (
        f"trades {s['trades']}  win rate {s['win_rate']:.1%}  avg {s['avg_r']:+.3f}R  total {s['total_r']:+.1f}R  "
        f"max drawdown {s['max_drawdown']:.1%}  final equity {s['final_equity']:.3f}  pass bar: {'PASS' if s['passes'] else 'FAIL'}"
    )


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
    parser.add_argument("--blackout-hours", type=float, default=6.0)
    parser.add_argument("--rank-by", choices=("policy", "yield2y"), default="policy")
    parser.add_argument("--min-rate-gap", type=float, default=0.0)
    parser.add_argument("--split", default=None)
    parser.add_argument("--out", default="logs/backtest/baseline")
    args = parser.parse_args(argv)

    began = time.time()
    data = MarketData.load(args.macro_dir)
    params = Params(score_threshold=args.score_threshold, stop_atr=args.stop_atr, trail=args.trail, entry_hours=args.entry_hours,
                    blackout_hours=args.blackout_hours)
    backtest = Backtest(Strategy(data, params.score_threshold, args.rank_by, args.min_rate_gap), args.candles_dir, params, red_events(args.macro_dir))
    result = backtest.run(utc_day(args.start), utc_day(args.end))
    summary = write_result(result, args.out)
    print(f"{args.start} .. {args.end}  threshold {params.score_threshold}  stop {params.stop_atr} ATR  trail {params.trail}  "
          f"entries {params.entry_hours}H  blackout {params.blackout_hours:g}h  rank {args.rank_by}  min gap {args.min_rate_gap:g}")
    print(line(summary))
    extra = {"rank_by": args.rank_by, "min_rate_gap": args.min_rate_gap}
    if args.split:
        split = utc_day(args.split)
        for label, part in (("before", result.summary(until=split)), ("from", result.summary(since=split))):
            print(f"  entered {label} {args.split}: {line(part)}")
            extra.update({f"{label}_split_{k}": v for k, v in part.items()})
        extra["split"] = args.split
    with open(Path(args.out) / "summary.txt", "a", encoding="utf-8") as f:
        for k, v in extra.items():
            f.write(f"{k}: {v}
")
    print(f"results in {args.out} ({time.time() - began:.0f}s)")
    return summary


if __name__ == "__main__":
    main()

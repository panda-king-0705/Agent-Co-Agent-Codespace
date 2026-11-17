"""Run the monthly-rebalanced carry basket on local data and write the results.

Usage:
    python -m scripts.run_basket [--start 2011-01-01] [--end 2026-11-01] [--per-side 3]
        [--rank-by policy|yield2y] [--filter none|vix|vix_gate|realized|target] [--vix-max 20]
        [--vol-days 20] [--target-vol 0.08] [--max-scale 2] [--split 2018-07-01]
        [--out logs/backtest/basket/base]

Reads the seven USD-pair H1 files in data/ and data/macro/ (policy rates, 2-year yields, VIX).
Writes daily.csv and summary.txt to --out and prints annual return, volatility, Sharpe ratio
and maximum drawdown (daily mark-to-market). The start leaves 2010 as history for the
volatility filters. --split also reports the days before and from that date separately.
See carry/basket.py for the rules and cost assumptions. Not trading advice.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from carry.basket import FILTERS, Basket, BasketParams, load_prices, summarize, write_result
from carry.macro import load_fred, load_policy_rates
from carry.yields import load_yields


def line(s: dict) -> str:
    if not s.get("days"):
        return "no days"
    return (
        f"return {s['annual_return']:+.1%}/yr  vol {s['annual_vol']:.1%}  Sharpe {s['sharpe']:+.2f} (t {s['t_stat']:+.1f})  "
        f"max drawdown {s['max_drawdown']:.1%}  invested {s['invested']:.0%}  "
        f"spot {s['spot_per_year']:+.1%}  carry {s['carry_per_year']:+.1%}  cost {s['cost_per_year']:.2%} a year"
    )


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="Backtest the carry basket.")
    parser.add_argument("--start", default="2011-01-01")
    parser.add_argument("--end", default="2026-11-01")
    parser.add_argument("--candles-dir", default="data")
    parser.add_argument("--macro-dir", default="data/macro")
    parser.add_argument("--per-side", type=int, default=3)
    parser.add_argument("--rank-by", choices=("policy", "yield2y"), default="policy")
    parser.add_argument("--filter", choices=FILTERS, default="none")
    parser.add_argument("--vix-max", type=float, default=20.0)
    parser.add_argument("--vol-days", type=int, default=20)
    parser.add_argument("--target-vol", type=float, default=0.08)
    parser.add_argument("--max-scale", type=float, default=2.0)
    parser.add_argument("--split", default=None)
    parser.add_argument("--out", default="logs/backtest/basket/base")
    args = parser.parse_args(argv)

    macro = Path(args.macro_dir)
    params = BasketParams(per_side=args.per_side, rank_by=args.rank_by, vol_filter=args.filter, vix_max=args.vix_max,
                          vol_days=args.vol_days, target_vol=args.target_vol, max_scale=args.max_scale)
    basket = Basket(load_prices(args.candles_dir), load_policy_rates(macro / "bis_cbpol.csv"),
                    load_yields(macro / "yields"), load_fred(macro / "fred_VIXCLS.csv"), params)
    days = basket.run(date.fromisoformat(args.start), date.fromisoformat(args.end))
    extra = {}
    print(f"{args.start} .. {args.end}  top/bottom {params.per_side}  rank {params.rank_by}  filter {params.vol_filter}")
    summary = summarize(days)
    print(line(summary))
    if args.split:
        split = date.fromisoformat(args.split)
        for label, part in (("before", [d for d in days if d.date < split]), ("from", [d for d in days if d.date >= split])):
            s = summarize(part)
            print(f"  {label} {args.split}: {line(s)}")
            extra.update({f"{label}_split_{k}": v for k, v in s.items()})
        extra["split"] = args.split
    write_result(days, params, args.out, extra)
    print(f"results in {args.out}")
    return summary


if __name__ == "__main__":
    main()

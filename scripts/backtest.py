"""Run the simulated FX scalper against a CSV of completed EUR/USD M1 bars.

Input columns: time, open, high, low, close; optional spread_pips.
Run: python -m scripts.backtest path/to/eur_usd_m1.csv [--log logs/trades.csv]
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime
from pathlib import Path
from fx_scalper.executor import CsvTradeLog, SimulatedExecutor
from fx_scalper.risk import BrokerInputs, RiskManager
from fx_scalper.settings import Settings
from fx_scalper.signal import Bar

def read_bars(path: str | Path) -> list[Bar]:
    bars = []
    with Path(path).open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            stamp = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            spread = float(row["spread_pips"]) if row.get("spread_pips") else None
            bars.append(Bar(stamp, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), spread))
    return bars

def summarize(trades):
    count = len(trades)
    wins = sum(t.net_pnl_pips > 0 for t in trades)
    net_pips = sum(t.net_pnl_pips for t in trades)
    money = sum(t.net_pnl_money for t in trades)
    equity = peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.net_pnl_money
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {"trades": count, "win_rate_pct": (100 * wins / count if count else 0.0),
            "net_pips": net_pips, "net_money": money, "max_drawdown_money": max_dd}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="1-minute EUR/USD candle CSV")
    parser.add_argument("--log", default="logs/trades.csv")
    args = parser.parse_args()
    bars = read_bars(args.csv)
    settings = Settings(api_token="backtest", account_id="backtest")
    risk = RiskManager()
    broker = BrokerInputs(max_leverage=10, lot_step=1, min_units=1, pip_value_per_unit=0.0001)
    executor = SimulatedExecutor(settings, risk, broker, lambda: 10000.0, CsvTradeLog(args.log))
    trades = executor.process_bars(bars)
    report = summarize(trades)
    for key, value in report.items():
        print(f"{key}: {value}")

if __name__ == "__main__":
    main()

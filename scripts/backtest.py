"""Backtest a completed EUR/USD M1 CSV; run python -m scripts.backtest FILE [--log PATH]."""
from __future__ import annotations
import argparse,csv
from datetime import datetime
from pathlib import Path
from fx_scalper.executor import CsvTradeLog,SimulatedExecutor
from fx_scalper.risk import BrokerInputs,RiskManager
from fx_scalper.settings import Settings
from fx_scalper.signal import Bar

def read_bars(path):
    bars=[]
    with Path(path).open(newline="",encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            stamp=datetime.fromisoformat(row["time"].replace("Z","+00:00"))
            spread=float(row["spread_pips"]) if row.get("spread_pips") else None
            bars.append(Bar(stamp,float(row["open"]),float(row["high"]),float(row["low"]),float(row["close"]),spread))
    return bars

def summarize(trades):
    n=len(trades); wins=sum(t.net_pnl_pips>0 for t in trades); pnl=sum(t.net_pnl_pips for t in trades); money=sum(t.net_pnl_money for t in trades)
    equity=peak=drawdown=0.0
    for t in trades:
        equity+=t.net_pnl_money; peak=max(peak,equity); drawdown=max(drawdown,peak-equity)
    return {"trades":n,"win_rate_pct":100*wins/n if n else 0.0,"net_pips":pnl,"net_money":money,"max_drawdown_money":drawdown}

def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("csv",help="1-minute EUR/USD candles"); parser.add_argument("--log",default="logs/trades.csv"); args=parser.parse_args()
    bars=read_bars(args.csv); settings=Settings(api_token="backtest",account_id="backtest"); risk=RiskManager()
    broker=BrokerInputs(max_leverage=10,lot_step=1,min_units=1,pip_value_per_unit=0.0001)
    trades=SimulatedExecutor(settings,risk,broker,lambda:10000.0,CsvTradeLog(args.log)).process_bars(bars)
    for key,value in summarize(trades).items(): print(f"{key}: {value}")
if __name__=="__main__": main()

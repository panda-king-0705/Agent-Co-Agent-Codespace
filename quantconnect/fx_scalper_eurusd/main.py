# region imports
from AlgorithmImports import *
# endregion

import json
import math
from datetime import datetime, time, timedelta

"""EUR/USD 1-minute EMA 9/21 + RSI scalper, ported to QuantConnect LEAN.

Same rules as docs/fx-scalper/spec.md and risk.md in the repository:
  entry  long:  EMA9 crosses above EMA21 on bar close and 50 < RSI(14, Wilder) < 70
         short: EMA9 crosses below EMA21 on bar close and 30 < RSI < 50
  exits  stop 4 pips, target 6 pips (real stop and limit orders), max hold 15 min,
         opposite crossover at bar close, flat by Friday 20:45 UTC
  gates  new entries 13:00-16:00 UTC, none after Friday 20:00 UTC, spread at entry <= 1.5 pips,
         optional news pauses (+-15 min), one position at a time
  risk   0.5% of equity at the stop, capped at 10:1 leverage, rounded down to 1 unit;
         20 trades/day; 2% daily loss halts new entries until 00:00 UTC

Costs: prices are OANDA bid/ask quote bars, so the real spread is paid on every fill.
On top of that: 0.5 pip slippage on market and stop fills, and 0.5 pip commission per
round trip (0.25 per side). Both are the repository's ASSUMPTION values, kept to stay
conservative; OANDA standard accounts are spread-only, so set commission_pips_round_trip=0
to model that.

Parameters (QuantConnect project parameters, used for robustness sweeps):
  stop_pips, target_pips, max_hold_minutes, session_start_hour, session_end_hour,
  max_spread_pips, slippage_pips, commission_pips_round_trip

Results: a summary in the log and runtime statistics, and every trade in the Object Store
at fx_scalper/eurusd_trades.json.
"""

PIP = 0.0001

# Times (UTC) of scheduled high-impact news. Entries pause within NEWS_PAUSE_MINUTES of each.
# Empty means no pauses, the same as the repository today (config/blackout.json does not exist).
NEWS_TIMES_UTC = []  # e.g. [datetime(2026, 10, 3, 12, 30)]
NEWS_PAUSE_MINUTES = 15


class PipCommissionFeeModel(FeeModel):
    """Charges a fixed number of pips per side, in USD (the quote currency of EUR/USD)."""

    def __init__(self, pips_per_side):
        super().__init__()
        self.pips_per_side = pips_per_side

    def get_order_fee(self, parameters):
        fee = abs(parameters.order.absolute_quantity) * PIP * self.pips_per_side
        return OrderFee(CashAmount(fee, "USD"))


class PipSlippageModel:
    """Constant slippage in pips, applied by LEAN to market and stop-market fills."""

    def __init__(self, pips):
        self.pips = pips

    def get_slippage_approximation(self, asset, order):
        return self.pips * PIP


class FxScalperEurUsd(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2026, 5, 1)
        self.set_end_date(2026, 10, 31)
        self.set_cash(10000)
        self.set_time_zone(TimeZones.UTC)
        self.set_brokerage_model(BrokerageName.OANDA_BROKERAGE, AccountType.MARGIN)

        # Strategy parameters (spec defaults).
        self.stop_pips = float(self.get_parameter("stop_pips", 4.0))
        self.target_pips = float(self.get_parameter("target_pips", 6.0))
        self.max_hold = timedelta(minutes=int(self.get_parameter("max_hold_minutes", 15)))
        self.session_start = int(self.get_parameter("session_start_hour", 13))
        self.session_end = int(self.get_parameter("session_end_hour", 16))
        self.max_spread_pips = float(self.get_parameter("max_spread_pips", 1.5))
        self.slippage_pips = float(self.get_parameter("slippage_pips", 0.5))
        self.commission_rt = float(self.get_parameter("commission_pips_round_trip", 0.5))

        # Risk limits (risk.md POLICY values, still pending CFO approval).
        self.risk_per_trade = 0.005
        self.max_leverage = 10.0
        self.daily_loss_limit = 0.02
        self.max_trades_per_day = 20
        # Break-even = assumed spread 1.0 + slippage + commission; stop and target must clear it.
        break_even = 1.0 + self.slippage_pips + self.commission_rt
        if not (self.stop_pips > break_even and self.target_pips > break_even):
            raise ValueError(f"stop and target must both exceed break-even of {break_even} pips")

        security = self.add_forex("EURUSD", Resolution.MINUTE, Market.OANDA)
        security.set_leverage(self.max_leverage)
        security.set_fee_model(PipCommissionFeeModel(self.commission_rt / 2))
        security.set_slippage_model(PipSlippageModel(self.slippage_pips))
        self.symbol = security.symbol

        self._fast = self.ema(self.symbol, 9, Resolution.MINUTE)
        self._slow = self.ema(self.symbol, 21, Resolution.MINUTE)
        self._rsi = self.rsi(self.symbol, 14, MovingAverageType.WILDERS, Resolution.MINUTE)
        self.set_warm_up(timedelta(hours=3))

        self.prev_diff = None
        self.day = None
        self.day_start_equity = None
        self.trades_today = 0
        self.halted = False
        self.position = None  # dict while a trade is open
        self.trades = []
        self.double_fills = 0
        self.skipped = {"spread": 0, "risk_halt": 0, "trade_limit": 0, "size": 0}

    # --- per-bar logic ------------------------------------------------------

    def on_data(self, data):
        bar = data.quote_bars.get(self.symbol)
        if bar is None:
            return
        self._roll_day()
        if not (self._fast.is_ready and self._slow.is_ready and self._rsi.is_ready):
            return

        diff = self._fast.current.value - self._slow.current.value
        prev, self.prev_diff = self.prev_diff, diff
        if self.is_warming_up or prev is None:
            return
        cross = 1 if prev <= 0 < diff else -1 if prev >= 0 > diff else 0

        if self.position is not None and self.portfolio[self.symbol].invested:
            self._manage_open_position(cross)
            return
        if self.position is None and cross != 0:
            self._try_entry(cross, bar)

    def _manage_open_position(self, cross):
        p = self.position
        if p.get("entry_time") is None:
            return
        if self.time - p["entry_time"] >= self.max_hold:
            self._exit("max_hold")
        elif self._past_friday(time(20, 45)):
            self._exit("weekend")
        elif cross == -p["direction"]:
            self._exit("opposite_cross")

    def _try_entry(self, direction, bar):
        rsi = self._rsi.current.value
        if direction > 0 and not (50 < rsi < 70):
            return
        if direction < 0 and not (30 < rsi < 50):
            return
        if not self._entry_window_open():
            return
        if self.halted:
            self.skipped["risk_halt"] += 1
            return
        if self.trades_today >= self.max_trades_per_day:
            self.skipped["trade_limit"] += 1
            return
        spread_pips = (bar.ask.close - bar.bid.close) / PIP
        if spread_pips > self.max_spread_pips:
            self.skipped["spread"] += 1
            return

        equity = self.portfolio.total_portfolio_value
        price = bar.ask.close if direction > 0 else bar.bid.close
        risk_units = equity * self.risk_per_trade / (self.stop_pips * PIP)
        # 1% under the cap so LEAN's buying-power check, which includes fees, still passes.
        cap_units = equity * self.max_leverage * 0.99 / price
        units = int(math.floor(min(risk_units, cap_units)))
        if units < 1:
            self.skipped["size"] += 1
            return

        self.position = {"direction": direction, "units": units, "spread_at_signal": spread_pips}
        self.trades_today += 1
        self.market_order(self.symbol, direction * units, tag="entry")

    def _exit(self, reason):
        self.transactions.cancel_open_orders(self.symbol)
        qty = self.portfolio[self.symbol].quantity
        if qty != 0:
            self.market_order(self.symbol, -qty, tag=f"exit:{reason}")

    # --- orders -------------------------------------------------------------

    def on_order_event(self, event):
        tag = self.transactions.get_order_by_id(event.order_id).tag or ""
        p = self.position
        if event.status in (OrderStatus.INVALID, OrderStatus.CANCELED) and tag == "entry":
            self.debug(f"{self.time}: entry order {event.status}: {event.message}")
            self.position = None
            return
        if event.status != OrderStatus.FILLED:
            return

        if tag == "entry" and p is not None:
            p["entry_price"] = event.fill_price
            p["entry_time"] = self.time
            d, q = p["direction"], event.fill_quantity
            stop = event.fill_price - d * self.stop_pips * PIP
            target = event.fill_price + d * self.target_pips * PIP
            p["stop_ticket"] = self.stop_market_order(self.symbol, -q, stop, tag="stop")
            p["target_ticket"] = self.limit_order(self.symbol, -q, target, tag="target")
            return

        if p is None or "entry_price" not in p:
            return
        if tag in ("stop", "target"):
            other = p["target_ticket"] if tag == "stop" else p["stop_ticket"]
            if other.status not in (OrderStatus.FILLED, OrderStatus.CANCELED):
                other.cancel()
            self._close_trade(tag, event.fill_price)
            if self.portfolio[self.symbol].quantity != 0:
                # Stop and target both filled in one bar; flatten and count it.
                self.double_fills += 1
                self.transactions.cancel_open_orders(self.symbol)
                self.market_order(self.symbol, -self.portfolio[self.symbol].quantity, tag="double_fill_fix")
        elif tag.startswith("exit:"):
            self._close_trade(tag.split(":", 1)[1], event.fill_price)

    def _close_trade(self, reason, exit_price):
        p = self.position
        gross = p["direction"] * (exit_price - p["entry_price"]) / PIP
        self.trades.append({
            "entry_time": str(p["entry_time"]),
            "exit_time": str(self.time),
            "direction": p["direction"],
            "units": p["units"],
            "entry_price": p["entry_price"],
            "exit_price": exit_price,
            "reason": reason,
            "spread_at_signal_pips": round(p["spread_at_signal"], 2),
            "gross_pips_after_spread": round(gross, 2),
            "net_pips": round(gross - self.commission_rt, 2),
            "minutes_held": round((self.time - p["entry_time"]).total_seconds() / 60, 1),
        })
        self.position = None

    # --- gates and daily risk ----------------------------------------------

    def _entry_window_open(self):
        now = self.time
        if not (self.session_start <= now.hour < self.session_end):
            return False
        if self._past_friday(time(20, 0)):
            return False
        pause = timedelta(minutes=NEWS_PAUSE_MINUTES)
        return all(abs(now - t) > pause for t in NEWS_TIMES_UTC)

    def _past_friday(self, cutoff):
        return self.time.weekday() == 4 and self.time.time() >= cutoff

    def _roll_day(self):
        equity = self.portfolio.total_portfolio_value
        if self.time.date() != self.day:
            self.day = self.time.date()
            self.day_start_equity = equity
            self.trades_today = 0
            self.halted = False
        elif not self.halted and equity <= self.day_start_equity * (1 - self.daily_loss_limit):
            self.halted = True
            self.debug(f"{self.time}: daily loss limit hit; no new entries until 00:00 UTC")

    # --- report -------------------------------------------------------------

    def on_end_of_algorithm(self):
        t = self.trades
        n = len(t)
        wins = [x["net_pips"] for x in t if x["net_pips"] > 0]
        losses = [x["net_pips"] for x in t if x["net_pips"] <= 0]
        by_reason = {}
        for x in t:
            r = by_reason.setdefault(x["reason"], {"trades": 0, "net_pips": 0.0})
            r["trades"] += 1
            r["net_pips"] = round(r["net_pips"] + x["net_pips"], 2)
        summary = {
            "trades": n,
            "win_rate_pct": round(100 * len(wins) / n, 1) if n else 0.0,
            "avg_win_pips": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss_pips": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "expectancy_pips": round(sum(x["net_pips"] for x in t) / n, 3) if n else 0.0,
            "net_pips": round(sum(x["net_pips"] for x in t), 1),
            "avg_spread_at_signal_pips": round(sum(x["spread_at_signal_pips"] for x in t) / n, 2) if n else 0.0,
            "by_exit_reason": by_reason,
            "double_fills": self.double_fills,
            "skipped_entries": self.skipped,
            "parameters": {
                "stop_pips": self.stop_pips, "target_pips": self.target_pips,
                "max_hold_minutes": self.max_hold.total_seconds() / 60,
                "session_utc": f"{self.session_start}:00-{self.session_end}:00",
                "max_spread_pips": self.max_spread_pips, "slippage_pips": self.slippage_pips,
                "commission_pips_round_trip": self.commission_rt,
            },
        }
        self.log("FX_SCALPER_SUMMARY " + json.dumps(summary))
        for key in ("trades", "win_rate_pct", "expectancy_pips", "net_pips"):
            self.set_runtime_statistic(key, str(summary[key]))
        self.object_store.save("fx_scalper/eurusd_trades.json", json.dumps({"summary": summary, "trades": t}))

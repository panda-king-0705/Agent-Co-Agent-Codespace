"""Backtest engine for the carry strategy.

Each UTC day the Strategy decides which pairs may be traded (carry rank, score, gates).
Two timeframe set-ups (Params.entry_hours):
- 1 (default): entries and exits on 1H bars, trend, stop and trail on 4H bars;
- 4: entries and exits on 4H bars, trend, stop and trail on daily bars.
Below, "entry bar" is the 1H or 4H bar and "trend bar" the 4H or daily bar.
For the pairs allowed that day, the entry rules are checked on each entry bar as it closes;
a signal fills at the next entry bar's open. Fills are at bid/ask: mid +/- half the spread.

Per trade:
- Stop = stop_atr x trend-bar ATR(14) from the fill (that distance is 1R); take-profit at
  take_profit_r (3R).
- At the target, if price is more than strong_trend_atr x trend-bar ATR beyond the trend-bar
  200 EMA (or always, with trail_always), close close_fraction_at_target (75%) and trail the
  rest (trail_atr x trend-bar ATR behind the best price, or the last trend-bar swing), with
  the stop at break-even or better; otherwise close it all. If a bar touches both stop and
  target, the stop is assumed hit first.
- trail_from_entry: no target; the stop trails trail_atr x trend-bar ATR behind the best
  price from the fill on, never looser than the initial stop.
- The trend bar used while managing entry bar i is the last one complete when bar i opens.
- Closed at the bar's open: when a red event for either currency is within the blackout
  window ahead, or when the carry ranking no longer has the pair's currencies in the
  top 3 / bottom 3. Stop to break-even if in profit when VIX turns risk-off, the score gap
  falls below the threshold, or COT turns.
- Swap: the policy-rate differential minus a broker markup, accrued over time held.

Risk: risk_per_trade of equity per trade (brake_risk once equity is brake_drawdown below its
peak). At most one new trade per day and no currency in more than one open trade.
Equity moves when a trade closes (no mark-to-market), so drawdown is on closed trades.
"""

from __future__ import annotations

import csv
from bisect import bisect_left
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from carry.strategy import GATES, Candidate, DayContext, Strategy
from carry.ta import Bar, atr, ema, load_h1, macd_histogram, resample, rsi, swing_history

HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class Params:
    score_threshold: int = 3
    stop_atr: float = 1.5
    take_profit_r: float = 3.0
    strong_trend_atr: float = 2.0
    close_fraction_at_target: float = 0.75
    trail: str = "atr"  # atr | swing
    trail_atr: float = 1.0  # ATR trail distance, in trend-bar ATRs
    trail_always: bool = False  # at the target, always partly close and trail (not only in strong trends)
    trail_from_entry: bool = False  # no target: trail from the fill
    risk_per_trade: float = 0.01
    brake_risk: float = 0.005
    brake_drawdown: float = 0.05
    blackout_hours: float = 6.0  # 0 turns the news blackout off
    signal_lookback: int = 3  # bars within which location and momentum may occur
    swap_markup: float = 0.01  # annual, taken off the rate differential
    max_new_per_day: int = 1
    entry_hours: int = 1  # 1: 1H entries / 4H trend; 4: 4H entries / daily trend


class PairData:
    """One instrument's entry bars with every indicator precomputed, and its trend bars.

    `bars` are the entry bars (`bar_hours` long); trend bars are built from them
    (`trend_hours` long: 4 for 1H entries, 24 for 4H entries).
    """

    def __init__(self, instrument: str, bars: list[Bar], bar_hours: int = 1, trend_hours: int = 4):
        self.instrument = instrument
        self.bars = bars
        self.bar_length = timedelta(hours=bar_hours)
        self.index = {b.time: i for i, b in enumerate(bars)}
        closes = [b.close for b in bars]
        self.ema50 = ema(closes, 50)
        self.rsi = rsi(closes, 14)
        self.macd = macd_histogram(closes)
        self.highs, self.lows = swing_history(bars)
        self.htf = resample(bars, trend_hours)
        htf_closes = [b.close for b in self.htf]
        self.htf_ema200 = ema(htf_closes, 200)
        self.htf_atr = atr(self.htf, 14)
        self.htf_highs, self.htf_lows = swing_history(self.htf)
        self._htf_done = [b.time + timedelta(hours=trend_hours) for b in self.htf]

    def htf_at(self, i: int) -> Optional[int]:
        """Index of the last trend bar complete when entry bar i closes."""
        closed = self.bars[i].time + self.bar_length
        k = bisect_left(self._htf_done, closed + timedelta(microseconds=1)) - 1
        return k if k >= 0 else None

    def trend(self, i: int, side: int) -> Optional[tuple[float, float, float]]:
        """(close, EMA200, ATR) of the trend bar at entry bar i if its trend is in `side`'s direction."""
        k = self.htf_at(i)
        if k is None or self.htf_ema200[k] is None or self.htf_atr[k] is None:
            return None
        close, line = self.htf[k].close, self.htf_ema200[k]
        return (close, line, self.htf_atr[k]) if side * (close - line) > 0 else None


def _location(p: PairData, k: int, side: int, atr4: float) -> bool:
    b = p.bars[k]
    if p.ema50[k] is not None and b.low <= p.ema50[k] <= b.high:
        return True
    highs, lows = p.highs[k], p.lows[k]
    if not highs or not lows:
        return False
    hi, lo = p.bars[highs[-1]].high, p.bars[lows[-1]].low
    if side > 0 and lows[-1] < highs[-1]:  # up-swing low -> high, pulling back
        top, bottom = hi - 0.382 * (hi - lo), hi - 0.618 * (hi - lo)
        if bottom <= b.low <= top:
            return True
    if side < 0 and highs[-1] < lows[-1]:  # down-swing high -> low, pulling back up
        bottom, top = lo + 0.382 * (hi - lo), lo + 0.618 * (hi - lo)
        if bottom <= b.high <= top:
            return True
    # Holding at the previous swing: tested within 0.2 x trend-bar ATR and closed back on its side.
    if side > 0:
        return abs(b.low - lo) <= 0.2 * atr4 and b.close > lo
    return abs(b.high - hi) <= 0.2 * atr4 and b.close < hi


def _trigger(p: PairData, k: int, side: int) -> bool:
    if k < 1:
        return False
    b, prev = p.bars[k], p.bars[k - 1]
    body, rng = abs(b.close - b.open), b.high - b.low
    if side > 0:
        engulf = prev.close < prev.open and b.close > b.open and b.close >= prev.open and b.open <= prev.close
        wick = min(b.open, b.close) - b.low
        pin = rng > 0 and wick >= 2 * body and wick >= 0.6 * rng and b.close >= b.low + rng / 2
        highs = p.highs[k]
        breakout = len(highs) >= 2 and p.bars[highs[-1]].high < p.bars[highs[-2]].high and b.close > p.bars[highs[-1]].high
        return engulf or pin or breakout
    engulf = prev.close > prev.open and b.close < b.open and b.close <= prev.open and b.open >= prev.close
    wick = b.high - max(b.open, b.close)
    pin = rng > 0 and wick >= 2 * body and wick >= 0.6 * rng and b.close <= b.high - rng / 2
    lows = p.lows[k]
    breakdown = len(lows) >= 2 and p.bars[lows[-1]].low > p.bars[lows[-2]].low and b.close < p.bars[lows[-1]].low
    return engulf or pin or breakdown


def _momentum(p: PairData, k: int, side: int) -> bool:
    if k < 1:
        return False
    r0, r1, m0, m1 = p.rsi[k - 1], p.rsi[k], p.macd[k - 1], p.macd[k]
    rsi_flip = r0 is not None and r1 is not None and side * (r0 - 50) < 0 <= side * (r1 - 50)
    macd_flip = m0 is not None and m1 is not None and side * m0 < 0 <= side * m1
    return rsi_flip or macd_flip


def entry_signal(p: PairData, i: int, side: int, lookback: int = 3) -> Optional[float]:
    """Trend-bar ATR if entry bar i (just closed) completes an entry in `side`'s direction, else None."""
    trend = p.trend(i, side)
    if trend is None or i < lookback:
        return None
    atr4 = trend[2]
    window = range(i - lookback + 1, i + 1)
    if not _trigger(p, i, side):
        return None
    if not any(_location(p, k, side, atr4) for k in window):
        return None
    if not any(_momentum(p, k, side) for k in window):
        return None
    return atr4


@dataclass
class Trade:
    instrument: str
    high: str
    low: str
    side: int
    entry_time: datetime
    entry: float
    r_distance: float
    stop: float
    target: float
    risk: float
    score_diff: int
    soft_count: int
    remaining: float = 1.0
    realized_r: float = 0.0
    trailing: bool = False
    best: float = 0.0
    swap_r: float = 0.0
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    exits: list = field(default_factory=list)

    @property
    def result_r(self) -> float:
        return self.realized_r + self.swap_r

    def close_part(self, fraction: float, price: float, when: datetime, reason: str) -> None:
        fraction = min(fraction, self.remaining)
        self.realized_r += fraction * self.side * (price - self.entry) / self.r_distance
        self.remaining -= fraction
        self.exits.append((when, price, fraction, reason))
        if self.remaining <= 1e-9:
            self.remaining = 0.0
            self.exit_time, self.exit_reason = when, reason


@dataclass
class Result:
    params: Params
    trades: list[Trade]
    equity: list[tuple[datetime, float]]
    gate_log: list[dict]

    def summary(self, since: Optional[datetime] = None, until: Optional[datetime] = None) -> dict:
        """Pass-bar statistics, for all trades or those entered in [since, until).

        For the whole run the equity curve is used; for a period it is rebuilt by compounding
        that period's trades in exit order (as the run does), starting from 1.
        """
        whole = since is None and until is None
        trades = [t for t in self.trades if (since is None or t.entry_time >= since) and (until is None or t.entry_time < until)]
        rs = [t.result_r for t in trades]
        wins = [r for r in rs if r > 0]
        if whole:
            curve = [e for _, e in self.equity] or [1.0]
        else:
            curve = [1.0]
            for t in sorted(trades, key=lambda t: t.exit_time):
                curve.append(curve[-1] * (1 + t.risk * t.result_r))
        equity, peak, worst = curve[-1], 1.0, 0.0
        for e in curve:
            peak = max(peak, e)
            worst = max(worst, 1 - e / peak)
        n = len(rs)
        avg = sum(rs) / n if n else 0.0
        return {
            "trades": n,
            "win_rate": len(wins) / n if n else 0.0,
            "avg_r": avg,
            "total_r": sum(rs),
            "max_drawdown": worst,
            "final_equity": equity,
            "passes": n >= 100 and avg >= 0.2 and worst <= 0.10,
        }


class Backtest:
    def __init__(self, strategy: Strategy, candles_dir: str | Path, params: Params, events: dict[str, list[datetime]]):
        self.strategy = strategy
        self.candles_dir = Path(candles_dir)
        self.params = params
        self.events = {c: sorted(ts) for c, ts in events.items()}
        self._pairs: dict[str, Optional[PairData]] = {}

    def pair(self, instrument: str) -> Optional[PairData]:
        if instrument not in self._pairs:
            path = self.candles_dir / f"{instrument}_H1.csv"
            if not path.exists():
                self._pairs[instrument] = None
            elif self.params.entry_hours == 1:
                self._pairs[instrument] = PairData(instrument, load_h1(path, instrument))
            else:
                bars = resample(load_h1(path, instrument), self.params.entry_hours)
                self._pairs[instrument] = PairData(instrument, bars, self.params.entry_hours, 24)
        return self._pairs[instrument]

    def event_ahead(self, currencies: tuple[str, ...], when: datetime) -> bool:
        if self.params.blackout_hours <= 0:
            return False
        horizon = when + timedelta(hours=self.params.blackout_hours)
        for c in currencies:
            times = self.events.get(c, [])
            k = bisect_left(times, when)
            if k < len(times) and times[k] <= horizon:
                return True
        return False

    def event_behind(self, currencies: tuple[str, ...], when: datetime) -> bool:
        if self.params.blackout_hours <= 0:
            return False
        since = when - timedelta(hours=self.params.blackout_hours)
        for c in currencies:
            times = self.events.get(c, [])
            k = bisect_left(times, since)
            if k < len(times) and times[k] <= when:
                return True
        return False

    def swap_r(self, t: Trade, start: datetime, end: datetime) -> float:
        data = self.strategy.data
        hi, lo = data.rates[t.high].asof(start), data.rates[t.low].asof(start)
        if hi is None or lo is None:
            return 0.0
        annual = (hi.value - lo.value) / 100.0 - self.params.swap_markup
        years = (end - start).total_seconds() / (365 * 24 * 3600)
        return annual * years * t.entry / t.r_distance

    def run(self, start: datetime, end: datetime) -> Result:
        p = self.params
        equity, peak = 1.0, 1.0
        curve: list[tuple[datetime, float]] = [(start, equity)]
        open_trades: list[Trade] = []
        done: list[Trade] = []
        gate_log: list[dict] = []
        day: Optional[DayContext] = None
        new_today = 0
        t = start.replace(minute=0, second=0, microsecond=0)

        def finish(trade: Trade) -> None:
            nonlocal equity, peak
            open_trades.remove(trade)
            done.append(trade)
            equity *= 1 + trade.risk * trade.result_r
            peak = max(peak, equity)
            curve.append((trade.exit_time, equity))

        while t < end:
            if day is None or t.date() != day.when.date():
                day = self.strategy.day(t)
                new_today = 0
                for c in day.candidates:
                    gate_log.append({"date": t.date().isoformat(), "instrument": c.instrument, "side": c.side,
                                     "high": c.high, "low": c.low, "score_diff": c.score_diff, "soft": c.soft_count,
                                     **{g: int(c.gates[g]) for g in GATES}, "all": int(c.passed)})
                by_pair = {(c.high, c.low): c for c in day.candidates}
                for trade in list(open_trades):
                    c = by_pair.get((trade.high, trade.low))
                    if c is None:  # carry ranking changed
                        pd = self.pair(trade.instrument)
                        i = pd.index.get(t) if pd else None
                        if i is not None:
                            bar = pd.bars[i]
                            price = bar.open - trade.side * bar.spread / 2
                            trade.swap_r += self.swap_r(trade, trade.entry_time, t)
                            trade.close_part(trade.remaining, price, t, "carry_change")
                            finish(trade)
                        continue
                    if not all(c.gates[g] for g in ("vix", "score", "cot") if g in c.required):
                        pd = self.pair(trade.instrument)
                        i = pd.index.get(t) if pd else None
                        if i is not None and trade.side * (pd.bars[i].open - trade.entry) > 0:
                            trade.stop = max(trade.stop, trade.entry) if trade.side > 0 else min(trade.stop, trade.entry)

            for trade in list(open_trades):
                pd = self.pair(trade.instrument)
                i = pd.index.get(t)
                if i is None:
                    continue
                self._manage(trade, pd, i, t)
                if trade.remaining == 0.0:
                    finish(trade)

            if new_today < p.max_new_per_day and day.candidates:
                busy = {c for tr in open_trades for c in (tr.high, tr.low)}
                for c in day.candidates:
                    if not c.passed or c.high in busy or c.low in busy:
                        continue
                    if self.event_ahead((c.high, c.low), t) or self.event_behind((c.high, c.low), t):
                        continue
                    pd = self.pair(c.instrument)
                    i = pd.index.get(t) if pd else None
                    if i is None or i < 1:
                        continue
                    atr4 = entry_signal(pd, i - 1, c.side, p.signal_lookback)
                    if atr4 is None:
                        continue
                    trade = self._open(c, pd, i, t, atr4, equity, peak)
                    open_trades.append(trade)
                    new_today += 1
                    self._manage(trade, pd, i, t)  # the rest of the entry bar can stop it out
                    if trade.remaining == 0.0:
                        finish(trade)
                    break
            t += HOUR

        for trade in list(open_trades):  # still open at the end: close at the last close
            pd = self.pair(trade.instrument)
            last = pd.bars[min(len(pd.bars) - 1, bisect_left([b.time for b in pd.bars], end) - 1)]
            price = last.close - trade.side * last.spread / 2
            trade.swap_r += self.swap_r(trade, trade.entry_time, last.time)
            trade.close_part(trade.remaining, price, last.time + pd.bar_length, "end_of_test")
            finish(trade)
        return Result(p, done, curve, gate_log)

    def _open(self, c: Candidate, pd: PairData, i: int, t: datetime, atr4: float, equity: float, peak: float) -> Trade:
        p = self.params
        bar = pd.bars[i]
        entry = bar.open + c.side * bar.spread / 2
        r = p.stop_atr * atr4
        risk = p.brake_risk if equity < (1 - p.brake_drawdown) * peak else p.risk_per_trade
        trade = Trade(c.instrument, c.high, c.low, c.side, t, entry, r, entry - c.side * r,
                      entry + c.side * p.take_profit_r * r, risk, c.score_diff, c.soft_count, best=entry)
        trade.trailing = p.trail_from_entry
        return trade

    def _manage(self, trade: Trade, pd: PairData, i: int, t: datetime) -> None:
        p = self.params
        bar = pd.bars[i]
        half = bar.spread / 2
        s = trade.side
        # Exit side of the book: a long sells at the bid, a short buys at the ask.
        open_px, low_px, high_px = bar.open - s * half, bar.low - s * half, bar.high - s * half
        adverse, favourable = (low_px, high_px) if s > 0 else (high_px, low_px)

        if self.event_ahead((trade.high, trade.low), t):
            trade.swap_r += self.swap_r(trade, trade.entry_time, t)
            trade.close_part(trade.remaining, open_px, t, "blackout")
            return

        def accrue_and_close(price: float, reason: str, fraction: float) -> None:
            if fraction >= trade.remaining:
                trade.swap_r += self.swap_r(trade, trade.entry_time, t + pd.bar_length)
            trade.close_part(fraction, price, t + pd.bar_length, reason)

        if s * (open_px - trade.stop) <= 0:  # gapped through the stop
            accrue_and_close(open_px, "stop", trade.remaining)
            return
        if s * (adverse - trade.stop) <= 0:
            accrue_and_close(trade.stop, "trail" if trade.trailing else "stop", trade.remaining)
            return
        k = pd.htf_at(i - 1) if i > 0 else None  # trend bars complete when this bar opened
        if not trade.trailing and s * (favourable - trade.target) >= 0:
            strong = p.trail_always or (
                k is not None and pd.htf_ema200[k] is not None and pd.htf_atr[k] is not None
                and s * (pd.htf[k].close - pd.htf_ema200[k]) > p.strong_trend_atr * pd.htf_atr[k]
            )
            if not strong:
                accrue_and_close(trade.target, "target", trade.remaining)
                return
            accrue_and_close(trade.target, "target", p.close_fraction_at_target)
            trade.trailing = True
            trade.best = trade.target
            trade.stop = max(trade.stop, trade.entry) if s > 0 else min(trade.stop, trade.entry)
        if trade.trailing:
            trade.best = max(trade.best, favourable) if s > 0 else min(trade.best, favourable)
            if p.trail == "atr" and k is not None and pd.htf_atr[k] is not None:
                level = trade.best - s * p.trail_atr * pd.htf_atr[k]
            elif k is not None:
                swings = pd.htf_lows[k] if s > 0 else pd.htf_highs[k]
                level = (pd.htf[swings[-1]].low if s > 0 else pd.htf[swings[-1]].high) if swings else trade.stop
            else:
                level = trade.stop
            trade.stop = max(trade.stop, level) if s > 0 else min(trade.stop, level)


# --- output ----------------------------------------------------------------------------------


def write_result(result: Result, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fields = ["instrument", "side", "high", "low", "entry_time", "exit_time", "exit_reason", "entry", "r_distance",
              "risk", "score_diff", "soft_count", "realized_r", "swap_r", "result_r"]
    with open(out / "trades.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for t in result.trades:
            row = {**asdict(t), "result_r": t.result_r}
            w.writerow([row[k].isoformat() if isinstance(row[k], datetime) else row[k] for k in fields])
    if result.gate_log:
        with open(out / "gates.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(result.gate_log[0]))
            w.writeheader()
            w.writerows(result.gate_log)
    with open(out / "equity.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "equity"])
        w.writerows((t.isoformat(), f"{e:.6f}") for t, e in result.equity)
    summary = result.summary()
    with open(out / "summary.txt", "w", encoding="utf-8") as f:
        for k, v in {**asdict(result.params), **summary}.items():
            f.write(f"{k}: {v}\n")
    return summary


def utc_day(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)

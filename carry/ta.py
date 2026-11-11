"""Price bars and indicators for the carry backtest (standard library only).

Bars come from the OANDA H1 files (mid OHLC plus spread in pips). 4H bars are built from
them on fixed UTC boundaries (00, 04, 08, 12, 16, 20). Indicator functions take plain lists
and return lists of the same length, with None until enough bars exist.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Optional

FOUR_HOURS = timedelta(hours=4)


@dataclass(frozen=True)
class Bar:
    time: datetime  # open time, UTC
    open: float
    high: float
    low: float
    close: float
    spread: float  # full bid/ask spread in price units


def pip_size(instrument: str) -> float:
    return 0.01 if instrument.endswith("JPY") else 0.0001


def load_h1(path: str | Path, instrument: str) -> list[Bar]:
    """OANDA H1 CSV -> bars, cleaned.

    - Sunday bars before 21:00 UTC (thin pre-open quotes in the 2010-2012 files) are dropped.
    - A zero or missing spread is replaced by the instrument's median spread.
    """
    pip = pip_size(instrument)
    raw = []
    with open(path, encoding="utf-8", newline="") as f:
        for rec in csv.DictReader(f):
            t = datetime.fromisoformat(rec["time"].replace("Z", "+00:00"))
            if t.weekday() == 6 and t.hour < 21:
                continue
            spread = float(rec["spread_pips"] or 0) * pip
            raw.append((t, float(rec["open"]), float(rec["high"]), float(rec["low"]), float(rec["close"]), spread))
    typical = median(s for *_, s in raw if s > 0) if any(s > 0 for *_, s in raw) else 0.0
    return [Bar(t, o, h, low, c, s if s > 0 else typical) for t, o, h, low, c, s in raw]


def resample_4h(bars: Sequence[Bar]) -> list[Bar]:
    """Group H1 bars into 4H bars by UTC block. A block's bar is complete at block start + 4h."""
    out: list[Bar] = []
    block = None
    for b in bars:
        start = b.time.replace(hour=b.time.hour - b.time.hour % 4, minute=0, second=0, microsecond=0)
        if block is None or start != block[0]:
            if block is not None:
                out.append(Bar(*block))
            block = [start, b.open, b.high, b.low, b.close, b.spread]
        else:
            block[2] = max(block[2], b.high)
            block[3] = min(block[3], b.low)
            block[4] = b.close
            block[5] = b.spread
    if block is not None:
        out.append(Bar(*block))
    return out


def ema(values: Sequence[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = values[i] * k + current * (1 - k)
        out[i] = current
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> list[Optional[float]]:
    """Wilder's average true range."""
    out: list[Optional[float]] = [None] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        prev = bars[i - 1].close if i else b.close
        trs.append(max(b.high - b.low, abs(b.high - prev), abs(b.low - prev)))
    if len(bars) < period:
        return out
    current = sum(trs[:period]) / period
    out[period - 1] = current
    for i in range(period, len(bars)):
        current = (current * (period - 1) + trs[i]) / period
        out[i] = current
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[Optional[float]]:
    """Wilder's RSI."""
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period

    def value(g: float, loss: float) -> float:
        return 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + g / loss)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def macd_histogram(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> list[Optional[float]]:
    fast_ema, slow_ema = ema(closes, fast), ema(closes, slow)
    macd = [f - s if f is not None and s is not None else None for f, s in zip(fast_ema, slow_ema)]
    first = next((i for i, m in enumerate(macd) if m is not None), len(macd))
    signal_line = [None] * first + ema([m for m in macd[first:]], signal)
    return [m - s if m is not None and s is not None else None for m, s in zip(macd, signal_line)]


def pivots(bars: Sequence[Bar], width: int = 2) -> tuple[list[Optional[int]], list[Optional[int]]]:
    """Index of the latest confirmed swing high / swing low known at each bar.

    A swing high at bar j has a high above the `width` bars on each side; it is confirmed,
    and so usable, only at bar j + width. Same for swing lows with lows.
    """
    n = len(bars)
    last_high: list[Optional[int]] = [None] * n
    last_low: list[Optional[int]] = [None] * n
    hi = lo = None
    for i in range(n):
        j = i - width
        if j >= width:
            window = bars[j - width : i + 1]
            if all(bars[j].high > b.high for k, b in enumerate(window) if k != width):
                hi = j
            if all(bars[j].low < b.low for k, b in enumerate(window) if k != width):
                lo = j
        last_high[i], last_low[i] = hi, lo
    return last_high, last_low


def swing_history(bars: Sequence[Bar], width: int = 2) -> tuple[list[list[int]], list[list[int]]]:
    """For each bar, the confirmed swing-high and swing-low indices so far (last 3 kept)."""
    highs_at: list[list[int]] = []
    lows_at: list[list[int]] = []
    highs: list[int] = []
    lows: list[int] = []
    last_high, last_low = pivots(bars, width)
    for i in range(len(bars)):
        if last_high[i] is not None and (not highs or highs[-1] != last_high[i]):
            highs = (highs + [last_high[i]])[-3:]
        if last_low[i] is not None and (not lows or lows[-1] != last_low[i]):
            lows = (lows + [last_low[i]])[-3:]
        highs_at.append(highs)
        lows_at.append(lows)
    return highs_at, lows_at


def sma(values: Sequence[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out

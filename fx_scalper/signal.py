"""Signal rules for the FX scalper (spec sections 2, 3, 4 and 7).

Pure functions only: no I/O, no broker calls. Thresholds, periods and pip
distances come from Settings (spec section 14 keys).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional, Sequence

from .settings import Settings

Side = Literal["long", "short"]


@dataclass(frozen=True)
class Bar:
    """One completed M1 candle, mid prices (spec section 1)."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    spread_pips: Optional[float] = None


# --- Indicators (spec section 2) -------------------------------------------


def ema(values: Sequence[float], period: int) -> list[Optional[float]]:
    """EMA seeded with the SMA of the first `period` values, k = 2/(period+1).

    Entries before the seed index are None.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        # No losses: 100 if there were gains; flat series is neutral 50.
        return 100.0 if avg_gain > 0.0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def rsi_wilder(values: Sequence[float], period: int) -> list[Optional[float]]:
    """RSI with Wilder smoothing (spec section 2).

    First average is the simple mean of the first `period` changes; then
    avg = (prev_avg * (period - 1) + current) / period.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = []
    losses = []
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = _rsi_from_averages(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
        out[i] = _rsi_from_averages(avg_gain, avg_loss)
    return out


# --- Entry rules (spec section 3) ------------------------------------------


def crossover(
    fast_prev: Optional[float],
    slow_prev: Optional[float],
    fast: Optional[float],
    slow: Optional[float],
) -> Optional[Side]:
    """EMA crossover on bar close (spec sections 3 and 4.4).

    Long:  fast[i-1] <= slow[i-1] and fast[i] > slow[i].
    Short: fast[i-1] >= slow[i-1] and fast[i] < slow[i].
    """
    if None in (fast_prev, slow_prev, fast, slow):
        return None
    if fast_prev <= slow_prev and fast > slow:
        return "long"
    if fast_prev >= slow_prev and fast < slow:
        return "short"
    return None


def entry_signal(
    fast_prev: Optional[float],
    slow_prev: Optional[float],
    fast: Optional[float],
    slow: Optional[float],
    rsi_now: Optional[float],
    settings: Settings,
) -> Optional[Side]:
    """Entry signal on the close of bar i (spec section 3).

    Long needs a bullish crossover AND RSI_LONG_MIN < RSI < RSI_LONG_MAX.
    Short needs a bearish crossover AND RSI_SHORT_MIN < RSI < RSI_SHORT_MAX.
    Bounds are exclusive. Gates in sections 5-8 are checked separately.
    """
    if rsi_now is None:
        return None
    side = crossover(fast_prev, slow_prev, fast, slow)
    if side == "long":
        if settings.rsi_long_min < rsi_now < settings.rsi_long_max:
            return "long"
        return None
    if side == "short":
        if settings.rsi_short_min < rsi_now < settings.rsi_short_max:
            return "short"
        return None
    return None


def signals_for_closes(
    closes: Sequence[float], settings: Settings
) -> list[Optional[Side]]:
    """Entry signal for every bar of a completed-bar close series (spec section 3).

    Index i is the signal evaluated on the close of bar i. Index 0 has no
    previous bar, so it is always None.
    """
    fast = ema(closes, settings.ema_fast_period)
    slow = ema(closes, settings.ema_slow_period)
    rsi = rsi_wilder(closes, settings.rsi_period)
    out: list[Optional[Side]] = [None] * len(closes)
    for i in range(1, len(closes)):
        out[i] = entry_signal(fast[i - 1], slow[i - 1], fast[i], slow[i], rsi[i], settings)
    return out


# --- Spread gate (spec section 7) ------------------------------------------


def spread_within_limit(spread_pips: float, settings: Settings) -> bool:
    """True when the spread at fill is not greater than MAX_SPREAD_PIPS."""
    return spread_pips <= settings.max_spread_pips


# --- Exit rules (spec section 4) -------------------------------------------


def stop_price(side: Side, entry_price: float, settings: Settings) -> float:
    """Stop-loss level: STOP_LOSS_PIPS x PIP_SIZE from entry (section 4.1)."""
    distance = settings.stop_loss_pips * settings.pip_size
    return entry_price - distance if side == "long" else entry_price + distance


def target_price(side: Side, entry_price: float, settings: Settings) -> float:
    """Take-profit level: TAKE_PROFIT_PIPS x PIP_SIZE from entry (section 4.2)."""
    distance = settings.take_profit_pips * settings.pip_size
    return entry_price + distance if side == "long" else entry_price - distance


def bar_price_exit(
    side: Side, entry_price: float, bar: Bar, settings: Settings
) -> Optional[str]:
    """Stop-loss / take-profit touched inside a bar (spec sections 4.1 and 4.2).

    If both levels fall inside the same bar, the stop is assumed first
    (section 4.2, conservative). Returns "stop_loss", "take_profit" or None.
    """
    stop = stop_price(side, entry_price, settings)
    target = target_price(side, entry_price, settings)
    if side == "long":
        stop_hit = bar.low <= stop
        target_hit = bar.high >= target
    else:
        stop_hit = bar.high >= stop
        target_hit = bar.low <= target
    if stop_hit:
        return "stop_loss"
    if target_hit:
        return "take_profit"
    return None


def max_hold_reached(entry_time: datetime, bar_time: datetime, settings: Settings) -> bool:
    """True at the first bar at or after MAX_HOLD_MINUTES from entry (section 4.3)."""
    return bar_time - entry_time >= timedelta(minutes=settings.max_hold_minutes)


def check_exits(
    side: Side,
    entry_price: float,
    entry_time: datetime,
    bar: Bar,
    fast_prev: Optional[float],
    slow_prev: Optional[float],
    fast: Optional[float],
    slow: Optional[float],
    forced_flat: bool,
    settings: Settings,
) -> Optional[str]:
    """Exit reason for one bar after entry, checked in spec section 4 order.

    1. stop_loss  2. take_profit  3. max_hold  4. opposite_cross  5. forced_flat.
    `forced_flat` is computed by the caller from session.is_forced_flat (section 8).
    Returns the exit_reason string used in the trade log (section 11), or None.
    """
    price_exit = bar_price_exit(side, entry_price, bar, settings)
    if price_exit is not None:
        return price_exit
    if max_hold_reached(entry_time, bar.time, settings):
        return "max_hold"
    cross = crossover(fast_prev, slow_prev, fast, slow)
    opposite = "short" if side == "long" else "long"
    if cross == opposite:
        return "opposite_cross"
    if forced_flat:
        return "forced_flat"
    return None

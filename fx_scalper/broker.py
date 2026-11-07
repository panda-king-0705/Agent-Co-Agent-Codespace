"""Abstract broker interface for the FX scalper (practice endpoint only).

No concrete OANDA client is implemented here. Any implementation must be
constructed from Settings and must refuse to run when live_mode is True
unless a later, separately approved task allows it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Quote:
    instrument: str
    bid: float
    ask: float
    time_utc: str  # ISO-8601 UTC

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    instrument: str
    units: int  # positive = long, negative = short
    fill_price: float


class Broker(ABC):
    """Minimal broker contract used by the strategy and the paper runner."""

    @abstractmethod
    def get_quote(self, instrument: str) -> Quote:
        """Return the current bid/ask for an instrument."""

    @abstractmethod
    def place_market_order(self, instrument: str, units: int) -> OrderResult:
        """Submit a market order. units > 0 buys, units < 0 sells."""

    @abstractmethod
    def close_position(self, instrument: str) -> OrderResult | None:
        """Close any open position in the instrument. Return None if flat."""

    @abstractmethod
    def open_position_units(self, instrument: str) -> int:
        """Return the net open units for the instrument (0 if flat)."""

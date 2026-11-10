"""OANDA v20 practice adapter for the FX scalper broker interface (spec section 15).

Practice endpoint only. Credentials come only from Settings (environment-backed).
All HTTP goes through an injectable transport, so tests never touch the network.
Nothing here logs or prints the token or the account id.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fx_scalper.broker import Broker, OrderResult, Quote
from fx_scalper.settings import PRACTICE_HOST, Settings, SettingsError

# transport(method, url, headers, json_body_or_None) -> (http_status, parsed_json)
Transport = Callable[[str, str, Mapping[str, str], Optional[dict]], tuple]


class LiveModeRefusedError(RuntimeError):
    """Raised when the adapter is asked to run against live mode or a non-practice host."""


class OandaApiError(RuntimeError):
    """Raised when OANDA returns a non-2xx response."""

    def __init__(self, status: int, message: str):
        super().__init__(f"OANDA API error {status}: {message}")
        self.status = status


class OrderRejectedError(RuntimeError):
    """Raised when OANDA accepts a request but does not fill it."""


# OANDA returns at most 5000 candles per request.
MAX_CANDLES_PER_REQUEST = 5000


@dataclass(frozen=True)
class Candle:
    """One completed candle. Prices are mid; spread_pips is ask minus bid at the bar open."""

    time: datetime  # bar open, timezone-aware UTC
    open: float
    high: float
    low: float
    close: float
    spread_pips: Optional[float]
    volume: int


def _rfc3339(when: datetime) -> str:
    if when.tzinfo is None:
        raise ValueError("candle times must be timezone-aware (UTC)")
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_oanda_time(text: str) -> datetime:
    """Parse OANDA RFC3339 times, which carry nanoseconds (9 digits) that fromisoformat rejects."""
    text = re.sub(r"(\.\d{6})\d+", r"\1", text.replace("Z", "+00:00"))
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _candle_from_payload(raw: Mapping, pip_size: float) -> Candle:
    mid = raw["mid"]
    spread = None
    if "bid" in raw and "ask" in raw:
        spread = round((float(raw["ask"]["o"]) - float(raw["bid"]["o"])) / pip_size, 4)
    return Candle(
        time=_parse_oanda_time(raw["time"]),
        open=float(mid["o"]),
        high=float(mid["h"]),
        low=float(mid["l"]),
        close=float(mid["c"]),
        spread_pips=spread,
        volume=int(raw.get("volume", 0)),
    )


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], body: Optional[dict]):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"errorMessage": raw[:200]}
        return exc.code, payload


class OandaPracticeBroker(Broker):
    """Implements the round-1 Broker interface against the OANDA v20 practice API."""

    def __init__(self, settings: Settings, transport: Optional[Transport] = None):
        if settings.live_mode:
            raise LiveModeRefusedError(
                "live mode is on, but this adapter is practice-only; "
                "live trading needs a separately approved task (broker.py, spec section 15)"
            )
        if settings.host != PRACTICE_HOST:
            raise LiveModeRefusedError("configured host is not the OANDA practice endpoint; refusing")
        if not settings.api_token or not settings.account_id:
            raise SettingsError("OANDA practice credentials are missing (OANDA_API_TOKEN, OANDA_ACCOUNT_ID)")
        self._host = PRACTICE_HOST
        self._account_id = settings.account_id
        self._headers = {
            "Authorization": f"Bearer {settings.api_token}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }
        self._transport: Transport = transport or _urllib_transport

    def __repr__(self) -> str:
        return f"OandaPracticeBroker(host={self._host!r}, account=<redacted>)"

    # --- internals -------------------------------------------------------

    def _account_path(self, suffix: str) -> str:
        return f"/v3/accounts/{self._account_id}{suffix}"

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        status, payload = self._transport(method, f"{self._host}{path}", self._headers, body)
        if not 200 <= status < 300:
            message = str(payload.get("errorMessage", "request failed"))
            raise OandaApiError(status, message)
        return payload

    # --- Market data --------------------------------------------------------

    def get_candles(
        self,
        instrument: str,
        granularity: str,
        start: datetime,
        end: datetime,
        pip_size: float = 0.0001,
    ) -> list[Candle]:
        """Return completed candles with start <= open time < end, oldest first.

        Requests mid, bid and ask (price=MBA) so each candle carries a real spread.
        Pages through the range 5000 candles at a time. Incomplete candles are dropped.

        Every page is requested with includeFirst=true, so each page after the first starts with
        the previous page's last candle (dropped as a duplicate). With includeFirst=false OANDA
        returns only count-1 candles, which made every page after the first look like the end of
        the data and cut each download off at 9,999 candles.
        """
        if end <= start:
            raise ValueError("end must be after start")
        end_utc = end.astimezone(timezone.utc)
        cursor = _rfc3339(start)
        candles: list[Candle] = []
        while True:
            query = urllib.parse.urlencode(
                {
                    "granularity": granularity,
                    "price": "MBA",
                    "from": cursor,
                    "count": MAX_CANDLES_PER_REQUEST,
                    "includeFirst": "true",
                }
            )
            payload = self._call("GET", f"/v3/instruments/{instrument}/candles?{query}")
            page = payload.get("candles", [])
            if not page:
                break
            for raw in page:
                if not raw.get("complete", False):
                    continue
                candle = _candle_from_payload(raw, pip_size)
                if candle.time >= end_utc:
                    return candles
                if not candles or candle.time > candles[-1].time:
                    candles.append(candle)
            last_time = _parse_oanda_time(page[-1]["time"])
            if last_time >= end_utc or len(page) < MAX_CANDLES_PER_REQUEST:
                break
            next_cursor = _rfc3339(last_time)
            if next_cursor == cursor:
                break  # no progress; guard against an endless loop
            cursor = next_cursor
        return candles

    # --- Broker interface -------------------------------------------------

    def get_quote(self, instrument: str) -> Quote:
        payload = self._call("GET", self._account_path(f"/pricing?instruments={instrument}"))
        matches = [p for p in payload.get("prices", []) if p.get("instrument") == instrument]
        if not matches:
            raise LookupError(f"no price returned for {instrument}")
        p = matches[0]
        return Quote(
            instrument=instrument,
            bid=float(p["bids"][0]["price"]),
            ask=float(p["asks"][0]["price"]),
            time_utc=p["time"],
        )

    def place_market_order(self, instrument: str, units: int) -> OrderResult:
        if units == 0:
            raise ValueError("units must be non-zero")
        body = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }
        payload = self._call("POST", self._account_path("/orders"), body)
        fill = payload.get("orderFillTransaction")
        if not fill:
            reason = payload.get("orderRejectTransaction", {}).get("rejectReason", "unknown")
            raise OrderRejectedError(f"market order for {instrument} not filled: {reason}")
        return OrderResult(
            order_id=str(fill["id"]),
            instrument=instrument,
            units=int(float(fill["units"])),
            fill_price=float(fill["price"]),
        )

    def close_position(self, instrument: str) -> Optional[OrderResult]:
        units = self.open_position_units(instrument)
        if units == 0:
            return None
        side = "longUnits" if units > 0 else "shortUnits"
        payload = self._call(
            "PUT", self._account_path(f"/positions/{instrument}/close"), {side: "ALL"}
        )
        fill = payload.get("longOrderFillTransaction" if units > 0 else "shortOrderFillTransaction")
        if not fill:
            raise OrderRejectedError(f"close of {instrument} not filled")
        return OrderResult(
            order_id=str(fill["id"]),
            instrument=instrument,
            units=int(float(fill["units"])),
            fill_price=float(fill["price"]),
        )

    def open_position_units(self, instrument: str) -> int:
        payload = self._call("GET", self._account_path("/openPositions"))
        for pos in payload.get("positions", []):
            if pos.get("instrument") == instrument:
                long_units = float(pos.get("long", {}).get("units", "0"))
                short_units = float(pos.get("short", {}).get("units", "0"))
                return int(long_units + short_units)
        return 0

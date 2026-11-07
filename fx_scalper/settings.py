"""Runtime settings for the FX scalper (spec sections 14 and 15).

Credentials are read only from environment variables. There are no defaults
for the token or account id, and nothing is read from files. Error messages
name the variable but never print its value.

Strategy parameters carry the defaults from docs/fx-scalper/spec.md section 14.
They are plain fields with defaults, so load_settings() needs no extra input.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

PRACTICE_HOST = "https://api-fxpractice.oanda.com"

ENV_TOKEN = "OANDA_API_TOKEN"
ENV_ACCOUNT_ID = "OANDA_ACCOUNT_ID"
ENV_LIVE_MODE = "FX_SCALPER_LIVE_MODE"


class SettingsError(RuntimeError):
    """Raised when required settings are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    api_token: str = field(repr=False)
    account_id: str = field(repr=False)
    live_mode: bool = False
    host: str = PRACTICE_HOST
    # Strategy parameters (spec section 14 keys, lower-cased).
    instrument: str = "EUR_USD"  # section 1
    pip_size: float = 0.0001  # section 1
    ema_fast_period: int = 9  # section 2
    ema_slow_period: int = 21  # section 2
    rsi_period: int = 14  # section 2
    rsi_long_min: float = 50.0  # section 3
    rsi_long_max: float = 70.0  # section 3
    rsi_short_min: float = 30.0  # section 3
    rsi_short_max: float = 50.0  # section 3
    stop_loss_pips: float = 4.0  # section 4 (design parameter)
    take_profit_pips: float = 6.0  # section 4 (design parameter)
    max_hold_minutes: int = 15  # section 4 (design parameter)
    session_start_utc: str = "13:00"  # section 5
    session_end_utc: str = "16:00"  # section 5
    blackout_file: str = "config/blackout.json"  # section 6
    news_pause_minutes: int = 15  # section 6
    max_spread_pips: float = 1.5  # section 7 (design parameter)
    no_entry_friday_utc: str = "20:00"  # section 8
    forced_flat_friday_utc: str = "20:45"  # section 8


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value.strip():
        raise SettingsError(f"required environment variable {name} is not set")
    return value.strip()


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from the environment.

    `env` defaults to os.environ; pass a mapping in tests.
    Live mode is on only when FX_SCALPER_LIVE_MODE is exactly "true".
    Any other value, including unset, leaves live mode off.
    """
    source = os.environ if env is None else env
    token = _required(source, ENV_TOKEN)
    account_id = _required(source, ENV_ACCOUNT_ID)
    live_mode = source.get(ENV_LIVE_MODE) == "true"
    return Settings(api_token=token, account_id=account_id, live_mode=live_mode)

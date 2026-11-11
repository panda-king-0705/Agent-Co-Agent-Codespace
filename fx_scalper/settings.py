"""Runtime settings for the FX scalper (spec sections 14 and 15).

Credentials come from environment variables or, failing that, from a `.env` file at the
repository root (git ignores it; see .env.example). A variable set in the environment
always wins over the file. Only the two credential variables are read from `.env`:
live mode can never be switched on from a file. There are no defaults for the token or
account id. Error messages name the variable but never print its value.

Strategy parameters carry the defaults from docs/fx-scalper/spec.md section 14.
They are plain fields with defaults, so load_settings() needs no extra input.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

PRACTICE_HOST = "https://api-fxpractice.oanda.com"

ENV_TOKEN = "OANDA_API_TOKEN"
ENV_ACCOUNT_ID = "OANDA_ACCOUNT_ID"
ENV_LIVE_MODE = "FX_SCALPER_LIVE_MODE"

DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"
DOTENV_KEYS = (ENV_TOKEN, ENV_ACCOUNT_ID)


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


def read_dotenv(path: str | Path, keys: tuple[str, ...] = DOTENV_KEYS) -> dict[str, str]:
    """Credential variables from a .env file: KEY=VALUE lines, # comments, optional quotes.

    A missing file gives {}. Keys other than `keys` (default DOTENV_KEYS) are ignored. Malformed lines are
    skipped silently so a value can never end up in an error message.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key in keys:
            values[key] = value
    return values


def load_settings(env: Mapping[str, str] | None = None, dotenv_path: str | Path | None = None) -> Settings:
    """Build Settings from the environment, falling back to `.env` for the credentials.

    With `env` given (tests), only that mapping is used unless `dotenv_path` is passed too.
    Otherwise os.environ is used, with DOTENV_PATH filling in credentials it doesn't set.
    Live mode is on only when FX_SCALPER_LIVE_MODE is exactly "true" in the environment.
    Any other value, including unset, leaves live mode off.
    """
    environment = os.environ if env is None else env
    path = DOTENV_PATH if env is None and dotenv_path is None else dotenv_path
    source = dict(environment)
    if path is not None:
        for key, value in read_dotenv(path).items():
            if not source.get(key, "").strip():
                source[key] = value
    token = _required(source, ENV_TOKEN)
    account_id = _required(source, ENV_ACCOUNT_ID)
    live_mode = source.get(ENV_LIVE_MODE) == "true"
    return Settings(api_token=token, account_id=account_id, live_mode=live_mode)

"""Runtime settings for the FX scalper (spec section 15).

Credentials are read only from environment variables. There are no defaults
for the token or account id, and nothing is read from files. Error messages
name the variable but never print its value.
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

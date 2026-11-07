import pytest

from fx_scalper.settings import (
    ENV_ACCOUNT_ID,
    ENV_LIVE_MODE,
    ENV_TOKEN,
    PRACTICE_HOST,
    SettingsError,
    load_settings,
)

GOOD = {ENV_TOKEN: "tok-secret", ENV_ACCOUNT_ID: "101-001-1234567-001"}


def test_loads_token_and_account_from_env():
    s = load_settings(GOOD)
    assert s.api_token == "tok-secret"
    assert s.account_id == "101-001-1234567-001"
    assert s.host == PRACTICE_HOST


def test_live_mode_off_by_default():
    assert load_settings(GOOD).live_mode is False


@pytest.mark.parametrize("value", ["1", "True", "yes", "", "false"])
def test_live_mode_only_on_exact_true(value):
    env = {**GOOD, ENV_LIVE_MODE: value}
    assert load_settings(env).live_mode is False


def test_live_mode_on_when_exact_true():
    env = {**GOOD, ENV_LIVE_MODE: "true"}
    assert load_settings(env).live_mode is True


@pytest.mark.parametrize("missing", [ENV_TOKEN, ENV_ACCOUNT_ID])
def test_missing_credential_raises_and_names_variable(missing):
    env = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(SettingsError) as exc:
        load_settings(env)
    assert missing in str(exc.value)


@pytest.mark.parametrize("missing", [ENV_TOKEN, ENV_ACCOUNT_ID])
def test_blank_credential_is_rejected(missing):
    env = {**GOOD, missing: "   "}
    with pytest.raises(SettingsError):
        load_settings(env)


def test_token_not_shown_in_repr():
    s = load_settings(GOOD)
    assert "tok-secret" not in repr(s)
    assert "101-001-1234567-001" not in repr(s)


def test_error_message_does_not_contain_values():
    env = {ENV_TOKEN: "tok-secret", ENV_ACCOUNT_ID: "  "}
    with pytest.raises(SettingsError) as exc:
        load_settings(env)
    assert "tok-secret" not in str(exc.value)


def test_no_defaults_for_credentials():
    with pytest.raises(SettingsError):
        load_settings({})

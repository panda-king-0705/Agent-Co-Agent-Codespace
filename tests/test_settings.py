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


# --- .env file -----------------------------------------------------------------------------

from fx_scalper import settings as settings_module  # noqa: E402
from fx_scalper.settings import read_dotenv  # noqa: E402


def write_env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_dotenv_fills_missing_credentials(tmp_path):
    path = write_env(tmp_path, f"# comment\n{ENV_TOKEN}=file-token\nexport {ENV_ACCOUNT_ID}='101-001-7654321-001'\n")
    s = load_settings({}, dotenv_path=path)
    assert (s.api_token, s.account_id) == ("file-token", "101-001-7654321-001")


def test_environment_wins_over_dotenv(tmp_path):
    path = write_env(tmp_path, f"{ENV_TOKEN}=file-token\n{ENV_ACCOUNT_ID}=file-account\n")
    s = load_settings({ENV_TOKEN: "env-token", ENV_ACCOUNT_ID: "  "}, dotenv_path=path)
    assert (s.api_token, s.account_id) == ("env-token", "file-account")


def test_dotenv_cannot_switch_on_live_mode(tmp_path):
    path = write_env(tmp_path, f"{ENV_TOKEN}=t\n{ENV_ACCOUNT_ID}=a\n{ENV_LIVE_MODE}=true\n")
    assert load_settings({}, dotenv_path=path).live_mode is False
    assert ENV_LIVE_MODE not in read_dotenv(path)


def test_missing_dotenv_still_requires_credentials(tmp_path):
    with pytest.raises(SettingsError):
        load_settings({}, dotenv_path=tmp_path / "absent.env")


def test_explicit_env_mapping_ignores_default_dotenv(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "DOTENV_PATH", write_env(tmp_path, f"{ENV_TOKEN}=t\n{ENV_ACCOUNT_ID}=a\n"))
    with pytest.raises(SettingsError):
        load_settings({})


def test_default_load_reads_dotenv_when_environment_lacks_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.delenv(ENV_ACCOUNT_ID, raising=False)
    monkeypatch.setattr(settings_module, "DOTENV_PATH", write_env(tmp_path, f"{ENV_TOKEN}=t\n{ENV_ACCOUNT_ID}=a\n"))
    s = load_settings()
    assert (s.api_token, s.account_id) == ("t", "a")

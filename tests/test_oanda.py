"""Tests for fx_scalper/oanda.py. All HTTP is replaced by a fake transport; no network calls."""

import pytest

from fx_scalper import oanda
from fx_scalper.oanda import (
    LiveModeRefusedError,
    OandaApiError,
    OandaPracticeBroker,
    OrderRejectedError,
)
from fx_scalper.settings import PRACTICE_HOST, Settings, SettingsError, load_settings

TOKEN = "test-token-not-real"
ACCOUNT = "101-000-0000000-001"


def practice_settings(**overrides):
    base = dict(api_token=TOKEN, account_id=ACCOUNT, live_mode=False, host=PRACTICE_HOST)
    base.update(overrides)
    return Settings(**base)


class FakeTransport:
    """Records every call and returns scripted (status, payload) responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        return self.responses.pop(0)


def test_refuses_live_mode_on():
    with pytest.raises(LiveModeRefusedError):
        OandaPracticeBroker(practice_settings(live_mode=True), transport=FakeTransport([]))


def test_refuses_non_practice_host():
    with pytest.raises(LiveModeRefusedError):
        OandaPracticeBroker(
            practice_settings(host="https://api-fxtrade.oanda.com"), transport=FakeTransport([])
        )


def test_missing_credentials_raise_settings_error(monkeypatch):
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    with pytest.raises(SettingsError) as exc:
        load_settings({})
    assert "OANDA_API_TOKEN" in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_empty_token_in_settings_is_rejected():
    with pytest.raises(SettingsError):
        OandaPracticeBroker(practice_settings(api_token=""), transport=FakeTransport([]))


def test_settings_load_from_env_mapping_and_live_flag_is_exact():
    env = {"OANDA_API_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": ACCOUNT}
    s = load_settings(env)
    assert s.live_mode is False
    assert load_settings({**env, "FX_SCALPER_LIVE_MODE": "TRUE"}).live_mode is False
    assert load_settings({**env, "FX_SCALPER_LIVE_MODE": "true"}).live_mode is True


def test_repr_does_not_leak_token_or_account():
    broker = OandaPracticeBroker(practice_settings(), transport=FakeTransport([]))
    text = repr(broker) + repr(practice_settings())
    assert TOKEN not in text
    assert ACCOUNT not in text


def test_get_quote_uses_practice_host_and_parses_bid_ask():
    payload = {
        "prices": [
            {
                "instrument": "EUR_USD",
                "time": "2026-11-09T13:00:00.000000000Z",
                "bids": [{"price": "1.10000"}],
                "asks": [{"price": "1.10010"}],
            }
        ]
    }
    t = FakeTransport([(200, payload)])
    broker = OandaPracticeBroker(practice_settings(), transport=t)
    q = broker.get_quote("EUR_USD")
    method, url, headers, body = t.calls[0]
    assert method == "GET"
    assert url.startswith(PRACTICE_HOST + f"/v3/accounts/{ACCOUNT}/pricing")
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert body is None
    assert q.bid == pytest.approx(1.10000)
    assert q.ask == pytest.approx(1.10010)
    assert q.spread == pytest.approx(0.0001)


def test_get_quote_missing_instrument_raises_lookup_error():
    t = FakeTransport([(200, {"prices": []})])
    with pytest.raises(LookupError):
        OandaPracticeBroker(practice_settings(), transport=t).get_quote("EUR_USD")


def test_place_market_order_buy_returns_fill():
    payload = {
        "orderFillTransaction": {"id": "42", "units": "1000", "price": "1.10010"}
    }
    t = FakeTransport([(201, payload)])
    res = OandaPracticeBroker(practice_settings(), transport=t).place_market_order("EUR_USD", 1000)
    method, url, _, body = t.calls[0]
    assert method == "POST"
    assert url == PRACTICE_HOST + f"/v3/accounts/{ACCOUNT}/orders"
    assert body["order"]["units"] == "1000"
    assert body["order"]["type"] == "MARKET"
    assert res.order_id == "42"
    assert res.units == 1000
    assert res.fill_price == pytest.approx(1.10010)


def test_place_market_order_sell_uses_negative_units():
    payload = {"orderFillTransaction": {"id": "43", "units": "-500", "price": "1.10000"}}
    t = FakeTransport([(201, payload)])
    res = OandaPracticeBroker(practice_settings(), transport=t).place_market_order("EUR_USD", -500)
    assert t.calls[0][3]["order"]["units"] == "-500"
    assert res.units == -500


def test_place_market_order_zero_units_rejected_without_http():
    t = FakeTransport([])
    with pytest.raises(ValueError):
        OandaPracticeBroker(practice_settings(), transport=t).place_market_order("EUR_USD", 0)
    assert t.calls == []


def test_unfilled_order_raises_order_rejected():
    payload = {"orderRejectTransaction": {"rejectReason": "INSUFFICIENT_MARGIN"}}
    t = FakeTransport([(201, payload)])
    with pytest.raises(OrderRejectedError) as exc:
        OandaPracticeBroker(practice_settings(), transport=t).place_market_order("EUR_USD", 1000)
    assert "INSUFFICIENT_MARGIN" in str(exc.value)


def test_http_error_raises_oanda_api_error_without_token():
    t = FakeTransport([(401, {"errorMessage": "Insufficient authorization"})])
    with pytest.raises(OandaApiError) as exc:
        OandaPracticeBroker(practice_settings(), transport=t).get_quote("EUR_USD")
    assert exc.value.status == 401
    assert TOKEN not in str(exc.value)


def test_open_position_units_net_long_and_flat():
    payload = {
        "positions": [
            {"instrument": "EUR_USD", "long": {"units": "700"}, "short": {"units": "0"}},
            {"instrument": "GBP_USD", "long": {"units": "0"}, "short": {"units": "-300"}},
        ]
    }
    t = FakeTransport([(200, payload), (200, {"positions": []})])
    broker = OandaPracticeBroker(practice_settings(), transport=t)
    assert broker.open_position_units("EUR_USD") == 700
    assert broker.open_position_units("AUD_USD") == 0


def test_close_position_when_flat_makes_no_close_call():
    t = FakeTransport([(200, {"positions": []})])
    assert OandaPracticeBroker(practice_settings(), transport=t).close_position("EUR_USD") is None
    assert len(t.calls) == 1  # only the openPositions lookup


def test_close_long_position_sends_longUnits_all():
    positions = {
        "positions": [{"instrument": "EUR_USD", "long": {"units": "1000"}, "short": {"units": "0"}}]
    }
    close_payload = {"longOrderFillTransaction": {"id": "99", "units": "-1000", "price": "1.10000"}}
    t = FakeTransport([(200, positions), (200, close_payload)])
    res = OandaPracticeBroker(practice_settings(), transport=t).close_position("EUR_USD")
    method, url, _, body = t.calls[1]
    assert method == "PUT"
    assert url.endswith(f"/v3/accounts/{ACCOUNT}/positions/EUR_USD/close")
    assert body == {"longUnits": "ALL"}
    assert res.order_id == "99"
    assert res.units == -1000


def test_adapter_implements_every_broker_method():
    from fx_scalper.broker import Broker

    abstract = Broker.__abstractmethods__
    assert abstract <= set(dir(OandaPracticeBroker))
    assert not getattr(OandaPracticeBroker, "__abstractmethods__", frozenset())


def test_module_makes_no_real_network_calls(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(oanda.urllib.request, "urlopen", boom)
    t = FakeTransport([(200, {"positions": []})])
    OandaPracticeBroker(practice_settings(), transport=t).open_position_units("EUR_USD")

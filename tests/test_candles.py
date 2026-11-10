"""Tests for OandaPracticeBroker.get_candles and scripts/fetch_candles.py. No network calls."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from fx_scalper import oanda
from fx_scalper.oanda import Candle, OandaApiError, OandaPracticeBroker
from fx_scalper.settings import PRACTICE_HOST, Settings
from scripts.backtest import read_bars
from scripts.fetch_candles import parse_utc, write_candles

T0 = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        return self.responses.pop(0)


def broker(responses):
    settings = Settings(api_token="test-token", account_id="101-000-0000000-001", host=PRACTICE_HOST)
    transport = FakeTransport(responses)
    return OandaPracticeBroker(settings, transport=transport), transport


def raw_candle(when, mid=1.10000, spread=0.00010, complete=True, nanos=True):
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S") + (".000000000Z" if nanos else "Z")
    half = spread / 2
    ohlc = lambda p: {"o": f"{p:.5f}", "h": f"{p + 0.0002:.5f}", "l": f"{p - 0.0002:.5f}", "c": f"{p + 0.0001:.5f}"}
    return {
        "complete": complete,
        "time": stamp,
        "volume": 12,
        "mid": ohlc(mid),
        "bid": ohlc(mid - half),
        "ask": ohlc(mid + half),
    }


def query(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_requests_mid_bid_ask_on_practice_instruments_endpoint():
    b, t = broker([(200, {"candles": [raw_candle(T0)]})])
    b.get_candles("EUR_USD", "M1", T0, T0 + timedelta(minutes=5))
    method, url, headers, body = t.calls[0]
    assert method == "GET" and body is None
    assert url.startswith(f"{PRACTICE_HOST}/v3/instruments/EUR_USD/candles?")
    q = query(url)
    assert q["granularity"] == "M1" and q["price"] == "MBA"
    assert q["from"] == "2026-11-02T13:00:00Z"
    assert q["count"] == str(oanda.MAX_CANDLES_PER_REQUEST)
    assert q["includeFirst"] == "true"


def test_parses_mid_prices_spread_and_nanosecond_times():
    b, _ = broker([(200, {"candles": [raw_candle(T0, mid=1.08500, spread=0.00012)]})])
    (c,) = b.get_candles("EUR_USD", "M1", T0, T0 + timedelta(minutes=5))
    assert c.time == T0 and c.time.tzinfo is not None
    assert (c.open, c.high, c.low, c.close) == (1.085, 1.0852, 1.0848, 1.0851)
    assert c.spread_pips == pytest.approx(1.2)
    assert c.volume == 12


def test_drops_incomplete_candles():
    page = [raw_candle(T0), raw_candle(T0 + timedelta(minutes=1), complete=False)]
    b, _ = broker([(200, {"candles": page})])
    candles = b.get_candles("EUR_USD", "M1", T0, T0 + timedelta(minutes=5))
    assert [c.time for c in candles] == [T0]


def test_stops_at_end_exclusive():
    page = [raw_candle(T0 + timedelta(minutes=i)) for i in range(5)]
    b, t = broker([(200, {"candles": page})])
    candles = b.get_candles("EUR_USD", "M1", T0, T0 + timedelta(minutes=3))
    assert [c.time for c in candles] == [T0 + timedelta(minutes=i) for i in range(3)]
    assert len(t.calls) == 1


def test_pages_through_full_pages(monkeypatch):
    monkeypatch.setattr(oanda, "MAX_CANDLES_PER_REQUEST", 3)
    first = [raw_candle(T0 + timedelta(minutes=i)) for i in range(3)]
    second = [raw_candle(T0 + timedelta(minutes=i)) for i in range(2, 4)]  # repeats minute 2
    b, t = broker([(200, {"candles": first}), (200, {"candles": second})])
    candles = b.get_candles("EUR_USD", "M1", T0, T0 + timedelta(hours=1))
    assert [c.time for c in candles] == [T0 + timedelta(minutes=i) for i in range(4)]
    assert len(t.calls) == 2
    q = query(t.calls[1][1])
    assert q["from"] == "2026-11-02T13:02:00Z" and q["includeFirst"] == "true"


class OandaLikeServer:
    """Serves a fixed candle series with OANDA's from/count/includeFirst semantics.

    includeFirst=false drops the candle at `from` and still counts it, so the page holds
    count-1 candles; that is the behaviour that truncated the real downloads.
    """

    def __init__(self, times):
        self.times = times
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append(url)
        q = query(url)
        start = datetime.fromisoformat(q["from"].replace("Z", "+00:00"))
        page = [t for t in self.times if t >= start][: int(q["count"])]
        if q["includeFirst"] == "false" and page and page[0] == start:
            page = page[1:]
        return 200, {"candles": [raw_candle(t) for t in page]}


def test_downloads_every_candle_across_many_pages(monkeypatch):
    monkeypatch.setattr(oanda, "MAX_CANDLES_PER_REQUEST", 5)
    times = [T0 + timedelta(minutes=i) for i in range(23)]
    server = OandaLikeServer(times)
    settings = Settings(api_token="test-token", account_id="101-000-0000000-001", host=PRACTICE_HOST)
    b = OandaPracticeBroker(settings, transport=server)
    candles = b.get_candles("EUR_USD", "M1", T0, T0 + timedelta(days=1))
    assert [c.time for c in candles] == times
    assert len(server.calls) == 6  # 5 full pages (4 new candles each after the first) + 1 short


def test_empty_page_ends_download():
    b, t = broker([(200, {"candles": []})])
    assert b.get_candles("EUR_USD", "M1", T0, T0 + timedelta(days=1)) == []
    assert len(t.calls) == 1


def test_api_error_is_raised_without_token():
    b, _ = broker([(400, {"errorMessage": "Invalid value specified for 'granularity'"})])
    with pytest.raises(OandaApiError) as exc:
        b.get_candles("EUR_USD", "X9", T0, T0 + timedelta(minutes=5))
    assert exc.value.status == 400 and "test-token" not in str(exc.value)


def test_rejects_bad_ranges():
    b, _ = broker([])
    with pytest.raises(ValueError):
        b.get_candles("EUR_USD", "M1", T0, T0)
    with pytest.raises(ValueError):
        b.get_candles("EUR_USD", "M1", T0.replace(tzinfo=None), T0.replace(tzinfo=None) + timedelta(minutes=1))


def test_parse_utc_treats_naive_as_utc():
    assert parse_utc("2026-05-01") == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert parse_utc("2026-05-01T08:00:00+08:00") == datetime(2026, 5, 1, tzinfo=timezone.utc)


def test_written_csv_round_trips_into_backtest_reader(tmp_path):
    candles = [
        Candle(T0, 1.1, 1.1002, 1.0998, 1.1001, 1.2, 10),
        Candle(T0 + timedelta(minutes=1), 1.1001, 1.1003, 1.0999, 1.1002, None, 8),
    ]
    out = tmp_path / "nested" / "candles.csv"
    assert write_candles(candles, out) == 2
    bars = read_bars(out)
    assert [b.time for b in bars] == [c.time for c in candles]
    assert bars[0].close == pytest.approx(1.1001)
    assert bars[0].spread_pips == pytest.approx(1.2)
    assert bars[1].spread_pips is None


# --- multi-instrument download (scripts/fetch_candles.py) --------------------

from scripts import fetch_candles as fc


class FakeBroker:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def get_candles(self, instrument, granularity, start, end, pip_size=0.0001):
        self.calls.append((instrument, granularity, start, end, pip_size))
        if instrument in self.fail:
            raise OandaApiError(400, "boom")
        return [Candle(start, 1.1, 1.2, 1.0, 1.15, 1.0, 5)]


def test_pip_size_is_hundredth_for_jpy_quoted_pairs():
    assert fc.pip_size("EUR_USD") == 0.0001
    assert fc.pip_size("GBP_NZD") == 0.0001
    assert fc.pip_size("USD_JPY") == 0.01
    assert fc.pip_size("chf_jpy") == 0.01


def test_jpy_spread_measured_in_jpy_pips():
    b, _ = broker([(200, {"candles": [raw_candle(T0, mid=150.000, spread=0.012)]})])
    (c,) = b.get_candles("USD_JPY", "H1", T0, T0 + timedelta(hours=2), pip_size=fc.pip_size("USD_JPY"))
    assert c.spread_pips == pytest.approx(1.2)


def test_majors28_covers_each_currency_seven_times():
    pairs = fc.parse_instruments("majors28")
    assert len(pairs) == len(set(pairs)) == 28
    for ccy in ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]:
        assert sum(ccy in p.split("_") for p in pairs) == 7
    assert fc.parse_instruments(" eur_usd, usd_jpy ") == ["EUR_USD", "USD_JPY"]


def test_multi_download_writes_one_file_per_instrument_with_right_pip(tmp_path):
    fake = FakeBroker()
    fc.main(["--instruments", "EUR_USD,USD_JPY", "--granularity", "H1", "--start", "2010-01-01",
             "--end", "2010-02-01", "--out-dir", str(tmp_path)], broker=fake)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["EUR_USD_H1.csv", "USD_JPY_H1.csv"]
    assert [(c[0], c[4]) for c in fake.calls] == [("EUR_USD", 0.0001), ("USD_JPY", 0.01)]
    assert fake.calls[0][2] == datetime(2010, 1, 1, tzinfo=timezone.utc)


def test_skip_existing_and_failures_continue(tmp_path, capsys):
    (tmp_path / "EUR_USD_H1.csv").write_text("time,open,high,low,close,spread_pips\n")
    fake = FakeBroker(fail={"GBP_USD"})
    with pytest.raises(SystemExit) as exc:
        fc.main(["--instruments", "EUR_USD,GBP_USD,USD_JPY", "--granularity", "H1", "--start", "2010-01-01",
                 "--end", "2010-02-01", "--out-dir", str(tmp_path), "--skip-existing"], broker=fake)
    assert exc.value.code == 1
    assert [c[0] for c in fake.calls] == ["GBP_USD", "USD_JPY"]
    assert (tmp_path / "USD_JPY_H1.csv").exists() and not (tmp_path / "GBP_USD_H1.csv").exists()
    assert "1 failure(s): GBP_USD" in capsys.readouterr().out


def test_write_leaves_no_partial_file(tmp_path):
    out = tmp_path / "X_H1.csv"
    fc.write_candles([Candle(T0, 1.1, 1.2, 1.0, 1.15, None, 1)], out)
    assert [p.name for p in tmp_path.iterdir()] == ["X_H1.csv"]

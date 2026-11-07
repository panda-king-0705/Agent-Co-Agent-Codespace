import pytest

from fx_scalper.broker import Broker, OrderResult, Quote


def test_broker_is_abstract():
    with pytest.raises(TypeError):
        Broker()  # type: ignore[abstract]


def test_partial_implementation_cannot_be_instantiated():
    class Partial(Broker):
        def get_quote(self, instrument):
            raise NotImplementedError

    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def test_full_fake_broker_satisfies_contract():
    class Fake(Broker):
        def __init__(self):
            self.units = 0

        def get_quote(self, instrument):
            return Quote(instrument, 1.10000, 1.10010, "2026-11-09T13:00:00Z")

        def place_market_order(self, instrument, units):
            self.units += units
            return OrderResult("1", instrument, units, 1.10010 if units > 0 else 1.10000)

        def close_position(self, instrument):
            if self.units == 0:
                return None
            result = OrderResult("2", instrument, -self.units, 1.10000)
            self.units = 0
            return result

        def open_position_units(self, instrument):
            return self.units

    b = Fake()
    q = b.get_quote("EUR_USD")
    assert q.spread == pytest.approx(0.0001)
    assert b.open_position_units("EUR_USD") == 0
    b.place_market_order("EUR_USD", 1000)
    assert b.open_position_units("EUR_USD") == 1000
    closed = b.close_position("EUR_USD")
    assert closed.units == -1000
    assert b.close_position("EUR_USD") is None

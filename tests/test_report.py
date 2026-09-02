"""Offline test of the market check text (fake Redis)."""
import json

import pytest

from app import config, report


class FakeRedis:
    def __init__(self, states, evals):
        self.states, self.evals = states, evals

    def lrange(self, name, a, b):
        return [e.encode() for e in self.evals]

    def hgetall(self, name):
        return {k.encode(): json.dumps(v).encode() for k, v in self.states.items()}


def state(**kw):
    base = dict(candle_ts="2026-09-02T12:00:00+00:00", evaluated_at="2026-09-02T12:01:00+00:00", close=100.0, ema=95.0,
                st=92.0, dir=1, flip=False, adx=24.0, hh=101.0, market_ok=True, above_ema=True, dist_st_pct=8.0,
                dist_ema_pct=5.0, dist_hh_pct=-1.0, checks={}, blockers=["20-Candle-Hoch"], in_position=False, stop=None)
    base.update(kw)
    return base


def test_market_check_lists_conditions_and_blockers(monkeypatch):
    monkeypatch.setattr(config, "SYMBOLS", ["BTC/USD", "ETH/USD"])
    monkeypatch.setattr(config, "REGIME_SYMBOL", "BTC/USD")
    fake = FakeRedis({"BTC/USD": state(), "ETH/USD": state(in_position=True, dist_st_pct=3.2, blockers=[])},
                     ["2026-09-02T12:01:00+00:00"])
    monkeypatch.setattr(report, "_r", fake)
    text = report.market_check()
    assert "Regime BTC/USD: 🟢" in text
    assert "BTC/USD: 🟢 Uptrend, über EMA200 (+5.0 %), ADX 24, 20-Candle-Hoch -1.0 % – fehlt: 20-Candle-Hoch" in text
    assert "ETH/USD" in text and "in Position, Stop 3.2 % unter Kurs" in text


def test_market_check_ready_and_downtrend_and_regime_off(monkeypatch):
    monkeypatch.setattr(config, "SYMBOLS", ["BTC/USD", "ETH/USD"])
    monkeypatch.setattr(config, "REGIME_SYMBOL", "BTC/USD")
    fake = FakeRedis({"BTC/USD": state(dir=-1, above_ema=False, dist_st_pct=-2.5, dist_ema_pct=-1.0, blockers=["Supertrend grün"]),
                      "ETH/USD": state(blockers=[])}, [])
    monkeypatch.setattr(report, "_r", fake)
    text = report.market_check()
    assert "Regime BTC/USD: 🔴" in text
    assert "BTC/USD: 🔴 Downtrend" in text and "Flip auf grün braucht +2.5 % und Kurs über EMA" in text
    assert "ETH/USD" in text and "alle Bedingungen erfüllt" in text


def test_market_check_without_data(monkeypatch):
    monkeypatch.setattr(report, "_r", FakeRedis({}, []))
    assert "noch keine Auswertung" in report.market_check()

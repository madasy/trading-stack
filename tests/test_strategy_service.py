"""Offline test of the strategy service glue (no Redis/Postgres/network): fake Redis + monkeypatched db."""
import json

import numpy as np
import pandas as pd
import pytest

from app import config, strategy
from tests.conftest import make_ohlcv


class FakeRedis:
    def __init__(self):
        self.kv, self.h, self.lists = {}, {}, {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v.encode() if isinstance(v, str) else v

    def hset(self, name, key, value):
        self.h.setdefault(name, {})[key] = value

    def rpush(self, name, value):
        self.lists.setdefault(name, []).append(value)

    def ltrim(self, name, a, b):
        pass


@pytest.fixture
def svc(monkeypatch):
    fake = FakeRedis()
    signals = []
    monkeypatch.setattr(strategy, "r", fake)
    monkeypatch.setattr(strategy.db, "insert_signal", lambda sig, status="pending": signals.append((sig, status)) or len(signals))
    monkeypatch.setattr(strategy.db, "insert_decision", lambda *a: None)
    monkeypatch.setattr(strategy.db, "realized_pnl", lambda: 0.0)
    monkeypatch.setattr(strategy.db, "open_positions", lambda: [])
    monkeypatch.setattr(strategy.db, "open_position_for", lambda s: None)
    monkeypatch.setattr(strategy.db, "last_entry_signal", lambda s: None)
    monkeypatch.setattr(strategy.db, "update_stop", lambda *a, **k: None)
    return fake, signals


def state(fake, sym):
    return json.loads(fake.h[config.K_STATE][sym])


def test_entry_signal_in_clean_uptrend_and_idempotent(svc, trend_up):
    fake, signals = svc
    strategy.evaluate("BTC/USD", trend_up, market_ok=True)
    assert len(signals) == 1
    sig, status = signals[0]
    assert (sig.kind, sig.side, status) == ("entry", "buy", "pending")
    assert sig.stop < sig.price and sig.qty > 0
    assert sig.qty * sig.price <= config.PAPER_CAPITAL * config.MAX_NOTIONAL_PCT / 100 * 1.0001
    assert sig.reason.startswith("Trend-Breakout")
    assert sig.context["adx"] > 20 and sig.context["dist_hh_pct"] > 0 and sig.context["market_ok"] is True
    assert "evaluated_at" not in sig.context and sig.context["checks"]["breakout"] is True
    assert len(fake.lists[config.Q_SIGNALS]) == 1
    st = state(fake, "BTC/USD")
    assert st["blockers"] == [] and st["dir"] == 1 and st["adx"] > 20 and st["dist_hh_pct"] > 0
    strategy.evaluate("BTC/USD", trend_up, market_ok=True)       # same closed candle -> nothing new
    assert len(signals) == 1


def test_market_regime_blocks_other_symbols(svc, trend_up):
    fake, signals = svc
    strategy.evaluate("ETH/USD", trend_up, market_ok=False)
    assert signals == []
    st = state(fake, "ETH/USD")
    assert st["market_ok"] is False and any("Regime" in b for b in st["blockers"])


def test_no_signal_in_downtrend_with_reasons(svc, trend_down):
    fake, signals = svc
    strategy.evaluate("BTC/USD", trend_down, market_ok=True)
    assert signals == []
    st = state(fake, "BTC/USD")
    assert st["dir"] == -1 and "Supertrend grün" in st["blockers"]


def test_cooldown_after_rejected_signal(svc, trend_up, monkeypatch):
    fake, signals = svc
    monkeypatch.setattr(strategy.db, "last_entry_signal",
                        lambda s: {"status": "rejected", "candle_ts": trend_up["ts"].iloc[-3]})   # 2 candles ago
    strategy.evaluate("BTC/USD", trend_up, market_ok=True)
    assert signals == []
    fake.kv.clear()
    monkeypatch.setattr(strategy.db, "last_entry_signal",
                        lambda s: {"status": "rejected", "candle_ts": trend_up["ts"].iloc[-20]})  # long ago
    strategy.evaluate("BTC/USD", trend_up, market_ok=True)
    assert len(signals) == 1


def test_max_positions_blocks_entry(svc, trend_up, monkeypatch):
    fake, signals = svc
    monkeypatch.setattr(strategy.db, "open_positions", lambda: [{"symbol": "X"}] * config.MAX_POSITIONS)
    strategy.evaluate("BTC/USD", trend_up, market_ok=True)
    assert signals == []


def test_trailing_stop_and_exit(svc, monkeypatch):
    fake, signals = svc
    stops = []
    up = 100 * 1.003 ** np.arange(400)
    frame = make_ohlcv(up, spread=0.001)
    pos = {"id": 1, "symbol": "BTC/USD", "qty": 0.5, "entry_price": 100.0, "stop": 1.0,
           "opened_at": frame["ts"].iloc[-30] + pd.Timedelta(hours=1), "entry_signal_id": 1}
    monkeypatch.setattr(strategy.db, "open_position_for", lambda s: pos)
    monkeypatch.setattr(strategy.db, "update_stop", lambda pid, stop, **kw: stops.append((stop, kw)))
    strategy.evaluate("BTC/USD", frame, market_ok=True)
    assert signals == [] and len(stops) == 1 and stops[0][0] > 1.0        # stop trailed up, no exit
    assert stops[0][1]["old_stop"] == 1.0 and stops[0][1]["reason"] and stops[0][1]["candle_ts"]
    fake.kv.clear()
    crash = make_ohlcv(np.concatenate([up, up[-1] * 0.99 ** np.arange(1, 40)]), spread=0.001)
    strategy.evaluate("BTC/USD", crash, market_ok=True)
    assert len(signals) == 1
    sig, status = signals[0]
    assert (sig.kind, sig.side, status, sig.qty) == ("exit", "sell", "auto", 0.5)
    assert "Supertrend flipped red" in sig.reason
    assert sig.context["bars"] == 30 + 39 and sig.context["mfe_pct"] > 0 and sig.context["mae_pct"] < sig.context["mfe_pct"]
    assert len(fake.lists[config.Q_DECISIONS]) == 1


def test_market_regime_helper(trend_up, trend_down, monkeypatch):
    monkeypatch.setattr(config, "REGIME_SYMBOL", "BTC/USD")
    assert strategy.market_regime({"BTC/USD": trend_up}) is True
    assert strategy.market_regime({"BTC/USD": trend_down}) is False
    assert strategy.market_regime({}) is False                      # enabled filter fails closed
    monkeypatch.setattr(config, "REGIME_SYMBOL", "")
    assert strategy.market_regime({"BTC/USD": trend_down}) is True  # filter off


def test_regime_requires_same_candle(trend_up, monkeypatch):
    monkeypatch.setattr(config, 'REGIME_SYMBOL', 'BTC/USD')
    last = trend_up.ts.iloc[-1]
    assert strategy.market_regime({'BTC/USD': trend_up}, last) is True
    assert strategy.market_regime({'BTC/USD': trend_up}, last + pd.Timedelta(hours=4)) is False
    assert strategy.market_regime({'BTC/USD': trend_up}, last - pd.Timedelta(hours=4)) is False

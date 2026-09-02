"""Offline test of the executor glue (fake broker, fake Redis, monkeypatched db)."""
from datetime import datetime, timedelta, timezone

import pytest

from app import config, executor
from app.brokers.base import Fill
from app.models import Decision, Signal


class FakeRedis:
    def __init__(self):
        self.kv, self.lists = {}, {}

    def get(self, k):
        return self.kv.get(k)

    def rpush(self, name, value):
        self.lists.setdefault(name, []).append(value)


class FakeBroker:
    name = "fake"

    def __init__(self, buy=100.0, sell=118.0):
        self.buy, self.sell = buy, sell

    def market_buy(self, symbol, qty):
        return Fill(self.buy, qty, None)

    def market_sell(self, symbol, qty):
        return Fill(self.sell, qty, None)


ENTRY_CTX = {"adx": 24.3, "dist_ema_pct": 3.1, "dist_hh_pct": 0.8, "dist_st_pct": -6.0, "market_ok": True}
SIGNALS = {
    1: Signal(1, "2026-09-01T00:00:00+00:00", "BTC/USD", "entry", "buy", 100.0, 94.0, 2.0, "Trend-Breakout", context=ENTRY_CTX),
    2: Signal(2, "2026-09-05T00:00:00+00:00", "BTC/USD", "exit", "sell", 118.5, None, 2.0, "Supertrend flipped red",
              context={"mfe_pct": 25.0, "mae_pct": -1.5, "bars": 24}),
}


@pytest.fixture
def env(monkeypatch):
    fake, calls = FakeRedis(), {}
    monkeypatch.setattr(executor, "r", fake)
    monkeypatch.setattr(executor, "broker", FakeBroker())
    monkeypatch.setattr(executor.db, "get_signal", lambda sid: SIGNALS.get(sid))
    monkeypatch.setattr(executor.db, "set_signal_status", lambda sid, st: calls.setdefault("status", []).append((sid, st)))
    monkeypatch.setattr(executor.db, "insert_trade", lambda *a, **k: calls.setdefault("trades", []).append(a))
    monkeypatch.setattr(executor.db, "open_positions", lambda: [])
    monkeypatch.setattr(executor.db, "open_position_for", lambda s: None)
    monkeypatch.setattr(executor.db, "open_position", lambda *a, **k: calls.setdefault("open", []).append((a, k)) or 7)
    monkeypatch.setattr(executor.db, "close_position",
                        lambda pid, price, **k: calls.setdefault("close", []).append((pid, price, k)) or (price - 100.0) * 2.0)
    return fake, calls


def notifications(fake):
    return [m.decode() if isinstance(m, bytes) else m for m in fake.lists.get(config.Q_NOTIFY, [])]


def test_entry_records_position_with_signal_link(env):
    fake, calls = env
    executor.handle(Decision(1, "yes", "user"))
    (args, kwargs), = calls["open"]
    assert args == ("BTC/USD", 2.0, 100.0, 94.0) and kwargs == {"entry_signal_id": 1}
    assert calls["status"] == [(1, "executed")]
    assert "BOUGHT BTC/USD" in notifications(fake)[0]


def test_entry_skipped_when_halted(env):
    fake, calls = env
    fake.kv[config.K_HALTED] = b"1"
    executor.handle(Decision(1, "yes", "user"))
    assert "open" not in calls and calls["status"] == [(1, "failed")]
    assert "halted" in notifications(fake)[0]


def test_exit_closes_with_post_mortem(env, monkeypatch):
    fake, calls = env
    pos = {"id": 7, "symbol": "BTC/USD", "qty": 2.0, "entry_price": 100.0, "stop": 110.0,
           "opened_at": datetime.now(timezone.utc) - timedelta(days=4), "entry_signal_id": 1}
    monkeypatch.setattr(executor.db, "open_position_for", lambda s: pos)
    executor.handle(Decision(2, "auto", "strategy"))
    (pid, price, stats), = calls["close"]
    assert (pid, price) == (7, 118.0)
    assert stats["r_multiple"] == pytest.approx(3.0) and stats["mfe_pct"] == 25.0 and stats["mae_pct"] == -1.5
    text = notifications(fake)[0]
    assert "SOLD BTC/USD" in text and "PnL +36.00 USD (+18.00%)" in text and "Reason: Supertrend flipped red" in text
    assert "📋 Post-Mortem: +3.0 R · 4 d (24 Candles) · Hoch +25.0 %, davon 28 % zurückgegeben · Tief -1.5 % · Entry: ADX 24" in text
    assert calls["status"] == [(2, "executed")]


def test_exit_of_legacy_position_without_entry_link(env, monkeypatch):
    fake, calls = env
    pos = {"id": 3, "symbol": "BTC/USD", "qty": 2.0, "entry_price": 100.0, "stop": 95.0,
           "opened_at": datetime.now(timezone.utc) - timedelta(hours=10), "entry_signal_id": None}
    monkeypatch.setattr(executor.db, "open_position_for", lambda s: pos)
    legacy_exit = Signal(2, "2026-09-05T00:00:00+00:00", "BTC/USD", "exit", "sell", 118.5, None, 2.0, "Supertrend flipped red")
    monkeypatch.setattr(executor.db, "get_signal", lambda sid: legacy_exit if sid == 2 else None)
    executor.handle(Decision(2, "auto", "strategy"))
    (_, _, stats), = calls["close"]
    assert stats == {"r_multiple": None, "mfe_pct": None, "mae_pct": None}
    assert "📋 Post-Mortem: 10 h" in notifications(fake)[0]


def test_exit_without_position_fails_gracefully(env):
    fake, calls = env
    executor.handle(Decision(2, "auto", "strategy"))
    assert "close" not in calls and calls["status"] == [(2, "failed")]
    assert "no open position" in notifications(fake)[0]

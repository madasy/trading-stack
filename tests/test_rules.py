import math

import numpy as np
import pytest

from app import rules
from app.rules import Params
from tests.conftest import make_ohlcv


def last_two(df):
    return df.iloc[-1], df.iloc[-2]


def test_prepare_adds_columns_and_hh_excludes_current_bar(trend_up):
    d = rules.prepare(trend_up, Params())
    for col in ("ema", "st", "dir", "adx", "hh"):
        assert col in d
    # hh is the max high of the previous 20 candles -> below the current high in a monotonic trend
    assert d["hh"].iloc[-1] == pytest.approx(d["high"].iloc[-21:-1].max())
    assert d["hh"].iloc[-1] < d["high"].iloc[-1]


def test_entry_all_conditions_true_in_clean_uptrend(trend_up):
    d = rules.prepare(trend_up, Params())
    row, prev = last_two(d)
    checks = rules.entry_checks(row, prev, Params(), market_ok=True)
    assert all(checks.values()), checks
    assert rules.entry_stop(row, prev, Params()) == pytest.approx(float(row["st"]))


def test_entry_blocked_by_each_filter(trend_up):
    p = Params()
    d = rules.prepare(trend_up, p)
    row, prev = last_two(d)
    assert rules.entry_stop(row, prev, p, market_ok=False) is None
    assert rules.entry_checks(row, prev, p, market_ok=False)["market"] is False

    r = row.copy(); r["adx"] = p.adx_min - 1
    assert rules.entry_checks(r, prev, p)["adx"] is False

    r = row.copy(); r["close"] = r["hh"]            # equal to prior high is not a breakout
    assert rules.entry_checks(r, prev, p)["breakout"] is False

    r = row.copy(); r["close"] = r["ema"] - 1
    assert rules.entry_checks(r, prev, p)["above_ema"] is False

    r = row.copy(); r["dir"] = -1
    assert rules.entry_checks(r, prev, p)["supertrend_green"] is False

    r = row.copy(); r["adx"] = float("nan"); r["hh"] = float("nan")   # warm-up -> no entry
    c = rules.entry_checks(r, prev, p)
    assert c["adx"] is False and c["breakout"] is False


def test_filters_can_be_disabled():
    p = Params(adx_min=0, breakout_len=0)
    d = rules.prepare(make_ohlcv(100 * 1.003 ** np.arange(300)), p)
    row, prev = last_two(d)
    checks = rules.entry_checks(row, prev, p)
    assert "adx" not in checks and "breakout" not in checks
    assert math.isnan(d["hh"].iloc[-1])


def test_flip_mode_requires_red_to_green_candle(trend_up):
    p = Params(entry_mode="flip")
    d = rules.prepare(trend_up, p)
    row, prev = last_two(d)
    assert rules.entry_checks(row, prev, p)["supertrend_flip"] is False   # already green before
    prev2 = prev.copy(); prev2["dir"] = -1
    assert rules.entry_checks(row, prev2, p)["supertrend_flip"] is True


def test_market_uptrend(trend_up, trend_down):
    assert rules.market_uptrend(rules.prepare(trend_up, Params()).iloc[-1]) is True
    assert rules.market_uptrend(rules.prepare(trend_down, Params()).iloc[-1]) is False


def test_trail_stop_only_moves_up():
    row = {"st": 105.0, "dir": 1}
    assert rules.trail_stop(row, None) == 105.0
    assert rules.trail_stop(row, 100.0) == 105.0
    assert rules.trail_stop(row, 110.0) == 110.0
    assert rules.trail_stop({"st": 90.0, "dir": -1}, 110.0) == 110.0   # red: keep the old stop


def test_exit_reason():
    assert rules.exit_reason({"dir": -1, "close": 120.0}, 100.0) == "Supertrend flipped red"
    assert "below stop" in rules.exit_reason({"dir": 1, "close": 99.0}, 100.0)
    assert rules.exit_reason({"dir": 1, "close": 100.0}, 100.0) is not None      # close == stop exits
    assert rules.exit_reason({"dir": 1, "close": 101.0}, 100.0) is None


def test_risk_qty_basic_cap_and_invalid():
    # 1% of 10_000 = 100 risk, stop 5 below entry -> 20 units
    assert rules.risk_qty(10_000, 1.0, 100.0, 95.0) == 20.0
    # tight stop would be 250 units = 250% of capital -> capped at 50% notional = 50 units
    assert rules.risk_qty(10_000, 1.0, 100.0, 99.6, max_notional_pct=50) == 50.0
    assert rules.risk_qty(10_000, 1.0, 100.0, 99.6, max_notional_pct=0) == pytest.approx(250.0)
    assert rules.risk_qty(10_000, 1.0, 100.0, 100.0) is None
    assert rules.risk_qty(10_000, 1.0, 100.0, 101.0) is None
    assert rules.risk_qty(0, 1.0, 100.0, 95.0) is None


def test_warmup_covers_longest_lookback():
    assert Params().warmup >= 200
    assert Params(ema_len=50, breakout_len=100).warmup >= 100


def test_rising_adx_filter_rejects_flat_falling_and_missing():
    p = Params(adx_rising=True)
    row = dict(dir=1, close=110, ema=100, adx=24, hh=109, st=103)
    for prior, expected in [(23, True), (24, False), (25, False), (float('nan'), False)]:
        assert rules.entry_checks(row, dict(adx=prior), p)['adx_rising'] is expected


def test_channel_exit_excludes_current_low(trend_up):
    p = Params(exit_len=12)
    d = rules.prepare(trend_up, p)
    assert d.ll.iloc[-1] == pytest.approx(d.low.iloc[-13:-1].min())
    row = dict(dir=1, close=105, ll=106)
    assert '12-candle low' in rules.exit_reason(row, 100, p)
    assert rules.exit_reason(row, 100, Params()) is None
    assert rules.exit_reason(dict(row, close=106), 100, p) is None


def test_portfolio_budget_and_invalid_sizing():
    positions = [dict(entry_price=100, stop=95, qty=10)]  # $50 risk
    assert rules.available_risk_pct(10000, positions, 1) == pytest.approx(.5)
    assert rules.available_risk_pct(10000, positions * 2, 1) == 0
    assert rules.available_risk_pct(10000, [dict(entry_price=100, stop=None, qty=10)], 1) == 0
    assert rules.available_risk_pct(10000, [dict(entry_price=100, stop=101, qty=10)], 1) == 1
    for value in [float('nan'), float('inf')]:
        assert rules.risk_qty(10000, 1, value, 95) is None
    assert rules.risk_qty(10000, 0, 100, 95) is None
    assert rules.risk_qty(10000, -1, 100, 95) is None
    assert rules.risk_qty(10000, 1, 103, 96) * 7 <= 100

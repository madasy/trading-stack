import numpy as np
import pandas as pd
import pytest

from app import backtest
from app.rules import Params
from tests.conftest import make_ohlcv


def synth(seed, n=3000, drift=0.0006, vol=0.02):
    rng = np.random.default_rng(seed)
    return make_ohlcv(100 * np.exp(np.cumsum(rng.normal(drift, vol, n))), start="2020-01-01")


@pytest.fixture
def frames():
    return {"BTC/USD": synth(1), "ETH/USD": synth(2)}


def run(frames, **kw):
    args = dict(symbols=list(frames), p=Params(), regime_symbol="BTC/USD", capital0=10_000, risk_pct=1.0,
                max_positions=2, max_notional_pct=50.0, fee=0.004, slippage=0.0005)
    args.update(kw)
    return backtest.simulate(frames, **args)


def test_equity_defined_and_accounting_identity(frames):
    res = run(frames)
    eq, tr = res["equity"], res["trades"]
    assert len(eq) > 2000 and eq.notna().all()
    assert len(tr) > 5
    open_mtm = sum((o["last"] - o["entry"]) * o["qty"] - o["fee_in"] for o in res["open"])
    assert eq.iloc[-1] == pytest.approx(10_000 + tr.pnl.sum() + open_mtm, rel=1e-9)


def test_risk_and_notional_cap_per_trade(frames):
    tr = run(frames)["trades"]
    for t in tr.itertuples():
        risk = (t.entry / (1 + 0.0005) - t.stop0) * t.qty          # sized on the signal close, filled at open + slippage
        assert t.qty * t.entry <= 0.5 * t.capital * 1.01            # notional cap (50 %) never exceeded
        assert risk <= 0.01 * t.capital * 1.05                       # never risks more than 1 % (+ open gap tolerance)


def test_never_more_than_max_positions(frames):
    tr = run(frames, max_positions=1)["trades"]
    events = sorted([(t.entry_ts, 1) for t in tr.itertuples()] + [(t.exit_ts, -1) for t in tr.itertuples()],
                    key=lambda e: (e[0], e[1]))   # exits (-1) sort before entries (+1) at the same timestamp
    cur = 0
    for _, d in events:
        cur += d
        assert cur <= 1


def test_exit_reasons_and_positive_holding(frames):
    tr = run(frames)["trades"]
    assert (tr.bars >= 1).all()
    assert tr.reason.str.startswith(("Supertrend flipped red", "Close ")).all()


def test_legacy_and_v3_differ_and_are_deterministic(frames):
    a = run(frames)
    b = run(frames)
    pd.testing.assert_series_equal(a["equity"], b["equity"])
    legacy = run(frames, p=Params(adx_min=0, breakout_len=0, entry_mode="flip"), regime_symbol=None)
    assert len(legacy["trades"]) != len(a["trades"])


def test_window_and_metrics(frames):
    res = run(frames, start="2021-01-01", end="2022-01-01")
    eq = res["equity"]
    assert eq.index[0] >= pd.Timestamp("2021-01-01", tz="UTC") and eq.index[-1] < pd.Timestamp("2022-01-01", tz="UTC")
    m = backtest.metrics(eq, res["trades"], 10_000, 365 * 6, res["exposure"])
    assert -1 <= m["mdd"] <= 0 and 0 <= m["exposure"] <= 1
    y = backtest.yearly(eq, res["trades"], 10_000)
    assert list(y.year) == [2021]


def test_fees_reduce_result(frames):
    gross = run(frames, fee=0.0, slippage=0.0)["equity"].iloc[-1]
    net = run(frames)["equity"].iloc[-1]
    assert net < gross


def test_map_symbol():
    assert backtest.map_symbol("BTC/USD", "binance") == "BTC/USDT"
    assert backtest.map_symbol("BTC/USD", "bitstamp") == "BTC/USD"
    assert backtest.map_symbol("BTC/USDT", "binance") == "BTC/USDT"

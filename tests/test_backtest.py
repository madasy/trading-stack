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
        risk = (t.entry / (1 + 0.0005) - t.stop0) * t.qty          # execution sizing is capped again at the fill price
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


def test_load_or_fetch_first_fetch_and_incremental(monkeypatch, tmp_path):
    import ccxt
    now_ms = int(pd.Timestamp("2026-01-10 10:00", tz="UTC").timestamp() * 1000)
    tf_ms = 4 * 3600 * 1000
    calls = []

    class FakeExchange:
        rateLimit = 0

        def __init__(self, *a, **k):
            pass

        def parse_timeframe(self, tf):
            return 4 * 3600

        def milliseconds(self):
            return now_ms

        def parse8601(self, s):
            return int(pd.Timestamp(s).timestamp() * 1000)

        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
            calls.append(since)
            start = pd.Timestamp("2026-01-01", tz="UTC").timestamp() * 1000
            rows = [[int(start + i * tf_ms), 100.0, 101.0, 99.0, 100.5, 1.0] for i in range(60)]   # last one is still forming
            return [r for r in rows if r[0] >= since][:limit]

    monkeypatch.setattr(ccxt, "binance", FakeExchange)
    df = backtest.load_or_fetch("binance", "BTC/USDT", "4h", "2026-01-01", tmp_path)
    assert str(df["ts"].dtype).startswith("datetime64") and df["ts"].dt.tz is not None      # first fetch: real datetimes
    assert df["ts"].iloc[-1] + pd.Timedelta(milliseconds=tf_ms) <= pd.Timestamp(now_ms, unit="ms", tz="UTC")
    n_first, n_calls = len(df), len(calls)
    df2 = backtest.load_or_fetch("binance", "BTC/USDT", "4h", "2026-01-01", tmp_path)     # cached + incremental
    assert len(df2) == n_first and str(df2["ts"].dtype).startswith("datetime64")
    assert calls[n_calls:] and calls[n_calls] > calls[0]                                    # resumed after the cached candles
    y = backtest.yearly(pd.Series(1.0, index=pd.Index(df2["ts"])), pd.DataFrame(), 1.0)
    assert list(y.year) == [2026]


def test_first_window_bar_uses_actual_previous_candle(frames, monkeypatch):
    p = Params(entry_mode='flip')
    expected = backtest.rules.prepare(frames['BTC/USD'], p).set_index('ts')
    start = expected.index[500].normalize()
    observed = []
    def capture(row, prev, *args):
        observed.append(prev['close'])
        return None
    monkeypatch.setattr(backtest.rules, 'entry_stop', capture)
    run(frames, p=p, start=str(start.date()))
    assert observed[0] == expected.loc[start - pd.Timedelta(hours=4), 'close']


def test_drawdown_includes_initial_capital():
    eq = pd.Series([9900., 9800.], index=pd.date_range('2026-01-01', periods=2, tz='UTC'))
    assert backtest.metrics(eq, pd.DataFrame(), 10000, 2190)['mdd'] == pytest.approx(-.02)


def test_portfolio_risk_cap_limits_simultaneous_entries(frames):
    # Disable filters so both symbols compete for the same remaining risk budget.
    result = run(frames, p=Params(adx_min=0, breakout_len=0), risk_pct=1, max_open_risk_pct=.5)
    for t in result['trades'].itertuples():
        assert (t.entry - t.stop0) * t.qty <= t.capital * .005 + 1e-8


def test_portfolio_budget_shared_by_same_candle_orders(monkeypatch):
    df = make_ohlcv(np.full(60, 100.0))
    def prepared(frame, p):
        return frame.assign(ema=99., st=90., dir=1, adx=30., hh=99., ll=np.nan)
    monkeypatch.setattr(backtest.rules, 'prepare', prepared)
    result = backtest.simulate({'A': df, 'B': df}, ['A', 'B'], Params(ema_len=1, adx_len=1),
                               risk_pct=1, max_open_risk_pct=1.5, fee=0, slippage=0)
    assert len(result['open']) == 2
    assert [p['qty'] for p in result['open']] == [10., 5.]
    assert sum((p['entry'] - p['stop']) * p['qty'] for p in result['open']) == 150

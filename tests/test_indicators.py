import numpy as np
import pandas as pd

from app.indicators import adx, atr, ema, supertrend
from tests.conftest import make_ohlcv


def test_ema_constant_series_is_constant():
    s = pd.Series([5.0] * 50)
    assert np.allclose(ema(s, 10), 5.0)


def test_atr_positive_and_wilder_smoothed(trend_up):
    a = atr(trend_up, 10)
    assert (a.iloc[20:] > 0).all()
    assert a.iloc[-1] < (trend_up["high"] - trend_up["low"]).max()


def test_supertrend_green_in_uptrend_and_line_below_close(trend_up):
    st = supertrend(trend_up, 10, 3.0)
    tail = st.iloc[50:]
    assert (tail["dir"] == 1).all()
    assert (tail["st"] < trend_up["close"].iloc[50:]).all()


def test_supertrend_red_in_downtrend_and_line_above_close(trend_down):
    st = supertrend(trend_down, 10, 3.0)
    tail = st.iloc[50:]
    assert (tail["dir"] == -1).all()
    assert (tail["st"] > trend_down["close"].iloc[50:]).all()


def test_supertrend_flips_on_reversal():
    up = 100 * 1.003 ** np.arange(200)
    down = up[-1] * 0.99 ** np.arange(1, 101)
    df = make_ohlcv(np.concatenate([up, down]))
    st = supertrend(df, 10, 3.0)
    assert st["dir"].iloc[199] == 1
    assert st["dir"].iloc[-1] == -1
    flips = (st["dir"] != st["dir"].shift(1)).iloc[1:]
    assert flips.sum() >= 1


def test_adx_high_in_trend_low_in_range(trend_up, sideways):
    assert adx(trend_up, 14).iloc[-1] > 40
    assert adx(sideways, 14).iloc[-1] < 25


def test_adx_bounded_and_defined_after_warmup(random_walk):
    a = adx(random_walk, 14)
    tail = a.iloc[50:]
    assert tail.notna().all()
    assert ((tail >= 0) & (tail <= 100)).all()

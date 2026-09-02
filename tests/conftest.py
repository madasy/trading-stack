import numpy as np
import pandas as pd
import pytest


def make_ohlcv(closes, spread=0.004, start="2024-01-01", freq="4h") -> pd.DataFrame:
    """OHLCV frame from a close series: open = previous close, high/low = close +/- spread."""
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    high = np.maximum(opens, closes) * (1 + spread)
    low = np.minimum(opens, closes) * (1 - spread)
    ts = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    return pd.DataFrame({"ts": ts, "open": opens, "high": high, "low": low, "close": closes, "volume": 1.0})


@pytest.fixture
def trend_up():
    """Smooth uptrend with a little noise: Supertrend green, ADX high, every bar a fresh high."""
    i = np.arange(400)
    return make_ohlcv(100 * 1.003 ** i * (1 + 0.001 * np.sin(i)), spread=0.001)


@pytest.fixture
def trend_down():
    i = np.arange(400)
    return make_ohlcv(100 * 0.997 ** i)


@pytest.fixture
def sideways():
    """Range-bound sawtooth: no trend, ADX should be low."""
    i = np.arange(400)
    return make_ohlcv(100 + 3 * np.sin(i / 3.0))


@pytest.fixture
def random_walk():
    rng = np.random.default_rng(7)
    r = rng.normal(0.0004, 0.02, 3000)
    return make_ohlcv(100 * np.exp(np.cumsum(r)), start="2020-01-01")

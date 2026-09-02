from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app import postmortem as pm
from tests.conftest import make_ohlcv


def test_candles_since_includes_the_candle_containing_opened_at():
    df = make_ohlcv(np.full(10, 100.0), start="2026-01-01")          # 4h candles from 00:00
    opened = pd.Timestamp("2026-01-01 09:30", tz="UTC")               # inside the 08:00 candle
    since = pm.candles_since(df, opened, 4 * 3600)
    assert since["ts"].iloc[0] == pd.Timestamp("2026-01-01 08:00", tz="UTC")
    assert len(since) == 8
    naive = datetime(2026, 1, 1, 9, 30)                                # naive datetimes are treated as UTC
    assert len(pm.candles_since(df, naive, 4 * 3600)) == 8


def test_excursion_uses_highs_and_lows():
    df = make_ohlcv([100, 110, 120, 105], spread=0.0)
    e = pm.excursion(df, 100.0)
    assert e["mfe_pct"] == pytest.approx(20.0) and e["mae_pct"] == pytest.approx(0.0) and e["bars"] == 4
    assert pm.excursion(df.iloc[0:0], 100.0) == {"mfe_pct": None, "mae_pct": None, "bars": 0}
    assert pm.excursion(df, 0.0)["bars"] == 0


def test_r_multiple_and_giveback():
    assert pm.r_multiple(100.0, 118.0, 94.0) == pytest.approx(3.0)
    assert pm.r_multiple(100.0, 91.0, 94.0) == pytest.approx(-1.5)
    assert pm.r_multiple(100.0, 118.0, None) is None
    assert pm.r_multiple(100.0, 118.0, 100.0) is None
    assert pm.giveback_pct(18.0, 25.0) == pytest.approx(28.0)
    assert pm.giveback_pct(30.0, 25.0) == 0.0                          # exit above the highest high (gap) -> 0
    assert pm.giveback_pct(-2.0, None) is None and pm.giveback_pct(-2.0, -1.0) is None


def test_holding_text():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert pm.holding_text(t0, t0 + timedelta(hours=30)) == "30 h"
    assert pm.holding_text(t0, t0 + timedelta(days=16, hours=3)) == "16 d"


def test_summary_full_and_minimal():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ctx = {"adx": 24.3, "dist_ema_pct": 3.1, "dist_hh_pct": 0.8, "dist_st_pct": -6.0, "market_ok": True}
    line, stats = pm.summary(100.0, 118.0, 94.0, t0, t0 + timedelta(days=4), {"mfe_pct": 25.0, "mae_pct": -1.5, "bars": 24}, ctx)
    assert line.startswith("📋 Post-Mortem: +3.0 R · 4 d (24 Candles) · Hoch +25.0 %, davon 28 % zurückgegeben · Tief -1.5 %")
    assert "Entry: ADX 24, +3.1 % zum EMA, Breakout +0.8 %, Stop 6.0 % unter Entry, Regime ✓" in line
    assert stats == {"r_multiple": pytest.approx(3.0), "mfe_pct": 25.0, "mae_pct": -1.5}
    line, stats = pm.summary(100.0, 95.0, None, t0, t0 + timedelta(hours=8), None, None)   # legacy position
    assert line == "📋 Post-Mortem: 8 h"
    assert stats == {"r_multiple": None, "mfe_pct": None, "mae_pct": None}

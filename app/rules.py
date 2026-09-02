"""
Pure strategy rules (no I/O). Shared by the live strategy service and the backtester, so the
backtest exercises exactly the production logic.

Strategy v3 "Trend-Breakout", evaluated on closed candles only:

  Entry   all of:  Supertrend(st_len, st_mult) green            (state, not only on the flip candle)
                   close > EMA(ema_len)
                   ADX(adx_len) > adx_min                          (trending market, skip ranges)
                   close > highest high of the previous breakout_len candles
                   market regime ok (the leading symbol, BTC, is itself in an uptrend)
  Exit    Supertrend flips red, or close <= trailed stop (stop = Supertrend line, only moves up)
  Sizing  risk_pct % of capital / (entry - stop), capped at max_notional_pct % of capital (spot)

entry_mode="flip" together with adx_min=0, breakout_len=0 and no regime symbol reproduces v2.
"""
from dataclasses import dataclass

import pandas as pd

from .indicators import adx, ema, supertrend


@dataclass(frozen=True)
class Params:
    ema_len: int = 200
    st_len: int = 10
    st_mult: float = 3.0
    adx_len: int = 14
    adx_min: float = 20.0       # 0 disables the ADX filter
    breakout_len: int = 20      # 0 disables the breakout confirmation
    entry_mode: str = "state"   # state (v3) | flip (v2: only on the red->green candle)

    @property
    def warmup(self) -> int:
        """Candles needed before signals are trustworthy."""
        return max(self.ema_len, self.breakout_len, 3 * self.adx_len) + 5


def prepare(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Return a copy of df (open/high/low/close) with indicator columns ema, st, dir, adx, hh."""
    out = df.copy()
    out["ema"] = ema(out["close"], p.ema_len)
    st = supertrend(out, p.st_len, p.st_mult)
    out["st"] = st["st"].to_numpy()
    out["dir"] = st["dir"].to_numpy()
    out["adx"] = adx(out, p.adx_len)
    # highest high of the *previous* breakout_len candles (excludes the current candle)
    out["hh"] = out["high"].rolling(p.breakout_len).max().shift(1) if p.breakout_len > 0 else float("nan")
    return out


def market_uptrend(row) -> bool:
    """Regime of the leading symbol: above its EMA and Supertrend green."""
    return bool(row["close"] > row["ema"] and row["dir"] == 1)


def entry_checks(row, prev, p: Params, market_ok: bool = True) -> dict[str, bool]:
    """Every condition an entry needs on this closed candle. All True -> entry signal."""
    checks = {
        "supertrend_green": bool(row["dir"] == 1),
        "above_ema": bool(row["close"] > row["ema"]),
    }
    if p.entry_mode == "flip":
        checks["supertrend_flip"] = bool(prev["dir"] == -1 and row["dir"] == 1)
    if p.adx_min > 0:
        checks["adx"] = bool(row["adx"] > p.adx_min)          # NaN during warm-up -> False
    if p.breakout_len > 0:
        checks["breakout"] = bool(row["close"] > row["hh"])   # NaN during warm-up -> False
    checks["market"] = bool(market_ok)
    checks["stop_below_close"] = bool(row["st"] < row["close"])
    return checks


def entry_stop(row, prev, p: Params, market_ok: bool = True) -> float | None:
    """Stop price (Supertrend line) if all entry conditions hold, else None."""
    if all(entry_checks(row, prev, p, market_ok).values()):
        return float(row["st"])
    return None


def trail_stop(row, current_stop: float | None) -> float:
    """Stop follows the Supertrend line upward only; never moves down."""
    st_line = float(row["st"])
    if current_stop is None:
        return st_line
    return max(current_stop, st_line) if row["dir"] == 1 else current_stop


def exit_reason(row, stop: float) -> str | None:
    if row["dir"] == -1:
        return "Supertrend flipped red"
    if row["close"] <= stop:
        return f"Close {row['close']:.2f} below stop {stop:.2f}"
    return None


def risk_qty(capital: float, risk_pct: float, entry: float, stop: float, max_notional_pct: float = 0) -> float | None:
    """Quantity so that (entry - stop) * qty == risk_pct % of capital, capped at max_notional_pct % of capital."""
    per_unit = entry - stop
    if per_unit <= 0 or capital <= 0 or entry <= 0:
        return None
    qty = capital * risk_pct / 100 / per_unit
    if max_notional_pct > 0:
        qty = min(qty, capital * max_notional_pct / 100 / entry)
    return round(qty, 6)

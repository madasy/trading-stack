"""Post-mortem numbers for a closed trade (pure, no I/O): R multiple, excursions while open, give-back from the peak."""
import pandas as pd


def candles_since(df: pd.DataFrame, opened_at, timeframe_seconds: int) -> pd.DataFrame:
    """Candles that overlap the holding period: the one containing opened_at and everything after it."""
    start = pd.Timestamp(opened_at)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    return df[df["ts"] > start - pd.Timedelta(seconds=timeframe_seconds)]


def excursion(candles: pd.DataFrame, entry_price: float) -> dict:
    """Max favourable / adverse excursion in percent of the entry price over the candles seen while open."""
    if candles is None or len(candles) == 0 or not entry_price or entry_price <= 0:
        return {"mfe_pct": None, "mae_pct": None, "bars": 0}
    return {
        "mfe_pct": float(candles["high"].max() / entry_price - 1) * 100,
        "mae_pct": float(candles["low"].min() / entry_price - 1) * 100,
        "bars": int(len(candles)),
    }


def r_multiple(entry: float, exit_price: float, stop0: float | None) -> float | None:
    """Result in units of the initial risk (entry - initial stop)."""
    if stop0 is None or entry is None or entry <= stop0:
        return None
    return (exit_price - entry) / (entry - stop0)


def giveback_pct(pnl_pct: float, mfe_pct: float | None) -> float | None:
    """Share of the best open profit that was given back before the exit (0 = sold at the top)."""
    if mfe_pct is None or mfe_pct <= 0:
        return None
    return max(0.0, (mfe_pct - pnl_pct) / mfe_pct * 100)


def holding_text(opened_at, closed_at) -> str:
    hours = (pd.Timestamp(closed_at) - pd.Timestamp(opened_at)).total_seconds() / 3600
    return f"{hours / 24:.0f} d" if hours >= 48 else f"{hours:.0f} h"


def entry_context_text(ctx: dict | None) -> str:
    """Short description of the market state stored with the entry signal."""
    if not ctx:
        return ""
    bits = []
    if ctx.get("adx") is not None:
        bits.append(f"ADX {ctx['adx']:.0f}")
    if ctx.get("dist_ema_pct") is not None:
        bits.append(f"{ctx['dist_ema_pct']:+.1f} % zum EMA")
    if ctx.get("dist_hh_pct") is not None:
        bits.append(f"Breakout {ctx['dist_hh_pct']:+.1f} %")
    if ctx.get("dist_st_pct") is not None:
        bits.append(f"Stop {abs(ctx['dist_st_pct']):.1f} % unter Entry")
    if "market_ok" in ctx:
        bits.append("Regime " + ("✓" if ctx["market_ok"] else "✗"))
    return ", ".join(bits)


def summary(entry: float, exit_price: float, stop0: float | None, opened_at, closed_at,
            exc: dict | None, entry_ctx: dict | None) -> tuple[str, dict]:
    """One Telegram line plus the numbers to persist on the position (r_multiple, mfe_pct, mae_pct)."""
    pnl_pct = (exit_price / entry - 1) * 100
    r = r_multiple(entry, exit_price, stop0)
    exc = exc or {}
    mfe, mae = exc.get("mfe_pct"), exc.get("mae_pct")
    parts = []
    if r is not None:
        parts.append(f"{r:+.1f} R")
    parts.append(holding_text(opened_at, closed_at) + (f" ({exc['bars']} Candles)" if exc.get("bars") else ""))
    if mfe is not None:
        gb = giveback_pct(pnl_pct, mfe)
        parts.append(f"Hoch {mfe:+.1f} %" + (f", davon {gb:.0f} % zurückgegeben" if gb is not None else ""))
    if mae is not None:
        parts.append(f"Tief {mae:+.1f} %")
    ctx_text = entry_context_text(entry_ctx)
    if ctx_text:
        parts.append("Entry: " + ctx_text)
    return "📋 Post-Mortem: " + " · ".join(parts), {"r_multiple": r, "mfe_pct": mfe, "mae_pct": mae}

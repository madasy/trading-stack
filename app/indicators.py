"""EMA + Supertrend, implemented to match TradingView's built-ins (no pandas-ta dependency)."""
import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def atr(df: pd.DataFrame, length: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    # RMA (Wilder smoothing) like TradingView's ta.atr
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def supertrend(df: pd.DataFrame, length: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """Returns DataFrame with columns st (line) and dir (1 = uptrend/green, -1 = downtrend/red)."""
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    a = atr(df, length).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)

    upper = hl2 + mult * a
    lower = hl2 - mult * a
    f_upper = upper.copy()
    f_lower = lower.copy()
    direction = np.ones(n, dtype=int)
    line = np.full(n, np.nan)

    for i in range(1, n):
        # final bands (ratchet), same rules as TradingView's ta.supertrend
        if not (lower[i] > f_lower[i - 1] or close[i - 1] < f_lower[i - 1]):
            f_lower[i] = f_lower[i - 1]
        if not (upper[i] < f_upper[i - 1] or close[i - 1] > f_upper[i - 1]):
            f_upper[i] = f_upper[i - 1]

        if direction[i - 1] == -1:            # was in downtrend (line = upper band)
            direction[i] = 1 if close[i] > f_upper[i] else -1
        else:                                 # was in uptrend (line = lower band)
            direction[i] = -1 if close[i] < f_lower[i] else 1

        line[i] = f_lower[i] if direction[i] == 1 else f_upper[i]

    return pd.DataFrame({"st": line, "dir": direction}, index=df.index)

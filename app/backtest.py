"""
Backtester: runs the production rules (app/rules.py) over historical candles as a portfolio with
shared capital, mirroring the live services.

    python -m app.backtest                          # Binance BTC/USDT + ETH/USDT since 2017-08, current config
    python -m app.backtest --compare --plot eq.png  # v3 vs legacy v2 vs buy & hold, with equity chart
    python -m app.backtest --start 2023-01-01       # evaluation window only (indicators still warm up before it)
    python -m app.backtest --exchange bitstamp --symbols BTC/USD,ETH/USD

Semantics: signals on closed candles, fills at the next candle's open (+ slippage), taker fee per side,
qty = RISK_PCT % of (capital + realized PnL) / (entry - stop) capped at MAX_NOTIONAL_PCT, MAX_POSITIONS.
Candles are cached in data/<exchange>_<SYMBOL>_<tf>.csv and updated incrementally. Kraken only serves
the last 720 candles, hence Binance as the default source (closes differ from Kraken by ~0.1 %).
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, rules
from .rules import Params

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


# ---------- data ----------

def map_symbol(symbol: str, exchange_id: str) -> str:
    """Kraken quotes in USD, Binance in USDT."""
    if exchange_id == "binance" and symbol.endswith("/USD"):
        return symbol + "T"
    return symbol


def load_or_fetch(exchange_id: str, symbol: str, timeframe: str, since: str, data_dir: Path) -> pd.DataFrame:
    import ccxt
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{exchange_id}_{symbol.replace('/', '')}_{timeframe}.csv"
    cached = pd.read_csv(path, parse_dates=["ts"]) if path.exists() else pd.DataFrame(columns=COLUMNS)
    ex = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    cursor = int(cached["ts"].iloc[-1].timestamp() * 1000) + 1 if len(cached) else ex.parse8601(f"{since}T00:00:00Z")
    rows = []
    while cursor < ex.milliseconds() - tf_ms:            # stop once we reach the still-forming candle
        batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        if not batch:
            break
        rows += batch
        nxt = batch[-1][0] + tf_ms
        if nxt <= cursor:
            break
        cursor = nxt
    new = pd.DataFrame(rows, columns=COLUMNS)
    new["ts"] = pd.to_datetime(new["ts"], unit="ms", utc=True)
    df = pd.concat([cached, new]) if len(cached) else new
    df = df.drop_duplicates("ts").sort_values("ts")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)                       # keep a real DatetimeIndex on the first fetch too
    now = pd.Timestamp(ex.milliseconds(), unit="ms", tz="UTC")
    df = df[df["ts"] + pd.Timedelta(milliseconds=tf_ms) <= now]         # closed candles only (exchange clock)
    df.to_csv(path, index=False)
    return df.reset_index(drop=True)


# ---------- simulation ----------

def simulate(frames: dict[str, pd.DataFrame], symbols: list[str], p: Params, regime_symbol: str | None = None,
             capital0: float = 10_000.0, risk_pct: float = 1.0, max_positions: int = 2, max_notional_pct: float = 50.0,
             fee: float = 0.004, slippage: float = 0.0005, start: str | None = None, end: str | None = None) -> dict:
    """Portfolio simulation. frames: symbol -> OHLCV (must include regime_symbol if set)."""
    needed = list(dict.fromkeys(symbols + ([regime_symbol] if regime_symbol else [])))
    prep = {s: rules.prepare(frames[s], p).iloc[p.warmup:].set_index("ts") for s in needed}
    idx = None
    for s in needed:
        idx = prep[s].index if idx is None else idx.intersection(prep[s].index)
    idx = idx.sort_values()
    if start:
        idx = idx[idx >= pd.Timestamp(start, tz="UTC")]
    if end:
        idx = idx[idx < pd.Timestamp(end, tz="UTC")]
    if len(idx) < 2:
        raise ValueError("no overlapping candles in the requested window")
    bars = {s: prep[s].loc[idx].to_dict("records") for s in needed}
    n = len(idx)

    realized, positions, pending, trades = 0.0, {}, {}, []
    equity = np.empty(n)
    exposure = 0
    for i in range(n):
        # 1) fills at this candle's open: exits first, then entries
        for s, order in sorted(pending.items(), key=lambda kv: kv[1]["kind"] != "exit"):
            o = bars[s][i]["open"]
            if order["kind"] == "exit" and s in positions:
                pos = positions.pop(s)
                price = o * (1 - slippage)
                gross = (price - pos["entry"]) * pos["qty"]
                cost = price * pos["qty"] * fee
                realized += gross - cost
                risk = (pos["entry"] - pos["stop0"]) * pos["qty"]
                trades.append(dict(symbol=s, entry_ts=pos["ts"], exit_ts=idx[i], entry=pos["entry"], exit=price,
                                   qty=pos["qty"], stop0=pos["stop0"], capital=pos["capital"],
                                   pnl=gross - cost - pos["fee_in"], r=(gross - cost - pos["fee_in"]) / risk if risk > 0 else 0.0,
                                   bars=i - pos["i0"], reason=order["reason"]))
            elif order["kind"] == "entry" and s not in positions and len(positions) < max_positions:
                price = o * (1 + slippage)
                capital = capital0 + realized
                qty = rules.risk_qty(capital, risk_pct, order["close"], order["stop"], max_notional_pct)
                if not qty:
                    continue
                fee_in = price * qty * fee
                realized -= fee_in
                positions[s] = dict(entry=price, qty=qty, stop=order["stop"], stop0=order["stop"], ts=idx[i], i0=i,
                                    fee_in=fee_in, capital=capital)
        pending = {}

        # 2) signals at this candle's close
        market_ok = rules.market_uptrend(bars[regime_symbol][i]) if regime_symbol else True
        for s in symbols:
            row, prev = bars[s][i], bars[s][i - 1]
            if s in positions:
                pos = positions[s]
                pos["stop"] = rules.trail_stop(row, pos["stop"])
                reason = rules.exit_reason(row, pos["stop"])
                if reason:
                    pending[s] = dict(kind="exit", reason=reason)
            else:
                slots = max_positions - len(positions) - sum(1 for o in pending.values() if o["kind"] == "entry")
                stop = rules.entry_stop(row, prev, p, market_ok if s != regime_symbol else True)
                if stop is not None and slots > 0:
                    pending[s] = dict(kind="entry", stop=stop, close=row["close"])

        # 3) mark to market
        equity[i] = capital0 + realized + sum((bars[s][i]["close"] - pos["entry"]) * pos["qty"] for s, pos in positions.items())
        exposure += bool(positions)

    open_positions = [dict(symbol=s, **pos, last=bars[s][n - 1]["close"]) for s, pos in positions.items()]
    return dict(equity=pd.Series(equity, index=idx), trades=pd.DataFrame(trades), open=open_positions,
                exposure=exposure / n, realized=realized, capital0=capital0)


def buy_hold(frames: dict[str, pd.DataFrame], symbols: list[str], index: pd.Index, capital0: float, fee: float) -> pd.Series:
    eq = None
    for s in symbols:
        c = frames[s].set_index("ts")["close"].reindex(index).ffill()
        part = capital0 / len(symbols) * (1 - fee) * c / c.iloc[0]
        eq = part if eq is None else eq + part
    return eq


# ---------- metrics ----------

def metrics(equity: pd.Series, trades: pd.DataFrame, capital0: float, bars_per_year: float, exposure: float = float("nan")) -> dict:
    r = equity.pct_change().dropna()
    years = len(equity) / bars_per_year
    end = equity.iloc[-1] / capital0
    dd = equity / equity.cummax() - 1
    m = dict(total=end - 1, cagr=end ** (1 / years) - 1 if years > 0 else float("nan"), mdd=dd.min(),
             sharpe=r.mean() / r.std() * math.sqrt(bars_per_year) if r.std() > 0 else 0.0, years=years, exposure=exposure)
    m["calmar"] = m["cagr"] / -m["mdd"] if m["mdd"] < 0 else float("nan")
    if len(trades):
        w, l = trades[trades.pnl > 0], trades[trades.pnl <= 0]
        m.update(n=len(trades), per_year=len(trades) / years, winrate=len(w) / len(trades),
                 pf=w.pnl.sum() / -l.pnl.sum() if len(l) and l.pnl.sum() < 0 else float("inf"),
                 avg_r=trades.r.mean(), avg_win_r=w.r.mean() if len(w) else 0.0, avg_loss_r=l.r.mean() if len(l) else 0.0,
                 avg_bars=trades.bars.mean(), max_consec_loss=_max_consecutive(trades.pnl <= 0))
    else:
        m.update(n=0, per_year=0.0, winrate=0.0, pf=0.0, avg_r=0.0, avg_win_r=0.0, avg_loss_r=0.0, avg_bars=0.0, max_consec_loss=0)
    return m


def _max_consecutive(mask) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def yearly(equity: pd.Series, trades: pd.DataFrame, capital0: float) -> pd.DataFrame:
    out = []
    for y, g in equity.groupby(equity.index.year):
        before = equity[equity.index < g.index[0]]
        e0 = before.iloc[-1] if len(before) else capital0
        n = int((trades.exit_ts.dt.year == y).sum()) if len(trades) else 0
        out.append(dict(year=y, ret=g.iloc[-1] / e0 - 1, mdd=(g / g.cummax() - 1).min(), trades=n))
    return pd.DataFrame(out)


def fmt(m: dict) -> str:
    return (f"ret {m['total']*100:8.1f}%  cagr {m['cagr']*100:6.1f}%  maxDD {m['mdd']*100:6.1f}%  calmar {m['calmar']:5.2f}  "
            f"sharpe {m['sharpe']:5.2f}  trades {m['n']:4d} ({m['per_year']:4.1f}/y)  win {m['winrate']*100:3.0f}%  "
            f"PF {m['pf']:5.2f}  avgR {m['avg_r']:5.2f}  exposure {m['exposure']*100:3.0f}%")


# ---------- cli ----------

def params_from_config(legacy: bool = False) -> Params:
    if legacy:
        return Params(ema_len=config.EMA_LEN, st_len=config.ST_LEN, st_mult=config.ST_MULT, adx_min=0, breakout_len=0, entry_mode="flip")
    return Params(ema_len=config.EMA_LEN, st_len=config.ST_LEN, st_mult=config.ST_MULT, adx_len=config.ADX_LEN,
                  adx_min=config.ADX_MIN, breakout_len=config.BREAKOUT_LEN, entry_mode=config.ENTRY_MODE)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--symbols", default=",".join(config.SYMBOLS), help="comma separated, Kraken-style pairs are mapped to the exchange")
    ap.add_argument("--regime-symbol", default=config.REGIME_SYMBOL, help="'' disables the market regime filter")
    ap.add_argument("--timeframe", default=config.TIMEFRAME)
    ap.add_argument("--since", default="2017-08-01", help="first candle to download")
    ap.add_argument("--start", default=None, help="evaluation window start (YYYY-MM-DD)")
    ap.add_argument("--end", default=None, help="evaluation window end (exclusive)")
    ap.add_argument("--fee", type=float, default=0.004, help="taker fee per side (0.004 = 0.4 %%)")
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--capital", type=float, default=config.PAPER_CAPITAL)
    ap.add_argument("--risk", type=float, default=config.RISK_PCT)
    ap.add_argument("--max-positions", type=int, default=config.MAX_POSITIONS)
    ap.add_argument("--max-notional", type=float, default=config.MAX_NOTIONAL_PCT)
    ap.add_argument("--legacy", action="store_true", help="v2 rules: entry only on the Supertrend flip, no ADX/breakout/regime")
    ap.add_argument("--compare", action="store_true", help="run v3 and legacy v2 side by side")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--plot", default=None, help="write equity chart PNG")
    ap.add_argument("--trades-csv", default=None, help="write the trade list")
    a = ap.parse_args(argv)

    symbols = [map_symbol(s.strip(), a.exchange) for s in a.symbols.split(",") if s.strip()]
    regime = map_symbol(a.regime_symbol.strip(), a.exchange) if a.regime_symbol and a.regime_symbol.strip() else None
    if regime and not any(s.split("/")[0] == regime.split("/")[0] for s in symbols) and regime not in symbols:
        pass  # regime symbol is fetched in addition to the traded ones
    needed = list(dict.fromkeys(symbols + ([regime] if regime else [])))
    frames = {}
    for s in needed:
        frames[s] = load_or_fetch(a.exchange, s, a.timeframe, a.since, Path(a.data_dir))
        print(f"{a.exchange} {s} {a.timeframe}: {len(frames[s])} candles {frames[s]['ts'].iloc[0]:%Y-%m-%d} .. {frames[s]['ts'].iloc[-1]:%Y-%m-%d %H:%M}")
    import ccxt
    bpy = 365 * 86400 / ccxt.Exchange().parse_timeframe(a.timeframe)
    common = dict(capital0=a.capital, risk_pct=a.risk, max_positions=a.max_positions, max_notional_pct=a.max_notional,
                  fee=a.fee, slippage=a.slippage, start=a.start, end=a.end)

    runs = []
    if not a.legacy or a.compare:
        runs.append(("v3 trend-breakout", params_from_config(), regime))
    if a.legacy or a.compare:
        runs.append(("v2 legacy flip", params_from_config(legacy=True), None))
    results = []
    for name, p, reg in runs:
        res = simulate(frames, symbols, p, reg, **common)
        m = metrics(res["equity"], res["trades"], a.capital, bpy, res["exposure"])
        results.append((name, res, m))
        print(f"\n== {name} ==  {p}  regime={reg}")
        print("   " + fmt(m))
        print(f"   avg win {m['avg_win_r']:.2f}R, avg loss {m['avg_loss_r']:.2f}R, avg hold {m['avg_bars']:.0f} candles, "
              f"max consecutive losses {m['max_consec_loss']}")
        if len(res["trades"]):
            per = res["trades"].groupby("symbol").agg(n=("pnl", "size"), pnl=("pnl", "sum"), avg_r=("r", "mean"),
                                                      win=("pnl", lambda x: (x > 0).mean()))
            print("   per symbol: " + "; ".join(f"{s}: {int(r.n)} trades, pnl {r.pnl:+.0f}, avgR {r.avg_r:.2f}, win {r.win*100:.0f}%" for s, r in per.iterrows()))
        y = yearly(res["equity"], res["trades"], a.capital)
        print("   " + "  ".join(f"{int(r.year)}: {r.ret*100:+.1f}% (DD {r.mdd*100:.0f}%, {int(r.trades)} tr)" for r in y.itertuples()))
        if res["open"]:
            print("   open at end: " + ", ".join(f"{o['symbol']} {o['qty']} @ {o['entry']:.2f} (stop {o['stop']:.2f})" for o in res["open"]))
    bh = buy_hold(frames, symbols, results[0][1]["equity"].index, a.capital, a.fee)
    mb = metrics(bh, pd.DataFrame(), a.capital, bpy, 1.0)
    print(f"\n== buy & hold equal weight ==\n   {fmt(mb)}")

    if a.trades_csv:
        results[0][1]["trades"].to_csv(a.trades_csv, index=False)
        print(f"trades written to {a.trades_csv}")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5.5), dpi=130)
        for name, res, m in results:
            ax.plot(res["equity"].index, res["equity"] / a.capital, linewidth=1.3,
                    label=f"{name}: CAGR {m['cagr']*100:.1f}%, maxDD {m['mdd']*100:.0f}%, Sharpe {m['sharpe']:.2f}")
        ax.plot(bh.index, bh / a.capital, linewidth=0.9, alpha=0.6, color="gray",
                label=f"buy & hold: CAGR {mb['cagr']*100:.1f}%, maxDD {mb['mdd']*100:.0f}%")
        ax.set_yscale("log"); ax.grid(alpha=0.3, which="both"); ax.set_ylabel("equity (x start), log scale")
        ax.set_title(f"{a.exchange} {', '.join(symbols)} {a.timeframe} | risk {a.risk}% | fee {a.fee*100:.2f}% + slippage {a.slippage*100:.2f}%")
        ax.legend(fontsize=8, loc="upper left")
        fig.tight_layout(); fig.savefig(a.plot)
        print(f"chart written to {a.plot}")


if __name__ == "__main__":
    main()

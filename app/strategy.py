"""
Strategy service (v3 "Trend-Breakout", rules in app/rules.py): evaluates once per *closed* candle per
symbol and pushes signals to Redis. The regime symbol (BTC) is fetched first; its own trend gates entries
in the other symbols.
"""
import json
import logging, math, time
import ccxt, pandas as pd, redis
from . import config, db, postmortem, rules
from .models import Signal, Decision, now_iso

log = logging.getLogger("strategy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

r = redis.from_url(config.REDIS_URL)
ex = ccxt.kraken({"enableRateLimit": True})

TF_SECONDS = ccxt.Exchange.parse_timeframe(config.TIMEFRAME)

P = rules.Params(ema_len=config.EMA_LEN, st_len=config.ST_LEN, st_mult=config.ST_MULT, adx_len=config.ADX_LEN,
                 adx_min=config.ADX_MIN, breakout_len=config.BREAKOUT_LEN, entry_mode=config.ENTRY_MODE,
                 exit_len=config.EXIT_LEN, adx_rising=config.ADX_RISING)

CHECK_LABELS = {                     # shown in /scan and on the signal card
    "supertrend_green": "Supertrend grün",
    "above_ema": f"Close > EMA{config.EMA_LEN}",
    "supertrend_flip": "Supertrend-Flip",
    "adx": f"ADX > {config.ADX_MIN:g}",
    "adx_rising": "ADX steigt",
    "breakout": f"{config.BREAKOUT_LEN}-Candle-Hoch",
    "market": f"Regime {config.REGIME_SYMBOL}",
    "stop_below_close": "Stop unter Kurs",
}


def fetch_closed_candles(symbol: str) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(symbol, config.TIMEFRAME, limit=720)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.iloc[:-1].reset_index(drop=True)          # drop the still-forming candle


def capital() -> float:
    return config.PAPER_CAPITAL + db.realized_pnl()


def emit_for_approval(sig: Signal):
    sig.id = db.insert_signal(sig, status="pending")
    r.rpush(config.Q_SIGNALS, sig.to_json())
    log.info("signal #%s %s %s %s @ %s", sig.id, sig.kind, sig.side, sig.symbol, sig.price)


def emit_auto(sig: Signal):
    sig.id = db.insert_signal(sig, status="auto")
    db.insert_decision(sig.id, "auto", "strategy")
    r.rpush(config.Q_DECISIONS, Decision(sig.id, "auto", "strategy").to_json())
    log.info("auto signal #%s %s %s", sig.id, sig.kind, sig.symbol)


def _num(v):
    v = float(v)
    return None if math.isnan(v) else v


def publish_state(symbol, ts_iso, row, prev, checks, pos, market_ok):
    """Market-check snapshot for /status, /scan and the daily report."""
    close, e, st_line = float(row["close"]), float(row["ema"]), float(row["st"])
    hh = _num(row["hh"])
    state = {
        "candle_ts": ts_iso, "evaluated_at": now_iso(),
        "close": close, "ema": e, "st": st_line, "dir": int(row["dir"]), "flip": int(row["dir"]) != int(prev["dir"]),
        "adx": _num(row["adx"]), "hh": hh, "market_ok": bool(market_ok),
        "above_ema": close > e,
        "dist_st_pct": (close - st_line) / close * 100,     # + = above line (uptrend), - = below
        "dist_ema_pct": (close - e) / close * 100,
        "dist_hh_pct": (close - hh) / close * 100 if hh else None,   # + = closed above the previous N-candle high
        "checks": checks,
        "blockers": [CHECK_LABELS.get(k, k) for k, ok in checks.items() if not ok],
        "in_position": pos is not None,
        "stop": float(pos["stop"]) if pos is not None and pos["stop"] is not None else None,
    }
    r.hset(config.K_STATE, symbol, json.dumps(state))
    r.rpush(config.K_EVALS, now_iso())
    r.ltrim(config.K_EVALS, -2000, -1)
    return state


def in_cooldown(symbol: str, df: pd.DataFrame) -> bool:
    """No new entry signal for REJECT_COOLDOWN_BARS candles after the user rejected one (or let it expire)."""
    last = db.last_entry_signal(symbol)
    if not last or last["status"] not in ("rejected", "expired"):
        return False
    bars_since = int((df["ts"] > last["candle_ts"]).sum())
    return bars_since < config.REJECT_COOLDOWN_BARS


def entry_reason(row, checks) -> str:
    bits = [f"ST grün (Linie {float(row['st']):.2f})", f"Close > EMA{config.EMA_LEN} {float(row['ema']):.2f}"]
    if "adx" in checks:
        bits.append(f"ADX {float(row['adx']):.1f} > {config.ADX_MIN:g}")
    if "adx_rising" in checks:
        bits.append("ADX steigt")
    if "breakout" in checks:
        bits.append(f"neues {config.BREAKOUT_LEN}-Candle-Hoch (> {float(row['hh']):.2f})")
    if config.REGIME_SYMBOL:
        bits.append(f"Regime {config.REGIME_SYMBOL} ✓")
    return "Trend-Breakout: " + ", ".join(bits)


def evaluate(symbol: str, df: pd.DataFrame, market_ok: bool = True):
    if len(df) < P.warmup:
        log.warning("%s: not enough candles (%d < %d)", symbol, len(df), P.warmup)
        return
    last_ts = df["ts"].iloc[-1]
    ts_iso = last_ts.isoformat()
    key = f"k:last:{symbol}"
    if (r.get(key) or b"").decode() == ts_iso:
        return                                            # this candle was already processed

    d = rules.prepare(df, P)
    row, prev = d.iloc[-1], d.iloc[-2]
    close, st_line = float(row["close"]), float(row["st"])
    checks = rules.entry_checks(row, prev, P, market_ok)
    log.info("%s close=%.2f ema=%.2f st=%.2f dir=%d adx=%.1f hh=%s market=%s checks=%s", symbol, close, float(row["ema"]),
             st_line, int(row["dir"]), float(row["adx"]), f"{float(row['hh']):.2f}" if _num(row["hh"]) else "-", market_ok,
             ",".join(k for k, ok in checks.items() if not ok) or "all ok")

    pos = db.open_position_for(symbol)
    state = publish_state(symbol, ts_iso, row, prev, checks, pos, market_ok)

    if pos is None:
        if all(checks.values()):
            if len(db.open_positions()) >= config.MAX_POSITIONS:
                log.info("%s: entry conditions met but MAX_POSITIONS=%d reached", symbol, config.MAX_POSITIONS)
            elif in_cooldown(symbol, df):
                log.info("%s: entry conditions met but in cooldown after a rejected signal", symbol)
            else:
                cap = capital()
                risk = min(config.RISK_PCT, rules.available_risk_pct(cap, db.open_positions(), config.MAX_OPEN_RISK_PCT))
                qty = rules.risk_qty(cap, risk, close, st_line, config.MAX_NOTIONAL_PCT)
                if qty:
                    context = {k: v for k, v in state.items() if k not in ("evaluated_at", "in_position", "stop")}
                    emit_for_approval(Signal(None, ts_iso, symbol, "entry", "buy", close, st_line, qty,
                                             entry_reason(row, checks), context=context))
    else:
        current_stop = float(pos["stop"]) if pos["stop"] is not None else None
        new_stop = rules.trail_stop(row, current_stop)              # trails upward only
        if current_stop is None or new_stop > current_stop:
            db.update_stop(pos["id"], new_stop, old_stop=current_stop, candle_ts=ts_iso, reason="Supertrend trail")
        exit_reason = rules.exit_reason(row, new_stop, P)
        if exit_reason:
            held = postmortem.candles_since(df, pos["opened_at"], TF_SECONDS) if pos.get("opened_at") else df.iloc[0:0]
            exc = postmortem.excursion(held, float(pos["entry_price"]))
            sig = Signal(None, ts_iso, symbol, "exit", "sell", close, None, float(pos["qty"]), exit_reason, context=exc)
            if config.AUTO_EXIT:
                emit_auto(sig)
            else:
                emit_for_approval(sig)

    r.set(key, ts_iso)


def market_regime(frames: dict[str, pd.DataFrame], asof=None) -> bool:
    """Fail closed when the enabled regime is missing or not aligned with the traded candle."""
    sym = config.REGIME_SYMBOL
    if not sym:
        return True
    if sym not in frames or len(frames[sym]) < P.warmup:
        log.warning("regime symbol %s has no data yet, blocking new entries", sym)
        return False
    if asof is not None and frames[sym]["ts"].iloc[-1] != asof:
        log.warning("regime symbol %s candle is not aligned, blocking new entries", sym)
        return False
    return rules.market_uptrend(rules.prepare(frames[sym], P).iloc[-1])


def main():
    db.init_schema()
    log.info("strategy up: %s %s EMA%d ST(%d,%.1f) ADX(%d)>%g breakout=%d regime=%s entry=%s broker=%s",
             config.SYMBOLS, config.TIMEFRAME, config.EMA_LEN, config.ST_LEN, config.ST_MULT, config.ADX_LEN, config.ADX_MIN,
             config.BREAKOUT_LEN, config.REGIME_SYMBOL or "off", config.ENTRY_MODE, config.BROKER)
    needed = list(dict.fromkeys(config.SYMBOLS + ([config.REGIME_SYMBOL] if config.REGIME_SYMBOL else [])))
    while True:
        frames = {}
        for symbol in needed:
            try:
                frames[symbol] = fetch_closed_candles(symbol)
            except Exception as exc:                      # keep the loop alive
                log.exception("%s: fetch failed: %s", symbol, exc)
        for symbol in config.SYMBOLS:
            if symbol not in frames:
                continue
            try:
                market_ok = market_regime(frames, frames[symbol]["ts"].iloc[-1]) if symbol != config.REGIME_SYMBOL else True
                evaluate(symbol, frames[symbol], market_ok)
            except Exception as exc:
                log.exception("%s: %s", symbol, exc)
        time.sleep(config.POLL_SECONDS)


if __name__ == "__main__":
    main()

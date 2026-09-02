"""
Strategy service: EMA-200 regime filter + Supertrend(10,3) entries/exits, long-only.
Evaluates once per *closed* candle per symbol and pushes signals to Redis.
"""
import json
import logging, time
import ccxt, pandas as pd, redis
from . import config, db
from .indicators import ema, supertrend
from .models import Signal, Decision, now_iso

log = logging.getLogger("strategy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

r = redis.from_url(config.REDIS_URL)
ex = ccxt.kraken({"enableRateLimit": True})


def fetch_closed_candles(symbol: str) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(symbol, config.TIMEFRAME, limit=720)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.iloc[:-1].reset_index(drop=True)          # drop the still-forming candle


def risk_qty(entry: float, stop: float) -> float | None:
    capital = config.PAPER_CAPITAL + db.realized_pnl()
    risk_amount = capital * config.RISK_PCT / 100
    per_unit = entry - stop
    if per_unit <= 0:
        return None
    return round(risk_amount / per_unit, 6)


def emit_for_approval(sig: Signal):
    sig.id = db.insert_signal(sig, status="pending")
    r.rpush(config.Q_SIGNALS, sig.to_json())
    log.info("signal #%s %s %s %s @ %s", sig.id, sig.kind, sig.side, sig.symbol, sig.price)


def emit_auto(sig: Signal):
    sig.id = db.insert_signal(sig, status="auto")
    db.insert_decision(sig.id, "auto", "strategy")
    r.rpush(config.Q_DECISIONS, Decision(sig.id, "auto", "strategy").to_json())
    log.info("auto signal #%s %s %s", sig.id, sig.kind, sig.symbol)


def publish_state(symbol, ts_iso, close, e, st_line, d_now, d_prev):
    """Market-check snapshot for /status and the daily report."""
    state = {
        "candle_ts": ts_iso, "evaluated_at": now_iso(),
        "close": close, "ema": e, "st": st_line, "dir": d_now, "flip": d_now != d_prev,
        "above_ema": close > e,
        "dist_st_pct": (close - st_line) / close * 100,     # + = above line (uptrend), - = below
        "dist_ema_pct": (close - e) / close * 100,
    }
    r.hset(config.K_STATE, symbol, json.dumps(state))
    r.rpush(config.K_EVALS, now_iso())
    r.ltrim(config.K_EVALS, -2000, -1)


def evaluate(symbol: str):
    df = fetch_closed_candles(symbol)
    if len(df) < config.EMA_LEN + 5:
        log.warning("%s: not enough candles (%d)", symbol, len(df))
        return
    last_ts = df["ts"].iloc[-1]
    ts_iso = last_ts.isoformat()
    key = f"k:last:{symbol}"
    if (r.get(key) or b"").decode() == ts_iso:
        return                                            # this candle was already processed

    df["ema"] = ema(df["close"], config.EMA_LEN)
    st = supertrend(df, config.ST_LEN, config.ST_MULT)
    close, e = float(df["close"].iloc[-1]), float(df["ema"].iloc[-1])
    st_line = float(st["st"].iloc[-1])
    d_now, d_prev = int(st["dir"].iloc[-1]), int(st["dir"].iloc[-2])
    log.info("%s close=%.2f ema=%.2f st=%.2f dir=%d (prev %d)", symbol, close, e, st_line, d_now, d_prev)
    publish_state(symbol, ts_iso, close, e, st_line, d_now, d_prev)

    pos = db.open_position_for(symbol)
    if pos is None:
        if d_prev == -1 and d_now == 1 and close > e:
            qty = risk_qty(close, st_line)
            if qty:
                emit_for_approval(Signal(
                    None, ts_iso, symbol, "entry", "buy", close, st_line, qty,
                    f"Supertrend flipped green, close {close:.2f} > EMA{config.EMA_LEN} {e:.2f}"))
    else:
        current_stop = float(pos["stop"]) if pos["stop"] is not None else st_line
        new_stop = max(current_stop, st_line) if d_now == 1 else current_stop   # trail upward only
        if pos["stop"] is None or new_stop > current_stop:
            db.update_stop(pos["id"], new_stop)

        exit_reason = None
        if d_now == -1:
            exit_reason = "Supertrend flipped red"
        elif close <= new_stop:
            exit_reason = f"Close {close:.2f} below stop {new_stop:.2f}"
        if exit_reason:
            sig = Signal(None, ts_iso, symbol, "exit", "sell", close, None, float(pos["qty"]), exit_reason)
            if config.AUTO_EXIT:
                emit_auto(sig)
            else:
                emit_for_approval(sig)

    r.set(key, ts_iso)


def main():
    db.init_schema()
    log.info("strategy up: %s %s EMA%d ST(%d,%.1f) broker=%s", config.SYMBOLS, config.TIMEFRAME,
             config.EMA_LEN, config.ST_LEN, config.ST_MULT, config.BROKER)
    while True:
        for symbol in config.SYMBOLS:
            try:
                evaluate(symbol)
            except Exception as exc:                      # keep the loop alive
                log.exception("%s: %s", symbol, exc)
        time.sleep(config.POLL_SECONDS)


if __name__ == "__main__":
    main()

"""
Executor: consumes approved/auto decisions, applies risk checks, places orders via the broker,
records fills and positions, notifies the user.
"""
import logging, math, time
from datetime import datetime, timezone
import redis
from . import config, db, postmortem, rules
from .brokers import get_broker
from .models import Decision

log = logging.getLogger("executor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

r = redis.from_url(config.REDIS_URL)
broker = get_broker()

def notify(text: str):
    r.rpush(config.Q_NOTIFY, text)

def handle(dec: Decision):
    sig = db.get_signal(dec.signal_id)
    if not sig:
        log.warning("unknown signal %s", dec.signal_id); return

    if sig.kind == "entry":
        if r.get(config.K_HALTED):
            db.set_signal_status(sig.id, "failed")
            notify(f"🛑 Signal #{sig.id} skipped: bot is halted."); return
        positions = db.open_positions()
        if len(positions) >= config.MAX_POSITIONS:
            db.set_signal_status(sig.id, "failed")
            notify(f"⚠️ Signal #{sig.id} skipped: max positions ({config.MAX_POSITIONS}) reached."); return
        if db.open_position_for(sig.symbol):
            db.set_signal_status(sig.id, "failed")
            notify(f"⚠️ Signal #{sig.id} skipped: already in {sig.symbol}."); return
        # Approval can arrive long after the candle. Never increase the approved quantity,
        # and recheck the risk budget against a fresh quote before submitting the order.
        quote = broker.last_price(sig.symbol)
        cap = config.PAPER_CAPITAL + db.realized_pnl()
        risk = min(config.RISK_PCT, rules.available_risk_pct(cap, positions, config.MAX_OPEN_RISK_PCT))
        qty = rules.risk_qty(cap, risk, quote, sig.stop, config.MAX_NOTIONAL_PCT) if sig.stop is not None else None
        if not qty or not math.isfinite(quote) or not sig.qty or not math.isfinite(sig.qty) or sig.qty <= 0 or quote <= sig.stop:
            db.set_signal_status(sig.id, "failed")
            notify(f"⚠️ Signal #{sig.id} skipped: invalid price/stop or no remaining risk budget."); return
        fill = broker.market_buy(sig.symbol, min(sig.qty, qty))
        db.insert_trade(sig.id, sig.symbol, "buy", fill.qty, fill.price, broker.name, fill.order_id)
        db.open_position(sig.symbol, fill.qty, fill.price, sig.stop, entry_signal_id=sig.id)
        db.set_signal_status(sig.id, "executed")
        notify(f"🟢 <b>BOUGHT {sig.symbol}</b> {fill.qty} @ {fill.price:.2f} (stop {sig.stop:.2f}) [{broker.name}]")

    elif sig.kind == "exit":
        pos = db.open_position_for(sig.symbol)
        if not pos:
            db.set_signal_status(sig.id, "failed")
            notify(f"⚠️ Exit #{sig.id}: no open position in {sig.symbol}."); return
        fill = broker.market_sell(sig.symbol, float(pos["qty"]))
        db.insert_trade(sig.id, sig.symbol, "sell", fill.qty, fill.price, broker.name, fill.order_id)
        entry_sig = db.get_signal(pos["entry_signal_id"]) if pos.get("entry_signal_id") else None
        line, stats = postmortem.summary(float(pos["entry_price"]), fill.price, entry_sig.stop if entry_sig else None,
                                         pos["opened_at"], datetime.now(timezone.utc), sig.context,
                                         entry_sig.context if entry_sig else None)
        pnl = db.close_position(pos["id"], fill.price, **stats)
        db.set_signal_status(sig.id, "executed")
        pct = pnl / (float(pos["entry_price"]) * float(pos["qty"])) * 100
        notify(f"🔴 <b>SOLD {sig.symbol}</b> {fill.qty} @ {fill.price:.2f} → PnL {pnl:+.2f} USD ({pct:+.2f}%)\n"
               f"Reason: {sig.reason}\n{line}")

def main():
    db.init_schema()
    log.info("executor up, broker=%s", broker.name)
    notify(f"🤖 Executor started (broker: <b>{broker.name}</b>).")
    while True:
        try:
            _, raw = r.blpop(config.Q_DECISIONS)
            dec = Decision.from_json(raw.decode())
            if dec.decision in ("yes", "auto"):
                handle(dec)
        except Exception as exc:
            log.exception("executor: %s", exc)
            time.sleep(2)

if __name__ == "__main__":
    main()

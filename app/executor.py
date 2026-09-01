"""
Executor: consumes approved/auto decisions, applies risk checks, places orders via the broker,
records fills and positions, notifies the user.
"""
import logging, time
import redis
from . import config, db
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
        if len(db.open_positions()) >= config.MAX_POSITIONS:
            db.set_signal_status(sig.id, "failed")
            notify(f"⚠️ Signal #{sig.id} skipped: max positions ({config.MAX_POSITIONS}) reached."); return
        if db.open_position_for(sig.symbol):
            db.set_signal_status(sig.id, "failed")
            notify(f"⚠️ Signal #{sig.id} skipped: already in {sig.symbol}."); return
        fill = broker.market_buy(sig.symbol, sig.qty)
        db.insert_trade(sig.id, sig.symbol, "buy", fill.qty, fill.price, broker.name, fill.order_id)
        db.open_position(sig.symbol, fill.qty, fill.price, sig.stop)
        db.set_signal_status(sig.id, "executed")
        notify(f"🟢 <b>BOUGHT {sig.symbol}</b> {fill.qty} @ {fill.price:.2f} (stop {sig.stop:.2f}) [{broker.name}]")

    elif sig.kind == "exit":
        pos = db.open_position_for(sig.symbol)
        if not pos:
            db.set_signal_status(sig.id, "failed")
            notify(f"⚠️ Exit #{sig.id}: no open position in {sig.symbol}."); return
        fill = broker.market_sell(sig.symbol, float(pos["qty"]))
        db.insert_trade(sig.id, sig.symbol, "sell", fill.qty, fill.price, broker.name, fill.order_id)
        pnl = db.close_position(pos["id"], fill.price)
        db.set_signal_status(sig.id, "executed")
        pct = pnl / (float(pos["entry_price"]) * float(pos["qty"])) * 100
        notify(f"🔴 <b>SOLD {sig.symbol}</b> {fill.qty} @ {fill.price:.2f} → PnL {pnl:+.2f} USD ({pct:+.2f}%)\n"
               f"Reason: {sig.reason}")

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

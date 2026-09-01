"""
Telegram bot: presents signals with Yes/No inline buttons, forwards decisions to the executor,
relays notifications, exposes /status /positions /halt /resume.
"""
import asyncio, logging
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from . import config, db
from .models import Signal, Decision

log = logging.getLogger("bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

bot = Bot(config.TELEGRAM_TOKEN)
dp = Dispatcher()
r = aioredis.from_url(config.REDIS_URL)
UID = config.ALLOWED_USER_ID

def allowed(uid: int) -> bool:
    return uid == UID

def keyboard(signal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Yes", callback_data=f"dec:{signal_id}:yes"),
        InlineKeyboardButton(text="❌ No",  callback_data=f"dec:{signal_id}:no"),
    ]])

def card(sig: Signal) -> str:
    lines = [f"📈 <b>{sig.kind.upper()} {sig.side.upper()} {sig.symbol}</b>  #{sig.id}",
             f"Candle: {sig.candle_ts[:16].replace('T', ' ')} UTC",
             f"Price: {sig.price:.2f}"]
    if sig.stop:
        risk_pct = (sig.price - sig.stop) / sig.price * 100
        lines.append(f"Stop: {sig.stop:.2f}  ({risk_pct:.1f}% below)")
    if sig.qty:
        lines.append(f"Qty: {sig.qty}  (≈ {sig.qty * sig.price:.0f} USD)")
    lines.append(f"Reason: {sig.reason}")
    lines.append(f"⏳ expires in {config.SIGNAL_TTL_MIN} min")
    return "\n".join(lines)

async def expire_later(signal_id: int, chat_id: int, message_id: int):
    await asyncio.sleep(config.SIGNAL_TTL_MIN * 60)
    if db.signal_status(signal_id) == "pending":
        db.set_signal_status(signal_id, "expired")
        db.insert_decision(signal_id, "expired", "timeout")
        try:
            await bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            await bot.send_message(chat_id, f"⌛ Signal #{signal_id} expired (no answer).")
        except Exception:
            pass

async def consume_signals():
    while True:
        try:
            _, raw = await r.blpop(config.Q_SIGNALS)
            sig = Signal.from_json(raw.decode())
            msg = await bot.send_message(UID, card(sig), parse_mode="HTML", reply_markup=keyboard(sig.id))
            asyncio.create_task(expire_later(sig.id, msg.chat.id, msg.message_id))
        except Exception as exc:
            log.exception("consume_signals: %s", exc)
            await asyncio.sleep(2)

async def consume_notify():
    while True:
        try:
            _, raw = await r.blpop(config.Q_NOTIFY)
            await bot.send_message(UID, raw.decode(), parse_mode="HTML")
        except Exception as exc:
            log.exception("consume_notify: %s", exc)
            await asyncio.sleep(2)

@dp.callback_query(F.data.startswith("dec:"))
async def on_decision(cb: CallbackQuery):
    if not allowed(cb.from_user.id):
        await cb.answer("Not authorised", show_alert=True); return
    _, sid, ans = cb.data.split(":")
    sid = int(sid)
    if db.signal_status(sid) != "pending":
        await cb.answer("Already decided / expired"); return
    db.set_signal_status(sid, "approved" if ans == "yes" else "rejected")
    db.insert_decision(sid, ans, str(cb.from_user.id))
    if ans == "yes":
        await r.rpush(config.Q_DECISIONS, Decision(sid, "yes", str(cb.from_user.id)).to_json())
    await cb.message.edit_text(cb.message.html_text + f"\n\n{'✅ Approved' if ans == 'yes' else '❌ Rejected'}",
                               parse_mode="HTML")
    await cb.answer()

@dp.message(Command("status"))
async def cmd_status(m: Message):
    if not allowed(m.from_user.id): return
    s = db.stats()
    halted = await r.get(config.K_HALTED)
    equity = config.PAPER_CAPITAL + s["pnl"]
    wr = f"{s['wins'] / s['closed'] * 100:.0f}%" if s["closed"] else "n/a"
    await m.answer(
        f"Broker: <b>{config.BROKER}</b>  {'🛑 HALTED' if halted else '🟢 running'}\n"
        f"Equity: {equity:.2f} USD (realized PnL {s['pnl']:+.2f})\n"
        f"Closed trades: {s['closed']}, win rate {wr}\n"
        f"Open positions: {len(db.open_positions())}/{config.MAX_POSITIONS}\n"
        f"Strategy: {config.TIMEFRAME} EMA{config.EMA_LEN} + ST({config.ST_LEN},{config.ST_MULT})",
        parse_mode="HTML")

@dp.message(Command("positions"))
async def cmd_positions(m: Message):
    if not allowed(m.from_user.id): return
    pos = db.open_positions()
    if not pos:
        await m.answer("No open positions."); return
    lines = [f"• {p['symbol']}: {float(p['qty'])} @ {float(p['entry_price']):.2f}, stop {float(p['stop'] or 0):.2f}"
             for p in pos]
    await m.answer("\n".join(lines))

@dp.message(Command("halt"))
async def cmd_halt(m: Message):
    if not allowed(m.from_user.id): return
    await r.set(config.K_HALTED, "1")
    await m.answer("🛑 Halted: no new entries will be executed. Exits still run. /resume to continue.")

@dp.message(Command("resume"))
async def cmd_resume(m: Message):
    if not allowed(m.from_user.id): return
    await r.delete(config.K_HALTED)
    await m.answer("🟢 Resumed.")

@dp.message(Command("start", "help"))
async def cmd_help(m: Message):
    if not allowed(m.from_user.id):
        await m.answer(f"Your id is {m.from_user.id}. This bot is private."); return
    await m.answer("Commands: /status /positions /halt /resume")

async def main():
    db.init_schema()
    log.info("bot up, allowed user %s", UID)
    asyncio.create_task(consume_signals())
    asyncio.create_task(consume_notify())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

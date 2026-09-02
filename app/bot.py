"""
Telegram bot: presents signals with Yes/No inline buttons, forwards decisions to the executor,
relays notifications, exposes /status /positions /halt /resume.
"""
import asyncio, logging
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from . import config, db, report
from aiogram.types import BufferedInputFile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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

# ---------- auto-approve ----------

async def auto_mode() -> str:
    v = await r.get(config.K_AUTO)
    return v.decode() if v else config.AUTO_APPROVE

def in_quiet_hours() -> bool:
    try:
        start, end = (int(x) for x in config.QUIET_HOURS.split("-"))
    except ValueError:
        return False
    h = datetime.now(ZoneInfo(config.REPORT_TZ)).hour
    return (start <= h or h < end) if start > end else (start <= h < end)

def auto_allowed() -> bool:
    return config.BROKER == "paper" or config.AUTO_APPROVE_LIVE == "I_UNDERSTAND"

async def should_auto_approve() -> str | None:
    """Returns a reason string if the signal should be approved without asking, else None."""
    if not auto_allowed():
        return None
    mode = await auto_mode()
    if mode == "always":
        return "auto (mode: always)"
    if mode == "night" and in_quiet_hours():
        return f"auto (quiet hours {config.QUIET_HOURS})"
    return None

async def approve(signal_id: int, by: str):
    db.set_signal_status(signal_id, "approved")
    db.insert_decision(signal_id, "yes", by)
    await r.rpush(config.Q_DECISIONS, Decision(signal_id, "yes", by).to_json())

async def expire_later(signal_id: int, chat_id: int, message_id: int):
    await asyncio.sleep(config.SIGNAL_TTL_MIN * 60)
    if db.signal_status(signal_id) != "pending":
        return
    try:
        await bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
    except Exception:
        pass
    if config.AUTO_ON_EXPIRE and auto_allowed():
        await approve(signal_id, "auto-expire")
        await bot.send_message(chat_id, f"🤖 Signal #{signal_id}: keine Antwort in {config.SIGNAL_TTL_MIN} min → automatisch freigegeben.")
    else:
        db.set_signal_status(signal_id, "expired")
        db.insert_decision(signal_id, "expired", "timeout")
        await bot.send_message(chat_id, f"⌛ Signal #{signal_id} verfallen (keine Antwort).")

async def consume_signals():
    while True:
        try:
            _, raw = await r.blpop(config.Q_SIGNALS)
            sig = Signal.from_json(raw.decode())
            reason = await should_auto_approve()
            if reason:
                await approve(sig.id, reason)
                await bot.send_message(UID, card(sig) + f"\n\n🤖 <i>{reason}</i>", parse_mode="HTML")
                continue
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
        f"Strategy: {config.TIMEFRAME} EMA{config.EMA_LEN} + ST({config.ST_LEN},{config.ST_MULT})\n"
        f"Auto-Entscheider: {await auto_mode()}\n\n"
        + await asyncio.to_thread(report.market_check),
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

@dp.message(Command("auto"))
async def cmd_auto(m: Message):
    if not allowed(m.from_user.id): return
    arg = (m.text.split(maxsplit=1)[1].strip().lower() if len(m.text.split()) > 1 else "")
    if arg in ("off", "night", "always"):
        await r.set(config.K_AUTO, arg)
    elif arg:
        await m.answer("Usage: /auto off | night | always"); return
    mode = await auto_mode()
    live_note = "" if auto_allowed() else "\n⚠️ Live-Broker: Auto-Freigabe deaktiviert (AUTO_APPROVE_LIVE fehlt)"
    await m.answer(f"Auto-Entscheider: <b>{mode}</b>"
                   f"{' – aktiv, Ruhezeit ' + config.QUIET_HOURS if mode == 'night' else ''}\n"
                   f"Ohne Antwort nach {config.SIGNAL_TTL_MIN} min: "
                   f"{'automatisch freigeben' if config.AUTO_ON_EXPIRE else 'verwerfen'}{live_note}",
                   parse_mode="HTML")

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

async def send_report(chat_id: int):
    m = await asyncio.to_thread(report.mark_to_market)
    text = await asyncio.to_thread(report.build_text, m)
    png = await asyncio.to_thread(report.build_chart, 30)
    if png:
        await bot.send_photo(chat_id, BufferedInputFile(png, "equity.png"), caption=text, parse_mode="HTML")
    else:
        await bot.send_message(chat_id, text + "\n\n<i>Chart folgt, sobald genügend Datenpunkte vorliegen.</i>",
                               parse_mode="HTML")

@dp.message(Command("scan"))
async def cmd_scan(m: Message):
    if not allowed(m.from_user.id): return
    await m.answer(await asyncio.to_thread(report.market_check), parse_mode="HTML")

@dp.message(Command("report"))
async def cmd_report(m: Message):
    if not allowed(m.from_user.id): return
    await send_report(m.chat.id)

async def snapshot_loop():
    while True:
        try:
            await asyncio.to_thread(report.snapshot)
        except Exception as exc:
            log.warning("snapshot failed: %s", exc)
        await asyncio.sleep(config.SNAPSHOT_MINUTES * 60)

async def daily_report_loop():
    if config.REPORT_HOUR < 0:
        return
    tz = ZoneInfo(config.REPORT_TZ)
    while True:
        now = datetime.now(tz)
        nxt = now.replace(hour=config.REPORT_HOUR, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            await send_report(UID)
        except Exception as exc:
            log.warning("daily report failed: %s", exc)

@dp.message(Command("start", "help"))
async def cmd_help(m: Message):
    if not allowed(m.from_user.id):
        await m.answer(f"Your id is {m.from_user.id}. This bot is private."); return
    await m.answer("Commands: /status /scan /positions /report /auto /halt /resume")

async def main():
    db.init_schema()
    log.info("bot up, allowed user %s", UID)
    asyncio.create_task(consume_signals())
    asyncio.create_task(consume_notify())
    asyncio.create_task(snapshot_loop())
    asyncio.create_task(daily_report_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

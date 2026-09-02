"""Portfolio report: mark-to-market, text summary and equity-curve chart (PNG bytes)."""
import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import ccxt
import redis
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from . import config, db

_ex = ccxt.kraken({"enableRateLimit": True})
_r = redis.from_url(config.REDIS_URL)


def market_check(hours: int = 24) -> str:
    """Per-symbol state of the last evaluation + how many candles were evaluated in the last N hours."""
    tz = ZoneInfo(config.REPORT_TZ)
    since = datetime.now(tz).timestamp() - hours * 3600
    evals = [t.decode() for t in _r.lrange(config.K_EVALS, 0, -1)]
    n_recent = sum(1 for t in evals if datetime.fromisoformat(t).timestamp() >= since)
    last_eval = max(evals) if evals else None
    lines = [f"<b>Markt-Check</b> ({n_recent} Candle-Auswertungen in {hours} h"
             + (f", letzte {datetime.fromisoformat(last_eval).astimezone(tz).strftime('%H:%M')})" if last_eval else ")")]
    states = _r.hgetall(config.K_STATE)
    if not states:
        lines.append("• noch keine Auswertung")
        return "\n".join(lines)
    for sym in config.SYMBOLS:
        raw = states.get(sym.encode())
        if not raw:
            lines.append(f"• {sym}: noch keine Daten"); continue
        st = json.loads(raw)
        trend = "🟢 Uptrend" if st["dir"] == 1 else "🔴 Downtrend"
        ema_ok = "über" if st["above_ema"] else "unter"
        if st["dir"] == 1:
            why = f"Stop-Linie {abs(st['dist_st_pct']):.1f} % unter Kurs"
            hint = "wartet auf Flip rot→grün" if not st["flip"] else "Flip auf grün!"
        else:
            why = f"Flip braucht +{abs(st['dist_st_pct']):.1f} %"
            hint = "" if st["above_ema"] else "und Kurs muss über EMA"
        lines.append(f"• {sym}: {trend}, {ema_ok} EMA{config.EMA_LEN} ({st['dist_ema_pct']:+.1f} %), {why}"
                     + (f" – {hint}" if hint else ""))
    return "\n".join(lines)


def _price(symbol: str) -> float:
    return float(_ex.fetch_ticker(symbol)["last"])


def mark_to_market() -> dict:
    """Equity = paper capital + realized PnL + unrealized PnL of open positions."""
    realized = db.realized_pnl()
    positions = []
    unrealized = 0.0
    for p in db.open_positions():
        qty, entry = float(p["qty"]), float(p["entry_price"])
        try:
            last = _price(p["symbol"])
        except Exception:
            last = entry
        upnl = (last - entry) * qty
        unrealized += upnl
        positions.append({**p, "last": last, "upnl": upnl,
                          "upnl_pct": (last - entry) / entry * 100})
    equity = config.PAPER_CAPITAL + realized + unrealized
    return {"equity": equity, "realized": realized, "unrealized": unrealized, "positions": positions}


def snapshot():
    m = mark_to_market()
    db.insert_snapshot(m["equity"], m["realized"], m["unrealized"])
    return m


def build_text(m: dict) -> str:
    s = db.stats()
    sc = db.signal_counts()
    tz = ZoneInfo(config.REPORT_TZ)
    now = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    total_pct = (m["equity"] - config.PAPER_CAPITAL) / config.PAPER_CAPITAL * 100
    wr = f"{s['wins'] / s['closed'] * 100:.0f} %" if s["closed"] else "n/a"

    lines = [f"📊 <b>Portfolio-Report</b> – {now}",
             f"Equity: <b>{m['equity']:,.2f} USD</b> ({total_pct:+.2f} % seit Start)",
             f"Realisiert: {m['realized']:+,.2f}  |  Offen: {m['unrealized']:+,.2f}",
             f"Trades: {s['closed']} geschlossen, Trefferquote {wr}",
             f"Signale: {sc.get('executed', 0)} ausgeführt, {sc.get('rejected', 0)} abgelehnt, "
             f"{sc.get('expired', 0)} verfallen, {sc.get('pending', 0)} offen",
             ""]
    try:
        lines += [market_check(), ""]
    except Exception as exc:
        lines += [f"Markt-Check nicht verfügbar: {exc}", ""]
    if m["positions"]:
        lines.append("<b>Offene Positionen</b>")
        for p in m["positions"]:
            lines.append(f"• {p['symbol']}: {float(p['qty'])} @ {float(p['entry_price']):.2f} → {p['last']:.2f} "
                         f"({p['upnl']:+.2f} USD, {p['upnl_pct']:+.2f} %), Stop {float(p['stop'] or 0):.2f}")
    else:
        lines.append("Keine offenen Positionen")

    last = db.closed_positions(5)
    if last:
        lines += ["", "<b>Letzte Trades</b>"]
        for p in last:
            pct = (float(p["exit_price"]) - float(p["entry_price"])) / float(p["entry_price"]) * 100
            lines.append(f"• {p['closed_at'].astimezone(tz).strftime('%d.%m')} {p['symbol']} "
                         f"{float(p['pnl']):+.2f} USD ({pct:+.2f} %)")
    return "\n".join(lines)


def build_chart(days: int = 30) -> bytes | None:
    rows = db.snapshots(days)
    if len(rows) < 2:
        return None
    ts = [r["ts"] for r in rows]
    eq = [float(r["equity"]) for r in rows]
    tz = ZoneInfo(config.REPORT_TZ)

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    ax.plot(ts, eq, linewidth=1.8)
    ax.axhline(config.PAPER_CAPITAL, linestyle="--", linewidth=1, alpha=0.6)
    ax.fill_between(ts, config.PAPER_CAPITAL, eq,
                    where=[e >= config.PAPER_CAPITAL for e in eq], alpha=0.15)
    ax.fill_between(ts, config.PAPER_CAPITAL, eq,
                    where=[e < config.PAPER_CAPITAL for e in eq], alpha=0.15)
    for p in db.closed_positions(50):
        if p["closed_at"] >= ts[0]:
            i = min(range(len(ts)), key=lambda k: abs((ts[k] - p["closed_at"]).total_seconds()))
            win = float(p["pnl"]) >= 0
            ax.plot(ts[i], eq[i], marker="^" if win else "v", color="green" if win else "red",
                    markersize=7, linestyle="none")
    ax.set_title(f"Equity – letzte {days} Tage ({config.BROKER})")
    ax.set_ylabel("USD")
    span_days = (ts[-1] - ts[0]).total_seconds() / 86400
    fmt = "%d.%m %H:%M" if span_days < 3 else "%d.%m"
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt, tz=tz))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()

"""Thin synchronous Postgres journal (psycopg 3)."""
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from . import config
from .models import Signal


def conn():
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row, autocommit=True)


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "init.sql"


def init_schema(retries: int = 30, delay: float = 2.0):
    """Create tables if missing (idempotent). Retries while Postgres is still starting."""
    sql = SCHEMA_PATH.read_text()
    for attempt in range(retries):
        try:
            with conn() as c:
                c.execute(sql)
            return
        except psycopg.OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)


# ---------- signals / decisions ----------

def insert_signal(sig: Signal, status="pending") -> int:
    with conn() as c:
        row = c.execute(
            """INSERT INTO signals(candle_ts,symbol,kind,side,price,stop,qty,reason,status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (sig.candle_ts, sig.symbol, sig.kind, sig.side, sig.price, sig.stop, sig.qty, sig.reason, status),
        ).fetchone()
        return row["id"]


def get_signal(signal_id: int) -> Signal | None:
    with conn() as c:
        r = c.execute("SELECT * FROM signals WHERE id=%s", (signal_id,)).fetchone()
    if not r:
        return None
    return Signal(
        r["id"], r["candle_ts"].isoformat(), r["symbol"], r["kind"], r["side"],
        float(r["price"]),
        float(r["stop"]) if r["stop"] is not None else None,
        float(r["qty"]) if r["qty"] is not None else None,
        r["reason"] or "",
    )


def signal_status(signal_id: int) -> str | None:
    with conn() as c:
        r = c.execute("SELECT status FROM signals WHERE id=%s", (signal_id,)).fetchone()
    return r["status"] if r else None


def set_signal_status(signal_id: int, status: str):
    with conn() as c:
        c.execute("UPDATE signals SET status=%s WHERE id=%s", (status, signal_id))


def insert_decision(signal_id: int, decision: str, by: str):
    with conn() as c:
        c.execute("INSERT INTO decisions(signal_id,decision,decided_by) VALUES (%s,%s,%s)",
                  (signal_id, decision, by))


# ---------- positions / trades ----------

def open_positions() -> list[dict]:
    with conn() as c:
        return c.execute("SELECT * FROM positions WHERE status='open' ORDER BY opened_at").fetchall()


def open_position_for(symbol: str) -> dict | None:
    with conn() as c:
        return c.execute("SELECT * FROM positions WHERE status='open' AND symbol=%s", (symbol,)).fetchone()


def open_position(symbol: str, qty: float, price: float, stop: float | None) -> int:
    with conn() as c:
        return c.execute(
            "INSERT INTO positions(symbol,qty,entry_price,stop) VALUES (%s,%s,%s,%s) RETURNING id",
            (symbol, qty, price, stop),
        ).fetchone()["id"]


def update_stop(position_id: int, stop: float):
    with conn() as c:
        c.execute("UPDATE positions SET stop=%s WHERE id=%s", (stop, position_id))


def close_position(position_id: int, exit_price: float) -> float:
    with conn() as c:
        p = c.execute("SELECT qty, entry_price FROM positions WHERE id=%s", (position_id,)).fetchone()
        pnl = (exit_price - float(p["entry_price"])) * float(p["qty"])
        c.execute(
            "UPDATE positions SET status='closed', closed_at=now(), exit_price=%s, pnl=%s WHERE id=%s",
            (exit_price, pnl, position_id),
        )
        return pnl


def insert_trade(signal_id, symbol, side, qty, price, broker, order_id=None):
    with conn() as c:
        c.execute(
            """INSERT INTO trades(signal_id,symbol,side,qty,price,broker,order_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (signal_id, symbol, side, qty, price, broker, order_id),
        )


def realized_pnl() -> float:
    with conn() as c:
        r = c.execute("SELECT COALESCE(SUM(pnl),0) AS pnl FROM positions WHERE status='closed'").fetchone()
    return float(r["pnl"])


def stats() -> dict:
    with conn() as c:
        r = c.execute(
            """SELECT COUNT(*) AS n,
                      COUNT(*) FILTER (WHERE pnl > 0) AS wins,
                      COALESCE(SUM(pnl),0) AS pnl
               FROM positions WHERE status='closed'"""
        ).fetchone()
    return {"closed": r["n"], "wins": r["wins"], "pnl": float(r["pnl"])}


# ---------- reporting ----------

def insert_snapshot(equity: float, realized: float, unrealized: float):
    with conn() as c:
        c.execute("INSERT INTO equity_snapshots(equity,realized,unrealized) VALUES (%s,%s,%s)",
                  (equity, realized, unrealized))


def snapshots(days: int = 30) -> list[dict]:
    with conn() as c:
        return c.execute(
            "SELECT ts, equity FROM equity_snapshots WHERE ts > now() - make_interval(days => %s) ORDER BY ts",
            (days,)).fetchall()


def closed_positions(limit: int = 10) -> list[dict]:
    with conn() as c:
        return c.execute(
            "SELECT * FROM positions WHERE status='closed' ORDER BY closed_at DESC LIMIT %s", (limit,)).fetchall()


def signal_counts() -> dict:
    with conn() as c:
        rows = c.execute("SELECT status, COUNT(*) AS n FROM signals GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}

import os


def _env(key, default=None, cast=str):
    v = os.getenv(key)
    if v is None or v.strip() == "":
        return default
    if cast is bool:
        return v.strip().lower() in ("1", "true", "yes", "on")
    return cast(v)


DATABASE_URL      = _env("DATABASE_URL", "postgresql://trader:change-me@db:5432/trading")
REDIS_URL         = _env("REDIS_URL", "redis://redis:6379/0")

TELEGRAM_TOKEN    = _env("TELEGRAM_TOKEN")
ALLOWED_USER_ID   = _env("ALLOWED_USER_ID", 0, int)

SYMBOLS           = [s.strip() for s in _env("SYMBOLS", "BTC/USD,ETH/USD").split(",") if s.strip()]
TIMEFRAME         = _env("TIMEFRAME", "4h")
EMA_LEN           = _env("EMA_LEN", 200, int)
ST_LEN            = _env("ST_LEN", 10, int)
ST_MULT           = _env("ST_MULT", 3.0, float)
ADX_LEN           = _env("ADX_LEN", 14, int)
ADX_MIN           = _env("ADX_MIN", 20.0, float)       # entries only while ADX > this; 0 = off
BREAKOUT_LEN      = _env("BREAKOUT_LEN", 20, int)      # close must exceed the high of the last N candles; 0 = off
ENTRY_MODE        = _env("ENTRY_MODE", "state")        # state (v3) | flip (v2: only on the red->green candle)
_regime           = _env("REGIME_SYMBOL", "BTC/USD").strip()
REGIME_SYMBOL     = "" if _regime.lower() in ("off", "none", "0") else _regime   # gates entries in the other symbols
POLL_SECONDS      = _env("POLL_SECONDS", 120, int)
SIGNAL_TTL_MIN    = _env("SIGNAL_TTL_MIN", 60, int)
REJECT_COOLDOWN_BARS = _env("REJECT_COOLDOWN_BARS", 6, int)   # candles without a new entry signal after a rejection

PAPER_CAPITAL     = _env("PAPER_CAPITAL", 10000.0, float)
RISK_PCT          = _env("RISK_PCT", 1.0, float)      # percent of capital risked per trade
MAX_POSITIONS     = _env("MAX_POSITIONS", 2, int)
MAX_NOTIONAL_PCT  = _env("MAX_NOTIONAL_PCT", 100.0 / MAX_POSITIONS, float)   # size cap per position in % of capital; default keeps all positions <= 100 % (spot)
AUTO_EXIT         = _env("AUTO_EXIT", True, bool)

BROKER            = _env("BROKER", "paper")            # paper | kraken
KRAKEN_API_KEY    = _env("KRAKEN_API_KEY")
KRAKEN_API_SECRET = _env("KRAKEN_API_SECRET")
LIVE_CONFIRM      = _env("LIVE_CONFIRM", "")

AUTO_APPROVE      = _env("AUTO_APPROVE", "night")     # off | night | always  (runtime override via /auto)
QUIET_HOURS       = _env("QUIET_HOURS", "22-07")      # local hours for "night" mode, e.g. 22-07
AUTO_ON_EXPIRE    = _env("AUTO_ON_EXPIRE", True, bool) # unanswered signal -> auto-approve instead of expire
AUTO_APPROVE_LIVE = _env("AUTO_APPROVE_LIVE", "")     # must be I_UNDERSTAND to auto-approve with BROKER=kraken

REPORT_TZ         = _env("REPORT_TZ", "Europe/Zurich")
REPORT_HOUR       = _env("REPORT_HOUR", 8, int)        # daily report at this local hour; -1 = off
SNAPSHOT_MINUTES  = _env("SNAPSHOT_MINUTES", 60, int)  # equity snapshot interval for the chart

# Redis queues / keys shared by all services
Q_SIGNALS   = "q:signals"     # strategy -> bot   (needs approval)
Q_DECISIONS = "q:decisions"   # bot/strategy -> executor
Q_NOTIFY    = "q:notify"      # executor/strategy -> bot (plain text to user)
K_HALTED    = "k:halted"      # kill-switch flag
K_AUTO      = "k:auto"        # runtime override for AUTO_APPROVE
K_STATE     = "k:state"       # hash symbol -> JSON of last evaluation (market check)
K_EVALS     = "k:evals"       # list of evaluation timestamps (activity counter)

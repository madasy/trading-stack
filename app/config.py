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
POLL_SECONDS      = _env("POLL_SECONDS", 120, int)
SIGNAL_TTL_MIN    = _env("SIGNAL_TTL_MIN", 60, int)

PAPER_CAPITAL     = _env("PAPER_CAPITAL", 10000.0, float)
RISK_PCT          = _env("RISK_PCT", 1.0, float)      # percent of capital risked per trade
MAX_POSITIONS     = _env("MAX_POSITIONS", 2, int)
AUTO_EXIT         = _env("AUTO_EXIT", True, bool)

BROKER            = _env("BROKER", "paper")            # paper | kraken
KRAKEN_API_KEY    = _env("KRAKEN_API_KEY")
KRAKEN_API_SECRET = _env("KRAKEN_API_SECRET")
LIVE_CONFIRM      = _env("LIVE_CONFIRM", "")

REPORT_TZ         = _env("REPORT_TZ", "Europe/Zurich")
REPORT_HOUR       = _env("REPORT_HOUR", 8, int)        # daily report at this local hour; -1 = off
SNAPSHOT_MINUTES  = _env("SNAPSHOT_MINUTES", 60, int)  # equity snapshot interval for the chart

# Redis queues / keys shared by all services
Q_SIGNALS   = "q:signals"     # strategy -> bot   (needs approval)
Q_DECISIONS = "q:decisions"   # bot/strategy -> executor
Q_NOTIFY    = "q:notify"      # executor/strategy -> bot (plain text to user)
K_HALTED    = "k:halted"      # kill-switch flag

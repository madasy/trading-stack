CREATE TABLE IF NOT EXISTS signals (
  id          BIGSERIAL PRIMARY KEY,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  candle_ts   TIMESTAMPTZ NOT NULL,
  symbol      TEXT NOT NULL,
  kind        TEXT NOT NULL,               -- entry | exit
  side        TEXT NOT NULL,               -- buy | sell
  price       NUMERIC NOT NULL,
  stop        NUMERIC,
  qty         NUMERIC,
  reason      TEXT,
  status      TEXT NOT NULL DEFAULT 'pending'  -- pending | approved | rejected | expired | auto | executed | failed
);

CREATE TABLE IF NOT EXISTS decisions (
  signal_id   BIGINT REFERENCES signals(id),
  decided_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  decision    TEXT NOT NULL,               -- yes | no | expired | auto
  decided_by  TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  id          BIGSERIAL PRIMARY KEY,
  symbol      TEXT NOT NULL,
  qty         NUMERIC NOT NULL,
  entry_price NUMERIC NOT NULL,
  stop        NUMERIC,
  opened_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at   TIMESTAMPTZ,
  exit_price  NUMERIC,
  pnl         NUMERIC,
  status      TEXT NOT NULL DEFAULT 'open'  -- open | closed
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_per_symbol ON positions(symbol) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS trades (
  id          BIGSERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
  signal_id   BIGINT REFERENCES signals(id),
  symbol      TEXT NOT NULL,
  side        TEXT NOT NULL,
  qty         NUMERIC NOT NULL,
  price       NUMERIC NOT NULL,
  broker      TEXT NOT NULL,
  order_id    TEXT
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
  ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
  equity      NUMERIC NOT NULL,
  realized    NUMERIC NOT NULL,
  unrealized  NUMERIC NOT NULL
);
CREATE INDEX IF NOT EXISTS equity_snapshots_ts ON equity_snapshots(ts);

-- v3 journal extensions (idempotent)
ALTER TABLE signals   ADD COLUMN IF NOT EXISTS context JSONB;             -- entry: indicator snapshot; exit: excursion while open
ALTER TABLE positions ADD COLUMN IF NOT EXISTS entry_signal_id BIGINT REFERENCES signals(id);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS r_multiple NUMERIC;        -- pnl in units of the initial risk
ALTER TABLE positions ADD COLUMN IF NOT EXISTS mfe_pct NUMERIC;           -- best excursion vs entry while open (%)
ALTER TABLE positions ADD COLUMN IF NOT EXISTS mae_pct NUMERIC;           -- worst excursion vs entry while open (%)

CREATE TABLE IF NOT EXISTS stop_updates (
  id          BIGSERIAL PRIMARY KEY,
  position_id BIGINT REFERENCES positions(id),
  ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
  candle_ts   TIMESTAMPTZ,
  old_stop    NUMERIC,
  new_stop    NUMERIC NOT NULL,
  reason      TEXT
);
CREATE INDEX IF NOT EXISTS stop_updates_position ON stop_updates(position_id);

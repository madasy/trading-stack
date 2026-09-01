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

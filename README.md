# trading-stack – semi-automatisches Crypto-Trading mit Telegram-Freigabe

Strategie: **EMA 200 (Regime-Filter) + Supertrend 10/3 (Entry/Exit)**, 4h, long-only, Spot.
Datenquelle: Kraken (public API – im Paper-Modus kein API-Key nötig).

```
strategy ──signals──▶ bot (Telegram ✅/❌) ──decisions──▶ executor ──▶ PaperBroker / Kraken
                            ▲                                  │
                            └──────────── notify ──────────────┘
                   Redis (Queues)  ·  Postgres (Journal)
```

## Variablen (Portainer-Stack oder `.env`)

| Variable | Default | Bedeutung |
|---|---|---|
| `TELEGRAM_TOKEN` | – | Bot-Token von @BotFather |
| `ALLOWED_USER_ID` | 0 | deine numerische Telegram-User-ID (nur sie darf freigeben) |
| `SYMBOLS` | BTC/USD,ETH/USD | Kraken-Paare |
| `TIMEFRAME` | 4h | Candle-Timeframe |
| `EMA_LEN` / `ST_LEN` / `ST_MULT` | 200 / 10 / 3.0 | Indikator-Parameter |
| `POLL_SECONDS` | 120 | Intervall, in dem nach neuen geschlossenen Candles geschaut wird |
| `SIGNAL_TTL_MIN` | 60 | unbeantwortete Signale verfallen nach X Minuten |
| `PAPER_CAPITAL` | 10000 | Start-Kapital (Paper) |
| `RISK_PCT` | 1.0 | **Prozent** des Kapitals Risiko pro Trade (Entry − Stop) |
| `MAX_POSITIONS` | 2 | max. gleichzeitig offene Positionen |
| `AUTO_EXIT` | true | Exits ohne Rückfrage; `false` = auch Exits per Ja/Nein |
| `BROKER` | paper | `paper` oder `kraken` |
| `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` | – | nur für `BROKER=kraken` |
| `LIVE_CONFIRM` | – | muss exakt `I_UNDERSTAND` sein, sonst startet der Kraken-Broker nicht |

## Deployment mit Portainer

1. Repo in ein **privates** GitHub-Repo pushen (`.env` ist per `.gitignore` ausgeschlossen).
2. Portainer → **Stacks → Add stack → Repository**
   - Repository URL + Branch `main`, Compose path `docker-compose.yml`
   - Authentication: GitHub-PAT (read-only)
3. **Environment variables → Advanced mode**: Inhalt von `.env.example` einfügen, `TELEGRAM_TOKEN` setzen.
4. **Deploy the stack** – Portainer baut das Image aus dem Repo-Root.
5. Bot in Telegram `/start` schicken → er antwortet mit deiner User-ID.
6. `ALLOWED_USER_ID` im Stack-Editor eintragen → **Update the stack**.
7. `/status` → Broker `paper`, 🟢 running.

Optional *GitOps updates* aktivieren → jeder Push auf `main` deployt neu.

## Lokal (ohne Portainer)

```
cp .env.example .env   # ausfüllen
docker compose up -d --build
docker compose logs -f strategy
```

## Bot-Befehle

`/status` · `/positions` · `/report` (Portfolio-Auswertung + Equity-Chart) · `/halt` (keine neuen Entries, Exits laufen weiter) · `/resume`

`/auto off|night|always` schaltet den Auto-Entscheider um (`AUTO_APPROVE`, Ruhezeit `QUIET_HOURS`). Mit `AUTO_ON_EXPIRE=true` werden unbeantwortete Signale nach `SIGNAL_TTL_MIN` automatisch freigegeben statt verworfen. Bei `BROKER=kraken` greift der Auto-Entscheider nur mit `AUTO_APPROVE_LIVE=I_UNDERSTAND`.

Täglicher Report automatisch um `REPORT_HOUR` Uhr (`REPORT_TZ`, Default 08:00 Europe/Zurich); `-1` schaltet ihn aus.

## Ablauf

- Strategy wertet nur **geschlossene** Candles aus (kein Repainting), jede Candle genau einmal.
- Supertrend dreht auf grün **und** Close > EMA 200 → Entry-Signal mit Ja/Nein in Telegram.
- Menge = `RISK_PCT % × Kapital ÷ (Entry − Stop)`; Stop = Supertrend-Linie, wird pro Candle nach oben nachgezogen.
- Exit bei Supertrend-Flip auf rot oder Close unter Stop (geprüft auf Candle-Schluss, 4h).
- Journal in Postgres: `signals`, `decisions`, `trades`, `positions`.

```
docker compose exec db psql -U trader trading
SELECT symbol, entry_price, exit_price, pnl, opened_at, closed_at FROM positions WHERE status='closed' ORDER BY closed_at;
```

## Später live gehen

Kraken-API-Key nur mit *Query Funds* + *Create & Modify Orders* (kein Withdraw), IP-Restriction setzen.
Dann `BROKER=kraken`, `LIVE_CONFIRM=I_UNDERSTAND`, Update the stack – erst nach mehreren Wochen Paper-Journal, mit kleinem Kapital.

## Hinweis

Technisches Grundgerüst, keine Anlageberatung. Trendfolge liefert in Seitwärtsmärkten Fehlsignale – genau das soll das Paper-Journal zeigen.

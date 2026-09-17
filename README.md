# trading-stack – semi-automatisches Crypto-Trading mit Telegram-Freigabe

Strategie v3 **„Trend-Breakout“**: EMA 200 + Supertrend 10/3 als Trendregime, ADX 14 > 20 als
Trendstärke-Filter, Einstieg auf ein neues 20-Candle-Hoch, BTC-Regime für die anderen Symbole.
4h, long-only, Spot. Exit über den Supertrend (Flip auf rot oder Close unter dem nachgezogenen Stop).
Datenquelle: Kraken (public API – im Paper-Modus kein API-Key nötig).

Review vom 17.09.2026: [Analyse und reproduzierbarer Vergleich](docs/strategy-review-2026-09-17.md).
Neue Defaults: 0,5 % Risiko pro Trade, 1 % gesamtes offenes Stop-Risiko; Entry-/Exit-Regeln bleiben v3.

Warum v3 und wie sie bewertet wurde: [docs/superpowers/specs/2026-09-02-strategy-v3-design.md](docs/superpowers/specs/2026-09-02-strategy-v3-design.md).

```
strategy ──signals──▶ bot (Telegram ✅/❌) ──decisions──▶ executor ──▶ PaperBroker / Kraken
                            ▲                                  │
                            └──────────── notify ──────────────┘
                   Redis (Queues)  ·  Postgres (Journal)
```

## Strategie im Detail

Alle Bedingungen werden auf **geschlossenen** Candles geprüft (kein Repainting), jede Candle genau einmal.

**Entry** (alle gleichzeitig, nicht nur am Flip-Candle):

1. Supertrend(10, 3) grün
2. Close > EMA 200
3. ADX(14) > 20 – der Markt trendet, keine Seitwärtsphase
4. Close > höchstes Hoch der letzten 20 Candles – Einstieg auf Stärke
5. Regime: `REGIME_SYMBOL` (BTC/USD) liegt selbst über EMA 200 mit grünem Supertrend (gilt für alle anderen Symbole)
6. Stop = Supertrend-Linie; Menge = `RISK_PCT % × Kapital ÷ (Entry − Stop)`, gedeckelt auf `MAX_NOTIONAL_PCT`

**Exit:** Supertrend dreht rot **oder** Close ≤ Stop. Der Stop wird pro Candle nur nach oben nachgezogen.

`ENTRY_MODE=flip`, `ADX_MIN=0`, `BREAKOUT_LEN=0`, `REGIME_SYMBOL=off` stellt die alte v2-Logik wieder her.

### Historischer, hier nicht neu reproduzierter Backtest 2017-08 – 2026-09 (Binance BTC + ETH 4h, 1 % Risiko, 0,40 % Gebühr + 0,05 % Slippage)

| | CAGR | Max Drawdown | Sharpe | Profit-Faktor | Trades/Jahr | Trefferquote | OOS 2023–26 CAGR |
|---|---|---|---|---|---|---|---|
| v2 (Flip-Entry) | 10,8 % | −26,6 % | 0,97 | 1,62 | 26 | 36 % | 1,7 % |
| **v3 Trend-Breakout** | **19,5 %** | **−10,5 %** | **1,51** | **2,42** | 25 | 43 % | **12,3 %** |
| Buy & Hold 50/50 | 34,3 % | −87 % | 0,77 | – | – | – | 38,7 % |

Bärenjahre (2018, 2022) bleiben mit ≈ −5 % leicht negativ; die Strategie ist long-only. Backtest ≠ Zukunft –
das Paper-Journal bleibt der Massstab.

## Backtest selbst laufen lassen

```
pip install -r requirements.txt
python -m app.backtest --compare --plot equity.png     # v3 vs. v2 vs. Buy & Hold, aktuelle Config
python -m app.backtest --start 2023-01-01               # nur ein Zeitfenster auswerten
python -m app.backtest --exchange bitstamp --symbols BTC/USD,ETH/USD
python -m app.backtest --help
```

Der Backtester teilt die Signal- und Risikoregeln mit dem Service (`app/rules.py`).
Er füllt zum nächsten Open mit Gebühren und Slippage; Paper nutzt aktuelle Ticker und erfasst derzeit keine Gebühren.
Default im Backtest: 0,8 % Gebühr pro Seite (mit `--fee` an den tatsächlichen Tarif anpassen). Kraken liefert nur
720 Candles, deshalb ist Binance die Standardquelle (`BTC/USD` wird auf `BTC/USDT` gemappt; Schlusskurse
weichen ~0,1 % ab). Candles werden in `data/` gecacht und inkrementell nachgeladen.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest -q
```

## Variablen (Portainer-Stack oder `.env`)

| Variable | Default | Bedeutung |
|---|---|---|
| `TELEGRAM_TOKEN` | – | Bot-Token von @BotFather |
| `ALLOWED_USER_ID` | 0 | deine numerische Telegram-User-ID (nur sie darf freigeben) |
| `SYMBOLS` | BTC/USD,ETH/USD | Kraken-Paare |
| `TIMEFRAME` | 4h | Candle-Timeframe |
| `EMA_LEN` / `ST_LEN` / `ST_MULT` | 200 / 10 / 3.0 | Regime-EMA und Supertrend |
| `ADX_LEN` / `ADX_MIN` | 14 / 20 | ADX-Filter, `ADX_MIN=0` schaltet ab |
| `BREAKOUT_LEN` | 20 | Close muss über dem Hoch der letzten N Candles liegen, 0 schaltet ab |
| `REGIME_SYMBOL` | BTC/USD | Markt-Regime für die anderen Symbole, `off` schaltet ab |
| `ENTRY_MODE` | state | `state` (v3) oder `flip` (v2: nur am Flip-Candle) |
| `POLL_SECONDS` | 120 | Intervall, in dem nach neuen geschlossenen Candles geschaut wird |
| `SIGNAL_TTL_MIN` | 60 | unbeantwortete Signale verfallen nach X Minuten |
| `REJECT_COOLDOWN_BARS` | 6 | Candles ohne neues Entry-Signal nach einem abgelehnten Signal |
| `PAPER_CAPITAL` | 10000 | Start-Kapital (Paper) |
| `RISK_PCT` | 0.5 | **Prozent** des Kapitals Risiko pro Trade (Entry − Stop) |
| `MAX_OPEN_RISK_PCT` | 1.0 | Gesamtes Entry-zu-Stop-Risiko in % des realisierten Kapitals; Executor prüft vor jedem Kauf erneut. 0 deaktiviert das Limit. |
| `ADX_RISING` / `EXIT_LEN` | false / 0 | Experimentelle ADX-Steigungsbestätigung / Exit unter vorherigem N-Candle-Tief; im Review nicht zur Aktivierung empfohlen. |
| `MAX_NOTIONAL_PCT` | 100 ÷ `MAX_POSITIONS` | Obergrenze pro Position in % des Kapitals; Default hält alle Positionen zusammen unter 100 % (Spot, kein Hebel) |
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
3. **Environment variables → Advanced mode**: Variablen eintragen, `TELEGRAM_TOKEN` setzen.
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

`/status` · `/scan` (Markt-Check: welche Entry-Bedingung fehlt gerade) · `/positions` · `/report` (Portfolio-Auswertung + Equity-Chart) · `/halt` (keine neuen Entries, Exits laufen weiter) · `/resume`

`/auto off|night|always` schaltet den Auto-Entscheider um (`AUTO_APPROVE`, Ruhezeit `QUIET_HOURS`). Mit `AUTO_ON_EXPIRE=true` werden unbeantwortete Signale nach `SIGNAL_TTL_MIN` automatisch freigegeben statt verworfen. Bei `BROKER=kraken` greift der Auto-Entscheider nur mit `AUTO_APPROVE_LIVE=I_UNDERSTAND`.

Täglicher Report automatisch um `REPORT_HOUR` Uhr (`REPORT_TZ`, Default 08:00 Europe/Zurich); `-1` schaltet ihn aus.

## Ablauf

- Strategy holt pro Runde zuerst `REGIME_SYMBOL`, dann alle `SYMBOLS`, und wertet nur **geschlossene** Candles aus.
- Sind alle Entry-Bedingungen erfüllt und ist ein Slot frei (`MAX_POSITIONS`), kommt ein Entry-Signal mit Ja/Nein in Telegram.
- Nach einem abgelehnten Signal gibt es für `REJECT_COOLDOWN_BARS` Candles kein neues Entry-Signal für dasselbe Symbol.
- Exit bei Supertrend-Flip auf rot oder Close unter Stop (geprüft auf Candle-Schluss, 4h).
- Journal in Postgres: `signals`, `decisions`, `trades`, `positions`, `stop_updates`.

## Journal und Post-Mortem

- Jedes Entry-Signal speichert in `signals.context` den Marktzustand am Signal-Candle (ADX, Abstand zum EMA, Breakout-Grösse, Stop-Abstand, Regime, alle Checks). Die Position verweist über `positions.entry_signal_id` darauf.
- Jede Stop-Anpassung landet in `stop_updates` (alter/neuer Stop, Candle, Grund).
- Beim Exit rechnet der Executor ein Post-Mortem und hängt es an die Telegram-Nachricht: R-Multiple, Haltedauer, bestes und schlechtestes Kursniveau während der Position, wie viel vom Höchstgewinn zurückgegeben wurde, Entry-Kontext. Die Zahlen liegen in `positions.r_multiple`, `mfe_pct`, `mae_pct`.

```
docker compose exec db psql -U trader trading
```

```sql
-- geschlossene Trades mit R-Multiple
SELECT symbol, entry_price, exit_price, pnl, r_multiple, mfe_pct, mae_pct, opened_at, closed_at
FROM positions WHERE status='closed' ORDER BY closed_at;

-- Trefferquote und Ø R nach ADX-Bereich beim Entry (Buckets 20–30, 30–40, 40–50, >50)
SELECT width_bucket((s.context->>'adx')::numeric, 20, 50, 3) AS adx_bucket,
       COUNT(*) AS n, ROUND(AVG((p.pnl > 0)::int) * 100) AS win_pct, ROUND(AVG(p.r_multiple), 2) AS avg_r
FROM positions p JOIN signals s ON s.id = p.entry_signal_id
WHERE p.status = 'closed' GROUP BY 1 ORDER BY 1;

-- Stop-Verlauf einer Position
SELECT ts, candle_ts, old_stop, new_stop, reason FROM stop_updates WHERE position_id = 7 ORDER BY id;
```

## Später live gehen

Kraken-API-Key nur mit *Query Funds* + *Create & Modify Orders* (kein Withdraw), IP-Restriction setzen.
Dann `BROKER=kraken`, `LIVE_CONFIRM=I_UNDERSTAND`, Update the stack – erst nach mehreren Wochen Paper-Journal, mit kleinem Kapital.

## Hinweis

Technisches Grundgerüst, keine Anlageberatung. Auch v3 verliert in Bärenmärkten und Seitwärtsphasen –
genau das soll das Paper-Journal zeigen.

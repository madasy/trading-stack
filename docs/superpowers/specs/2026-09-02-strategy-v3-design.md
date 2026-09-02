# Strategie v3 „Trend-Breakout“ – Bewertung der bisherigen Strategie und Design

Datum: 2026-09-02

## 1. Ausgangslage

Bisher (v2): EMA 200 als Regimefilter, Supertrend(10, 3) als Entry/Exit, 4h, long-only, Spot,
1 % Risiko pro Trade, max. 2 Positionen. Entry **nur** auf dem Candle, an dem der Supertrend von
rot auf grün dreht und der Close über dem EMA 200 liegt. Exit bei Flip auf rot bzw. Close unter dem
nachgezogenen Stop (Supertrend-Linie).

Es gab keinen Backtest im Repo. Für die Bewertung wurde eine Portfolio-Simulation gebaut, die die
Live-Semantik nachbildet:

- Auswertung nur auf geschlossenen Candles, Fill zum Open des Folgecandles (+0,05 % Slippage)
- Gebühren 0,40 % pro Seite (Kraken Taker, unterste Volumenstufe)
- Menge = 1 % × (Startkapital + realisierter PnL) ÷ (Entry − Stop), Notional gedeckelt auf 50 % des Kapitals
- gemeinsames Kapital für BTC und ETH, max. 2 offene Positionen
- Daten: Binance BTC/USDT + ETH/USDT 4h, 2017-08 bis 2026-09 (19 805 Candles je Symbol);
  Bitstamp BTC/USD + ETH/USD als unabhängige Gegenprobe (USD-notiert wie Kraken).
  Kraken selbst liefert nur 720 Candles (~120 Tage). Binance- und Kraken-Schlusskurse weichen
  im Überlappungszeitraum im Mittel 0,08 % voneinander ab.
- In-Sample (IS) 2017-08 – 2022-12, Out-of-Sample (OOS) 2023-01 – 2026-09

## 2. Bewertung der bisherigen Strategie (v2)

| Zeitraum | Rendite | CAGR | Max DD | Sharpe | Trades/J | Trefferquote | Profit-Faktor | Ø R |
|---|---|---|---|---|---|---|---|---|
| 2017–2026 | +151 % | 10,8 % | −26,6 % | 0,97 | 25,9 | 36 % | 1,62 | 0,44 |
| IS 2017–2022 | +144 % | 18,0 % | −15,0 % | 1,42 | 23,6 | 41 % | 2,3 | 0,75 |
| OOS 2023–2026 | +6 % | 1,7 % | −15,2 % | 0,22 | 27,8 | 30 % | 1,11 | 0,12 |

Buy & Hold 50/50 zum Vergleich: CAGR 34,3 %, Max DD −87 %, Sharpe 0,77.

Befund:

1. **Positiver Erwartungswert, aber schwach seit 2023.** 2023 und 2024 waren starke Trendjahre
   (Buy & Hold +123 % bzw. +89 %), die Strategie machte −8 % bzw. +2 %.
2. **Struktureller Fehler im Entry:** Der Entry feuert nur auf dem Flip-Candle. Dreht der Supertrend
   unterhalb des EMA 200 auf grün (typisch nach einem Bärenmarkt) und kreuzt der Kurs erst später den
   EMA, gibt es **kein** Signal, bis der nächste Flip kommt. Genau so wurden die Trends 2023/2024 verpasst.
3. **Keine Unterscheidung Trend/Seitwärts.** Supertrend(10, 3) auf 4h produziert in Seitwärtsphasen
   Serien von Fehlsignalen (bis zu 13 Verlierer in Folge, 1 846 Tage längste Unterwasserphase).
4. **Parameter sind nicht das Problem.** Raster über 80 Kombinationen (ST-Länge 7–20, Multiplikator
   2–4, EMA 100–400): alle profitabel, Sharpe 0,62–1,24, flach. (10, 3, 200) liegt auf Rang 53/80.
   Tuning bringt nichts, die Struktur muss sich ändern.
5. **Bug in der Positionsgrösse:** `RISK_PCT ÷ (Entry − Stop)` ist nicht gedeckelt. Bei engem Stop
   (z. B. 0,4 %) ergibt das 250 % des Kapitals – auf Spot nicht ausführbar (Kraken lehnt ab, der
   Paper-Broker füllt es stillschweigend). Fix: `MAX_NOTIONAL_PCT`.

## 3. Geprüfte Alternativen (Auszug, Binance 2017–2026, identische Kosten)

| Variante | CAGR | Max DD | Sharpe | PF | Treffer | Ø R | OOS CAGR | OOS Sharpe | Bitstamp Sharpe |
|---|---|---|---|---|---|---|---|---|---|
| v2 Baseline (Flip-Entry) | 10,7 % | −26,6 % | 0,97 | 1,62 | 36 % | 0,44 | 1,7 % | 0,22 | 1,13 |
| A: Zustands-Entry (ST grün **und** Close > EMA) | 15,7 % | −16,9 % | 1,15 | 1,66 | 38 % | 0,44 | 6,4 % | 0,56 | 1,16 |
| D: A + Breakout-Bestätigung (20-Candle-Hoch) | 17,0 % | −14,7 % | 1,25 | 1,85 | 39 % | 0,52 | 9,9 % | 0,83 | 1,18 |
| H: A + BTC-Regime für ETH | 17,0 % | −15,5 % | 1,23 | 1,82 | 39 % | 0,51 | 8,3 % | 0,69 | 1,29 |
| X4: D + ADX(14) > 20 | 18,5 % | −11,5 % | 1,43 | 2,21 | 43 % | 0,67 | 12,4 % | 1,13 | 1,32 |
| **X5: X4 + BTC-Regime (gewählt)** | **19,3 %** | **−10,5 %** | **1,50** | **2,42** | **43 %** | **0,76** | **12,3 %** | **1,15** | **1,41** |
| X5 + EMA-Steigung > 0 (30 Candles) | 15,2 % | −14,7 % | 1,31 | 2,17 | 40 % | 0,77 | 4,7 % | 0,58 | 1,26 |
| Voting-Ensemble 3 Supertrends, 2 von 3 | 14,4 % | −13,7 % | 1,22 | 1,81 | 38 % | 0,50 | 4,8 % | 0,52 | 1,28 |
| Voting-Ensemble, 3 von 3 | 11,7 % | −7,6 % | 1,31 | 2,09 | 39 % | 0,41 | 6,4 % | 0,87 | 1,28 |
| Langsamer Supertrend(60, 3) als Regime | 15,6 % | −16,9 % | 1,14 | 1,65 | 37 % | 0,45 | 6,6 % | 0,56 | 1,15 |
| Chandelier-Trailing (3 ATR) | 7,7 % | −22,6 % | 0,66 | 1,35 | 38 % | 0,12 | 0,1 % | 0,07 | 0,68 |
| Teilgewinn 50 % bei 2R | 9,2 % | −15,5 % | 0,90 | 1,41 | 39 % | 0,26 | 1,0 % | 0,15 | 0,94 |
| Pullback-Entry an EMA 20 | 7,1 % | −23,3 % | 0,56 | 1,36 | 33 % | 0,33 | 4,1 % | 0,41 | 0,59 |
| Donchian-Exit (20) | 8,6 % | −22,0 % | 0,72 | 1,42 | 37 % | 0,39 | 8,3 % | 0,59 | 0,74 |
| EMA-Exit (Close < EMA 200) | 1,2 % | −18,5 % | 0,21 | 1,12 | 28 % | 0,03 | 0,2 % | 0,07 | 0,48 |

(Research-Engine, identische Trades wie `python -m app.backtest`; der CLI-Backtester zählt die Jahre ab
Ende der Aufwärmphase und zeigt deshalb CAGR 19,5 % statt 19,3 % für X5.)

Robustheit der gewählten Struktur:

- ADX-Schwelle 15 / 18 / 20 / 22 / 25 / 30 → Sharpe 1,23 / 1,34 / 1,43 / 1,43 / 1,36 / 1,38 (Plateau, keine Spitze)
- Breakout-Länge 15 / 20 / 30 / 40 → Sharpe 1,42 / 1,43 / 1,42 / 1,40 (flach)
- Raster über dieselben 80 ST/EMA-Kombinationen mit der neuen Struktur: Sharpe Ø 1,18, min 0,86,
  kein einziger Profit-Faktor < 1, auch nicht OOS
- Gebühren 0,60 % + 0,10 % Slippage: CAGR 15,7 %, Sharpe 1,23 – bleibt klar positiv
- Jahresvergleich v2 → X5: 2023 −7,8 % → +36,9 %, 2024 +2,2 % → +24,5 %; 2018/2022 (Bär) beide ≈ −5 %
- Notional-Deckel (50 %) greift bei 1 von 228 Trades; Median-Positionsgrösse 16 % des Kapitals,
  Anfangs-Stop im Median 6,1 % unter Entry (10.–90. Perzentil 3,4–10,8 %)
- Live-Service rechnet auf einem 720-Candle-Fenster: über die letzten 500 Candles identische
  Supertrend-Richtung, ADX und Stop-Linie wie im Backtest auf voller Historie, 1 Grenzfall bei den
  Entry-Bedingungen (EMA-Abweichung 1,5 × 10⁻⁴)
- Bootstrap der Trade-Reihenfolge (2 000 Züge, 1 % Risiko): Median-Drawdown −7,5 %, 95. Perzentil −12,2 %

Verworfen, weil schlechter: engere Trailing-Stops, Teilgewinne, EMA-Exits, Pullback-Entries,
Donchian-Exits, langsamer Supertrend als Regime, EMA-Steigung (hilft IS, schadet OOS),
Voting-Ensembles (weniger Drawdown, aber deutlich weniger Rendite als X5 bei gleichem Calmar).

## 4. Design v3 „Trend-Breakout“

Alle Bedingungen werden auf dem **geschlossenen** Candle geprüft (kein Repainting).

**Entry (alle Bedingungen gleichzeitig, nicht nur am Flip-Candle):**

1. Supertrend(10, 3) ist grün
2. Close > EMA 200
3. ADX(14) > 20 – Markt trendet, keine Seitwärtsphase
4. Close > höchstes Hoch der vorangegangenen 20 Candles – Einstieg auf Stärke
5. Markt-Regime: für alle Symbole ausser `REGIME_SYMBOL` (BTC/USD) muss BTC selbst über seinem
   EMA 200 liegen und einen grünen Supertrend haben
6. Stop (Supertrend-Linie) liegt unter dem Close; Menge nach Risiko, gedeckelt auf `MAX_NOTIONAL_PCT`
7. kein offener Slot: bei `MAX_POSITIONS` wird gar nicht erst signalisiert
8. Cooldown: nach einem abgelehnten/verfallenen Entry-Signal für `REJECT_COOLDOWN_BARS` Candles
   kein neues Entry-Signal für dasselbe Symbol (verhindert 4h-Spam in starken Trends)

**Exit (unverändert):** Supertrend dreht rot **oder** Close ≤ Stop. Stop = Supertrend-Linie, wird nur
nach oben nachgezogen. Exits laufen wie bisher automatisch (`AUTO_EXIT`).

**Sizing:** `qty = RISK_PCT % × (PAPER_CAPITAL + realisierter PnL) ÷ (Entry − Stop)`,
`qty ≤ MAX_NOTIONAL_PCT % × Kapital ÷ Entry`.

**Legacy-Modus:** `ENTRY_MODE=flip`, `ADX_MIN=0`, `BREAKOUT_LEN=0`, `REGIME_SYMBOL=` stellt v2 exakt wieder her
(im Backtest: `--legacy`).

## 5. Architektur

```
app/indicators.py   ema, atr, supertrend (unverändert) + adx (neu, TradingView ta.dmi)
app/rules.py        reine Regel-Logik ohne I/O: Params, prepare(), entry_checks(), entry_stop(),
                    trail_stop(), exit_reason(), market_uptrend(), risk_qty()
app/strategy.py     Service-Schleife (ccxt, Redis, Postgres) – benutzt rules.py
app/backtest.py     CLI-Backtester – benutzt dieselben rules.py (Daten via ccxt mit Pagination, Cache in data/)
app/report.py       Markt-Check zeigt jede Entry-Bedingung und was gerade blockiert
tests/              pytest: Indikatoren, Regeln, Backtest-Engine (synthetische Daten, kein Netz)
```

Live-Service und Backtester teilen sich exakt dieselben Funktionen; die Simulation ist damit ein
Test der Produktionslogik, nicht einer Kopie davon.

## 6. Neue Konfiguration

| Variable | Default | Bedeutung |
|---|---|---|
| `ADX_LEN` / `ADX_MIN` | 14 / 20 | ADX-Filter, `ADX_MIN=0` schaltet ab |
| `BREAKOUT_LEN` | 20 | Close muss über dem Hoch der letzten N Candles liegen, 0 schaltet ab |
| `REGIME_SYMBOL` | BTC/USD | Markt-Regime für die anderen Symbole, leer schaltet ab |
| `ENTRY_MODE` | state | `state` (v3) oder `flip` (v2) |
| `MAX_NOTIONAL_PCT` | 50 | Obergrenze Positionsgrösse in % des Kapitals |
| `REJECT_COOLDOWN_BARS` | 6 | Candles Pause nach abgelehntem Entry-Signal |

## 7. Erwartung und Grenzen

- ~25 Signale pro Jahr über beide Symbole, Trefferquote ~43 %, Ø Gewinner 2,7 R, Ø Verlierer −0,7 R, max. 7 Verlierer in Folge.
- Bärenjahre bleiben leicht negativ (−5 %); die Strategie ist long-only und verdient nur in Aufwärtstrends.
- Backtest ≠ Zukunft. Das Paper-Journal bleibt der Massstab, bevor Kapital eingesetzt wird.
- Gebühren dominieren: Bei Kraken-Taker 0,40 % kostet ein Round-Trip ~0,9 % inkl. Slippage; mit
  Maker-Orders oder höherer Volumenstufe wäre die Rendite ~1,5 Prozentpunkte p. a. höher.

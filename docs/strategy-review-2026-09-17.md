# Strategy review — 17 September 2026

The implemented improvement is risk control, not a claim of a newly profitable signal.
Keep the existing 4h EMA200 / Supertrend(10,3) / ADX>20 / breakout20 rules. Reduce
new-trade risk from 1% to 0.5% and cap combined open entry-to-stop risk at 1% of
realized capital. Faster exits and rising-ADX confirmation were researched but remain disabled.

## Evidence from the supplied export

The Telegram JSON contains 62 messages and three completed paper trades:

| Symbol | Reported PnL | Holding time | Best reported excursion |
|---|---:|---:|---:|
| BTC/USD | −$63.39 | 7 days | +1.3% |
| SOL/USD | −$63.04 | 7 days | +2.3% |
| ETH/USD | −$37.18 | 14 days | +6.1% |

Total: **−$163.61, or −1.6361% of the initial $10,000**, before costs the paper
broker does not record. All three entries were generated together on 3 September,
with initial stop distances of 6.2–8.0%. This was concentrated exposure to one crypto
upswing, not three independent demonstrations of an edge. ADX weakened while the
positions remained open. The final supplied equity chart agrees with the loss progression.

Reports stop after 11 September; ETH exits immediately after an executor restart on
17 September. An outage is plausible but cannot be established from this export alone.
No running-service logs or remote deployment were inspected. Parameter changes cannot
protect a position while the strategy/executor is offline. Stops remain evaluated on
closed candles, not exchange-hosted stop orders.

## Historical comparison

Downloaded 12,880 closed Binance 4h candles per symbol, BTC/USDT, ETH/USDT and SOL/USDT,
from 2020-11-01 through 2026-09-17 12:00 UTC. Checked ordering, duplicates and gaps.
Each simulation starts with $10,000 and no positions; indicators use preceding warmup
history. Three position slots and a one-third notional cap reproduce the export's
universe/slot count. Orders fill at next open with 0.05% slippage per side.

The main comparison charges **0.8% per side**, the entry-level taker fee displayed on
[Kraken's current schedule](https://www.kraken.com/features/fee-schedule).
The actual account tier was not accessed. Also evaluated baseline/selected signal
at 0.4%. Historical uniform fees are a present-cost stress assumption, not a reconstruction
of every historical fee tier. Binance USDT prices differ from Kraken USD execution.

Eight predeclared signal variants: exit below the previous 0/6/12/20-candle low,
with/without rising ADX. Zero disables the extra exit. Train: 2021–2023; validation:
2024–2025. Require positive return and at least 30 completed trades in each; rank by
the lower Calmar ratio across those two windows. The selected rising-ADX variant was
then compared with baseline on January–August 2026. September is an already-observed
diagnostic period and was not used for candidate ranking. These windows have now been
examined and must not be reused as an untouched holdout for further tuning.

| Period | Baseline return / max DD | Rising ADX return / max DD | Revised risk return / max DD |
|---|---:|---:|---:|
| 2021–2023 | +84.1% / −15.1% | +79.2% / −14.3% | +40.3% / −6.9% |
| 2024–2025 | +14.6% / −19.5% | +20.4% / −15.1% | +8.3% / −10.8% |
| Jan–Aug 2026 | +3.4% / −14.3% | −0.7% / −13.7% | +3.0% / −7.3% |
| Sep 1–17 2026 | −2.7% / −2.7% | −1.9% / −1.9% | −0.9% / −0.9% |

Returns are total period returns, not annualized. Terminal equity includes unrealized
positions without forced liquidation or a terminal exit fee. In particular, Jan–Aug
results contain open winners while closed-trade profit factors are poor; read both
in the CSV. The revised risk policy was chosen to reduce exposure after reviewing the
journal, not selected as an independently validated alpha strategy. Lower risk also
reduces upside, particularly in strong trending years.

Faster exits reduced development returns and increased turnover. Rising ADX improved
validation but failed the reserved period at both fee assumptions; it was not enabled.
Full metrics, open-position counts and exact evaluated timestamps are in
[strategy-comparison.csv](strategy-comparison.csv).

## Changes and limits

- Default `RISK_PCT=0.5`, `MAX_OPEN_RISK_PCT=1.0`; the executor rechecks the combined
  budget for each purchase, including multiple approvals from the same candle.
- Before buying, fetch a fresh quote and reduce quantity if required by risk/notional
  limits. Never increase the approved quantity. Reject a quote at/below the stop or
  an exhausted budget. Quantities round down.
- Missing, insufficient or differently dated BTC regime candles block new altcoin
  entries. Exits remain independent of the regime gate.
- Fix backtest first-window previous-candle lookup, which previously wrapped to the
  final candle. Include starting capital in drawdown calculations.
- Both research options use the shared signal rules and remain off by default.

The portfolio cap budgets `sum(max(entry − current stop, 0) × quantity)`; it excludes
fees, gaps and unrealized-profit giveback. A fresh quote does not guarantee the market
fill price. A single executor is assumed. Existing positions are not resized. Existing
paper accounting still omits fees; backtest accounting includes them. Uptime and
broker-hosted protective orders remain separate operational limitations.

## Reproduce and apply

Install requirements-dev.txt in a virtual environment. Download public candles once:

```sh
python - <<'PY'
from pathlib import Path
from app.backtest import load_or_fetch
for symbol in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']:
    load_or_fetch('binance', symbol, '4h', '2020-11-01', Path('data'))
PY
python -m app.optimize --output docs/strategy-comparison.csv
python -m pytest -q
```

The comparison is offline once the CSV cache exists. Input hashes are stored in
[strategy-data-manifest.json](strategy-data-manifest.json). The September diagnostic
uses whatever completed candles are present before 2026-09-18; use the documented
snapshot to reproduce that row exactly. `python -m app.backtest` uses current config;
`python -m app.optimize` uses explicit baseline/research settings independent of env.

Changes are local and **not deployed**. For the existing paper stack, explicitly set:

```dotenv
BROKER=paper
RISK_PCT=0.5
MAX_OPEN_RISK_PCT=1.0
ADX_RISING=false
EXIT_LEN=0
```

Rebuild the strategy, executor and bot from this code. Existing Portainer environment
values override Compose defaults, so an existing `RISK_PCT=1.0` must be updated to
apply the per-trade reduction. The 4h timeframe and existing symbols may remain.

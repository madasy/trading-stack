"""Offline, chronological strategy comparison using cached Binance 4h candles.

python -m app.optimize --output docs/strategy-comparison.csv
Selection uses 2021-2025 only; 2026 before September is reserved for a single
baseline/selected comparison. September is a diagnostic, already seen in the journal.
No settings, orders or running services are changed by this command.
"""
import argparse
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from . import backtest
from .rules import Params

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
WINDOWS = {
    'train': ('2021-01-01', '2024-01-01'),
    'validation': ('2024-01-01', '2026-01-01'),
    'holdout': ('2026-01-01', '2026-09-01'),
    'journal': ('2026-09-01', '2026-09-18'),
}


def load_frames(data_dir):
    frames = {}
    for symbol in SYMBOLS:
        path = Path(data_dir) / f"binance_{symbol.replace('/', '')}_4h.csv"
        df = pd.read_csv(path, parse_dates=['ts'])
        df['ts'] = pd.to_datetime(df['ts'], utc=True)
        if df.ts.duplicated().any() or not df.ts.is_monotonic_increasing:
            raise ValueError(f'{path}: duplicate or unordered candles')
        if not df.ts.diff().dropna().eq(pd.Timedelta(hours=4)).all():
            raise ValueError(f'{path}: missing 4h candles; do not silently bridge gaps')
        if df.ts.min() > pd.Timestamp('2020-11-01', tz='UTC') or df.ts.max() < pd.Timestamp('2026-08-31 20:00', tz='UTC'):
            raise ValueError(f'{path}: insufficient training/warmup/holdout coverage')
        frames[symbol] = df
    return frames


def compare(frames):
    candidates = {f'exit{length}_rising{int(rising)}': Params(exit_len=length, adx_rising=rising)
                  for length in (0, 6, 12, 20) for rising in (False, True)}
    records = []

    def run(name, window, fee):
        start, end = WINDOWS[window]
        result = backtest.simulate(frames, SYMBOLS, candidates[name], 'BTC/USDT',
                                   max_positions=3, max_notional_pct=100 / 3, fee=fee,
                                   start=start, end=end,
                                   risk_pct=.5 if name == 'risk_guarded' else 1.0,
                                   max_open_risk_pct=1.0 if name == 'risk_guarded' else 0.0)
        m = backtest.metrics(result['equity'], result['trades'], 10000, 365 * 6, result['exposure'])
        record = dict(profile=name, window=window, fee=fee, **asdict(candidates[name]), **m,
                      risk_pct=.5 if name == 'risk_guarded' else 1.0,
                      max_open_risk_pct=1.0 if name == 'risk_guarded' else 0.0,
                      open_positions=len(result['open']), start=str(result['equity'].index[0]),
                      last=str(result['equity'].index[-1]))
        records.append(record)
        print(name, window, fee, backtest.fmt(m), flush=True)
        return record

    development = {}
    for name in candidates:
        development[name] = [run(name, window, .008) for window in ('train', 'validation')]
    # Require positive return and at least 30 exits in each development period.
    # Rank by the worse Calmar of the two periods, without inspecting holdout results.
    eligible = {name: min(r['calmar'] for r in rows) for name, rows in development.items()
                if all(r['total'] > 0 and r['n'] >= 30 and pd.notna(r['calmar']) for r in rows)}
    selected = max(eligible, key=eligible.get) if eligible else 'exit0_rising0'
    print('Selected on development periods:', selected, flush=True)
    for fee in (.008, .004):
        for name in dict.fromkeys(['exit0_rising0', selected]):
            for window in ('holdout', 'journal'):
                run(name, window, fee)
    # Risk reduction is an explicit exposure decision, not a new alpha candidate.
    candidates['risk_guarded'] = Params()
    for window in WINDOWS:
        run('risk_guarded', window, .008)
    return pd.DataFrame(records), selected


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-dir', default='data')
    ap.add_argument('--output', default='docs/strategy-comparison.csv')
    args = ap.parse_args()
    records, selected = compare(load_frames(args.data_dir))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    records.to_csv(path, index=False)
    print(f'Research candidate: {selected}; results: {path}. Inspect holdout before adoption.')


if __name__ == '__main__':
    main()

"""
backtest_real.py — Three-way comparison backtest (original vs v1 vs v2)
using REAL Binance historical kline data.

V1 = regime-aware (ADX trending/ranging/transitioning branches)
V2 = all improvements:
     1. Momentum signal cap (prevents correlated-signal pile-on / score inversion)
     2. Continuation-only 24h change (EMA must confirm direction; spike ignored)
     3. Volume vs 100-period baseline (high-conviction breakout filter)
     4. BB squeeze follows trend in trending regime
     5. RSI tiered in ranging (20/35/65/80 vs flat 25/75)
     6. ADX noise-zone damper (15 < ADX < 22 → |score| reduced by 1)
     7. ATR-adaptive lookahead (volatile market → evaluate sooner; calm → wait longer)

Usage:
  python backtest_real.py --file "BTCUSDT-1h-*.csv"
  python backtest_real.py --file BTCUSDT-1h-2025-01.csv
"""

import sys
import os
import argparse
import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime

try:
    from ta.momentum import RSIIndicator
    from ta.trend import MACD, EMAIndicator, ADXIndicator
    from ta.volatility import BollingerBands, AverageTrueRange
except ImportError:
    print("ERROR: pip install ta pandas numpy")
    sys.exit(1)

BINANCE_API = "https://api.binance.com/api/v3"

TIMEFRAME_CONFIG = {
    'supershort': ( 5,  12, 0.003, 15, '1m',  500),
    'short':      ( 9,  21, 0.010, 16, '15m', 500),
    'mid':        (20,  50, 0.015,  6, '1h',  500),
    'long':       (50, 200, 0.020,  9, '4h',  300),
    'ulong':      (50, 200, 0.040,  7, '1d',  200),
}

LOOKBACK_24H = {'1m': 1440, '15m': 96, '1h': 24, '4h': 6, '1d': 1}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def fetch_binance(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    r = requests.get(
        f"{BINANCE_API}/klines",
        params={'symbol': f"{symbol}USDT", 'interval': interval, 'limit': limit},
        timeout=20
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json(), columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_vol', 'trades', 'tb_base', 'tb_quote', 'ignore'
    ])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])
    return df.reset_index(drop=True)


def load_csv(path: str) -> pd.DataFrame:
    import glob
    paths = sorted(glob.glob(path)) if '*' in path else [path]
    if not paths:
        raise FileNotFoundError(f"No files matched: {path}")
    frames = []
    for p in paths:
        with open(p, 'r') as f:
            first_line = f.readline().strip()
        first_cell = first_line.split(',')[0].strip()
        is_binance_vision = first_cell.isdigit() and len(first_cell) > 10
        if is_binance_vision:
            df = pd.read_csv(p, header=None, names=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_vol', 'trades', 'tb_base', 'tb_quote', 'ignore'
            ])
        else:
            df = pd.read_csv(p)
            df.columns = df.columns.str.lower().str.strip()
            renames = {}
            for col in df.columns:
                for target, aliases in {
                    'open': ['open'], 'high': ['high'], 'low': ['low'],
                    'close': ['close', 'price', 'last'],
                    'volume': ['volume', 'vol'],
                }.items():
                    if col in aliases:
                        renames[col] = target
            df = df.rename(columns=renames)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=['open', 'high', 'low', 'close']).reset_index(drop=True)
    src = os.path.basename(paths[0]) if len(paths) == 1 else f"{len(paths)} files"
    print(f"  Loaded {len(df):,} rows from {src}")
    return df


# ---------------------------------------------------------------------------
# Pre-compute all indicator series across the full dataset
# ---------------------------------------------------------------------------
def precompute_series(df: pd.DataFrame, fast_p: int, slow_p: int) -> pd.DataFrame:
    closes  = df['close'].astype(float)
    highs   = df['high'].astype(float)
    lows    = df['low'].astype(float)
    volumes = df['volume'].astype(float)

    # RSI
    try:
        df['_rsi'] = RSIIndicator(close=closes, window=14).rsi()
    except Exception:
        df['_rsi'] = 50.0

    # MACD diff
    try:
        diff = MACD(close=closes).macd_diff()
        df['_macd_diff']      = diff
        df['_macd_diff_prev'] = diff.shift(1)
    except Exception:
        df['_macd_diff'] = 0.0; df['_macd_diff_prev'] = 0.0

    # EMAs
    try:
        fp = min(fast_p, len(df) - 1)
        sp = min(slow_p, len(df) - 1)
        df['_ema_fast'] = EMAIndicator(close=closes, window=fp).ema_indicator()
        df['_ema_slow'] = EMAIndicator(close=closes, window=sp).ema_indicator()
    except Exception:
        df['_ema_fast'] = closes; df['_ema_slow'] = closes

    # Volume MAs: 20-period (trend) and 100-period (conviction)
    df['_vol_ma20']  = volumes.rolling(20).mean()
    df['_vol_ma100'] = volumes.rolling(100).mean()

    # Bollinger Bands
    try:
        bb = BollingerBands(close=closes, window=20, window_dev=2)
        df['_bb_pband']      = bb.bollinger_pband()
        df['_bb_wband']      = bb.bollinger_wband()
        df['_bb_wband_min50'] = df['_bb_wband'].rolling(50).min()
    except Exception:
        df['_bb_pband'] = 0.5; df['_bb_wband'] = 1.0; df['_bb_wband_min50'] = 1.0

    # ADX
    try:
        adx_obj = ADXIndicator(high=highs, low=lows, close=closes, window=14)
        df['_adx']     = adx_obj.adx()
        df['_adx_pos'] = adx_obj.adx_pos()
        df['_adx_neg'] = adx_obj.adx_neg()
    except Exception:
        df['_adx'] = 20.0; df['_adx_pos'] = 25.0; df['_adx_neg'] = 25.0

    # ATR (for adaptive lookahead)
    try:
        atr_obj = AverageTrueRange(high=highs, low=lows, close=closes, window=14)
        df['_atr']     = atr_obj.average_true_range()
        df['_atr_pct'] = df['_atr'] / closes * 100
    except Exception:
        df['_atr'] = 0.0; df['_atr_pct'] = 1.0

    # RSI divergence (vectorised approximation, n=10 lookback)
    df['_div'] = 0
    try:
        rsi_s       = df['_rsi']
        px_min_n    = closes.rolling(10).min()
        px_max_n    = closes.rolling(10).max()
        rsi_at_low  = rsi_s.rolling(10).min()
        rsi_at_high = rsi_s.rolling(10).max()
        bull = (closes <= px_min_n * 1.01) & (rsi_s > rsi_at_low + 10)
        bear = (closes >= px_max_n * 0.99) & (rsi_s < rsi_at_high - 10)
        df.loc[bull, '_div'] = 1
        df.loc[bear, '_div'] = -1
    except Exception:
        pass

    return df


def indicators_at(df: pd.DataFrame, i: int) -> dict:
    """Extract indicator snapshot for row i from precomputed series."""
    row = df.iloc[i]
    ind = {}

    def safe(col, default):
        v = row.get(col, default)
        return default if (v is None or (isinstance(v, float) and np.isnan(v))) else float(v)

    # RSI
    ind['rsi'] = safe('_rsi', 50.0)

    # MACD
    now  = safe('_macd_diff',      0.0)
    prev = safe('_macd_diff_prev', 0.0)
    if   now > 0 and prev <= 0: ind['macd_signal'] = 'bullish_cross'
    elif now < 0 and prev >= 0: ind['macd_signal'] = 'bearish_cross'
    elif now > 0:               ind['macd_signal'] = 'bullish'
    elif now < 0:               ind['macd_signal'] = 'bearish'
    else:                       ind['macd_signal'] = 'neutral'

    # EMA trend
    ef = safe('_ema_fast', float(row['close']))
    es = safe('_ema_slow', float(row['close']))
    px = float(row['close'])
    if   px > ef > es: ind['ema_trend'] = 'upward'
    elif px < ef < es: ind['ema_trend'] = 'downward'
    elif ef > es:      ind['ema_trend'] = 'upward'
    elif ef < es:      ind['ema_trend'] = 'downward'
    else:              ind['ema_trend'] = 'sideways'

    # Volume trends (20-period for short-term, 100-period for conviction)
    vm20  = safe('_vol_ma20',  float(row['volume']))
    vm100 = safe('_vol_ma100', float(row['volume']))
    cv    = float(row['volume'])
    r20   = cv / vm20  if vm20  > 0 else 1.0
    r100  = cv / vm100 if vm100 > 0 else 1.0
    ind['volume_ratio']   = r20
    ind['vol_ratio_100']  = r100
    if   r20 > 1.2: ind['volume_trend'] = 'increasing'
    elif r20 < 0.8: ind['volume_trend'] = 'decreasing'
    else:           ind['volume_trend'] = 'stable'

    # Bollinger Bands
    pbv = safe('_bb_pband',       0.5)
    wb  = safe('_bb_wband',       1.0)
    wbm = safe('_bb_wband_min50', 1.0)
    sq  = wb <= wbm * 1.05
    ind['bb_squeeze'] = sq
    if   pbv <= 0.05: ind['bb_signal'] = 'at_lower'
    elif pbv >= 0.95: ind['bb_signal'] = 'at_upper'
    elif sq:          ind['bb_signal'] = 'squeeze'
    else:             ind['bb_signal'] = 'neutral'

    # ADX / regime
    adx_v = safe('_adx',     20.0)
    adx_p = safe('_adx_pos', 25.0)
    adx_n = safe('_adx_neg', 25.0)
    regime = 'trending' if adx_v >= 25 else ('ranging' if adx_v <= 15 else 'transitioning')
    ind['adx'] = adx_v; ind['adx_pos'] = adx_p; ind['adx_neg'] = adx_n
    ind['market_regime'] = regime

    # ATR
    ind['atr_pct'] = safe('_atr_pct', 1.0)

    # RSI divergence
    ind['rsi_divergence'] = int(safe('_div', 0))

    return ind


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------
def score_orig(ind, c24=0.0, ob=0):
    """Original algorithm — no regime awareness."""
    sc  = 0
    ema = ind.get('ema_trend', 'sideways')
    rsi = ind.get('rsi', 50)
    ms  = ind.get('macd_signal', 'neutral')
    bb  = ind.get('bb_signal', 'neutral')
    vt  = ind.get('volume_trend', 'stable')
    if rsi < 30: sc += 1
    elif rsi > 70: sc -= 1
    if   'bullish_cross' in ms: sc += 2
    elif 'bullish'       in ms: sc += 1
    elif 'bearish_cross' in ms: sc -= 2
    elif 'bearish'       in ms: sc -= 1
    if ema == 'upward':   sc += 1
    elif ema == 'downward': sc -= 1
    if vt == 'increasing':
        if ema == 'upward':   sc += 1
        elif ema == 'downward': sc -= 1
    if c24 > 3: sc += 1
    elif c24 < -3: sc -= 1
    if bb == 'at_lower' and ema != 'downward': sc += 1
    elif bb == 'at_upper' and ema != 'upward': sc -= 1
    sc += ind.get('rsi_divergence', 0); sc += ob
    return sc


def score_v1(ind, c24=0.0, ob=0):
    """V1 — regime-aware branches (ADX trending/ranging/transitioning)."""
    sc     = 0
    ema    = ind.get('ema_trend', 'sideways')
    regime = ind.get('market_regime', 'transitioning')
    rsi    = ind.get('rsi', 50)
    ms     = ind.get('macd_signal', 'neutral')
    bb     = ind.get('bb_signal', 'neutral')
    vt     = ind.get('volume_trend', 'stable')
    if regime == 'trending':
        if   'bullish_cross' in ms: sc += 2
        elif 'bullish'       in ms: sc += 1
        elif 'bearish_cross' in ms: sc -= 2
        elif 'bearish'       in ms: sc -= 1
        ap = ind.get('adx_pos', 25); an = ind.get('adx_neg', 25)
        if ap > an + 5: sc += 1
        elif an > ap + 5: sc -= 1
        if vt == 'increasing':
            if ema == 'upward': sc += 1
            elif ema == 'downward': sc -= 1
        if rsi < 30: sc += 1
        elif rsi > 70: sc -= 1
    elif regime == 'ranging':
        if rsi < 25: sc += 1
        elif rsi > 75: sc -= 1
        if bb == 'at_lower' and ema != 'downward': sc += 1
        elif bb == 'at_upper' and ema != 'upward': sc -= 1
        if ms == 'bullish_cross': sc += 1
        elif ms == 'bearish_cross': sc -= 1
    else:
        if rsi < 30: sc += 1
        elif rsi > 70: sc -= 1
        if   'bullish_cross' in ms: sc += 2
        elif 'bullish'       in ms: sc += 1
        elif 'bearish_cross' in ms: sc -= 2
        elif 'bearish'       in ms: sc -= 1
        if ema == 'upward': sc += 1
        elif ema == 'downward': sc -= 1
        if vt == 'increasing':
            if ema == 'upward': sc += 1
            elif ema == 'downward': sc -= 1
        if bb == 'at_lower' and ema != 'downward': sc += 1
        elif bb == 'at_upper' and ema != 'upward': sc -= 1
    if c24 > 3: sc += 1
    elif c24 < -3: sc -= 1
    sc += ind.get('rsi_divergence', 0); sc += ob
    return sc


def score_v2(ind, c24=0.0, ob=0):
    """
    V2 — all improvements:
    1. Momentum cap (±2): MACD+EMA+Volume+24h-change are correlated; capping prevents
       score-inversion where score=+5 is driven by the same signal measured 4 ways.
    2. 24h change as continuation-only: only counts when EMA confirms direction
       AND RSI isn't already overextended (not a spike).
    3. Volume vs 100-period baseline (high-conviction filter: ratio_100 > 1.3).
    4. BB squeeze follows trend in trending regime.
    5. RSI tiered in ranging (20/35 and 65/80 thresholds for +2/+1).
    6. ADX noise-zone damper (15 < ADX < 22 → reduce |score| by 1).
    """
    ema    = ind.get('ema_trend', 'sideways')
    regime = ind.get('market_regime', 'transitioning')
    adx    = ind.get('adx', 20.0)
    rsi    = ind.get('rsi', 50)
    ms     = ind.get('macd_signal', 'neutral')
    bb     = ind.get('bb_signal', 'neutral')
    vt     = ind.get('volume_trend', 'stable')
    r100   = ind.get('vol_ratio_100', 1.0)   # volume vs 100-period MA
    adx_p  = ind.get('adx_pos', 25.0)
    adx_n  = ind.get('adx_neg', 25.0)

    noise_zone = 15 < adx < 22

    # ── Group 1: Correlated momentum signals — CAP at ±2 ────────────────
    # These all measure "price has been going up/down recently" — letting them
    # freely stack to +5 or +6 causes the score-inversion problem.
    momentum = 0

    if   'bullish_cross' in ms: momentum += 2
    elif 'bullish'       in ms: momentum += 1
    elif 'bearish_cross' in ms: momentum -= 2
    elif 'bearish'       in ms: momentum -= 1

    if   ema == 'upward':   momentum += 1
    elif ema == 'downward': momentum -= 1

    # Volume: only count when high-conviction vs 100-period baseline
    if vt == 'increasing' and r100 > 1.3:
        if   ema == 'upward':   momentum += 1
        elif ema == 'downward': momentum -= 1

    # 24h change: continuation only (EMA confirms + RSI not overextended)
    if c24 > 3 and ema == 'upward' and rsi < 68:
        momentum += 1
    elif c24 < -3 and ema == 'downward' and rsi > 32:
        momentum -= 1

    # THE KEY FIX: cap correlated momentum at ±2
    momentum = max(-2, min(2, momentum))
    score = momentum

    # ── Group 2: Mean-reversion / regime-specific signals ───────────────
    if regime == 'trending':
        # RSI extreme = momentum dip/surge within the trend
        if   rsi < 30: score += 1
        elif rsi > 70: score -= 1
        # ADX directional bias: +DI vs -DI
        if   adx_p > adx_n + 5: score += 1
        elif adx_n > adx_p + 5: score -= 1
        # BB squeeze in trending = volatility compression before trend continuation
        if bb == 'squeeze':
            if   ema == 'upward':   score += 1
            elif ema == 'downward': score -= 1

    elif regime == 'ranging':
        # Tiered RSI for mean-reversion (stronger signal at extreme levels)
        if   rsi < 20: score += 2
        elif rsi < 35: score += 1
        elif rsi > 80: score -= 2
        elif rsi > 65: score -= 1
        # BB at edges = high-probability bounce in a ranging market
        if   bb == 'at_lower': score += 2
        elif bb == 'at_upper': score -= 2

    else:  # transitioning
        if   rsi < 30: score += 1
        elif rsi > 70: score -= 1
        if   bb == 'at_lower' and ema != 'downward': score += 1
        elif bb == 'at_upper' and ema != 'upward':   score -= 1

    # ── Group 3: Truly independent signals ──────────────────────────────
    score += ind.get('rsi_divergence', 0)
    score += ob

    # ── Noise zone damper ───────────────────────────────────────────────
    if noise_zone and abs(score) > 1:
        score = score - 1 if score > 0 else score + 1

    return score


# ---------------------------------------------------------------------------
# Rolling-window backtest
# ---------------------------------------------------------------------------
def backtest(df, fast_p, slow_p, mpu, base_lookahead, interval, min_window=60, step=1):
    lb24 = LOOKBACK_24H.get(interval, 24)
    rows_o, rows_v1, rows_v2 = [], [], []

    print("  Pre-computing indicator series…", flush=True)
    df = precompute_series(df.copy(), fast_p, slow_p)
    n_windows = len(range(min_window, len(df) - base_lookahead * 2, step))
    print(f"  Done. Running {n_windows:,} windows…", flush=True)

    for i in range(min_window, len(df) - base_lookahead * 2, step):
        ep  = float(df.iloc[i]['close'])
        lb  = min(lb24, i)
        pp  = float(df.iloc[i - lb]['close'])
        c24 = (ep - pp) / pp * 100 if pp > 0 else 0.0

        ind = indicators_at(df, i - 1)

        # ATR-adaptive lookahead (improvement #7)
        # High ATR = market moves fast → evaluate sooner
        # Low ATR  = market needs more time → give it more candles
        atr_pct = max(ind.get('atr_pct', 1.0), 0.1)
        adaptive_la = max(3, min(base_lookahead * 2, round(base_lookahead * 1.0 / atr_pct)))
        adaptive_la = min(adaptive_la, len(df) - i - 1)
        if adaptive_la < 1:
            continue

        fu  = df.iloc[i: i + adaptive_la]
        fhi = float(fu['high'].max())
        flo = float(fu['low'].min())
        fc  = float(fu.iloc[-1]['close'])
        actual = (fc - ep) / ep * 100

        sc_o  = score_orig(ind, c24)
        sc_v1 = score_v1(ind, c24)
        sc_v2 = score_v2(ind, c24)

        for sc, rows in [(sc_o, rows_o), (sc_v1, rows_v1), (sc_v2, rows_v2)]:
            pd_ok = bool(int(np.sign(sc)) == int(np.sign(actual))) if sc != 0 else None
            if   sc > 0: th = fhi >= ep * (1 + sc * mpu)
            elif sc < 0: th = flo <= ep * (1 + sc * mpu)
            else:        th = abs(actual) < mpu * 100
            rows.append(dict(
                score=sc, actual_pct=actual, dir_correct=pd_ok,
                target_hit=th, market_regime=ind.get('market_regime', 'transitioning')
            ))

    return pd.DataFrame(rows_o), pd.DataFrame(rows_v1), pd.DataFrame(rows_v2)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def report(orig, v1, v2, label):
    print(f"\n{'='*65}")
    print(f"  {label}")
    print(f"{'='*65}")
    for name, df in [('Original', orig), ('V1 regime', v1), ('V2 full', v2)]:
        active = df[df['score'] != 0]
        if len(active) == 0:
            continue
        da = active['dir_correct'].mean() * 100
        ta = active['target_hit'].mean() * 100
        sr = len(active) / len(df) * 100
        print(f"  {name:10s}: dir={da:5.1f}%  tgt={ta:5.1f}%  sig_rate={sr:.1f}%  n={len(active)}")

    # Per-score band for V2
    print(f"\n  V2 score breakdown:")
    v2_active = v2[v2['score'] != 0]
    for lo, hi, lbl in [(5,9,'score≥5'), (3,4,'score 3-4'), (2,2,'score=2'), (1,1,'score=1')]:
        for sign, slbl in [(1, 'bull'), (-1, 'bear')]:
            sub = v2_active[v2_active['score'].between(
                sign*lo if sign > 0 else sign*hi,
                sign*hi if sign > 0 else sign*lo
            )]
            if len(sub) < 5:
                continue
            da = sub['dir_correct'].mean() * 100
            print(f"    {slbl:4s} {lbl:10s}  n={len(sub):4d}  dir={da:.1f}%")

    # Regime breakdown for V2
    print(f"\n  V2 by detected regime:")
    for r in ['trending', 'transitioning', 'ranging']:
        sub = v2[(v2['market_regime'] == r) & (v2['score'] != 0)]
        if len(sub) < 5:
            continue
        da = sub['dir_correct'].mean() * 100
        ta = sub['target_hit'].mean() * 100
        print(f"    {r:15s}  n={len(sub):4d}  dir={da:.1f}%  tgt={ta:.1f}%")

    # High-conviction filter: what if we only trade |score| >= 2?
    print(f"\n  V2 high-conviction filter (|score| ≥ 2):")
    hc = v2[v2['score'].abs() >= 2]
    if len(hc) > 0:
        da = hc['dir_correct'].mean() * 100
        ta = hc['target_hit'].mean() * 100
        sr = len(hc) / len(v2) * 100
        print(f"    dir={da:.1f}%  tgt={ta:.1f}%  sig_rate={sr:.1f}%  n={len(hc)}")

    print(f"\n  V2 high-conviction filter (|score| ≥ 3):")
    hc3 = v2[v2['score'].abs() >= 3]
    if len(hc3) > 0:
        da = hc3['dir_correct'].mean() * 100
        ta = hc3['target_hit'].mean() * 100
        sr = len(hc3) / len(v2) * 100
        print(f"    dir={da:.1f}%  tgt={ta:.1f}%  sig_rate={sr:.1f}%  n={len(hc3)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument('--file',     help='Path to CSV or glob: "BTCUSDT-1h-*.csv"')
parser.add_argument('--interval', default=None)
parser.add_argument('--symbol',   default='BTC')
parser.add_argument('--step',     type=int, default=1)
args = parser.parse_args()

tf_map = {'1m':'supershort','15m':'short','1h':'mid','4h':'long','1d':'ulong'}

interval = args.interval
if interval is None and args.file:
    import re, glob as _glob
    sample_file = sorted(_glob.glob(args.file))[0] if '*' in args.file else args.file
    m = re.search(r'[-_](1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|3d|1w)[-_.]',
                  os.path.basename(sample_file), re.IGNORECASE)
    interval = m.group(1).lower() if m else '1h'
    print(f"  Auto-detected interval: {interval}")
elif interval is None:
    interval = '1h'

tf = tf_map.get(interval, 'mid')
fast_p, slow_p, mpu, la, _, limit = TIMEFRAME_CONFIG[tf]

print("=" * 65)
print(f"REAL DATA BACKTEST — {args.symbol} @ {interval} ({tf})")
print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 65)

if args.file:
    df = load_csv(args.file)
else:
    print(f"\nFetching {args.symbol}USDT {interval} ({limit} candles) from Binance…")
    df = fetch_binance(args.symbol, interval, limit)

print(f"\nRunning rolling-window backtest (step={args.step}, ATR-adaptive lookahead)…")
t0 = time.time()
orig, v1, v2 = backtest(df, fast_p, slow_p, mpu, la, interval, step=args.step)
print(f"Completed in {time.time()-t0:.1f}s")

report(orig, v1, v2, f"{args.symbol} {interval} — {len(orig):,} windows")

print(f"\n✅ Done. Tested {len(orig):,} windows across all three algorithms.")

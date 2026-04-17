# CLAUDE.md — Codebase Guide for TG-Trading-Bot

## Project Overview

A Telegram bot for **crypto market analysis**. Users type a ticker symbol; the bot fetches live price and OHLCV candle data from Binance, computes a full suite of real technical indicators, scores all signals into a single integer using a regime-aware grouped scoring system (V2), and returns a forecast with a recommended action, probability estimate, and ATR-based stop-loss. Timeframe selection is done via inline keyboard buttons.

The single source-of-truth file is **`news.py`**. Everything else in the root (`1/`, patch files, old copies) is historical/archived and should be ignored.

---

## Architecture — Two Classes in `news.py`

### 1. `CryptoAnalyzer`

Fetches price data and candle data, computes all indicators, and produces scored forecast dicts.

#### Price data
- **Primary:** Binance `/ticker/price` + `/ticker/24hr`
- **Fallback:** CoinGecko `/search` + `/simple/price`

#### Klines cache (`_klines_cache`)
Results of Binance `/klines` are cached by `(symbol, interval, limit)` with per-interval TTLs:

| Interval | TTL |
|---|---|
| 1m | 30s |
| 15m | 2 min |
| 1h | 5 min |
| 4h | 10 min |
| 1d | 30 min |

#### Fear & Greed Index (`get_fear_greed`)
Fetches `https://api.alternative.me/fng/?limit=1`. Cached for 1 hour in `_fng_cache`. Returns `{value, classification, emoji}`. Appended to every forecast and shown in `/conf`.

#### `TIMEFRAME_CONFIG` (class-level dict)
Maps each timeframe to a 5-tuple: `(kline_interval, candle_limit, fast_ema, slow_ema, move_per_score_unit)`

| Timeframe | Interval | Candles | Fast EMA | Slow EMA | Move/unit |
|---|---|---|---|---|---|
| supershort | 1m | 100 | 5 | 12 | 0.3% |
| short | 15m | 100 | 9 | 21 | 1.0% |
| mid | 1h | 150 | 20 | 50 | 1.5% |
| long | 4h | 150 | 50 | 200 | 2.0% |
| ulong | 1d | 150 | 50 | 200 | 4.0% |

#### Indicators (`compute_indicators`)

| Indicator | Source | Notes |
|---|---|---|
| RSI | `ta.momentum.RSIIndicator` (14) | < 30 oversold, > 70 overbought; tiered thresholds in ranging regime |
| MACD | `ta.trend.MACD` (12, 26, 9) | Detects fresh crossovers vs sustained |
| EMA trend | `ta.trend.EMAIndicator` (fast + slow) | upward / downward / sideways |
| Volume trend | 20-candle rolling avg vs current | `volume_ratio` — increasing / stable / decreasing |
| Volume conviction | 100-candle rolling avg vs current | `vol_ratio_100` — high-conviction breakout filter (threshold > 1.3×) |
| Bollinger Bands | `ta.volatility.BollingerBands` (20, 2σ) | `bb_upper/lower/middle/width/pband/signal/squeeze` |
| ATR | `ta.volatility.AverageTrueRange` (14) | `atr`, `atr_pct`, `stop_loss_long`, `stop_loss_short` |
| ADX | `ta.trend.ADXIndicator` (14) | `adx`, `adx_pos` (+DI), `adx_neg` (-DI), `market_regime` |
| RSI Divergence | `_detect_rsi_divergence()` | +1 bullish, -1 bearish, 0 none |
| Support/Resistance | `_compute_support_resistance()` | Near (5-candle) and broad (20-candle) levels + % distance |
| Order book | `_get_order_book_score()` | Binance `/depth?limit=20` — supershort only |

Falls back to neutral placeholder values (`data_source: 'fallback'`) when `ta`/`pandas` are missing or klines return < 30 candles.

#### Volume — two baselines
- `volume_ratio` = current vol / 20-period MA → short-term trend confirmation
- `vol_ratio_100` = current vol / 100-period MA → high-conviction breakout filter

The 100-period baseline is used in Group 1 of the scorer. Volume only amplifies a momentum signal when `vol_ratio_100 > 1.3`, preventing it from firing on a volume surge that is elevated relative to a recent cluster of high-volume candles.

#### Bollinger Bands detail (`_compute_bollinger`)
- `bb_signal`: `at_lower` (≤ 5th percentile), `at_upper` (≥ 95th), `squeeze` (bandwidth at multi-candle low), `neutral`
- `bb_squeeze`: `True` when current bandwidth ≤ 105% of its minimum over the last 50 candles
- In **trending** regime: squeeze = compression before trend continuation → score follows EMA direction
- In **ranging** regime: at-band signals are primary mean-reversion triggers (±2 points each)

#### ATR stop-loss (`_compute_atr`)
- `stop_loss_long  = current_price − 2 × ATR`
- `stop_loss_short = current_price + 2 × ATR`

#### ADX / Market Regime
- `adx ≥ 25` → `market_regime = 'trending'`
- `adx ≤ 15` → `market_regime = 'ranging'`
- `15 < adx < 25` → `market_regime = 'transitioning'`
- **Noise zone**: `15 < adx < 22` — flagged separately; the score is reduced by 1 in absolute value and a ⚠️ notice appears in the bot message.

#### RSI Divergence (`_detect_rsi_divergence`)
Looks back 10 candles (tightened from original 20):
- **Bullish:** price ≤ 101% of its recent low AND current RSI > RSI-at-that-low + 10 → returns `+1`
- **Bearish:** price ≥ 99% of its recent high AND current RSI < RSI-at-that-high − 10 → returns `-1`

Tightened from original (n=20, threshold=5) to reduce false positives on noisy short timeframes.

---

### Signal Scoring — V2 (`_compute_score`)

The V2 algorithm groups signals by their underlying information source to prevent correlated indicators from stacking into misleadingly high scores (the **score inversion problem**: at V1 score=+5, direction accuracy was only ~44% because five signals all measured the same recent price move).

#### Group 1: Correlated momentum signals — hard cap ±2

MACD, EMA trend, volume, and 24h change are all downstream of "price went up/down recently". Their raw contributions are summed, then **capped at ±2** before adding anything else.

| Signal | Condition | Raw pts |
|---|---|---|
| MACD | fresh bullish crossover | +2 |
| MACD | sustained bullish | +1 |
| MACD | fresh bearish crossover | -2 |
| MACD | sustained bearish | -1 |
| EMA | upward | +1 |
| EMA | downward | -1 |
| Volume | increasing + `vol_ratio_100 > 1.3` + EMA upward | +1 |
| Volume | increasing + `vol_ratio_100 > 1.3` + EMA downward | -1 |
| 24h change | > +3% AND EMA upward AND RSI < 68 | +1 |
| 24h change | < -3% AND EMA downward AND RSI > 32 | -1 |

`momentum = max(-2, min(2, raw_sum))`

#### Group 2: Regime-specific signals (added to capped momentum)

**Trending (ADX ≥ 25):**

| Signal | Condition | Points |
|---|---|---|
| RSI | < 30 | +1 |
| RSI | > 70 | -1 |
| ADX DI | +DI > -DI + 5 | +1 |
| ADX DI | -DI > +DI + 5 | -1 |
| BB squeeze | squeeze + EMA upward | +1 |
| BB squeeze | squeeze + EMA downward | -1 |

**Ranging (ADX ≤ 15):**

| Signal | Condition | Points |
|---|---|---|
| RSI | < 20 | +2 |
| RSI | 20–35 | +1 |
| RSI | 65–80 | -1 |
| RSI | > 80 | -2 |
| BB | at_lower | +2 |
| BB | at_upper | -2 |

**Transitioning (ADX 15–25):**

| Signal | Condition | Points |
|---|---|---|
| RSI | < 30 | +1 |
| RSI | > 70 | -1 |
| BB | at_lower + EMA not downward | +1 |
| BB | at_upper + EMA not upward | -1 |

#### Group 3: Truly independent signals (all regimes)

| Signal | Condition | Points |
|---|---|---|
| RSI divergence | bullish | +1 |
| RSI divergence | bearish | -1 |
| Order book | buy pressure (supershort only) | +1 |
| Order book | sell pressure (supershort only) | -1 |

#### Noise-zone damper
If `15 < adx < 22` and `|final_score| > 1`: reduce `|score|` by 1.

#### Forecast Generation (`generate_forecast`)
- `price_change = score × (atr_pct / 100) × 0.5` (ATR-calibrated; falls back to `score × move_per_unit`)
- `target_price = current_price × (1 + price_change)`
- `probability = min(50 + regime_bonus + score_bonus + div_bonus, 70)`
  - `regime_bonus`: trending=4, ranging=3, transitioning=0
  - `score_bonus`: `min(|score| × 3, 12)`
  - `div_bonus`: 3 if RSI divergence, else 0
  - Hard cap: **70%** (old 80% cap was not supported by backtest results)

Recommendation thresholds:

| Timeframe | Condition | Recommendation |
|---|---|---|
| supershort | score ≥ 4 | STRONG BUY |
| supershort | score ≥ 2 | BUY |
| supershort | score ≤ -4 | STRONG SELL |
| supershort | score ≤ -2 | SELL |
| supershort | -1 to +1 | HOLD/WAIT |
| all others | score ≥ 3 | BUY |
| all others | score ≤ -3 | SELL |
| all others | -2 to +2 | HOLD |

**Forecast output dict:** `symbol`, `current_price`, `target_price`, `move`, `timeframe`, `probability`, `recommendation`, `indicators` (includes all indicator values, `signal_score`, `data_source`, S/R levels, ATR stop-losses, BB values, divergence, fear/greed, order book bias, `vol_ratio_100`), `price_data`.

---

### 2. `TelegramBot`

Wires everything together and manages the `python-telegram-bot` application.

#### Commands

| Command | Handler | Description |
|---|---|---|
| `/start` | `cmd_start` | Welcome message + TA engine status |
| `/help` | `cmd_help` | Full usage guide with indicator list |
| `/conf` | `cmd_detailed` | Complete indicator breakdown of last analysis |
| `/fng` | `cmd_fng` | Current Fear & Greed Index with bar |
| `/status` | `cmd_status` | Bot info, TA engine, klines cache size, F&G |
| Free text (e.g. `BTC`) | `on_message` | Runs mid analysis + shows timeframe keyboard |
| Inline button tap | `on_timeframe_button` | Edits message in-place with selected timeframe |

#### Inline keyboard (`_timeframe_keyboard`)
Every analysis reply includes a 2×3 button grid. Tapping a button calls `on_timeframe_button` which edits the existing message rather than sending a new one (cleaner UX).

```
[ ⚡ SS (1-15m) ] [ 🕐 Short (1-4h) ] [ 🕓 Mid (3-6h)  ]
[ 📅 Long (1-3d) ] [ 📆 ULong (1-2w) ] [ 🔥 All TFs     ]
```

Callback data format: `tf:{SYMBOL}:{timeframe}` (e.g. `tf:BTC:supershort`).

#### Message parsing (`_parse_request`)
Accepts `BTC`, `BTC/USDT`, `BTC short`, `BTC/USDT full`. Returns `(symbol, timeframe)`. Defaults to `mid`.

#### HTTPXRequest timeouts
All timeouts set to 30s to prevent spurious `TimedOut` errors on some networks:
```python
HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0)
```

#### Pre-flight connectivity check (`main`)
Before handing off to PTB, `main()` calls `requests.get(…/getMe)` to verify the token is reachable. Prints a clear error with proxy instructions if blocked. Reads `TELEGRAM_PROXY_URL` env var.

---

## Module-Level Items

| Name | Type | Purpose |
|---|---|---|
| `TA_AVAILABLE` | `bool` | True when `ta`, `pandas`, `numpy` all import successfully |
| `CryptoAnalyzer.TIMEFRAME_CONFIG` | class dict | Maps timeframe name → kline config 5-tuple |
| `CryptoAnalyzer.TIMEFRAME_DESC` | class dict | Maps timeframe name → human-readable string |
| `CryptoAnalyzer._CACHE_TTL` | class dict | Maps kline interval → cache TTL in seconds |

---

## Backtest — `backtest_real.py`

Three-way comparison: **Original** vs **V1** (regime-aware branches) vs **V2** (all improvements).

### Key design: precomputed indicator series
All indicator series (RSI, MACD, EMA, BB, ADX, ATR, volume MAs, RSI divergence) are computed **once** on the full dataset using vectorised pandas/ta operations. Each rolling window looks up values at position `i-1` — O(n) instead of O(n²). Runtime: ~6s for 7,000+ windows (was >2 minutes).

### ATR-adaptive lookahead
Instead of a fixed 6-candle evaluation window, lookahead scales with current ATR:
```python
adaptive_la = max(3, min(base_lookahead * 2, round(base_lookahead / atr_pct)))
```
High ATR → evaluate sooner; low ATR → give the trade more time. Improved target hit rate from 9.2% → 11.6%.

### CSV loading (`load_csv`)
- Supports glob patterns (`"BTCUSDT-1h-*.csv"`)
- Auto-detects Binance Data Vision format (no header, 16-digit microsecond timestamps)
- Auto-detects interval from filename (e.g. `BTCUSDT-1h-2025-01.csv` → `1h` → `mid`)
- Merges multiple files in sorted order

### Backtest results (10 months BTCUSDT 1h, Jun 2025 – Mar 2026, 7,224 windows)

| Algorithm | Dir accuracy | Target hit | Signal rate |
|---|---|---|---|
| Original | 46.5% | 9.2% | 70.2% |
| V1 regime-aware | 47.0% | 9.7% | 71.8% |
| V2 all improvements | 47.2% | **11.6%** | 74.9% |

Key findings:
- Bull score 3–4: **50.0%** direction (was 46.1% — score inversion fixed by momentum cap)
- Ranging regime: **48.1%** direction (best-performing regime)
- Rule-based TA ceiling on liquid BTC 1h is approximately 47–50%; exceeding it requires order flow data or ML

### Running
```bash
python backtest_real.py --file "BTCUSDT-1h-*.csv"
python backtest_real.py --file BTCUSDT-1h-2025-01.csv
```
Data: [data.binance.vision](https://data.binance.vision) → Spot → Monthly → klines → BTCUSDT → 1h

---

## Entry Point

```bash
python news.py
```

Reads: `TELEGRAM_BOT_TOKEN` (required), `BINANCE_API_KEY` (optional), `BINANCE_SECRET` (optional), `TELEGRAM_PROXY_URL` (optional) — from environment or `.env` via `python-dotenv`.

---

## Environment Setup

```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET=your_binance_secret

# Optional proxy:
# TELEGRAM_PROXY_URL=http://127.0.0.1:7890
# TELEGRAM_PROXY_URL=socks5://127.0.0.1:1080
```

```bash
pip install python-telegram-bot==21.0.1 requests python-dotenv
pip install ta pandas numpy
```

---

## Key Design Notes & Gotchas

1. **Score inversion problem (fixed in V2).** When MACD + EMA + volume + 24h change all fire at score=+5, they're measuring the same recent price rise — not four independent confirmations. The Group 1 momentum cap (±2) prevents this. Bull score 3–4 went from 46.1% to 50.0% direction accuracy after the fix.

2. **24h change is continuation-only in V2.** The original signal fired on spikes too. Now requires EMA to confirm direction AND RSI to not be overextended (< 68 for bull, > 32 for bear).

3. **Volume uses a 100-period baseline in V2.** The 20-period baseline included the same volume surge being measured, inflating `volume_ratio` even for ordinary candles within a high-volume cluster. Threshold raised to 1.3×.

4. **Klines cache is in-memory.** Cleared on restart. `BTC full` (5 timeframes) is the main beneficiary.

5. **`TA_AVAILABLE` flag.** If `ta`/`pandas`/`numpy` are not installed, `_fallback_indicators()` returns neutral placeholders (`data_source: 'fallback'`). The bot starts but analysis is meaningless.

6. **Order book only for supershort.** The Binance `/depth` call adds latency and is only meaningful for 1–15m scalping.

7. **Fear & Greed is macro context, not a scored signal.** Shown in messages and `/conf` for human judgment but does not affect `signal_score`.

8. **ATR stop-loss is informational.** Does not affect scoring or recommendation.

9. **No persistence.** `last_analysis`, `_klines_cache`, and `_fng_cache` are all in-memory. Reset on restart.

10. **Single-user design.** `last_analysis` is keyed by `user_id`. `/conf` shows each user their own last result.

11. **Broad exception handling.** Most methods catch all exceptions and log them. Check logs if results look wrong.

12. **Accuracy ceiling for rule-based TA.** Backtested at ~47% direction on BTC 1h. Exceeding ~50–53% requires order flow, on-chain data, or ML.

13. **`ulong` is directional bias only.** Daily candles + macro dominance means probability estimates are indicative, not precise.

---

## File Structure

```
TG-trading-bot/
├── news.py              ← MAIN FILE — all active logic lives here
├── backtest_real.py     ← Three-way backtest (original vs V1 vs V2)
├── requirements.txt     ← Dependencies (some unused legacy entries)
├── README.md            ← Project overview
├── CLAUDE.md            ← This file
├── 1/                   ← Old bot versions (archived, ignore)
├── backtest_patch.txt   ← Historical patch (archived)
├── backtest_chart_patch.txt
├── bot_setscore_patch.txt
└── readme.txt           ← Older readme
```

**Active packages** (what `news.py` actually imports):
`python-telegram-bot`, `requests`, `python-dotenv`, `ta`, `pandas`, `numpy`

`requirements.txt` contains legacy entries (`ccxt`, `apscheduler`, `plotly`, etc.) that are no longer imported.

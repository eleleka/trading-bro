# CLAUDE.md — Codebase Guide for TG-Trading-Bot

## Project Overview

A Telegram bot for **crypto market analysis**. Users type a ticker symbol; the bot fetches live price and OHLCV candle data from Binance, computes a full suite of real technical indicators, scores all signals into a single integer, and returns a forecast with a recommended action, probability estimate, and ATR-based stop-loss. Timeframe selection is done via inline keyboard buttons.

The single source-of-truth file is **`news.py`**. Everything else in the root (`1/`, patch files, old copies) is historical/archived and should be ignored.

---

## Architecture — Two Classes in `news.py`

### 1. `CryptoAnalyzer`

Fetches price data and candle data, computes all indicators, and produces scored forecast dicts.

#### Price data
- **Primary:** Binance `/ticker/price` + `/ticker/24hr`
- **Fallback:** CoinGecko `/search` + `/simple/price`

#### Klines cache (`_klines_cache`)
Results of Binance `/klines` are cached by `(symbol, interval, limit)` with per-interval TTLs to avoid redundant API calls (especially for `BTC full`, which hits 5 timeframes at once):

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
| RSI | `ta.momentum.RSIIndicator` (14) | < 30 oversold, > 70 overbought |
| MACD | `ta.trend.MACD` (12, 26, 9) | Detects fresh crossovers vs sustained |
| EMA trend | `ta.trend.EMAIndicator` (fast + slow) | upward / downward / sideways |
| Volume trend | 20-candle rolling avg vs current | increasing / stable / decreasing |
| Bollinger Bands | `ta.volatility.BollingerBands` (20, 2σ) | `bb_upper/lower/middle/width/pband/signal/squeeze` |
| ATR | `ta.volatility.AverageTrueRange` (14) | `atr`, `atr_pct`, `stop_loss_long`, `stop_loss_short` |
| RSI Divergence | `_detect_rsi_divergence()` | +1 bullish, -1 bearish, 0 none |
| Support/Resistance | `_compute_support_resistance()` | Near (5-candle) and broad (20-candle) levels + % distance |
| Order book | `_get_order_book_score()` | Binance `/depth?limit=20` — supershort only |

Falls back to neutral placeholder values (`data_source: 'fallback'`) when `ta`/`pandas` are missing or klines return < 30 candles.

#### Bollinger Bands detail (`_compute_bollinger`)
- `bb_signal`: `at_lower` (≤ 5th percentile of band), `at_upper` (≥ 95th), `squeeze` (bandwidth at multi-candle low), `neutral`
- `bb_squeeze`: `True` when current bandwidth ≤ 105% of its minimum over the last 50 candles — signals a volatility compression likely to precede a breakout

#### ATR stop-loss (`_compute_atr`)
- `stop_loss_long  = current_price − 2 × ATR`
- `stop_loss_short = current_price + 2 × ATR`

#### RSI Divergence (`_detect_rsi_divergence`)
Looks back 20 candles:
- **Bullish:** price ≤ 101.5% of its recent low AND current RSI > RSI-at-that-low + 5 → returns `+1`
- **Bearish:** price ≥ 98.5% of its recent high AND current RSI < RSI-at-that-high − 5 → returns `-1`

#### Signal Scoring (`_compute_score`)

| Signal | Condition | Points |
|---|---|---|
| RSI | < 30 (oversold) | +1 |
| RSI | > 70 (overbought) | -1 |
| MACD | fresh bullish crossover | +2 |
| MACD | sustained bullish | +1 |
| MACD | fresh bearish crossover | -2 |
| MACD | sustained bearish | -1 |
| EMA | upward | +1 |
| EMA | downward | -1 |
| Volume | increasing + EMA upward | +1 |
| Volume | increasing + EMA downward | -1 |
| 24h change | > +3% | +1 |
| 24h change | < -3% | -1 |
| Bollinger | at_lower + EMA not downward | +1 |
| Bollinger | at_upper + EMA not upward | -1 |
| RSI divergence | bullish | +1 |
| RSI divergence | bearish | -1 |
| Order book | buy pressure (supershort only) | +1 |
| Order book | sell pressure (supershort only) | -1 |

Maximum achievable: **±9** (supershort) / **±8** (other timeframes).

#### Forecast Generation (`generate_forecast`)
- `price_change = signal_score × move_per_score_unit` (deterministic)
- `target_price = current_price × (1 + price_change)`
- `probability  = min(50 + abs(score) × 5, 80)` — 50% (neutral) to 80% (max alignment)

Recommendation thresholds:

| Timeframe | Condition | Recommendation |
|---|---|---|
| supershort | score ≥ 3 | STRONG BUY |
| supershort | score ≥ 1 | BUY |
| supershort | score ≤ -3 | STRONG SELL |
| supershort | score ≤ -1 | SELL |
| supershort | score = 0 | HOLD/WAIT |
| all others | score ≥ 2 | BUY |
| all others | score ≤ -2 | SELL |
| all others | -1 to +1 | HOLD |

**Forecast output dict:** `symbol`, `current_price`, `target_price`, `move`, `timeframe`, `probability`, `recommendation`, `indicators` (includes all indicator values, `signal_score`, `data_source`, S/R levels, ATR stop-losses, BB values, divergence, fear/greed, order book bias), `price_data`.

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
- Accepts `BTC`, `BTC/USDT`, `BTC short`, `BTC/USDT full`, etc.
- Returns `(symbol, timeframe)` tuple; defaults to `mid` if no timeframe given.

---

## Module-Level Items

| Name | Type | Purpose |
|---|---|---|
| `TA_AVAILABLE` | `bool` | True when `ta`, `pandas`, `numpy` all import successfully |
| `CryptoAnalyzer.TIMEFRAME_CONFIG` | class dict | Maps timeframe name → kline config 5-tuple |
| `CryptoAnalyzer.TIMEFRAME_DESC` | class dict | Maps timeframe name → human-readable string |
| `CryptoAnalyzer._CACHE_TTL` | class dict | Maps kline interval → cache TTL in seconds |

---

## Entry Point

```bash
python news.py
```

Reads: `TELEGRAM_BOT_TOKEN` (required), `BINANCE_API_KEY` (optional), `BINANCE_SECRET` (optional) — from environment or `.env` file via `python-dotenv`.

---

## Environment Setup

```
# .env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
BINANCE_API_KEY=your_binance_api_key      # optional
BINANCE_SECRET=your_binance_secret        # optional
```

```bash
pip install python-telegram-bot==21.0.1 requests python-dotenv
pip install ta pandas numpy               # required for real TA
```

> `requirements.txt` lists packages from earlier versions (`ccxt`, `apscheduler`, `plotly`, etc.) that are no longer imported. The actively used packages are: `python-telegram-bot`, `requests`, `python-dotenv`, `ta`, `pandas`, `numpy`.

---

## Key Design Notes & Gotchas

1. **Klines cache is in-memory.** Cleared on restart. A `BTC full` request is the main beneficiary — without the cache, it would hit `/klines` 5 times sequentially; with it, repeated button taps within the TTL window cost zero network calls.

2. **`TA_AVAILABLE` flag.** If `ta`/`pandas`/`numpy` are not installed, `_fallback_indicators()` returns neutral placeholders (RSI=50, MACD=neutral, etc.) and `data_source` is set to `'fallback'`. The bot starts and functions but analysis is meaningless. `/start` and `/status` show which mode is active.

3. **Order book only for supershort.** The Binance `/depth` call adds latency and is only meaningful for 1–15m scalping. It is skipped for all other timeframes.

4. **Fear & Greed is macro context, not a scored signal.** It is displayed in the forecast summary and `/conf` for human judgment but does not directly affect `signal_score` or `recommendation`.

5. **ATR stop-loss is informational.** The 2×ATR stop is a standard starting point; it does not affect scoring or recommendation.

6. **RSI divergence is a 20-candle lookback.** The threshold (±5 RSI points at the pivot) is conservative to avoid false positives on noisy short timeframes.

7. **No persistence.** `last_analysis`, `_klines_cache`, and `_fng_cache` are all in-memory. They reset on restart.

8. **Single-user design.** `last_analysis` is keyed by `user_id` but there is no per-user state beyond that. Multiple users can run analyses independently; `/conf` shows each user their own last result.

9. **Broad exception handling.** Most methods catch all exceptions and log them, keeping the bot alive but potentially masking bugs. Check logs if results look wrong.

10. **`ulong` confidence is inherently limited.** Daily candles + ±4% move/unit means macro factors dominate. Even perfect signal alignment only reaches 80% probability. Treat `ulong` forecasts as directional bias, not precise targets.

---

## File Structure

```
TG-trading-bot/
├── news.py              ← MAIN FILE — all active logic lives here
├── requirements.txt     ← Dependencies (some unused; ta/pandas/numpy now active)
├── README.md            ← General project overview (outdated)
├── CLAUDE.md            ← This file
├── 1/                   ← Old bot versions (archived, ignore)
├── backtest_patch.txt   ← Historical patch (archived)
├── backtest_chart_patch.txt
├── bot_setscore_patch.txt
└── readme.txt           ← Older readme
```

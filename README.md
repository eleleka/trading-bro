# Crypto Analysis Bot 🤖📈

A Telegram bot for crypto market analysis. Type a ticker symbol (e.g. `BTC`, `ETH`) and the bot fetches live price data from Binance, computes a full suite of technical indicators, and returns a forecast with a recommended action, probability estimate, and ATR-based stop-loss. Timeframe selection is done via inline keyboard buttons.

> ⚠️ Educational purposes only. Not financial advice. Do your own research.

---

## Features

- **Live price data** from Binance (fallback to CoinGecko)
- **Real technical analysis**: RSI, MACD, EMA, Bollinger Bands, ATR, ADX, Volume, Support/Resistance
- **Regime-aware scoring**: algorithm adapts based on whether the market is trending, ranging, or transitioning (ADX-based)
- **Momentum signal cap**: prevents correlated indicators from creating misleadingly high-confidence scores
- **ATR stop-loss levels**: 2× ATR for long and short positions
- **RSI divergence detection**: bullish and bearish divergence signals
- **Bollinger Band squeeze detection**: volatility compression warning
- **Fear & Greed Index**: macro sentiment context from alternative.me
- **Order book analysis**: buy/sell pressure for scalping (supershort timeframe only)
- **Five timeframes**: supershort (1m), short (15m), mid (1h), long (4h), ultra-long (1d)
- **Inline keyboard**: tap a button to switch timeframes without retyping
- **Proxy support**: for networks where Telegram is blocked

---

## Requirements

```bash
pip install python-telegram-bot==21.0.1 requests python-dotenv ta pandas numpy
```

No database, no persistence, no external scheduler required.

---

## Setup

### 1. Get API keys

**Telegram:**
- Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token

**Binance (optional but recommended):**
- Binance.com → API Management → Create key → enable **read-only** access

### 2. Create `.env`

```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET=your_binance_secret

# Proxy (uncomment if Telegram is blocked on your network):
# TELEGRAM_PROXY_URL=http://127.0.0.1:7890        (HTTP / Clash)
# TELEGRAM_PROXY_URL=socks5://127.0.0.1:1080      (SOCKS5 / shadowsocks)
```

### 3. Run

```bash
python news.py
```

The bot verifies the token on startup and shows TA engine status before polling begins.

---

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and TA engine status |
| `/help` | Full usage guide with indicator list |
| `/conf` | Complete indicator breakdown of the last analysis |
| `/fng` | Current Fear & Greed Index with visual bar |
| `/status` | Bot info, TA engine status, klines cache size |
| `BTC` (free text) | Run mid-timeframe analysis and show timeframe keyboard |
| `BTC short` | Run analysis at a specific timeframe directly |
| `BTC/USDT full` | Run all five timeframes in one message |

---

## Timeframes

| Button | Interval | Candles | Use case |
|---|---|---|---|
| ⚡ SS | 1m | 100 | Scalping (includes order book) |
| 🕐 Short | 15m | 100 | Intraday swing |
| 🕓 Mid | 1h | 150 | Default — swing trade |
| 📅 Long | 4h | 150 | Multi-day position |
| 📆 ULong | 1d | 150 | Macro / weekly view |
| 🔥 All TFs | — | — | Full breakdown all timeframes |

---

## Indicators

| Indicator | Notes |
|---|---|
| RSI (14) | Oversold < 30, overbought > 70; tiered thresholds in ranging markets |
| MACD (12/26/9) | Fresh crossovers weighted higher; sustained trend weighted lower |
| EMA (fast/slow) | Trend direction: upward / downward / sideways |
| Bollinger Bands (20, 2σ) | At-band signals, squeeze detection, squeeze follows trend in trending regime |
| ATR (14) | 2× ATR stop-loss levels for long and short |
| ADX (14) | Market regime: trending ≥ 25, ranging ≤ 15, transitioning 15–25 |
| Volume | Short-term ratio (vs 20-period MA) + conviction ratio (vs 100-period MA) |
| RSI Divergence | 10-candle lookback, ±10 RSI threshold |
| Support/Resistance | Near (5-candle) and broad (20-candle) levels |
| Order Book | Binance depth top-20, supershort only |
| Fear & Greed | alternative.me, cached 1 hour |

---

## Scoring Algorithm (V2)

Signals are grouped to prevent correlated indicators from inflating scores:

**Group 1 — Momentum (capped at ±2):** MACD + EMA + volume (vs 100-period baseline) + 24h change (continuation only, EMA-confirmed). Capping prevents the "score inversion" problem where a strong recent move generates a misleadingly high score by being counted four times.

**Group 2 — Regime-specific:**
- *Trending*: RSI extreme, ADX +DI/-DI bias, BB squeeze direction
- *Ranging*: Tiered RSI (±1 at 35/65, ±2 at 20/80), BB at-band (±2)
- *Transitioning*: Standard RSI extremes + BB positional signal

**Group 3 — Independent:** RSI divergence, order book (supershort)

**Noise-zone damper:** When ADX is 15–22 (uncertain regime boundary), the absolute score is reduced by 1 and a ⚠️ noise zone warning appears in the message.

**Recommendation thresholds:**
- Supershort: `|score| ≥ 4` → STRONG BUY/SELL, `|score| ≥ 2` → BUY/SELL
- All others: `|score| ≥ 3` → BUY/SELL

**Probability:** capped at 70% (50% base + regime bonus + score bonus + divergence bonus). The old formula allowed 80%, which was not empirically supported.

---

## Backtest Results

Tested against 10 months of real BTCUSDT 1h data (Jun 2025 – Mar 2026), 7,224 rolling windows:

| Algorithm | Direction accuracy | Target hit rate | Signal rate |
|---|---|---|---|
| Original | 46.5% | 9.2% | 70.2% |
| V1 (regime-aware) | 47.0% | 9.7% | 71.8% |
| V2 (all improvements) | 47.2% | **11.6%** | 74.9% |

Key findings:
- Bull score 3–4 improved from 46.1% → **50.0%** direction accuracy after momentum cap (score inversion fixed for bullish signals)
- Target hit rate improved +26% due to ATR-adaptive lookahead
- Ranging regime: **48.1%** — best-performing market condition
- Rule-based TA on liquid BTC hourly data has an inherent ceiling around 47–50%; beating it consistently requires order flow data or ML

Run your own backtest with Binance Data Vision CSVs:
```bash
# Download from: https://data.binance.vision → Spot → Monthly → klines → BTCUSDT → 1h
python backtest_real.py --file "BTCUSDT-1h-*.csv"
```

---

## File Structure

```
TG-trading-bot/
├── news.py              ← Main file — all active logic
├── backtest_real.py     ← Three-way backtest (original vs V1 vs V2)
├── requirements.txt     ← Dependencies
├── CLAUDE.md            ← Developer/AI codebase guide
├── README.md            ← This file
├── 1/                   ← Archived old versions (ignore)
├── backtest_patch.txt   ← Historical patches (ignore)
└── backtest_chart_patch.txt
```

---

## Security

- Use **read-only** Binance API keys
- Never commit your `.env` file
- The bot does not place any trades — it only reads market data

---

## License

MIT-style. Educational use only. No guarantees. Use at your own risk.

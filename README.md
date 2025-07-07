# Crypto Trading Bot 🤖📈

A Telegram bot for crypto market analysis using Binance data, technical indicators, Bitcoin trend filtering, and news sentiment detection. Built with Python, `python-telegram-bot`, and `ccxt`.

---

## 🚀 Features

- ✅ Technical analysis (MA, RSI, volume)
- 🔄 Scheduled auto-checks (1h–24h)
- 🔔 Auto alerts & trend monitoring
- 📰 Crypto news sentiment parsing
- 🧠 Recommendations: BUY / SELL / HOLD
- 💾 SQLite persistence
- 📊 Chart generation (optional)

---

## 🛠 Requirements

See `requirements.txt` for details.

Install all dependencies:

```bash
pip install -r requirements.txt
```

---

## 📦 Project Structure

```
crypto_trading_bot/
├── crypto_trading_bot.py    # Main bot logic
├── requirements.txt         # Dependencies
├── crypto_bot.db            # SQLite DB (auto-created)
├── .env                     # Environment config
└── README.md                # This file
```

---

## 🔐 Setup Instructions

### 1. Get your API keys

#### Telegram:
- Message @BotFather
- Run `/newbot`
- Get the token

#### Binance (optional):
- Go to Binance.com
- API Management → Create key
- Enable **read-only access**
- Copy `API Key` and `Secret`

---

### 2. Configure `.env` file

Create `.env`:

```
TELEGRAM_BOT_TOKEN=1234567890:ABCDEFyourtoken
BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET=your_binance_secret
```

---

### 3. Run the bot

```bash
python crypto_trading_bot.py
```

---

## 📘 Commands

| Command           | Description |
|------------------|-------------|
| `/start`         | Welcome message |
| `/setticker <T>` | Set ticker (e.g. PEPEUSDT) |
| `/analyze <T>`   | Analyze ticker instantly |
| `/watchlist`     | Show current settings |
| `/setinterval Xh`| Set auto-check interval |
| `/enablealerts`  | Enable scheduled alerts |
| `/disablealerts` | Disable alerts |
| `/help`          | Help & usage info |

---

## 📊 Analysis & Strategy

- **Indicators**: MA7, MA25, MA99, RSI14, volume %
- **Bitcoin trend filtering**
- **Support/resistance detection**
- **News sentiment scan**
- **Trend change detection**
- Score-based recommendation logic:
  - `>= 3` → ✅ BUY
  - `<= -3` → ❌ SELL
  - Else → ⏸ HOLD

---

## 🛡 Security

- ❗ Use **read-only** Binance keys
- ⚠️ This bot is **not a trading bot**
- Do your own research before trading

---

## 🧪 Troubleshooting

| Problem           | Solution |
|------------------|----------|
| `Module not found` | Run `pip install -r requirements.txt` |
| `Invalid token`   | Check bot token from @BotFather |
| `Exchange not available` | Check internet or API region blocks |

---

## 📈 Extend It

- Add MACD, Bollinger Bands, Stochastic
- Add more RSS feeds (e.g. Cointelegraph)
- Use Plotly for candlestick charts
- Add backtesting logic

---

## 📄 License

MIT-style. Educational use only. No guarantees.
Use at your own risk.

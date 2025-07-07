# requirements.txt

# Telegram Bot Framework
python-telegram-bot==21.0.1

# Crypto Exchange APIs
ccxt==4.1.68

# Data Analysis
pandas==2.1.4
numpy==1.24.4

# Technical Analysis
ta==0.10.2

# News and Web Scraping
feedparser==6.0.10
requests==2.31.0

# Task Scheduling
apscheduler==3.10.4

# Альтернативні бібліотеки для розширеної функціональності
# TA-Lib==0.4.28  # Для більш потужного технічного аналізу
# nltk==3.8.1     # Для аналізу сентиментів новин
# textblob==0.17.1 # Для обробки тексту
# plotly==5.17.0  # Для створення графіків
# aiogram==3.3.0  # Альтернатива python-telegram-bot

# Environment Variables
python-dotenv==1.0.0

# Optional: Better Logging
# colorlog==6.8.0

# Development Dependencies (uncomment for development)
# pytest==7.4.3
# black==23.11.0
# flake8==6.1.0

# =============================================================================
# ІНСТРУКЦІЯ ПО ВСТАНОВЛЕННЮ ТА ЗАПУСКУ
# =============================================================================

## 1. Встановлення залежностей
```bash
pip install -r requirements.txt
```

## 2. Отримання токенів

### Telegram Bot Token:
1. Напишіть @BotFather в Telegram
2. Надішліть команду /newbot
3. Вкажіть назву бота (наприклад: "Crypto Trading Assistant")
4. Вкажіть username бота (наприклад: "my_crypto_trading_bot")
5. Скопіюйте отриманий токен

### Binance API (опціонально для вищої швидкості):
1. Зареєструйтесь на binance.com
2. Перейдіть в Account → API Management
3. Створіть новий API ключ
4. Увімкніть "Enable Reading" (торгівля НЕ потрібна)
5. Скопіюйте API Key та Secret Key

## 3. Конфігурація
Відредагуйте файл crypto_trading_bot.py:

```python
# Замініть ці значення на ваші реальні токени
BOT_TOKEN = "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
BINANCE_API_KEY = "your_binance_api_key_here"    # Опціонально
BINANCE_SECRET = "your_binance_secret_here"      # Опціонально
```

## 4. Запуск бота
```bash
python crypto_trading_bot.py
```

## 5. Використання бота

### Базові команди:
- `/start` - запуск бота
- `/setticker PEPEUSDT` - встановити тікер для моніторингу
- `/analyze BTCUSDT` - миттєвий аналіз
- `/help` - довідка

### Налаштування:
- `/setinterval 2h` - змінити інтервал перевірки
- `/enablealerts` - увімкнути автоматичні сповіщення
- `/disablealerts` - вимкнути сповіщення
- `/watchlist` - поточні налаштування

## 6. Структура проекту
```
crypto_trading_bot/
├── crypto_trading_bot.py    # Основний файл бота
├── requirements.txt         # Залежності
├── crypto_bot.db           # База даних SQLite (створюється автоматично)
└── README.md               # Ця інструкція
```

## 7. Функціональність

### Технічний аналіз:
- Ковзні середні (MA7, MA25, MA99)
- RSI індикатор (14 періодів)
- Аналіз об'єму торгів
- Визначення тренду
- Фільтрація по тренду Bitcoin

### Рекомендації:
- ✅ BUY - сильні сигнали до покупки
- ❌ SELL - сильні сигнали до продажу
- ⏸️ HOLD - утримувати позицію

### Автоматизація:
- Періодичні перевірки (1h - 24h)
- Автоматичні сповіщення
- Збереження історії аналізу

### Новини:
- Моніторинг CoinDesk RSS
- Базовий аналіз сентиментів
- Попередження про волатильність

## 8. Безпека

⚠️ **ВАЖЛИВО:**
- Не використовуйте API ключі з правами на торгівлю
- Бот призначений ТІЛЬКИ для аналізу, не для торгівлі
- Всі рекомендації мають інформаційний характер
- Завжди проводьте власний аналіз перед торгівлею

## 9. Поширені помилки

### "Module not found":
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### "Invalid token":
- Перевірте правильність Telegram Bot Token
- Переконайтесь, що токен не містить зайвих символів

### "Exchange not available":
- Перевірте інтернет-з'єднання
- Binance API може бути заблокованим в деяких регіонах

## 10. Розширення функціональності

### Додавання нових індикаторів:
```python
# У функції calculate_technical_indicators
df['macd'] = ta.trend.macd(df['close'])
df['bollinger_high'] = ta.volatility.bollinger_hband(df['close'])
df['bollinger_low'] = ta.volatility.bollinger_lband(df['close'])
```

### Додавання нових джерел новин:
```python
# У функції get_crypto_news
feed2 = feedparser.parse('https://cointelegraph.com/rss')
feed3 = feedparser.parse('https://decrypt.co/feed')
```

### Додавання графіків:
```python
import plotly.graph_objects as go
import plotly.io as pio

# Створення candlestick графіка
fig = go.Figure(data=go.Candlestick(
    x=df['timestamp'],
    open=df['open'],
    high=df['high'],
    low=df['low'],
    close=df['close']
))
```

## 11. Підтримка
- Для технічних питань: перевірте логи бота
- Для проблем з API: документація ccxt та python-telegram-bot
- Для питань по алгоритмам: документація бібліотеки ta

## 12. Ліцензія
Цей код надається "як є" для навчальних цілей. Використовуйте на свій ризик.

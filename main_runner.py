#!/usr/bin/env python3
"""
Основний файл для запуску Crypto Trading Bot
"""

import sys
import os
import logging
import asyncio
from datetime import datetime
import signal
from pathlib import Path

# Додаємо поточну директорію до шляху
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from config_file import config, MESSAGES, setup_environment
    from crypto_trading_bot import CryptoTradingBot
except ImportError as e:
    print(f"❌ Помилка імпорту: {e}")
    print("Переконайтесь, що всі необхідні файли знаходяться в одній директорії:")
    print("  - main_runner.py (цей файл)")
    print("  - crypto_trading_bot.py")
    print("  - config_file.py")
    print("  - requirements.txt")
    sys.exit(1)

def setup_logging():
    """Налаштовує логування"""

    # Створюємо директорію для логів
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)

    # Налаштовуємо формат логування
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Налаштовуємо рівень логування
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    # Конфігуруємо логування
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.FileHandler(log_dir / config.LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    # Зменшуємо рівень логування для сторонніх бібліотек
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.getLogger('ccxt').setLevel(logging.WARNING)

    return logging.getLogger(__name__)

def check_requirements():
    """Перевіряє наявність необхідних залежностей"""
    required_modules = [
        'telegram', 'ccxt', 'pandas', 'numpy', 'ta',
        'feedparser', 'requests', 'apscheduler'
    ]

    missing_modules = []

    for module in required_modules:
        try:
            __import__(module)
        except ImportError:
            missing_modules.append(module)

    if missing_modules:
        print("❌ Відсутні необхідні модулі:")
        for module in missing_modules:
            print(f"  - {module}")
        print("\nДля встановлення виконайте:")
        print("pip install -r requirements.txt")
        return False

    return True

def create_directories():
    """Створює необхідні директорії"""
    directories = ['logs', 'backups', 'data']

    for directory in directories:
        Path(directory).mkdir(exist_ok=True)

    print("📁 Створено необхідні директорії")

def check_tokens():
    """Перевіряє наявність необхідних токенів"""
    if not config.TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не встановлено!")
        print("\nДля налаштування токена:")
        print("1. Створіть файл .env у корені проекту")
        print("2. Додайте рядок: TELEGRAM_BOT_TOKEN=your_token_here")
        print("3. Або встановіть змінну оточення TELEGRAM_BOT_TOKEN")
        print("4. Отримати токен можна у @BotFather в Telegram")
        return False

    if not config.BINANCE_API_KEY:
        print("⚠️ BINANCE_API_KEY не встановлено")
        print("Бот буде працювати з публічними даними Binance")
        print("Для вищої швидкості встановіть API ключі")

    return True

def signal_handler(signum, frame):
    """Обробник сигналів для graceful shutdown"""
    print(f"\n🛑 Отримано сигнал {signum}. Завершуємо роботу...")
    sys.exit(0)

def print_startup_banner():
    """Виводить банер при запуску"""
    banner = """
╔═══════════════════════════════════════════════════╗
║            🤖 CRYPTO TRADING BOT 🚀              ║
║                                                   ║
║  Telegram-бот для аналізу криптовалют            ║
║  Версія: 1.0.0                                   ║
║  Автор: AI Assistant                             ║
║                                                   ║
╚═══════════════════════════════════════════════════╝
"""
    print(banner)
    print(f"🕐 Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🐍 Python: {sys.version.split()[0]}")
    print(f"📁 Робоча директорія: {os.getcwd()}")

def print_configuration():
    """Виводить поточну конфігурацію"""
    print("\n⚙️ Конфігурація бота:")
    print(f"  📊 Таймфрейм: {config.DEFAULT_TIMEFRAME}")
    print(f"  ⏰ Інтервал: {config.DEFAULT_INTERVAL_HOURS} годин")
    print(f"  📈 RSI пороги: {config.RSI_OVERSOLD} - {config.RSI_OVERBOUGHT}")
    print(f"  📦 Поріг об'єму: {config.VOLUME_THRESHOLD}%")
    print(f"  📰 Джерел новин: {len(config.NEWS_SOURCES)}")
    print(f"  🗃️ База даних: {config.DATABASE_PATH}")
    print(f"  📋 Лог файл: {config.LOG_FILE}")

def run_tests():
    """Запуск тестів для перевірки функціональності"""
    print("🧪 Запуск тестів...")

    # Тест підключення до Binance
    try:
        import ccxt
        exchange = ccxt.binance({'enableRateLimit': True})
        ticker = exchange.fetch_ticker('BTC/USDT')
        print(f"✅ Binance API: OK (BTC/USDT = ${ticker['last']:.2f})")
    except Exception as e:
        print(f"❌ Binance API: {e}")

    # Тест обробки новин
    try:
        import feedparser
        feed = feedparser.parse('https://www.coindesk.com/arc/outboundfeeds/rss/')
        if feed.entries:
            print(f"✅ RSS новини: OK ({len(feed.entries)} новин)")
        else:
            print("⚠️ RSS новини: Немає новин")
    except Exception as e:
        print(f"❌ RSS новини: {e}")

    # Тест технічного аналізу
    try:
        import pandas as pd
        import numpy as np
        import ta

        # Створюємо тестові дані
        data = pd.DataFrame({
            'close': np.random.randn(100).cumsum() + 100,
            'volume': np.random.rand(100) * 1000
        })

        # Тестуємо індикатори
        ma7 = ta.trend.sma_indicator(data['close'], window=7)
        rsi = ta.momentum.rsi(data['close'], window=14)

        print(f"✅ Технічний аналіз: OK")
    except Exception as e:
        print(f"❌ Технічний аналіз: {e}")

    print("🧪 Тести завершено\n")

def create_env_file():
    """Створює приклад .env файлу"""
    env_content = """# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# Binance API Configuration (Optional)
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_SECRET=your_binance_secret_here

# Bot Settings
DEFAULT_INTERVAL_HOURS=4
RSI_OVERSOLD=30
RSI_OVERBOUGHT=70
VOLUME_THRESHOLD=10.0
LOG_LEVEL=INFO

# Instructions:
# 1. Get Telegram Bot Token from @BotFather
# 2. Get Binance API keys from binance.com (optional)
# 3. Replace 'your_*_here' with real values
# 4. Remove this comment section
"""

    if not os.path.exists('.env'):
        with open('.env.example', 'w', encoding='utf-8') as f:
            f.write(env_content)
        print("📄 Створено .env.example з прикладом конфігурації")
        print("💡 Скопіюйте .env.example в .env та заповніть токени")

def main():
    """Основна функція запуску бота"""

    # Обробка аргументів командного рядка
    if len(sys.argv) > 1:
        if sys.argv[1] == 'test':
            print_startup_banner()
            run_tests()
            return 0
        elif sys.argv[1] == 'setup':
            print("🔧 Налаштування проекту...")
            setup_environment()
            create_directories()
            create_env_file()
            print("✅ Налаштування завершено!")
            return 0
        elif sys.argv[1] == '--help':
            print("Використання:")
            print("  python main_runner.py        - запуск бота")
            print("  python main_runner.py test   - запуск тестів")
            print("  python main_runner.py setup  - налаштування проекту")
            return 0

    # Виводимо банер
    print_startup_banner()

    # Перевіряємо залежності
    if not check_requirements():
        return 1

    # Налаштовуємо оточення
    setup_environment()

    # Створюємо директорії
    create_directories()

    # Створюємо приклад .env файлу
    create_env_file()

    # Перевіряємо конфігурацію
    if not config.validate():
        return 1

    # Перевіряємо токени
    if not check_tokens():
        return 1

    # Виводимо конфігурацію
    print_configuration()

    # Налаштовуємо логування
    logger = setup_logging()

    # Встановлюємо обробники сигналів
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Створюємо та запускаємо бота
        logger.info("🚀 Запуск Crypto Trading Bot...")

        bot = CryptoTradingBot(
            bot_token=config.TELEGRAM_BOT_TOKEN,
            binance_api_key=config.BINANCE_API_KEY,
            binance_secret=config.BINANCE_SECRET
        )

        print("\n✅ Бот успішно запущено!")
        print("💬 Знайдіть бота в Telegram та надішліть /start")
        print("🛑 Для зупинки натисніть Ctrl+C")
        print("📋 Логи зберігаються в папці logs/")
        print("-" * 50)

        # Запускаємо бота
        bot.run()

    except KeyboardInterrupt:
        logger.info("🛑 Бот зупинено користувачем")
        print("\n👋 Бот зупинено. До зустрічі!")
        return 0

    except Exception as e:
        logger.error(f"💥 Критична помилка: {e}", exc_info=True)
        print(f"\n❌ Критична помилка: {e}")
        print("📋 Детальну інформацію дивіться в лог файлі")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)

# Пустий сервер, щоб Render вважав сервіс живим
from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running!"

def run_web_server():
    app.run(host='0.0.0.0', port=10000)

if __name__ == "__main__":
    threading.Thread(target=run_web_server).start()
    main()

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
import threading
from flask import Flask

# Додаємо поточну директорію до шляху
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from config_file import config, MESSAGES, setup_environment
    from crypto_trading_bot import CryptoTradingBot
except ImportError as e:
    print(f"❌ Помилка імпорту: {e}")
    sys.exit(1)

# ==== Flask фіктивний сервер для Render ====
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Bot is alive!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)

# ==== Основна логіка ====

def setup_logging():
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.FileHandler(log_dir / config.LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.getLogger('ccxt').setLevel(logging.WARNING)

    return logging.getLogger(__name__)

def check_requirements():
    required_modules = [
        'telegram', 'ccxt', 'pandas', 'numpy', 'ta',
        'feedparser', 'requests', 'apscheduler'
    ]
    for module in required_modules:
        try:
            __import__(module)
        except ImportError:
            print(f"❌ Модуль {module} відсутній. Встанови: pip install -r requirements.txt")
            return False
    return True

def create_directories():
    for directory in ['logs', 'backups', 'data']:
        Path(directory).mkdir(exist_ok=True)

def check_tokens():
    """Перевіряє наявність необхідних токенів"""
    if not config.TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не встановлено!")
        return False
    return True

def signal_handler(signum, frame):
    """Обробник сигналів для graceful shutdown"""
    print(f"\n🛑 Отримано сигнал {signum}. Завершуємо роботу...")
    sys.exit(0)

def print_startup_banner():
    print("""
╔═══════════════════════════════════════════════════╗
║            🤖 CRYPTO TRADING BOT 🚀              ║
╚═══════════════════════════════════════════════════╝
""")
    print(f"🕐 Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📁 Робоча директорія: {os.getcwd()}")

def print_configuration():
    print("\n⚙️ Конфігурація:")
    print(f"  📊 Таймфрейм: {config.DEFAULT_TIMEFRAME}")
    print(f"  ⏰ Інтервал: {config.DEFAULT_INTERVAL_HOURS} годин")
    print(f"  📰 Джерел новин: {len(config.NEWS_SOURCES)}")
    print(f"  🗃️ База даних: {config.DATABASE_PATH}")
    print(f"  📋 Лог файл: {config.LOG_FILE}")

def main():
    print_startup_banner()

    if not check_requirements():
        return 1

    setup_environment()
    create_directories()

    if not config.validate():
        return 1

    if not check_tokens():
        return 1

    print_configuration()

    logger = setup_logging()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        logger.info("🚀 Запуск Crypto Trading Bot...")

        bot = CryptoTradingBot(
            bot_token=config.TELEGRAM_BOT_TOKEN,
            binance_api_key=config.BINANCE_API_KEY,
            binance_secret=config.BINANCE_SECRET
        )

        print("\n✅ Бот успішно запущено!")
        print("💬 Знайди бота в Telegram та надішли /start")
        print("🛑 Ctrl+C для зупинки")
        print("📋 Логи зберігаються в logs/")
        print("-" * 50)

        bot.run()

    except KeyboardInterrupt:
        logger.info("🛑 Бот зупинено користувачем")
        print("👋 До зустрічі!")
        return 0

    except Exception as e:
        logger.error(f"💥 Критична помилка: {e}", exc_info=True)
        print(f"\n❌ Помилка: {e}")
        return 1

if __name__ == "__main__":
    # 🧵 Запускаємо Flask в окремому потоці
    threading.Thread(target=run_flask).start()

    # 🚀 Запускаємо основну логіку
    exit_code = main()
    sys.exit(exit_code)

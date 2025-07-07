# config.py
"""
Файл конфігурації для Crypto Trading Bot
"""

import os
from dataclasses import dataclass
from typing import Dict, List

@dataclass
class BotConfig:
    """Конфігурація бота"""
    
    # Основні налаштування
    TELEGRAM_BOT_TOKEN: str = os.getenv('TELEGRAM_BOT_TOKEN', '')
    BINANCE_API_KEY: str = os.getenv('BINANCE_API_KEY', '')
    BINANCE_SECRET: str = os.getenv('BINANCE_SECRET', '')
    
    # Налаштування бази даних
    DATABASE_PATH: str = 'crypto_bot.db'
    
    # Налаштування технічного аналізу
    DEFAULT_TIMEFRAME: str = '4h'
    DEFAULT_INTERVAL_HOURS: int = 4
    CANDLES_LIMIT: int = 100
    
    # Параметри індикаторів
    MA_PERIODS: Dict[str, int] = None
    RSI_PERIOD: int = 14
    
    # Пороги для рекомендацій
    RSI_OVERSOLD: float = 30.0
    RSI_OVERBOUGHT: float = 70.0
    VOLUME_THRESHOLD: float = 10.0  # відсотки
    
    # Налаштування новин
    NEWS_SOURCES: List[str] = None
    NEWS_UPDATE_HOURS: int = 1
    
    # Налаштування логування
    LOG_LEVEL: str = 'INFO'
    LOG_FILE: str = 'crypto_bot.log'
    
    def __post_init__(self):
        """Ініціалізація значень за замовчуванням"""
        if self.MA_PERIODS is None:
            self.MA_PERIODS = {
                'fast': 7,
                'medium': 25,
                'slow': 99
            }
        
        if self.NEWS_SOURCES is None:
            self.NEWS_SOURCES = [
                'https://www.coindesk.com/arc/outboundfeeds/rss/',
                'https://cointelegraph.com/rss',
                'https://decrypt.co/feed'
            ]
    
    def validate(self) -> bool:
        """Перевіряє конфігурацію"""
        errors = []
        
        if not self.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN не встановлено")
        
        if self.DEFAULT_INTERVAL_HOURS < 1:
            errors.append("DEFAULT_INTERVAL_HOURS має бути >= 1")
        
        if not (0 < self.RSI_OVERSOLD < self.RSI_OVERBOUGHT < 100):
            errors.append("Некоректні пороги RSI")
        
        if errors:
            print("❌ Помилки конфігурації:")
            for error in errors:
                print(f"  - {error}")
            return False
        
        return True

# Глобальна конфігурація
config = BotConfig()

# Словники для локалізації
MESSAGES = {
    'start': """🤖 Вітаю! Я ваш помічник для торгівлі криптовалютами.

📋 Доступні команди:
/setticker PEPEUSDT - встановити тікер для моніторингу
/analyze PEPEUSDT - миттєвий аналіз
/setinterval 2h - змінити інтервал перевірки
/watchlist - список відстежуваних тікерів
/enablealerts - увімкнути автоматичні сповіщення
/disablealerts - вимкнути автоматичні сповіщення
/help - довідка

🚀 Почніть з команди /setticker для встановлення тікера!""",
    
    'help': """📚 Довідка по командах:

🎯 /setticker <TICKER> - встановити тікер для моніторингу
   Приклад: /setticker PEPEUSDT

🔍 /analyze <TICKER> - миттєвий аналіз тікера
   Приклад: /analyze BTCUSDT

⏰ /setinterval <TIME> - змінити інтервал перевірки
   Приклад: /setinterval 2h
   Доступні: 1h, 2h, 4h, 8h, 12h, 24h

📋 /watchlist - показати поточні налаштування

🔔 /enablealerts - увімкнути автоматичні сповіщення
🔕 /disablealerts - вимкнути автоматичні сповіщення

📊 Технічний аналіз включає:
• Ковзні середні (MA7, MA25, MA99)
• RSI індикатор
• Аналіз об'єму
• Тренд Bitcoin як фільтр
• Моніторинг новин

💡 Рекомендації:
• ✅ BUY - сигнал до покупки
• ❌ SELL - сигнал до продажу  
• ⏸️ HOLD - утримувати позицію""",
    
    'ticker_set': "✅ Тікер {ticker} встановлено! Автоматичний аналіз кожні {interval} годин.",
    'ticker_error': "❌ Тікер {ticker} не знайдено на Binance",
    'interval_changed': "✅ Інтервал змінено на {interval}",
    'alerts_enabled': "🔔 Автоматичні сповіщення увімкнено!",
    'alerts_disabled': "🔕 Автоматичні сповіщення вимкнено!",
    'analyzing': "🔄 Аналізую {ticker}...",
    'no_ticker': "❌ Спочатку встановіть тікер командою /setticker",
    'invalid_format': "❌ Неправильний формат. {example}",
    'unknown_command': "❌ Невідома команда. Використайте /help для перегляду доступних команд."
}

# Емодзі для різних станів
EMOJIS = {
    'buy': '✅',
    'sell': '❌',
    'hold': '⏸️',
    'up_trend': '📈',
    'down_trend': '📉',
    'sideways': '➡️',
    'btc': '₿',
    'news': '📰',
    'analysis': '📊',
    'price': '💰',
    'volume': '📦',
    'rsi': '🔄',
    'ma': '📏',
    'time': '⏰',
    'alert': '🔔',
    'warning': '⚠️',
    'error': '❌',
    'success': '✅',
    'loading': '🔄'
}

# Налаштування для різних ринкових умов
MARKET_CONDITIONS = {
    'bull': {
        'rsi_buy_threshold': 35,
        'rsi_sell_threshold': 75,
        'volume_multiplier': 1.2
    },
    'bear': {
        'rsi_buy_threshold': 25,
        'rsi_sell_threshold': 65,
        'volume_multiplier': 0.8
    },
    'neutral': {
        'rsi_buy_threshold': 30,
        'rsi_sell_threshold': 70,
        'volume_multiplier': 1.0
    }
}

# Популярні торгові пари
POPULAR_PAIRS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'SOL/USDT', 'DOT/USDT', 'DOGE/USDT',
    'AVAX/USDT', 'LUNA/USDT', 'LINK/USDT', 'UNI/USDT',
    'PEPE/USDT', 'SHIB/USDT', 'MATIC/USDT', 'ATOM/USDT'
]

# Налаштування для бекапу
BACKUP_CONFIG = {
    'enabled': True,
    'interval_hours': 24,
    'max_backups': 7,
    'backup_path': 'backups/'
}

def load_config_from_file(filepath: str = 'bot_config.json') -> BotConfig:
    """Завантажує конфігурацію з JSON файлу"""
    import json
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        return BotConfig(**config_data)
    except FileNotFoundError:
        print(f"⚠️ Файл конфігурації {filepath} не знайдено. Використовуємо значення за замовчуванням.")
        return BotConfig()
    except Exception as e:
        print(f"❌ Помилка завантаження конфігурації: {e}")
        return BotConfig()

def save_config_to_file(config: BotConfig, filepath: str = 'bot_config.json'):
    """Зберігає конфігурацію у JSON файл"""
    import json
    
    try:
        config_dict = {
            'TELEGRAM_BOT_TOKEN': config.TELEGRAM_BOT_TOKEN,
            'BINANCE_API_KEY': config.BINANCE_API_KEY,
            'BINANCE_SECRET': config.BINANCE_SECRET,
            'DEFAULT_TIMEFRAME': config.DEFAULT_TIMEFRAME,
            'DEFAULT_INTERVAL_HOURS': config.DEFAULT_INTERVAL_HOURS,
            'RSI_OVERSOLD': config.RSI_OVERSOLD,
            'RSI_OVERBOUGHT': config.RSI_OVERBOUGHT,
            'VOLUME_THRESHOLD': config.VOLUME_THRESHOLD,
            'LOG_LEVEL': config.LOG_LEVEL
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Конфігурацію збережено у {filepath}")
    except Exception as e:
        print(f"❌ Помилка збереження конфігурації: {e}")

# Приклад використання змінних оточення
def setup_environment():
    """Налаштовує змінні оточення для бота"""
    import os
    
    # Приклад .env файлу:
    env_example = """
# Telegram Bot Token (обов'язковий)
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# Binance API (опціонально)
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_SECRET=your_binance_secret_here

# Налаштування бота
DEFAULT_INTERVAL_HOURS=4
RSI_OVERSOLD=30
RSI_OVERBOUGHT=70
LOG_LEVEL=INFO
"""
    
    # Створюємо приклад .env файлу якщо його немає
    if not os.path.exists('.env'):
        with open('.env.example', 'w') as f:
            f.write(env_example)
        print("📄 Створено файл .env.example з прикладом конфігурації")

if __name__ == "__main__":
    # Тестування конфігурації
    print("🔧 Перевірка конфігурації...")
    
    if config.validate():
        print("✅ Конфігурація валідна")
        
        # Зберігаємо приклад конфігурації
        save_config_to_file(config, 'bot_config.example.json')
        
        # Налаштовуємо оточення
        setup_environment()
        
        print("\n📋 Поточна конфігурація:")
        print(f"  Таймфрейм: {config.DEFAULT_TIMEFRAME}")
        print(f"  Інтервал: {config.DEFAULT_INTERVAL_HOURS} годин")
        print(f"  RSI пороги: {config.RSI_OVERSOLD} - {config.RSI_OVERBOUGHT}")
        print(f"  Поріг об'єму: {config.VOLUME_THRESHOLD}%")
        print(f"  Джерел новин: {len(config.NEWS_SOURCES)}")
        
    else:
        print("❌ Конфігурація містить помилки")

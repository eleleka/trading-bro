import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import os
from dataclasses import dataclass
import re

import ccxt
import pandas as pd
import numpy as np
import ta
import feedparser
import requests
import matplotlib.pyplot as plt
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
load_dotenv()

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

@dataclass
class TechnicalAnalysis:
    price: float
    ma7: float
    ma25: float
    ma99: float
    rsi: float
    volume_change: float
    recommendation: str
    trend: str
    support_level: float = 0.0
    resistance_level: float = 0.0

@dataclass
class NewsAnalysis:
    sentiment: str
    impact: str
    summary: str
    score: int = 0

@dataclass
class TrendChange:
    ticker: str
    old_trend: str
    new_trend: str
    timestamp: datetime

class CryptoTradingBot:
    def __init__(self, bot_token: str, binance_api_key: str = None, binance_secret: str = None):
        self.bot_token = bot_token
        self.binance_api_key = binance_api_key
        self.binance_secret = binance_secret

        # Ініціалізуємо Binance API
        self.exchange = ccxt.binance({
            'apiKey': binance_api_key,
            'secret': binance_secret,
            'sandbox': False,
            'enableRateLimit': True,
        })

        # База даних для зберігання налаштувань користувачів
        self.init_database()

        # Планувальник задач
        self.scheduler = AsyncIOScheduler()

        # Налаштування за замовчуванням
        self.default_interval = 4  # годин
        self.user_settings = {}
        self.trend_cache = {}  # Кеш для відслідковування змін тренду

        # Telegram application
        self.application = None

    def generate_chart(self, df: pd.DataFrame, analysis: TechnicalAnalysis, ticker: str) -> BytesIO:
        """Генерує графік з ціною, MA, рівнями підтримки та опору"""
        import matplotlib.pyplot as plt
        from io import BytesIO

        plt.figure(figsize=(12, 6))

        plt.plot(df['timestamp'], df['close'], label='Ціна', color='black')
        plt.plot(df['timestamp'], df['ma7'], label='MA7', color='blue', linestyle='--')
        plt.plot(df['timestamp'], df['ma25'], label='MA25', color='orange', linestyle='--')
        plt.plot(df['timestamp'], df['ma99'], label='MA99', color='green', linestyle='--')

        # Рівні підтримки / опору
        if analysis.support_level > 0:
            plt.axhline(analysis.support_level, color='red', linestyle=':', label='Підтримка')
        if analysis.resistance_level > 0:
            plt.axhline(analysis.resistance_level, color='purple', linestyle=':', label='Опір')

        plt.title(f'{ticker} - Графік з аналізом')
        plt.xlabel('Час')
        plt.ylabel('Ціна (USDT)')
        plt.legend()
        plt.grid(True)
        plt.tight_layout()

        buffer = BytesIO()
        plt.savefig(buffer, format='png')
        buffer.seek(0)
        plt.close()
        return buffer

    def init_database(self):
        """Ініціалізує базу даних SQLite для зберігання налаштувань"""
        conn = sqlite3.connect('crypto_bot.db')
        cursor = conn.cursor()

        # Таблиця для налаштувань користувачів
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                ticker TEXT,
                interval_hours INTEGER DEFAULT 4,
                alerts_enabled BOOLEAN DEFAULT TRUE,
                chat_id INTEGER,
                watchlist TEXT DEFAULT '[]',
                buy_threshold INTEGER DEFAULT 3,
                sell_threshold INTEGER DEFAULT -3,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблиця для логування аналізу
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                ticker TEXT,
                price REAL,
                recommendation TEXT,
                analysis_data TEXT,
                btc_trend TEXT,
                news_sentiment TEXT
            )
        ''')

        # Таблиця для відслідковування змін тренду
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trend_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                ticker TEXT,
                old_trend TEXT,
                new_trend TEXT,
                notified BOOLEAN DEFAULT FALSE
            )
        ''')

        conn.commit()
        conn.close()

    def get_user_settings(self, user_id: int) -> dict:
        """Отримує налаштування користувача з бази даних"""
        conn = sqlite3.connect('crypto_bot.db')
        cursor = conn.cursor()

        cursor.execute('''
            SELECT ticker, interval_hours, alerts_enabled, chat_id, watchlist, buy_threshold, sell_threshold
            FROM user_settings WHERE user_id = ?
        ''', (user_id,))

        result = cursor.fetchone()
        conn.close()

        if result:
            watchlist = json.loads(result[4]) if result[4] else []
            return {
                'ticker': result[0],
                'interval_hours': result[1],
                'alerts_enabled': result[2],
                'chat_id': result[3],
                'watchlist': json.loads(result[4]) if result[4] else [],
                'buy_threshold': result[5] if result[5] is not None else 3,
                'sell_threshold': result[6] if result[6] is not None else -3
            }
        return {}

    def update_user_settings(self, user_id: int, **kwargs):
        """Оновлює налаштування користувача"""
        conn = sqlite3.connect('crypto_bot.db')
        cursor = conn.cursor()

        # Перевіряємо, чи існує користувач
        cursor.execute('SELECT user_id FROM user_settings WHERE user_id = ?', (user_id,))
        exists = cursor.fetchone()

        if exists:
            # Оновлюємо існуючі налаштування
            set_clauses = []
            values = []
            for key, value in kwargs.items():
                if key == 'watchlist':
                    value = json.dumps(value)
                set_clauses.append(f"{key} = ?")
                values.append(value)
            values.append(user_id)

            query = f"UPDATE user_settings SET {', '.join(set_clauses)} WHERE user_id = ?"
            cursor.execute(query, values)
        else:
            # Створюємо нові налаштування
            kwargs['user_id'] = user_id
            if 'watchlist' in kwargs:
                kwargs['watchlist'] = json.dumps(kwargs['watchlist'])
            columns = ', '.join(kwargs.keys())
            placeholders = ', '.join(['?' for _ in kwargs])
            values = list(kwargs.values())

            query = f"INSERT INTO user_settings ({columns}) VALUES ({placeholders})"
            cursor.execute(query, values)

        conn.commit()
        conn.close()

    def add_to_watchlist(self, user_id: int, ticker: str):
        """Додає тікер до списку відстеження"""
        settings = self.get_user_settings(user_id)
        watchlist = settings.get('watchlist', [])

        if ticker not in watchlist:
            watchlist.append(ticker)
            self.update_user_settings(user_id, watchlist=watchlist)
            return True
        return False

    def remove_from_watchlist(self, user_id: int, ticker: str):
        """Видаляє тікер зі списку відстеження"""
        settings = self.get_user_settings(user_id)
        watchlist = settings.get('watchlist', [])

        if ticker in watchlist:
            watchlist.remove(ticker)
            self.update_user_settings(user_id, watchlist=watchlist)
            return True
        return False

    def log_analysis(self, user_id: int, ticker: str, analysis: TechnicalAnalysis,
                     btc_trend: str = "", news_sentiment: str = ""):
        """Логує результати аналізу"""
        conn = sqlite3.connect('crypto_bot.db')
        cursor = conn.cursor()

        analysis_data = {
            'price': analysis.price,
            'ma7': analysis.ma7,
            'ma25': analysis.ma25,
            'ma99': analysis.ma99,
            'rsi': analysis.rsi,
            'volume_change': analysis.volume_change,
            'trend': analysis.trend,
            'support_level': analysis.support_level,
            'resistance_level': analysis.resistance_level
        }

        cursor.execute('''
            INSERT INTO analysis_log (user_id, ticker, price, recommendation, analysis_data, btc_trend, news_sentiment)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, ticker, analysis.price, analysis.recommendation,
              json.dumps(analysis_data), btc_trend, news_sentiment))

        conn.commit()
        conn.close()

    def log_trend_change(self, user_id: int, ticker: str, old_trend: str, new_trend: str):
        """Логує зміну тренду"""
        conn = sqlite3.connect('crypto_bot.db')
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO trend_changes (user_id, ticker, old_trend, new_trend)
            VALUES (?, ?, ?, ?)
        ''', (user_id, ticker, old_trend, new_trend))

        conn.commit()
        conn.close()

    async def get_crypto_data(self, symbol: str, timeframe: str = '4h', limit: int = 100) -> pd.DataFrame:
        """Отримує дані про криптовалюту з Binance"""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            logger.info(f"✅ Отримано {len(df)} рядків даних для {symbol}")
            return df
        except Exception as e:
            logger.error(f"❌ Помилка отримання даних для {symbol}: {e}")
            return pd.DataFrame()

    def calculate_support_resistance(self, df: pd.DataFrame) -> Tuple[float, float]:
        """Розраховує рівні підтримки та опору"""
        if df.empty or len(df) < 20:
            return 0.0, 0.0

        # Беремо останні 20 свічок
        recent_data = df.tail(20)

        # Рівень підтримки - мінімум з останніх низьких цін
        support = recent_data['low'].min()

        # Рівень опору - максимум з останніх високих цін
        resistance = recent_data['high'].max()

        return support, resistance

    def calculate_technical_indicators(self, df: pd.DataFrame, buy_threshold=3, sell_threshold=-3) -> TechnicalAnalysis:
        """Розраховує технічні індикатори"""
        if df.empty or len(df) < 99:
            return None

        # Розрахунок ковзних середніх
        df['ma7'] = ta.trend.sma_indicator(df['close'], window=7)
        df['ma25'] = ta.trend.sma_indicator(df['close'], window=25)
        df['ma99'] = ta.trend.sma_indicator(df['close'], window=99)

        # RSI
        df['rsi'] = ta.momentum.rsi(df['close'], window=14)

        # Зміна об'єму
        df['volume_change'] = df['volume'].pct_change() * 100

        # Отримуємо останні значення
        latest = df.iloc[-1]

        # Розраховуємо рівні підтримки та опору
        support, resistance = self.calculate_support_resistance(df)

        # Визначаємо тренд на основі MA
        if latest['ma7'] > latest['ma25'] > latest['ma99']:
            trend = "📈 UP"
        elif latest['ma7'] < latest['ma25'] < latest['ma99']:
            trend = "📉 DOWN"
        else:
            trend = "➡️ SIDEWAYS"

        # Генеруємо рекомендацію
        recommendation = self.generate_recommendation(
            latest['rsi'], trend, latest['volume_change'],
            latest['close'], support, resistance,
            buy_threshold, sell_threshold
        )

        return TechnicalAnalysis(
            price=latest['close'],
            ma7=latest['ma7'],
            ma25=latest['ma25'],
            ma99=latest['ma99'],
            rsi=latest['rsi'],
            volume_change=latest['volume_change'],
            recommendation=recommendation,
            trend=trend,
            support_level=support,
            resistance_level=resistance
        )

    def generate_recommendation(self, rsi, trend, volume_change, price, support, resistance,
                            buy_threshold=3, sell_threshold=-3) -> str:
        """Генерує рекомендацію на основі технічних індикаторів"""
        score = 0

        # RSI аналіз
        if rsi < 30:
            score += 2  # Перепроданість - сигнал до покупки
        elif rsi > 70:
            score -= 2  # Перекупленість - сигнал до продажу
        elif 30 <= rsi <= 50:
            score += 1  # Нейтральна зона з нахилом до покупки

        # Тренд аналіз
        if "UP" in trend:
            score += 2
        elif "DOWN" in trend:
            score -= 2

        # Об'єм
        if volume_change > 10:
            score += 1
        elif volume_change < -10:
            score -= 1

        # Аналіз відносно рівнів підтримки/опору
        if support > 0 and resistance > 0:
            price_position = (price - support) / (resistance - support)
            if price_position < 0.2:  # Близько до підтримки
                score += 1
            elif price_position > 0.8:  # Близько до опору
                score -= 1

        # Генеруємо рекомендацію
        if score >= buy_threshold:
            return "✅ BUY"
        elif score <= sell_threshold:
            return "❌ SELL"
        else:
            return "⏸️ HOLD"

    async def analyze_btc_trend(self) -> str:
        """Аналізує тренд BTC як додатковий фільтр"""
        try:
            btc_df = await self.get_crypto_data('BTCUSDT', '4h', 100)
            if btc_df.empty:
                logger.warning("⚠️ BTCUSDT: отримано порожній DataFrame")
                return "❓ BTC UNKNOWN"

            btc_analysis = self.calculate_technical_indicators(btc_df)
            if not btc_analysis:
                logger.warning("⚠️ BTCUSDT: не вдалося обчислити технічні індикатори")
                return "❓ BTC UNKNOWN"

            logger.info(f"BTC тренд визначено: {btc_analysis.trend}")
            return btc_analysis.trend
        except Exception as e:
            logger.error(f"❌ Помилка аналізу BTC: {e}")
            return "❓ BTC UNKNOWN"

    async def get_crypto_news(self) -> NewsAnalysis:
        """Отримує та аналізує новини про криптовалюти"""
        try:
            # RSS джерела новин
            feeds = [
                'https://www.coindesk.com/arc/outboundfeeds/rss/',
                'https://cointelegraph.com/rss',
                'https://decrypt.co/feed'
            ]

            all_news = []
            for feed_url in feeds:
                try:
                    feed = feedparser.parse(feed_url)
                    for entry in feed.entries[:3]:  # Беремо 3 останні новини з кожного джерела
                        all_news.append({
                            'title': entry.title,
                            'summary': entry.summary if hasattr(entry, 'summary') else '',
                            'published': entry.published if hasattr(entry, 'published') else '',
                            'link': entry.link if hasattr(entry, 'link') else ''
                        })
                except Exception as e:
                    logger.error(f"Помилка отримання новин з {feed_url}: {e}")

            # Простий аналіз сентиментів
            negative_keywords = ['падіння', 'зниження', 'криза', 'заборона', 'регулювання', 'хакерська атака', 'SEC']
            positive_keywords = ['зростання', 'підвищення', 'інвестиції', 'прийняття', 'партнерство', 'інновації']

            sentiment_score = 0
            impact_news = []

            for news in all_news:
                title_text = news['title'].lower()
                summary_text = news['summary'].lower()
                combined_text = f"{title_text} {summary_text}"

                for keyword in negative_keywords:
                    if keyword in combined_text:
                        sentiment_score -= 1
                        impact_news.append(f"⚠️ {news['title'][:50]}...")

                for keyword in positive_keywords:
                    if keyword in combined_text:
                        sentiment_score += 1
                        impact_news.append(f"✅ {news['title'][:50]}...")

            # Визначаємо загальний сентимент
            if sentiment_score > 2:
                sentiment = "📈 POSITIVE"
                impact = "Позитивні новини можуть сприяти зростанню ринку"
            elif sentiment_score < -2:
                sentiment = "📉 NEGATIVE"
                impact = "Негативні новини можуть призвести до волатильності"
            else:
                sentiment = "➡️ NEUTRAL"
                impact = "Нейтральний фон новин"

            summary = "; ".join(impact_news[:3]) if impact_news else "Немає значних новин"

            return NewsAnalysis(
                sentiment=sentiment,
                impact=impact,
                summary=summary,
                score=sentiment_score
            )

        except Exception as e:
            logger.error(f"Помилка аналізу новин: {e}")
            return NewsAnalysis(
                sentiment="❓ UNKNOWN",
                impact="Не вдалося отримати новини",
                summary="Помилка завантаження новин",
                score=0
            )

    def format_analysis_message(self, ticker: str, analysis: TechnicalAnalysis,
                              btc_trend: str = "", news: NewsAnalysis = None) -> str:
        """Форматує повідомлення з результатами аналізу"""

        # Визначаємо тренд MA
        if analysis.ma7 > analysis.ma25 > analysis.ma99:
            ma_trend = "MA7 > MA25 > MA99 → ап-тренд"
        elif analysis.ma7 < analysis.ma25 < analysis.ma99:
            ma_trend = "MA7 < MA25 < MA99 → даун-тренд"
        else:
            ma_trend = "MA змішані → боковий тренд"

        # Інтерпретація RSI
        if analysis.rsi < 30:
            rsi_interpretation = "перепроданість"
        elif analysis.rsi > 70:
            rsi_interpretation = "перекупленість"
        else:
            rsi_interpretation = "нейтральна зона"

        # Аналіз об'єму
        if analysis.volume_change > 10:
            volume_text = f"зростає на {analysis.volume_change:.1f}%"
        elif analysis.volume_change < -10:
            volume_text = f"знижується на {abs(analysis.volume_change):.1f}%"
        else:
            volume_text = f"стабільний ({analysis.volume_change:.1f}%)"

        message = f"""📊 {ticker} (4H)
💰 Ціна: {analysis.price:.8f}
Рекомендація: {analysis.recommendation}

🔍 Аналіз:
• {ma_trend}
• RSI: {analysis.rsi:.1f} ({rsi_interpretation})
• ₿ BTC тренд: {btc_trend}
• 📦 Об'єм: {volume_text}

📏 Технічні рівні:
• 🔻 Підтримка: {analysis.support_level:.8f}
• 🔺 Опір: {analysis.resistance_level:.8f}"""

        # Додаємо новини якщо є
        if news and news.sentiment != "❓ UNKNOWN":
            message += f"\n\n📰 Новини: {news.sentiment}\n{news.impact}"
            if news.summary and news.summary != "Немає значних новин":
                message += f"\n{news.summary}"

        return message

    def check_trend_changes(self, user_id: int, ticker: str, current_trend: str) -> bool:
        """Перевіряє зміни тренду"""
        cache_key = f"{user_id}_{ticker}"
        old_trend = self.trend_cache.get(cache_key)

        if old_trend and old_trend != current_trend:
            self.log_trend_change(user_id, ticker, old_trend, current_trend)
            self.trend_cache[cache_key] = current_trend
            return True

        self.trend_cache[cache_key] = current_trend
        return False

    def parse_interval(self, interval_str: str) -> int:
        """Парсить інтервал в годинах"""
        interval_map = {
            '1h': 1, '2h': 2, '4h': 4, '8h': 8, '12h': 12, '24h': 24,
            '1': 1, '2': 2, '4': 4, '8': 8, '12': 12, '24': 24
        }
        return interval_map.get(interval_str.lower(), 4)

    def validate_ticker(self, ticker: str) -> bool:
        """Перевіряє валідність тікера"""
        try:
            # Перевіряємо формат тікера
            if not re.match(r'^[A-Z0-9]{3,10}USDT$', ticker.upper()):
                return False

            # Перевіряємо наявність на біржі
            ticker_info = self.exchange.fetch_ticker(ticker.upper())
            return ticker_info is not None
        except:
            return False

    # Telegram команди
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє команду /start"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        self.update_user_settings(user_id, chat_id=chat_id)

        message = """🤖 Вітаю! Я ваш помічник для торгівлі криптовалютами.

📋 Доступні команди:
/setticker PEPEUSDT - встановити тікер для моніторингу
/analyze PEPEUSDT - миттєвий аналіз
/setinterval 2h - змінити інтервал перевірки
/watchlist - список відстежуваних тікерів
/enablealerts - увімкнути автоматичні сповіщення
/disablealerts - вимкнути автоматичні сповіщення
/help - довідка

🚀 Почніть з команди /setticker для встановлення тікера!"""

        await update.message.reply_text(message)

    # Команди для налаштування
    async def setscore_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if len(context.args) != 2:
        await update.message.reply_text("❌ Use: /setscore <BUY_THRESHOLD> <SELL_THRESHOLD>\nExample: /setscore 2 -2")
        return

    try:
        buy_threshold = int(context.args[0])
        sell_threshold = int(context.args[1])

        self.update_user_settings(user_id, buy_threshold=buy_threshold, sell_threshold=sell_threshold)
        await update.message.reply_text(f"✅ Updated thresholds:\nBUY if score ≥ {buy_threshold}\nSELL if score ≤ {sell_threshold}")

    except ValueError:
        await update.message.reply_text("❌ Invalid values. Use integers like: /setscore 2 -2")

    # Коаманда /help
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє команду /help"""
        message = """📚 Довідка по командах:

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
• ⏸️ HOLD - утримувати позицію"""

        await update.message.reply_text(message)

    async def setticker_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє команду /setticker"""
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("❌ Неправильний формат. Приклад: /setticker PEPEUSDT")
            return

        ticker = context.args[0].upper()

        if not self.validate_ticker(ticker):
            await update.message.reply_text(f"❌ Тікер {ticker} не знайдено на Binance")
            return

        settings = self.get_user_settings(user_id)
        interval = settings.get('interval_hours', 4)

        self.update_user_settings(user_id, ticker=ticker)

        # Додаємо до watchlist
        self.add_to_watchlist(user_id, ticker)

        # Запускаємо автоматичний аналіз
        await self.schedule_analysis(user_id, ticker, interval)

        await update.message.reply_text(f"✅ Тікер {ticker} встановлено! Автоматичний аналіз кожні {interval} годин.")

    async def analyze_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє команду /analyze"""
        user_id = update.effective_user.id
        settings = self.get_user_settings(user_id)
        buy_th = settings.get('buy_threshold', 3)
        sell_th = settings.get('sell_threshold', -3)

        if not context.args:
            settings = self.get_user_settings(user_id)
            ticker = settings.get('ticker')
            if not ticker:
                await update.message.reply_text("❌ Спочатку встановіть тікер командою /setticker")
                return
        else:
            ticker = context.args[0].upper()
            if not self.validate_ticker(ticker):
                await update.message.reply_text(f"❌ Тікер {ticker} не знайдено на Binance")
                return

        await update.message.reply_text(f"🔄 Аналізую {ticker}...")

        try:
            # Отримуємо дані
            df = await self.get_crypto_data(ticker, '4h', 100)
            if df.empty:
                await update.message.reply_text("❌ Не вдалося отримати дані")
                return

            # Технічний аналіз
            analysis = self.calculate_technical_indicators(df, buy_th, sell_th)
            if not analysis:
                await update.message.reply_text("❌ Недостатньо даних для аналізу")
                return

            # Аналіз BTC
            btc_trend = await self.analyze_btc_trend()

            # Новини
            news = await self.get_crypto_news()

            # Логування
            self.log_analysis(user_id, ticker, analysis, btc_trend, news.sentiment)

            # Перевірка змін тренду
            trend_changed = self.check_trend_changes(user_id, ticker, analysis.trend)

            # Формування повідомлення
            message = self.format_analysis_message(ticker, analysis, btc_trend, news)

            if trend_changed:
                message += "\n\n⚠️ Зміна тренду виявлена!"

            # Генеруємо графік
            chart = self.generate_chart(df, analysis, ticker)
            await update.message.reply_photo(photo=chart)

            # Відправляємо повідомлення з аналізом
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Помилка аналізу: {e}")
            await update.message.reply_text("❌ Помилка під час аналізу")

    async def setinterval_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє команду /setinterval"""
        user_id = update.effective_user.id

        if not context.args:
            await update.message.reply_text("❌ Неправильний формат. Приклад: /setinterval 2h")
            return

        interval_str = context.args[0]
        interval_hours = self.parse_interval(interval_str)

        if interval_hours not in [1, 2, 4, 8, 12, 24]:
            await update.message.reply_text("❌ Невірний інтервал. Доступні: 1h, 2h, 4h, 8h, 12h, 24h")
            return

        self.update_user_settings(user_id, interval_hours=interval_hours)

        # Перезапускаємо планувальник
        settings = self.get_user_settings(user_id)
        ticker = settings.get('ticker')
        if ticker:
            await self.schedule_analysis(user_id, ticker, interval_hours)

        await update.message.reply_text(f"✅ Інтервал змінено на {interval_str}")

    # Патч для crypto_trading_bot.py - додаємо відсутні методи та функціональність

    async def watchlist_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє команду /watchlist"""
        user_id = update.effective_user.id
        settings = self.get_user_settings(user_id)

        current_ticker = settings.get('ticker', 'Не встановлено')
        interval = settings.get('interval_hours', 4)
        alerts_enabled = settings.get('alerts_enabled', True)
        watchlist = settings.get('watchlist', [])

        status = "✅ Увімкнено" if alerts_enabled else "❌ Вимкнено"

        message = f"""📋 Поточні налаштування:
🎯 Основний тікер: {current_ticker}
⏰ Інтервал: {interval}h
🔔 Сповіщення: {status}

📋 Список відстеження:"""

        if watchlist:
            for ticker in watchlist:
                message += f"\n• {ticker}"
        else:
            message += "\n• Список порожній"

        # Додаємо inline клавіатуру для управління
        keyboard = [
            [InlineKeyboardButton("➕ Додати тікер", callback_data="add_ticker")],
            [InlineKeyboardButton("➖ Видалити тікер", callback_data="remove_ticker")],
            [InlineKeyboardButton("🔄 Оновити", callback_data="refresh_watchlist")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(message, reply_markup=reply_markup)

    async def enablealerts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє команду /enablealerts"""
        user_id = update.effective_user.id
        self.update_user_settings(user_id, alerts_enabled=True)

        # Перезапускаємо планувальник
        settings = self.get_user_settings(user_id)
        ticker = settings.get('ticker')
        interval = settings.get('interval_hours', 4)

        if ticker:
            await self.schedule_analysis(user_id, ticker, interval)

        await update.message.reply_text("✅ Автоматичні сповіщення увімкнено!")

    async def disablealerts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє команду /disablealerts"""
        user_id = update.effective_user.id
        self.update_user_settings(user_id, alerts_enabled=False)

        # Зупиняємо планувальник для цього користувача
        job_id = f"analysis_{user_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        await update.message.reply_text("❌ Автоматичні сповіщення вимкнено!")

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє callback запити від inline клавіатури"""
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id

        if query.data == "add_ticker":
            await query.edit_message_text(
                "📝 Надішліть тікер для додавання до списку відстеження\n"
                "Приклад: BTCUSDT"
            )
        elif query.data == "remove_ticker":
            settings = self.get_user_settings(user_id)
            watchlist = settings.get('watchlist', [])

            if not watchlist:
                await query.edit_message_text("📋 Список відстеження порожній")
                return

            keyboard = []
            for ticker in watchlist:
                keyboard.append([InlineKeyboardButton(f"❌ {ticker}", callback_data=f"remove_{ticker}")])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="refresh_watchlist")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Виберіть тікер для видалення:", reply_markup=reply_markup)

        elif query.data.startswith("remove_"):
            ticker = query.data.replace("remove_", "")
            if self.remove_from_watchlist(user_id, ticker):
                await query.edit_message_text(f"✅ Тікер {ticker} видалено зі списку відстеження")
            else:
                await query.edit_message_text(f"❌ Помилка видалення тікера {ticker}")

        elif query.data == "refresh_watchlist":
            # Відновлюємо список відстеження
            await self.watchlist_command(update, context)

    async def handle_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробляє введення тікера користувачем"""
        user_id = update.effective_user.id
        text = update.message.text.upper()

        # Перевіряємо чи це тікер
        if self.validate_ticker(text):
            if self.add_to_watchlist(user_id, text):
                await update.message.reply_text(f"✅ Тікер {text} додано до списку відстеження")
            else:
                await update.message.reply_text(f"ℹ️ Тікер {text} вже є у списку відстеження")
        else:
            await update.message.reply_text(f"❌ Невірний тікер: {text}")

    async def schedule_analysis(self, user_id: int, ticker: str, interval_hours: int):
        """Планує автоматичний аналіз"""
        job_id = f"analysis_{user_id}"
        settings = self.get_user_settings(user_id)
        buy_th = settings.get('buy_threshold', 3)
        sell_th = settings.get('sell_threshold', -3)

        # Видаляємо існуючий job якщо є
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # Додаємо новий job
        self.scheduler.add_job(
            self.scheduled_analysis,
            IntervalTrigger(hours=interval_hours),
            id=job_id,
            args=[user_id, ticker],
            replace_existing=True
        )

        logger.info(f"Запланований аналіз для користувача {user_id}, тікер {ticker}, інтервал {interval_hours}h")

    async def scheduled_analysis(self, user_id: int, ticker: str):
        """Виконує запланований аналіз"""
        try:
            settings = self.get_user_settings(user_id)
            if not settings.get('alerts_enabled', True):
                return

            chat_id = settings.get('chat_id')
            if not chat_id:
                return

            # Отримуємо дані
            df = await self.get_crypto_data(ticker, '4h', 100)
            if df.empty:
                return

            # Технічний аналіз
            analysis = self.calculate_technical_indicators(df, buy_th, sell_th)
            if not analysis:
                return

            # Аналіз BTC
            btc_trend = await self.analyze_btc_trend()

            # Новини
            news = await self.get_crypto_news()

            # Логування
            self.log_analysis(user_id, ticker, analysis, btc_trend, news.sentiment)

            # Перевірка змін тренду
            trend_changed = self.check_trend_changes(user_id, ticker, analysis.trend)

            # Формування повідомлення
            message = f"🔄 Автоматичний аналіз\n\n"
            message += self.format_analysis_message(ticker, analysis, btc_trend, news)

            if trend_changed:
                message += "\n\n⚠️ Зміна тренду виявлена!"

            # Додаємо попередження про RSI
            if analysis.rsi > 70:
                message += f"\n\n⚠️ RSI {analysis.rsi:.1f} - перекупленість!"
            elif analysis.rsi < 30:
                message += f"\n\n⚠️ RSI {analysis.rsi:.1f} - перепроданість!"

            # Генеруємо та відправляємо графік
            chart = self.generate_chart(df, analysis, ticker)
            await self.application.bot.send_photo(chat_id=chat_id, photo=chart)

            # Відправляємо повідомлення
            await self.application.bot.send_message(chat_id=chat_id, text=message)

        except Exception as e:
            logger.error(f"Помилка запланованого аналізу: {e}")

    def run_bot(self):
        """Запускає бота"""
        try:
            # Створюємо Application
            self.application = Application.builder().token(self.bot_token).build()

            # Реєструємо обробники команд
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("setticker", self.setticker_command))
            self.application.add_handler(CommandHandler("analyze", self.analyze_command))
            self.application.add_handler(CommandHandler("setinterval", self.setinterval_command))
            self.application.add_handler(CommandHandler("watchlist", self.watchlist_command))
            self.application.add_handler(CommandHandler("enablealerts", self.enablealerts_command))
            self.application.add_handler(CommandHandler("disablealerts", self.disablealerts_command))
            self.application.add_handler(CommandHandler("setscore", self.setscore_command))

            # Реєструємо обробники callback запитів
            self.application.add_handler(CallbackQueryHandler(self.callback_query_handler))

            # Реєструємо обробник текстових повідомлень
            self.application.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.handle_ticker_input
            ))

            # Запускаємо планувальник
            self.scheduler.start()

            logger.info("Бот запущено!")

            # Запускаємо бота
            self.application.run_polling(allowed_updates=Update.ALL_TYPES)

        except Exception as e:
            logger.error(f"Помилка запуску бота: {e}")
        finally:
            self.scheduler.shutdown()


# Основна функція для запуску бота
def main():
    """Основна функція"""
    # Отримуємо токени з змінних середовища
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    binance_api_key = os.getenv("BINANCE_API_KEY")  # Опційно
    binance_secret = os.getenv("BINANCE_SECRET")    # Опційно

    if not bot_token:
        logger.error("Не встановлено TELEGRAM_BOT_TOKEN")
        logger.info("Встановіть змінну середовища: export TELEGRAM_BOT_TOKEN='your_token'")
        logger.info("Або змініть токен прямо в коді нижче")
        return

    # Створюємо та запускаємо бота
    bot = CryptoTradingBot(bot_token, binance_api_key, binance_secret)
    bot.run_bot()


if __name__ == "__main__":
    # ВАРІАНТ 1: Встановіть токен тут (замініть YOUR_BOT_TOKEN_HERE на реальний токен)
    os.environ["TELEGRAM_BOT_TOKEN"] = os.getenv("TELEGRAM_BOT_TOKEN")

    # ВАРІАНТ 2: Або встановіть змінну середовища в терміналі:
    # export TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN_HERE"

    # ВАРІАНТ 3: Або встановіть токен безпосередньо
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        # Замініть цей рядок на ваш реальний токен
        os.environ["TELEGRAM_BOT_TOKEN"] = os.getenv("TELEGRAM_BOT_TOKEN")

    # Запускаємо бота
    main()

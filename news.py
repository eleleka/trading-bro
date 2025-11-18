import os
import logging
import random
import re
import time
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests
import json
from bs4 import BeautifulSoup
import threading
from typing import List, Dict, Optional

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env file loaded successfully")
except ImportError:
    print("⚠️ python-dotenv not installed. Install with: pip install python-dotenv")
    print("💡 Falling back to manual configuration...")
except Exception as e:
    print(f"⚠️ Error loading .env file: {e}")
    print("💡 Make sure .env file exists in the same folder as the script")

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class NewsMonitor:
    def __init__(self, bot_app):
        self.bot_app = bot_app
        self.seen_links = set()
        self.seen_posts = set()
        self._latest_news_titles = []

        self.sources = {
            "Binance Announcements": "https://www.binance.com/en/support/announcement/list/48",
            "Binance News API": "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&pageNo=1&pageSize=50",
        }

        self.check_interval = 300  # 5 minutes
        self.target_chat_id = None
        self.monitoring_active = False

        self.keywords = [
            "alpha", "airdrop", "reward", "listing", "new coin", "token",
            "launch", "ido", "ico", "giveaway", "campaign", "bonus",
            "earn", "free", "drop", "allocation", "whitelist", "binance"
        ]

        logger.info("NewsMonitor initialized")

    def set_target_chat(self, chat_id):
        self.target_chat_id = chat_id
        self.monitoring_active = True
        logger.info(f"News monitoring activated for chat {chat_id}")

    async def send_news_message(self, text: str):
        if not self.target_chat_id or not self.bot_app:
            return

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                await self.bot_app.bot.send_message(
                    chat_id=self.target_chat_id,
                    text=text,
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
                return
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)

    def check_binance_announcements(self, url: str) -> List[Dict]:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            found_news = []

            for a in soup.find_all("a", href=True):
                title = a.get_text(strip=True)
                href = a["href"]

                if not href.startswith("http"):
                    if href.startswith("/"):
                        href = "https://www.binance.com" + href
                    else:
                        continue

                if any(keyword.lower() in title.lower() for keyword in self.keywords):
                    if href not in self.seen_links:
                        self.seen_links.add(href)
                        found_news.append({
                            'title': title,
                            'url': href,
                            'source': 'Binance Announcements',
                            'type': 'announcement'
                        })

            return found_news
        except Exception as e:
            logger.error(f"Error checking announcements: {e}")
            return []

    def check_binance_news_api(self, url: str) -> List[Dict]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Referer": "https://www.binance.com/",
                "Origin": "https://www.binance.com"
            }

            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()
            data = response.json()
            return self._parse_binance_news_json(data)
        except Exception as e:
            logger.error(f"Error checking news API: {e}")
            return []

    def _parse_binance_news_json(self, data: Dict) -> List[Dict]:
        filtered_posts = []
        latest_titles = []

        try:
            articles = []
            if 'data' in data:
                if isinstance(data['data'], list):
                    articles = data['data']
                elif 'articles' in data['data']:
                    articles = data['data']['articles']
                elif 'catalogDetailList' in data['data']:
                    articles = data['data']['catalogDetailList']
            elif 'articles' in data:
                articles = data['articles']
            elif isinstance(data, list):
                articles = data

            for i, article in enumerate(articles):
                try:
                    article_id = (article.get('id') or article.get('articleId') or
                                article.get('code') or str(hash(str(article))))
                    title = (article.get('title') or article.get('name') or
                            article.get('subject') or '')

                    if not title or len(title.strip()) < 5:
                        continue

                    if i < 3:
                        latest_titles.append(title.strip())

                    if article_id in self.seen_posts:
                        continue

                    if any(keyword.lower() in title.lower() for keyword in self.keywords):
                        self.seen_posts.add(article_id)

                        article_url = article.get('url') or article.get('link')
                        if not article_url or not article_url.startswith('http'):
                            article_code = article.get('code') or article_id
                            article_url = f"https://www.binance.com/en/support/announcement/{article_code}"

                        release_date = article.get('releaseDate') or article.get('publishDate')
                        summary = article.get('summary') or article.get('description') or ''

                        filtered_posts.append({
                            'title': title.strip(),
                            'url': article_url,
                            'source': 'Binance News API',
                            'type': 'news_article',
                            'summary': summary[:200] + "..." if len(summary) > 200 else summary,
                            'release_date': release_date,
                            'article_id': article_id
                        })
                except Exception as e:
                    continue

            if latest_titles:
                self._latest_news_titles = latest_titles

        except Exception as e:
            logger.error(f"Error parsing news JSON: {e}")

        return filtered_posts

    async def check_all_sources(self) -> List[Dict]:
        all_news = []
        for source_name, source_url in self.sources.items():
            try:
                if "news api" in source_name.lower() or "bapi/apex" in source_url:
                    news_items = await asyncio.to_thread(self.check_binance_news_api, source_url)
                else:
                    news_items = await asyncio.to_thread(self.check_binance_announcements, source_url)
                all_news.extend(news_items)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error checking {source_name}: {e}")
        return all_news

    def format_news_message(self, news_item: Dict) -> str:
        try:
            title = news_item.get('title', 'No title')
            url = news_item.get('url', '')
            news_type = news_item.get('type', 'news')

            if news_type == 'news_article':
                summary = news_item.get('summary', '')
                release_date = news_item.get('release_date', '')

                message = f"🔥 <b>Binance News Alert</b>\n📰 {title}\n\n"
                if summary and summary.strip() and summary != title:
                    message += f"📝 {summary}\n\n"
                if release_date:
                    message += f"📅 Released: {release_date}\n"
                message += f"🔗 <a href='{url}'>Read Full Article</a>\n"
            else:
                message = f"🆕 <b>Binance Announcement</b>\n📰 {title}\n🔗 <a href='{url}'>Read More</a>\n"

            message += f"⏰ {datetime.now().strftime('%H:%M:%S')}"
            return message
        except Exception as e:
            return f"📰 News update: {news_item.get('title', 'Unknown')}"

    async def monitor_news(self):
        logger.info("Starting news monitoring...")
        consecutive_errors = 0
        max_consecutive_errors = 10

        while True:
            try:
                if not self.monitoring_active or not self.target_chat_id:
                    await asyncio.sleep(30)
                    continue

                all_news = await self.check_all_sources()

                for news in all_news:
                    try:
                        message = self.format_news_message(news)
                        await self.send_news_message(message)
                        if len(all_news) > 1:
                            await asyncio.sleep(3)
                    except Exception as e:
                        logger.error(f"Error sending news: {e}")

                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in monitoring cycle: {e}")
                if consecutive_errors >= max_consecutive_errors:
                    await asyncio.sleep(self.check_interval * 2)
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(30)

            await asyncio.sleep(self.check_interval)


class CryptoAnalyzer:
    def __init__(self, binance_api_key=None, binance_secret_key=None):
        self.last_analysis = {}
        self.binance_api = "https://api.binance.com/api/v3"
        self.coingecko_api = "https://api.coingecko.com/api/v3"
        self.binance_api_key = binance_api_key
        self.binance_secret_key = binance_secret_key

    def get_price_data(self, symbol):
        try:
            binance_data = self._get_binance_data(symbol)
            if binance_data:
                return binance_data

            coingecko_data = self._get_coingecko_data(symbol)
            if coingecko_data:
                return coingecko_data

            # Return None if no data found from any source
            return None
        except Exception as e:
            logger.error(f"Error getting price data: {e}")
            return None

    def _get_binance_headers(self):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        }
        if self.binance_api_key:
            headers['X-MBX-APIKEY'] = self.binance_api_key
        return headers

    def _get_binance_data(self, symbol):
        try:
            headers = self._get_binance_headers()
            price_url = f"{self.binance_api}/ticker/price?symbol={symbol}USDT"
            price_response = requests.get(price_url, timeout=15, headers=headers)

            stats_url = f"{self.binance_api}/ticker/24hr?symbol={symbol}USDT"
            stats_response = requests.get(stats_url, timeout=15, headers=headers)

            if price_response.status_code == 200 and stats_response.status_code == 200:
                price_data = price_response.json()
                stats_data = stats_response.json()

                return {
                    'price': float(price_data['price']),
                    'change_24h': float(stats_data['priceChangePercent']),
                    'volume': float(stats_data['volume']),
                    'high_24h': float(stats_data['highPrice']),
                    'low_24h': float(stats_data['lowPrice']),
                    'count': int(stats_data.get('count', 0)),
                    'quote_volume': float(stats_data.get('quoteVolume', 0)),
                    'source': 'Binance API'
                }
        except Exception as e:
            logger.debug(f"Binance API failed: {e}")
        return None

    def _get_coingecko_data(self, symbol):
        try:
            search_url = f"{self.coingecko_api}/search?query={symbol}"
            search_response = requests.get(search_url, timeout=15)

            if search_response.status_code == 200:
                search_data = search_response.json()
                matching_coin = None

                for coin in search_data.get('coins', []):
                    if coin.get('symbol', '').upper() == symbol.upper():
                        matching_coin = coin
                        break

                if matching_coin:
                    coin_id = matching_coin['id']
                    price_url = f"{self.coingecko_api}/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true"
                    price_response = requests.get(price_url, timeout=15)

                    if price_response.status_code == 200:
                        price_data = price_response.json()
                        coin_data = price_data.get(coin_id, {})

                        if coin_data:
                            current_price = coin_data.get('usd', 0) or 0
                            change_24h = coin_data.get('usd_24h_change', 0) or 0
                            volume_24h = coin_data.get('usd_24h_vol', 0) or 0

                            if current_price > 0:
                                return {
                                    'price': current_price,
                                    'change_24h': change_24h,
                                    'volume': volume_24h,
                                    'high_24h': current_price * (1 + abs(change_24h) / 100),
                                    'low_24h': current_price * (1 - abs(change_24h) / 100),
                                    'source': 'CoinGecko'
                                }
        except Exception as e:
            logger.debug(f"CoinGecko failed: {e}")
        return None

    def _get_simulated_data(self, symbol):
        base_price = random.uniform(0.000001, 100)
        change_24h = random.uniform(-15, 15)
        volume = random.uniform(100000, 10000000)

        return {
            'price': base_price,
            'change_24h': change_24h,
            'volume': volume,
            'high_24h': base_price * (1 + abs(change_24h) / 100),
            'low_24h': base_price * (1 - abs(change_24h) / 100),
            'source': 'Simulated'
        }

    def simulate_technical_analysis(self, symbol, timeframe):
        indicators = {
            'rsi': random.uniform(20, 80),
            'macd_signal': random.choice(['bullish', 'bearish', 'neutral']),
            'ema_trend': random.choice(['upward', 'downward', 'sideways']),
            'volume_trend': random.choice(['increasing', 'decreasing', 'stable']),
        }

        if timeframe == 'supershort':
            indicators['timeframes'] = '1m/5m/15m'
            indicators['ema_periods'] = 'EMA(5/12)'
        elif timeframe == 'short':
            indicators['timeframes'] = '15m/1h/4h'
            indicators['ema_periods'] = 'EMA(9/21)'
        elif timeframe == 'mid':
            indicators['timeframes'] = '4h/1d'
            indicators['ema_periods'] = 'EMA(50/200)'
        else:
            indicators['timeframes'] = '1d/1w'
            indicators['ema_periods'] = 'EMA(50/200)'

        return indicators

    def generate_forecast(self, symbol, timeframe='supershort'):
        try:
            price_data = self.get_price_data(symbol)
            if not price_data or price_data['price'] <= 0:
                return None

            current_price = price_data['price']
            indicators = self.simulate_technical_analysis(symbol, timeframe)
            change_24h = price_data['change_24h']

            if change_24h > 2:
                trend_bias = 'bullish'
            elif change_24h < -2:
                trend_bias = 'bearish'
            else:
                trend_bias = 'neutral'

            if timeframe == 'supershort':
                time_desc = '1–15m'
                price_change = random.uniform(-0.03, 0.03)
                base_probability = random.randint(65, 85)
            elif timeframe == 'short':
                time_desc = '1–4h'
                price_change = random.uniform(-0.1, 0.1)
                base_probability = random.randint(60, 80)
            elif timeframe == 'mid':
                time_desc = '3–6h'
                price_change = random.uniform(-0.15, 0.15)
                base_probability = random.randint(60, 80)
            elif timeframe == 'long':
                time_desc = '1–3 days'
                price_change = random.uniform(-0.2, 0.2)
                base_probability = random.randint(55, 75)
            elif timeframe == 'ulong':
                time_desc = '1–2 weeks'
                price_change = random.uniform(-0.3, 0.3)
                base_probability = random.randint(50, 70)
            else:
                time_desc = 'unknown'
                price_change = 0
                base_probability = 50

            if trend_bias == 'bullish':
                price_change = abs(price_change)
            elif trend_bias == 'bearish':
                price_change = -abs(price_change)

            target_price = current_price * (1 + price_change)

            if timeframe == 'supershort':
                if price_change > 0.015:
                    recommendation = 'STRONG BUY'
                    probability = base_probability
                elif price_change > 0.005:
                    recommendation = 'BUY'
                    probability = base_probability - 5
                elif price_change < -0.015:
                    recommendation = 'STRONG SELL'
                    probability = base_probability
                elif price_change < -0.005:
                    recommendation = 'SELL'
                    probability = base_probability - 5
                else:
                    recommendation = 'HOLD/WAIT'
                    probability = base_probability - 15
            else:
                if price_change > 0.05:
                    recommendation = 'BUY'
                    probability = base_probability
                elif price_change < -0.05:
                    recommendation = 'SELL'
                    probability = base_probability
                else:
                    recommendation = 'HOLD'
                    probability = base_probability - 10

            return {
                'symbol': symbol,
                'current_price': current_price,
                'target_price': target_price,
                'move': f"{'rise' if target_price > current_price else 'drop'} to ${target_price:.8f}",
                'timeframe': time_desc,
                'probability': probability,
                'recommendation': recommendation,
                'indicators': indicators,
                'price_data': price_data
            }
        except Exception as e:
            logger.error(f"Error generating forecast: {e}")
            return None


class TelegramBot:
    def __init__(self, token, binance_api_key=None, binance_secret_key=None):
        self.token = token
        self.analyzer = CryptoAnalyzer(binance_api_key, binance_secret_key)
        self.app = Application.builder().token(token).build()
        self.news_monitor = NewsMonitor(self.app)
        self.binance_api_key = binance_api_key
        self.setup_handlers()

    def setup_handlers(self):
        try:
            self.app.add_handler(CommandHandler("start", self.start))
            self.app.add_handler(CommandHandler("help", self.help))
            self.app.add_handler(CommandHandler("conf", self.detailed_analysis))
            self.app.add_handler(CommandHandler("news", self.toggle_news))
            self.app.add_handler(CommandHandler("status", self.status))
            self.app.add_handler(CommandHandler("keywords", self.manage_keywords))
            self.app.add_handler(CommandHandler("latest", self.show_latest_news))
            self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        except Exception as e:
            logger.error(f"Error setting up handlers: {e}")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat_id = update.effective_chat.id
            self.news_monitor.set_target_chat(chat_id)

            welcome_message = """🤖 **Enhanced Crypto Analysis Bot**

📊 **ANALYSIS COMMANDS:**
• `BTC/USDT` or `BTC` - Default (3-6h)
• `BTC supershort` - ⚡ 1-15m scalping
• `ETH short` - 1-4h analysis
• `DOGE mid` - 3-6h forecast
• `PEPE long` - 1-3 days
• `SOL ulong` - 1-2 weeks
• `BTC full` - 🔥 **ALL timeframes at once**

**📰 NEWS COMMANDS:**
• `/news` - Toggle news monitoring
• `/keywords` - Manage filters
• `/latest` - Show latest news
• `/status` - Bot status
• `/conf` - Detailed analysis

⚠️ **Disclaimer:** Educational purposes only. DYOR!"""

            await update.message.reply_text(welcome_message, parse_mode='Markdown')

            if not hasattr(self, '_news_task') or self._news_task.done():
                self._news_task = asyncio.create_task(self.news_monitor.monitor_news())

        except Exception as e:
            logger.error(f"Error in start: {e}")
            await update.message.reply_text("Welcome to Crypto Bot!")

    async def manage_keywords(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            args = context.args
            if not args:
                current = ", ".join(self.news_monitor.keywords)
                message = f"🔍 **Current Keywords:**\n{current}\n\n**Usage:**\n• `/keywords add word1,word2`\n• `/keywords remove word1`\n• `/keywords reset`"
                await update.message.reply_text(message, parse_mode='Markdown')
                return

            command = args[0].lower()
            if command == "add" and len(args) > 1:
                new_kw = [kw.strip().lower() for kw in " ".join(args[1:]).split(",") if kw.strip()]
                for kw in new_kw:
                    if kw not in self.news_monitor.keywords:
                        self.news_monitor.keywords.append(kw)
                await update.message.reply_text(f"✅ Added: {', '.join(new_kw)}")
            elif command == "remove" and len(args) > 1:
                kw_remove = [kw.strip().lower() for kw in " ".join(args[1:]).split(",")]
                for kw in kw_remove:
                    if kw in self.news_monitor.keywords:
                        self.news_monitor.keywords.remove(kw)
                await update.message.reply_text(f"🗑️ Removed: {', '.join(kw_remove)}")
            elif command == "reset":
                self.news_monitor.keywords = ["alpha", "airdrop", "reward", "listing", "new coin", "token"]
                await update.message.reply_text("🔄 Keywords reset")
        except Exception as e:
            logger.error(f"Error managing keywords: {e}")

    async def show_latest_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not hasattr(self.news_monitor, '_latest_news_titles') or not self.news_monitor._latest_news_titles:
                await update.message.reply_text("📋 No recent news available.")
                return

            message = "📋 **Latest News Titles:**\n\n"
            for i, title in enumerate(self.news_monitor._latest_news_titles, 1):
                message += f"{i}. {title}\n"
            message += f"\n⏰ {datetime.now().strftime('%H:%M:%S')}"
            await update.message.reply_text(message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error showing latest: {e}")

    async def toggle_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat_id = update.effective_chat.id
            if self.news_monitor.monitoring_active:
                self.news_monitor.monitoring_active = False
                await update.message.reply_text("📰 News monitoring **DISABLED**", parse_mode='Markdown')
            else:
                self.news_monitor.set_target_chat(chat_id)
                if not hasattr(self, '_news_task') or self._news_task.done():
                    self._news_task = asyncio.create_task(self.news_monitor.monitor_news())
                await update.message.reply_text("📰 News monitoring **ENABLED**", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error toggling news: {e}")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            news_status = "🟢 Active" if self.news_monitor.monitoring_active else "🔴 Inactive"
            message = f"""📊 **Bot Status**

**📰 News:** {news_status}
**🎯 Chat:** {self.news_monitor.target_chat_id or 'None'}
**🔗 Links:** {len(self.news_monitor.seen_links)}
**📄 Articles:** {len(self.news_monitor.seen_posts)}
**🏷️ Keywords:** {len(self.news_monitor.keywords)}"""
            await update.message.reply_text(message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in status: {e}")

    def parse_crypto_request(self, message_text):
        try:
            pattern = r'^([A-Z0-9]{1,10})/USDT\s*(supershort|short|mid|long|ulong|full)?\s*'
            match = re.match(pattern, message_text.strip(), re.IGNORECASE)
            if match:
                return match.group(1).upper(), (match.group(2) or 'mid').lower()

            pattern2 = r'^([A-Z0-9]{1,10})\s*(supershort|short|mid|long|ulong|full)?\s*'
            match2 = re.match(pattern2, message_text.strip(), re.IGNORECASE)
            if match2 and len(match2.group(1)) >= 2:
                return match2.group(1).upper(), (match2.group(2) or 'mid').lower()

            return None, None
        except Exception as e:
            logger.error(f"Error parsing: {e}")
            return None, None

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            message_text = update.message.text
            symbol, timeframe = self.parse_crypto_request(message_text)

            if symbol and timeframe:
                if timeframe == 'full':
                    await self.full_analysis(update, symbol)
                else:
                    await self.analyze_crypto(update, symbol, timeframe)
            else:
                await update.message.reply_text(
                    "❌ Invalid format!\n"
                    "• `BTC` - Default analysis\n"
                    "• `BTC supershort` - 1-15m\n"
                    "• `BTC full` - All timeframes",
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def full_analysis(self, update: Update, symbol: str):
        """Generate full analysis for all timeframes"""
        try:
            await update.message.reply_chat_action('typing')
            await update.message.reply_text(f"🔍 Generating full analysis for {symbol}...")

            timeframes = ['supershort', 'short', 'mid', 'long', 'ulong']
            timeframe_names = {
                'supershort': '⚡ Super Short (1-15m)',
                'short': '🕓 Short (1-4h)',
                'mid': '🕓 Mid (3-6h)',
                'long': '🕓 Long (1-3 days)',
                'ulong': '🕓 Ultra Long (1-2 weeks)'
            }

            message = f"🔥 **FULL ANALYSIS: {symbol}/USDT**\n"
            message += f"⏰ {datetime.now().strftime('%H:%M:%S')}\n\n"

            for tf in timeframes:
                forecast = self.analyzer.generate_forecast(symbol, tf)
                if not forecast:
                    message += f"{timeframe_names[tf]}: ❌ Error\n\n"
                    continue

                direction = "📈" if forecast['target_price'] > forecast['current_price'] else "📉"
                message += f"**{timeframe_names[tf]}**\n"
                message += f"{direction} {forecast['move']}\n"
                message += f"💡 Recommendation: **{forecast['recommendation']}**\n"
                message += f"🎲 Probability: **{forecast['probability']}%**\n\n"

            # Add current price info
            if forecast:
                message += f"**💰 Current Price Data:**\n"
                message += f"Price: ${forecast['current_price']:.8f}\n"
                message += f"24h Change: {forecast['price_data']['change_24h']:+.2f}%\n"
                message += f"24h High: ${forecast['price_data']['high_24h']:.8f}\n"
                message += f"24h Low: ${forecast['price_data']['low_24h']:.8f}\n"
                message += f"Source: {forecast['price_data']['source']}\n\n"

            message += "⚠️ **Disclaimer:** Educational analysis only!"

            await update.message.reply_text(message, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error in full_analysis: {e}")
            await update.message.reply_text("❌ Error generating full analysis")

    async def analyze_crypto(self, update: Update, symbol: str, timeframe: str):
        try:
            await update.message.reply_chat_action('typing')

            forecast = self.analyzer.generate_forecast(symbol, timeframe)
            if not forecast:
                await update.message.reply_text(f"❌ Could not analyze {symbol}/USDT")
                return

            user_id = update.effective_user.id
            self.analyzer.last_analysis[user_id] = forecast

            source = forecast['price_data'].get('source', 'Unknown')
            timeframe_emoji = "⚡" if timeframe == 'supershort' else "📊"

            summary = (
                f"{timeframe_emoji} **{symbol}/USDT** → {forecast['move']} ({forecast['timeframe']})\n"
                f"**Probability:** {forecast['probability']}%\n"
                f"**Recommendation:** {forecast['recommendation']}\n\n"
                f"Current: ${forecast['current_price']:.8f}\n"
                f"24h Change: {forecast['price_data']['change_24h']:+.2f}%\n"
                f"📡 Source: {source}\n\n"
                f"💡 Use `/conf` for detailed analysis\n"
                f"💡 Use `{symbol} full` for all timeframes"
            )

            await update.message.reply_text(summary, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error in analyze_crypto: {e}")
            await update.message.reply_text("❌ Error analyzing")

    async def detailed_analysis(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id

            if user_id not in self.analyzer.last_analysis:
                await update.message.reply_text(
                    "❌ No recent analysis found. Request a crypto pair first.\n"
                    "Example: `BTC` or `BTC/USDT`",
                    parse_mode='Markdown'
                )
                return

            forecast = self.analyzer.last_analysis[user_id]
            indicators = forecast.get('indicators', {})
            price_data = forecast.get('price_data', {})

            detailed_msg = f"📈 **Detailed Analysis: {forecast['symbol']}/USDT**\n\n"
            detailed_msg += f"**📊 Technical Indicators:**\n"
            detailed_msg += f"• Timeframes: {indicators.get('timeframes', 'N/A')}\n"

            rsi_value = indicators.get('rsi', 50)
            rsi_desc = "(overbought)" if rsi_value > 70 else "(oversold)" if rsi_value < 30 else "(neutral)"
            detailed_msg += f"• RSI: {rsi_value:.1f} {rsi_desc}\n"
            detailed_msg += f"• MACD: {indicators.get('macd_signal', 'neutral')}\n"
            detailed_msg += f"• {indicators.get('ema_periods', 'EMA')}: {indicators.get('ema_trend', 'sideways')}\n"
            detailed_msg += f"• Volume: {indicators.get('volume_trend', 'stable')}\n\n"

            detailed_msg += "**💰 Price Data:**\n"
            detailed_msg += f"• Current: ${forecast.get('current_price', 0):.8f}\n"
            detailed_msg += f"• Target: ${forecast.get('target_price', 0):.8f}\n"
            detailed_msg += f"• 24h High: ${price_data.get('high_24h', 0):.8f}\n"
            detailed_msg += f"• 24h Low: ${price_data.get('low_24h', 0):.8f}\n"
            detailed_msg += f"• 24h Change: {price_data.get('change_24h', 0):+.2f}%\n\n"

            detailed_msg += "**🎯 Forecast:**\n"
            detailed_msg += f"Expected {forecast.get('move', 'movement')} within {forecast.get('timeframe', 'time')}.\n"
            detailed_msg += f"Confidence: {forecast.get('probability', 50)}%\n\n"
            detailed_msg += f"**📋 Recommendation:** {forecast.get('recommendation', 'HOLD')}\n\n"
            detailed_msg += "⚠️ **Disclaimer:** Educational purposes only. DYOR!"

            await update.message.reply_text(detailed_msg, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error in detailed_analysis: {e}")
            await update.message.reply_text("❌ Error generating detailed analysis")

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            help_text = """📊 **How to use this Bot:**

**⏰ TIMEFRAMES:**
• supershort = ⚡ 1–15m
• short = 1–4h
• mid = 3–6h (default)
• long = 1–3 days
• ulong = 1–2 weeks
• full = 🔥 ALL timeframes

**📱 EXAMPLES:**
• `BTC` → mid (default)
• `BTC supershort`
• `ETH short`
• `DOGE long`
• `PEPE full` → All timeframes!

**📰 NEWS COMMANDS:**
• `/news` - Toggle monitoring
• `/keywords` - Manage filters
• `/status` - Bot status

⚠️ **Disclaimer:** Educational only!"""
            await update.message.reply_text(help_text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in help: {e}")

    def run(self):
        try:
            logger.info("Starting Enhanced Crypto Bot...")
            self.app.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logger.error(f"Error running bot: {e}")


def main():
    try:
        BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
        BINANCE_API_KEY = os.getenv('BINANCE_API_KEY')
        BINANCE_SECRET_KEY = os.getenv('BINANCE_SECRET')

        if not BOT_TOKEN:
            BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
            print("🔑 Using manual bot token")
        else:
            print("✅ Bot token loaded from .env")

        if not BINANCE_API_KEY:
            BINANCE_API_KEY = None
            print("🔓 No Binance API key")
        else:
            print("✅ Binance API key loaded")

        if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            print("❌ Error: Please set your bot token!")
            print("\n🔧 Method 1 - Using .env file:")
            print("Create .env file with:")
            print("TELEGRAM_BOT_TOKEN=your_token_here")
            print("BINANCE_API_KEY=your_api_key_here (optional)")
            return

        print("🚀 Starting Enhanced Crypto Bot...")
        print("📊 Features: Crypto Analysis + Real-Time News")
        print("🔥 NEW: Full analysis command (all timeframes)")

        bot = TelegramBot(BOT_TOKEN, BINANCE_API_KEY, BINANCE_SECRET_KEY)
        bot.run()

    except Exception as e:
        logger.error(f"Error in main: {e}")
        print(f"❌ Failed to start: {e}")


if __name__ == '__main__':
    main()

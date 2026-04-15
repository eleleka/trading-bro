import os
import logging
import re
import asyncio
import time
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import requests
from typing import Dict, List, Optional, Tuple

# --- Optional heavy dependencies (graceful fallback if missing) ---
try:
    import pandas as pd
    import numpy as np
    from ta.momentum import RSIIndicator
    from ta.trend import MACD, EMAIndicator
    from ta.volatility import BollingerBands, AverageTrueRange
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    print("⚠️  ta/pandas/numpy not installed — real TA disabled. Run: pip install ta pandas numpy")

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env file loaded")
except ImportError:
    print("⚠️ python-dotenv not installed. Run: pip install python-dotenv")
except Exception as e:
    print(f"⚠️ Error loading .env: {e}")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ===========================================================================
# CryptoAnalyzer
# ===========================================================================
class CryptoAnalyzer:
    # (kline_interval, candle_limit, fast_ema, slow_ema, move_per_score_unit)
    TIMEFRAME_CONFIG: Dict[str, Tuple] = {
        'supershort': ('1m',  100,  5,  12,  0.003),
        'short':      ('15m', 100,  9,  21,  0.010),
        'mid':        ('1h',  150, 20,  50,  0.015),
        'long':       ('4h',  150, 50, 200,  0.020),
        'ulong':      ('1d',  150, 50, 200,  0.040),
    }

    TIMEFRAME_DESC: Dict[str, str] = {
        'supershort': '1–15m',
        'short':      '1–4h',
        'mid':        '3–6h',
        'long':       '1–3 days',
        'ulong':      '1–2 weeks',
    }

    # Klines cache TTL per interval (seconds)
    _CACHE_TTL: Dict[str, int] = {
        '1m': 30, '15m': 120, '1h': 300, '4h': 600, '1d': 1800
    }

    def __init__(self, binance_api_key=None, binance_secret_key=None):
        self.last_analysis: Dict = {}
        self.binance_api   = "https://api.binance.com/api/v3"
        self.coingecko_api = "https://api.coingecko.com/api/v3"
        self.binance_api_key    = binance_api_key
        self.binance_secret_key = binance_secret_key

        # Klines cache: (symbol, interval, limit) -> (DataFrame, timestamp)
        self._klines_cache: Dict[tuple, tuple] = {}

        # Fear & Greed cache: (result_dict, timestamp)
        self._fng_cache: Tuple = (None, 0.0)

    # ------------------------------------------------------------------
    # Price data
    # ------------------------------------------------------------------
    def _get_binance_headers(self) -> Dict:
        h = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        }
        if self.binance_api_key:
            h['X-MBX-APIKEY'] = self.binance_api_key
        return h

    def _get_binance_data(self, symbol: str) -> Optional[Dict]:
        try:
            hdrs = self._get_binance_headers()
            pr = requests.get(f"{self.binance_api}/ticker/price?symbol={symbol}USDT",
                              timeout=15, headers=hdrs)
            sr = requests.get(f"{self.binance_api}/ticker/24hr?symbol={symbol}USDT",
                              timeout=15, headers=hdrs)
            if pr.status_code == 200 and sr.status_code == 200:
                p, s = pr.json(), sr.json()
                return {
                    'price':        float(p['price']),
                    'change_24h':   float(s['priceChangePercent']),
                    'volume':       float(s['volume']),
                    'high_24h':     float(s['highPrice']),
                    'low_24h':      float(s['lowPrice']),
                    'quote_volume': float(s.get('quoteVolume', 0)),
                    'source':       'Binance',
                }
        except Exception as e:
            logger.debug(f"Binance ticker failed: {e}")
        return None

    def _get_coingecko_data(self, symbol: str) -> Optional[Dict]:
        try:
            sr = requests.get(f"{self.coingecko_api}/search?query={symbol}", timeout=15)
            if sr.status_code != 200:
                return None
            coin = next(
                (c for c in sr.json().get('coins', [])
                 if c.get('symbol', '').upper() == symbol.upper()), None
            )
            if not coin:
                return None
            pr = requests.get(
                f"{self.coingecko_api}/simple/price"
                f"?ids={coin['id']}&vs_currencies=usd"
                f"&include_24hr_change=true&include_24hr_vol=true",
                timeout=15
            )
            if pr.status_code != 200:
                return None
            d = pr.json().get(coin['id'], {})
            price = d.get('usd', 0) or 0
            if price <= 0:
                return None
            chg = d.get('usd_24h_change', 0) or 0
            return {
                'price':      price,
                'change_24h': chg,
                'volume':     d.get('usd_24h_vol', 0) or 0,
                'high_24h':   price * (1 + abs(chg) / 100),
                'low_24h':    price * (1 - abs(chg) / 100),
                'source':     'CoinGecko',
            }
        except Exception as e:
            logger.debug(f"CoinGecko failed: {e}")
        return None

    def get_price_data(self, symbol: str) -> Optional[Dict]:
        return self._get_binance_data(symbol) or self._get_coingecko_data(symbol)

    # ------------------------------------------------------------------
    # Fear & Greed Index
    # ------------------------------------------------------------------
    def get_fear_greed(self) -> Optional[Dict]:
        """Fetch F&G from alternative.me. Cached 1 hour."""
        cached, ts = self._fng_cache
        if cached and (time.time() - ts) < 3600:
            return cached
        try:
            r = requests.get('https://api.alternative.me/fng/?limit=1', timeout=10)
            r.raise_for_status()
            item = r.json()['data'][0]
            value = int(item['value'])
            result = {
                'value':          value,
                'classification': item['value_classification'],
                'emoji':          self._fng_emoji(value),
            }
            self._fng_cache = (result, time.time())
            return result
        except Exception as e:
            logger.debug(f"Fear & Greed fetch failed: {e}")
            return None

    @staticmethod
    def _fng_emoji(value: int) -> str:
        if value <= 24: return '😱'
        if value <= 44: return '😨'
        if value <= 54: return '😐'
        if value <= 74: return '🤑'
        return '🚀'

    # ------------------------------------------------------------------
    # Klines — with TTL cache
    # ------------------------------------------------------------------
    def _get_klines(self, symbol: str, interval: str, limit: int = 100):
        """Return a cached or freshly fetched OHLCV DataFrame, or None."""
        if not TA_AVAILABLE:
            return None

        key = (symbol, interval, limit)
        ttl = self._CACHE_TTL.get(interval, 120)

        if key in self._klines_cache:
            df_cached, ts = self._klines_cache[key]
            if time.time() - ts < ttl:
                return df_cached

        try:
            url    = f"{self.binance_api}/klines"
            params = {'symbol': f"{symbol}USDT", 'interval': interval, 'limit': limit}
            r = requests.get(url, params=params, headers=self._get_binance_headers(), timeout=15)
            r.raise_for_status()
            df = pd.DataFrame(r.json(), columns=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades',
                'taker_buy_base', 'taker_buy_quote', 'ignore'
            ])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col])
            self._klines_cache[key] = (df, time.time())
            return df
        except Exception as e:
            logger.error(f"Klines fetch failed ({symbol} {interval}): {e}")
            return None

    # ------------------------------------------------------------------
    # Support & Resistance
    # ------------------------------------------------------------------
    def _compute_support_resistance(self, highs, lows, closes) -> Dict:
        result = {}
        try:
            current = float(closes.iloc[-1])
            result['resistance']      = round(float(highs.iloc[-20:].max()), 8)
            result['support']         = round(float(lows.iloc[-20:].min()), 8)
            result['near_resistance'] = round(float(highs.iloc[-5:].max()), 8)
            result['near_support']    = round(float(lows.iloc[-5:].min()), 8)
            if current > 0:
                result['pct_to_resistance'] = round(
                    (result['near_resistance'] - current) / current * 100, 2)
                result['pct_to_support'] = round(
                    (current - result['near_support']) / current * 100, 2)
        except Exception as e:
            logger.debug(f"S/R error: {e}")
        return result

    # ------------------------------------------------------------------
    # Bollinger Bands
    # ------------------------------------------------------------------
    def _compute_bollinger(self, closes) -> Dict:
        """Bollinger Bands (20, 2σ) with squeeze detection."""
        result = {}
        try:
            bb = BollingerBands(close=closes, window=20, window_dev=2)
            upper  = bb.bollinger_hband()
            lower  = bb.bollinger_lband()
            middle = bb.bollinger_mavg()
            width  = bb.bollinger_wband()   # (upper-lower)/middle × 100
            pband  = bb.bollinger_pband()   # (price-lower)/(upper-lower)

            result['bb_upper']  = round(float(upper.iloc[-1]),  8)
            result['bb_lower']  = round(float(lower.iloc[-1]),  8)
            result['bb_middle'] = round(float(middle.iloc[-1]), 8)
            result['bb_width']  = round(float(width.iloc[-1]),  4)
            result['bb_pband']  = round(float(pband.iloc[-1]),  4)

            # Squeeze: bandwidth is at or near its lowest in the lookback
            lookback  = min(50, len(width))
            min_width = float(width.iloc[-lookback:].min())
            result['bb_squeeze'] = float(width.iloc[-1]) <= min_width * 1.05

            # Positional signal
            pb = result['bb_pband']
            if pb <= 0.05:
                result['bb_signal'] = 'at_lower'    # potential bounce
            elif pb >= 0.95:
                result['bb_signal'] = 'at_upper'    # potential reversal
            elif result['bb_squeeze']:
                result['bb_signal'] = 'squeeze'     # volatility compression
            else:
                result['bb_signal'] = 'neutral'

        except Exception as e:
            logger.debug(f"Bollinger error: {e}")
        return result

    # ------------------------------------------------------------------
    # ATR — stop-loss suggestion
    # ------------------------------------------------------------------
    def _compute_atr(self, highs, lows, closes) -> Dict:
        """14-period ATR with 2×ATR stop-loss levels."""
        result = {}
        try:
            atr_ind = AverageTrueRange(high=highs, low=lows, close=closes, window=14)
            atr_val = float(atr_ind.average_true_range().iloc[-1])
            current = float(closes.iloc[-1])
            result['atr']              = round(atr_val, 8)
            result['atr_pct']          = round(atr_val / current * 100, 3)
            result['stop_loss_long']   = round(current - 2 * atr_val, 8)
            result['stop_loss_short']  = round(current + 2 * atr_val, 8)
        except Exception as e:
            logger.debug(f"ATR error: {e}")
        return result

    # ------------------------------------------------------------------
    # RSI Divergence
    # ------------------------------------------------------------------
    def _detect_rsi_divergence(self, closes, rsi_series) -> int:
        """
        Bullish divergence: price near recent low but RSI higher than it was → +1
        Bearish divergence: price near recent high but RSI lower than it was → -1
        Returns 0 if no clear divergence.
        """
        try:
            n = 20
            price_w = closes.iloc[-n:]
            rsi_w   = rsi_series.iloc[-n:].dropna()
            if len(rsi_w) < 5:
                return 0

            current_price = float(closes.iloc[-1])
            current_rsi   = float(rsi_series.iloc[-1])

            # Bullish: price at/near its recent low, RSI notably above its value then
            price_min_idx = price_w.idxmin()
            price_min     = float(price_w.min())
            rsi_at_pmin   = float(rsi_series.loc[price_min_idx]) if price_min_idx in rsi_series.index else current_rsi

            if current_price <= price_min * 1.015 and current_rsi > rsi_at_pmin + 5:
                return 1  # bullish divergence

            # Bearish: price at/near its recent high, RSI notably below its value then
            price_max_idx = price_w.idxmax()
            price_max     = float(price_w.max())
            rsi_at_pmax   = float(rsi_series.loc[price_max_idx]) if price_max_idx in rsi_series.index else current_rsi

            if current_price >= price_max * 0.985 and current_rsi < rsi_at_pmax - 5:
                return -1  # bearish divergence

        except Exception as e:
            logger.debug(f"Divergence detection error: {e}")
        return 0

    # ------------------------------------------------------------------
    # Order book
    # ------------------------------------------------------------------
    def _get_order_book_score(self, symbol: str) -> int:
        """+1 buy pressure, -1 sell pressure, 0 balanced (top-20 book)."""
        try:
            r = requests.get(
                f"{self.binance_api}/depth",
                params={'symbol': f"{symbol}USDT", 'limit': 20},
                headers=self._get_binance_headers(),
                timeout=10
            )
            r.raise_for_status()
            data = r.json()
            bid_vol = sum(float(q) for _, q in data.get('bids', []))
            ask_vol = sum(float(q) for _, q in data.get('asks', []))
            total   = bid_vol + ask_vol
            if total == 0:
                return 0
            ratio = bid_vol / total
            if ratio > 0.60: return  1
            if ratio < 0.40: return -1
            return 0
        except Exception as e:
            logger.debug(f"Order book failed: {e}")
            return 0

    # ------------------------------------------------------------------
    # Fallback indicators (when TA or klines unavailable)
    # ------------------------------------------------------------------
    def _fallback_indicators(self, base: Dict) -> Dict:
        base.update({
            'rsi': 50.0, 'macd_signal': 'neutral',
            'ema_trend': 'sideways', 'volume_trend': 'stable',
            'volume_ratio': 1.0, 'rsi_divergence': 0,
            'bb_signal': 'neutral', 'bb_squeeze': False,
            'data_source': 'fallback',
        })
        return base

    # ------------------------------------------------------------------
    # Core indicator computation
    # ------------------------------------------------------------------
    def compute_indicators(self, symbol: str, timeframe: str) -> Dict:
        cfg = self.TIMEFRAME_CONFIG.get(timeframe, self.TIMEFRAME_CONFIG['mid'])
        interval, limit, fast_p, slow_p, _ = cfg

        indicators: Dict = {
            'timeframes':  {'supershort':'1m/5m/15m','short':'15m/1h/4h',
                            'mid':'4h/1d','long':'1d/1w','ulong':'1d/1w'}.get(timeframe, interval),
            'ema_periods': f'EMA({fast_p}/{slow_p})',
        }

        if not TA_AVAILABLE:
            return self._fallback_indicators(indicators)

        df = self._get_klines(symbol, interval, limit)
        if df is None or len(df) < 30:
            logger.warning(f"Insufficient klines for {symbol} — fallback indicators")
            return self._fallback_indicators(indicators)

        closes  = df['close']
        highs   = df['high']
        lows    = df['low']
        volumes = df['volume']

        # --- RSI ---
        try:
            rsi_series = RSIIndicator(close=closes, window=14).rsi()
            indicators['rsi'] = round(float(rsi_series.iloc[-1]), 2)
        except Exception:
            rsi_series = None
            indicators['rsi'] = 50.0

        # --- MACD ---
        try:
            macd_obj  = MACD(close=closes)
            macd_diff = macd_obj.macd_diff()
            now, prev = float(macd_diff.iloc[-1]), float(macd_diff.iloc[-2]) if len(macd_diff) > 1 else 0.0
            if   now > 0 and prev <= 0: indicators['macd_signal'] = 'bullish_cross'
            elif now < 0 and prev >= 0: indicators['macd_signal'] = 'bearish_cross'
            elif now > 0:               indicators['macd_signal'] = 'bullish'
            elif now < 0:               indicators['macd_signal'] = 'bearish'
            else:                       indicators['macd_signal'] = 'neutral'
            indicators['macd_diff'] = round(now, 10)
        except Exception:
            indicators['macd_signal'] = 'neutral'

        # --- EMA trend ---
        try:
            fp = min(fast_p, len(closes) - 1)
            sp = min(slow_p, len(closes) - 1)
            ef = float(EMAIndicator(close=closes, window=fp).ema_indicator().iloc[-1])
            es = float(EMAIndicator(close=closes, window=sp).ema_indicator().iloc[-1])
            px = float(closes.iloc[-1])
            indicators.update({'ema_fast': round(ef, 8), 'ema_slow': round(es, 8)})
            if   px > ef > es: indicators['ema_trend'] = 'upward'
            elif px < ef < es: indicators['ema_trend'] = 'downward'
            elif ef > es:      indicators['ema_trend'] = 'upward'
            elif ef < es:      indicators['ema_trend'] = 'downward'
            else:              indicators['ema_trend'] = 'sideways'
        except Exception:
            indicators['ema_trend'] = 'sideways'

        # --- Volume ---
        try:
            vol_mean = float(volumes.iloc[-20:].mean())
            vol_now  = float(volumes.iloc[-1])
            ratio    = vol_now / vol_mean if vol_mean > 0 else 1.0
            indicators['volume_ratio'] = round(ratio, 2)
            if   ratio > 1.2: indicators['volume_trend'] = 'increasing'
            elif ratio < 0.8: indicators['volume_trend'] = 'decreasing'
            else:             indicators['volume_trend'] = 'stable'
        except Exception:
            indicators.update({'volume_trend': 'stable', 'volume_ratio': 1.0})

        # --- Bollinger Bands ---
        indicators.update(self._compute_bollinger(closes))

        # --- ATR + stop-loss ---
        indicators.update(self._compute_atr(highs, lows, closes))

        # --- RSI divergence ---
        if rsi_series is not None:
            indicators['rsi_divergence'] = self._detect_rsi_divergence(closes, rsi_series)
        else:
            indicators['rsi_divergence'] = 0

        # --- Support & Resistance ---
        indicators.update(self._compute_support_resistance(highs, lows, closes))

        indicators['data_source'] = 'live'
        return indicators

    # ------------------------------------------------------------------
    # Signal scoring
    # ------------------------------------------------------------------
    def _compute_score(self, indicators: Dict, change_24h: float, ob_score: int) -> int:
        score = 0
        ema_trend = indicators.get('ema_trend', 'sideways')

        # RSI
        rsi = indicators.get('rsi', 50)
        if   rsi < 30: score += 1
        elif rsi > 70: score -= 1

        # MACD (crossovers worth double)
        ms = indicators.get('macd_signal', 'neutral')
        if   'bullish_cross' in ms: score += 2
        elif 'bullish'       in ms: score += 1
        elif 'bearish_cross' in ms: score -= 2
        elif 'bearish'       in ms: score -= 1

        # EMA
        if   ema_trend == 'upward':   score += 1
        elif ema_trend == 'downward': score -= 1

        # Volume confirms trend direction
        vt = indicators.get('volume_trend', 'stable')
        if vt == 'increasing':
            if   ema_trend == 'upward':   score += 1
            elif ema_trend == 'downward': score -= 1

        # 24h macro bias
        if   change_24h >  3: score += 1
        elif change_24h < -3: score -= 1

        # Bollinger Bands positional signal
        bb_sig = indicators.get('bb_signal', 'neutral')
        if   bb_sig == 'at_lower' and ema_trend != 'downward': score += 1
        elif bb_sig == 'at_upper' and ema_trend != 'upward':   score -= 1

        # RSI divergence
        score += indicators.get('rsi_divergence', 0)

        # Order book (supershort only; 0 for other timeframes)
        score += ob_score

        return score

    # ------------------------------------------------------------------
    # Forecast (main public API)
    # ------------------------------------------------------------------
    def generate_forecast(self, symbol: str, timeframe: str = 'supershort') -> Optional[Dict]:
        try:
            price_data = self.get_price_data(symbol)
            if not price_data or price_data['price'] <= 0:
                return None

            current_price = price_data['price']
            change_24h    = price_data['change_24h']

            indicators = self.compute_indicators(symbol, timeframe)

            # Order book for supershort only
            ob_score = 0
            if timeframe == 'supershort':
                ob_score = self._get_order_book_score(symbol)
                indicators['order_book_score'] = ob_score
                indicators['order_book_bias'] = (
                    'buy pressure' if ob_score > 0 else
                    'sell pressure' if ob_score < 0 else 'balanced'
                )

            # Fear & Greed
            fng = self.get_fear_greed()
            if fng:
                indicators['fear_greed'] = fng

            score = self._compute_score(indicators, change_24h, ob_score)
            indicators['signal_score'] = score

            cfg = self.TIMEFRAME_CONFIG.get(timeframe, self.TIMEFRAME_CONFIG['mid'])
            _, _, _, _, move_per_unit = cfg
            time_desc = self.TIMEFRAME_DESC.get(timeframe, 'unknown')

            price_change = score * move_per_unit
            target_price = current_price * (1 + price_change)
            probability  = min(50 + abs(score) * 5, 80)

            # Recommendation
            if timeframe == 'supershort':
                if   score >= 3:  recommendation = 'STRONG BUY'
                elif score >= 1:  recommendation = 'BUY'
                elif score <= -3: recommendation = 'STRONG SELL'
                elif score <= -1: recommendation = 'SELL'
                else:             recommendation = 'HOLD/WAIT'
            else:
                if   score >= 2:  recommendation = 'BUY'
                elif score <= -2: recommendation = 'SELL'
                else:             recommendation = 'HOLD'

            return {
                'symbol':         symbol,
                'current_price':  current_price,
                'target_price':   target_price,
                'move': f"{'rise' if target_price >= current_price else 'drop'} to ${target_price:.8f}",
                'timeframe':      time_desc,
                'probability':    probability,
                'recommendation': recommendation,
                'indicators':     indicators,
                'price_data':     price_data,
            }

        except Exception as e:
            logger.error(f"Forecast error: {e}")
            return None


# ===========================================================================
# TelegramBot
# ===========================================================================
class TelegramBot:
    def __init__(self, token: str, binance_api_key=None, binance_secret_key=None):
        self.token    = token
        self.analyzer = CryptoAnalyzer(binance_api_key, binance_secret_key)
        self.app      = Application.builder().token(token).build()
        self._setup_handlers()

    def _setup_handlers(self):
        self.app.add_handler(CommandHandler("start",  self.cmd_start))
        self.app.add_handler(CommandHandler("help",   self.cmd_help))
        self.app.add_handler(CommandHandler("conf",   self.cmd_detailed))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("fng",    self.cmd_fng))
        self.app.add_handler(CallbackQueryHandler(self.on_timeframe_button, pattern=r'^tf:'))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))

    # ------------------------------------------------------------------
    # Keyboard builder
    # ------------------------------------------------------------------
    @staticmethod
    def _timeframe_keyboard(symbol: str) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("⚡ SS (1-15m)",  callback_data=f"tf:{symbol}:supershort"),
                InlineKeyboardButton("🕐 Short (1-4h)", callback_data=f"tf:{symbol}:short"),
                InlineKeyboardButton("🕓 Mid (3-6h)",   callback_data=f"tf:{symbol}:mid"),
            ],
            [
                InlineKeyboardButton("📅 Long (1-3d)",  callback_data=f"tf:{symbol}:long"),
                InlineKeyboardButton("📆 ULong (1-2w)", callback_data=f"tf:{symbol}:ulong"),
                InlineKeyboardButton("🔥 All TFs",      callback_data=f"tf:{symbol}:full"),
            ],
        ]
        return InlineKeyboardMarkup(rows)

    # ------------------------------------------------------------------
    # Shared analysis formatter
    # ------------------------------------------------------------------
    def _format_analysis(self, forecast: Dict, timeframe: str) -> str:
        ind       = forecast['indicators']
        pd_data   = forecast['price_data']
        score     = ind.get('signal_score', 0)
        data_src  = ind.get('data_source', 'fallback')
        tf_emoji  = "⚡" if timeframe == 'supershort' else "📊"
        src_emoji = "🔬" if data_src == 'live' else "⚠️"

        lines = [
            f"{tf_emoji} *{forecast['symbol']}/USDT* → {forecast['move']} "
            f"({forecast['timeframe']})",
            f"Signal Score: *{score:+d}*  |  Probability: *{forecast['probability']}%*",
            f"Recommendation: *{forecast['recommendation']}*",
            "",
            f"Price:      ${forecast['current_price']:.8f}",
            f"24h Change: {pd_data['change_24h']:+.2f}%",
        ]

        # ATR stop-loss
        if 'stop_loss_long' in ind and score >= 0:
            lines.append(f"Stop-Loss:  ${ind['stop_loss_long']:.8f}  "
                         f"(ATR {ind.get('atr_pct', 0):.2f}%)")
        elif 'stop_loss_short' in ind and score < 0:
            lines.append(f"Stop-Loss:  ${ind['stop_loss_short']:.8f}  "
                         f"(ATR {ind.get('atr_pct', 0):.2f}%)")

        # BB squeeze notice
        if ind.get('bb_squeeze'):
            lines.append("⚠️ BB Squeeze — breakout may be imminent")

        # RSI divergence
        div = ind.get('rsi_divergence', 0)
        if div == 1:
            lines.append("📐 Bullish RSI divergence detected")
        elif div == -1:
            lines.append("📐 Bearish RSI divergence detected")

        # Fear & Greed
        fng = ind.get('fear_greed')
        if fng:
            lines.append(f"\n{fng['emoji']} Market Sentiment: "
                         f"*{fng['classification']}* ({fng['value']}/100)")

        lines += [
            "",
            f"{src_emoji} TA: {data_src}  |  📡 Price: {pd_data['source']}",
            "💡 /conf for full indicator breakdown",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # /start
    # ------------------------------------------------------------------
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ta = "✅ Real TA (ta + pandas)" if TA_AVAILABLE else "⚠️ Simplified fallback"
        msg = f"""🤖 *Crypto Analysis Bot*

📊 *How to analyse a coin:*
Just type the ticker — e.g. `BTC`, `ETH`, `PEPE`
Then pick a timeframe from the buttons that appear.

🔬 *What you get:*
RSI · MACD · EMA · Volume · Bollinger Bands
ATR Stop-Loss · RSI Divergence · S/R Levels
Fear & Greed Index · Order Book (supershort)

*Commands:*
• /conf — full indicator breakdown of last analysis
• /fng  — current Fear & Greed Index
• /status — bot info
• /help — usage guide

🔬 TA Engine: {ta}
⚠️ _Educational purposes only. DYOR._"""
        await update.message.reply_text(msg, parse_mode='Markdown')

    # ------------------------------------------------------------------
    # /help
    # ------------------------------------------------------------------
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = """📊 *Usage Guide*

*Analyse a coin:*
Type `BTC`, `ETH`, `SOL` etc.
Buttons appear — tap a timeframe:

⚡ *SS (1–15m)* — scalping + order book
🕐 *Short (1–4h)* — intraday swing
🕓 *Mid (3–6h)* — default
📅 *Long (1–3d)* — position trade
📆 *ULong (1–2w)* — macro view
🔥 *All TFs* — full breakdown

Or type directly: `BTC supershort`, `ETH full`

*Indicators used:*
• RSI (14) — overbought/oversold
• MACD (12/26/9) — momentum & crossovers
• EMA — trend direction
• Bollinger Bands (20, 2σ) — volatility & squeeze
• ATR (14) — stop-loss placement
• RSI Divergence — trend reversal warning
• Volume — trend confirmation
• Support & Resistance — nearby price levels
• Order book — buy/sell pressure (supershort)
• Fear & Greed — macro market sentiment

*Commands:*
• /conf — last analysis detail
• /fng  — Fear & Greed Index
• /status — bot status

⚠️ _Educational only. Not financial advice._"""
        await update.message.reply_text(msg, parse_mode='Markdown')

    # ------------------------------------------------------------------
    # /fng
    # ------------------------------------------------------------------
    async def cmd_fng(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        fng = self.analyzer.get_fear_greed()
        if not fng:
            await update.message.reply_text("❌ Could not fetch Fear & Greed Index.")
            return
        bar_filled = int(fng['value'] / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        msg = (f"{fng['emoji']} *Fear & Greed Index*\n\n"
               f"`{bar}` {fng['value']}/100\n"
               f"*{fng['classification']}*")
        await update.message.reply_text(msg, parse_mode='Markdown')

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ta  = "✅ Real TA" if TA_AVAILABLE else "⚠️ Fallback"
        fng = self.analyzer.get_fear_greed()
        fng_str = (f"{fng['emoji']} {fng['classification']} ({fng['value']})"
                   if fng else "unavailable")
        cache_entries = len(self.analyzer._klines_cache)
        msg = (f"📊 *Bot Status*\n\n"
               f"🔬 TA Engine:     {ta}\n"
               f"🗄️  Klines cache: {cache_entries} entries\n"
               f"😱 Fear & Greed:  {fng_str}")
        await update.message.reply_text(msg, parse_mode='Markdown')

    # ------------------------------------------------------------------
    # /conf — detailed last analysis
    # ------------------------------------------------------------------
    async def cmd_detailed(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.analyzer.last_analysis:
            await update.message.reply_text(
                "❌ No recent analysis. Type a ticker first, e.g. `BTC`",
                parse_mode='Markdown'
            )
            return

        fc  = self.analyzer.last_analysis[user_id]
        ind = fc['indicators']
        pd_data = fc['price_data']
        score = ind.get('signal_score', 0)

        rsi    = ind.get('rsi', 50)
        r_desc = "(overbought)" if rsi > 70 else "(oversold)" if rsi < 30 else "(neutral)"

        div = ind.get('rsi_divergence', 0)
        div_str = "📐 Bullish" if div == 1 else "📐 Bearish" if div == -1 else "None"

        bb_sig = ind.get('bb_signal', 'neutral')
        bb_squeeze = "⚠️ YES" if ind.get('bb_squeeze') else "No"

        lines = [
            f"📈 *Detailed Analysis: {fc['symbol']}/USDT*",
            f"Timeframe: {fc['timeframe']}",
            "",
            "*📊 Indicators:*",
            f"• RSI:        {rsi:.1f} {r_desc}",
            f"• MACD:       {ind.get('macd_signal','neutral')}",
            f"• EMA Trend:  {ind.get('ema_trend','sideways')} ({ind.get('ema_periods','')})",
            f"• Volume:     {ind.get('volume_trend','stable')} ({ind.get('volume_ratio',1.0):.2f}×)",
            "",
            "*📉 Bollinger Bands:*",
            f"• Upper: ${ind.get('bb_upper',0):.8f}",
            f"• Middle: ${ind.get('bb_middle',0):.8f}",
            f"• Lower: ${ind.get('bb_lower',0):.8f}",
            f"• Width: {ind.get('bb_width',0):.2f}%   Signal: {bb_sig}",
            f"• Squeeze: {bb_squeeze}",
            "",
            "*🛑 ATR Stop-Loss (2×ATR):*",
            f"• ATR: ${ind.get('atr',0):.8f} ({ind.get('atr_pct',0):.2f}%)",
            f"• Long SL:  ${ind.get('stop_loss_long',0):.8f}",
            f"• Short SL: ${ind.get('stop_loss_short',0):.8f}",
            "",
            "*📐 Divergence:* " + div_str,
        ]

        if 'near_support' in ind:
            lines += [
                "",
                "*📌 Support & Resistance:*",
                f"• Near Support:    ${ind['near_support']:.8f}"
                + (f" ({ind['pct_to_support']:.2f}% away)" if 'pct_to_support' in ind else ""),
                f"• Near Resistance: ${ind['near_resistance']:.8f}"
                + (f" ({ind['pct_to_resistance']:.2f}% away)" if 'pct_to_resistance' in ind else ""),
            ]

        if 'order_book_bias' in ind:
            lines.append(f"\n*📖 Order Book:* {ind['order_book_bias']}")

        fng = ind.get('fear_greed')
        if fng:
            lines.append(f"\n*😱 Fear & Greed:* {fng['emoji']} {fng['classification']} ({fng['value']}/100)")

        lines += [
            "",
            f"*⚡ Signal Score: {score:+d}*",
            f"*📋 Recommendation: {fc['recommendation']}*",
            f"Probability: {fc['probability']}%",
            "",
            "*💰 Price:*",
            f"• Current: ${fc['current_price']:.8f}",
            f"• Target:  ${fc['target_price']:.8f}",
            f"• 24h High: ${pd_data.get('high_24h',0):.8f}",
            f"• 24h Low:  ${pd_data.get('low_24h',0):.8f}",
            f"• 24h Change: {pd_data.get('change_24h',0):+.2f}%",
            "",
            "⚠️ _Educational only. DYOR._",
        ]

        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

    # ------------------------------------------------------------------
    # Message handler — parse ticker, show mid analysis + keyboard
    # ------------------------------------------------------------------
    def _parse_request(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        p1 = r'^([A-Z0-9]{1,10})/USDT\s*(supershort|short|mid|long|ulong|full)?\s*$'
        m  = re.match(p1, text.strip(), re.IGNORECASE)
        if m:
            return m.group(1).upper(), (m.group(2) or 'mid').lower()
        p2 = r'^([A-Z0-9]{2,10})\s*(supershort|short|mid|long|ulong|full)?\s*$'
        m  = re.match(p2, text.strip(), re.IGNORECASE)
        if m:
            return m.group(1).upper(), (m.group(2) or 'mid').lower()
        return None, None

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        symbol, timeframe = self._parse_request(update.message.text)
        if not symbol:
            await update.message.reply_text(
                "❌ Not recognised. Type a ticker like `BTC` or `ETH`.",
                parse_mode='Markdown'
            )
            return

        if timeframe == 'full':
            await self._send_full_analysis(update.message, symbol)
        else:
            await self._send_analysis(update.message, symbol, timeframe, show_keyboard=True)

    # ------------------------------------------------------------------
    # Inline button callback
    # ------------------------------------------------------------------
    async def on_timeframe_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        _, symbol, timeframe = query.data.split(':')

        if timeframe == 'full':
            await self._send_full_analysis(query.message, symbol)
        else:
            await self._edit_analysis(query, symbol, timeframe)

    # ------------------------------------------------------------------
    # Internal send/edit helpers
    # ------------------------------------------------------------------
    async def _send_analysis(self, message, symbol: str, timeframe: str,
                              show_keyboard: bool = False):
        try:
            await message.reply_chat_action('typing')
            forecast = self.analyzer.generate_forecast(symbol, timeframe)
            if not forecast:
                await message.reply_text(f"❌ Could not fetch data for {symbol}/USDT.")
                return

            # Store for /conf
            if hasattr(message, 'from_user') and message.from_user:
                self.analyzer.last_analysis[message.from_user.id] = forecast

            text    = self._format_analysis(forecast, timeframe)
            kb      = self._timeframe_keyboard(symbol) if show_keyboard else None
            await message.reply_text(text, parse_mode='Markdown', reply_markup=kb)
        except Exception as e:
            logger.error(f"Error in _send_analysis: {e}")
            await message.reply_text("❌ Error running analysis.")

    async def _edit_analysis(self, query, symbol: str, timeframe: str):
        try:
            await query.message.reply_chat_action('typing')
            forecast = self.analyzer.generate_forecast(symbol, timeframe)
            if not forecast:
                await query.edit_message_text(f"❌ Could not fetch data for {symbol}/USDT.")
                return

            # Store for /conf using the callback query user
            if query.from_user:
                self.analyzer.last_analysis[query.from_user.id] = forecast

            text = self._format_analysis(forecast, timeframe)
            kb   = self._timeframe_keyboard(symbol)
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=kb)
        except Exception as e:
            logger.error(f"Error in _edit_analysis: {e}")

    async def _send_full_analysis(self, message, symbol: str):
        try:
            await message.reply_chat_action('typing')
            await message.reply_text(f"🔍 Running all timeframes for *{symbol}*…",
                                     parse_mode='Markdown')

            timeframes = ['supershort', 'short', 'mid', 'long', 'ulong']
            tf_labels  = {
                'supershort': '⚡ SS (1–15m)',
                'short':      '🕐 Short (1–4h)',
                'mid':        '🕓 Mid (3–6h)',
                'long':       '📅 Long (1–3d)',
                'ulong':      '📆 Ultra (1–2w)',
            }
            lines = [f"🔥 *FULL ANALYSIS: {symbol}/USDT*",
                     f"⏰ {datetime.now().strftime('%H:%M:%S')}", ""]

            last_fc = None
            for tf in timeframes:
                fc = self.analyzer.generate_forecast(symbol, tf)
                if not fc:
                    lines.append(f"{tf_labels[tf]}: ❌ Error\n")
                    continue
                last_fc = fc
                direction = "📈" if fc['target_price'] >= fc['current_price'] else "📉"
                score     = fc['indicators'].get('signal_score', 0)
                lines += [
                    f"*{tf_labels[tf]}*",
                    f"{direction} {fc['move']}",
                    f"*{fc['recommendation']}* | Score {score:+d} | {fc['probability']}%",
                    "",
                ]

            if last_fc:
                pd_data = last_fc['price_data']
                fng     = last_fc['indicators'].get('fear_greed')
                lines += [
                    "*💰 Market Snapshot:*",
                    f"Price: ${last_fc['current_price']:.8f}",
                    f"24h Change: {pd_data['change_24h']:+.2f}%",
                    f"Source: {pd_data['source']}",
                ]
                if fng:
                    lines.append(f"{fng['emoji']} Sentiment: {fng['classification']} ({fng['value']}/100)")

            lines.append("\n⚠️ _Educational only._")
            await message.reply_text("\n".join(lines), parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Full analysis error: {e}")
            await message.reply_text("❌ Error generating full analysis.")

    def run(self):
        logger.info("Starting Crypto Analysis Bot…")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)


# ===========================================================================
# Entry point
# ===========================================================================
def main():
    BOT_TOKEN       = os.getenv('TELEGRAM_BOT_TOKEN')
    BINANCE_API_KEY = os.getenv('BINANCE_API_KEY')
    BINANCE_SECRET  = os.getenv('BINANCE_SECRET')

    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set.")
        print("Create a .env file: TELEGRAM_BOT_TOKEN=your_token")
        return

    print("✅ Bot token loaded")
    print(f"🔬 Real TA: {'enabled' if TA_AVAILABLE else 'DISABLED — run: pip install ta pandas numpy'}")
    print("🚀 Starting…")

    TelegramBot(BOT_TOKEN, BINANCE_API_KEY, BINANCE_SECRET).run()


if __name__ == '__main__':
    main()

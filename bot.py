import os
import json
import time
import requests
import threading
import base64
import traceback
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
PORT = int(os.environ.get("PORT", 8080))

PAIRS = ["EUR/USD", "GBP/USD"]
TIMEFRAMES = ["15min", "1h", "4h"]
OPPORTUNITIES_FILE = "opportunities.json"

PAIR_CURRENCIES = {
    "EUR/USD": ["EUR", "USD"],
    "GBP/USD": ["GBP", "USD"],
}

# حالة التريد المنتظر للتأكيد
pending_trade = {}
waiting_confirmation = False

# تتبع الـ Setups النشطة لكل زوج
active_setups = {}

# Cache ديال البيانات باش ما نطلبوش أكثر من مرة
data_cache = {}

def fetch_all_data():
    """كيجيب بيانات كل الأزواج مرة واحدة ويحفظها فالـ cache"""
    global data_cache
    data_cache = {}
    for pair in PAIRS:
        data_cache[pair] = {}
        for tf in TIMEFRAMES:  # تم حذف الـ 5min نهائياً
            result = get_price_data(pair, tf)
            data_cache[pair][tf] = result

def get_cached_data(pair, interval):
    """كيرجع البيانات من الـ cache"""
    return data_cache.get(pair, {}).get(interval, None)

def send_telegram(msg, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    r = requests.post(url, json=payload)
    print(r.status_code)
    print(r.text)

def send_with_buttons(msg, trade):
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ نعم، دخلها!", "callback_data": "yes"},
            {"text": "❌ لا، تجاوزها", "callback_data": "no"}
        ]]
    }
    send_telegram(msg, reply_markup=keyboard)

def answer_callback(callback_query_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": callback_query_id})

def set_webhook():
    # امسح الـ webhook القديم أولاً
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook")
    time.sleep(2)
    # سجل الجديد
    webhook_url = "https://forex-trading-bot-production-4f87.up.railway.app/webhook"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    r = requests.post(url, json={"url": webhook_url})
    print(f"Webhook set: {r.json()}")

def get_high_impact_news(pair):
    try:
        currencies = PAIR_CURRENCIES.get(pair, [])
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=10)
        events = r.json()
        now = datetime.now(timezone.utc)
        danger_events = []
        warning_events = []
        for event in events:
            print(
                event.get("title"),
                event.get("currency"),
                event.get("impact"),
                event.get("date")
            )

            if event.get("impact") != "High":
                continue
            if event.get("currency") not in currencies:
                continue
            try:
                event_time = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            except:
                continue
            diff_minutes = (event_time - now).total_seconds() / 60
            if -30 <= diff_minutes <= 120:
                danger_events.append(event["title"])
            elif 120 < diff_minutes <= 480:
                warning_events.append(event["title"])
        return danger_events, warning_events
    except Exception as e:
        print(f"News API Error: {e}")
        return [], []

def get_market_summary(pair):
    """كيجيب ملخص تحركات السوق ديال اليوم"""
    try:
        result_1h = get_cached_data(pair, "1h") or get_price_data(pair, "1h", 24)
        result_15 = get_cached_data(pair, "15min") or get_price_data(pair, "15min", 8)
        if not result_1h or not result_15:
            return None

        closes_1h = result_1h[0]
        closes_15 = result_15[0]

        # تحرك اليوم
        open_price = closes_1h[0]
        current = closes_1h[-1]
        change = round(current - open_price, 6)
        change_pct = round((change / open_price) * 100, 3)
        direction_emoji = "📈" if change > 0 else "📉"

        # أعلى وأدنى اليوم
        highs_1h = result_1h[1]
        lows_1h = result_1h[2]
        high_day = round(max(highs_1h), 6)
        low_day = round(min(lows_1h), 6)

        # تحرك آخر ساعة
        last_hour_change = round(closes_15[-1] - closes_15[0], 6)
        last_hour_emoji = "⬆️" if last_hour_change > 0 else "⬇️"

        return {
            "change": change,
            "change_pct": change_pct,
            "direction_emoji": direction_emoji,
            "high_day": high_day,
            "low_day": low_day,
            "last_hour_change": last_hour_change,
            "last_hour_emoji": last_hour_emoji,
            "current": current
        }
    except:
        return None

def get_news_summary(pair):
    """كيجيب ملخص الأخبار ديال اليوم"""
    try:
        currencies = PAIR_CURRENCIES.get(pair, [])
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=10)
        events = r.json()
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        today_news = []
        for event in events:
            if event.get("impact") not in ["High", "Medium"]:
                continue
            if event.get("currency") not in currencies:
                continue
            try:
                event_time = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            except:
                continue
            if event_time.strftime("%Y-%m-%d") == today:
                impact_emoji = "🔴" if event.get("impact") == "High" else "🟡"
                diff = (event_time - now).total_seconds() / 60
                if diff < -60:
                    status = "مرات"
                elif diff < 0:
                    status = "داز دابا"
                else:
                    status = f"بعد {int(diff)} دقيقة"
                today_news.append(f"{impact_emoji} {event['title']} ({status})")
        return today_news
    except:
        return []
price_cache = {}

CACHE_SECONDS = {
    "15min": 900,
    "1h": 3600,
    "4h": 14400
}

def get_price_data(pair, interval="15min", outputsize=250, bypass_cache=False):
    global price_cache

    cache_key = f"{pair}_{interval}"
    now_ts = time.time()

    if not bypass_cache and cache_key in price_cache:
        cached_time = price_cache[cache_key]["time"]

        if now_ts - cached_time < CACHE_SECONDS.get(interval, 900):
            return price_cache[cache_key]["data"]

    params = {
        "symbol": pair,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY
    }

    try:
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params=params,
            timeout=15
        )

        data = r.json()

        if "values" not in data:
            print(
                f"API Error {pair} {interval}: "
                f"{data.get('message', data.get('code', 'unknown'))}"
            )
            return None

        closes = [float(v["close"]) for v in reversed(data["values"])]
        highs = [float(v["high"]) for v in reversed(data["values"])]
        lows = [float(v["low"]) for v in reversed(data["values"])]
        opens = [float(v["open"]) for v in reversed(data["values"])]

        result = (closes, highs, lows, opens)

        price_cache[cache_key] = {
            "time": now_ts,
            "data": result
        }

        return result

    except Exception:
        print(f"Price API Error {pair} {interval}:")
        traceback.print_exc()
        return None

def calc_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calc_macd(closes):
    def ema(data, period):
        k = 2 / (period + 1)
        result = [data[0]]
        for v in data[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result
    if len(closes) < 26:
        return None, None
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal = ema(macd_line, 9)
    return round(macd_line[-1], 6), round(signal[-1], 6)

def calc_atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return None
    return round(sum(trs[-period:]) / period, 6)

def calc_ema(prices, period=200):
    if len(prices) < period:
        return None

    ema = sum(prices[:period]) / period
    multiplier = 2 / (period + 1)

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return ema

def calc_ema_series(prices, period=200):
    if len(prices) < period:
        return None
    ema_values = [None] * (period - 1)
    ema = sum(prices[:period]) / period
    ema_values.append(ema)
    multiplier = 2 / (period + 1)
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
        ema_values.append(ema)
    return ema_values

def get_trend_structure(closes):
    if len(closes) < 20:
        return None

    recent = closes[-10:]
    older = closes[-20:-10]

    if max(recent) > max(older) and min(recent) > min(older):
        return "UP"

    if max(recent) < max(older) and min(recent) < min(older):
        return "DOWN"

    return "SIDEWAYS"

def get_support_resistance(highs, lows):
    support = min(lows[-20:])
    resistance = max(highs[-20:])
    return support, resistance

def check_confirmation_candle(closes, highs, lows, direction):
    if len(closes) < 3:
        return False
    if direction == "BUY":
        # Strong Bullish
        body = closes[-1] - closes[-2]
        candle_range = highs[-1] - lows[-1]
        strong_bullish = body > 0 and candle_range > 0 and (body >= 0.5 * candle_range)

        # Bullish Engulfing
        is_curr_bullish = closes[-1] > closes[-2]
        is_prev_bearish = closes[-2] < closes[-3]
        body_curr = closes[-1] - closes[-2]
        body_prev = closes[-3] - closes[-2]
        bullish_engulfing = is_curr_bullish and is_prev_bearish and (body_curr > body_prev)

        return strong_bullish or bullish_engulfing
    elif direction == "SELL":
        # Strong Bearish
        body = closes[-2] - closes[-1]
        candle_range = highs[-1] - lows[-1]
        strong_bearish = body > 0 and candle_range > 0 and (body >= 0.5 * candle_range)

        # Bearish Engulfing
        is_curr_bearish = closes[-1] < closes[-2]
        is_prev_bullish = closes[-2] > closes[-3]
        body_curr = closes[-2] - closes[-1]
        body_prev = closes[-2] - closes[-3]
        bearish_engulfing = is_curr_bearish and is_prev_bullish and (body_curr > body_prev)

        return strong_bearish or bearish_engulfing
    return False

def analyze_timeframe(pair, interval, bypass_cache=False):
    """المرحلة الأولى: فحص شروط الـ Setup الأساسية فقط (لا يشمل Pullback أو الشموع)"""
    if bypass_cache:
        result = get_price_data(pair, interval, bypass_cache=True)
    else:
        result = get_cached_data(pair, interval) or get_price_data(pair, interval)

    if not result:
        return None

    closes, highs, lows, opens = result

    rsi = calc_rsi(closes)
    macd, signal = calc_macd(closes)
    atr = calc_atr(highs, lows, closes)

    if rsi is None or macd is None or atr is None:
        return None

    current_price = closes[-1]

    # بالنسبة للفريمات الكبيرة (1H و 4H): تحليل الاتجاه العام الكامل
    if interval in ["1h", "4h"]:
        ema200 = calc_ema(closes, 200)
        if ema200 is None:
            return None
        trend = get_trend_structure(closes)
        support, resistance = get_support_resistance(highs, lows)
        resistance_distance = abs(resistance - current_price)
        support_distance = abs(current_price - support)
        sr_threshold = round(atr * 1.2, 6)

        # شروط الشراء الكاملة للفريمات الكبيرة
        buy_checks = {
            "RSI":    True,
            "MACD":   macd > signal,
            "EMA200": current_price > ema200,
            "Trend":  trend == "UP",
        }
        buy_ready = sum(buy_checks.values()) == 4 and (resistance_distance > sr_threshold)

        # شروط البيع الكاملة للفريمات الكبيرة
        sell_checks = {
            "RSI":    True,
            "MACD":   macd < signal,
            "EMA200": current_price < ema200,
            "Trend":  trend == "DOWN",
        }
        sell_ready = sum(sell_checks.values()) == 4 and (support_distance > sr_threshold)

        if buy_ready:
            return {"direction": "BUY", "rsi": rsi, "atr": atr, "price": current_price}
        elif sell_ready:
            return {"direction": "SELL", "rsi": rsi, "atr": atr, "price": current_price}

    # بالنسبة لفريم الدخول (15min): التحقق من الـ MACD فقط (مع الـ Pullback وشمعة التأكيد لاحقاً)
    elif interval == "15min":
        buy_ready = macd > signal
        sell_ready = macd < signal

        if buy_ready:
            return {"direction": "BUY", "rsi": rsi, "atr": atr, "price": current_price}
        elif sell_ready:
            return {"direction": "SELL", "rsi": rsi, "atr": atr, "price": current_price}

    return None

def check_setup_alignment(pair, bypass_cache=False):
    """تحديد ما إذا كان هناك Setup متوافق على الأطر الزمنية"""
    results = {}
    for tf in ["15min", "1h", "4h"]:
        res = analyze_timeframe(pair, tf, bypass_cache=bypass_cache)
        if res:
            results[tf] = res

    # فريم 15min إلزامي دائمًا كقاعدة للـ Setup
    if "15min" not in results:
        return None

    m15_direction = results["15min"]["direction"]

    align_1h = ("1h" in results and results["1h"]["direction"] == m15_direction)
    align_4h = ("4h" in results and results["4h"]["direction"] == m15_direction)

    # يجب أن تتوافق 15min مع 1H أو 4H على الأقل لفتح صفقة
    if not (align_1h or align_4h):
        return None

    confirmed_tfs = ["15min"]
    if align_1h:
        confirmed_tfs.append("1h")
    if align_4h:
        confirmed_tfs.append("4h")

    return {
        "direction": m15_direction,
        "confirmed_tfs": confirmed_tfs,
        "details": results
    }

def analyze_pair(pair, bypass_cache=False):
    """الدالة الرئيسية لفحص وتأكيد الدخول بناءً على Setup ثم الـ Trigger"""
    global active_setups

    # 1. المرحلة الأولى: فحص وتحديث توافق الـ Setup
    setup = check_setup_alignment(pair, bypass_cache=bypass_cache)

    if setup:
        # إذا تحقق التوافق، يتم حفظ أو تحديث الـ Setup النشط
        active_setups[pair] = {
            "direction": setup["direction"],
            "time": time.time(),
            "confirmed_tfs": setup["confirmed_tfs"],
            "details": setup["details"]
        }
    else:
        # إذا فقد أي شرط أساسي، يتم إلغاء الـ Setup فوراً
        active_setups.pop(pair, None)
        return None

    # 2. المرحلة الثانية: مراقبة الـ Trigger على فريم 15min فقط
    setup_data = active_setups[pair]
    setup_direction = setup_data["direction"]

    if bypass_cache:
        result_15 = get_price_data(pair, "15min", bypass_cache=True)
    else:
        result_15 = get_cached_data(pair, "15min") or get_price_data(pair, "15min")

    if not result_15:
        return None

    closes, highs, lows, opens = result_15
    atr = calc_atr(highs, lows, closes)
    ema200_series = calc_ema_series(closes, 200)

    if not atr or not ema200_series:
        return None

    # فحص الـ Pullback إلى EMA200 (Touch or Near في الشموع الـ 3 الأخيرة)
    pullback_ok = False
    near_threshold = 0.25 * atr
    for i in [-1, -2, -3]:
        if abs(i) <= len(closes):
            ema_i = ema200_series[i]
            high_i = highs[i]
            low_i = lows[i]
            close_i = closes[i]
            touch = (low_i <= ema_i <= high_i)
            near = (abs(close_i - ema_i) <= near_threshold) or (abs(high_i - ema_i) <= near_threshold) or (abs(low_i - ema_i) <= near_threshold)
            if touch or near:
                pullback_ok = True
                break

    # فحص شمعة التأكيد (Confirmation Candle)
    confirmed_ok = check_confirmation_candle(closes, highs, lows, setup_direction)

    # إذا لم يتحقق الـ Trigger حتى الآن، ننتظر الدورة القادمة
    if not (pullback_ok and confirmed_ok):
        return None

    # عند تحقق الـ Setup والـ Trigger معاً، نمرر الصفقات لحساب الأهداف
    main_15 = setup_data["details"]["15min"]
    price = main_15["price"]
    atr = main_15["atr"]

    direction = "BUY 📈" if setup_direction == "BUY" else "SELL 📉"

    if "BUY" in direction:
        tp_distance = min(atr * 1.5, 0.00200)
        sl_distance = tp_distance / 1.5
        tp = round(price + tp_distance, 6)
        sl = round(price - sl_distance, 6)
    else:
        tp_distance = min(atr * 1.5, 0.00200)
        sl_distance = tp_distance / 1.5
        tp = round(price - tp_distance, 6)
        sl = round(price + sl_distance, 6)

    rr = round(abs(tp - price) / abs(sl - price), 2)

    return {
        "pair": pair,
        "direction": direction,
        "price": price,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "strength": len(setup_data["confirmed_tfs"]),
        "confirmed_tfs": setup_data["confirmed_tfs"],
        "details": setup_data["details"]
    }

def get_debug_report(pair):
    """تقرير المراقبة وتحديث مراحل الـ State Machine"""
    result_15 = get_cached_data(pair, "15min") or get_price_data(pair, "15min")
    result_1h = get_cached_data(pair, "1h") or get_price_data(pair, "1h")
    result_4h = get_cached_data(pair, "4h") or get_price_data(pair, "4h")

    if not result_15:
        return f"🔍 {pair} - Market Status Report\n━━━━━━━━━━━━━━━━\n⚠️ فريم 15min خالي من البيانات حالياً."

    closes, highs, lows, opens = result_15
    current_price = closes[-1]

    lines = [f"🔍 {pair} - Market Status Report", "━━━━━━━━━━━━━━━━"]

    # ==================== 15min (Entry Logic) ====================
    lines.append("15min (Entry Logic)")
    state_key = f"{pair}_15min"
    state = active_setups.get(pair, {})
    direction = state.get("direction", None)

    # دالة مساعدة لإنشاء Checklist لكل اتجاه
    def build_checklist(for_dir):
        if direction == for_dir:
            c_setup = "✅ Trend Setup: Aligned"
            # للـ Pullback والـ Candle، كنجيبوهم من الـ analyze_pair الحالية
            closes_15, highs_15, lows_15, opens_15 = result_15
            atr = calc_atr(highs_15, lows_15, closes_15)
            ema200_series = calc_ema_series(closes_15, 200)
            
            pullback_ok = False
            if atr and ema200_series:
                near_threshold = 0.25 * atr
                for i in [-1, -2, -3]:
                    if abs(i) <= len(closes_15):
                        ema_i = ema200_series[i]
                        high_i = highs_15[i]
                        low_i = lows_15[i]
                        close_i = closes_15[i]
                        touch = (low_i <= ema_i <= high_i)
                        near = (abs(close_i - ema_i) <= near_threshold) or (abs(high_i - ema_i) <= near_threshold) or (abs(low_i - ema_i) <= near_threshold)
                        if touch or near:
                            pullback_ok = True
                            break
            
            confirmed_ok = check_confirmation_candle(closes_15, highs_15, lows_15, direction)
            
            c_pb = "✅ Pullback: Touched/Near" if pullback_ok else "⏳ Pullback: Waiting"
            c_candle = "✅ Candle Conf: Confirmed" if confirmed_ok else "⏳ Candle Conf: Waiting"
            
            score = 1 + (1 if pullback_ok else 0) + (1 if confirmed_ok else 0)
        else:
            c_setup = "❌ Trend Setup: No Setup"
            c_pb = "❌ Pullback: Waiting"
            c_candle = "❌ Candle Conf: Waiting"
            score = 0

        return [
            f"{c_setup}",
            f"{c_pb}",
            f"{c_candle}",
            f"Score: {score}/3"
        ]

    lines.append("BUY")
    lines.extend(build_checklist("BUY"))
    lines.append("")
    lines.append("SELL")
    lines.extend(build_checklist("SELL"))
    lines.append("━━━━━━━━━━━━━━━━")

    # ==================== 1H (Trend Context) ====================
    lines.append("1H (Trend Context)")
    if result_1h:
        closes_1h, _, _, _ = result_1h
        ema_1h = calc_ema(closes_1h, 200)
        trend_1h = get_trend_structure(closes_1h)
        price_1h = closes_1h[-1]

        # BUY 1H
        b_ema = price_1h > ema_1h if ema_1h else False
        b_trend = trend_1h == "UP"
        b_score = (2 if b_ema and b_trend else (1 if b_ema or b_trend else 0))
        lines.append("BUY")
        if b_ema:
            lines.append(f"✅ EMA200: Price ({price_1h}) &gt; EMA ({round(ema_1h, 5) if ema_1h else 0})")
        else:
            lines.append(f"❌ EMA200: Price &lt; EMA ({round(ema_1h, 5) if ema_1h else 0}) (Bullish ❌)")
        
        if b_trend:
            lines.append("✅ Trend Structure: UP (Higher Highs)")
        else:
            lines.append(f"❌ Trend Structure: {trend_1h} (Bullish ❌)")
        lines.append(f"Score: {b_score}/2")

        # SELL 1H
        s_ema = price_1h < ema_1h if ema_1h else False
        s_trend = trend_1h == "DOWN"
        s_score = (2 if s_ema and s_trend else (1 if s_ema or s_trend else 0))
        lines.append("")
        lines.append("SELL")
        if s_ema:
            lines.append(f"✅ EMA200: Price ({price_1h}) &lt; EMA ({round(ema_1h, 5) if ema_1h else 0})")
        else:
            lines.append(f"❌ EMA200: Price &gt; EMA ({round(ema_1h, 5) if ema_1h else 0}) (Bearish ❌)")
        
        if s_trend:
            lines.append("✅ Trend Structure: DOWN (Lower Highs)")
        else:
            lines.append(f"❌ Trend Structure: {trend_1h} (Bearish ❌)")
            lines.append(f"Score: {s_score}/2")
    else:
        lines.append("⚠️ فريم 1H خالي من البيانات حالياً.")
    lines.append("━━━━━━━━━━━━━━━━")

    # ==================== 4H (Major Trend) ====================
    lines.append("4H (Major Trend)")
    if result_4h:
        closes_4h, _, _, _ = result_4h
        ema_4h = calc_ema(closes_4h, 200)
        trend_4h = get_trend_structure(closes_4h)
        price_4h = closes_4h[-1]

        # BUY 4H
        b_ema = price_4h > ema_4h if ema_4h else False
        b_trend = trend_4h == "UP"
        b_score = (2 if b_ema and b_trend else (1 if b_ema or b_trend else 0))
        lines.append("BUY")
        if b_ema:
            lines.append("✅ EMA200: Price &gt; EMA200")
        else:
            lines.append("❌ EMA200: Price &lt; EMA200 (Bullish ❌)")
        
        if b_trend:
            lines.append("✅ Trend Structure: UP")
        else:
            lines.append(f"❌ Trend Structure: {trend_4h} (Bullish ❌)")
        lines.append(f"Score: {b_score}/2")

        # SELL 4H
        s_ema = price_4h < ema_4h if ema_4h else False
        s_trend = trend_4h == "DOWN"
        s_score = (2 if s_ema and s_trend else (1 if s_ema or s_trend else 0))
        lines.append("")
        lines.append("SELL")
        if s_ema:
            lines.append("✅ EMA200: Price &lt; EMA200")
        else:
            lines.append("❌ EMA200: Price &gt; EMA200 (Bearish ❌)")
        
        if s_trend:
            lines.append("✅ Trend Structure: DOWN")
        else:
            lines.append(f"❌ Trend Structure: {trend_4h} (Bearish ❌)")
        lines.append(f"Score: {s_score}/2")
    else:
        lines.append("⚠️ فريم 4H خالي من البيانات حالياً.")
    lines.append("━━━━━━━━━━━━━━━━")

    # ==================== Overall Status ====================
    status_label = "⏳ Waiting for 15min Setup Alignment"
    strength_label = "⭐ Bronze (No Setup)"
    
    if direction:
        status_label = f"⏳ Setup Active ({direction}) — Monitoring Pullback/Candle"

        # Stars Rating
        h1_bias = get_timeframe_bias(pair, "1h")
        h4_bias = get_timeframe_bias(pair, "4h")
        confirmed_count = 1
        if h1_bias == direction:
            confirmed_count += 1
        if h4_bias == direction:
            confirmed_count += 1

        if confirmed_count == 3:
            strength_label = "⭐⭐⭐ Gold (4H + 1H + 15m Alignment OK)"
        elif confirmed_count == 2:
            strength_label = "⭐⭐ Silver (1H + 15m Alignment OK)"
        else:
            strength_label = "⭐ Bronze (15m only)"

    lines.append(f"Overall Status: {status_label}")
    lines.append(f"Strength: {strength_label}")
    lines.append(f"Price: {current_price}")

    return "\n".join(lines)

def get_strength_label(strength):
    if strength == 3:
        return "⭐⭐⭐ Gold (4H + 1H + 15m)"
    elif strength == 2:
        return "⭐⭐ Silver (1H + 15m)"
    return "⭐ Bronze (15m only)"

def check_pre_signal(pair, rsi_15):
    """كيشوف واش RSI ديال 15min + 1h كيقتربو من منطقة الإشارة"""
    result_1h = get_cached_data(pair, "1h") or get_price_data(pair, "1h")
    if not result_1h:
        return None, None
    rsi_1h = calc_rsi(result_1h[0])
    if not rsi_1h:
        return None, None

    if 55 <= rsi_15 <= 59 and 55 <= rsi_1h <= 65:
        return "SELL", rsi_15
    elif 40 <= rsi_15 <= 45 and 35 <= rsi_1h <= 45:
        return "BUY", rsi_15
    return None, None

def pull_from_github():
    if not GH_TOKEN or not GITHUB_REPO:
        return []
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{OPPORTUNITIES_FILE}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return []
    content = base64.b64decode(r.json()["content"]).decode()
    try:
        return json.loads(content)
    except:
        return []

def push_to_github(opportunities):
    if not GH_TOKEN or not GITHUB_REPO:
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{OPPORTUNITIES_FILE}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    r = requests.get(url, headers=headers)
    sha = r.json().get("sha", "") if r.status_code == 200 else ""
    content = json.dumps(opportunities, ensure_ascii=False, indent=2)
    encoded = base64.b64encode(content.encode()).decode()
    payload = {"message": "update opportunities", "content": encoded, "sha": sha}
    requests.put(url, headers=headers, json=payload)

def monitor_trade(trade):
    global waiting_confirmation, pending_trades
    pair = trade["pair"]

    for i in range(3):
        time.sleep(600)  # كل 10 دقائق
        if not waiting_confirmation.get(pair):
            return

        result = get_price_data(pair)
        if not result:
            continue
        closes = result[0]
        current_price = closes[-1]

        if "BUY" in trade["direction"]:
            progress = "📈 السوق ماشي فالاتجاه الصح" if current_price > trade["price"] else "⚠️ السوق راجع شوية"
        else:
            progress = "📈 السوق ماشي فالاتجاه الصح" if current_price < trade["price"] else "⚠️ السوق راجع شوية"

        remaining = 20 - (i + 1) * 10
        send_telegram(
            f"🔄 <b>تحديث — {trade['pair']}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{progress}\n"
            f"💰 السعر دابا: <b>{current_price}</b>\n"
            f"⏳ باقي: <b>{remaining} دقيقة</b>\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )

    if waiting_confirmation.get(pair):
        result = get_price_data(pair)
        current_price = result[0][-1] if result else trade["price"]
        send_telegram(
            f"🎯 <b>وقت الدخول — {pair}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"الإشارة باقية قوية ✅\n"
            f"💰 السعر دابا: <b>{current_price}</b>\n"
            f"🎯 TP: <b>{trade['tp']}</b>\n"
            f"🛑 SL: <b>{trade['sl']}</b>\n"
            f"⚖️ R/R: <b>1:{trade['rr']}</b>\n\n"
            f"واش واجد تدخل؟ 🚀\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
    waiting_confirmation[pair] = False
    pending_trades.pop(pair, None)

class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def do_POST(self):
        global waiting_confirmation, pending_trades
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)
        self.send_response(200)
        self.end_headers()

        try:
            update = json.loads(body)

            if "callback_query" in update:
                cb = update["callback_query"]
                data = cb.get("data", "")
                answer_callback(cb["id"])

                if "_" in data:
                    action, pair_key = data.split("_", 1)
                    pair = next((p for p in pending_trades if p.replace("/", "") == pair_key), None)
                else:
                    action, pair = data, None

                if action == "yes" and pair and pair in pending_trades:
                    waiting_confirmation[pair] = True
                    trade = pending_trades[pair].copy()
                    send_telegram(
                        f"✅ <b>واخا! غادي نراقب التريد 30 دقيقة</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"غادي نبعت ليك تحديث كل 10 دقائق 👀\n"
                        f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
                    )
                    t = threading.Thread(target=monitor_trade, args=(trade,))
                    t.daemon = True
                    t.start()

                elif action == "no" and pair:
                    pending_trades.pop(pair, None)
                    waiting_confirmation[pair] = False
                    send_telegram("❌ واخا، تجاوزنا هاد التريد. غادي نكملو نراقبو السوق 👀")

        except Exception as e:
            print(f"Webhook error: {e}")

    def log_message(self, format, *args):
        pass

def run_server():
    server = HTTPServer(('0.0.0.0', PORT), WebhookHandler)
    print(f"Server running on port {PORT}")
    server.serve_forever()

def send_hourly_report(pairs_status):
    """كيبعت تقرير كل ساعة عن حالة السوق"""
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    msg = f"🕐 <b>تقرير السوق — {now_str}</b>\n━━━━━━━━━━━━━━━━\n"

    for pair, status in pairs_status.items():
        market = status.get("market")
        rsi_15 = status.get("rsi_15")
        reason = status.get("reason")

        if market:
            msg += (
                f"\n💱 <b>{pair}</b>\n"
                f"  {market['direction_emoji']} اليوم: {market['change_pct']:+.3f}% | "
                f"{market['last_hour_emoji']} آخر ساعة: {market['last_hour_change']:+.6f}\n"
            )
        else:
            msg += f"\n💱 <b>{pair}</b>\n"

        if rsi_15:
            msg += f"  📊 RSI(15min): {rsi_15}\n"

        if reason:
            msg += f"  🔍 {reason}\n"

    # أخبار اليوم
    all_news = []
    for pair in pairs_status:
        news = get_news_summary(pair)
        for n in news:
            if n not in all_news:
                all_news.append(n)

    if all_news:
        msg += f"\n📰 <b>أخبار اليوم:</b>\n"
        msg += "\n".join([f"  {n}" for n in all_news[:5]])
        msg += "\n"

    msg += f"\n━━━━━━━━━━━━━━━━\n⏳ باقي مراقب السوق..."
    send_telegram(msg)

def main_loop():
    global pending_trade, waiting_confirmation, active_setups
    time.sleep(5)
    set_webhook()

    try:
        opportunities = pull_from_github()
    except Exception:
        print("⚠️ pull_from_github failed at startup:")
        traceback.print_exc()
        opportunities = []

    last_report_hour = -1
    last_daily_report_date = None
    already_warned = {}

    while True:
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%H:%M UTC")

        try:
            today = now.strftime("%Y-%m-%d")

            if now.hour >= 21 and last_daily_report_date != today:
                last_daily_report_date = today
                today_ops = [o for o in opportunities if o.get("date", "").startswith(today)]

                if not today_ops:
                    send_telegram(
                        f"📊 <b>التقرير اليومي — {today}</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"ما كانت كاينة حتى فرصة اليوم\n"
                        f"🕐 {now_str}"
                    )
                else:
                    msg = f"📊 <b>التقرير اليومي — {today}</b>\n━━━━━━━━━━━━━━━━\n"
                    msg += f"📈 عدد الفرص: <b>{len(today_ops)}</b>\n\n"
                    for i, op in enumerate(today_ops, 1):
                        status = "🚫 ملغاة (news)" if op.get("cancelled") else "✅ أُرسلت"
                        msg += (
                            f"<b>{i}. {op['pair']}</b> — {op['direction']}\n"
                            f"   💰 {op['price']} | 🎯 {op['tp']} | 🛑 {op['sl']}\n"
                            f"   ⏱ {op['time']} | {status}\n\n"
                        )
                    msg += "━━━━━━━━━━━━━━━━\n⚠️ هاد المعلومات للتعلم فقط"
                    send_telegram(msg)

                time.sleep(900)
                continue

            # جلب البيانات بشكل مستمر ومحمي
            fetch_all_data()

            # تقرير كل ساعة دقيق ومحمي ضد زحف الدقائق (Drift)
            if now.hour != last_report_hour and not waiting_confirmation:
                last_report_hour = now.hour
                pairs_status = {}
                for pair in PAIRS:
                    market = get_market_summary(pair)
                    rsi_data = None
                    reason = None
                    result = get_cached_data(pair, "15min")
                    if result:
                        rsi_data = calc_rsi(result[0])
                        if rsi_data:
                            if 40 <= rsi_data <= 60:
                                reason = f"RSI = {rsi_data} — السوق محايد، مراقب..."
                            elif rsi_data < 40:
                                reason = f"RSI = {rsi_data} — قريب من منطقة BUY، مراقب MACD..."
                            else:
                                reason = f"RSI = {rsi_data} — قريب من منطقة SELL، مراقب MACD..."
                    pairs_status[pair] = {"market": market, "rsi_15": rsi_data, "reason": reason}
                send_hourly_report(pairs_status)

                # 🔍 DEBUG MODE
                for pair in PAIRS:
                    print(f"Starting debug for {pair}")
                    debug_text = get_debug_report(pair)
                    send_telegram(debug_text)
                    print(f"Debug sent for {pair}")

            # تحذير مسبق 15 دقيقة قبل الإشارة
            if not waiting_confirmation:
                for pair in PAIRS:
                    result = get_cached_data(pair, "15min")
                    if result:
                        rsi_current = calc_rsi(result[0])
                        if rsi_current:
                            direction, rsi_val = check_pre_signal(pair, rsi_current)
                            if direction:
                                if already_warned.get(pair) != direction:
                                    already_warned[pair] = direction
                                    direction_emoji = "📉 SELL" if direction == "SELL" else "📈 BUY"
                                    send_telegram(
                                        f"⚠️ <b>تحذير مسبق — {pair}</b>\n"
                                        f"━━━━━━━━━━━━━━━━\n"
                                        f"RSI = <b>{rsi_val}</b> — كيقترب من منطقة {direction_emoji}\n"
                                        f"⏳ كون مستعد — ممكن تجي إشارة فـ 15 دقيقة\n"
                                        f"🕐 {now_str}"
                                    )
                            else:
                                already_warned.pop(pair, None)

            if not waiting_confirmation:
                for pair in PAIRS:
                    # 1. فحص توافق الـ Setup والـ Trigger معاً (باستعمال الكاش)
                    trade = analyze_pair(pair, bypass_cache=False)
                    if not trade:
                        continue

                    # 🔄 المرحلة الرابعة: Final Recheck (فحص حقيقي للاتجاه بدون كاش لضمان بقائه صحيحاً)
                    print(f"🔄 Rechecking conditions for {pair} before sending...")
                    setup_direction = "BUY" if "BUY" in trade["direction"] else "SELL"
                    
                    # الـ Recheck يتحقق فقط من أن الاتجاه الأساسي مازال قائماً بدون إعادة فحص pullback أو شمعة التأكيد
                    recheck_setup = check_setup_alignment(pair, bypass_cache=True)
                    if not recheck_setup or recheck_setup["direction"] != setup_direction:
                        print(f"❌ Final Recheck failed or direction changed for {pair}. Cancelled.")
                        active_setups.pop(pair, None)
                        continue

                    danger_news, warning_news = get_high_impact_news(pair)

                    op = {
                        "date": now.strftime("%Y-%m-%d %H:%M"),
                        "time": now_str,
                        "pair": pair,
                        "direction": trade["direction"],
                        "price": trade["price"],
                        "tp": trade["tp"],
                        "sl": trade["sl"],
                        "rr": trade["rr"],
                        "strength": trade["strength"],
                        "cancelled": bool(danger_news)
                    }
                    opportunities.append(op)
                    push_to_github(opportunities)

                    if danger_news:
                        send_telegram(
                            f"⚠️ <b>تحذير — {pair}</b>\n"
                            f"━━━━━━━━━━━━━━━━\n"
                            f"كانت كاينة إشارة {trade['direction']} ولكن تم إلغاؤها:\n\n"
                            + "\n".join([f"🔴 {n}" for n in danger_news]) +
                            f"\n\n⏳ استنى تعدي الأخبار\n🕐 {now_str}"
                        )
                        active_setups.pop(pair, None)
                        continue

                    tfs_text = " + ".join(trade["confirmed_tfs"])
                    strength_text = get_strength_label(trade["strength"])
                    details_lines = "".join([f"  • {tf}: RSI {data['rsi']}\n" for tf, data in trade["details"].items()])

                    news_warning = ""
                    if warning_news:
                        news_warning = "\n⚠️ <b>أخبار قادمة:</b>\n" + "\n".join([f"🟡 {n}" for n in warning_news]) + "\n"

                    market = get_market_summary(trade['pair'])
                    today_news = get_news_summary(trade['pair'])

                    market_section = ""
                    if market:
                        market_section = (
                            f"\n📊 <b>السوق اليوم:</b>\n"
                            f"  {market['direction_emoji']} التغيير: {market['change']:+.6f} ({market['change_pct']:+.3f}%)\n"
                            f"  🔝 أعلى: {market['high_day']} | 🔻 أدنى: {market['low_day']}\n"
                            f"  {market['last_hour_emoji']} آخر ساعة: {market['last_hour_change']:+.6f}\n"
                        )

                    news_section = ""
                    if today_news:
                        news_section = f"\n📰 <b>أخبار اليوم:</b>\n" + "\n".join([f"  {n}" for n in today_news]) + "\n"

                    msg = (
                        f"🔔 <b>فرصة تريد — {trade['pair']}</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"📊 الإشارة: <b>{trade['direction']}</b>\n"
                        f"💪 القوة: <b>{strength_text}</b>\n"
                        f"⏱ مؤكدة على: <b>{tfs_text}</b>\n"
                        f"{market_section}"
                        f"{news_section}"
                        f"\n💰 السعر الحالي: <b>{trade['price']}</b>\n"
                        f"🎯 TP: <b>{trade['tp']}</b>\n"
                        f"🛑 SL: <b>{trade['sl']}</b>\n"
                        f"⚖️ R/R: <b>1:{trade['rr']}</b>\n\n"
                        f"📋 RSI Details:\n{details_lines}"
                        f"{news_warning}"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"🕐 {now_str}\n\n"
                        f"واش بغيتي تدخل هاد التريد؟"
                    )

                    pending_trade = trade
                    send_with_buttons(msg, trade)
                    active_setups.pop(pair, None)  # ريسيت للـ Setup بعد إرسال التنبيه للتنفيذ
                    break

        except Exception:
            print("Error in main_loop:")
            traceback.print_exc()

        time.sleep(900)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    main_loop()

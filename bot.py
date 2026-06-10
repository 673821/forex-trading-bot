import os
import requests
from datetime import datetime

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]
TIMEFRAMES = ["15min", "1h", "4h"]

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"})

def get_price_data(pair, interval="15min", outputsize=50):
    params = {
        "symbol": pair,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY
    }
    r = requests.get("https://api.twelvedata.com/time_series", params=params)
    data = r.json()
    if "values" not in data:
        return None
    closes = [float(v["close"]) for v in reversed(data["values"])]
    highs  = [float(v["high"])  for v in reversed(data["values"])]
    lows   = [float(v["low"])   for v in reversed(data["values"])]
    return closes, highs, lows

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

def analyze_timeframe(pair, interval):
    result = get_price_data(pair, interval)
    if not result:
        return None
    closes, highs, lows = result
    rsi = calc_rsi(closes)
    macd, signal = calc_macd(closes)
    atr = calc_atr(highs, lows, closes)
    if rsi is None or macd is None or atr is None:
        return None

    if rsi < 35 and macd > signal:
        return {"direction": "BUY", "rsi": rsi, "atr": atr, "price": closes[-1]}
    elif rsi > 65 and macd < signal:
        return {"direction": "SELL", "rsi": rsi, "atr": atr, "price": closes[-1]}
    return None

def analyze_pair(pair):
    results = {}
    for tf in TIMEFRAMES:
        res = analyze_timeframe(pair, tf)
        if res:
            results[tf] = res

    if len(results) < 2:
        return None

    directions = [r["direction"] for r in results.values()]
    if directions.count("BUY") >= 2:
        direction = "BUY 📈"
    elif directions.count("SELL") >= 2:
        direction = "SELL 📉"
    else:
        return None

    confirmed_tfs = [tf for tf, r in results.items() if r["direction"] in direction]
    main = list(results.values())[0]
    price = main["price"]
    atr = main["atr"]

    if "BUY" in direction:
        tp = round(price + atr * 1.5, 6)
        sl = round(price - atr, 6)
    else:
        tp = round(price - atr * 1.5, 6)
        sl = round(price + atr, 6)

    rr = round(abs(tp - price) / abs(sl - price), 2)
    strength = len(confirmed_tfs)

    return {
        "pair": pair,
        "direction": direction,
        "price": price,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "strength": strength,
        "confirmed_tfs": confirmed_tfs,
        "details": results
    }

def get_strength_label(strength):
    if strength == 3:
        return "⭐⭐⭐ قوية جداً"
    elif strength == 2:
        return "⭐⭐ متوسطة"
    return "⭐ ضعيفة"

def main():
    now = datetime.utcnow().strftime("%H:%M UTC")
    found = False

    for pair in PAIRS:
        trade = analyze_pair(pair)
        if not trade:
            continue

        found = True
        tfs_text = " + ".join(trade["confirmed_tfs"])
        strength_text = get_strength_label(trade["strength"])

        details_lines = ""
        for tf, data in trade["details"].items():
            details_lines += f"  • {tf}: RSI {data['rsi']}\n"

        msg = (
            f"🔔 <b>فرصة تريد — {trade['pair']}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 الإشارة: <b>{trade['direction']}</b>\n"
            f"💪 القوة: <b>{strength_text}</b>\n"
            f"⏱ مؤكدة على: <b>{tfs_text}</b>\n\n"
            f"💰 السعر الحالي: <b>{trade['price']}</b>\n"
            f"🎯 TP: <b>{trade['tp']}</b>\n"
            f"🛑 SL: <b>{trade['sl']}</b>\n"
            f"⚖️ R/R: <b>1:{trade['rr']}</b>\n\n"
            f"📋 التفاصيل:\n{details_lines}"
            f"━━━━━━━━━━━━━━━━\n"
            f"🕐 {now}\n"
            f"⚠️ هاد المعلومات للتعلم فقط"
        )
        send_telegram(msg)

    if not found:
        print(f"[{now}] ما كاينة حتى فرصة دابا")

if __name__ == "__main__":
    main()

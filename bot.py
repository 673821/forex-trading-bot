import os
import requests
from datetime import datetime

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"})

def get_price_data(pair, interval="15min", outputsize=50):
    symbol = pair.replace("/", "")
    url = f"https://api.twelvedata.com/time_series"
    params = {
        "symbol": pair,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY
    }
    r = requests.get(url, params=params)
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

def analyze_pair(pair):
    result = get_price_data(pair)
    if not result:
        return None
    closes, highs, lows = result

    rsi   = calc_rsi(closes)
    macd, signal = calc_macd(closes)
    atr   = calc_atr(highs, lows, closes)
    price = closes[-1]

    if rsi is None or macd is None or atr is None:
        return None

    signal_type = None
    reasons = []

    if rsi < 35 and macd > signal:
        signal_type = "BUY 📈"
        reasons.append(f"RSI منخفض ({rsi}) — السوق oversold")
        reasons.append("MACD كيتقاطع فوق السيغنال — momentum إيجابي")
    elif rsi > 65 and macd < signal:
        signal_type = "SELL 📉"
        reasons.append(f"RSI مرتفع ({rsi}) — السوق overbought")
        reasons.append("MACD كيتقاطع تحت السيغنال — momentum سلبي")

    if not signal_type:
        return None

    atr_multiplier = 1.5
    if signal_type.startswith("BUY"):
        tp = round(price + atr * atr_multiplier, 6)
        sl = round(price - atr, 6)
    else:
        tp = round(price - atr * atr_multiplier, 6)
        sl = round(price + atr, 6)

    rr = round(abs(tp - price) / abs(sl - price), 2)

    return {
        "pair": pair,
        "signal": signal_type,
        "price": price,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "rsi": rsi,
        "reasons": reasons
    }

def main():
    now = datetime.utcnow().strftime("%H:%M UTC")
    found = False

    for pair in PAIRS:
        trade = analyze_pair(pair)
        if not trade:
            continue

        found = True
        reasons_text = "\n".join([f"• {r}" in trade["reasons"] for r in trade["reasons"]])
        reasons_text = "\n".join([f"• {r}" for r in trade["reasons"]])

        msg = (
            f"🔔 <b>فرصة تريد — {trade['pair']}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 الإشارة: <b>{trade['signal']}</b>\n"
            f"💰 السعر الحالي: <b>{trade['price']}</b>\n\n"
            f"🎯 TP: <b>{trade['tp']}</b>\n"
            f"🛑 SL: <b>{trade['sl']}</b>\n"
            f"⚖️ R/R: <b>1:{trade['rr']}</b>\n\n"
            f"📋 الأسباب:\n{reasons_text}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🕐 {now}\n"
            f"⚠️ هاد المعلومات للتعلم فقط"
        )
        send_telegram(msg)

    if not found:
        print(f"[{now}] ما كاينة حتى فرصة دابا")

if __name__ == "__main__":
    main()

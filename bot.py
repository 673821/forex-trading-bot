import os
import json
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]
TIMEFRAMES = ["15min", "1h", "4h"]
OPPORTUNITIES_FILE = "opportunities.json"

PAIR_CURRENCIES = {
    "EUR/USD": ["EUR", "USD"],
    "GBP/USD": ["GBP", "USD"],
    "USD/JPY": ["USD", "JPY"]
}

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"})

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
    except:
        return [], []

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
    return {
        "pair": pair,
        "direction": direction,
        "price": price,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "strength": len(confirmed_tfs),
        "confirmed_tfs": confirmed_tfs,
        "details": results
    }

def get_strength_label(strength):
    if strength == 3:
        return "⭐⭐⭐ قوية جداً"
    elif strength == 2:
        return "⭐⭐ متوسطة"
    return "⭐ ضعيفة"

def load_opportunities():
    try:
        with open(OPPORTUNITIES_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_opportunities(data):
    with open(OPPORTUNITIES_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def push_to_github(content):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{OPPORTUNITIES_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    r = requests.get(url, headers=headers)
    sha = r.json().get("sha", "") if r.status_code == 200 else ""
    import base64
    encoded = base64.b64encode(content.encode()).decode()
    payload = {
        "message": "update opportunities",
        "content": encoded,
        "sha": sha
    }
    requests.put(url, headers=headers, json=payload)

def pull_from_github():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return []
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{OPPORTUNITIES_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return []
    import base64
    content = base64.b64decode(r.json()["content"]).decode()
    try:
        return json.loads(content)
    except:
        return []

def send_daily_report(opportunities):
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    today_ops = [o for o in opportunities if o.get("date", "").startswith(today)]

    if not today_ops:
        send_telegram(
            f"📊 <b>التقرير اليومي — {today}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"ما كانت كاينة حتى فرصة اليوم\n"
            f"🕐 {now.strftime('%H:%M UTC')}"
        )
        return

    msg = f"📊 <b>التقرير اليومي — {today}</b>\n━━━━━━━━━━━━━━━━\n"
    msg += f"📈 عدد الفرص: <b>{len(today_ops)}</b>\n\n"

    for i, op in enumerate(today_ops, 1):
        status = "🚫 ملغاة (news)" if op.get("cancelled") else "✅ أُرسلت"
        msg += (
            f"<b>{i}. {op['pair']}</b> — {op['direction']}\n"
            f"   💰 {op['price']} | 🎯 {op['tp']} | 🛑 {op['sl']}\n"
            f"   ⏱ {op['time']} | {status}\n\n"
        )

    msg += f"━━━━━━━━━━━━━━━━\n⚠️ هاد المعلومات للتعلم فقط"
    send_telegram(msg)

def main():
    now = datetime.utcnow()
    now_str = now.strftime("%H:%M UTC")

    opportunities = pull_from_github()

    # تقرير يومي فـ 21:00 UTC
    if now.hour == 2 and now.minute >= 41:
        send_daily_report(opportunities)
        # نمسحو الفرص القديمة (أكثر من 7 أيام)
        from datetime import timedelta
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        opportunities = [o for o in opportunities if o.get("date", "") >= cutoff]
        content = json.dumps(opportunities, ensure_ascii=False, indent=2)
        push_to_github(content)
        return

    found = False

    for pair in PAIRS:
        trade = analyze_pair(pair)
        if not trade:
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

        if danger_news:
            print(f"[{now_str}] {pair} — إشارة موجودة ولكن news خطير، تم تجاهلها")
            msg = (
                f"⚠️ <b>تحذير — {pair}</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"كانت كاينة إشارة {trade['direction']} ولكن تم إلغاؤها بسبب أخبار خطيرة:\n\n"
                + "\n".join([f"🔴 {n}" for n in danger_news]) +
                f"\n\n⏳ استنى تعدي الأخبار قبل ما تدخل أي تريد\n"
                f"🕐 {now_str}"
            )
            send_telegram(msg)
            continue

        found = True
        tfs_text = " + ".join(trade["confirmed_tfs"])
        strength_text = get_strength_label(trade["strength"])
        details_lines = ""
        for tf, data in trade["details"].items():
            details_lines += f"  • {tf}: RSI {data['rsi']}\n"

        news_warning = ""
        if warning_news:
            news_warning = "\n⚠️ <b>انتبه — أخبار قادمة:</b>\n"
            news_warning += "\n".join([f"🟡 {n}" for n in warning_news])
            news_warning += "\n"

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
            f"{news_warning}"
            f"━━━━━━━━━━━━━━━━\n"
            f"🕐 {now_str}\n"
            f"⚠️ هاد المعلومات للتعلم فقط"
        )
        send_telegram(msg)

    content = json.dumps(opportunities, ensure_ascii=False, indent=2)
    push_to_github(content)

    if not found:
        print(f"[{now_str}] ما كاينة حتى فرصة دابا")

if __name__ == "__main__":
    main()

import os, time, requests, json, smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ALPHA_KEY        = os.environ.get("ALPHA_VANTAGE_KEY", "")

WATCHLIST = WATCHLIST = {
    "VOO":   {"name": "Vanguard S&P 500 ETF",    "type": "ETF",   "expense": 0.03},
    "VUG":   {"name": "Vanguard Growth ETF",       "type": "ETF",   "expense": 0.03},
    "VGT":   {"name": "Vanguard Info Tech ETF",    "type": "ETF",   "expense": 0.09},
    "QQQ":   {"name": "Invesco NASDAQ 100",        "type": "ETF",   "expense": 0.20},
    "SCHD":  {"name": "Schwab Dividend ETF",       "type": "ETF",   "expense": 0.06},
    "AAPL":  {"name": "Apple Inc",                 "type": "STOCK", "sector": "Consumer Tech"},
    "GOOGL": {"name": "Alphabet Inc",              "type": "STOCK", "sector": "AI/Ads"},
    "AMZN":  {"name": "Amazon",                    "type": "STOCK", "sector": "Cloud/Retail"},
    "META":  {"name": "Meta Platforms",            "type": "STOCK", "sector": "AI/Social"},
    "MSFT":  {"name": "Microsoft Corp",            "type": "STOCK", "sector": "Cloud/AI"},
    "NVDA":  {"name": "NVIDIA Corp",               "type": "STOCK", "sector": "AI Chips"},
    "TSLA":  {"name": "Tesla Inc",                 "type": "STOCK", "sector": "EV/Energy"},
    "NFLX":  {"name": "Netflix Inc",               "type": "STOCK", "sector": "Streaming"},
}

def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        print("No Telegram token")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        print(f"Telegram: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

def get_prices(ticker):
    url = (f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED"
           f"&symbol={ticker}&outputsize=full&apikey={ALPHA_KEY}")
    try:
        r = requests.get(url, timeout=20)
        data = r.json()
        ts = data.get("Time Series (Daily)", {})
        if not ts:
            print(f"  No data for {ticker}: {list(data.keys())}")
            return []
        return [float(v["5. adjusted close"]) for v in list(ts.values())]
    except Exception as e:
        print(f"  Price error {ticker}: {e}")
        return []

def get_rsi(ticker):
    url = (f"https://www.alphavantage.co/query?function=RSI"
           f"&symbol={ticker}&interval=daily&time_period=14"
           f"&series_type=close&apikey={ALPHA_KEY}")
    try:
        r = requests.get(url, timeout=20)
        data = r.json()
        analysis = data.get("Technical Analysis: RSI", {})
        if not analysis:
            return None
        latest = sorted(analysis.keys())[-1]
        return float(analysis[latest]["RSI"])
    except Exception as e:
        print(f"  RSI error {ticker}: {e}")
        return None

def analyse(ticker):
    signals = []
    print(f"Scanning {ticker}...")
    prices = get_prices(ticker)
    if len(prices) < 50:
        print(f"  Not enough data for {ticker}")
        return signals

    price = prices[0]
    ath = max(prices[:252])
    drawdown = ((ath - price) / ath) * 100

    # SMA 200
    if len(prices) >= 200:
        sma200 = sum(prices[:200]) / 200
        if price < sma200:
            pct = ((sma200 - price) / sma200) * 100
            signals.append(f"🟢 {ticker}: BELOW SMA200 — STRONG BUY\nPrice ${price:.2f} is {pct:.1f}% below 200-day MA (${sma200:.2f})")

    # SMA 300
    if len(prices) >= 300:
        sma300 = sum(prices[:300]) / 300
        if price < sma300:
            pct = ((sma300 - price) / sma300) * 100
            signals.append(f"🟩 {ticker}: BELOW SMA300 — MAXIMUM BUY\nPrice ${price:.2f} is {pct:.1f}% below 300-day MA (${sma300:.2f}). Generational entry.")

    time.sleep(15)  # rate limit

    # RSI
    rsi = get_rsi(ticker)
    time.sleep(15)  # rate limit
    if rsi and rsi < 30:
        signals.append(f"🔵 {ticker}: RSI {rsi:.1f} — OVERSOLD STRONG BUY\nRSI below 30. High probability mean reversion.")

    # Drawdown
    if drawdown >= 20:
        signals.append(f"🚨 {ticker}: {drawdown:.1f}% DRAWDOWN — DEPLOY LUMP SUM\nPrice ${price:.2f} is {drawdown:.1f}% off ATH (${ath:.2f})")
    elif drawdown >= 10:
        signals.append(f"🟡 {ticker}: {drawdown:.1f}% PULLBACK — ADD POSITION\nPrice ${price:.2f} is {drawdown:.1f}% off ATH (${ath:.2f})")
    elif drawdown < 2:
        signals.append(f"⚪ {ticker}: NEAR ATH — DCA ONLY\nPrice ${price:.2f} within {drawdown:.1f}% of ATH. No lump sum.")

    return signals

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M SGT")
    print(f"=== Stock Signal Bot starting {now} ===")
    print(f"Token set: {bool(TELEGRAM_TOKEN)}")
    print(f"Chat ID set: {bool(TELEGRAM_CHAT_ID)}")
    print(f"Alpha Vantage key set: {bool(ALPHA_KEY)}")

    if not ALPHA_KEY:
        send_telegram("⚠️ Bot error: ALPHA_VANTAGE_KEY secret not set in GitHub.")
        return

    all_signals = []

    for ticker in WATCHLIST:
        sigs = analyse(ticker)
        all_signals.extend(sigs)
        time.sleep(15)

    # Build digest message
    buy_sigs = [s for s in all_signals if any(x in s for x in ["BUY", "DRAWDOWN", "PULLBACK"])]
    hold_sigs = [s for s in all_signals if "ATH" in s]

    lines = [
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 RETIREMENT ALPHA BOT",
        f"🕐 {now}",
        f"🎯 Target: $3M by age 50",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    if buy_sigs:
        lines.append(f"🟢 BUY SIGNALS ({len(buy_sigs)}):")
        for s in buy_sigs:
            lines.append(s)
            lines.append("")
    
    if hold_sigs:
        lines.append(f"⚪ HOLD / DCA ONLY:")
        for s in hold_sigs:
            lines.append(s)
            lines.append("")

    if not buy_sigs and not hold_sigs:
        lines.append("✅ No major signals today.")
        lines.append("Stay the course. DCA as scheduled.")

    lines += [
        "",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📋 Watching: {', '.join(WATCHLIST)}",
    ]

    msg = "\n".join(lines)
    print(msg)
    send_telegram(msg)

    # Save log
    with open("signal_log.json", "w") as f:
        json.dump({"timestamp": now, "signals": all_signals}, f, indent=2)

    print(f"Done. {len(all_signals)} signals found, {len(buy_sigs)} buy signals.")

if __name__ == "__main__":
    main()

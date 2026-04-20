import os, time, requests, json
from datetime import datetime

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ALPHA_KEY        = os.environ.get("ALPHA_VANTAGE_KEY", "")

WATCHLIST = {
    "VOO":   {"name": "Vanguard S&P 500 ETF",    "type": "ETF",   "expense": 0.03,  "pe_sector": 21},
    "VUG":   {"name": "Vanguard Growth ETF",       "type": "ETF",   "expense": 0.03,  "pe_sector": 24},
    "VGT":   {"name": "Vanguard Info Tech ETF",    "type": "ETF",   "expense": 0.09,  "pe_sector": 28},
    "QQQ":   {"name": "Invesco NASDAQ 100",        "type": "ETF",   "expense": 0.20,  "pe_sector": 26},
    "SCHD":  {"name": "Schwab Dividend ETF",       "type": "ETF",   "expense": 0.06,  "pe_sector": 18},
    "AAPL":  {"name": "Apple Inc",                 "type": "STOCK", "sector": "Consumer Tech", "pe_sector": 26},
    "GOOGL": {"name": "Alphabet Inc",              "type": "STOCK", "sector": "AI/Ads",        "pe_sector": 28},
    "AMZN":  {"name": "Amazon",                    "type": "STOCK", "sector": "Cloud/Retail",  "pe_sector": 35},
    "META":  {"name": "Meta Platforms",            "type": "STOCK", "sector": "AI/Social",     "pe_sector": 28},
    "MSFT":  {"name": "Microsoft Corp",            "type": "STOCK", "sector": "Cloud/AI",      "pe_sector": 30},
    "NVDA":  {"name": "NVIDIA Corp",               "type": "STOCK", "sector": "AI Chips",      "pe_sector": 40},
    "TSLA":  {"name": "Tesla Inc",                 "type": "STOCK", "sector": "EV/Energy",     "pe_sector": 25},
    "NFLX":  {"name": "Netflix Inc",               "type": "STOCK", "sector": "Streaming",     "pe_sector": 35},
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
    """Fetch daily prices from Alpha Vantage. Returns list of closes, newest first."""
    url = (f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED"
           f"&symbol={ticker}&outputsize=full&apikey={ALPHA_KEY}")
    try:
        r = requests.get(url, timeout=20)
        data = r.json()
        ts = data.get("Time Series (Daily)", {})
        if not ts:
            print(f"  No data for {ticker}: {list(data.keys())}")
            return []
        # Sort by date descending (newest first)
        sorted_dates = sorted(ts.keys(), reverse=True)
        return [float(ts[d]["5. adjusted close"]) for d in sorted_dates]
    except Exception as e:
        print(f"  Price error {ticker}: {e}")
        return []

def calc_weekly_rsi(daily_prices, period=14):
    """
    Calculate weekly RSI-14 from daily prices using Wilder smoothing.
    Converts daily closes to weekly closes first (every 5 trading days).
    This matches TradingView weekly RSI with length=14, smoothing=1.
    """
    if len(daily_prices) < period * 5 + 5:
        return None

    # Convert daily to weekly (take every 5th price = weekly close)
    # daily_prices[0] is most recent, so we reverse to get oldest first
    daily_oldest_first = list(reversed(daily_prices))
    weekly = [daily_oldest_first[i] for i in range(0, len(daily_oldest_first), 5)]

    if len(weekly) < period + 1:
        return None

    # Calculate gains and losses
    gains, losses = [], []
    for i in range(1, len(weekly)):
        diff = weekly[i] - weekly[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    # Initial averages (first 14 periods)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing for remaining periods
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def get_dca_zone(rsi, drawdown):
    """Return DCA zone 1-4 and action description."""
    if rsi is not None and rsi < 35 and drawdown > 15:
        return 4, "Deploy lump sum — dual signal: oversold RSI + major drawdown"
    elif drawdown >= 20:
        return 4, "Deploy lump sum — 20%+ drawdown from ATH"
    elif rsi is not None and rsi < 35:
        return 4, "Deploy lump sum — RSI deeply oversold"
    elif rsi is not None and rsi < 45 and drawdown > 8:
        return 3, "Double your DCA — RSI approaching oversold + significant pullback"
    elif drawdown >= 10:
        return 3, "Double your DCA — 10%+ pullback from ATH"
    elif drawdown >= 5 or (rsi is not None and rsi < 50):
        return 2, "Increase DCA by 50% — mild pullback in progress"
    else:
        return 1, "Standard DCA only — no major signal"

def analyse(ticker, meta):
    """
    Fetch price data and compute all signals.
    Only 1 API call per ticker (price data).
    RSI calculated locally from price data — no extra API call needed.
    Total: 13 calls for 13 tickers. Well within Alpha Vantage free limit of 25/day.
    """
    signals = []
    print(f"Scanning {ticker}...")

    prices = get_prices(ticker)
    if len(prices) < 50:
        print(f"  Not enough data for {ticker}")
        return signals, None

    price      = prices[0]
    ath        = max(prices[:252])
    drawdown   = ((ath - price) / ath) * 100
    sma200     = sum(prices[:200]) / 200 if len(prices) >= 200 else None
    sma300     = sum(prices[:300]) / 300 if len(prices) >= 300 else None
    below_sma200 = price < sma200 if sma200 else False
    below_sma300 = price < sma300 if sma300 else False

    # 5-day (weekly) price dip
    weekly_drop = 0.0
    if len(prices) >= 5:
        price_5d_ago = prices[4]
        weekly_drop = ((price_5d_ago - price) / price_5d_ago) * 100

    # RSI calculated from daily prices — no separate API call
    rsi = calc_weekly_rsi(prices)
    print(f"  Price: ${price:.2f} | RSI-14w: {rsi} | Drawdown: {drawdown:.1f}%")

    dca_zone, dca_action = get_dca_zone(rsi, drawdown)

    # ── Build Telegram signal strings ──────────────────────────────────────────
    if below_sma200:
        pct = ((sma200 - price) / sma200) * 100
        signals.append(
            f"🟢 {ticker}: BELOW SMA200 — STRONG BUY\n"
            f"Price ${price:.2f} is {pct:.1f}% below 200-day MA (${sma200:.2f})"
        )
    if below_sma300:
        pct = ((sma300 - price) / sma300) * 100
        signals.append(
            f"🟩 {ticker}: BELOW SMA300 — MAXIMUM BUY\n"
            f"Price ${price:.2f} is {pct:.1f}% below 300-day MA (${sma300:.2f}). Generational entry."
        )
    if rsi is not None and rsi < 30:
        signals.append(
            f"🔵 {ticker}: RSI {rsi} — OVERSOLD STRONG BUY\n"
            f"Weekly RSI-14 below 30. High probability mean reversion."
        )
    if drawdown >= 20:
        signals.append(
            f"🚨 {ticker}: {drawdown:.1f}% DRAWDOWN — DEPLOY LUMP SUM\n"
            f"Price ${price:.2f} is {drawdown:.1f}% off ATH (${ath:.2f})"
        )
    elif drawdown >= 10:
        signals.append(
            f"🟡 {ticker}: {drawdown:.1f}% PULLBACK — ADD POSITION\n"
            f"Price ${price:.2f} is {drawdown:.1f}% off ATH (${ath:.2f})"
        )
    elif drawdown < 2:
        signals.append(
            f"⚪ {ticker}: NEAR ATH — DCA ONLY\n"
            f"Price ${price:.2f} within {drawdown:.1f}% of ATH. No lump sum."
        )
    if weekly_drop >= 15:
        signals.append(
            f"📉 {ticker}: {weekly_drop:.1f}% WEEKLY DIP — STRONG BUY\n"
            f"Price fell {weekly_drop:.1f}% in 5 days. Aggressive add opportunity."
        )
    elif weekly_drop >= 10:
        signals.append(
            f"🟡 {ticker}: {weekly_drop:.1f}% WEEKLY DIP — ADD POSITION\n"
            f"Price fell {weekly_drop:.1f}% in 5 days. Good DCA entry."
        )
    elif weekly_drop >= 5:
        signals.append(
            f"👀 {ticker}: {weekly_drop:.1f}% WEEKLY DIP — WATCH\n"
            f"Price fell {weekly_drop:.1f}% in 5 days. Monitor closely."
        )

    verdict = "bull" if dca_zone >= 3 else ("bear" if ticker == "TSLA" else "neutral")

    ticker_data = {
        "ticker":       ticker,
        "name":         meta["name"],
        "type":         meta["type"],
        "pe_sector":    meta.get("pe_sector", 25),
        "price":        round(price, 2),
        "ath":          round(ath, 2),
        "drawdown":     round(drawdown, 2),
        "weekly_drop":  round(weekly_drop, 2),
        "rsi":          rsi,
        "sma200":       round(sma200, 2) if sma200 else None,
        "sma300":       round(sma300, 2) if sma300 else None,
        "below_sma200": below_sma200,
        "below_sma300": below_sma300,
        "dca_zone":     dca_zone,
        "dca_action":   dca_action,
        "verdict":      verdict,
        "signal_count": len(signals),
    }
    return signals, ticker_data


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M SGT")
    print(f"=== Stock Signal Bot starting {now} ===")
    print(f"Alpha Vantage key set: {bool(ALPHA_KEY)}")
    print(f"Note: RSI calculated from price data. Only 1 API call per ticker = 13 total (free limit: 25/day)")

    if not ALPHA_KEY:
        send_telegram("⚠️ Bot error: ALPHA_VANTAGE_KEY secret not set in GitHub.")
        return

    all_signals    = []
    watchlist_data = []

    for ticker, meta in WATCHLIST.items():
        sigs, ticker_data = analyse(ticker, meta)
        all_signals.extend(sigs)
        if ticker_data:
            watchlist_data.append(ticker_data)
        time.sleep(13)  # ~1 call per 13s = stay under 5 calls/min free limit

    # ── Build Telegram digest ──────────────────────────────────────────────────
    buy_sigs  = [s for s in all_signals if any(x in s for x in ["BUY", "DRAWDOWN", "PULLBACK"])]
    hold_sigs = [s for s in all_signals if "ATH" in s]

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📊 RETIREMENT ALPHA BOT",
        f"🕐 {now}",
        "🎯 Target: $3M by age 50",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if buy_sigs:
        lines.append(f"🟢 BUY SIGNALS ({len(buy_sigs)}):")
        for s in buy_sigs:
            lines.append(s)
            lines.append("")
    if hold_sigs:
        lines.append("⚪ HOLD / DCA ONLY:")
        for s in hold_sigs:
            lines.append(s)
            lines.append("")
    if not buy_sigs and not hold_sigs:
        lines.append("✅ No major signals today.")
        lines.append("Stay the course. DCA as scheduled.")
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📋 Watching: {', '.join(WATCHLIST)}",
    ]

    msg = "\n".join(lines)
    print(msg)
    send_telegram(msg)

    # ── Save full data for dashboard ───────────────────────────────────────────
    output = {
        "timestamp": now,
        "signals":   all_signals,
        "watchlist": watchlist_data,
        "summary": {
            "total_tickers": len(watchlist_data),
            "buy_signals":   len(buy_sigs),
            "zone4_count":   sum(1 for w in watchlist_data if w["dca_zone"] == 4),
            "zone3_count":   sum(1 for w in watchlist_data if w["dca_zone"] == 3),
            "zone2_count":   sum(1 for w in watchlist_data if w["dca_zone"] == 2),
            "zone1_count":   sum(1 for w in watchlist_data if w["dca_zone"] == 1),
        }
    }
    with open("signal_log.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone. {len(all_signals)} signals, {len(buy_sigs)} buy signals.")
    print(f"Watchlist saved: {len(watchlist_data)} tickers.")
    print(f"API calls used: {len(WATCHLIST)} (one per ticker, within 25/day free limit).")

if __name__ == "__main__":
    main()

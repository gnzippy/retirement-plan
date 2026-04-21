import os, json, time
from datetime import datetime
import requests

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

WATCHLIST = {
    "VOO":   {"name": "Vanguard S&P 500 ETF",    "type": "ETF",   "pe_sector": 21},
    "VUG":   {"name": "Vanguard Growth ETF",       "type": "ETF",   "pe_sector": 24},
    "VGT":   {"name": "Vanguard Info Tech ETF",    "type": "ETF",   "pe_sector": 28},
    "QQQ":   {"name": "Invesco NASDAQ 100",        "type": "ETF",   "pe_sector": 26},
    "SCHD":  {"name": "Schwab Dividend ETF",       "type": "ETF",   "pe_sector": 18},
    "AAPL":  {"name": "Apple Inc",                 "type": "STOCK", "pe_sector": 26},
    "GOOGL": {"name": "Alphabet Inc",              "type": "STOCK", "pe_sector": 28},
    "AMZN":  {"name": "Amazon",                    "type": "STOCK", "pe_sector": 35},
    "META":  {"name": "Meta Platforms",            "type": "STOCK", "pe_sector": 28},
    "MSFT":  {"name": "Microsoft Corp",            "type": "STOCK", "pe_sector": 30},
    "NVDA":  {"name": "NVIDIA Corp",               "type": "STOCK", "pe_sector": 40},
    "TSLA":  {"name": "Tesla Inc",                 "type": "STOCK", "pe_sector": 25},
    "NFLX":  {"name": "Netflix Inc",               "type": "STOCK", "pe_sector": 35},
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

def get_data(ticker):
    """
    Fetch 2 years of daily adjusted close prices from Yahoo Finance.
    Uses adjclose which is split and dividend adjusted — all prices
    on the same scale regardless of splits. Free, no API key needed.
    """
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&range=2y&events=adjsplit")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            print(f"  No data for {ticker}")
            return None

        indicators = result[0].get("indicators", {})

        # Use adjclose (split-adjusted) — this is the correct field
        adjclose_data = indicators.get("adjclose", [{}])
        adjcloses = adjclose_data[0].get("adjclose", []) if adjclose_data else []
        adjcloses = [c for c in adjcloses if c is not None]

        # Fallback to regular close if adjclose not available
        if len(adjcloses) < 20:
            closes = indicators.get("quote", [{}])[0].get("close", [])
            adjcloses = [c for c in closes if c is not None]

        if len(adjcloses) < 20:
            print(f"  Not enough data for {ticker}")
            return None

        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice", adjcloses[-1])

        return {
            "adjcloses": adjcloses,  # oldest first, all split-adjusted
            "price": price,
        }
    except Exception as e:
        print(f"  Error fetching {ticker}: {e}")
        return None

def calc_weekly_rsi(daily_closes, period=14):
    """
    Calculate weekly RSI-14 using Wilder smoothing.
    Converts daily closes to weekly (every 5 days).
    Matches TradingView weekly RSI-14, length=14, smoothing=1.
    daily_closes must be oldest-first.
    """
    if len(daily_closes) < period * 5 + 5:
        return None
    weekly = [daily_closes[i] for i in range(0, len(daily_closes), 5)]
    if len(weekly) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(weekly)):
        diff = weekly[i] - weekly[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_g / avg_l)), 1)

def get_dca_zone(rsi, drawdown):
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
    signals = []
    print(f"Scanning {ticker}...")

    raw = get_data(ticker)
    if not raw:
        return signals, None

    adjcloses_old_first = raw["adjcloses"]    # oldest first, split-adjusted
    adjcloses_new_first = list(reversed(adjcloses_old_first))

    price = raw["price"]

    # True ATH from 2 years of split-adjusted data
    # Scale it: adjcloses are adjusted, so we need ratio to get true ATH in current price terms
    # The last adjclose corresponds to current price, so we scale proportionally
    last_adj = adjcloses_old_first[-1]
    scale = price / last_adj if last_adj > 0 else 1.0
    ath = max(adjcloses_old_first) * scale

    drawdown = ((ath - price) / ath) * 100 if ath > 0 else 0

    # SMA200 and SMA300 from adjusted closes (scaled to current price)
    adj_new_first_scaled = [c * scale for c in adjcloses_new_first]
    sma200 = sum(adj_new_first_scaled[:200]) / 200 if len(adj_new_first_scaled) >= 200 else None
    sma300 = sum(adj_new_first_scaled[:300]) / 300 if len(adj_new_first_scaled) >= 300 else None
    below_sma200 = (price < sma200) if sma200 else False
    below_sma300 = (price < sma300) if sma300 else False

    # 5-day price dip using scaled adjusted closes
    weekly_drop = 0.0
    if len(adj_new_first_scaled) >= 5:
        price_5d_ago = adj_new_first_scaled[4]
        if price_5d_ago > 0:
            weekly_drop = ((price_5d_ago - price) / price_5d_ago) * 100

    # Weekly RSI-14 from adjusted closes (scaled)
    rsi = calc_weekly_rsi([c * scale for c in adjcloses_old_first])

    print(f"  Price: ${price:.2f} | ATH: ${ath:.2f} | RSI-14w: {rsi} | Drawdown: {drawdown:.1f}% | SMA200: {'below' if below_sma200 else 'above'}")

    dca_zone, dca_action = get_dca_zone(rsi, drawdown)

    # Build Telegram signal strings
    if below_sma200 and sma200:
        pct = ((sma200 - price) / sma200) * 100
        signals.append(
            f"🟢 {ticker}: BELOW SMA200 — STRONG BUY\n"
            f"Price ${price:.2f} is {pct:.1f}% below 200-day MA (${sma200:.2f})"
        )
    if below_sma300 and sma300:
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
            f"Price ${price:.2f} is {drawdown:.1f}% off 2-year ATH (${ath:.2f})"
        )
    elif drawdown >= 10:
        signals.append(
            f"🟡 {ticker}: {drawdown:.1f}% PULLBACK — ADD POSITION\n"
            f"Price ${price:.2f} is {drawdown:.1f}% off 2-year ATH (${ath:.2f})"
        )
    elif drawdown < 2:
        signals.append(
            f"⚪ {ticker}: NEAR ATH — DCA ONLY\n"
            f"Price ${price:.2f} within {drawdown:.1f}% of 2-year ATH. No lump sum."
        )
    if weekly_drop >= 15:
        signals.append(
            f"📉 {ticker}: {weekly_drop:.1f}% WEEKLY DIP — STRONG BUY\n"
            f"Price fell {weekly_drop:.1f}% in 5 days."
        )
    elif weekly_drop >= 10:
        signals.append(
            f"🟡 {ticker}: {weekly_drop:.1f}% WEEKLY DIP — ADD POSITION\n"
            f"Price fell {weekly_drop:.1f}% in 5 days."
        )
    elif weekly_drop >= 5:
        signals.append(
            f"👀 {ticker}: {weekly_drop:.1f}% WEEKLY DIP — WATCH\n"
            f"Price fell {weekly_drop:.1f}% in 5 days."
        )

    verdict = "bull" if dca_zone >= 3 else ("bear" if ticker == "TSLA" else "neutral")

    ticker_data = {
        "ticker":       ticker,
        "name":         meta["name"],
        "type":         meta["type"],
        "pe_sector":    meta.get("pe_sector", 25),
        "price":        round(price, 2),
        "high52":       round(ath, 2),
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
    print(f"Data source: Yahoo Finance — split-adjusted prices, 2-year ATH")

    all_signals    = []
    watchlist_data = []

    for ticker, meta in WATCHLIST.items():
        sigs, ticker_data = analyse(ticker, meta)
        all_signals.extend(sigs)
        if ticker_data:
            watchlist_data.append(ticker_data)
        time.sleep(2)

    buy_sigs  = [s for s in all_signals if any(x in s for x in ["BUY", "DRAWDOWN", "PULLBACK"])]
    hold_sigs = [s for s in all_signals if "ATH" in s and "DEPLOY" not in s and "DRAWDOWN" not in s]

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

if __name__ == "__main__":
    main()

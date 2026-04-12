# retirement-plan
retirement plan
"""
Single-run version of the bot — designed for GitHub Actions.
Runs one full scan cycle, sends alerts, saves log, then exits.
"""

import os, json, time
from datetime import datetime
from bot import (
    WATCHLIST,
    compute_signals,
    check_rate_cycle,
    send_daily_digest,
    send_telegram,
    alert_if_new,
)

def main():
    print(f"[{datetime.now()}] Starting single-run scan for GitHub Actions...")

    all_signals = []
    signal_log = {"timestamp": datetime.now().isoformat(), "signals": []}

    for ticker, meta in WATCHLIST.items():
        signals = compute_signals(ticker, meta)
        for s in signals:
            alert_if_new(s)
            signal_log["signals"].append(s)
        all_signals.extend(signals)
        time.sleep(15)

    macro = check_rate_cycle()
    for s in macro:
        alert_if_new(s)
    all_signals.extend(macro)

    # Send daily digest on first run of the day (7am SGT ≈ 23 UTC)
    hour_utc = datetime.utcnow().hour
    if hour_utc == 23:
        send_daily_digest(all_signals)

    # Save signal log as artifact
    with open("signal_log.json", "w") as f:
        json.dump(signal_log, f, indent=2, default=str)

    buy_count = sum(1 for s in all_signals if "BUY" in s.get("label","").upper())
    print(f"[{datetime.now()}] Scan complete. {len(all_signals)} signals ({buy_count} buy signals).")

if __name__ == "__main__":
    main()

"""
╔══════════════════════════════════════════════════════════════╗
║          RETIREMENT ALPHA SIGNAL BOT  v2.0                  ║
║  Signals: SMA200, SMA300, RSI<30, Drawdown, Rate Cycles     ║
║  Assets: VOO, VUG, VGT, NVDA, MSFT, META                    ║
║  Alerts: Telegram + Email                                    ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import time
import smtplib
import requests
import json
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── CONFIG ─────────────────────────────────────────────────────────────────
# Copy .env.example to .env and fill in your values
# Or set these as environment variables on your hosting platform

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")

EMAIL_FROM        = os.getenv("EMAIL_FROM", "youremail@gmail.com")
EMAIL_PASSWORD    = os.getenv("EMAIL_PASSWORD", "your_app_password")  # Gmail App Password
EMAIL_TO          = os.getenv("EMAIL_TO", "youremail@gmail.com")

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "demo")  # Free at alphavantage.co
FRED_API_KEY      = os.getenv("FRED_API_KEY", "")           # Free at fred.stlouisfed.org

# ─── WATCHLIST ────────────────────────────────────────────────────────────────
WATCHLIST = {
    # ETFs — Core long-term retirement foundation
    "VOO":  {"name": "Vanguard S&P 500 ETF",       "type": "ETF",   "expense": 0.03},
    "VUG":  {"name": "Vanguard Growth ETF",          "type": "ETF",   "expense": 0.03},
    "VGT":  {"name": "Vanguard Info Tech ETF",       "type": "ETF",   "expense": 0.09},
    # Tech stocks — high-conviction AI plays
    "NVDA": {"name": "NVIDIA Corp",                  "type": "STOCK", "sector": "AI Chips"},
    "MSFT": {"name": "Microsoft Corp",               "type": "STOCK", "sector": "Cloud/AI"},
    "META": {"name": "Meta Platforms",               "type": "META",  "sector": "AI/Social"},
}

# ─── SIGNAL THRESHOLDS ──────────────────────────────────────────────────────
SIGNALS = {
    "sma200_buy":     "Price crosses BELOW SMA200 → STRONG BUY (Long-term entry)",
    "sma300_buy":     "Price crosses BELOW SMA300 → MAXIMUM BUY (Generational entry)",
    "rsi_oversold":   "RSI drops BELOW 30 → STRONG BUY (Oversold, mean reversion)",
    "pullback_10":    "10% drawdown from ATH → ADD POSITION (DCA opportunity)",
    "pullback_20":    "20%+ drawdown from ATH → AGGRESSIVE BUY (Deploy lump sum)",
    "rate_rising":    "Fed rate rising → REDUCE lump sum, increase DCA frequency",
    "rate_falling":   "Fed rate falling → INCREASE position size, equity bias",
    "ath_zone":       "Price within 2% of ATH → HOLD / DCA ONLY (no lump sum)",
}

CHECK_INTERVAL_SECONDS = 3600  # Check every hour (respect free API limits)

# ─── CACHE ───────────────────────────────────────────────────────────────────
_last_alerts = {}    # Avoid duplicate alerts
_ath_cache   = {}    # Store all-time highs per ticker
_rate_cache  = {"rate": None, "timestamp": None}

# ═════════════════════════════════════════════════════════════════════════════
#  DATA FETCHING
# ═════════════════════════════════════════════════════════════════════════════

def fetch_daily_prices(ticker: str) -> list[float] | None:
    """Fetch up to 500 days of daily close prices from Alpha Vantage."""
    url = (
        f"https://www.alphavantage.co/query"
        f"?function=TIME_SERIES_DAILY_ADJUSTED"
        f"&symbol={ticker}"
        f"&outputsize=full"
        f"&apikey={ALPHA_VANTAGE_KEY}"
    )
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        ts = data.get("Time Series (Daily)", {})
        if not ts:
            print(f"  [!] No price data for {ticker}: {list(data.keys())}")
            return None
        closes = [float(v["5. adjusted close"]) for v in list(ts.values())]
        return closes  # Index 0 = most recent
    except Exception as e:
        print(f"  [!] Fetch error {ticker}: {e}")
        return None


def fetch_rsi(ticker: str, period: int = 14) -> float | None:
    """Fetch RSI from Alpha Vantage technical indicator endpoint."""
    url = (
        f"https://www.alphavantage.co/query"
        f"?function=RSI"
        f"&symbol={ticker}"
        f"&interval=daily"
        f"&time_period={period}"
        f"&series_type=close"
        f"&apikey={ALPHA_VANTAGE_KEY}"
    )
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        analysis = data.get("Technical Analysis: RSI", {})
        if not analysis:
            return None
        latest_date = sorted(analysis.keys())[-1]
        return float(analysis[latest_date]["RSI"])
    except Exception as e:
        print(f"  [!] RSI fetch error {ticker}: {e}")
        return None


def fetch_fed_rate() -> float | None:
    """Fetch current Fed Funds Rate from FRED (free). Falls back to Yahoo."""
    # Cache for 24 hours — rate doesn't change that often
    now = datetime.utcnow()
    if _rate_cache["rate"] and _rate_cache["timestamp"]:
        age_hours = (now - _rate_cache["timestamp"]).total_seconds() / 3600
        if age_hours < 24:
            return _rate_cache["rate"]

    if FRED_API_KEY:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=FEDFUNDS"
            f"&api_key={FRED_API_KEY}"
            f"&file_type=json"
            f"&sort_order=desc"
            f"&limit=3"
        )
        try:
            r = requests.get(url, timeout=10)
            obs = r.json().get("observations", [])
            if obs:
                rate = float(obs[0]["value"])
                _rate_cache.update({"rate": rate, "timestamp": now})
                return rate
        except Exception as e:
            print(f"  [!] FRED fetch error: {e}")

    # Fallback: hardcoded last known rate (update manually if needed)
    print("  [!] FRED key not set — using fallback rate value")
    return 4.33  # Update this periodically if no FRED key


# ═════════════════════════════════════════════════════════════════════════════
#  SIGNAL COMPUTATION
# ═════════════════════════════════════════════════════════════════════════════

def compute_sma(prices: list[float], period: int) -> float | None:
    if len(prices) < period:
        return None
    return sum(prices[:period]) / period


def compute_signals(ticker: str, meta: dict) -> list[dict]:
    """
    Returns a list of triggered signal dicts for the given ticker.
    Each dict: {ticker, signal_key, label, price, detail, severity}
    """
    triggered = []
    print(f"  Analysing {ticker}...")

    prices = fetch_daily_prices(ticker)
    if not prices or len(prices) < 5:
        print(f"    → insufficient price data")
        return triggered

    price_now = prices[0]
    time.sleep(12)  # Alpha Vantage free tier: 5 calls/min

    # ── SMA 200 ──────────────────────────────────────────────────────────────
    sma200 = compute_sma(prices, 200)
    if sma200 and price_now < sma200:
        pct_below = ((sma200 - price_now) / sma200) * 100
        triggered.append({
            "ticker": ticker,
            "signal_key": "sma200_buy",
            "label": "🟢 BELOW SMA200 — STRONG BUY",
            "price": price_now,
            "detail": f"Price ${price_now:.2f} is {pct_below:.1f}% below 200-day SMA (${sma200:.2f})",
            "severity": "HIGH",
        })

    # ── SMA 300 ──────────────────────────────────────────────────────────────
    sma300 = compute_sma(prices, 300)
    if sma300 and price_now < sma300:
        pct_below = ((sma300 - price_now) / sma300) * 100
        triggered.append({
            "ticker": ticker,
            "signal_key": "sma300_buy",
            "label": "🟩 BELOW SMA300 — MAXIMUM BUY",
            "price": price_now,
            "detail": f"Price ${price_now:.2f} is {pct_below:.1f}% below 300-day SMA (${sma300:.2f}). Generational entry point.",
            "severity": "CRITICAL",
        })

    # ── RSI < 30 ─────────────────────────────────────────────────────────────
    rsi = fetch_rsi(ticker)
    time.sleep(12)
    if rsi is not None and rsi < 30:
        triggered.append({
            "ticker": ticker,
            "signal_key": "rsi_oversold",
            "label": f"🔵 RSI OVERSOLD ({rsi:.1f}) — STRONG BUY",
            "price": price_now,
            "detail": f"RSI={rsi:.1f} is below 30. Asset is statistically oversold. High probability mean reversion.",
            "severity": "HIGH",
        })

    # ── ALL-TIME HIGH TRACKING & DRAWDOWN ────────────────────────────────────
    ath = max(prices[:252])  # Rolling 1-year ATH (approx)
    global_ath = _ath_cache.get(ticker, 0)
    if ath > global_ath:
        _ath_cache[ticker] = ath
        ath = ath
    else:
        ath = global_ath

    drawdown_pct = ((ath - price_now) / ath) * 100

    if drawdown_pct >= 20:
        triggered.append({
            "ticker": ticker,
            "signal_key": "pullback_20",
            "label": f"🚨 {drawdown_pct:.1f}% DRAWDOWN — AGGRESSIVE BUY",
            "price": price_now,
            "detail": f"Price ${price_now:.2f} is {drawdown_pct:.1f}% off ATH (${ath:.2f}). DEPLOY MAXIMUM LUMP SUM.",
            "severity": "CRITICAL",
        })
    elif drawdown_pct >= 10:
        triggered.append({
            "ticker": ticker,
            "signal_key": "pullback_10",
            "label": f"🟡 {drawdown_pct:.1f}% PULLBACK — ADD POSITION",
            "price": price_now,
            "detail": f"Price ${price_now:.2f} is {drawdown_pct:.1f}% off ATH (${ath:.2f}). Good DCA entry.",
            "severity": "MEDIUM",
        })
    elif drawdown_pct < 2:
        triggered.append({
            "ticker": ticker,
            "signal_key": "ath_zone",
            "label": "⚪ NEAR ATH — HOLD / DCA ONLY",
            "price": price_now,
            "detail": f"Price ${price_now:.2f} within {drawdown_pct:.1f}% of ATH. Avoid lump sum. DCA only.",
            "severity": "INFO",
        })

    return triggered


def check_rate_cycle() -> list[dict]:
    """Check Fed rate environment and return macro signal."""
    triggered = []
    rate = fetch_fed_rate()
    if rate is None:
        return triggered

    # Simple heuristic: >4% = restrictive cycle, <2% = accommodative
    if rate > 4.0:
        triggered.append({
            "ticker": "MACRO",
            "signal_key": "rate_rising",
            "label": f"📊 FED RATE HIGH ({rate:.2f}%) — REDUCE LUMP SUM",
            "price": rate,
            "detail": (
                f"Fed Funds Rate is {rate:.2f}% — restrictive territory. "
                "Strategy: favour DCA over lump sum. Keep 6-month emergency fund. "
                "Consider adding bond ETF (BND) as ballast."
            ),
            "severity": "MEDIUM",
        })
    elif rate < 2.0:
        triggered.append({
            "ticker": "MACRO",
            "signal_key": "rate_falling",
            "label": f"📉 FED RATE LOW ({rate:.2f}%) — INCREASE EQUITY EXPOSURE",
            "price": rate,
            "detail": (
                f"Fed Funds Rate is {rate:.2f}% — accommodative. "
                "Strategy: increase equity allocation, deploy larger lump sums, "
                "reduce cash drag. Growth ETFs (VUG, VGT) outperform in this environment."
            ),
            "severity": "MEDIUM",
        })

    return triggered


# ═════════════════════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ═════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    if TELEGRAM_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print("  [Telegram] Not configured — skipping")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"  [!] Telegram error: {e}")
        return False


def send_email(subject: str, body: str) -> bool:
    if EMAIL_PASSWORD == "your_app_password":
        print("  [Email] Not configured — skipping")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        return True
    except Exception as e:
        print(f"  [!] Email error: {e}")
        return False


def format_alert(signal: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🏦 RETIREMENT ALPHA SIGNAL",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"*Ticker:* {signal['ticker']}",
        f"*Signal:* {signal['label']}",
        f"*Detail:* {signal['detail']}",
        f"*Severity:* {signal['severity']}",
        f"*Time:* {now} SGT",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 Target: $3,000,000 by age 50",
        f"📈 Strategy: Long-term DCA + signal-based lump sums",
    ]
    return "\n".join(lines)


def alert_if_new(signal: dict) -> None:
    """Only alert if we haven't alerted for this signal in the last 24 hours."""
    key = f"{signal['ticker']}_{signal['signal_key']}"
    last = _last_alerts.get(key)
    if last and (datetime.now() - last).total_seconds() < 86400:
        return  # Already alerted today

    msg = format_alert(signal)
    subject = f"[ALPHA SIGNAL] {signal['ticker']}: {signal['label']}"

    print(f"\n  🚨 ALERT: {signal['ticker']} — {signal['label']}")
    print(f"     {signal['detail']}")

    send_telegram(msg)
    send_email(subject, msg)
    _last_alerts[key] = datetime.now()


# ═════════════════════════════════════════════════════════════════════════════
#  DAILY PORTFOLIO DIGEST
# ═════════════════════════════════════════════════════════════════════════════

def send_daily_digest(all_signals: list[dict]) -> None:
    now = datetime.now().strftime("%Y-%m-%d")
    buy_signals = [s for s in all_signals if "BUY" in s["label"].upper() or "OVERSOLD" in s["label"].upper()]
    hold_signals = [s for s in all_signals if "HOLD" in s["label"].upper() or "ATH" in s["label"].upper()]
    macro_signals = [s for s in all_signals if s["ticker"] == "MACRO"]

    lines = [
        f"📊 *DAILY PORTFOLIO DIGEST — {now}*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 Goal: $3M by age 50 | Rate: 10% compound",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    if buy_signals:
        lines.append(f"🟢 *BUY SIGNALS ACTIVE ({len(buy_signals)})*")
        for s in buy_signals:
            lines.append(f"  • {s['ticker']}: {s['label']}")
        lines.append("")

    if hold_signals:
        lines.append(f"⚪ *HOLD SIGNALS ({len(hold_signals)})*")
        for s in hold_signals:
            lines.append(f"  • {s['ticker']}: {s['label']}")
        lines.append("")

    if macro_signals:
        lines.append(f"📈 *MACRO ENVIRONMENT*")
        for s in macro_signals:
            lines.append(f"  • {s['label']}")
        lines.append("")

    if not buy_signals and not hold_signals:
        lines.append("✅ No major signals today. Stay the course. DCA as scheduled.")
        lines.append("")

    lines += [
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📋 *WATCHLIST:* VOO | VUG | VGT | NVDA | MSFT | META",
        f"💡 *Reminder:* Never time the bottom. SMA200/300 + RSI<30 together = highest conviction.",
    ]

    msg = "\n".join(lines)
    send_telegram(msg)
    send_email(f"Daily Portfolio Digest — {now}", msg)
    print("\n" + msg)


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ═════════════════════════════════════════════════════════════════════════════

def run_check_cycle() -> list[dict]:
    print(f"\n{'='*60}")
    print(f"  SCAN STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    all_signals = []

    # Check each ticker in watchlist
    for ticker, meta in WATCHLIST.items():
        signals = compute_signals(ticker, meta)
        for s in signals:
            alert_if_new(s)
        all_signals.extend(signals)
        time.sleep(15)  # Rate limit protection

    # Check macro environment
    macro = check_rate_cycle()
    for s in macro:
        alert_if_new(s)
    all_signals.extend(macro)

    print(f"\n  Scan complete. {len(all_signals)} signals found.")
    return all_signals


def main():
    print("""
╔══════════════════════════════════════════════════════╗
║       RETIREMENT ALPHA SIGNAL BOT  v2.0             ║
║   Monitoring: VOO | VUG | VGT | NVDA | MSFT | META  ║
║   Signals: SMA200 | SMA300 | RSI<30 | Drawdown       ║
╚══════════════════════════════════════════════════════╝
    """)

    daily_digest_sent_date = None

    while True:
        try:
            all_signals = run_check_cycle()

            # Send daily digest once per day at first run of the day
            today = datetime.now().date()
            if daily_digest_sent_date != today:
                send_daily_digest(all_signals)
                daily_digest_sent_date = today

            print(f"\n  Next check in {CHECK_INTERVAL_SECONDS // 60} minutes...")
            time.sleep(CHECK_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("\n  Bot stopped by user.")
            break
        except Exception as e:
            print(f"\n  [!] Unexpected error: {e}")
            time.sleep(300)  # Wait 5 min then retry


if __name__ == "__main__":
    main()
requests==2.31.0
python-dotenv==1.0.0

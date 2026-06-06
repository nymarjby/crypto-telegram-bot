import logging
import re
import io
import asyncio
from datetime import datetime, timezone

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# ─── CONFIG ────────────────────────────────────────────────────────────────────
import os
TELEGRAM_BOT_TOKEN = os.environ.get("8545101004:AAGHbE4kG2g9N2rtRkgkx22n_i4r5O5Ro44")
MEXC_BASE = "https://api.mexc.com"
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def find_symbol(ticker: str) -> str | None:
    """Return the MEXC symbol (e.g. BTCUSDT) for a given ticker, or None."""
    ticker = ticker.upper()
    candidates = [f"{ticker}USDT", f"{ticker}USDC", f"{ticker}BTC"]
    for sym in candidates:
        r = requests.get(f"{MEXC_BASE}/api/v3/ticker/24hr", params={"symbol": sym}, timeout=10)
        if r.status_code == 200:
            return sym
    # Fallback: search exchange info
    try:
        r = requests.get(f"{MEXC_BASE}/api/v3/exchangeInfo", timeout=15)
        symbols = r.json().get("symbols", [])
        for s in symbols:
            if s.get("baseAsset", "").upper() == ticker and s.get("quoteAsset") == "USDT":
                return s["symbol"]
    except Exception:
        pass
    return None


def get_ticker(symbol: str) -> dict | None:
    r = requests.get(f"{MEXC_BASE}/api/v3/ticker/24hr", params={"symbol": symbol}, timeout=10)
    return r.json() if r.status_code == 200 else None


def get_klines(symbol: str, interval: str = "1h", limit: int = 168) -> list | None:
    """Fetch kline/candlestick data. Default: hourly for last 7 days."""
    r = requests.get(
        f"{MEXC_BASE}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
    )
    return r.json() if r.status_code == 200 else None


def build_chart(symbol: str, klines: list, ticker: dict) -> io.BytesIO:
    """Render a price chart and return it as a PNG buffer."""
    times, closes, volumes = [], [], []
    for k in klines:
        times.append(datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc))
        closes.append(float(k[4]))
        volumes.append(float(k[5]))

    closes = np.array(closes)
    price_change = float(ticker.get("priceChangePercent", 0))
    color = "#00c896" if price_change >= 0 else "#ff4d4d"

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 6),
        gridspec_kw={"height_ratios": [3, 1]},
        facecolor="#0f1117"
    )
    fig.subplots_adjust(hspace=0.05)

    # ── price line ──
    ax1.set_facecolor("#0f1117")
    ax1.plot(times, closes, color=color, linewidth=1.8, zorder=3)
    ax1.fill_between(times, closes, closes.min(), alpha=0.15, color=color)
    ax1.set_xlim(times[0], times[-1])
    ax1.yaxis.set_label_position("right")
    ax1.yaxis.tick_right()
    ax1.tick_params(colors="#aaaaaa", labelsize=8)
    ax1.xaxis.set_visible(False)
    for spine in ax1.spines.values():
        spine.set_edgecolor("#2a2a3a")
    ax1.grid(True, color="#1e1e2e", linewidth=0.5, zorder=0)

    last_price = closes[-1]
    ax1.annotate(
        f"${last_price:,.6g}",
        xy=(times[-1], last_price),
        xytext=(-8, 0), textcoords="offset points",
        ha="right", va="center",
        color=color, fontsize=9, fontweight="bold"
    )

    ticker_label = symbol.replace("USDT", "/USDT")
    change_sign = "+" if price_change >= 0 else ""
    ax1.set_title(
        f"{ticker_label}   {change_sign}{price_change:.2f}%   (7d Hourly)",
        color="#ffffff", fontsize=12, fontweight="bold", pad=10, loc="left"
    )

    # ── volume bars ──
    ax2.set_facecolor("#0f1117")
    bar_colors = [color] * len(times)
    ax2.bar(times, volumes, width=0.03, color=bar_colors, alpha=0.6)
    ax2.set_xlim(times[0], times[-1])
    ax2.yaxis.set_label_position("right")
    ax2.yaxis.tick_right()
    ax2.tick_params(colors="#aaaaaa", labelsize=7)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax2.xaxis.set_major_locator(mdates.DayLocator())
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30, ha="right", color="#aaaaaa")
    for spine in ax2.spines.values():
        spine.set_edgecolor("#2a2a3a")
    ax2.grid(True, color="#1e1e2e", linewidth=0.5)
    ax2.set_ylabel("Vol", color="#666680", fontsize=7)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="#0f1117")
    plt.close(fig)
    buf.seek(0)
    return buf


def fmt_large(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:.4f}"


def build_caption(symbol: str, t: dict) -> str:
    price = float(t.get("lastPrice", 0))
    change = float(t.get("priceChangePercent", 0))
    high = float(t.get("highPrice", 0))
    low = float(t.get("lowPrice", 0))
    vol_base = float(t.get("volume", 0))
    vol_quote = float(t.get("quoteVolume", 0))

    change_emoji = "🟢" if change >= 0 else "🔴"
    change_sign = "+" if change >= 0 else ""
    base = symbol.replace("USDT", "").replace("USDC", "").replace("BTC", "")

    lines = [
        f"💎 *{base} / USDT*",
        "",
        f"💰 *Price:* `${price:,.6g}`",
        f"{change_emoji} *24h Change:* `{change_sign}{change:.2f}%`",
        f"📈 *24h High:* `${high:,.6g}`",
        f"📉 *24h Low:* `${low:,.6g}`",
        f"📊 *24h Volume:* `{fmt_large(vol_quote)}`",
        f"🔄 *24h Trades Vol:* `{vol_base:,.0f} {base}`",
        "",
        f"🏦 *Exchange:* MEXC",
        f"🕐 *Updated:* {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
    ]
    return "\n".join(lines)


# ── handler ──────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    tickers = re.findall(r"\$([A-Za-z]{1,20})", text)
    if not tickers:
        return

    # Deduplicate while preserving order
    seen, unique = set(), []
    for t in tickers:
        if t.upper() not in seen:
            seen.add(t.upper())
            unique.append(t.upper())

    for ticker in unique[:3]:   # max 3 per message to avoid spam
        symbol = find_symbol(ticker)
        if not symbol:
            await update.message.reply_text(
                f"❌ Could not find *${ticker}* on MEXC.",
                parse_mode="Markdown"
            )
            continue

        ticker_data = get_ticker(symbol)
        if not ticker_data or "lastPrice" not in ticker_data:
            await update.message.reply_text(
                f"❌ Failed to fetch data for *{symbol}*.",
                parse_mode="Markdown"
            )
            continue

        klines = get_klines(symbol)
        caption = build_caption(symbol, ticker_data)

        if klines and len(klines) > 5:
            chart_buf = await asyncio.get_event_loop().run_in_executor(
                None, build_chart, symbol, klines, ticker_data
            )
            await update.message.reply_photo(
                photo=chart_buf,
                caption=caption,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(caption, parse_mode="Markdown")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()

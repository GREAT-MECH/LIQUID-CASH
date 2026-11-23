# LIQUID-CASH
#FOREX APP 
# LIQUID CASH — Liquidity + MAEE Forex Engine

## Core Strategy (Rayner Teo MAEE + Institutional Liquidity)
1. **M (Market Structure & Liquidity):**
   - Trend filter via 200 EMA + Higher Highs/Higher Lows (Uptrend) or Lower Highs/Lower Lows (Downtrend).
   - Map Buy-Side Liquidity (previous 4H/Daily swing highs) & Sell-Side Liquidity (previous 4H/Daily swing lows).
2. **A (Area of Value):**
   - Confluence where a Liquidity Pool aligns with 20/50 EMA dynamic support/resistance or key horizontal structure.
3. **E (Entry Trigger):**
   - Wait for a liquidity sweep at the Area of Value, followed by a confirmed rejection candlestick close (Bullish/Bearish Engulfing or Pin Bar/Hammer).
4. **E (Exit & Profit Protection Engine):**
   - **Take-Profit 1 (Liquidity Target):** Exit just *before* the next opposing liquidity pool (nearest swing high/low) before institutional reversal.
   - **Trailing Exit (Trend Runner):** Trail stop-loss using 3x ATR (Chandelier Exit) or close beyond the 20 EMA to lock in profits automatically.

## Tech Stack
- **Database:** Supabase (PostgreSQL)
- **Backend / Scanner:** Python (FastAPI + APScheduler + Pandas)
- **Frontend:** React + Vite + Tailwind CSS + TradingView Lightweight Charts
- **Hosting:** Render (Background Worker + Static Site / Web Service)
- **Alerts:** Telegram Bot API

import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="LIQUID CASH Engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY", "XAU/USD"]

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})

def fetch_candles(pair: str, interval: str = "1h", outputsize: int = 250) -> pd.DataFrame:
    url = f"https://api.twelvedata.com/time_series?symbol={pair}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_KEY}"
    res = requests.get(url).json()
    if "values" not in res:
        raise ValueError(f"TwelveData error for {pair}: {res.get('message', 'Unknown error')}")
    df = pd.DataFrame(res["values"])
    df = df.iloc[::-1].reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    return df

def analyze_maee_liquidity(df: pd.DataFrame, pair: str, timeframe: str = "1h"):
    # Indicators: 200 EMA (Market Structure), 20 & 50 EMA (Area of Value), 14 ATR (Trailing Exit)
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1)))
    )
    df["atr"] = tr.rolling(14).mean()

    # Liquidity Pools: Previous 30-candle swing highs (BSL) and swing lows (SSL) excluding last 3 candles
    lookback = df.iloc[-35:-3]
    bsl = float(lookback["high"].max())  # Buy-Side Liquidity
    ssl = float(lookback["low"].min())   # Sell-Side Liquidity

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    atr = float(curr["atr"])

    # M — Market Structure
    uptrend = curr["close"] > curr["ema200"] and curr["ema50"] > curr["ema200"]
    downtrend = curr["close"] < curr["ema200"] and curr["ema50"] < curr["ema200"]

    body = abs(curr["close"] - curr["open"])
    lower_wick = min(curr["open"], curr["close"]) - curr["low"]
    upper_wick = curr["high"] - max(curr["open"], curr["close"])

    # E — Candlestick Triggers (Pin Bar or Engulfing)
    bull_pin = (lower_wick > body * 2.0) and (curr["close"] > curr["open"])
    bull_engulf = (prev["close"] < prev["open"]) and (curr["close"] > curr["open"]) and (curr["close"] > prev["open"]) and (curr["open"] <= prev["close"])

    bear_pin = (upper_wick > body * 2.0) and (curr["close"] < curr["open"])
    bear_engulf = (prev["close"] > prev["open"]) and (curr["close"] < curr["open"]) and (curr["close"] < prev["open"]) and (curr["open"] >= prev["close"])

    # A — Area of Value + Liquidity Sweep Check
    near_value_bull = abs(curr["low"] - curr["ema50"]) <= (atr * 0.8) or abs(curr["low"] - ssl) <= (atr * 0.8)
    near_value_bear = abs(curr["high"] - curr["ema50"]) <= (atr * 0.8) or abs(curr["high"] - bsl) <= (atr * 0.8)

    signal = None
    if uptrend and near_value_bull and (bull_pin or bull_engulf):
        pattern = "Bullish Engulfing" if bull_engulf else "Bullish Pin Bar"
        entry = float(curr["close"])
        sl = round(min(float(curr["low"]), ssl) - (0.5 * atr), 5)
        # E — Exit Engine: Just before opposing Buy-Side Liquidity + 3x ATR trailing stop
        tp_liquidity = round(bsl - (0.15 * atr), 5)
        trailing_exit = round(entry - (3.0 * atr), 5)
        signal = {
            "pair": pair,
            "timeframe": timeframe,
            "direction": "BUY",
            "market_structure": "Bullish (Above 200 EMA)",
            "liquidity_zone": round(ssl, 5),
            "entry_price": round(entry, 5),
            "stop_loss": sl,
            "take_profit_liquidity": tp_liquidity,
            "trailing_atr_exit": trailing_exit,
            "candle_pattern": pattern,
            "status": "ACTIVE"
        }
    elif downtrend and near_value_bear and (bear_pin or bear_engulf):
        pattern = "Bearish Engulfing" if bear_engulf else "Bearish Pin Bar"
        entry = float(curr["close"])
        sl = round(max(float(curr["high"]), bsl) + (0.5 * atr), 5)
        # E — Exit Engine: Just before opposing Sell-Side Liquidity + 3x ATR trailing stop
        tp_liquidity = round(ssl + (0.15 * atr), 5)
        trailing_exit = round(entry + (3.0 * atr), 5)
        signal = {
            "pair": pair,
            "timeframe": timeframe,
            "direction": "SELL",
            "market_structure": "Bearish (Below 200 EMA)",
            "liquidity_zone": round(bsl, 5),
            "entry_price": round(entry, 5),
            "stop_loss": sl,
            "take_profit_liquidity": tp_liquidity,
            "trailing_atr_exit": trailing_exit,
            "candle_pattern": pattern,
            "status": "ACTIVE"
        }

    return signal, {"bsl": round(bsl, 5), "ssl": round(ssl, 5), "ema200": round(float(curr["ema200"]), 5), "ema50": round(float(curr["ema50"]), 5)}

def run_market_scan():
    for pair in PAIRS:
        try:
            df = fetch_candles(pair, "1h", 250)
            signal, _ = analyze_maee_liquidity(df, pair, "1h")
            if signal:
                supabase.table("signals").insert(signal).execute()
                msg = (
                    f"💧 *LIQUID CASH SIGNAL — {signal['pair']} ({signal['direction']})*\n"
                    f"• *Structure:* {signal['market_structure']}\n"
                    f"• *Trigger:* {signal['candle_pattern']} at Value/Liquidity\n"
                    f"• *Entry:* `{signal['entry_price']}`\n"
                    f"• *Stop Loss:* `{signal['stop_loss']}`\n"
                    f"• *🎯 EXIT TARGET (Before Liquidity):* `{signal['take_profit_liquidity']}`\n"
                    f"• *🛡️ Profit Lock (3x ATR Trail):* `{signal['trailing_atr_exit']}`"
                )
                send_telegram(msg)
        except Exception as e:
            print(f"Scan error on {pair}: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(run_market_scan, "interval", minutes=15)
scheduler.start()

@app.get("/api/chart/{pair:path}")
def get_chart_data(pair: str, interval: str = "1h"):
    try:
        df = fetch_candles(pair, interval, 220)
        signal, levels = analyze_maee_liquidity(df, pair, interval)
        candles = [
            {
                "time": int(datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            for _, row in df.tail(120).iterrows()
        ]
        return {"pair": pair, "candles": candles, "levels": levels, "live_signal": signal}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/signals")
def get_signals():
    res = supabase.table("signals").select("*").order("created_at", desc=True).limit(15).execute()
    return res.data

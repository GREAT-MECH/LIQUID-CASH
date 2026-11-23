import os
from datetime import datetime
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client, create_client

load_dotenv()

app = FastAPI(title="LIQUID CASH Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY", "XAU/USD"]


def parse_candle_timestamp(raw_dt: Any) -> int:
    """Parse TwelveData datetime string into Unix epoch seconds."""
    text = str(raw_dt).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    return int(datetime.fromisoformat(text).timestamp())


def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
    except Exception as exc:
        print(f"Telegram alert error: {exc}")


def fetch_candles(pair: str, interval: str = "1h", outputsize: int = 250) -> pd.DataFrame:
    if not TWELVE_DATA_KEY:
        raise ValueError("Missing TWELVE_DATA_KEY environment variable.")

    normalized_pair = pair.replace("-", "/").strip().upper()
    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol={normalized_pair}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_KEY}"
    )
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    payload = response.json()

    if "values" not in payload or not payload["values"]:
        detail = payload.get("message", "No candle values returned from TwelveData")
        raise ValueError(f"TwelveData error for {normalized_pair}: {detail}")

    df = pd.DataFrame(payload["values"])
    df = df.iloc[::-1].reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)

    return df


def analyze_maee_liquidity(df: pd.DataFrame, pair: str, timeframe: str = "1h"):
    """
    M - Market Structure (200 EMA + 50 EMA trend filter)
    A - Area of Value & Liquidity Sweep (BSL / SSL pools)
    E - Entry Candle Trigger (Pin Bar or Engulfing)
    E - Exit Engine (Liquidity target + 3x ATR trailing stop)
    """
    df = df.copy()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    prev_close = df["close"].shift(1)
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ),
    )
    df["atr"] = tr.rolling(14).mean()

    lookback = df.iloc[-35:-3] if len(df) >= 38 else df.iloc[:-1]
    bsl = float(lookback["high"].max())
    ssl = float(lookback["low"].min())

    curr = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else curr
    atr = float(curr["atr"]) if pd.notna(curr["atr"]) and float(curr["atr"]) > 0 else 0.0005

    uptrend = float(curr["close"]) > float(curr["ema200"]) and float(curr["ema50"]) > float(curr["ema200"])
    downtrend = float(curr["close"]) < float(curr["ema200"]) and float(curr["ema50"]) < float(curr["ema200"])

    body = abs(float(curr["close"]) - float(curr["open"]))
    lower_wick = min(float(curr["open"]), float(curr["close"])) - float(curr["low"])
    upper_wick = float(curr["high"]) - max(float(curr["open"]), float(curr["close"]))

    bull_pin = (lower_wick > body * 2.0) and (float(curr["close"]) > float(curr["open"]))
    bull_engulf = (
        float(prev["close"]) < float(prev["open"])
        and float(curr["close"]) > float(curr["open"])
        and float(curr["close"]) > float(prev["open"])
        and float(curr["open"]) <= float(prev["close"])
    )

    bear_pin = (upper_wick > body * 2.0) and (float(curr["close"]) < float(curr["open"]))
    bear_engulf = (
        float(prev["close"]) > float(prev["open"])
        and float(curr["close"]) < float(curr["open"])
        and float(curr["close"]) < float(prev["open"])
        and float(curr["open"]) >= float(prev["close"])
    )

    near_value_bull = (
        abs(float(curr["low"]) - float(curr["ema50"])) <= (atr * 0.8)
        or abs(float(curr["low"]) - ssl) <= (atr * 0.8)
    )
    near_value_bear = (
        abs(float(curr["high"]) - float(curr["ema50"])) <= (atr * 0.8)
        or abs(float(curr["high"]) - bsl) <= (atr * 0.8)
    )

    signal: Optional[Dict[str, Any]] = None
    if uptrend and near_value_bull and (bull_pin or bull_engulf):
        pattern = "Bullish Engulfing" if bull_engulf else "Bullish Pin Bar"
        entry = float(curr["close"])
        sl = round(min(float(curr["low"]), ssl) - (0.5 * atr), 5)
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
            "status": "ACTIVE",
        }
    elif downtrend and near_value_bear and (bear_pin or bear_engulf):
        pattern = "Bearish Engulfing" if bear_engulf else "Bearish Pin Bar"
        entry = float(curr["close"])
        sl = round(max(float(curr["high"]), bsl) + (0.5 * atr), 5)
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
            "status": "ACTIVE",
        }

    levels = {
        "bsl": round(bsl, 5),
        "ssl": round(ssl, 5),
        "ema200": round(float(curr["ema200"]), 5),
        "ema50": round(float(curr["ema50"]), 5),
        "ema20": round(float(curr["ema20"]), 5),
        "atr": round(atr, 5),
    }

    return signal, levels


def run_market_scan() -> None:
    for pair in PAIRS:
        try:
            df = fetch_candles(pair, "1h", 250)
            signal, _ = analyze_maee_liquidity(df, pair, "1h")
            if signal:
                if supabase:
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
        except Exception as exc:
            print(f"Scan error on {pair}: {exc}")


scheduler = BackgroundScheduler()
scheduler.add_job(run_market_scan, "interval", minutes=15)
scheduler.start()


@app.get("/")
def root():
    return {
        "service": "LIQUID CASH Engine",
        "status": "running",
        "pairs": PAIRS,
    }


@app.get("/api/chart/{pair:path}")
def get_chart_data(pair: str, interval: str = "1h"):
    try:
        df = fetch_candles(pair, interval, 220)
        signal, levels = analyze_maee_liquidity(df, pair, interval)
        candles = [
            {
                "time": parse_candle_timestamp(row["datetime"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            for _, row in df.tail(120).iterrows()
        ]
        return {
            "pair": pair,
            "interval": interval,
            "candles": candles,
            "levels": levels,
            "live_signal": signal,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/signals")
def get_signals():
    if not supabase:
        return []
    try:
        res = (
            supabase.table("signals")
            .select("*")
            .order("created_at", desc=True)
            .limit(15)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

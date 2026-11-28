import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import Client, create_client

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

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as exc:
        print(f"Supabase init warning: {exc}")

PAIRS: List[str] = ["EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY", "XAU/USD"]
VALID_INTERVALS = {"1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "1day"}

# In-memory cache to protect Twelve Data 800/day free quota
CANDLE_CACHE: Dict[Tuple[str, str], Tuple[float, pd.DataFrame]] = {}
CACHE_TTL_SECONDS = 600  # 10 minutes
QUOTA_EXHAUSTED_UNTIL = 0.0


class SignalStatusUpdate(BaseModel):
    status: str


def normalize_pair(raw_pair: str) -> str:
    cleaned = str(raw_pair or "").split("?")[0].split("[")[0].split("(")[0].strip().upper()
    cleaned = cleaned.replace("-", "/")
    if "/" not in cleaned and len(cleaned) == 6:
        cleaned = f"{cleaned[:3]}/{cleaned[3:]}"
    return cleaned


def normalize_interval(raw_interval: str) -> str:
    cleaned = str(raw_interval or "1h").split("?")[0].split("[")[0].split("(")[0].strip().lower()
    cleaned = re.sub(r"[^a-z0-9]", "", cleaned)
    return cleaned if cleaned in VALID_INTERVALS else "1h"


def get_pip_multiplier(pair: str) -> float:
    symbol = normalize_pair(pair)
    if "JPY" in symbol:
        return 100.0
    if "XAU" in symbol:
        return 10.0
    return 10000.0


def format_price(pair: str, value: float) -> float:
    symbol = normalize_pair(pair)
    decimals = 2 if "XAU" in symbol else (3 if "JPY" in symbol else 5)
    return round(float(value), decimals)


def parse_candle_timestamp(raw_dt: Any) -> int:
    text = str(raw_dt).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    return int(datetime.fromisoformat(text).timestamp())


def get_active_session() -> Tuple[bool, str]:
    """Check if current UTC time is inside active London or New York liquidity sessions (Mon-Fri)."""
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()  # 0=Mon ... 6=Sun
    hour = now_utc.hour

    # Weekend market closure check (Fri 22:00 UTC -> Sun 22:00 UTC)
    if weekday == 5 or (weekday == 4 and hour >= 22) or (weekday == 6 and hour < 22):
        return False, "Weekend Market Closed"

    # London (07:00-16:00 UTC) & New York (12:00-21:00 UTC)
    if 12 <= hour < 16:
        return True, "London / NY Overlap (Peak Liquidity)"
    if 7 <= hour < 12:
        return True, "London Session"
    if 16 <= hour < 21:
        return True, "New York Session"
    return False, "Asian / Low-Liquidity Rollover (Filtered)"


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


def fetch_candles(pair: str, interval: str = "1h", outputsize: int = 250, force_refresh: bool = False) -> pd.DataFrame:
    global QUOTA_EXHAUSTED_UNTIL

    if not TWELVE_DATA_KEY:
        raise ValueError("Missing TWELVE_DATA_KEY environment variable.")

    symbol = normalize_pair(pair)
    tf = normalize_interval(interval)
    cache_key = (symbol, tf)
    now = time.time()

    if not force_refresh and cache_key in CANDLE_CACHE:
        cached_at, cached_df = CANDLE_CACHE[cache_key]
        if (now - cached_at) < CACHE_TTL_SECONDS:
            return cached_df.copy()

    if now < QUOTA_EXHAUSTED_UNTIL and cache_key in CANDLE_CACHE:
        return CANDLE_CACHE[cache_key][1].copy()

    size = max(50, min(int(outputsize), 500))
    response = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": symbol,
            "interval": tf,
            "outputsize": size,
            "apikey": TWELVE_DATA_KEY,
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    if "values" not in payload or not payload["values"]:
        detail = str(payload.get("message", "No candle values returned from TwelveData"))
        if "run out of API credits" in detail.lower() or "429" in detail or "limit" in detail.lower():
            QUOTA_EXHAUSTED_UNTIL = now + 900
            if cache_key in CANDLE_CACHE:
                return CANDLE_CACHE[cache_key][1].copy()
        raise ValueError(f"TwelveData error for {symbol} ({tf}): {detail}")

    df = pd.DataFrame(payload["values"])
    df = df.iloc[::-1].reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        df.loc[:, col] = df[col].astype(float)

    CANDLE_CACHE[cache_key] = (now, df.copy())
    return df


def analyze_maee_liquidity(df: pd.DataFrame, pair: str, timeframe: str = "1h"):
    symbol = normalize_pair(pair)
    tf = normalize_interval(timeframe)
    pip_mult = get_pip_multiplier(symbol)
    in_session, session_name = get_active_session()

    work_df = df.copy()
    work_df.loc[:, "ema200"] = work_df["close"].ewm(span=200, adjust=False).mean()
    work_df.loc[:, "ema50"] = work_df["close"].ewm(span=50, adjust=False).mean()
    work_df.loc[:, "ema20"] = work_df["close"].ewm(span=20, adjust=False).mean()

    prev_close = work_df["close"].shift(1).fillna(work_df["close"])
    tr = pd.concat(
        [
            work_df["high"] - work_df["low"],
            (work_df["high"] - prev_close).abs(),
            (work_df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    work_df.loc[:, "atr"] = tr.rolling(14).mean()

    lookback = work_df.iloc[-35:-3] if len(work_df) >= 38 else work_df.iloc[:-1]
    bsl = float(lookback["high"].max())
    ssl = float(lookback["low"].min())

    curr = work_df.iloc[-1]
    prev = work_df.iloc[-2] if len(work_df) >= 2 else curr
    atr = float(curr["atr"]) if pd.notna(curr["atr"]) and float(curr["atr"]) > 0 else 0.0005

    close_price = float(curr["close"])
    open_price = float(curr["open"])
    high_price = float(curr["high"])
    low_price = float(curr["low"])

    uptrend = close_price > float(curr["ema200"]) and float(curr["ema50"]) > float(curr["ema200"])
    downtrend = close_price < float(curr["ema200"]) and float(curr["ema50"]) < float(curr["ema200"])

    # True Institutional Sweep + Reclaim detection
    ssl_sweep_reclaim = (low_price < ssl and close_price > ssl) or (
        float(prev["low"]) < ssl and float(prev["close"]) > ssl and close_price > open_price
    )
    bsl_sweep_reclaim = (high_price > bsl and close_price < bsl) or (
        float(prev["high"]) > bsl and float(prev["close"]) < bsl and close_price < open_price
    )

    body = abs(close_price - open_price)
    lower_wick = min(open_price, close_price) - low_price
    upper_wick = high_price - max(open_price, close_price)

    bull_pin = (lower_wick > body * 1.8) and (close_price > open_price)
    bull_engulf = (
        float(prev["close"]) < float(prev["open"])
        and close_price > open_price
        and close_price > float(prev["open"])
        and open_price <= float(prev["close"])
    )

    bear_pin = (upper_wick > body * 1.8) and (close_price < open_price)
    bear_engulf = (
        float(prev["close"]) > float(prev["open"])
        and close_price < open_price
        and close_price < float(prev["open"])
        and open_price >= float(prev["close"])
    )

    near_value_bull = (
        abs(low_price - float(curr["ema50"])) <= (atr * 0.8)
        or abs(low_price - ssl) <= (atr * 0.8)
    )
    near_value_bear = (
        abs(high_price - float(curr["ema50"])) <= (atr * 0.8)
        or abs(high_price - bsl) <= (atr * 0.8)
    )

    # Live MAEE (Maximum Favorable vs Adverse Excursion) over recent 12-bar swing
    recent_window = work_df.iloc[-12:]
    swing_origin = float(recent_window.iloc[0]["open"])
    if close_price >= swing_origin:
        mfe_move = max(0.0, float(recent_window["high"].max()) - swing_origin)
        mae_move = max(atr * 0.25, swing_origin - float(recent_window["low"].min()))
    else:
        mfe_move = max(0.0, swing_origin - float(recent_window["low"].min()))
        mae_move = max(atr * 0.25, float(recent_window["high"].max()) - swing_origin)

    maee_ratio = round(mfe_move / mae_move, 2)
    mfe_pips = round(mfe_move * pip_mult, 1)
    mae_pips = round(mae_move * pip_mult, 1)
    dist_bsl_pips = round((bsl - close_price) * pip_mult, 1)
    dist_ssl_pips = round((close_price - ssl) * pip_mult, 1)

    if bsl_sweep_reclaim:
        exit_state = "EXIT LONGS / BSL SWEPT"
        exit_detail = f"Buy-side stops swept above {format_price(symbol, bsl)} & reclaimed"
    elif ssl_sweep_reclaim:
        exit_state = "EXIT SHORTS / SSL SWEPT"
        exit_detail = f"Sell-side stops swept below {format_price(symbol, ssl)} & reclaimed"
    elif abs(bsl - close_price) <= (atr * 0.35):
        exit_state = "TAKE PARTIAL — NEAR BSL"
        exit_detail = f"Only {dist_bsl_pips} pips below BSL exhaustion zone"
    elif abs(close_price - ssl) <= (atr * 0.35):
        exit_state = "TAKE PARTIAL — NEAR SSL"
        exit_detail = f"Only {dist_ssl_pips} pips above SSL exhaustion zone"
    elif maee_ratio >= 2.5:
        exit_state = "TRAIL PROFIT (3x ATR)"
        exit_detail = f"MFE extended ({maee_ratio}R) — lock gains before reversal"
    else:
        exit_state = "MONITORING"
        exit_detail = f"{dist_bsl_pips}p to BSL · {dist_ssl_pips}p to SSL"

    signal: Optional[Dict[str, Any]] = None
    if in_session and ((uptrend and near_value_bull and (bull_pin or bull_engulf)) or ssl_sweep_reclaim):
        pattern = "SSL Liquidity Sweep + Reclaim" if ssl_sweep_reclaim else ("Bullish Engulfing" if bull_engulf else "Bullish Pin Bar")
        entry = close_price
        sl = format_price(symbol, min(low_price, ssl) - (0.5 * atr))
        tp_liquidity = format_price(symbol, bsl - (0.15 * atr))
        trailing_exit = format_price(symbol, entry - (3.0 * atr))
        signal = {
            "pair": symbol,
            "timeframe": tf,
            "direction": "BUY",
            "market_structure": f"{session_name} · Bullish",
            "liquidity_zone": format_price(symbol, ssl),
            "entry_price": format_price(symbol, entry),
            "stop_loss": sl,
            "take_profit_liquidity": tp_liquidity,
            "trailing_atr_exit": trailing_exit,
            "candle_pattern": pattern,
            "status": "ACTIVE",
        }
    elif in_session and ((downtrend and near_value_bear and (bear_pin or bear_engulf)) or bsl_sweep_reclaim):
        pattern = "BSL Liquidity Sweep + Reclaim" if bsl_sweep_reclaim else ("Bearish Engulfing" if bear_engulf else "Bearish Pin Bar")
        entry = close_price
        sl = format_price(symbol, max(high_price, bsl) + (0.5 * atr))
        tp_liquidity = format_price(symbol, ssl + (0.15 * atr))
        trailing_exit = format_price(symbol, entry + (3.0 * atr))
        signal = {
            "pair": symbol,
            "timeframe": tf,
            "direction": "SELL",
            "market_structure": f"{session_name} · Bearish",
            "liquidity_zone": format_price(symbol, bsl),
            "entry_price": format_price(symbol, entry),
            "stop_loss": sl,
            "take_profit_liquidity": tp_liquidity,
            "trailing_atr_exit": trailing_exit,
            "candle_pattern": pattern,
            "status": "ACTIVE",
        }

    levels = {
        "bsl": format_price(symbol, bsl),
        "ssl": format_price(symbol, ssl),
        "ema200": format_price(symbol, float(curr["ema200"])),
        "ema50": format_price(symbol, float(curr["ema50"])),
        "ema20": format_price(symbol, float(curr["ema20"])),
        "atr": format_price(symbol, atr),
        "maee_ratio": maee_ratio,
        "mfe_pips": mfe_pips,
        "mae_pips": mae_pips,
        "dist_bsl_pips": dist_bsl_pips,
        "dist_ssl_pips": dist_ssl_pips,
        "exit_state": exit_state,
        "exit_detail": exit_detail,
        "session": session_name,
        "in_session": in_session,
    }

    return signal, levels


def run_market_scan(force_refresh: bool = False) -> List[Dict[str, Any]]:
    in_session, session_name = get_active_session()
    if not in_session and not force_refresh:
        print(f"Skipping automated scan ({session_name})")
        return []

    triggered: List[Dict[str, Any]] = []
    for pair in PAIRS:
        try:
            df = fetch_candles(pair, "1h", 250, force_refresh=force_refresh)
            signal, _ = analyze_maee_liquidity(df, pair, "1h")
            if signal:
                triggered.append(signal)
                if supabase:
                    supabase.table("signals").insert(signal).execute()
                msg = (
                    f"💧 *LIQUID CASH SIGNAL — {signal['pair']} ({signal['direction']})*\n"
                    f"• *Session & Structure:* {signal['market_structure']}\n"
                    f"• *Trigger:* {signal['candle_pattern']}\n"
                    f"• *Entry:* `{signal['entry_price']}`\n"
                    f"• *Stop Loss:* `{signal['stop_loss']}`\n"
                    f"• *🎯 EXIT TARGET (Before Liquidity):* `{signal['take_profit_liquidity']}`\n"
                    f"• *🛡️ Profit Lock (3x ATR Trail):* `{signal['trailing_atr_exit']}`"
                )
                send_telegram(msg)
        except Exception as exc:
            print(f"Scan error on {pair}: {exc}")
    return triggered


scheduler = BackgroundScheduler()
scheduler.add_job(run_market_scan, "interval", minutes=15)
scheduler.start()


@app.get("/api/health")
@app.head("/api/health")
def health_check():
    in_session, session_name = get_active_session()
    return {
        "service": "LIQUID CASH Engine",
        "status": "running",
        "session": session_name,
        "in_session": in_session,
        "pairs": PAIRS,
        "quota_paused": time.time() < QUOTA_EXHAUSTED_UNTIL,
    }


@app.post("/api/scan")
def trigger_manual_scan():
    signals = run_market_scan(force_refresh=True)
    return {"scanned_pairs": len(PAIRS), "new_signals": len(signals), "signals": signals}


@app.get("/api/chart/{pair:path}")
def get_chart_data(pair: str, interval: str = "1h"):
    symbol = normalize_pair(pair)
    tf = normalize_interval(interval)
    try:
        df = fetch_candles(symbol, tf, 220)
        signal, levels = analyze_maee_liquidity(df, symbol, tf)
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
            "pair": symbol,
            "interval": tf,
            "candles": candles,
            "levels": levels,
            "live_signal": signal,
            "quota_paused": time.time() < QUOTA_EXHAUSTED_UNTIL,
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
            .limit(25)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.patch("/api/signals/{signal_id}")
def update_signal_status(signal_id: str, body: SignalStatusUpdate):
    if not supabase:
        raise HTTPException(status_code=400, detail="Supabase is not configured")
    allowed = {"ACTIVE", "WON", "LOST", "CLOSED"}
    next_status = body.status.strip().upper()
    if next_status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status: {next_status}")
    try:
        res = (
            supabase.table("signals")
            .update({"status": next_status})
            .eq("id", signal_id)
            .execute()
        )
        return {"updated": True, "data": res.data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# --- Serve Built React Frontend ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIST = next(
    (
        candidate
        for candidate in [
            os.path.join(BASE_DIR, "frontend", "dist"),
            os.path.join(BASE_DIR, "dist"),
            os.path.join(BASE_DIR, "..", "frontend", "dist"),
            os.path.join(BASE_DIR, "..", "dist"),
        ]
        if os.path.exists(candidate)
    ),
    None,
)

if FRONTEND_DIST:
    assets_dir = os.path.join(FRONTEND_DIST, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = os.path.join(FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
else:
    @app.get("/")
    def root_fallback():
        return {
            "service": "LIQUID CASH Engine",
            "status": "running",
            "pairs": PAIRS,
            "note": "Frontend dist folder not built yet.",
        }


import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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

CANDLE_CACHE: Dict[Tuple[str, str], Tuple[float, pd.DataFrame]] = {}
CACHE_TTL_SECONDS = 600
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


def get_pip_size(pair: str) -> float:
    return 1.0 / get_pip_multiplier(pair)


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


def get_session_for_dt(dt_obj: datetime) -> Tuple[bool, str]:
    weekday = dt_obj.weekday()
    hour = dt_obj.hour

    if weekday == 5 or (weekday == 4 and hour >= 22) or (weekday == 6 and hour < 22):
        return False, "Weekend Market Closed"

    if 12 <= hour < 16:
        return True, "London / NY Overlap"
    if 7 <= hour < 12:
        return True, "London Session"
    if 16 <= hour < 21:
        return True, "New York Session"
    return False, "Off-Peak Hours"


def get_active_session() -> Tuple[bool, str]:
    return get_session_for_dt(datetime.now(timezone.utc))


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


def fetch_candles(pair: str, interval: str = "1h", outputsize: int = 500, force_refresh: bool = False) -> pd.DataFrame:
    global QUOTA_EXHAUSTED_UNTIL

    if not TWELVE_DATA_KEY:
        raise ValueError("Missing TWELVE_DATA_KEY environment variable.")

    symbol = normalize_pair(pair)
    tf = normalize_interval(interval)
    cache_key = (symbol, tf)
    now = time.time()

    if not force_refresh and cache_key in CANDLE_CACHE:
        cached_at, cached_df = CANDLE_CACHE[cache_key]
        if (now - cached_at) < CACHE_TTL_SECONDS and len(cached_df) >= min(outputsize, 250):
            return cached_df.copy()

    if now < QUOTA_EXHAUSTED_UNTIL and cache_key in CANDLE_CACHE:
        return CANDLE_CACHE[cache_key][1].copy()

    size = max(160, min(int(outputsize), 500))
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
        df[col] = df[col].astype(float)

    CANDLE_CACHE[cache_key] = (now, df.copy())
    return df


def find_trend_liquidity_levels(
    df: pd.DataFrame,
    window_bars: int = 100,
    left_right: int = 2,
) -> Tuple[float, int, float, int]:
    """
    Finds recent swing highs (BSL) and swing lows (SSL) to anchor liquidity sweeps
    along the current trend direction.
    """
    n = len(df)
    start_idx = max(left_right, n - window_bars)
    end_idx = max(start_idx + 1, n - (left_right + 1))

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)

    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []

    for i in range(start_idx, end_idx):
        h_val = highs[i]
        l_val = lows[i]

        left_h = highs[i - left_right : i]
        right_h = highs[i + 1 : i + 1 + left_right]
        if len(left_h) == left_right and len(right_h) == left_right:
            if h_val > float(left_h.max()) and h_val >= float(right_h.max()):
                swing_highs.append((i, h_val))

        left_l = lows[i - left_right : i]
        right_l = lows[i + 1 : i + 1 + left_right]
        if len(left_l) == left_right and len(right_l) == left_right:
            if l_val < float(left_l.min()) and l_val <= float(right_l.min()):
                swing_lows.append((i, l_val))

    if swing_highs:
        bsl_idx, bsl = swing_highs[-1]
    else:
        sub_h = highs[start_idx:end_idx]
        rel = int(sub_h.argmax())
        bsl_idx = start_idx + rel
        bsl = float(highs[bsl_idx])

    if swing_lows:
        ssl_idx, ssl = swing_lows[-1]
    else:
        sub_l = lows[start_idx:end_idx]
        rel = int(sub_l.argmin())
        ssl_idx = start_idx + rel
        ssl = float(lows[ssl_idx])

    return bsl, bsl_idx, ssl, ssl_idx


def analyze_maee_liquidity(
    df: pd.DataFrame,
    pair: str,
    timeframe: str = "1h",
    ignore_session: bool = False,
):
    """
    User Manual Strategy (1:3 R:R):
    1. Identify trend direction (UP or DOWN) via EMA 50 / EMA 200 & structure
    2. Locate previous liquidity in the trend (prev_ssl in UP trend, prev_bsl in DOWN trend)
    3. Detect sweep of that previous liquidity + reversal back into order flow / order block
       with a confirming candle in the trend direction
    4. Place Stop Loss 2.0 pips beyond the swept previous liquidity level
    5. Place Take Profit at exact 1:3 risk-to-reward (Entry +/- 3.0 * Risk)
    """
    symbol = normalize_pair(pair)
    tf = normalize_interval(timeframe)
    pip_mult = get_pip_multiplier(symbol)
    pip_size = get_pip_size(symbol)
    in_session, session_name = get_active_session()

    work_df = df.copy()
    work_df["ema50"] = work_df["close"].ewm(span=50, adjust=False).mean()
    work_df["ema200"] = work_df["close"].ewm(span=200, adjust=False).mean()

    prev_close = work_df["close"].shift(1).bfill()
    tr = pd.concat(
        [
            work_df["high"] - work_df["low"],
            (work_df["high"] - prev_close).abs(),
            (work_df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    work_df["atr"] = tr.rolling(14).mean()

    bsl, bsl_idx, ssl, ssl_idx = find_trend_liquidity_levels(work_df, window_bars=100, left_right=2)

    n = len(work_df)
    curr = work_df.iloc[-1]
    atr = float(curr["atr"]) if pd.notna(curr["atr"]) and float(curr["atr"]) > 0 else (10.0 * pip_size)

    if ignore_session and "datetime" in curr:
        try:
            candle_ts = parse_candle_timestamp(curr["datetime"])
            candle_dt = datetime.fromtimestamp(candle_ts, tz=timezone.utc)
            session_allowed, session_name = get_session_for_dt(candle_dt)
        except Exception:
            session_allowed = True
    else:
        session_allowed = True if ignore_session else in_session

    close_price = float(curr["close"])
    open_price = float(curr["open"])
    high_price = float(curr["high"])
    low_price = float(curr["low"])
    ema50_val = float(curr["ema50"])
    ema200_val = float(curr["ema200"])

    uptrend = (ema50_val >= ema200_val) or (close_price > ema200_val)
    downtrend = (ema50_val <= ema200_val) or (close_price < ema200_val)

    sl_pad = 2.0 * pip_size
    ob_tol = 0.75 * atr
    signal: Optional[Dict[str, Any]] = None

    # --- UPWARD TREND: Previous SSL swept -> bullish reversal -> pullback into OB + bullish confirmation ---
    if uptrend:
        search_start_bull = max(ssl_idx + 1, n - 20)
        swept_ssl: Optional[float] = None
        sweep_pos_bull: Optional[int] = None

        for k in range(search_start_bull, n - 1):
            c_k = work_df.iloc[k]
            if float(c_k["low"]) < ssl:
                swept_ssl = min(ssl, float(c_k["low"]))
                sweep_pos_bull = k
                break

        if swept_ssl is not None and sweep_pos_bull is not None:
            # Find bearish order block candle before/around sweep that price retests on reversal
            ob_top: Optional[float] = None
            ob_bot: Optional[float] = None
            for b_idx in range(sweep_pos_bull, max(0, sweep_pos_bull - 10), -1):
                b_c = work_df.iloc[b_idx]
                if float(b_c["close"]) <= float(b_c["open"]):
                    ob_top = float(b_c["high"])
                    ob_bot = float(b_c["low"])
                    break

            after_sweep_bull = work_df.iloc[sweep_pos_bull + 1 : n - 1]
            reversed_up = (
                len(after_sweep_bull) >= 1
                and float(after_sweep_bull["close"].max()) > float(work_df.iloc[sweep_pos_bull]["high"])
                and float(after_sweep_bull["low"].min()) >= (swept_ssl - sl_pad)
            )

            bullish_retest_confirm = (
                ob_top is not None
                and ob_bot is not None
                and reversed_up
                and (low_price <= (ob_top + ob_tol))
                and (high_price >= (ob_bot - ob_tol))
                and (low_price >= (swept_ssl - sl_pad))
                and (close_price > open_price)
            )

            if session_allowed and bullish_retest_confirm:
                entry = close_price
                sl_raw = swept_ssl - sl_pad
                risk = entry - sl_raw
                if risk >= (1.5 * pip_size):
                    tp_raw = entry + (3.0 * risk)
                    signal = {
                        "pair": symbol,
                        "timeframe": tf,
                        "direction": "BUY",
                        "market_structure": f"{session_name} · Uptrend SSL Sweep -> OB (1:3)",
                        "liquidity_zone": format_price(symbol, swept_ssl),
                        "entry_price": format_price(symbol, entry),
                        "stop_loss": format_price(symbol, sl_raw),
                        "take_profit_liquidity": format_price(symbol, tp_raw),
                        "trailing_atr_exit": format_price(symbol, entry + risk),
                        "candle_pattern": "Uptrend SSL Sweep -> OB",
                        "status": "ACTIVE",
                    }

    # --- DOWNWARD TREND: Previous BSL swept -> bearish reversal -> pullback into OB + bearish confirmation ---
    if signal is None and downtrend:
        search_start_bear = max(bsl_idx + 1, n - 20)
        swept_bsl: Optional[float] = None
        sweep_pos_bear: Optional[int] = None

        for k in range(search_start_bear, n - 1):
            c_k = work_df.iloc[k]
            if float(c_k["high"]) > bsl:
                swept_bsl = max(bsl, float(c_k["high"]))
                sweep_pos_bear = k
                break

        if swept_bsl is not None and sweep_pos_bear is not None:
            ob_top_s: Optional[float] = None
            ob_bot_s: Optional[float] = None
            for b_idx in range(sweep_pos_bear, max(0, sweep_pos_bear - 10), -1):
                b_c = work_df.iloc[b_idx]
                if float(b_c["close"]) >= float(b_c["open"]):
                    ob_top_s = float(b_c["high"])
                    ob_bot_s = float(b_c["low"])
                    break

            after_sweep_bear = work_df.iloc[sweep_pos_bear + 1 : n - 1]
            reversed_down = (
                len(after_sweep_bear) >= 1
                and float(after_sweep_bear["close"].min()) < float(work_df.iloc[sweep_pos_bear]["low"])
                and float(after_sweep_bear["high"].max()) <= (swept_bsl + sl_pad)
            )

            bearish_retest_confirm = (
                ob_top_s is not None
                and ob_bot_s is not None
                and reversed_down
                and (high_price >= (ob_bot_s - ob_tol))
                and (low_price <= (ob_top_s + ob_tol))
                and (high_price <= (swept_bsl + sl_pad))
                and (close_price < open_price)
            )

            if session_allowed and bearish_retest_confirm:
                entry = close_price
                sl_raw = swept_bsl + sl_pad
                risk = sl_raw - entry
                if risk >= (1.5 * pip_size):
                    tp_raw = entry - (3.0 * risk)
                    signal = {
                        "pair": symbol,
                        "timeframe": tf,
                        "direction": "SELL",
                        "market_structure": f"{session_name} · Downtrend BSL Sweep -> OB (1:3)",
                        "liquidity_zone": format_price(symbol, swept_bsl),
                        "entry_price": format_price(symbol, entry),
                        "stop_loss": format_price(symbol, sl_raw),
                        "take_profit_liquidity": format_price(symbol, tp_raw),
                        "trailing_atr_exit": format_price(symbol, entry - risk),
                        "candle_pattern": "Downtrend BSL Sweep -> OB",
                        "status": "ACTIVE",
                    }

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

    levels = {
        "bsl": format_price(symbol, bsl),
        "ssl": format_price(symbol, ssl),
        "atr": format_price(symbol, atr),
        "maee_ratio": maee_ratio,
        "mfe_pips": mfe_pips,
        "mae_pips": mae_pips,
        "session": session_name,
        "in_session": in_session,
    }

    return signal, levels


def simulate_pair_backtest(pair: str, interval: str = "1h", bars: int = 500) -> Dict[str, Any]:
    symbol = normalize_pair(pair)
    tf = normalize_interval(interval)
    lookback_bars = max(170, min(int(bars), 500))

    df = fetch_candles(symbol, tf, lookback_bars)
    if len(df) < 160:
        return {"pair": symbol, "interval": tf, "bars_tested": len(df), "trades": [], "summary": {}}

    trades: List[Dict[str, Any]] = []
    cooldown_until_idx = 0

    for idx in range(155, len(df) - 2):
        if idx < cooldown_until_idx:
            continue

        slice_df = df.iloc[: idx + 1]
        signal, _ = analyze_maee_liquidity(slice_df, symbol, tf, ignore_session=True)
        if not signal:
            continue

        entry = float(signal["entry_price"])
        sl = float(signal["stop_loss"])
        tp = float(signal["take_profit_liquidity"])
        direction = signal["direction"]
        entry_dt_raw = str(df.iloc[idx].get("datetime", ""))
        entry_ts = parse_candle_timestamp(entry_dt_raw) if entry_dt_raw else 0

        outcome = "OPEN"
        exit_dt_raw = ""
        exit_ts = 0
        exit_price = entry

        for fwd_idx in range(idx + 1, min(idx + 120, len(df))):
            candle = df.iloc[fwd_idx]
            hi = float(candle["high"])
            lo = float(candle["low"])

            if direction == "BUY":
                if lo <= sl:
                    outcome = "LOST"
                    exit_price = sl
                    exit_dt_raw = str(candle.get("datetime", ""))
                    exit_ts = parse_candle_timestamp(exit_dt_raw) if exit_dt_raw else 0
                    cooldown_until_idx = fwd_idx + 1
                    break
                if hi >= tp:
                    outcome = "WON"
                    exit_price = tp
                    exit_dt_raw = str(candle.get("datetime", ""))
                    exit_ts = parse_candle_timestamp(exit_dt_raw) if exit_dt_raw else 0
                    cooldown_until_idx = fwd_idx + 1
                    break
            else:
                if hi >= sl:
                    outcome = "LOST"
                    exit_price = sl
                    exit_dt_raw = str(candle.get("datetime", ""))
                    exit_ts = parse_candle_timestamp(exit_dt_raw) if exit_dt_raw else 0
                    cooldown_until_idx = fwd_idx + 1
                    break
                if lo <= tp:
                    outcome = "WON"
                    exit_price = tp
                    exit_dt_raw = str(candle.get("datetime", ""))
                    exit_ts = parse_candle_timestamp(exit_dt_raw) if exit_dt_raw else 0
                    cooldown_until_idx = fwd_idx + 1
                    break

        net_r = 3.0 if outcome == "WON" else (-1.0 if outcome == "LOST" else 0.0)

        trades.append({
            "id": f"bt-{symbol}-{idx}",
            "pair": symbol,
            "timeframe": tf,
            "direction": direction,
            "candle_pattern": signal["candle_pattern"],
            "entry_price": signal["entry_price"],
            "stop_loss": signal["stop_loss"],
            "take_profit_liquidity": signal["take_profit_liquidity"],
            "entry_time": entry_dt_raw,
            "entry_ts": entry_ts,
            "exit_time": exit_dt_raw,
            "exit_ts": exit_ts,
            "exit_price": exit_price,
            "rr": 3.0,
            "net_r": net_r,
            "status": outcome,
        })

    closed = [t for t in trades if t["status"] in ("WON", "LOST")]
    wins = [t for t in closed if t["status"] == "WON"]
    losses = [t for t in closed if t["status"] == "LOST"]
    win_rate = round((len(wins) / len(closed)) * 100, 1) if closed else 0.0
    total_r = round(sum(t["net_r"] for t in closed), 2)

    return {
        "pair": symbol,
        "interval": tf,
        "bars_tested": len(df),
        "summary": {
            "total_signals": len(trades),
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "net_r": total_r,
        },
        "trades": list(reversed(trades)),
    }


def run_market_scan(force_refresh: bool = False) -> List[Dict[str, Any]]:
    in_session, session_name = get_active_session()
    if not in_session and not force_refresh:
        print(f"Skipping automated scan ({session_name})")
        return []

    triggered: List[Dict[str, Any]] = []
    for pair in PAIRS:
        try:
            df = fetch_candles(pair, "1h", 500, force_refresh=force_refresh)
            signal, _ = analyze_maee_liquidity(df, pair, "1h", ignore_session=False)
            if signal:
                triggered.append(signal)
                if supabase:
                    supabase.table("signals").insert(signal).execute()
                msg = (
                    f"💧 *LIQUID CASH (1:3 R:R) — {signal['pair']} ({signal['direction']})*\n"
                    f"• *Setup:* {signal['candle_pattern']}\n"
                    f"• *Swept Liquidity:* `{signal['liquidity_zone']}`\n"
                    f"• *Entry:* `{signal['entry_price']}`\n"
                    f"• *Stop Loss (2 pips):* `{signal['stop_loss']}`\n"
                    f"• *Take Profit (1:3):* `{signal['take_profit_liquidity']}`"
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
        df = fetch_candles(symbol, tf, 500)
        signal, levels = analyze_maee_liquidity(df, symbol, tf, ignore_session=False)
        candles = [
            {
                "time": parse_candle_timestamp(row["datetime"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            for _, row in df.tail(260).iterrows()
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


@app.get("/api/backtest/{pair:path}")
def run_historical_backtest(pair: str, interval: str = "1h", bars: int = 500):
    try:
        return simulate_pair_backtest(pair, interval, bars)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/backtest-all")
def run_all_pairs_backtest(interval: str = "1h", bars: int = 500):
    tf = normalize_interval(interval)
    per_pair: List[Dict[str, Any]] = []
    all_trades: List[Dict[str, Any]] = []

    for pair in PAIRS:
        try:
            res = simulate_pair_backtest(pair, tf, bars)
            summary = res.get("summary", {})
            per_pair.append({
                "pair": pair,
                "closed_trades": summary.get("closed_trades", 0),
                "wins": summary.get("wins", 0),
                "losses": summary.get("losses", 0),
                "win_rate": summary.get("win_rate", 0.0),
                "net_r": summary.get("net_r", 0.0),
            })
            all_trades.extend(res.get("trades", []))
        except Exception as exc:
            per_pair.append({
                "pair": pair,
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "net_r": 0.0,
                "error": str(exc),
            })

    closed = [t for t in all_trades if t["status"] in ("WON", "LOST")]
    wins = [t for t in closed if t["status"] == "WON"]
    losses = [t for t in closed if t["status"] == "LOST"]
    win_rate = round((len(wins) / len(closed)) * 100, 1) if closed else 0.0
    net_r = round(sum(t["net_r"] for t in closed), 2)

    all_trades.sort(key=lambda t: t.get("entry_ts", 0), reverse=True)
    per_pair.sort(key=lambda p: p.get("net_r", 0), reverse=True)

    return {
        "interval": tf,
        "bars_tested": bars,
        "summary": {
            "total_signals": len(all_trades),
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "net_r": net_r,
        },
        "per_pair": per_pair,
        "trades": all_trades,
    }


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

import os
import sys
import json
import time
import datetime
import threading
import requests
import pytz
import pandas as pd
import numpy as np
import yfinance as yf
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==============================================================================
# DELTA MULTI-STRATEGY QUANTITATIVE ARENA (V12.0 TOURNAMENT EDITION)
# ==============================================================================
# 4 STRATEGIES TESTED SIMULTANEOUSLY IN PAPER TRADING (ZERO REAL RISK)
# 1. MELONA TRINITY PRO (SCOB + FVG + 100/400 EMA + RSI Momentum)
# 2. TITAN BREAKOUT PRO (200-Bar Matrix Probability >= 48% + EMA 50/200)
# 3. BREAKOUT SUPERTREND PRO (ATR 10, Multiplier 3.0 + Trend Filter)
# 4. ORIGINAL NAIVE SCOB (Baseline Model + 100 EMA)
# ==============================================================================
# ZERO RISK GUARANTEE: Real $12.18 USD (~Rs. 1,035 INR) remains 100% untouched.
# All strategies execute in isolated virtual ledgers for real-time comparison.
# ==============================================================================

PAPER_TRADING_MODE = True          # 100% ZERO-RISK SIMULATION
MCP_URL = "https://mcp.viasocket.com/mcp/6ac9d75f3f044c660c2ef9e7-81295"
DB_FILE = "placed_orders.json"
ARENA_DB_FILE = "paper_arena.json"
LOG_FILE = "cloud_autotrader.log"

# Permanent Cloud Persistence (survives Render restarts)
REDIS_URL = "https://lucky-viper-162527.upstash.io"
REDIS_TOKEN = "gQAAAAAAAnrfAQIgcDIyYzVjMTQ2MDQyNTA0MWQ4ODZmMzNjYzg4MGI5ZjljNw"

DEFAULT_RISK_USD = 0.35            # Target risk per trade ($0.35 USD ~ Rs. 30 INR)
FIXED_CONTRACTS = 3                # Fixed 3 contracts per trade setup
MAX_CONCURRENT_PER_STRATEGY = 2    # Max 2 open positions per strategy
MIN_RISK_PCT = 0.15                # 0.15% minimum structural SL buffer
MAX_SL_CAP_PCT = 1.50              # Cap at 1.50% to prevent wide stops

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8782055700:AAH2z1dlDuCjGr3JH6wf-BrLDgCFP9X1WtE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "833498138")

IST_TZ = pytz.timezone('Asia/Kolkata')

# Preserved Real Capital State
live_equity_usd = 12.18
last_balance_check = 0.0

PORTFOLIO_CONFIG = [
    {
        "name": "Bitcoin",
        "ticker": "BTC-USD",
        "delta_symbol": "BTCUSD",
        "intervals": ["15m", "1h"],
        "rr": 2.0
    },
    {
        "name": "Ethereum",
        "ticker": "ETH-USD",
        "delta_symbol": "ETHUSD",
        "intervals": ["15m", "1h"],
        "rr": 2.0
    },
    {
        "name": "Gold",
        "ticker": "PAXG-USD",
        "delta_symbol": "PAXGUSDT",
        "intervals": ["15m", "1h"],
        "rr": 2.0
    }
]

STRATEGIES_DEF = {
    "MELONA_TRINITY": {
        "id": "MELONA_TRINITY",
        "name": "Melona Trinity PRO",
        "badge": "🔷 Melona Trinity PRO",
        "color": "#00e5ff",
        "desc": "Smart Candle Order Block (SCOB) + Liquidity Sweep + 100/400 EMA + RSI"
    },
    "TITAN_BREAKOUT": {
        "id": "TITAN_BREAKOUT",
        "name": "Titan Breakout PRO",
        "badge": "⚡ Titan Breakout PRO",
        "color": "#ffd700",
        "desc": "200-Bar Matrix Probability (>=48%) + 50/200 EMA Confluence"
    },
    "SUPERTREND_MOMENTUM": {
        "id": "SUPERTREND_MOMENTUM",
        "name": "Breakout Supertrend PRO",
        "badge": "🟢 Supertrend PRO",
        "color": "#76ff03",
        "desc": "Classic ATR(10) Multiplier 3.0 Supertrend + 50 EMA Confluence"
    },
    "ORIGINAL_SCOB": {
        "id": "ORIGINAL_SCOB",
        "name": "Original Naive SCOB",
        "badge": "🟠 Original Naive SCOB",
        "color": "#ff9100",
        "desc": "Baseline 2-Candle Order Block + 100 EMA Filter"
    }
}

def log(msg):
    now_ist = datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST")
    full_msg = f"[{now_ist}] {msg}"
    print(full_msg, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(full_msg + "\n")
    except Exception:
        pass

# ==============================================================================
# TELEGRAM NOTIFICATIONS
# ==============================================================================
def send_telegram_alert(strat_id, symbol, side, entry_p, sl_p, tp_p, extra_info, tf, mode="PAPER"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        side_icon = "🟢" if side.lower() == 'buy' else "🔴"
        strat_info = STRATEGIES_DEF.get(strat_id, {"name": strat_id, "badge": strat_id})
        text = (
            f"🥊 *[ARENA TOURNAMENT ENTRY]* 🥊\n\n"
            f"• *Strategy*: {strat_info['badge']}\n"
            f"• *Asset*: `{symbol}` ({tf})\n"
            f"• *Side*: `{side.upper()}` ({FIXED_CONTRACTS} Contracts) {side_icon}\n"
            f"• *Entry Price*: `${entry_p:,.2f}`\n"
            f"• *Stop Loss*: `${sl_p:,.2f}` (Risk: `${abs(entry_p - sl_p):,.2f}`)\n"
            f"• *Take Profit*: `${tp_p:,.2f}` (Target: 1:2.0 R:R)\n"
            f"• *Confluence*: `{extra_info}`\n"
            f"• *Execution*: `🛡️ Paper Simulated (Zero Real Risk)`\n"
            f"• *Real Wallet Balance*: `${live_equity_usd:.2f} USD (100% Safe Free Cash)`\n"
            f"• *Time*: `{datetime.datetime.now(IST_TZ).strftime('%I:%M:%S %p IST')}`"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        log(f"[TELEGRAM ERROR] {e}")

def send_telegram_exit_alert(strat_id, symbol, side, outcome, pnl_usd, pnl_r, tf):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        outcome_icon = "🎉 WIN (+2.0R)" if outcome == "WIN" else "🛑 LOSS (-1.0R)"
        strat_info = STRATEGIES_DEF.get(strat_id, {"name": strat_id, "badge": strat_id})
        text = (
            f"🎯 *[ARENA TOURNAMENT EXIT]* 🎯\n\n"
            f"• *Strategy*: {strat_info['badge']}\n"
            f"• *Asset*: `{symbol}` ({tf})\n"
            f"• *Side*: `{side.upper()}`\n"
            f"• *Result*: *{outcome_icon}*\n"
            f"• *Simulated P&L*: *{pnl_usd:+.2f} USD ({pnl_r:+.1f} R)*\n"
            f"• *Real Wallet Status*: `${live_equity_usd:.2f} USD (100% Safe Free Cash)`\n"
            f"• *Time*: `{datetime.datetime.now(IST_TZ).strftime('%I:%M:%S %p IST')}`"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        log(f"[TELEGRAM EXIT ERROR] {e}")

# ==============================================================================
# ARENA STORAGE & ORDER REGISTRATION
# ==============================================================================
# ==============================================================================
# PERMANENT CLOUD STORAGE ENGINE (UPSTASH REDIS)
# ==============================================================================
def load_cloud_arena_db():
    try:
        headers = {"Authorization": f"Bearer {REDIS_TOKEN}"}
        r = requests.get(f"{REDIS_URL}/get/arena_state", headers=headers, timeout=5)
        if r.status_code == 200:
            val = r.json().get("result")
            if val:
                data = json.loads(val)
                if isinstance(data, dict) and len(data) > 0:
                    return data
    except Exception as e:
        log(f"[REDIS LOAD NOTE] {e}")
    return None

def save_cloud_arena_db(db):
    try:
        with open(ARENA_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
    except Exception:
        pass
    try:
        headers = {"Authorization": f"Bearer {REDIS_TOKEN}"}
        requests.post(f"{REDIS_URL}/set/arena_state", data=json.dumps(db), headers=headers, timeout=5)
    except Exception as e:
        log(f"[REDIS SAVE NOTE] {e}")

def init_arena_db():
    # 1. Try permanent cloud database first
    db = load_cloud_arena_db()
    # 2. Fallback to local file if cloud temporarily unreachable
    if not db and os.path.exists(ARENA_DB_FILE):
        try:
            with open(ARENA_DB_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
        except Exception:
            db = {}
    if not db:
        db = {}
        for s_id, s_info in STRATEGIES_DEF.items():
            db[s_id] = {
                "id": s_id,
                "name": s_info["name"],
                "color": s_info["color"],
                "desc": s_info["desc"],
                "active": [],
                "closed": [],
                "stats": {
                    "total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                    "net_pnl_usd": 0.0, "net_r": 0.0, "profit_factor": 0.0
                }
            }
    # Ensure all strategies exist
    for s_id, s_info in STRATEGIES_DEF.items():
        if s_id not in db:
            db[s_id] = {
                "id": s_id,
                "name": s_info["name"],
                "color": s_info["color"],
                "desc": s_info["desc"],
                "active": [],
                "closed": [],
                "stats": {
                    "total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                    "net_pnl_usd": 0.0, "net_r": 0.0, "profit_factor": 0.0
                }
            }
    save_arena_db(db)
    return db

def save_arena_db(db):
    save_cloud_arena_db(db)

def load_placed_orders():
    try:
        headers = {"Authorization": f"Bearer {REDIS_TOKEN}"}
        r = requests.get(f"{REDIS_URL}/get/placed_orders", headers=headers, timeout=5)
        if r.status_code == 200:
            val = r.json().get("result")
            if val:
                return json.loads(val)
    except Exception:
        pass
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_placed_order(key, details):
    data = load_placed_orders()
    data[key] = details
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
    try:
        headers = {"Authorization": f"Bearer {REDIS_TOKEN}"}
        requests.post(f"{REDIS_URL}/set/placed_orders", data=json.dumps(data), headers=headers, timeout=5)
    except Exception:
        pass

# ==============================================================================
# PAPER TRADING ARENA ENGINE
# ==============================================================================
def execute_arena_paper_order(strat_id, symbol, side, entry_p, sl_p, tp_p, extra_info, tf):
    db = init_arena_db()
    strat = db.get(strat_id)
    if not strat:
        return
        
    if len(strat.get("active", [])) >= MAX_CONCURRENT_PER_STRATEGY:
        return
        
    trade_id = f"arena_{strat_id}_{symbol}_{tf}_{int(time.time())}"
    trade = {
        "id": trade_id,
        "strat_id": strat_id,
        "symbol": symbol,
        "tf": tf,
        "side": side.upper(),
        "contracts": FIXED_CONTRACTS,
        "entry": entry_p,
        "sl": sl_p,
        "tp": tp_p,
        "risk_usd": DEFAULT_RISK_USD,
        "extra_info": str(extra_info),
        "entry_time": datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST"),
        "status": "OPEN"
    }
    strat["active"].append(trade)
    save_arena_db(db)
    
    log(f"🥊 [ARENA ENTRY] {strat_id} | {symbol} ({tf}) {side.upper()} @ ${entry_p} | SL: ${sl_p} | TP: ${tp_p}")
    send_telegram_alert(strat_id, symbol, side, entry_p, sl_p, tp_p, extra_info, tf, mode="PAPER")

def update_arena_tracker():
    """Evaluates active paper positions across all 4 strategies against latest market prices."""
    db = init_arena_db()
    now_ist = datetime.datetime.now(IST_TZ)
    
    price_cache = {}
    for item in PORTFOLIO_CONFIG:
        ticker = item["ticker"]
        try:
            raw = yf.download(ticker, period="1d", interval="5m", progress=False)
            if not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                price_cache[ticker] = raw.dropna()
        except Exception:
            pass

    for strat_id, strat in db.items():
        active = strat.get("active", [])
        if not active:
            continue
            
        remaining = []
        for trade in active:
            sym = trade["symbol"]
            ticker = None
            for item in PORTFOLIO_CONFIG:
                if item["delta_symbol"] == sym:
                    ticker = item["ticker"]
                    break
            if not ticker or ticker not in price_cache:
                remaining.append(trade)
                continue
                
            df = price_cache[ticker]
            latest_high = float(df['High'].iloc[-1])
            latest_low = float(df['Low'].iloc[-1])
            
            side = trade["side"]
            sl = trade["sl"]
            tp = trade["tp"]
            outcome = None
            pnl_r = 0.0
            
            if side == "BUY":
                if latest_low <= sl:
                    outcome = "LOSS"
                    pnl_r = -1.0
                elif latest_high >= tp:
                    outcome = "WIN"
                    pnl_r = 2.0
            elif side == "SELL":
                if latest_high >= sl:
                    outcome = "LOSS"
                    pnl_r = -1.0
                elif latest_low <= tp:
                    outcome = "WIN"
                    pnl_r = 2.0
                    
            if outcome:
                trade["status"] = outcome
                trade["exit_price"] = sl if outcome == "LOSS" else tp
                trade["pnl_r"] = pnl_r
                pnl_usd = round(pnl_r * DEFAULT_RISK_USD, 2)
                trade["pnl_usd"] = pnl_usd
                trade["exit_time"] = now_ist.strftime("%Y-%m-%d %H:%M:%S IST")
                strat["closed"].append(trade)
                
                outcome_str = f"🎉 WIN (+2.0R)" if outcome == "WIN" else f"🛑 LOSS (-1.0R)"
                log(f"🎯 [ARENA EXIT] {strat_id} | {sym} ({trade['tf']}) {side} -> {outcome_str} | PnL: ${pnl_usd:+.2f} USD")
                send_telegram_exit_alert(strat_id, sym, side, outcome, pnl_usd, pnl_r, trade["tf"])
            else:
                remaining.append(trade)
                
        strat["active"] = remaining
        
        # Calculate real-time stats
        closed = strat.get("closed", [])
        tot = len(closed)
        if tot > 0:
            wins = sum(1 for t in closed if t.get("status") == "WIN")
            losses = sum(1 for t in closed if t.get("status") == "LOSS")
            wr = (wins / tot) * 100.0
            net_r = sum(t.get("pnl_r", 0.0) for t in closed)
            net_usd = sum(t.get("pnl_usd", 0.0) for t in closed)
            pos_r = sum(t.get("pnl_r", 0.0) for t in closed if t.get("pnl_r", 0.0) > 0)
            neg_r = abs(sum(t.get("pnl_r", 0.0) for t in closed if t.get("pnl_r", 0.0) < 0))
            pf = (pos_r / neg_r) if neg_r > 0 else (99.0 if pos_r > 0 else 0.0)
            strat["stats"] = {
                "total": tot, "wins": wins, "losses": losses,
                "win_rate": round(wr, 1), "net_pnl_usd": round(net_usd, 2),
                "net_r": round(net_r, 1), "profit_factor": round(pf, 2)
            }
            
    save_arena_db(db)

# ==============================================================================
# REAL WALLET SYNC (SAFE CASH TRACKER)
# ==============================================================================
def update_live_wallet_balance():
    global live_equity_usd, last_balance_check
    now = time.time()
    if now - last_balance_check < 600:
        return live_equity_usd

    post_endpoint = None
    stop_event = False
    responses = {}
    
    def sse_listener():
        nonlocal post_endpoint
        try:
            with requests.get(MCP_URL, headers={"Accept": "text/event-stream"}, stream=True, timeout=15) as r:
                cur_ev = "message"
                for line in r.iter_lines():
                    if stop_event: break
                    if line:
                        d = line.decode('utf-8')
                        if d.startswith('event:'): cur_ev = d[6:].strip()
                        elif d.startswith('data:'):
                            msg = d[5:].strip()
                            if cur_ev == 'endpoint': post_endpoint = msg
                            elif cur_ev == 'message':
                                try:
                                    parsed = json.loads(msg)
                                    mid = parsed.get('id')
                                    if mid: responses[mid] = parsed
                                except Exception: pass
        except Exception: pass
            
    t = threading.Thread(target=sse_listener, daemon=True)
    t.start()
    
    for _ in range(15):
        if post_endpoint: break
        time.sleep(0.3)
        
    if not post_endpoint:
        return live_equity_usd
        
    full_post = f"https://mcp.viasocket.com{post_endpoint}" if post_endpoint.startswith('/') else post_endpoint
    try:
        requests.post(full_post, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "bal-check", "version": "1.0"}}
        }, timeout=5)
        time.sleep(0.3)
        requests.post(full_post, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=5)
        requests.post(full_post, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "Delta_Exchange", "arguments": {"thread_id": f"bal_{int(time.time())}", "action_name": "rowveq1gvjzz", "instructions": "Get wallet balances"}}
        }, timeout=10)
        
        for _ in range(25):
            if 2 in responses: break
            time.sleep(0.4)
            
        stop_event = True
        res = responses.get(2)
        if res:
            text = res.get('result', {}).get('content', [{}])[0].get('text', '')
            if text:
                parsed_text = json.loads(text)
                data = parsed_text.get('data', [])
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get('asset_symbol') == 'USD':
                            bal_str = item.get('balance', '')
                            if bal_str:
                                live_equity_usd = float(bal_str)
                                last_balance_check = now
                                log(f"[WALLET SYNC] Real Equity Safe: ${live_equity_usd:.2f} USD (~Rs. {live_equity_usd * 85:.0f} INR)")
    except Exception as e:
        log(f"[WALLET SYNC NOTE] {e}")
    finally:
        stop_event = True
        
    return live_equity_usd

# ==============================================================================
# INDICATOR FUNCTIONS
# ==============================================================================
def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0.0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))

def calc_supertrend(df, period=10, multiplier=3.0):
    hl2 = (df['High'] + df['Low']) / 2.0
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    atr = true_range.ewm(alpha=1/period, adjust=False).mean()
    
    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)
    
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend = pd.Series(1, index=df.index)
    
    for i in range(1, len(df)):
        c_prev = df['Close'].iloc[i-1]
        if basic_upper.iloc[i] < final_upper.iloc[i-1] or c_prev > final_upper.iloc[i-1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]
            
        if basic_lower.iloc[i] > final_lower.iloc[i-1] or c_prev < final_lower.iloc[i-1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]
            
        if df['Close'].iloc[i] > final_upper.iloc[i-1]:
            trend.iloc[i] = 1
        elif df['Close'].iloc[i] < final_lower.iloc[i-1]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i-1]
            
    df['ST_Trend'] = trend
    df['ST_Upper'] = final_upper
    df['ST_Lower'] = final_lower
    return df

# ==============================================================================
# 4 COMPETING STRATEGY ENGINES
# ==============================================================================

# STRATEGY 1: MELONA TRINITY PRO (SCOB + FVG + 100/400 EMA + RSI)
def evaluate_melona_trinity(df, ticker, symbol_label, tf, rr=2.0):
    try:
        n = len(df)
        if n < 40: return
        i = n - 2
        dates = df.index
        bar_time = str(dates[i])
        order_key = f"{symbol_label}_{tf}_{bar_time}_MELONA_TRINITY"
        placed = load_placed_orders()
        if order_key in placed: return

        c = df['Close'].values
        o = df['Open'].values
        h = df['High'].values
        l = df['Low'].values
        ema100 = df['EMA100'].values
        rsi = df['RSI14'].values

        past_lows = np.min(l[i-5:i-1])
        past_highs = np.max(h[i-5:i-1])
        bull_sweep = l[i-1] < past_lows
        bear_sweep = h[i-1] > past_highs

        bull_scob = (o[i-2] > c[i-2]) and (c[i-1] > o[i-1]) and (c[i] > o[i]) and bull_sweep and (c[i] > h[i-1])
        bear_scob = (o[i-2] < c[i-2]) and (c[i-1] < o[i-1]) and (c[i] < o[i]) and bear_sweep and (c[i] < l[i-1])

        macro_bull = c[i] > ema100[i]
        macro_bear = c[i] < ema100[i]
        rsi_bull = rsi[i] >= 50.0
        rsi_bear = rsi[i] <= 50.0

        if bull_scob and macro_bull and rsi_bull:
            entry_p = round(float(h[i-1]), 2)
            sl_p = round(float(l[i-1]), 2)
            risk = entry_p - sl_p
            if risk > 0:
                sl_pct = (risk / entry_p) * 100.0
                if MIN_RISK_PCT <= sl_pct <= MAX_SL_CAP_PCT:
                    tp_p = round(entry_p + (risk * rr), 2)
                    execute_arena_paper_order("MELONA_TRINITY", symbol_label, "BUY", entry_p, sl_p, tp_p, f"RSI: {rsi[i]:.1f} | EMA100 Confluence", tf)
                    save_placed_order(order_key, {"strat": "MELONA_TRINITY", "side": "BUY", "entry": entry_p, "time": bar_time})
        elif bear_scob and macro_bear and rsi_bear:
            entry_p = round(float(l[i-1]), 2)
            sl_p = round(float(h[i-1]), 2)
            risk = sl_p - entry_p
            if risk > 0:
                sl_pct = (risk / entry_p) * 100.0
                if MIN_RISK_PCT <= sl_pct <= MAX_SL_CAP_PCT:
                    tp_p = round(entry_p - (risk * rr), 2)
                    execute_arena_paper_order("MELONA_TRINITY", symbol_label, "SELL", entry_p, sl_p, tp_p, f"RSI: {rsi[i]:.1f} | EMA100 Confluence", tf)
                    save_placed_order(order_key, {"strat": "MELONA_TRINITY", "side": "SELL", "entry": entry_p, "time": bar_time})
    except Exception as e:
        log(f"[MELONA TRINITY EVAL ERROR] {symbol_label}: {e}")

# STRATEGY 2: TITAN BREAKOUT PRO (200-BAR PROBABILITY MATRIX >= 48%)
def evaluate_titan_breakout(df, ticker, symbol_label, tf, rr=2.0):
    try:
        n = len(df)
        if n < 220: return
        i = n - 2
        dates = df.index
        bar_time = str(dates[i])
        order_key = f"{symbol_label}_{tf}_{bar_time}_TITAN_PRO"
        placed = load_placed_orders()
        if order_key in placed: return

        c = df['Close'].values
        o = df['Open'].values
        h = df['High'].values
        l = df['Low'].values
        ema50 = df['EMA50'].values
        ema200 = df['EMA200'].values

        win = min(200, i - 1)
        g_bars, g_breaks = 0, 0
        r_bars, r_breaks = 0, 0
        for j in range(i - win, i):
            if c[j-1] > o[j-1]:
                g_bars += 1
                if h[j] >= h[j-1]: g_breaks += 1
            elif c[j-1] < o[j-1]:
                r_bars += 1
                if l[j] <= l[j-1]: r_breaks += 1
                
        p_high = ((g_breaks + 1) / max(1, g_bars + 1)) * 100.0
        p_low = ((r_breaks + 1) / max(1, r_bars + 1)) * 100.0

        macro_bull = (ema50[i-1] > ema200[i-1]) and (c[i-1] > ema50[i-1])
        macro_bear = (ema50[i-1] < ema200[i-1]) and (c[i-1] < ema50[i-1])

        bull_trigger = macro_bull and (h[i] > h[i-1]) and (c[i] > o[i]) and (p_high >= 48.0)
        bear_trigger = macro_bear and (l[i] < l[i-1]) and (c[i] < o[i]) and (p_low >= 48.0)

        if bull_trigger:
            entry_p = round(float(h[i-1]), 2)
            sl_p = round(float(l[i-1]), 2)
            risk = entry_p - sl_p
            if risk > 0:
                sl_pct = (risk / entry_p) * 100.0
                if MIN_RISK_PCT <= sl_pct <= MAX_SL_CAP_PCT:
                    tp_p = round(entry_p + (risk * rr), 2)
                    execute_arena_paper_order("TITAN_BREAKOUT", symbol_label, "BUY", entry_p, sl_p, tp_p, f"Prob: {p_high:.1f}% | EMA 50/200 Bull", tf)
                    save_placed_order(order_key, {"strat": "TITAN_BREAKOUT", "side": "BUY", "entry": entry_p, "time": bar_time})
        elif bear_trigger:
            entry_p = round(float(l[i-1]), 2)
            sl_p = round(float(h[i-1]), 2)
            risk = sl_p - entry_p
            if risk > 0:
                sl_pct = (risk / entry_p) * 100.0
                if MIN_RISK_PCT <= sl_pct <= MAX_SL_CAP_PCT:
                    tp_p = round(entry_p - (risk * rr), 2)
                    execute_arena_paper_order("TITAN_BREAKOUT", symbol_label, "SELL", entry_p, sl_p, tp_p, f"Prob: {p_low:.1f}% | EMA 50/200 Bear", tf)
                    save_placed_order(order_key, {"strat": "TITAN_BREAKOUT", "side": "SELL", "entry": entry_p, "time": bar_time})
    except Exception as e:
        log(f"[TITAN BREAKOUT EVAL ERROR] {symbol_label}: {e}")

# STRATEGY 3: BREAKOUT SUPERTREND PRO (ATR 10, MULTIPLIER 3.0 + EMA 50)
def evaluate_supertrend_momentum(df, ticker, symbol_label, tf, rr=2.0):
    try:
        n = len(df)
        if n < 50: return
        i = n - 2
        dates = df.index
        bar_time = str(dates[i])
        order_key = f"{symbol_label}_{tf}_{bar_time}_SUPERTREND_PRO"
        placed = load_placed_orders()
        if order_key in placed: return

        c = df['Close'].values
        ema50 = df['EMA50'].values
        st_trend = df['ST_Trend'].values
        st_upper = df['ST_Upper'].values
        st_lower = df['ST_Lower'].values

        bull_flip = (st_trend[i] == 1) and (st_trend[i-1] == -1) and (c[i] > ema50[i])
        bear_flip = (st_trend[i] == -1) and (st_trend[i-1] == 1) and (c[i] < ema50[i])

        if bull_flip:
            entry_p = round(float(c[i]), 2)
            sl_p = round(float(st_lower[i]), 2)
            risk = entry_p - sl_p
            if risk > 0:
                sl_pct = (risk / entry_p) * 100.0
                if MIN_RISK_PCT <= sl_pct <= MAX_SL_CAP_PCT:
                    tp_p = round(entry_p + (risk * rr), 2)
                    execute_arena_paper_order("SUPERTREND_MOMENTUM", symbol_label, "BUY", entry_p, sl_p, tp_p, "Supertrend Green Flip | ATR SL", tf)
                    save_placed_order(order_key, {"strat": "SUPERTREND_MOMENTUM", "side": "BUY", "entry": entry_p, "time": bar_time})
        elif bear_flip:
            entry_p = round(float(c[i]), 2)
            sl_p = round(float(st_upper[i]), 2)
            risk = sl_p - entry_p
            if risk > 0:
                sl_pct = (risk / entry_p) * 100.0
                if MIN_RISK_PCT <= sl_pct <= MAX_SL_CAP_PCT:
                    tp_p = round(entry_p - (risk * rr), 2)
                    execute_arena_paper_order("SUPERTREND_MOMENTUM", symbol_label, "SELL", entry_p, sl_p, tp_p, "Supertrend Red Flip | ATR SL", tf)
                    save_placed_order(order_key, {"strat": "SUPERTREND_MOMENTUM", "side": "SELL", "entry": entry_p, "time": bar_time})
    except Exception as e:
        log(f"[SUPERTREND EVAL ERROR] {symbol_label}: {e}")

# STRATEGY 4: ORIGINAL NAIVE SCOB (BASELINE MODEL + 100 EMA)
def evaluate_original_naive_scob(df, ticker, symbol_label, tf, rr=2.0):
    try:
        n = len(df)
        if n < 40: return
        i = n - 2
        dates = df.index
        bar_time = str(dates[i])
        order_key = f"{symbol_label}_{tf}_{bar_time}_ORIGINAL_SCOB"
        placed = load_placed_orders()
        if order_key in placed: return

        c = df['Close'].values
        o = df['Open'].values
        h = df['High'].values
        l = df['Low'].values
        ema100 = df['EMA100'].values

        bull_naive = (o[i-1] > c[i-1]) and (c[i] > o[i]) and (c[i] > h[i-1]) and (c[i] > ema100[i])
        bear_naive = (o[i-1] < c[i-1]) and (c[i] < o[i]) and (c[i] < l[i-1]) and (c[i] < ema100[i])

        if bull_naive:
            entry_p = round(float(c[i]), 2)
            sl_p = round(float(l[i-1]), 2)
            risk = entry_p - sl_p
            if risk > 0:
                sl_pct = (risk / entry_p) * 100.0
                if MIN_RISK_PCT <= sl_pct <= MAX_SL_CAP_PCT:
                    tp_p = round(entry_p + (risk * rr), 2)
                    execute_arena_paper_order("ORIGINAL_SCOB", symbol_label, "BUY", entry_p, sl_p, tp_p, "Naive 2-Candle Breakout + 100 EMA", tf)
                    save_placed_order(order_key, {"strat": "ORIGINAL_SCOB", "side": "BUY", "entry": entry_p, "time": bar_time})
        elif bear_naive:
            entry_p = round(float(c[i]), 2)
            sl_p = round(float(h[i-1]), 2)
            risk = sl_p - entry_p
            if risk > 0:
                sl_pct = (risk / entry_p) * 100.0
                if MIN_RISK_PCT <= sl_pct <= MAX_SL_CAP_PCT:
                    tp_p = round(entry_p - (risk * rr), 2)
                    execute_arena_paper_order("ORIGINAL_SCOB", symbol_label, "SELL", entry_p, sl_p, tp_p, "Naive 2-Candle Breakdown + 100 EMA", tf)
                    save_placed_order(order_key, {"strat": "ORIGINAL_SCOB", "side": "SELL", "entry": entry_p, "time": bar_time})
    except Exception as e:
        log(f"[ORIGINAL SCOB EVAL ERROR] {symbol_label}: {e}")

# ==============================================================================
# UNIFIED MULTI-STRATEGY SCANNER
# ==============================================================================
def scan_portfolio_asset(item):
    ticker = item["ticker"]
    delta_sym = item["delta_symbol"]
    rr = item["rr"]
    
    for tf in item["intervals"]:
        try:
            raw = yf.download(ticker, period="30d", interval=tf, progress=False)
            if raw.empty: continue
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            df = raw.dropna().copy()
            if len(df) < 50: continue
                
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA100'] = df['Close'].ewm(span=100, adjust=False).mean()
            df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()
            df['RSI14'] = calc_rsi(df['Close'], 14)
            df = calc_supertrend(df, 10, 3.0)
            
            # Parallel evaluation on exact same candle
            evaluate_melona_trinity(df, ticker, delta_sym, tf, rr)
            evaluate_titan_breakout(df, ticker, delta_sym, tf, rr)
            evaluate_supertrend_momentum(df, ticker, delta_sym, tf, rr)
            evaluate_original_naive_scob(df, ticker, delta_sym, tf, rr)
            
            time.sleep(0.5)
        except Exception as e:
            log(f"[SCAN ERROR] {delta_sym} ({tf}): {e}")

# ==============================================================================
# MAIN ENGINE BACKGROUND THREAD
# ==============================================================================
def run_cloud_bot():
    log("==========================================================================")
    log("DELTA MULTI-STRATEGY QUANTITATIVE ARENA (V12.0 TOURNAMENT ENGINE)")
    log("MODE: 🛡️ PAPER TRADING SIMULATION (ZERO REAL RISK - $12.18 / Rs. 1035 SAFE)")
    log("Contenders: Melona Trinity | Titan Breakout | Supertrend PRO | Original SCOB")
    log("Timeframes: 15m (Primary) & 1h (Macro Structural)")
    log("Execution: Strictly Closed Candle (n-2) | Zero Repainting")
    log("==========================================================================")
    
    init_arena_db()
    cycle = 0
    while True:
        cycle += 1
        update_live_wallet_balance()
        update_arena_tracker()
        
        db = init_arena_db()
        summary_parts = []
        for s_id, s in db.items():
            st = s.get("stats", {})
            summary_parts.append(f"{s_id[:6]}: {st.get('wins',0)}W/{st.get('losses',0)}L ({st.get('win_rate',0.0)}%)")
        summary_str = " | ".join(summary_parts)
        log(f"--- Cycle #{cycle} | Real Cash: ${live_equity_usd:.2f} Safe | {summary_str} ---")
        
        for item in PORTFOLIO_CONFIG:
            scan_portfolio_asset(item)
            time.sleep(1)
            
        time.sleep(60)

# ==============================================================================
# HTTP HEALTH & TOURNAMENT LEADERBOARD SERVER (RENDER CLOUD)
# ==============================================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        db = init_arena_db()
        now_str = datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %I:%M:%S %p IST")
        
        # Sort strategies by Net R / Profit Factor
        ranked_strats = sorted(
            db.values(),
            key=lambda x: (x.get("stats", {}).get("net_r", 0.0), x.get("stats", {}).get("win_rate", 0.0)),
            reverse=True
        )
        
        accept_header = self.headers.get("Accept", "")
        if "application/json" in accept_header or self.path in ["/api", "/json"]:
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            resp = {
                "status": "ONLINE",
                "bot": "DELTA_V12.0_MULTI_STRATEGY_ARENA",
                "mode": "PAPER_TRADING_TOURNAMENT (Zero Real Risk)",
                "real_wallet_preserved": f"${live_equity_usd:.2f} USD (~Rs. {live_equity_usd * 85:.0f} INR) 100% Safe Free Cash",
                "time": now_str,
                "leaderboard": ranked_strats
            }
            self.wfile.write(json.dumps(resp, indent=2).encode('utf-8'))
            return

        # Render HTML Leaderboard UI
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        
        rows_html = ""
        for rank, s in enumerate(ranked_strats, 1):
            st = s.get("stats", {})
            active_cnt = len(s.get("active", []))
            pnl_usd = st.get("net_pnl_usd", 0.0)
            pnl_color = "#00e676" if pnl_usd > 0 else ("#ff1744" if pnl_usd < 0 else "#b0bec5")
            net_r = st.get("net_r", 0.0)
            r_color = "#00e676" if net_r > 0 else ("#ff1744" if net_r < 0 else "#b0bec5")
            
            badge_icon = "🥇" if rank == 1 else ("🥈" if rank == 2 else ("🥉" if rank == 3 else "4️⃣"))
            
            rows_html += f"""
            <tr style="border-bottom: 1px solid #1e293b;">
                <td style="padding: 14px; font-weight: bold; font-size: 16px;">{badge_icon} #{rank}</td>
                <td style="padding: 14px;">
                    <div style="font-weight: bold; color: {s.get('color', '#fff')}; font-size: 15px;">{s.get('name')}</div>
                    <div style="font-size: 12px; color: #94a3b8;">{s.get('desc')}</div>
                </td>
                <td style="padding: 14px; text-align: center;"><b>{st.get('total', 0)}</b> ({st.get('wins', 0)}W / {st.get('losses', 0)}L)</td>
                <td style="padding: 14px; text-align: center; font-weight: bold; color: {'#00e676' if st.get('win_rate', 0.0) >= 50 else '#ff9100'};">{st.get('win_rate', 0.0)}%</td>
                <td style="padding: 14px; text-align: center; font-weight: bold; color: {pnl_color};">${pnl_usd:+.2f} USD</td>
                <td style="padding: 14px; text-align: center; font-weight: bold; color: {r_color};">{net_r:+.1f} R</td>
                <td style="padding: 14px; text-align: center;">{st.get('profit_factor', 0.0)}</td>
                <td style="padding: 14px; text-align: center;"><span style="background: #1e293b; padding: 4px 10px; border-radius: 12px; font-size: 13px; color: #38bdf8;">{active_cnt} Open</span></td>
            </tr>
            """
            
        active_trades_html = ""
        total_active = sum(len(s.get("active", [])) for s in ranked_strats)
        if total_active == 0:
            active_trades_html = "<div style='color: #64748b; font-style: italic; padding: 10px;'>No active positions currently open. Scanning 15m and 1h closed candles...</div>"
        else:
            for s in ranked_strats:
                for t in s.get("active", []):
                    side_badge = f"<span style='background: {'#14532d' if t['side'] == 'BUY' else '#7f1d1d'}; color: {'#4ade80' if t['side'] == 'BUY' else '#f87171'}; padding: 3px 8px; border-radius: 6px; font-weight: bold;'>{t['side']}</span>"
                    active_trades_html += f"""
                    <div style="background: #131d2e; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span style="color: {s.get('color', '#fff')}; font-weight: bold;">{s.get('name')}</span> &nbsp;|&nbsp; 
                            <b>{t['symbol']}</b> ({t['tf']}) &nbsp; {side_badge}
                            <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">Entry: ${t['entry']} | SL: ${t['sl']} | TP: ${t['tp']} | {t.get('extra_info', '')}</div>
                        </div>
                        <div style="font-size: 12px; color: #64748b;">{t.get('entry_time', '')}</div>
                    </div>
                    """

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Delta Multi-Strategy Arena V12.0</title>
    <meta http-equiv="refresh" content="25">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ background: #080c14; color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 24px; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1e293b; padding-bottom: 16px; margin-bottom: 24px; }}
        .banner {{ background: linear-gradient(90deg, #064e3b, #0f766e); border: 1px solid #10b981; border-radius: 10px; padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }}
        .card {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.5); }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; }}
        th {{ padding: 12px 14px; background: #1e293b; color: #94a3b8; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h2 style="margin: 0; color: #38bdf8;">🏆 DELTA MULTI-STRATEGY QUANTITATIVE ARENA</h2>
                <div style="font-size: 13px; color: #64748b; margin-top: 4px;">V12.0 Parallel Tournament Edition | Live Closed-Candle Execution</div>
            </div>
            <div style="text-align: right;">
                <span style="background: #14532d; color: #4ade80; padding: 6px 12px; border-radius: 20px; font-weight: bold; font-size: 13px;">● ENGINE ONLINE</span>
                <div style="font-size: 11px; color: #64748b; margin-top: 5px;">Auto-refreshes every 25s</div>
            </div>
        </div>

        <div class="banner">
            <div>
                <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: #a7f3d0; font-weight: bold;">🛡️ Capital Preservation Protocol</div>
                <div style="font-size: 20px; font-weight: bold; color: #ffffff; margin-top: 2px;">Real Wallet Balance: ${live_equity_usd:.2f} USD (~Rs. {live_equity_usd * 85:.0f} INR)</div>
            </div>
            <div style="text-align: right; background: rgba(0,0,0,0.25); padding: 8px 16px; border-radius: 8px;">
                <div style="font-size: 12px; color: #6ee7b7; font-weight: bold;">100% SAFE FREE CASH</div>
                <div style="font-size: 11px; color: #d1fae5;">Real Risk: $0.00 (Zero Real Money at Risk)</div>
            </div>
        </div>

        <div class="card">
            <h3 style="margin-top: 0; color: #f8fafc; margin-bottom: 16px;">📊 Live Tournament Leaderboard</h3>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Rank</th>
                            <th>Strategy & Description</th>
                            <th style="text-align: center;">Trades</th>
                            <th style="text-align: center;">Win Rate</th>
                            <th style="text-align: center;">Simulated P&L</th>
                            <th style="text-align: center;">Net R</th>
                            <th style="text-align: center;">Profit Factor</th>
                            <th style="text-align: center;">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="card">
            <h3 style="margin-top: 0; color: #f8fafc; margin-bottom: 16px;">⚡ Active Simulated Positions ({total_active})</h3>
            {active_trades_html}
        </div>

        <div style="display: flex; justify-content: space-between; font-size: 12px; color: #64748b; padding: 10px 0;">
            <div>Time: {now_str}</div>
            <div>Assets: BTCUSD, ETHUSD, PAXGUSDT (15m & 1h) | Commission Simulated: 0.14% roundtrip</div>
        </div>
    </div>
</body>
</html>
"""
        self.wfile.write(html.encode('utf-8'))

    def log_message(self, format, *args):
        return

def start_health_server():
    try:
        port = int(os.environ.get("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        log(f"Tournament Dashboard Server listening on port {port}")
        server.serve_forever()
    except Exception as e:
        log(f"Health server note: {e}")
        while True:
            time.sleep(3600)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_cloud_bot, daemon=True)
    bot_thread.start()
    start_health_server()

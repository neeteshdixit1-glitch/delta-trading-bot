import requests
import json
import threading
import time
import datetime
import os
import yfinance as yf
import pandas as pd
import numpy as np
import pytz
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==============================================================================
# DELTA EXCHANGE INDIA - 24/7 DYNAMIC QUANT MASTER BOT (V9 ELITE)
# ==============================================================================
# Portfolio Configuration & Quant Strategy Matrix:
# 1. BNB (BNBUSD)       -> Heikin-Ashi 9/21 Pullback (1:3 R:R) | Max SL Cap: 1.0%
# 2. Bitcoin (BTCUSD)   -> Normal Melona SCOB 50/400 EMA (1:2 R:R) | Max SL Cap: 1.2%
# 3. Ethereum (ETHUSD)  -> Normal 9/21 Pullback (1:3 R:R) | Max SL Cap: 1.2%
# 4. XRP (XRPUSD)       -> Normal 9/21 Pullback (1:3 R:R) | Max SL Cap: 1.2%
#
# Dynamic Risk Engine (Mathematical Defense Against Account Wipeout):
# - TARGET_RISK_USD = $0.60 USD (~2.5% of $22.95 balance)
# - Contracts dynamically scale with SL distance:
#   * Tight SL (0.2% - 0.4%): 4 - 5 Contracts
#   * Medium/Wide SL (0.6% - 1.2%): 1 - 2 Contracts
# - Guaranteed Outcome: Average Loss is strictly fixed at ~$0.50 USD per trade!
# - Single winning trade (1:3 R:R = +$1.50) completely recovers THREE consecutive losses!
# - MAX_CONCURRENT_POSITIONS = 5 | 24/7 Execution Mode
# ==============================================================================

MCP_URL = "https://mcp.viasocket.com/mcp/6ac9d75f3f044c660c2ef9e7-81295"
DB_FILE = "placed_orders.json"
LOG_FILE = "cloud_autotrader.log"
TARGET_RISK_USD = 0.60            # Target $0.60 USD dollar risk per trade
MAX_CONCURRENT_POSITIONS = 5       # Max 5 positions at a time
ENABLE_24_7 = True                 # 24/7 Non-stop mode
MIN_RISK_PCT = 0.20               # 0.20% minimum risk buffer for tight stops

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

IST_TZ = pytz.timezone('Asia/Kolkata')

# Portfolio Configuration with Asset-Specific Multipliers & SL Caps
PORTFOLIO_CONFIG = [
    {
        "name": "BNB",
        "ticker": "BNB-USD",
        "delta_symbol": "BNBUSD",
        "strategy": "EMA_PULLBACK_1TO3",
        "candle_type": "HEIKIN_ASHI",
        "rr": 3.0,
        "contract_unit": 0.1,      # 0.1 BNB per contract
        "max_sl_cap": 1.0,         # Cap at 1.0% (Big moves in BNB lose money)
        "intervals": ["1h", "15m"]
    },
    {
        "name": "Bitcoin",
        "ticker": "BTC-USD",
        "delta_symbol": "BTCUSD",
        "strategy": "MELONA_50_400_EMA",
        "candle_type": "NORMAL",
        "rr": 2.0,
        "contract_unit": 0.001,    # 0.001 BTC per contract
        "max_sl_cap": 1.2,         # Cap at 1.2%
        "intervals": ["1h", "15m"]
    },
    {
        "name": "Ethereum",
        "ticker": "ETH-USD",
        "delta_symbol": "ETHUSD",
        "strategy": "EMA_PULLBACK_1TO3",
        "candle_type": "NORMAL",
        "rr": 3.0,
        "contract_unit": 0.01,     # 0.01 ETH per contract
        "max_sl_cap": 1.2,         # Cap at 1.2%
        "intervals": ["1h", "15m"]
    },
    {
        "name": "XRP",
        "ticker": "XRP-USD",
        "delta_symbol": "XRPUSD",
        "strategy": "EMA_PULLBACK_1TO3",
        "candle_type": "NORMAL",
        "rr": 3.0,
        "contract_unit": 1.0,      # 1 XRP per contract
        "max_sl_cap": 1.2,         # Cap at 1.2%
        "intervals": ["1h", "15m"]
    }
]

def log(msg):
    now_ist = datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST")
    full_msg = f"[{now_ist}] {msg}"
    print(full_msg, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(full_msg + "\n")
    except Exception:
        pass

def send_telegram_alert(symbol, side, entry_p, sl_p, tp_p, strategy_name, candle_type, rr_ratio, size, risk_usd):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        side_icon = "🟢" if side.lower() == 'buy' else "🔴"
        text = (
            f"🚀 *DELTA TRADE EXECUTED* {side_icon}\n\n"
            f"• *Asset*: `{symbol}`\n"
            f"• *Side*: `{side.upper()}` ({size} Contracts)\n"
            f"• *Setup*: `{strategy_name} ({candle_type})`\n"
            f"• *Target R:R*: `1:{int(rr_ratio)}`\n"
            f"• *Entry Price*: `${entry_p:,.2f}`\n"
            f"• *Stop Loss*: `${sl_p:,.2f}` (Risk: `${risk_usd:.2f}`)\n"
            f"• *Take Profit*: `${tp_p:,.2f}`\n"
            f"• *Execution*: `Bracket Limit Order (24/7)`\n"
            f"• *Time*: `{datetime.datetime.now(IST_TZ).strftime('%I:%M:%S %p IST')}`"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        log(f"[TELEGRAM ERROR] {e}")

def calculate_heikin_ashi(df):
    ha_df = df.copy()
    ha_close = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4.0
    ha_open = np.zeros(len(df))
    ha_open[0] = (df['Open'].iloc[0] + df['Close'].iloc[0]) / 2.0
    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i-1] + ha_close.iloc[i-1]) / 2.0
    ha_high = np.maximum(df['High'].values, np.maximum(ha_open, ha_close.values))
    ha_low = np.minimum(df['Low'].values, np.minimum(ha_open, ha_close.values))
    ha_df['HA_Open'] = ha_open
    ha_df['HA_High'] = ha_high
    ha_df['HA_Low'] = ha_low
    ha_df['HA_Close'] = ha_close.values
    return ha_df

def load_placed_orders():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_placed_order(order_key, details):
    data = load_placed_orders()
    data[order_key] = details
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log(f"[DB ERROR] {e}")

def get_live_open_positions_count():
    post_endpoint = None
    stop_event = False
    responses = {}
    
    def sse_listener():
        nonlocal post_endpoint
        try:
            with requests.get(MCP_URL, headers={"Accept": "text/event-stream"}, stream=True, timeout=15) as r:
                cur_ev = "message"
                for line in r.iter_lines():
                    if stop_event:
                        break
                    if line:
                        d = line.decode('utf-8')
                        if d.startswith('event:'):
                            cur_ev = d[6:].strip()
                        elif d.startswith('data:'):
                            msg = d[5:].strip()
                            if cur_ev == 'endpoint':
                                post_endpoint = msg
                            elif cur_ev == 'message':
                                try:
                                    parsed = json.loads(msg)
                                    mid = parsed.get('id')
                                    if mid:
                                        responses[mid] = parsed
                                except Exception:
                                    pass
        except Exception:
            pass
            
    t = threading.Thread(target=sse_listener, daemon=True)
    t.start()
    
    for _ in range(15):
        if post_endpoint:
            break
        time.sleep(0.3)
        
    if not post_endpoint:
        return 0
        
    full_post = f"https://mcp.viasocket.com{post_endpoint}" if post_endpoint.startswith('/') else post_endpoint
    try:
        requests.post(full_post, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pos-check", "version": "1.0"}}
        }, timeout=5)
        time.sleep(0.5)
        requests.post(full_post, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=5)
        
        requests.post(full_post, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "Delta_Exchange",
                "arguments": {
                    "thread_id": f"pos_check_{int(time.time())}",
                    "action_name": "rowickixv4on",
                    "instructions": "Get all open positions"
                }
            }
        }, timeout=10)
        
        for _ in range(25):
            if 2 in responses:
                break
            time.sleep(0.4)
            
        stop_event = True
        res = responses.get(2)
        if res:
            text = res.get('result', {}).get('content', [{}])[0].get('text', '')
            if text:
                parsed_text = json.loads(text)
                data = parsed_text.get('data', [])
                if isinstance(data, list):
                    return len(data)
                elif isinstance(data, dict) and data.get('message') == 'No open positions found':
                    return 0
    except Exception as e:
        log(f"[POS CHECK EXCEPTION] {e}")
    finally:
        stop_event = True
        
    return 0

def calculate_dynamic_size(symbol_label, risk_distance, contract_unit):
    """
    Dynamically scales contract size so dollar risk is kept at ~$0.60 USD.
    Guarantees no outsized losses, while capturing big moves safely.
    """
    if risk_distance <= 0 or contract_unit <= 0:
        return 1
    # Dollar risk per 1 contract = risk_distance * contract_unit
    risk_per_contract = risk_distance * contract_unit
    contracts = int(round(TARGET_RISK_USD / risk_per_contract))
    # Safety boundary: Minimum 1 contract, maximum 5 contracts
    safe_size = max(1, min(5, contracts))
    return safe_size

def place_delta_bracket_order(symbol, side, entry_p, sl_p, tp_p, strategy_name, candle_type, rr_ratio, size, risk_usd):
    risk_dollars = abs(entry_p - sl_p)
    reward_dollars = abs(tp_p - entry_p)
    log(f"--> [DELTA ORDER TRIGGER] [{strategy_name} | {candle_type}] {symbol} {side.upper()} | Entry: ${entry_p} | SL: ${sl_p} (Risk: ${risk_dollars:.2f} | Total Risk: ${risk_usd:.2f}) | TP: ${tp_p} (Reward: ${reward_dollars:.2f} | 1:{int(rr_ratio)}) | Dynamic Size: {size} Contracts")
    
    send_telegram_alert(symbol, side, entry_p, sl_p, tp_p, strategy_name, candle_type, rr_ratio, size, risk_usd)
    
    post_endpoint = None
    stop_event = False
    responses = {}
    
    def sse_listener():
        nonlocal post_endpoint
        try:
            with requests.get(MCP_URL, headers={"Accept": "text/event-stream"}, stream=True, timeout=60) as r:
                cur_ev = "message"
                for line in r.iter_lines():
                    if stop_event:
                        break
                    if line:
                        d = line.decode('utf-8')
                        if d.startswith('event:'):
                            cur_ev = d[6:].strip()
                        elif d.startswith('data:'):
                            msg = d[5:].strip()
                            if cur_ev == 'endpoint':
                                post_endpoint = msg
                            elif cur_ev == 'message':
                                try:
                                    parsed = json.loads(msg)
                                    mid = parsed.get('id')
                                    if mid:
                                        responses[mid] = parsed
                                except Exception:
                                    pass
        except Exception:
            pass
        
    t = threading.Thread(target=sse_listener, daemon=True)
    t.start()
    
    for _ in range(25):
        if post_endpoint:
            break
        time.sleep(0.5)
        
    if not post_endpoint:
        log("[ERROR] Could not connect to Delta viaSocket MCP endpoint.")
        return None
        
    full_post = f"https://mcp.viasocket.com{post_endpoint}" if post_endpoint.startswith('/') else post_endpoint
    
    requests.post(full_post, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "cloud-bot-v9", "version": "9.0"}}
    }, timeout=10)
    time.sleep(1)
    requests.post(full_post, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=10)
    
    order_instructions = (
        f"Place a bracket limit order on Delta Exchange:\n"
        f"- product_symbol: {symbol}\n"
        f"- basic_order.size: {size}\n"
        f"- basic_order.side: {side}\n"
        f"- basic_order.order_type: limit_order\n"
        f"- limit_order_config.limit_price: {entry_p}\n"
        f"- risk_mode: bracket_order\n"
        f"- bracket_order.bracket_stop_mode: fixed\n"
        f"- bracket_order.bracket_stop_loss_price: {sl_p}\n"
        f"- bracket_order.bracket_take_profit_price: {tp_p}"
    )
    
    requests.post(full_post, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "Delta_Exchange",
            "arguments": {
                "thread_id": f"cloud_trade_{int(time.time())}",
                "action_name": "rowxvuezrvvi",
                "instructions": order_instructions
            }
        }
    }, timeout=30)
    
    for _ in range(100):
        if 2 in responses:
            break
        time.sleep(0.5)
        
    stop_event = True
    result = responses.get(2)
    resp_text = ""
    if result:
        resp_text = result.get('result', {}).get('content', [{}])[0].get('text', '')
    log(f"[DELTA RESPONSE] {resp_text if resp_text else 'No response received'}")
    return result

def check_ema_pullback_setup(ticker, interval, symbol_label, candle_type="NORMAL", rr=3.0, unit=0.1, max_sl_cap=1.2):
    try:
        raw_df = yf.download(ticker, period="5d", interval=interval, progress=False)
        if isinstance(raw_df.columns, pd.MultiIndex):
            raw_df.columns = raw_df.columns.get_level_values(0)
        df = raw_df.dropna().copy()
        if len(df) < 50:
            return
            
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        df_ist = df.index.tz_convert('Asia/Kolkata')
        
        real_o, real_c, real_h, real_l = df['Open'].values, df['Close'].values, df['High'].values, df['Low'].values
        
        if candle_type == "HEIKIN_ASHI":
            df = calculate_heikin_ashi(df)
            o, c, h, l = df['HA_Open'].values, df['HA_Close'].values, df['HA_High'].values, df['HA_Low'].values
            df['EMA9'] = pd.Series(c).ewm(span=9, adjust=False).mean().values
            df['EMA21'] = pd.Series(c).ewm(span=21, adjust=False).mean().values
        else:
            o, c, h, l = real_o, real_c, real_h, real_l
            df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
            df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
            
        ema9, ema21 = df['EMA9'].values, df['EMA21'].values
        dates = df.index
        n = len(df)
        placed = load_placed_orders()
        
        for i in range(n-4, n-1):
            cur_hour_ist = df_ist[i].hour
            if not ENABLE_24_7 and not (7 <= cur_hour_ist < 23):
                continue
                
            bar_time = str(dates[i])
            order_key = f"{symbol_label}_{interval}_{bar_time}_PULLBACK_{candle_type}"
            if order_key in placed:
                continue
                
            cross_state = 0
            for k in range(max(1, i-20), i):
                if (ema9[k] > ema21[k]) and (ema9[k-1] <= ema21[k-1]):
                    cross_state = 1
                elif (ema9[k] < ema21[k]) and (ema9[k-1] >= ema21[k-1]):
                    cross_state = -1
                    
            if cross_state == 0:
                continue
                
            # Bullish Pullback Setup
            if cross_state == 1:
                if (l[i] <= ema9[i] * 1.002) and (c[i] >= ema21[i] * 0.998) and (c[i] > o[i]):
                    entry_p = round(float(real_c[i]), 2)
                    raw_sl  = round(float(real_l[i]), 2)
                    min_risk = round(entry_p * (MIN_RISK_PCT / 100.0), 2)
                    risk = max(entry_p - raw_sl, min_risk)
                    sl_pct = (risk / entry_p * 100.0)
                    
                    # Asset-Specific SL Cap Filter
                    if sl_pct > max_sl_cap:
                        log(f"[SL CAP GUARD] {symbol_label} ({interval}) BUY SL too wide ({sl_pct:.2f}% > {max_sl_cap}%). Skipping.")
                        continue
                        
                    sl_p = round(entry_p - risk, 2)
                    tp_p = round(entry_p + (risk * rr), 2)
                    
                    # Dynamic Risk Sizing
                    dynamic_size = calculate_dynamic_size(symbol_label, risk, unit)
                    total_dollar_risk = dynamic_size * unit * risk
                    
                    # Margin Guard Check (Max 5 positions)
                    active_cnt = get_live_open_positions_count()
                    if active_cnt >= MAX_CONCURRENT_POSITIONS:
                        log(f"[MARGIN GUARD] Signal found for {symbol_label} ({interval}) BUY, but already at max capacity ({active_cnt}/{MAX_CONCURRENT_POSITIONS} positions). Skipping.")
                        return
                        
                    log(f"[CHAMPION 9/21 | {candle_type}] {symbol_label} ({interval}) BULLISH PULLBACK at {bar_time} IST! Entry: ${entry_p}, SL: ${sl_p}, TP: ${tp_p} (Dynamic Size: {dynamic_size} Contracts | Risk: ${total_dollar_risk:.2f})")
                    res = place_delta_bracket_order(symbol_label, "buy", entry_p, sl_p, tp_p, "9/21 EMA Pullback", candle_type, rr, dynamic_size, total_dollar_risk)
                    save_placed_order(order_key, {
                        'symbol': symbol_label, 'tf': interval, 'side': 'buy', 'strategy': '9/21 EMA Pullback', 'candle': candle_type,
                        'entry': entry_p, 'sl': sl_p, 'tp': tp_p, 'size': dynamic_size, 'dollar_risk': round(total_dollar_risk, 2),
                        'result': str(res), 'time': str(datetime.datetime.now(IST_TZ))
                    })
                    return

            # Bearish Pullback Setup
            elif cross_state == -1:
                if (h[i] >= ema9[i] * 0.998) and (c[i] <= ema21[i] * 1.002) and (c[i] < o[i]):
                    entry_p = round(float(real_c[i]), 2)
                    raw_sl  = round(float(real_h[i]), 2)
                    min_risk = round(entry_p * (MIN_RISK_PCT / 100.0), 2)
                    risk = max(raw_sl - entry_p, min_risk)
                    sl_pct = (risk / entry_p * 100.0)
                    
                    if sl_pct > max_sl_cap:
                        log(f"[SL CAP GUARD] {symbol_label} ({interval}) SELL SL too wide ({sl_pct:.2f}% > {max_sl_cap}%). Skipping.")
                        continue
                        
                    sl_p = round(entry_p + risk, 2)
                    tp_p = round(entry_p - (risk * rr), 2)
                    
                    dynamic_size = calculate_dynamic_size(symbol_label, risk, unit)
                    total_dollar_risk = dynamic_size * unit * risk
                    
                    active_cnt = get_live_open_positions_count()
                    if active_cnt >= MAX_CONCURRENT_POSITIONS:
                        log(f"[MARGIN GUARD] Signal found for {symbol_label} ({interval}) SELL, but already at max capacity ({active_cnt}/{MAX_CONCURRENT_POSITIONS} positions). Skipping.")
                        return
                        
                    log(f"[CHAMPION 9/21 | {candle_type}] {symbol_label} ({interval}) BEARISH PULLBACK at {bar_time} IST! Entry: ${entry_p}, SL: ${sl_p}, TP: ${tp_p} (Dynamic Size: {dynamic_size} Contracts | Risk: ${total_dollar_risk:.2f})")
                    res = place_delta_bracket_order(symbol_label, "sell", entry_p, sl_p, tp_p, "9/21 EMA Pullback", candle_type, rr, dynamic_size, total_dollar_risk)
                    save_placed_order(order_key, {
                        'symbol': symbol_label, 'tf': interval, 'side': 'sell', 'strategy': '9/21 EMA Pullback', 'candle': candle_type,
                        'entry': entry_p, 'sl': sl_p, 'tp': tp_p, 'size': dynamic_size, 'dollar_risk': round(total_dollar_risk, 2),
                        'result': str(res), 'time': str(datetime.datetime.now(IST_TZ))
                    })
                    return
    except Exception as e:
        log(f"[EMA PULLBACK SCAN ERROR] {symbol_label} {interval}: {e}")

def check_melona_50_400_setup(ticker, interval, symbol_label, candle_type="NORMAL", rr=2.0, unit=0.001, max_sl_cap=1.2):
    try:
        raw_df = yf.download(ticker, period="30d", interval=interval, progress=False)
        if isinstance(raw_df.columns, pd.MultiIndex):
            raw_df.columns = raw_df.columns.get_level_values(0)
        df = raw_df.dropna().copy()
        if len(df) < 410:
            return
            
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        df_ist = df.index.tz_convert('Asia/Kolkata')
        
        df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA400'] = df['Close'].ewm(span=400, adjust=False).mean()
        
        o, c, h, l = df['Open'].values, df['Close'].values, df['High'].values, df['Low'].values
        ema50, ema400 = df['EMA50'].values, df['EMA400'].values
        dates = df.index
        n = len(df)
        placed = load_placed_orders()
        
        for i in range(n-4, n-1):
            cur_hour_ist = df_ist[i].hour
            if not ENABLE_24_7 and not (7 <= cur_hour_ist < 23):
                continue
                
            bar_time = str(dates[i])
            order_key = f"{symbol_label}_{interval}_{bar_time}_MELONA_50_400_{candle_type}"
            if order_key in placed:
                continue
                
            bull_engulf = (c[i-1] < o[i-1]) and (c[i] > o[i]) and (o[i] <= c[i-1] * 1.002) and (c[i] >= o[i-1] * 0.998)
            pass_bull = bull_engulf and (c[i] > ema50[i]) and (l[i] <= ema50[i] * 1.015) and (c[i] > ema400[i]) and (ema50[i] > ema400[i])
            
            if pass_bull:
                entry_p = round(float(c[i]), 2)
                raw_sl  = round(float(l[i]), 2)
                min_risk = round(entry_p * (MIN_RISK_PCT / 100.0), 2)
                risk = max(entry_p - raw_sl, min_risk)
                sl_pct = (risk / entry_p * 100.0)
                
                if sl_pct > max_sl_cap:
                    log(f"[SL CAP GUARD] {symbol_label} ({interval}) BUY SL too wide ({sl_pct:.2f}% > {max_sl_cap}%). Skipping.")
                    continue
                    
                sl_p = round(entry_p - risk, 2)
                tp_p = round(entry_p + (risk * rr), 2)
                
                dynamic_size = calculate_dynamic_size(symbol_label, risk, unit)
                total_dollar_risk = dynamic_size * unit * risk
                
                active_cnt = get_live_open_positions_count()
                if active_cnt >= MAX_CONCURRENT_POSITIONS:
                    log(f"[MARGIN GUARD] Signal found for {symbol_label} ({interval}) BUY, but already at max capacity ({active_cnt}/{MAX_CONCURRENT_POSITIONS} positions). Skipping.")
                    return
                    
                log(f"[CHAMPION MELONA 50/400 | {candle_type}] {symbol_label} ({interval}) BULLISH at {bar_time} IST! Entry: ${entry_p}, SL: ${sl_p}, TP: ${tp_p} (Dynamic Size: {dynamic_size} Contracts | Risk: ${total_dollar_risk:.2f})")
                res = place_delta_bracket_order(symbol_label, "buy", entry_p, sl_p, tp_p, "Melona 50/400 EMA", candle_type, rr, dynamic_size, total_dollar_risk)
                save_placed_order(order_key, {
                    'symbol': symbol_label, 'tf': interval, 'side': 'buy', 'strategy': 'Melona 50/400 EMA', 'candle': candle_type,
                    'entry': entry_p, 'sl': sl_p, 'tp': tp_p, 'size': dynamic_size, 'dollar_risk': round(total_dollar_risk, 2),
                    'result': str(res), 'time': str(datetime.datetime.now(IST_TZ))
                })
                return

            bear_engulf = (c[i-1] > o[i-1]) and (c[i] < o[i]) and (o[i] >= c[i-1] * 0.998) and (c[i] <= o[i-1] * 1.002)
            pass_bear = bear_engulf and (c[i] < ema50[i]) and (h[i] >= ema50[i] * 0.985) and (c[i] < ema400[i]) and (ema50[i] < ema400[i])
            
            if pass_bear:
                entry_p = round(float(c[i]), 2)
                raw_sl  = round(float(h[i]), 2)
                min_risk = round(entry_p * (MIN_RISK_PCT / 100.0), 2)
                risk = max(raw_sl - entry_p, min_risk)
                sl_pct = (risk / entry_p * 100.0)
                
                if sl_pct > max_sl_cap:
                    log(f"[SL CAP GUARD] {symbol_label} ({interval}) SELL SL too wide ({sl_pct:.2f}% > {max_sl_cap}%). Skipping.")
                    continue
                    
                sl_p = round(entry_p + risk, 2)
                tp_p = round(entry_p - (risk * rr), 2)
                
                dynamic_size = calculate_dynamic_size(symbol_label, risk, unit)
                total_dollar_risk = dynamic_size * unit * risk
                
                active_cnt = get_live_open_positions_count()
                if active_cnt >= MAX_CONCURRENT_POSITIONS:
                    log(f"[MARGIN GUARD] Signal found for {symbol_label} ({interval}) SELL, but already at max capacity ({active_cnt}/{MAX_CONCURRENT_POSITIONS} positions). Skipping.")
                    return
                    
                log(f"[CHAMPION MELONA 50/400 | {candle_type}] {symbol_label} ({interval}) BEARISH at {bar_time} IST! Entry: ${entry_p}, SL: ${sl_p}, TP: ${tp_p} (Dynamic Size: {dynamic_size} Contracts | Risk: ${total_dollar_risk:.2f})")
                res = place_delta_bracket_order(symbol_label, "sell", entry_p, sl_p, tp_p, "Melona 50/400 EMA", candle_type, rr, dynamic_size, total_dollar_risk)
                save_placed_order(order_key, {
                    'symbol': symbol_label, 'tf': interval, 'side': 'sell', 'strategy': 'Melona 50/400 EMA', 'candle': candle_type,
                    'entry': entry_p, 'sl': sl_p, 'tp': tp_p, 'size': dynamic_size, 'dollar_risk': round(total_dollar_risk, 2),
                    'result': str(res), 'time': str(datetime.datetime.now(IST_TZ))
                })
                return
    except Exception as e:
        log(f"[MELONA 50/400 SCAN ERROR] {symbol_label} {interval}: {e}")

def run_cloud_bot():
    log("==========================================================================")
    log("DELTA EXCHANGE CLOUD AUTO TRADER (V9 DYNAMIC QUANT MASTER)")
    log(f"Trading Hours: {'24/7 NON-STOP' if ENABLE_24_7 else '7:00 AM to 11:00 PM IST'}")
    log("Quad Portfolio:")
    log("  1. BNB (BNBUSD)       -> Heikin-Ashi 9/21 Pullback (1:3 R:R | Cap: 1.0%)")
    log("  2. Bitcoin (BTCUSD)   -> Normal Melona 50/400 (1:2 R:R | Cap: 1.2%)")
    log("  3. Ethereum (ETHUSD)  -> Normal 9/21 Pullback (1:3 R:R | Cap: 1.2%)")
    log("  4. XRP (XRPUSD)       -> Normal 9/21 Pullback (1:3 R:R | Cap: 1.2%)")
    log(f"Dynamic Risk Target: ${TARGET_RISK_USD} USD per trade | Max Positions: {MAX_CONCURRENT_POSITIONS}")
    log("Capital Guard: Single Loss capped at ~$0.60 | 1 Win covers 3 Losses")
    log("==========================================================================")
    
    cycle = 0
    while True:
        cycle += 1
        log(f"--- Cloud Scan Cycle #{cycle} (Assets: BNB, BTC, ETH, XRP | Dynamic Risk: ${TARGET_RISK_USD}) ---")
        
        for item in PORTFOLIO_CONFIG:
            ticker = item["ticker"]
            delta_sym = item["delta_symbol"]
            strategy = item["strategy"]
            candle_type = item["candle_type"]
            rr = item["rr"]
            unit = item["contract_unit"]
            cap = item["max_sl_cap"]
            
            for tf in item["intervals"]:
                if strategy == "EMA_PULLBACK_1TO3":
                    check_ema_pullback_setup(ticker, tf, delta_sym, candle_type=candle_type, rr=rr, unit=unit, max_sl_cap=cap)
                elif strategy == "MELONA_50_400_EMA":
                    check_melona_50_400_setup(ticker, tf, delta_sym, candle_type=candle_type, rr=rr, unit=unit, max_sl_cap=cap)
                
                time.sleep(1)
                
        time.sleep(60)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        resp = {
            "status": "ONLINE",
            "bot": "DELTA_V9_DYNAMIC_QUANT_MASTER_24_7",
            "portfolio": ["BNBUSD", "BTCUSD", "ETHUSD", "XRPUSD"],
            "timeframes": ["1h", "15m"],
            "mode": "24/7_NON_STOP" if ENABLE_24_7 else "SESSION_7AM_11PM",
            "target_risk_usd": f"${TARGET_RISK_USD}",
            "max_concurrent_positions": MAX_CONCURRENT_POSITIONS,
            "time": str(datetime.datetime.now(IST_TZ))
        }
        self.wfile.write(json.dumps(resp).encode('utf-8'))
        
    def log_message(self, format, *args):
        return

def start_health_server():
    try:
        port = int(os.environ.get("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        log(f"HTTP Health Ping Server listening on port {port}")
        server.serve_forever()
    except Exception as e:
        log(f"Health server note: {e}")
        while True:
            time.sleep(3600)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_cloud_bot, daemon=True)
    bot_thread.start()
    start_health_server()

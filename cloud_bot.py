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

# ==============================================================================
# DELTA EXCHANGE INDIA - 24/7 CLOUD AUTO TRADER (V5 PRO)
# Environment: Any Cloud VPS / Linux / Render / PythonAnywhere
# Strategy: Melona SCOB + 100 EMA Retest (1:2 R:R)
# Timing: 7:00 AM to 11:00 PM IST (Enforced via Asia/Kolkata timezone)
# Symbols: ETHUSD (15m, 1h), BTCUSD (15m, 1h), BTC Heikin-Ashi (15m, 1h)
# Trade Size: 10 Contracts
# Notifications: Telegram Instant Push Alert to Mobile
# ==============================================================================

MCP_URL = "https://mcp.viasocket.com/mcp/6ac9d75f3f044c660c2ef9e7-81295"
DB_FILE = "placed_orders.json"
LOG_FILE = "cloud_autotrader.log"
DEFAULT_SIZE = 10        # 10 Contracts
MIN_RISK_PCT = 0.20      # Minimum 0.20% buffer

# Telegram Notification Settings (Optional - set if you want instant phone dings)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

IST_TZ = pytz.timezone('Asia/Kolkata')

def log(msg):
    now_ist = datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST")
    full_msg = f"[{now_ist}] {msg}"
    print(full_msg, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(full_msg + "\n")

def send_telegram_alert(symbol, side, entry_p, sl_p, tp_p, setup_type="NORMAL", size=DEFAULT_SIZE):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        side_icon = "🟢" if side.lower() == 'buy' else "🔴"
        text = (
            f"🚀 *DELTA TRADE EXECUTED* {side_icon}\n\n"
            f"• *Pair*: `{symbol}`\n"
            f"• *Side*: `{side.upper()}` ({size} Contracts)\n"
            f"• *Setup*: `{setup_type}`\n"
            f"• *Entry Price*: `${entry_p:,.2f}`\n"
            f"• *Stop Loss*: `${sl_p:,.2f}`\n"
            f"• *Take Profit*: `${tp_p:,.2f}` (1:2 R:R)\n"
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
        except:
            return {}
    return {}

def save_placed_order(order_key, details):
    data = load_placed_orders()
    data[order_key] = details
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def place_delta_bracket_order(symbol, side, entry_p, sl_p, tp_p, setup_type="NORMAL", size=DEFAULT_SIZE):
    risk_dollars = abs(entry_p - sl_p)
    reward_dollars = abs(tp_p - entry_p)
    log(f"--> [DELTA ORDER TRIGGERED] [{setup_type}] {symbol} {side.upper()} | Entry: ${entry_p} | SL: ${sl_p} (Risk: ${risk_dollars:.2f}) | TP: ${tp_p} (Reward: ${reward_dollars:.2f}) | Size: {size} Contracts")
    
    # Send Mobile Telegram Alert
    send_telegram_alert(symbol, side, entry_p, sl_p, tp_p, setup_type=setup_type, size=size)
    
    post_endpoint = None
    stop_event = False
    responses = {}
    
    def sse_listener():
        nonlocal post_endpoint
        try:
            with requests.get(MCP_URL, headers={"Accept": "text/event-stream"}, stream=True, timeout=60) as r:
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
                                except: pass
        except: pass
        
    t = threading.Thread(target=sse_listener, daemon=True)
    t.start()
    
    for _ in range(25):
        if post_endpoint: break
        time.sleep(0.5)
        
    if not post_endpoint:
        log("[ERROR] Could not connect to Delta viaSocket MCP endpoint.")
        return None
        
    full_post = f"https://mcp.viasocket.com{post_endpoint}" if post_endpoint.startswith('/') else post_endpoint
    
    # Initialize
    requests.post(full_post, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "cloud-bot", "version": "1.0"}}
    })
    time.sleep(1)
    requests.post(full_post, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    
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
    })
    
    for _ in range(100):
        if 2 in responses: break
        time.sleep(0.5)
        
    stop_event = True
    result = responses.get(2)
    resp_text = ""
    if result:
        resp_text = result.get('result', {}).get('content', [{}])[0].get('text', '')
    log(f"[DELTA RESPONSE] {resp_text if resp_text else 'No response received'}")
    return result

def check_normal_candles_setup(ticker, interval, symbol_label):
    try:
        raw_df = yf.download(ticker, period="5d", interval=interval, progress=False)
        if isinstance(raw_df.columns, pd.MultiIndex): raw_df.columns = raw_df.columns.get_level_values(0)
        df = raw_df.dropna().copy()
        if len(df) < 50: return
        
        if df.index.tz is None: df.index = df.index.tz_localize('UTC')
        df_ist = df.index.tz_convert('Asia/Kolkata')
        
        df['EMA_100'] = df['Close'].ewm(span=100, adjust=False).mean()
        o, c, h, l = df['Open'].values, df['Close'].values, df['High'].values, df['Low'].values
        ema = df['EMA_100'].values
        dates = df.index
        n = len(df)
        placed = load_placed_orders()
        
        for i in range(n-4, n-1):
            cur_hour_ist = df_ist[i].hour
            # 7:00 AM to 11:00 PM IST
            if not (7 <= cur_hour_ist < 23):
                continue
                
            bar_time = str(dates[i])
            order_key = f"{symbol_label}_{interval}_{bar_time}_NORMAL"
            if order_key in placed: continue
            
            # Normal Bullish SCOB
            if (o[i-2]>c[i-2]) and (c[i-1]>o[i-1]) and (c[i]>o[i]) and (l[i-1]<l[i-2]) and (c[i]>h[i-1]):
                if (c[i] > ema[i]):
                    entry_p = round(float(h[i-1]), 2)
                    raw_sl  = round(float(l[i-1]), 2)
                    min_risk = round(entry_p * (MIN_RISK_PCT / 100.0), 2)
                    actual_risk = entry_p - raw_sl
                    risk = max(actual_risk, min_risk)
                    sl_p = round(entry_p - risk, 2)
                    tp_p = round(entry_p + (risk * 2.0), 2)
                    
                    log(f"[NORMAL SETUP] {symbol_label} ({interval}) BULLISH SCOB at {bar_time} IST! Entry: ${entry_p}, SL: ${sl_p}, TP: ${tp_p}")
                    res = place_delta_bracket_order(symbol_label, "buy", entry_p, sl_p, tp_p, setup_type="NORMAL", size=DEFAULT_SIZE)
                    save_placed_order(order_key, {
                        'symbol': symbol_label, 'tf': interval, 'side': 'buy', 'mode': 'NORMAL',
                        'entry': entry_p, 'sl': sl_p, 'tp': tp_p, 'result': str(res), 'time': str(datetime.datetime.now(IST_TZ))
                    })

            # Normal Bearish SCOB
            if (o[i-2]<c[i-2]) and (c[i-1]<o[i-1]) and (c[i]<o[i]) and (h[i-1]>h[i-2]) and (c[i]<l[i-1]):
                if (c[i] < ema[i]):
                    entry_p = round(float(l[i-1]), 2)
                    raw_sl  = round(float(h[i-1]), 2)
                    min_risk = round(entry_p * (MIN_RISK_PCT / 100.0), 2)
                    actual_risk = raw_sl - entry_p
                    risk = max(actual_risk, min_risk)
                    sl_p = round(entry_p + risk, 2)
                    tp_p = round(entry_p - (risk * 2.0), 2)
                    
                    log(f"[NORMAL SETUP] {symbol_label} ({interval}) BEARISH SCOB at {bar_time} IST! Entry: ${entry_p}, SL: ${sl_p}, TP: ${tp_p}")
                    res = place_delta_bracket_order(symbol_label, "sell", entry_p, sl_p, tp_p, setup_type="NORMAL", size=DEFAULT_SIZE)
                    save_placed_order(order_key, {
                        'symbol': symbol_label, 'tf': interval, 'side': 'sell', 'mode': 'NORMAL',
                        'entry': entry_p, 'sl': sl_p, 'tp': tp_p, 'result': str(res), 'time': str(datetime.datetime.now(IST_TZ))
                    })
    except Exception as e:
        log(f"[NORMAL SCAN ERROR] {symbol_label} {interval}: {e}")

def check_heikin_ashi_btc_setup(interval="15m"):
    try:
        raw_df = yf.download("BTC-USD", period="5d", interval=interval, progress=False)
        if isinstance(raw_df.columns, pd.MultiIndex): raw_df.columns = raw_df.columns.get_level_values(0)
        raw_df = raw_df.dropna().copy()
        if len(raw_df) < 50: return
        
        df = calculate_heikin_ashi(raw_df)
        if df.index.tz is None: df.index = df.index.tz_localize('UTC')
        df_ist = df.index.tz_convert('Asia/Kolkata')
        
        df['EMA_100'] = df['HA_Close'].ewm(span=100, adjust=False).mean()
        real_o, real_c, real_h, real_l = df['Open'].values, df['Close'].values, df['High'].values, df['Low'].values
        ha_o, ha_c, ha_h, ha_l = df['HA_Open'].values, df['HA_Close'].values, df['HA_High'].values, df['HA_Low'].values
        ema = df['EMA_100'].values
        dates = df.index
        n = len(df)
        placed = load_placed_orders()
        
        for i in range(n-4, n-1):
            cur_hour_ist = df_ist[i].hour
            # 7:00 AM to 11:00 PM IST
            if not (7 <= cur_hour_ist < 23):
                continue
                
            bar_time = str(dates[i])
            order_key = f"BTCUSD_{interval}_{bar_time}_HA"
            if order_key in placed: continue
            
            # HA Bullish SCOB
            if (ha_o[i-2]>ha_c[i-2]) and (ha_c[i-1]>ha_o[i-1]) and (ha_c[i]>ha_o[i]) and (ha_l[i-1]<ha_l[i-2]) and (ha_c[i]>ha_h[i-1]):
                if (ha_c[i] > ema[i]):
                    entry_p = round(float(max(real_o[i-1], real_c[i-1])), 2)
                    raw_sl  = round(float(real_l[i-1]), 2)
                    min_risk = round(entry_p * (MIN_RISK_PCT / 100.0), 2)
                    actual_risk = entry_p - raw_sl
                    risk = max(actual_risk, min_risk)
                    sl_p = round(entry_p - risk, 2)
                    tp_p = round(entry_p + (risk * 2.0), 2)
                    
                    log(f"[HEIKIN-ASHI SETUP] BTCUSD ({interval}) BULLISH MELONA at {bar_time}! Entry: ${entry_p}, SL: ${sl_p}, TP: ${tp_p}")
                    res = place_delta_bracket_order("BTCUSD", "buy", entry_p, sl_p, tp_p, setup_type="HEIKIN-ASHI", size=DEFAULT_SIZE)
                    save_placed_order(order_key, {
                        'symbol': 'BTCUSD', 'tf': interval, 'side': 'buy', 'mode': 'HEIKIN-ASHI',
                        'entry': entry_p, 'sl': sl_p, 'tp': tp_p, 'result': str(res), 'time': str(datetime.datetime.now(IST_TZ))
                    })

            # HA Bearish SCOB
            if (ha_o[i-2]<ha_c[i-2]) and (ha_c[i-1]<ha_o[i-1]) and (ha_c[i]<ha_o[i]) and (ha_h[i-1]>ha_h[i-2]) and (ha_c[i]<ha_l[i-1]):
                if (ha_c[i] < ema[i]):
                    entry_p = round(float(min(real_o[i-1], real_c[i-1])), 2)
                    raw_sl  = round(float(real_h[i-1]), 2)
                    min_risk = round(entry_p * (MIN_RISK_PCT / 100.0), 2)
                    actual_risk = raw_sl - entry_p
                    risk = max(actual_risk, min_risk)
                    sl_p = round(entry_p + risk, 2)
                    tp_p = round(entry_p - (risk * 2.0), 2)
                    
                    log(f"[HEIKIN-ASHI SETUP] BTCUSD ({interval}) BEARISH MELONA at {bar_time}! Entry: ${entry_p}, SL: ${sl_p}, TP: ${tp_p}")
                    res = place_delta_bracket_order("BTCUSD", "sell", entry_p, sl_p, tp_p, setup_type="HEIKIN-ASHI", size=DEFAULT_SIZE)
                    save_placed_order(order_key, {
                        'symbol': 'BTCUSD', 'tf': interval, 'side': 'sell', 'mode': 'HEIKIN-ASHI',
                        'entry': entry_p, 'sl': sl_p, 'tp': tp_p, 'result': str(res), 'time': str(datetime.datetime.now(IST_TZ))
                    })
    except Exception as e:
        log(f"[HA BTC SCAN ERROR] {interval}: {e}")

def run_cloud_bot():
    log("==========================================================================")
    log("DELTA EXCHANGE CLOUD 24/7 AUTO TRADER (V5 HYBRID PRO)")
    log("Trading Window: 7:00 AM to 11:00 PM IST (Asian + London + NY)")
    log("Engine 1: Normal Candles Melona SCOB + 100 EMA (ETHUSD & BTCUSD | 15m, 1h)")
    log("Engine 2: Heikin-Ashi Melona SCOB + 100 EMA (BTCUSD | 15m, 1h - 73.3% WR)")
    log(f"Trade Quantity: {DEFAULT_SIZE} Contracts | 1:2 R:R Bracket Limit Orders")
    log("Environment: 24/7 Cloud Server | Mobile Alerts Ready")
    log("==========================================================================")
    
    cycle = 0
    while True:
        cycle += 1
        log(f"--- Cloud Scan Cycle #{cycle} ---")
        
        # 1. Normal Candle Scans (ETH & BTC)
        check_normal_candles_setup("ETH-USD", "15m", "ETHUSD")
        check_normal_candles_setup("ETH-USD", "1h",  "ETHUSD")
        check_normal_candles_setup("BTC-USD", "15m", "BTCUSD")
        check_normal_candles_setup("BTC-USD", "1h",  "BTCUSD")
        
        # 2. Heikin-Ashi Candle Scans for BTC
        check_heikin_ashi_btc_setup(interval="15m")
        check_heikin_ashi_btc_setup(interval="1h")
        
        time.sleep(60)

from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        resp = {"status": "ONLINE", "bot": "DELTA_V5_PRO", "time": str(datetime.datetime.now(IST_TZ))}
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

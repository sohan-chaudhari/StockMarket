import websocket
import json

tickers = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
           "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
           "BAJFINANCE", "LT", "WIPRO", "AXISBANK", "TITAN",
           "ADANIENT", "NTPC", "SUNPHARMA", "POWERGRID", "MARUTI",
           "ULTRACEMCO", "HCLTECH", "TATAMOTORS", "ONGC", "M&M",
           "COALINDIA", "IOC", "BPCL", "HINDALCO", "TRENT"]

FEED_URL = "wss://feeds.feeds.smartapi.in/feeds?token=W01pEufpuj75Z8GGmTFw2CMT2dZnZL6b"
old_handle = None

def on_message(ws, msg):
    global old_handle
    try:
        data = json.loads(msg)
        if isinstance(data, list):
            for item in data:
                tok = item.get("tok")
                if tok and "lp" in item:
                    print(f"Ticker: {tok} -> LTP: {item['lp']}")
        else:
            print("Single message:", data)
    except Exception:
        pass

def on_error(ws, error):
    print("Error:", error)

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed")

def on_open(ws):
    print("WebSocket opened")
    request = {
        "action": "subscribe",
        "params": {
            "mode": 3,
            "tokenList": tickers
        }
    }
    ws.send(json.dumps(request))
    print("Subscription request sent")

ws = websocket.WebSocketApp(
    FEED_URL,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close,
)
ws.on_open = on_open
ws.run_forever()

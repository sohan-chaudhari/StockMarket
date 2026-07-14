import re
import json

with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# First, remove any partial or fully broken dashboard_websocket method
content = re.sub(r'@app\.websocket\("/ws/dashboard"\).*?(?=# --- CSRF Protection Logic ---)', '', content, flags=re.DOTALL)

ws_code = """@app.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    await websocket.accept()
    dashboard_connections.add(websocket)
    tickers = list(LANDING_PAGE_TICKERS)
    last_price_update: float = 0
    last_heartbeat_time: float = 0
    last_status: str = ""

    try:
        await websocket.send_json({
            "type": "connected",
            "ts": datetime.now(IST).isoformat()
        })

        try:
            init_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
            if isinstance(init_msg, dict) and "tickers" in init_msg:
                new_tickers = [str(t).upper().strip() for t in init_msg["tickers"] if t]
                if new_tickers:
                    tickers = new_tickers
                    print(f"[WS Dashboard] Subscribed to {len(tickers)} tickers")
                    try:
                        angelone_service.subscribe_tickers(tickers)
                    except Exception: pass
        except asyncio.TimeoutError:
            pass
        except Exception as _e:
            print(f"[WS Dashboard] Init message error: {_e}")

        while True:
            now_ts = time.time()
            now_ist = datetime.now(IST)
            market_open = is_market_open(now_ist)
            price_interval = 2.0 if market_open else 60.0

            new_status = "open" if market_open else "closed"
            if new_status != last_status:
                try:
                    await websocket.send_json({
                        "type": "market_status",
                        "status": new_status,
                        "ts": now_ist.isoformat()
                    })
                    last_status = new_status
                except Exception:
                    break

            if now_ts - last_price_update >= price_interval:
                prices: dict = {}
                missing: list = []
                for t in tickers:
                    try:
                        yf_t = resolve_yf_ticker(t)
                        cached_entry = _live_prices_cache.get(yf_t)
                        cached_data = cached_entry["data"] if cached_entry and cached_entry.get("data") else None
                        
                        live_tick = angelone_service.latest_ticks.get(t)
                        
                        if live_tick and "current_price" in live_tick and cached_data:
                            prices[t] = cached_data.copy()
                            prices[t]["current"] = live_tick.get('current_price')
                        elif cached_entry and (now_ts - cached_entry["ts"] < 120):
                            if cached_data:
                                prices[t] = cached_data
                        else:
                            missing.append(t)
                            
                        if live_tick and not cached_data and t not in missing:
                            missing.append(t)
                    except Exception:
                        pass

                if missing:
                    async def fetch_and_cache(miss_list):
                        try:
                            yf_missing = [resolve_yf_ticker(tk) for tk in miss_list]
                            fetched = await asyncio.wait_for(fetch_batch_live_data(yf_missing), timeout=8.0)
                            now = time.time()
                            for tk in miss_list:
                                yt = resolve_yf_ticker(tk)
                                raw = fetched.get(yt) or fetched.get(tk)
                                if raw and raw.get('current_price'):
                                    dv = {
                                        "current": raw.get('current_price', 0),
                                        "open":    raw.get('open', 0),
                                        "high":    raw.get('high', 0),
                                        "low":     raw.get('low', 0),
                                        "prev_close": raw.get('previous_close', raw.get('current_price', 0))
                                    }
                                    _live_prices_cache[yt] = {"data": dv, "ts": now}
                                else:
                                    _live_prices_cache[yt] = {"data": None, "ts": now}
                        except Exception as _cwe:
                            print(f"[WS Dashboard] Async Cache-warm error: {_cwe}")
                            
                    asyncio.create_task(fetch_and_cache(missing))

                if prices:
                    try:
                        await websocket.send_json({
                            "type": "price_update",
                            "data": prices,
                            "ts": now_ist.isoformat()
                        })
                    except Exception:
                        break
                last_price_update = now_ts

            if now_ts - last_heartbeat_time >= 30:
                try:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "ts": now_ist.isoformat()
                    })
                    last_heartbeat_time = now_ts
                except Exception:
                    break

            try:
                client_msg = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
                if isinstance(client_msg, dict):
                    msg_type = client_msg.get("type", "")
                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong", "ts": datetime.now(IST).isoformat()})
                    elif msg_type == "subscribe":
                        new_t = client_msg.get("tickers", [])
                        if new_t:
                            tickers = [str(t).upper().strip() for t in new_t if t]
                            dashboard_tickers.update(tickers)
                            try:
                                angelone_service.subscribe_tickers(tickers)
                            except Exception: pass
                            last_price_update = 0
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        print("[WS Dashboard] Client disconnected normally")
    except Exception as _ex:
        print(f"[WS Dashboard] Unexpected error: {_ex}")
    finally:
        dashboard_connections.discard(websocket)
        print("[WS Dashboard] Connection closed")

"""

content = content.replace('# --- CSRF Protection Logic ---', ws_code + '\n# --- CSRF Protection Logic ---')

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Replaced successfully.")

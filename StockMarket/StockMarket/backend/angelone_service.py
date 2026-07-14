"""
Angel One SmartAPI Service
Centralized service for fetching live stock prices from Angel One API
"""

import os
import asyncio
import pandas as pd
import requests
from typing import Optional, Dict
from SmartApi import SmartConnect
import pyotp
import time
from datetime import datetime
from dotenv import load_dotenv
import pathlib

# Load environment variables from backend/.env
backend_dir = pathlib.Path(__file__).parent.resolve()
env_path = backend_dir / ".env"
load_dotenv(dotenv_path=env_path)


from SmartApi.smartWebSocketV2 import SmartWebSocketV2
import json
import threading
from aggregator import candle_aggregator
from hardcoded_tokens import HARDCODED_TOKENS as _HARDCODED_TOKENS

INSTRUMENTS_CACHE_FILE = "instruments_cache.json"

TICKER_ALIASES = {
    "MARUTL": "MARUTI",
    "MARUTI": "MARUTI",
    "NIFTY5O": "NIFTY50",
    "NIFTY50": "NIFTY50",
    "NIFTY": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "SENSEX": "SENSEX",
    "FINNIFTY": "FINNIFTY",
}

class AngelOneService:
    """Singleton service for Angel One API integration"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AngelOneService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.api_key = os.getenv("ANGELONE_API_KEY")
        self.client_id = os.getenv("ANGELONE_CLIENT_ID")
        self.password = os.getenv("ANGELONE_PASSWORD")
        self.totp_token = os.getenv("ANGELONE_TOTP_TOKEN")
        
        self.smart_api = None
        self.instruments_df = None
        self.is_logged_in = False
        self._initialized = True
        
        # WebSocket Streaming Data
        self.sws = None
        self.feed_token = None
        self.latest_ticks = {} # Format: { "TOKEN_ID": {"ltp": 1500.25, "time": timestamp, "ticker": "RELIANCE.NS"} }
        self.latest_ticks_lock = threading.Lock()
        self.subscribed_tokens = set()
        self.subscription_times = {} # ticker -> timestamp
        self.token_to_ticker_map = {}
        self.token_to_exch_type_map = {} # token -> exchangeType
        self.on_tick_callback = None # Callback for unthrottled broadcasts
        self.ws_thread = None
        self._last_login_time = 0.0
        
        print("[AngelOne] Service initialized (credentials loaded from environment)")
    
    def login(self) -> bool:
        """
        Authenticate with Angel One API
        Returns: True if successful, False otherwise
        """
        if not all([self.api_key, self.client_id, self.password, self.totp_token]):
            print("[!] [AngelOne] Missing credentials in environment variables")
            return False
        
        try:
            print("[AUTH] [AngelOne] Logging in to Angel One...")
            
            # Initialize SmartConnect
            self.smart_api = SmartConnect(api_key=self.api_key)
            
            # Generate TOTP code
            totp = pyotp.TOTP(self.totp_token)
            totp_code = totp.now()
            
            # Login
            session = self.smart_api.generateSession(
                self.client_id, 
                self.password, 
                totp_code
            )
            
            if not session.get('status'):
                print(f"[X] [AngelOne] Login failed: {session.get('message', 'Unknown error')}")
                return False
            
            data = session.get('data', {})
            self.feed_token = data.get('feedToken')
            self.jwt_token = data.get('jwtToken')
            
            self.is_logged_in = True
            self._last_login_time = time.time()
            print(f"[OK] [AngelOne] Login successful! Session: {self.jwt_token[:20]}...")
            
            # Initialize WebSocket immediately on login
            self._init_websocket()
            
            return True
            
        except Exception as e:
            print(f"[X] [AngelOne] Login error: {e}")
            self.is_logged_in = False
            return False
            
    def _init_websocket(self):
        """Initialize and connect the AngelOne SmartStream WebSocket"""
        if not self.feed_token or not self.client_id or not self.api_key or not self.jwt_token:
            print("[!] [AngelOne WS] Cannot initialize WS: missing credentials.")
            return
            
        try:
            client_code = self.client_id
            feed_token = self.feed_token
            api_key = self.api_key
            jwt_token = self.jwt_token
            
            # SmartWebSocketV2(jwtToken, api_key, client_code, feed_token)
            self.sws = SmartWebSocketV2(jwt_token, api_key, client_code, feed_token)
            self.sws.ROOT_URI = 'wss://smartapisocket.angelone.in/smart-stream'
            self.sws.MAX_RETRY_ATTEMPT = 20
            self.ws_connected = False
            
            def on_data(wsapp, msg):
                # Thin handler as requested
                self._handle_ws_tick(msg)
                
            def on_open(wsapp):
                print("[AngelOne WS] Connection Opened Successfully")
                self.ws_connected = True
                # Resubscribe to existing tokens if reconnecting
                if self.subscribed_tokens:
                    self._send_subscription(list(self.subscribed_tokens))
                    
            def on_error(wsapp, error):
                print(f"[!] [AngelOne WS] Error: {error}")
                self.ws_connected = False
                # WS auth failure does NOT affect REST API — separate concern
                if "401" in str(error) or "Unauthorized" in str(error):
                    print("[AngelOne WS] Unauthorized — will retry WS connection later")
                
            def on_close(wsapp, close_status_code, close_msg):
                print(f"[AngelOne WS] Connection Closed: {close_status_code} - {close_msg}")
                self.ws_connected = False
                
            self.sws.on_open = on_open
            self.sws.on_data = on_data
            self.sws.on_error = on_error
            self.sws.on_close = on_close
            
            # Run the ticker in a background thread
            if getattr(self, 'ws_thread', None) and self.ws_thread.is_alive():
                self.ws_thread.join(timeout=1.0)
                if self.ws_thread.is_alive():
                    print("[AngelOne WS] Old thread stuck, abandoning it")
            
            self.ws_thread = threading.Thread(target=self.sws.connect, daemon=True)
            self.ws_thread.start()
            self.last_ws_connect_time = time.time()
            print("[AngelOne WS] Client Thread Started")
            
        except Exception as e:
            print(f"[!] [AngelOne WS] Initialization error: {e}")

    async def ensure_connection(self):
        """Checks REST session status and re-logs in if needed.
        Proactively re-logs in if session is older than 12 hours to avoid token expiry."""
        if not self.is_logged_in or not self.smart_api or not self.jwt_token:
            print("[AngelOne] Session invalid or tokens missing. Attempting re-login...")
            return self.login()

        # Proactive re-login: AngelOne tokens typically expire after 24h
        if time.time() - self._last_login_time > 43200:  # 12 hours
            print("[AngelOne] Session approaching expiry, re-logging...")
            return self.login()

        # WS reconnection is handled separately by ensure_ws_connected
        return True

    def ensure_ws_connected(self):
        """Checks WS status and reconnects if down. Called periodically from background task."""
        if not self.is_logged_in:
            return False

        # --- Silent Drop Detection ---
        if self.ws_connected and self.subscribed_tokens:
            import time
            now = time.time()
            last_tick = 0
            with self.latest_ticks_lock:
                for t_data in self.latest_ticks.values():
                    ts = t_data.get("_ts", 0)
                    if ts > last_tick:
                        last_tick = ts
            # If we received ticks previously, but none in the last 60s -> assume dead
            # BUT wait 60s after connecting before deciding it's a silent drop
            connect_age = now - getattr(self, 'last_ws_connect_time', now)
            if last_tick > 0 and (now - last_tick) > 60 and connect_age > 60:
                print(f"[AngelOne WS] Silent drop detected (no ticks for {int(now - last_tick)}s). Reconnecting...")
                self.ws_connected = False
                try:
                    if hasattr(self, 'sws') and self.sws:
                        # try to close the socket forcefully
                        self.sws.close()
                except Exception:
                    pass

        if self.ws_connected:
            return True
        try:
            print("[AngelOne WS] Reconnecting WebSocket...")
            self._init_websocket()
            # on_open callback will auto-resubscribe once connected
            return True
        except Exception as e:
            print(f"[!] [AngelOne WS] Reconnection error: {e}")
            return False

    def _handle_ws_tick(self, msg):
        """
        Store the latest tick into the buffer.
        Implements Multi-Key Normalization.
        AngelOne WS sends prices in paise (×100) — divide by 100 to get rupees.
        """
        try:
            if not isinstance(msg, dict):
                return

            # 1. Multi-Key Normalization (SDK Resilience)
            token = msg.get('token') or msg.get('tk') or msg.get('subscription_token')
            ltp = msg.get('last_traded_price') or msg.get('ltp') or msg.get('lp')

            if token and ltp is not None:
                price = round(float(ltp) / 100.0, 2)

                ticker = self.token_to_ticker_map.get(str(token))
                if ticker:
                    # 2. Extract exchange timestamp from WS message (epoch seconds)
                    exch_ts = msg.get('exchange_timestamp') or msg.get('exch_timestamp') or msg.get('last_traded_timestamp') or msg.get('ft')
                    try:
                        exch_ts = float(exch_ts)
                        if exch_ts > 1e10:  # milliseconds → seconds
                            exch_ts = exch_ts / 1000.0
                        if exch_ts < 1.5e9:  # unreasonably old or zero — use server time
                            if exch_ts <= 0:
                                print(f"[AngelWS] {ticker} exchange_timestamp=0, falling back to server time")
                            exch_ts = time.time()
                    except (TypeError, ValueError):
                        exch_ts = time.time()

                    def _to_rupees(v):
                        try:
                            return round(float(v) / 100.0, 2)
                        except (ValueError, TypeError):
                            return 0.0

                    open_rupees = _to_rupees(msg.get('open_price_of_the_day') or msg.get('open') or 0)
                    high_rupees = _to_rupees(msg.get('high_price_of_the_day') or msg.get('high') or 0)
                    low_rupees = _to_rupees(msg.get('low_price_of_the_day') or msg.get('low') or 0)
                    prev_close_rupees = _to_rupees(msg.get('closed_price') or msg.get('close') or msg.get('previous_close') or 0)
                    daily_volume = int(msg.get('volume_trade_for_the_day') or 0)
                    tick_volume = int(msg.get('last_traded_quantity') or 0)

                    with self.latest_ticks_lock:
                        self.latest_ticks[ticker] = {
                            "current_price": price,
                            "open": open_rupees,
                            "high": high_rupees,
                            "low": low_rupees,
                            "prev_close": prev_close_rupees,
                            "volume": daily_volume,
                            "tick_volume": tick_volume,
                            "time": datetime.now(),
                            "token": token,
                            "_ts": exch_ts,
                            "_source": "angel_ws",
                        }
                    
                    # 3. Trigger Unthrottled Callback (The "Visual Wiggle" Path)
                    # Build tick_data directly from computed values to avoid race with REST poller
                    if self.on_tick_callback:
                        try:
                            self.on_tick_callback(ticker, {
                                "current_price": price,
                                "current": price,
                                "open": open_rupees,
                                "high": high_rupees,
                                "low": low_rupees,
                                "prev_close": prev_close_rupees,
                                "volume": daily_volume,
                                "tick_volume": tick_volume,
                                "_ts": exch_ts,
                                "_source": "angel_ws",
                            })
                        except Exception as e:
                            print(f"[AngelWS] Callback error for {ticker}: {e}")
        except Exception as e:
            print(f"[AngelWS] Tick handler error for token {token}: {e}")
            
    def _send_subscription(self, token_list):
        """Send correlation payload to WS"""
        if not self.sws: return
        try:
            # Mode 3 = Full (Open, High, Low, Close, LTP, Volume)
            correlation_id = "stream_1"
            action = 1 # 1 = subscribe
            mode = 3   # 3 for FULL
            
            exch_groups = {}
            for tk in token_list:
                exch_type = self.token_to_exch_type_map.get(tk, 1)
                if exch_type not in exch_groups:
                    exch_groups[exch_type] = []
                exch_groups[exch_type].append(tk)
                
            token_list_formatted = []
            for exch_type, tks in exch_groups.items():
                token_list_formatted.append({"exchangeType": exch_type, "tokens": tks})
                
            self.sws.subscribe(correlation_id, mode, token_list_formatted)
            print(f"[AngelOne WS] Subscribed to {len(token_list)} tokens across {len(exch_groups)} exchanges")
        except Exception as e:
            print(f"[!] [AngelOne WS] Subscription Error: {e}")

    def subscribe_tickers(self, tickers: list):
        """
        Takes a list of string tickers, resolves their tokens, and subscribes.
        Implements the 'Wait-for-Handshake' Pattern (Anti-Race Condition).
        """
        import time
        new_tokens = []
        for t in tickers:
            token_info = self.get_token(t)
            if token_info:
                exch = token_info.get('exchange', 'NSE')
                exch_type = 1 if exch == 'NSE' else (3 if exch == 'BSE' else 1)
                tk = str(token_info['token'])
                
                self.token_to_ticker_map[tk] = t
                self.token_to_exch_type_map[tk] = exch_type
                
                if tk not in self.subscribed_tokens:
                    new_tokens.append(tk)
                    self.subscribed_tokens.add(tk)
                    self.subscription_times[t] = time.time()
                    
        if new_tokens and self.sws:
            # Wait for Handshake Pattern — allow up to 15s for WS to connect
            retry_count = 0
            while not getattr(self, 'ws_connected', False) and retry_count < 30:
                if retry_count < 10 or retry_count % 5 == 0:
                    print(f"[AngelOne WS] Waiting for handshake... attempt {retry_count+1}")
                time.sleep(0.5)
                retry_count += 1
            
            if getattr(self, 'ws_connected', False):
                self._send_subscription(list(self.subscribed_tokens))
            else:
                print("[!] [AngelOne WS] Handshake timeout. Subscription will occur on_open.")
    
    def unsubscribe_tickers(self, tickers: list):
        """Unsubscribe tickers from AngelOne WebSocket."""
        tokens_to_remove = []
        for t in tickers:
            token_info = self.get_token(t)
            if token_info:
                tk = str(token_info['token'])
                if tk in self.subscribed_tokens:
                    tokens_to_remove.append(tk)

        if tokens_to_remove and self.sws and getattr(self, 'ws_connected', False):
            exch_groups = {}
            for tk in tokens_to_remove:
                exch_type = self.token_to_exch_type_map.get(tk, 1)
                exch_groups.setdefault(exch_type, []).append(tk)
            token_list = [{"exchangeType": et, "tokens": tks} for et, tks in exch_groups.items()]
            try:
                self.sws.unsubscribe("stream_1", 3, token_list)
                for tk in tokens_to_remove:
                    self.subscribed_tokens.discard(tk)
                print(f"[AngelOne WS] Unsubscribed {len(tokens_to_remove)} tokens")
            except Exception as e:
                print(f"[!] [AngelOne WS] Unsubscribe error: {e}")

    def _cache_path(self) -> str:
        return os.path.join(str(backend_dir), INSTRUMENTS_CACHE_FILE)

    def load_instruments(self) -> bool:
        """
        Load instrument list from local cache or download from Angel One.
        Falls back to hardcoded tokens (~2400 stocks) if download fails.
        """
        cache_path = self._cache_path()

        # 1. Try loading from local cache first
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    instruments_data = json.load(f)
                self.instruments_df = pd.DataFrame(instruments_data)
                nse_count = len(self.instruments_df[self.instruments_df['exch_seg'] == 'NSE'])
                print(f"[OK] [AngelOne] Loaded {len(self.instruments_df)} instruments from cache ({nse_count} NSE)")
                return True
            except Exception as e:
                print(f"[!] [AngelOne] Cache load failed: {e}")

        # 2. Download from AngelOne
        try:
            print("[DL] [AngelOne] Downloading instrument list...")
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            response = requests.get(url, timeout=60)

            if response.status_code != 200:
                print(f"[X] [AngelOne] HTTP {response.status_code}")
                raise IOError(f"HTTP {response.status_code}")

            instruments_data = response.json()
            self.instruments_df = pd.DataFrame(instruments_data)

            # Save to local cache
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(instruments_data, f)
                print(f"[OK] [AngelOne] Cached to {cache_path}")
            except Exception as ce:
                print(f"[!] [AngelOne] Could not write cache: {ce}")

            nse_count = len(self.instruments_df[self.instruments_df['exch_seg'] == 'NSE'])
            print(f"[OK] [AngelOne] Downloaded {len(self.instruments_df)} instruments ({nse_count} NSE)")
            return True

        except Exception as e:
            print(f"[X] [AngelOne] Download failed: {e}")
            print(f"[!] [AngelOne] Using hardcoded tokens ({len(_HARDCODED_TOKENS)} stocks) as fallback")
            return False
    
    def _normalize_ticker(self, ticker: str) -> str:
        """Normalize ticker: uppercase, strip suffixes, fix common misspellings."""
        t = ticker.upper().strip()
        # Strip exchange suffixes
        for suffix in [".NS", ".NSE", ".BO", ".BSE", "-EQ", "-BE"]:
            if t.endswith(suffix):
                t = t[:-len(suffix)]
        # Check aliases for common misspellings
        return TICKER_ALIASES.get(t, t)

    def get_token(self, ticker: str, exchange: str = "NSE") -> Optional[Dict]:
        """
        Get instrument token and trading symbol for a ticker.
        
        Resolution order:
          1. Ticker normalization (fix misspellings, strip suffixes)
          2. Known index tokens (hardcoded)
          3. Hardcoded tokens (~2400 stocks, works without download)
          4. instruments_df (downloaded + cached instrument list)
          5. Partial/name match in instruments_df
        """
        ticker = self._normalize_ticker(ticker)
        
        # 1. Known index tokens (always available regardless of download)
        INDEX_TOKENS = {
            "NIFTY":      {"token": "26000", "symbol": "NIFTY 50", "name": "NIFTY 50", "exchange": "NSE"},
            "NIFTY50":    {"token": "26000", "symbol": "NIFTY 50", "name": "NIFTY 50", "exchange": "NSE"},
            "BANKNIFTY":  {"token": "26009", "symbol": "NIFTY BANK", "name": "NIFTY BANK", "exchange": "NSE"},
            "NIFTYBANK":  {"token": "26009", "symbol": "NIFTY BANK", "name": "NIFTY BANK", "exchange": "NSE"},
            "FINNIFTY":   {"token": "26037", "symbol": "NIFTY FIN SERVICE", "name": "NIFTY FIN SERVICE", "exchange": "NSE"},
            "MIDCAP":     {"token": "26074", "symbol": "NIFTY MIDCAP 50", "name": "NIFTY MIDCAP 50", "exchange": "NSE"},
            "SENSEX":     {"token": "99919000", "symbol": "SENSEX", "name": "BSE SENSEX", "exchange": "BSE"},
        }
        if ticker in INDEX_TOKENS:
            return dict(INDEX_TOKENS[ticker])
        
        # 2. Try hardcoded tokens (~2400 stocks, works offline)
        if ticker in _HARDCODED_TOKENS:
            token_data = dict(_HARDCODED_TOKENS[ticker])
            if "exchange" not in token_data:
                token_data["exchange"] = exchange
            return token_data
        
        # 3. If instruments not loaded, hardcoded is all we have
        if self.instruments_df is None:
            return None
        
        # 4. Search in instruments_df
        idx_mappings = {"NIFTY": "NIFTY 50", "NIFTY50": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
                        "SENSEX": "SENSEX", "FINNIFTY": "NIFTY FIN SERVICE"}
        search = idx_mappings.get(ticker, ticker)
        
        # Exact symbol match
        matches = self.instruments_df[
            (self.instruments_df["symbol"] == search) &
            (self.instruments_df["exch_seg"] == exchange)
        ]
        # Try with -EQ suffix
        if matches.empty and exchange == "NSE":
            matches = self.instruments_df[
                (self.instruments_df["symbol"] == f"{search}-EQ") &
                (self.instruments_df["exch_seg"] == exchange)
            ]
        # Partial match
        if matches.empty:
            matches = self.instruments_df[
                self.instruments_df["symbol"].str.contains(search, case=False, na=False) &
                (self.instruments_df["exch_seg"] == exchange)
            ]
        # Name match
        if matches.empty:
            matches = self.instruments_df[
                self.instruments_df["name"].str.contains(search, case=False, na=False) &
                (self.instruments_df["exch_seg"] == exchange)
            ]
        
        if matches.empty:
            return None
        
        row = matches.iloc[0]
        return {"token": str(row["token"]), "symbol": row["symbol"],
                "name": row["name"], "exchange": row["exch_seg"]}
    
    async def get_live_price(self, ticker: str) -> Optional[Dict]:
        """
        Fetch live price data for a ticker from Angel One
        """
        await self.ensure_connection()
        if not self.is_logged_in:
            return None
        
        instrument = self.get_token(ticker)
        if not instrument:
            return None
        exch = instrument['exchange']
        sym = instrument['symbol']
        tok = instrument['token']
        
        for attempt in range(2): # Retry once if token is missing
            try:
                ltp_data = await asyncio.to_thread(
                    self.smart_api.ltpData, exch, sym, tok
                )
                
                # Check for "Token missing" error specifically
                if not ltp_data.get('status'):
                    msg = ltp_data.get('message', '').lower()
                    err_code = ltp_data.get('errorCode', '')
                    
                    if 'token missing' in msg or err_code == 'AG8003' or 'unauthorized' in msg:
                        print(f"[!] [AngelOne] Token missing detected (Attempt {attempt+1}). Re-logging...")
                        self.is_logged_in = False
                        if await self.ensure_connection():
                            continue # Retry the loop
                        else:
                            return None
                            
                    print(f"[!] [AngelOne] API error for {ticker}: {ltp_data.get('message', 'Unknown error')}")
                    return None
                
                data = ltp_data.get('data', {})
                ltp = float(data.get('ltp', 0))
                if ltp > 0:
                    return {
                        'current_price': ltp,
                        'open': float(data.get('open', ltp)),
                        'high': float(data.get('high', ltp)),
                        'low': float(data.get('low', ltp)),
                        'volume': int(data.get('volume', 0)),
                        'previous_close': float(data.get('close', ltp))
                    }
                return None
                
            except Exception as e:
                print(f"[X] [AngelOne] Error fetching price for {ticker}: {e}")
                if "Token missing" in str(e) or "unauthorized" in str(e).lower():
                    self.is_logged_in = False
                    if await self.ensure_connection(): continue
                return None
        return None
    
    async def get_batch_live_prices(self, tickers: list) -> dict:
        """
        Fetch live prices for multiple tickers concurrently using ltpData.
        """
        await self.ensure_connection()
        if not self.is_logged_in or not tickers:
            return {}

        # OPTIMIZATION: If we are batch fetching > 10 stocks, do NOT hit AngelOne REST API for the stocks.
        # It takes ~1-2 seconds per request. Let them fail so `main.py` can fetch them via `yf.download()`.
        # However, ALWAYS fetch major indices from AngelOne so their `prev_close` is perfectly accurate.
        indices = [t for t in tickers if t in ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY']]
        stocks = [t for t in tickers if t not in indices]
        
        tickers_to_fetch = indices.copy()
        if len(stocks) <= 10:
            tickers_to_fetch.extend(stocks)
            
        if not tickers_to_fetch:
            return {}

        semaphore = asyncio.Semaphore(5)

        async def fetch_one(t):
            async with semaphore:
                try:
                    result = await self.get_live_price(t)
                    if result:
                        prev_close = result.get('previous_close', 0)
                        try:
                            candle_aggregator.set_previous_close(t, prev_close)
                        except Exception:
                            pass
                        return t, result
                except Exception as e:
                    print(f"[X] [AngelOne] Batch fetch error for {t}: {e}")
                return t, None

        tasks = [fetch_one(t) for t in tickers_to_fetch]
        results_list = await asyncio.gather(*tasks)
        return {t: r for t, r in results_list if r is not None}

    def is_subscription_ready(self, ticker: str, grace_period_seconds: int = 15) -> bool:
        """
        Checks if a ticker is likely receiving live ticks or if we should fallback to REST.
        Avoids REST API hammers during WebSocket handshake/warmup.
        """
        import time
        # If we already have a live tick, it's ready!
        if self.latest_ticks.get(ticker):
            return True
            
        last_sub_time = self.subscription_times.get(ticker)
        if not last_sub_time:
            # Not even in subscription list yet
            return False
            
        # If it's been less than the grace period, tell caller to "Wait 5 for WebSockets"
        return (time.time() - last_sub_time) > grace_period_seconds

    def logout(self):
        """Terminate Angel One session"""
        if self.smart_api and self.is_logged_in:
            try:
                self.smart_api.terminateSession(self.client_id)
                print("[OK] [AngelOne] Logged out successfully")
            except Exception as e:
                print(f"[!] [AngelOne] Logout error: {e}")
            finally:
                self.is_logged_in = False


# Global singleton instance
angelone_service = AngelOneService()

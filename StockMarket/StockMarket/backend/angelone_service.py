"""
Angel One SmartAPI Service
Centralized service for fetching live stock prices from Angel One API
"""

import os
import asyncio
import logging
import requests
from typing import Optional, Dict
from SmartApi import SmartConnect
import pyotp
import time
from datetime import datetime
import sys
from dotenv import load_dotenv
import pathlib

# Load environment variables from backend/.env and ensure backend is in sys.path
backend_dir = pathlib.Path(__file__).parent.resolve()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
env_path = backend_dir / ".env"
load_dotenv(dotenv_path=env_path)

# HIGH-04 fix (Batch 4): _handle_ws_tick runs on EVERY incoming tick from the
# AngelOne WS, per subscribed ticker (potentially thousands during market
# hours). It previously used bare print() even on its hot path -- logger.debug
# costs nothing when the level is above DEBUG (the format args are never
# evaluated), unlike an f-string print() which always pays the formatting
# cost and always writes to stdout regardless of level.
logger = logging.getLogger(__name__)


from SmartApi.smartWebSocketV2 import SmartWebSocketV2
import json
import threading
from aggregator import candle_aggregator
from hardcoded_tokens import HARDCODED_TOKENS as _HARDCODED_TOKENS

INSTRUMENTS_CACHE_FILE = "instruments_cache.json"

# HIGH-04: rate limit for the exchange_timestamp-fallback debug log, matching
# the pattern main.py's event-loop-lag probe already uses for a similar
# "could fire every tick" diagnostic.
_EXCH_TS_FALLBACK_LOG_INTERVAL = 60  # seconds

TICKER_ALIASES = {
    "MARUTL": "MARUTI",
    "MARUTI": "MARUTI",
    "NIFTY5O": "NIFTY50",
    "NIFTY50": "NIFTY50",
    "NIFTY": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "SENSEX": "SENSEX",
    "FINNIFTY": "FINNIFTY",
    "MIDCAP": "MIDCAP",
    "MIDCPNIFTY": "MIDCAP",
    "SMALLCAP": "SMALLCAP",
    "NIFTYSMLCAP100": "SMALLCAP",
    "NIFTY_AUTO": "NIFTY_AUTO",
    "NIFTYAUTO": "NIFTY_AUTO",
    "NIFTY_IT": "NIFTY_IT",
    "NIFTYIT": "NIFTY_IT",
    "NIFTY_PHARMA": "NIFTY_PHARMA",
    "NIFTYPHARMA": "NIFTY_PHARMA",
    "NIFTY_FMCG": "NIFTY_FMCG",
    "NIFTYFMCG": "NIFTY_FMCG",
    "NIFTY_METAL": "NIFTY_METAL",
    "NIFTYMETAL": "NIFTY_METAL",
    "NIFTY_ENERGY": "NIFTY_ENERGY",
    "NIFTYENERGY": "NIFTY_ENERGY",
    "NIFTY_MEDIA": "NIFTY_MEDIA",
    "NIFTYMEDIA": "NIFTY_MEDIA",
    "NIFTY_PSU_BANK": "NIFTY_PSU_BANK",
    "NIFTYPSUBANK": "NIFTY_PSU_BANK",
    "NIFTY_REALTY": "NIFTY_REALTY",
    "NIFTYREALTY": "NIFTY_REALTY",
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
        # instruments_df permanently set to None — instrument data is now held in
        # _sym_idx and _instruments_rows (plain dicts, no pandas DataFrame).
        self.instruments_df = None
        self._instruments_loaded = False        # True once load_instruments() succeeds
        self._sym_idx: dict = {}               # (symbol.upper(), exch_seg) -> list[row_dict]
        self._instruments_rows: list = []      # minimal rows for partial/name match
        self.is_logged_in = False
        self._initialized = True
        
        # WebSocket Streaming Data
        self.sws = None
        self.feed_token = None
        self.latest_ticks = {} # Format: { "TOKEN_ID": {"ltp": 1500.25, "time": timestamp, "ticker": "RELIANCE.NS"} }
        self.latest_ticks_lock = threading.RLock()
        # Decision 3 (duplicate-tick volume dedup): Angel One's Mode-2/Quote WS
        # payload carries no unique per-trade ID (only token, ltp, exchange
        # timestamp, day OHLC, day volume, last-traded quantity) — confirmed by
        # reading _handle_ws_tick's field extraction below. In the absence of a
        # true trade ID, a composite (exchange_ts, price, qty) fingerprint is
        # the safest available dedup key: a WS reconnect replaying the last
        # trade reproduces this exact tuple, while two genuinely different
        # trades sharing it in the same second is comparatively rare and, if it
        # ever happens, drops one real trade's volume rather than double
        # counting a replayed one — a smaller error than the current gap.
        # Lives on the singleton instance (not the socket), so it survives a
        # WS reconnect intact instead of resetting exactly when it's needed.
        self._last_tick_fingerprint: Dict[str, tuple] = {}
        self._duplicate_tick_count = 0
        # HIGH-04: cumulative count of ticks missing/zero exchange_timestamp
        # (never reset -- meant for stats/observability), plus a rate-limit
        # marker so the corresponding debug log line fires at most once per
        # _EXCH_TS_FALLBACK_LOG_INTERVAL even if every tick hits this path.
        self._exch_ts_fallback_count = 0
        self._exch_ts_fallback_last_log = 0.0
        self.subscribed_tokens = set()
        self.token_lock = threading.Lock()
        self.subscription_times = {} # ticker -> timestamp
        self.token_to_ticker_map = {}
        self.token_to_exch_type_map = {} # token -> exchangeType
        self.on_tick_callback = None # Callback for unthrottled broadcasts
        self.ws_thread = None
        self._last_login_time = 0.0
        self._ws_reconnecting = False  # guard: prevents overlapping reconnect calls
        
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

            # RT-07: close the previous socket before replacing self.sws --
            # otherwise a re-login (e.g. the 12h proactive refresh) leaves the
            # old socket's connect() loop running forever in its own thread
            # with nothing referencing it, doubling live connection usage.
            old_sws = getattr(self, 'sws', None)
            if old_sws:
                try:
                    # SMOKE-01 fix: SmartWebSocketV2 has no .close() method (a
                    # real production smoke test proved this -- confirmed via
                    # dir(SmartWebSocketV2): the actual method is
                    # close_connection()). This silently no-op'd behind the
                    # broad except below on every single reconnect/re-login
                    # since RT-07 was written -- the old socket's connect()
                    # loop was never actually closed, exactly the leak RT-07
                    # was meant to prevent.
                    old_sws.close_connection()
                except Exception:
                    pass

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
                self._ws_reconnecting = False  # allow future reconnects
                self.last_ws_connect_time = time.time()
                # Resubscribe to existing tokens if reconnecting (snapshot under token_lock)
                with self.token_lock:
                    tokens_snapshot = list(self.subscribed_tokens)
                if tokens_snapshot:
                    self._send_subscription(tokens_snapshot)

            def on_error(wsapp, error):
                print(f"[!] [AngelOne WS] Error: {error}")
                self.ws_connected = False
                self._ws_reconnecting = False  # allow watchdog to retry
                # WS auth failure does NOT affect REST API — separate concern
                if "401" in str(error) or "Unauthorized" in str(error):
                    print("[AngelOne WS] Unauthorized — will retry WS connection later")

            def on_close(wsapp, close_status_code, close_msg):
                print(f"[AngelOne WS] Connection Closed: {close_status_code} - {close_msg}")
                self.ws_connected = False
                self._ws_reconnecting = False  # allow watchdog to retry
                
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
            # RT-08: login() does blocking network I/O -- run off the event
            # loop so it doesn't stall every concurrent request/broadcast.
            return await asyncio.to_thread(self.login)

        # Proactive re-login: AngelOne tokens typically expire after 24h
        if time.time() - self._last_login_time > 43200:  # 12 hours
            print("[AngelOne] Session approaching expiry, re-logging...")
            return await asyncio.to_thread(self.login)

        # WS reconnection is handled separately by ensure_ws_connected
        return True

    def ensure_ws_connected(self):
        """Checks WS status and reconnects if down. Called periodically from background task."""
        if not self.is_logged_in:
            return False

        # --- Silent Drop Detection ---
        with self.token_lock:
            has_sub_tokens = bool(self.subscribed_tokens)
        if self.ws_connected and has_sub_tokens:
            import time
            now = time.time()
            last_tick = 0
            with self.latest_ticks_lock:
                for t_data in self.latest_ticks.values():
                    ts = t_data.get("_ts", 0)
                    if ts > last_tick:
                        last_tick = ts
            # If we received ticks previously, but none in the last 20s -> assume dead
            # BUT wait 30s after connecting before deciding it's a silent drop
            connect_age = now - getattr(self, 'last_ws_connect_time', now)
            if last_tick > 0 and (now - last_tick) > 20 and connect_age > 30:
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
        # Guard: if a reconnect attempt is already in-flight, don't spawn another.
        # on_open/on_close/on_error each clear this flag so the next watchdog tick
        # can retry after the current attempt settles.
        if self._ws_reconnecting:
            return False
        try:
            self._ws_reconnecting = True
            print("[AngelOne WS] Reconnecting WebSocket...")
            self._init_websocket()
            # ws_connected stays False until on_open fires; flag cleared there too
            return True
        except Exception as e:
            print(f"[!] [AngelOne WS] Reconnection error: {e}")
            self._ws_reconnecting = False
            return False

    def get_dedup_stats(self) -> dict:
        """Observability for Decision 3 — how often the fingerprint guard fires."""
        return {
            "duplicate_ticks_rejected": self._duplicate_tick_count,
            "tickers_tracked": len(self._last_tick_fingerprint),
            "exchange_timestamp_fallbacks": self._exch_ts_fallback_count,
        }

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
                                # HIGH-04 fix: this used to be an unconditional
                                # print() on every tick that hits this branch --
                                # a real risk given AngelOne alternates full-quote
                                # and LTP-only packets (the latter commonly omit
                                # exchange_timestamp). Now a cheap counter
                                # (always) plus a rate-limited debug log (at most
                                # once per _EXCH_TS_FALLBACK_LOG_INTERVAL), not a
                                # print on every occurrence. Market-data behavior
                                # (falling back to server time) is unchanged.
                                self._exch_ts_fallback_count += 1
                                now_wall = time.time()
                                if now_wall - self._exch_ts_fallback_last_log > _EXCH_TS_FALLBACK_LOG_INTERVAL:
                                    logger.debug(
                                        "[AngelWS] exchange_timestamp missing/zero, falling back to "
                                        "server time (%d occurrence(s) so far; last ticker: %s)",
                                        self._exch_ts_fallback_count, ticker,
                                    )
                                    self._exch_ts_fallback_last_log = now_wall
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

                    # AngelOne's WS alternates between full "quote" packets and
                    # LTP-only packets that carry no OHLC/prev-close fields at all.
                    # Without this fallback, an LTP-only tick would zero out
                    # open/high/low/prev_close for the ticker, making the frontend's
                    # client-side change/% calc (which falls back through
                    # prev_close -> open -> current) briefly show a wildly wrong
                    # change % until the next full-quote tick arrives. Mirrors the
                    # same preserve-on-zero pattern already used by
                    # CriticalIndexPoller._poll_ticker in price_poller.py.
                    with self.latest_ticks_lock:
                        _prev_tick = self.latest_ticks.get(ticker, {})
                    if open_rupees <= 0 and _prev_tick.get('open', 0) > 0:
                        open_rupees = _prev_tick['open']
                    if high_rupees <= 0 and _prev_tick.get('high', 0) > 0:
                        high_rupees = _prev_tick['high']
                    if low_rupees <= 0 and _prev_tick.get('low', 0) > 0:
                        low_rupees = _prev_tick['low']
                    if prev_close_rupees <= 0 and _prev_tick.get('prev_close', 0) > 0:
                        prev_close_rupees = _prev_tick['prev_close']

                    # Decision 3: reject an exact replay of the last tick for this
                    # ticker (same exchange timestamp + price + quantity) before it
                    # ever reaches the aggregator, so a WS reconnect resubscribe
                    # snapshot can't double-count that trade's volume. See the
                    # fingerprint field's docstring in __init__ for why this
                    # composite key was chosen over a true trade ID.
                    fingerprint = (exch_ts, price, tick_volume)
                    is_duplicate = self._last_tick_fingerprint.get(ticker) == fingerprint
                    self._last_tick_fingerprint[ticker] = fingerprint
                    if is_duplicate:
                        self._duplicate_tick_count += 1
                        with self.latest_ticks_lock:
                            if ticker in self.latest_ticks:
                                self.latest_ticks[ticker]["_received_ts"] = time.time()
                        return

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
                            "_received_ts": time.time(),  # server receive time (for stale detection)
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
                            logger.warning("[AngelWS] Callback error for %s: %s", ticker, e)
        except Exception as e:
            logger.error("[AngelWS] Tick handler error for token %s: %s", token, e)
            
    def _send_subscription(self, token_list):
        """Send correlation payload to WS. Batches into chunks of 200 tokens to avoid
        overwhelming AngelOne's socket (oversized payloads cause silent drops / rejections)."""
        if not self.sws: return
        BATCH_SIZE = 200
        correlation_id = "stream_1"
        action = 1  # subscribe
        mode = 2    # Quote mode (Indices do not support mode 3 SnapQuote)
        total_sent = 0
        for batch_start in range(0, len(token_list), BATCH_SIZE):
            batch = token_list[batch_start:batch_start + BATCH_SIZE]
            try:
                exch_groups = {}
                for tk in batch:
                    exch_type = self.token_to_exch_type_map.get(tk, 1)
                    exch_groups.setdefault(exch_type, []).append(tk)
                token_list_formatted = [{"exchangeType": e, "tokens": tks} for e, tks in exch_groups.items()]
                self.sws.subscribe(correlation_id, mode, token_list_formatted)
                total_sent += len(batch)
                if batch_start + BATCH_SIZE < len(token_list):
                    time.sleep(0.15)  # brief pause between batches
            except Exception as e:
                print(f"[!] [AngelOne WS] Subscription Error (batch {batch_start//BATCH_SIZE + 1}): {e}")
        if total_sent:
            print(f"[AngelOne WS] Subscribed {total_sent} tokens in {(len(token_list) + BATCH_SIZE - 1) // BATCH_SIZE} batch(es)")

    def subscribe_tickers(self, tickers: list):
        """
        Takes a list of string tickers, resolves their tokens, and subscribes.
        Implements the 'Wait-for-Handshake' Pattern (Anti-Race Condition).
        Uses token_lock to protect self.subscribed_tokens mutations.
        """
        import time
        new_tokens = []
        with self.token_lock:
            for t in tickers:
                clean_t = t.strip().upper().replace('.NS', '').replace('.BO', '')
                token_info = self.get_token(clean_t) or self.get_token(t)
                if token_info:
                    tk = str(token_info['token'])  # FIX: was never assigned — caused NameError / stale value
                    exch = token_info.get('exchange', 'NSE')
                    exch_type = 1 if exch == 'NSE' else (3 if exch == 'BSE' else 1)
                    CANONICAL_INDEX_TOKENS = {
                        "99926000": "NIFTY",
                        "99926009": "BANKNIFTY",
                        "99926037": "FINNIFTY",
                        "99926074": "MIDCAP",
                        "99926032": "SMALLCAP",
                        "99919000": "SENSEX",
                        "99926029": "NIFTY_AUTO",
                        "99926008": "NIFTY_IT",
                        "99926023": "NIFTY_PHARMA",
                        "99926021": "NIFTY_FMCG",
                        "99926030": "NIFTY_METAL",
                        "99926020": "NIFTY_ENERGY",
                        "99926031": "NIFTY_MEDIA",
                        "99926025": "NIFTY_PSU_BANK",
                        "99926018": "NIFTY_REALTY",
                    }
                    if tk in CANONICAL_INDEX_TOKENS:
                        clean_t = CANONICAL_INDEX_TOKENS[tk]
                    
                    self.token_to_ticker_map[tk] = clean_t
                    self.token_to_exch_type_map[tk] = exch_type
                    
                    if tk not in self.subscribed_tokens:
                        new_tokens.append(tk)
                        self.subscribed_tokens.add(tk)
                        self.subscription_times[clean_t] = time.time()
                    
        if new_tokens and self.sws:
            # Wait for Handshake Pattern — allow up to 15s for WS to connect
            retry_count = 0
            while not getattr(self, 'ws_connected', False) and retry_count < 30:
                if retry_count < 10 or retry_count % 5 == 0:
                    print(f"[AngelOne WS] Waiting for handshake... attempt {retry_count+1}")
                time.sleep(0.5)
                retry_count += 1
            
            if getattr(self, 'ws_connected', False):
                self._send_subscription(new_tokens)
            else:
                print("[!] [AngelOne WS] Handshake timeout. Subscription will occur on_open.")
    
    def unsubscribe_tickers(self, tickers: list):
        """Unsubscribe tickers from AngelOne WebSocket.
        Protects token mutations under token_lock without holding lock during broker API calls."""
        tokens_to_remove = []
        tickers_to_remove = []
        with self.token_lock:
            for t in tickers:
                token_info = self.get_token(t)
                if token_info:
                    tk = str(token_info['token'])
                    if tk in self.subscribed_tokens:
                        tokens_to_remove.append(tk)
                        tickers_to_remove.append(t)
                        self.subscribed_tokens.discard(tk)

        if tokens_to_remove and self.sws and getattr(self, 'ws_connected', False):
            exch_groups = {}
            for tk in tokens_to_remove:
                exch_type = self.token_to_exch_type_map.get(tk, 1)
                exch_groups.setdefault(exch_type, []).append(tk)
            token_list = [{"exchangeType": et, "tokens": tks} for et, tks in exch_groups.items()]
            try:
                self.sws.unsubscribe("stream_1", 3, token_list)
                print(f"[AngelOne WS] Unsubscribed {len(tokens_to_remove)} tokens")
            except Exception as e:
                print(f"[!] [AngelOne WS] Unsubscribe error: {e}")

        # Phase 15A: token_to_ticker_map/token_to_exch_type_map/subscription_times
        # and latest_ticks were populated in subscribe_tickers/_handle_ws_tick but
        # never pruned here -- is_subscription_ready() (below) treats a truthy
        # latest_ticks entry as "still receiving live ticks", so an unsubscribed
        # ticker kept reporting ready forever on its last frozen price instead of
        # correctly falling back to REST. Cleared last, after the broker call, so
        # exch_type lookups above still see the token they need.
        with self.token_lock:
            for tk in tokens_to_remove:
                self.token_to_ticker_map.pop(tk, None)
                self.token_to_exch_type_map.pop(tk, None)
        for t in tickers_to_remove:
            self.subscription_times.pop(t, None)
        if tickers_to_remove:
            with self.latest_ticks_lock:
                for t in tickers_to_remove:
                    self.latest_ticks.pop(t, None)

    def _cache_path(self) -> str:
        return os.path.join(str(backend_dir), INSTRUMENTS_CACHE_FILE)

    def _build_instrument_index(self, instruments_data: list) -> None:
        """
        Build two fast lookup structures from the raw instrument JSON.
        Stores only 4 fields per instrument (token, symbol, name, exch_seg).
        Does NOT create a pandas DataFrame — the list is discarded after indexing.
        """
        sym_idx: dict = {}
        rows: list = []
        for item in instruments_data:
            token = str(item.get("token", "")).strip()
            symbol = str(item.get("symbol", "")).strip()
            name = str(item.get("name", "")).strip()
            exch_seg = str(item.get("exch_seg", "")).strip()
            if not token or not symbol or not exch_seg:
                continue
            row = {"token": token, "symbol": symbol, "name": name, "exch_seg": exch_seg}
            rows.append(row)
            # Primary key: exact symbol + exchange (case-normalised for lookup speed)
            key = (symbol.upper(), exch_seg)
            sym_idx.setdefault(key, []).append(row)
        self._sym_idx = sym_idx
        self._instruments_rows = rows
        self._instruments_loaded = True
        # instruments_df intentionally stays None — never populated again

    def load_instruments(self) -> bool:
        """
        Load instrument list from local cache or download from Angel One.
        Falls back to hardcoded tokens (~2400 stocks) if download fails.
        Builds a fast dict index — no pandas DataFrame is created.
        """
        cache_path = self._cache_path()

        # 1. Try loading from local cache first
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    instruments_data = json.load(f)
                self._build_instrument_index(instruments_data)
                del instruments_data   # release the raw list immediately
                nse_count = sum(1 for r in self._instruments_rows if r["exch_seg"] == "NSE")
                print(f"[OK] [AngelOne] Loaded {len(self._instruments_rows)} instruments from cache ({nse_count} NSE)")
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

            # Save to local cache before indexing so we always have the full data on disk
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(instruments_data, f)
                print(f"[OK] [AngelOne] Cached to {cache_path}")
            except Exception as ce:
                print(f"[!] [AngelOne] Could not write cache: {ce}")

            self._build_instrument_index(instruments_data)
            del instruments_data   # release the raw list immediately
            nse_count = sum(1 for r in self._instruments_rows if r["exch_seg"] == "NSE")
            print(f"[OK] [AngelOne] Downloaded {len(self._instruments_rows)} instruments ({nse_count} NSE)")
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
          4. _sym_idx exact/EQ lookup (dict, O(1))
          5. Partial/name match in _instruments_rows (linear scan, rare)
        """
        ticker = self._normalize_ticker(ticker)
        
        # 1. Known index tokens (always available regardless of download)
        INDEX_TOKENS = {
            "NIFTY":          {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY 50", "exchange": "NSE"},
            "NIFTY50":        {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY 50", "exchange": "NSE"},
            "BANKNIFTY":      {"token": "99926009", "symbol": "Nifty Bank", "name": "BANK NIFTY", "exchange": "NSE"},
            "NIFTYBANK":      {"token": "99926009", "symbol": "Nifty Bank", "name": "BANK NIFTY", "exchange": "NSE"},
            "FINNIFTY":       {"token": "99926037", "symbol": "Nifty Fin Service", "name": "NIFTY FIN", "exchange": "NSE"},
            "MIDCAP":         {"token": "99926074", "symbol": "NIFTY MID SELECT", "name": "MIDCAP", "exchange": "NSE"},
            "MIDCPNIFTY":     {"token": "99926074", "symbol": "NIFTY MID SELECT", "name": "MIDCAP", "exchange": "NSE"},
            "SMALLCAP":       {"token": "99926032", "symbol": "NIFTY SMLCAP 100", "name": "SMALLCAP", "exchange": "NSE"},
            "NIFTYSMLCAP100": {"token": "99926032", "symbol": "NIFTY SMLCAP 100", "name": "SMALLCAP", "exchange": "NSE"},
            "NIFTYMIDCAP100": {"token": "99926011", "symbol": "NIFTY MIDCAP 100", "name": "NIFTY MIDCAP 100", "exchange": "NSE"},
            "SENSEX":         {"token": "99919000", "symbol": "SENSEX", "name": "SENSEX", "exchange": "BSE"},
            "NIFTY_AUTO":     {"token": "99926029", "symbol": "Nifty Auto", "name": "NIFTY AUTO", "exchange": "NSE"},
            "NIFTYAUTO":      {"token": "99926029", "symbol": "Nifty Auto", "name": "NIFTY AUTO", "exchange": "NSE"},
            "NIFTY_IT":       {"token": "99926008", "symbol": "Nifty IT", "name": "NIFTY IT", "exchange": "NSE"},
            "NIFTYIT":        {"token": "99926008", "symbol": "Nifty IT", "name": "NIFTY IT", "exchange": "NSE"},
            "NIFTY_PHARMA":   {"token": "99926023", "symbol": "Nifty Pharma", "name": "NIFTY PHARMA", "exchange": "NSE"},
            "NIFTYPHARMA":    {"token": "99926023", "symbol": "Nifty Pharma", "name": "NIFTY PHARMA", "exchange": "NSE"},
            "NIFTY_FMCG":     {"token": "99926021", "symbol": "Nifty FMCG", "name": "NIFTY FMCG", "exchange": "NSE"},
            "NIFTYFMCG":      {"token": "99926021", "symbol": "Nifty FMCG", "name": "NIFTY FMCG", "exchange": "NSE"},
            "NIFTY_METAL":    {"token": "99926030", "symbol": "Nifty Metal", "name": "NIFTY METAL", "exchange": "NSE"},
            "NIFTYMETAL":     {"token": "99926030", "symbol": "Nifty Metal", "name": "NIFTY METAL", "exchange": "NSE"},
            "NIFTY_ENERGY":   {"token": "99926020", "symbol": "Nifty Energy", "name": "NIFTY ENERGY", "exchange": "NSE"},
            "NIFTYENERGY":    {"token": "99926020", "symbol": "Nifty Energy", "name": "NIFTY ENERGY", "exchange": "NSE"},
            "NIFTY_MEDIA":    {"token": "99926031", "symbol": "Nifty Media", "name": "NIFTY MEDIA", "exchange": "NSE"},
            "NIFTYMEDIA":     {"token": "99926031", "symbol": "Nifty Media", "name": "NIFTY MEDIA", "exchange": "NSE"},
            "NIFTY_PSU_BANK": {"token": "99926025", "symbol": "Nifty PSU Bank", "name": "NIFTY PSU BANK", "exchange": "NSE"},
            "NIFTYPSUBANK":   {"token": "99926025", "symbol": "Nifty PSU Bank", "name": "NIFTY PSU BANK", "exchange": "NSE"},
            "NIFTY_REALTY":   {"token": "99926018", "symbol": "Nifty Realty", "name": "NIFTY REALTY", "exchange": "NSE"},
            "NIFTYREALTY":    {"token": "99926018", "symbol": "Nifty Realty", "name": "NIFTY REALTY", "exchange": "NSE"},
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
        if not self._instruments_loaded:
            return None

        # 4. Search in _sym_idx / _instruments_rows (no pandas needed)
        idx_mappings = {"NIFTY": "NIFTY 50", "NIFTY50": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
                        "SENSEX": "SENSEX", "FINNIFTY": "NIFTY FIN SERVICE"}
        search = idx_mappings.get(ticker, ticker)

        # 4a. Exact symbol match (O(1) dict lookup)
        matches = self._sym_idx.get((search.upper(), exchange), [])

        # 4b. Try with -EQ suffix (O(1) dict lookup)
        if not matches and exchange == "NSE":
            matches = self._sym_idx.get((f"{search.upper()}-EQ", exchange), [])

        # 4c. Partial symbol match (linear scan — rare fallback path)
        if not matches:
            sl = search.lower()
            matches = [r for r in self._instruments_rows
                       if sl in r["symbol"].lower() and r["exch_seg"] == exchange]

        # 4d. Name match (linear scan — rarest fallback path)
        if not matches:
            matches = [r for r in self._instruments_rows
                       if sl in r["name"].lower() and r["exch_seg"] == exchange]

        if not matches:
            return None

        row = matches[0]
        return {"token": row["token"], "symbol": row["symbol"],
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
        with self.latest_ticks_lock:
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

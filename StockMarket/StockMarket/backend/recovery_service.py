"""
RecoveryService
================
Lazy, per-ticker crash recovery using REST API as the authoritative source.

On first view after a crash:
  1. Read last completed 5m from DB
  2. Call Angel One REST API from (last_ts - 5min) to now
  3. UPSERT all returned candles (overlap replaces incomplete state)
  4. Pass recovered buffer directly to Live5mBuilder + LiveTimeframeManager
  5. Mark ticker as recovered

Never recovers all 5,243 tickers at startup. Only recovers what users view.
"""

import queue
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from event_bus import event_bus

IST = timedelta(hours=5, minutes=30)


class RecoveryService:
    def __init__(self, db_session_factory, historical_service=None, yf_downloader=None):
        self._db_factory = db_session_factory
        self._historical_service = historical_service
        self._yf_downloader = yf_downloader
        self._lock = threading.Lock()
        self._recovered: Set[str] = set()
        self._in_progress: Set[str] = set()
        self._recovery_errors: Dict[str, str] = {}
        self._total_recoveries = 0
        self._total_duration_ms = 0
        self._queue = queue.Queue()
        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()

    def _process_queue(self):
        while True:
            try:
                item = self._queue.get()
                if item is None:
                    break
                ticker, live_5m_builder = item
                self.recover_ticker(ticker, live_5m_builder)
                time.sleep(0.35)  # Max ~2.8 req/sec to safely stay within AngelOne rate limits
                self._queue.task_done()
            except Exception as e:
                print(f"[RecoveryQueue] Error: {e}")

    def enqueue_recovery(self, ticker: str, live_5m_builder=None):
        with self._lock:
            if ticker in self._recovered or ticker in self._in_progress:
                return
            self._in_progress.add(ticker)
        self._queue.put((ticker, live_5m_builder))

    def needs_recovery(self, ticker: str) -> bool:
        with self._lock:
            if ticker in self._recovered or ticker in self._in_progress:
                return False
        return True

    def mark_recovered(self, ticker: str):
        with self._lock:
            self._recovered.add(ticker)
            self._in_progress.discard(ticker)

    def mark_needs_recovery(self, ticker: str):
        """Re-arms a ticker for recovery.

        needs_recovery() permanently returns False after a ticker's first
        recovery this process lifetime, so recover_ticker() silently no-ops
        on every later call for it -- including a second WS gap for the same
        ticker later the same day. Callers that detect a fresh gap (e.g. the
        WS reconnect watchdog) call this immediately before recover_ticker()
        so the one-shot guard doesn't swallow a recovery that's genuinely
        needed again.
        """
        with self._lock:
            self._recovered.discard(ticker)
            self._in_progress.discard(ticker)

    def reset(self):
        with self._lock:
            self._recovered.clear()
            self._in_progress.clear()
            self._recovery_errors.clear()
            self._total_recoveries = 0
            self._total_duration_ms = 0

    def recover_ticker(self, ticker: str, live_5m_builder) -> bool:
        with self._lock:
            if ticker in self._recovered:
                return True
            self._in_progress.add(ticker)

        start = datetime.now()
        print(f"[Recovery] Starting recovery for {ticker}")

        try:
            db = self._db_factory()
            try:
                from models import Candle
                last = db.query(Candle).filter(
                    Candle.ticker == ticker,
                    Candle.timeframe == "5m",
                    Candle.is_completed == True,
                ).order_by(Candle.timestamp.desc()).first()
            finally:
                db.close()

            since_ts = None
            if last is not None:
                ts = last.timestamp
                if isinstance(ts, datetime):
                    since_ts = ts - timedelta(minutes=5)
                else:
                    since_ts = datetime.now() - timedelta(days=7)
            else:
                since_ts = datetime.now() - timedelta(days=7)

            recovered_candles = self._fetch_from_rest(ticker, since_ts)

            if recovered_candles:
                self._upsert_candles(recovered_candles)
                if live_5m_builder is not None:
                    latest = recovered_candles[-1]
                    live_5m_builder.init_ticker_from_last_candle(ticker, latest)

            with self._lock:
                self._recovered.add(ticker)
                self._total_recoveries += 1
                elapsed = (datetime.now() - start).total_seconds() * 1000
                self._total_duration_ms += elapsed

            print(f"[Recovery] {ticker} complete ({len(recovered_candles)} candles restored)")
            event_bus.emit("recovery.complete", ticker=ticker,
                           recent_5m_buffer=recovered_candles or [])
            return True

        except Exception as e:
            with self._lock:
                self._recovery_errors[ticker] = str(e)
            print(f"[Recovery] FAILED for {ticker}: {e}")
            return False

    def _fetch_from_rest(self, ticker: str, since: datetime) -> List[Dict]:
        candles = []
        if self._historical_service is not None:
            try:
                if not self._historical_service.is_logged_in:
                    self._historical_service.login()
                if self._historical_service.is_logged_in:
                    from_date = since.date()
                    to_date = datetime.now().date()
                    angel_candles = self._historical_service.get_historical_candles(
                        ticker=ticker, interval="FIVE_MINUTE",
                        from_date=from_date, to_date=to_date, exchange='BSE' if ticker == 'SENSEX' else 'NSE'
                    )
                    if angel_candles:
                        for c in angel_candles:
                            ts = c.get('timestamp')
                            if not isinstance(ts, datetime):
                                continue
                            candles.append({
                                "ticker": ticker,
                                "timeframe": "5m",
                                "timestamp": ts,
                                "open": float(c.get('open', 0)),
                                "high": float(c.get('high', 0)),
                                "low": float(c.get('low', 0)),
                                "close": float(c.get('close', 0)),
                                "volume": int(c.get('volume', 0)),
                            })
                        return candles
            except Exception as e:
                print(f"[Recovery] Angel One REST failed for {ticker}: {e}")

        if self._yf_downloader is not None and not self._yf_downloader.is_failed(ticker):
            try:
                # Keep in sync with INDEX_MAP in main.py -- an index missing here
                # falls through to the `f"{ticker}.NS"` guess below, which isn't a
                # real yfinance symbol for indices (that suffix is for NSE equities).
                # FINNIFTY was missing this entry, so every recovery attempt for it
                # silently failed ("FINNIFTY.NS" doesn't exist on Yahoo Finance),
                # leaving its 1D candle table stuck for months.
                YFINANCE_INDEX_MAP = {
                    'NIFTY': '^NSEI',
                    'BANKNIFTY': '^NSEBANK',
                    'SENSEX': '^BSESN',
                    'FINNIFTY': 'NIFTY_FIN_SERVICE.NS',
                    'MIDCAP': '^NSEMDCP50',
                    'SMALLCAP': '^CNXSC',
                }
                yf_ticker = YFINANCE_INDEX_MAP.get(ticker, f"{ticker}.NS")
                df = self._yf_downloader.download_single(yf_ticker, period="5d", interval="5m")
                if df and not df[0].empty:
                    data = df[0]
                    for idx, row in data.iterrows():
                        ts = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
                        candles.append({
                            "ticker": ticker,
                            "timeframe": "5m",
                            "timestamp": ts.replace(tzinfo=None) if ts.tzinfo else ts,
                            "open": float(row.get('Open', 0)),
                            "high": float(row.get('High', 0)),
                            "low": float(row.get('Low', 0)),
                            "close": float(row.get('Close', 0)),
                            "volume": int(row.get('Volume', 0)),
                        })
            except Exception as e:
                print(f"[Recovery] yfinance fallback failed for {ticker}: {e}")

        return candles

    def _upsert_candles(self, candles: List[Dict]):
        from aggregator import batch_flush_candles
        try:
            batch_flush_candles(candles)
        except Exception as e:
            print(f"[Recovery] UPSERT error: {e}")
            raise

    def get_stats(self) -> dict:
        with self._lock:
            avg_ms = (self._total_duration_ms / self._total_recoveries) if self._total_recoveries else 0
            return {
                "recovered_tickers": len(self._recovered),
                "total_recoveries": self._total_recoveries,
                "avg_duration_ms": round(avg_ms, 1),
                "errors": len(self._recovery_errors),
                "error_details": dict(list(self._recovery_errors.items())[:5]),
            }

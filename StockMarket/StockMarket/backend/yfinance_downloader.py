"""
Yahoo Finance Download Manager
==============================
Centralized handling for all yfinance interactions across the application.

Error classification:
  - DELISTED:    Symbol no longer exists on exchange
  - NOT_FOUND:   Symbol not found in yfinance database
  - NETWORK:     Transient network failure
  - RATE_LIMIT:  Rate limited by Yahoo Finance
  - EMPTY:       Download returned no data
  - TIMEOUT:     Request timed out

Features:
  - Exponential backoff retry with per-error-class intervals
  - Per-ticker isolation (one failure never blocks the batch)
  - DB-persisted inactive/delisted tracking via StockMetadata
  - NSE symbol validation before download
  - Failure report generation
  - Thread-safe
"""

import time
import threading
import functools
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Set, Callable, Any
from enum import Enum
import logging

import yfinance as yf
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger("yfinance_downloader")


class YFErrorClass(Enum):
    DELISTED = "delisted"
    NOT_FOUND = "not_found"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    EMPTY = "empty"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


# Retry intervals per error class (seconds before retrying)
YF_RETRY_INTERVALS: Dict[YFErrorClass, float] = {
    YFErrorClass.DELISTED:  86400 * 7,     # 1 week — delisted stocks don't come back
    YFErrorClass.NOT_FOUND: 86400 * 7,     # 1 week — won't magically appear
    YFErrorClass.NETWORK:   60,            # 1 min — transient
    YFErrorClass.RATE_LIMIT: 300,          # 5 min — Yahoo's rate limit
    YFErrorClass.EMPTY:     3600,          # 1 hour — might reappear
    YFErrorClass.TIMEOUT:   120,           # 2 min — transient
    YFErrorClass.UNKNOWN:   600,           # 10 min — be conservative
}

# Maximum retry attempts for transient errors before escalating
MAX_RETRY_ATTEMPTS = {
    YFErrorClass.DELISTED:  0,
    YFErrorClass.NOT_FOUND: 0,
    YFErrorClass.NETWORK:   0,
    YFErrorClass.RATE_LIMIT: 0,
    YFErrorClass.EMPTY:     0,
    YFErrorClass.TIMEOUT:   0,
    YFErrorClass.UNKNOWN:   0,
}

# Base backoff seconds for exponential backoff
BASE_BACKOFF = 0.5


def classify_yfinance_error(exc: Exception, result=None) -> YFErrorClass:
    """Classify a yfinance failure into an error class."""
    exc_str = str(exc).lower()

    if result is not None and hasattr(result, 'empty') and result.empty:
        return YFErrorClass.EMPTY

    if isinstance(exc, asyncio.TimeoutError):
        return YFErrorClass.TIMEOUT

    msg = str(exc).lower()
    if "expecting value" in msg or "jsondecode" in msg or "blocked" in msg:
        return YFErrorClass.RATE_LIMIT
    if "possibly delisted" in msg or "delisted" in msg:
        return YFErrorClass.DELISTED
    if "no price data found" in msg or "not found" in msg or "no data" in msg:
        return YFErrorClass.NOT_FOUND
    if "rate limited" in msg or "too many requests" in msg or "429" in msg:
        return YFErrorClass.RATE_LIMIT
    if "timeout" in msg or "timed out" in msg:
        return YFErrorClass.TIMEOUT
    if "connection" in msg or "network" in msg or "reset" in msg or "refused" in msg:
        return YFErrorClass.NETWORK

    return YFErrorClass.UNKNOWN


class YFinanceDownloader:
    """
    Thread-safe yfinance download manager.

    Usage:
        downloader = YFinanceDownloader()
        result = downloader.download(["RELIANCE.NS", "SABOOSOD.NS"], period="5d", interval="1d")
        # result contains only valid symbols; failures are tracked internally

    To retrieve the failure report:
        report = downloader.get_failure_report()
    """

    def __init__(self):
        self._lock = threading.Lock()

        # failure_cache[ticker] = (error_class, timestamp_of_failure)
        self._failure_cache: Dict[str, Tuple[YFErrorClass, float]] = {}

        # retry_counts[ticker] = number of consecutive retries
        self._retry_counts: Dict[str, int] = {}

        # inactive_symbols[ticker] = error_class (for DB persistence)
        self._inactive_symbols: Dict[str, YFErrorClass] = {}

        # Known NSE symbols cache (loaded from stocks_temp.json)
        self._known_symbols: Optional[Set[str]] = None
        self._known_symbols_lock = threading.Lock()

    # ── NSE Symbol Validation ───────────────────────────────────────

    def _load_known_symbols(self) -> Set[str]:
        """Load known NSE symbols from stocks_temp.json."""
        import json
        import os

        backend_dir = os.path.dirname(os.path.abspath(__file__))
        meta_path = os.path.join(backend_dir, "..", "frontend", "stocks_temp.json")
        if not os.path.exists(meta_path):
            meta_path = os.path.join(backend_dir, "..", "StockMarket", "frontend", "stocks_temp.json")
        if not os.path.exists(meta_path):
            return set()

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                stocks = json.load(f)
            return {s["ticker"].strip().upper() for s in stocks if s.get("ticker")}
        except Exception:
            return set()

    def get_known_symbols(self) -> Set[str]:
        """Get the set of known NSE symbols (lazily loaded and cached)."""
        with self._known_symbols_lock:
            if self._known_symbols is None:
                self._known_symbols = self._load_known_symbols()
            return self._known_symbols

    def validate_symbol(self, ticker: str) -> Tuple[bool, str]:
        """
        Validate a ticker symbol against known NSE symbols.

        Returns:
            (is_valid, reason) where reason describes why it's invalid.
        """
        clean = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
        known = self.get_known_symbols()

        if not known:
            # No known symbols loaded — skip validation (allow through)
            return True, ""

        if clean in known:
            return True, ""

        # Check if it's an index (starts with ^)
        if clean.startswith("^"):
            return True, ""

        # Check common index names
        indices = {"NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCAP", "SMALLCAP"}
        if clean in indices or clean.startswith("NIFTY_"):
            return True, ""

        # Check partial match
        for known_sym in known:
            if clean in known_sym or known_sym in clean:
                return True, f"found as partial match for {known_sym}"

        return False, f"symbol {clean} not found in known NSE symbol list"

    # ── Failure Tracking ────────────────────────────────────────────

    def is_failed(self, ticker: str) -> bool:
        """Check if a ticker is in the failure cache and still within its retry interval."""
        with self._lock:
            clean = ticker.strip().upper()
            entry = self._failure_cache.get(clean)
            if entry is None:
                return False
            error_class, fail_ts = entry
            retry_after = YF_RETRY_INTERVALS.get(error_class, 600)
            if time.time() - fail_ts > retry_after:
                # Retry interval expired — remove and allow retry
                del self._failure_cache[clean]
                return False
            return True

    def _mark_failed(self, ticker: str, error_class: YFErrorClass):
        """Mark a ticker as failed with a specific error class."""
        with self._lock:
            clean = ticker.strip().upper()
            old_entry = self._failure_cache.get(clean)
            if old_entry and old_entry[0] == error_class:
                self._retry_counts[clean] = self._retry_counts.get(clean, 0) + 1
            else:
                self._retry_counts[clean] = 1
            self._failure_cache[clean] = (error_class, time.time())

            # P1.3: only a genuine "possibly delisted" signal may mark a symbol
            # permanently INACTIVE. _sync_yfinance_inactive_symbols() persists
            # that set to stock_metadata.is_active=False, which hides the ticker
            # from /api/all-stocks and the screener and -- critically -- has no
            # un-deactivate path.
            # NOT_FOUND also fires for TRANSIENT conditions ("no data found for
            # this date range", "ticker not found in batch result"), so persisting
            # it irreversibly hid valid, data-bearing tickers (measured locally:
            # 4,298 of 4,627 currently-hidden tickers are exactly the set a
            # transient NOT_FOUND would produce, e.g. AARTISURF/ACME/ADISOFT).
            # It still enters the in-process failure cache for its retry interval
            # below, so no extra yfinance load is created -- it just no longer
            # causes a destructive, irreversible DB write.
            if error_class is YFErrorClass.DELISTED:
                self._inactive_symbols[clean] = error_class
                logger.warning(f"[YFDownloader] Marked {clean} as INACTIVE ({error_class.value})")

    def get_inactive_symbols(self) -> Dict[str, str]:
        """Get symbols that should be marked inactive in DB."""
        with self._lock:
            return {k: v.value for k, v in self._inactive_symbols.items()}

    def clear_inactive_symbols(self):
        """Clear inactive symbols list (after persisting to DB)."""
        with self._lock:
            self._inactive_symbols.clear()

    def download(
        self,
        tickers: List[str],
        period: str = "5d",
        interval: str = "5m",
        timeout: float = 15.0,
        retry: bool = True,
        **kwargs
    ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str]]:
        """
        Download data for multiple tickers with per-ticker isolation.

        Args:
            tickers: List of yfinance ticker symbols (e.g. ["RELIANCE.NS", "^NSEI"])
            period: yfinance period string
            interval: yfinance interval string
            timeout: Per-download timeout in seconds
            retry: Whether to retry failed downloads with exponential backoff
            **kwargs: Additional kwargs passed to yf.download

        Returns:
            (results, failures):
                results: Dict[str, pd.DataFrame] — ticker -> DataFrame for successful downloads
                failures: Dict[str, str] — ticker -> error description for failed downloads
        """
        results: Dict[str, pd.DataFrame] = {}
        failures: Dict[str, str] = {}

        # Filter out already-failed tickers
        eligible = []
        skipped = 0
        for t in tickers:
            clean = t.strip().upper()
            if self.is_failed(clean):
                skipped += 1
                failures[clean] = "in failure cache (will retry on next interval)"
                continue
            eligible.append(t)
        if skipped:
            logger.info(f"[YFDownloader] Skipped {skipped}/{len(tickers)} tickers from failure cache")

        if not eligible:
            return results, failures

        # Download all eligible tickers in a single batch
        try:
            df = self._download_batch(eligible, period, interval, timeout, **kwargs)
        except Exception as e:
            error_class = classify_yfinance_error(e)
            if retry:
                # Retry with exponential backoff
                backoff = BASE_BACKOFF
                for attempt in range(MAX_RETRY_ATTEMPTS.get(error_class, 2)):
                    logger.warning(
                        f"[YFDownloader] Batch download failed (attempt {attempt+1}): {e}. "
                        f"Retrying in {backoff}s..."
                    )
                    time.sleep(backoff)
                    try:
                        df = self._download_batch(eligible, period, interval, timeout, **kwargs)
                        break
                    except Exception as retry_e:
                        e = retry_e
                        error_class = classify_yfinance_error(retry_e)
                        backoff *= 2
                else:
                    # All retries exhausted — mark all tickers as failed
                    for t in eligible:
                        clean = t.strip().upper()
                        self._mark_failed(clean, error_class)
                        failures[clean] = str(e)
                    return results, failures
            else:
                for t in eligible:
                    clean = t.strip().upper()
                    self._mark_failed(clean, error_class)
                    failures[clean] = str(e)
                return results, failures

        # Process results per-ticker
        if df is None or df.empty:
            for t in eligible:
                clean = t.strip().upper()
                self._mark_failed(clean, YFErrorClass.EMPTY)
                failures[clean] = "download returned empty DataFrame"
            return results, failures

        # Handle MultiIndex columns (group_by="ticker" mode)
        if isinstance(df.columns, pd.MultiIndex):
            for t in eligible:
                clean = t.strip().upper()
                try:
                    if t in df.columns.get_level_values(0):
                        ticker_df = df[t].dropna(how='all')
                        if not ticker_df.empty:
                            results[clean] = ticker_df
                        else:
                            self._mark_failed(clean, YFErrorClass.EMPTY)
                            failures[clean] = "no data for ticker in batch result"
                    elif t.replace(".NS", "") in df.columns.get_level_values(0):
                        alt_t = t.replace(".NS", "")
                        ticker_df = df[alt_t].dropna(how='all')
                        if not ticker_df.empty:
                            results[clean] = ticker_df
                        else:
                            self._mark_failed(clean, YFErrorClass.EMPTY)
                            failures[clean] = "no data for ticker in batch result"
                    else:
                        self._mark_failed(clean, YFErrorClass.NOT_FOUND)
                        failures[clean] = "ticker not found in batch download"
                except Exception as e:
                    self._mark_failed(clean, YFErrorClass.UNKNOWN)
                    failures[clean] = str(e)
        else:
            # Single ticker result
            if len(eligible) == 1:
                clean = eligible[0].strip().upper()
                if not df.empty:
                    results[clean] = df
                else:
                    self._mark_failed(clean, YFErrorClass.EMPTY)
                    failures[clean] = "download returned empty DataFrame"
            else:
                # Multiple tickers without MultiIndex — distribute by rows
                for t in eligible:
                    clean = t.strip().upper()
                    self._mark_failed(clean, YFErrorClass.UNKNOWN)
                    failures[clean] = "unexpected DataFrame format"

        return results, failures

    def download_single(
        self,
        ticker: str,
        period: str = "5d",
        interval: str = "5m",
        timeout: float = 15.0,
        retry: bool = True,
        **kwargs
    ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """
        Download data for a single ticker.

        Returns:
            (DataFrame or None, error description or None)
        """
        results, failures = self.download(
            [ticker], period=period, interval=interval,
            timeout=timeout, retry=retry, **kwargs
        )
        clean = ticker.strip().upper()
        if clean in results:
            return results[clean], None
        return None, failures.get(clean, "unknown error")

    def _download_batch(
        self,
        tickers: List[str],
        period: str,
        interval: str,
        timeout: float,
        **kwargs
    ) -> pd.DataFrame:
        """Execute the actual yfinance download (can be run in executor for async)."""
        group_by = "ticker" if len(tickers) > 1 else None
        return yf.download(
            tickers=tickers,
            period=period,
            interval=interval,
            group_by=group_by,
            progress=False,
            timeout=timeout,
            **kwargs
        )

    # ── Status Report ───────────────────────────────────────────────

    def get_failure_report(self) -> Dict[str, Any]:
        """Produce a report of all tracked failures."""
        with self._lock:
            now = time.time()
            report = {
                "total_failed": len(self._failure_cache),
                "inactive_symbols": len(self._inactive_symbols),
                "failures": {},
                "retry_counts": dict(self._retry_counts),
            }
            for ticker, (error_class, fail_ts) in sorted(self._failure_cache.items()):
                remaining = YF_RETRY_INTERVALS.get(error_class, 600) - (now - fail_ts)
                report["failures"][ticker] = {
                    "error_class": error_class.value,
                    "failed_at": datetime.fromtimestamp(fail_ts, tz=IST).isoformat(),
                    "retry_in_seconds": max(0, int(remaining)),
                    "retry_count": self._retry_counts.get(ticker, 0),
                }
            return report

    def get_invalid_symbols_report(self) -> List[Dict]:
        """Return a detailed report of all invalid/delisted symbols."""
        with self._lock:
            report = []
            for ticker, (error_class, fail_ts) in sorted(self._failure_cache.items()):
                if error_class in (YFErrorClass.DELISTED, YFErrorClass.NOT_FOUND):
                    report.append({
                        "ticker": ticker,
                        "status": error_class.value,
                        "detected_at": datetime.fromtimestamp(fail_ts, tz=IST).isoformat(),
                    })
            return report


# ── Global singleton ──────────────────────────────────────────────────
yf_downloader = YFinanceDownloader()

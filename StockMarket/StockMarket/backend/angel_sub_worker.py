"""Bounded, serialised worker for Angel One subscription changes.

Replaces six ad-hoc ``threading.Thread(...).start()`` sites in main.py that were
unbounded, unguarded and outside lifecycle management.

Ownership: an explicit INSTANCE owns its queue, counters, thread and lock (no
module-global mutable state), so instances cannot consume each other's work.

Queue saturation policy (desired-state reconciliation, never silent):
  * the queue is genuinely bounded (queue.Queue(maxsize=...));
  * a rejected submit is counted, logged, AND its intent is coalesced per ticker
    into a bounded intent map (bounded by the instrument universe, not by the
    number of operations) so a burst of operations cannot grow memory;
  * whenever the consumer goes idle it reconciles pending intents by calling the
    original subscribe/unsubscribe callable with the coalesced ticker list; a
    reconciliation failure puts the intents back and is logged + counted, so the
    consumer can never lose them silently;
  * a stale duplicate is harmless because reconciliation applies the LATEST
    intent per ticker.

No new dependencies (queue, threading only).
"""
import queue
import threading


class AngelSubWorker:
    def __init__(self, max_pending=256, logger=None, reconcile_interval=0.25):
        self._q = queue.Queue(maxsize=max_pending)
        self.max_pending = max_pending
        self._lock = threading.Lock()
        self._stats = {"started": 0, "done": 0, "failed": 0, "dropped": 0,
                       "max_pending_seen": 0, "reconciled": 0,
                       "reconcile_failures": 0, "pending_intents": 0}
        self._thread = None
        self._log = logger or print
        self._name = "angel-sub-%d" % id(self)
        self._intents = {}
        self._intent_lock = threading.Lock()
        self._idle = max(0.01, float(reconcile_interval))

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        """Idempotent: repeated calls never create a second consumer."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
            self._thread.start()
            self._stats["started"] += 1
            return self._thread

    def stop(self, timeout=2.0):
        """Deterministic shutdown: sentinel + join. Safe if never started."""
        t = self._thread
        if t is None:
            return True
        try:
            self._q.put_nowait(None)
        except queue.Full:
            try:
                self._q.get_nowait(); self._q.task_done()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(None)
            except queue.Full:
                return False
        t.join(timeout)
        return not t.is_alive()

    # -- counters ----------------------------------------------------------
    def _bump(self, key, n=1):
        with self._lock:
            self._stats[key] = self._stats.get(key, 0) + n

    def _set(self, key, value):
        with self._lock:
            self._stats[key] = value

    # -- intents -----------------------------------------------------------
    @staticmethod
    def _direction(fn):
        return "unsub" if "unsub" in getattr(fn, "__name__", "") else "sub"

    def _record_intent(self, fn, tickers):
        d = self._direction(fn)
        with self._intent_lock:
            bucket = self._intents.setdefault(d, {})
            for t in tickers:
                bucket[t] = fn
            total = sum(len(b) for b in self._intents.values())
        self._set("pending_intents", total)
        return total

    def _take_intents(self):
        with self._intent_lock:
            batch = [(d, list(b.items())) for d, b in self._intents.items() if b]
            self._intents = {}
        self._set("pending_intents", 0)
        return batch

    def _reconcile_once(self):
        batch = self._take_intents()
        if not batch:
            return False
        for direction, items in batch:
            fn = items[0][1]
            tickers = [t for t, _ in items]
            try:
                fn(tickers)
            except Exception as exc:
                with self._intent_lock:
                    bucket = self._intents.setdefault(direction, {})
                    for t in tickers:
                        bucket[t] = fn
                    total = sum(len(b) for b in self._intents.values())
                self._set("pending_intents", total)
                self._bump("reconcile_failures")
                self._log("[AngelSub] reconcile failed (%s, %d tickers): %s: %s"
                          % (direction, len(tickers), type(exc).__name__, exc))
            else:
                self._bump("reconciled")
        return True

    # -- work --------------------------------------------------------------
    def _run(self):
        while True:
            try:
                item = self._q.get(timeout=self._idle)
            except queue.Empty:
                self._reconcile_once()
                continue
            if item is None:
                try:
                    self._reconcile_once()
                finally:
                    self._q.task_done()
                return
            try:
                fn, args = item
                try:
                    fn(*args)
                except Exception as exc:
                    self._bump("failed")
                    self._log("[AngelSub] %s failed: %s: %s"
                              % (getattr(fn, "__name__", "op"), type(exc).__name__, exc))
                else:
                    self._bump("done")
            finally:
                self._q.task_done()

    # -- api ---------------------------------------------------------------
    def submit(self, fn, *args):
        """Queue one operation. False when saturated (intent is kept, not lost)."""
        self.start()
        try:
            self._q.put_nowait((fn, tuple(args)))
        except queue.Full:
            tickers = []
            if args and isinstance(args[0], (list, tuple, set)):
                tickers = list(args[0])
            pending = self._record_intent(fn, tickers) if tickers else 0
            self._bump("dropped")
            self._log("[AngelSub] queue full -- deferred %s for %d ticker(s); %d pending intent(s)"
                      % (self._direction(fn), len(tickers), pending))
            return False
        depth = self._q.qsize()
        with self._lock:
            if depth > self._stats["max_pending_seen"]:
                self._stats["max_pending_seen"] = depth
        return True

    def pending(self):
        return self._q.qsize()

    def stats(self):
        with self._lock:
            return dict(self._stats)


# One worker for the process, owned by main.py.
shared = AngelSubWorker()
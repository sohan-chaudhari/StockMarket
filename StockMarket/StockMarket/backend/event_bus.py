import threading
import asyncio
from typing import Callable, Dict, List, Optional, Any

EVENT_PREFIX = "v1"

class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._handlers: Dict[str, List[Callable]] = {}
        self._async_handlers: Dict[str, List[Callable]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def on(self, event: str, handler: Callable):
        full_event = f"{EVENT_PREFIX}.{event}" if not event.startswith(EVENT_PREFIX) else event
        with self._lock:
            if asyncio.iscoroutinefunction(handler):
                self._async_handlers.setdefault(full_event, []).append(handler)
            else:
                self._handlers.setdefault(full_event, []).append(handler)

    def off(self, event: str, handler: Callable):
        full_event = f"{EVENT_PREFIX}.{event}" if not event.startswith(EVENT_PREFIX) else event
        with self._lock:
            sync_handlers = self._handlers.get(full_event, [])
            if handler in sync_handlers:
                sync_handlers.remove(handler)
            async_handlers = self._async_handlers.get(full_event, [])
            if handler in async_handlers:
                async_handlers.remove(handler)

    def emit(self, event: str, **data):
        full_event = f"{EVENT_PREFIX}.{event}" if not event.startswith(EVENT_PREFIX) else event
        handlers = []
        async_handlers = []
        with self._lock:
            handlers = list(self._handlers.get(full_event, []))
            async_handlers = list(self._async_handlers.get(full_event, []))
        for h in handlers:
            try:
                h(**data)
            except Exception as e:
                print(f"[EventBus] Error in handler {h.__name__} for {full_event}: {e}")
        if async_handlers and self._loop is not None and self._loop.is_running():
            for h in async_handlers:
                try:
                    asyncio.run_coroutine_threadsafe(h(**data), self._loop)
                except Exception as e:
                    print(f"[EventBus] Error scheduling async handler {h.__name__} for {full_event}: {e}")

    def subscriber_count(self) -> int:
        with self._lock:
            sync = sum(len(v) for v in self._handlers.values())
            async_ = sum(len(v) for v in self._async_handlers.values())
            return sync + async_

    @property
    def status(self) -> str:
        return "healthy"


event_bus = EventBus()

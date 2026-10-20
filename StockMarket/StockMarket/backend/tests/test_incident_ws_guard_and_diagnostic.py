"""Contract tests for the incident changes in main.py.

These are SOURCE-LEVEL tests on purpose: importing backend.main pulls the whole
app (DB engine, market-data services) and would start background work inside a
test run. Behavioural WS testing needs a dedicated app fixture and is listed as
an outstanding item in the incident report.

Covers:
  * the dashboard handshake tolerates an expected WebSocketDisconnect;
  * the faulthandler diagnostic is bounded (>= 2000 ms, <= 1 dump / 60 s);
  * no blocking sleep/watchdog was added to the event-loop path.
"""
import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _src() -> str:
    return MAIN.read_text(encoding="utf-8", errors="replace")


def test_ws_handshake_guards_expected_disconnect():
    src = _src()
    assert 'await websocket.send_json({"type": "connected", "client_id": client_id})' in src
    idx = src.index('{"type": "connected", "client_id": client_id}')
    window = src[max(0, idx - 400): idx + 400]
    assert "except WebSocketDisconnect:" in window, "expected disconnect is not guarded"
    assert re.search(r"WebSocketDisconnect", src), "WebSocketDisconnect must be referenced"


def test_stack_dump_diagnostic_is_bounded():
    src = _src()
    assert "faulthandler.dump_traceback_later(1, repeat=False)" in src
    assert "lag_ms >= 2000" in src
    assert ">= 60" in src, "dump must be rate limited to one per 60 s"
    assert "_last_stack_dump_ts = 0.0" in src


def test_no_blocking_sleep_added_to_loop_path():
    src = _src()
    # every time.sleep() must live in a worker/executor context, never in an
    # async def. Cheap structural check: no 'await asyncio.sleep' was replaced
    # and no time.sleep appears inside the lag probe function body.
    start = src.index("async def _event_loop_lag_probe")
    end = src.index("async def", start + 10)
    assert "time.sleep(" not in src[start:end], "time.sleep must not appear in the lag probe"

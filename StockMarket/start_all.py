"""LEVERAGE + SCANX unified launcher.

Starts both backends and stays in the foreground until Ctrl+C.

Note on what you'll see: the Stock Market backend does a large warm-up before it
binds port 8000, so there is a long gap with no output of its own. This launcher
prints a heartbeat while it waits so a slow start is distinguishable from a hang.
"""
import os
import subprocess
import sys
import time
import socket

# UTF-8 so model/scraper logs can't kill the process with a charmap error, and
# unbuffered so our own progress lines appear immediately instead of sitting in a
# block buffer when the output is piped to a file or another tool.
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUNBUFFERED"] = "1"

STOCK_BACKEND_DIR = r"C:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\backend"
NEWS_BACKEND_DIR = r"C:\Users\sohan\Desktop\StockMarket\StockMarket\News_Sentiment\backend"

STOCK_PORT = 8000
NEWS_PORT = 8003

project_root = os.path.dirname(os.path.abspath(__file__))
venv_python = os.path.join(project_root, "venv", "Scripts", "python.exe")
if not os.path.exists(venv_python):
    venv_python = os.path.join(project_root, "venv", "bin", "python")
PYTHON_EXE = venv_python if os.path.exists(venv_python) else sys.executable


def log(msg=""):
    # flush=True on every line: without it the launcher's own output is invisible
    # whenever stdout isn't a console, while the child processes (separate handles)
    # still print - which makes a working launcher look like it stalled.
    print(msg, flush=True)


def _listening_pids(port):
    """PIDs with the port actually bound."""
    pids = set()
    try:
        out = subprocess.check_output("netstat -ano", shell=True, stderr=subprocess.DEVNULL).decode(errors="ignore")
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and "LISTENING" in line:
                local = parts[1]
                if local.endswith(f":{port}"):
                    pids.add(parts[-1])
    except Exception:
        pass
    return pids


def _uvicorn_pids_for_port(port):
    """PIDs of our uvicorn processes for this port, listening or not.

    The old launcher only looked at LISTENING sockets. A previous run still inside
    its (multi-minute) startup hasn't bound the port yet, so it survived the
    "free the port" step and then raced the new instance for it - which is how
    several duplicate backends end up alive at once.
    """
    pids = set()
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        f"Where-Object {{ $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match '--port\\s+{port}' }} | "
        "ForEach-Object {{ $_.ProcessId }}"
    ).replace("{{", "{").replace("}}", "}")
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            stderr=subprocess.DEVNULL,
        ).decode(errors="ignore")
        for line in out.split():
            if line.strip().isdigit():
                pids.add(line.strip())
    except Exception:
        pass
    return pids


def free_port(port, timeout=20):
    """Stop anything already using (or about to use) the port, and confirm it's free."""
    targets = _listening_pids(port) | _uvicorn_pids_for_port(port)
    for pid in targets:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            log(f"  freed port {port}: stopped PID {pid}")
        except Exception:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _listening_pids(port):
            return True
        time.sleep(0.5)
    log(f"  WARNING: port {port} still occupied after {timeout}s")
    return False


def port_is_serving(port):
    import urllib.request
    try:
        url = f"http://127.0.0.1:{port}/api/time" if port == 8000 else f"http://127.0.0.1:{port}/docs"
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            return resp.status in (200, 404)
    except Exception:
        return False


def wait_until_serving(proc, port, name, timeout=900):
    """Block until the port answers, printing a heartbeat so a slow start is visible."""
    started = time.time()
    next_beat = 15
    while time.time() - started < timeout:
        if proc.poll() is not None:
            log(f"  {name} exited during startup (code {proc.poll()})")
            return False
        if port_is_serving(port):
            log(f"  {name} is serving on port {port} (took {int(time.time() - started)}s)")
            return True
        waited = int(time.time() - started)
        if waited >= next_beat:
            log(f"  ...still starting {name} ({waited}s elapsed, port {port} not open yet)")
            next_beat += 15
        time.sleep(1)
    log(f"  {name} did not open port {port} within {timeout}s")
    return False


def _spawn(cwd, module, port, extra_env=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    # -u on the child too, so its logs stream instead of arriving in chunks.
    return subprocess.Popen(
        [PYTHON_EXE, "-u", "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)],
        cwd=cwd, env=env,
    )


def start_news_sentiment():
    log(f"Starting News Sentiment backend on port {NEWS_PORT}...")
    free_port(NEWS_PORT)
    return _spawn(NEWS_BACKEND_DIR, "app.main:app", NEWS_PORT)


def start_stock_market():
    log(f"Starting Stock Market backend on port {STOCK_PORT}...")
    free_port(STOCK_PORT)
    return _spawn(STOCK_BACKEND_DIR, "main:app", STOCK_PORT, {"PYTHONPATH": STOCK_BACKEND_DIR})


if __name__ == "__main__":
    log("========================================")
    log("   LEVERAGE & SCANX UNIFIED LAUNCHER")
    log("========================================")
    log(f"Python: {PYTHON_EXE}")
    log()

    processes = []
    try:
        p_news = start_news_sentiment()
        processes.append(("News Sentiment", p_news, NEWS_PORT))

        p_stock = start_stock_market()
        processes.append(("Stock Market", p_stock, STOCK_PORT))

        log()
        log("Both processes launched. Waiting for them to accept connections")
        log("(the Stock Market backend warms a large ticker set first - this can take a few minutes).")
        log()

        ready = {}
        for name, proc, port in processes:
            ready[name] = wait_until_serving(proc, port, name)

        log()
        if all(ready.values()):
            log("Both platforms are running:")
        else:
            log("Launcher finished starting, but not everything came up:")
        log(f"   - Stock Market:   http://127.0.0.1:{STOCK_PORT}   [{'ready' if ready.get('Stock Market') else 'NOT READY'}]")
        log(f"   - News Sentiment: http://127.0.0.1:{NEWS_PORT}   [{'ready' if ready.get('News Sentiment') else 'NOT READY'}]")
        log()
        log("Press Ctrl+C to stop both services.")
        log()

        while True:
            time.sleep(1)
            for name, proc, _port in processes:
                if proc.poll() is not None:
                    log(f"{name} stopped unexpectedly (exit code {proc.poll()})")
                    raise KeyboardInterrupt

    except KeyboardInterrupt:
        log()
        log("Stopping all services...")
        for _name, proc, _port in processes:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            except Exception:
                pass
        log("Services stopped.")

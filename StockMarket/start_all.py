import subprocess
import time
import os
import sys

# Set Python console I/O encoding to UTF-8 to prevent charmap/UnicodeEncodeError crashes on Windows
os.environ["PYTHONIOENCODING"] = "utf-8"

# Paths to the backend directories
STOCK_BACKEND_DIR = r"C:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\backend"
NEWS_BACKEND_DIR = r"C:\Users\sohan\Desktop\StockMarket\StockMarket\News_Sentiment\backend"

# Detect virtual environment python
project_root = os.path.dirname(os.path.abspath(__file__))
venv_python = os.path.join(project_root, "venv", "Scripts", "python.exe")
if not os.path.exists(venv_python):
    venv_python = os.path.join(project_root, "venv", "bin", "python")

PYTHON_EXE = venv_python if os.path.exists(venv_python) else sys.executable
print(f"Using Python executable: {PYTHON_EXE}")

def free_port(port):
    """Kill any process listening on the given port."""
    import socket, struct
    try:
        out = subprocess.check_output(f'netstat -ano | findstr "LISTENING" | findstr ":{port}"', shell=True).decode()
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 5:
                pid = parts[-1]
                try:
                    os.kill(int(pid), 9)
                    print(f"  Killed PID {pid} on port {port}")
                except:
                    pass
    except:
        pass

def start_news_sentiment():
    print("Starting News Sentiment Backend on Port 8003...")
    free_port(8003)
    env = os.environ.copy()
    return subprocess.Popen(
        [PYTHON_EXE, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8003"],
        cwd=NEWS_BACKEND_DIR, env=env
    )

def start_stock_market():
    print("Starting Stock Market Backend on Port 8000...")
    free_port(8000)
    env = os.environ.copy()
    env["PYTHONPATH"] = STOCK_BACKEND_DIR
    return subprocess.Popen(
        [PYTHON_EXE, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=STOCK_BACKEND_DIR, env=env
    )

if __name__ == "__main__":
    print("========================================")
    print("   LEVERAGE & SCANX UNIFIED LAUNCHER   ")
    print("========================================\n")
    
    processes = []
    try:
        # Start News Sentiment first as it might be slower to initialize
        p_news = start_news_sentiment()
        processes.append(p_news)
        
        # Small delay to let first one bind to port
        time.sleep(2)
        
        # Start Stock Market
        p_stock = start_stock_market()
        processes.append(p_stock)
        
        print("\nBoth platforms are running!")
        print("   - Stock Market:   http://127.0.0.1:8000")
        print("   - News Sentiment: http://127.0.0.1:8003")
        print("\nPress Ctrl+C to stop both services.\n")
        
        # Keep the script running
        while True:
            time.sleep(1)
            
            # Check if any process died
            for p in processes:
                if p.poll() is not None:
                    print(f"One of the services stopped unexpectedly (Exit code: {p.poll()})")
                    raise KeyboardInterrupt
                    
    except KeyboardInterrupt:
        print("\nStopping all services...")
        for p in processes:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("Services stopped successfully.")

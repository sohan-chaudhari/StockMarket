import os

log_file = r"c:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\server.log"
if os.path.exists(log_file):
    try:
        # Try reading with utf-16
        with open(log_file, 'r', encoding='utf-16') as f:
            lines = f.readlines()
        print(f"Total lines: {len(lines)}")
        print("Last 100 lines of server.log:")
        for line in lines[-100:]:
            print(line, end='')
    except Exception as e:
        print(f"Error reading UTF-16: {e}")
        try:
            # Fallback to utf-8 with ignore
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            print(f"Total lines (utf-8): {len(lines)}")
            print("Last 100 lines of server.log:")
            for line in lines[-100:]:
                print(line, end='')
        except Exception as e2:
            print(f"Error reading UTF-8 fallback: {e2}")
else:
    print("server.log does not exist.")

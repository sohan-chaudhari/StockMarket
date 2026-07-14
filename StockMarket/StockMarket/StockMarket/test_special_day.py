
from datetime import date, datetime
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

# Mock the database/models to avoid import errors if not needed, 
# but main.py imports them. Let's try importing main directly.
try:
    from backend.main import is_market_open, SPECIAL_TRADING_DAYS, NSE_HOLIDAYS, process_ticker
    from backend import main
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

print(f"Today: {date.today()}")
print(f"Special Trading Days: {SPECIAL_TRADING_DAYS}")
print(f"Is Feb 1 2026 in Special Days? {date(2026, 2, 1) in SPECIAL_TRADING_DAYS}")

now = datetime.now()
print(f"Current Time: {now}")

# Mocking NSE_HOLIDAYS if it's empty
if not NSE_HOLIDAYS:
    print("NSE_HOLIDAYS was empty, simulating reload...")
    # In real app, it loads from DB.
    # We can manually set it to verify logic if needed, but 'is_market_open' handles empty set fine.

is_open = is_market_open()
print(f"is_market_open() returned: {is_open}")

# Check the logic inside process_ticker manually
today = date.today()
market_start = datetime.strptime("09:15", "%H:%M").time()
is_pre_market = now.time() < market_start
is_special = today in SPECIAL_TRADING_DAYS

print(f"Manual Check:")
print(f"  Day Index: {today.weekday()}")
print(f"  Is Special: {is_special}")
print(f"  Is PreMarket: {is_pre_market}")

condition = (today.weekday() >= 5 and not is_special) or (today in NSE_HOLIDAYS and not is_special) or is_pre_market
print(f"  Condition (Should return False to run live): {condition}")

if not condition:
    print(">> Logic says: FETCH LIVE DATA")
else:
    print(">> Logic says: MARKET CLOSED (or Pre-market)")

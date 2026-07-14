import sys
import os

# Set Path
sys.path.append(os.getcwd())

from backend import main, schemas

# Create Mock Request
class MockRequest:
    tickers = ["^NSEI"]

try:
    print("Testing get_live_prices_batch...")
    # Need DB?
    # get_live_prices_batch(request, db)
    # create session
    db = next(main.get_db())
    
    req = main.BatchPriceRequest(tickers=["NIFTY"])
    result = main.get_live_prices_batch(req, db)
    print("Result:", result)

except Exception as e:
    import traceback
    traceback.print_exc()

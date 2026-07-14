import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.append('.')
from aggregator import LiveCandleAggregator, IST, snap_to_nse_session

agg = LiveCandleAggregator()
# Provide dummy batch flush callback to avoid errors
agg.batch_flush_callback = lambda batch: print(f"Flushed {len(batch)} candles")

# Simulate a tick during market hours (today)
today = datetime.now(IST).date()
now_dt = datetime.combine(today, datetime.min.time()).replace(tzinfo=IST).replace(hour=9, minute=16, second=0)
now_epoch = now_dt.timestamp()

try:
    print("Sending first tick...")
    agg.process_tick("RELIANCE", 100.0, 10, tick_ts=now_epoch)
    print("First tick processed.")
    
    # Next minute (rollover)
    now_dt = now_dt.replace(minute=17)
    now_epoch = now_dt.timestamp()
    print("Sending tick for next minute to trigger rollover...")
    agg.process_tick("RELIANCE", 101.0, 5, tick_ts=now_epoch)
    print("Rollover tick processed.")
    
    print("Aggregator state:")
    print(agg.get_current("RELIANCE"))
    print("SUCCESS!")
except Exception as e:
    import traceback
    traceback.print_exc()

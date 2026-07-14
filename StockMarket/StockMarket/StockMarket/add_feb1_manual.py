"""
Manual entry for Feb 1st, 2026 Budget Day special session data
Since Feb 1st was a special Sunday trading session for Budget Day,
we need to manually add this data.

Please fill in the values from TradingView for each stock.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from backend.database import SessionLocal
from backend.models import StockData
from datetime import date

# Feb 1st, 2026 Budget Day Special Session Data
# TODO: Fill in these values from TradingView
FEB1_DATA = {
    'RELIANCE': {
        'open': 0.0,  # TODO: Fill from TradingView
        'high': 0.0,  # TODO: Fill from TradingView
        'low': 0.0,   # TODO: Fill from TradingView
        'close': 0.0, # TODO: Fill from TradingView
        'volume': 0   # TODO: Fill from TradingView
    },
    'NIFTY': {
        'open': 0.0,
        'high': 0.0,
        'low': 0.0,
        'close': 0.0,
        'volume': 0
    },
    # Add more stocks as needed
}

def add_feb1_data():
    """Add Feb 1st special session data to database"""
    db = SessionLocal()
    feb1 = date(2026, 2, 1)
    
    records = []
    for ticker, values in FEB1_DATA.items():
        # Skip if values not filled
        if values['close'] == 0.0:
            print(f"⚠ Skipping {ticker} - values not filled")
            continue
            
        records.append({
            'ticker': ticker,
            'date': feb1,
            'open': values['open'],
            'high': values['high'],
            'low': values['low'],
            'close': values['close'],
            'adj_close': values['close'],
            'volume': values['volume']
        })
    
    if records:
        # Delete any existing Feb 1st data first
        db.query(StockData).filter(StockData.date == feb1).delete()
        db.commit()
        
        # Insert new data
        db.bulk_insert_mappings(StockData, records)
        db.commit()
        
        print(f"✓ Added {len(records)} Feb 1st records")
    else:
        print("✗ No data to add - please fill in the FEB1_DATA dictionary")
    
    db.close()

if __name__ == "__main__":
    print("=" * 60)
    print("Feb 1st, 2026 Budget Day Special Session Data Entry")
    print("=" * 60)
    print("\nPlease edit this file and fill in the FEB1_DATA dictionary")
    print("with values from TradingView, then run again.")
    print("\nExample:")
    print("  'RELIANCE': {")
    print("    'open': 1395.50,")
    print("    'high': 1410.00,")
    print("    'low': 1390.00,")
    print("    'close': 1405.25,")
    print("    'volume': 5000000")
    print("  }")
    print("\n" + "=" * 60)
    
    add_feb1_data()


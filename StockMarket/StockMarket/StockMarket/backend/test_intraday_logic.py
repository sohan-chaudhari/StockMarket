import pandas as pd
from datetime import datetime, timedelta
from main import IST
import schemas
from indicator_service import indicator_service

def test_logic():
    print("Testing Intraday Logic...")
    
    # 1. Create Mock Data (15m candles)
    now = datetime.now(IST).replace(tzinfo=None)
    data = []
    for i in range(100):
        ts = now - timedelta(minutes=15 * (100 - i))
        data.append({
            "timestamp": ts,
            "open": 100.0 + i,
            "high": 105.0 + i,
            "low": 95.0 + i,
            "close": 101.0 + i,
            "volume": 1000
        })
    
    print(f"Created {len(data)} mock candles.")

    # 2. Test Resampling (30m)
    interval = "30m"
    try:
        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        
        offset_map = {"30m": "30min", "1h": "1h"}
        rule = offset_map.get(interval)
        
        if rule:
            resampled = df.resample(rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            
            resampled_data = []
            for ts, row in resampled.iterrows():
                resampled_data.append({
                    "timestamp": ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts,
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close']),
                    "volume": int(row['volume'])
                })
            
            print(f"Resampled to {len(resampled_data)} candles (30m).")
            data = resampled_data
    except Exception as e:
        print(f"Resampling Error: {e}")

    # 3. Apply Indicators
    try:
        if len(data) >= 20:
            data = indicator_service.apply_all(data)
            print(f"Applied indicators. Result count: {len(data)}")
            print(f"Sample Indicator (sma_20): {data[-1].get('sma_20')}")
    except Exception as e:
        print(f"Indicator Error: {e}")

    # 4. Final Validation
    try:
        results = [schemas.IntradayCandleResponse(**d) for d in data]
        print(f"Successfully serialized {len(results)} candles.")
        print(f"Last candle timestamp: {results[-1].timestamp}")
    except Exception as e:
        print(f"Serialization Error: {e}")
        for i, d in enumerate(data):
            if d.get("timestamp") is None:
                print(f"Row {i} has None timestamp: {d}")

if __name__ == "__main__":
    test_logic()

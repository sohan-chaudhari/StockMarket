from backend.database import engine
import pandas as pd
df = pd.read_sql("SELECT MAX(timestamp), MIN(timestamp), COUNT(*) FROM intraday_candles_5min WHERE ticker='RELIANCE'", engine)
print(df)

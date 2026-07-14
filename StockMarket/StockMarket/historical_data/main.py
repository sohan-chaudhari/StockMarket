import yfinance as yf
import pandas as pd
from datetime import datetime

# =============================
# CONFIG
# =============================
TICKER = "RELIANCE.NS"                # Change ticker here
START_DATE = "2015-01-01"
END_DATE = datetime.today().strftime("%Y-%m-%d")
OUTPUT_FILE = f"{TICKER}_daily.csv"

# =============================
# DOWNLOAD DATA
# =============================
df = yf.download(
    TICKER,
    start=START_DATE,
    end=END_DATE,
    interval="1d",
    group_by="column",        # FIXES duplicate ticker header
    auto_adjust=False,
    progress=False
)

# =============================
# FIX MULTIINDEX COLUMN ISSUE
# =============================
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

# =============================
# CLEAN & STANDARDIZE
# =============================
df.reset_index(inplace=True)

df.rename(columns={
    "Date": "date",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume"
}, inplace=True)

# Ensure clean date format (YYYY-MM-DD)
df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

# Sort chronologically
df.sort_values("date", inplace=True)

# Drop any malformed rows
df = df[df["date"] != TICKER]

# Drop missing values
df.dropna(inplace=True)

# Round prices
price_cols = ["open", "high", "low", "close", "adj_close"]
df[price_cols] = df[price_cols].round(2)

# Volume as integer
df["volume"] = df["volume"].astype(int)

# =============================
# AUTO-APPEND LOGIC (SAFE UPDATE)
# =============================
try:
    old = pd.read_csv(OUTPUT_FILE)

    # Safety: remove corrupted ticker row if present
    old = old[old["date"] != TICKER]

    df = pd.concat([old, df], ignore_index=True)
    df.drop_duplicates(subset=["date"], inplace=True)
    df.sort_values("date", inplace=True)

except FileNotFoundError:
    # First run → no old file
    pass

# Reset final index
df.reset_index(drop=True, inplace=True)

# =============================
# SAVE CSV
# =============================
df.to_csv(OUTPUT_FILE, index=False)

print("✅ Data download & update complete")
print(f"📁 File saved as: {OUTPUT_FILE}")
print(f"📊 Total rows: {len(df)}")
print(df.head())
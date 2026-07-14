"""
Test different yfinance methods to get Feb 2 index data
"""
import yfinance as yf
from datetime import date, timedelta

target_date = date(2026, 2, 2)

print("Testing different yfinance methods for NIFTY (^NSEI)")
print("=" * 60)

ticker = yf.Ticker('^NSEI')

# Method 1: history with date range
print("\n1. history(start, end):")
start = date(2026, 1, 27)
end = date(2026, 2, 4)
hist1 = ticker.history(start=start, end=end)
print(f"   Dates: {[d.date() for d in hist1.index]}")

# Method 2: history with period
print("\n2. history(period='5d'):")
hist2 = ticker.history(period='5d')
print(f"   Dates: {[d.date() for d in hist2.index]}")

# Method 3: history with period and interval
print("\n3. history(period='1mo', interval='1d'):")
hist3 = ticker.history(period='1mo', interval='1d')
print(f"   Dates: {[d.date() for d in hist3.index]}")
print(f"   Last 10 dates: {[d.date() for d in hist3.index[-10:]]}")

# Check if Feb 2 exists in any
feb2_in_1 = any(d.date() == target_date for d in hist1.index)
feb2_in_2 = any(d.date() == target_date for d in hist2.index)
feb2_in_3 = any(d.date() == target_date for d in hist3.index)

print("\n" + "=" * 60)
print(f"Feb 2 in method 1: {feb2_in_1}")
print(f"Feb 2 in method 2: {feb2_in_2}")
print(f"Feb 2 in method 3: {feb2_in_3}")

if feb2_in_3:
    print("\nFeb 2 data found in method 3!")
    feb2_data = hist3[hist3.index.date == target_date].iloc[0]
    print(f"O: {feb2_data['Open']:.2f}, H: {feb2_data['High']:.2f}, L: {feb2_data['Low']:.2f}, C: {feb2_data['Close']:.2f}")

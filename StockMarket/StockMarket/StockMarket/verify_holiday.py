
from datetime import date
import sys

NSE_HOLIDAYS = {
    date(2024, 12, 25), # Christmas
}

def check_date(test_date):
    print(f"\nChecking {test_date} ({test_date.strftime('%A')})")
    is_weekend = test_date.weekday() >= 5
    is_holiday = test_date in NSE_HOLIDAYS
    
    if is_weekend or is_holiday:
        print("RESULT: Market Closed (Show Last Available Data)")
        if is_holiday: print("(Reason: Holiday)")
        if is_weekend: print("(Reason: Weekend)")
    else:
        print("RESULT: Market Open (Fetch Live)")

# Test Cases
check_date(date(2024, 12, 21)) # Saturday
check_date(date(2024, 12, 25)) # Wednesday (Christmas)
check_date(date(2024, 12, 23)) # Monday (Open)

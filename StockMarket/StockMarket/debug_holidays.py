from backend.main import refresh_holidays, NSE_HOLIDAYS
from datetime import date
import sys

def debug():
    print("Initial Holidays:", len(NSE_HOLIDAYS))
    refresh_holidays()
    
    # Access the updated global (Note: import might have bound the old one if it was simple import, 
    # but 'from backend.main import NSE_HOLIDAYS' imports the OBJECT. 
    # If refresh_holidays does 'NSE_HOLIDAYS = ...', it rebinds the name in main.py module.
    # So I need to access it via module to see change.)
    
    import backend.main
    current_holidays = backend.main.NSE_HOLIDAYS
    print("Refreshed Holidays:", len(current_holidays))
    
    today = date(2025, 12, 25)
    if today in current_holidays:
        print(f"SUCCESS: {today} is in NSE_HOLIDAYS.")
    else:
        print(f"FAILURE: {today} NOT found in NSE_HOLIDAYS.")
        print("First 5:", list(current_holidays)[:5])

if __name__ == "__main__":
    debug()

import yfinance as yf
import requests

def verify_indices():
    # Candidates
    indices = [
        {"name": "Bank Nifty", "yahoo": "^NSEBANK", "google": "https://www.google.com/finance/quote/NIFTY_BANK:INDEXNSE"},
        {"name": "Fin Nifty", "yahoo": "NIFTY_FIN_SERVICE.NS", "google": "https://www.google.com/finance/quote/NIFTY_FIN_SERVICE:INDEXNSE"}, # Yahoo might be wrong
        {"name": "Midcap 50", "yahoo": "^NSEMDCP50", "google": "https://www.google.com/finance/quote/NIFTY_MIDCAP_50:INDEXNSE"}
    ]
    
    print("--- Verifying Yahoo Finance ---")
    for idx in indices:
        try:
            t = yf.Ticker(idx["yahoo"])
            hist = t.history(period="1d")
            found = not hist.empty
            print(f"{idx['name']} ({idx['yahoo']}): {'FOUND' if found else 'NOT FOUND'}")
            if not found:
                 # Try alternative for FinNifty
                 if idx["name"] == "Fin Nifty":
                     print("Trying alternative ^CNXFIN...")
                     t2 = yf.Ticker("^CNXFIN") # Possible alternative
                     if not t2.history(period="1d").empty:
                         print("Found as ^CNXFIN")
        except Exception as e:
            print(f"Error checking {idx['yahoo']}: {e}")

    print("\n--- Verifying Google Finance ---")
    for idx in indices:
        try:
            resp = requests.get(idx["google"])
            print(f"{idx['name']} ({idx['google']}): {resp.status_code}")
        except Exception as e:
            print(f"Error checking Google: {e}")

if __name__ == "__main__":
    verify_indices()

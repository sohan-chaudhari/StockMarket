import yfinance as yf
import math

def resolve_yf_ticker(ticker: str) -> str:
    raw = ticker.strip().upper()
    if "." not in raw and "^" not in raw:
        return f"{raw}.NS"
    return raw

def test_fetch(ticker):
    fetch_ticker = resolve_yf_ticker(ticker)
    dat = yf.Ticker(fetch_ticker)
    info = dat.fast_info
    price = info.last_price
    vol = getattr(info, 'last_volume', 0)
    print(f"Ticker: {ticker} -> {fetch_ticker}")
    print(f"Price: {price}")
    print(f"Volume: {vol}")

test_fetch("RELIANCE.NS")

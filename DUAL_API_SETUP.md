# Dual Angel One API Setup Guide

## 🔑 Two Separate API Accounts

Your StockMarket system now uses **two distinct Angel One API credentials** for better performance and request management:

### 1️⃣ **Live Price API** (Real-time Trading)
**Used for:** WebSocket streaming, live price updates, current_day_candle  
**Variables:** `ANGELONE_API_KEY`, `ANGELONE_CLIENT_ID`, `ANGELONE_PASSWORD`, `ANGELONE_TOTP_TOKEN`  
**Service:** `angelone_service.py`

### 2️⃣ **Historical Data API** (Batch Fetching)
**Used for:** Backfilling charts, historical analysis, bulk data loading  
**Variables:** `HISTORICAL_API_KEY`, `HISTORICAL_CLIENT_ID`, `HISTORICAL_PASSWORD`, `HISTORICAL_TOTP_TOKEN`  
**Service:** `historical_service.py`

---

## 📝 Setup Instructions

### Step 1: Get Your Historical API Credentials

1. Go to: https://smartapi.angelbroking.com/
2. Login with your **historical data account**
3. Navigate to "My APIs" or "API Keys"
4. Copy the following:
   - API Key
   - Client ID
   - Password
   - TOTP Token (from "2FA Setup")

### Step 2: Update `.env` File

Open `backend/.env` and replace the placeholder values:

```env
# Angel One API Configuration - HISTORICAL DATA
HISTORICAL_API_KEY=your_actual_historical_key_here
HISTORICAL_CLIENT_ID=your_actual_client_id_here
HISTORICAL_PASSWORD=your_actual_password_here
HISTORICAL_TOTP_TOKEN=your_actual_totp_token_here
```

**Example:**
```env
HISTORICAL_API_KEY=XyZ123abc
HISTORICAL_CLIENT_ID=HIST001
HISTORICAL_PASSWORD=MySecurePass123
HISTORICAL_TOTP_TOKEN=ABCDEF123456GHIJKLMN
```

---

## ✅ Test Your Setup

### Test 1: Verify Historical Service

```bash
cd backend
python historical_service.py
```

**Expected output:**
```
[HISTORICAL] 📊 Historical Data Service initialized
[HISTORICAL] 🔐 Logging in to Angel One (Historical API)...
[HISTORICAL] ✅ Login successful!

✅ Fetched 21 candles
First 3 candles:
  2024-01-01 | O:2850.50 H:2865.00 L:2840.25 C:2860.75 V:1,234,567
  2024-01-02 | O:2862.00 H:2875.30 L:2855.10 C:2870.15 V:1,345,678
  2024-01-03 | O:2871.20 H:2880.00 L:2865.50 C:2875.90 V:1,456,789
```

### Test 2: Run Sample Backfill

```bash
python backfill_phase1_daily.py --ticker RELIANCE.NS
```

**Expected output:**
```
🔐 Logging in to Historical Data API...
[HISTORICAL] ✅ Login successful!

🔄 RELIANCE.NS         ✅  875 candles (2015-2019)

✅ Success: 1
📊 Total candles: 875
```

---

## 🔄 How It Works

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│  StockMarket Backend                                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────────┐         ┌───────────────────┐    │
│  │ angelone_service │         │ historical_service│    │
│  │   (LTP API)      │         │  (Historical API) │    │
│  └────────┬─────────┘         └─────────┬─────────┘    │
│           │                              │              │
│           │ Live prices                  │ Historical   │
│           │ WebSocket                    │ candles      │
│           │                              │              │
│           ▼                              ▼              │
│  ┌─────────────────┐         ┌───────────────────┐     │
│  │  main.py        │         │ backfill_*.py     │     │
│  │  dashboard      │         │ migration scripts │     │
│  └─────────────────┘         └───────────────────┘     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Request Flow Separation

**Live Price Request:**
```python
from angelone_service import angelone_service

# Uses ANGELONE_* credentials
price = angelone_service.get_live_price('RELIANCE')
```

**Historical Data Request:**
```python
from historical_service import historical_service

# Uses HISTORICAL_* credentials
candles = historical_service.get_historical_candles(
    ticker='RELIANCE',
    interval='ONE_DAY',
    from_date=date(2015, 1, 1),
    to_date=date(2019, 12, 31)
)
```

---

## 🎯 Benefits

| Aspect | Benefit |
|--------|---------|
| **Rate Limits** | Live & historical requests don't interfere |
| **Performance** | Batch operations won't slow down real-time updates |
| **Monitoring** | Track usage separately for debugging |
| **Fault Tolerance** | If historical fails, live prices still work |
| **Scalability** | Can upgrade each API tier independently |

---

## 🔧 Fallback Behavior

If you **don't** set historical credentials, the system automatically falls back to using your main API credentials:

```python
# historical_service.py automatically does this:
if not HISTORICAL_API_KEY or HISTORICAL_API_KEY == "placeholder":
    print("⚠️  Falling back to main API credentials")
    USE_ANGELONE_API_KEY()
```

This means you can:
- ✅ Start immediately with one set of credentials
- ✅ Add dedicated historical credentials later
- ✅ No breaking changes if credentials missing

---

## 📊 Monitoring

### Check Which Service is Being Used

```bash
# Look for log prefixes
[AngelOne]    # Main service (LTP)
[HISTORICAL]  # Historical service
```

### Example Logs

```
[AngelOne] Login successful! (for live prices)
[HISTORICAL] Login successful! (for backfill)
[HISTORICAL] ✅ Fetched 875 candles
```

---

## 🚨 Troubleshooting

### Issue: "Historical API login failed"

**Check:**
1. Verify credentials in `.env` are not placeholders
2. Ensure TOTP token is valid (test on Angel One website)
3. Check if historical account has API access enabled

**Quick Fix:**
```bash
# Test credentials manually
python -c "from historical_service import historical_service; print(historical_service.login())"
```

### Issue: "Falling back to main API"

This is **normal** if you haven't set up historical credentials yet. The system will work, just using one API account for everything.

---

## 🎉 Ready to Use!

Once configured, run:

```bash
# Test with 3 stocks (10 seconds)
python backfill_phase1_daily.py --tickers RELIANCE.NS,TCS.NS,INFY.NS

# Full backfill all stocks (12 minutes)
python backfill_phase1_daily.py --all --workers 10
```

Your historical data will be fetched using the **dedicated historical API**, keeping your live price service running smoothly! 🚀

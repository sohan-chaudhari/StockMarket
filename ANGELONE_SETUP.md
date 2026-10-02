# Angel One API Integration - Setup Instructions

## Prerequisites

Before running the server with Angel One integration, you need:

1. **Angel One Trading Account** with API access enabled
2. **API Credentials** from Angel One SmartAPI portal

---

## Step 1: Get Angel One API Credentials

### 1.1 Login to Angel One SmartAPI Portal
Visit: https://smartapi.angelbroking.com/

### 1.2 Get Your Credentials
You'll need these 4 values:
- **API KEY**: Your application API key
- **CLIENT ID**: Your Angel One client ID (usually `A` followed by numbers)
- **PASSWORD**: Your Angel One trading account password
- **TOTP TOKEN**: Secret key for two-factor authentication

### 1.3 Generate TOTP Token (If you don't have one)
1. Go to Angel One SmartAPI dashboard
2. Navigate to "Apps" → "Create App"
3. Note down the **TOTP Secret** provided

---

## Step 2: Configure Environment Variables

### 2.1 Create `.env` File
Copy the `.env.example` to create your `.env` file:

```bash
cd backend
cp .env.example .env
```

### 2.2 Add Your Credentials
Open `.env` and replace the placeholders:

```bash
# Angel One API Configuration
ANGELONE_API_KEY=your_actual_api_key_here
ANGELONE_CLIENT_ID=your_actual_client_id_here  
ANGELONE_PASSWORD=your_actual_password_here
ANGELONE_TOTP_TOKEN=your_actual_totp_secret_here
```

**Example**:
```bash
ANGELONE_API_KEY=AbcDefGh
ANGELONE_CLIENT_ID=A12345678
ANGELONE_PASSWORD=MySecurePassword123
ANGELONE_TOTP_TOKEN=JBSWY3DPEHPK3PXPEXAMPLETOTP123
```

---

## Step 3: Install Dependencies

Install the new Angel One dependencies:

```bash
cd backend
pip install -r requirements.txt
```

This will install:
- `smartapi-python==1.3.5` - Angel One SDK
- `pyotp==2.9.0` - TOTP code generator  
- `pandas==2.2.3` - Data manipulation (if not already installed)

---

## Step 4: Test Angel One Connection

Before starting the main server, test the Angel One integration:

```bash
cd backend
python test_angelone_service.py
```

**Expected Output**:
```
============================================================
ANGEL ONE SERVICE TEST
============================================================

[Test 1] Login
------------------------------------------------------------
🔐 [AngelOne] Logging in to Angel One...
✅ [AngelOne] Login successful! Session: eyJhbGciOiJIUzI1Ni...
✅ Login successful

[Test 2] Load Instruments
------------------------------------------------------------
📥 [AngelOne] Downloading instrument list...
✅ [AngelOne] Downloaded 8347 instruments
   → NSE stocks: 2156
✅ Loaded 8347 instruments

[Test 3] Token Lookup
------------------------------------------------------------
✅ RELIANCE     → Token: 2885    | Symbol: RELIANCE-EQ
✅ TCS          → Token: 11536   | Symbol: TCS-EQ
✅ HDFCBANK     → Token: 1333    | Symbol: HDFCBANK-EQ
✅ INFY         → Token: 1594    | Symbol: INFY-EQ
✅ NIFTY        → Token: 99926000| Symbol: NIFTY 50

[Test 4] Live Price Fetching
------------------------------------------------------------
Fetching RELIANCE...
✅ RELIANCE:
   Current Price: ₹2845.50
   Open:  ₹2850.00
   High:  ₹2860.75
   Low:   ₹2830.20
   Volume: 5,234,876
...
```

---

## Step 5: Start the Backend Server

Start the FastAPI server:

```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Startup Logs**:
```
[Startup] Initializing Angel One API...
🔐 [AngelOne] Logging in to Angel One...
✅ [AngelOne] Login successful! Session: eyJhbGci...
📥 [AngelOne] Downloading instrument list...
✅ [AngelOne] Downloaded 8347 instruments
✅ [Startup] Angel One API initialized successfully
[Holidays] Loaded 15 holidays from DB.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

---

## Step 6: Verify Integration

### 6.1 Test Live Price API
Open browser or use curl:

```bash
curl http://localhost:8000/api/live/RELIANCE
```

**Expected Response**:
```json
{
  "current_price": 2845.50,
  "open": 2850.00,
  "high": 2860.75,
  "low": 2830.20,
  "volume": 5234876,
  "previous_close": 2848.30
}
```

### 6.2 Check Backend Logs
You should see:
```
✅ [LiveData] Fetched RELIANCE from Angel One: ₹2845.50
```

If Angel One fails, you'll see fallback:
```
⚠️ [LiveData] Angel One failed for RELIANCE: ..., falling back to Yahoo Finance
[LiveData] Using Yahoo Finance for RELIANCE
```

---

## Troubleshooting

### Issue 1: Login Failed
**Error**: `❌ [AngelOne] Login failed: Invalid credentials`

**Solution**:
- Verify your `ANGELONE_CLIENT_ID` and `ANGELONE_PASSWORD` in `.env`
- Make sure there are no extra spaces
- Check if your account has API access enabled

### Issue 2: TOTP Error
**Error**: `❌ [AngelOne] Login failed: Invalid OTP`

**Solution**:
- Verify your `ANGELONE_TOTP_TOKEN` is the SECRET KEY (not the 6-digit code)
- The TOTP token should be a long alphanumeric string (e.g., `JBSWY3DPEHPK3PXP`)

### Issue 3: Stock Not Found
**Error**: `⚠️ [AngelOne] Stock 'XYZ' not found on NSE`

**Solution**:
- Check if the ticker is listed on NSE
- For equities, Angel One uses format like `RELIANCE-EQ`, but our code handles this automatically
- Indices work: NIFTY, BANKNIFTY, SENSEX

### Issue 4: Rate Limiting
**Error**: Too many requests

**Solution**:
- Angel One has rate limits (typically 10 requests/second)
- Our implementation already includes delays
- If you're testing, add `time.sleep(0.5)` between requests

---

## Fallback Behavior

**Angel One is Primary, Yahoo Finance is Fallback**:

1. **During Market Hours** (9:15 AM - 3:30 PM IST Monday-Friday):
   - Uses Angel One for live prices
   - Falls back to Yahoo Finance if Angel One fails

2. **Outside Market Hours**:
   - Uses Angel One if logged in
   - Falls back to Yahoo Finance for indices and last prices

3. **If Angel One Not Configured**:
   - Server starts normally
   - Uses Yahoo Finance for all requests
   - Logs warning: `⚠️ [Startup] Angel One initialization failed. Will use Yahoo Finance fallback.`

---

## Security Notes

⚠️ **IMPORTANT**:
- Never commit your `.env` file to git (it's in `.gitignore`)
- Keep your Angel One credentials secure
- Use environment variables in production
- Consider using AWS Secrets Manager or similar for production deployments

---

## Next Steps

Once the integration is working:
1. Test with multiple stocks from the dashboard
2. Verify WebSocket live updates
3. Check portfolio page for real-time P&L
4. Monitor backend logs for any errors

For production deployment, consider:
- Using a production-grade `.env` management system
- Setting up monitoring for Angel One API health
- Implementing retry logic with exponential backoff
- Adding circuit breaker pattern for API failures

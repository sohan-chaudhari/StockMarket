# Running the Frontend

## Quick Start

Your frontend is a **static HTML/CSS/JavaScript application** that runs directly in the browser.

### Method 1: Using VS Code Live Server (Easiest)

1. **Install Live Server Extension** in VS Code:
   - Open VS Code
   - Go to Extensions (Ctrl+Shift+X)
   - Search for "Live Server"
   - Install "Live Server" by Ritwick Dey

2. **Start Live Server**:
   - Open `frontend/index.html` in VS Code
   - Right-click in the editor
   - Select **"Open with Live Server"**
   - Your browser will open at: `http://127.0.0.1:5500/frontend/index.html`

### Method 2: Using Python HTTP Server

Open a **new terminal** (keep backend running) and run:

```bash
cd C:\Users\rahul\Desktop\StockMarket\StockMarket\frontend
python -m http.server 3000
```

Then open: http://localhost:3000

### Method 3: Using Node.js HTTP Server

If you have npm installed:

```bash
cd frontend
npx http-server -p 3000
```

Then open: http://localhost:3000

### Method 4: Open Directly in Browser

Simply double-click `frontend/index.html`

**Note**: This may have CORS issues when calling the backend API. Use one of the server methods above for full functionality.

---

## 🎯 Accessing Different Pages

- **Landing Page**: `index.html`
- **Login**: `login.html`
- **Register**: `register.html`  
- **Dashboard**: `home.html` (requires login)
- **Chart**: `chart.html`

---

## ✅ Verify Backend Connection

Make sure your backend is running at: http://localhost:8000

The frontend will connect to the backend API for:
- Live stock prices (via Angel One)
- Historical data
- User authentication
- Trading features

---

## 🔧 Configuration

The frontend is configured to connect to the backend at `http://localhost:8000`

If you're running the backend on a different port, update the API endpoint in the JavaScript files:
- `frontend/index.js`
- `frontend/dashboard.js`
- `frontend/stocks.js`

Look for lines like:
```javascript
const API_BASE_URL = "http://localhost:8000";
```

---

## 📱 Features Available

- ✅ Real-time stock charts with TradingView integration
- ✅ Live price updates via Angel One
- ✅ User authentication (register/login)
- ✅ Portfolio management
- ✅ Trading with TP/SL support
- ✅ Stock search and watchlist

---

## 🐛 Troubleshooting

### "Cannot Connect to Backend"
- Ensure backend is running: http://localhost:8000/docs
- Check browser console for CORS errors
- Verify API_BASE_URL in JavaScript files

### "CORS Error"
- Don't open HTML files directly (Method 4)
- Use Live Server or HTTP server (Methods 1-3)

### Pages Not Loading Files
- Make sure you're accessing via server (Methods 1-3)
- Check browser console for 404 errors on CSS/JS files

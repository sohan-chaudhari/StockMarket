# News Sentiment Analysis - HTML/CSS/JS Frontend

A pure vanilla JavaScript frontend for the News Sentiment Analysis platform with a premium crystal black theme.

## Features

✨ **Crystal Black Design**
- Pure black background (#000000)
- Glowy white borders on cards
- Premium, modern aesthetic

📰 **Horizontal News Cards**
- Stock logo on the left
- News content on the right
- Full-width rectangle layout
- Smooth hover animations

🎨 **Interactive UI**
- Real-time sentiment analysis display
- Time window selection (5m, 30m, 1h, 24h, 7d)
- Stock search with autocomplete
- Responsive design for all devices

## Tech Stack

- **HTML5** - Semantic structure
- **CSS3** - Custom styling with animations
- **Vanilla JavaScript** - No frameworks required
- **Google Fonts** - Inter & Roboto Mono

## Quick Start

### 1. Ensure Backend is Running

The frontend connects to the backend API at `http://localhost:8000`. Make sure your backend is running:

```bash
# From the News_Sentiment root directory
docker-compose up -d
```

### 2. Open the Frontend

Simply open `index.html` in your browser:

```bash
# Option 1: Direct file
# Open file:///path/to/News_Sentiment/frontend(HS)/index.html in your browser

# Option 2: Using Python's HTTP server (recommended to avoid CORS issues)
cd frontend(HS)
python -m http.server 8080
# Then visit: http://localhost:8080
```

### 3. Select a Stock

- Use the search bar to find Indian stocks (RELIANCE, TCS, INFY, etc.)
- Click on a suggestion to load news
- Adjust the time window as needed

## File Structure

```
frontend(HS)/
├── index.html          # Main HTML structure
├── styles.css          # Crystal black theme styling
├── app.js              # JavaScript logic & API integration
└── README.md           # This file
```

## Design System

### Colors

| Color | Hex | Usage |
|-------|-----|-------|
| Pure Black | `#000000` | Background |
| Crystal Black | `#0a0a0a` | Cards |
| White | `#ffffff` | Primary text |
| Accent Blue | `#1E88E5` | Links, buttons |
| Market Green | `#00C853` | Positive sentiment |
| Market Red | `#D50000` | Negative sentiment |

### Typography

- **Sans-serif**: Inter (headings, body)
- **Monospace**: Roboto Mono (scores, metrics)

## API Integration

The app connects to the backend API:

```javascript
GET /api/v1/news/{ticker}?time_window={window}&limit={limit}
```

**Parameters:**
- `ticker`: Stock symbol (e.g., RELIANCE, TCS)
- `time_window`: 5m, 30m, 1h, 24h, 7d
- `limit`: Number of articles (default: 20)

## Browser Compatibility

- ✅ Chrome/Edge (latest)
- ✅ Firefox (latest)
- ✅ Safari (latest)
- ⚠️ IE11 (not supported)

## Customization

### Changing the API URL

Edit `app.js`:

```javascript
const API_BASE_URL = 'http://your-api-url/api/v1';
```

### Adding Stock Logos

To add actual stock logos instead of initials:

1. Add logo images to an `assets/logos/` folder
2. Update the `createNewsCard()` function in `app.js`:

```javascript
<div class="stock-logo">
    <img src="assets/logos/${article.ticker}.png" 
         alt="${article.ticker}" 
         class="stock-logo-img">
</div>
```

### Modifying Colors

Edit CSS variables in `styles.css`:

```css
:root {
    --pure-black: #000000;
    --crystal-black: #0a0a0a;
    --accent-blue: #1E88E5;
    /* ... etc */
}
```

## Features Implemented

- ✅ Crystal black background with glowy borders
- ✅ Horizontal news card layout
- ✅ Stock logo display (initials)
- ✅ Sentiment badge with confidence
- ✅ Recency score display
- ✅ Time window selector
- ✅ Stock search with autocomplete
- ✅ Responsive mobile design
- ✅ Loading skeletons
- ✅ Empty state handling
- ✅ Smooth animations & transitions

## Troubleshooting

### CORS Errors

If you see CORS errors, run the frontend using a local server:

```bash
# Python 3
python -m http.server 8080

# Python 2
python -m SimpleHTTPServer 8080

# Node.js (if you have http-server installed)
npx http-server -p 8080
```

### API Connection Failed

1. Verify backend is running: `docker ps`
2. Check API URL in `app.js`
3. Test API directly: `http://localhost:8000/api/v1/news/RELIANCE`

### No News Displayed

- The backend needs to have news data scraped
- Try different stocks (RELIANCE, TCS, INFY)
- Check console for errors (F12 → Console)

## License

Part of the News Sentiment Analysis Platform

/* ==========================================
   INDIAN STOCK MARKET WEBSITE - JAVASCRIPT
   Live Search & Price Updates
   ========================================== */

// Dropdown open/close + outside-click-close is provided by nav.js (window.toggleUserDropdown).

// ==========================================
// STOCK DATA - Indian Stocks & Indices
// ==========================================
// ==========================================
// STOCK DATA - Indian Stocks & Indices
// ==========================================

// Helper to check for image logo
function isImageLogo(logo) {
  return logo && (logo.startsWith('http') || logo.startsWith('logos/'));
}

// Stocks to display in the main grid (Curated list with base prices)
const FEATURED_STOCKS = [
  // Major Indices (Using local SVG logos)
  { ticker: 'NIFTY', name: 'NIFTY 50', logo: 'logos/NIFTY.svg', basePrice: 21500 },
  { ticker: 'BANKNIFTY', name: 'BANK NIFTY', logo: 'logos/BANKNIFTY.svg', basePrice: 45500 },
  { ticker: 'SENSEX', name: 'SENSEX', logo: 'logos/SENSEX.svg', basePrice: 71000 },

  // Major Stocks & Nifty 50 — logos synced from ALL_STOCKS at runtime
  { ticker: 'RELIANCE', name: 'Reliance Industries', basePrice: 2450 },
  { ticker: 'TCS', name: 'Tata Consultancy Services', basePrice: 3650 },
  { ticker: 'HDFCBANK', name: 'HDFC Bank', basePrice: 973.30 },
  { ticker: 'INFY', name: 'Infosys Limited', basePrice: 1450 },
  { ticker: 'ICICIBANK', name: 'ICICI Bank', basePrice: 1050 },
  { ticker: 'BHARTIARTL', name: 'Bharti Airtel', basePrice: 1350 },
  { ticker: 'ITC', name: 'ITC Limited', basePrice: 420 },
  { ticker: 'KOTAKBANK', name: 'Kotak Mahindra Bank', basePrice: 1750 },
  { ticker: 'LT', name: 'Larsen & Toubro', basePrice: 3200 },
  { ticker: 'AXISBANK', name: 'Axis Bank', basePrice: 1080 },
  { ticker: 'SBIN', name: 'State Bank of India', basePrice: 580 },
  { ticker: 'HINDUNILVR', name: 'Hindustan Unilever', basePrice: 2400 },
  { ticker: 'BAJFINANCE', name: 'Bajaj Finance', basePrice: 6500 },
  { ticker: 'ASIANPAINT', name: 'Asian Paints', basePrice: 2900 },
  { ticker: 'MARUTI', name: 'Maruti Suzuki', basePrice: 12500 },
  { ticker: 'TITAN', name: 'Titan Company', basePrice: 3400 },
  { ticker: 'SUNPHARMA', name: 'Sun Pharmaceutical', basePrice: 1650 },
  { ticker: 'ULTRACEMCO', name: 'UltraTech Cement', basePrice: 9200 },
  { ticker: 'TATAMOTORS', name: 'Tata Motors', basePrice: 850 },
  { ticker: 'TATASTEEL', name: 'Tata Steel', basePrice: 140 },
  { ticker: 'WIPRO', name: 'Wipro Limited', basePrice: 450 },
  { ticker: 'NESTLEIND', name: 'Nestle India', basePrice: 24000 },
  { ticker: 'ADANIENT', name: 'Adani Enterprises', basePrice: 2800 },
  { ticker: 'ADANIPORTS', name: 'Adani Ports', basePrice: 1200 },
  { ticker: 'M&M', name: 'Mahindra & Mahindra', basePrice: 1600 },
  { ticker: 'ONGC', name: 'ONGC', basePrice: 270 },
  { ticker: 'NTPC', name: 'NTPC Limited', basePrice: 310 },
  { ticker: 'POWERGRID', name: 'Power Grid Corp', basePrice: 240 },
  { ticker: 'JSWSTEEL', name: 'JSW Steel', basePrice: 820 },
  { ticker: 'TECHM', name: 'Tech Mahindra', basePrice: 1250 },
  { ticker: 'LTIM', name: 'LTIMindtree', basePrice: 5200 },
  { ticker: 'HDFCLIFE', name: 'HDFC Life Insurance', basePrice: 650 },
  { ticker: 'SBILIFE', name: 'SBI Life Insurance', basePrice: 1400 },
  { ticker: 'DRREDDY', name: 'Dr Reddys Labs', basePrice: 5800 },
  { ticker: 'CIPLA', name: 'Cipla Limited', basePrice: 1350 },
  { ticker: 'APOLLOHOSP', name: 'Apollo Hospitals', basePrice: 6200 },
  { ticker: 'BRITANNIA', name: 'Britannia Industries', basePrice: 4800 },
  { ticker: 'INDUSINDBK', name: 'IndusInd Bank', basePrice: 1500 },
  { ticker: 'EICHERMOT', name: 'Eicher Motors', basePrice: 3800 },
  { ticker: 'DIVISLAB', name: 'Divis Laboratories', basePrice: 3800 },
  { ticker: 'BAJAJ-AUTO', name: 'Bajaj Auto', basePrice: 7500 },
  { ticker: 'HEROMOTOCO', name: 'Hero MotoCorp', basePrice: 4500 },
  { ticker: 'TATACONSUM', name: 'Tata Consumer', basePrice: 1100 },
  { ticker: 'GRASIM', name: 'Grasim Industries', basePrice: 2100 },
  { ticker: 'UPL', name: 'UPL Limited', basePrice: 550 },
  { ticker: 'ALKEM', name: 'Alkem Laboratories', basePrice: 5506.00 },
  { ticker: 'ZOMATO', name: 'Zomato', basePrice: 150 },
  { ticker: 'PAYTM', name: 'Paytm', basePrice: 600 },
  { ticker: 'DLF', name: 'DLF Limited', basePrice: 850 },
  { ticker: 'HAL', name: 'Hindustan Aeronautics', basePrice: 3000 },
  { ticker: 'BEL', name: 'Bharat Electronics', basePrice: 190 },
  { ticker: 'TRENT', name: 'Trent Limited', basePrice: 3800 },
  { ticker: 'VEDL', name: 'Vedanta', basePrice: 260 },
  { ticker: 'IOC', name: 'Indian Oil Corp', basePrice: 180 },
  { ticker: 'GAIL', name: 'GAIL India', basePrice: 170 },
  { ticker: 'SHREECEM', name: 'Shree Cement', basePrice: 27000 },
];

// Sync Logos from ALL_STOCKS (TradingView data)
function syncLogos() {
  if (typeof ALL_STOCKS !== 'undefined') {
    // Create a map for fast lookup
    const stockMap = new Map(ALL_STOCKS.map(s => [s.ticker, s]));

    FEATURED_STOCKS.forEach(featured => {
      const match = stockMap.get(featured.ticker);
      if (match && match.logo && isImageLogo(match.logo)) {
        featured.logo = match.logo;
      }
    });
  }

  // Re-render market cards OR tracker grid
  const marketGrid = document.getElementById('marketGrid');
  if (marketGrid) {
    if (marketGrid.classList.contains('tracker-grid')) {
      renderTrackerGrid();
    } else {
      renderMarketCards();
    }
  }
}

// Moved syncLogos exection to end of file to avoid ReferenceError


// Stocks to display in the main grid (first 9)
const DISPLAY_STOCKS = FEATURED_STOCKS.slice(0, 9);

// Store current prices (initialized with base prices)
const currentPrices = {};
const currentChanges = {};
// Track currently visible search tickers for live updates
let visibleSearchTickers = [];

// Initialize prices for Featured Stocks
FEATURED_STOCKS.forEach(stock => {
  currentPrices[stock.ticker] = stock.basePrice;
});

// Helper to sync prices from ALL_STOCKS
// ALL_STOCKS.basePrice now comes from the latest 1D candle close in the DB (real price),
// so it always wins over the hardcoded FEATURED_STOCKS placeholder values.
function _syncPricesFromAllStocks() {
  if (Array.isArray(window.ALL_STOCKS)) {
    window.ALL_STOCKS.forEach(stock => {
      if (stock && stock.ticker && stock.basePrice > 0) {
        currentPrices[stock.ticker] = stock.basePrice;
      }
    });
  }
}

if (typeof ALL_STOCKS !== 'undefined' && ALL_STOCKS.length > 0) {
  _syncPricesFromAllStocks();
}
window.addEventListener('stocksLoaded', _syncPricesFromAllStocks);

// ==========================================
// SEARCH FUNCTIONALITY
// ==========================================
// Live search — elements injected by nav.js, query fresh each time
(function() {
  var searchInput = document.getElementById('stockSearch');
  var searchResults = document.getElementById('searchResults');
  if (!searchInput || !searchResults) return;

  // Live search on every keystroke
searchInput.addEventListener('input', (e) => {
  const query = e.target.value.trim().toLowerCase();

  // Clear results if query is empty
  if (!query) {
    searchResults.classList.remove('visible');
    searchResults.innerHTML = '';
    document.body.style.overflow = ''; // Unlock Scroll
    return;
  }

  // Use ALL_STOCKS if available, else fallback to FEATURED
  const sourceList = (typeof ALL_STOCKS !== 'undefined') ? ALL_STOCKS : FEATURED_STOCKS;

  // Filter stocks - PREFIX ONLY (StartsWith), Limit to Top 5
  function _levenshtein(a, b) {
    if (a === b) return 0;
    if (!a.length) return b.length;
    if (!b.length) return a.length;
    var row = [];
    for (var i = 0; i <= b.length; i++) row[i] = i;
    for (var i = 1; i <= a.length; i++) {
      var prev = i;
      for (var j = 1; j <= b.length; j++) {
        var val = (a.charAt(i - 1) === b.charAt(j - 1)) ? row[j - 1] : Math.min(row[j - 1] + 1, Math.min(prev + 1, row[j] + 1));
        row[j - 1] = prev;
        prev = val;
      }
      row[b.length] = prev;
    }
    return row[b.length];
  }

  // Calculate Match Score
  function getMatchScore(stock, query) {
    let cleanQuery = query.toLowerCase().replace(/[^a-z0-9]/g, '');
    if (!cleanQuery) return 0;

    // Support stripping .NS or .BO if added by user (e.g. "SENSEX.NS" -> "SENSEX")
    if (cleanQuery.endsWith('ns')) cleanQuery = cleanQuery.slice(0, -2);
    else if (cleanQuery.endsWith('bo')) cleanQuery = cleanQuery.slice(0, -2);

    const ticker = (stock.ticker || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const rawName = (stock.name || '').toLowerCase();
    const cleanName = rawName.replace(/[^a-z0-9]/g, '');

    // 1. Exact Ticker Match (Highest)
    if (ticker === cleanQuery) return 1000;

    // 2. Ticker StartsWith
    if (ticker.startsWith(cleanQuery)) return 800;

    // 3. Name StartsWith
    if (cleanName.startsWith(cleanQuery)) return 700;

    // 4. Any Word in Name StartsWith
    var words = rawName.split(/[\s\-_\.,]+/);
    for (var i = 0; i < words.length; i++) {
      var w = words[i].replace(/[^a-z0-9]/g, '');
      if (w && w.startsWith(cleanQuery)) return 600;
    }

    // 5. Continuous Substring in Ticker or Name
    if (ticker.indexOf(cleanQuery) !== -1) return 500;
    if (cleanName.indexOf(cleanQuery) !== -1) return 400;

    // 6. Handle "NSE "/"BSE " prefix for indices (e.g. "NSE SENSEX" -> "SENSEX")
    const isIndex = ['NIFTY', 'BANKNIFTY', 'SENSEX'].includes(stock.ticker);
    if (isIndex && (query.toLowerCase().startsWith('nse ') || query.toLowerCase().startsWith('bse '))) {
      const subQuery = query.toLowerCase().split(' ').slice(1).join(' ').replace(/[^a-z0-9]/g, '');
      if (subQuery && (ticker.startsWith(subQuery) || cleanName.startsWith(subQuery))) {
        return 550;
      }
    }

    // 7. Typo / Phonetic tolerance (Levenshtein distance <= 1 for >= 3 chars, <= 2 for >= 6 chars)
    if (cleanQuery.length >= 3) {
      var maxDist = cleanQuery.length >= 6 ? 2 : 1;
      var tDistPref = _levenshtein(ticker.slice(0, cleanQuery.length), cleanQuery);
      var tDistFull = _levenshtein(ticker, cleanQuery);
      var tDist = Math.min(tDistPref, tDistFull);
      if (tDist <= maxDist) return 300 - tDist * 50;

      for (var i = 0; i < words.length; i++) {
        var w = words[i].replace(/[^a-z0-9]/g, '');
        if (w.length >= 3) {
          var wDistPref = _levenshtein(w.slice(0, cleanQuery.length), cleanQuery);
          var wDistFull = _levenshtein(w, cleanQuery);
          var wDist = Math.min(wDistPref, wDistFull);
          if (wDist <= maxDist) return 250 - wDist * 50;
        }
      }
    }

    return 0;
  }

  // Get Matches with Score
  let scored = sourceList.map(s => ({ stock: s, score: getMatchScore(s, query) }))
    .filter(item => item.score > 0);

  // Sort by Score DESC, then NSE Priority
  scored.sort((a, b) => {
    const scoreDiff = b.score - a.score;
    if (scoreDiff !== 0) return scoreDiff;

    // Secondary Sort: NSE first
    if (a.stock.exchange === 'NSE') return -1;
    if (b.stock.exchange === 'NSE') return 1;
    return 0;
  });

  // Extract top 5 stocks
  const matches = scored.slice(0, 5).map(item => item.stock);

  const filtered = matches;


  // Update global list for live updates (Use Full Ticker with Suffix to distinguish NSE/BSE)
  visibleSearchTickers = filtered.map(s => {
    let t = s.ticker;
    if (s.exchange === 'BSE' && !t.endsWith('.BO')) t += '.BO';
    else if (s.exchange === 'NSE' && !t.endsWith('.NS')) t += '.NS';
    return t;
  });

  // Display results
  if (filtered.length > 0) {
    searchResults.innerHTML = filtered.map(stock => {
      const price = currentPrices[stock.ticker] || stock.basePrice || 0;
      const changeData = currentChanges[stock.ticker] || { value: 0, percent: 0 };
      const isPositive = changeData.value >= 0;
      const color = isPositive ? '#00C853' : '#FF5252'; // Match mockup colors
      const sign = isPositive ? '+' : '';
      const arrow = isPositive ? '▲' : '▼';

      const isIndex = ['NIFTY', 'BANKNIFTY', 'SENSEX'].includes(stock.ticker);
      // Use actual exchange if available, else default logic
      const type = stock.exchange || (isIndex ? 'INDEX' : 'NSE');

      let displayTicker = stock.ticker;
      if (type === 'BSE' && !displayTicker.endsWith('.BO')) displayTicker += '.BO';
      else if (type === 'NSE' && !displayTicker.endsWith('.NS')) displayTicker += '.NS';

      return `
      <div class="result-item">
        <div class="result-logo">
        ${isImageLogo(stock.logo)
          ? `<img src="${stock.logo}" class="search-logo-img" alt="${stock.ticker}" onerror="this.style.display='none'">`
          : ''}
      </div>
        <div class="result-info">
          <div class="result-name">${stock.name}</div>
          <div class="result-ticker-row" style="display: flex; align-items: center; gap: 6px;">
             <span class="result-ticker">${displayTicker}</span>
             <span class="stock-type-badge" style="font-size: 0.6rem; padding: 1px 4px;">${type}</span>
          </div>
        </div>
        
        <div class="result-meta">
            <div class="result-price" style="text-align: right; margin-right: 12px;">
                <div id="search-price-${displayTicker}" style="color: white; font-weight: 600;">₹${formatPrice(price)}</div>
                <div id="search-change-${displayTicker}" style="font-size: 0.75rem; color: ${color}; margin-top: 2px; font-weight: 500;">
                    ${arrow} ${sign}${Math.abs(changeData.percent).toFixed(2)}%
                </div>
            </div>
            <button class="search-launch-btn" onclick="launchChart('${stock.ticker.replace(/'/g, '')}', '${stock.exchange.replace(/'/g, '')}')">Launch Chart</button>
        </div>
      </div>
    `}).join('');
    searchResults.classList.add('visible');
    document.body.style.overflow = 'hidden'; // Lock Scroll
  } else {
    searchResults.innerHTML = '<div class="no-results" style="padding: 1.5rem; text-align: center; color: #52525b;">No stocks found</div>';
    searchResults.classList.add('visible');
    document.body.style.overflow = 'hidden'; // Lock Scroll
  }
});

// Hide search results when clicking outside
document.addEventListener('click', (e) => {
  var sr = document.getElementById('searchResults');
  if (!sr) return;
  if (!e.target.closest('.search-panel')) {
    sr.classList.remove('visible');
    document.body.style.overflow = '';
  }
});

// Prevent search results from closing when clicking inside
document.getElementById('searchResults')?.addEventListener('click', (e) => {
  e.stopPropagation();
});
}()); // end search IIFE

// Navigate to chart page with ticker parameter
// Navigate to chart page with ticker parameter
// Navigate to chart page with ticker parameter
function getChartPage() {
  const path = window.location.pathname;
  return path.includes('home.html') || path.includes('stock.html') || path.includes('portfolio.html') ? 'stock.html' : 'stock.html';
}

function launchChart(ticker, exchange) {
  let tickerParam = ticker;

  if (exchange === 'NSE' && !ticker.endsWith('.NS')) {
    tickerParam = `${ticker}.NS`;
  } else if (exchange === 'BSE' && !ticker.endsWith('.BO')) {
    tickerParam = `${ticker}.BO`;
  }
  // INDEX types (NIFTY etc) usually don't need suffix or handled by backend, 
  // currently we pass raw ticker for them based on previous logic.
  // Previous logic: if isIndex ? ticker : ticker.NS
  // Here exchange 'INDEX' means raw ticker.

  // Fallback if exchange not provided (legacy behavior):
  if (!exchange) {
    const isIndex = ['NIFTY', 'BANKNIFTY', 'SENSEX'].includes(ticker);
    tickerParam = isIndex ? ticker : `${ticker}.NS`;
  }

  window.location.href = `${getChartPage()}?ticker=${encodeURIComponent(tickerParam)}`;
}

// ==========================================
// MARKET CARDS - DYNAMIC RENDERING
// ==========================================
const marketGrid = document.getElementById('marketGrid');

// Generate initial market cards (DISABLED if tracker is active)
function renderMarketCards() {
  const marketGrid = document.getElementById('marketGrid');
  if (!marketGrid || marketGrid.classList.contains('tracker-grid')) return;

  marketGrid.innerHTML = DISPLAY_STOCKS.map(stock => {
    const price = currentPrices[stock.ticker];
    const isIndex = ['NIFTY', 'BANKNIFTY', 'SENSEX'].includes(stock.ticker);
    const type = stock.exchange || (isIndex ? 'INDEX' : 'NSE');

    // URL Construction
    let urlTicker = stock.ticker;
    if (type === 'BSE') urlTicker += '.BO';
    else if (type === 'NSE') urlTicker += '.NS';
    // Indices (type='INDEX') stay raw

    return `
      <div class="market-card">
        ${isImageLogo(stock.logo)
        ? `<img src="${stock.logo}" class="card-icon" alt="${stock.ticker}" onerror="this.style.display='none'">`
        : '<div class="card-icon">📈</div>'
      }
        <div class="card-ticker-group">
            <div class="card-ticker">${stock.ticker}</div>
            <div class="stock-type-badge">${type}</div>
        </div>
        <div class="card-name">${stock.name}</div>
        
        <div class="card-price" id="price-${stock.ticker}">
          ₹${formatPrice(price)}
        </div>
        
        <div class="price-change neutral" id="change-${stock.ticker}">
            <span>0.00%</span>
        </div>

        <button class="view-chart-btn" onclick="launchChart('${stock.ticker}', '${type}')">
          View Chart &rarr;
        </button>
      </div>
    `;
  }).join('');
}

// Format price based on value (adds commas for readability)
function formatPrice(price) {
  if (price == null || isNaN(price)) return '0.00';
  if (price >= 10000) {
    return price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return price.toFixed(2);
}

// ==========================================
// ==========================================
// REAL-TIME PRICE UPDATES (yfinance)
// ==========================================
// Consolidated real-time updates handles both Tracker and Search
let loaderHidden = false; // Flag to hide loader only once
let historyLoaded = false; // Flag to track when history data is loaded

const STRIP_TICKERS = ['NIFTY', 'SENSEX', 'BANKNIFTY', 'FINNIFTY'];

async function updatePrices() {
  // Skip REST call if WS is actively pushing price data (within last 10s)
  if (window._lastWsPriceTime && Date.now() - window._lastWsPriceTime < 10000) return;

  const tickers = [...new Set([...TRACKER_TICKERS, ...STRIP_TICKERS, ...visibleSearchTickers])];

  try {
    const response = await fetch('/api/live-prices', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tickers: tickers })
    });

    if (!response.ok) return;
    const livePrices = await response.json();

    Object.keys(livePrices).forEach(ticker => {
      const data = livePrices[ticker];

      if (!data || typeof data !== 'object') return;

      if (data.current === undefined || data.current === null) return;

      const newPrice = data.current;
      // Use server-computed change/pct (already relative to prev_close) when available
      const change = data.change != null ? data.change : (newPrice - (data.prev_close || data.open || newPrice));
      const pct = data.change_pct != null ? data.change_pct : (data.prev_close && data.prev_close > 0 ? ((newPrice - data.prev_close) / data.prev_close) * 100 : 0);

      // Store under BOTH the suffixed and plain key so search renders always find it
      currentPrices[ticker] = newPrice;
      currentChanges[ticker] = { value: change, percent: pct };
      const plainTicker = ticker.replace(/\.(NS|BO)$/i, '');
      if (plainTicker !== ticker) {
        currentPrices[plainTicker] = newPrice;
        currentChanges[plainTicker] = { value: change, percent: pct };
      }

      // 1. Update Tracker Card (if ticker is in TRACKER_TICKERS)
      if (TRACKER_TICKERS.includes(ticker)) {
        updateTrackerCard(ticker, newPrice, data.prev_close || data.open);
      }

      // 2. Update Index Ticker Strip
      if (STRIP_TICKERS.includes(ticker)) {
        const stripPriceEl = document.getElementById(`strip-price-${ticker}`);
        const stripChangeEl = document.getElementById(`strip-change-${ticker}`);
        if (stripPriceEl && stripChangeEl) {
          const sign = change >= 0 ? '+' : '';
          stripPriceEl.innerText = `₹${formatPrice(newPrice)}`;
          stripChangeEl.innerText = `${sign}${change.toFixed(2)} (${sign}${Math.abs(pct).toFixed(2)}%)`;
          stripChangeEl.className = 'change ' + (change >= 0 ? 'text-green' : 'text-red');
        }
      }


      // 4. Update Search Result UI (if displayed)
      const searchPriceEl = document.getElementById(`search-price-${ticker}`);
      const searchChangeEl = document.getElementById(`search-change-${ticker}`);

      if (searchPriceEl && searchChangeEl) {
        searchPriceEl.innerText = `₹${formatPrice(newPrice)}`;
        searchPriceEl.style.transition = "color 0.2s";
        searchPriceEl.style.color = "#fff";

        const sign = change >= 0 ? '+' : '';
        const arrow = change >= 0 ? '▲' : '▼';
        const colorHex = change >= 0 ? '#00C853' : '#FF5252';

        searchChangeEl.innerHTML = `${arrow} ${sign}${Math.abs(pct).toFixed(2)}%`;
        searchChangeEl.style.color = colorHex;

      }
    });

    // Hide loader after first successful price update ONLY if history is also loaded
    if (!loaderHidden && historyLoaded && typeof LeverageLoader !== 'undefined') {
      // Ensure we have reasonable amount of data (at least 3 tickers updated) to consider it "Ready"
      if (Object.keys(livePrices).length >= 3) {
        // Hide loader instantly without artificial delay
        if (!loaderHidden) {
          LeverageLoader.hide(0);
          loaderHidden = true;
        }
      }
    }

  } catch (e) {
    if (e && e.name === 'AbortError') return;
    console.error("Live update failed", e);
  }
}

// ==========================================
// INITIALIZATION
// ==========================================
// ==========================================
// TOP 9 STOCKS REAL-TIME TRACKER
// ==========================================

const TRACKER_TICKERS = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL'];
let trackerData = {}; // Stores { ticker: { history: [], todayOpen: 0, currentPrice: 0, yAxisMax: 0, hasRebalanced: false } }

async function initStockTracker() {
  const marketGrid = document.getElementById('marketGrid');
  if (!marketGrid) return;

  // Change layout to tracker grid
  marketGrid.classList.remove('market-grid');
  marketGrid.classList.add('tracker-grid');
  marketGrid.innerHTML = '<div style="color: #52525b; text-align: center; grid-column: 1/-1; padding: 3rem;">Loading Tracker Data...</div>';

  try {
    // ── sessionStorage cache (5 min TTL) ─────────────────────────────────
    let historyMap = null;
    const cachedHistory = sessionStorage.getItem('tracker_history_cache');
    const cachedHistoryTs = parseInt(sessionStorage.getItem('tracker_history_ts') || '0', 10);
    if (cachedHistory && (Date.now() - cachedHistoryTs < 300000)) { // 5 min
      try {
        historyMap = JSON.parse(cachedHistory);
        console.log('[Tracker] History from sessionStorage cache');
      } catch (e) { historyMap = null; }
    }

    if (!historyMap) {
      const ac = new AbortController();
      const tid = setTimeout(() => ac.abort(), 15000);
      const response = await fetch('/api/top-9-history', { signal: ac.signal });
      clearTimeout(tid);
      historyMap = await response.json();
      // Cache for 5 minutes
      try {
        sessionStorage.setItem('tracker_history_cache', JSON.stringify(historyMap));
        sessionStorage.setItem('tracker_history_ts', Date.now().toString());
      } catch (e) {}
    }

    TRACKER_TICKERS.forEach(ticker => {
      const history = historyMap[ticker] || [];
      // history is list of {day, price} -> we need flat list
      // Points 1-4: Close prices for 4 days
      // Helper to normalize date strings (removes leading zeros)
      const normalizeDate = (dateStr) => {
        const parts = dateStr.match(/(\w+)\s+(\d+)/);
        if (parts) return `${parts[1]} ${parseInt(parts[2], 10)}`;
        return dateStr;
      };

      const chartPoints = [];
      history.forEach(h => {
        const normalizedDay = normalizeDate(h.day);
        // Explicitly filter out Jan 26 (Holiday) - Robust check
        if (normalizedDay === 'Jan 26' || (normalizedDay.includes('Jan') && normalizedDay.includes('26'))) {
          console.log("Filtered out Jan 26 point:", h);
          return;
        }
        chartPoints.push({ label: normalizedDay, price: h.price });
      });

      const minHist = chartPoints.length > 0 ? Math.min(...chartPoints.map(p => p.price)) : 0;
      trackerData[ticker] = {
        historyPoints: chartPoints, // May be empty if no history data
        todayOpen: 0,  // Will be set from live API data
        currentPrice: 0,
        yAxisMax: 0,
        hasRebalanced: false,
        lowestPrice: minHist
      };

      // Force initial price from history if available (Fixes 0.00 issue)
      if (chartPoints.length > 0) {
        const lastPoint = chartPoints[chartPoints.length - 1];
        trackerData[ticker].currentPrice = lastPoint.price;

        // Initialize todayOpen to history last point (effectively yesterday's close)
        // This prevents the huge +6,000,000% jump on initial render.
        // It will be updated with actual today's open price as soon as live data arrives.
        trackerData[ticker].todayOpen = lastPoint.price;
      } else {
        // NO HISTORY DATA - Use live price as fallback
        console.warn(`[Tracker] No history data for ${ticker}, will use live price`);
        // Set minimal initial values - will be updated by live data immediately
        trackerData[ticker].currentPrice = 0;
        trackerData[ticker].todayOpen = 0;
      }
    });

    // Seed tracker cards from last_live_prices cache for instant live prices
    try {
      const cachedPricesRaw = sessionStorage.getItem('last_live_prices');
      if (cachedPricesRaw) {
        const cachedPrices = JSON.parse(cachedPricesRaw);
        TRACKER_TICKERS.forEach(ticker => {
          const d = cachedPrices[ticker];
          if (d && d.current) {
            trackerData[ticker].currentPrice = d.current;
            trackerData[ticker].todayOpen = d.prev_close || d.open || d.current;
          }
        });
      }
    } catch (e) {}

    renderTrackerGrid();

    // Mark history as loaded — hide loader now (cache served instantly)
    historyLoaded = true;
    if (!loaderHidden && typeof LeverageLoader !== 'undefined') {
      LeverageLoader.hide(0);
      loaderHidden = true;
    }

    // Attempt to sync logos if ALL_STOCKS is ready
    if (typeof syncLogos === 'function') syncLogos();

  } catch (e) {
    if (e && e.name !== 'AbortError') console.error("Failed to init tracker", e);
    marketGrid.innerHTML = '<div style="color: #ef4444; text-align: center; grid-column: 1/-1; padding: 3rem;">Failed to load historical data.</div>';
    historyLoaded = true;
  }
}

function renderTrackerGrid() {
  const marketGrid = document.getElementById('marketGrid');
  marketGrid.innerHTML = TRACKER_TICKERS.map(ticker => {
    const stock = FEATURED_STOCKS.find(s => s.ticker === ticker) || { logo: '📈' };

    // Explicitly check logical logo property
    // Explicitly check logical logo property
    let logoHTML;
    // Fix: Treat 'logos/' as valid image path
    const isImg = stock.logo && (stock.logo.startsWith('http') || stock.logo.startsWith('logos/'));

    if (isImg) {
      // Image URL (External or Local)
      logoHTML = `<img src="${stock.logo}" class="tracker-logo" alt="${ticker}" onerror="this.parentElement.innerHTML='<span class=\\'tracker-logo-icon\\'>📈</span>'">`;
    } else if (stock.logo && stock.logo.length > 0) {
      // Emojis or short text (Fallback)
      logoHTML = `<span class="tracker-logo-icon">${stock.logo}</span>`;
    } else {
      // Ultimate Fallback
      logoHTML = `<span class="tracker-logo-icon">📈</span>`;
    }

    return `
            <div class="tracker-card" id="tracker-card-${ticker}">
                <a href="${getChartPage()}?ticker=${getParmTicker(ticker)}" class="tracker-view-btn" onclick="event.preventDefault(); launchChart('${ticker}', '${stock.exchange || 'NSE'}')">View Chart</a>
                <div class="tracker-header">
                    <div class="tracker-symbol-group">
                        ${logoHTML}
                        <div style="display: flex; flex-direction: column; margin-left: 8px;">
                            <div style="display: flex; align-items: center; gap: 6px;">
                               <span class="tracker-symbol">${ticker}</span>
                               <span class="tracker-badge">${stock.exchange || (['NIFTY', 'BANKNIFTY', 'SENSEX'].includes(ticker) ? 'INDEX' : 'NSE')}</span>
                            </div>
                            <span class="tracker-name" style="font-size: 0.75rem; color: #a1a1aa; font-weight: 500; letter-spacing: 0.5px;">${stock.name}</span>
                        </div>
                    </div>
                    <div class="tracker-price-row">
                        <div class="tracker-current-price" id="tracker-price-${ticker}">₹0.00</div>
                        <div class="tracker-change" id="tracker-change-${ticker}">+0.00 (+0.00%)</div>
                    </div>
                </div>
                <div class="tracker-chart-container">
                    <svg class="tracker-chart-svg" id="tracker-svg-${ticker}" viewBox="0 0 300 120" preserveAspectRatio="none">
                        <defs>
                            <linearGradient id="grad-pos-${ticker}" x1="0%" y1="0%" x2="0%" y2="100%">
                                <stop offset="0%" style="stop-color:#10b981;stop-opacity:0.4" />
                                <stop offset="100%" style="stop-color:#10b981;stop-opacity:0" />
                            </linearGradient>
                            <linearGradient id="grad-neg-${ticker}" x1="0%" y1="0%" x2="0%" y2="100%">
                                <stop offset="0%" style="stop-color:#ef4444;stop-opacity:0.4" />
                                <stop offset="100%" style="stop-color:#ef4444;stop-opacity:0" />
                            </linearGradient>
                        </defs>
                        <g class="tracker-grid-lines"></g>
                        <path class="tracker-area" id="tracker-area-${ticker}"></path>
                        <path class="tracker-chart-line" id="tracker-path-${ticker}"></path>
                        <g class="tracker-points"></g>
                    </svg>
                    <div class="tracker-tooltip" id="tooltip-${ticker}"></div>
                </div>
            </div>
        `;
  }).join('');

  // Initial draw for each card
  TRACKER_TICKERS.forEach(ticker => updateTrackerCard(ticker, 0, 0));
}

function updateTrackerCard(ticker, currentPrice, todayOpen) {
  const data = trackerData[ticker];
  if (!data) return;

  // Update state
  // CRITICAL FIX: Always update todayOpen from API when provided (not 0)
  // This ensures we compare with actual today's open, not yesterday's close
  if (todayOpen > 0) {
    data.todayOpen = todayOpen;
  }
  if (currentPrice > 0) data.currentPrice = currentPrice;

  // Fallback: If currentPrice is 0, use last history point (prevents empty graph)
  if (data.currentPrice === 0 && data.historyPoints.length > 0) {
    data.currentPrice = data.historyPoints[data.historyPoints.length - 1].price;
  }

  // CRITICAL FIX: Handle empty history gracefully
  if (data.currentPrice === 0) {
    console.warn(`[Tracker] No price data for ${ticker} yet`);
    return; // Skip update until we have data
  }

  // If no history points, create minimal display with just live price
  if (data.historyPoints.length === 0) {
    console.log(`[Tracker] ${ticker}: No history, showing live price only`);

    // Update price display
    const priceEl = document.getElementById(`tracker-price-${ticker}`);
    const changeEl = document.getElementById(`tracker-change-${ticker}`);

    if (priceEl) {
      const oldText = priceEl.innerText.replace(/[^0-9.]/g, '');
      const oldPrice = parseFloat(oldText);
      const newPriceStr = formatPrice(data.currentPrice);
      priceEl.innerText = `₹${newPriceStr}`;
      
      if (!isNaN(oldPrice) && Math.abs(data.currentPrice - oldPrice) > 0.01) {
        const isUp = data.currentPrice > oldPrice;
        priceEl.style.transition = 'background 0s, color 0s';
        priceEl.style.backgroundColor = isUp ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)';
        priceEl.style.color = isUp ? '#10b981' : '#ef4444';
        priceEl.style.borderRadius = '4px';
        
        setTimeout(() => {
          if (priceEl) {
            priceEl.style.transition = 'background 0.8s, color 0.8s';
            priceEl.style.backgroundColor = 'transparent';
            priceEl.style.color = '#fff';
          }
        }, 300);
      }
    }

    if (changeEl && data.todayOpen > 0) {
      const change = data.currentPrice - data.todayOpen;
      const pct = (change / data.todayOpen) * 100;
      const isPositive = change >= 0;
      const sign = isPositive ? '+' : '';
      const arrow = isPositive ? '▲' : '▼';

      changeEl.innerText = `${arrow} ${sign}${Math.abs(change).toFixed(2)} (${sign}${Math.abs(pct).toFixed(2)}%)`;
      changeEl.className = `tracker-change ${isPositive ? 'positive' : 'negative'}`;
    } else if (changeEl) {
      changeEl.innerText = '-- (0.00%)';
      changeEl.className = 'tracker-change';
    }

    return; // Don't try to draw chart without history
  }

  // Prepare 5 points logic (Respecting Weekends) - MOVED UP for Y-Axis Calculation
  // Helper to normalize date strings (removes leading zeros for consistent comparison)
  const normalizeDate = (dateStr) => {
    const parts = dateStr.match(/(\w+)\s+(\d+)/);
    if (parts) return `${parts[1]} ${parseInt(parts[2], 10)}`;
    return dateStr;
  };

  const todayStr = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  const todayNormalized = normalizeDate(todayStr);
  const dayOfWeek = new Date().getDay();
  const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;
  // Explicitly mark holidays to prevent auto-adding point
  const isHoliday = (todayNormalized === 'Jan 26' || todayNormalized === 'Jan 15');

  // Check if market is currently open (9:15 AM - 3:30 PM IST)
  const now = new Date();
  const currentMinutes = now.getHours() * 60 + now.getMinutes();
  const marketOpenMinutes = 9 * 60 + 15;  // 9:15 AM
  const marketCloseMinutes = 15 * 60 + 30; // 3:30 PM
  const isMarketOpen = currentMinutes >= marketOpenMinutes && currentMinutes <= marketCloseMinutes;

  // Normalize all history point labels to remove leading zeros (e.g., "Feb 02" → "Feb 2")
  let points = data.historyPoints.map(p => ({
    label: normalizeDate(p.label),
    price: p.price
  }));

  if (points.length > 0) {
    const lastPoint = points[points.length - 1];
    const lastLabelNormalized = normalizeDate(lastPoint.label);

    // Only add today's point if it's a trading day AND market is open
    if (!isWeekend && !isHoliday && isMarketOpen) {
      // Compare normalized dates to handle format differences (e.g., "Feb 02" vs "Feb 2")
      if (lastLabelNormalized === todayNormalized) {
        lastPoint.price = data.currentPrice;
      } else {
        points.push({ label: todayNormalized, price: data.currentPrice });
        if (points.length > 5) points.shift();
      }
    }
  }

  // Dynamic Y-Axis Logic (Maximize Amplitude)
  let yMin, yMax;
  if (points.length > 0) {
    const prices = points.map(p => p.price);
    const minP = Math.min(...prices);
    const maxP = Math.max(...prices);
    const diff = maxP - minP;

    // Add small padding (10% of range) to prevent cutting off
    const padding = diff === 0 ? minP * 0.01 : diff * 0.2;

    yMin = minP - padding;
    yMax = maxP + padding;
  } else {
    // Fallback
    yMin = data.currentPrice * 0.98;
    yMax = data.currentPrice * 1.02;
  }

  // Update UI text
  const priceEl = document.getElementById(`tracker-price-${ticker}`);
  const changeEl = document.getElementById(`tracker-change-${ticker}`);

  if (!priceEl || !changeEl) return;

  const change = data.currentPrice - data.todayOpen;
  const pct = (change / (data.todayOpen || 1)) * 100;
  const isPositive = change >= 0;
  const sign = isPositive ? '+' : '';
  const arrow = isPositive ? '▲' : '▼';

  priceEl.innerText = `₹${formatPrice(data.currentPrice)}`;
  changeEl.innerText = `${arrow} ${sign}${Math.abs(change).toFixed(2)} (${sign}${Math.abs(pct).toFixed(2)}%)`;
  changeEl.className = `tracker-change ${isPositive ? 'positive' : 'negative'}`;

  // Prepare 5 points logic - Already Done Above

  // SVG Drawing
  const svg = document.getElementById(`tracker-svg-${ticker}`);
  const path = document.getElementById(`tracker-path-${ticker}`);
  const areaPath = document.getElementById(`tracker-area-${ticker}`); // New Area Path

  if (!svg || !path) return;
  const gridG = svg.querySelector('.tracker-grid-lines');
  const pointsG = svg.querySelector('.tracker-points');
  if (!gridG || !pointsG) return;

  const width = 300;
  const height = 120;
  const padding = 10;
  // Dynamic X-spacing based on point count (usually 5)
  const count = points.length > 1 ? points.length - 1 : 1;
  const xOffset = 50; // Increased to prevent Y-axis overlap
  const getX = (i) => xOffset + (i * ((width - xOffset - 10) / count));
  const range = yMax - yMin;
  const getY = (val) => height - padding - ((val - yMin) / (range > 0 ? range : 1)) * (height - 2 * padding);

  // Update Line Path
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${getX(i)} ${getY(p.price)}`).join(' ');
  path.setAttribute('d', d);
  const chartColor = isPositive ? '#089981' : '#f23645';
  path.style.stroke = chartColor;
  path.style.fill = 'none'; // Ensure line itself has no fill

  // Update Area Path
  if (areaPath) {
    // Area starts at bottom-left, goes up to first point, follows line, goes down to bottom-right, closes loop
    const areaD = `M ${getX(0)} ${height} L ${getX(0)} ${getY(points[0].price)} ` +
      points.slice(1).map((p, i) => `L ${getX(i + 1)} ${getY(p.price)}`).join(' ') +
      ` L ${getX(points.length - 1)} ${height} Z`;

    areaPath.setAttribute('d', areaD);
    areaPath.style.fill = `url(#${isPositive ? 'grad-pos' : 'grad-neg'}-${ticker})`;
    areaPath.style.stroke = 'none';
  }

  // Draw Points
  pointsG.innerHTML = '';
  const tooltip = document.getElementById(`tooltip-${ticker}`);

  points.forEach((p, i) => {
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    const cx = getX(i);
    const cy = getY(p.price);

    // Draw Points
    circle.setAttribute("cx", cx);
    circle.setAttribute("cy", cy);
    circle.setAttribute("r", (i === points.length - 1) ? 4 : 3); // Highlight last point
    circle.setAttribute("class", "tracker-chart-point");
    circle.setAttribute("stroke", chartColor);
    circle.style.cursor = "pointer"; // Indicate interactivity

    // Hover Events
    circle.addEventListener('mouseenter', () => {
      if (tooltip) {
        tooltip.style.left = `${(cx / width) * 100}%`;
        tooltip.style.top = `${(cy / height) * 100}%`;
        tooltip.innerHTML = `<strong>${p.label}</strong><br>₹${formatPrice(p.price)}`;
        tooltip.style.display = 'block';

        // Smart Positioning to prevent cropping
        let translateX = '-50%';
        if (i === 0) translateX = '0%'; // Align left-edge to point
        if (i === points.length - 1) translateX = '-100%'; // Align right-edge to point

        tooltip.style.transform = `translate(${translateX}, -120%)`;
      }
      circle.setAttribute("r", 6); // visual feedback
      circle.setAttribute("fill", "#fff");
    });

    circle.addEventListener('mouseleave', () => {
      if (tooltip) tooltip.style.display = 'none';
      circle.setAttribute("r", (i === points.length - 1) ? 4 : 3);
      circle.setAttribute("fill", "#000");
    });

    pointsG.appendChild(circle);
  });

  // Draw Grid and Labels - ALWAYS update to reflect current yMin/yMax
  // Clear existing grid content
  gridG.innerHTML = '';

  // Horizontal grid lines and Y-axis labels
  for (let i = 0; i <= 4; i++) {
    const y = padding + (i * (height - 2 * padding) / 4);
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", xOffset);
    line.setAttribute("y1", y);
    line.setAttribute("x2", width);
    line.setAttribute("y2", y);
    gridG.appendChild(line);

    // Y-Axis Labels with correct values
    const labelText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    const priceVal = yMax - (i * (yMax - yMin) / 4);
    labelText.setAttribute("x", 2);
    labelText.setAttribute("y", y + 3);
    labelText.setAttribute("class", "tracker-axis-label y-axis");
    labelText.textContent = formatPrice(priceVal);
    gridG.appendChild(labelText);
  }

  // X-Axis Labels
  points.forEach((p, i) => {
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", getX(i));
    text.setAttribute("y", height - 2);
    text.setAttribute("text-anchor", i === points.length - 1 ? "end" : (i === 0 ? "start" : "middle"));
    text.setAttribute("class", "tracker-axis-label");
    text.textContent = p.label;

    if (points.length <= 6 || i % 2 === 0) gridG.appendChild(text);
  });

}


// Initial Call
// Consolidated initialization
document.addEventListener('DOMContentLoaded', () => {
  console.log('🚀 Indian Stock Market Website Initialized');

  // 1. Initialize Tracker (Replaces Original Market Cards)
  initStockTracker();

  // Fetch live prices immediately on load
  updatePrices();

  // Backup polling to update prices periodically if WebSockets are slow/idle
  var _idxInterval = setInterval(updatePrices, 15000);
  document.addEventListener('visibilitychange', function() {
      if (document.hidden) { clearInterval(_idxInterval); _idxInterval = null; }
      else if (!_idxInterval) { _idxInterval = setInterval(updatePrices, 15000); updatePrices(); }
  });

  // Start DashboardWS for real-time price updates (dashboard.js also calls this — start() is idempotent)

  // 2. Listen for live price updates from DashboardWS (replaces HTTP poll loop)
  //    dashboard.js fires 'dashboard_price_update' on every WS price_update message
  window.addEventListener('dashboard_price_update', (evt) => {
    const raw = evt.detail;
    if (!raw) return;
    const prices = raw.prices || raw;
    // Update all tickers from WS price data
    Object.keys(prices).forEach(ticker => {
      const d = prices[ticker];
      if (!d || d.current === undefined) return;
      const wsChange = d.change != null ? d.change : (d.current - (d.prev_close || d.open || d.current));
      const wsPct = d.change_pct != null ? d.change_pct : (d.prev_close && d.prev_close > 0 ? ((d.current - d.prev_close) / d.prev_close) * 100 : 0);

      currentPrices[ticker] = d.current;
      currentChanges[ticker] = { value: wsChange, percent: wsPct };
      const plainTkr = ticker.replace(/\.(NS|BO)$/i, '');
      if (plainTkr !== ticker) {
        currentPrices[plainTkr] = d.current;
        currentChanges[plainTkr] = { value: wsChange, percent: wsPct };
      }

      if (TRACKER_TICKERS.includes(ticker)) {
        updateTrackerCard(ticker, d.current, d.prev_close || d.open || d.current);
      }

      // Update index ticker strip
      if (STRIP_TICKERS.includes(ticker)) {
        const sp = document.getElementById(`strip-price-${ticker}`);
        const sc = document.getElementById(`strip-change-${ticker}`);
        if (sp && sc) {
          const sign = wsChange >= 0 ? '+' : '';
          sp.innerText = `₹${formatPrice(d.current)}`;
          sc.innerText = `${sign}${wsChange.toFixed(2)} (${sign}${Math.abs(wsPct).toFixed(2)}%)`;
          sc.className = 'change ' + (wsChange >= 0 ? 'text-green' : 'text-red');
        }
      }

    });
    // Update any visible search results
    visibleSearchTickers.forEach(ticker => {
      const d = prices[ticker];
      if (!d || d.current === undefined) return;
      const searchPriceEl  = document.getElementById(`search-price-${ticker}`);
      const searchChangeEl = document.getElementById(`search-change-${ticker}`);
      if (searchPriceEl && searchChangeEl) {
        const newPrice  = d.current;
        const change    = d.change != null ? d.change : (newPrice - (d.prev_close || d.open || newPrice));
        const pct       = d.change_pct != null ? d.change_pct : (d.prev_close && d.prev_close > 0 ? ((newPrice - d.prev_close) / d.prev_close) * 100 : 0);
        const sign      = change >= 0 ? '+' : '';
        const arrow     = change >= 0 ? '▲' : '▼';
        const colorHex  = change >= 0 ? '#00C853' : '#FF5252';
        searchPriceEl.innerText           = `₹${formatPrice(newPrice)}`;
        searchChangeEl.innerHTML          = `${arrow} ${sign}${Math.abs(pct).toFixed(2)}%`;
        searchChangeEl.style.color        = colorHex;
      }
    });
    // Hide loader once we have live data (fallback if not hidden by tracker init)
    if (!loaderHidden && historyLoaded && typeof LeverageLoader !== 'undefined') {
      LeverageLoader.hide(0);
      loaderHidden = true;
    }
  });

  // 3. Safety Timeout: Force hide loader after 5 seconds (Fail-safe only)
  setTimeout(() => {
    if (typeof LeverageLoader !== 'undefined') {
      const loader = document.getElementById('leverage-loader');
      if (loader && !loader.classList.contains('fade-out')) {
        console.log('[Loader] Safety timeout triggered (5s)');
        LeverageLoader.hide(100);
      }
    }
  }, 5000);
});


// ==========================================
// KEYBOARD SHORTCUTS
// ==========================================
document.addEventListener('keydown', (e) => {
  var searchInput = document.getElementById('stockSearch');
  var searchResults = document.getElementById('searchResults');
  // Press '/' to focus search
  if (e.key === '/' && searchInput && document.activeElement !== searchInput) {
    e.preventDefault();
    searchInput.focus();
  }

  // Press 'Escape' to clear search
  if (e.key === 'Escape' && searchInput && searchResults) {
    searchInput.value = '';
    searchResults.classList.remove('visible');
    document.body.style.overflow = '';
    searchInput.blur();
  }
});

// ==========================================
// SYNC LOGOS (Dynamic Update)
// ==========================================
// Try initially (in case already loaded)
syncLogos();

// Listen for Async Load from stocks.js
window.addEventListener('stocksLoaded', () => {
  syncLogos();
});

// Helper for ticker params
function getParmTicker(ticker) {
  if (['NIFTY', 'BANKNIFTY', 'SENSEX'].includes(ticker)) return ticker;
  return `${ticker}.NS`;
}

// ==========================================
// AUTH STATE MANAGEMENT
// ==========================================
async function checkAuthState() {
  const token = sessionStorage.getItem('token');
  const userStr = sessionStorage.getItem('user');
  const authButtons = document.getElementById('authButtons');
  const loginBtn = document.getElementById('loginBtn');
  const userInfo = document.getElementById('userInfo');

  function setLoggedIn(show) {
    if (authButtons) authButtons.style.display = show ? 'none' : 'flex';
    if (loginBtn) loginBtn.style.display = show ? 'none' : 'inline-flex';
    if (userInfo) userInfo.style.display = show ? 'flex' : 'none';
  }

  if (token && userStr) {
    try {
      let user = JSON.parse(userStr);

      setLoggedIn(true);

      const updateUI = (userData) => {
        const userBalance = document.getElementById('userBalance');
        if (userBalance && userData.virtual_balance !== undefined) {
          userBalance.textContent = `₹${formatPrice(userData.virtual_balance)}`;
        }
      };

      // 1. Initial UI update from session storage (Immediate)
      updateUI(user);

      // 2. Background Sync (Fresh Balance/Profile)
      try {
        const response = await fetch('/api/auth/me', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (response.ok) {
          const freshUser = await response.json();
          // Update session storage and UI
          user = { ...user, ...freshUser };
          sessionStorage.setItem('user', JSON.stringify(user));
          updateUI(user);
        } else if (response.status === 401) {
          // Token expired or invalid
          sessionStorage.removeItem('token');
          sessionStorage.removeItem('user');
          setLoggedIn(false);
        }
      } catch (err) {
        console.log('[AUTH] Background balance sync failed');
      }
    } catch (e) {
      console.error('Error parsing user data:', e);
    }
  } else {
    setLoggedIn(false);
  }
}

async function handleLogout() {
  const token = sessionStorage.getItem('token');

  // Call logout API to blacklist token
  if (token) {
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });
    } catch (e) {
      console.log('[AUTH] Logout API call failed, clearing locally');
    }
  }

  sessionStorage.removeItem('token');
  sessionStorage.removeItem('user');

  // Refresh auth state
  checkAuthState();

  console.log('[AUTH] User logged out');
}

// Check auth state on page load
document.addEventListener('DOMContentLoaded', () => {
  // Existing init code runs first, then check auth
  setTimeout(checkAuthState, 100);
});


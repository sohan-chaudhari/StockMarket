(function () {
  'use strict';

  function escapeHTML(str) { var div = document.createElement('div'); div.appendChild(document.createTextNode(str)); return div.innerHTML; }

  /* ===================== CONFIG ===================== */
  var NAV_ITEMS = [
    { id: 'home', label: 'Home', href: 'home.html' },
    { id: 'markets', label: 'Markets', href: 'markets.html' },
    { id: 'stocks', label: 'Stocks', href: 'stocks.html' },

    { id: 'news', label: 'News', href: 'news.html' },
    { id: 'watchlist', label: 'Watchlist', href: 'watchlist.html' },
    { id: 'screener', label: 'Screener', href: 'screener.html' },
    { id: 'portfolio', label: 'Portfolio', href: 'portfolio.html' },
    { id: 'more', label: 'More', href: '#', hasDropdown: true },
  ];

  var MORE_ITEMS = [
    { label: 'IPOs', href: 'ipos.html', icon: '\uD83C\uDFE2' },
    { label: 'Mutual Funds', href: 'mf.html', icon: '\uD83D\uDCB0' },
    { label: 'ETFs', href: 'etfs.html', icon: '\uD83D\uDCCA' },
    { label: 'Economic Calendar', href: 'calendar.html', icon: '\uD83D\uDCC5' },
    { label: 'Settings', href: 'settings.html', icon: '\u2699\uFE0F' },
    { label: 'Support', href: 'support.html', icon: '\uD83D\uDEE0\uFE0F' },
    { label: 'Theme', href: '#', icon: '\uD83C\uDFA8' },
    { label: 'About', href: 'about.html', icon: '\u2139\uFE0F' },
  ];

  var SEARCH_FALLBACK = [
    { ticker: 'RELIANCE', name: 'Reliance Industries', exchange: 'NSE' },
    { ticker: 'TCS', name: 'Tata Consultancy Services', exchange: 'NSE' },
    { ticker: 'HDFCBANK', name: 'HDFC Bank', exchange: 'NSE' },
    { ticker: 'INFY', name: 'Infosys', exchange: 'NSE' },
    { ticker: 'ICICIBANK', name: 'ICICI Bank', exchange: 'NSE' },
    { ticker: 'BHARTIARTL', name: 'Bharti Airtel', exchange: 'NSE' },
    { ticker: 'SBIN', name: 'State Bank of India', exchange: 'NSE' },
    { ticker: 'NIFTY', name: 'NIFTY 50', exchange: 'INDEX' },
    { ticker: 'BANKNIFTY', name: 'BANK NIFTY', exchange: 'INDEX' },
    { ticker: 'SENSEX', name: 'SENSEX', exchange: 'INDEX' },
  ];

  /* ===================== UTILITY ===================== */
  function getCurrentPageId() {
    var path = window.location.pathname.split('/').pop() || 'home.html';
    var map = {
      'home.html': 'home', 'markets.html': 'markets', 'stocks.html': 'stocks',
      'news.html': 'news', 'watchlist.html': 'watchlist',
      'screener.html': 'screener', 'portfolio.html': 'portfolio',
      'index.html': 'home', 'stock.html': 'stocks', 'stock.html': 'markets',
    };
    return map[path] || 'home';
  }

  function isDashboardPage() {
    var path = (window.location.pathname.split('/').pop() || 'home.html').toLowerCase();
    return path === '' || path === 'home.html' || path === 'index.html' || path === '/' || path === 'index';
  }

  function isChartPage() {
    var path = (window.location.pathname.split('/').pop() || '').toLowerCase();
    return path === 'stock.html' || path === 'chart.html' || path === 'index_chart.html' || path === 'stock_rebuilt.html' || path === 'stock_restructured.html' || path === 'tv-chart.html' || path === 'market.html';
  }

  /* ===================== BUILD HEADER HTML ===================== */
  function buildHeaderHTML() {
    var currentPage = getCurrentPageId();

    var navHTML = NAV_ITEMS.map(function (item) {
      if (item.hasDropdown) {
        var subItems = MORE_ITEMS.map(function (sub) {
          return '<a href="' + sub.href + '" class="nav-dropdown-item">' +
            '<span class="nav-dropdown-icon">' + sub.icon + '</span> ' + sub.label + '</a>';
        }).join('');
        return '<div class="nav-dropdown">' +
          '<a href="' + item.href + '" class="nav-link ' + (currentPage === item.id ? 'active' : '') + '">' +
          item.label + ' <span class="nav-dropdown-arrow">\u25BE</span></a>' +
          '<div class="nav-dropdown-menu">' + subItems + '</div></div>';
      }
      return '<a href="' + item.href + '" class="nav-link ' + (currentPage === item.id ? 'active' : '') + '">' + item.label + '</a>';
    }).join('');

    var marketStatusHTML = isDashboardPage()
      ? '      <div class="market-status-indicator" id="marketStatusIndicator">' +
        '        <span class="pulse-dot"></span>' +
        '        <span class="status-text" id="marketStatusText">Loading...</span>' +
        '      </div>'
      : '';

    var subnavHTML = (!isDashboardPage() && !isChartPage())
      ? '<div class="page-subnav-bar">' +
        '  <div class="page-subnav-container">' +
        '    <a href="javascript:void(0)" onclick="if(window.history.length>1){window.history.back();}else{window.location.href=\'home.html\';}" class="back-btn" title="Go back" aria-label="Go back">' +
        '      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '        <path d="M19 12H5M12 19l-7-7 7-7"/>' +
        '      </svg>' +
        '      <span>Back</span>' +
        '    </a>' +
        '  </div>' +
        '</div>'
      : '';

    return '' +
      '<header class="header">' +
      '  <div class="header-container">' +
      '    <div class="header-top-row">' +
      '      <a href="home.html" class="logo-container" aria-label="LEVERAGE Home">' +
      '        <img src="Leverage_Horizontal_White.svg" alt="LEVERAGE" class="brand-logo-full">' +
      '        <img src="Leverage_Icon_White.svg" alt="LEVERAGE" class="brand-logo-icon-only">' +
      '      </a>' +
      '      <div class="search-panel">' +
      '        <div class="search-container">' +
      '          <svg class="search-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>' +
      '          <input type="text" id="stockSearch" class="search-input" placeholder=" " autocomplete="off">' +
      '          <div class="placeholder-overlay">' +
      '            <span class="static-text">Search for</span>' +
      '            <div class="scrolling-wrapper">' +
      '              <div class="scrolling-content">' +
      '                <div class="scrolling-item">RELIANCE</div>' +
      '                <div class="scrolling-item">INFY</div>' +
      '                <div class="scrolling-item">HDFC</div>' +
      '                <div class="scrolling-item">TCS</div>' +
      '                <div class="scrolling-item">ICICIBANK</div>' +
      '                <div class="scrolling-item">RELIANCE</div>' +
      '              </div>' +
      '            </div>' +
      '          </div>' +
      '          <div id="searchResults" class="search-results"></div>' +
      '        </div>' +
      '      </div>' +
      '      <div class="nav-actions">' +
      '        <button class="nav-icon-btn" title="Notifications" aria-label="Notifications" id="notificationBtn">' +
      '          <svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.73 21a2 2 0 0 1-3.46 0"></path></svg>' +
      '          <span class="notification-badge" id="notificationBadge">3</span>' +
      '        </button>' +
      '        <div class="auth-buttons" id="authButtons">' +
      '          <a href="login.html" class="auth-btn" onclick="try{sessionStorage.setItem(\'auth_return_url\',window.location.href);}catch(e){}">Login / Register</a>' +
      '        </div>' +
      '        <div class="user-info" id="userInfo" style="display:none; align-items: center; gap: 15px;">' +
      '          <div style="text-align: right;">' +
      '             <div style="font-size: 0.75rem; color: #a1a1aa; text-transform: uppercase; letter-spacing: 0.5px;">Balance</div>' +
      '             <div class="user-balance" id="userBalance" style="color: #089981; font-weight: bold; font-family: \'Roboto Mono\', monospace;">₹0</div>' +
      '          </div>' +
      '          <a href="profile.html?v=1" class="user-profile-btn" style="text-decoration: none;" title="Account Dashboard">' +
      '            <div class="user-avatar">' +
      '              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>' +
      '            </div>' +
      '          </a>' +
      '        </div>' +
      '      </div>' +
      '    </div>' +
      '    <div class="header-bottom-row">' +
      '      <nav class="nav-links">' + navHTML + '</nav>' +
      marketStatusHTML +
      '    </div>' +
      '  </div>' +
      '</header>' +
      subnavHTML;
  }

  /* ===================== SEARCH ===================== */
  var _navFetchingStocks = false;
  function initSearch() {
    var input = document.getElementById('stockSearch');
    var results = document.getElementById('searchResults');
    if (!input || !results) return;

    function sourceList() {
      if (typeof window !== 'undefined' && Array.isArray(window.ALL_STOCKS) && window.ALL_STOCKS.length > 0) {
        return window.ALL_STOCKS;
      }
      try {
        var stored = localStorage.getItem('leverage_stocks');
        if (stored) {
          var parsed = JSON.parse(stored);
          if (Array.isArray(parsed) && parsed.length > 0) {
            window.ALL_STOCKS = parsed;
            return parsed;
          }
        }
      } catch (e) {}

      // If cache miss, fetch fresh stocks from API asynchronously
      if (!_navFetchingStocks) {
        _navFetchingStocks = true;
        fetch('/api/all-stocks').then(function(r) { return r.json(); }).then(function(stocks) {
          if (Array.isArray(stocks) && stocks.length > 0) {
            window.ALL_STOCKS = stocks;
            try { localStorage.setItem('leverage_stocks', JSON.stringify(stocks)); } catch(e){}
            window.dispatchEvent(new Event('stocksLoaded'));
          }
        }).catch(function(){}).finally(function(){ _navFetchingStocks = false; });
      }

      return (typeof SEARCH_FALLBACK !== 'undefined') ? SEARCH_FALLBACK : [];
    }

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

    function getScore(stock, query) {
      var cleanQuery = query.toLowerCase().replace(/[^a-z0-9]/g, '');
      if (!cleanQuery) return 0;

      // Support stripping .NS or .BO if added by user
      if (cleanQuery.endsWith('ns')) cleanQuery = cleanQuery.slice(0, -2);
      else if (cleanQuery.endsWith('bo')) cleanQuery = cleanQuery.slice(0, -2);

      var ticker = (stock.ticker || '').toLowerCase().replace(/[^a-z0-9]/g, '');
      var rawName = (stock.name || '').toLowerCase();
      var cleanName = rawName.replace(/[^a-z0-9]/g, '');

      // 1. Exact Ticker Match
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
      var isIndex = ['NIFTY', 'BANKNIFTY', 'SENSEX'].indexOf(stock.ticker) !== -1;
      if (isIndex && (query.toLowerCase().startsWith('nse ') || query.toLowerCase().startsWith('bse '))) {
        var subQuery = query.toLowerCase().split(' ').slice(1).join(' ').replace(/[^a-z0-9]/g, '');
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

        for (var j = 0; j < words.length; j++) {
          var word = words[j].replace(/[^a-z0-9]/g, '');
          if (word.length >= 3) {
            var wDistPref = _levenshtein(word.slice(0, cleanQuery.length), cleanQuery);
            var wDistFull = _levenshtein(word, cleanQuery);
            var wDist = Math.min(wDistPref, wDistFull);
            if (wDist <= maxDist) return 250 - wDist * 50;
          }
        }
      }

      return 0;
    }

    window._searchPriceCache = window._searchPriceCache || {};
    var _navSearchFetchTimer = null;

    function _fetchNavSearchLivePrices(stocks) {
      if (!stocks || stocks.length === 0) return;
      var tickers = stocks.map(function(s) { return s.ticker; });
      clearTimeout(_navSearchFetchTimer);
      _navSearchFetchTimer = setTimeout(function() {
        fetch('/api/live-prices', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tickers: tickers })
        }).then(function(r) { return r.json(); }).then(function(data) {
          if (!data || typeof data !== 'object') return;
          Object.keys(data).forEach(function(tkr) {
            var item = data[tkr];
            if (!item || item.current == null) return;
            var cp = Number(item.current);
            var chg = item.change != null ? Number(item.change) : (cp - Number(item.prev_close || cp));
            var pct = item.change_pct != null ? Number(item.change_pct) : (item.prev_close && item.prev_close > 0 ? ((cp - Number(item.prev_close)) / Number(item.prev_close)) * 100 : 0);

            var plain = tkr.replace(/\.(NS|BO)$/i, '');
            var cacheVal = { current: cp, change: chg, percent: pct };
            window._searchPriceCache[tkr] = cacheVal;
            window._searchPriceCache[plain] = cacheVal;

            var cleanTkr = plain.replace(/[^a-zA-Z0-9]/g, '');
            var priceEl = document.getElementById('nav-search-price-' + cleanTkr);
            var changeEl = document.getElementById('nav-search-change-' + cleanTkr);
            if (priceEl && changeEl) {
              priceEl.textContent = '₹' + cp.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
              var isPos = pct >= 0;
              var sign = isPos ? '+' : '';
              var arrow = isPos ? '▲' : '▼';
              changeEl.textContent = arrow + ' ' + sign + Math.abs(pct).toFixed(2) + '%';
              changeEl.style.color = isPos ? '#00C853' : '#FF5252';
            }
          });
        }).catch(function() {});
      }, 30);
    }

    input.addEventListener('input', function (e) {
      var query = e.target.value.trim().toLowerCase();
      if (!query) {
        results.classList.remove('visible');
        results.innerHTML = '';
        return;
      }

      var list = sourceList();
      var scored = list.map(function (s) { return { stock: s, score: getScore(s, query) }; })
        .filter(function (item) { return item.score > 0; });

      scored.sort(function (a, b) {
        var diff = b.score - a.score;
        if (diff !== 0) return diff;
        if (a.stock.exchange === 'NSE') return -1;
        if (b.stock.exchange === 'NSE') return 1;
        return 0;
      });

      var matches = scored.slice(0, 5).map(function (item) { return item.stock; });

      if (matches.length > 0) {
        results.innerHTML = matches.map(function (stock) {
          var type = stock.exchange || 'NSE';
          var displayTicker = stock.ticker;
          if (type === 'BSE' && displayTicker.indexOf('.BO') === -1) displayTicker += '.BO';
          else if (type === 'NSE' && displayTicker.indexOf('.NS') === -1 && type !== 'INDEX') displayTicker += '.NS';

          var logoHtml = '';
          if (stock.logo && (stock.logo.startsWith('http') || stock.logo.startsWith('/')) && !stock.logo.includes('gstatic.com') && !stock.logo.includes('faviconV2')) {
            logoHtml = '<div class="result-logo" style="margin-right:12px;display:flex;align-items:center;">' +
              '<img src="' + escapeHTML(stock.logo) + '" class="search-logo-img" alt="' + escapeHTML(stock.ticker) + '" onerror="this.style.display=\'none\'">' +
              '</div>';
          }

          var liveObj = (window._searchPriceCache && (window._searchPriceCache[stock.ticker] || window._searchPriceCache[stock.ticker.replace(/\.(NS|BO)$/i, '')])) ||
                        (typeof currentPrices !== 'undefined' && (currentPrices[stock.ticker] || currentPrices[stock.ticker.replace(/\.(NS|BO)$/i, '')]) ? {
                          current: currentPrices[stock.ticker] || currentPrices[stock.ticker.replace(/\.(NS|BO)$/i, '')],
                          percent: (typeof currentChanges !== 'undefined' && (currentChanges[stock.ticker] || currentChanges[stock.ticker.replace(/\.(NS|BO)$/i, '')])) ? (currentChanges[stock.ticker] || currentChanges[stock.ticker.replace(/\.(NS|BO)$/i, '')]).percent : 0,
                          change: (typeof currentChanges !== 'undefined' && (currentChanges[stock.ticker] || currentChanges[stock.ticker.replace(/\.(NS|BO)$/i, '')])) ? (currentChanges[stock.ticker] || currentChanges[stock.ticker.replace(/\.(NS|BO)$/i, '')]).value : 0
                        } : null);

          var price = (liveObj && liveObj.current != null) ? liveObj.current : (stock.basePrice || 0);
          var priceStr = Number(price).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          
          var chgPct = (liveObj && liveObj.percent != null) ? liveObj.percent : (stock.changePercent != null ? stock.changePercent : (stock.change_pct != null ? stock.change_pct : 0));
          var isPositive = chgPct >= 0;
          var color = isPositive ? '#00C853' : '#FF5252';
          var sign = isPositive ? '+' : '';
          var arrow = isPositive ? '▲' : '▼';
          var cleanTkr = stock.ticker.replace(/[^a-zA-Z0-9]/g, '');

          return '<div class="result-item" onclick="openStockOverview(\'' + escapeHTML(stock.ticker) + '\')">' +
            logoHtml +
            '<div class="result-info">' +
              '<div class="result-name">' + escapeHTML(stock.name) + '</div>' +
              '<div class="result-ticker-row" style="display: flex; align-items: center; gap: 6px; margin-top: 2px;">' +
                '<span class="result-ticker">' + displayTicker + '</span>' +
                '<span class="stock-type-badge" style="font-size: 0.6rem; padding: 1px 4px; background: rgba(255,255,255,0.08); border-radius: 3px; color: #a1a1aa;">' + type + '</span>' +
              '</div>' +
            '</div>' +
            '<div class="result-meta" style="display: flex; align-items: center; margin-left: auto;">' +
              '<div class="result-price" style="text-align: right; margin-right: 12px;">' +
                '<div id="nav-search-price-' + cleanTkr + '" style="color: white; font-weight: 600;">₹' + priceStr + '</div>' +
                '<div id="nav-search-change-' + cleanTkr + '" style="font-size: 0.75rem; color: ' + color + '; margin-top: 2px; font-weight: 500;">' +
                  arrow + ' ' + sign + Math.abs(chgPct).toFixed(2) + '%' +
                '</div>' +
              '</div>' +
              '<button class="search-launch-btn" onclick="event.stopPropagation(); launchStockChart(\'' + escapeHTML(stock.ticker) + '\', \'' + escapeHTML(type) + '\')">Launch Chart</button>' +
            '</div>' +
          '</div>';
        }).join('');
        results.classList.add('visible');

        // Immediately fetch live price & change for displayed search matches
        _fetchNavSearchLivePrices(matches);
      } else {
        results.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--text-muted)">No stocks found</div>';
        results.classList.add('visible');
      }
    });

    window.openStockOverview = function (ticker) {
      var cleanTicker = (ticker || '').replace(/\.(NS|BO)$/i, '');
      window.location.href = 'overview.html?ticker=' + encodeURIComponent(cleanTicker);
    };

    window.launchStockChart = function (ticker, exchange) {
      var cleanTicker = (ticker || '').replace(/\.(NS|BO)$/i, '');
      window.location.href = 'stock.html?ticker=' + encodeURIComponent(cleanTicker);
    };

    document.addEventListener('click', function (e) {
      if (!e.target.closest('.search-panel')) {
        results.classList.remove('visible');
      }
    });

    // Re-run search when ALL_STOCKS finishes loading (async fetch race)
    window.addEventListener('stocksLoaded', function () {
      if (input.value.trim()) input.dispatchEvent(new Event('input'));
    });
  }

  function _fmtNavPrice(n) {
    var val = parseFloat(n);
    if (isNaN(val)) return '0.00';
    return val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  /* ===================== AUTH ===================== */
  function initAuth() {
    var token, userStr;
    try { token = sessionStorage.getItem('token') || localStorage.getItem('token'); } catch(e) {}
    try { userStr = sessionStorage.getItem('user') || localStorage.getItem('user'); } catch(e) {}
    var authButtons = document.getElementById('authButtons');
    var userInfo = document.getElementById('userInfo');

    if (token && userStr) {
      try {
        var user = JSON.parse(userStr);
        // Logged in: hide login button, show user info
        if (authButtons) authButtons.style.display = 'none';
        if (userInfo) {
          userInfo.style.display = 'flex';
          var balEl = document.getElementById('userBalance');
          var nameEl = document.getElementById('userName');
          if (balEl) balEl.textContent = '\u20B9' + _fmtNavPrice(user.virtual_balance || 0);
          if (nameEl) nameEl.textContent = user.full_name || (user.email ? user.email.split('@')[0] : 'User');
        }
      } catch (e) {
        // Parse error — treat as logged out
        if (authButtons) authButtons.style.display = 'flex';
        if (userInfo) userInfo.style.display = 'none';
      }
    } else {
      // Not logged in: show login button, hide user info
      if (authButtons) authButtons.style.display = 'flex';
      if (userInfo) userInfo.style.display = 'none';
    }
  }

  /* ===================== MOBILE MARKET-STATUS SLOT ===================== */
  // On pages with a #marketStatusSlot (currently home.html/index.html, between the header
  // and the index ticker strip), move the market-status pill there on mobile so it doesn't
  // crowd the header — and move it back into the header on desktop. Relocates the one real
  // element rather than duplicating it, so there's still only a single #marketStatusIndicator.
  function initMobileMarketStatusSlot() {
    if (!isDashboardPage()) return;
    var indicator = document.getElementById('marketStatusIndicator');
    var slot = document.getElementById('marketStatusSlot');
    var headerRow = document.querySelector('.header-bottom-row');
    var headerContainer = document.querySelector('.header-container');
    if (!indicator) return;

    if (!slot && headerContainer) {
      slot = document.createElement('div');
      slot.className = 'mobile-market-status-slot';
      slot.id = 'marketStatusSlot';
      headerContainer.appendChild(slot);
    }
    if (!slot) return;

    var isPermanent = slot.getAttribute('data-permanent') === 'true' || slot.classList.contains('permanent-slot');

    function place() {
      if (isPermanent || window.innerWidth <= 768) {
        slot.style.display = 'flex';
        if (indicator.parentNode !== slot) slot.appendChild(indicator);
      } else if (headerRow && indicator.parentNode !== headerRow) {
        slot.style.display = 'none';
        headerRow.appendChild(indicator);
      }
    }
    place();
    window.addEventListener('resize', place);
  }

  /* ===================== MARKET STATUS ===================== */
  function initMarketStatus() {
    if (!isDashboardPage()) return;
    var textEl = document.getElementById('marketStatusText');
    var dotEl = document.querySelector('.pulse-dot');
    if (!textEl || !dotEl) return;

    var now = new Date();
    var ist = new Date(now.getTime() + (3600000 * 5.5));
    var day = ist.getUTCDay();
    var hours = ist.getUTCHours();
    var minutes = ist.getUTCMinutes();
    var isWeekday = day >= 1 && day <= 5;
    var pastOpen = hours > 9 || (hours === 9 && minutes >= 15);
    var beforeClose = hours < 15 || (hours === 15 && minutes < 30);

    if (isWeekday && pastOpen && beforeClose) {
      textEl.innerText = 'Markets Open';
      dotEl.classList.remove('closed');
    } else {
      textEl.innerText = 'Markets Closed';
      dotEl.classList.add('closed');
    }
  }

  /* ===================== THEME TOGGLE ===================== */
  function initThemeToggle() {
    var btn = document.getElementById('themeToggle');
    if (!btn) return;
    btn.addEventListener('click', function () {
      document.body.classList.toggle('light-mode');
      if (btn.querySelector('svg')) {
        btn.innerHTML = '<span>\uD83C\uDF19</span>';
      } else {
        var isMoon = btn.textContent === '\uD83C\uDF19';
        btn.textContent = isMoon ? '\u2600\uFE0F' : '\uD83C\uDF19';
      }
    });
  }

  /* ===================== EXPOSE GLOBALS ===================== */
  window.toggleUserDropdown = function () {
    var dropdown = document.getElementById('userDropdownMenu');
    var container = document.querySelector('.user-dropdown');
    if (!dropdown || !container) return;
    dropdown.classList.toggle('show');
    container.classList.toggle('active');
  };

  window.handleLogout = function () {
    var token = sessionStorage.getItem('token') || localStorage.getItem('token');
    if (token) {
      fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' }
      }).catch(function () {});
    }
    sessionStorage.removeItem('token');
    sessionStorage.removeItem('user');
    try {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
    } catch(e) {}
    window.location.href = 'home.html';
  };

  document.addEventListener('click', function (e) {
    var container = document.querySelector('.user-dropdown');
    var dropdown = document.getElementById('userDropdownMenu');
    if (container && !container.contains(e.target) && dropdown && dropdown.classList.contains('show')) {
      dropdown.classList.remove('show');
      container.classList.remove('active');
    }
  });

  function initNotifications() {
    var btn = document.getElementById('notificationBtn');
    if (!btn || btn.dataset.init) return;
    btn.dataset.init = '1';

    var existing = document.getElementById('notificationPanel');
    if (existing) existing.remove();

    var panel = document.createElement('div');
    panel.id = 'notificationPanel';
    panel.className = 'app-notification-panel';
    panel.innerHTML = 
      '<div class="notif-header">' +
      '  <div class="notif-header-title">&#128276; Notifications <span class="notif-header-badge">3</span></div>' +
      '  <button type="button" class="notif-close-btn" id="notifCloseBtn" aria-label="Close">&times;</button>' +
      '</div>' +
      '<div class="notif-body">' +
      '  <div class="notif-item unread">' +
      '    <div class="notif-icon-circle">&#128200;</div>' +
      '    <div class="notif-content">' +
      '      <div class="notif-title">Market Live Tracking</div>' +
      '      <div class="notif-desc">NIFTY 50 and Bank Nifty active streaming.</div>' +
      '      <div class="notif-time">Just now</div>' +
      '    </div>' +
      '  </div>' +
      '  <div class="notif-item">' +
      '    <div class="notif-icon-circle">&#128176;</div>' +
      '    <div class="notif-content">' +
      '      <div class="notif-title">Virtual Trading Account</div>' +
      '      <div class="notif-desc">₹10,00,000 practice balance active.</div>' +
      '      <div class="notif-time">1 hr ago</div>' +
      '    </div>' +
      '  </div>' +
      '  <div class="notif-item">' +
      '    <div class="notif-icon-circle">&#9889;</div>' +
      '    <div class="notif-content">' +
      '      <div class="notif-title">Watchlist Alerts Ready</div>' +
      '      <div class="notif-desc">Configure price alerts and trigger conditions.</div>' +
      '      <div class="notif-time">Today</div>' +
      '    </div>' +
      '  </div>' +
      '</div>' +
      '<div class="notif-footer">' +
      '  <a href="watchlist.html" class="notif-footer-link">View Watchlist Alerts &rarr;</a>' +
      '</div>';

    document.body.appendChild(panel);

    function positionPanel() {
      if (window.innerWidth <= 640) {
        panel.style.top = '52px';
        panel.style.left = '10px';
        panel.style.right = '10px';
        panel.style.width = 'auto';
        panel.style.maxWidth = 'calc(100vw - 20px)';
      } else {
        var rect = btn.getBoundingClientRect();
        panel.style.top = (rect.bottom + 8) + 'px';
        panel.style.right = Math.max(12, window.innerWidth - rect.right) + 'px';
        panel.style.left = 'auto';
        panel.style.width = '330px';
        panel.style.maxWidth = '360px';
      }
    }

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var isVisible = panel.classList.contains('show');
      if (isVisible) {
        panel.classList.remove('show');
      } else {
        positionPanel();
        panel.classList.add('show');
      }
    });

    var closeBtn = panel.querySelector('#notifCloseBtn');
    if (closeBtn) {
      closeBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        panel.classList.remove('show');
      });
    }

    panel.addEventListener('click', function(e) {
      e.stopPropagation();
    });

    document.addEventListener('click', function (e) {
      if (!btn.contains(e.target) && !panel.contains(e.target)) {
        panel.classList.remove('show');
      }
    });

    window.addEventListener('resize', function() {
      if (panel.classList.contains('show')) positionPanel();
    });
  }

  /* ===================== INIT ===================== */
  function init() {
    var root = document.getElementById('nav-root');
    if (root) {
      root.innerHTML = buildHeaderHTML();
    }
    initSearch();
    initAuth();
    initMarketStatus();
    initThemeToggle();
    initNotifications();
    initMobileMarketStatusSlot();
    setTimeout(initAuth, 0);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  document.addEventListener('keydown', function(e) {
    if (e.key !== '/') return;
    var active = document.activeElement;
    if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return;
    var inp = document.getElementById('stockSearch');
    if (!inp) return;
    e.preventDefault();
    inp.focus();
    inp.select();
  });

})();


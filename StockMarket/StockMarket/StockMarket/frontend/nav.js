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

  function formatPrice(p) {
    if (p >= 10000) return p.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return p.toFixed(2);
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

    return '' +
      '<header class="header">' +
      '  <div class="header-container">' +
      '    <div class="header-top-row">' +
      '      <a href="home.html" class="logo-container">' +
      '        <img src="logo.jpeg" alt="Logo" class="logo-icon">' +
      '        <div class="brand-image-wrapper">' +
      '          <img src="leverage_logo.jpg" alt="LEVERAGE" class="brand-image">' +
      '        </div>' +
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
      '        <button class="nav-icon-btn" title="Toggle Theme" aria-label="Toggle theme" id="themeToggle">' +
      '          <svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>' +
      '        </button>' +
      '        <div class="auth-buttons" id="authButtons">' +
      '          <a href="login.html" class="auth-btn">Login / Register</a>' +
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
      '      <div class="market-status-indicator" id="marketStatusIndicator">' +
      '        <span class="pulse-dot"></span>' +
      '        <span class="status-text" id="marketStatusText">Loading...</span>' +
      '      </div>' +
      '    </div>' +
      '  </div>' +
      '</header>';
  }

  /* ===================== SEARCH ===================== */
  function initSearch() {
    var input = document.getElementById('stockSearch');
    var results = document.getElementById('searchResults');
    if (!input || !results) return;

    function sourceList() {
      return (typeof ALL_STOCKS !== 'undefined' && ALL_STOCKS.length > 0) ? ALL_STOCKS : SEARCH_FALLBACK;
    }

    function fuzzyMatch(text, query) {
      var t = 0, q = 0;
      text = text.toLowerCase();
      query = query.toLowerCase().replace(/\s+/g, '');
      while (t < text.length && q < query.length) {
        if (text[t] === query[q]) q++;
        t++;
      }
      return q === query.length;
    }

    function getScore(stock, query) {
      var cleanQuery = query.toLowerCase().replace(/\s+/g, '');
      var ticker = stock.ticker.toLowerCase().replace(/[^a-z0-9]/g, '');
      var name = stock.name.toLowerCase().replace(/[^a-z0-9]/g, '');
      if (ticker === cleanQuery) return 1000;
      if (ticker.startsWith(cleanQuery)) return 800;
      if (name.startsWith(cleanQuery)) return 600;
      if (stock.name.toLowerCase().includes(query.toLowerCase())) return 400;
      if (fuzzyMatch(stock.name, query) || fuzzyMatch(stock.ticker, query)) return 100;
      return 0;
    }

    input.addEventListener('input', function (e) {
      var query = e.target.value.trim().toLowerCase();
      if (!query) {
        results.classList.remove('visible');
        results.innerHTML = '';
        return;
      }

      var scored = sourceList().map(function (s) { return { stock: s, score: getScore(s, query) }; })
        .filter(function (item) { return item.score > 0; })
        .sort(function (a, b) { return b.score - a.score; })
        .slice(0, 5)
        .map(function (item) { return item.stock; });

      if (scored.length > 0) {
        results.innerHTML = scored.map(function (stock) {
          var type = stock.exchange || 'NSE';
          var displayTicker = stock.ticker;
          if (type === 'BSE' && displayTicker.indexOf('.BO') === -1) displayTicker += '.BO';
          else if (type === 'NSE' && displayTicker.indexOf('.NS') === -1 && type !== 'INDEX') displayTicker += '.NS';
          var targetPage = type === 'INDEX' ? 'overview.html?ticker=' : 'overview.html?ticker=';
          return '<div class="result-item" onclick="window.location.href=\'' + targetPage + encodeURIComponent(stock.ticker) + '\'">' +
            '<div class="result-info">' +
            '<div class="result-name">' + escapeHTML(stock.name) + '</div>' +
            '<div class="result-ticker">' + displayTicker + ' <span style="font-size:0.6rem;color:var(--text-muted)">' + type + '</span></div>' +
            '</div>' +
            '<button class="search-launch-btn">Overview</button>' +
            '</div>';
        }).join('');
        results.classList.add('visible');
      } else {
        results.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--text-muted)">No stocks found</div>';
        results.classList.add('visible');
      }
    });

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

  /* ===================== AUTH ===================== */
  function initAuth() {
    var token, userStr;
    try { token = sessionStorage.getItem('token'); } catch(e) {}
    try { userStr = sessionStorage.getItem('user'); } catch(e) {}
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
          if (balEl) balEl.textContent = '\u20B9' + formatPrice(user.virtual_balance || 0);
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
    var indicator = document.getElementById('marketStatusIndicator');
    var slot = document.getElementById('marketStatusSlot');
    var headerRow = document.querySelector('.header-bottom-row');
    if (!indicator || !slot || !headerRow) return;

    function place() {
      if (window.innerWidth <= 768) {
        if (indicator.parentNode !== slot) slot.appendChild(indicator);
      } else if (indicator.parentNode !== headerRow) {
        headerRow.appendChild(indicator);
      }
    }
    place();
    window.addEventListener('resize', place);
  }

  /* ===================== MARKET STATUS ===================== */
  function initMarketStatus() {
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
    var token = sessionStorage.getItem('token');
    if (token) {
      fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' }
      }).catch(function () {});
    }
    sessionStorage.removeItem('token');
    sessionStorage.removeItem('user');
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
    var panel = document.createElement('div');
    panel.id = 'notificationPanel';
    panel.style.cssText = 'display:none;position:absolute;top:100%;right:0;background:#15171a;border:1px solid rgba(255,255,255,0.08);border-radius:10px;min-width:280px;max-height:360px;overflow-y:auto;z-index:300;box-shadow:0 8px 32px rgba(0,0,0,0.5);padding:8px 0;';
    panel.innerHTML = '<div style="padding:12px 16px;font-size:0.85rem;font-weight:600;color:#fff;border-bottom:1px solid rgba(255,255,255,0.06);">Notifications</div>' +
      '<div style="padding:24px 16px;text-align:center;color:var(--text-secondary);font-size:0.8rem;">No new notifications</div>' +
      '<div style="padding:8px 16px;border-top:1px solid rgba(255,255,255,0.06);text-align:center;"><a href="settings.html" style="color:#4A90E2;font-size:0.8rem;text-decoration:none;">Notification Settings</a></div>';
    btn.style.position = 'relative';
    btn.appendChild(panel);
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
    });
    document.addEventListener('click', function (e) {
      if (!btn.contains(e.target)) panel.style.display = 'none';
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


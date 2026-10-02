    function escapeHTML(str) { var div = document.createElement('div'); div.appendChild(document.createTextNode(str)); return div.innerHTML; }
    // Auth Check for Private Chart Page — non-blocking; only redirect on trade actions
    function _requireAuth() {
      if (!sessionStorage.getItem('token')) {
        // Redirect to login with ?redirect= so user comes back after sign-in
        var redirect = encodeURIComponent(window.location.href);
        window.location.href = 'login.html?redirect=' + redirect;
        return false;
      }
      return true;
    }

    // Handle Trade Button click — show sign-in prompt OR trade modal
    function handleTradeClick(defaultType) {
      const token = sessionStorage.getItem('token');
      if (!token) {
        // Set the redirect URL in the sign-in modal link
        var redirect = encodeURIComponent(window.location.href);
        var btn = document.getElementById('signInRedirectBtn');
        if (btn) btn.href = 'login.html?redirect=' + redirect;
        var modal = document.getElementById('signInPromptModal');
        if (modal) modal.classList.add('visible');
      } else {
        showTradeModal(defaultType);
      }
    }
    window.handleTradeClick = handleTradeClick;

    // Logout Confirmation Logic
    function handleLogout() {
      // Show logout confirmation modal
      // This overrides the simple handleLogout in dashboard.js if called locally, 
      // BUT dashboard.js is loaded before this script block. 
      // If dashboard.js exposes handleLogout globally, this overwrite works.
      const modal = document.getElementById('logoutModal');
      if (modal) modal.classList.add('visible');
    }

    function hideLogoutConfirmation() {
      const modal = document.getElementById('logoutModal');
      if (modal) modal.classList.remove('visible');
    }

    function confirmLogout() {
      hideLogoutConfirmation();
      const loader = document.getElementById('logout-loader');
      if (loader) loader.style.display = 'flex';

      setTimeout(() => {
        sessionStorage.removeItem('token');
        sessionStorage.removeItem('user');
        window.location.href = 'index.html';
      }, 1500);
    }

    // CSRF Logic
    let csrfToken = '';
    async function fetchCSRF() {
      try {
        const res = await fetch('/api/csrf-token');
        if (res.ok) {
          const data = await res.json();
          csrfToken = data.csrf_token;
        }
      } catch (e) {
        console.error(e);
      }
    }
    fetchCSRF();

    // Delete Account Logic
    function showDeleteConfirmation() {
      var el = document.getElementById('deleteModal');
      if (el) el.classList.add('visible');
    }

    function hideDeleteConfirmation() {
      var el = document.getElementById('deleteModal');
      if (el) el.classList.remove('visible');
    }

    async function handleDeleteAccount() {
      const btn = document.getElementById('deleteConfirmBtn');
      const message = document.getElementById('deleteMessage');
      const token = sessionStorage.getItem('token');

      if (!token) return;

      btn.disabled = true;
      btn.innerHTML = 'Deleting...';
      message.innerHTML = '';

      try {
        const response = await fetch('/api/auth/me', {
          method: 'DELETE',
          headers: {
            'Authorization': `Bearer ${token}`,
            'X-CSRF-Token': csrfToken
          }
        });

        if (response.ok) {
          message.innerHTML = '<span style="color: #00C853">Account deleted successfully. Redirecting...</span>';
          sessionStorage.removeItem('token');
          sessionStorage.removeItem('user');
          setTimeout(() => {
            window.location.href = 'index.html';
          }, 1500);
        } else {
          const data = await response.json();
          message.innerHTML = '<span style="color: #ff4444">' + escapeHTML(data.detail || 'Failed to delete account') + '</span>';
          btn.disabled = false;
          btn.innerHTML = 'Yes, Delete My Account';
        }
      } catch (error) {
        console.error('Delete error:', error);
        message.innerHTML = '<span style="color: #ff4444">Connection error. Please try again.</span>';
        btn.disabled = false;
        btn.innerHTML = 'Yes, Delete My Account';
      }
    }

    // ==================== TRADING LOGIC ====================
    const API_BASE = '';
    let currentPositionType = 'LONG';
    let openPositions = [];
    window.positionsPanelClosedManually = false;

    function launchChart(ticker, exchange) {
      if (!ticker) return;
      window.location.href = 'overview.html?ticker=' + encodeURIComponent(ticker);
    }

    // Show Trade Modal
    async function showTradeModal(defaultType) {
      if (!_requireAuth()) return;
      const ticker = window.currentTicker || (new URLSearchParams(window.location.search).get('ticker') || '--');
      const price = parseFloat(document.getElementById('header-price')?.textContent?.replace(/[₹,]/g, '')) || 0;
      let user = JSON.parse(sessionStorage.getItem('user') || '{}');

      const tickerEl = document.getElementById('tradeModalTicker');
      if (tickerEl) tickerEl.textContent = ticker;
      const entryPriceEl = document.getElementById('tradeEntryPrice');
      if (entryPriceEl && price > 0) entryPriceEl.value = price.toFixed(2);
      const qtyEl = document.getElementById('tradeQuantity');
      if (qtyEl && !qtyEl.value) qtyEl.value = 1;
      const tpEl = document.getElementById('tradeTakeProfit');
      if (tpEl) tpEl.value = '';
      const slEl = document.getElementById('tradeStopLoss');
      if (slEl) slEl.value = '';
      const msgEl = document.getElementById('tradeMessage');
      if (msgEl) msgEl.innerHTML = '';

      // Fetch fresh balance from API if not in session or is 0
      if (!user.virtual_balance) {
        try {
          const token = sessionStorage.getItem('token');
          const res = await fetch(`${API_BASE}/api/portfolio/summary`, {
            headers: { 'Authorization': `Bearer ${token}` }
          });
          if (res.ok) {
            const data = await res.json();
            user.virtual_balance = data.current_balance;
            sessionStorage.setItem('user', JSON.stringify(user));
          }
        } catch (e) { console.error('Failed to fetch balance:', e); }
      }

      const balEl = document.getElementById('tradeAvailableBalance');
      if (balEl) balEl.textContent = `₹${(user.virtual_balance || 0).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;

      // Check Market Status (09:15 - 15:30 IST Mon-Fri)
      const now = new Date();
      // Calculate IST time
      const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
      const istTime = new Date(utc + (3600000 * 5.5));
      const day = istTime.getDay();
      const hours = istTime.getHours();
      const minutes = istTime.getMinutes();
      const totalMinutes = hours * 60 + minutes;
      const isMarketOpen = (day >= 1 && day <= 5) && (totalMinutes >= (9 * 60 + 15) && totalMinutes <= (15 * 60 + 30));

      const amoBanner = document.getElementById('amoBanner');
      const submitBtn = document.getElementById('tradeSubmitBtn');
      const nextOpenText = document.getElementById('marketClosedNextOpenText');

      if (!isMarketOpen) {
        // Calculate next trading day display
        let nextTradingDay = new Date(istTime);
        nextTradingDay.setDate(nextTradingDay.getDate() + 1);
        while (nextTradingDay.getDay() === 0 || nextTradingDay.getDay() === 6) {
          nextTradingDay.setDate(nextTradingDay.getDate() + 1);
        }
        const options = { weekday: 'long', day: 'numeric', month: 'short' };
        const dayStr = nextTradingDay.toLocaleDateString('en-IN', options);

        if (amoBanner) amoBanner.style.display = 'block';
        if (nextOpenText) nextOpenText.textContent = `Trading is strictly disabled outside NSE hours (09:15 - 15:30 IST). Market opens at 09:15 AM on ${dayStr}.`;
        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.style.opacity = '0.5';
          submitBtn.style.cursor = 'not-allowed';
          submitBtn.style.background = '#333';
          submitBtn.textContent = 'Market Closed';
        }
      } else {
        if (amoBanner) amoBanner.style.display = 'none';
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.style.opacity = '1';
          submitBtn.style.cursor = 'pointer';
          submitBtn.style.background = 'linear-gradient(135deg,#4A90E2,#7B61FF)';
          submitBtn.textContent = 'Place Order';
        }
      }

      let initialType = 'LONG';
      if (typeof defaultType === 'string') {
        const dUpper = defaultType.toUpperCase();
        if (dUpper === 'SHORT' || dUpper === 'SELL') initialType = 'SHORT';
        else initialType = 'LONG';
      }
      setPositionType(initialType);
      calculateTotal();
      const overlay = document.getElementById('tradePanelOverlay');
      if (overlay) overlay.classList.add('visible');
    }

    function hideTradeModal() {
      const overlay = document.getElementById('tradePanelOverlay');
      if (overlay) overlay.classList.remove('visible');
    }

    window.showTradeModal = showTradeModal;
    window.hideTradeModal = hideTradeModal;

    window.toggleTradeModal = function(defaultType) {
      const panel = document.getElementById('tradePanelOverlay');
      if (panel && panel.classList.contains('visible')) {
        hideTradeModal();
      } else {
        showTradeModal(defaultType);
      }
    };

    function setPositionType(type) {
      currentPositionType = type;
      const btnLong = document.getElementById('btnLong');
      const btnShort = document.getElementById('btnShort');

      if (type === 'LONG') {
        if (btnLong) {
          btnLong.style.background = 'rgba(74, 144, 226, 0.2)';
          btnLong.style.borderColor = '#4A90E2';
          btnLong.style.color = '#4A90E2';
        }
        if (btnShort) {
          btnShort.style.background = 'transparent';
          btnShort.style.borderColor = '#333';
          btnShort.style.color = '#888';
        }
      } else {
        if (btnLong) {
          btnLong.style.background = 'transparent';
          btnLong.style.borderColor = '#333';
          btnLong.style.color = '#888';
        }
        if (btnShort) {
          btnShort.style.background = 'rgba(74, 144, 226, 0.2)';
          btnShort.style.borderColor = '#4A90E2';
          btnShort.style.color = '#4A90E2';
        }
      }
    }
    window.setPositionType = setPositionType;

    function calculateTotal() {
      const price = parseFloat(document.getElementById('tradeEntryPrice').value) || 0;
      const qty = parseInt(document.getElementById('tradeQuantity').value, 10) || 0;
      const total = price * qty;
      document.getElementById('tradeTotalInvestment').textContent = `₹${total.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    }

    async function submitTrade() {
      const token = sessionStorage.getItem('token');
      if (!token) {
        if (typeof showAuthModal === 'function') showAuthModal('login');
        return;
      }

      if (window._isSubmittingTrade) return;
      window._isSubmittingTrade = true;

      const btn = document.getElementById('tradeSubmitBtn');
      const msg = document.getElementById('tradeMessage');
      if (btn) {
        btn.disabled = true;
        btn.style.opacity = '0.7';
        btn.style.cursor = 'not-allowed';
        btn.innerHTML = '<span style="display:inline-flex;align-items:center;justify-content:center;gap:8px;">'
          + '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" style="animation:spin 0.8s linear infinite;">'
          + '<path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>'
          + '</svg>Placing Order...</span>';
      }
      if (msg) msg.innerHTML = '';

      const payload = {
        ticker: document.getElementById('tradeModalTicker').textContent,
        position_type: currentPositionType,
        quantity: parseInt(document.getElementById('tradeQuantity').value, 10),
        entry_price: parseFloat(document.getElementById('tradeEntryPrice').value),
        take_profit: parseFloat(document.getElementById('tradeTakeProfit').value) || null,
        stop_loss: parseFloat(document.getElementById('tradeStopLoss').value) || null
      };

      try {
        const csrfRes = await fetch(`${API_BASE}/api/csrf-token`);
        if (!csrfRes.ok) throw new Error('CSRF token fetch failed');
        const csrfData = await csrfRes.json();

        const res = await fetch(`${API_BASE}/api/trade/place-order`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
            'X-CSRF-Token': csrfData.csrf_token
          },
          body: JSON.stringify(payload)
        });

        const data = await res.json();

        if (res.ok) {
          // Update balance in header and session
          if (data && data.balance != null) {
            const user = JSON.parse(sessionStorage.getItem('user') || '{}');
            user.virtual_balance = data.balance;
            sessionStorage.setItem('user', JSON.stringify(user));
            var ub = document.getElementById('userBalance');
            if (ub) ub.textContent = `₹${Number(data.balance).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
          }

          // Hide trade modal and show success animation
          hideTradeModal();
          const isAmo = data.is_amo === true;
          const title = isAmo ? 'AMO Order Queued (09:15 AM Market Open)' : 'Order Executed Successfully!';
          const detail = `${payload.position_type} | ${payload.quantity} Share${payload.quantity > 1 ? 's' : ''}`;
          showOrderSuccessModal(payload.ticker, detail, title);

          // RESET Manual closing flag so the panel opens
          window.positionsPanelClosedManually = false;

          const panel = document.getElementById('openPositionsPanel');
          if (panel) {
            panel.style.display = 'block';
            switchPositionsTab(isAmo ? 'queued' : 'open');
          }

          loadOpenPositions();
        } else {
          if (msg) msg.innerHTML = '<span style="color: #ef5350">' + escapeHTML(data.detail || 'Failed to place order') + '</span>';
        }
      } catch (e) {
        console.error(e);
        if (msg) msg.innerHTML = '<span style="color: #ef5350">Connection error</span>';
      } finally {
        window._isSubmittingTrade = false;
        if (btn) {
          btn.disabled = false;
          btn.style.opacity = '1';
          btn.style.cursor = 'pointer';
          btn.innerHTML = 'Place Order';
        }
      }
    }

    function showOrderSuccessModal(ticker, detail, title = 'Order Placed Successfully!') {
      const modal = document.getElementById('orderSuccessModal');
      document.getElementById('successTitle').textContent = title;
      document.getElementById('successTicker').textContent = ticker;
      if (detail) {
        document.getElementById('successDetails').textContent = detail;
        document.getElementById('successDetails').style.display = 'block';
      } else {
        document.getElementById('successDetails').style.display = 'none';
      }
      modal.classList.add('visible');
    }

    function hideOrderSuccessModal() {
      document.getElementById('orderSuccessModal').classList.remove('visible');
    }

    function formatFullDate(dateStr) {
      if (!dateStr) return '-';
      const date = new Date(dateStr);
      const day = String(date.getDate()).padStart(2, '0');
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const year = date.getFullYear();

      let hours = date.getHours();
      const minutes = String(date.getMinutes()).padStart(2, '0');
      const seconds = String(date.getSeconds()).padStart(2, '0');
      const ampm = hours >= 12 ? 'pm' : 'am';

      hours = hours % 12;
      hours = hours ? hours : 12;
      const strHours = String(hours).padStart(2, '0');

      return `${day}/${month}/${year}, ${strHours}:${minutes}:${seconds} ${ampm}`;
    }

    // Open Positions
    var _positionLivePrices = {};

    async function _fetchPositionLivePrices() {
      if (!window.openPositions || window.openPositions.length === 0) return;
      var tickers = window.openPositions.map(function(p){ return p.ticker; }).filter(function(t,i,s){ return t && s.indexOf(t)===i; });
      if (tickers.length === 0) return;
      try {
        var res = await fetch('/api/live-prices', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({tickers: tickers})
        });
        var data = await res.json();
        tickers.forEach(function(t){
          if (data[t]) _positionLivePrices[t] = data[t].current_price || data[t].current || 0;
        });
      } catch(e) {}
    }

    function _getLivePrice(ticker) {
      if (ticker === window.currentTicker) {
        var live = window.lastLivePrice || parseFloat(document.getElementById('header-price')?.textContent?.replace(/[₹,]/g, '')) || 0;
        if (live > 0) return live;
      }
      if (_positionLivePrices[ticker]) return _positionLivePrices[ticker];
      return 0;
    }

    let queuedOrders = [];
    window.queuedOrders = queuedOrders;

    async function loadOpenPositions() {
      const token = sessionStorage.getItem('token');
      if (!token) return;

      try {
        const posRes = await fetch(`${API_BASE}/api/portfolio/open-positions`, { headers: { 'Authorization': `Bearer ${token}` } });
        if (posRes.ok) {
          openPositions = await posRes.json();
          window.openPositions = openPositions;
          _fetchPositionLivePrices();
          renderOpenPositions();
        }
      } catch (e) {
        console.error('Failed to load positions:', e);
      }
    }

    if (!window._livePnLInterval) {
      window._livePnLInterval = setInterval(function() {
        _fetchPositionLivePrices();
        renderOpenPositions();
      }, 5000);
    }

    function renderOpenPositions() {
      const tbody = document.getElementById('openPositionsBody');
      const mobileList = document.getElementById('openPositionsMobileList');
      const countEl = document.getElementById('openPositionsCount');
      const headerCountEl = document.getElementById('headerPositionsCount');

      const totalItems = openPositions.length;
      if (countEl) countEl.textContent = `(${totalItems})`;
      if (headerCountEl) {
        headerCountEl.textContent = totalItems;
        headerCountEl.style.display = totalItems > 0 ? 'inline-block' : 'none';
      }

      if (totalItems === 0) {
        const emptyMsg = '<div style="text-align:center; padding:24px 12px; color:#a1a1aa; font-size:0.88rem;">No active open positions.</div>';
        if (tbody) tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:24px; color:#a1a1aa; font-size:0.9rem;">No active open positions.</td></tr>`;
        if (mobileList) mobileList.innerHTML = emptyMsg;
        return;
      }

      // Generate desktop table HTML
      if (tbody) {
        tbody.innerHTML = openPositions.map(pos => {
          var live = _getLivePrice(pos.ticker);
          let currentPrice = live > 0 ? live : (pos.current_price || pos.entry_price);

          const pnl = pos.position_type === 'LONG'
            ? (currentPrice - pos.entry_price) * pos.quantity
            : (pos.entry_price - currentPrice) * pos.quantity;

          const pnlColor  = pnl >= 0 ? '#089981' : '#FF5252';
          const pnlBg     = pnl >= 0 ? 'rgba(8, 153, 129,0.12)' : 'rgba(255,82,82,0.12)';
          const pnlBorder = pnl >= 0 ? 'rgba(8, 153, 129,0.25)' : 'rgba(255,82,82,0.25)';
          const pnlPrefix = pnl >= 0 ? '+' : '-';
          const isLong    = pos.position_type === 'LONG';
          const typeBg    = isLong ? 'rgba(0,200,83,0.12)' : 'rgba(255,23,68,0.12)';
          const typeColor = isLong ? '#089981' : '#FF5252';
          const typeLabel = isLong ? '▲ LONG' : '▼ SHORT';

          const tpStr = pos.take_profit != null ? `<span style="color:#089981; font-weight:600;">TP ₹${pos.take_profit.toFixed(2)}</span>` : '<span style="color:#555;">—</span>';
          const slStr = pos.stop_loss  != null ? `<span style="color:#FF5252; font-weight:600;">SL ₹${pos.stop_loss.toFixed(2)}</span>`  : '<span style="color:#555;">—</span>';

          const tickerDisplay = (pos.ticker && pos.ticker.trim() !== '--' && pos.ticker.trim() !== '—') ? pos.ticker.trim() : (window.currentTicker || 'STOCK');
          const nameDisplay   = pos.stock_name || '';
          const logoTicker = tickerDisplay.split('.')[0];

          return `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.04); transition:background 0.15s;" 
              onmouseover="this.style.background='rgba(255,255,255,0.03)'" 
              onmouseout="this.style.background='transparent'">
            <td style="padding:12px;">
              <div style="display:flex; align-items:center; gap:10px;">
                <img src="/logos/${logoTicker}.svg" style="width:28px;height:28px;border-radius:50%;background:#1e1e1e;object-fit:contain;padding:2px;" onerror="this.style.display='none'">
                <div>
                  <div style="font-weight:600; color:#fff; font-size:0.9rem;">${tickerDisplay}</div>
                  ${nameDisplay ? `<div style="font-size:0.75rem; color:#666; margin-top:1px;">${nameDisplay}</div>` : ''}
                </div>
              </div>
            </td>
            <td style="padding:12px;">
              <span style="background:${typeBg}; color:${typeColor}; font-size:0.75rem; font-weight:700; padding:4px 10px; border-radius:6px; letter-spacing:0.5px; white-space:nowrap;">${typeLabel}</span>
            </td>
            <td style="padding:12px; text-align:right; font-family:'Roboto Mono',monospace; font-weight:600; color:#d1d4dc;">${pos.quantity}</td>
            <td style="padding:12px; text-align:right; font-family:'Roboto Mono',monospace; color:#d1d4dc;">₹${pos.entry_price.toFixed(2)}</td>
            <td style="padding:12px; text-align:right; font-family:'Roboto Mono',monospace; color:#fff; font-weight:600;">₹${currentPrice.toFixed(2)}</td>
            <td style="padding:12px; text-align:right;">
              <span style="background:${pnlBg}; border:1px solid ${pnlBorder}; color:${pnlColor}; font-family:'Roboto Mono',monospace; font-weight:700; font-size:0.85rem; padding:4px 10px; border-radius:6px; white-space:nowrap;">
                ${pnlPrefix}₹${Math.abs(pnl).toFixed(2)}
              </span>
            </td>
            <td style="padding:12px; text-align:right;">
              <div style="display:flex; flex-direction:column; align-items:flex-end; gap:2px; font-size:0.78rem;">
                ${tpStr}${slStr}
              </div>
            </td>
            <td style="padding:12px; text-align:right;" onclick="event.stopPropagation()">
              <div style="display:flex; gap:6px; justify-content:flex-end; align-items:center;">
                <button onclick="showEditLimitsModal(${pos.id ?? ''}, ${pos.take_profit ?? 'null'}, ${pos.stop_loss ?? 'null'}, ${pos.tp_edit_count ?? 0}, ${pos.sl_edit_count ?? 0})"
                  style="background:rgba(74,144,226,0.1); border:1px solid rgba(74,144,226,0.3); color:#4A90E2; padding:6px 12px; border-radius:6px; cursor:pointer; font-size:0.78rem; font-weight:600; transition:all 0.2s; white-space:nowrap;"
                  onmouseover="this.style.background='rgba(74,144,226,0.2)'" onmouseout="this.style.background='rgba(74,144,226,0.1)'">
                  Edit
                </button>
                <button onclick="closePosition(${pos.id ?? ''})"
                  style="background:rgba(255,23,68,0.1); border:1px solid rgba(255,23,68,0.3); color:#FF5252; padding:6px 12px; border-radius:6px; cursor:pointer; font-size:0.78rem; font-weight:600; transition:all 0.2s; white-space:nowrap;"
                  onmouseover="this.style.background='rgba(255,23,68,0.2)'" onmouseout="this.style.background='rgba(255,23,68,0.1)'">
                  Close
                </button>
              </div>
            </td>
          </tr>`;
        }).join('');
      }

      // Generate mobile cards HTML
      if (mobileList) {
        mobileList.innerHTML = openPositions.map(pos => {
          var live = _getLivePrice(pos.ticker);
          let currentPrice = live > 0 ? live : (pos.current_price || pos.entry_price);

          const pnl = pos.position_type === 'LONG'
            ? (currentPrice - pos.entry_price) * pos.quantity
            : (pos.entry_price - currentPrice) * pos.quantity;

          const pnlColor  = pnl >= 0 ? '#089981' : '#FF5252';
          const pnlBg     = pnl >= 0 ? 'rgba(8, 153, 129,0.12)' : 'rgba(255,82,82,0.12)';
          const pnlBorder = pnl >= 0 ? 'rgba(8, 153, 129,0.25)' : 'rgba(255,82,82,0.25)';
          const pnlPrefix = pnl >= 0 ? '+' : '-';
          const isLong    = pos.position_type === 'LONG';
          const typeBg    = isLong ? 'rgba(0,200,83,0.12)' : 'rgba(255,23,68,0.12)';
          const typeColor = isLong ? '#089981' : '#FF5252';
          const typeLabel = isLong ? '▲ LONG' : '▼ SHORT';

          const tpSlText = (pos.take_profit || pos.stop_loss)
            ? `${pos.take_profit ? 'TP ₹' + pos.take_profit.toFixed(1) : ''}${pos.take_profit && pos.stop_loss ? ' ' : ''}${pos.stop_loss ? 'SL ₹' + pos.stop_loss.toFixed(1) : ''}`
            : '—';

          const tickerDisplay = (pos.ticker && pos.ticker.trim() !== '--' && pos.ticker.trim() !== '—') ? pos.ticker.trim() : (window.currentTicker || 'STOCK');
          const logoTicker = tickerDisplay.split('.')[0];

          return `
          <div class="pos-mobile-card">
            <!-- Header: Stock Info + Inline P&L -->
            <div class="pos-m-head">
              <div class="pos-m-stock">
                <img src="/logos/${logoTicker}.svg" class="pos-m-logo" onerror="this.style.display='none'">
                <span class="pos-m-ticker">${tickerDisplay}</span>
                <span class="pos-m-type" style="background:${typeBg}; color:${typeColor};">${typeLabel}</span>
              </div>
              <div class="pos-m-pnl" style="background:${pnlBg}; border:1px solid ${pnlBorder}; color:${pnlColor};">
                ${pnlPrefix}₹${Math.abs(pnl).toFixed(2)}
              </div>
            </div>

            <!-- Stats Strip: Qty, Entry, CMP, Limits -->
            <div class="pos-m-grid">
              <div class="pos-m-stat">
                <span class="pos-m-stat-lbl">Qty</span>
                <span class="pos-m-stat-val">${pos.quantity}</span>
              </div>
              <div class="pos-m-stat">
                <span class="pos-m-stat-lbl">Entry</span>
                <span class="pos-m-stat-val">₹${pos.entry_price.toFixed(1)}</span>
              </div>
              <div class="pos-m-stat">
                <span class="pos-m-stat-lbl">CMP</span>
                <span class="pos-m-stat-val" style="color:#fff; font-weight:700;">₹${currentPrice.toFixed(1)}</span>
              </div>
              <div class="pos-m-stat">
                <span class="pos-m-stat-lbl">TP / SL</span>
                <span class="pos-m-stat-val" style="color:${pos.take_profit ? '#089981' : (pos.stop_loss ? '#FF5252' : '#888')}; font-size:0.68rem;">${tpSlText}</span>
              </div>
            </div>

            <!-- Actions Bar -->
            <div class="pos-m-actions" onclick="event.stopPropagation()">
              <button class="pos-m-btn-edit" onclick="showEditLimitsModal(${pos.id ?? ''}, ${pos.take_profit ?? 'null'}, ${pos.stop_loss ?? 'null'}, ${pos.tp_edit_count ?? 0}, ${pos.sl_edit_count ?? 0})">
                <span>&#9998;</span> Edit TP/SL
              </button>
              <button class="pos-m-btn-close" onclick="closePosition(${pos.id ?? ''})">
                <span>&#10005;</span> Close
              </button>
            </div>
          </div>`;
        }).join('');
      }
    }

    let closedPositions = [];
    window.closedPositions = closedPositions;

    async function loadClosedPositions() {
      const token = sessionStorage.getItem('token');
      if (!token) return;

      try {
        const res = await fetch(`${API_BASE}/api/portfolio/closed-positions?limit=20`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.ok) {
          closedPositions = await res.json();
          window.closedPositions = closedPositions;
          renderClosedPositions();
        }
      } catch (e) {
        console.error('Failed to load closed positions:', e);
      }
    }

    window.loadClosedPositions = loadClosedPositions;
    window.switchPositionsTab = switchPositionsTab;
    window.renderClosedPositions = renderClosedPositions;
    window.togglePositionsPanel = togglePositionsPanel;

    function switchPositionsTab(tab) {
      const btnOpen = document.getElementById('tabPanelOpen');
      const btnHistory = document.getElementById('tabPanelHistory');
      const contOpen = document.getElementById('openPositionsContainer');
      const contHistory = document.getElementById('closedPositionsContainer');

      if (tab === 'open') {
        if (btnOpen) { btnOpen.style.background = '#4A90E2'; btnOpen.style.color = '#fff'; btnOpen.style.border = 'none'; }
        if (btnHistory) { btnHistory.style.background = 'rgba(255,255,255,0.06)'; btnHistory.style.color = '#a1a1aa'; btnHistory.style.border = '1px solid rgba(255,255,255,0.1)'; }
        if (contOpen) contOpen.style.display = 'block';
        if (contHistory) contHistory.style.display = 'none';
        loadOpenPositions();
      } else {
        if (btnHistory) { btnHistory.style.background = '#4A90E2'; btnHistory.style.color = '#fff'; btnHistory.style.border = 'none'; }
        if (btnOpen) { btnOpen.style.background = 'rgba(255,255,255,0.06)'; btnOpen.style.color = '#a1a1aa'; btnOpen.style.border = '1px solid rgba(255,255,255,0.1)'; }
        if (contOpen) contOpen.style.display = 'none';
        if (contHistory) contHistory.style.display = 'block';
        loadClosedPositions();
      }
    }

    function renderClosedPositions() {
      const tbody = document.getElementById('closedPositionsBody');
      const mobileList = document.getElementById('closedPositionsMobileList');
      const countEl = document.getElementById('closedPositionsCount');
      if (countEl) countEl.textContent = `(${closedPositions.length})`;

      if (closedPositions.length === 0) {
        const emptyMsg = '<div style="text-align:center; padding:24px 12px; color:#a1a1aa; font-size:0.88rem;">No closed trades yet.</div>';
        if (tbody) tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:24px; color:#a1a1aa; font-size:0.9rem;">No closed trades yet.</td></tr>`;
        if (mobileList) mobileList.innerHTML = emptyMsg;
        return;
      }

      if (tbody) {
        tbody.innerHTML = closedPositions.map(pos => {
          const isLong = pos.position_type === 'LONG';
          const typeBg = isLong ? 'rgba(0,200,83,0.12)' : 'rgba(255,23,68,0.12)';
          const typeColor = isLong ? '#089981' : '#FF5252';
          const typeLabel = isLong ? '▲ LONG' : '▼ SHORT';

          const pnl = pos.realized_pnl != null ? pos.realized_pnl : 0;
          const pnlColor = pnl >= 0 ? '#089981' : '#FF5252';
          const pnlBg = pnl >= 0 ? 'rgba(8, 153, 129,0.08)' : 'rgba(255,82,82,0.08)';
          const pnlBorder = pnl >= 0 ? 'rgba(8, 153, 129,0.25)' : 'rgba(255,82,82,0.25)';
          const pnlPrefix = pnl >= 0 ? '+' : '';

          // Badge styling for exit reason
          let reasonBadge = '';
          if (pos.exit_reason === 'TP_HIT') {
            reasonBadge = `<span style="background:rgba(8, 153, 129,0.12); border:1px solid rgba(8, 153, 129,0.3); color:#089981; font-size:0.72rem; font-weight:700; padding:3px 8px; border-radius:6px; white-space:nowrap;">🎯 TP Hit</span>`;
          } else if (pos.exit_reason === 'SL_HIT') {
            reasonBadge = `<span style="background:rgba(255,82,82,0.12); border:1px solid rgba(255,82,82,0.3); color:#FF5252; font-size:0.72rem; font-weight:700; padding:3px 8px; border-radius:6px; white-space:nowrap;">🛑 SL Hit</span>`;
          } else {
            reasonBadge = `<span style="background:rgba(74,144,226,0.12); border:1px solid rgba(74,144,226,0.3); color:#4A90E2; font-size:0.72rem; font-weight:700; padding:3px 8px; border-radius:6px; white-space:nowrap;">✋ Manual Close</span>`;
          }

          const tickerDisplay = (pos.ticker && pos.ticker.trim() !== '--' && pos.ticker.trim() !== '—') ? pos.ticker.trim() : (window.currentTicker || 'STOCK');
          const logoTicker = tickerDisplay.split('.')[0];
          const dateStr = pos.closed_at ? new Date(pos.closed_at).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' }) : '—';

          return `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.04); transition:background 0.15s;"
              onmouseover="this.style.background='rgba(255,255,255,0.03)'"
              onmouseout="this.style.background='transparent'">
            <td style="padding:12px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <img src="/logos/${logoTicker}.svg" style="width:24px;height:24px;border-radius:50%;background:#1e1e1e;object-fit:contain;padding:2px;" onerror="this.style.display='none'">
                <div>
                  <div style="font-weight:600; color:#fff; font-size:0.85rem;">${tickerDisplay}</div>
                  <div style="font-size:0.72rem; color:#666;">${pos.stock_name || ''}</div>
                </div>
              </div>
            </td>
            <td style="padding:12px;">
              <span style="background:${typeBg}; color:${typeColor}; font-size:0.72rem; font-weight:700; padding:3px 8px; border-radius:6px; letter-spacing:0.5px; white-space:nowrap;">${typeLabel}</span>
            </td>
            <td style="padding:12px; text-align:right; font-family:'Roboto Mono',monospace; font-weight:600; color:#d1d4dc;">${pos.quantity}</td>
            <td style="padding:12px; text-align:right; font-family:'Roboto Mono',monospace; color:#d1d4dc;">₹${pos.entry_price.toFixed(2)}</td>
            <td style="padding:12px; text-align:right; font-family:'Roboto Mono',monospace; color:#d1d4dc;">${pos.closing_price != null ? '₹' + pos.closing_price.toFixed(2) : '—'}</td>
            <td style="padding:12px; text-align:center;">${reasonBadge}</td>
            <td style="padding:12px; text-align:right;">
              <span style="font-family:'Roboto Mono',monospace; font-weight:700; font-size:0.85rem; color:${pnlColor}; background:${pnlBg}; border:1px solid ${pnlBorder}; padding:3px 8px; border-radius:6px;">
                ${pnlPrefix}₹${Math.abs(pnl).toFixed(2)}
              </span>
            </td>
            <td style="padding:12px; text-align:right; color:#888; font-size:0.75rem; white-space:nowrap;">${dateStr}</td>
          </tr>`;
        }).join('');
      }

      if (mobileList) {
        mobileList.innerHTML = closedPositions.map(pos => {
          const isLong = pos.position_type === 'LONG';
          const typeBg = isLong ? 'rgba(0,200,83,0.12)' : 'rgba(255,23,68,0.12)';
          const typeColor = isLong ? '#089981' : '#FF5252';
          const typeLabel = isLong ? '▲ LONG' : '▼ SHORT';

          const pnl = pos.realized_pnl != null ? pos.realized_pnl : 0;
          const pnlColor = pnl >= 0 ? '#089981' : '#FF5252';
          const pnlBg = pnl >= 0 ? 'rgba(8, 153, 129,0.12)' : 'rgba(255,82,82,0.12)';
          const pnlBorder = pnl >= 0 ? 'rgba(8, 153, 129,0.25)' : 'rgba(255,82,82,0.25)';
          const pnlPrefix = pnl >= 0 ? '+' : '';

          let reasonBadge = '';
          if (pos.exit_reason === 'TP_HIT') {
            reasonBadge = `<span style="background:rgba(8, 153, 129,0.12); border:1px solid rgba(8, 153, 129,0.3); color:#089981; font-size:0.62rem; font-weight:700; padding:1px 4px; border-radius:3px; white-space:nowrap;">🎯 TP</span>`;
          } else if (pos.exit_reason === 'SL_HIT') {
            reasonBadge = `<span style="background:rgba(255,82,82,0.12); border:1px solid rgba(255,82,82,0.3); color:#FF5252; font-size:0.62rem; font-weight:700; padding:1px 4px; border-radius:3px; white-space:nowrap;">🛑 SL</span>`;
          } else {
            reasonBadge = `<span style="background:rgba(74,144,226,0.12); border:1px solid rgba(74,144,226,0.3); color:#4A90E2; font-size:0.62rem; font-weight:700; padding:1px 4px; border-radius:3px; white-space:nowrap;">✋ Manual</span>`;
          }

          const tickerDisplay = (pos.ticker && pos.ticker.trim() !== '--' && pos.ticker.trim() !== '—') ? pos.ticker.trim() : (window.currentTicker || 'STOCK');
          const logoTicker = tickerDisplay.split('.')[0];
          const dateStr = pos.closed_at ? new Date(pos.closed_at).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' }) : '—';

          return `
          <div class="pos-mobile-card">
            <!-- Header -->
            <div class="pos-m-head">
              <div class="pos-m-stock">
                <img src="/logos/${logoTicker}.svg" class="pos-m-logo" onerror="this.style.display='none'">
                <span class="pos-m-ticker">${tickerDisplay}</span>
                <span class="pos-m-type" style="background:${typeBg}; color:${typeColor};">${typeLabel}</span>
                ${reasonBadge}
              </div>
              <div class="pos-m-pnl" style="background:${pnlBg}; border:1px solid ${pnlBorder}; color:${pnlColor};">
                ${pnlPrefix}₹${Math.abs(pnl).toFixed(2)}
              </div>
            </div>

            <!-- Stats Strip: Qty, Entry, Exit, Date -->
            <div class="pos-m-grid">
              <div class="pos-m-stat">
                <span class="pos-m-stat-lbl">Qty</span>
                <span class="pos-m-stat-val">${pos.quantity}</span>
              </div>
              <div class="pos-m-stat">
                <span class="pos-m-stat-lbl">Entry</span>
                <span class="pos-m-stat-val">₹${pos.entry_price.toFixed(1)}</span>
              </div>
              <div class="pos-m-stat">
                <span class="pos-m-stat-lbl">Exit</span>
                <span class="pos-m-stat-val" style="color:#fff; font-weight:700;">${pos.closing_price != null ? '₹' + pos.closing_price.toFixed(1) : '—'}</span>
              </div>
              <div class="pos-m-stat">
                <span class="pos-m-stat-lbl">Closed</span>
                <span class="pos-m-stat-val" style="color:#a1a1aa; font-size:0.68rem;">${dateStr}</span>
              </div>
            </div>
          </div>`;
        }).join('');
      }
    }

    function togglePositionsPanel() {
      const panel = document.getElementById('openPositionsPanel');
      if (!panel) return;
      const isHidden = (panel.style.display === 'none' || !panel.style.display);
      panel.style.display = isHidden ? 'block' : 'none';

      // Update manual state
      window.positionsPanelClosedManually = !isHidden;

      // If opening, ensure fresh positions are loaded & rendered
      if (isHidden) {
        loadOpenPositions();
        loadClosedPositions();
      }

      // Smooth chart resize & reposition
      requestAnimationFrame(function() {
        const chartParent = document.getElementById('chart-container');
        if (chartParent && window.bigChart) {
          const h = chartParent.offsetHeight || 350;
          const w = chartParent.clientWidth || 800;
          window._chartLastSize = { height: h, width: w };
          window.bigChart.applyOptions({ height: h, width: w });
        }
        window.dispatchEvent(new Event('resize'));
      });
    }

    function switchPanelTab(tab) {
      // Update tab buttons safely
      var tOpen = document.getElementById('tabPanelOpen');
      if (tOpen) {
        tOpen.style.background = tab === 'open' ? '#4A90E2' : '#333';
        tOpen.style.color = tab === 'open' ? '#fff' : '#888';
      }
      var tClosed = document.getElementById('tabPanelClosed');
      if (tClosed) {
        tClosed.style.background = tab === 'closed' ? '#4A90E2' : '#333';
        tClosed.style.color = tab === 'closed' ? '#fff' : '#888';
      }
      var tTrans = document.getElementById('tabPanelTransactions');
      if (tTrans) {
        tTrans.style.background = tab === 'transactions' ? '#4A90E2' : '#333';
        tTrans.style.color = tab === 'transactions' ? '#fff' : '#888';
      }

      // Show/hide content
      var cOpen = document.getElementById('panelOpenContent');
      if (cOpen) cOpen.style.display = tab === 'open' ? 'block' : 'none';
      var cClosed = document.getElementById('panelClosedContent');
      if (cClosed) cClosed.style.display = tab === 'closed' ? 'block' : 'none';
      var cTrans = document.getElementById('panelTransactionsContent');
      if (cTrans) cTrans.style.display = tab === 'transactions' ? 'block' : 'none';

      // Load data if needed
      if (tab === 'closed') loadPanelClosedPositions();
      if (tab === 'transactions') loadPanelTransactions();
    }

    async function loadPanelClosedPositions() {
      const token = sessionStorage.getItem('token');
      if (!token) return;

      try {
        const res = await fetch(`${API_BASE}/api/portfolio/closed-positions`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });

        if (res.ok) {
          const data = await res.json();
          const positions = Array.isArray(data) ? data : (data.positions || []);
          const tbody = document.getElementById('panelClosedBody');

          if (positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="padding: 20px; text-align: center; color: #666;">No closed positions</td></tr>';
            return;
          }

          tbody.innerHTML = positions.map(pos => {
            const pnl = pos.realized_pnl || 0;
            const pnlColor = pnl >= 0 ? '#26a69a' : '#ef5350';
            const date = formatFullDate(pos.closed_at);

            return `<tr>
              <td class="ticker-cell">
                ${pos.ticker && pos.ticker.trim() !== '--' && pos.ticker.trim() !== '—' ? '<img src="/logos/' + pos.ticker.trim().split('.')[0] + '.svg" class="stock-logo" onerror="this.style.display=\'none\'">' : ''}
                ${pos.ticker || '--'}
              </td>
              <td><span class="badge" style="color: #fff; background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 700;">${pos.position_type}</span></td>
              <td class="mono">₹${pos.entry_price.toFixed(2)}</td>
              <td class="mono">₹${pos.closing_price.toFixed(2)}</td>
              <td class="mono" style="color: ${pnlColor}; font-weight: 700;">${pnl >= 0 ? '+' : ''}₹${Math.abs(pnl).toFixed(2)}</td>
              <td><span style="background: rgba(255,255,255,0.1); padding: 2px 8px; border-radius: 4px; font-size: 11px; color: #fff; font-weight: 700;">${pos.close_type}</span></td>
              <td style="font-size: 12px; color: #fff; font-weight: 700;">${date}</td>
            </tr>`;
          }).join('');
        }
      } catch (e) {
        console.error('Failed to load closed positions:', e);
      }
    }

    async function loadPanelTransactions() {
      const token = sessionStorage.getItem('token');
      if (!token) return;

      try {
        const res = await fetch(`${API_BASE}/api/portfolio/transactions`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });

        if (res.ok) {
          const data = await res.json();
          const tbody = document.getElementById('panelTransactionsBody');
          const tfoot = document.getElementById('panelTransactionsFoot');

          if (!data.transactions || data.transactions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="padding: 20px; text-align: center; color: #666;">No transactions</td></tr>';
            if (tfoot) tfoot.innerHTML = '';
            return;
          }

          var _txPage = 0;
          var _txPageSize = 20;
          function renderTransactions(){
            var start = 0;
            var end = (_txPage + 1) * _txPageSize;
            var slice = data.transactions.slice(0, end);
            tbody.innerHTML = slice.map(txn => {
              const date = formatFullDate(txn.created_at);
              const pnl = txn.pnl || 0;
              const pnlColor = pnl >= 0 ? '#26a69a' : '#ef5350';
              const amountColor = txn.amount >= 0 ? '#26a69a' : '#ef5350';

              let statusName = txn.transaction_type.replace('_EXECUTED', '').replace('MANUAL', 'CLOSE');
              if (txn.transaction_type.includes('CLOSE')) statusName = 'CLOSE';
              if (txn.transaction_type === 'OPEN') statusName = 'OPEN';

              return `<tr>
                <td style="font-size: 12px; color: #fff; font-weight: 700;">${date}</td>
                <td><span class="badge" style="background: rgba(255,255,255,0.1); padding: 2px 8px; border-radius: 4px; font-size: 11px; color: #fff; font-weight: 700;">${statusName}</span></td>
                <td>${txn.position_type ? `<span class="badge" style="color: #fff; background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 700;">${txn.position_type}</span>` : '-'}</td>
                <td class="ticker-cell" style="font-weight: 700;">
                  ${txn.ticker && txn.ticker !== '--' && txn.ticker.trim() ? `<img src="/logos/${txn.ticker.split('.')[0]}.svg" class="stock-logo" onerror="this.style.display='none'">${txn.ticker}` : '-'}
                </td>
                <td class="mono" style="color: ${amountColor};">₹${Math.abs(txn.amount).toFixed(2)}</td>
                <td class="mono" style="color: ${pnlColor};">${pnl ? (pnl >= 0 ? '+' : '-') + '₹' + Math.abs(pnl).toFixed(2) : '-'}</td>
                <td class="mono" style="color: #fff;">₹${txn.balance_after.toFixed(2)}</td>
              </tr>`;
            }).join('');
            if (tfoot) {
              if (end < data.transactions.length) {
                tfoot.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:10px;"><button class="btn-secondary" onclick="_txPage++;renderTransactions();">Load More (' + (data.transactions.length - end) + ' remaining)</button></td></tr>';
              } else {
                tfoot.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:10px;color:#a1a1aa;font-size:0.8rem;">Showing all ' + data.transactions.length + ' transactions</td></tr>';
              }
            }
          }
          renderTransactions();
        }
      } catch (e) {
        console.error('Failed to load transactions:', e);
      }
    }

    async function closePosition(positionId) {
      if (positionId == null || positionId === '') return;
      const pos = openPositions.find(p => p.id === positionId);
      if (!pos) return;

      var closePosId = document.getElementById('closePositionId'); if (closePosId) closePosId.value = positionId;

      var closeTicker = document.getElementById('closeDetailTicker'); if (closeTicker) closeTicker.innerText = pos.ticker;
      var closeQty = document.getElementById('closeDetailQty'); if (closeQty) closeQty.innerText = pos.quantity;
      var closeEntry = document.getElementById('closeDetailEntry'); if (closeEntry) closeEntry.innerText = '₹' + pos.entry_price.toFixed(2);
      var closeTP = document.getElementById('closeDetailTP'); if (closeTP) closeTP.innerText = pos.take_profit ? '₹' + pos.take_profit.toFixed(2) : '-';
      var closeSL = document.getElementById('closeDetailSL'); if (closeSL) closeSL.innerText = pos.stop_loss ? '₹' + pos.stop_loss.toFixed(2) : '-';

      // Set Loading State for Price/PnL
      document.getElementById('closeDetailExit').innerText = 'Fetching...';
      const pnlEl = document.getElementById('closeDetailPnL');
      pnlEl.innerText = 'Calculating...';
      pnlEl.style.color = '#888';

      document.getElementById('closePositionModal').style.display = 'flex';

      // Calculate or Fetch Current/Exit Price
      let currentPrice = 0;
      if (pos.ticker === window.currentTicker) {
        currentPrice = window.lastLivePrice || parseFloat(document.getElementById('header-price')?.textContent?.replace(/[₹,]/g, '')) || 0;
      }

      // If we don't have a reliable price (wrong ticker or no websocket data), fetch it
      if (!currentPrice || currentPrice <= 0) {
        try {
          const res = await fetch(`${API_BASE}/api/live-prices`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tickers: [pos.ticker] })
          });
          if (res.ok) {
            const data = await res.json();
            if (data[pos.ticker]) {
              currentPrice = data[pos.ticker].current || data[pos.ticker];
            }
          }
        } catch (e) {
          console.error('Failed to fetch live price for modal:', e);
        }
      }

      if (currentPrice > 0) {
        document.getElementById('closeDetailExit').innerText = `₹${currentPrice.toFixed(2)}`;
        document.getElementById('closeDetailPnL').style.display = 'block';

        const pnl = pos.position_type === 'LONG'
          ? (currentPrice - pos.entry_price) * pos.quantity
          : (pos.entry_price - currentPrice) * pos.quantity;

        pnlEl.innerText = `${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(2)}`;
        pnlEl.style.color = pnl >= 0 ? '#26a69a' : '#ef5350';
      } else {
        document.getElementById('closeDetailExit').innerText = 'Price Unavailable';
        pnlEl.innerText = 'N/A';
      }

      // Check Market Status (09:15 - 15:30 IST Mon-Fri)
      const now = new Date();
      const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
      const istTime = new Date(utc + (3600000 * 5.5));
      const day = istTime.getDay();
      const hours = istTime.getHours();
      const minutes = istTime.getMinutes();
      const totalMinutes = hours * 60 + minutes;
      const isMarketOpen = (day >= 1 && day <= 5) && (totalMinutes >= (9 * 60 + 15) && totalMinutes <= (15 * 60 + 30));
      const confirmBtn = document.getElementById('btnConfirmClose');
      const closeMsg = document.getElementById('closePositionMessage');

      if (!isMarketOpen) {
        let nextTradingDay = new Date(istTime);
        nextTradingDay.setDate(nextTradingDay.getDate() + 1);
        while (nextTradingDay.getDay() === 0 || nextTradingDay.getDay() === 6) {
          nextTradingDay.setDate(nextTradingDay.getDate() + 1);
        }
        const options = { weekday: 'long', day: 'numeric', month: 'short' };
        const dayStr = nextTradingDay.toLocaleDateString('en-IN', options);

        closeMsg.innerHTML = `<div style="color: #ff8a80; background: rgba(255,82,82,0.12); padding: 8px 12px; border-radius: 6px; border: 1px solid rgba(255,82,82,0.3); font-size: 12px; line-height: 1.4; margin-top: 10px;">
          ⛔ <strong>Market is Closed</strong><br/>Positions cannot be closed now. Square-off resumes at 09:15 AM on ${dayStr}.
        </div>`;
        if (confirmBtn) {
          confirmBtn.disabled = true;
          confirmBtn.style.opacity = '0.5';
          confirmBtn.style.cursor = 'not-allowed';
        }
      } else {
        closeMsg.innerHTML = '';
        if (confirmBtn) {
          confirmBtn.disabled = false;
          confirmBtn.style.opacity = '1';
          confirmBtn.style.cursor = 'pointer';
        }
      }
    }

    function hideClosePositionModal() {
      document.getElementById('closePositionModal').style.display = 'none';
      document.getElementById('closePositionMessage').innerHTML = '';
    }

    async function confirmClosePosition() {
      // Guard: prevent double-click while request is in flight
      if (window._isClosingPosition) return;

      const btn = document.getElementById('btnConfirmClose');
      const positionId = parseInt(document.getElementById('closePositionId').value, 10);
      const token = sessionStorage.getItem('token');
      const msgEl = document.getElementById('closePositionMessage');

      const position = openPositions.find(p => p.id === positionId);
      if (!position) return;

      let closingPrice = 0;

      // 1. Check if it's the current chart's ticker
      if (position.ticker === window.currentTicker) {
        closingPrice = window.lastLivePrice || parseFloat(document.getElementById('header-price')?.textContent?.replace(/[₹,]/g, '')) || 0;
      }

      // 2. Fallback: Fetch specific price if current chart is different or price missing
      if (!closingPrice || closingPrice <= 0) {
        msgEl.innerHTML = '<div style="color: #4A90E2; font-size: 13px;">Refreshing live price...</div>';
        try {
          const priceRes = await fetch(`${API_BASE}/api/live-prices`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tickers: [position.ticker] })
          });
          if (priceRes.ok) {
            const priceData = await priceRes.json();
            closingPrice = priceData[position.ticker]?.current;
          }
        } catch (e) {
          console.error('Final price fetch failed:', e);
        }
      }

      if (!closingPrice || closingPrice <= 0) {
        msgEl.innerHTML = '<div style="color: #ef5350; font-size: 13px;">Unable to determine closing price. Please try again.</div>';
        return;
      }

      // Lock the UI — show loading spinner inside button
      window._isClosingPosition = true;
      if (btn) {
        btn.disabled = true;
        btn.style.opacity = '0.7';
        btn.style.cursor = 'not-allowed';
        btn.innerHTML = '<span style="display:inline-flex;align-items:center;gap:8px;">'
          + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" style="animation:spin 0.8s linear infinite;">'
          + '<path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>'
          + '</svg>Closing...</span>';
      }
      msgEl.innerHTML = '';

      try {
        // Fetch CSRF token — if it fails, proceed without it (endpoint may not require it)
        let csrfToken = '';
        try {
          const csrfRes = await fetch(`${API_BASE}/api/csrf-token`, { credentials: 'include' });
          if (csrfRes.ok) {
            const csrfData = await csrfRes.json();
            csrfToken = csrfData.csrf_token || '';
          }
        } catch (csrfErr) {
          console.warn('CSRF fetch failed, proceeding without:', csrfErr);
        }

        const headers = {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        };
        if (csrfToken) headers['X-CSRF-Token'] = csrfToken;

        const res = await fetch(`${API_BASE}/api/trade/close-position`, {
          method: 'POST',
          headers: headers,
          credentials: 'include',
          body: JSON.stringify({ position_id: positionId, closing_price: closingPrice })
        });

        let data;
        try {
          data = await res.json();
        } catch (parseErr) {
          throw new Error('Server returned an invalid response (status ' + res.status + ')');
        }

        if (res.ok) {
          // Update balance safely
          if (data && data.balance != null) {
            const user = JSON.parse(sessionStorage.getItem('user') || '{}');
            user.virtual_balance = data.balance;
            sessionStorage.setItem('user', JSON.stringify(user));
            var ub = document.getElementById('userBalance');
            if (ub) ub.textContent = `₹${Number(data.balance).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
          }

          // Hide close confirmation modal
          hideClosePositionModal();

          // Show success modal with P&L
          const pnl = (data && data.realized_pnl != null) ? data.realized_pnl : 0;
          const pnlColor = pnl >= 0 ? '#26a69a' : '#ef5350';
          const pnlSign = pnl >= 0 ? '+' : '';
          var pnlEl = document.getElementById('closedPnlAmount');
          if (pnlEl) {
            pnlEl.textContent = `${pnlSign}₹${Number(pnl).toFixed(2)}`;
            pnlEl.style.color = pnlColor;
          }
          var succModal = document.getElementById('positionClosedSuccessModal');
          if (succModal) succModal.style.display = 'flex';

          // Reload positions
          loadOpenPositions();
        } else {
          // Show the actual backend error message
          const detail = data.detail || data.message || ('Server error: ' + res.status);
          msgEl.innerHTML = '<div style="color: #ef5350; font-size: 13px;">' + escapeHTML(detail) + '</div>';
        }
      } catch (e) {
        console.error('Close position error:', e);
        msgEl.innerHTML = '<div style="color: #ef5350; font-size: 13px;">' + escapeHTML(e.message || 'Network error. Please try again.') + '</div>';
      } finally {
        // Always unlock so user can retry if needed
        window._isClosingPosition = false;
        if (btn) {
          btn.disabled = false;
          btn.style.opacity = '1';
          btn.style.cursor = 'pointer';
          btn.innerHTML = 'Yes, Close';
        }
      }
    }

    function hidePositionClosedSuccessModal() {
      document.getElementById('positionClosedSuccessModal').style.display = 'none';
    }

    // Edit TP/SL
    function showEditLimitsModal(positionId, tp, sl, tpCount, slCount) {
      var pos = openPositions.find(function (p) { return p.id === positionId; });
      if (!pos) return;

      // Check Market Status (09:15 - 15:30 IST Mon-Fri)
      const now = new Date();
      const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
      const istTime = new Date(utc + (3600000 * 5.5));
      const day = istTime.getDay();
      const hours = istTime.getHours();
      const minutes = istTime.getMinutes();
      const totalMinutes = hours * 60 + minutes;
      const isMarketOpen = (day >= 1 && day <= 5) && (totalMinutes >= (9 * 60 + 15) && totalMinutes <= (15 * 60 + 30));

      document.getElementById('editPositionId').value = positionId;
      const tpInput = document.getElementById('editTakeProfit');
      const slInput = document.getElementById('editStopLoss');
      tpInput.value = tp || '';
      slInput.value = sl || '';
      // Store original values for validation
      window.originalTP = tp || null;
      window.originalSL = sl || null;
      document.getElementById('editTPCount').textContent = `${tpCount}/3`;
      document.getElementById('editSLCount').textContent = `${slCount}/3`;
      const msg = document.getElementById('editLimitsMessage');
      const submitBtn = document.getElementById('editLimitsModal').querySelector('button:last-of-type');

      if (!isMarketOpen) {
        let nextTradingDay = new Date(istTime);
        nextTradingDay.setDate(nextTradingDay.getDate() + 1);
        while (nextTradingDay.getDay() === 0 || nextTradingDay.getDay() === 6) {
          nextTradingDay.setDate(nextTradingDay.getDate() + 1);
        }
        const options = { weekday: 'long', day: 'numeric', month: 'short' };
        const dayStr = nextTradingDay.toLocaleDateString('en-IN', options);

        msg.innerHTML = `<div style="color: #ff8a80; background: rgba(255,82,82,0.12); padding: 8px 12px; border-radius: 6px; border: 1px solid rgba(255,82,82,0.3); font-size: 12px; line-height: 1.4;">
          ⛔ <strong>Market is Closed</strong><br/>Take Profit & Stop Loss cannot be modified now. Modifications resume at 09:15 AM on ${dayStr}.
        </div>`;
        tpInput.disabled = true;
        slInput.disabled = true;
        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.style.opacity = '0.5';
          submitBtn.style.cursor = 'not-allowed';
        }
      } else {
        msg.innerHTML = '';
        tpInput.disabled = false;
        slInput.disabled = false;
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.style.opacity = '1';
          submitBtn.style.cursor = 'pointer';
        }
      }

      document.getElementById('editLimitsModal').classList.add('visible');
    }

    function hideEditLimitsModal() {
      document.getElementById('editLimitsModal').classList.remove('visible');
    }

    async function submitEditLimits() {
      const btn = document.getElementById('editLimitsModal').querySelector('button:last-of-type');
      const token = sessionStorage.getItem('token');
      const positionId = parseInt(document.getElementById('editPositionId').value, 10);
      if (!positionId) return;
      const tp = parseFloat(document.getElementById('editTakeProfit').value) || null;
      const sl = parseFloat(document.getElementById('editStopLoss').value) || null;
      const msg = document.getElementById('editLimitsMessage');

      // Check if both fields are empty
      if (tp === null && sl === null) {
        msg.innerHTML = '<span style="color: #ef5350">Enter a value</span>';
        return;
      }

      // Check if values actually changed
      if (tp === window.originalTP && sl === window.originalSL) {
        msg.innerHTML = '<span style="color: #ef5350">This TP and SL are already there</span>';
        return;
      }

      try {
        if (btn) btn.disabled = true;
        const csrfRes = await fetch(`${API_BASE}/api/csrf-token`);
        const csrfData = await csrfRes.json();
        const res = await fetch(`${API_BASE}/api/trade/set-limits`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
            'X-CSRF-Token': csrfData.csrf_token
          },
          body: JSON.stringify({ position_id: parseInt(positionId, 10), take_profit: tp, stop_loss: sl })
        });

        const data = await res.json();

        if (res.ok) {
          hideEditLimitsModal();
          const ticker = window.currentTicker || '--';
          const detail = `TP: ${tp || '-'} | SL: ${sl || '-'}`;
          showOrderSuccessModal(ticker, detail, 'Order Updated Successfully!');
          loadOpenPositions();
        } else {
          msg.innerHTML = `<span style="color: #ef5350">${data.detail}</span>`;
        }
        if (btn) btn.disabled = false;
      } catch (e) {
        msg.innerHTML = '<span style="color: #ef5350">Connection error</span>';
        if (btn) btn.disabled = false;
      }
    }

    // Portfolio Modal
    async function showPortfolioModal() {
      document.getElementById('portfolioModal').classList.add('visible');
      await loadPortfolioSummary();
      showPortfolioTab('open'); // Default to open
    }

    function hidePortfolioModal() {
      document.getElementById('portfolioModal').classList.remove('visible');
    }

    function showPortfolioTab(tab) {
      document.getElementById('openPositionsTab').style.display = tab === 'open' ? 'block' : 'none';
      document.getElementById('closedPositionsTab').style.display = tab === 'closed' ? 'block' : 'none';
      document.getElementById('transactionsTab').style.display = tab === 'transactions' ? 'block' : 'none';

      const tabOpen = document.getElementById('tabOpen');
      const tabClosed = document.getElementById('tabClosed');
      const tabTxn = document.getElementById('tabTransactions');

      tabOpen.style.background = tab === 'open' ? '#4A90E2' : '#333';
      tabOpen.style.color = tab === 'open' ? '#fff' : '#888';

      tabClosed.style.background = tab === 'closed' ? '#4A90E2' : '#333';
      tabClosed.style.color = tab === 'closed' ? '#fff' : '#888';

      tabTxn.style.background = tab === 'transactions' ? '#4A90E2' : '#333';
      tabTxn.style.color = tab === 'transactions' ? '#fff' : '#888';

      if (tab === 'open') renderPortfolioOpenPositions();
      if (tab === 'closed') loadClosedPositions();
      if (tab === 'transactions') loadTransactions();
    }

    function renderPortfolioOpenPositions() {
      const tbody = document.getElementById('portfolioOpenPositionsBody');
      // Use global openPositions if available
      const positions = window.openPositions || [];

      if (positions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #666;">No open positions</td></tr>';
        return;
      }

      // Draw lines on chart
      if (typeof drawTradeLines === 'function') drawTradeLines(positions);

      tbody.innerHTML = positions.map(pos => {
        var live = _getLivePrice(pos.ticker);
        let currentPrice = live > 0 ? live : (pos.current_price || pos.entry_price);
        // Simple P&L calc (might be slightly off if not real-time tick, but good enough for modal)
        const pnl = pos.position_type === 'LONG'
          ? (currentPrice - pos.entry_price) * pos.quantity
          : (pos.entry_price - currentPrice) * pos.quantity;

        const pnlColor = pnl >= 0 ? '#26a69a' : '#ef5350';
        const typeColor = pos.position_type === 'LONG' ? '#26a69a' : '#ef5350';

        return `<tr style="border-bottom: 1px solid #222;">
              <td style="padding: 10px; color: #fff; font-weight: 600;">${pos.ticker}</td>
              <td style="padding: 10px; text-align: center; color: ${typeColor}; font-weight: 600;">${pos.position_type}</td>
              <td style="padding: 10px; text-align: right; color: #fff; font-weight: 600;">₹${pos.entry_price.toFixed(2)}</td>
              <td style="padding: 10px; text-align: right; color: #fff; font-weight: 600;">₹${currentPrice.toFixed(2)}</td>
              <td style="padding: 10px; text-align: right; color: ${pnlColor}; font-weight: 600;">${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(2)}</td>
            </tr>`;
      }).join('');
    }

    async function loadPortfolioSummary() {
      const token = sessionStorage.getItem('token');
      try {
        const res = await fetch(`${API_BASE}/api/portfolio/summary`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.ok) {
          const data = await res.json();
          document.getElementById('portfolioBalance').textContent = `₹${data.current_balance.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
          document.getElementById('portfolioInvested').textContent = `₹${data.total_invested.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
          const pnlColor = data.total_realized_pnl >= 0 ? '#26a69a' : '#ef5350';
          document.getElementById('portfolioRealizedPnl').style.color = pnlColor;
          document.getElementById('portfolioRealizedPnl').textContent = `${data.total_realized_pnl >= 0 ? '+' : ''}₹${data.total_realized_pnl.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
          document.getElementById('portfolioWinRate').textContent = `${data.win_rate.toFixed(1)}%`;
        }
      } catch (e) { console.error(e); }
    }

    async function loadTransactions() {
      const token = sessionStorage.getItem('token');
      try {
        const res = await fetch(`${API_BASE}/api/portfolio/transactions`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.ok) {
          const data = await res.json();
          const tbody = document.getElementById('transactionsBody');
          if (data.transactions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="padding: 20px; text-align: center; color: #666;">No transactions</td></tr>';
            return;
          }
          tbody.innerHTML = data.transactions.map(txn => {
            const date = new Date(txn.created_at).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' });
            const pnlColor = (txn.pnl || 0) >= 0 ? '#26a69a' : '#ef5350';
            const amountColor = txn.amount >= 0 ? '#26a69a' : '#ef5350';

            let status = 'Unknown';
            if (txn.transaction_type === 'OPEN') status = 'Open';
            else if (txn.transaction_type.includes('CLOSE') || txn.transaction_type.includes('EXECUTED') || txn.transaction_type === 'MANUAL') status = 'Closed';

            const typeDisplay = txn.position_type || '-';
            const typeColor = typeDisplay === 'LONG' ? '#26a69a' : (typeDisplay === 'SHORT' ? '#ef5350' : '#888');

            return `<tr style="border-bottom: 1px solid #222;">
              <td style="padding: 10px; color: #888; font-size: 11px;">${date}</td>
              <td style="padding: 10px; color: #fff; font-weight: 600;">${txn.ticker || '-'}</td>
              <td style="padding: 10px; color: #888;">${txn.stock_name || '-'}</td>
              <td style="padding: 10px; text-align: center;"><span style="background: ${status === 'Open' ? 'rgba(59, 130, 246, 0.2)' : 'rgba(239, 83, 80, 0.2)'}; padding: 2px 8px; border-radius: 4px; font-size: 11px;">${status}</span></td>
              <td style="padding: 10px; text-align: center; color: ${typeColor}; font-weight: 600;">${typeDisplay}</td>
              <td style="padding: 10px; text-align: right; color: ${amountColor}; font-weight: 600;">${txn.amount >= 0 ? '+' : ''}₹${Math.abs(txn.amount).toFixed(2)}</td>
              <td style="padding: 10px; text-align: right; color: ${pnlColor}; font-weight: 600;">${txn.pnl ? (txn.pnl >= 0 ? '+' : '') + '₹' + txn.pnl.toFixed(2) : '-'}</td>
              <td style="padding: 10px; text-align: right; color: #fff; font-weight: 600;">₹${txn.balance_after.toFixed(2)}</td>
            </tr>`;
          }).join('');
        }
      } catch (e) { console.error(e); }
    }


    // Visualization of Trade Lines
    let activeTradeLines = [];

    function drawTradeLines(positions) {
      // Ensure chart series exists
      if (!window.bigCandleSeries) return;

      // Clear existing lines
      activeTradeLines.forEach(line => window.bigCandleSeries.removePriceLine(line));
      activeTradeLines = [];

      // Filter positions for current ticker
      const relevantPositions = positions.filter(p => p.ticker === window.currentTicker);

      relevantPositions.forEach(pos => {
        // 1. Entry Line
        const entryLine = window.bigCandleSeries.createPriceLine({
          price: pos.entry_price,
          color: pos.position_type === 'LONG' ? '#4A90E2' : '#EF5350',
          lineWidth: 1,
          lineStyle: 2, // Dashed
          axisLabelVisible: true,
          title: `${pos.position_type} Entry`,
        });
        activeTradeLines.push(entryLine);

        // 2. Take Profit Line
        if (pos.take_profit) {
          const tpLine = window.bigCandleSeries.createPriceLine({
            price: pos.take_profit,
            color: '#089981', // Green
            lineWidth: 1,
            lineStyle: 0, // Solid
            axisLabelVisible: true,
            title: `TP (${pos.id})`,
          });
          activeTradeLines.push(tpLine);
        }

        // 3. Stop Loss Line
        if (pos.stop_loss) {
          const slLine = window.bigCandleSeries.createPriceLine({
            price: pos.stop_loss,
            color: '#FF1744', // Red
            lineWidth: 1,
            lineStyle: 0, // Solid
            axisLabelVisible: true,
            title: `SL (${pos.id})`,
          });
          activeTradeLines.push(slLine);
        }
      });
    }

    // Call drawTradeLines in main renderOpenPositions as well
    const originalRender = window.renderOpenPositions;
    window.renderOpenPositions = function () {
      if (originalRender) originalRender();
      if (window.openPositions) drawTradeLines(window.openPositions);
    };
    function showToast(message, type = 'info') {
      let container = document.getElementById('toast-container');
      if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.style.cssText = 'position: fixed; top: 80px; right: 20px; z-index: 10000;';
        document.body.appendChild(container);
      }

      const toast = document.createElement('div');
      toast.style.cssText = `
            background: ${type === 'success' ? '#26a69a' : (type === 'error' ? '#ef5350' : '#333')};
            color: #fff;
            padding: 12px 24px;
            border-radius: 8px;
            margin-bottom: 10px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            opacity: 0;
            transition: opacity 0.3s;
            font-family: -apple-system, sans-serif;
            font-size: 14px;
            line-height: 1.4;
        `;
      toast.innerText = message;

      container.appendChild(toast);

      requestAnimationFrame(() => toast.style.opacity = '1');

      setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
      }, 5000);
    }

    function connectUserWebSocket() {
      const token = sessionStorage.getItem('token');
      if (!token) return;

      var wsBase = API_BASE.replace(/^http/, (window.location.protocol === 'https:' ? 'wss' : 'ws'));
      const ws = new WebSocket(`${wsBase}/ws/user`);

      ws.onopen = () => {
        console.log('[User WS] Connected');
        window._wsReconnectAttempts = 0;
        // Send auth token as first message (not in URL)
        ws.send(JSON.stringify({ token: token }));
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'order_filled') {
            const data = msg.data;
            const text = `${data.order_type} EXECUTED: ${data.ticker}\nPrice: ₹${data.execution_price.toFixed(2)} | P&L: ₹${data.pnl.toFixed(2)}`;
            showToast(text, 'success');

            // Update balance
            var ub2 = document.getElementById('userBalance'); if (ub2) ub2.textContent = `₹${data.new_balance.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

            const user = JSON.parse(sessionStorage.getItem('user') || '{}');
            user.virtual_balance = data.new_balance;
            sessionStorage.setItem('user', JSON.stringify(user));

            // Refresh positions
            if (typeof loadOpenPositions === 'function') loadOpenPositions();
            if (typeof loadClosedPositions === 'function') loadClosedPositions();
            if (typeof loadTransactions === 'function') loadTransactions();

            // Force open panel on order fill
            window.positionsPanelClosedManually = false;
            const panel = document.getElementById('openPositionsPanel');
            if (panel) {
              panel.style.display = 'block';
              if (typeof switchPanelTab === 'function') switchPanelTab('open');
            }
          }
        } catch (e) { console.error('WS Error:', e); }
      };

      ws.onclose = (event) => {
        // code 4001 = auth failure — don't retry, token is invalid
        if (event.code === 4001) {
          console.log('[User WS] Auth rejected — not reconnecting');
          return;
        }
        window._wsReconnectAttempts = (window._wsReconnectAttempts || 0) + 1;
        var delay = Math.min(30000, 5000 * Math.pow(1.5, window._wsReconnectAttempts));
        console.log('[User WS] Disconnected - Reconnecting in ' + (delay / 1000).toFixed(1) + 's');
        setTimeout(connectUserWebSocket, delay);
      };
    }

    var _posPollInterval = null;

    function startPositionsPoll() {
      stopPositionsPoll();
      _posPollInterval = setInterval(loadOpenPositions, 30000);
    }

    function stopPositionsPoll() {
      if (_posPollInterval) { clearInterval(_posPollInterval); _posPollInterval = null; }
    }

    document.addEventListener('visibilitychange', function() {
      if (document.hidden) { stopPositionsPoll(); }
      else { startPositionsPoll(); loadOpenPositions(); }
    });

    // Load positions on page load & check URL action parameters
    document.addEventListener('DOMContentLoaded', () => {
      setTimeout(loadOpenPositions, 1500);
      setTimeout(startPositionsPoll, 5000);
      connectUserWebSocket();
      
      // Auto-open Trade Modal if action query param is present (e.g. from overview.html Buy/Sell)
      try {
        const urlParams = new URLSearchParams(window.location.search);
        const action = (urlParams.get('action') || '').toLowerCase();
        if (action === 'buy' || action === 'long') {
          setTimeout(() => showTradeModal('LONG'), 300);
        } else if (action === 'sell' || action === 'short') {
          setTimeout(() => showTradeModal('SHORT'), 300);
        } else if (action === 'trade') {
          setTimeout(() => showTradeModal('LONG'), 300);
        }
      } catch (e) {
        console.error('Failed to parse URL action param:', e);
      }

      const panel = document.getElementById('openPositionsPanel');
      if (panel) {
        new MutationObserver(() => {
          setTimeout(() => window.dispatchEvent(new Event('resize')), 10);
        }).observe(panel, { attributes: true, attributeFilter: ['style'] });
      }
    });

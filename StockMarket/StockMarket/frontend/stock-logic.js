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
    function handleTradeClick() {
      const token = sessionStorage.getItem('token');
      if (!token) {
        // Set the redirect URL in the sign-in modal link
        var redirect = encodeURIComponent(window.location.href);
        var btn = document.getElementById('signInRedirectBtn');
        if (btn) btn.href = 'login.html?redirect=' + redirect;
        document.getElementById('signInPromptModal').classList.add('visible');
      } else {
        showTradeModal();
      }
    }

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
      window.location.href = (exchange === 'INDEX' ? 'stock.html?ticker=' : 'stock.html?ticker=') + encodeURIComponent(ticker);
    }

    // Show Trade Modal
    async function showTradeModal() {
      if (!_requireAuth()) return;
      const ticker = window.currentTicker || '--';
      const price = parseFloat(document.getElementById('header-price')?.textContent?.replace(/[₹,]/g, '')) || 0;
      let user = JSON.parse(sessionStorage.getItem('user') || '{}');

      document.getElementById('tradeModalTicker').textContent = ticker;
      document.getElementById('tradeEntryPrice').value = price.toFixed(2);
      document.getElementById('tradeQuantity').value = 1;
      document.getElementById('tradeTakeProfit').value = '';
      document.getElementById('tradeStopLoss').value = '';
      document.getElementById('tradeMessage').innerHTML = '';

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

      document.getElementById('tradeAvailableBalance').textContent = `₹${(user.virtual_balance || 0).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;

      setPositionType('LONG');
      calculateTotal();
      document.getElementById('tradePanelOverlay').classList.add('visible');
    }

    function hideTradeModal() {
      document.getElementById('tradePanelOverlay').classList.remove('visible');
    }

    window.toggleTradeModal = function() {
      const panel = document.getElementById('tradePanelOverlay');
      if (panel && panel.classList.contains('visible')) {
        hideTradeModal();
      } else {
        showTradeModal();
      }
    };

    function setPositionType(type) {
      currentPositionType = type;
      const btnLong = document.getElementById('btnLong');
      const btnShort = document.getElementById('btnShort');

      if (type === 'LONG') {
        btnLong.style.background = 'rgba(74, 144, 226, 0.2)';
        btnLong.style.borderColor = '#4A90E2';
        btnLong.style.color = '#4A90E2';

        btnShort.style.background = 'transparent';
        btnShort.style.borderColor = '#333';
        btnShort.style.color = '#888';
      } else {
        btnLong.style.background = 'transparent';
        btnLong.style.borderColor = '#333';
        btnLong.style.color = '#888';

        btnShort.style.background = 'rgba(74, 144, 226, 0.2)';
        btnShort.style.borderColor = '#4A90E2';
        btnShort.style.color = '#4A90E2';
      }
    }

    function calculateTotal() {
      const price = parseFloat(document.getElementById('tradeEntryPrice').value) || 0;
      const qty = parseInt(document.getElementById('tradeQuantity').value, 10) || 0;
      const total = price * qty;
      document.getElementById('tradeTotalInvestment').textContent = `₹${total.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    }

    async function submitTrade() {
      const token = sessionStorage.getItem('token');
      if (!token) return;

      const btn = document.getElementById('tradeSubmitBtn');
      const msg = document.getElementById('tradeMessage');
      btn.disabled = true;
      btn.textContent = 'Placing Order...';
      msg.innerHTML = '';

      const payload = {
        ticker: document.getElementById('tradeModalTicker').textContent,
        position_type: currentPositionType,
        quantity: parseInt(document.getElementById('tradeQuantity').value, 10),
        entry_price: parseFloat(document.getElementById('tradeEntryPrice').value),
        take_profit: parseFloat(document.getElementById('tradeTakeProfit').value) || null,
        stop_loss: parseFloat(document.getElementById('tradeStopLoss').value) || null
      };

      try {
        const res = await fetch(`${API_BASE}/api/trade/place-order`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
            'X-CSRF-Token': csrfToken
          },
          body: JSON.stringify(payload)
        });

        const data = await res.json();

        if (res.ok) {
          // Update balance in header and session
          const user = JSON.parse(sessionStorage.getItem('user') || '{}');
          user.virtual_balance = data.balance;
          sessionStorage.setItem('user', JSON.stringify(user));
          var ub = document.getElementById('userBalance'); if (ub) ub.textContent = `₹${data.balance.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

          // Hide trade modal and show success animation
          hideTradeModal();
          const detail = `${payload.position_type} | ${payload.quantity} Share${payload.quantity > 1 ? 's' : ''}`;
          showOrderSuccessModal(payload.ticker, detail);

          // RESET Manual closing flag so the new position is visible in the panel
          window.positionsPanelClosedManually = false;

          // Force open the panel and switch to 'open' tab
          const panel = document.getElementById('openPositionsPanel');
          if (panel) {
            panel.style.display = 'block';
            switchPanelTab('open');
          }

          loadOpenPositions();
        } else {
          msg.innerHTML = '<span style="color: #ef5350">' + escapeHTML(data.detail || 'Failed to place order') + '</span>';
        }
      } catch (e) {
        console.error(e);
        msg.innerHTML = '<span style="color: #ef5350">Connection error</span>';
      }

      btn.disabled = false;
      btn.textContent = 'Place Order';
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

    async function loadOpenPositions() {
      const token = sessionStorage.getItem('token');
      if (!token) return;

      try {
        const res = await fetch(`${API_BASE}/api/portfolio/open-positions`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });

        if (res.ok) {
          openPositions = await res.json();
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
      const countEl = document.getElementById('openPositionsCount');
      const panel = document.getElementById('openPositionsPanel');

      if (countEl) countEl.textContent = `(${openPositions.length})`;

      if (openPositions.length === 0) {
        if (tbody) tbody.innerHTML = '';
        if (panel) panel.style.display = 'none';
        return;
      }

      if (!window.positionsPanelClosedManually) {
        if (panel) panel.style.display = 'block';
      }

      if (!tbody) return;

      tbody.innerHTML = openPositions.map(pos => {
        var live = _getLivePrice(pos.ticker);
        let currentPrice = live > 0 ? live : (pos.current_price || pos.entry_price);

        const pnl = pos.position_type === 'LONG'
          ? (currentPrice - pos.entry_price) * pos.quantity
          : (pos.entry_price - currentPrice) * pos.quantity;

        const pnlColor  = pnl >= 0 ? '#00E676' : '#FF5252';
        const pnlBg     = pnl >= 0 ? 'rgba(0,230,118,0.08)' : 'rgba(255,82,82,0.08)';
        const pnlPrefix = pnl >= 0 ? '+' : '-';
        const isLong    = pos.position_type === 'LONG';
        const typeBg    = isLong ? 'rgba(0,200,83,0.12)' : 'rgba(255,23,68,0.12)';
        const typeColor = isLong ? '#00E676' : '#FF5252';
        const typeLabel = isLong ? '▲ LONG' : '▼ SHORT';

        const tpStr = pos.take_profit != null ? `<span style="color:#00E676;">TP ₹${pos.take_profit.toFixed(2)}</span>` : '<span style="color:#555;">—</span>';
        const slStr = pos.stop_loss  != null ? `<span style="color:#FF5252;">SL ₹${pos.stop_loss.toFixed(2)}</span>`  : '<span style="color:#555;">—</span>';

        const tickerDisplay = pos.ticker || '—';
        const nameDisplay   = pos.stock_name || '';

        return `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.04); transition:background 0.15s;" 
            onmouseover="this.style.background='rgba(255,255,255,0.03)'" 
            onmouseout="this.style.background='transparent'">
          <td style="padding:14px 12px;">
            <div style="display:flex; align-items:center; gap:10px;">
              ${tickerDisplay && tickerDisplay.trim() !== '--' && tickerDisplay.trim() !== '—' ? '<img src="logos/' + tickerDisplay.trim().split('.')[0] + '.svg" style="width:28px;height:28px;border-radius:50%;background:#1e1e1e;object-fit:contain;padding:3px;" onerror="this.style.display=\'none\'">' : ''}
              <div>
                <div style="font-weight:600; color:#fff; font-size:0.9rem;">${tickerDisplay}</div>
                ${nameDisplay ? `<div style="font-size:0.75rem; color:#666; margin-top:1px;">${nameDisplay}</div>` : ''}
              </div>
            </div>
          </td>
          <td style="padding:14px 12px;">
            <span style="background:${typeBg}; color:${typeColor}; font-size:0.75rem; font-weight:700; padding:4px 10px; border-radius:6px; letter-spacing:0.5px; white-space:nowrap;">${typeLabel}</span>
          </td>
          <td style="padding:14px 12px; text-align:right; font-family:'Roboto Mono',monospace; font-weight:600; color:#d1d4dc;">${pos.quantity}</td>
          <td style="padding:14px 12px; text-align:right; font-family:'Roboto Mono',monospace; color:#d1d4dc;">₹${pos.entry_price.toFixed(2)}</td>
          <td style="padding:14px 12px; text-align:right; font-family:'Roboto Mono',monospace; color:#fff; font-weight:600;">₹${currentPrice.toFixed(2)}</td>
          <td style="padding:14px 12px; text-align:right;">
            <span style="background:${pnlBg}; color:${pnlColor}; font-family:'Roboto Mono',monospace; font-weight:700; font-size:0.9rem; padding:4px 10px; border-radius:6px; white-space:nowrap;">
              ${pnlPrefix}₹${Math.abs(pnl).toFixed(2)}
            </span>
          </td>
          <td style="padding:14px 12px; text-align:right;">
            <div style="display:flex; flex-direction:column; align-items:flex-end; gap:3px; font-size:0.78rem;">${tpStr}${slStr}</div>
          </td>
          <td style="padding:14px 12px; text-align:right;" onclick="event.stopPropagation()">
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
    // Expose globally so WebSocket can trigger P&L updates
    window.renderOpenPositions = renderOpenPositions;

    function togglePositionsPanel() {
      const panel = document.getElementById('openPositionsPanel');
      const isHidden = (panel.style.display === 'none' || !panel.style.display);
      const targetDisplay = isHidden ? 'block' : 'none';
      panel.style.display = targetDisplay;

      // Update manual state: if we just hid it, it's manually closed. If we just showed it, it's NOT manually closed.
      window.positionsPanelClosedManually = !isHidden;
      // Panel is now in document flow, flexbox handles layout automatically
    }

    function switchPanelTab(tab) {
      // Update tab buttons
      document.getElementById('tabPanelOpen').style.background = tab === 'open' ? '#4A90E2' : '#333';
      document.getElementById('tabPanelOpen').style.color = tab === 'open' ? '#fff' : '#888';
      document.getElementById('tabPanelClosed').style.background = tab === 'closed' ? '#4A90E2' : '#333';
      document.getElementById('tabPanelClosed').style.color = tab === 'closed' ? '#fff' : '#888';
      document.getElementById('tabPanelTransactions').style.background = tab === 'transactions' ? '#4A90E2' : '#333';
      document.getElementById('tabPanelTransactions').style.color = tab === 'transactions' ? '#fff' : '#888';

      // Show/hide content
      document.getElementById('panelOpenContent').style.display = tab === 'open' ? 'block' : 'none';
      document.getElementById('panelClosedContent').style.display = tab === 'closed' ? 'block' : 'none';
      document.getElementById('panelTransactionsContent').style.display = tab === 'transactions' ? 'block' : 'none';

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
          const tbody = document.getElementById('panelClosedBody');

          if (!data.positions || data.positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="padding: 20px; text-align: center; color: #666;">No closed positions</td></tr>';
            return;
          }

          tbody.innerHTML = data.positions.map(pos => {
            const pnl = pos.realized_pnl || 0;
            const pnlColor = pnl >= 0 ? '#26a69a' : '#ef5350';
            const date = formatFullDate(pos.closed_at);

            return `<tr>
              <td class="ticker-cell">
                ${pos.ticker && pos.ticker.trim() !== '--' && pos.ticker.trim() !== '—' ? '<img src="logos/' + pos.ticker.trim().split('.')[0] + '.svg" class="stock-logo" onerror="this.style.display=\'none\'">' : ''}
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
                  ${txn.ticker && txn.ticker !== '--' && txn.ticker.trim() ? `<img src="logos/${txn.ticker.split('.')[0]}.svg" class="stock-logo" onerror="this.style.display='none'">${txn.ticker}` : '-'}
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
    }

    function hideClosePositionModal() {
      document.getElementById('closePositionModal').style.display = 'none';
      document.getElementById('closePositionMessage').innerHTML = '';
    }

    async function confirmClosePosition() {
      const btn = document.getElementById('closePositionModal').querySelector('button:last-of-type');
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

      try {
        if (btn) btn.disabled = true;
        const csrfRes = await fetch(`${API_BASE}/api/csrf-token`);
        if (!csrfRes.ok) throw new Error('CSRF token fetch failed');
        const csrfData = await csrfRes.json();

        const res = await fetch(`${API_BASE}/api/trade/close-position`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
            'X-CSRF-Token': csrfData.csrf_token
          },
          body: JSON.stringify({ position_id: positionId, closing_price: closingPrice })
        });

        const data = await res.json();
        if (res.ok) {
          // Update balance
          const user = JSON.parse(sessionStorage.getItem('user') || '{}');
          user.virtual_balance = data.balance;
          sessionStorage.setItem('user', JSON.stringify(user));
          var ub = document.getElementById('userBalance'); if (ub) ub.textContent = `₹${data.balance.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

          // Hide close confirmation modal
          hideClosePositionModal();

          // Show success modal with P&L
          const pnl = data.realized_pnl || 0;
          const pnlColor = pnl >= 0 ? '#26a69a' : '#ef5350';
          const pnlSign = pnl >= 0 ? '+' : '';
          document.getElementById('closedPnlAmount').textContent = `${pnlSign}₹${pnl.toFixed(2)}`;
          document.getElementById('closedPnlAmount').style.color = pnlColor;
          document.getElementById('positionClosedSuccessModal').style.display = 'flex';

          // Reload positions
          loadOpenPositions();
        } else {
          msgEl.innerHTML = '<div style="color: #ef5350; font-size: 13px;">' + escapeHTML(data.detail || 'Failed to close position') + '</div>';
        }
        if (btn) btn.disabled = false;
      } catch (e) {
        msgEl.innerHTML = '<div style="color: #ef5350; font-size: 13px;">Network error. Please try again.</div>';
      }
    }

    function hidePositionClosedSuccessModal() {
      document.getElementById('positionClosedSuccessModal').style.display = 'none';
    }

    // Edit TP/SL
    function showEditLimitsModal(positionId, tp, sl, tpCount, slCount) {
      var pos = openPositions.find(function (p) { return p.id === positionId; });
      if (!pos) return;
      document.getElementById('editPositionId').value = positionId;
      document.getElementById('editTakeProfit').value = tp || '';
      document.getElementById('editStopLoss').value = sl || '';
      // Store original values for validation
      window.originalTP = tp || null;
      window.originalSL = sl || null;
      document.getElementById('editTPCount').textContent = `${tpCount}/3`;
      document.getElementById('editSLCount').textContent = `${slCount}/3`;
      document.getElementById('editLimitsMessage').innerHTML = '';
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

    async function loadClosedPositions() {
      const token = sessionStorage.getItem('token');
      try {
        const res = await fetch(`${API_BASE}/api/portfolio/closed-positions`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.ok) {
          const data = await res.json();
          const tbody = document.getElementById('closedPositionsBody');
          if (data.positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="padding: 20px; text-align: center; color: #666;">No closed positions</td></tr>';
            return;
          }
          tbody.innerHTML = data.positions.map(pos => {
            const pnlColor = pos.realized_pnl >= 0 ? '#26a69a' : '#ef5350';
            const typeColor = pos.position_type === 'LONG' ? '#26a69a' : '#ef5350';
            return `<tr style="border-bottom: 1px solid #222;">
              <td style="padding: 10px; color: #fff; font-weight: 600;">${pos.ticker}</td>
              <td style="padding: 10px; color: #888;">${pos.stock_name || '-'}</td>
              <td style="padding: 10px; text-align: center;"><span style="background: rgba(239, 83, 80, 0.2); color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 11px;">Closed</span></td>
              <td style="padding: 10px; text-align: right; color: #fff; font-weight: 600;">₹${pos.entry_price.toFixed(2)}</td>
              <td style="padding: 10px; text-align: right; color: #fff; font-weight: 600;">₹${pos.closing_price.toFixed(2)}</td>
              <td style="padding: 10px; text-align: right; color: ${pnlColor}; font-weight: 600;">${pos.realized_pnl >= 0 ? '+' : ''}₹${pos.realized_pnl.toFixed(2)}</td>
              <td style="padding: 10px; text-align: center; font-weight: 600;"><span style="background: #333; padding: 2px 8px; border-radius: 4px; font-size: 11px;">${pos.close_type}</span></td>
              <td style="padding: 10px; text-align: right; color: #888; font-weight: 600;">${pos.duration}</td>
            </tr>`;
          }).join('');
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
            color: '#00E676', // Green
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

    // Load positions on page load
    document.addEventListener('DOMContentLoaded', () => {
      setTimeout(loadOpenPositions, 2000);
      setTimeout(startPositionsPoll, 5000);
      connectUserWebSocket();
      
      const panel = document.getElementById('openPositionsPanel');
      if (panel) {
        new MutationObserver(() => {
          setTimeout(() => window.dispatchEvent(new Event('resize')), 10);
        }).observe(panel, { attributes: true, attributeFilter: ['style'] });
      }
    });

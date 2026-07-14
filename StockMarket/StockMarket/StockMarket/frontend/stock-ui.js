    async function toggleNewsModal() {
      const modal = document.getElementById('newsModal');
      if (!modal) return;
      const isVisible = modal.classList.toggle('visible');
      
      if (isVisible) {
        const ticker = window.currentTicker || 'RELIANCE.NS';
        const titleEl = document.getElementById('newsModalTitle');
        if (titleEl) titleEl.innerText = `${ticker} News Sentiment`;
        const loadingEl = document.getElementById('loading-ticker');
        if (loadingEl) loadingEl.innerText = ticker;
        
        // Clear previous news
        const container = document.getElementById('ticker-news-container');
        if (container) {
          container.innerHTML = `
            <div class="leverage-loader simple-glow-theme news-loader" style="padding: 2rem;">
                <div class="loader-text-clean">
                    <span class="loader-dot-pulse"></span>
                    <span class="loader-message-text">Downloading live news for ${ticker}...</span>
                    <p style="font-size: 12px; margin-top: 5px; color: #888; text-align: center;">(This live AI scraping process takes about 15-20 seconds)</p>
                </div>
            </div>
          `;
        }
        
        if (typeof fetchStockNews === 'function') {
          try {
            const news = await fetchStockNews(ticker);
            if (typeof renderNewsCards === 'function') renderNewsCards('ticker-news-container', news);
          } catch(e) {
            const container = document.getElementById('ticker-news-container');
            if (container) container.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-secondary);">Failed to load news. <button onclick="toggleNewsModal()" style="background:none;border:none;color:#4A90E2;cursor:pointer;font-family:inherit;">Close</button></div>';
          }
        }
      }
    }

    // Missing UI Dropdown Handlers
    function toggleRangeMenu() {
      const menu = document.getElementById('rangeMenu');
      if (menu) menu.classList.toggle('visible');
    }

    function selectRange(range) {
      var crt = document.getElementById('currentRangeText'); if (crt) crt.innerText = range;
      document.querySelectorAll('.range-item').forEach(el => {
        el.classList.remove('active');
        if (el.textContent === range) el.classList.add('active');
      });
      if (window.loadData) window.loadData(range);
    }

    function toggleIndicatorMenu() {
      const menu = document.getElementById('indicatorMenu');
      if (menu) menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
    }

    function toggleIndicator(ind, event) {
      event.stopPropagation();
      const check = document.getElementById('check_' + ind);
      if (check) {
        const isActive = check.style.opacity === '1';
        check.style.opacity = isActive ? '0' : '1';
      }
    }

    // Click outside to close dropdowns
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.range-dropdown')) {
        const rangeMenu = document.getElementById('rangeMenu');
        if (rangeMenu) rangeMenu.classList.remove('visible');
      }
      if (!e.target.closest('.indicator-dropdown')) {
        const indMenu = document.getElementById('indicatorMenu');
        if (indMenu) indMenu.style.display = 'none';
      }
    });

    // Load html2pdf dynamically
    function loadHtml2Pdf() {
      return new Promise((resolve, reject) => {
        if (window.html2pdf) return resolve(window.html2pdf);
        const script = document.createElement('script');
        script.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js';
        script.onload = () => resolve(window.html2pdf);
        script.onerror = reject;
        document.head.appendChild(script);
      });
    }

    async function downloadNewsPDF(ticker) {
      const container = document.getElementById('ticker-news-container');
      if (!container) return;
      try {
        const btn = document.getElementById('downloadPdfBtn');
        if (btn) btn.innerText = 'Generating...';
        const html2pdf = await loadHtml2Pdf();
        const opt = {
          margin:       0.5,
          filename:     `${ticker}_News_Sentiment.pdf`,
          image:        { type: 'jpeg', quality: 0.98 },
          html2canvas:  { scale: 2 },
          jsPDF:        { unit: 'in', format: 'letter', orientation: 'portrait' }
        };
        await html2pdf().set(opt).from(container).save();
        if (btn) btn.innerHTML = '&#128196; Download PDF';
      } catch (err) {
        console.error('PDF generation failed:', err);
        alert('Failed to generate PDF.');
        const btn = document.getElementById('downloadPdfBtn');
        if (btn) btn.innerHTML = '&#128196; Download PDF';
      }
    }

    async function toggleNewsModal() {
      let modal = document.getElementById('newsModal');
      
      // Dynamically create modal if it doesn't exist (e.g. in stock.html)
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'newsModal';
        modal.className = 'modal';
        modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:9999;display:none;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
        
        const modalContent = document.createElement('div');
        modalContent.className = 'modal-content';
        modalContent.style.cssText = 'background:#111;border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:2rem;max-width:800px;width:90%;max-height:85vh;display:flex;flex-direction:column;box-shadow:0 24px 60px rgba(0,0,0,0.6);';
        
        modalContent.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem;border-bottom:1px solid rgba(255,255,255,0.1);padding-bottom:1rem;">
            <h2 id="newsModalTitle" style="color:#fff;margin:0;font-size:1.4rem;">Stock News Sentiment</h2>
            <div style="display:flex;gap:10px;">
              <button id="downloadPdfBtn" style="background:#4A90E2;border:none;color:#fff;padding:6px 12px;border-radius:6px;cursor:pointer;font-weight:600;font-size:13px;display:none;">&#128196; Download PDF</button>
              <button onclick="toggleNewsModal()" style="background:none;border:none;color:#999;font-size:24px;cursor:pointer;line-height:1;">&times;</button>
            </div>
          </div>
          <div id="ticker-news-container" style="flex:1;overflow-y:auto;padding-right:10px;"></div>
        `;
        
        modal.appendChild(modalContent);
        document.body.appendChild(modal);
        
        // Close on outside click
        modal.addEventListener('click', function(e) {
            if (e.target === modal) toggleNewsModal();
        });
      }
      
      const isVisible = (modal.style.display === 'flex' || modal.classList.contains('visible'));
      if (isVisible) {
          modal.style.display = 'none';
          modal.classList.remove('visible');
      } else {
          modal.style.display = 'flex';
          modal.classList.add('visible');
          
          const ticker = window.currentTicker || 'RELIANCE.NS';
          const titleEl = document.getElementById('newsModalTitle');
          if (titleEl) titleEl.innerText = `${ticker} News Sentiment`;
          
          const downloadBtn = document.getElementById('downloadPdfBtn');
          if (downloadBtn) {
              downloadBtn.style.display = 'block';
              downloadBtn.onclick = () => downloadNewsPDF(ticker);
          }
          
          const container = document.getElementById('ticker-news-container');
          if (container) {
            container.innerHTML = `
              <div class="leverage-loader simple-glow-theme news-loader" style="padding: 2rem;text-align:center;">
                  <div style="width:24px;height:24px;border:3px solid rgba(74,144,226,0.3);border-top-color:#4A90E2;border-radius:50%;animation:dashNewsSpinAnim 0.8s linear infinite;margin:0 auto 1rem;"></div>
                  <div class="loader-message-text" style="color:#a1a1aa;">Downloading live news for ${ticker}...</div>
              </div>
            `;
          }
          
          // Use fetchStockNews to get data
          fetchStockNews(ticker).then(news => {
              renderNewsCards('ticker-news-container', news);
          }).catch(e => {
              console.error(e);
              if (container) container.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-secondary);">Failed to load news. <button onclick="toggleNewsModal()" style="background:none;border:none;color:#4A90E2;cursor:pointer;font-family:inherit;">Close</button></div>';
          });
      }
    }

    async function fetchStockNews(ticker) {
        try {
            var resp = await fetch('/api/news/search/' + encodeURIComponent(ticker), { signal: AbortSignal.timeout(15000) });
            if (resp.ok) {
                var articles = await resp.json();
                return Array.isArray(articles) ? articles : [];
            }
        } catch(e) {
            if (e && e.name === 'AbortError') return [];
        }
        return [];
    }

    function renderNewsCards(containerId, newsData) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        if (!newsData || !Array.isArray(newsData) || newsData.length === 0) {
            container.innerHTML = '<p style="color:#a1a1aa;padding:1rem;text-align:center;">No recent market news available for this ticker.</p>';
            return;
        }
        
        let html = '<div style="display:flex;flex-direction:column;gap:15px;padding-bottom:20px;">';
        newsData.forEach(item => {
            const label = (item.sentiment && item.sentiment.label) ? item.sentiment.label : 'Neutral';
            const score = (item.sentiment && item.sentiment.sentiment_score !== undefined) ? item.sentiment.sentiment_score.toFixed(2) : '0.00';
            let sentColor = '#a1a1aa';
            let sentEmoji = '🎯';
            if (label.toLowerCase() === 'bullish' || label.toLowerCase() === 'positive') { sentColor = '#00E676'; sentEmoji = '📈'; }
            if (label.toLowerCase() === 'bearish' || label.toLowerCase() === 'negative') { sentColor = '#FF0055'; sentEmoji = '📉'; }
            
            const dateStr = item.published_at ? new Date(item.published_at).toLocaleString('en-IN', {dateStyle:'medium', timeStyle:'short'}) : '';
            
            html += `
              <div style="background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.05);border-radius:10px;padding:16px;">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                      <a href="${item.url || '#'}" target="_blank" rel="noopener" style="color:#fff;font-size:1.1rem;font-weight:600;text-decoration:none;flex:1;margin-right:15px;line-height:1.4;" onmouseover="this.style.textDecoration='underline'" onmouseout="this.style.textDecoration='none'">
                          ${item.title || item.headline || 'No Title'}
                      </a>
                      <div style="font-size:0.75rem;color:${sentColor};background:rgba(255,255,255,0.05);padding:4px 10px;border-radius:20px;font-weight:600;white-space:nowrap;display:flex;flex-direction:column;align-items:center;">
                          <span>${sentEmoji} ${label}</span>
                          <span style="font-size:0.65rem;opacity:0.8;margin-top:2px;">Score: ${score}</span>
                      </div>
                  </div>
                  <div style="font-size:0.75rem;color:#8a8a8a;margin-bottom:10px;display:flex;gap:10px;">
                      <span>${item.source || 'ScanX News'}</span>
                      ${dateStr ? '<span>•</span><span>' + dateStr + '</span>' : ''}
                  </div>
                  <div style="font-size:0.9rem;color:#d1d1d6;line-height:1.5;">
                      ${item.excerpt || item.snippet || ''}
                  </div>
              </div>
            `;
        });
        html += '</div>';
        
        container.innerHTML = html;
    }

    // Missing UI Dropdown Handlers
    window.toggleRangeMenu = function toggleRangeMenu() {
      const menu = document.getElementById('rangeMenu');
      if (menu) menu.classList.toggle('visible');
    }

    window.selectRange = function selectRange(range) {
      // Close the dropdown menu first
      const menu = document.getElementById('rangeMenu');
      if (menu) menu.classList.remove('visible');
      var crt = document.getElementById('currentRangeText'); if (crt) crt.innerText = range;
      document.querySelectorAll('.range-item').forEach(el => {
        el.classList.remove('active');
        if (el.textContent.trim() === range) el.classList.add('active');
      });
      try {
        var url = new URL(window.location);
        url.searchParams.set('range', range);
        history.replaceState(null, '', url);
      } catch(e) {}
      if (window.loadData) window.loadData(range);
    }

    window.toggleIndicatorMenu = function toggleIndicatorMenu() {
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

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
        modalContent.style.cssText = 'background:#111;border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:2rem;max-width:800px;width:90%;max-height:85vh;display:flex;flex-direction:column;box-shadow:0 24px 60px rgba(0,0,0,0.6);text-align:left;';
        
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
          if (container && typeof _newsSkeletonCards === 'function') {
            container.innerHTML = _newsSkeletonCards(5);
          }

          // fetchStockNews + renderNewsCards are defined in news.js (loaded first)
          fetchStockNews(ticker).then(news => {
              renderNewsCards('ticker-news-container', news);
          }).catch(e => {
              console.error(e);
              if (container) container.innerHTML =
                  '<div style="padding:3rem 1rem;text-align:center;">' +
                  '<div style="font-size:1.75rem;margin-bottom:0.75rem;">📡</div>' +
                  '<div style="color:#e2e8f0;font-weight:600;margin-bottom:0.4rem;font-size:0.95rem;">Failed to load news</div>' +
                  '<div style="color:#6b7280;font-size:0.82rem;margin-bottom:1.25rem;">Check your connection and try again.</div>' +
                  '<button onclick="toggleNewsModal()" style="padding:7px 18px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15);border-radius:8px;color:#fff;cursor:pointer;font-size:0.82rem;">Close</button>' +
                  '</div>';
          });
      }
    }

    // fetchStockNews and renderNewsCards live in news.js (loaded before this file).
    // Delegate to those authoritative implementations so the card UI is always identical.

    // Missing UI Dropdown Handlers
    window.toggleRangeMenu = function toggleRangeMenu() {
      const menu = document.getElementById('rangeMenu');
      if (menu) menu.classList.toggle('visible');
    }

    window.selectRange = function selectRange(range) {
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

    // Mobile-only "more" menu: consolidates Trade/Positions/News/Indicators into one button
    // so the chart header only shows 2 controls (range + this) on a narrow screen.
    function toggleMobileMoreMenu(event) {
      event.stopPropagation();
      const menu = document.getElementById('mobileMoreMenu');
      if (menu) menu.classList.toggle('visible');
    }

    function closeMobileMoreMenu() {
      const menu = document.getElementById('mobileMoreMenu');
      if (menu) menu.classList.remove('visible');
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
      if (!e.target.closest('.mobile-more-dropdown')) {
        const moreMenu = document.getElementById('mobileMoreMenu');
        if (moreMenu) moreMenu.classList.remove('visible');
      }
    });

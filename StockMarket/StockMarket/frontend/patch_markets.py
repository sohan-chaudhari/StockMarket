import re

with open('markets.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace fetchGlobalIndices
orig_global = """            async function fetchGlobalIndices(){
                try {
                    var res = await fetch('/api/live-prices', {
                        method: 'POST', headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({tickers: ['^GSPC','^IXIC','^N225','^HSI','^GDAXI','^FTSE']})
                    });
                    var data = await res.json();
                    var globals = [
                        {id:'sp500',flag:'🇺🇸',name:'S&P 500',key:'^GSPC'},
                        {id:'nasdaq',flag:'🇺🇸',name:'NASDAQ',key:'^IXIC'},
                        {id:'nikkei',flag:'🇯🇵',name:'Nikkei 225',key:'^N225'},
                        {id:'hsi',flag:'🇭🇰',name:'Hang Seng',key:'^HSI'},
                        {id:'dax',flag:'🇩🇪',name:'DAX',key:'^GDAXI'},
                        {id:'ftse',flag:'🇬🇧',name:'FTSE 100',key:'^FTSE'}
                    ];
                    globals.forEach(function(g){
                        var d = data[g.key]||{};
                        var price = d.current;
                        var valEl = document.getElementById(g.id+'Val');
                        var chgEl = document.getElementById(g.id+'Chg');
                        if(valEl) {
                            if(price) {
                                valEl.textContent = price.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
                            } else {
                                valEl.textContent = '--';
                            }
                        }
                        if(chgEl && d.current) {
                            var base = d.prev_close || d.open || d.current;
                            var diff = d.current - base;
                            var pct = base > 0 ? (diff/base)*100 : 0;
                            chgEl.textContent = (diff>=0?'+':'')+pct.toFixed(2)+'%';
                            chgEl.style.color = diff>=0?'#00E676':'#FF0055';
                        } else if(chgEl) {
                            chgEl.textContent = '';
                        }
                    });
                } catch(e) {
                }
            }"""

new_global = """            function _applyGlobalIndices(data) {
                if (!data) return;
                var globals = [
                    {id:'sp500',flag:'🇺🇸',name:'S&P 500',key:'^GSPC'},
                    {id:'nasdaq',flag:'🇺🇸',name:'NASDAQ',key:'^IXIC'},
                    {id:'nikkei',flag:'🇯🇵',name:'Nikkei 225',key:'^N225'},
                    {id:'hsi',flag:'🇭🇰',name:'Hang Seng',key:'^HSI'},
                    {id:'dax',flag:'🇩🇪',name:'DAX',key:'^GDAXI'},
                    {id:'ftse',flag:'🇬🇧',name:'FTSE 100',key:'^FTSE'}
                ];
                globals.forEach(function(g){
                    var d = data[g.key]||{};
                    var price = d.current;
                    var valEl = document.getElementById(g.id+'Val');
                    var chgEl = document.getElementById(g.id+'Chg');
                    if(valEl) {
                        if(price) {
                            valEl.textContent = price.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
                        } else {
                            valEl.textContent = '--';
                        }
                    }
                    if(chgEl && d.current) {
                        var base = d.prev_close || d.open || d.current;
                        var diff = d.current - base;
                        var pct = base > 0 ? (diff/base)*100 : 0;
                        chgEl.textContent = (diff>=0?'+':'')+pct.toFixed(2)+'%';
                        chgEl.style.color = diff>=0?'#00E676':'#FF0055';
                    } else if(chgEl) {
                        chgEl.textContent = '';
                    }
                });
            }
            async function fetchGlobalIndices(){
                PageCache.fetch('/api/live-prices', {
                    method: 'POST', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({tickers: ['^GSPC','^IXIC','^N225','^HSI','^GDAXI','^FTSE']})
                }, 60000, _applyGlobalIndices);
            }"""

content = content.replace(orig_global, new_global)

# Replace fetchFiiDii
orig_fii = """            async function fetchFiiDii(){
                try {
                    var res = await fetch('/api/fii-dii');
                    var data = await res.json();
                    var entries = data.entries;
                    if(entries && entries.length > 0) {
                        var latest = entries[0];
                        function fmt(val){
                            var n = parseFloat(val) || 0;
                            return (n >= 0 ? '+' : '-') + '₹' + Math.abs(n).toFixed(2) + ' Cr';
                        }
                        document.getElementById('fiiCashVal').textContent = fmt(latest.fii_cash_cr);
                        document.getElementById('fiiCashVal').style.color = (parseFloat(latest.fii_cash_cr)||0) >= 0 ? '#00E676' : '#FF0055';
                        document.getElementById('fiiFoVal').textContent = fmt(latest.fii_fo_cr);
                        document.getElementById('fiiFoVal').style.color = (parseFloat(latest.fii_fo_cr)||0) >= 0 ? '#00E676' : '#FF0055';
                        document.getElementById('diiCashVal').textContent = fmt(latest.dii_cash_cr);
                        document.getElementById('diiCashVal').style.color = (parseFloat(latest.dii_cash_cr)||0) >= 0 ? '#00E676' : '#FF0055';
                        document.getElementById('fiiNetVal').textContent = fmt(latest.net_total_cr);
                        document.getElementById('fiiNetVal').style.color = (parseFloat(latest.net_total_cr)||0) >= 0 ? '#00E676' : '#FF0055';
                        var badgeDate = latest.date || 'Today';
                        var badgeEl = document.getElementById('fiiBadge');
                        if(badgeEl) badgeEl.textContent = badgeDate;
                        document.getElementById('fiiSource').textContent = 'Source: ' + (data.source||'NSE') + ' | ' + badgeDate;
                    } else {
                        // No entries returned — show unavailable notice
                        ['fiiCashVal','fiiFoVal','diiCashVal','fiiNetVal'].forEach(function(id){
                            var el = document.getElementById(id);
                            if(el){ el.textContent = 'N/A'; el.style.color = '#52525b'; }
                        });
                        document.getElementById('fiiSource').innerHTML =
                            '<span style="color:#a1a1aa;font-size:0.72rem;">⚠️ NSE restricts direct server access for FII/DII data. Data will appear when available.</span>';
                    }
                } catch(e){
                    ['fiiCashVal','fiiFoVal','diiCashVal','fiiNetVal'].forEach(function(id){
                        var el = document.getElementById(id);
                        if(el){ el.textContent = 'N/A'; el.style.color = '#52525b'; }
                    });
                    document.getElementById('fiiSource').innerHTML =
                        '<span style="color:#a1a1aa;font-size:0.72rem;">⚠️ FII/DII data unavailable — backend unreachable</span>';
                }
            }"""

new_fii = """            function _applyFiiDii(data) {
                if (!data) return;
                var entries = data.entries;
                if(entries && entries.length > 0) {
                    var latest = entries[0];
                    function fmt(val){
                        var n = parseFloat(val) || 0;
                        return (n >= 0 ? '+' : '-') + '₹' + Math.abs(n).toFixed(2) + ' Cr';
                    }
                    document.getElementById('fiiCashVal').textContent = fmt(latest.fii_cash_cr);
                    document.getElementById('fiiCashVal').style.color = (parseFloat(latest.fii_cash_cr)||0) >= 0 ? '#00E676' : '#FF0055';
                    document.getElementById('fiiFoVal').textContent = fmt(latest.fii_fo_cr);
                    document.getElementById('fiiFoVal').style.color = (parseFloat(latest.fii_fo_cr)||0) >= 0 ? '#00E676' : '#FF0055';
                    document.getElementById('diiCashVal').textContent = fmt(latest.dii_cash_cr);
                    document.getElementById('diiCashVal').style.color = (parseFloat(latest.dii_cash_cr)||0) >= 0 ? '#00E676' : '#FF0055';
                    document.getElementById('fiiNetVal').textContent = fmt(latest.net_total_cr);
                    document.getElementById('fiiNetVal').style.color = (parseFloat(latest.net_total_cr)||0) >= 0 ? '#00E676' : '#FF0055';
                    var badgeDate = latest.date || 'Today';
                    var badgeEl = document.getElementById('fiiBadge');
                    if(badgeEl) badgeEl.textContent = badgeDate;
                    document.getElementById('fiiSource').textContent = 'Source: ' + (data.source||'NSE') + ' | ' + badgeDate;
                } else {
                    ['fiiCashVal','fiiFoVal','diiCashVal','fiiNetVal'].forEach(function(id){
                        var el = document.getElementById(id);
                        if(el){ el.textContent = 'N/A'; el.style.color = '#52525b'; }
                    });
                    document.getElementById('fiiSource').innerHTML =
                        '<span style="color:#a1a1aa;font-size:0.72rem;">⚠️ NSE restricts direct server access for FII/DII data. Data will appear when available.</span>';
                }
            }
            async function fetchFiiDii(){
                PageCache.fetch('/api/fii-dii', null, 3600000, _applyFiiDii);
            }"""
content = content.replace(orig_fii, new_fii)

# Replace fetchBSEIndices
orig_bse = """            async function fetchBSEIndices(){
                try {
                    var res = await fetch('/api/live-prices', {
                        method:'POST', headers:{'Content-Type':'application/json'},
                        body: JSON.stringify({tickers: BSE_INDICES})
                    });
                    var data = await res.json();
                    var mainContainer = document.getElementById('bseIndicesContainer');
                    mainContainer.innerHTML = BSE_INDICES.map(function(t){
                        var d = data[t]||{}; var price=d.current||0; var prev=d.prev_close||d.open||price;
                        var diff=price-prev; var pct=prev>0?(diff/prev)*100:0;
                        var cls=diff>=0?'text-green':'text-red'; var sign=diff>=0?'+':'';
                        return '<div class="index-row"><span class="index-name">'+t+'</span><span class="index-price" style="color:#fff;">\u20B9'+price.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})+'</span><span class="index-change '+cls+'">'+sign+diff.toFixed(2)+' ('+sign+pct.toFixed(2)+'%)</span></div>';
                    }).join('');
                } catch(e) {
                    document.getElementById('bseIndicesContainer').innerHTML = '<div style="color:var(--text-secondary);padding:1rem 0;text-align:center;">Start backend server</div>';
                }
            }"""

new_bse = """            async function fetchBSEIndices(){
                PageCache.fetch('/api/live-prices', {
                    method:'POST', headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({tickers: BSE_INDICES})
                }, 15000, function(data) {
                    if (!data) return;
                    var mainContainer = document.getElementById('bseIndicesContainer');
                    mainContainer.innerHTML = BSE_INDICES.map(function(t){
                        var d = data[t]||{}; var price=d.current||0; var prev=d.prev_close||d.open||price;
                        var diff=price-prev; var pct=prev>0?(diff/prev)*100:0;
                        var cls=diff>=0?'text-green':'text-red'; var sign=diff>=0?'+':'';
                        return '<div class="index-row"><span class="index-name">'+t+'</span><span class="index-price" style="color:#fff;">\u20B9'+price.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})+'</span><span class="index-change '+cls+'">'+sign+diff.toFixed(2)+' ('+sign+pct.toFixed(2)+'%)</span></div>';
                    }).join('');
                });
            }"""
content = content.replace(orig_bse, new_bse)

# Replace fetchIndiaVIX
orig_vix = """            async function fetchIndiaVIX(){
                try {
                    var res = await fetch('/api/live-prices', {
                        method:'POST', headers:{'Content-Type':'application/json'},
                        body: JSON.stringify({tickers:['^INDIAVIX']})
                    });
                    var data = await res.json();
                    var d = data['^INDIAVIX']||{};
                    var vix = d.current||0;
                    var el = document.getElementById('vixValue');
                    var chgEl = document.getElementById('vixChange');
                    if(el) el.textContent = vix ? vix.toFixed(2) : '--';
                    if(chgEl && vix && d.prev_close) {
                        var diff = vix - d.prev_close;
                        chgEl.textContent = (diff>=0?'+':'')+diff.toFixed(2);
                        chgEl.style.color = diff>=0?'#FF0055':'#00E676';
                    }
                } catch(e) {}
            }"""

new_vix = """            async function fetchIndiaVIX(){
                PageCache.fetch('/api/live-prices', {
                    method:'POST', headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({tickers:['^INDIAVIX']})
                }, 15000, function(data) {
                    if (!data) return;
                    var d = data['^INDIAVIX']||{};
                    var vix = d.current||0;
                    var el = document.getElementById('vixValue');
                    var chgEl = document.getElementById('vixChange');
                    if(el) el.textContent = vix ? vix.toFixed(2) : '--';
                    if(chgEl && vix && d.prev_close) {
                        var diff = vix - d.prev_close;
                        chgEl.textContent = (diff>=0?'+':'')+diff.toFixed(2);
                        chgEl.style.color = diff>=0?'#FF0055':'#00E676';
                    }
                });
            }"""
content = content.replace(orig_vix, new_vix)

# Replace fetchCrypto
orig_crypto = """            async function fetchCrypto(){
                try {
                    var res = await fetch('/api/crypto-prices');
                    var data = await res.json();
                    var container = document.getElementById('cryptoContainer');
                    if(!data || data.length === 0){
                        container.innerHTML = '<div style="color:var(--text-secondary);padding:1rem 0;text-align:center;">Crypto data temporarily unavailable</div>';
                        return;
                    }
                    container.innerHTML = data.map(function(c){
                        var cls = c.change_24h >= 0 ? 'text-green' : 'text-red';
                        var sign = c.change_24h >= 0 ? '+' : '';
                        var mcapStr = c.market_cap ? '$' + (c.market_cap >= 1e12 ? (c.market_cap/1e12).toFixed(2)+'T' : c.market_cap >= 1e9 ? (c.market_cap/1e9).toFixed(2)+'B' : (c.market_cap/1e6).toFixed(2)+'M') : '--';
                        return '<div class="global-row"><span><span style="font-weight:600;color:#fff;">'+c.symbol+'</span><span style="color:var(--text-secondary);font-size:0.8rem;margin-left:8px;">'+c.name+'</span></span><span style="font-family:\\'Roboto Mono\\',monospace;font-weight:600;">$'+c.price.toLocaleString('en-US')+'</span><span class="index-change '+cls+'">'+sign+c.change_24h.toFixed(2)+'%</span></div>';
                    }).join('');
                } catch(e) {
                    document.getElementById('cryptoContainer').innerHTML = '<div style="color:var(--text-secondary);padding:1rem 0;text-align:center;">Start backend server</div>';
                }
            }"""

new_crypto = """            async function fetchCrypto(){
                PageCache.fetch('/api/crypto-prices', null, 60000, function(data) {
                    var container = document.getElementById('cryptoContainer');
                    if(!data || data.length === 0){
                        container.innerHTML = '<div style="color:var(--text-secondary);padding:1rem 0;text-align:center;">Crypto data temporarily unavailable</div>';
                        return;
                    }
                    container.innerHTML = data.map(function(c){
                        var cls = c.change_24h >= 0 ? 'text-green' : 'text-red';
                        var sign = c.change_24h >= 0 ? '+' : '';
                        return '<div class="global-row"><span><span style="font-weight:600;color:#fff;">'+c.symbol+'</span><span style="color:var(--text-secondary);font-size:0.8rem;margin-left:8px;">'+c.name+'</span></span><span style="font-family:\\'Roboto Mono\\',monospace;font-weight:600;">$'+c.price.toLocaleString('en-US')+'</span><span class="index-change '+cls+'">'+sign+c.change_24h.toFixed(2)+'%</span></div>';
                    }).join('');
                });
            }"""
content = content.replace(orig_crypto, new_crypto)

# Replace fetchSectorIndices
orig_sector = """            async function fetchSectorIndices() {
                var sTickers = SECTOR_INDICES.map(function(s){ return s.ticker; });
                try {
                    var res = await fetch('/api/live-prices', {
                        method: 'POST', headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({tickers: sTickers})
                    });
                    var data = await res.json();
                    updateSectorsFromData(data);
                } catch(e) {
                    SECTOR_INDICES.forEach(function(s){ sectorPcts[s.name] = 0; });
                }
                renderSectors();
                renderTrending();
            }"""

new_sector = """            async function fetchSectorIndices() {
                var sTickers = SECTOR_INDICES.map(function(s){ return s.ticker; });
                PageCache.fetch('/api/live-prices', {
                    method: 'POST', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({tickers: sTickers})
                }, 15000, function(data) {
                    if (data) updateSectorsFromData(data);
                });
            }"""
content = content.replace(orig_sector, new_sector)

with open('markets.html', 'w', encoding='utf-8') as f:
    f.write(content)

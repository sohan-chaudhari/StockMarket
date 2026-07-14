import re

with open('markets.html', 'r', encoding='utf-8') as f:
    content = f.read()

orig_leaders = """            async function fetchSectorLeaders() {
                try {
                    var res = await fetch('/api/sector-leaders');
                    sectorLeadersCache = await res.json();
                } catch(e) {
                    console.error("Failed to load sector leaders", e);
                }
                renderSectorLeaders();
            }"""

new_leaders = """            async function fetchSectorLeaders() {
                PageCache.fetch('/api/sector-leaders', null, 30000, function(data) {
                    if (data) {
                        sectorLeadersCache = data;
                        renderSectorLeaders();
                    }
                });
            }"""
content = content.replace(orig_leaders, new_leaders)

orig_heatmap = """            async function loadHeatmapTickers() {
                try {
                    var res = await fetch('/api/market-movers');
                    var data = await res.json();
                    _heatmapTickers = (data.advancing||[]).concat(data.declining||[]).slice(0,150).map(function(m){ return m.ticker; });
                } catch(e) {}
                renderHeatmap();
            }"""

new_heatmap = """            async function loadHeatmapTickers() {
                PageCache.fetch('/api/market-movers', null, 30000, function(data) {
                    if (data) {
                        _heatmapTickers = (data.advancing||[]).concat(data.declining||[]).slice(0,150).map(function(m){ return m.ticker; });
                        renderHeatmap();
                    }
                });
            }"""
content = content.replace(orig_heatmap, new_heatmap)

orig_render_heatmap = """            async function renderHeatmap() {
                var container = document.getElementById('heatmapContainer');
                if (!container) return;
                if (_heatmapTickers.length === 0) {
                    try {
                        var res = await fetch('/api/screener');
                        var data = await res.json();
                        _heatmapTickers = data.slice(0,150).map(function(s){ return s.ticker; });
                    } catch(e) { return; }
                }
                try {
                    var res = await fetch('/api/live-prices', {
                        method: 'POST', headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({tickers: _heatmapTickers})
                    });
                    var raw = await res.json();
                    var items = _heatmapTickers.map(function(t){
                        var d = raw[t]||{};
                        var cp = d.current_price || d.current || 0;
                        var prev = d.prev_close || d.open || cp;
                        var pct = prev > 0 ? ((cp - prev)/prev*100) : 0;
                        return { ticker: t, price: cp, pct: pct, name: (STOCK_META[t]||{}).name || t };
                    });
                    items.sort(function(a,b){ return Math.abs(b.pct) - Math.abs(a.pct); });
                    container.innerHTML = items.slice(0,96).map(function(s){
                        var p = s.pct;
                        var cls = p >= 0 ? '#00E676' : '#FF0055';
                        var intensity = Math.min(Math.abs(p)/4, 1);
                        var bg;
                        if (p >= 0) {
                            var g = Math.floor(230 - (1-intensity)*180);
                            var b = Math.floor(118 - (1-intensity)*100);
                            bg = 'rgba(0,'+g+','+b+',0.35)';
                        } else {
                            var r = Math.floor(255 - (1-intensity)*200);
                            var gb = Math.floor(85 - (1-intensity)*70);
                            bg = 'rgba('+r+',0,'+gb+',0.35)';
                        }
                        return '<div id="hm-cell-'+s.ticker+'" class="heatmap-cell" style="background:'+bg+';border:1px solid rgba(255,255,255,0.06);cursor:pointer;" onclick="window.location.href=\\'stock.html?ticker='+s.ticker+'\\'"><div class="hm-name">'+s.name.substring(0,14)+'</div><div id="hm-pct-'+s.ticker+'" class="hm-pct" style="color:'+cls+'">'+(p>=0?'+':'')+p.toFixed(2)+'%</div></div>';
                    }).join('');
                } catch(e) {
                    container.innerHTML = '<div style="color:var(--text-secondary);text-align:center;grid-column:1/-1;padding:1rem;">Failed to load heatmap</div>';
                }
                
                // Subscribe to live updates for these tickers
                if (sectorWs && sectorWs.readyState === WebSocket.OPEN) {
                    sectorWs.send(JSON.stringify({ type: 'subscribe', topics: _heatmapTickers }));
                }
            }"""

new_render_heatmap = """            async function renderHeatmap() {
                var container = document.getElementById('heatmapContainer');
                if (!container) return;
                var doRender = function() {
                    PageCache.fetch('/api/live-prices', {
                        method: 'POST', headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({tickers: _heatmapTickers})
                    }, 15000, function(raw) {
                        if (!raw) return;
                        var items = _heatmapTickers.map(function(t){
                            var d = raw[t]||{};
                            var cp = d.current_price || d.current || 0;
                            var prev = d.prev_close || d.open || cp;
                            var pct = prev > 0 ? ((cp - prev)/prev*100) : 0;
                            return { ticker: t, price: cp, pct: pct, name: (STOCK_META[t]||{}).name || t };
                        });
                        items.sort(function(a,b){ return Math.abs(b.pct) - Math.abs(a.pct); });
                        container.innerHTML = items.slice(0,96).map(function(s){
                            var p = s.pct;
                            var cls = p >= 0 ? '#00E676' : '#FF0055';
                            var intensity = Math.min(Math.abs(p)/4, 1);
                            var bg;
                            if (p >= 0) {
                                var g = Math.floor(230 - (1-intensity)*180);
                                var b = Math.floor(118 - (1-intensity)*100);
                                bg = 'rgba(0,'+g+','+b+',0.35)';
                            } else {
                                var r = Math.floor(255 - (1-intensity)*200);
                                var gb = Math.floor(85 - (1-intensity)*70);
                                bg = 'rgba('+r+',0,'+gb+',0.35)';
                            }
                            return '<div id="hm-cell-'+s.ticker+'" class="heatmap-cell" style="background:'+bg+';border:1px solid rgba(255,255,255,0.06);cursor:pointer;" onclick="window.location.href=\\'stock.html?ticker='+s.ticker+'\\'"><div class="hm-name">'+s.name.substring(0,14)+'</div><div id="hm-pct-'+s.ticker+'" class="hm-pct" style="color:'+cls+'">'+(p>=0?'+':'')+p.toFixed(2)+'%</div></div>';
                        }).join('');
                    });
                    if (sectorWs && sectorWs.readyState === WebSocket.OPEN) {
                        sectorWs.send(JSON.stringify({ type: 'subscribe', topics: _heatmapTickers }));
                    }
                };

                if (_heatmapTickers.length === 0) {
                    PageCache.fetch('/api/screener', null, 3600000, function(data) {
                        if (data) {
                            _heatmapTickers = data.slice(0,150).map(function(s){ return s.ticker; });
                            doRender();
                        }
                    });
                } else {
                    doRender();
                }
            }"""
content = content.replace(orig_render_heatmap, new_render_heatmap)

with open('markets.html', 'w', encoding='utf-8') as f:
    f.write(content)

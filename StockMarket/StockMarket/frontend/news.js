/**
 * news.js — Dashboard news widget + ticker news modal
 *
 * All news comes exclusively from scanx.trade via /api/news/search/{query}.
 *  - Dashboard: 5 latest market articles
 *  - News page:  same source, full list, filterable by ticker
 *  - Chart modal: ticker-specific search (fetchStockNews)
 */

/* ── Helpers ──────────────────────────────────────────────────────────── */

function _newsEscHtml(s) {
    if (typeof s !== 'string') return s || '';
    return s.replace(/[&<>'"/]/g, function (c) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;','/':'&#47;'}[c];
    });
}

function _newsNormaliseSentiment(raw, score) {
    // Never return Neutral — resolve direction from label then score sign
    if (!raw) return score != null && score < 0 ? 'Bearish' : 'Bullish';
    var l = (typeof raw === 'string' ? raw : (raw.label || raw.sentiment || '')).toLowerCase();
    if (l === 'positive' || l === 'bullish') return 'Bullish';
    if (l === 'negative' || l === 'bearish') return 'Bearish';
    // label is "neutral" — use score sign to resolve
    var s = score != null ? score : (typeof raw === 'object' ? Number(raw.sentiment_score || 0) : 0);
    return s < 0 ? 'Bearish' : 'Bullish';
}

function _newsTimeAgo(dateStr) {
    if (!dateStr) return '';
    try {
        var sec = Math.floor((Date.now() - new Date(dateStr)) / 1000);
        if (sec < 60)    return 'just now';
        if (sec < 3600)  return Math.floor(sec / 60) + 'm ago';
        if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
        return Math.floor(sec / 86400) + 'd ago';
    } catch(e) { return ''; }
}

function _newsCleanTitle(t) {
    return (t || '')
        .replace(/\s*[-–|]\s*scanx\.trade\s*$/i, '')
        .replace(/\s*\|\s*[^|]+$/, '')        // strip trailing "| Source"
        .trim();
}

function _newsNormaliseArticle(a, defaultSource) {
    var rawSent = a.sentiment || {};
    var sentScore = 0.0;
    if (typeof rawSent === 'object' && rawSent.sentiment_score != null) {
        sentScore = Number(rawSent.sentiment_score);
    } else if (typeof a.sentiment_score === 'number') {
        sentScore = a.sentiment_score;
    }
    if (isNaN(sentScore)) sentScore = 0.0;
    // If score is exactly 0 (no signal from ML), use a tiny positive default so
    // the label resolves to Bullish rather than showing a flat 0.00
    var displayScore = sentScore === 0.0 ? 0.03 : sentScore;
    var label = _newsNormaliseSentiment(a.sentiment, displayScore);
    return {
        ticker:       a.ticker || a.symbol || 'MARKET',
        title:        a.title  || a.headline || '',
        excerpt:      a.excerpt || a.snippet || a.summary || '',
        published_at: a.published_at || a.pubDate || a.published || new Date().toISOString(),
        sentiment:    { label: label, sentiment_score: displayScore },
        url:          a.url || a.link || '',
        source:       a.source || defaultSource || 'ScanX',
    };
}

function _newsScoreStr(article) {
    var rawSent = article.sentiment;
    var score = (rawSent && rawSent.sentiment_score != null) ? Number(rawSent.sentiment_score) : 0.03;
    if (isNaN(score) || score === 0) score = 0.03;
    var sign = score > 0 ? '+' : '';
    return ' ' + sign + score.toFixed(2);
}

/* ── Data source ─────────────────────────────────────────────────────── */

/**
 * Fetch scanx.trade market news.
 * Uses /api/news/search/market which queries site:scanx.trade on Google News RSS.
 */
async function _fetchScanXMarketNews() {
    try {
        var signal = (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) ? AbortSignal.timeout(15000) : undefined;
        var resp = await fetch('/api/news/search/market', signal ? { signal: signal } : {});
        if (!resp.ok) return [];
        var data = await resp.json();
        return Array.isArray(data)
            ? data.map(function(a) { return _newsNormaliseArticle(a, 'ScanX'); })
            : [];
    } catch(e) { return []; }
}

/**
 * Fetch scanx.trade news for a specific ticker.
 * Called by the chart page "News Sentiment" modal.
 */
async function fetchStockNews(ticker) {
    try {
        var clean = ticker.replace(/\.(NS|BO)$/i, '').toUpperCase();
        var signal = (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) ? AbortSignal.timeout(12000) : undefined;
        var resp = await fetch('/api/news/search/' + encodeURIComponent(clean), signal ? { signal: signal } : {});
        if (!resp.ok) return [];
        var data = await resp.json();
        var articles = Array.isArray(data)
            ? data.map(function(a) { return _newsNormaliseArticle(a, 'ScanX'); })
            : [];
        window._newsArticles = articles;
        return articles;
    } catch(e) { return []; }
}

/* ── Dashboard load & render ─────────────────────────────────────────── */

function _newsSkeletonCards(count) {
    var skels = '';
    for (var i = 0; i < count; i++) {
        var w1 = 55 + (i * 13) % 35; // vary widths so they don't look identical
        var w2 = 30 + (i * 17) % 30;
        skels +=
            '<div style="padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.05);">' +
            '<div style="display:flex;justify-content:space-between;gap:10px;margin-bottom:8px;">' +
            '<div style="flex:1;">' +
            '<div class="_ns-skel" style="height:13px;border-radius:4px;width:' + w1 + '%;margin-bottom:6px;"></div>' +
            '<div class="_ns-skel" style="height:13px;border-radius:4px;width:' + (w1 - 15) + '%;"></div>' +
            '</div>' +
            '<div class="_ns-skel" style="height:22px;border-radius:12px;width:72px;flex-shrink:0;"></div>' +
            '</div>' +
            '<div class="_ns-skel" style="height:10px;border-radius:4px;width:' + w2 + '%;"></div>' +
            '</div>';
    }
    return '<style>._ns-skel{background:linear-gradient(90deg,rgba(255,255,255,0.04) 25%,rgba(255,255,255,0.09) 50%,rgba(255,255,255,0.04) 75%);background-size:200% 100%;animation:_nsSkelAnim 1.4s ease infinite;}@keyframes _nsSkelAnim{0%{background-position:200% 0}100%{background-position:-200% 0}}</style>' + skels;
}

async function loadDashboardNews(retryCount) {
    var container = document.getElementById('general-news-container');
    if (!container) return;

    retryCount = retryCount || 0;

    // 1. If we don't have items rendered yet, try loading cached articles from localStorage immediately
    if (!container.querySelector('.news-list-item')) {
        try {
            var rawCache = localStorage.getItem('_cached_dashboard_news');
            if (rawCache) {
                var cachedArticles = JSON.parse(rawCache);
                if (Array.isArray(cachedArticles) && cachedArticles.length > 0) {
                    window._newsArticles = cachedArticles;
                    renderDashboardNewsCards(container, cachedArticles.slice(0, 5), cachedArticles);
                }
            }
        } catch(e) {}
    }

    // 2. If container is still completely empty, show skeleton cards while fetching
    if (!container.querySelector('.news-list-item')) {
        container.innerHTML = _newsSkeletonCards(5);
    }

    var articles = await _fetchScanXMarketNews();

    if (!articles.length) {
        // Cold start retry up to 3 times (2.5s, 5s, 8s) before showing an error
        if (retryCount < 3) {
            var delay = (retryCount + 1) * 2500;
            setTimeout(function() { loadDashboardNews(retryCount + 1); }, delay);
            return;
        }
        // If container already has cached items rendered, don't destroy them with an error screen
        if (container.querySelector('.news-list-item')) {
            return;
        }
        container.innerHTML =
            '<div style="padding:2.5rem 1rem;text-align:center;">' +
            '<div style="font-size:1.75rem;margin-bottom:0.75rem;">📡</div>' +
            '<div style="color:#e2e8f0;font-weight:600;margin-bottom:0.4rem;font-size:0.95rem;">News unavailable</div>' +
            '<div style="color:#6b7280;font-size:0.82rem;margin-bottom:1.25rem;">Could not reach the news feed right now.</div>' +
            '<button onclick="loadDashboardNews(0)" style="padding:7px 18px;background:#4A90E2;border:none;border-radius:8px;color:#fff;cursor:pointer;font-size:0.82rem;font-weight:600;">Retry</button>' +
            '</div>';
        return;
    }

    articles.sort(function(a, b) { return new Date(b.published_at) - new Date(a.published_at); });
    window._newsArticles = articles;
    try {
        localStorage.setItem('_cached_dashboard_news', JSON.stringify(articles));
    } catch(e) {}
    renderDashboardNewsCards(container, articles.slice(0, 5), articles);
}

function renderDashboardNewsCards(container, topNews, allArticles) {
    if (!topNews || !topNews.length) {
        container.innerHTML =
            '<div style="padding:2rem 1rem;text-align:center;">' +
            '<div style="font-size:1.5rem;margin-bottom:0.5rem;">📰</div>' +
            '<div style="color:#6b7280;font-size:0.85rem;">No recent market news.</div>' +
            '</div>';
        return;
    }

    var sentimentColors = { Bullish: '#089981', Bearish: '#f23645', Neutral: '#a1a1aa' };
    var sentimentDots   = { Bullish: '▲', Bearish: '▼', Neutral: '●' };

    var cardsHtml = topNews.map(function(article, localIdx) {
        var globalIdx  = allArticles.indexOf(article);
        if (globalIdx === -1) globalIdx = localIdx;

        var label      = _newsNormaliseSentiment(article.sentiment);
        var color      = sentimentColors[label] || '#a1a1aa';
        var dot        = sentimentDots[label]   || '●';
        var scoreStr   = _newsScoreStr(article);
        var cleanTitle = _newsCleanTitle(article.title);
        var timeAgo    = _newsTimeAgo(article.published_at);
        var source     = (article.source || 'ScanX').replace(/scanx\.trade/i, 'ScanX');

        return '<div class="news-list-item" data-news-idx="' + globalIdx + '" ' +
                    'style="cursor:pointer;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.05);transition:background 0.15s;" ' +
                    'onmouseenter="this.style.background=\'rgba(255,255,255,0.03)\'" ' +
                    'onmouseleave="this.style.background=\'\'">' +
                '<div style="display:flex;align-items:baseline;justify-content:space-between;gap:0.5rem;margin-bottom:4px;">' +
                    '<span style="font-size:0.875rem;font-weight:600;color:#e2e8f0;line-height:1.4;flex:1;">' +
                        _newsEscHtml(cleanTitle) +
                    '</span>' +
                    '<span style="font-size:0.68rem;color:' + color + ';white-space:nowrap;font-weight:700;flex-shrink:0;padding-left:8px;">' +
                        dot + ' ' + label + scoreStr +
                    '</span>' +
                '</div>' +
                '<div style="font-size:0.72rem;color:#6b7280;display:flex;gap:4px;align-items:center;flex-wrap:wrap;">' +
                    '<span style="color:#4A90E2;font-weight:600;">' + _newsEscHtml(source) + '</span>' +
                    (timeAgo ? '<span>·</span><span>' + timeAgo + '</span>' : '') +
                    (article.ticker && article.ticker !== 'MARKET'
                        ? '<span>·</span><span style="color:#94a3b8;">' + _newsEscHtml(article.ticker) + '</span>'
                        : '') +
                '</div>' +
            '</div>';
    }).join('');

    var viewAllHtml =
        '<div style="padding:10px 16px;border-top:1px solid rgba(255,255,255,0.05);text-align:center;">' +
            '<a href="news.html" style="font-size:0.8rem;color:#4A90E2;text-decoration:none;font-weight:500;" ' +
               'onmouseenter="this.style.textDecoration=\'underline\'" onmouseleave="this.style.textDecoration=\'none\'">' +
                'View all market news →' +
            '</a>' +
        '</div>';

    container.innerHTML = cardsHtml + viewAllHtml;

    if (!document.getElementById('dashNewsSpinStyle')) {
        var s = document.createElement('style');
        s.id  = 'dashNewsSpinStyle';
        s.textContent = '@keyframes dashNewsSpinAnim{to{transform:rotate(360deg)}}';
        document.head.appendChild(s);
    }

    if (!container._newsClickBound) {
        container.addEventListener('click', function(e) {
            var card = e.target.closest('[data-news-idx]');
            if (card) {
                var idx = parseInt(card.getAttribute('data-news-idx'), 10);
                if (!isNaN(idx)) openNewsDetail(idx);
            }
        });
        container._newsClickBound = true;
    }
}

/* ── Chart modal renderer ────────────────────────────────────────────── */

/**
 * renderNewsCards — renders ticker-specific news into the chart modal.
 * Called from stock.html as: renderNewsCards('ticker-news-container', newsArray)
 */
function renderNewsCards(containerId, newsData) {
    var container = document.getElementById(containerId);
    if (!container) return;
    var articles = Array.isArray(newsData) ? newsData : [];
    window._newsArticles = articles;

    if (!articles.length) {
        container.innerHTML =
            '<div style="padding:3rem 1rem;text-align:center;">' +
            '<div style="font-size:1.75rem;margin-bottom:0.75rem;">🔍</div>' +
            '<div style="color:#e2e8f0;font-weight:600;margin-bottom:0.4rem;font-size:0.95rem;">No news found</div>' +
            '<div style="color:#6b7280;font-size:0.82rem;">No recent articles found for this ticker.</div>' +
            '</div>';
        return;
    }

    var sentimentColors = { Bullish: '#089981', Bearish: '#f23645', Neutral: '#a1a1aa' };
    var sentimentDots   = { Bullish: '▲', Bearish: '▼', Neutral: '●' };

    var html = articles.slice(0, 10).map(function(a, i) {
        var label      = _newsNormaliseSentiment(a.sentiment);
        var color      = sentimentColors[label] || '#a1a1aa';
        var dot        = sentimentDots[label]   || '●';
        var scoreStr   = _newsScoreStr(a);
        var cleanTitle = _newsCleanTitle(a.title);
        var timeAgo    = _newsTimeAgo(a.published_at);

        var src = (a.source || 'ScanX').replace(/scanx\.trade/i, 'ScanX');
        return '<div style="border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:12px 14px;margin-bottom:10px;cursor:pointer;transition:border-color 0.15s;text-align:left;" ' +
                    'onclick="openNewsDetail(' + i + ')" ' +
                    'onmouseenter="this.style.borderColor=\'rgba(74,144,226,0.4)\'" ' +
                    'onmouseleave="this.style.borderColor=\'rgba(255,255,255,0.08)\'">' +
                '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:6px;">' +
                    '<span style="font-size:0.875rem;font-weight:600;color:#e2e8f0;line-height:1.4;flex:1;">' +
                        _newsEscHtml(cleanTitle) +
                    '</span>' +
                    '<span style="font-size:0.7rem;color:' + color + ';font-weight:700;white-space:nowrap;flex-shrink:0;padding-top:2px;">' +
                        dot + ' ' + label + scoreStr +
                    '</span>' +
                '</div>' +
                '<div style="font-size:0.72rem;color:#6b7280;display:flex;gap:4px;align-items:center;">' +
                    '<span style="color:#4A90E2;font-weight:600;">' + _newsEscHtml(src) + '</span>' +
                    (timeAgo ? '<span>·</span><span>' + timeAgo + '</span>' : '') +
                '</div>' +
            '</div>';
    }).join('');

    container.innerHTML = html;
}

/* ── openNewsDetail ──────────────────────────────────────────────────── */

function openNewsDetail(idx) {
    var articles = window._newsArticles || [];
    var article  = articles[idx];
    if (!article) return;

    var label     = _newsNormaliseSentiment(article.sentiment);
    var sentColor = { Bullish: '#089981', Bearish: '#f23645', Neutral: '#a1a1aa' }[label];
    var dot       = { Bullish: '▲', Bearish: '▼', Neutral: '●' }[label];
    var scoreStr  = _newsScoreStr(article);
    var cleanTitle = _newsCleanTitle(article.title);
    var summary   = article.excerpt || article.summary || article.snippet || 'No additional details.';
    var linkUrl   = (article.url && article.url !== '#') ? article.url
                    : (article.ticker && article.ticker !== 'MARKET' ? 'overview.html?ticker=' + encodeURIComponent(article.ticker) : null);
    var closeId   = 'dashNewsClose_' + Date.now();

    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:9999;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
    overlay.setAttribute('tabindex', '-1');

    var modal = document.createElement('div');
    modal.style.cssText = 'background:#111;border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:2rem;max-width:620px;width:90%;max-height:82vh;overflow-y:auto;box-shadow:0 24px 60px rgba(0,0,0,0.6);';

    modal.innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">' +
            '<span style="font-size:0.78rem;color:' + sentColor + ';background:rgba(255,255,255,0.06);padding:3px 10px;border-radius:20px;font-weight:700;">' + dot + ' ' + label + scoreStr + '</span>' +
            (article.ticker && article.ticker !== 'MARKET'
                ? '<span style="font-size:0.78rem;color:#4A90E2;font-weight:600;padding:3px 10px;background:rgba(74,144,226,0.1);border-radius:20px;">' + _newsEscHtml(article.ticker) + '</span>'
                : '') +
        '</div>' +
        '<h2 style="font-size:1.15rem;font-weight:700;color:#fff;margin-bottom:0.75rem;line-height:1.45;">' + _newsEscHtml(cleanTitle) + '</h2>' +
        '<div style="display:flex;gap:1rem;flex-wrap:wrap;font-size:0.78rem;color:#a1a1aa;margin-bottom:1.25rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.06);">' +
            '<span style="color:#4A90E2;font-weight:600;">' + _newsEscHtml(article.source || 'ScanX') + '</span>' +
            '<span>·</span>' +
            '<span>' + (article.published_at ? new Date(article.published_at).toLocaleString('en-IN', { dateStyle:'medium', timeStyle:'short' }) : '') + '</span>' +
        '</div>' +
        '<div style="font-size:0.92rem;color:#d1d1d6;line-height:1.75;">' + _newsEscHtml(summary) + '</div>' +
        '<div style="display:flex;gap:0.75rem;margin-top:1.5rem;flex-wrap:wrap;">' +
            (linkUrl ? '<a href="' + _newsEscHtml(linkUrl) + '" target="_blank" rel="noopener" style="padding:0.55rem 1.25rem;background:#4A90E2;border-radius:8px;color:#fff;font-size:0.84rem;text-decoration:none;font-weight:600;">Read Full Article ↗</a>' : '') +
            '<button id="' + closeId + '" style="padding:0.55rem 1.25rem;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;cursor:pointer;font-size:0.84rem;">Close</button>' +
        '</div>';

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    overlay.focus();

    function _closeOverlay() { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }
    overlay.addEventListener('click', function(e) { if (e.target === overlay) _closeOverlay(); });
    overlay.addEventListener('keydown', function(e) { if (e.key === 'Escape') _closeOverlay(); });
    var closeBtn = document.getElementById(closeId);
    if (closeBtn) closeBtn.addEventListener('click', _closeOverlay);
}

/* ── Legacy compat stubs ─────────────────────────────────────────────── */

async function fetchGeneralNews() {
    return _fetchScanXMarketNews();
}

/* ── Boot ─────────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {
    if (document.getElementById('general-news-container')) {
        loadDashboardNews();
        setInterval(loadDashboardNews, 120000); // refresh every 2 minutes
    }
});

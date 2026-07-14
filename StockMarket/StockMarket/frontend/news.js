/**
 * news.js  — Dashboard news widget
 * Uses the same multi-source pipeline as the full News page:
 *   1. /api/news/general        (main backend — market data articles)
 *   2. /api/scanx/news/full/all (proxied to News Sentiment service on port 8003)
 *   3. Google News RSS via allorigins.win proxy (live fallback)
 * Shows max 6 articles on the dashboard with a "View All" link.
 */

const NEWS_API_BASE = '/api/news';

/* ── Helpers ──────────────────────────────────────────────────────────── */

function _newsEscHtml(s) {
    if (typeof s !== 'string') return s || '';
    return s.replace(/[&<>'"/]/g, function (c) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;','/':'&#47;'}[c];
    });
}

function _newsNormaliseSentiment(raw) {
    if (!raw) return 'Neutral';
    var l = (typeof raw === 'string' ? raw : (raw.label || raw.sentiment || '')).toLowerCase();
    if (l === 'positive' || l === 'bullish') return 'Bullish';
    if (l === 'negative' || l === 'bearish') return 'Bearish';
    return 'Neutral';
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
    return (t || '').replace(/\s*-\s*scanx\.trade\s*$/i, '')
                    .replace(/\s*\|\s*scanx\.trade\s*$/i, '')
                    .replace(/\s*-\s*[A-Z][a-zA-Z\s]+$/, '') // strip trailing " - Source Name"
                    .trim();
}

function _newsNormaliseArticle(a, defaultSource) {
    return {
        ticker: a.ticker || a.symbol || '',
        title:  a.title || a.headline || '',
        excerpt: a.excerpt || a.snippet || a.summary || '',
        published_at: a.published_at || a.pubDate || a.published || new Date().toISOString(),
        sentiment: { label: _newsNormaliseSentiment(a.sentiment) },
        url:    a.url || a.link || '',
        source: a.source || defaultSource || 'News'
    };
}

/* ── Data Sources ─────────────────────────────────────────────────────── */

async function _fetchMainBackendNews() {
    try {
        var controller = new AbortController();
        var tid = setTimeout(function () { controller.abort(); }, 8000);
        var resp = await fetch('/api/news/general', { signal: controller.signal });
        clearTimeout(tid);
        if (!resp.ok) return [];
        var data = await resp.json();
        var articles = Array.isArray(data) ? data : (data.articles || []);
        return articles.map(function (a) { return _newsNormaliseArticle(a, 'Market Data'); });
    } catch(e) {
        console.warn('[DashNews] Main backend failed:', e.message);
        return [];
    }
}

async function _fetchScanXNews() {
    try {
        var controller = new AbortController();
        var tid = setTimeout(function () { controller.abort(); }, 12000);
        var resp = await fetch('/api/scanx/news/full/all?limit=10', { signal: controller.signal });
        clearTimeout(tid);
        if (!resp.ok) return [];
        var data = await resp.json();
        if (!Array.isArray(data) || data.length === 0) return [];
        return data.map(function (a) { return _newsNormaliseArticle(a, 'ScanX'); });
    } catch(e) {
        console.warn('[DashNews] ScanX fetch failed:', e.message);
        return [];
    }
}

async function _fetchGoogleNewsRSS() {
    var queries = ['NSE+India+stock+market+today', 'Nifty+Sensex+today'];
    var articles = [];
    for (var i = 0; i < queries.length; i++) {
        try {
            var proxy   = '/api/v1/news/google-rss?q=' + encodeURIComponent(queries[i]);
            var signal  = (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) ? AbortSignal.timeout(8000) : undefined;
            var resp    = await fetch(proxy, signal ? { signal: signal } : {});
            if (!resp.ok) continue;
            var xml     = await resp.text();
            var parser  = new DOMParser();
            var doc     = parser.parseFromString(xml, 'text/xml');
            var items   = doc.querySelectorAll('item');
            items.forEach(function (item) {
                var title = item.querySelector('title')   ? item.querySelector('title').textContent   : '';
                var link  = item.querySelector('link')    ? item.querySelector('link').textContent    :
                            (item.querySelector('guid')   ? item.querySelector('guid').textContent    : '');
                var pub   = item.querySelector('pubDate') ? item.querySelector('pubDate').textContent : '';
                var desc  = item.querySelector('description') ? item.querySelector('description').textContent : '';
                var tmp   = document.createElement('div'); tmp.innerHTML = desc;
                var plain = (tmp.textContent || '').substring(0, 200);
                if (title && title.length > 5) {
                    articles.push({
                        ticker: 'MARKET',
                        title:  title.replace(/\s*-\s*[^-]+$/, '').trim(),
                        excerpt: plain,
                        published_at: pub ? new Date(pub).toISOString() : new Date().toISOString(),
                        sentiment: { label: 'Neutral' },
                        url: link || '',
                        source: 'Google News'
                    });
                }
            });
        } catch(e) {
            console.warn('[DashNews] Google RSS failed:', e.message);
        }
        if (articles.length >= 6) break;
    }
    return articles;
}

/* ── Load & Merge ─────────────────────────────────────────────────────── */

async function loadDashboardNews() {
    var container = document.getElementById('general-news-container');
    if (!container) return;

    // Show skeleton while loading (keep existing skeleton if present, else show spinner)
    if (!container.querySelector('.news-list-item')) {
        container.innerHTML =
            '<div style="padding:1.5rem;text-align:center;color:#a1a1aa;display:flex;align-items:center;justify-content:center;gap:0.5rem;">' +
            '<div style="width:16px;height:16px;border:2px solid rgba(74,144,226,0.3);border-top-color:#4A90E2;border-radius:50%;animation:dashNewsSpinAnim 0.8s linear infinite;"></div>' +
            'Loading live news…</div>';
    }

    var combined = [];

    // Run all three fetches in parallel for speed
    var results = await Promise.allSettled([
        _fetchMainBackendNews(),
        _fetchScanXNews(),
        _fetchGoogleNewsRSS()
    ]);

    results.forEach(function (r) {
        if (r.status === 'fulfilled' && Array.isArray(r.value)) {
            combined = combined.concat(r.value);
        }
    });

    // Deduplicate by normalised title prefix (60 chars)
    var seen = {};
    combined = combined.filter(function (a) {
        var key = (a.title || '').trim().toLowerCase().substring(0, 60);
        if (!key || seen[key]) return false;
        seen[key] = true;
        return true;
    });

    // Sort newest-first
    combined.sort(function (a, b) {
        return new Date(b.published_at || 0) - new Date(a.published_at || 0);
    });

    // Keep only top 6 for dashboard
    var topNews = combined.slice(0, 6);

    window._newsArticles = combined; // keep full list for openNewsDetail
    renderDashboardNewsCards(container, topNews, combined);
}

/* ── Render ───────────────────────────────────────────────────────────── */

function renderDashboardNewsCards(container, topNews, allArticles) {
    if (!topNews || topNews.length === 0) {
        container.innerHTML =
            '<p style="color:#a1a1aa;padding:1rem;">No recent market news available. ' +
            '<a href="news.html" style="color:#4A90E2;">Visit News page ↗</a></p>';
        return;
    }

    var sentimentColors = { Bullish: '#00E676', Bearish: '#FF0055', Neutral: '#a1a1aa' };
    var sentimentEmojis = { Bullish: '📈', Bearish: '📉', Neutral: '🎯' };

    var cardsHtml = topNews.map(function (article, localIdx) {
        // Find the real index in allArticles so the modal gets the right article
        var globalIdx = allArticles.indexOf(article);
        if (globalIdx === -1) globalIdx = localIdx;

        var label      = _newsNormaliseSentiment(article.sentiment);
        var color      = sentimentColors[label] || '#a1a1aa';
        var emoji      = sentimentEmojis[label] || '🎯';
        var cleanTitle = _newsCleanTitle(article.title);
        var timeAgo    = _newsTimeAgo(article.published_at);
        var summary    = article.excerpt || '';
        var displaySum = summary.length > 160 ? summary.substring(0, 160) + '…' : summary;
        var source     = (article.source || 'News').replace(/ MORE$/i, '').replace(/scanx\.trade/i, 'ScanX');

        return '<div class="news-list-item" style="cursor:pointer;border-bottom:1px solid rgba(255,255,255,0.05);padding:14px 16px;transition:background 0.15s;" ' +
                    'data-news-idx="' + globalIdx + '" ' +
                    'onmouseenter="this.style.background=\'rgba(255,255,255,0.03)\'" ' +
                    'onmouseleave="this.style.background=\'\'">' +
                '<div class="news-item-title-row" style="display:flex;align-items:flex-start;justify-content:space-between;gap:0.5rem;margin-bottom:6px;">' +
                    '<span class="news-item-title" style="font-size:0.875rem;font-weight:600;color:#fff;line-height:1.4;flex:1;">' +
                        _newsEscHtml(cleanTitle) +
                    '</span>' +
                    '<span style="font-size:0.7rem;color:' + color + ';background:rgba(255,255,255,0.05);padding:2px 8px;border-radius:20px;white-space:nowrap;font-weight:600;flex-shrink:0;">' +
                        emoji + ' ' + label +
                    '</span>' +
                '</div>' +
                '<div style="display:flex;gap:0.5rem;font-size:0.72rem;color:#6b7280;margin-bottom:' + (displaySum ? '6px' : '0') + ';">' +
                    '<span>' + _newsEscHtml(source) + '</span>' +
                    (timeAgo ? '<span>•</span><span>' + timeAgo + '</span>' : '') +
                    (article.ticker && article.ticker !== 'MARKET' ? '<span>•</span><span style="color:#4A90E2;font-weight:600;">' + _newsEscHtml(article.ticker) + '</span>' : '') +
                '</div>' +
                (displaySum ? '<p style="font-size:0.8rem;color:#9ca3af;margin:0;line-height:1.5;">' + _newsEscHtml(displaySum) + '</p>' : '') +
            '</div>';
    }).join('');

    var viewAllHtml =
        '<div style="padding:12px 16px;border-top:1px solid rgba(255,255,255,0.05);text-align:center;">' +
            '<a href="news.html" style="font-size:0.82rem;color:#4A90E2;text-decoration:none;font-weight:500;" ' +
               'onmouseenter="this.style.textDecoration=\'underline\'" onmouseleave="this.style.textDecoration=\'none\'">' +
                'View all market news →' +
            '</a>' +
        '</div>';

    container.innerHTML = cardsHtml + viewAllHtml;

    // Add spinner keyframe if not already present
    if (!document.getElementById('dashNewsSpinStyle')) {
        var style = document.createElement('style');
        style.id  = 'dashNewsSpinStyle';
        style.textContent = '@keyframes dashNewsSpinAnim{to{transform:rotate(360deg)}}';
        document.head.appendChild(style);
    }

    // Event delegation — click any news card to open detail modal
    container.addEventListener('click', function (e) {
        var card = e.target.closest('[data-news-idx]');
        if (card) {
            var idx = parseInt(card.getAttribute('data-news-idx'), 10);
            if (!isNaN(idx)) openNewsDetail(idx);
        }
    }, { once: true }); // re-added each render, once:true prevents duplicates
    // Note: re-register on each render
    container._delegationBound = false;
}

/* ── openNewsDetail (also used by home page stock news) ──────────────── */

function openNewsDetail(idx) {
    var articles = window._newsArticles || [];
    var article  = articles[idx];
    if (!article) return;

    var label      = _newsNormaliseSentiment(article.sentiment);
    var sentColor  = { Bullish: '#00E676', Bearish: '#FF0055', Neutral: '#a1a1aa' }[label];
    var sentEmoji  = { Bullish: '📈', Bearish: '📉', Neutral: '🎯' }[label];
    var summary    = article.excerpt || article.summary || article.snippet || 'No details available.';
    var cleanTitle = _newsCleanTitle(article.title);

    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:9999;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
    overlay.setAttribute('tabindex', '-1');

    var modal = document.createElement('div');
    modal.style.cssText = 'background:#111;border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:2rem;max-width:620px;width:90%;max-height:82vh;overflow-y:auto;box-shadow:0 24px 60px rgba(0,0,0,0.6);';

    var linkUrl  = (article.url && article.url !== '#') ? article.url : (article.ticker && article.ticker !== 'MARKET' ? 'stock.html?ticker=' + encodeURIComponent(article.ticker) : null);
    var closeId  = 'dashNewsClose_' + Date.now();

    modal.innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:1rem;">' +
            '<div style="font-size:0.8rem;color:' + sentColor + ';background:rgba(255,255,255,0.05);padding:4px 12px;border-radius:20px;font-weight:600;">' + sentEmoji + ' ' + label + '</div>' +
            (article.ticker && article.ticker !== 'MARKET' ? '<div style="font-size:0.8rem;color:#4A90E2;font-weight:600;padding:4px 12px;background:rgba(74,144,226,0.1);border-radius:20px;">' + _newsEscHtml(article.ticker) + '</div>' : '') +
        '</div>' +
        '<h2 style="font-size:1.2rem;font-weight:700;color:#fff;margin-bottom:0.75rem;line-height:1.45;">' + _newsEscHtml(cleanTitle) + '</h2>' +
        '<div style="display:flex;gap:1rem;flex-wrap:wrap;font-size:0.8rem;color:#a1a1aa;margin-bottom:1.25rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.06);">' +
            '<span>' + _newsEscHtml(article.source || 'Market News') + '</span>' +
            '<span>•</span>' +
            '<span>' + (article.published_at ? new Date(article.published_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '') + '</span>' +
        '</div>' +
        '<div style="font-size:0.95rem;color:#d1d1d6;line-height:1.75;">' + _newsEscHtml(summary) + '</div>' +
        '<div style="display:flex;gap:1rem;margin-top:1.5rem;flex-wrap:wrap;">' +
            (linkUrl ? '<a href="' + _newsEscHtml(linkUrl) + '" target="_blank" rel="noopener" style="padding:0.6rem 1.5rem;background:#4A90E2;border-radius:8px;color:#fff;font-size:0.85rem;text-decoration:none;font-weight:600;">Read Full Article ↗</a>' : '') +
            '<button id="' + closeId + '" style="padding:0.6rem 1.5rem;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;cursor:pointer;font-size:0.85rem;">Close</button>' +
        '</div>';

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    overlay.focus();

    overlay.addEventListener('click', function (e) { if (e.target === overlay) document.body.removeChild(overlay); });
    overlay.addEventListener('keydown', function (e) { if (e.key === 'Escape') document.body.removeChild(overlay); });
    document.getElementById(closeId).addEventListener('click', function () { document.body.removeChild(overlay); });
}

/* ── Keep legacy fetchGeneralNews for anything that still calls it ────── */
async function fetchGeneralNews() {
    return _fetchMainBackendNews();
}

function renderNewsCards(containerId, newsData) {
    // Legacy wrapper — if called directly, use the new renderer
    var container = document.getElementById(containerId);
    if (!container) return;
    window._newsArticles = Array.isArray(newsData) ? newsData : [];
    renderDashboardNewsCards(container, window._newsArticles.slice(0, 6), window._newsArticles);
}

/* ── Boot ─────────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('general-news-container')) {
        loadDashboardNews();
        // Auto-refresh every 90 seconds
        setInterval(loadDashboardNews, 90000);
    }
});

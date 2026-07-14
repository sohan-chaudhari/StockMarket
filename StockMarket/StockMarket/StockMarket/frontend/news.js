// Point to the Main Backend API directly using relative path.
const NEWS_API_BASE = '/api/news';

/**
 * Fetches general news from the backend proxy with a 12s timeout.
 */
async function fetchGeneralNews() {
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 12000);
        const response = await fetch(`${NEWS_API_BASE}/general`, { signal: controller.signal });
        clearTimeout(timeoutId);
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            console.error('General news fetch failed:', response.status, errorData);
            throw new Error(errorData.detail || 'Failed to fetch general news');
        }
        const data = await response.json();
        // Backend may return {error, articles} or a raw array
        if (Array.isArray(data)) return data;
        if (data && Array.isArray(data.articles)) return data.articles;
        return [];
    } catch (error) {
        console.error('Error in fetchGeneralNews:', error);
        return [];
    }
}

/**
 * Fetches ticker-specific news from the backend proxy.
 */
async function fetchStockNews(ticker) {
    try {
        // Clean ticker for the news service
        const cleanTicker = ticker.replace('.NS', '').replace('.BO', '');
        const response = await fetch(`${NEWS_API_BASE}/ticker/${cleanTicker}`);
        if (!response.ok) throw new Error(`Failed to fetch news for ${ticker}`);
        const data = await response.json();
        if (Array.isArray(data)) return data;
        if (data && Array.isArray(data.articles)) return data.articles;
        return [];
    } catch (error) {
        console.error('Error in fetchStockNews:', error);
        return [];
    }
}

/**
 * Renders news cards into a container.
 */
function renderNewsCards(containerId, newsData) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!newsData || newsData.length === 0) {
        container.innerHTML = '<p class="no-news">No recent news available.</p>';
        return;
    }

    window._newsArticles = Array.isArray(newsData) ? newsData : [];

    container.innerHTML = window._newsArticles.map((article, idx) => {
        // Sentiment might be a string or an object depending on the API source
        let sentimentLabel = 'Neutral';
        let sentimentScore = null;
        if (typeof article.sentiment === 'string') {
            sentimentLabel = article.sentiment;
        } else if (article.sentiment && typeof article.sentiment === 'object') {
            sentimentLabel = article.sentiment.label || 'Neutral';
            sentimentScore = article.sentiment.sentiment_score;
        }

        const sentimentClass = sentimentLabel.toLowerCase();
        const sentimentEmoji = sentimentClass === 'positive' ? '📈' : (sentimentClass === 'negative' ? '📉' : '🎯');
        
        // Clean source name
        let cleanSource = (article.source || 'Market News').replace(/ MORE$/i, '').replace(/scanx\.trade/i, 'ScanX');

        // Get excerpt/summary
        const summary = article.excerpt || article.summary || article.snippet || '';
        const displaySummary = summary ? (summary.length > 200 ? summary.substring(0, 200) + '...' : summary) : 'No summary available.';

        // Escape for XSS protection
        const escapeHTML = str => {
            if (typeof str !== 'string') return str;
            return str.replace(/[&<>'"]/g, tag => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
            }[tag]));
        };
        const safeTitle = escapeHTML(article.title);
        const safeSummary = escapeHTML(displaySummary);
        
        return `
            <div class="news-list-item" style="cursor:pointer;" onclick="openNewsDetail(${idx})">
                <div class="news-item-title-row">
                    <span class="news-item-title">${safeTitle}</span>
                    <span class="external-icon">↗</span>
                    <div class="news-item-badges">
                        ${sentimentScore !== null ? `<span class="news-score-badge ${sentimentClass}">${sentimentScore > 0 ? '+' : ''}${sentimentScore}</span>` : ''}
                        <span class="news-sentiment-badge ${sentimentClass}">${sentimentEmoji} ${sentimentLabel}</span>
                    </div>
                </div>
                <div class="news-item-meta">
                    <span class="news-item-source">${cleanSource}</span>
                    <span class="news-item-dot">•</span>
                    <span class="news-item-date">${new Date(article.published_at || Date.now()).toLocaleDateString()}</span>
                </div>
                <p class="news-item-summary">${safeSummary}</p>
            </div>
        `;
    }).join('');
}

/** Open a news detail modal */
function openNewsDetail(idx) {
    var articles = window._newsArticles || [];
    var article = articles[idx];
    if (!article) return;

    var sentimentLabel = 'Neutral';
    if (typeof article.sentiment === 'string') {
        sentimentLabel = article.sentiment;
    } else if (article.sentiment && typeof article.sentiment === 'object') {
        sentimentLabel = article.sentiment.label || 'Neutral';
    }
    var sentimentClass = sentimentLabel.toLowerCase();
    var sentimentEmoji = sentimentClass === 'positive' ? '📈' : (sentimentClass === 'negative' ? '📉' : '🎯');
    var summary = article.excerpt || article.summary || article.snippet || 'No details available.';

    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.75);z-index:9999;display:flex;align-items:center;justify-content:center;';
    overlay.onclick = function(e) { if (e.target === overlay) document.body.removeChild(overlay); };

    var modal = document.createElement('div');
    modal.style.cssText = 'background:#111;border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:2rem;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;';

    var sentColor = sentimentClass === 'positive' ? '#00E676' : (sentimentClass === 'negative' ? '#FF0055' : '#a1a1aa');
    var headerHtml = '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:1rem;">' +
        '<div style="font-size:0.8rem;color:' + sentColor + ';background:rgba(255,255,255,0.05);padding:4px 12px;border-radius:20px;font-weight:600;">' + sentimentEmoji + ' ' + sentimentLabel + '</div>' +
        (article.ticker ? '<div style="font-size:0.8rem;color:var(--accent-blue,#4A90E2);font-weight:600;padding:4px 12px;background:rgba(74,144,226,0.1);border-radius:20px;">' + article.ticker + '</div>' : '') +
        '</div>';

    var titleHtml = '<h2 style="font-size:1.3rem;font-weight:700;color:#fff;margin-bottom:0.75rem;line-height:1.4;">' + escapeHTML(article.title) + '</h2>';

    var metaHtml = '<div style="display:flex;gap:1rem;font-size:0.8rem;color:#a1a1aa;margin-bottom:1.25rem;padding-bottom:1rem;border-bottom:1px solid rgba(255,255,255,0.06);">' +
        '<span>' + (article.source || 'Market News') + '</span>' +
        '<span>•</span>' +
        '<span>' + new Date(article.published_at || Date.now()).toLocaleDateString() + '</span>' +
        '</div>';

    var bodyHtml = '<div style="font-size:0.95rem;color:#d1d1d6;line-height:1.7;">' + escapeHTML(summary) + '</div>';

    var linkUrl = article.url ? article.url : (article.ticker ? 'index_chart.html?ticker=' + article.ticker : null);
    var readMoreBtn = linkUrl ? '<a href="' + escapeHTML(linkUrl) + '" target="_blank" style="padding:0.6rem 1.5rem;background:var(--accent-blue,#4A90E2);border:none;border-radius:8px;color:#fff;cursor:pointer;font-family:\'Inter\',sans-serif;font-size:0.85rem;text-decoration:none;display:inline-block;">Read More</a>' : '';
    
    var closeBtn = '<button onclick="document.body.removeChild(this.parentElement.parentElement.parentElement)" style="padding:0.6rem 1.5rem;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:8px;color:#fff;cursor:pointer;font-family:\'Inter\',sans-serif;font-size:0.85rem;">Close</button>';

    var actionsHtml = '<div style="display:flex;gap:1rem;margin-top:1.5rem;">' + readMoreBtn + closeBtn + '</div>';

    modal.innerHTML = headerHtml + titleHtml + metaHtml + bodyHtml + actionsHtml;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    overlay.focus();
    overlay.addEventListener('keydown', function(e) { if (e.key === 'Escape') document.body.removeChild(overlay); });
}

function escapeHTML(str) {
    if (typeof str !== 'string') return str;
    return str.replace(/[&<>'"]/g, function(tag) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[tag];
    });
}

// Initial load for landing page if container exists
document.addEventListener('DOMContentLoaded', async () => {
    const generalNewsContainer = document.getElementById('general-news-container');
    if (generalNewsContainer) {
        generalNewsContainer.innerHTML = `
            <div class="news-loading-state" style="padding: 2rem; text-align: center; color: var(--text-muted, #888);">
                <span class="loading-spinner" style="display: block; margin-bottom: 1rem; font-size: 24px;">⌛</span>
                <p>Loading the latest market news...</p>
            </div>
        `;
        
        const news = await fetchGeneralNews();
        renderNewsCards('general-news-container', news);
    }
});

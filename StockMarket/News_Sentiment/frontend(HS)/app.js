// ==================== Configuration ====================
const API_BASE_URL = 'http://127.0.0.1:8000/api';

// Popular Indian stocks for search suggestions
let ALL_STOCKS = [];  // Will be loaded from API
const POPULAR_STOCKS = [
    { ticker: 'RELIANCE', name: 'Reliance Industries' },
    { ticker: 'TCS', name: 'Tata Consultancy Services' },
    { ticker: 'HDFCBANK', name: 'HDFC Bank' },
    { ticker: 'INFY', name: 'Infosys' },
    { ticker: 'ICICIBANK', name: 'ICICI Bank' },
    { ticker: 'BHARTIARTL', name: 'Bharti Airtel' },
    { ticker: 'SBIN', name: 'State Bank of India' },
    { ticker: 'HINDUNILVR', name: 'Hindustan Unilever' },
    { ticker: 'ITC', name: 'ITC Limited' },
    { ticker: 'LT', name: 'Larsen & Toubro' },
    { ticker: 'KOTAKBANK', name: 'Kotak Mahindra Bank' },
    { ticker: 'AXISBANK', name: 'Axis Bank' },
    { ticker: 'ASIANPAINT', name: 'Asian Paints' },
    { ticker: 'MARUTI', name: 'Maruti Suzuki' },
    { ticker: 'WIPRO', name: 'Wipro' }
];

// ==================== State Management ====================
let currentTicker = 'ALL'; // Default to show all news
const DEFAULT_TIME_WINDOW = '30d'; // Fetch last 30 days of news
let newsData = [];

// ==================== Auto-Refresh Configuration ====================
const AUTO_REFRESH_INTERVAL = 10 * 60 * 1000; // 10 minutes in milliseconds
let refreshIntervalId = null;
let lastRefreshTime = null;

// ==================== DOM Elements ====================
const searchInput = document.getElementById('stockSearch');
const searchSuggestions = document.getElementById('searchSuggestions');
const clearSearchBtn = document.getElementById('clearSearch');
const currentTickerSpan = document.getElementById('currentTicker');
const newsFeed = document.getElementById('newsFeed');
const emptyState = document.getElementById('emptyState');

// ==================== Loading Functions ====================
function showLoading() {
    const loadingState = document.getElementById('loadingState');

    loadingState.classList.add('active');
    newsFeed.style.display = 'none';
    emptyState.style.display = 'none';
}

function hideLoading() {
    const loadingState = document.getElementById('loadingState');
    loadingState.classList.remove('active');
}

// ==================== Initialization ====================
document.addEventListener('DOMContentLoaded', () => {
    initializeApp();
});

function initializeApp() {
    loadAllStocks();  // Load all stocks from API
    setupEventListeners();
    fetchNews(currentTicker, DEFAULT_TIME_WINDOW);
    startAutoRefresh();  // Start automatic news refresh
}

// ==================== Auto-Refresh Functions ====================
function startAutoRefresh() {
    // Clear any existing interval
    if (refreshIntervalId) {
        clearInterval(refreshIntervalId);
    }

    // Set up new interval
    refreshIntervalId = setInterval(() => {
        console.log('🔄 Auto-refreshing news...');
        fetchNewsInBackground(currentTicker, DEFAULT_TIME_WINDOW);
    }, AUTO_REFRESH_INTERVAL);

    lastRefreshTime = new Date();
    console.log(`✓ Auto-refresh enabled (every ${AUTO_REFRESH_INTERVAL / 60000} minutes)`);
}

function stopAutoRefresh() {
    if (refreshIntervalId) {
        clearInterval(refreshIntervalId);
        refreshIntervalId = null;
        console.log('⏹ Auto-refresh stopped');
    }
}

async function fetchNewsInBackground(ticker, timeWindow) {
    try {
        const endpoint = ticker === 'ALL'
            ? `${API_BASE_URL}/scanx/news/full/all`
            : `${API_BASE_URL}/scanx/news/full/${ticker}`;

        const response = await fetch(`${endpoint}?limit=20`);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const newData = await response.json();

        // Check if there are new articles
        const newArticles = findNewArticles(newData, newsData);

        if (newArticles.length > 0) {
            console.log(`🆕 Found ${newArticles.length} new article(s)!`);
            newsData = newData;
            renderNews(newsData);
            showNewArticleNotification(newArticles.length);
        } else {
            console.log('No new articles found');
        }

        lastRefreshTime = new Date();

    } catch (error) {
        // Background refresh can fail if the API is busy scraping - this is expected
        console.warn('Background refresh skipped:', error.message || 'API busy');
    }
}

function findNewArticles(newData, oldData) {
    if (!oldData || oldData.length === 0) return [];

    const oldUrls = new Set(oldData.map(a => a.url));
    return newData.filter(article => !oldUrls.has(article.url));
}

function showNewArticleNotification(count) {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = 'new-article-notification';
    notification.innerHTML = `
        <span>🆕 ${count} new article${count > 1 ? 's' : ''} added!</span>
    `;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: linear-gradient(135deg, #10b981, #059669);
        color: white;
        padding: 12px 20px;
        border-radius: 8px;
        font-weight: 500;
        box-shadow: 0 4px 12px rgba(16, 185, 129, 0.4);
        z-index: 10000;
        animation: slideIn 0.3s ease-out;
    `;

    document.body.appendChild(notification);

    // Remove after 4 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-in';
        setTimeout(() => notification.remove(), 300);
    }, 4000);
}

// ==================== Event Listeners ====================
function setupEventListeners() {
    // Search input
    searchInput.addEventListener('input', handleSearchInput);
    searchInput.addEventListener('focus', handleSearchFocus);

    // Close suggestions when clicking outside
    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !searchSuggestions.contains(e.target)) {
            searchSuggestions.classList.remove('active');
        }
    });
}

function handleSearchInput(e) {
    const query = e.target.value.toLowerCase().trim();

    if (query.length === 0) {
        showAllSuggestions();
    } else {
        // Search in ALL_STOCKS if loaded, otherwise use POPULAR_STOCKS
        const stocksToSearch = ALL_STOCKS.length > 0 ? ALL_STOCKS : POPULAR_STOCKS;
        const filtered = stocksToSearch.filter(stock =>
            stock.ticker.toLowerCase().includes(query) ||
            stock.name.toLowerCase().includes(query)
        );
        renderSuggestions(filtered.slice(0, 20));  // Limit to top 20 results
    }
}

function handleSearchFocus() {
    showAllSuggestions();
}

// ==================== Search Suggestions ====================
function showAllSuggestions() {
    renderSuggestions(POPULAR_STOCKS);
}

function renderSuggestions(stocks) {
    if (stocks.length === 0) {
        searchSuggestions.classList.remove('active');
        return;
    }

    const html = stocks.map(stock => `
        <div class="suggestion-item">
            <div class="suggestion-info" onclick="selectStock('${stock.ticker}')">
                <span class="suggestion-ticker">${stock.ticker}</span>
                <span class="suggestion-name">${stock.name}</span>
            </div>
            <button class="launch-news-btn" onclick="event.stopPropagation(); selectStock('${stock.ticker}')" title="Launch News for ${stock.ticker}">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M22 2L11 13"></path>
                    <path d="M22 2L15 22L11 13L2 9L22 2Z"></path>
                </svg>
                Launch News
            </button>
        </div>
    `).join('');

    searchSuggestions.innerHTML = html;
    searchSuggestions.classList.add('active');
}

function selectStock(ticker) {
    currentTicker = ticker;
    currentTickerSpan.textContent = ticker === 'ALL' ? 'All Stocks' : ticker;
    searchInput.value = ticker === 'ALL' ? '' : ticker;
    searchSuggestions.classList.remove('active');

    // Show/hide clear button
    if (ticker !== 'ALL' && clearSearchBtn) {
        clearSearchBtn.style.display = 'flex';
    } else if (clearSearchBtn) {
        clearSearchBtn.style.display = 'none';
    }

    fetchNews(ticker, DEFAULT_TIME_WINDOW);
}

function clearSearch() {
    searchInput.value = '';
    if (clearSearchBtn) {
        clearSearchBtn.style.display = 'none';
    }
    selectStock('ALL');
}

// ==================== Load All Stocks ====================
async function loadAllStocks() {
    try {
        const response = await fetch(`${API_BASE_URL}/stocks/list?limit=6000`);

        if (response.ok) {
            const data = await response.json();
            ALL_STOCKS = data.stocks || [];
            console.log(`✓ Loaded ${ALL_STOCKS.length} stocks for search`);
        } else {
            console.warn('Failed to load stocks, using popular stocks only');
        }
    } catch (error) {
        console.error('Error loading stocks:', error);
        console.warn('Using popular stocks only');
    }
}

// Mode toggle removed - always using Full (Playwright) mode

// ==================== API Functions ====================
async function fetchNews(ticker, timeWindow) {
    showLoading();

    try {
        // Always use Playwright full scraping
        const endpoint = ticker === 'ALL'
            ? `${API_BASE_URL}/scanx/news/full/all`
            : `${API_BASE_URL}/scanx/news/full/${ticker}`;

        const response = await fetch(`${endpoint}?limit=20`);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        newsData = data;

        if (newsData.length === 0) {
            hideLoading();
            showEmptyState();
        } else {
            hideLoading();
            renderNews(newsData);
        }
    } catch (error) {
        console.error('Error fetching news:', error);
        hideLoading();
        showError();
    }
}

// ==================== Rendering Functions ====================
function showEmptyState() {
    newsFeed.style.display = 'none';
    emptyState.style.display = 'block';
}

function showError() {
    newsFeed.innerHTML = `
        <div class="empty-state">
            <p class="empty-title">Failed to load news</p>
            <p class="empty-subtitle">Please check your connection and try again</p>
        </div>
    `;
}

function renderNews(articles) {
    // Sort by published_at (newest first)
    const sortedArticles = [...articles].sort((a, b) => {
        return new Date(b.published_at) - new Date(a.published_at);
    });

    const html = sortedArticles.map(article => createNewsCard(article)).join('');
    newsFeed.innerHTML = html;
    newsFeed.style.display = 'flex';
    emptyState.style.display = 'none';
}

function createNewsCard(article) {
    const publishedDate = new Date(article.published_at);
    const isRecent = Date.now() - publishedDate.getTime() < 5 * 60 * 1000; // < 5 min
    const timeAgo = formatTimeAgo(publishedDate);

    // Sentiment data (may not be available for live scraped news)
    const sentiment = article.sentiment || {};
    const hasSentiment = sentiment.label && sentiment.sentiment_score !== undefined;
    const sentimentLabel = sentiment.label || 'neutral';
    const sentimentScore = sentiment.sentiment_score || 0;
    const confidence = sentiment.confidence || 0;

    // Recency score
    const recencyScore = article.recency_score;
    const hasRecency = recencyScore !== undefined && recencyScore !== null;

    // Stock logo (use logo URL if available, otherwise use initials)
    const logoUrl = article.logo_url;
    const stockInitials = article.ticker.substring(0, 2);

    const logoHTML = logoUrl
        ? `<img src="${logoUrl}" alt="${article.ticker}" style="width: 100%; height: 100%; object-fit: cover; background: white; border-radius: 0.75rem;" onerror="this.onerror=null; this.style.display='none'; this.parentElement.innerHTML='${stockInitials}';" />`
        : stockInitials;

    // Clean title: remove " - scanx.trade" suffix
    const cleanTitle = article.title.replace(/\s*-\s*scanx\.trade\s*$/i, '');

    // Clean excerpt: remove " scanx.trade" suffix and hide if same as title
    let cleanExcerpt = (article.excerpt || '').replace(/\s*scanx\.trade\s*$/i, '');
    const isSameAsTitle = cleanExcerpt.toLowerCase().trim() === cleanTitle.toLowerCase().trim();
    if (isSameAsTitle) {
        cleanExcerpt = '';  // Hide if duplicate
    }

    return `
        <div class="news-card" onclick="openArticle('${article.url}')">
            <!-- Stock Logo -->
            <div class="stock-logo">
                ${logoHTML}
            </div>
            
            <!-- News Content -->
            <div class="news-content">
                <!-- Header -->
                <div class="news-header">
                    <div class="news-meta">
                        <div class="news-time">
                            <svg class="clock-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <circle cx="12" cy="12" r="10"></circle>
                                <polyline points="12 6 12 12 16 14"></polyline>
                            </svg>
                            <span>${timeAgo}</span>
                        </div>
                    </div>
                    ${isRecent ? '<span class="live-badge">⚡ Live</span>' : ''}
                
                    <!-- Sentiment Badge on Right -->
                    ${hasSentiment ? `
                    <div class="sentiment-info">
                        ${createSentimentBadge(sentimentLabel, sentimentScore, confidence)}
                    </div>
                    ` : ''}
                </div>
                
                <!-- Title -->
                <h3 class="news-title">${cleanTitle}</h3>
                
                <!-- Excerpt (only show if different from title) -->
                ${cleanExcerpt ? `<p class="news-excerpt">${cleanExcerpt}</p>` : ''}
            </div>
        </div>
    `;
}

function createSentimentBadge(label, score, confidence) {
    const sentimentClass = label.toLowerCase();
    const displayScore = score.toFixed(2);
    const displayConfidence = (confidence * 100).toFixed(0);

    return `
        <div class="sentiment-badge ${sentimentClass}">
            <span>${label.charAt(0).toUpperCase() + label.slice(1)}</span>
            <span class="sentiment-score">${displayScore}</span>
            <span style="color: var(--dim-gray); font-size: 0.75rem;">(${displayConfidence}%)</span>
        </div>
    `;
}

function createRecencyScore(score) {
    const scoreClass = score > 0 ? 'positive' : score < 0 ? 'negative' : 'neutral';
    const displayScore = score > 0 ? `+${score.toFixed(0)}` : score.toFixed(0);

    return `
        <div class="recency-score">
            <span class="recency-label">1h Recency</span>
            <span class="recency-value ${scoreClass}">${displayScore}</span>
        </div>
    `;
}

// ==================== Utility Functions ====================
function formatTimeAgo(date) {
    const seconds = Math.floor((new Date() - date) / 1000);

    const intervals = {
        year: 31536000,
        month: 2592000,
        week: 604800,
        day: 86400,
        hour: 3600,
        minute: 60
    };

    for (const [unit, secondsInUnit] of Object.entries(intervals)) {
        const interval = Math.floor(seconds / secondsInUnit);

        if (interval >= 1) {
            return `${interval} ${unit}${interval === 1 ? '' : 's'} ago`;
        }
    }

    return 'just now';
}

function openArticle(url) {
    window.open(url, '_blank');
}

// ==================== Make functions globally accessible ====================
window.selectStock = selectStock;
window.openArticle = openArticle;


"""
Google News Playwright Scraper - Reusable Module
Extracts full HTML and structured metadata from Google News using Playwright.
Designed to work with ticker-based searches and integrate with the backend API.
"""

import asyncio
import re
import threading
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs, quote_plus
import csv

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout
from fake_useragent import UserAgent


class GoogleNewsPlaywrightScraper:
    """Scraper for Google News with stealth capabilities and infinite scroll handling."""
    
    def __init__(self, csv_path: str = None):
        """
        Initialize the scraper.
        
        Args:
            csv_path: Path to stocks_list.csv for ticker to company name mapping
        """
        if csv_path is None:
            csv_path = Path(__file__).parent.parent / "stocks_list.csv"
        
        self.csv_path = csv_path
        self.ticker_to_company = {}
        self.ticker_to_logo = {}
        self._ticker_lookup_cache = {}
        self._sorted_company_names = []
        self._load_stock_mapping()
        
        # Generate random user agent
        ua = UserAgent()
        self.user_agent = ua.random
        
        # Scroll configuration
        self.max_scroll_attempts = 10  # Reduced for faster API responses
        self.scroll_pause_time = 1.5   # Faster scrolling
        self.no_change_limit = 2       # Stop sooner
    
    def _load_stock_mapping(self):
        """Load ticker to company name mapping from CSV"""
        try:
            with open(self.csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ticker = row.get('ticker', '').strip().upper()
                    name = row.get('name', '').strip()
                    logo = row.get('logo', '').strip()
                    
                    if ticker and name:
                        clean_name = self._clean_company_name(name)
                        self.ticker_to_company[ticker] = clean_name
                        if logo:
                            self.ticker_to_logo[ticker] = logo
            
            self._sorted_company_names = sorted(
                [(name.lower(), ticker) for ticker, name in self.ticker_to_company.items()],
                key=lambda x: -len(x[0])
            )
            print(f"[OK] Loaded {len(self.ticker_to_company)} stock mappings")
        except Exception as e:
            print(f"[ERROR] Error loading stock mappings: {e}")
            self.ticker_to_company = {}
            self._sorted_company_names = []
    
    def _clean_company_name(self, name: str) -> str:
        """Clean company name by removing common suffixes"""
        suffixes = [
            r'\s+Limited$', r'\s+Ltd\.?$', r'\s+Corporation$',
            r'\s+Corp\.?$', r'\s+Inc\.?$', r'\s+Private$',
            r'\s+Pvt\.?$', r'\s+\(India\)$', r'\s+India$'
        ]
        
        cleaned = name
        for suffix_pattern in suffixes:
            cleaned = re.sub(suffix_pattern, '', cleaned, flags=re.IGNORECASE)
        
        return cleaned.strip()
    
    def get_company_name(self, ticker: str) -> Optional[str]:
        """Get cleaned company name for a ticker"""
        return self.ticker_to_company.get(ticker.upper())
    
    async def _apply_stealth(self, page: Page):
        """Apply stealth techniques to avoid bot detection."""
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        await page.add_init_script("""
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        
        await page.add_init_script("""
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
        """)
    
    async def _scroll_to_load_all(self, page: Page) -> int:
        """Scroll to the bottom of the page repeatedly to load all articles."""
        scroll_count = 0
        no_change_count = 0
        previous_height = 0
        
        for attempt in range(self.max_scroll_attempts):
            current_height = await page.evaluate("document.body.scrollHeight")
            
            if current_height == previous_height:
                no_change_count += 1
                if no_change_count >= self.no_change_limit:
                    break
            else:
                no_change_count = 0
            
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            scroll_count += 1
            await asyncio.sleep(self.scroll_pause_time)
            previous_height = current_height
        
        return scroll_count
    
    def _decode_google_url(self, url: str) -> str:
        """Attempt to decode Google News redirect URL."""
        try:
            if '/articles/' in url:
                return url
            
            parsed = urlparse(url)
            if parsed.query:
                params = parse_qs(parsed.query)
                if 'url' in params:
                    return unquote(params['url'][0])
            
            return url
        except Exception:
            return url
    
    def _extract_ticker_from_headline(self, headline: str) -> tuple:
        """
        Extract most likely ticker from headline by matching company names.
        Returns (ticker, logo_url) tuple
        """
        if not headline:
            return (None, '')
        
        # Check cache first
        cached = self._ticker_lookup_cache.get(headline)
        if cached is not None:
            return cached
        
        headline_lower = headline.lower()
        best_match = None
        
        # Sorted by longest name first, so first match is the best
        for company_lower, ticker in self._sorted_company_names:
            if company_lower in headline_lower:
                best_match = ticker
                break
        
        if best_match:
            logo_url = self.ticker_to_logo.get(best_match, '')
            result = (best_match, logo_url)
        else:
            result = (None, '')
        
        # Cache result (limit cache size)
        if len(self._ticker_lookup_cache) > 1000:
            self._ticker_lookup_cache.clear()
        self._ticker_lookup_cache[headline] = result
        return result
    
    def _parse_relative_time(self, time_str: str) -> Optional[str]:
        """Parse relative time strings like '2 hours ago' and convert to ISO datetime."""
        if not time_str:
            return None
        
        time_str = time_str.strip()
        
        # If it's already an ISO timestamp (contains 'T' or ends with 'Z'), return unchanged
        if 'T' in time_str or time_str.endswith('Z'):
            return time_str
        
        # Convert to lowercase for pattern matching
        time_str = time_str.lower()
        now = datetime.now()
        
        # Try to match patterns and calculate actual datetime
        patterns = [
            (r'(\d+)\s*minute', lambda n: now - timedelta(minutes=int(n))),
            (r'(\d+)\s*hour', lambda n: now - timedelta(hours=int(n))),
            (r'(\d+)\s*day', lambda n: now - timedelta(days=int(n))),
            (r'(\d+)\s*week', lambda n: now - timedelta(weeks=int(n))),
            (r'(\d+)\s*month', lambda n: now - timedelta(days=int(n)*30)),
        ]
        
        for pattern, calculator in patterns:
            match = re.search(pattern, time_str)
            if match:
                dt = calculator(match.group(1))
                return dt.isoformat()
        
        # If we can't parse, return current time
        return now.isoformat()
    
    async def _extract_articles(self, page: Page, ticker: str = None) -> List[Dict[str, str]]:
        """Extract article metadata from the loaded page."""
        await asyncio.sleep(0.5)  # Wait for page to stabilize
        
        # Extract articles using Google News-specific selectors
        articles = await page.evaluate("""
            () => {
                // Google News uses <article> tags or div.m5k28 as fallback
                let articleElements = Array.from(document.querySelectorAll('div.IFHyqb'));

                
                console.log(`Found ${articleElements.length} article containers`);
                
                const results = [];
                
                articleElements.forEach((article, index) => {
                    try {
                        // Find headline using a.JtKRv (confirmed working selector)
                        let headline = '';
                        const headlineEl = article.querySelector('a.JtKRv');
                        if (headlineEl) {
                            headline = headlineEl.innerText.trim();
                        }
                        
                        // Find article link - try a.WwrzSb first, then fall back to headline link
                        let url = '';
                        const linkEl = article.querySelector('a.WwrzSb') || headlineEl;
                        if (linkEl) {
                            const href = linkEl.getAttribute('href');
                            if (href) {
                                url = href.startsWith('http') ? href : `https://news.google.com${href}`;
                            }
                        }
                        
                        // Find source - could be in various places
                        let source = '';
                        const sourceEl = article.querySelector('div.vr1PYe, div.MCAGUe');
                        if (sourceEl) {
                            source = sourceEl.innerText.trim();
                        }
                        
                        // Find published time - try multiple selectors
                        let publishedTime = '';
                        const timeEl = article.querySelector('time.hvbAAd');
                        if (timeEl) {
                            publishedTime = timeEl.getAttribute('datetime') || timeEl.innerText.trim();
                        } else {
                            // Fallback: look for spans that might contain time text
                            const allSpans = article.querySelectorAll('span');
                            for (const span of allSpans) {
                                const text = span.innerText.trim().toLowerCase();
                                if (text.match(/\d+\s*(hour|day|week|month|minute|min)/)) {
                                    publishedTime = text;
                                    break;
                                }
                            }
                        }
                        
                        // NEW: Find article snippet/description for better sentiment analysis
                        let snippet = '';
                        // Try multiple possible selectors for article preview text
                        const snippetEl = article.querySelector('div.GI74Re, div.Y3v8qd, div.Rai5ob, span.xBbh9');
                        if (snippetEl) {
                            snippet = snippetEl.innerText.trim();
                        }
                        
                        // Only add if we have at least a headline
                        if (headline) {
                            results.push({
                                headline: headline,
                                snippet: snippet,  // Include snippet
                                source: source,
                                url: url,
                                published_time: publishedTime,
                                index: index
                            });
                        }
                    } catch (e) {
                        console.log(`Error parsing article ${index}:`, e);
                    }
                });
                
                console.log(`Successfully parsed ${results.length} articles`);
                return results;
            }
        """)
        
        # Post-process articles
        processed_articles = []
        for article in articles:
            if article['url']:
                article['url'] = self._decode_google_url(article['url'])
            
            if article['published_time']:
                article['published_time'] = self._parse_relative_time(article['published_time'])
            
            # Add ticker and logo info
            if ticker:
                # Specific ticker mode - use provided ticker
                article['ticker'] = ticker.upper()
                article['logo_url'] = self.ticker_to_logo.get(ticker.upper(), '')
            else:
                # All stocks mode - extract ticker from headline
                extracted_ticker, logo_url = self._extract_ticker_from_headline(article.get('headline', ''))
                article['ticker'] = extracted_ticker or 'UNKNOWN'
                article['logo_url'] = logo_url
            
            processed_articles.append(article)
        
        return processed_articles
    
    async def _get_browser(self):
        """Get or create a persistent Playwright browser instance."""
        if not hasattr(self, '_playwright') or self._playwright is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            print(f"[Playwright] Browser launched (persistent mode)")
        return self._browser

    async def close_browser(self):
        """Close the persistent browser if open."""
        if hasattr(self, '_browser') and self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if hasattr(self, '_playwright') and self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
            print(f"[Playwright] Browser closed")

    async def scrape_for_ticker(self, ticker: str, limit: int = 20) -> List[Dict]:
        """
        Scrape Google News for a specific ticker.
        
        Args:
            ticker: Stock ticker symbol (e.g., 'RELIANCE')
            limit: Maximum number of articles to return
            
        Returns:
            List of article dictionaries
        """
        ticker = ticker.upper()
        company_name = self.get_company_name(ticker)
        
        if not company_name:
            print(f"[WARNING] Ticker {ticker} not found in mapping")
            company_name = ticker
        
        # Build search query with proper URL encoding
        # Search for articles mentioning scanx.trade and the company name
        query = f"scanx.trade {company_name}"
        encoded_query = quote_plus(query)
        base_url = f"https://news.google.com/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        print(f"\n[SCRAPE] Fetching news for {ticker} ({company_name})")
        print(f"[SCRAPE] URL: {base_url}")
        
        browser = await self._get_browser()
        page = await browser.new_page()
        
        try:
            await page.goto(base_url, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(8)
            
            print(f"[SCRAPE] Skipping scroll - extracting articles immediately")
            
            articles = await self._extract_articles(page, ticker)
            articles = articles[:limit]
            
            print(f"[SCRAPE] Extracted {len(articles)} articles for {ticker}\n")
            
            return articles
            
        except PlaywrightTimeout as e:
            print(f"[ERROR] Timeout: {e}")
            return []
        except Exception as e:
            print(f"[ERROR] Scraping failed: {e}")
            return []
        finally:
            await page.close()
    
    async def scrape_all_news(self, limit: int = 100) -> List[Dict]:
        """
        Scrape general market news from Google News.
        
        Args:
            limit: Maximum number of articles to return
            
        Returns:
            List of article dictionaries
        """
        query = "scanx.trade"
        encoded_query = quote_plus(query)
        base_url = f"https://news.google.com/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        print(f"\n[SCRAPE] Fetching all news")
        print(f"[SCRAPE] URL: {base_url}")
        
        browser = await self._get_browser()
        page = await browser.new_page()
        
        try:
            await page.goto(base_url, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(1)
            
            print(f"[SCRAPE] Skipping scroll - extracting articles immediately")
            
            articles = await self._extract_articles(page)
            articles = articles[:limit]
            
            print(f"[SCRAPE] Extracted {len(articles)} articles\n")
            
            return articles
            
        except PlaywrightTimeout as e:
            print(f"[ERROR] Timeout: {e}")
            return []
        except Exception as e:
            print(f"[ERROR] Scraping failed: {e}")
            return []
        finally:
            await page.close()


# Module-level scraper singleton — persistent browser reused across calls from the same event loop
_shared_scraper = None
_shared_scraper_lock = threading.Lock()

def _get_shared_scraper():
    global _shared_scraper
    if _shared_scraper is None:
        with _shared_scraper_lock:
            if _shared_scraper is None:
                _shared_scraper = GoogleNewsPlaywrightScraper()
    return _shared_scraper

# Synchronous wrapper for use in non-async contexts
def scrape_ticker_sync(ticker: str, limit: int = 20) -> List[Dict]:
    """Synchronous wrapper for scrape_for_ticker"""
    scraper = _get_shared_scraper()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(scraper.scrape_for_ticker(ticker, limit))
    finally:
        loop.close()


def scrape_all_sync(limit: int = 100) -> List[Dict]:
    """Synchronous wrapper for scrape_all_news"""
    scraper = _get_shared_scraper()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(scraper.scrape_all_news(limit))
    finally:
        loop.close()

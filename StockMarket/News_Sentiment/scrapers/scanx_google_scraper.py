"""
ScanX.trade News Scraper via Google News RSS

This module fetches news articles exclusively from scanx.trade domain
using Google News RSS API. It maps ticker symbols to company names and
cleans them for better search results.
"""

import feedparser
import csv
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from pathlib import Path
from urllib.parse import quote_plus


class ScanXGoogleScraper:
    """
    Scrape scanx.trade news from Google News RSS
    """
    
    def __init__(self, csv_path: str = None):
        """
        Initialize the scraper with stock list CSV
        
        Args:
            csv_path: Path to stocks_list.csv file
        """
        if csv_path is None:
            # Default to project root
            csv_path = Path(__file__).parent.parent / "stocks_list.csv"
        
        self.csv_path = csv_path
        self.ticker_to_company = {}
        self.company_to_ticker = {}
        self.ticker_to_logo = {}  # Store logo URLs
        self._load_stock_mapping()
    
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
                        # Clean company name (remove Limited, Ltd, etc.)
                        clean_name = self._clean_company_name(name)
                        self.ticker_to_company[ticker] = clean_name
                        self.company_to_ticker[clean_name.lower()] = ticker
                        
                        # Store logo URL
                        if logo:
                            self.ticker_to_logo[ticker] = logo
            
            print(f"[OK] Loaded {len(self.ticker_to_company)} stock mappings")
        except Exception as e:
            print(f"[ERROR] Error loading stock mappings: {e}")
            # Fallback for common stocks
            self.ticker_to_company = {
                'RELIANCE': 'Reliance Industries',
                'TCS': 'Tata Consultancy Services',
                'HDFCBANK': 'HDFC Bank',
                'INFY': 'Infosys',
                'ICICIBANK': 'ICICI Bank'
            }
    
    def _clean_company_name(self, name: str) -> str:
        """
        Clean company name by removing common suffixes
        
        Examples:
            "Reliance Industries Limited" -> "Reliance Industries"
            "Tata Consultancy Services Ltd." -> "Tata Consultancy Services"
        """
        # Remove common suffixes (case insensitive)
        suffixes = [
            r'\s+Limited$',
            r'\s+Ltd\.?$',
            r'\s+Corporation$',
            r'\s+Corp\.?$',
            r'\s+Inc\.?$',
            r'\s+Private$',
            r'\s+Pvt\.?$',
            r'\s+\(India\)$',
            r'\s+India$'
        ]
        
        cleaned = name
        for suffix_pattern in suffixes:
            cleaned = re.sub(suffix_pattern, '', cleaned, flags=re.IGNORECASE)
        
        return cleaned.strip()
    
    def _extract_ticker_from_title(self, title: str) -> str:
        """Extract stock ticker from article title"""
        title_lower = title.lower()
        
        # Search for company names in title
        for ticker, company_name in self.ticker_to_company.items():
            if company_name.lower() in title_lower:
                return ticker
        
        # Search for ticker symbols (3-12 uppercase letters)
        import re
        tickers = re.findall(r'\b[A-Z]{3,12}\b', title)
        for t in tickers:
            if t in self.ticker_to_company:
                return t
        
        return 'UNKNOWN'
    
    def fetch_all_scanx_news(self, limit: int = 100, days: int = 30) -> List[Dict]:
        """
        Fetch all recent scanx.trade news (general market news)
        
        Args:
            limit: Maximum number of articles to return
            days: Number of days to look back
            
        Returns:
            List of news articles
        """
        query = "site:scanx.trade"
        return self._fetch_from_google_news(query, "ALL", limit, days)
    
    def fetch_fresh_news(self, ticker: str = "ALL", limit: int = 100) -> List[Dict]:
        """
        Fetch the FRESHEST possible news using cascading time windows
        This tries to get news from last hour first, then falls back to older periods
        
        Args:
            ticker: Stock ticker or "ALL" for all news
            limit: Maximum number of articles
            
        Returns:
            List of fresh news articles
        """
        all_articles = []
        seen_urls = set()
        
        # Define time windows to try (newest first)
        time_windows = [
            ('1h', '1 hour'),
            ('6h', '6 hours'),
            ('1d', '1 day'),
        ]
        
        print(f"\n[FRESH NEWS] Fetching freshest news for {ticker}")
        
        # Build base query
        if ticker == "ALL":
            base_query = "site:scanx.trade"
        else:
            company_name = self.ticker_to_company.get(ticker.upper(), ticker)
            base_query = f'site:scanx.trade ({ticker} OR "{company_name}")'
        
        # Try each time window
        for when, label in time_windows:
            print(f"  Trying last {label}...")
            
            articles = self._fetch_from_google_news(
                query=base_query,
                ticker=ticker,
                limit=limit * 2,
                days=1,
                when=when  # Use time filter
            )
            
            # Add unique articles
            new_count = 0
            for article in articles:
                if article['url'] not in seen_urls:
                    seen_urls.add(article['url'])
                    all_articles.append(article)
                    new_count += 1
            
            print(f"    Found {new_count} new articles (total: {len(all_articles)})")
            
            # Stop if we have enough
            if len(all_articles) >= limit:
                break
        
        # Sort by date and limit
        all_articles.sort(key=lambda x: x['published_at'], reverse=True)
        all_articles = all_articles[:limit]
        
        if all_articles:
            newest = all_articles[0]['published_at'].strftime('%Y-%m-%d %H:%M')
            print(f"[FRESH NEWS] Returning {len(all_articles)} articles (newest: {newest})\n")
        else:
            print(f"[FRESH NEWS] No articles found\n")
        
        return all_articles
    
    def fetch_scanx_news_for_ticker(self, ticker: str, limit: int = 100, days: int = 30) -> List[Dict]:
        """
        Fetch scanx.trade news for specific ticker using multiple queries
        
        Args:
            ticker: Stock ticker symbol (e.g., 'RELIANCE')
            limit: Maximum number of articles to return
            days: Number of days to look back
            
        Returns:
            List of news articles
        """
        ticker = ticker.upper()
        
        # Get company name
        company_name = self.ticker_to_company.get(ticker)
        if not company_name:
            print(f"⚠ Ticker {ticker} not found in mapping, using ticker name")
            company_name = ticker
        
        # Use MULTIPLE queries with priority on ticker appearing first in title
        # This helps ensure the stock is the PRIMARY subject, not just mentioned
        queries = [
            f'site:scanx.trade {ticker}',  # Ticker first - most relevant
            f'site:scanx.trade "{company_name}"',  # Exact company name match
        ]
        
        all_articles = []
        seen_urls = set()  # Track unique URLs to avoid duplicates
        
        print(f"\n[MULTI-QUERY] Fetching news for {ticker} using {len(queries)} queries")
        
        for query_idx, query in enumerate(queries, 1):
            print(f"  Query {query_idx}/{len(queries)}: {query}")
            
            articles = self._fetch_from_google_news(query, ticker, limit * 2, days)
            
            # Add only unique articles
            new_count = 0
            for article in articles:
                if article['url'] not in seen_urls:
                    seen_urls.add(article['url'])
                    all_articles.append(article)
                    new_count += 1
            
            print(f"    Added {new_count} unique articles (total now: {len(all_articles)})")
        
        # ========== ADDITIONAL FILTERING FOR RELEVANCE ==========
        # Filter out articles where the searched stock is only mentioned,
        # but another stock is the main subject
        filtered_articles = []
        
        for article in all_articles:
            title_lower = article['title'].lower()
            company_lower = company_name.lower()
            ticker_lower = ticker.lower()
            
            # Check if our company/ticker appears in the title
            has_company = company_lower in title_lower
            has_ticker = ticker_lower in title_lower
            
            if not has_company and not has_ticker:
                # Skip if neither company nor ticker mentioned in title
                continue
            
            # ===== EXCLUSION RULES =====
            # Skip if stock is mentioned as "backed by", "subsidiary of", etc.
            backed_patterns = ['-backed', 'backed by', 'backed firm', 'subsidiary']
            is_backed = any(pattern in title_lower for pattern in backed_patterns)
            
            # Skip if it's a list article ("Among Top", "Top 10", etc.)
            list_patterns = ['among top', 'top 10', 'top ten', 'top ipos']
            is_list = any(pattern in title_lower for pattern in list_patterns)
            
            if is_backed or is_list:
                continue  # Skip these types of articles
            
            # ===== INCLUSION RULES =====
            # Check if title starts with a different company name
            # This helps filter "XYZ Company Secures Contract from Reliance..." type articles
            title_start = title_lower[:30]  # First 30 chars
            if has_company or has_ticker:
                # If our stock is mentioned, check if it's at the start (primary subject)
                position = title_lower.find(company_lower) if has_company else title_lower.find(ticker_lower)
                
                # If our stock appears in first 40% of title, it's likely the main subject
                if position < len(title_lower) * 0.4:
                    filtered_articles.append(article)
                # Or if the title has our stock name but is a general market news
                elif 'market' in title_lower or 'ipo' in title_lower or 'stocks' in title_lower:
                    filtered_articles.append(article)
                # Or if it's the only company mentioned
                else:
                    # Count how many other company names appear
                    other_companies = 0
                    for other_ticker, other_name in self.ticker_to_company.items():
                        if other_ticker != ticker and other_name.lower() in title_lower:
                            other_companies += 1
                
                    # If no other companies mentioned, include it
                    if other_companies == 0:
                        filtered_articles.append(article)
        
        # Sort filtered articles by published date (newest first) and limit
        filtered_articles.sort(key=lambda x: x['published_at'], reverse=True)
        filtered_articles = filtered_articles[:limit]
        
        print(f"[MULTI-QUERY] Filtered to {len(filtered_articles)} relevant articles (from {len(all_articles)} total)\n")
        
        return filtered_articles
    
    def _fetch_from_google_news(self, query: str, ticker: str, limit: int, days: int, when: str = None) -> List[Dict]:
        """
        Fetch articles from Google News RSS with optional time filtering
        
        Args:
            query: Search query
            ticker: Ticker symbol to tag articles with
            limit: Maximum articles
            days: Days to look back
            when: Time filter - '1h', '6h', '1d', '7d' for fresher results
            
        Returns:
            List of articles
        """
        base_url = "https://news.google.com/rss/search"
        # URL encode the query to handle spaces and special characters
        encoded_query = quote_plus(query)
        rss_url = f"{base_url}?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        # Add time filter if specified (makes results fresher)
        if when:
            rss_url += f"&when={when}"
        
        try:
            print(f"[SEARCH] Fetching: {query}")
            feed = feedparser.parse(rss_url)
            articles = []
            
            # Don't use strict date cutoff - Google News doesn't filter by date
            # Process all entries from RSS feed to match Google News behavior
            
            for entry in feed.entries:  # Process ALL entries, not just a subset
                # Parse published date
                pub_date = self._parse_published_date(
                    entry.published if hasattr(entry, 'published') else None
                )
                
                # No date filtering - we'll sort by date and limit after
                
                # CRITICAL: Check if source is scanx.trade
                # Google News RSS uses redirect URLs, so we check the source tag
                is_scanx = False
                if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
                    source_title = entry.source.title.lower()
                    is_scanx = 'scanx.trade' in source_title or 'scanx' in source_title
                
                # Also check title as fallback
                if not is_scanx and hasattr(entry, 'title'):
                    is_scanx = 'scanx.trade' in entry.title.lower()
                
                if not is_scanx:
                    continue
                
                # Get URL (Google News redirect URL is fine, it will redirect to scanx.trade)
                url = entry.link if hasattr(entry, 'link') else ''
                
                # Extract title
                title = entry.title if hasattr(entry, 'title') else ''
                
                # ========== FILTER OUT IRRELEVANT ARTICLES ==========
                # Skip generic pages and non-news content
                
                exclude_patterns = [
                    # Generic price pages
                    'share price today',
                    'stock price',
                    'nse/bse',
                    'live price',
                    
                    # Generic site pages
                    'stock screener',
                    'market research platform',
                    'squeezing range',
                    'put btst',
                    'stocks selection',
                    'stock holdings',
                    'mutual funds',
                    'portfolio',
                    'screener',
                    
                    # Technical indicator pages / Screeners
                    'rsi -',  # "RSI - scanx.trade"
                    'moving average',
                    'swing trade',
                    'intraday scanner',
                    'momentum -',
                    'total mkt',
                    'eq wm',
                    'price down',
                    'price up',
                    '15 mins with',
                    'my scan',
                    'rsi below',
                    'rsi above',
                    'rsi oversold',
                    'oversold rsi',
                    'negative stock',
                    'f&o movers',
                    'filter ',
                    'quartely',
                    'crossover',
                ]
                
                # Check if title contains any exclude pattern
                title_lower = title.lower()
                should_exclude = any(pattern in title_lower for pattern in exclude_patterns)
                
                # Also exclude very short titles (likely screener names)
                # Real news articles have descriptive titles
                title_without_source = title.replace(' - scanx.trade', '').replace(' scanx.trade', '').strip()
                if len(title_without_source) < 30:  # Too short to be a real news title
                    should_exclude = True
                
                if should_exclude:
                    continue
                
                # For ticker-specific queries, filter only if clearly about a DIFFERENT specific stock
                # Keep general industry news (e.g., "Major Banks...", "Credit Card Updates")
                if ticker != "ALL":
                    import re
                    potential_tickers = re.findall(r'\b[A-Z]{2,}\b', title)
                    
                    if potential_tickers:
                        # Remove common words
                        common_words = {'IPO', 'NSE', 'BSE', 'CEO', 'CFO', 'MD', 'USA', 'UAE', 'UK', 'US', 'IT', 'AI', 'HR', 'PR', 'GST', 'RBI', 'SEBI', 'FD', 'FII', 'DII'}
                        actual_tickers = [t for t in potential_tickers if t not in common_words]
                        
                        # Only filter if there's EXACTLY ONE other specific stock ticker (not ours)
                        # This keeps "HDFC, ICICI, IDFC Banks..." but filters "VIVIMELAB announces..."
                        if len(actual_tickers) == 1 and ticker not in actual_tickers:
                            other_ticker = actual_tickers[0]
                            # Filter only if it's a known stock ticker (4+ chars)
                            if len(other_ticker) >= 4:
                                continue
                
                # =================================================
                
                # Extract excerpt/summary
                excerpt = ''
                if hasattr(entry, 'summary'):
                    excerpt = self._clean_html(entry.summary)
                elif hasattr(entry, 'description'):
                    excerpt = self._clean_html(entry.description)
                
                # Extract actual ticker for ALL stocks
                actual_ticker = ticker if ticker != "ALL" else self._extract_ticker_from_title(title)
                
                articles.append({
                    'ticker': actual_ticker,
                    'title': title,
                    'url': url,
                    'source': 'scanx.trade',
                    'published_at': pub_date,
                    'excerpt': excerpt[:300],  # Limit excerpt length
                    'logo_url': self.ticker_to_logo.get(actual_ticker, '')
                })
                
            # Sort by published date (newest first)
            articles.sort(key=lambda x: x['published_at'], reverse=True)
            
            # Limit to requested number AFTER sorting
            articles = articles[:limit]
            
            print(f"[OK] Found {len(articles)} scanx.trade articles (sorted by newest first)")
            return articles
            
        except Exception as e:
            print(f"[ERROR] Error fetching from Google News: {e}")
            return []
    
    def _is_scanx_url(self, url: str) -> bool:
        """Check if URL is from scanx.trade domain"""
        return 'scanx.trade' in url.lower()
    
    def _parse_published_date(self, pub_str: Optional[str]) -> datetime:
        """Parse published date from RSS"""
        if not pub_str:
            return datetime.now(timezone.utc)
        
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(pub_str)
            # Ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except:
            return datetime.now(timezone.utc)
    
    def _clean_html(self, html: str) -> str:
        """Remove HTML tags from text"""
        if not html:
            return ''
        
        # Simple HTML tag removal
        clean = re.sub(r'<[^>]+>', '', html)
        clean = re.sub(r'\s+', ' ', clean)
        return clean.strip()
    
    def get_company_name(self, ticker: str) -> Optional[str]:
        """Get cleaned company name for a ticker"""
        return self.ticker_to_company.get(ticker.upper())


# Example usage and testing
if __name__ == "__main__":
    scraper = ScanXGoogleScraper()
    
    print("\n" + "="*60)
    print("Testing ScanX.trade News Scraper")
    print("="*60)
    
    # Test 1: Fetch all news
    print("\n1. Fetching all scanx.trade news...")
    all_news = scraper.fetch_all_scanx_news(limit=5)
    for idx, article in enumerate(all_news[:3], 1):
        print(f"\n   [{idx}] {article['title']}")
        print(f"       URL: {article['url']}")
        print(f"       Published: {article['published_at'].strftime('%Y-%m-%d %H:%M')}")
    
    # Test 2: Fetch for specific ticker
    print("\n2. Fetching news for RELIANCE...")
    company_name = scraper.get_company_name('RELIANCE')
    print(f"   Company name: {company_name}")
    reliance_news = scraper.fetch_scanx_news_for_ticker('RELIANCE', limit=5)
    for idx, article in enumerate(reliance_news[:3], 1):
        print(f"\n   [{idx}] {article['title']}")
        print(f"       URL: {article['url']}")
    
    # Test 3: Test company name cleaning
    print("\n3. Testing company name cleaning...")
    test_names = [
        "Reliance Industries Limited",
        "Tata Consultancy Services Ltd.",
        "HDFC Bank Limited",
        "Infosys Limited"
    ]
    for name in test_names:
        cleaned = scraper._clean_company_name(name)
        print(f"   {name:45} -> {cleaned}")
    
    print("\n" + "="*60)

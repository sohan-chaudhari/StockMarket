import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict
import time
from fake_useragent import UserAgent
from urllib.parse import quote_plus  # Add URL encoding support


class GoogleNewsScraper:
    """
    Scrape Google News RSS for Indian stock news
    """
    
    def __init__(self):
        self.ua = UserAgent()
        self.base_url = "https://news.google.com/rss/search"
    
    def fetch_news(self, query: str, ticker: str, limit: int = 20) -> List[Dict]:
        """
        Fetch news from Google News RSS - ONLY articles from scanx.trade
        """
        # Search query: ONLY scanx.trade articles about the company
        # Using (TICKER OR "Company Name") to catch all variations
        # E.g., "TCS" matches short mentions, "Tata Consultancy Services" matches full names
        search_query = f'site:scanx.trade ({ticker} OR "{query}")'
        
        # Properly URL-encode the search query to handle spaces and special characters
        encoded_query = quote_plus(search_query)
        rss_url = f"{self.base_url}?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        try:
            feed = feedparser.parse(rss_url)
            articles = []
            
            for entry in feed.entries[:limit]:
                # Parse published date
                pub_date = self._parse_published(entry.published if hasattr(entry, 'published') else None)
                
                # Extract source
                source = "Google News"
                if hasattr(entry, 'source'):
                    source = entry.source.title
                
                articles.append({
                    "ticker": ticker,
                    "title": entry.title,
                    "url": entry.link,
                    "source": source,
                    "published_at": pub_date,
                    "excerpt": entry.summary if hasattr(entry, 'summary') else ""
                })
            
            return articles
            
        except Exception as e:
            print(f"Error fetching Google News: {e}")
            return []
    
    def _parse_published(self, pub_str: str) -> datetime:
        """Parse published date from RSS"""
        if not pub_str:
            return datetime.utcnow()
        
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(pub_str)
        except:
            return datetime.utcnow()


class ScanXNewsScraper:
    """
    Scrape news from scanx.trade for specific tickers
    """
    
    def __init__(self):
        self.ua = UserAgent()
        self.base_url = "https://scanx.trade"
    
    def fetch_ticker_news(self, ticker: str, limit: int = 20) -> List[Dict]:
        """
        Fetch news for a specific ticker from scanx.trade
        
        Note: This is a generic scraper. Adjust based on actual scanx.trade structure.
        """
        headers = {'User-Agent': self.ua.random}
        
        # Try different URL patterns
        urls_to_try = [
            f"{self.base_url}/stock/{ticker}/news",
            f"{self.base_url}/stocks/{ticker}/news",
            f"{self.base_url}/news?symbol={ticker}",
        ]
        
        for url in urls_to_try:
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    articles = self._parse_news_page(response.text, ticker)
                    if articles:
                        return articles[:limit]
                    
                time.sleep(1)  # Rate limiting
                
            except Exception as e:
                print(f"Failed to fetch from {url}: {e}")
                continue
        
        return []
    
    def _parse_news_page(self, html: str, ticker: str) -> List[Dict]:
        """
        Parse scanx.trade news page HTML
        """
        soup = BeautifulSoup(html, 'html.parser')
        articles = []
        
        # Common patterns for news items
        news_containers = soup.find_all(['article', 'div'], class_=['news-item', 'news-card', 'article'])
        
        if not news_containers:
            # Fallback: find all links
            news_containers = soup.find_all('a', href=True)
        
        for item in news_containers[:50]:  # Parse first 50 items
            try:
                # Extract title
                title_elem = item.find(['h2', 'h3', 'h4', 'a', 'span'])
                if not title_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                if len(title) < 20:  # Skip too short titles
                    continue
                
                # Extract URL
                url = item.get('href')
                if not url:
                    link_elem = item.find('a', href=True)
                    url = link_elem.get('href') if link_elem else None
                
                if not url:
                    continue
                
                # Make absolute URL
                if url.startswith('/'):
                    url = f"{self.base_url}{url}"
                elif not url.startswith('http'):
                    url = f"{self.base_url}/{url}"
                
                # Extract source
                source = "scanx.trade"
                
                # Extract excerpt
                excerpt_elem = item.find(['p', 'div'], class_=['excerpt', 'description'])
                excerpt = excerpt_elem.get_text(strip=True)[:200] if excerpt_elem else ""
                
                articles.append({
                    "ticker": ticker,
                    "title": title,
                    "url": url,
                    "source": source,
                    "published_at": datetime.utcnow(),
                    "excerpt": excerpt
                })
                
            except Exception as e:
                continue
        
        return articles


class HybridNewsScraper:
    """
    Combine Google News + scanx.trade with deduplication
    """
    
    def __init__(self):
        self.google_scraper = GoogleNewsScraper()
        self.scanx_scraper = ScanXNewsScraper()
    
    def fetch_all_news(self, ticker: str, company_name: str) -> List[Dict]:
        """
        Fetch from both sources and deduplicate
        """
        all_articles = []
        
        # 1. Fetch from scanx.trade
        print(f"Fetching from scanx.trade for {ticker}...")
        try:
            scanx_articles = self.scanx_scraper.fetch_ticker_news(ticker)
            all_articles.extend(scanx_articles)
            print(f"  Found {len(scanx_articles)} articles from scanx.trade")
        except Exception as e:
            print(f"  scanx.trade failed: {e}")
        
        # 2. Fetch from Google News (scanx.trade articles only)
        print(f"Fetching from Google News for scanx.trade articles about {company_name}...")
        try:
            google_articles = self.google_scraper.fetch_news(company_name, ticker)
            all_articles.extend(google_articles)
            print(f"  Found {len(google_articles)} scanx.trade articles from Google News")
        except Exception as e:
            print(f"  Google News failed: {e}")
        
        # 3. Deduplicate
        deduped = self._deduplicate(all_articles)
        print(f"After deduplication: {len(deduped)} unique articles")
        
        # 4. Sort by recency
        deduped.sort(key=lambda x: x["published_at"], reverse=True)
        
        return deduped
    
    def _deduplicate(self, articles: List[Dict]) -> List[Dict]:
        """
        Remove duplicates by URL and title similarity
        """
        from fuzzywuzzy import fuzz
        
        seen_urls = set()
        seen_titles = []
        unique = []
        
        for article in articles:
            url = article["url"]
            title = article["title"].lower()
            
            # Check URL
            if url in seen_urls:
                continue
            
            # Check title similarity
            is_duplicate = False
            for seen_title in seen_titles:
                similarity = fuzz.ratio(title, seen_title)
                if similarity > 85:  # 85% similar = duplicate
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                unique.append(article)
                seen_urls.add(url)
                seen_titles.append(title)
        
        return unique

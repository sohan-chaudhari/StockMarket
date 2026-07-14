"""
Google News Full Web Scraper
Extracts full HTML and structured metadata from Google News search results.
Uses Playwright with stealth mode to avoid bot detection.
Handles infinite scrolling to capture all articles.
"""

import asyncio
import json
import random
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs
import re

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout
from fake_useragent import UserAgent


class GoogleNewsScraper:
    """Scraper for Google News with stealth capabilities and infinite scroll handling."""
    
    def __init__(self, query: str, output_dir: str = "."):
        """
        Initialize the scraper.
        
        Args:
            query: Search query (e.g., "scanx.trade")
            output_dir: Directory to save output files
        """
        self.query = query
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate random user agent
        ua = UserAgent()
        self.user_agent = ua.random
        
        # Target URL
        self.base_url = (
            f"https://news.google.com/search?"
            f"q={query}&hl=en-US&gl=US&ceid=US:en"
        )
        
        # Scroll configuration
        self.max_scroll_attempts = 20  # Maximum number of scroll attempts
        self.scroll_pause_time = 2  # Seconds to wait between scrolls
        self.no_change_limit = 3  # Stop if content doesn't change after N scrolls
        
    async def _apply_stealth(self, page: Page):
        """Apply stealth techniques to avoid bot detection."""
        # Override navigator.webdriver
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        # Override permissions
        await page.add_init_script("""
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        
        # Override plugins
        await page.add_init_script("""
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
        """)
        
        # Override languages
        await page.add_init_script("""
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        """)
    
    async def _scroll_to_load_all(self, page: Page) -> int:
        """
        Scroll to the bottom of the page repeatedly to load all articles.
        
        Returns:
            Number of scroll attempts made
        """
        print("Starting infinite scroll...")
        scroll_count = 0
        no_change_count = 0
        previous_height = 0
        
        for attempt in range(self.max_scroll_attempts):
            # Get current scroll height
            current_height = await page.evaluate("document.body.scrollHeight")
            
            # Check if height changed
            if current_height == previous_height:
                no_change_count += 1
                print(f"  No new content loaded (attempt {no_change_count}/{self.no_change_limit})")
                
                if no_change_count >= self.no_change_limit:
                    print("  Reached bottom - no more content loading")
                    break
            else:
                no_change_count = 0
                print(f"  Scroll {attempt + 1}: Loaded more content (height: {current_height})")
            
            # Scroll to bottom
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            scroll_count += 1
            
            # Wait for new content to load
            await asyncio.sleep(self.scroll_pause_time)
            
            # Random additional delay to appear more human-like
            await asyncio.sleep(random.uniform(0.5, 1.5))
            
            previous_height = current_height
        
        print(f"Completed scrolling after {scroll_count} attempts")
        return scroll_count
    
    def _decode_google_url(self, url: str) -> str:
        """
        Attempt to decode Google News redirect URL to get the actual article URL.
        
        Args:
            url: Google News URL (may be a redirect)
            
        Returns:
            Decoded URL or original URL if decoding fails
        """
        try:
            # Google News URLs often contain the actual URL in a specific pattern
            if '/articles/' in url:
                # Extract the article ID and construct direct link
                return url
            
            # Try to parse query parameters for redirect URLs
            parsed = urlparse(url)
            if parsed.query:
                params = parse_qs(parsed.query)
                if 'url' in params:
                    return unquote(params['url'][0])
            
            return url
        except Exception as e:
            print(f"  Warning: Could not decode URL: {e}")
            return url
    
    def _parse_relative_time(self, time_str: str) -> Optional[str]:
        """
        Parse relative time strings like '2 hours ago' into a standardized format.
        
        Args:
            time_str: Relative time string
            
        Returns:
            Standardized time description or original string
        """
        if not time_str:
            return None
        
        time_str = time_str.strip().lower()
        
        # Common patterns
        patterns = {
            r'(\d+)\s*hour': lambda n: f"{n} hours ago",
            r'(\d+)\s*day': lambda n: f"{n} days ago",
            r'(\d+)\s*week': lambda n: f"{n} weeks ago",
            r'(\d+)\s*month': lambda n: f"{n} months ago",
            r'(\d+)\s*min': lambda n: f"{n} minutes ago",
        }
        
        for pattern, formatter in patterns.items():
            match = re.search(pattern, time_str)
            if match:
                return formatter(match.group(1))
        
        return time_str
    
    async def _extract_articles(self, page: Page) -> List[Dict[str, str]]:
        """
        Extract article metadata from the loaded page.
        
        Args:
            page: Playwright page object
            
        Returns:
            List of article dictionaries
        """
        print("Extracting article metadata...")
        
        # Wait for page to stabilize
        await asyncio.sleep(2)
        
        # Extract articles using Google News-specific selectors
        articles = await page.evaluate("""
            () => {
                // Google News uses div.m5k28 as article containers
                const articleElements = Array.from(document.querySelectorAll('div.m5k28'));
                console.log(`Found ${articleElements.length} article containers`);
                
                const results = [];
                
                articleElements.forEach((article, index) => {
                    try {
                        // Find headline using a.JtKRv
                        let headline = '';
                        const headlineEl = article.querySelector('a.JtKRv');
                        if (headlineEl) {
                            headline = headlineEl.innerText.trim();
                        }
                        
                        // Find article link from headline link
                        let url = '';
                        if (headlineEl) {
                            const href = headlineEl.getAttribute('href');
                            if (href) {
                                url = href.startsWith('http') ? href : `https://news.google.com${href}`;
                            }
                        }
                        
                        // Find source using div.vr1PYe
                        let source = '';
                        const sourceEl = article.querySelector('div.vr1PYe');
                        if (sourceEl) {
                            source = sourceEl.innerText.trim();
                        }
                        
                        // Find published time using time.hvbAAd
                        let publishedTime = '';
                        const timeEl = article.querySelector('time.hvbAAd');
                        if (timeEl) {
                            // Try to get datetime attribute first, fallback to text
                            publishedTime = timeEl.getAttribute('datetime') || timeEl.innerText.trim();
                        }
                        
                        // Only add if we have at least a headline
                        if (headline) {
                            results.push({
                                headline: headline,
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
            # Decode Google URL
            if article['url']:
                article['url'] = self._decode_google_url(article['url'])
            
            # Parse relative time
            if article['published_time']:
                article['published_time'] = self._parse_relative_time(article['published_time'])
            
            processed_articles.append(article)
        
        print(f"  Extracted {len(processed_articles)} articles")
        return processed_articles
    
    async def scrape(self) -> Dict[str, any]:
        """
        Execute the full scraping process.
        
        Returns:
            Dictionary with scraping results and metadata
        """
        print(f"\n{'='*60}")
        print(f"Google News Scraper")
        print(f"{'='*60}")
        print(f"Query: {self.query}")
        print(f"Target URL: {self.base_url}")
        print(f"User-Agent: {self.user_agent[:50]}...")
        print(f"{'='*60}\n")
        
        async with async_playwright() as p:
            # Launch browser in headless mode
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled',
                ]
            )
            
            # Create context with custom user agent
            context = await browser.new_context(
                user_agent=self.user_agent,
                viewport={'width': 1920, 'height': 1080}
            )
            
            # Create page
            page = await context.new_page()
            
            # Apply stealth techniques
            await self._apply_stealth(page)
            
            try:
                # Navigate to Google News
                print(f"Navigating to Google News...")
                await page.goto(self.base_url, wait_until='networkidle', timeout=30000)
                print("  Page loaded successfully")
                
                # Wait for initial content
                await asyncio.sleep(2)
                
                # Perform infinite scroll
                scroll_count = await self._scroll_to_load_all(page)
                
                # Extract article data
                articles = await self._extract_articles(page)
                
                # Get full HTML
                print("Extracting full page HTML...")
                html_content = await page.content()
                print(f"  HTML size: {len(html_content):,} bytes")
                
                # Save HTML file
                html_filename = f"raw_{self.query.replace(' ', '_').replace('.', '_')}_results.html"
                html_path = self.output_dir / html_filename
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                print(f"  Saved HTML to: {html_path}")
                
                # Save JSON file
                json_filename = f"{self.query.replace(' ', '_').replace('.', '_')}_news_data.json"
                json_path = self.output_dir / json_filename
                
                output_data = {
                    'query': self.query,
                    'scrape_timestamp': datetime.now().isoformat(),
                    'url': self.base_url,
                    'total_articles': len(articles),
                    'scroll_attempts': scroll_count,
                    'articles': articles
                }
                
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(output_data, f, indent=2, ensure_ascii=False)
                print(f"  Saved JSON to: {json_path}")
                
                print(f"\n{'='*60}")
                print(f"Scraping completed successfully!")
                print(f"Total articles extracted: {len(articles)}")
                print(f"{'='*60}\n")
                
                return output_data
                
            except PlaywrightTimeout as e:
                print(f"\nTimeout error: {e}")
                raise
            except Exception as e:
                print(f"\nUnexpected error: {e}")
                raise
            finally:
                await browser.close()


async def main():
    """Main execution function."""
    # Configuration
    QUERY = "scanx.trade"
    OUTPUT_DIR = "."
    
    # Create scraper instance
    scraper = GoogleNewsScraper(query=QUERY, output_dir=OUTPUT_DIR)
    
    # Execute scraping
    try:
        results = await scraper.scrape()
        
        # Display sample results
        print("\nSample Articles:")
        print("-" * 60)
        for i, article in enumerate(results['articles'][:5], 1):
            print(f"\n{i}. {article['headline']}")
            print(f"   Source: {article['source']}")
            print(f"   Time: {article['published_time']}")
            print(f"   URL: {article['url'][:80]}...")
        
        if len(results['articles']) > 5:
            print(f"\n... and {len(results['articles']) - 5} more articles")
        
    except Exception as e:
        print(f"\nScraping failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))

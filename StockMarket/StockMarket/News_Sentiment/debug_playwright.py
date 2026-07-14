"""
Debug Playwright scraper to see what elements are on the page
"""
import asyncio
from playwright.async_api import async_playwright

async def debug_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # Non-headless for debugging
        page = await browser.new_page()
        
        url = "https://news.google.com/search?q=Indian+stock+market+OR+NSE+OR+BSE&hl=en-US&gl=IN&ceid=IN:en"
        print(f"Navigating to: {url}")
        
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)  # Wait for page to load
        
        # Check for different selectors
        print("\n=== DEBUGGING PAGE ELEMENTS ===\n")
        
        # Try to find articles
        article_count = await page.evaluate("document.querySelectorAll('article').length")
        print(f"article tags found: {article_count}")
        
        div_m5k28_count = await page.evaluate("document.querySelectorAll('div.m5k28').length")
        print(f"div.m5k28 found: {div_m5k28_count}")
        
        # Try to find news items with different selectors
        all_divs = await page.evaluate("document.querySelectorAll('div').length")
        print(f"Total divs: {all_divs}")
        
        # Get the first few article elements if any
        if article_count > 0:
            first_article_html = await page.evaluate("""
                () => {
                    const article = document.querySelector('article');
                    return article ? article.outerHTML.substring(0, 500) : 'none';
                }
            """)
            print(f"\nFirst article HTML (first 500 chars):\n{first_article_html}")
        
        # Try to detect any news-looking elements
        news_elements = await page.evaluate("""
            () => {
                const elements = document.querySelectorAll('[jsname], [data-n-a-id], c-wiz');
                return elements.length;
            }
        """)
        print(f"\nElements with Google News attributes: {news_elements}")
        
        # Get page title to verify we're on the right page
        title = await page.title()
        print(f"\nPage title: {title}")
        
        # Wait a bit to see the page
        print("\nBrowser will stay open for 10 seconds for visual inspection...")
        await asyncio.sleep(10)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_page())

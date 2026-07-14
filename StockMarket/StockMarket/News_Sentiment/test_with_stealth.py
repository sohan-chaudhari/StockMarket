"""
Test if stealth scripts are causing the issue
"""
import asyncio
from playwright.async_api import async_playwright

async def test_without_stealth():
    url = "https://news.google.com/search?q=site%3Ascanx.trade+Reliance+Industries&hl=en-IN&gl=IN&ceid=IN%3Aen"
    
    print(f"Testing WITH stealth and custom user agent...\n")
    
    async with async_playwright() as p:
        # This mimics what the scraper class does
        from fake_useragent import UserAgent
        ua = UserAgent()
        user_agent = ua.random
        
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-blink-features=AutomationControlled',
            ]
        )
        
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080}
        )
        
        page = await context.new_page()
        
        # Apply stealth
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        print(f"Using user agent: {user_agent[:50]}...")
        
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(8)
        
        # Extract
        articles = await page.evaluate("""
            () => {
                let articleElements = Array.from(document.querySelectorAll('article'));
                if (articleElements.length === 0) {
                    articleElements = Array.from(document.querySelectorAll('div.m5k28'));
                }
                
                const results = [];
                articleElements.forEach((article, index) => {
                    try {
                        let headline = '';
                        const headlineEl = article.querySelector('a.JtKRv');
                        if (headlineEl) {
                            headline = headlineEl.innerText.trim();
                        }
                        
                        if (headline) {
                            results.push({ headline: headline });
                        }
                    } catch (e) {}
                });
                
                return results;
            }
        """)
        
        print(f"Extracted {len(articles)} articles\n")
        if len(articles) > 0:
            for i, art in enumerate(articles[:3], 1):
                print(f"{i}. {art['headline'][:60]}")
        
        await browser.close()
        return articles

if __name__ == "__main__":
    result = asyncio.run(test_without_stealth())
    print(f"\n[{'SUCCESS' if len(result) > 0 else 'FAILED'}] Got {len(result)} articles")

"""
Test with EXACT browser subagent URL format
"""
import asyncio
from playwright.async_api import async_playwright

async def test_exact_url():
    # This is the EXACT URL that worked for the browser subagent
    url = "https://news.google.com/search?q=site%3Ascanx.trade+Reliance+Industries&hl=en-IN&gl=IN&ceid=IN%3Aen"
    
    print(f"Testing URL: {url}\n")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(8)
        
        # Use the EXACT extraction code from the scraper
        articles = await page.evaluate("""
            () => {
                let articleElements = Array.from(document.querySelectorAll('article'));
                if (articleElements.length === 0) {
                    articleElements = Array.from(document.querySelectorAll('div.m5k28'));
                }
                
                console.log(`Found ${articleElements.length} containers`);
                
                const results = [];
                articleElements.forEach((article, index) => {
                    try {
                        let headline = '';
                        const headlineEl = article.querySelector('a.JtKRv');
                        if (headlineEl) {
                            headline = headlineEl.innerText.trim();
                        }
                        
                        if (headline) {
                            results.push({
                                headline: headline,
                                index: index
                            });
                        }
                    } catch (e) {
                        console.log(`Error at ${index}:`, e);
                    }
                });
                
                console.log(`Extracted ${results.length} articles`);
                return results;
            }
        """)
        
        print(f"Found {len(articles)} articles:\n")
        for i, art in enumerate(articles[:5], 1):
            print(f"{i}. {art['headline']}")
        
        await browser.close()
        return articles

if __name__ == "__main__":
    result = asyncio.run(test_exact_url())
    print(f"\n[{'SUCCESS' if len(result) > 0 else 'FAILED'}] Got {len(result)} total articles")

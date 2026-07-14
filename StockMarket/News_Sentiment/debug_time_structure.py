"""
Debug script to see what's actually on the Google News page
"""
import asyncio
from playwright.async_api import async_playwright

async def debug_page_structure():
    url = "https://news.google.com/search?q=scanx.trade+Reliance+Industries&hl=en-IN&gl=IN&ceid=IN:en"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(8)
        
        # Debug: Get all time elements
        result = await page.evaluate("""
            () => {
                // Find all time elements on the page
                const allTimes = Array.from(document.querySelectorAll('time'));
                console.log('Total time elements:', allTimes.length);
                
                // Find all article elements
                const allArticles = Array.from(document.querySelectorAll('article'));
                console.log('Total article elements:', allArticles.length);
                
                // Try div.m5k28 fallback
                const allDivs = Array.from(document.querySelectorAll('div.m5k28'));
                console.log('Total div.m5k28 elements:', allDivs.length);
                
                // Check first article and look for time inside it
                if (allArticles.length > 0) {
                    const firstArticle = allArticles[0];
                    const timeInArticle = firstArticle.querySelector('time');
                    console.log('Time in first article:', timeInArticle ? 'FOUND' : 'NOT FOUND');
                    
                    if (timeInArticle) {
                        console.log('Time element HTML:', timeInArticle.outerHTML);
                    }
                }
                
                return {
                    totalTimes: allTimes.length,
                    totalArticles: allArticles.length,
                    totalDivs: allDivs.length,
                    timesData: allTimes.slice(0, 3).map(t => ({
                        className: t.className,
                        datetime: t.getAttribute('datetime'),
                        textContent: t.textContent,
                        outerHTML: t.outerHTML
                    })),
                    firstArticleHasTime: allArticles.length > 0 && allArticles[0].querySelector('time') !== null
                };
            }
        """)
        
        print("=== DEBUG RESULTS ===")
        print(f"Total time elements on page: {result['totalTimes']}")
        print(f"Total article elements: {result['totalArticles']}")
        print(f"Total div.m5k28 elements: {result['totalDivs']}")
        print(f"First article has time: {result['firstArticleHasTime']}")
        print(f"\nFirst 3 time elements:")
        for i, t in enumerate(result['timesData'], 1):
            print(f"\n{i}. Class: {t['className']}")
            print(f"   Datetime: {t['datetime']}")
            print(f"   Text: {t['textContent']}")
            print(f"   HTML: {t['outerHTML'][:150]}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_page_structure())

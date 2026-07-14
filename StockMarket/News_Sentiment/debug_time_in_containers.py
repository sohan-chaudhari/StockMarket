"""
Check if time elements are inside div.m5k28 containers
"""
import asyncio
from playwright.async_api import async_playwright

async def check_time_in_containers():
    url = "https://news.google.com/search?q=scanx.trade+Reliance+Industries&hl=en-IN&gl=IN&ceid=IN:en"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(8)
        
        result = await page.evaluate("""
            () => {
                const containers = Array.from(document.querySelectorAll('div.m5k28'));
                console.log('Total containers:', containers.length);
                
                const results = [];
                containers.slice(0, 5).forEach((container, idx) => {
                   // Check if time is inside this container
                    const timeEl = container.querySelector('time.hvbAAd');
                    const timeElGeneric = container.querySelector('time');
                    
                    // Get headline
                    const headlineEl = container.querySelector('a.JtKRv');
                    const headline = headlineEl ? headlineEl.innerText.substring(0, 60) : 'NO HEADLINE';
                    
                    results.push({
                        index: idx,
                        headline: headline,
                        hasTimeHvbAAd: timeEl !== null,
                        hasTimeGeneric: timeElGeneric !== null,
                        timeValue: timeEl ? timeEl.getAttribute('datetime') : (timeElGeneric ? timeElGeneric.getAttribute('datetime') : null),
                        timeText: timeEl ? timeEl.textContent : (timeElGeneric ? timeElGeneric.textContent : null)
                    });
                });
                
                return results;
            }
        """)
        
        print("=== CHECKING FIRST 5 CONTAINERS ===\n")
        for r in result:
            print(f"{r['index']}. {r['headline']}")
            print(f"   Has time.hvbAAd: {r['hasTimeHvbAAd']}")
            print(f"   Has time (generic): {r['hasTimeGeneric']}")
            print(f"   Time value: {r['timeValue']}")
            print(f"   Time text: {r['timeText']}")
            print()
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(check_time_in_containers())

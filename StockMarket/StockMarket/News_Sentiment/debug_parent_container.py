"""
Find the common parent container that has both headline and time
"""
import asyncio
from playwright.async_api import async_playwright

async def find_proper_container():
    url = "https://news.google.com/search?q=scanx.trade+Reliance+Industries&hl=en-IN&gl=IN&ceid=IN:en"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(8)
        
        result = await page.evaluate("""
            () => {
                // Find first headline
                const firstHeadline = document.querySelector('a.JtKRv');
                if (!firstHeadline) return {error: 'No headline found'};
                
                // Walk up the parent tree to find a container that ALSO contains a time element
                let container = firstHeadline.parentElement;
                let found = null;
                
                for (let i = 0; i < 10; i++) {
                    if (!container) break;
                    
                    const timeInContainer = container.querySelector('time.hvbAAd');
                    if (timeInContainer) {
                        found = {
                            tagName: container.tagName,
                            className: container.className,
                            hasTime: true,
                            timeValue: timeInContainer.getAttribute('datetime'),
                            containerHTML: container.outerHTML.substring(0, 300)
                        };
                        break;
                    }
                    
                    container = container.parentElement;
                }
                
                return found || {error: 'Could not find common parent'};
            }
        """)
        
        print("=== FINDING COMMON PARENT CONTAINER ===\n")
        if 'error' in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Tag: {result['tagName']}")
            print(f"Class: {result['className']}")
            print(f"Has time: {result['hasTime']}")
            print(f"Time value: {result['timeValue']}")
            print(f"\nHTML preview:\n{result['containerHTML']}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(find_proper_container())

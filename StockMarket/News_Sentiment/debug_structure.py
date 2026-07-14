"""
Debug to see the structure of div.m5k28 elements
"""
import asyncio
from playwright.async_api import async_playwright

async def debug_structure():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        url = "https://news.google.com/search?q=Reliance+Industries&hl=en-US&gl=IN&ceid=IN:en"
        print(f"Navigating to: {url}")
        
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(3)
        
        # Get structure of first few div.m5k28 elements
        structure = await page.evaluate("""
            () => {
                const containers = document.querySelectorAll('div.m5k28');
                console.log(`Found ${containers.length} div.m5k28 containers`);
                
                const results = [];
                
                for (let i = 0; i < Math.min(5, containers.length); i++) {
                    const container = containers[i];
                    
                    // Try different selectors for headline
                    const headlineSelectors = ['a.JtKRv', 'a', 'h3', 'h4', '[role="heading"]'];
                    const foundSelectors = {};
                    
                    headlineSelectors.forEach(selector => {
                        const el = container.querySelector(selector);
                        if (el) {
                            foundSelectors[selector] = {
                                exists: true,
                                text: el.innerText ? el.innerText.substring(0, 100) : '(no text)',
                                classes: el.className
                            };
                        } else {
                            foundSelectors[selector] = { exists: false };
                        }
                    });
                    
                    // Get HTML structure (first 1000 chars)
                    const htmlStructure = container.outerHTML.substring(0, 1000);
                    
                    results.push({
                        index: i,
                        selectors: foundSelectors,
                        htmlPreview: htmlStructure
                    });
                }
                
                return results;
            }
        """)
        
        print("\n=== FIRST 5 CONTAINERS STRUCTURE ===\n")
        for item in structure:
            print(f"\nContainer #{item['index']}:")
            print(f"Selectors found:")
            for selector, data in item['selectors'].items():
                if data['exists']:
                    print(f"  + {selector}: '{data['text'][:60]}...'")
                else:
                    print(f"  - {selector}: NOT FOUND")
            print(f"\nHTML Preview (first 500 chars):\n{item['htmlPreview'][:500]}\n")
            print("-" * 80)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_structure())

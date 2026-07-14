"""
Test the exact extraction logic from the scraper
"""
import asyncio
from playwright.async_api import async_playwright

async def test_extraction():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        url = "https://news.google.com/search?q=Reliance+Industries&hl=en-US&gl=IN&ceid=IN:en"
        print(f"Navigating to: {url}")
        
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)  # Wait for page to stabilize (same as scraper)
        
        # This is the EXACT extraction code from the scraper
        articles = await page.evaluate("""
            () => {
                // Google News uses <article> tags or div.m5k28 as fallback
                let articleElements = Array.from(document.querySelectorAll('article'));
                
                // Fallback to div.m5k28 if no article tags found
                if (articleElements.length === 0) {
                    articleElements = Array.from(document.querySelectorAll('div.m5k28'));
                }
                
                console.log(`Found ${articleElements.length} article containers`);
                
                const results = [];
                
                articleElements.forEach((article, index) => {
                    try {
                        // Find headline using a.JtKRv (confirmed working selector)
                        let headline = '';
                        const headlineEl = article.querySelector('a.JtKRv');
                        if (headlineEl) {
                            headline = headlineEl.innerText.trim();
                        }
                        
                        // Find article link - try a.WwrzSb first, then fall back to headline link
                        let url = '';
                        const linkEl = article.querySelector('a.WwrzSb') || headlineEl;
                        if (linkEl) {
                            const href = linkEl.getAttribute('href');
                            if (href) {
                                url = href.startsWith('http') ? href : `https://news.google.com${href}`;
                            }
                        }
                        
                        // Find source - could be in various places
                        let source = '';
                        const sourceEl = article.querySelector('div.vr1PYe, div.MCAGUe');
                        if (sourceEl) {
                            source = sourceEl.innerText.trim();
                        }
                        
                        // Find published time using <time> tag (more reliable)
                        let publishedTime = '';
                        const timeEl = article.querySelector('time');
                        if (timeEl) {
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
                        } else {
                            console.log(`Skipping article ${index}: no headline found`);
                        }
                    } catch (e) {
                        console.log(`Error parsing article ${index}:`, e);
                    }
                });
                
                console.log(`Successfully parsed ${results.length} articles`);
                return results;
            }
        """)
        
        print(f"\n=== EXTRACTION RESULTS ===")
        print(f"Total articles extracted: {len(articles)}\n")
        
        for i, article in enumerate(articles[:5], 1):
            print(f"{i}. {article['headline'][:80]}")
            print(f"   URL: {article['url'][:80]}...")
            print(f"   Source: {article['source']}")
            print(f"   Time: {article['published_time']}")
            print()
        
        await browser.close()
        
        return articles

if __name__ == "__main__":
    articles = asyncio.run(test_extraction())
    print(f"\n[SUCCESS] Extracted {len(articles)} articles total")

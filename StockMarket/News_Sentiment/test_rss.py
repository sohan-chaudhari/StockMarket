from scrapers.scanx_google_scraper import ScanXGoogleScraper

scraper = ScanXGoogleScraper()
news = scraper.fetch_all_scanx_news(limit=1)
print(news[0])

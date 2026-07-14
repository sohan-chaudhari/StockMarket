import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.celery_app import celery_app
from app.database import SessionLocal
from app.models import Ticker
from scrapers.news_scraper import HybridNewsScraper
from loguru import logger
import requests
from datetime import datetime


# Initialize scraper
scraper = HybridNewsScraper()


@celery_app.task(name='tasks.scraper_task.scrape_ticker_news')
def scrape_ticker_news(ticker: str, company_name: str):
    """
    Scrape news for a specific ticker
    """
    logger.info(f"Scraping news for {ticker} ({company_name})")
    
    try:
        # Fetch articles
        articles = scraper.fetch_all_news(ticker, company_name)
        
        logger.info(f"Found {len(articles)} articles for {ticker}")
        
        # Ingest each article
        ingested_count = 0
        for article in articles:
            try:
                # Add full text (scrape if needed)
                full_text = _scrape_full_text(article["url"])
                
                # Ensure text meets minimum length requirement (50 chars)
                if len(full_text) < 50:
                    # Use title + excerpt as fallback
                    full_text = f"{article['title']}. {article.get('excerpt', '')}"
                
                article["text"] = full_text
                
                # Ensure excerpt doesn't exceed max length (500 chars)
                if article.get("excerpt") and len(article["excerpt"]) > 500:
                    article["excerpt"] = article["excerpt"][:497] + "..."
                
                # Convert datetime to ISO format string for JSON serialization
                if isinstance(article.get("published_at"), datetime):
                    article["published_at"] = article["published_at"].isoformat()
                
                # POST to ingest endpoint
                logger.info(f"  → Attempting to ingest: {article['title'][:60]}...")
                
                try:
                    response = requests.post(
                        "http://localhost:8000/api/ingest",
                        json=article,
                        timeout=10
                    )
                    
                    logger.info(f"  → Response status: {response.status_code}")
                    
                    if response.status_code == 200:
                        result = response.json()
                        if result["status"] == "success":
                            ingested_count += 1
                            logger.info(f"  ✓ Ingested: {article['title'][:60]}...")
                        elif result.get("status") == "duplicate":
                            logger.info(f"  ⊘ Duplicate (skipped): {article['title'][:60]}...")
                        else:
                            logger.error(f"  ✗ Unexpected response: {result}")
                    elif response.status_code == 422:
                        logger.error(f"  ✗ Validation failed: {response.json()}")
                    else:
                        logger.error(f"  ✗ Failed with status {response.status_code}: {response.text}")
                        
                except requests.exceptions.ConnectionError as e:
                    logger.error(f"  ✗ Connection error: Cannot reach backend at localhost:8000 - {e}")
                except requests.exceptions.Timeout as e:
                    logger.error(f"  ✗ Timeout: Backend took too long to respond - {e}")
                except Exception as e:
                    logger.error(f"  ✗ Request failed: {e}")
                
            except Exception as e:
                logger.error(f"  ✗ Failed to process article: {e}")
                continue
        
        logger.info(f"Successfully ingested {ingested_count}/{len(articles)} articles for {ticker}")
        
        return {
            "status": "success",
            "ticker": ticker,
            "articles_found": len(articles),
            "articles_ingested": ingested_count
        }
        
    except Exception as e:
        logger.error(f"Error scraping {ticker}: {e}")
        return {"status": "error", "ticker": ticker, "message": str(e)}


@celery_app.task(name='tasks.scraper_task.scrape_top_tickers')
def scrape_top_tickers():
    """
    Scrape news for all MVP tickers (scheduled every 5 minutes)
    """
    logger.info("Starting scheduled scrape for top tickers")
    
    db = SessionLocal()
    
    try:
        # Get all tickers
        tickers = db.query(Ticker).all()
        
        results = []
        for ticker in tickers:
            result = scrape_ticker_news.delay(ticker.symbol, ticker.name)
            results.append(result.id)
        
        logger.info(f"Queued scraping for {len(tickers)} tickers")
        
        return {
            "status": "success",
            "tickers_queued": len(tickers),
            "task_ids": results
        }
        
    except Exception as e:
        logger.error(f"Error in scheduled scrape: {e}")
        return {"status": "error", "message": str(e)}
        
    finally:
        db.close()


def _scrape_full_text(url: str) -> str:
    """
    Scrape full article text from URL
    """
    try:
        from bs4 import BeautifulSoup
        
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        
        # Get text
        text = soup.get_text()
        
        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)
        
        return text[:5000]  # Limit to 5000 chars
        
    except Exception as e:
        logger.error(f"Failed to scrape full text from {url}: {e}")
        return "Full text unavailable"

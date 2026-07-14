"""
ScanX.trade News API Endpoints

Endpoints for fetching news exclusively from scanx.trade via Google News
"""

from fastapi import APIRouter, Query, HTTPException, BackgroundTasks
from typing import List, Optional
from datetime import datetime, timedelta
import re
import sys
import asyncio
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Add parent directory to path so we can import from scrapers
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from scrapers.scanx_google_scraper import ScanXGoogleScraper
from app.utils.sentiment_helper import get_sentiment

router = APIRouter()

# Initialize scraper (loads CSV once at startup)
scraper = ScanXGoogleScraper()

# Shared thread pool executor to avoid creating new threads per request
_scraper_executor = ThreadPoolExecutor(max_workers=4)

# In-memory cache for news articles
_news_cache = {
    "articles": None,
    "timestamp": 0.0,
    "refresh_in_progress": False
}
CACHE_TTL_SECONDS = 300  # 5 minutes

async def _refresh_news_cache(limit: int = 20):
    """Background task to refresh the news cache"""
    global _news_cache
    if _news_cache["refresh_in_progress"]:
        return
    _news_cache["refresh_in_progress"] = True
    try:
        import os
        parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        from scrapers.google_news_playwright_scraper import scrape_all_sync
        loop = asyncio.get_event_loop()
        articles = await loop.run_in_executor(_scraper_executor, scrape_all_sync, limit)
        response = []
        for idx, article in enumerate(articles):
            headline = article.get('headline', '')
            ticker = article.get('ticker', 'UNKNOWN')
            source = article.get('source', 'scanx.trade')
            snippet = article.get('snippet', '')
            sentiment = get_sentiment(headline, ticker, source, snippet)
            response.append({
                'id': f"full_{idx}",
                'ticker': ticker,
                'title': headline,
                'url': article.get('url', ''),
                'source': source,
                'published_at': article.get('published_time', datetime.now().isoformat()),
                'excerpt': snippet,
                'logo_url': article.get('logo_url', ''),
                'sentiment': sentiment
            })
        _news_cache["articles"] = response
        _news_cache["timestamp"] = time.time()
    except Exception as e:
        print(f"[Cache] Refresh failed: {e}")
    finally:
        _news_cache["refresh_in_progress"] = False


@router.get("/scanx/company/{ticker}")
async def get_company_info(ticker: str):
    """
    Get company information for a ticker
    
    Returns the cleaned company name used in searches
    """
    company_name = scraper.get_company_name(ticker.upper())
    
    if not company_name:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
    
    return {
        'ticker': ticker.upper(),
        'company_name': company_name,
        'search_query': f'site:scanx.trade "{company_name}"'
    }




# ============================================================
# FULL SCRAPING ENDPOINTS (Playwright-based)
# ============================================================

@router.get("/scanx/news/full/all")
async def get_all_scanx_news_full(
    limit: int = Query(20, ge=1, le=100, description="Maximum number of articles"),
    background_tasks: BackgroundTasks = None
):
    """
    Get all recent scanx.trade news using cached scraping results.
    First request scrapes live (~5-8s), subsequent requests return instantly
    from cache for 5 minutes.
    """
    global _news_cache
    
    elapsed = time.time() - _news_cache["timestamp"]
    cache_fresh = _news_cache["articles"] is not None and elapsed < CACHE_TTL_SECONDS
    
    if cache_fresh:
        return _news_cache["articles"]
    
    if elapsed < CACHE_TTL_SECONDS + 60 and _news_cache["articles"] is not None:
        # Cache is stale but exists — return stale and refresh in background
        if background_tasks:
            background_tasks.add_task(_refresh_news_cache, limit)
        return _news_cache["articles"]
    
    # No cache - scrape live (first request)
    try:
        import os
        import sys
        parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
            
        from scrapers.scanx_google_scraper import ScanXGoogleScraper
        scraper = ScanXGoogleScraper()
        
        loop = asyncio.get_event_loop()
        articles = await loop.run_in_executor(None, scraper.fetch_all_scanx_news, limit, 30)
        
        # Trigger full Playwright scrape in background to enrich cache
        if background_tasks:
            background_tasks.add_task(_refresh_news_cache, limit)

        from app.utils.sentiment_helper import get_sentiment
        
        response = []
        for idx, article in enumerate(articles):
            headline = article.get('headline', article.get('title', ''))
            ticker = article.get('ticker', 'UNKNOWN')
            source = article.get('source', 'scanx.trade')
            snippet = article.get('snippet', article.get('excerpt', ''))
            sentiment = get_sentiment(headline, ticker, source, snippet)
            response.append({
                'id': f"full_{idx}",
                'ticker': ticker,
                'title': headline,
                'url': article.get('url', ''),
                'source': source,
                'published_at': article.get('published_time', datetime.now().isoformat()),
                'excerpt': snippet,
                'logo_url': article.get('logo_url', ''),
                'sentiment': sentiment
            })
        _news_cache["articles"] = response
        _news_cache["timestamp"] = time.time()
        return response
    except Exception as e:
        if _news_cache["articles"] is not None:
            return _news_cache["articles"]
        raise HTTPException(status_code=500, detail=f"Error fetching full news: {str(e)}")


@router.get("/scanx/news/full/{ticker}")
async def get_scanx_news_full_by_ticker(
    ticker: str,
    limit: int = Query(20, ge=1, le=100, description="Maximum number of articles")
):
    """
    Get scanx.trade news for a specific ticker using Playwright full scraping
    
    This endpoint uses Playwright to scrape Google News directly, searching for
    both the ticker symbol and company name. It's slower but provides more
    comprehensive results than the RSS-based endpoint.
    
    Note: This may take 15-30 seconds to complete.
    """
    try:
        import sys
        import os
        
        parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        
        from scrapers.scanx_google_scraper import ScanXGoogleScraper
        scraper = ScanXGoogleScraper()
        
        # Run the synchronous scraper in the shared thread pool
        loop = asyncio.get_event_loop()
        articles = await loop.run_in_executor(None, scraper.fetch_scanx_news_for_ticker, ticker.upper(), limit, 30)

        from app.utils.sentiment_helper import get_sentiment
        
        response = []
        for idx, article in enumerate(articles):
            headline = article.get('headline', article.get('title', ''))
            ticker_symbol = article.get('ticker', ticker.upper())
            source = article.get('source', 'scanx.trade')
            snippet = article.get('snippet', article.get('excerpt', ''))
            
            # Get sentiment for headline
            sentiment = get_sentiment(headline, ticker_symbol, source, snippet)
            
            published_at = article.get('published_time') or datetime.now().isoformat()
            
            response.append({
                'id': f"full_{ticker}_{idx}",
                'ticker': ticker_symbol,
                'title': headline,
                'url': article.get('url', ''),
                'source': source,
                'published_at': published_at,
                'excerpt': snippet,  # Include snippet
                'company_name': article.get('company_name', ''),
                'logo_url': article.get('logo_url', ''),
                'sentiment': sentiment  # Add sentiment data
            })
            
        return response
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching full news for {ticker}: {str(e)}")

@router.get("/scanx/news/market-sentiment")
async def get_market_sentiment(background_tasks: BackgroundTasks = None):
    """
    Returns an aggregated AI market sentiment score (0-100) based on recently scraped news.
    """
    global _news_cache
    articles = _news_cache.get("articles")
    
    if not articles or len(articles) == 0:
        if background_tasks:
            background_tasks.add_task(_refresh_news_cache, 20)
        return {"score": 50, "label": "Neutral", "summary": "Waiting for market data..."}
        
    total_score = 0
    bullish_count = 0
    bearish_count = 0
    
    for a in articles:
        sentiment = a.get('sentiment', {})
        # sentiment_score is typically -1.0 to 1.0
        s_score = sentiment.get('sentiment_score', 0)
        total_score += s_score
        
        if s_score > 0.1:
            bullish_count += 1
        elif s_score < -0.1:
            bearish_count += 1
            
    avg_score = total_score / len(articles)
    
    # Map -1.0 to 1.0 -> 0 to 100
    normalized_score = int(((avg_score + 1.0) / 2.0) * 100)
    
    # Ensure it stays within bounds
    normalized_score = max(0, min(100, normalized_score))
    
    if normalized_score >= 60:
        label = "Bullish"
        summary = f"Strong momentum. {bullish_count} recent news catalysts indicate positive growth."
    elif normalized_score <= 40:
        label = "Bearish"
        summary = f"Market caution. {bearish_count} recent news events indicate downward pressure."
    else:
        label = "Neutral"
        summary = "Consolidating. Mixed signals across broader market indices."
        
    return {
        "score": normalized_score,
        "label": label,
        "summary": summary
    }

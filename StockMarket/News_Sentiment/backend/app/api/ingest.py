from fastapi import APIRouter, HTTPException
from datetime import datetime
import hashlib
import logging

from app.database import mongodb
from app.schemas import ArticleIngestRequest

# Try to import Celery tasks, but make it optional
try:
    from tasks.sentiment_task import analyze_sentiment_task
    CELERY_AVAILABLE = True
except ImportError:
    CELERY_AVAILABLE = False
    logging.warning("Celery tasks not available - sentiment analysis will be skipped")

router = APIRouter()


@router.post("/ingest")
async def ingest_article(article: ArticleIngestRequest):
    """
    Ingest a new news article and trigger sentiment analysis
    """
    # Check for duplicates by URL hash
    url_hash = hashlib.md5(article.url.encode()).hexdigest()
    
    existing = await mongodb.news_articles.find_one({"url_hash": url_hash})
    if existing:
        return {"status": "duplicate", "article_id": str(existing["_id"])}
    
    # Insert into MongoDB
    article_doc = {
        "ticker": article.ticker.upper(),
        "title": article.title,
        "url": article.url,
        "url_hash": url_hash,
        "source": article.source,
        "published_at": article.published_at,
        "text": article.text,
        "excerpt": article.excerpt or article.text[:200],
        "created_at": datetime.utcnow()
    }
    
    result = await mongodb.news_articles.insert_one(article_doc)
    article_id = str(result.inserted_id)
    
    # Trigger Celery task for sentiment analysis (if available)
    if CELERY_AVAILABLE:
        analyze_sentiment_task.delay(article_id)
        message = "Article ingested, sentiment analysis queued"
    else:
        message = "Article ingested (sentiment analysis unavailable)"
    
    return {
        "status": "success",
        "article_id": article_id,
        "message": message
    }

"""
Setup script to generate all remaining project files for News Sentiment Analysis Platform
Run this after infrastructure setup to create the complete codebase
"""

import os
from pathlib import Path

# Base directory
BASE_DIR = Path(r"c:\Users\rahul\Desktop\News_Sentiment")

# File templates
FILES = {
    "backend/app/__init__.py": "",
    
    "backend/app/api/__init__.py": "",
    
    # News endpoint - continued in next file due to size
    "backend/app/api/news.py": '''from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta

from app.database import get_db, mongodb
from app.models import SentimentResult, RecencyScore
from app.schemas import NewsArticleResponse, SentimentResponse

router = APIRouter()


@router.get("/news/ticker/{ticker}", response_model=List[NewsArticleResponse])
async def get_news_by_ticker(
    ticker: str,
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    window: str = Query("1h", regex="^(5m|30m|1h|24h|7d)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Get news articles for a specific ticker with sentiment and recency scores
    """
    # Default date range: last 7 days
    if not to_date:
        to_date = datetime.utcnow()
    if not from_date:
        from_date = to_date - timedelta(days=7)
    
    # Query MongoDB for articles
    query = {
        "ticker": ticker.upper(),
        "published_at": {"$gte": from_date, "$lte": to_date}
    }
    
    articles_cursor = mongodb.news_articles.find(query).sort("published_at", -1).skip(offset).limit(limit)
    articles = await articles_cursor.to_list(length=limit)
    
    if not articles:
        return []
    
    # Get sentiment results from PostgreSQL
    article_ids = [str(art["_id"]) for art in articles]
    sentiments = db.query(SentimentResult).filter(
        SentimentResult.article_id.in_(article_ids)
    ).all()
    
    sentiment_map = {s.article_id: s for s in sentiments}
    
    # Get recency scores
    recency_scores = db.query(RecencyScore).filter(
        RecencyScore.article_id.in_(article_ids),
        RecencyScore.window == window
    ).all()
    
    recency_map = {r.article_id: r for r in recency_scores}
    
    # Build response
    response = []
    for article in articles:
        article_id = str(article["_id"])
        sentiment = sentiment_map.get(article_id)
        recency = recency_map.get(article_id)
        
        sentiment_response = None
        if sentiment:
            sentiment_response = SentimentResponse(
                ticker=sentiment.ticker,
                sentiment_score=sentiment.sentiment_score,
                label=sentiment.label,
                confidence=sentiment.confidence,
                explanation=sentiment.explanation or "",
                key_entities=sentiment.key_entities or [],
                model_breakdown={
                    "S1": sentiment.finbert_score,
                    "S3": sentiment.lexicon_score
                }
            )
        
        response.append(NewsArticleResponse(
            id=article_id,
            ticker=article["ticker"],
            title=article["title"],
            url=article["url"],
            source=article["source"],
            published_at=article["published_at"],
            excerpt=article.get("excerpt", "")[:200],
            sentiment=sentiment_response,
            recency_score=recency.recency_score if recency else None
        ))
    
    return response
''',
    
    # Ingest endpoint
    "backend/app/api/ingest.py": '''from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
import hashlib

from app.database import mongodb
from app.schemas import ArticleIngestRequest
from tasks.sentiment_task import analyze_sentiment_task

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
    
    # Trigger Celery task for sentiment analysis
    analyze_sentiment_task.delay(article_id)
    
    return {
        "status": "success",
        "article_id": article_id,
        "message": "Article ingested, sentiment analysis queued"
    }
''',

    # ML requirements
    "ml/requirements.txt": '''transformers==4.37.0
torch==2.1.2
tokenizers==0.15.0
spacy==3.7.2
yfinance==0.2.35
pandas==2.1.4
numpy==1.26.3
google-generativeai==0.3.2
nltk==3.8.1
scikit-learn==1.4.0
scipy==1.11.4
python-dotenv==1.0.0
tqdm==4.66.1
pysentiment2==1.1.1
fuzzywuzzy==0.18.0
python-Levenshtein==0.25.0
''',

    # Initialization script
    "backend/scripts/init_db.py": '''import sys
sys.path.append(".")

from app.database import engine
from app.models import Base
from loguru import logger

logger.info("Creating database tables...")
Base.metadata.create_all(bind=engine)
logger.info("Tables created successfully!")
''',

    # Ticker seeding script
    "backend/scripts/seed_tickers.py": '''import sys
sys.path.append(".")

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Ticker
from loguru import logger

# MVP Tickers (Top 5 Indian stocks)
TICKERS = [
    {
        "symbol": "RELIANCE",
        "name": "Reliance Industries Limited",
        "isin": "INE002A01018",
        "sector": "Energy",
        "market_cap": 17.5,  # in trillion INR
        "exchange": "NSE"
    },
    {
        "symbol": "TCS",
        "name": "Tata Consultancy Services Limited",
        "isin": "INE467B01029",
        "sector": "IT Services",
        "market_cap": 13.2,
        "exchange": "NSE"
    },
    {
        "symbol": "INFY",
        "name": "Infosys Limited",
        "isin": "INE009A01021",
        "sector": "IT Services",
        "market_cap": 6.8,
        "exchange": "NSE"
    },
    {
        "symbol": "HDFCBANK",
        "name": "HDFC Bank Limited",
        "isin": "INE040A01034",
        "sector": "Banking",
        "market_cap": 12.1,
        "exchange": "NSE"
    },
    {
        "symbol": "ICICIBANK",
        "name": "ICICI Bank Limited",
        "isin": "INE090A01021",
        "sector": "Banking",
        "market_cap": 7.9,
        "exchange": "NSE"
    }
]

def seed_tickers():
    db = SessionLocal()
    try:
        for ticker_data in TICKERS:
            # Check if exists
            existing = db.query(Ticker).filter(Ticker.symbol == ticker_data["symbol"]).first()
            if existing:
                logger.info(f"Ticker {ticker_data['symbol']} already exists, skipping")
                continue
            
            ticker = Ticker(**ticker_data)
            db.add(ticker)
            logger.info(f"Added ticker: {ticker_data['symbol']} - {ticker_data['name']}")
        
        db.commit()
        logger.info("✓ Successfully seeded all tickers!")
    except Exception as e:
        logger.error(f"Error seeding tickers: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_tickers()
''',
}

def create_files():
    """Create all files from templates"""
    for filepath, content in FILES.items():
        full_path = BASE_DIR / filepath
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        print(f"✓ Created: {filepath}")

if __name__ == "__main__":
    create_files()
    print("\n✅ All backend files created successfully!")
    print("\nNext steps:")
    print("1. Run: cd backend && python scripts/init_db.py")
    print("2. Run: python scripts/seed_tickers.py")
    print("3. Continue with ML pipeline setup")

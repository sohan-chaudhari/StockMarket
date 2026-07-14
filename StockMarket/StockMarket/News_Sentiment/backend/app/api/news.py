from fastapi import APIRouter, Depends, Query, HTTPException
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
    # Default date range: last 30 days (extended from 7 to show more articles)
    if not to_date:
        to_date = datetime.utcnow()
    if not from_date:
        from_date = to_date - timedelta(days=30)
    
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

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.celery_app import celery_app
from app.database import SessionLocal
from app.models import SentimentResult, RecencyScore
from app.config import settings
from loguru import logger
import yfinance as yf
from datetime import datetime, timedelta
import math


@celery_app.task(name='tasks.recency_task.calculate_recency_score')
def calculate_recency_score(article_id: str, window: str = "1h"):
    """
    Calculate recency score for an article
    
    Recency formula:
    R_actual = (P_after - P_before) / P_before
    R_pred = sentiment_score × μ_W
    D = R_actual - R_pred
    recency_score = tanh(D / (σ_W + ε)) × 100
    """
    if not settings.ENABLE_RECENCY_SCORING:
        return {"status": "skipped", "message": "Recency scoring disabled"}
    
    logger.info(f"Calculating recency score for article {article_id}, window {window}")
    
    db = SessionLocal()
    
    try:
        # 1. Get sentiment result
        sentiment = db.query(SentimentResult).filter(
            SentimentResult.article_id == article_id
        ).first()
        
        if not sentiment:
            logger.warning(f"No sentiment found for article {article_id}")
            return {"status": "error", "message": "Sentiment not found"}
        
        # 2. Get article publish time from MongoDB
        from app.database import mongodb
        from bson.objectid import ObjectId
        
        article = mongodb.news_articles.find_one({"_id": ObjectId(article_id)})
        if not article:
            return {"status": "error", "message": "Article not found"}
        
        publish_time = article["published_at"]
        ticker = sentiment.ticker
        
        # 3. Calculate time after window
        window_deltas = {
            "5m": timedelta(minutes=5),
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7)
        }
        
        after_time = publish_time + window_deltas.get(window, timedelta(hours=1))
        
        # Skip if after_time is in the future
        now_utc = datetime.now(after_time.tzinfo) if after_time.tzinfo else datetime.utcnow()
        if after_time > now_utc:
            logger.info(f"Window not yet complete for {article_id}, skipping")
            return {"status": "pending", "message": "Window not complete"}
        
        # 4. Fetch prices
        price_before = _get_price_at_time(ticker, publish_time)
        price_after = _get_price_at_time(ticker, after_time)
        
        if not price_before or not price_after:
            logger.warning(f"Failed to fetch prices for {ticker}")
            return {"status": "error", "message": "Price data unavailable"}
        
        # 5. Calculate actual return
        R_actual = (price_after - price_before) / price_before
        
        # 6. Calculate predicted return (simplified for MVP)
        # μ_W = historical median move (hardcoded for MVP, should be calculated daily)
        mu_w = 0.01  # 1% median move for 1h window (example)
        sigma_w = 0.015  # 1.5% volatility (example)
        
        R_pred = sentiment.sentiment_score * mu_w
        
        # 7. Calculate deviation
        D = R_actual - R_pred
        
        # 8. Normalize to recency score
        epsilon = 0.0001
        recency_score_raw = math.tanh(D / (sigma_w + epsilon))
        recency_score = recency_score_raw * 100  # Scale to [-100, 100]
        
        # 9. Store in database
        recency = RecencyScore(
            article_id=article_id,
            ticker=ticker,
            window=window,
            price_at_publish=price_before,
            price_after_window=price_after,
            actual_return=R_actual,
            predicted_return=R_pred,
            recency_score=recency_score,
            mu_w=mu_w,
            sigma_w=sigma_w
        )
        
        db.add(recency)
        db.commit()
        
        logger.info(f"✓ Recency score calculated: {recency_score:.2f} for {article_id}")
        
        return {
            "status": "success",
            "article_id": article_id,
            "recency_score": recency_score,
            "actual_return": R_actual * 100,
            "predicted_return": R_pred * 100
        }
        
    except Exception as e:
        logger.error(f"Error calculating recency score: {e}")
        db.rollback()
        return {"status": "error", "message": str(e)}
        
    finally:
        db.close()


@celery_app.task(name='tasks.recency_task.calculate_all_recency_scores')
def calculate_all_recency_scores():
    """
    Calculate recency scores for all articles (scheduled hourly)
    """
    logger.info("Starting batch recency score calculation")
    
    db = SessionLocal()
    
    try:
        # Get all sentiment results without recency scores
        from app.database import mongodb
        from datetime import datetime, timedelta
        
        # Get articles from last 7 days
        cutoff = datetime.utcnow() - timedelta(days=7)
        
        articles = mongodb.news_articles.find({
            "published_at": {"$gte": cutoff}
        })
        
        queued = 0
        for article in articles:
            article_id = str(article["_id"])
            
            # Queue recency calculation (1h window for MVP)
            calculate_recency_score.delay(article_id, "1h")
            queued += 1
        
        logger.info(f"Queued recency calculation for {queued} articles")
        
        return {"status": "success", "articles_queued": queued}
        
    except Exception as e:
        logger.error(f"Error in batch recency calculation: {e}")
        return {"status": "error", "message": str(e)}
        
    finally:
        db.close()


def _get_price_at_time(ticker: str, timestamp: datetime) -> float:
    """
    Fetch price at a specific time using yfinance
    """
    try:
        # Convert ticker to NSE format
        ticker_symbol = f"{ticker}.NS"
        
        # Fetch data around the timestamp
        start = timestamp - timedelta(hours=2)
        end = timestamp + timedelta(hours=2)
        
        stock = yf.Ticker(ticker_symbol)
        hist = stock.history(start=start, end=end, interval="1h")
        
        if hist.empty:
            logger.warning(f"No price data for {ticker} at {timestamp}")
            return None
        
        # Get closest price
        closest_idx = (hist.index - timestamp).abs().argmin()
        price = hist.iloc[closest_idx]["Close"]
        
        return float(price)
        
    except Exception as e:
        logger.error(f"Error fetching price for {ticker}: {e}")
        return None

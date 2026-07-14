import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.celery_app import celery_app
from app.database import SessionLocal, mongodb
from app.models import SentimentResult
from ml.sentiment_analyzer import SentimentAnalyzer
from loguru import logger
from bson.objectid import ObjectId


# Initialize sentiment analyzer (loaded once per worker)
analyzer = SentimentAnalyzer()


@celery_app.task(name='tasks.sentiment_task.analyze_sentiment_task')
def analyze_sentiment_task(article_id: str):
    """
    Celery task to analyze sentiment for an article
    """
    logger.info(f"Analyzing sentiment for article: {article_id}")
    
    db = SessionLocal()
    
    try:
        # 1. Fetch article from MongoDB
        article_doc = mongodb.news_articles.find_one({"_id": ObjectId(article_id)})
        
        if not article_doc:
            logger.error(f"Article {article_id} not found in MongoDB")
            return {"status": "error", "message": "Article not found"}
        
        # 2. Run sentiment analysis
        article_data = {
            "text": article_doc["text"],
            "ticker": article_doc["ticker"],
            "source": article_doc.get("source", "Unknown")
        }
        
        result = analyzer.analyze(article_data)
        
        # 3. Store results in PostgreSQL
        sentiment = SentimentResult(
            article_id=article_id,
            ticker=result["ticker"],
            sentiment_score=result["sentiment_score"],
            label=result["label"],
            confidence=result["confidence"],
            finbert_score=result["finbert_score"],
            lexicon_score=result["lexicon_score"],
            raw_sentiment=result["raw_sentiment"],
            calibrated_sentiment=result["sentiment_score"],
            llm_used=result["llm_used"],
            explanation=result["explanation"],
            key_entities=result["key_entities"]
        )
        
        db.add(sentiment)
        db.commit()
        
        logger.info(f"✓ Sentiment analysis complete for {article_id}: {result['label']} ({result['sentiment_score']:.2f})")
        
        return {
            "status": "success",
            "article_id": article_id,
            "sentiment": result["label"],
            "score": result["sentiment_score"]
        }
        
    except Exception as e:
        logger.error(f"Error analyzing sentiment for {article_id}: {e}")
        db.rollback()
        return {"status": "error", "message": str(e)}
        
    finally:
        db.close()

from sqlalchemy import Column, String, Float, Integer, DateTime, Boolean, JSON
from datetime import datetime
from app.database import Base


class Ticker(Base):
    """Stock ticker metadata"""
    __tablename__ = "tickers"
    
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, unique=True, index=True, nullable=False)  # e.g., "RELIANCE"
    name = Column(String, nullable=False)  # e.g., "Reliance Industries Limited"
    isin = Column(String, unique=True)  # e.g., "INE002A01018"
    sector = Column(String)  # e.g., "Energy"
    market_cap = Column(Float)  # in billions
    exchange = Column(String, default="NSE")  # NSE or BSE
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SentimentResult(Base):
    """Sentiment analysis results"""
    __tablename__ = "sentiment_results"
    
    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(String, unique=True, index=True, nullable=False)  # MongoDB _id
    ticker = Column(String, index=True, nullable=False)
    
    # Sentiment scores
    sentiment_score = Column(Float, nullable=False)  # -1 to +1
    label = Column(String, nullable=False)  # Bullish/Neutral/Bearish
    confidence = Column(Float, nullable=False)  # 0 to 1
    
    # Model breakdown
    finbert_score = Column(Float)  # S1
    lexicon_score = Column(Float)  # S3
    market_model_score = Column(Float, nullable=True)  # S2 (Phase 3)
    
    # LLM calibration
    raw_sentiment = Column(Float)
    calibrated_sentiment = Column(Float)
    llm_used = Column(Boolean, default=False)
    
    # Explanation
    explanation = Column(String)
    key_entities = Column(JSON)  # List of company names
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    

class RecencyScore(Base):
    """Recency validation scores"""
    __tablename__ = "recency_scores"
    
    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(String, index=True, nullable=False)
    ticker = Column(String, index=True, nullable=False)
    
    # Time window
    window = Column(String, nullable=False)  # "5m", "30m", "1h", "24h", "7d"
    
    # Prices
    price_at_publish = Column(Float)
    price_after_window = Column(Float)
    
    # Returns
    actual_return = Column(Float)  # R_actual
    predicted_return = Column(Float)  # R_pred = sentiment_score * μ_W
    
    # Recency score
    recency_score = Column(Float)  # -100 to +100
    
    # Calibration params
    mu_w = Column(Float)  # Median move magnitude for ticker & window
    sigma_w = Column(Float)  # Volatility for ticker & window
    
    # Metadata
    calculated_at = Column(DateTime, default=datetime.utcnow)


class PriceHistory(Base):
    """Historical price data for μ_W and σ_W calculation"""
    __tablename__ = "price_history"
    
    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, index=True,nullable=False)
    
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)
    
    created_at = Column(DateTime, default=datetime.utcnow)

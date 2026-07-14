from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime


# Request/Response Schemas

class ArticleIngestRequest(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol")
    title: str = Field(..., min_length=10, max_length=500)
    url: str = Field(..., description="Article URL")
    source: str = Field(..., description="News source name")
    published_at: datetime
    text: str = Field(..., min_length=50, description="Full article text")
    excerpt: Optional[str] = Field(None, max_length=500)


class SentimentResponse(BaseModel):
    ticker: str
    sentiment_score: float  # -1 to +1
    label: str  # Bullish/Neutral/Bearish
    confidence: float  # 0 to 1
    explanation: str
    key_entities: List[str]
    model_breakdown: Dict[str, float]
    recency_scores: Optional[Dict[str, float]] = None


class NewsArticleResponse(BaseModel):
    id: str
    ticker: str
    title: str
    url: str
    source: str
    published_at: datetime
    excerpt: str
    sentiment: Optional[SentimentResponse] = None
    recency_score: Optional[float] = None


class TickerSearchResponse(BaseModel):
    symbol: str
    name: str
    isin: Optional[str]
    sector: Optional[str]
    market_cap: Optional[float]
    exchange: str


class HealthResponse(BaseModel):
    status: str
    services: Dict[str, str]
    timestamp: datetime

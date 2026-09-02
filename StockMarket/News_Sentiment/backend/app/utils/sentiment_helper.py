"""
Sentiment Analysis Helper
Provides sentiment analysis for news articles using the ML pipeline
"""
import sys
from pathlib import Path
from typing import Dict, Optional

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from ml.sentiment_analyzer import SentimentAnalyzer

_FALLBACK_POSITIVE = {
    'profit', 'growth', 'surge', 'gain', 'strong', 'wins', 'rises', 'record',
    'beats', 'approval', 'deal', 'launch', 'dividend', 'buyback', 'acquire',
    'expansion', 'positive', 'higher', 'rally', 'increase', 'upgrade', 'bullish',
}
_FALLBACK_NEGATIVE = {
    'loss', 'decline', 'fall', 'drops', 'weak', 'miss', 'probe', 'penalty',
    'fraud', 'default', 'risk', 'reduce', 'layoff', 'concern', 'cut', 'slump',
    'negative', 'lower', 'downgrade', 'bearish', 'halt', 'resign', 'steps down',
}

def _quick_keyword_score(text: str) -> float:
    text_l = text.lower()
    pos = sum(1 for w in _FALLBACK_POSITIVE if w in text_l)
    neg = sum(1 for w in _FALLBACK_NEGATIVE if w in text_l)
    raw = (pos - neg) * 0.1
    return round(max(-0.5, min(0.5, raw if raw != 0 else 0.03)), 3)


class SentimentHelper:
    """
    Singleton wrapper for sentiment analysis
    Loads model once at startup to avoid repeated loading
    """
    _instance: Optional['SentimentHelper'] = None
    _analyzer: Optional[SentimentAnalyzer] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._analyzer is None:
            print("Loading sentiment analysis models (this may take 5-10 seconds)...")
            self._analyzer = SentimentAnalyzer()
            print("Sentiment analyzer ready!")
    
    def analyze_article(self, headline: str, ticker: str = None, source: str = None) -> Dict:
        """
        Analyze sentiment for a news article headline
        
        Args:
            headline: Article headline text
            ticker: Stock ticker symbol
            source: News source name
        
        Returns:
            Dict with sentiment_score, label, confidence
        """
        try:
            result = self._analyzer.analyze({
                "text": headline,
                "ticker": ticker or "UNKNOWN",
                "source": source or "Unknown"
            })
            
            return {
                "sentiment_score": round(result["sentiment_score"], 3),
                "label": result["label"],
                "confidence": round(result["confidence"], 2)
            }
        except Exception as e:
            print(f"Error analyzing sentiment: {e}")
            # Fallback: quick keyword scan so we never return a dead 0.00 Neutral
            score = _quick_keyword_score(headline)
            return {
                "sentiment_score": score,
                "label": "Bullish" if score >= 0 else "Bearish",
                "confidence": 0.3,
            }


# Global singleton instance
sentiment_helper = SentimentHelper()


def get_sentiment(headline: str, ticker: str = None, source: str = None, snippet: str = "") -> Dict:
    """
    Convenience function to get sentiment for headline + snippet
    
    Args:
        headline: Article headline
        ticker: Stock ticker (optional)
        source: News source (optional) 
        snippet: Article overview/description (optional)
    
    Returns:
        dict with sentiment_score, label, confidence
    """
    # Combine headline and snippet for better context
    text_to_analyze = headline
    if snippet and snippet.strip():
        # Add snippet after headline (limit to 200 chars)
        text_to_analyze += ". " + snippet[:200]
    
    return sentiment_helper.analyze_article(text_to_analyze, ticker, source)

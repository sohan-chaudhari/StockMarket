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
            # Return neutral sentiment on error
            return {
                "sentiment_score": 0.0,
                "label": "Neutral",
                "confidence": 0.5
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

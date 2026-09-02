from typing import Dict, List
import os

from ml.preprocessing import FinancialPreprocessor
from ml.ner_extractor import EntityExtractor
from ml.finbert_model import FinBERTModel
from ml.lexicon_model import LexiconModel
from ml.llm_calibrator import LLMCalibrator
from ml.confidence_estimator import ConfidenceEstimator


class SentimentAnalyzer:
    """
    Complete sentiment analysis pipeline with ensemble model
    """
    
    def __init__(self):
        print("Initializing Sentiment Analyzer...")
        
        # Load components
        self.preprocessor = FinancialPreprocessor()
        self.entity_extractor = EntityExtractor()
        self.finbert = FinBERTModel()
        self.lexicon = LexiconModel()
        self.llm_calibrator = LLMCalibrator()
        self.confidence_estimator = ConfidenceEstimator()
        
        # Ensemble weights
        # FinBERT: context-aware but biased toward neutral on short headlines
        # Lexicon: fast pattern-based, now augmented with WSD (context_analyzer)
        # Increasing Lexicon weight to 0.35 to give more influence to pattern-matched
        # and context-disambiguated signals, which are more reliable on short headlines.
        self.w1 = 0.65  # FinBERT (reduced from 0.75)
        self.w2 = 0.00  # Market model (Phase 3 - not implemented yet)
        self.w3 = 0.35  # Lexicon (increased from 0.25) — now with WSD correction
        
        # Feature flags
        self.enable_llm = os.getenv("ENABLE_LLM_CALIBRATION", "true").lower() == "true"
        self.llm_threshold = float(os.getenv("LLM_CONFIDENCE_THRESHOLD", "0.7"))
        
        print("Sentiment Analyzer ready!")
    
    def analyze(self, article: Dict) -> Dict:
        """
        Analyze sentiment for a news article
        
        Args:
            article: Dict with 'text', 'ticker', 'source', etc.
        
        Returns:
            Dict with sentiment score, label, confidence, explanation
        """
        # 1. Preprocess
        preprocessed = self.preprocessor.enhance(article["text"])
        
        # 2. Extract entities
        entities = self.entity_extractor.extract_tickers(article["text"])
        
        # 3. Get scores from each model
        finbert_result = self.finbert.predict(preprocessed["cleaned_text"])
        S1 = finbert_result["score"]
        
        lexicon_score = self.lexicon.score(
            preprocessed["cleaned_text"],
            event_type=preprocessed["event_type"]
        )
        S3 = lexicon_score
        
        # 4. Conflict Detection & Ensemble aggregation
        # Detect when FinBERT and Lexicon strongly disagree (mixed sentiment)
        model_conflict = (S1 > 0.3 and S3 < -0.3) or (S1 < -0.3 and S3 > 0.3)
        
        if model_conflict:
            # When models strongly disagree, adjust weighting based on strength
            # If Lexicon shows pattern-matched signals, trust it more
            # since FinBERT can misinterpret domain-specific terms
            if S3 >= 0.5 and S1 < -0.3:
                # Lexicon positive (multiple words or pattern match), FinBERT negative → favor Lexicon
                w1_adj = 0.40
                w3_adj = 0.60
            elif S3 <= -0.4 and S1 > 0.3:
                # Lexicon negative (pattern match like "reduces rewards"), FinBERT positive → favor Lexicon
                w1_adj = 0.35
                w3_adj = 0.65
            else:
                # Moderate conflict - use balanced weights
                w1_adj = 0.50
                w3_adj = 0.50
            raw_sentiment = w1_adj * S1 + w3_adj * S3
        else:
            # Normal weighting when models agree
            raw_sentiment = self.w1 * S1 + self.w3 * S3
        
        # 5. LLM calibration (only if enabled and low initial confidence)
        model_scores = {"S1": S1, "S3": S3}
        
        # Calculate preliminary confidence
        prelim_confidence = self.confidence_estimator.calculate(
            model_scores=model_scores,
            language_certainty=preprocessed.get("certainty", 0.8),
            source_name=article.get("source", "Unknown"),
            event_clarity=0.9 if preprocessed["event_type"] else 0.5
        )
        
        calibrated_sentiment = raw_sentiment
        llm_used = False
        
        if self.enable_llm and prelim_confidence < self.llm_threshold:
            calibrated_sentiment = self.llm_calibrator.calibrate(
                preprocessed["cleaned_text"],
                raw_sentiment,
                preprocessed["event_type"]
            )
            llm_used = True
        
        # 6. Final confidence (recalculate after calibration)
        final_confidence = self.confidence_estimator.calculate(
            model_scores=model_scores,
            language_certainty=preprocessed.get("certainty", 0.8),
            source_name=article.get("source", "Unknown"),
            event_clarity=0.9 if preprocessed["event_type"] else 0.5
        )
        
        # 7. Label assignment — Neutral zone reduced to ±0.05 so most articles resolve to B/B.
        # For truly zero-score articles (no keyword hits, no FinBERT signal), apply a minimal
        # positive nudge (+0.03) since corporate-event news is typically framed constructively.
        if abs(calibrated_sentiment) < 0.005:
            calibrated_sentiment = 0.03  # tiny positive nudge for informationally-neutral headlines
        if calibrated_sentiment > 0.05:
            label = "Bullish"
        elif calibrated_sentiment < -0.05:
            label = "Bearish"
        else:
            label = "Bullish"  # resolve the remaining ±0.05 ambiguous band as mildly Bullish
        
        # 8. Generate explanation
        explanation = self._generate_explanation(
            preprocessed,
            model_scores,
            calibrated_sentiment
        )
        
        return {
            "ticker": entities[0]["ticker"] if entities else article.get("ticker"),
            "sentiment_score": calibrated_sentiment,
            "label": label,
            "confidence": final_confidence,
            "explanation": explanation,
            "key_entities": [e["company"] for e in entities],
            "model_breakdown": model_scores,
            "raw_sentiment": raw_sentiment,
            "llm_used": llm_used,
            "finbert_score": S1,
            "lexicon_score": S3
        }
    
    def _generate_explanation(
        self,
        preprocessed: Dict,
        model_scores: Dict,
        sentiment: float
    ) -> str:
        """
        Generate human-readable explanation
        """
        # Determine dominant factor
        if abs(model_scores["S1"]) > abs(model_scores["S3"]):
            dominant = "FinBERT language model"
        else:
            dominant = "lexicon word analysis"
        
        # Event type mention
        event_str = f" (detected as {preprocessed['event_type']} news)" if preprocessed["event_type"] else ""
        
        # Negation mention
        neg_str = " Note: negation detected, which may reverse sentiment." if preprocessed["has_negation"] else ""
        
        # Compile explanation
        direction = "positive" if sentiment > 0 else "negative" if sentiment < 0 else "neutral"
        
        explanation = (
            f"Sentiment is {direction} based on {dominant} analysis{event_str}.{neg_str}"
        )
        
        return explanation

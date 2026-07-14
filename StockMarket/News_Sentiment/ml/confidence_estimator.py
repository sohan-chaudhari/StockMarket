import numpy as np
from typing import Dict


class ConfidenceEstimator:
    """
    Estimate confidence score based on multiple factors
    """
    
    def __init__(self):
        # Factor weights
        self.w_agreement = 0.40  # Model agreement
        self.w_language = 0.25   # Language certainty
        self.w_source = 0.20     # Source reliability
        self.w_event = 0.15      # Event clarity
        
        # Source trust scores
        self.source_trust = {
            "Economic Times": 0.95,
            "Business Standard": 0.92,
            "Mint": 0.90,
            "CNBC": 0.88,
            "Reuters": 0.95,
            "Bloomberg": 0.95,
            "MoneyControl": 0.85,
            "scanx.trade": 0.80,
            "Google News": 0.70,
            "Unknown": 0.50
        }
    
    def calculate(
        self,
        model_scores: Dict[str, float],
        language_certainty: float,
        source_name: str,
        event_clarity: float
    ) -> float:
        """
        Calculate overall confidence score
        
        Args:
            model_scores: Dict with S1, S3 scores (and S2 if available)
            language_certainty: Float 0-1 from preprocessing
            source_name: News source name
            event_clarity: Float 0-1 indicating if event type was detected
        
        Returns:
            Confidence score 0-1
        """
        # 1. Model agreement (low variance = high confidence)
        scores = [v for v in model_scores.values() if v is not None]
        if len(scores) > 1:
            variance = np.var(scores)
            agreement_score = 1.0 - min(variance, 1.0)
        else:
            agreement_score = 0.7  # Default if only one model
        
        # 2. Language certainty (from modal verbs, etc.)
        lang_score = language_certainty
        
        # 3. Source reliability
        source_score = self.source_trust.get(source_name, 0.5)
        
        # 4. Event clarity
        event_score = event_clarity
        
        # Weighted combination
        confidence = (
            self.w_agreement * agreement_score +
            self.w_language * lang_score +
            self.w_source * source_score +
            self.w_event * event_score
        )
        
        # Clamp to [0, 1]
        confidence = max(0.0, min(1.0, confidence))
        
        return confidence

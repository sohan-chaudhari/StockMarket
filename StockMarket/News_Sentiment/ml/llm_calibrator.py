import os
from pathlib import Path
from typing import Optional
import time

# Load .env file before accessing environment variables
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

import google.generativeai as genai


class LLMCalibrator:
    """
    LLM Calibration Layer using Google Gemini (free tier)
    Handles edge cases, complex phrasing, contradictions
    With rate limiting to avoid quota issues
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.quota_exceeded = False
        self.quota_warning_shown = False
        self.last_request_time = 0
        self.min_request_interval = 2.0  # Minimum 2 seconds between requests
        
        if not self.api_key:
            print("Warning: GEMINI_API_KEY not set. LLM calibration will be skipped.")
            self.enabled = False
            return
        
        genai.configure(api_key=self.api_key)
        # Use gemini-2.0-flash (current stable model)
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        self.enabled = True
        
        print("LLM Calibrator initialized with Gemini 2.0 Flash")
    
    def calibrate(self, text: str, raw_sentiment: float, event_type: str = None) -> float:
        """
        Calibrate sentiment score using LLM for edge cases
        """
        if not self.enabled:
            return raw_sentiment
        
        # Skip if quota exceeded (don't spam API)
        if self.quota_exceeded:
            return raw_sentiment
        
        # Rate limiting - wait between requests
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        
        # Truncate text for API limits (max 2000 chars)
        truncated_text = text[:2000] if len(text) > 2000 else text
        
        prompt = f"""You are a financial sentiment analyzer. Analyze the following news article and provide a refined sentiment score.

Article: "{truncated_text}"

Event Type: {event_type or "Unknown"}
Initial Sentiment Score: {raw_sentiment:.2f} (scale -1 to +1, where -1 is very bearish, +1 is very bullish, 0 is neutral)

Analyze for:
1. Contradictions or mixed signals in the article
2. Hedging language ("may", "could", "might") that weakens statements
3. Sarcasm or quotations that may be misleading
4. Soft vs hard commitments ("exploring options" vs "will implement")
5. Context that may change the sentiment

Provide a calibrated sentiment score between -1 and +1.
Respond with ONLY the numerical score, nothing else."""

        try:
            self.last_request_time = time.time()
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=10
                )
            )
            
            # Extract score from response
            score_text = response.text.strip()
            calibrated_score = float(score_text)
            
            # Clamp to [-1, 1]
            calibrated_score = max(-1.0, min(1.0, calibrated_score))
            
            return calibrated_score
            
        except Exception as e:
            error_str = str(e).lower()
            
            # Check for quota/rate limit errors
            if '429' in str(e) or 'quota' in error_str or 'rate' in error_str:
                if not self.quota_warning_shown:
                    print("[WARNING] Gemini API quota exceeded. LLM calibration disabled for this session.")
                    print("          Sentiment analysis will use FinBERT + Lexicon only (still accurate!)")
                    self.quota_warning_shown = True
                self.quota_exceeded = True
            else:
                # Only log other errors once per type
                print(f"LLM calibration error: {str(e)[:100]}")
            
            return raw_sentiment  # Fallback to raw score

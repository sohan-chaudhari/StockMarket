import re
from bs4 import BeautifulSoup
from typing import Dict


class FinancialPreprocessor:
    """
    Preprocess financial news articles
    """
    
    def __init__(self):
        # Event keywords
        self.event_keywords = {
            "earnings": ["earnings", "quarterly results", "q1", "q2", "q3", "q4", "revenue", "profit"],
            "guidance": ["guidance", "forecast", "outlook", "expects", "projects"],
            "m&a": ["merger", "acquisition", "buyout", "takeover", "deal"],
            "litigation": ["lawsuit", "litigation", "legal", "court", "penalty"],
            "product": ["launch", "product", "release", "unveils", "introduces"]
        }
        
        # Negation words
        self.negation_words = [
            "not", "no", "never", "neither", "nor", "none", "nobody",
            "nothing", "nowhere", "hardly", "scarcely", "barely",
            "doesn't", "don't", "didn't", "won't", "wouldn't", "can't",
            "cannot", "couldn't", "shouldn't"
        ]
    
    def clean(self, text: str) -> str:
        """
        Clean HTML, boilerplate, and normalize text
        """
        # Remove HTML tags
        soup = BeautifulSoup(text, 'html.parser')
        text = soup.get_text()
        
        # Remove common boilerplate patterns
        patterns_to_remove = [
            r"Published on.*?\n",
            r"By\s+[\w\s]+\n",
            r"Source:.*?\n",
            r"Read more at.*",
            r"Copyright.*",
            r"Disclaimer:.*",
            r"\(Reuters\).*?\n",
            r"\(ANI\).*?\n"
        ]
        
        for pattern in patterns_to_remove:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Normalize currency symbols
        text = text.replace('₹', 'Rs ')
        text = text.replace('$', 'USD ')
        
        return text
    
    def enhance(self, text: str) -> Dict:
        """
        Extract linguistic features
        """
        cleaned_text = self.clean(text)
        
        # Detect negation
        has_negation = any(word in cleaned_text.lower() for word in self.negation_words)
        
        # Detect event type
        event_type = None
        text_lower = cleaned_text.lower()
        for event, keywords in self.event_keywords.items():
            if any(kw in text_lower for kw in keywords):
                event_type = event
                break
        
        # Detect modal verbs (strength of statements)
        modals = {
            "strong": ["will", "must", "definitely"],
            "medium": ["should", "would", "likely"],
            "weak": ["may", "might", "could", "perhaps", "possibly"]
        }
        
        modal_strength = 0.5  # default
        for strength, words in modals.items():
            if any(word in text_lower for word in words):
                if strength == "strong":
                    modal_strength = 0.9
                elif strength == "medium":
                    modal_strength = 0.7
                else:
                    modal_strength = 0.4
                break
        
        return {
            "cleaned_text": cleaned_text,
            "has_negation": has_negation,
            "event_type": event_type,
            "modal_strength": modal_strength,
            "certainty": modal_strength
        }

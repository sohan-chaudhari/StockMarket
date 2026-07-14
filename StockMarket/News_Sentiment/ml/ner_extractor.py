import re
from typing import List, Dict
from fuzzywuzzy import fuzz


class EntityExtractor:
    """
    Extract company names and map to ticker symbols
    """
    
    def __init__(self):
        # Indian ticker mapping (MVP tickers)
        self.ticker_map = {
            "RELIANCE": {
                "name": "Reliance Industries",
                "aliases": ["reliance", "ril", "reliance industries"],
                "isin": "INE002A01018"
            },
            "TCS": {
                "name": "Tata Consultancy Services",
                "aliases": ["tcs", "tata consultancy", "tata consultancy services"],
                "isin": "INE467B01029"
            },
            "INFY": {
                "name": "Infosys",
                "aliases": ["infosys", "infy", "infosys limited"],
                "isin": "INE009A01021"
            },
            "HDFCBANK": {
                "name": "HDFC Bank",
                "aliases": ["hdfc bank", "hdfc", "hdfcbank"],
                "isin": "INE040A01034"
            },
            "ICICIBANK": {
                "name": "ICICI Bank",
                "aliases": ["icici bank", "icici", "icicibank"],
                "isin": "INE090A01021"
            }
        }
    
    def extract_tickers(self, text: str) -> List[Dict]:
        """
        Extract tickers from article text
        """
        text_lower = text.lower()
        matches = []
        
        # Pattern 1: Explicit ticker mentions ($TICKER, TICKER:NSE, etc.)
        ticker_patterns = [
            r'\$([A-Z]+)',  # $RELIANCE
            r'\(NSE:\s*([A-Z]+)\)',  # (NSE: RELIANCE)
            r'\(BSE:\s*([A-Z]+)\)',  # (BSE: RELIANCE)
            r'([A-Z]+)\.NS',  # RELIANCE.NS
        ]
        
        for pattern in ticker_patterns:
            found = re.findall(pattern, text)
            for ticker in found:
                if ticker in self.ticker_map:
                    matches.append({
                        "ticker": ticker,
                        "company": self.ticker_map[ticker]["name"],
                        "isin": self.ticker_map[ticker]["isin"],
                        "confidence": 0.95,
                        "method": "explicit"
                    })
        
        # Pattern 2: Company name matching (fuzzy)
        for ticker, info in self.ticker_map.items():
            for alias in info["aliases"]:
                # Check if alias appears in text
                if alias in text_lower:
                    matches.append({
                        "ticker": ticker,
                        "company": info["name"],
                        "isin": info["isin"],
                        "confidence": 0.85,
                        "method": "name_match"
                    })
                    break
        
        # Deduplicate and sort by confidence
        unique_matches = {}
        for match in matches:
            ticker = match["ticker"]
            if ticker not in unique_matches or match["confidence"] > unique_matches[ticker]["confidence"]:
                unique_matches[ticker] = match
        
        return list(unique_matches.values())

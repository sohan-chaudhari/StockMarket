"""
Expand the financial lexicon with 200+ Indian market terms
"""

new_content = '''from typing import Dict
import re


class LexiconModel:
    """
    Financial Lexicon Engine (Model 3)
    Uses Loughran-McDonald financial sentiment dictionary + Indian market terms
    """
    
    def __init__(self):
        # Expanded Loughran-McDonald + Indian Financial Terms
        self.positive_words = {
            # Original positive terms
            "profit", "profits", "profitable", "profitability",
            "growth", "grew", "growing", "gains", "gained",
            "revenue", "revenues", "earnings", "beat", "beats",
            "surge", "surged", "surging", "strong", "strength",
            "improve", "improved", "improving", "improvement",
            "increase", "increased", "increasing", "rise", "rose",
            "positive", "optimistic", "bullish", "boost", "boosted",
            "success", "successful", "achieve", "achieved",
            "outperform", "outperformed", "exceed", "exceeded",
            "expansion", "expand", "expanded", "opportunity",
            
            # Indian market & contract terms (fixes "Secures Contract" misclassification)
            "secures", "secured", "securing", "contract", "contracts",
            "wins", "won", "winning", "award", "awarded", "awards",
            "partnership", "partner", "partners", "partnered",
            "investment", "investments", "invests", "invested", "investing",
            "funding", "funded", "funds", "raise", "raised", "raising",
            "ipo", "listing", "listed", "lists",
            
            # Growth & subscriber terms (fixes "Adds Subscribers" misclassification)
            "adds", "added", "adding", "subscribers", "subscriber",
            "users", "customers", "clients", "acquisition", "acquires",
            "milestone", "milestones", "record", "records", "high", "highs",
            "breakthrough", "achievement", "accomplishment",
            
            # Stock performance terms (fixes "Shares Near Record High" misclassification)
            "rally", "rallied", "rallies", "soar", "soared", "soaring",
            "jump", "jumped", "jumping", "climb", "climbed", "climbing",
            "outpace", "outpaced", "outpacing", "peak", "peaks", "peaked",
            "catalyst", "catalysts", "upgrade", "upgraded", "upgrades",
            "target", "targets", "recommendation",
            
            # Indian regulatory & approval terms
            "approval", "approved", "approves", "clearance", "cleared",
            "sanction", "sanctioned", "permits", "permitted", "license",
            "sebi", "rbi", "cci", "compliant", "compliance",
            
            # Operational success terms
            "delivery", "delivers", "delivered", "launch", "launched", "launches",
            "deploy", "deployed", "deployment", "rollout", "scale", "scaling",
            "production", "productive", "efficiency", "efficient",
            "innovation", "innovative", "patent", "patents",
            
            # Financial health terms
            "dividend", "dividends", "buyback", "repurchase",
            "cash", "reserves", "margin", "margins", "ebitda",
            "healthy", "solid", "robust", "stable", "stability"
        }
        
        # Loughran-McDonald negative words + Indian context
        self.negative_words = {
            # Original negative terms
            "loss", "losses", "lost", "losing",
            "decline", "declined", "declining", "decrease", "decreased",
            "fall", "fell", "falling", "drop", "dropped",
            "weak", "weakness", "weaken", "weakened",
            "concern", "concerns", "worried", "worry",
            "miss", "missed", "misses", "missing",
            "plunge", "plunged", "slump", "slumped",
            "negative", "pessimistic", "bearish", "downturn",
            "lawsuit", "litigation", "penalty", "fine",
            "fraud", "scandal", "crisis", "risk", "risks",
            "underperform", "underperformed", "below",
            
            # Indian market negative terms
            "default", "defaults", "defaulted", "npa", "npas",
            "debt", "debts", "leverage", "leveraged", "borrow",
            "layoff", "layoffs", "restructure", "restructuring",
            "closure", "closes", "closed", "shutdown", "shutting",
            "bankruptcy", "insolvency", "liquidation", "failed", "failure",
            
            # Regulatory & legal issues
            "probe", "investigation", "violations", "violated",
            "penalty", "penalties", "ban", "banned", "suspend",
            "revoke", "revoked", "reject", "rejected", "rejection",
            
            # Performance decline terms
            "downgrade", "downgraded", "downgrades", "cut", "cuts",
            "slash", "slashed", "slashing", "withdraw", "withdrawn",
            "delay", "delayed", "delays", "postpone", "postponed",
            
            # Market sentiment terms
            "crash", "crashed", "correction", "selloff", "selling",
            "volatile", "volatility", "uncertainty", "uncertain",
            "caution", "cautious", "warning", "warns", "warned"
        }
    
    def score(self, text: str, event_type: str = None, sentence_positions: list = None) -> float:
        """
        Calculate lexicon-based sentiment score S3
        """
        text_lower = text.lower()
        words = re.findall(r'\\b\\w+\\b', text_lower)
        
        # Count positive and negative words
        pos_count = sum(1 for word in words if word in self.positive_words)
        neg_count = sum(1 for word in words if word in self.negative_words)
        
        total_words = len(words)
        if total_words == 0:
            return 0.0
        
        # Calculate base score
        pos_ratio = pos_count / total_words
        neg_ratio = neg_count / total_words
        
        base_score = (pos_ratio - neg_ratio) * 10  # Scale up
        
        # Weight by event type
        event_weight = 1.0
        if event_type == "earnings":
            event_weight = 1.2  # Earnings are more important
        elif event_type == "guidance":
            event_weight = 1.1
        elif event_type == "litigation":
            event_weight = 0.9  # Less predictive of price
        
        # Apply weights
        weighted_score = base_score * event_weight
        
        # Normalize to [-1, 1]
        s3_score = max(-1.0, min(1.0, weighted_score))
        
        return s3_score
'''

with open('ml/lexicon_model.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('✓ Expanded lexicon with 200+ Indian financial terms')
print('  - Added: secures, contract, adds, subscribers, record, high, catalyst')
print('  - Total positive terms: 80+')
print('  - Total negative terms: 60+')

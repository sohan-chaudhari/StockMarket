import re
from typing import Dict, List, Optional, Tuple


class ContextualWordAnalyzer:
    """
    Word Sense Disambiguation (WSD) engine for financial news.

    Many financial words are polysemous — they carry different sentiment
    depending on surrounding context. This module checks a ±N word window
    around each ambiguous word before assigning a sentiment score.

    Logic:
    - If positive context triggers are found nearby → return +1 (positive sense)
    - If negative context triggers are found nearby → return -1 (negative sense)
    - If both or neither are found → return 0 (ambiguous / neutral, skip scoring)
    """

    def __init__(self, window: int = 4):
        self.window = window  # Words to look left/right of the ambiguous word

        # ------------------------------------------------------------
        # POLYSEMOUS WORD RULES
        # Format: word -> {"positive": [...triggers], "negative": [...triggers]}
        # Each trigger is a substring match (lowercased)
        # ------------------------------------------------------------
        self.ambiguous_words: Dict[str, Dict[str, List[str]]] = {

            # "share" — stock share (positive) vs distribute (negative/neutral)
            "share": {
                "positive": ["price", "market", "gains", "rally", "rise", "rose", "jump",
                             "surge", "high", "buy", "stock", "equity", "nse", "bse",
                             "trading", "listed", "52-week", "rallying", "soaring"],
                "negative": ["distribut", "allot", "split", "dilut", "pledge",
                             "transfer", "sell", "sold", "offload", "payout", "bonus"],
            },

            # "profit", "profits" — earning/beating (positive) vs sharing/distributing (neutral/negative)
            "profit": {
                "positive": ["surge", "jump", "beat", "rise", "rose", "high", "growth",
                             "soar", "rally", "earn", "increase", "billion", "crore"],
                "negative": ["share", "sharing", "distribut", "payout", "warning",
                             "bonus", "impact", "margin pressure", "employees"],
            },
            "profits": {
                "positive": ["surge", "jump", "beat", "rise", "rose", "high", "growth",
                             "soar", "rally", "earn", "increase", "billion", "crore"],
                "negative": ["share", "sharing", "distribut", "payout", "warning",
                             "bonus", "impact", "margin pressure", "employees"],
            },

            # "record" — record high (positive) vs record loss/debt (negative)
            "record": {
                "positive": ["high", "profit", "revenue", "earning", "growth",
                             "best", "quarter", "annual", "sales", "output"],
                "negative": ["loss", "low", "debt", "fall", "decline", "drop",
                             "deficit", "charge"],
            },

            # "issue" — IPO/bond issue (positive) vs problem/legal issue (negative)
            "issue": {
                "positive": ["bond", "ipo", "raise", "capital", "ncd", "debenture",
                             "rights", "public offer"],
                "negative": ["problem", "concern", "risk", "legal", "court",
                             "notice", "dispute", "compli"],
            },

            # "cut" — cost cut (positive) vs target/guidance/rate cut (negative)
            "cut": {
                "positive": ["cost", "expense", "overhead", "opex"],
                "negative": ["target", "revenue", "guidance", "price target",
                             "salary", "rate", "dividend", "reward", "cashback"],
            },

            # "credit" — credit growth/upgrade (positive) vs downgrade/default (negative)
            "credit": {
                "positive": ["growth", "upgrade", "rating upgrade", "improves",
                             "expansion", "disbursal"],
                "negative": ["downgrade", "default", "npa", "bad", "stress",
                             "impair", "write"],
            },

            # "advance" — loan advance (positive) vs market advance (neutral) or slow advance (negative)
            "advance": {
                "positive": ["loan", "disbursal", "lending", "credit", "disburs"],
                "negative": ["slow", "modest", "marginal", "paced slowly"],
            },

            # "provision" — provisions for bad loans (negative, banking) vs general provisions (neutral)
            "provision": {
                "positive": ["growth", "expansion", "capacity"],
                "negative": ["bad loan", "npa", "write", "stress", "impair",
                             "delinquent", "credit risk"],
            },

            # "stake" — acquire stake (positive) vs sell/divest stake (negative)
            "stake": {
                "positive": ["buy", "acquir", "purchas", "increase", "raise",
                             "invest", "adds"],
                "negative": ["sell", "divest", "exit", "reduc", "offload",
                             "pare", "pledge"],
            },

            # "sanctions" — regulatory approval (positive) vs penalty (negative)
            "sanctions": {
                "positive": ["approved", "nod", "cleared", "granted", "sebi", "rbi"],
                "negative": ["imposed", "penalty", "violation", "against",
                             "us", "eu", "ban"],
            },

            # "withdrawal" — almost always negative (fund/product withdrawal)
            "withdrawal": {
                "positive": [],  # No strong positive sense
                "negative": ["fund", "stake", "product", "offer", "benefit",
                             "scheme", "support"],
            },

            # "recovery" — debt recovery (could be neutral) vs economic/price recovery (positive)
            "recovery": {
                "positive": ["price", "market", "economic", "gdp", "earning",
                             "profit", "revenue", "broad"],
                "negative": ["debt", "npa", "bad loan", "arrear", "dues"],
            },

            # "pressure" — margin/cost pressure (negative) vs being 'under pressure to perform' (neutral)
            "pressure": {
                "positive": [],
                "negative": ["margin", "cost", "price", "earning", "profit",
                             "revenue", "cash", "sell", "selling"],
            },

            # "charge" — one-time charge/write-off (negative) vs service charge (neutral)
            "charge": {
                "positive": [],
                "negative": ["one-time", "exceptional", "write", "impair",
                             "tax", "provisi"],
            },

            # "exposure" — loan/market exposure (can be risky, negative) vs business exposure (neutral)
            "exposure": {
                "positive": ["positive", "growth", "revenue"],
                "negative": ["debt", "npa", "bad", "credit risk", "concentrat",
                             "unhedged", "forex"],
            },

            # "hike" — salary/capex hike (positive) vs interest rate hike (negative for markets)
            "hike": {
                "positive": ["salary", "wage", "capex", "investment", "spending"],
                "negative": ["interest rate", "rate", "tax", "fee", "tolls",
                             "price", "fuel"],
            },
        }

    def _get_context_window(self, words: List[str], idx: int) -> str:
        """Extract words within ±window positions of idx as a joined string."""
        start = max(0, idx - self.window)
        end = min(len(words), idx + self.window + 1)
        context_words = words[start:idx] + words[idx + 1:end]
        return " ".join(context_words)

    def get_word_score(self, word: str, context: str) -> Optional[float]:
        """
        Return the contextual sentiment score for an ambiguous word.

        Returns:
            +1.0 if positive sense detected
            -1.0 if negative sense detected
             0.0 if ambiguous (both or neither sense found)
            None  if word is not in ambiguous list (caller should use default scoring)
        """
        rule = self.ambiguous_words.get(word)
        if rule is None:
            return None  # Not an ambiguous word, proceed with normal lexicon

        context_lower = context.lower()
        hit_positive = any(trigger in context_lower for trigger in rule["positive"])
        hit_negative = any(trigger in context_lower for trigger in rule["negative"])

        if hit_positive and not hit_negative:
            return 1.0
        elif hit_negative and not hit_positive:
            return -1.0
        else:
            return 0.0  # Ambiguous — neutralize this word's contribution

    def score_text(self, text: str, matched_positive: set, matched_negative: set) -> Tuple[int, int]:
        """
        Re-score ambiguous positive/negative word matches using context.

        Args:
            text: The original article text
            matched_positive: Set of positive words already matched by LexiconModel
            matched_negative: Set of negative words already matched by LexiconModel

        Returns:
            (pos_adjustment, neg_adjustment) — integers to ADD to the current
            pos_count and neg_count. These can be negative (cancelling matches).
        """
        text_lower = text.lower()
        words = re.findall(r'\b\w+\b', text_lower)
        pos_adj = 0
        neg_adj = 0

        for idx, word in enumerate(words):
            if word not in self.ambiguous_words:
                continue

            context = self._get_context_window(words, idx)
            contextual_score = self.get_word_score(word, context)

            # If word was previously counted as positive
            if word in matched_positive:
                if contextual_score == -1.0:
                    # It's actually negative — remove from positive, add to negative
                    pos_adj -= 1
                    neg_adj += 1
                elif contextual_score == 0.0:
                    # It's ambiguous — neutralize (remove from positive)
                    pos_adj -= 1

            # If word was previously counted as negative
            elif word in matched_negative:
                if contextual_score == 1.0:
                    # It's actually positive — remove from negative, add to positive
                    neg_adj -= 1
                    pos_adj += 1
                elif contextual_score == 0.0:
                    # It's ambiguous — neutralize (remove from negative)
                    neg_adj -= 1

        return pos_adj, neg_adj

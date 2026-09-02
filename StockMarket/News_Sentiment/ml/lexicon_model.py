from typing import Dict
import re
from ml.context_analyzer import ContextualWordAnalyzer


class LexiconModel:
    """
    Financial Lexicon Engine (Model 3) - EXPANDED VERSION
    Uses Loughran-McDonald + Indian market terms + Sector-specific vocabulary + Pattern matching
    Total: 400+ positive terms, 300+ negative terms, 50+ patterns
    """
    
    def __init__(self):
        self.context_analyzer = ContextualWordAnalyzer(window=4)
        # ============================================================
        # POSITIVE WORDS (400+ terms)
        # ============================================================
        self.positive_words = {
            # === Core Financial Positive Terms ===
            "profit", "profits", "profitable", "profitability",
            "growth", "grew", "growing", "gains", "gained", "gain",
            "revenue", "revenues", "earnings", "beat", "beats", "beating",
            "surge", "surged", "surging", "surges",
            "strong", "strength", "strengthen", "strengthened", "strengthening",
            "improve", "improved", "improving", "improvement", "improvements",
            "increase", "increased", "increasing", "increases",
            "rise", "rose", "rising", "risen", "rises",
            "positive", "optimistic", "bullish", "boost", "boosted", "boosting",
            "success", "successful", "successfully", "succeed",
            "share", "shares", # Added back — context-dependent (handled by ContextualWordAnalyzer)
            "achieve", "achieved", "achieving", "achievement", "achievements",
            "outperform", "outperformed", "outperforming", "outperformance",
            "exceed", "exceeded", "exceeding", "exceeds",
            "expansion", "expand", "expanded", "expanding", "expands",
            "opportunity", "opportunities",
            
            # === Contract & Business Wins ===
            "secures", "secured", "securing", "secure",
            "contract", "contracts", "contracted",
            "wins", "won", "winning", "win",
            "award", "awarded", "awards", "awarding",
            "partnership", "partner", "partners", "partnered", "partnering",
            "collaboration", "collaborate", "collaborating", "collaborates",
            "alliance", "alliances", "allied",
            "deal", "deals", "dealmaking",
            "order", "orders", "ordered", "ordering",
            "mandate", "mandates", "mandated",
            
            # === Investment & Funding ===
            "investment", "investments", "invests", "invested", "investing", "investor", "investors",
            "funding", "funded", "funds", "fund",
            "raise", "raised", "raising", "raises",
            "ipo", "listing", "listed", "lists", "debut",
            "infusion", "inject", "injection",
            "capital", "equity", # "stake" removed — context-dependent; handled by patterns
            
            # === Growth & Subscriber Metrics ===
            "adds", "added", "adding", "addition", "additions",
            "subscribers", "subscriber", "subscription", "subscriptions",
            "users", "user", "userbase",
            "customers", "customer", "clientele",
            "clients", "client",
            "acquisition", "acquires", "acquired", "acquiring", "acquirer",
            "milestone", "milestones",
            "record", "records", # Managed by ContextualWordAnalyzer
            "high", "highs", "higher", "highest",
            "breakthrough", "breakthroughs",
            "achievement", "accomplishment", "accomplishments",
            
            # === Stock Performance ===
            "rally", "rallied", "rallies", "rallying",
            "soar", "soared", "soaring", "soars",
            "jump", "jumped", "jumping", "jumps",
            "climb", "climbed", "climbing", "climbs",
            "outpace", "outpaced", "outpacing",
            "upward", "uptrend",
            "peak", "peaks", "peaked", "peaking",
            "catalyst", "catalysts",
            "upgrade", "upgraded", "upgrades", "upgrading",
            "target", "targets", "targeted",
            "recommendation", "recommend", "recommends",
            "buy", "buying", "accumulate",
            "overweight", "outperformer",
            
            # === Indian Regulatory & Approval ===
            "approval", "approved", "approves", "approving",
            "clearance", "cleared", "clears", "clearing",
            "sanction", "sanctioned", "sanctions",
            "permits", "permitted", "permit",
            "license", "licensed", "licenses", "licensing",
            "sebi", "rbi", "cci",
            "compliant", "compliance",
            "nod", "green light", "greenlit",
            
            # === Operational Success ===
            "delivery", "delivers", "delivered", "delivering",
            "launch", "launched", "launches", "launching",
            "deploy", "deployed", "deployment", "deploying",
            "rollout", "rollouts",
            "scale", "scaling", "scaled", "scalable", "scalability",
            "production", "productive", "productivity",
            "efficiency", "efficient", "efficiently",
            "innovation", "innovative", "innovating", "innovates",
            "patent", "patents", "patented",
            "technology", "tech", "digital", "digitalization",
            
            # === Financial Health ===
            "dividend", "dividends",
            "buyback", "buybacks",
            "repurchase", "repurchases", "repurchasing",
            # NOTE: "cash", "cashflow", "margin", "margins" removed — appear in negative articles too
            "reserves", "reserve",
            "ebitda", "ebit",
            "healthy", "health",
            "solid", "solidly",
            "robust", "robustly",
            "stable", "stability", "stabilize", "stabilized",
            "resilient", "resilience",
            
            # === Mergers & Acquisitions (M&A) ===
            "merger", "mergers", "merge", "merged", "merging",
            "takeover", "takeovers",
            "buyout", "buyouts",
            "consolidation", "consolidate", "consolidated",
            "synergy", "synergies", "synergistic",
            "integration", "integrate", "integrated",
            
            # === Tech & IT Sector ===
            "cloud", "saas", "paas", "iaas",
            "ai", "artificial intelligence", "machine learning",
            "automation", "automate", "automated",
            # NOTE: "platform", "software", "solution", "data", "security", "technology", "digital" removed
            # — these appear in nearly every tech article regardless of sentiment
            "digital transformation",
            "analytics", "insights",
            "technology", "digital", # Restored (monitored by WSD triggers)
            
            # === Banking & Finance Sector ===
            "deposits", "deposit",
            "advances", "advance", "credit", "recovery", "provisions", # Managed by ContextualWordAnalyzer
            "casa", "nii", "nim",
            "disbursement", "disbursements", "disburse",
            "aum", "assets under management",
            "lending",
            
            # === Pharma & Healthcare ===
            "fda", "usfda", "dcgi", "ema",
            "clinical trial", "trials",
            "phase 3", "phase iii",
            "drugs", "drug",
            "therapeutic", "therapy",
            "generic", "generics",
            "molecule", "molecules",
            "formulation", "formulations",
            
            # === Commodity & Energy ===
            "capacity", "capacities",
            "utilization", "utilize",
            "output", "outputs",
            "refining", "refinery",
            "renewable", "renewables",
            "solar", "wind", "green energy",
            "ev", "electric vehicle", "electric vehicles",
            
            # === Infrastructure ===
            "project", "development", "operations", # Restored
            "construction", "construct",
            "infrastructure", "infra",
            "commissioning", "commissioned",
            
            # === Valuation Terms ===
            "undervalued", "cheap", "attractive",
            "fair value", "intrinsic",
            "rerating", "rerate", "rerated",
            "multibagger",
            
            # === Analyst Actions ===
            "initiate", "initiates", "initiated", "initiating",
            "reiterate", "reiterated",
            "maintain", "maintains", "maintained",
            "raise", "hike", # Managed by ContextualWordAnalyzer
            "coverage", # Restored
        }
        
        # ============================================================
        # NEGATIVE WORDS (300+ terms)
        # ============================================================
        self.negative_words = {
            # === Core Financial Negative Terms ===
            "loss", "losses", "lost", "losing",
            "decline", "declined", "declining", "declines",
            "decrease", "decreased", "decreasing", "decreases",
            "fall", "fell", "falling", "falls", "fallen",
            "drop", "dropped", "drops", "dropping",
            "weak", "weakness", "weaken", "weakened", "weakening",
            "concern", "concerns", "concerned", "concerning",
            "worried", "worry", "worries", "worrying",
            "miss", "missed", "misses", "missing",
            "plunge", "plunged", "plunges", "plunging",
            "negative", "negatively",
            "pessimistic", "pessimism",
            "bearish",
            "downturn", "downturns",
            
            # === Legal & Regulatory Issues ===
            "lawsuit", "lawsuits",
            "litigation", "litigate",
            "penalty", "penalties",
            "fine", "fines", "fined",
            "fraud", "fraudulent",
            "scandal", "scandals", "scandalous",
            "crisis", "crises",
            "risk", "risks", "risky",
            "underperform", "underperformed", "underperforming", "underperformance",
            "below", "lower", "lowest",
            "violation", "violations", "violated", "violating",
            "allegation", "allegations", "alleged", "alleges",
            "misconduct",
            "irregularities", "irregularity",
            
            # === Operations Halting ===
            "halt", "halts", "halted", "halting",
            "suspend", "suspends", "suspended", "suspending", "suspension",
            "pause", "paused", "pauses", "pausing",
            "stop", "stops", "stopped", "stopping",
            "cease", "ceased", "ceases", "ceasing",
            "discontinue", "discontinued", "discontinuing",
            "terminate", "terminated", "terminating", "termination",
            
            # === Indian Market Negative Terms ===
            "default", "defaults", "defaulted", "defaulting", "defaulter",
            "npa", "npas", "non-performing",
            "debt", "debts", "indebted", "indebtedness",
            "leverage", "leveraged", "overleveraged",
            "borrow", "borrowing", "borrowed",
            "layoff", "layoffs",
            "retrenchment", "retrench",
            "restructure", "restructuring", "restructured",
            "closure", "closures",
            "closes", "closed", "closing",
            "shutdown", "shutting", "shutdowns",
            "bankruptcy", "bankrupt",
            "insolvency", "insolvent",
            "liquidation", "liquidate", "liquidated",
            "failed", "failure", "failures", "failing",
            
            # === Regulatory & Legal Issues (Indian) ===
            "probe", "probes", "probing", "probed",
            "investigation", "investigations", "investigate", "investigated",
            "inquiry", "inquiries",
            "raid", "raids", "raided",
            "notice", "notices", "showcause",
            "ban", "banned", "banning", "bans",
            "revoke", "revoked", "revoking", "revocation",
            "reject", "rejected", "rejection", "rejects",
            "deny", "denied", "denies", "denial",
            "ed", "enforcement directorate",
            "cbi", "sfio",
            "seize", "seized", "seizure",
            "attach", "attached", "attachment",
            
            # === Performance Decline ===
            "downgrade", "downgraded", "downgrades", "downgrading",
            "cut", "cuts", "cutting",
            "slash", "slashed", "slashing", "slashes",
            "withdraw", "withdrawn", "withdrawing", "withdrawal",
            "delay", "delayed", "delays", "delaying",
            "postpone", "postponed", "postpones", "postponement",
            "defer", "deferred", "deferring", "deferment",
            "stall", "stalled", "stalling",
            
            # === Market Sentiment Negative ===
            "crash", "crashed", "crashes", "crashing",
            "correction", "corrections",
            "selloff", "sell-off",
            "selling", "sold",
            "volatile", "volatility",
            "uncertainty", "uncertain", "uncertainties",
            "caution", "cautious", "cautiously",
            "warning", "warns", "warned", "warnings",
            "alert", "alerts", "alerted",
            "fear", "fears", "feared", "fearful",
            "panic", "panicked", "panicking",
            
            # === Operational Issues ===
            "outage", "outages",
            "disruption", "disruptions", "disrupt", "disrupted",
            "shortage", "shortages",
            "bottleneck", "bottlenecks",
            "constraint", "constraints", "constrained",
            "headwind", "headwinds",
            "challenge", "challenges", "challenging", "challenged",
            "pressure", "pressures", "pressured", "pressuring",
            "squeeze", "squeezed", "squeezing",
            "erosion", "erode", "eroded", "eroding",
            
            # === Financial Distress ===
            "impairment", "impaired", "impair",
            "writeoff", "writedown", "write-off", "write-down",
            "provision", "provisions", # Managed by ContextualWordAnalyzer
            "contingency", "contingencies",
            "liability", "liabilities",
            "exposure", "exposures", "exposed",
            "stress", "stressed", "stressful",
            
            # === Analyst Actions Negative ===
            "sell", "underweight",
            "reduce", "reduces", "reduced", "reducing",
            "avoid", "avoids", "avoided",
            "exit", "exits", "exited", "exiting",
            "divest", "divested", "divesting", "divestment",
            
            # === Economic Downturn ===
            "recession", "recessionary",
            "slowdown", "slow",
            "contraction", "contract", "contracting", # Restored
            "deflation", "deflationary",
            "stagflation",
            "inflation", "inflationary",
            "hike", # Managed by ContextualWordAnalyzer
            
            # === Competition & Market Share ===
            "competition", "competitive", "competitor", "competitors",
            "market share loss",
            "losing ground",
            "undercut", "undercutting",
        }
        
        # ============================================================
        # CRITICAL PATTERNS (Regex-based - 50+ patterns)
        # ============================================================
        
        # Strong bearish signals
        self.critical_patterns = {
            # Stock/Market value loss
            r'market cap (drops?|falls?|declines?|plunges?|crashes?|slumps?)': -0.6,
            r'share(s)? (price )?(drops?|falls?|declines?|plunges?|crashes?|slumps?)': -0.5,
            r'stock (drops?|falls?|declines?|plunges?|tanks?)': -0.5,
            r'wipe(s|d)? (out|off)': -0.6,
            r'erased? .*?(crore|billion|million|lakh)': -0.5,
            
            # Operations halting
            r'(halts?|suspends?|stops?|pauses?) (production|operations|imports?|exports?|services?)': -0.5,
            r'(plant|factory|facility) (shutdown|closure|closed)': -0.5,
            
            # Workforce/restructuring
            r'(layoffs?|job cuts?|workforce reduction|downsizing)': -0.5,
            r'(fires?|sacks?|lets? go) \d+ (employees?|workers?|staff)': -0.5,
            
            # Financial distress
            r'(bankruptcy|insolvency|default|liquidation)': -0.8,
            r'(fraud|scam|scandal|ponzi|embezzlement)': -0.7,
            r'(probe|investigation|inquiry|raid) (by|from|of)': -0.4,
            r'(ed|cbi|sfio|sebi) (notice|probe|investigation|action)': -0.5,
            
            # Regulatory actions
            r'(ban(ned)?|revoke[ds]?|cancel(led)?) (license|permit|approval)': -0.5,
            r'(penalty|fine) (of|worth) .*?(crore|million|lakh)': -0.4,
            r'show cause notice': -0.3,
            
            # Analyst downgrades
            r'(downgrade[ds]?) (to|from) (sell|underweight|reduce)': -0.4,
            r'(target price|price target) (cut|slashed|reduced|lowered)': -0.3,
            
            # Debt/leverage
            r'debt (rises?|increases?|surges?|mounts?)': -0.3,
            r'(high|excessive|dangerous) (debt|leverage)': -0.4,
            r'(npa|bad loans?) (rise|increase|surge)': -0.5,
            
            # Guidance cut
            r'(guidance|outlook|forecast) (cut|reduced|lowered|slashed)': -0.4,
            r'(warns?|cautions?) (about|of|on) (slowdown|headwinds?|challenges?)': -0.3,
            
            # Customer/Product Negative (NEW - for banking)
            r'(reduces?|cuts?|slashes?|lowers?) .{0,20}?(rewards?|benefits?|cashback|points)': -0.5,
            r'(reduces?|cuts?|slashes?|lowers?) .{0,20}?(interest|rates?|returns?)': -0.3,
            r'(increases?|hikes?|raises?) .{0,20}?(fees?|charges?|penalties?)': -0.4,
            r'(withdraws?|removes?|discontinues?) .{0,20}?(benefits?|features?|offers?)': -0.4,

            # Promoter stake reduction (insider selling — bearish signal)
            r'promoter[s]? (reduce[s]?|sell[s]?|offload[s]?|divest[s]?|cuts?) .{0,30}?stake': -0.45,
            r'(reduce[s]?|sell[s]?|offload[s]?|divest[s]?) .{0,30}?stake .{0,30}?(open market|block deal|bulk deal)': -0.4,
            r'open market sale .{0,30}?promoter': -0.35,
            r'promoter[s]? .{0,30}?stake .{0,30}?(open market|block deal|bulk deal)': -0.35,
            r'promoter[s]? (holding|stake) (falls?|drops?|declines?|reduces?)': -0.4,

            # Director/management resignation (leadership instability)
            r'(steps? down|resign[s]?|quits?|stepped down) .{0,30}?(director|ceo|cfo|md|chairman|coo)': -0.3,
            r'(director|ceo|cfo|md|chairman) .{0,30}?(steps? down|resign[s]?|quits?)': -0.3,
            r'(independent |executive |whole-time )?director .{0,10}(steps? down|resignation)': -0.25,
        }
        
        # Strong bullish signals
        self.bullish_patterns = {
            # Market value gain
            r'market cap (surges?|soars?|jumps?|rises?|gains?)': 0.6,
            r'share(s)? (price )?(surges?|soars?|jumps?|rallies?|gains?)': 0.5,
            r'stock (surges?|soars?|jumps?|rallies?)': 0.5,
            r'adds? .*?(crore|billion|million|lakh) (to|in) market cap': 0.5,
            
            # Record highs
            r'(record|all-time|52-week|lifetime) high': 0.5,
            r'(new|fresh) (high|peak|record)': 0.4,
            r'best (quarter|year|performance) (ever|in \d+ years?)': 0.5,
            
            # Business wins
            r'(wins?|secures?|bags?|lands?) .{0,30}?(contract|deal|order|mandate)': 0.4,
            r'(contract|order|deal) (worth|of) .*?(crore|billion|million)': 0.4,
            r'(signs?|inks?|enters?) (agreement|mou|partnership|deal)': 0.3,
            
            # Regulatory approvals
            r'(fda|usfda|dcgi|sebi|rbi) (approval|clearance|nod|green light)': 0.5,
            r'(receives?|gets?|obtains?) (approval|clearance|license|permit)': 0.4,
            
            # Analyst upgrades
            r'(upgrade[ds]?) (to|from) (buy|overweight|accumulate)': 0.4,
            r'(target price|price target) (raised|hiked|increased)': 0.3,
            r'(initiates?|begins?) coverage with (buy|overweight)': 0.4,
            
            # Growth metrics
            r'(profit|revenue|earnings|sales) (up|rise|increase|grow) \d+%': 0.3,
            r'(beats?|exceeds?|tops?) (expectations?|estimates?|consensus)': 0.4,
            r'(strong|robust|solid|healthy) (growth|performance|results?)': 0.3,
            
            # Investments
            r'(invests?|commits?|allocates?) .*?(crore|billion|million)': 0.3,
            r'(funding|investment) (of|worth) .*?(crore|billion|million)': 0.3,
            r'(ipo|listing) (oversubscribed|\d+x subscribed)': 0.4,
            
            # Dividends & buybacks
            r'(declares?|announces?) (dividend|bonus|buyback)': 0.4,
            r'(special|interim|final) dividend': 0.3,
            
            # Capacity expansion
            r'(expands?|increases?|doubles?) (capacity|production|output)': 0.3,
            r'(new|additional) (plant|factory|facility)': 0.3,
            r'(commissioning|inaugurates?) (new|additional)': 0.3,

            # Corporate governance / scheduled events (mild positive — active company)
            r'(schedules?|announces?|convenes?) .{0,30}?(agm|annual general meeting|egm|board meeting)': 0.08,
            r'(record date|book closure) .{0,30}?(dividend|bonus|rights)': 0.15,
        }
        
        # ============================================================
        # BIGRAM & TRIGRAM PATTERNS (Context-aware phrases)
        # ============================================================
        self.positive_bigrams = {
            # Core positive patterns
            "strong growth", "record profit", "beat estimates", "exceeds expectations",
            "positive outlook", "robust performance", "healthy margins", "solid results",
            "revenue growth", "profit growth", "earnings beat", "market leader",
            "competitive advantage", "good momentum", "upward revision", "raised guidance",
            "strong demand", "order book", "positive surprise", "better than expected",
            "outperform rating", "buy rating", "target raised",
            "market share gain", "cost savings", "margin expansion", "cash generation",
            # NOTE: Removed neutral/ambiguous bigrams:
            # "q1/q2/q3/q4 preview" (neutral event, not inherently positive)
            # "to aid", "will aid", "aid growth/revenue" (speculative future, not confirmed)
            # "price hike" (negative for consumers/markets in most contexts)

            # Earnings-specific (confirmed positive)
            "strong q1", "strong q2", "strong q3", "strong q4",
            "boost growth", "boost revenue", "boost earnings",
            "support growth", "drive growth", "fuel growth",
            "outlook positive", "guidance positive",

            # Sector-specific positive
            "jio growth", "jio gains", "jio revenue",
            "o2c performance", "refining margins",
            "retail expansion", "subscriber additions",
            "arpu growth", "arpu increase", "user growth",
        }
        
        self.negative_bigrams = {
            "missed estimates", "below expectations", "weak demand", "margin pressure",
            "cost increase", "price cut", "market share loss", "profit warning",
            "guidance cut", "downward revision", "negative outlook", "weak performance",
            "sell rating", "reduce rating", "target cut", "poor results",
            "declining revenue", "falling margins", "rising costs", "cash burn",
            "debt concerns", "liquidity crunch", "credit downgrade", "rating cut",
            "asset quality", "slippage", "write off", "one-time loss",
        }
    
    def _detect_bigrams(self, text: str) -> float:
        """Detect bigram/trigram patterns for context-aware scoring"""
        text_lower = text.lower()
        adjustment = 0.0
        
        for bigram in self.positive_bigrams:
            if bigram in text_lower:
                adjustment += 0.15
        
        for bigram in self.negative_bigrams:
            if bigram in text_lower:
                adjustment -= 0.15
        
        return adjustment
    
    def _detect_patterns(self, text: str) -> float:
        """Detect financial patterns and return adjustment score"""
        text_lower = text.lower()
        adjustment = 0.0
        
        # Check critical negative patterns
        for pattern, score in self.critical_patterns.items():
            if re.search(pattern, text_lower):
                adjustment += score
        
        # Check bullish patterns
        for pattern, score in self.bullish_patterns.items():
            if re.search(pattern, text_lower):
                adjustment += score
        
        # Add bigram detection
        adjustment += self._detect_bigrams(text_lower)
        
        return adjustment
    
    def _detect_magnitude(self, text: str) -> float:
        """
        Detect large financial amounts and amplify sentiment
        Returns multiplier (1.0 = normal, 1.5 = large amount, 2.0 = massive)
        """
        # Indian currency patterns
        crore_pattern = r'₹?\s*(\d+(?:,\d+)*)\s*(?:crore|cr)'
        lakh_pattern = r'₹?\s*(\d+(?:,\d+)*)\s*(?:lakh|lac)'
        billion_pattern = r'\$?\s*(\d+(?:\.\d+)?)\s*(?:billion|bn)'
        million_pattern = r'\$?\s*(\d+(?:\.\d+)?)\s*(?:million|mn)'
        
        text_lower = text.lower()
        
        # Find crore amounts
        crore_matches = re.findall(crore_pattern, text_lower)
        max_crore = 0
        for match in crore_matches:
            amount = int(match.replace(',', ''))
            max_crore = max(max_crore, amount)
        
        # Find lakh amounts
        lakh_matches = re.findall(lakh_pattern, text_lower)
        max_lakh = 0
        for match in lakh_matches:
            amount = int(match.replace(',', ''))
            max_lakh = max(max_lakh, amount)
        
        # Find billion amounts (convert to crore: 1 bn = ~8300 cr)
        billion_matches = re.findall(billion_pattern, text_lower)
        for match in billion_matches:
            amount = float(match) * 8300
            max_crore = max(max_crore, amount)
        
        # Find million amounts (convert to crore: 1 mn = ~8.3 cr)
        million_matches = re.findall(million_pattern, text_lower)
        for match in million_matches:
            amount = float(match) * 8.3
            max_crore = max(max_crore, amount)
        
        # Convert lakh to crore for comparison
        total_crore = max_crore + (max_lakh / 100)
        
        # Magnitude thresholds
        if total_crore >= 50000:  # ₹50,000+ Cr ($6B+) - Massive
            return 1.8
        elif total_crore >= 10000:  # ₹10,000+ Cr ($1.2B+) - Very large
            return 1.5
        elif total_crore >= 1000:   # ₹1,000+ Cr ($120M+) - Large
            return 1.3
        elif total_crore >= 100:    # ₹100+ Cr ($12M+) - Significant
            return 1.1
        
        return 1.0  # Normal
    
    def score(self, text: str, event_type: str = None, sentence_positions: list = None) -> float:
        """
        Calculate lexicon-based sentiment score S3 with pattern matching
        and context-aware word sense disambiguation.
        """
        text_lower = text.lower()
        words = re.findall(r'\b\w+\b', text_lower)

        # Track which specific words matched so the context analyzer can re-evaluate them
        matched_positive = set(w for w in words if w in self.positive_words)
        matched_negative = set(w for w in words if w in self.negative_words)

        # Raw counts
        pos_count = sum(1 for w in words if w in self.positive_words)
        neg_count = sum(1 for w in words if w in self.negative_words)

        # --- Word Sense Disambiguation via ContextualWordAnalyzer ---
        pos_adj, neg_adj = self.context_analyzer.score_text(
            text, matched_positive, matched_negative
        )
        pos_count = max(0, pos_count + pos_adj)
        neg_count = max(0, neg_count + neg_adj)
        # -----------------------------------------------------------

        total_words = len(words)
        if total_words == 0:
            return 0.0

        # Calculate base score
        pos_ratio = pos_count / total_words
        neg_ratio = neg_count / total_words

        base_score = (pos_ratio - neg_ratio) * 10  # Scale up

        # Add pattern detection (includes bigrams)
        pattern_adjustment = self._detect_patterns(text)

        # Detect magnitude — but only amplify if there is a meaningful direction
        # Prevents weak positives being pushed into Bullish by large amounts alone
        raw_pre_magnitude = base_score + pattern_adjustment
        if abs(raw_pre_magnitude) >= 0.15:
            magnitude_multiplier = self._detect_magnitude(text)
        else:
            magnitude_multiplier = 1.0  # Ambiguous articles: no amplification

        # Weight by event type
        event_weight = 1.0
        if event_type == "earnings":
            event_weight = 1.2
        elif event_type == "guidance":
            event_weight = 1.1
        elif event_type == "litigation":
            event_weight = 0.9
        elif event_type == "dividend":
            event_weight = 1.15
        elif event_type == "merger" or event_type == "acquisition":
            event_weight = 1.2

        # Apply weights and patterns
        weighted_score = raw_pre_magnitude * event_weight * magnitude_multiplier

        # Normalize to [-1, 1]
        s3_score = max(-1.0, min(1.0, weighted_score))

        return s3_score
    
    def get_stats(self) -> dict:
        """Return lexicon statistics"""
        return {
            "positive_words": len(self.positive_words),
            "negative_words": len(self.negative_words),
            "critical_patterns": len(self.critical_patterns),
            "bullish_patterns": len(self.bullish_patterns),
            "positive_bigrams": len(self.positive_bigrams),
            "negative_bigrams": len(self.negative_bigrams),
        }

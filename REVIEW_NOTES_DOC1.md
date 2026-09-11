# Document 1 Review Notes — Frozen

## Confirmed Fixes (Officially Verified, apply in final edit pass)

### 1. Regression Trend (Section 3.2)
- **Change**: "2–3 point structure" → **"exactly 2 points"**
- **Source**: TradingView official doc (Point 1 Bar, Point 2 Bar in Coordinates tab)

### 2. Ghost Feed (Section 6)
- **Rewrite**: Remove "overlays a semi-transparent duplicate of the actual price series shifted in time"
- **Replace with**: Ghost Feed generates synthetic projected candles from a starting point, based on "Avg HL in minticks" and "Variance" settings. It is not a copy of real data but a projection tool.
- **Source**: TradingView official Ghost Feed doc

### 3. Forecast (Section 6)
- **Rewrite**: Remove "click-drag a freehand projected price path into the future (literally sketching what you think price will do) — it's a 'brush' that specifically lives past the current last candle"
- **Replace with**: Position Forecast is a 2-point line tool (entry/source and exit/target) that auto-evaluates as "success" or "failure" as time progresses.
- **Source**: TradingView official Position Forecast doc

## Optional Clarifications

### Fib Wedge (Section 4)
- Note: 2-point arc-based Fibonacci tool — "a set of arcs spreading out of the point of a trend's beginning"

### Pitchfan (Section 4)
- Note: 3-point ray-based Fibonacci tool — "a set of rays spreading out...inclined with coefficients formed by a Fibonacci number sequence"

## Manual Verification Required (do not modify)

| Item | Reason |
|------|--------|
| Disjoint Channel | No official help page found |
| Flat Top/Bottom | No official help page found |
| Modified Schiff Pitchfork | No dedicated help page |
| Inside Pitchfork median rule | Official doc text ambiguous |  
| Bars Pattern anchor count | Not explicitly documented |
| Measure tool dismissal | Not explicitly documented |
| Fib Spiral behavior | No dedicated help page |

## Previously Applied Fixes (Category A from first review pass)
- Ray drag contradiction (Sections 2.2 vs 12) — Fixed
- Extended Line drag description (Section 12) — Fixed  
- Coordinates panel format (Section 0) — Fixed
- Fib Arcs & Circles rendering type (Section 4) — Fixed

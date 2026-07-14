from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List
from fuzzywuzzy import fuzz

from app.database import get_db
from app.models import Ticker
from app.schemas import TickerSearchResponse

router = APIRouter()


@router.get("/search", response_model=List[TickerSearchResponse])
def search_ticker(
    q: str = Query(..., min_length=1, description="Search query (ticker symbol or company name)"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """
    Search for tickers by symbol or company name with fuzzy matching
    """
    query_upper = q.upper()
    
    # Exact match first (symbol or ISIN)
    exact_matches = db.query(Ticker).filter(
        or_(
            Ticker.symbol == query_upper,
            Ticker.isin == query_upper
        )
    ).all()
    
    if exact_matches:
        return [_ticker_to_response(t) for t in exact_matches[:limit]]
    
    # Fuzzy match on company name
    all_tickers = db.query(Ticker).all()
    
    # Calculate fuzzy match scores
    matches = []
    for ticker in all_tickers:
        # Symbol similarity
        symbol_score = fuzz.ratio(query_upper, ticker.symbol)
        
        # Name similarity
        name_score = fuzz.partial_ratio(q.lower(), ticker.name.lower())
        
        # Best score
        best_score = max(symbol_score, name_score)
        
        if best_score >= 60:  # 60% similarity threshold
            matches.append((ticker, best_score))
    
    # Sort by score desc
    matches.sort(key=lambda x: x[1], reverse=True)
    
    # Return top matches
    return [_ticker_to_response(t) for t, score in matches[:limit]]


def _ticker_to_response(ticker: Ticker) -> TickerSearchResponse:
    return TickerSearchResponse(
        symbol=ticker.symbol,
        name=ticker.name,
        isin=ticker.isin,
        sector=ticker.sector,
        market_cap=ticker.market_cap,
        exchange=ticker.exchange
    )

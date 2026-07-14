"""
API endpoint to serve the full stocks list for frontend autocomplete
"""

from fastapi import APIRouter
from typing import List, Dict
import csv
from pathlib import Path

router = APIRouter()

# Cache the stocks list in memory
_stocks_cache = None

def load_stocks_list() -> List[Dict[str, str]]:
    """Load stocks from CSV file"""
    global _stocks_cache
    
    if _stocks_cache is not None:
        return _stocks_cache
    
    csv_path = Path(__file__).parent.parent.parent.parent / "stocks_list.csv"
    
    stocks = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row.get('ticker', '').strip().upper()
                name = row.get('name', '').strip()
                
                if ticker and name:
                    stocks.append({
                        'ticker': ticker,
                        'name': name
                    })
        
        _stocks_cache = stocks
        print(f"[OK] Loaded {len(stocks)} stocks for API")
        
    except Exception as e:
        print(f"[ERROR] Failed to load stocks list: {e}")
        # Return some fallback stocks
        stocks = [
            {'ticker': 'RELIANCE', 'name': 'Reliance Industries'},
            {'ticker': 'TCS', 'name': 'Tata Consultancy Services'},
            {'ticker': 'INFY', 'name': 'Infosys'},
        ]
    
    return stocks


@router.get("/stocks/list")
async def get_stocks_list(
    limit: int = 5571,
    search: str = None
):
    """
    Get list of all available stocks
    
    Args:
        limit: Maximum number of stocks to return
        search: Optional search query to filter stocks
    
    Returns:
        List of stock tickers and names
    """
    stocks = load_stocks_list()
    
    # Filter by search query if provided
    if search:
        search_lower = search.lower()
        stocks = [
            s for s in stocks
            if search_lower in s['ticker'].lower() or search_lower in s['name'].lower()
        ]
    
    # Limit results
    stocks = stocks[:limit]
    
    return {
        'total': len(stocks),
        'stocks': stocks
    }

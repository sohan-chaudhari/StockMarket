"""
StockMarket Backend Startup Script

Usage: python run.py
"""

import uvicorn

if __name__ == "__main__":
    print("Starting StockMarket Backend Server...")
    print("=" * 50)
    print("Server will be available at: http://localhost:8000")
    print("API Documentation: http://localhost:8000/docs")
    print("=" * 50)
    
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )

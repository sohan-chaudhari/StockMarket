"""
Minimal FastAPI app for ScanX.trade news scraper only
No database dependencies required
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
import sys

# Configure logging
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    level="INFO"
)

# Initialize FastAPI app
app = FastAPI(
    title="ScanX.trade News Scraper API",
    description="Fetch news exclusively from scanx.trade via Google News",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and include ONLY the scanx_news router
from app.api import scanx_news

app.include_router(scanx_news.router, prefix="/api/v1", tags=["ScanX News"])


@app.get("/")
async def root():
    return {
        "message": "ScanX.trade News Scraper API",
        "endpoints": {
            "all_news": "/api/v1/scanx/news/all",
            "ticker_news": "/api/v1/scanx/news/{ticker}",
            "company_info": "/api/v1/scanx/company/{ticker}",
            "docs": "/docs"
        }
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "scanx-scraper"}


@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Starting ScanX.trade News Scraper API...")
    logger.info("📊 Loaded stock mappings from CSV")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("👋 Shutting down ScanX.trade News Scraper API...")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

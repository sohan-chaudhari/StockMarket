from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from loguru import logger
import sys
import asyncio
from datetime import datetime

from app.config import settings
from app.database import engine, Base, get_engine
from app.api import health, search, news, ingest, scanx_news, stocks

# Configure logging
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level=settings.LOG_LEVEL
)

# Initialize FastAPI app
app = FastAPI(
    title="News Sentiment Analysis API",
    description="Production-ready sentiment analysis for Indian stocks",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Create database tables (optional — app works without DB for live scraping)
try:
    eng = get_engine()
    if eng:
        Base.metadata.create_all(bind=eng)
        logger.info("Database tables created successfully")
    else:
        logger.warning("Database not available (running in scrape-only mode)")
except Exception as e:
    logger.warning(f"Database not available (running in scrape-only mode): {e}")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Error handling middleware
@app.middleware("http")
async def error_handling_middleware(request: Request, call_next):
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"}
        )


# Request logging middleware
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    start_time = datetime.now()
    logger.info(f"Request: {request.method} {request.url.path}")
    
    response = await call_next(request)
    
    duration = (datetime.now() - start_time).total_seconds()
    logger.info(f"Response: {response.status_code} (took {duration:.3f}s)")
    
    return response


# Root redirect to docs
@app.get("/")
async def root():
    return RedirectResponse(url="/docs")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon")

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(search.router, prefix="/api", tags=["Search"])
app.include_router(news.router, prefix="/api", tags=["News"])
app.include_router(ingest.router, prefix="/api", tags=["Ingest"])
app.include_router(scanx_news.router, prefix="/api", tags=["ScanX News"])
app.include_router(stocks.router, prefix="/api", tags=["Stocks"])


# ==================== FII/DII DATA ====================
import httpx
import re as _re
import json as _json

_FII_DII_CACHE = {"data": None, "ts": None}
_FII_DII_TTL = 300  # 5 minutes

@app.get("/api/fii-dii")
async def get_fii_dii():
    """Fetch FII/DII data: tries NSE first, then Moneycontrol scrape."""
    from datetime import timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    if _FII_DII_CACHE["data"] and _FII_DII_CACHE["ts"]:
        age = (now - _FII_DII_CACHE["ts"]).total_seconds()
        if age < _FII_DII_TTL:
            return _FII_DII_CACHE["data"]

    today_str = now.strftime("%d-%m-%Y")

    # Attempt 1: NSE India
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            client.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            await client.get("https://www.nseindia.com")
            await asyncio.sleep(1)
            resp = await client.get(
                f"https://www.nseindia.com/api/fiidii?from={today_str}&to={today_str}",
                headers={"Accept": "application/json, text/javascript, */*; q=0.01", "Referer": "https://www.nseindia.com/"}
            )
            if resp.status_code == 200:
                data = resp.json()
                raw_list = data if isinstance(data, list) else data.get("data", [])
                entries = []
                for item in raw_list:
                    def _parse(val):
                        try: return float(str(val).replace(',', ''))
                        except: return 0.0
                    fii_cash = _parse(item.get("FII Cash", item.get("fiiCash", item.get("fii_cash", 0))))
                    dii_cash = _parse(item.get("DII Cash", item.get("diiCash", item.get("dii_cash", 0))))
                    fii_fo   = _parse(item.get("FII FO",   item.get("fiiFo",   item.get("fii_fo",   0))))
                    net      = _parse(item.get("Net Total", item.get("netTotal", item.get("net_total", fii_cash + dii_cash))))
                    entries.append({
                        "date": item.get("date", today_str),
                        "fii_cash_cr": fii_cash,
                        "dii_cash_cr": dii_cash,
                        "fii_fo_cr": fii_fo,
                        "net_total_cr": net,
                    })
                if entries:
                    result = {"entries": entries, "source": "NSE India", "last_updated": now.isoformat()}
                    _FII_DII_CACHE["data"], _FII_DII_CACHE["ts"] = result, now
                    return result
    except Exception as e:
        logger.warning(f"[FII/DII] NSE attempt failed: {e}")

    # Attempt 2: Moneycontrol
    try:
        mc_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.moneycontrol.com/",
        }
        async with httpx.AsyncClient(headers=mc_headers, timeout=10) as client:
            mc_resp = await client.get("https://www.moneycontrol.com/markets/fii-dii-data/")
            if mc_resp.status_code == 200:
                m = _re.search(r'<script id="__NEXT_DATA__"[^>]*>({.*?})</script>', mc_resp.text, _re.DOTALL)
                if m:
                    payload = _json.loads(m.group(1))
                    rows = payload.get("props", {}).get("pageProps", {}).get("FiiDiiData", {}).get("fiiDiiData", [])
                    entries = []
                    for row in rows[:5]:
                        def _mc_parse(s):
                            try: return float(str(s).replace(',', '').replace('\u20b9', '').strip())
                            except: return 0.0
                        fii_fo = _mc_parse(row.get("fiiIdxFut","0")) + _mc_parse(row.get("fiiIdxOpt","0")) + _mc_parse(row.get("fiiStkFut","0")) + _mc_parse(row.get("fiiStkOpt","0"))
                        fii_cash = _mc_parse(row.get("fiiCM","0"))
                        dii_cash = _mc_parse(row.get("diiCM","0"))
                        entries.append({
                            "date": row.get("date",""),
                            "fii_cash_cr": fii_cash,
                            "dii_cash_cr": dii_cash,
                            "fii_fo_cr": fii_fo,
                            "net_total_cr": dii_cash + fii_cash,
                        })
                    if entries:
                        result = {"entries": entries, "source": "Moneycontrol", "last_updated": now.isoformat()}
                        _FII_DII_CACHE["data"], _FII_DII_CACHE["ts"] = result, now
                        return result
    except Exception as e:
        logger.warning(f"[FII/DII] Moneycontrol attempt failed: {e}")

    result = {"entries": [], "source": "Unavailable", "last_updated": now.isoformat(), "error": "FII/DII data temporarily unavailable."}
    _FII_DII_CACHE["data"], _FII_DII_CACHE["ts"] = result, now
    return result


@app.on_event("startup")
async def startup_event():
    logger.info("Starting News Sentiment Analysis API...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"LLM Calibration: {'Enabled' if settings.ENABLE_LLM_CALIBRATION else 'Disabled'}")
    logger.info(f"Recency Scoring: {'Enabled' if settings.ENABLE_RECENCY_SCORING else 'Disabled'}")
    
    # Warm the news cache in background so first user request is fast
    async def warm_cache():
        try:
            from app.api.scanx_news import _refresh_news_cache
            logger.info("Warming news cache in background...")
            await _refresh_news_cache(limit=20)
            logger.info("News cache warmed successfully")
        except Exception as e:
            logger.warning(f"News cache warmup failed (will scrape on first request): {e}")
    asyncio.create_task(warm_cache())


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down News Sentiment Analysis API...")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.API_PORT)

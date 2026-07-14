# News Sentiment Analysis Platform - Setup Guide

## Quick Start

### Prerequisites
- Docker & Docker Compose installed
- Python 3.11+
- Node.js 18+
- Google Gemini API key (free from https://ai.google.dev/)

### 1. Environment Setup

```bash
# Clone/navigate to project
cd News_Sentiment

# Create .env file
cp .env.example .env

# Edit .env and add your GEMINI_API_KEY
# Get free key from: https://ai.google.dev/
```

### 2. Start Infrastructure (Docker)

```bash
# Start databases
docker-compose up -d postgres mongo redis

# OR start everything (backend + frontend + databases)
docker-compose up -d
```

### 3. Initialize Backend

```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Create database tables
python -c "from app.database import Base, engine; Base.metadata.create_all(bind=engine)"

# Seed MVP tickers
python scripts/seed_tickers.py
```

### 4. Download ML Models

```bash
cd ../ml

# Install ML dependencies
pip install -r requirements.txt

# Download spaCy model
python -m spacy download en_core_web_sm

# FinBERT will auto-download on first run
```

### 5. Start Backend Services

**Terminal 1: FastAPI**
```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

**Terminal 2: Celery Worker**
```bash
cd backend
celery -A tasks.celery_app worker -l info
```

**Terminal 3: Celery Beat (Scheduler)**
```bash
cd backend
celery -A tasks.celery_app beat -l info
```

**Terminal 4 (Optional): Flower (Celery Monitor)**
```bash
cd backend
celery -A tasks.celery_app flower --port=5555
```

### 6. Start Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

### 7. Access the App

- **Frontend**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs
- **Flower (Celery)**: http://localhost:5555

---

## Testing the System

### 1. Check Health
```bash
curl http://localhost:8000/api/health
```

### 2. Search for Ticker
```bash
curl "http://localhost:8000/api/search?q=RELIANCE"
```

### 3. Trigger Manual Scrape
```python
# In Python REPL
from scrapers.news_scraper import HybridNewsScraper
scraper = HybridNewsScraper()
articles = scraper.fetch_all_news("RELIANCE", "Reliance Industries")
print(f"Found {len(articles)} articles")
```

### 4. Manual Article Ingest
```bash
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "RELIANCE",
    "title": "Reliance Industries reports record profit",
    "url": "https://example.com/article",
    "source": "Test Source",
    "published_at": "2025-12-28T10:00:00Z",
    "text": "Reliance Industries Limited has reported a record quarterly profit of Rs 15,000 crore, beating analyst estimates. The company showed strong performance in its petrochemical and retail segments.",
    "excerpt": "Reliance reports record profit"
  }'
```

### 5. Get News for Ticker
```bash
curl "http://localhost:8000/api/news/ticker/RELIANCE?window=1h&limit=10"
```

---

## Architecture Overview

```
┌─────────────┐
│   Frontend  │  (Next.js + Tailwind)
│ localhost:  │
│    3000     │
└──────┬──────┘
       │
       ↓ HTTP
┌─────────────┐
│   Backend   │  (FastAPI)
│ localhost:  │
│    8000     │
└──────┬──────┘
       │
       ├──→ PostgreSQL (Sentiment results)
       ├──→ MongoDB (Raw articles)
       └──→ Redis (Caching + Celery)
       
┌─────────────┐       ┌─────────────┐
│   Celery    │←Redis→│   Celery    │
│   Worker    │       │    Beat     │
└──────┬──────┘       └─────────────┘
       │
       ↓
┌─────────────┐
│     ML      │  (FinBERT + Lexicon)
│  Pipeline   │
└─────────────┘
```

---

## Common Issues & Solutions

### Issue: "ModuleNotFoundError"
**Solution**: Make sure you're in the correct directory and have installed dependencies
```bash
cd backend && pip install -r requirements.txt
cd ml && pip install -r requirements.txt
```

### Issue: "Connection refused" to databases
**Solution**: Start Docker services
```bash
docker-compose up -d postgres mongo redis
```

### Issue: FinBERT model download fails
**Solution**: Download manually
```python
from transformers import AutoModel
AutoModel.from_pretrained("ProsusAI/finbert")
```

### Issue: Celery tasks not running
**Solution**: Check Redis is running and Celery worker is started
```bash
redis-cli ping  # Should return PONG
celery -A tasks.celery_app inspect active
```

---

## Development Workflow

1. **Add New Ticker**: Edit `backend/scripts/seed_tickers.py` and run it
2. **Test Sentiment Model**: Run `ml/test_sentiment.py` (create this file)
3. **View Logs**: Check terminal outputs or `logs/` directory
4. **Monitor Tasks**: Open http://localhost:5555 (Flower)
5. **API Testing**: Use http://localhost:8000/docs (Swagger UI)

---

## Production Deployment

See [free_alternatives.md](./docs/free_alternatives.md) for deploying to:
- Vercel (frontend) - FREE
- Railway (backend) - $5/month
- MongoDB Atlas - FREE
- Supabase - FREE

Total cost: **$0-5/month**

---

## MVP Tickers

The system supports 5 large-cap Indian stocks:
1. **RELIANCE** - Reliance Industries
2. **TCS** - Tata Consultancy Services
3. **INFY** - Infosys
4. **HDFCBANK** - HDFC Bank
5. **ICICIBANK** - ICICI Bank

News is scraped every 5 minutes for these tickers.

---

## Tech Stack

- **Backend**: FastAPI + PostgreSQL + MongoDB + Redis
- **ML**: FinBERT + Gemini API + Loughran-McDonald Lexicon
- **Frontend**: Next.js 14 + Tailwind CSS
- **Queue**: Celery + Redis
- **Scraper**: Google News RSS + scanx.trade

---

## Next Steps

1. Let automated scraping run for a few hours to collect data
2. Check http://localhost:3000 to see news appearing
3. Monitor sentiment scores in API responses
4. Adjust ensemble weights in `ml/sentiment_analyzer.py` if needed
5. Add more tickers by editing seed script

---

## Support

- Documentation: See `docs/` folder
- API Docs: http://localhost:8000/docs
- Issues: Check logs in terminals or Flower dashboard

**Happy Trading! 📈**

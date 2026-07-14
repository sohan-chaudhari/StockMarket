# News Sentiment Analysis Platform

## 🚀 Complete MVP Built!

Full-stack news sentiment analysis platform for Indian stocks with production-ready ML pipeline.

---

## 📁 Project Structure

```
News_Sentiment/
├── backend/           # FastAPI backend
│   ├── app/
│   │   ├── api/      # API routes (health, search, news, ingest)
│   │   ├── models.py # Database models
│   │   └── main.py   # FastAPI app
│   └── scripts/      # Database initialization
│
├── ml/               # Machine Learning Pipeline
│   ├── finbert_model.py      # FinBERT sentiment (S1)
│   ├── lexicon_model.py      # Loughran-McDonald (S3)
│   ├── llm_calibrator.py     # Gemini API calibration
│   ├── confidence_estimator.py
│   ├── sentiment_analyzer.py # Complete ensemble
│   └── preprocessing.py
│
├── scrapers/         # News ingestion
│   └── news_scraper.py # Google News + scanx.trade
│
├── tasks/            # Celery background jobs
│   ├── sentiment_task.py  # Sentiment analysis
│   ├── scraper_task.py    # Scheduled scraping
│   └── recency_task.py    # Recency score calculation
│
├── frontend/         # Next.js UI
│   ├── app/
│   │   ├── page.tsx        # Landing page
│   │   └── stock/[ticker]/ # Stock page
│   └── components/
│       ├── NewsCard.tsx
│       ├── SentimentBadge.tsx
│       ├── SearchBar.tsx
│       ├── NewsFeed.tsx
│       └── SentimentChart.tsx
│
└── docker-compose.yml # All services
```

---

## ⚙️ Quick Start

### 1. Environment Setup

```bash
# Create .env file
cp .env.example .env

# Add your Gemini API key (FREE from https://ai.google.dev/)
GEMINI_API_KEY=your_key_here
```

### 2. Start Everything with Docker

```bash
# Start all services (databases + backend + frontend)
docker-compose up -d

# View logs
docker-compose logs -f backend
```

**OR** Run Locally:

```bash
# Terminal 1: Databases only
docker-compose up -d postgres mongo redis

# Terminal 2: Backend
cd backend
pip install -r requirements.txt
python scripts/seed_tickers.py
uvicorn app.main:app --reload

# Terminal 3: Celery Worker
celery -A tasks.celery_app worker -l info

# Terminal 4: Celery Beat (Scraper)
celery -A tasks.celery_app beat -l info

# Terminal 5: Frontend
cd frontend
npm install
npm run dev
```

### 3. Access the Platform

- **Frontend**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs
- **Flower (Celery)**: http://localhost:5555

---

## 🎯 Features

### ✅ Backend API
- `/api/health` - Service health check
- `/api/search?q={ticker}` - Fuzzy ticker search
- `/api/news/ticker/{ticker}` - Get news with sentiment
- `/api/ingest` - Ingest new articles

### ✅ ML Pipeline
- **FinBERT** (Base sentiment signal)
- **Loughran-McDonald Lexicon** (Financial keywords)
- **Google Gemini** (LLM calibration for edge cases)
- **Confidence Scoring** (Multi-factor estimation)
- **Recency Validation** (Predicted vs actual price movement)

### ✅ News Scraping
- **Google News RSS** (Broad coverage)
- **scanx.trade** (Ticker-specific)
- **Deduplication** (URL + title similarity)
- **Auto-scheduling** (Every 5 min for top tickers)

### ✅ Frontend
- **Dark Theme** (Market colors: green/red/gray)
- **Landing Page** (Live news feed)
- **Stock Page** (Sentiment distribution + news)
- **SearchBar** (Autocomplete with fuzzy matching)
- **NewsCard** (Sentiment badge + recency score)
- **Micro-interactions** (Hover effects, animations)

---

## 📊 MVP Tickers

1. **RELIANCE** - Reliance Industries
2. **TCS** - Tata Consultancy Services
3. **INFY** - Infosys
4. **HDFCBANK** - HDFC Bank
5. **ICICIBANK** - ICICI Bank

---

## 🧪 Testing

### Manual Test Flow

1. **Health Check**:
   ```bash
   curl http://localhost:8000/api/health
   ```

2. **Search Ticker**:
   ```bash
   curl "http://localhost:8000/api/search?q=RELIANCE"
   ```

3. **Ingest Test Article**:
   ```bash
   curl -X POST http://localhost:8000/api/ingest \
     -H "Content-Type: application/json" \
     -d '{
       "ticker": "RELIANCE",
       "title": "Reliance reports record profit",
       "url": "https://example.com/test",
       "source": "Test",
       "published_at": "2025-12-28T10:00:00Z",
       "text": "Reliance Industries posted strong quarterly results with earnings beating estimates.",
       "excerpt": "Strong quarterly results"
     }'
   ```

4. **Get News**:
   ```bash
   curl "http://localhost:8000/api/news/ticker/RELIANCE?window=1h"
   ```

5. **Check Frontend**: Visit http://localhost:3000

---

## 💰 Cost (Free Tier)

- **Development**: $0/month (local)
- **Production**: $0-15/month
  - Vercel (frontend): FREE
  - Railway (backend): $5/month
  - MongoDB Atlas: FREE (512MB)
  - Supabase PostgreSQL: FREE (500MB)
  - Upstash Redis: FREE (10k commands/day)
  - Gemini API: FREE (1500 requests/day)

See `free_alternatives.md` for deployment guide.

---

## 🛠️ Tech Stack

- **Backend**: FastAPI + PostgreSQL + MongoDB + Redis
- **ML**: FinBERT + Gemini + Loughran-McDonald
- **Frontend**: Next.js 14 + Tailwind CSS + Recharts
- **Queue**: Celery + Redis
- **Deployment**: Docker Compose

---

## 📖 Documentation

- **Setup Guide**: See `SETUP.md`
- **Implementation Plan**: See `implementation_plan.md`
- **Free Deployment**: See `free_alternatives.md`
- **Tech Stack**: See `tech_stack.md`

---

## 🎨 UI Preview

- **Dark Theme** with market colors (Green/Red/Gray)
- **Live News** with pulse animation (<5min old)
- **Sentiment Badges** (🔺 Bullish, 🔻 Bearish, ➖ Neutral)
- **Recency Scores** (How market moved vs prediction)
- **Hover Effects** with elevation & glow

---

## 🚧 Roadmap

- [x] Phase 1: MVP (Backend + ML + Frontend)
- [ ] Phase 2: More tickers (NIFTY50)
- [ ] Phase 3: Custom market-reaction model (S2)
- [ ] Phase 4: Active learning loop
- [ ] Phase 5: Production polish

---

## 📝 License

MIT

---

**Happy Trading! 📈**

For issues or questions, see documentation in `docs/` or open an issue.

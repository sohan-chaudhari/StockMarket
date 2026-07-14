# News Scraping Strategy - ScanX.trade Only

## ✅ What's Been Fixed

The news scraper now **only fetches articles published on scanx.trade** that are indexed by Google News.

---

## 🔍 How It Works

### **Google News Query Format**

**Before (incorrect - fetching random news):**
```
Query: "Reliance Industries stock india OR RELIANCE.NS OR NSE:RELIANCE"
Result: Any news from any source about Reliance
```

**After (correct - only scanx.trade):**
```
Query: site:scanx.trade "Reliance Industries"
Result: ONLY news published on scanx.trade about Reliance Industries
```

---

## 📋 Scraping Process

1. **ScanX.trade Direct Scrape** (Primary)
   - Attempts to scrape directly from scanx.trade website
   - **Status**: Usually finds 0 articles (HTML structure varies)

2. **Google News (scanx.trade filter)** (Secondary)
   - Searches Google News RSS with filter: `site:scanx.trade "Company Name"`
   - Returns **ONLY** articles that:
     - ✅ Are published on scanx.trade
     - ✅ Are indexed by Google News
     - ✅ Match the company name

3. **Deduplication**
   - Removes duplicate articles by URL and title similarity
   - Keeps only unique articles

---

## 🎯 Expected Results

When you restart the Celery worker, you should see logs like:

```
Fetching from scanx.trade for RELIANCE...
  Found 0 articles from scanx.trade
Fetching from Google News for scanx.trade articles about Reliance Industries...
  Found 5 scanx.trade articles from Google News
After deduplication: 5 unique articles
✓ Ingested: Article title from scanx.trade...
✓ Ingested: Article title from scanx.trade...
Successfully ingested 5/5 articles for RELIANCE
```

---

## 🔄 Restart Instructions

**Stop Celery Worker & Beat:**
- Terminal 3 (Worker): Press `Ctrl+C`
- Terminal 4 (Beat): Press `Ctrl+C`

**Restart with updated code:**

**Terminal 3:**
```powershell
cd c:\Users\rahul\Desktop\News_Sentiment\backend
.\start_celery_worker.bat
```

**Terminal 4:**
```powershell
cd c:\Users\rahul\Desktop\News_Sentiment\backend
.\start_celery_beat.bat
```

---

## 📝 Important Notes

1. **Fewer Articles Expected**: Since we're only scraping scanx.trade (not all sources), you'll see fewer articles but they'll all be from the correct source.

2. **Google News Indexing**: Articles will only appear if:
   - scanx.trade has published them
   - Google News has indexed them
   - They match the company name search

3. **No Source Display Needed**: Since ALL news is from scanx.trade, you don't need to show the source in the frontend.

---

## ✅ Verification

After restarting, check the logs for:
- ✅ `site:scanx.trade` in search queries
- ✅ "scanx.trade articles" in log messages
- ✅ Articles being ingested successfully
- ✅ Frontend displaying news (all from scanx.trade)

The fix ensures **100% of articles come from scanx.trade only**! 🎉

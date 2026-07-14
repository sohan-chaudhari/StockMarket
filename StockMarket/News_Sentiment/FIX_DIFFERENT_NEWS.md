# Fix: Different News on Different Platforms

## 🔍 Problem Identified

Your screenshots show:
1. **Terminal (Celery)** ✅ - Fetching scanx.trade articles (NEW code working!)
2. **Google News** ✅ - Shows scanx.trade articles correctly  
3. **Frontend** ❌ - Shows "bollywoodhelpline.com" articles (OLD data!)

## 🎯 Root Cause

The frontend is displaying **old articles from the database** that were scraped **before** the scanx.trade-only fix was applied.

Your Celery worker logs show the new code IS working:
```
✓ Ingested: Infosys to pay up to ₹21 lakh... (from scanx.trade)
✓ Ingested: Infosys Clarifies ADR Price... (from scanx.trade)
```

But the frontend shows old "bollywoodhelpline.com" articles because they're still in MongoDB.

---

## ✅ Solution: Clear Old Articles

### **Option 1: Clear ALL Articles (Recommended)**

This removes everything and lets the new scraper populate only scanx.trade articles:

```powershell
docker exec stock-news-mongo mongosh news_articles --eval "db.news_articles.deleteMany({})"
```

### **Option 2: Keep Only ScanX Articles**

This removes only non-scanx.trade articles:

```powershell
docker exec stock-news-mongo mongosh news_articles --eval "db.news_articles.deleteMany({source: {\$ne: 'scanx.trade'}})"
```

### **Option 3: Manual Database Cleanup**

```powershell
# Connect to MongoDB
docker exec stock-news-mongo mongosh news_articles

# Inside MongoDB shell:
db.news_articles.deleteMany({source: {$ne: "scanx.trade"}})
db.news_articles.countDocuments()  # Check remaining count
exit
```

---

## 🔄 After Clearing Database

**1. Wait 5-10 minutes**  
   - Celery Beat will trigger new scraping
   - Worker will fetch ONLY scanx.trade articles
   - Database will populate with clean data

**2. Refresh Frontend**
   ```
   Open: http://localhost:8080
   Press: Ctrl + Shift + R (hard refresh)
   ```

**3. Verify All Sources Match**
   - ✅ Terminal: Ingesting scanx.trade articles
   - ✅ Google News: Showing scanx.trade articles
   - ✅ Frontend: Displaying scanx.trade articles

---

## 📊 Verification Commands

**Check article count:**
```powershell
docker exec stock-news-mongo mongosh news_articles --eval "db.news_articles.countDocuments({})"
```

**Check article sources:**
```powershell
docker exec stock-news-mongo mongosh news_articles --eval "db.news_articles.aggregate([{$group: {_id: '$source', count: {$sum: 1}}}])"
```

**View sample articles:**
```powershell
docker exec stock-news-mongo mongosh news_articles --eval "db.news_articles.find({}, {title: 1, source: 1}).limit(5).toArray()"
```

---

## 🎯 Expected Result

After clearing and waiting for new scrapes, ALL THREE platforms will show the SAME scanx.trade articles:

1. **Terminal** → `✓ Ingested: [scanx.trade article]`
2. **Google News** → scanx.trade articles for Infosys
3. **Frontend** → Same scanx.trade articles with sentiment

---

## 💡 Why This Happened

Timeline:
1. ❌ **Before fix**: Scraper fetched random Google News articles
2. 📦 **Database**: Stored articles from "bollywoodhelpline.com", etc.
3. ✅ **After fix**: Scraper now only fetches scanx.trade articles
4. 🐛 **Problem**: Frontend still shows OLD articles from step 2
5. ✅ **Solution**: Clear old articles, wait for new ones

---

## 🚀 Quick Fix Steps

```powershell
# 1. Clear old articles
docker exec stock-news-mongo mongosh news_articles --eval "db.news_articles.deleteMany({})"

# 2. Wait 5-10 minutes for new scraping

# 3. Refresh frontend (Ctrl + Shift + R)

# 4. Verify all sources are scanx.trade
docker exec stock-news-mongo mongosh news_articles --eval "db.news_articles.find({}, {source: 1}).limit(10).toArray()"
```

All platforms will now show consistent scanx.trade-only news! 🎉

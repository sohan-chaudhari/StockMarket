@echo off
echo Clearing old non-scanx.trade articles from database...
echo.

docker exec stock-news-mongo mongosh news_articles --eval "db.news_articles.deleteMany({})"

echo.
echo Done! All articles cleared.
echo Wait 5-10 minutes for new scanx.trade articles to be scraped.
echo Then refresh your frontend (Ctrl+Shift+R)
pause

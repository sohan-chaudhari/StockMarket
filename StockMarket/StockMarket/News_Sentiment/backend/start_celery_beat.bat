@echo off
REM Start Celery Beat for scheduled news scraping
echo Starting Celery Beat (News Scraper Scheduler)...

REM Go to project root
cd ..

REM Set PYTHONPATH for imports
set PYTHONPATH=%CD%;%CD%\backend

REM Start Celery Beat scheduler
python -m celery -A tasks.celery_app beat -l info

pause

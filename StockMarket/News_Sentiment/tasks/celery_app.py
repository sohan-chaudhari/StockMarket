import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from celery import Celery
from celery.schedules import crontab
from app.config import settings

# Initialize Celery
celery_app = Celery(
    "news_sentiment",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

# Celery configuration
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max per task
)

# Celery Beat schedule (cron jobs)
celery_app.conf.beat_schedule = {
    # Scrape top 5 tickers every 5 minutes
    'scrape-top-tickers': {
        'task': 'tasks.scraper_task.scrape_top_tickers',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
    # Calculate recency scores every hour
    'calculate-recency-scores': {
        'task': 'tasks.recency_task.calculate_all_recency_scores',
        'schedule': crontab(minute=0),  # Every hour
    },
}

# Import tasks
from tasks import sentiment_task, scraper_task, recency_task

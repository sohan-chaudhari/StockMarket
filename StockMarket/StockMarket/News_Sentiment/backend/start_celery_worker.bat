@echo off
REM Start Celery Worker for sentiment analysis
echo Starting Celery Worker...

REM Go to project root
cd ..

REM Set PYTHONPATH for imports
set PYTHONPATH=%CD%;%CD%\backend

REM Start Celery Worker with solo pool (required on Windows)
python -m celery -A tasks.celery_app worker -l info --pool=solo

pause

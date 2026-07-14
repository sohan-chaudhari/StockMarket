@echo off
REM Start FastAPI Backend
echo Starting FastAPI Backend on port 8000...
python -m uvicorn app.main:app --reload --port 8000

# Full-Stack Financial Charting Application

This Application rivals TradingView in performance using FastAPI (Backend) and React + Lightweight Charts (Frontend).

## Prerequisites
- Python 3.8+
- Node.js 16+
- PostgreSQL database with `infy_daily` table populated.

## Setup Instructions

### 1. Database Configuration
Ensure your PostgreSQL database is running and holds the data imported earlier.
By default, the backend connects to:
- **Host**: localhost
- **Port**: 5432
- **Database**: stock_data
- **User**: postgres
- **Password**: (Empty)

To change these, create a `.env` file in `backend/` or set environment variables:
`DB_USER=your_user DB_PASSWORD=your_password ...`

### 2. Backend Setup
Navigate to the `backend` directory (if separate) or root.
The backend code is located in `e:\StockMarket\backend`.

Run the server:
```bash
cd e:\StockMarket
uvicorn backend.main:app --reload --port 8000
```
Check API status at: [http://localhost:8000](http://localhost:8000)

### 3. Frontend Setup
Navigate to the `frontend` directory.
The frontend code is located in `e:\StockMarket\frontend`.

Install dependencies (if not already done):
```bash
cd e:\StockMarket\frontend
npm install
```

Run the development server:
```bash
npm run dev
```
Open the application at: [http://localhost:5173](http://localhost:5173)

## Features
- **High Performance**: 60fps rendering with Canvas.
- **REST API**: Efficient data fetching with FastAPI.
- **Interactive Chart**: Zoom, Pan, Volume bars, Crosshair.
- **Time Ranges**: 1D, 1W, 1M, 3M, 6M, 1Y, ALL selectors.
- **Dark Mode**: Professional dark theme UI.

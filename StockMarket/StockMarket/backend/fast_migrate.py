import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import SessionLocal, engine
from sqlalchemy import text as sql_text
from resampler import CandleResampler
from datetime import datetime

BATCH = 50

def progress(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def main():
    db = SessionLocal()
    try:
        # 1. Ensure staging table
        progress("Creating staging table...")
        db.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS candles_migration (LIKE candles INCLUDING ALL)
        """))
        db.commit()
        progress("Staging table ready.")

        # 2. Copy existing 5m candles
        progress("Copying existing 5m candles from candles table...")
        result = db.execute(sql_text("""
            INSERT INTO candles_migration (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
            SELECT ticker, '5m', timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled
            FROM candles WHERE timeframe = '5m'
            ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING
        """))
        db.commit()
        progress(f"Copied {result.rowcount} 5m candles.")

        # 3. Get all tickers with 5m data
        rows = db.execute(sql_text("""
            SELECT DISTINCT ticker FROM candles_migration WHERE timeframe = '5m' ORDER BY ticker
        """)).fetchall()
        tickers = [r[0] for r in rows]
        progress(f"Found {len(tickers)} tickers with 5m data.")

        # 4. For each ticker, resample 5m -> higher TFs
        target_tfs = ["15m", "30m", "1h", "4h", "1D", "1W", "1M"]
        total_inserted = {tf: 0 for tf in target_tfs}
        ticker_count = 0

        for idx, ticker in enumerate(tickers):
            # Read 5m candles for this ticker
            candle_rows = db.execute(sql_text("""
                SELECT timestamp, open, high, low, close, volume
                FROM candles_migration
                WHERE ticker = :t AND timeframe = '5m'
                ORDER BY timestamp
            """), {"t": ticker}).fetchall()

            candles_5m = [
                {"timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]), "volume": int(r[5] or 0)}
                for r in candle_rows
            ]

            for tf in target_tfs:
                try:
                    resampled = CandleResampler.resample_5m_to(candles_5m, tf)
                except Exception as e:
                    progress(f"  Error resampling {ticker} -> {tf}: {e}")
                    continue

                if not resampled:
                    continue

                # Delete old data for clean slate
                db.execute(sql_text(
                    "DELETE FROM candles_migration WHERE ticker = :t AND timeframe = :tf"
                ), {"t": ticker, "tf": tf})

                # Batch insert
                for i in range(0, len(resampled), BATCH):
                    batch = resampled[i:i+BATCH]
                    values = []
                    params = {}
                    for j, c in enumerate(batch):
                        ts = c["timestamp"]
                        if isinstance(ts, datetime):
                            ts = ts.replace(tzinfo=None)
                        values.append(
                            f"(:ticker_{j}, :tf_{j}, :ts_{j}, :o_{j}, :h_{j}, :l_{j}, :c_{j}, :v_{j})"
                        )
                        params.update({
                            f"ticker_{j}": ticker,
                            f"tf_{j}": tf,
                            f"ts_{j}": ts,
                            f"o_{j}": float(c["open"]),
                            f"h_{j}": float(c["high"]),
                            f"l_{j}": float(c["low"]),
                            f"c_{j}": float(c["close"]),
                            f"v_{j}": int(c.get("volume", 0)),
                        })

                    db.execute(sql_text(f"""
                        INSERT INTO candles_migration (ticker, timeframe, timestamp, open, high, low, close, volume, is_completed, data_source, is_backfilled)
                        VALUES {', '.join(values)}
                        ON CONFLICT (ticker, timeframe, timestamp) DO NOTHING
                    """), params)
                    db.commit()

                total_inserted[tf] += len(resampled)

            ticker_count += 1
            if ticker_count % 20 == 0:
                summaries = " | ".join(f"{tf}={total_inserted[tf]:,}" for tf in target_tfs)
                progress(f"  [{ticker_count}/{len(tickers)}] {ticker} -- {summaries}")

        # 5. Summary
        progress(f"\n{'='*60}")
        progress(f"  DONE! Processed {ticker_count} tickers")
        for tf in target_tfs:
            progress(f"  {tf:>4s}: {total_inserted[tf]:>8,} candles")
        total = sum(total_inserted.values())
        progress(f"  TOTAL: {total:,} candles in candles_migration")
        progress(f"{'='*60}")

    finally:
        db.close()

if __name__ == "__main__":
    main()

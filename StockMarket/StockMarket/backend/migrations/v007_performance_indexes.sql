CREATE INDEX IF NOT EXISTS ix_candle_ticker_tf_ts ON candles (ticker, timeframe, timestamp DESC);
CREATE INDEX IF NOT EXISTS ix_currentdaycandle_ticker_date ON current_day_candle (ticker, trading_date DESC);
CREATE INDEX IF NOT EXISTS ix_metadata_is_premium ON stock_metadata (is_premium) WHERE is_premium = TRUE;
CREATE INDEX IF NOT EXISTS ix_stock_data_ticker_date_desc ON stock_data (ticker, date DESC);

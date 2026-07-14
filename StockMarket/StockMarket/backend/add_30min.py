with open('models.py', 'a') as f:
    f.write('''
class IntradayCandle30Min(Base):
    __tablename__ = "intraday_candles_30min"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uix_ticker_timestamp_30min'),
        Index('idx_30min_ticker_ts', 'ticker', 'timestamp'),
    )
''')
print('Added 30min model')

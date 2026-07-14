import re

with open('backend/models.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add IntradayCandle1H
new_model = """
class IntradayCandle1H(Base):
    __tablename__ = "intraday_candles_1h"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uix_ticker_timestamp_1h'),
        Index('idx_1h_ticker_ts', 'ticker', 'timestamp'),
    )
"""

if "class IntradayCandle1H" not in content:
    content = content.replace('class IntradayCandle30Min(Base):', new_model + '\nclass IntradayCandle30Min(Base):')

with open('backend/models.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Added IntradayCandle1H to models.py")

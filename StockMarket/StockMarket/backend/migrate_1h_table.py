from database import engine, Base
from models import IntradayCandle1H

print("Creating IntradayCandle1H table...")
IntradayCandle1H.__table__.create(engine, checkfirst=True)
print("Table created successfully!")

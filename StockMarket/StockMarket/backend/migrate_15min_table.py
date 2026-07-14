from backend.database import engine, Base
from backend.models import IntradayCandle15Min

print("Creating IntradayCandle15Min table...")
Base.metadata.create_all(bind=engine)
print("Table created successfully.")

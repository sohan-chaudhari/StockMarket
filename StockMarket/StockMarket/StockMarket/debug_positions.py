from backend import database, models
db = database.SessionLocal()
print("-" * 50)
print("LATEST 5 POSITIONS:")
positions = db.query(models.Position).order_by(models.Position.created_at.desc()).limit(5).all()
for p in positions:
    print(f"ID: {p.id} | User: {p.user_id} | Ticker: {p.ticker} | Type: {p.position_type} | TP: {p.take_profit} | SL: {p.stop_loss}")
print("-" * 50)

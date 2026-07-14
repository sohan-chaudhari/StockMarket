import requests
from bs4 import BeautifulSoup
from datetime import datetime
from backend.database import SessionLocal, engine
from backend import models

# Ensure table exists
models.Base.metadata.create_all(bind=engine)

URL = "https://zerodha.com/marketintel/holiday-calendar/"

def scrape_holidays():
    headers = {
        'User-Agent': 'Mozilla/5.0'
    }
    try:
        response = requests.get(URL, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        tables = soup.find_all('table')
        
        db = SessionLocal()
        count = 0
        seen_dates = set()
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if not cols: continue
                
                # Zerodha Column 0: Day Name, Column 1: Date, Column 2: Name
                # Example: Thursday | 25 Dec 2025 | Christmas
                
                if len(cols) >= 3:
                    date_raw = cols[1].text.strip()
                    desc = cols[2].text.strip()
                    
                    try:
                        # Parse "25 Dec 2025"
                        holiday_date = datetime.strptime(date_raw, "%d %b %Y").date()
                        
                        if holiday_date in seen_dates:
                            continue
                        seen_dates.add(holiday_date)
                        
                        # Upsert
                        exists = db.query(models.Holiday).filter(models.Holiday.date == holiday_date).first()
                        if not exists:
                            h = models.Holiday(date=holiday_date, description=desc)
                            db.add(h)
                            count += 1
                    except ValueError:
                        print(f"Skipping invalid date: {date_raw}")
                        continue
                        
        db.commit()

        print(f"Successfully added/updated {count} holidays.")
        db.close()
            
    except Exception as e:
        print(f"Error scraping: {e}")

if __name__ == "__main__":
    scrape_holidays()

# Quick PostgreSQL Password Setup

## Your New Universal Password: `YOUR_POSTGRES_PASSWORD`

### ✅ Step 1: Set PostgreSQL Password to YOUR_POSTGRES_PASSWORD

**Using pgAdmin (Easiest):**
1. Open **pgAdmin 4**
2. Expand: Servers → PostgreSQL 18 → Login/Group Roles
3. Right-click **postgres** → Properties
4. Go to **Definition** tab
5. Enter password: `YOUR_POSTGRES_PASSWORD`
6. Click **Save**

**Using Command Line:**
Open Command Prompt as Administrator:

```cmd
cd "C:\Program Files\PostgreSQL\18\bin"
psql -U postgres
```

Then run:
```sql
ALTER USER postgres WITH PASSWORD 'YOUR_POSTGRES_PASSWORD';
\q
```

### ✅ Step 2: Create Database

```cmd
cd "C:\Program Files\PostgreSQL\18\bin"
psql -U postgres -c "CREATE DATABASE stock_data;"
```

Enter password when prompted: `YOUR_POSTGRES_PASSWORD`

### ✅ Step 3: Test Connection

```cmd
cd "C:\Program Files\PostgreSQL\18\bin"
psql -U postgres -h localhost -d stock_data
```

Password: `YOUR_POSTGRES_PASSWORD`

If successful, type `\q` to exit.

### ✅ Step 4: Uncomment Database Initialization

Open: `backend\main.py`

Find line ~31 and **uncomment**:

```python
# Change from:
# models.Base.metadata.create_all(bind=database.engine)

# To:
models.Base.metadata.create_all(bind=database.engine)
```

### ✅ Step 5: Restart Server

Stop the current server (Ctrl+C in the terminal) and restart:

```cmd
cd C:\Users\rahul\Desktop\StockMarket\StockMarket
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

You should now see the server start without PostgreSQL errors!

---

## Your Configuration Summary

- **DB User:** `postgres`
- **DB Password:** `YOUR_POSTGRES_PASSWORD`
- **DB Host:** `localhost`
- **DB Port:** `5432`
- **DB Name:** `stock_data`

All set in: `backend\.env`

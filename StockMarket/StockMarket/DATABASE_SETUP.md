# PostgreSQL Database Configuration Guide

## Current Issue
Your server is running but getting: `password authentication failed for user "postgres"`

This means the password in your `.env` file doesn't match PostgreSQL's password.

---

## Solution: Reset PostgreSQL Password

### Step 1: Connect to PostgreSQL as Superuser

Open Command Prompt **as Administrator** and run:

```bash
psql -U postgres
```

If prompted for password, try:
- Press Enter (no password)
- Try: `postgres`
- Try: `admin`
- Try: `root`

### Step 2: Reset Password

Once connected to `psql`, run this SQL command:

```sql
ALTER USER postgres WITH PASSWORD 'medikart@3145';
```

You should see: `ALTER ROLE`

### Step 3: Exit PostgreSQL

```sql
\q
```

### Step 4: Test Connection

Test if the new password works:

```bash
psql -U postgres -h localhost -d stock_data
```

Enter password: `medikart@3145`

If successful, you'll see:
```
stock_data=#
```

Exit with `\q`

---

## Alternative: Find Existing Password

### Option A: Check Other .env Files

Check if you have other projects with working PostgreSQL connections:

```bash
# Search for .env files with DB_PASSWORD
Get-ChildItem -Path C:\Users\rahul -Recurse -Filter ".env" | Select-String "DB_PASSWORD"
```

### Option B: Use pgAdmin

If you have pgAdmin installed:
1. Open pgAdmin
2. Right-click on "Servers" → Create → Server
3. Try connecting with different passwords to find the working one

---

## After Fixing Password

### 1. Verify .env File

Check that `backend\.env` has:

```env
DB_USER=postgres
DB_PASSWORD=medikart@3145
DB_HOST=localhost
DB_PORT=5432
DB_NAME=stock_data
```

### 2. Create Database (if doesn't exist)

```bash
psql -U postgres
```

```sql
CREATE DATABASE stock_data;
\q
```

### 3. Uncomment Database Initialization

In `backend\main.py`, line ~31, **uncomment**:

```python
# Currently commented:
# models.Base.metadata.create_all(bind=database.engine)

# Change to:
models.Base.metadata.create_all(bind=database.engine)
```

### 4. Restart Server

Stop the server (Ctrl+C) and restart:

```bash
cd C:\Users\rahul\Desktop\StockMarket\StockMarket
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

You should see:
```
INFO:     Application startup complete.
```

Without any PostgreSQL errors!

---

## Troubleshooting

### Error: "psql: command not found"

Add PostgreSQL to PATH:

1. Find PostgreSQL bin folder (usually: `C:\Program Files\PostgreSQL\18\bin`)
2. Add to System PATH environment variable
3. Restart Command Prompt

### Error: "database does not exist"

Create it manually:

```bash
psql -U postgres -c "CREATE DATABASE stock_data;"
```

### Still Getting Password Error?

Try resetting via pg_hba.conf:

1. Find `pg_hba.conf` (usually in: `C:\Program Files\PostgreSQL\18\data\`)
2. Change `md5` to `trust` for localhost
3. Restart PostgreSQL service
4. Connect without password
5. Reset password using ALTER USER
6. Change `trust` back to `md5`
7. Restart PostgreSQL again

---

## Quick Commands Summary

```bash
# 1. Connect to PostgreSQL
psql -U postgres

# 2. Reset password
ALTER USER postgres WITH PASSWORD 'medikart@3145';

# 3. Create database
CREATE DATABASE stock_data;

# 4. Exit
\q

# 5. Test connection
psql -U postgres -h localhost -d stock_data

# 6. Start server
cd C:\Users\rahul\Desktop\StockMarket\StockMarket
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

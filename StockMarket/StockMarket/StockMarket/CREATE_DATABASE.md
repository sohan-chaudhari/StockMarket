# Create Stock Market Database in PostgreSQL

## Step-by-Step Database Creation

### Method 1: Using pgAdmin 4 (Recommended - Visual Interface)

#### Step 1: Open pgAdmin 4
- Search for "pgAdmin 4" in Windows Start Menu
- Launch the application

#### Step 2: Connect to PostgreSQL Server
- In the left sidebar, you'll see "Servers"
- Expand: **Servers → PostgreSQL 18**
- It may ask for a password - if you know it, enter it
- If you don't know the password, we'll reset it in the next steps

#### Step 3: Create Database
1. Right-click on **"Databases"** under PostgreSQL 18
2. Select **"Create" → "Database..."**
3. In the "General" tab:
   - **Database name**: `stock_data`
   - **Owner**: `postgres`
   - **Encoding**: `UTF8`
4. Click **"Save"**

✅ **Done!** You should now see `stock_data` listed under Databases.

---

### Method 2: Using Command Line

#### Step 1: Open Command Prompt as Administrator

Right-click Command Prompt → "Run as administrator"

#### Step 2: Navigate to PostgreSQL Bin Directory

```cmd
cd "C:\Program Files\PostgreSQL\18\bin"
```

#### Step 3: Connect to PostgreSQL

```cmd
psql -U postgres
```

**If it asks for password:**
- Try pressing **Enter** (empty password)
- Try: `postgres`
- Try: `admin`  
- Try: `medikart@3145`

#### Step 4: Create Database

Once connected (you'll see `postgres=#`), run:

```sql
CREATE DATABASE stock_data
    WITH 
    OWNER = postgres
    ENCODING = 'UTF8'
    CONNECTION LIMIT = -1;
```

You should see: `CREATE DATABASE`

#### Step 5: Verify Database Creation

```sql
\l
```

This lists all databases. You should see `stock_data` in the list.

#### Step 6: Exit

```sql
\q
```

---

### Method 3: Using SQL Shell (psql)

1. Search for **"SQL Shell (psql)"** in Windows Start Menu
2. Press Enter for:
   - Server [localhost]:
   - Database [postgres]:
   - Port [5432]:
   - Username [postgres]:
3. Enter your password (or press Enter if none)
4. Run the CREATE DATABASE command from Method 2, Step 4

---

## What If Password Doesn't Work?

### Quick Password Reset

1. **Find PostgreSQL Data Directory**
   Usually: `C:\Program Files\PostgreSQL\18\data\`

2. **Edit pg_hba.conf**
   - Open `pg_hba.conf` in Notepad (as Administrator)
   - Find lines with `127.0.0.1/32` and `::1/128`
   - Change `md5` or `scram-sha-256` to `trust`:
   
   **Before:**
   ```
   host    all             all             127.0.0.1/32            md5
   host    all             all             ::1/128                 md5
   ```
   
   **After:**
   ```
   host    all             all             127.0.0.1/32            trust
   host    all             all             ::1/128                 trust
   ```

3. **Restart PostgreSQL Service**
   ```cmd
   net stop postgresql-x64-18
   net start postgresql-x64-18
   ```

4. **Connect Without Password**
   ```cmd
   cd "C:\Program Files\PostgreSQL\18\bin"
   psql -U postgres
   ```

5. **Set New Password**
   ```sql
   ALTER USER postgres WITH PASSWORD 'medikart@3145';
   \q
   ```

6. **Restore pg_hba.conf**
   - Change `trust` back to `md5`
   - Restart PostgreSQL again

---

## After Creating Database

### Verify Connection with New Password

```cmd
cd "C:\Program Files\PostgreSQL\18\bin"
psql -U postgres -h localhost -d stock_data
```

Password: `medikart@3145`

If you see `stock_data=#`, success! ✅

Type `\q` to exit.

---

## Next Steps

Once database is created:

1. ✅ Database created: `stock_data`
2. ✅ Password set: `medikart@3145`
3. ✅ Configuration file ready: `backend\.env`

Now you can:
- Uncomment database initialization in `backend\main.py` (line 31)
- Restart your server
- Server will create all required tables automatically

---

## Quick Test Command

To verify everything is ready:

```cmd
cd "C:\Program Files\PostgreSQL\18\bin"
psql -U postgres -h localhost -d stock_data -c "SELECT version();"
```

If this works, you're all set! 🎉

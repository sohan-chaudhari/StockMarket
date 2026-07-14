# Quick Start Guide - Running Live News Fetching

## ✅ **Easiest Method: Use the Batch Files**

I've created batch scripts that handle all the PYTHONPATH setup for you automatically.

### **From the backend folder, just double-click or run:**

```cmd
cd c:\Users\rahul\Desktop\News_Sentiment\backend

# Terminal 1: Start Backend API
start_backend.bat

# Terminal 2: Start Celery Worker (in a new terminal)
start_celery_worker.bat

# Terminal 3: Start Celery Beat (in a new terminal)
start_celery_beat.bat
```

### **From the frontend(HS) folder:**

```cmd
cd c:\Users\rahul\Desktop\News_Sentiment\frontend(HS)
python -m http.server 8080
```

---

## 🔄 **Alternative: Manual Commands**

If you prefer to run the commands manually, here's the correct syntax:

### **1. Start Backend**
```powershell
cd c:\Users\rahul\Desktop\News_Sentiment\backend
python -m uvicorn app.main:app --reload --port 8000
```

### **2. Start Celery Worker**
```powershell
cd c:\Users\rahul\Desktop\News_Sentiment
set PYTHONPATH=%CD%;%CD%\backend
python -m celery -A tasks.celery_app worker -l info --pool=solo
```

### **3. Start Celery Beat**  
```powershell
cd c:\Users\rahul\Desktop\News_Sentiment
set PYTHONPATH=%CD%;%CD%\backend
python -m celery -A tasks.celery_app beat -l info
```

### **4. Start Frontend**
```powershell
cd c:\Users\rahul\Desktop\News_Sentiment\frontend(HS)
python -m http.server 8080
```

---

## 📊 **Access Points**

Once all services are running:

- **Frontend**: http://localhost:8080
- **Backend API**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/api/health

---

## ⚠️ **Important Notes**

1. **Run Docker first**: Ensure databases are running
   ```cmd
   docker-compose up -d postgres mongo redis
   ```

2. **PYTHONPATH is critical**: The batch files handle this automatically. If running manually, you MUST set it from the News_Sentiment folder.

3. **Order matters**: Start services in this order:
   - Docker databases
   - Backend API
   - Celery Worker
   - Celery Beat
   - Frontend

4. **Windows Requirement**: Must use `--pool=solo` for Celery on Windows

---

## 🎯 **Verify Everything is Working**

### Test the Backend:
```powershell
curl http://localhost:8000/api/health
```

Should return: `{"status":"healthy"}`

### Check Frontend:
Open: http://localhost:8080

You should see the crystal black interface. News will appear within 5-10 minutes.

---

## 🐛 **Troubleshooting**

### Error: "module 'tasks' has no attribute 'celery_app'"
**Solution**: Use the batch files, or ensure PYTHONPATH is set correctly from the News_Sentiment folder (not backend folder).

### Error: "Connection refused"
**Solution**: Check that Docker databases are running: `docker ps`

### Frontend shows "Failed to load news"
**Solution**: Wait 5-10 minutes for the first scraping cycle, or check that backend is running on port 8000.

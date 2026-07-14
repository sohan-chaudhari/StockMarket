
import uvicorn
import traceback
import sys

try:
    print("Attempting to import app...")
    from backend.main import app
    print("App imported successfully.")
    
    print("Starting Uvicorn...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
except Exception as e:
    print(f"CRITICAL ERROR: {e}")
    traceback.print_exc()

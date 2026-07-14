import asyncio
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
try:
    response = client.get("/api/top-9-history")
    print(response.status_code)
    print(response.text)
except Exception as e:
    import traceback
    traceback.print_exc()

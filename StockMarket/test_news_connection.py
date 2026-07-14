import requests
import json

NEWS_SERVICE_URL = "http://127.0.0.1:8000/api"
url = f"{NEWS_SERVICE_URL}/scanx/news/full/all"

print(f"Testing connection to: {url}")
try:
    response = requests.get(url, timeout=30)
    print(f"Status Code: {response.status_code}")
    print(f"Response Preview: {response.text[:200]}")
except Exception as e:
    print(f"Error: {e}")

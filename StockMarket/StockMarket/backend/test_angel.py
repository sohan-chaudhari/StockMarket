import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from angelone_service import angelone_service

def test_api():
    print("Logging in...")
    angelone_service.login()
    angelone_service.load_instruments()
    
    token = angelone_service.get_token("RELIANCE")
    print(f"Token for RELIANCE: {token}")
    
    params = {
        "exchange": "NSE",
        "symboltoken": token['token'],
        "interval": "ONE_DAY",
        "fromdate": "2024-07-10 09:15",
        "todate": "2025-07-10 15:30"
    }
    resp = angelone_service.smart_api.getCandleData(params)
    print("ONE_DAY Resp:", resp)

    params2 = {
        "exchange": "NSE",
        "symboltoken": token['token'],
        "interval": "ONE_HOUR",
        "fromdate": "2025-07-01 09:15",
        "todate": "2025-07-10 15:30"
    }
    resp2 = angelone_service.smart_api.getCandleData(params2)
    print("ONE_HOUR Resp:", resp2)

test_api()

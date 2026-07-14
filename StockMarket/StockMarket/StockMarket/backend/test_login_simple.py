"""
Simple test to verify Angel One login works
"""

import sys
import os
from dotenv import load_dotenv
import pathlib

# Load environment variables
backend_dir = pathlib.Path(__file__).parent.resolve()
env_path = backend_dir / ".env"
load_dotenv(dotenv_path=env_path)

# Import after loading env
from SmartApi import SmartConnect
import pyotp

def test_login():
    api_key = os.getenv("ANGELONE_API_KEY")
    client_id = os.getenv("ANGELONE_CLIENT_ID")
    password = os.getenv("ANGELONE_PASSWORD")
    totp_token = os.getenv("ANGELONE_TOTP_TOKEN")
    
    if not all([api_key, client_id, password, totp_token]):
        print("[X] Missing credentials")
        return False
    
    try:
        print("Testing Angel One Login...")
        print(f"API Key: {api_key[:4]}****")
        print(f"Client ID: {client_id}")
        
        # Initialize SmartConnect
        smart_api = SmartConnect(api_key=api_key)
        
        # Generate TOTP
        totp = pyotp.TOTP(totp_token)
        totp_code = totp.now()
        print(f"TOTP Code: {totp_code}")
        
        # Login
        session = smart_api.generateSession(client_id, password, totp_code)
        
        print(f"\nResponse Status: {session.get('status')}")
        print(f"Message: {session.get('message', 'N/A')}")
        
        if session.get('status'):
            print("\n[OK] Login SUCCESSFUL!")
            print(f"Feed Token: {session.get('data', {}).get('feedToken', 'N/A')}")
            return True
        else:
            print(f"\n[X] Login FAILED: {session.get('message')}")
            return False
            
    except Exception as e:
        print(f"\n[X] Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_login()
    sys.exit(0 if success else 1)

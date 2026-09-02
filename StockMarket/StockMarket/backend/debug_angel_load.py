
from backend.angelone_service import AngelOneService
import sys

try:
    print("[DEBUG] Initializing AngelOneService...")
    service = AngelOneService()
    print("[DEBUG] Service Initialized.")
    if service._instruments_loaded:
        print(f"[DEBUG] Instruments Loaded: {len(service._instruments_rows)}")
        print(service._instruments_rows[:2])
    else:
        print("[DEBUG] Instruments NOT LOADED.")
except Exception as e:
    print(f"[DEBUG] CRASH: {e}")
    import traceback
    traceback.print_exc()

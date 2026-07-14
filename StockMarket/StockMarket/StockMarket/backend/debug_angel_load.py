
from backend.angelone_service import AngelOneService
import sys

try:
    print("[DEBUG] Initializing AngelOneService...")
    service = AngelOneService()
    print("[DEBUG] Service Initialized.")
    if service.instruments_df is not None:
        print(f"[DEBUG] Instruments Loaded: {len(service.instruments_df)}")
        print(service.instruments_df.head(2))
    else:
        print("[DEBUG] Instruments NOT LOADED.")
except Exception as e:
    print(f"[DEBUG] CRASH: {e}")
    import traceback
    traceback.print_exc()

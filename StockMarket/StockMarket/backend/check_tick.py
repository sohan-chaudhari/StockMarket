import sys
import os
sys.path.append(os.path.dirname(__file__))
from angelone_service import angelone_service
print(angelone_service.latest_ticks.get('NIFTY', 'No NIFTY tick'))

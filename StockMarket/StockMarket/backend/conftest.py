import sys
import os

# Ensure the backend directory is in the PYTHONPATH so tests can import modules
backend_dir = os.path.dirname(__file__)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

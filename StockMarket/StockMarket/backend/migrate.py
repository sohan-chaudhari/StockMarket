"""Database migration script to create trading tables"""
import sys
import os

# Add parent directory to path to allow package imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import engine
from backend.models import Base

# Create all tables
Base.metadata.create_all(bind=engine)
print("✓ All tables created successfully!")

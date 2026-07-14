from pydantic_settings import BaseSettings
from typing import Optional
import os


def _find_env_file() -> str:
    """Find .env relative to this file's directory (backend/app/ -> backend/ or project root)."""
    this_dir = os.path.dirname(os.path.abspath(__file__))          # backend/app/
    backend_dir = os.path.dirname(this_dir)                         # backend/
    project_dir = os.path.dirname(backend_dir)                      # News_Sentiment/
    # Priority: backend/.env > News_Sentiment/.env
    for candidate in [os.path.join(backend_dir, ".env"),
                      os.path.join(project_dir, ".env")]:
        if os.path.exists(candidate):
            return candidate
    return ".env"  # fall back to CWD


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql://postgres:@localhost:5432/news_sentiment"
    MONGODB_URI: str = "mongodb://localhost:27017/news_articles"
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # API Keys
    GEMINI_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    NEWSAPI_KEY: Optional[str] = None
    
    # Application
    ENVIRONMENT: str = "development"
    SECRET_KEY: str = "change-me-in-production"
    API_PORT: int = 8000
    
    # ML Settings
    MODEL_CACHE_DIR: str = "./models"
    USE_GPU: bool = False
    
    # Feature Flags
    ENABLE_LLM_CALIBRATION: bool = True
    LLM_CONFIDENCE_THRESHOLD: float = 0.7
    ENABLE_RECENCY_SCORING: bool = True
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    class Config:
        env_file = _find_env_file()
        case_sensitive = False
        extra = "allow"


settings = Settings()

from fastapi import APIRouter
from sqlalchemy import text
from datetime import datetime
import redis

from app.database import get_db, get_redis_instance, get_mongodb_instance
from app.schemas import HealthResponse

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint
    Returns status of all services
    """
    services = {}
    
    # Check PostgreSQL
    try:
        db = next(get_db())
        if db:
            db.execute(text("SELECT 1"))
            services["postgres"] = "healthy"
        else:
            services["postgres"] = "unavailable"
    except Exception as e:
        services["postgres"] = f"unhealthy: {str(e)}"
    
    # Check MongoDB
    try:
        mongo = get_mongodb_instance()
        if mongo is not None:
            await mongo.command("ping")
            services["mongodb"] = "healthy"
        else:
            services["mongodb"] = "unavailable"
    except Exception as e:
        services["mongodb"] = f"unhealthy: {str(e)}"
    
    # Check Redis
    try:
        redis_client = get_redis_instance()
        if redis_client:
            redis_client.ping()
            services["redis"] = "healthy"
        else:
            services["redis"] = "unavailable"
    except Exception as e:
        services["redis"] = f"unhealthy: {str(e)}"
    
    # Overall status
    healthy_count = sum(1 for s in services.values() if s == "healthy")
    total = len(services)
    status = "healthy" if healthy_count == total else ("degraded" if healthy_count > 0 else "unhealthy")
    
    return HealthResponse(
        status=status,
        services=services,
        timestamp=datetime.utcnow()
    )

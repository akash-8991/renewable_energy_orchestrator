from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Liveness: the process is up. Says nothing about its dependencies."""
    return {"status": "ok"}


@router.get("/ready")
def ready():
    """Readiness: the api can actually serve — the database and Redis both
    answer. 503 otherwise, so a load balancer stops sending it traffic."""
    from database.connection import SessionLocal
    from reo_common.config import get_settings

    checks: dict[str, str] = {}
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        import redis

        redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ready" if healthy else "not_ready", "checks": checks}, status_code=200 if healthy else 503)

"""
Subscriber self-service API — /api/subscriber/
Fase 2 placeholder. Fase 3: self-service para app móvil/web del suscriptor.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/subscriber", tags=["Subscriber"])


@router.get("/ping")
async def subscriber_ping():
    return {"status": "ok", "domain": "subscriber", "note": "Fase 3 — en construcción"}

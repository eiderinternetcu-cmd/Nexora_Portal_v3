from fastapi import APIRouter
from app.api.client import auth, profile, catalog, playback

router = APIRouter(prefix="/api/client", tags=["Client"])

router.include_router(auth.router)
router.include_router(profile.router)
router.include_router(catalog.router)
router.include_router(playback.router)

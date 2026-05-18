from fastapi import APIRouter
from app.api.stb import devices, playback

router = APIRouter(prefix="/api/stb")
router.include_router(devices.router)
router.include_router(playback.router)

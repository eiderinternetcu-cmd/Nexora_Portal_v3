from fastapi import APIRouter
from app.api.v1 import auth, users, subscribers, devices, plans

router = APIRouter(prefix="/api/v1")

router.include_router(auth.router)
router.include_router(users.router)
router.include_router(subscribers.router)
router.include_router(devices.router)
router.include_router(plans.router)

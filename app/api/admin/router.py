from fastapi import APIRouter
from app.api.v1 import auth, users, subscribers, devices, plans
from app.api.admin import sessions, subscriptions, channels, flussonic

router = APIRouter(prefix="/api/admin")

router.include_router(auth.router)
router.include_router(users.router)
router.include_router(subscribers.router)
router.include_router(devices.router)
router.include_router(plans.router)
router.include_router(sessions.router)
router.include_router(subscriptions.router)
router.include_router(channels.router)
router.include_router(flussonic.router)

"""Client (subscriber) authentication endpoints."""
from fastapi import APIRouter, Depends, Request
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import get_current_subscriber, get_client_token_payload
from app.models.subscriber import Subscriber
from app.schemas.client import (
    ClientLoginRequest,
    ClientTokenResponse,
    ClientRefreshRequest,
    ClientLogoutRequest,
)
from app.services.client_auth_service import ClientAuthService

router = APIRouter(prefix="/auth", tags=["Client Auth"])


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=ClientTokenResponse)
async def client_login(
    data: ClientLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    svc = ClientAuthService(db, redis)
    access_token, refresh_token, subscriber_id, expires_in, device_registration = await svc.login(
        data=data,
        ip=_get_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    await db.commit()
    return ClientTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        subscriber_id=subscriber_id,
        device_registration=device_registration,
    )


@router.post("/refresh", response_model=ClientTokenResponse)
async def client_refresh(
    data: ClientRefreshRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    svc = ClientAuthService(db, redis)
    access_token, refresh_token, subscriber_id, expires_in = await svc.refresh(data.refresh_token)
    return ClientTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        subscriber_id=subscriber_id,
    )


@router.post("/logout", status_code=204)
async def client_logout(
    data: ClientLogoutRequest | None = None,
    payload: dict = Depends(get_client_token_payload),
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    svc = ClientAuthService(db, redis)
    await svc.logout(
        access_jti=payload["jti"],
        refresh_token_str=data.refresh_token if data else None,
    )

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.database import get_db
from app.redis_client import get_redis
from app.services.auth_service import AuthService
from app.schemas.auth import LoginRequest, TokenResponse, RefreshRequest
from app.schemas.common import MessageResponse
from app.core.dependencies import get_current_user, get_client_ip, _get_token_payload
from app.schemas.auth import TokenPayload

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Login con username/password. Devuelve access + refresh token."""
    ip = get_client_ip(request)
    service = AuthService(db, redis)
    return await service.login(body.username, body.password, ip)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Rota el refresh token y emite un nuevo par de tokens."""
    service = AuthService(db, redis)
    return await service.refresh(body.refresh_token)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: RefreshRequest | None = None,
    payload: TokenPayload = Depends(_get_token_payload),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Revoca el access token actual y opcionalmente el refresh token."""
    service = AuthService(db, redis)
    refresh = body.refresh_token if body else None
    await service.logout(payload.jti, refresh)
    return MessageResponse(message="Logged out successfully")


@router.get("/me")
async def me(user=Depends(get_current_user)):
    """Devuelve el usuario autenticado actual."""
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
    }

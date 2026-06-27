"""Internal stream-auth endpoint for the Nginx `auth_request` gate (FASE 5).

Nginx issues a subrequest here for every /stream/* request. We validate the
playback token (signature, type, aud, iss, exp, session, device, stream binding)
and answer 2xx (allow) or 401/403 (deny). No secrets are returned.

This endpoint is meant to be reachable ONLY from the edge (internal location in
Nginx). It performs an authorization check; it returns no stream data.
"""
from urllib.parse import urlsplit, parse_qs

from fastapi import APIRouter, Request
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from fastapi import Depends
from app.services.stream_auth_service import StreamAuthService

router = APIRouter(prefix="/internal/stream-auth", tags=["Internal — Stream Auth"])


def _extract(request: Request) -> tuple[str | None, str | None, str | None]:
    """Resolve (token, stream_key, node) from query params or the original URI.

    Preferred (explicit, e.g. tests / simple Nginx): ?token=&stream_key=&node=.
    Nginx auth_request: pass X-Playback-Token + X-Original-URI; we parse the
    original /stream/<node>/<stream_key>/... path and its ?token=.
    """
    token = request.query_params.get("token") or request.headers.get("X-Playback-Token")
    stream_key = request.query_params.get("stream_key")
    node = request.query_params.get("node")

    original = request.headers.get("X-Original-URI")
    if original and (not token or not stream_key or not node):
        parts = urlsplit(original)
        if not token:
            token = parse_qs(parts.query).get("token", [None])[0]
        segs = [s for s in parts.path.split("/") if s]
        if "stream" in segs:
            i = segs.index("stream")
            if node is None and len(segs) > i + 1:
                node = segs[i + 1]
            if stream_key is None and len(segs) > i + 2:
                stream_key = segs[i + 2]
    return token, stream_key, node


@router.get("/validate")
async def validate_stream(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    token, stream_key, node = _extract(request)
    svc = StreamAuthService(db, redis)
    # Raises NexoraException(401/403) on failure → handled by the global handler.
    result = await svc.validate_stream_request(token, stream_key=stream_key, node=node)
    return {"ok": True, "channel_id": result.get("channel_id")}

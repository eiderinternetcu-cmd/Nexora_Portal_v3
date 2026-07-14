"""Internal stream-auth endpoint for the Nginx `auth_request` gate (FASE 5).

Nginx issues a subrequest here for every /stream/* request. We validate the
playback token (signature, type, aud, iss, exp, session, device, stream binding)
and answer 2xx (allow) or 401/403 (deny). No secrets are returned.

This endpoint is meant to be reachable ONLY from the edge (internal location in
Nginx). It performs an authorization check; it returns no stream data.
"""
from urllib.parse import urlsplit, parse_qs

from fastapi import APIRouter, Depends, Request
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.redis_client import get_redis
from app.core.security import hash_ip
from app.core.exceptions import NexoraException
from app.services.stream_auth_service import StreamAuthService

settings = get_settings()

router = APIRouter(prefix="/internal/stream-auth", tags=["Internal — Stream Auth"])


def _client_ip(request: Request) -> str | None:
    """Client IP as set by the trusted edge (Nginx). Prefer X-Real-IP, then the
    first hop of X-Forwarded-For. The endpoint is edge-internal (see Nginx
    `internal` location), so these headers come from Nginx, not the public XFF."""
    xri = request.headers.get("X-Real-IP")
    if xri:
        return xri.strip()
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return None


def _extract(request: Request) -> tuple[str | None, str | None, str | None]:
    """Resolve (token, stream_key, node) from query params or the original URI.

    Preferred (explicit, e.g. tests / simple Nginx): ?token=&stream_key=&node=.
    Nginx auth_request: pass X-Playback-Token + X-Original-URI; we parse the
    original /stream/<node>/<stream_key>/... path and its ?token=.
    """
    token = request.query_params.get("token") or request.headers.get("X-Playback-Token")
    stream_key = request.query_params.get("stream_key") or request.headers.get("X-Stream-Key")
    node = request.query_params.get("node") or request.headers.get("X-Stream-Node")

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
    """Edge auth gate for /stream/*.

    - Request WITH token (HLS manifest): full token validation + IP binding, then
      seed a short-lived grant so tokenless segments of the same node+stream+IP pass.
    - Request WITHOUT token (HLS segment): allow only if a valid grant exists for
      this node+stream+client IP (seeded by a prior manifest). Otherwise 401.
    Raises NexoraException(401/403) on failure → handled by the global handler.
    """
    token, stream_key, node = _extract(request)
    client_ip = _client_ip(request)
    svc = StreamAuthService(db, redis)

    if token:
        try:
            out = await svc.validate_stream_request(
                token, stream_key=stream_key, node=node, client_ip=client_ip
            )
        except NexoraException:
            # Continuity (M1): a present-but-expired/invalid token still passes if a
            # valid grant already exists for this node+stream+IP (seeded by a prior
            # valid manifest from the same client). Guards a token expiring mid-session
            # between renewals. No grant → propagate the original deny.
            if (
                settings.stream_grant_token_fallback
                and node and stream_key
                and await svc.check_stream_grant(node, stream_key, hash_ip(client_ip))
            ):
                return {"ok": True}
            raise
        g_node = out.get("node") or node
        g_key = out.get("stream_key") or stream_key
        if g_node and g_key:
            await svc.grant_stream_access(g_node, g_key, hash_ip(client_ip), out.get("session_id"))
        return {"ok": True, "channel_id": out.get("channel_id")}

    # Tokenless (segment): must be covered by a grant from a prior manifest.
    if not (node and stream_key):
        raise NexoraException(401, "Missing playback token")
    if not await svc.check_stream_grant(node, stream_key, hash_ip(client_ip)):
        raise NexoraException(401, "No stream authorization for this segment")
    return {"ok": True}

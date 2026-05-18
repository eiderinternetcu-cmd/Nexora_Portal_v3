"""Admin — Flussonic read-only integration endpoints.

Allows admins to inspect what streams exist in Flussonic so they can
update the local channel catalog (stream_key mappings) accordingly.

SECURITY:
  - Requires admin/reseller Bearer token.
  - Flussonic credentials are never included in any response.
  - All operations are READ-ONLY. Write methods raise RuntimeError.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.dependencies import require_admin_or_reseller
from app.models.user import User
from app.integrations.flussonic_client import get_flussonic_client

router = APIRouter(prefix="/flussonic", tags=["Admin Flussonic"])

_flussonic = get_flussonic_client()


class FlussonicStreamItem(BaseModel):
    """Safe, minimal stream info — no credentials or internal config."""
    name: str
    alive: bool
    client_count: int
    hls_url: str


class FlussonicHealthOut(BaseModel):
    configured: bool
    reachable: bool
    base_url_host: str  # only host:port, not credentials


@router.get("/health", response_model=FlussonicHealthOut)
async def flussonic_health(
    user: User = Depends(require_admin_or_reseller),
):
    """Check if Flussonic is configured and reachable."""
    if not _flussonic.is_configured:
        return FlussonicHealthOut(
            configured=False,
            reachable=False,
            base_url_host="",
        )

    reachable = await _flussonic.check_connectivity()

    # Extract only host:port — never expose credentials or full URL with auth
    from urllib.parse import urlparse
    parsed = urlparse(_flussonic._base)
    host_port = parsed.netloc  # e.g. "181.78.246.211:8002"

    return FlussonicHealthOut(
        configured=True,
        reachable=reachable,
        base_url_host=host_port,
    )


@router.get("/streams", response_model=list[FlussonicStreamItem])
async def list_flussonic_streams(
    user: User = Depends(require_admin_or_reseller),
):
    """
    List all streams from Flussonic.
    Use this to discover stream names and map them to local channel stream_key values.
    No Flussonic credentials in the response.
    503 if Flussonic is not configured.
    """
    if not _flussonic.is_configured:
        raise HTTPException(
            status_code=503,
            detail="Flussonic integration is not configured.",
        )

    try:
        raw = await _flussonic.list_streams()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Flussonic unreachable: {exc}")

    return [
        FlussonicStreamItem(
            name=s.get("name", ""),
            alive=bool(s.get("alive", False)),
            client_count=int(s.get("client_count", 0)),
            hls_url=_flussonic.stream_hls_url(s.get("name", "")),
        )
        for s in raw
        if s.get("name")
    ]


@router.get("/streams/{stream_name}", response_model=FlussonicStreamItem)
async def get_flussonic_stream(
    stream_name: str,
    user: User = Depends(require_admin_or_reseller),
):
    """Get info for a specific Flussonic stream by name."""
    if not _flussonic.is_configured:
        raise HTTPException(status_code=503, detail="Flussonic not configured.")

    status = await _flussonic.get_stream_status(stream_name)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Stream '{stream_name}' not found.")

    return FlussonicStreamItem(
        name=status.name,
        alive=status.alive,
        client_count=status.client_count,
        hls_url=_flussonic.stream_hls_url(status.name),
    )

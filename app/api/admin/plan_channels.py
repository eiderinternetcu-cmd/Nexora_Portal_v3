"""Admin — plan ↔ channel whitelist (plan_channels).

Until now this table had no API at all and had to be seeded by hand
(scripts/seed_plan_channels.py) even though it is what decides, per plan, which
channels playback allows: EntitlementService denies CHANNEL_NOT_INCLUDED for any
channel without an enabled row, so a plan with no rows grants NOTHING.

Reads follow the rest of the panel (admin or reseller). Writes are admin-only
and always audited: changing a plan's whitelist changes what every subscriber of
that plan can watch, at once.
"""
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_client_ip, require_admin, require_admin_or_reseller
from app.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.plan_channel import (
    PlanChannelOut,
    PlanChannelsReplace,
    PlanChannelsSummary,
)
from app.services.audit_service import AuditService
from app.services.plan_channel_service import PlanChannelService

router = APIRouter(prefix="/plans", tags=["Plan Channels"])


@router.get("/{plan_id}/channels", response_model=ApiResponse[list[PlanChannelOut]])
async def list_plan_channels(
    plan_id: uuid.UUID,
    include_excluded: bool = Query(
        False,
        description="Return the full channel catalog marking which ones the "
                    "plan includes, instead of only the included ones.",
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin_or_reseller),
):
    svc = PlanChannelService(db)
    data = await svc.list_for_plan(plan_id, include_excluded=include_excluded)
    return ApiResponse(data=data)


@router.put("/{plan_id}/channels", response_model=ApiResponse[PlanChannelsSummary])
async def replace_plan_channels(
    plan_id: uuid.UUID,
    body: PlanChannelsReplace,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    """Atomically replace the whole whitelist with `channel_ids`."""
    svc = PlanChannelService(db)
    audit = AuditService(db)
    summary = await svc.replace(plan_id, body.channel_ids, actor_id=actor.id)
    await audit.log(
        "plan.channels_replace", actor, "plan", str(plan_id),
        {
            "channel_ids": [str(c) for c in body.channel_ids],
            "added": summary.added,
            "removed": summary.removed,
            "total": summary.total,
        },
        get_client_ip(request),
    )
    return ApiResponse(data=summary, message="Plan channels replaced")


@router.post(
    "/{plan_id}/channels/{channel_id}",
    response_model=ApiResponse[PlanChannelsSummary],
)
async def add_plan_channel(
    plan_id: uuid.UUID,
    channel_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    """Include one channel. Idempotent — 200 with added=0 if already included
    (201 is deliberately not used: the call is a state assertion, not a
    creation, and repeating it must not look like a different outcome)."""
    svc = PlanChannelService(db)
    audit = AuditService(db)
    summary = await svc.add(plan_id, channel_id, actor_id=actor.id)
    await audit.log(
        "plan.channels_add", actor, "plan", str(plan_id),
        {"channel_id": str(channel_id), "added": summary.added, "total": summary.total},
        get_client_ip(request),
    )
    return ApiResponse(data=summary, message="Channel included in plan")


@router.delete(
    "/{plan_id}/channels/{channel_id}",
    response_model=ApiResponse[PlanChannelsSummary],
)
async def remove_plan_channel(
    plan_id: uuid.UUID,
    channel_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    """Exclude one channel. Idempotent — 200 with removed=0 if it was not in
    the plan."""
    svc = PlanChannelService(db)
    audit = AuditService(db)
    summary = await svc.remove(plan_id, channel_id)
    await audit.log(
        "plan.channels_remove", actor, "plan", str(plan_id),
        {"channel_id": str(channel_id), "removed": summary.removed, "total": summary.total},
        get_client_ip(request),
    )
    return ApiResponse(data=summary, message="Channel excluded from plan")

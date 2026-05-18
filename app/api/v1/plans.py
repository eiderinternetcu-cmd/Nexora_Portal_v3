import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.plan_service import PlanService
from app.services.audit_service import AuditService
from app.schemas.plan import PlanCreate, PlanUpdate, PlanOut
from app.schemas.common import ApiResponse, MessageResponse
from app.core.dependencies import require_admin, require_admin_or_reseller, get_client_ip
from app.models.user import User

router = APIRouter(prefix="/plans", tags=["Plans"])


@router.get("", response_model=ApiResponse[list[PlanOut]])
async def list_plans(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin_or_reseller),
):
    svc = PlanService(db)
    plans = await svc.list_plans(only_active=False)
    return ApiResponse(data=plans)


@router.post("", response_model=ApiResponse[PlanOut], status_code=201)
async def create_plan(
    body: PlanCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    svc = PlanService(db)
    audit = AuditService(db)
    plan = await svc.create(body)
    await audit.log("plan.create", actor, "plan", str(plan.id),
                    {"name": plan.name}, get_client_ip(request))
    return ApiResponse(data=PlanOut.model_validate(plan), message="Plan created")


@router.get("/{plan_id}", response_model=ApiResponse[PlanOut])
async def get_plan(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin_or_reseller),
):
    svc = PlanService(db)
    plan = await svc.get_by_id(plan_id)
    return ApiResponse(data=PlanOut.model_validate(plan))


@router.patch("/{plan_id}", response_model=ApiResponse[PlanOut])
async def update_plan(
    plan_id: uuid.UUID,
    body: PlanUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    svc = PlanService(db)
    audit = AuditService(db)
    plan = await svc.update(plan_id, body)
    await audit.log("plan.update", actor, "plan", str(plan_id),
                    body.model_dump(exclude_none=True), get_client_ip(request))
    return ApiResponse(data=PlanOut.model_validate(plan))


@router.delete("/{plan_id}", response_model=MessageResponse)
async def delete_plan(
    plan_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    svc = PlanService(db)
    audit = AuditService(db)
    await svc.delete(plan_id)
    await audit.log("plan.delete", actor, "plan", str(plan_id),
                    None, get_client_ip(request))
    return MessageResponse(message="Plan deleted")

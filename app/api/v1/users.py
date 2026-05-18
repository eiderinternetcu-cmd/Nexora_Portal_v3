import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.user_service import UserService
from app.services.audit_service import AuditService
from app.schemas.user import UserCreate, UserUpdate, UserOut, UserPasswordChange
from app.schemas.common import PaginatedResponse, MessageResponse, ApiResponse
from app.core.dependencies import get_current_user, require_admin, get_client_ip
from app.models.user import User
from fastapi import Request

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("", response_model=PaginatedResponse[UserOut])
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    svc = UserService(db)
    users, total = await svc.list_users(page, page_size)
    pages = (total + page_size - 1) // page_size
    return PaginatedResponse(data=users, total=total, page=page, page_size=page_size, pages=pages)


@router.post("", response_model=ApiResponse[UserOut], status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    svc = UserService(db)
    audit = AuditService(db)
    user = await svc.create(body)
    await audit.log("user.create", actor, "user", str(user.id),
                    {"username": user.username, "role": user.role.value},
                    get_client_ip(request))
    return ApiResponse(data=UserOut.model_validate(user), message="User created")


@router.get("/{user_id}", response_model=ApiResponse[UserOut])
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    svc = UserService(db)
    user = await svc.get_by_id(user_id)
    return ApiResponse(data=UserOut.model_validate(user))


@router.patch("/{user_id}", response_model=ApiResponse[UserOut])
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    svc = UserService(db)
    audit = AuditService(db)
    user = await svc.update(user_id, body)
    await audit.log("user.update", actor, "user", str(user_id),
                    body.model_dump(exclude_none=True), get_client_ip(request))
    return ApiResponse(data=UserOut.model_validate(user))


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    svc = UserService(db)
    audit = AuditService(db)
    await svc.delete(user_id)
    await audit.log("user.delete", actor, "user", str(user_id), None, get_client_ip(request))
    return MessageResponse(message="User deleted")


@router.post("/me/change-password", response_model=MessageResponse)
async def change_own_password(
    body: UserPasswordChange,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    svc = UserService(db)
    await svc.change_password(actor.id, body.current_password, body.new_password)
    return MessageResponse(message="Password updated")

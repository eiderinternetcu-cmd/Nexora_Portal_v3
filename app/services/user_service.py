import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from app.models.user import User, UserRole
from app.core.security import hash_password
from app.core.exceptions import not_found, already_exists, bad_request
from app.schemas.user import UserCreate, UserUpdate
from app.services.subscriber_service import LIKE_ESCAPE, escape_like


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: uuid.UUID) -> User:
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise not_found("User")
        return user

    async def get_by_username(self, username: str) -> User | None:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def list_users(
        self,
        page: int = 1,
        page_size: int = 50,
        q: str | None = None,
        role: UserRole | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[User], int]:
        query = select(User)
        count_query = select(func.count()).select_from(User)
        if q and q.strip():
            # Busqueda libre del panel: username, nombre y email.
            pattern = f"%{escape_like(q.strip())}%"
            search = or_(
                User.username.ilike(pattern, escape=LIKE_ESCAPE),
                User.full_name.ilike(pattern, escape=LIKE_ESCAPE),
                User.email.ilike(pattern, escape=LIKE_ESCAPE),
            )
            query = query.where(search)
            count_query = count_query.where(search)
        if role is not None:
            query = query.where(User.role == role)
            count_query = count_query.where(User.role == role)
        if is_active is not None:
            query = query.where(User.is_active == is_active)
            count_query = count_query.where(User.is_active == is_active)
        offset = (page - 1) * page_size
        # Mismo criterio determinista que en subscribers: sin ORDER BY, paginar
        # con offset/limit puede repetir y perder filas. El id desempata empates
        # de created_at y hace el recorrido estable.
        query = (
            query.order_by(User.created_at.desc(), User.id).offset(offset).limit(page_size)
        )
        result = await self.db.execute(query)
        total = (await self.db.execute(count_query)).scalar_one()
        return list(result.scalars().all()), total

    async def create(self, data: UserCreate) -> User:
        if await self.get_by_username(data.username):
            raise already_exists("Username")
        existing_email = await self.db.execute(select(User).where(User.email == data.email))
        if existing_email.scalar_one_or_none():
            raise already_exists("Email")
        user = User(
            username=data.username,
            email=data.email,
            password_hash=hash_password(data.password),
            full_name=data.full_name,
            role=data.role,
            notes=data.notes,
        )
        self.db.add(user)
        await self.db.flush()
        return user

    async def update(self, user_id: uuid.UUID, data: UserUpdate) -> User:
        user = await self.get_by_id(user_id)
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(user, field, value)
        await self.db.flush()
        return user

    async def change_password(self, user_id: uuid.UUID, current_password: str, new_password: str) -> None:
        from app.core.security import verify_password
        user = await self.get_by_id(user_id)
        if not verify_password(current_password, user.password_hash):
            raise bad_request("Current password is incorrect")
        user.password_hash = hash_password(new_password)
        await self.db.flush()

    async def set_password(self, user_id: uuid.UUID, new_password: str) -> None:
        """Reposicion por un administrador: NO exige la contrasena actual.
        El control de acceso vive en el endpoint (require_admin) y la escritura
        queda registrada en auditoria, igual que en subscribers."""
        user = await self.get_by_id(user_id)
        user.password_hash = hash_password(new_password)
        await self.db.flush()

    async def delete(self, user_id: uuid.UUID) -> None:
        user = await self.get_by_id(user_id)
        await self.db.delete(user)
        await self.db.flush()

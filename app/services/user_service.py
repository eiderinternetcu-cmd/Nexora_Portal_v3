import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.user import User
from app.core.security import hash_password
from app.core.exceptions import not_found, already_exists, bad_request
from app.schemas.user import UserCreate, UserUpdate


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

    async def list_users(self, page: int = 1, page_size: int = 50) -> tuple[list[User], int]:
        offset = (page - 1) * page_size
        result = await self.db.execute(select(User).offset(offset).limit(page_size))
        users = result.scalars().all()
        count = await self.db.execute(select(func.count()).select_from(User))
        total = count.scalar_one()
        return list(users), total

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

    async def delete(self, user_id: uuid.UUID) -> None:
        user = await self.get_by_id(user_id)
        await self.db.delete(user)
        await self.db.flush()

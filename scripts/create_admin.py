"""
Crea el primer usuario admin.
Uso: python scripts/create_admin.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.config import get_settings
from app.models.user import User, UserRole
from app.core.security import hash_password
import uuid

settings = get_settings()


async def main():
    username = input("Admin username [admin]: ").strip() or "admin"
    email    = input("Admin email: ").strip()
    password = input("Admin password: ").strip()

    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        user = User(
            id=uuid.uuid4(),
            username=username,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.admin,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        print(f"\n✓ Admin '{username}' created with id={user.id}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

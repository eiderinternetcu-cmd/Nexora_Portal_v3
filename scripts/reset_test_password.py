"""Reset testuser1 password to NexoraTest123! for testing."""
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.database import AsyncSessionLocal
from app.core.security import hash_password


async def main() -> None:
    password = "NexoraTest123!"
    new_hash = hash_password(password)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("UPDATE subscribers SET password_hash = :h WHERE username = 'testuser1' RETURNING username"),
            {"h": new_hash},
        )
        row = result.fetchone()
        await db.commit()
        if row:
            print(f"Password updated for: {row[0]}")
            print(f"New password: {password}")
        else:
            print("User not found")


if __name__ == "__main__":
    asyncio.run(main())

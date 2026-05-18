import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.plan import Plan
from app.core.exceptions import not_found, already_exists
from app.schemas.plan import PlanCreate, PlanUpdate


class PlanService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, plan_id: uuid.UUID) -> Plan:
        result = await self.db.execute(select(Plan).where(Plan.id == plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            raise not_found("Plan")
        return plan

    async def list_plans(self, only_active: bool = True) -> list[Plan]:
        query = select(Plan)
        if only_active:
            query = query.where(Plan.is_active == True)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create(self, data: PlanCreate) -> Plan:
        existing = await self.db.execute(select(Plan).where(Plan.name == data.name))
        if existing.scalar_one_or_none():
            raise already_exists("Plan name")
        plan = Plan(**data.model_dump())
        self.db.add(plan)
        await self.db.flush()
        return plan

    async def update(self, plan_id: uuid.UUID, data: PlanUpdate) -> Plan:
        plan = await self.get_by_id(plan_id)
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(plan, field, value)
        await self.db.flush()
        return plan

    async def delete(self, plan_id: uuid.UUID) -> None:
        plan = await self.get_by_id(plan_id)
        await self.db.delete(plan)
        await self.db.flush()

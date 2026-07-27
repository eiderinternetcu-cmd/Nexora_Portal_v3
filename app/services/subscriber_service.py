import uuid
import secrets
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from app.models.subscriber import Subscriber, SubscriberStatus
from app.core.security import hash_password
from app.core.exceptions import not_found, already_exists, bad_request
from app.schemas.subscriber import SubscriberCreate, SubscriberUpdate

# Caracter de escape para los patrones LIKE/ILIKE. En Python "\\" es UNA barra.
LIKE_ESCAPE = "\\"


def escape_like(term: str) -> str:
    """Neutraliza los comodines de LIKE en texto tecleado por el usuario.

    Sin esto, un admin que escriba `%` en el buscador no filtraria nada: el
    patron pasaria a ser `%%%` y devolveria la tabla entera. Se escapa tambien
    la propia barra para que `\\` se busque como literal.
    """
    return (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


class SubscriberService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, sub_id: uuid.UUID) -> Subscriber:
        result = await self.db.execute(select(Subscriber).where(Subscriber.id == sub_id))
        sub = result.scalar_one_or_none()
        if not sub:
            raise not_found("Subscriber")
        return sub

    async def get_by_username(self, username: str) -> Subscriber | None:
        result = await self.db.execute(select(Subscriber).where(Subscriber.username == username))
        return result.scalar_one_or_none()

    async def get_by_activation_code(self, code: str) -> Subscriber | None:
        result = await self.db.execute(
            select(Subscriber).where(Subscriber.activation_code == code)
        )
        return result.scalar_one_or_none()

    async def list_subscribers(
        self,
        page: int = 1,
        page_size: int = 50,
        status: SubscriberStatus | None = None,
        q: str | None = None,
    ) -> tuple[list[Subscriber], int]:
        query = select(Subscriber)
        count_query = select(func.count()).select_from(Subscriber)
        if status:
            query = query.where(Subscriber.status == status)
            count_query = count_query.where(Subscriber.status == status)
        if q and q.strip():
            # Busqueda libre del panel: username, nombre, email y cedula.
            pattern = f"%{escape_like(q.strip())}%"
            search = or_(
                Subscriber.username.ilike(pattern, escape=LIKE_ESCAPE),
                Subscriber.full_name.ilike(pattern, escape=LIKE_ESCAPE),
                Subscriber.email.ilike(pattern, escape=LIKE_ESCAPE),
                Subscriber.id_cedula.ilike(pattern, escape=LIKE_ESCAPE),
            )
            query = query.where(search)
            count_query = count_query.where(search)
        offset = (page - 1) * page_size
        # ORDER BY determinista: sin el, Postgres no garantiza orden entre
        # consultas y con offset/limit una misma fila puede salir dos veces en la
        # pagina 2 y nunca en la 1. El id desempata cuando dos filas comparten
        # created_at, que es lo que hace el orden ESTABLE y no solo "ordenado".
        query = (
            query.order_by(Subscriber.created_at.desc(), Subscriber.id)
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(query)
        total = (await self.db.execute(count_query)).scalar_one()
        return list(result.scalars().all()), total

    async def create(self, data: SubscriberCreate, created_by: uuid.UUID | None = None) -> Subscriber:
        if await self.get_by_username(data.username):
            raise already_exists("Username")
        if not data.password and not data.activation_code:
            raise bad_request("Either password or activation_code is required")

        password_hash = hash_password(data.password) if data.password else None
        activation_code = data.activation_code or secrets.token_urlsafe(12)

        sub = Subscriber(
            username=data.username,
            password_hash=password_hash,
            activation_code=activation_code,
            email=data.email,
            phone=data.phone,
            full_name=data.full_name,
            id_cedula=data.id_cedula,
            notes=data.notes,
            created_by=created_by,
        )
        self.db.add(sub)
        await self.db.flush()
        return sub

    async def update(self, sub_id: uuid.UUID, data: SubscriberUpdate) -> Subscriber:
        sub = await self.get_by_id(sub_id)
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(sub, field, value)
        await self.db.flush()
        return sub

    async def set_password(self, sub_id: uuid.UUID, new_password: str) -> None:
        sub = await self.get_by_id(sub_id)
        sub.password_hash = hash_password(new_password)
        await self.db.flush()

    async def set_status(self, sub_id: uuid.UUID, status: SubscriberStatus) -> Subscriber:
        sub = await self.get_by_id(sub_id)
        sub.status = status
        await self.db.flush()
        return sub

    async def delete(self, sub_id: uuid.UUID) -> None:
        sub = await self.get_by_id(sub_id)
        await self.db.delete(sub)
        await self.db.flush()

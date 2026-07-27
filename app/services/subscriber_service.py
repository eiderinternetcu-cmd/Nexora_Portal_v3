import uuid
import secrets
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, true

from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.subscription import Subscription
from app.models.plan import Plan
from app.models.user import User
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

    def _enriched_select(self):
        """SELECT que trae cada suscriptor junto a su suscripcion ACTIVA, el
        nombre del plan y el username del owner en UNA sola consulta (sin N+1).

        La suscripcion activa se resuelve con un LEFT JOIN LATERAL limitado a 1
        fila (la de mayor expires_at). El LATERAL con LIMIT 1 garantiza como
        maximo una fila por suscriptor, asi que el join NO multiplica filas y no
        rompe la paginacion aunque un suscriptor tuviera varias suscripciones
        marcadas is_active.
        """
        active = (
            select(
                Subscription.expires_at.label("expires_at"),
                Subscription.plan_id.label("plan_id"),
            )
            .where(
                Subscription.subscriber_id == Subscriber.id,
                Subscription.is_active.is_(True),
            )
            .order_by(Subscription.expires_at.desc())
            .limit(1)
            .lateral("active_sub")
        )
        return (
            select(
                Subscriber,
                active.c.expires_at,
                Plan.name,
                User.username,
            )
            .select_from(Subscriber)
            .outerjoin(active, true())
            .outerjoin(Plan, Plan.id == active.c.plan_id)
            .outerjoin(User, User.id == Subscriber.created_by)
        )

    @staticmethod
    def _enrich(row) -> Subscriber:
        """Adjunta los campos calculados del panel a la instancia Subscriber.

        Son atributos de instancia (no columnas mapeadas); SubscriberOut los lee
        via from_attributes.
        """
        sub, expires_at, plan_name, owner_username = row
        sub.subscription_expires_at = expires_at
        sub.plan_name = plan_name
        sub.owner_username = owner_username
        if expires_at is not None:
            now = datetime.now(timezone.utc)
            sub.days_remaining = (expires_at - now).days
        else:
            sub.days_remaining = None
        return sub

    async def get_by_id(self, sub_id: uuid.UUID, owner_id: uuid.UUID | None = None) -> Subscriber:
        """Devuelve el suscriptor enriquecido. Si `owner_id` viene (reseller), se
        restringe a los suyos: un suscriptor ajeno se trata como INEXISTENTE
        (404), sin filtrar su existencia."""
        query = self._enriched_select().where(Subscriber.id == sub_id)
        if owner_id is not None:
            query = query.where(Subscriber.created_by == owner_id)
        row = (await self.db.execute(query)).first()
        if not row:
            raise not_found("Subscriber")
        return self._enrich(row)

    async def get_by_username(self, username: str) -> Subscriber | None:
        result = await self.db.execute(select(Subscriber).where(Subscriber.username == username))
        return result.scalar_one_or_none()

    async def get_by_activation_code(self, code: str) -> Subscriber | None:
        result = await self.db.execute(
            select(Subscriber).where(Subscriber.activation_code == code)
        )
        return result.scalar_one_or_none()

    def _apply_filters(
        self,
        query,
        count_query,
        status: SubscriberStatus | None,
        q: str | None,
        owner_id: uuid.UUID | None,
    ):
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
        if owner_id is not None:
            # Scoping de reseller: solo los suscriptores que creo. Un reseller no
            # llega ni a saber que existen los ajenos.
            query = query.where(Subscriber.created_by == owner_id)
            count_query = count_query.where(Subscriber.created_by == owner_id)
        return query, count_query

    async def list_subscribers(
        self,
        page: int = 1,
        page_size: int = 50,
        status: SubscriberStatus | None = None,
        q: str | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> tuple[list[Subscriber], int]:
        query = self._enriched_select()
        count_query = select(func.count()).select_from(Subscriber)
        query, count_query = self._apply_filters(query, count_query, status, q, owner_id)
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
        rows = (await self.db.execute(query)).all()
        total = (await self.db.execute(count_query)).scalar_one()
        return [self._enrich(r) for r in rows], total

    async def list_for_export(
        self,
        status: SubscriberStatus | None = None,
        q: str | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> list[Subscriber]:
        """Todos los suscriptores (sin paginar) que casan con los filtros y el
        scoping, para el export CSV. Mismo enriquecimiento y mismo orden que el
        listado."""
        query = self._enriched_select()
        count_query = select(func.count()).select_from(Subscriber)
        query, _ = self._apply_filters(query, count_query, status, q, owner_id)
        query = query.order_by(Subscriber.created_at.desc(), Subscriber.id)
        rows = (await self.db.execute(query)).all()
        return [self._enrich(r) for r in rows]

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

    async def update(
        self, sub_id: uuid.UUID, data: SubscriberUpdate, owner_id: uuid.UUID | None = None
    ) -> Subscriber:
        sub = await self.get_by_id(sub_id, owner_id=owner_id)
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(sub, field, value)
        await self.db.flush()
        return sub

    async def set_password(
        self, sub_id: uuid.UUID, new_password: str, owner_id: uuid.UUID | None = None
    ) -> None:
        sub = await self.get_by_id(sub_id, owner_id=owner_id)
        sub.password_hash = hash_password(new_password)
        await self.db.flush()

    async def set_status(
        self, sub_id: uuid.UUID, status: SubscriberStatus, owner_id: uuid.UUID | None = None
    ) -> Subscriber:
        sub = await self.get_by_id(sub_id, owner_id=owner_id)
        sub.status = status
        await self.db.flush()
        return sub

    async def delete(self, sub_id: uuid.UUID, owner_id: uuid.UUID | None = None) -> None:
        sub = await self.get_by_id(sub_id, owner_id=owner_id)
        await self.db.delete(sub)
        await self.db.flush()

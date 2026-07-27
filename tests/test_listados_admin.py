"""Listados del panel de administracion: orden estable, busqueda y reposicion
de contrasena por un admin.

Tres defectos que cubren estos tests:
  1. Paginar sin ORDER BY: offset/limit sobre un select sin orden no garantiza
     nada en Postgres, asi que una fila puede aparecer dos veces en la pagina 2
     y no salir nunca en la 1.
  2. Los listados no tenian busqueda por texto (ni filtros de rol/activo en
     usuarios), asi que el buscador del panel solo filtraba la pagina cargada.
  3. Un admin no podia reponer la contrasena de otro usuario.
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.core.security import hash_password, verify_password
from app.models.audit import AuditLog
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.user import User, UserRole
from app.services.subscriber_service import SubscriberService, escape_like
from app.services.user_service import UserService

pytestmark = pytest.mark.asyncio

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sub(username: str, *, created_at: datetime = TS, **kw) -> Subscriber:
    kw.setdefault("password_hash", "x")
    return Subscriber(username=username, created_at=created_at, updated_at=created_at, **kw)


def _user(username: str, *, created_at: datetime = TS, **kw) -> User:
    kw.setdefault("password_hash", "x")
    kw.setdefault("email", f"{username}@mail.test")
    return User(username=username, created_at=created_at, updated_at=created_at, **kw)


async def _paginar_todo(listar, total_esperado: int, page_size: int) -> list[uuid.UUID]:
    """Recorre TODAS las paginas y devuelve los ids en el orden en que salieron."""
    vistos: list[uuid.UUID] = []
    paginas = (total_esperado + page_size - 1) // page_size
    for page in range(1, paginas + 1):
        rows, total = await listar(page=page, page_size=page_size)
        assert total == total_esperado
        vistos.extend(r.id for r in rows)
    return vistos


def _app_como(actor: User, db):
    """Monta la app con `actor` autenticado. Se sobreescribe get_current_user,
    NO require_admin, para que la comprobacion de rol se ejecute de verdad."""
    from app.main import app
    from app.database import get_db
    from app.core.dependencies import get_current_user

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: actor
    return app


# ── 1. Orden determinista y estable ──────────────────────────────────────────

async def test_paginar_subscribers_con_created_at_identico_no_repite_ni_pierde(db_session):
    """EL test del arreglo: 12 filas con el MISMO created_at. Sin desempate por
    una columna unica el orden entre paginas no esta definido y el recorrido
    puede duplicar filas y saltarse otras."""
    db_session.add_all([_sub(f"sub_{i:02d}") for i in range(12)])
    await db_session.commit()
    svc = SubscriberService(db_session)

    vistos = await _paginar_todo(svc.list_subscribers, total_esperado=12, page_size=5)

    assert len(vistos) == 12
    assert len(set(vistos)) == 12, "una fila salio en dos paginas distintas"
    # Con created_at empatado el desempate es el id: el recorrido completo tiene
    # que ser exactamente el conjunto ordenado por id. Los ids son uuid4, asi que
    # este orden no coincide con el de insercion — si el ORDER BY no estuviera,
    # la asercion fallaria en lugar de pasar por casualidad.
    todos = (await db_session.execute(select(Subscriber.id))).scalars().all()
    assert vistos == sorted(todos)


async def test_paginar_users_con_created_at_identico_no_repite_ni_pierde(db_session):
    db_session.add_all([_user(f"user_{i:02d}") for i in range(12)])
    await db_session.commit()
    svc = UserService(db_session)

    vistos = await _paginar_todo(svc.list_users, total_esperado=12, page_size=5)

    assert len(vistos) == 12
    assert len(set(vistos)) == 12, "una fila salio en dos paginas distintas"
    todos = (await db_session.execute(select(User.id))).scalars().all()
    assert vistos == sorted(todos)


async def test_el_orden_se_repite_entre_consultas_identicas(db_session):
    """Estable = la misma consulta devuelve lo mismo cada vez, tambien despues
    de escribir en la tabla (que es cuando el orden fisico se mueve)."""
    db_session.add_all([_sub(f"sub_{i:02d}") for i in range(9)])
    await db_session.commit()
    svc = SubscriberService(db_session)

    primera, _ = await svc.list_subscribers(page=2, page_size=3)
    ids_primera = [r.id for r in primera]

    # Escribir y borrar mueve las filas de sitio en el heap de Postgres.
    ruido = _sub("ruido", created_at=TS)
    db_session.add(ruido)
    await db_session.commit()
    await db_session.delete(ruido)
    await db_session.commit()

    segunda, _ = await svc.list_subscribers(page=2, page_size=3)
    assert [r.id for r in segunda] == ids_primera


async def test_orden_por_created_at_descendente(db_session):
    """Con created_at distintos manda la fecha: el mas reciente primero."""
    db_session.add_all([
        _sub("viejo", created_at=TS - timedelta(days=2)),
        _sub("nuevo", created_at=TS),
        _sub("medio", created_at=TS - timedelta(days=1)),
    ])
    await db_session.commit()

    rows, _ = await SubscriberService(db_session).list_subscribers()
    assert [r.username for r in rows] == ["nuevo", "medio", "viejo"]

    db_session.add_all([
        _user("u_viejo", created_at=TS - timedelta(days=2)),
        _user("u_nuevo", created_at=TS),
    ])
    await db_session.commit()
    rows, _ = await UserService(db_session).list_users()
    assert [r.username for r in rows] == ["u_nuevo", "u_viejo"]


# ── 2. Busqueda por texto ────────────────────────────────────────────────────

@pytest.fixture
def _q_subs():
    return [
        _sub("ana.perez", full_name="Ana Perez", email="ana@mail.test", id_cedula="1712345678"),
        _sub("bgomez", full_name="Roberto Gomez", email="bob@otro.test", id_cedula="0999999999"),
        _sub("cvacio"),  # full_name/email/id_cedula a NULL: ILIKE sobre NULL no debe romper
    ]


@pytest.mark.parametrize("termino,esperado", [
    ("ana.perez", "ana.perez"),      # username
    ("Roberto Gomez", "bgomez"),     # full_name
    ("ana@mail.test", "ana.perez"),  # email
    ("1712345678", "ana.perez"),     # id_cedula
])
async def test_busqueda_subscribers_por_cada_campo(db_session, _q_subs, termino, esperado):
    db_session.add_all(_q_subs)
    await db_session.commit()
    rows, total = await SubscriberService(db_session).list_subscribers(q=termino)
    assert total == 1 and rows[0].username == esperado


@pytest.mark.parametrize("termino", ["ROBERTO", "roberto", "RoBeRtO gOmEz"])
async def test_busqueda_subscribers_insensible_a_mayusculas(db_session, _q_subs, termino):
    db_session.add_all(_q_subs)
    await db_session.commit()
    rows, total = await SubscriberService(db_session).list_subscribers(q=termino)
    assert total == 1 and rows[0].username == "bgomez"


async def test_busqueda_subscribers_parcial_y_sin_resultados(db_session, _q_subs):
    db_session.add_all(_q_subs)
    await db_session.commit()
    svc = SubscriberService(db_session)
    _, total = await svc.list_subscribers(q="mail.test")   # subcadena del email
    assert total == 1
    _, total = await svc.list_subscribers(q="no-existe-nadie")
    assert total == 0


async def test_comodines_de_like_se_tratan_como_literales(db_session):
    """Un `%` o un `_` tecleados por el admin son texto a buscar, no comodines.
    Sin escapar, q='%' devolveria la tabla entera y q='_' tambien."""
    db_session.add_all([
        _sub("cliente_a", full_name="Descuento 100% Socio"),
        _sub("clienteb", full_name="Normal"),
        _sub("clientec", full_name="Otro"),
    ])
    await db_session.commit()
    svc = SubscriberService(db_session)

    rows, total = await svc.list_subscribers(q="%")
    assert total == 1 and rows[0].username == "cliente_a", "el % actuo como comodin"

    rows, total = await svc.list_subscribers(q="_")
    assert total == 1 and rows[0].username == "cliente_a", "el _ actuo como comodin"

    _, total = await svc.list_subscribers(q="100% Socio")
    assert total == 1

    # La barra de escape tambien es literal: no arrastra al caracter siguiente.
    _, total = await svc.list_subscribers(q="\\")
    assert total == 0


def test_escape_like_neutraliza_los_tres_caracteres():
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("c:\\ruta") == "c:\\\\ruta"
    assert escape_like("normal") == "normal"


async def test_busqueda_y_filtro_de_estado_se_combinan(db_session):
    db_session.add_all([
        _sub("ana.activa", full_name="Ana Uno", status=SubscriberStatus.active),
        _sub("ana.suspendida", full_name="Ana Dos", status=SubscriberStatus.suspended),
        _sub("otro", full_name="Pedro", status=SubscriberStatus.active),
    ])
    await db_session.commit()
    svc = SubscriberService(db_session)

    _, total = await svc.list_subscribers(q="Ana")
    assert total == 2
    rows, total = await svc.list_subscribers(q="Ana", status=SubscriberStatus.active)
    assert total == 1 and rows[0].username == "ana.activa"


async def test_total_refleja_la_busqueda_no_la_tabla_entera(db_session):
    """El total es lo que pagina el panel: si contara la tabla entera pintaria
    paginas vacias."""
    db_session.add_all([_sub(f"sub_{i:02d}", full_name="Ana Perez" if i < 3 else "Otro")
                        for i in range(10)])
    await db_session.commit()
    rows, total = await SubscriberService(db_session).list_subscribers(q="ana", page_size=2)
    assert total == 3 and len(rows) == 2


# ── 2b. Busqueda y filtros en usuarios ───────────────────────────────────────

@pytest.fixture
def _q_users():
    return [
        _user("ana.admin", full_name="Ana Perez", email="ana@corp.test",
              role=UserRole.admin, is_active=True),
        _user("ana.reseller", full_name="Ana Lopez", email="anal@corp.test",
              role=UserRole.reseller, is_active=True),
        _user("ana.baja", full_name="Ana Ruiz", email="anar@corp.test",
              role=UserRole.reseller, is_active=False),
        _user("bruno", full_name="Bruno Diaz", email="bruno@corp.test",
              role=UserRole.reseller, is_active=True),
    ]


@pytest.mark.parametrize("termino,esperado", [
    ("ana.admin", "ana.admin"),        # username
    ("Ana Lopez", "ana.reseller"),     # full_name
    ("anar@corp.test", "ana.baja"),    # email
    ("BRUNO DIAZ", "bruno"),           # insensible a mayusculas
])
async def test_busqueda_users_por_cada_campo(db_session, _q_users, termino, esperado):
    db_session.add_all(_q_users)
    await db_session.commit()
    rows, total = await UserService(db_session).list_users(q=termino)
    assert total == 1 and rows[0].username == esperado


async def test_comodin_literal_en_busqueda_de_users(db_session):
    db_session.add_all([
        _user("con_guion", full_name="Tiene 50% Descuento"),
        _user("singuion", full_name="Normal"),
    ])
    await db_session.commit()
    svc = UserService(db_session)
    rows, total = await svc.list_users(q="%")
    assert total == 1 and rows[0].username == "con_guion"
    rows, total = await svc.list_users(q="_")
    assert total == 1 and rows[0].username == "con_guion"


async def test_filtros_de_rol_y_activo_en_users(db_session, _q_users):
    db_session.add_all(_q_users)
    await db_session.commit()
    svc = UserService(db_session)

    _, total = await svc.list_users(role=UserRole.admin)
    assert total == 1
    _, total = await svc.list_users(role=UserRole.reseller)
    assert total == 3
    _, total = await svc.list_users(is_active=True)
    assert total == 3
    _, total = await svc.list_users(is_active=False)
    assert total == 1


async def test_filtros_de_users_combinados_con_la_busqueda(db_session, _q_users):
    db_session.add_all(_q_users)
    await db_session.commit()
    svc = UserService(db_session)

    _, total = await svc.list_users(q="Ana")
    assert total == 3
    rows, total = await svc.list_users(q="Ana", role=UserRole.reseller)
    assert total == 2
    rows, total = await svc.list_users(q="Ana", role=UserRole.reseller, is_active=False)
    assert total == 1 and rows[0].username == "ana.baja"
    # is_active=False es un filtro real, no un "sin filtro" por ser falsy.
    _, total = await svc.list_users(is_active=False, role=UserRole.admin)
    assert total == 0


# ── 3. Reposicion de contrasena por un admin ─────────────────────────────────

async def test_admin_repone_la_contrasena_de_otro_usuario(db_session, redis_client):
    """Funciona, queda en auditoria y el usuario entra con la nueva clave."""
    admin = _user("admin_repo", role=UserRole.admin, is_active=True,
                  password_hash=hash_password("AdminPass123!"))
    victima = _user("reseller_olvidadizo", role=UserRole.reseller, is_active=True,
                    password_hash=hash_password("ClaveVieja123!"))
    db_session.add_all([admin, victima])
    await db_session.commit()

    app = _app_como(admin, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/api/v1/users/{victima.id}/set-password",
                             json={"new_password": "ClaveNueva123!"})
            assert r.status_code == 200, r.text
            assert r.json()["success"] is True
    finally:
        app.dependency_overrides.clear()

    await db_session.refresh(victima)
    assert verify_password("ClaveNueva123!", victima.password_hash)
    assert not verify_password("ClaveVieja123!", victima.password_hash)

    # Queda registrado en auditoria, con actor y objetivo.
    entradas = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "user.password_change"))).scalars().all()
    assert len(entradas) == 1
    assert entradas[0].actor_username == "admin_repo"
    assert entradas[0].target_type == "user"
    assert entradas[0].target_id == str(victima.id)

    # Y el usuario puede autenticarse de verdad con la nueva.
    from app.services.auth_service import AuthService
    tokens = await AuthService(db_session, redis_client).login(
        "reseller_olvidadizo", "ClaveNueva123!", ip="1.1.1.1")
    assert tokens.access_token


async def test_reponer_contrasena_exige_rol_admin(db_session):
    reseller = _user("reseller_curioso", role=UserRole.reseller, is_active=True,
                     password_hash=hash_password("Reseller123!"))
    victima = _user("victima", role=UserRole.reseller, is_active=True,
                    password_hash=hash_password("ClaveVieja123!"))
    db_session.add_all([reseller, victima])
    await db_session.commit()

    app = _app_como(reseller, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/api/v1/users/{victima.id}/set-password",
                             json={"new_password": "ClaveNueva123!"})
            assert r.status_code == 403, r.text
    finally:
        app.dependency_overrides.clear()

    await db_session.refresh(victima)
    assert verify_password("ClaveVieja123!", victima.password_hash), "la clave cambio pese al 403"
    assert (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "user.password_change"))).scalars().all() == []


async def test_reponer_contrasena_valida_longitud_y_usuario_inexistente(db_session):
    admin = _user("admin_val", role=UserRole.admin, is_active=True,
                  password_hash=hash_password("AdminPass123!"))
    db_session.add(admin)
    await db_session.commit()

    app = _app_como(admin, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/api/v1/users/{uuid.uuid4()}/set-password",
                             json={"new_password": "corta"})
            assert r.status_code == 422, r.text  # min_length=8, igual que UserCreate

            r = await c.post(f"/api/v1/users/{uuid.uuid4()}/set-password",
                             json={"new_password": "ClaveNueva123!"})
            assert r.status_code == 404, r.text
    finally:
        app.dependency_overrides.clear()


# ── 4. Compatibilidad: sin parametros nuevos, mismo comportamiento ───────────

async def test_llamar_sin_parametros_nuevos_devuelve_todo(db_session):
    """Los parametros nuevos son opcionales: omitirlos no filtra nada."""
    db_session.add_all([_sub(f"sub_{i:02d}") for i in range(7)])
    db_session.add_all([
        _user("u_admin", role=UserRole.admin, is_active=True),
        _user("u_reseller", role=UserRole.reseller, is_active=True),
        _user("u_baja", role=UserRole.reseller, is_active=False),
    ])
    await db_session.commit()

    rows, total = await SubscriberService(db_session).list_subscribers()
    assert total == 7 and len(rows) == 7

    # Sin role/is_active salen TAMBIEN los inactivos: no se cuela un filtro nuevo.
    rows, total = await UserService(db_session).list_users()
    assert total == 3 and len(rows) == 3
    assert {r.username for r in rows} == {"u_admin", "u_reseller", "u_baja"}

    # q vacio o solo espacios se ignora, no busca la cadena vacia.
    for vacio in ("", "   ", None):
        _, total = await SubscriberService(db_session).list_subscribers(q=vacio)
        assert total == 7
        _, total = await UserService(db_session).list_users(q=vacio)
        assert total == 3


async def test_endpoints_http_sin_parametros_nuevos_siguen_respondiendo(db_session):
    """El contrato HTTP no cambia: mismas claves, mismos valores de paginacion."""
    admin = _user("admin_compat", role=UserRole.admin, is_active=True,
                  password_hash=hash_password("AdminPass123!"))
    db_session.add(admin)
    db_session.add_all([_sub(f"sub_{i:02d}") for i in range(4)])
    await db_session.commit()

    app = _app_como(admin, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/subscribers")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total"] == 4 and body["page"] == 1
            assert body["page_size"] == 50 and body["pages"] == 1
            assert len(body["data"]) == 4

            r = await c.get("/api/v1/users")
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 1  # solo el admin
    finally:
        app.dependency_overrides.clear()


async def test_parametros_nuevos_por_http(db_session):
    admin = _user("admin_http", role=UserRole.admin, is_active=True,
                  password_hash=hash_password("AdminPass123!"))
    db_session.add(admin)
    db_session.add_all([
        _sub("ana.perez", full_name="Ana Perez", id_cedula="1712345678"),
        _sub("bgomez", full_name="Roberto Gomez"),
    ])
    db_session.add_all([
        _user("ana.reseller", full_name="Ana Lopez", role=UserRole.reseller, is_active=False),
    ])
    await db_session.commit()

    app = _app_como(admin, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/subscribers", params={"q": "1712345678"})
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 1

            r = await c.get("/api/v1/subscribers", params={"q": "%"})
            assert r.json()["total"] == 0  # literal, no comodin

            r = await c.get("/api/v1/users",
                            params={"q": "ana", "role": "reseller", "is_active": "false"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total"] == 1 and body["data"][0]["username"] == "ana.reseller"
    finally:
        app.dependency_overrides.clear()

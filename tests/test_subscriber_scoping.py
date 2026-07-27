"""Scoping por reseller + enriquecimiento del listado + export CSV.

El test que importa es el de AISLAMIENTO: un reseller no puede listar, ver,
editar NI borrar un suscriptor creado por otro reseller; recibe 404 (como si no
existiera) y no aparece en su listado. El admin ve ambos. Eso prueba que el
agujero (un reseller veia/borraba la cartera entera) esta cerrado.
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import event, select

from app.core.security import hash_password
from app.models.plan import Plan
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.subscription import Subscription
from app.models.user import User, UserRole
from app.services.subscriber_service import SubscriberService

pytestmark = pytest.mark.asyncio

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _user(username: str, *, role=UserRole.reseller, **kw) -> User:
    kw.setdefault("password_hash", hash_password("Passw0rd!"))
    kw.setdefault("email", f"{username}@mail.test")
    kw.setdefault("is_active", True)
    return User(username=username, role=role, **kw)


def _sub(username: str, *, created_by=None, created_at=TS, **kw) -> Subscriber:
    kw.setdefault("password_hash", "x")
    return Subscriber(username=username, created_by=created_by,
                      created_at=created_at, updated_at=created_at, **kw)


def _app_como(actor: User, db):
    """Monta la app con `actor` autenticado. Se sobreescribe get_current_user
    (no require_admin_or_reseller) para que la comprobacion de rol corra de
    verdad, igual que en test_listados_admin."""
    from app.main import app
    from app.database import get_db
    from app.core.dependencies import get_current_user

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: actor
    return app


# ── 1. AISLAMIENTO (el test central) ─────────────────────────────────────────

async def test_reseller_no_ve_ni_toca_suscriptores_ajenos(db_session):
    """A y B son resellers. B crea un suscriptor. A no puede listarlo, verlo,
    editarlo ni borrarlo: 404 en todo y no aparece en su listado. El admin si."""
    admin = _user("admin", role=UserRole.admin)
    a = _user("reseller_a")
    b = _user("reseller_b")
    db_session.add_all([admin, a, b])
    await db_session.flush()

    de_a = _sub("cliente_de_a", created_by=a.id)
    de_b = _sub("cliente_de_b", created_by=b.id)
    db_session.add_all([de_a, de_b])
    await db_session.commit()

    # -- Listado: A solo ve el suyo, B solo el suyo, admin ve ambos.
    async def _list(actor):
        app = _app_como(actor, db_session)
        try:
            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/api/v1/subscribers")
                assert r.status_code == 200, r.text
                return r.json()
        finally:
            app.dependency_overrides.clear()

    body_a = await _list(a)
    assert body_a["total"] == 1
    assert {row["username"] for row in body_a["data"]} == {"cliente_de_a"}

    body_admin = await _list(admin)
    assert body_admin["total"] == 2
    assert {row["username"] for row in body_admin["data"]} == {"cliente_de_a", "cliente_de_b"}

    # -- A intenta operar sobre el suscriptor de B: 404 en get/patch/delete y en
    #    las rutas de suscripciones/dispositivos que pasan por este router.
    app = _app_como(a, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.get(f"/api/v1/subscribers/{de_b.id}")).status_code == 404
            assert (await c.patch(f"/api/v1/subscribers/{de_b.id}",
                                  json={"full_name": "hackeado"})).status_code == 404
            assert (await c.post(f"/api/v1/subscribers/{de_b.id}/set-password",
                                 json={"new_password": "NuevaClave1"})).status_code == 404
            assert (await c.post(f"/api/v1/subscribers/{de_b.id}/suspend")).status_code == 404
            assert (await c.post(f"/api/v1/subscribers/{de_b.id}/activate")).status_code == 404
            assert (await c.get(f"/api/v1/subscribers/{de_b.id}/status")).status_code == 404
            assert (await c.delete(f"/api/v1/subscribers/{de_b.id}")).status_code == 404
    finally:
        app.dependency_overrides.clear()

    # El suscriptor de B sigue intacto: nada se borro ni cambio pese a los 404.
    await db_session.refresh(de_b)
    assert de_b.full_name is None
    assert de_b.status == SubscriberStatus.active

    # -- El admin si accede al de B.
    app = _app_como(admin, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.get(f"/api/v1/subscribers/{de_b.id}")).status_code == 200
    finally:
        app.dependency_overrides.clear()


async def test_scoping_a_nivel_servicio(db_session):
    """Mismo aislamiento comprobado directo en el servicio (sin HTTP)."""
    a, b = _user("svc_a"), _user("svc_b")
    db_session.add_all([a, b])
    await db_session.flush()
    de_a = _sub("svc_cli_a", created_by=a.id)
    de_b = _sub("svc_cli_b", created_by=b.id)
    db_session.add_all([de_a, de_b])
    await db_session.commit()
    svc = SubscriberService(db_session)

    rows, total = await svc.list_subscribers(owner_id=a.id)
    assert total == 1 and rows[0].username == "svc_cli_a"

    # A pide el de B con su scope -> 404 (no distingue de inexistente).
    from app.core.exceptions import NexoraException
    with pytest.raises(NexoraException) as exc:
        await svc.get_by_id(de_b.id, owner_id=a.id)
    assert exc.value.status_code == 404

    # Sin scope (admin) si lo trae.
    got = await svc.get_by_id(de_b.id)
    assert got.username == "svc_cli_b"


# ── 2. create asigna created_by ──────────────────────────────────────────────

async def test_create_asigna_created_by(db_session):
    reseller = _user("dueno")
    db_session.add(reseller)
    await db_session.commit()

    app = _app_como(reseller, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/v1/subscribers",
                             json={"username": "nuevo_cli", "password": "Passw0rd!"})
            assert r.status_code == 201, r.text
            assert r.json()["data"]["created_by"] == str(reseller.id)
    finally:
        app.dependency_overrides.clear()

    sub = (await db_session.execute(
        select(Subscriber).where(Subscriber.username == "nuevo_cli"))).scalar_one()
    assert sub.created_by == reseller.id


# ── 3. Enriquecimiento sin N+1 ───────────────────────────────────────────────

async def test_listado_trae_expiracion_plan_y_owner(db_session):
    reseller = _user("owner_enrich")
    plan = Plan(name="Premium", max_connections=1, max_devices=2, duration_days=30, is_active=True)
    db_session.add_all([reseller, plan])
    await db_session.flush()

    now = datetime.now(timezone.utc)
    con_sub = _sub("con_suscripcion", created_by=reseller.id)
    sin_sub = _sub("sin_suscripcion", created_by=reseller.id)
    db_session.add_all([con_sub, sin_sub])
    await db_session.flush()
    db_session.add(Subscription(
        subscriber_id=con_sub.id, plan_id=plan.id,
        starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=30),
        is_active=True,
    ))
    await db_session.commit()

    rows, _ = await SubscriberService(db_session).list_subscribers()
    by_name = {r.username: r for r in rows}

    enr = by_name["con_suscripcion"]
    assert enr.plan_name == "Premium"
    assert enr.subscription_expires_at is not None
    assert 29 <= enr.days_remaining <= 30
    assert enr.owner_username == "owner_enrich"

    vacio = by_name["sin_suscripcion"]
    assert vacio.plan_name is None
    assert vacio.subscription_expires_at is None
    assert vacio.days_remaining is None
    assert vacio.owner_username == "owner_enrich"


async def test_listado_no_hace_una_consulta_por_fila(db_session):
    """N+1 cerrado: listar N suscriptores CON suscripcion emite un numero de
    consultas constante (data + count), no una por fila."""
    reseller = _user("owner_nmasuno")
    plan = Plan(name="P30", max_connections=1, max_devices=2, duration_days=30, is_active=True)
    db_session.add_all([reseller, plan])
    await db_session.flush()

    now = datetime.now(timezone.utc)
    for i in range(5):
        s = _sub(f"cli_{i}", created_by=reseller.id)
        db_session.add(s)
        await db_session.flush()
        db_session.add(Subscription(
            subscriber_id=s.id, plan_id=plan.id,
            starts_at=now, expires_at=now + timedelta(days=30), is_active=True,
        ))
    await db_session.commit()

    sync_engine = db_session.bind.sync_engine
    contador = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        contador["n"] += 1

    event.listen(sync_engine, "before_cursor_execute", _before)
    try:
        rows, total = await SubscriberService(db_session).list_subscribers()
    finally:
        event.remove(sync_engine, "before_cursor_execute", _before)

    assert total == 5
    assert all(r.plan_name == "P30" for r in rows)
    # Con N+1 serian 2 + 5 = 7. El join lo mantiene constante (data + count).
    assert contador["n"] <= 3, f"posible N+1: {contador['n']} consultas para 5 filas"


# ── 4. CSV respeta el scoping ────────────────────────────────────────────────

async def test_export_csv_respeta_scoping_de_reseller(db_session):
    a, b = _user("csv_a"), _user("csv_b")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add_all([
        _sub("csv_cli_a1", created_by=a.id),
        _sub("csv_cli_a2", created_by=a.id),
        _sub("csv_cli_b1", created_by=b.id),
    ])
    await db_session.commit()

    app = _app_como(a, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/subscribers/export")
            assert r.status_code == 200, r.text
            assert r.headers["content-type"].startswith("text/csv")
            assert "attachment" in r.headers["content-disposition"]
            body = r.text
            assert "csv_cli_a1" in body and "csv_cli_a2" in body
            assert "csv_cli_b1" not in body, "un reseller exporto un suscriptor ajeno"
            # cabecera + 2 filas (sin contar la linea final vacia)
            filas = [ln for ln in body.splitlines() if ln.strip()]
            assert len(filas) == 3
    finally:
        app.dependency_overrides.clear()

    # El admin exporta todos.
    app = _app_como(_user("csv_admin", role=UserRole.admin), db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/subscribers/export")
            assert r.status_code == 200, r.text
            body = r.text
            assert all(u in body for u in ("csv_cli_a1", "csv_cli_a2", "csv_cli_b1"))
    finally:
        app.dependency_overrides.clear()


async def test_export_respeta_filtros_q_y_status(db_session):
    a = _user("csv_filtros")
    db_session.add(a)
    await db_session.flush()
    db_session.add_all([
        _sub("filtra_ana", full_name="Ana Perez", created_by=a.id, status=SubscriberStatus.active),
        _sub("filtra_bob", full_name="Bob Ruiz", created_by=a.id, status=SubscriberStatus.suspended),
    ])
    await db_session.commit()

    app = _app_como(a, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/subscribers/export", params={"q": "Ana"})
            assert "filtra_ana" in r.text and "filtra_bob" not in r.text
            r = await c.get("/api/v1/subscribers/export", params={"status": "suspended"})
            assert "filtra_bob" in r.text and "filtra_ana" not in r.text
    finally:
        app.dependency_overrides.clear()


# ── 5. Compatibilidad: el admin sin parametros nuevos ve lo de siempre ────────

async def test_admin_sin_scoping_ve_todo_como_antes(db_session):
    """Sin owner_id el servicio no filtra por propiedad: mismo comportamiento
    que antes del scoping."""
    a, b = _user("compat_a"), _user("compat_b")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add_all([
        _sub("compat_1", created_by=a.id),
        _sub("compat_2", created_by=b.id),
        _sub("compat_3", created_by=None),  # legado, sin owner
    ])
    await db_session.commit()

    rows, total = await SubscriberService(db_session).list_subscribers()
    assert total == 3 and {r.username for r in rows} == {"compat_1", "compat_2", "compat_3"}

    admin = _user("compat_admin", role=UserRole.admin)
    app = _app_como(admin, db_session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/subscribers")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total"] == 3 and body["page"] == 1
            assert body["page_size"] == 50 and body["pages"] == 1
    finally:
        app.dependency_overrides.clear()

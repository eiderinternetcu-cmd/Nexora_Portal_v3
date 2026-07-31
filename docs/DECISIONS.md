# DECISIONS.md — Decisiones Técnicas Nexora
_Last updated: 2026-05-18_

---

## DECISIÓN PRINCIPAL: Nexora Core = FastAPI + PostgreSQL + Redis

**Fecha**: 2026-05-17
**Estado**: FIRME — no revertir

### Motivación
Nexora es una plataforma OTT moderna (web, Android TV, iOS). El nuevo core se construyó desde cero en Python para tener control total sobre autenticación, concurrencia, dispositivos y playback Flussonic sin depender de stacks legacy.

### Regla
> **No construir nuevo código crítico fuera del stack aprobado.**
> El portal legacy (PHP) se retiene temporalmente solo para migración de datos — no para lógica nueva.

---

## STACK APROBADO

| Decisión | Elegido | Descartado | Razón |
|----------|---------|------------|-------|
| Framework | FastAPI 0.115+ | PHP 8, Express | Async nativo, tipado, OpenAPI |
| Base de datos | PostgreSQL 16 | MySQL, SQLite | UUID nativo, JSONB, triggers |
| Cache/Sessions | Redis 7 | Memcached, DB sessions | TTL nativo, ZSET para concurrencia |
| ORM | SQLAlchemy 2.x async | Tortoise, raw SQL | Ecosystem maduro, type hints |
| DB driver | psycopg3 (psycopg[binary]) | asyncpg | asyncpg requiere compilación Rust en Windows; psycopg3 tiene wheels binarios |
| JWT | PyJWT[crypto] | python-jose | python-jose requiere Rust/link.exe; PyJWT usa cryptography con wheels |
| Password | Argon2id (passlib+argon2-cffi) | bcrypt, PBKDF2 | Winner de Password Hashing Competition |
| Migraciones | Alembic async | Django migrations | Native SQLAlchemy, control fino |
| Runtime Python | 3.12 (Docker), 3.14 (dev local) | 3.11, 3.10 | Velocidad, type hints modernos |

---

## ARGON2ID — PARÁMETROS

```python
pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__memory_cost=65536,   # 64 MB
    argon2__time_cost=3,
    argon2__parallelism=4,
    argon2__type="ID",           # Argon2id (no Argon2i ni Argon2d)
)
```

Estos parámetros son de grado producción según OWASP 2024. No bajar sin revisión de seguridad.

---

## JWT — ESTRUCTURA

```python
# Access token payload
{
    "sub": "user_uuid",
    "role": "admin|reseller",
    "type": "access",
    "jti": "uuid4",
    "iat": unix_timestamp,
    "exp": unix_timestamp + 30min
}

# Refresh token payload
{
    "sub": "user_uuid",
    "role": "admin|reseller",
    "type": "refresh",
    "jti": "uuid4",
    "iat": unix_timestamp,
    "exp": unix_timestamp + 30d
}
```

El `jti` es la clave en Redis. En blacklist: `nexora:blacklist:{jti}`.

---

## PSYCOPG3 — NOTAS IMPORTANTES

- URL: `postgresql+psycopg://` (NO `postgresql+asyncpg://`)
- `connect_args={"autocommit": False}` requerido en `create_async_engine`
- Alembic usa `NullPool` en migraciones (no connection pool)
- Compatible con Python 3.12 y 3.14 (wheels binarios disponibles)

---

## PORTAL LEGACY — RETENCIÓN TEMPORAL

El portal PHP legacy se mantiene activo únicamente para:
- Migración gradual de datos existentes a PostgreSQL
- Referencia de esquemas de suscriptores heredados

No se escribe lógica nueva en PHP. Todo flujo nuevo pasa por el Modern Client API (`/api/client/*`).

---

## SEPARACIÓN DE DOMINIOS

```
/api/client/    → app moderna (web, Android TV, iOS) — Modern Client API
/api/admin/     → solo role=admin, gestión del sistema
/api/stb/       → autenticación de dispositivo + callback Flussonic backend-auth
/api/v1/        → compatibilidad admin/reseller legacy (sin flujo nuevo aquí)
/internal/      → inter-service, no expuesto públicamente
```

---

## CONCURRENCIA IPTV — DISEÑO

```
Redis ZSET: nexora:active_conns:{subscriber_id}
  score = unix_timestamp de expiración
  member = device_id

Regla:
  1. Limpiar miembros con score < NOW()
  2. Contar miembros restantes
  3. Si count >= plan.max_connections → rechazar
  4. ZADD con score = NOW() + 180s
  5. Heartbeat renueva el score cada 60s
  6. Sin heartbeat por 180s → expira automático
```

---

## NO HACER (REGLAS PROHIBIDAS)

- ❌ No usar MySQL en módulos nuevos
- ❌ No escribir lógica de negocio nueva en PHP
- ❌ No usar python-jose (Rust dependency)
- ❌ No usar asyncpg (Rust compilation)
- ❌ No empezar UI en este repo — está en `e:/WEBSITE/nexora_app`
- ❌ No hacer migraciones directo en producción — siempre entorno clonado primero
- ❌ No commitear `.env` real al repositorio
- ❌ No exponer credenciales Flussonic en ninguna respuesta de API
- ❌ No hacer proxy de video desde Nexora — el cliente reproduce directo desde Flussonic
- ❌ No exponer `stream_key` al cliente — solo `channel_key`

# Nexora API

FastAPI middleware para IPTV — gestión de suscriptores, dispositivos, planes y autenticación STB.

## Stack
- **FastAPI** 0.115+ / Python 3.12+
- **PostgreSQL** 16 (SQLAlchemy 2.x async + psycopg3)
- **Redis** 7 (sesiones, rate limit, heartbeat TTL)
- **Argon2id** (passwords) + **PyJWT** (tokens)
- **Alembic** (migraciones async)

## Inicio rápido

```bash
cp .env.example .env
# editar .env con tus valores

docker-compose up -d
docker-compose exec api alembic upgrade head
docker-compose exec api python scripts/create_admin.py
```

API disponible en: http://localhost:8000  
Docs: http://localhost:8000/docs

## Documentación

| Archivo | Contenido |
|---------|-----------|
| PROJECT_STATUS.md | Estado actual, archivos, endpoints |
| ARCHITECTURE.md | Stack, estructura, flujos, Redis keys |
| TODO_NEXT.md | Próximos pasos exactos (Fase 2) |
| SETUP.md | Variables .env, comandos, curl tests |
| DECISIONS.md | Decisiones técnicas y reglas del proyecto |

## Fase actual: 1 completada — Fase 2 en progreso

Ver TODO_NEXT.md para el plan exacto.

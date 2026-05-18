# SETUP.md — Nexora API Setup Guide
_Last updated: 2026-05-17_

---

## Variables .env requeridas

Crear `nexora_api/.env` con:

```env
# App
APP_NAME=NexoraAPI
APP_ENV=development
DEBUG=true
SECRET_KEY=genera-una-clave-aleatoria-de-64-caracteres-minimo-aqui

# JWT
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=30

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=nexora
POSTGRES_USER=nexora
POSTGRES_PASSWORD=nexora_secret

# Redis
REDIS_HOST=localhost
REDIS_PORT=6380
REDIS_PASSWORD=
REDIS_DB=0

# Security
MAX_LOGIN_ATTEMPTS=5
LOGIN_LOCKOUT_MINUTES=15
RATE_LIMIT_PER_MINUTE=60

# IPTV
HEARTBEAT_TTL_SECONDS=180

# STB Portal (PHP legacy)
STB_PORTAL_URL=http://172.27.99.151/nexora_portal
```

> NOTA: En Docker Compose, POSTGRES_HOST=postgres y REDIS_HOST=redis (nombres de servicios).
> En local (venv directo), usar localhost con los puertos mapeados (5433, 6380).

---

## Instalar dependencias

### Con Docker (recomendado)
```bash
docker-compose build
```

### Con venv local (Python 3.12+)
```bash
# Windows - crear venv
C:\Users\EIGO_\.local\bin\python3.14.exe -m venv .venv

# Activar
.\.venv\Scripts\Activate.ps1

# Instalar
pip install --no-cache-dir --prefer-binary -r requirements.txt
```

---

## Levantar servicios

### Docker Compose (completo)
```bash
# Levantar todo
docker-compose up -d

# Ver logs
docker-compose logs -f api

# Solo bases de datos (si se corre API en local)
docker-compose up -d postgres redis
```

### FastAPI en local (desarrollo)
```bash
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

### Servicios Docker Compose
| Servicio | Host:Puerto | Credenciales |
|----------|-------------|-------------|
| PostgreSQL | localhost:5433 | nexora / nexora_secret |
| Redis | localhost:6380 | sin password |
| API | localhost:8000 | — |

---

## Comandos Alembic

```bash
# Con Docker
docker-compose exec api alembic upgrade head

# En local (venv activado, desde nexora_api/)
alembic upgrade head

# Ver historial
alembic history

# Ver estado actual
alembic current

# Crear nueva migración
alembic revision --autogenerate -m "nombre_de_la_migracion"

# Rollback una migración
alembic downgrade -1

# Rollback completo
alembic downgrade base
```

---

## Crear primer admin

```bash
# Con Docker
docker-compose exec api python scripts/create_admin.py

# En local
python scripts/create_admin.py
```

---

## Comandos curl de prueba

### Health check
```bash
curl http://localhost:8000/health
# Esperado: {"status":"ok","service":"nexora-api","version":"1.0.0","redis":"ok"}
```

### Login
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"tu_password"}'
# Respuesta: {"access_token":"...","refresh_token":"...","expires_in":1800}
```

### Ver usuario actual
```bash
TOKEN="eyJ..."
curl http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"
```

### Refresh token
```bash
curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"eyJ..."}'
```

### Logout
```bash
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"eyJ..."}'
```

### Crear suscriptor
```bash
curl -X POST http://localhost:8000/api/v1/subscribers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "juan123",
    "password": "password123",
    "full_name": "Juan Pérez",
    "email": "juan@example.com",
    "phone": "0991234567"
  }'
```

### Registrar dispositivo
```bash
curl -X POST http://localhost:8000/api/v1/devices/register/{sub_id} \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "AA:BB:CC:DD:EE:FF",
    "mac_address": "AA:BB:CC:DD:EE:FF",
    "device_type": "android_tv",
    "model": "Fire TV Stick",
    "brand": "Amazon"
  }'
```

### Heartbeat
```bash
curl -X POST http://localhost:8000/api/v1/devices/heartbeat \
  -H "Content-Type: application/json" \
  -d '{"device_id":"AA:BB:CC:DD:EE:FF","subscriber_id":"uuid-del-suscriptor"}'
```

### Crear plan
```bash
curl -X POST http://localhost:8000/api/v1/plans \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Plan Básico 30 días",
    "max_connections": 1,
    "max_devices": 2,
    "duration_days": 30,
    "price": 9.99
  }'
```

### Test rate limit (debe dar 429 en el intento 11)
```bash
for i in {1..12}; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"x","password":"y"}'
done
```

---

## Swagger UI

```
http://localhost:8000/docs
http://localhost:8000/redoc
```

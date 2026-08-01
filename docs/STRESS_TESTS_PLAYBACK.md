# Stress tests de playback (P1.1 · Fase 4 · Bloque 3)

Primera ejecución real de los escenarios que estaban escritos como plan desde la
Fase 4. Fecha: 2026-07-31. Rama `feat/client-api-blockers`, stack local
(`nexora_api:8000`, `nexora_postgres:5433`, `nexora_redis:6380`).

Los scripts viven en `scripts/stress/` y están hechos para volver a correrse
antes de cada despliegue — ver `scripts/stress/README.md`. Ninguna contraseña
está escrita en ningún fichero: se pasan por entorno (`NX_USER`, `NX_PASS`, …).

---

## Resumen ejecutivo

Se encontraron **5 problemas**, uno de ellos un bypass completo del límite de
conexiones concurrentes.

| # | Severidad | Problema |
|---|---|---|
| F1 | **Alta** | `max_connections` es evitable: `heartbeat` → `authorize` entrega token aunque el límite esté lleno. Medido: **10 slots en un plan de 3**. |
| F2 | **Media-alta** | El pool de Redis está en el default de la librería (**100**) y no espera: por encima de 100 operaciones en vuelo lanza `MaxConnectionsError` → 500. |
| F3 | **Media** (ajena) | Deriva ORM↔migraciones: `subscribers.parental_pin_hash` no existe en la BD. **Bloqueó los 5 escenarios end-to-end.** |
| F4 | Baja | `/health` devuelve 500 en la **primera** petición tras reiniciar Redis; luego se recupera solo. |
| F5 | Baja / informativa | No existe ningún endpoint que cierre una conexión. El slot solo se libera por TTL (180 s). |

**Lo que sí aguantó** (con números, más abajo): la atomicidad del script Lua bajo
concurrencia real, el zapping sobre un mismo dispositivo, la caducidad por TTL,
el reinicio de `api` y el reinicio *ordenado* de `redis`.

---

## Aviso sobre lo que NO se pudo ejecutar

**Los 5 escenarios end-to-end sobre HTTP no llegaron a correrse.** A mitad de
sesión, todo endpoint que carga un `Subscriber` empezó a devolver 500 (F3), lo
que incluye `POST /api/client/auth/login` y por tanto cualquier escenario que
empiece por autenticarse.

Los scripts `01`–`05` quedan escritos, revisados y listos; están **sin
ejecutar**. Lo que sí se ejecutó es `conn_service_probe.py`, que ataca el
*mecanismo* (la clase `ConnectionService` real y su Lua real contra el Redis
real) sin pasar por PostgreSQL ni por HTTP. Todos los números de F1, F2 y de la
sección "lo que aguantó" salen de ahí y son reproducibles hoy.

Consecuencia honesta: **F1 está demostrado a nivel de servicio, no a nivel de
petición HTTP.** La cadena de endpoints que lo expone está verificada leyendo el
código (ver F1), pero falta confirmarla con dos `curl` en cuanto la BD vuelva.

Tampoco se ejecutaron:

- el soak largo del escenario 2 (3–6 h del plan; el script tiene `NX_DURATION_S`)
- el escenario 5 en modo `--real` (espera el TTL completo de 180 s)
- un `docker kill -9` de Redis (caída *sucia*). Solo se probó el reinicio
  ordenado. Se descartó porque el Redis es compartido por otros siete workers y
  una caída sucia les habría borrado hasta 60 s de estado (ver F4).

---

## F1 — `max_connections` se puede saltar con un heartbeat · Severidad ALTA

### Qué pasa

`ConnectionService` tiene dos caminos de escritura sobre el ZSET y solo uno
comprueba el límite.

`app/services/connection_service.py:60-67` — el heartbeat hace un `ZADD` pelado,
sin ningún control de `max_connections`:

```python
async def extend_connection(self, subscriber_id, device_id) -> None:
    """Heartbeat renewal — adds or updates the entry with a fresh TTL."""
    key = key_active_connections(str(subscriber_id))
    score = time.time() + self.ttl
    await self.redis.zadd(key, {str(device_id): score})
    await self.redis.expire(key, self.ttl + 60)
```

Y `_OPEN_CONNECTION_LUA` (líneas 28-38) solo evalúa el límite **cuando el miembro
no está ya en el ZSET**:

```lua
if not redis.call('ZSCORE', KEYS[1], ARGV[3]) then
  if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then
    return 0
  end
end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[3])
```

Encadenando las dos: el heartbeat mete al dispositivo en el ZSET, y a partir de
ese momento `ZSCORE` ya no es nil, así que el `authorize` siguiente **se salta la
comprobación entera** y devuelve token de reproducción.

### Medición (sección D/D2 de `conn_service_probe.py`)

```
D — Does heartbeat (extend_connection) respect max_connections?
  [PASS] 4th device is refused by open_connection — opened=False
  [FAIL] heartbeat does NOT let a refused device take a slot
         — zcard after heartbeat=4 (limit=3), count_active=4

D2 — Chain: does a heartbeat-seeded device then WIN an authorize?
  [PASS] baseline — 4th device refused
  [FAIL] after a heartbeat, the SAME refused device must still be refused
         — open_connection returned True, zcard=4 (limit=3)
  [FAIL] ZSET stays within max_connections under repeated abuse
         — zcard=10 with max_connections=3
```

Con `max_connections=3` se llegó a **10 conexiones activas**. El techo real no es
el plan: es `max_devices` (10 para `testuser1`).

### Cómo reproducirlo

```bash
docker exec nexora_api python /app/scripts/stress/conn_service_probe.py
# secciones D y D2
```

End-to-end, cuando la BD funcione (`04_concurrent_devices.mjs` lo automatiza en
su bloque 4b):

1. Ocupar los 3 slots con 3 dispositivos vía `POST /api/client/playback/authorize`.
2. Con un 4º dispositivo: `POST /api/client/playback/authorize` → **409** esperado.
3. Con ese mismo 4º: `POST /api/client/profile/devices/heartbeat`.
4. Repetir el paso 2 → devuelve **200 con token** en vez de 409.

### Por qué es alta

Los dos endpoints son de suscriptor autenticado normal
(`app/api/client/profile.py:120` y `app/api/client/playback.py:154`); el
heartbeat solo comprueba que el dispositivo pertenezca al suscriptor. No hace
falta nada especial: **un cliente que mande su heartbeat antes de pedir
`authorize` obtiene el slot aunque el plan esté lleno.** Es decir, puede estar
ocurriendo sin que nadie ataque nada — el límite de conexiones es el control de
monetización del plan.

### Nota de diseño para quien lo arregle

Saltarse la comprobación cuando el miembro ya existe es *deliberado* y correcto
para `authorize` (renovar el propio slot no debe consumir uno nuevo). El fallo
está en que `extend_connection` puede **crear** miembros. Un heartbeat debería
renovar solo lo que ya existe (p. ej. `ZADD ... XX`, o el mismo Lua con el chequeo
de límite), nunca insertar.

---

## F2 — El pool de Redis se agota a las 100 operaciones y no espera · Severidad MEDIA-ALTA

`app/redis_client.py:12` crea el cliente sin acotar el pool:

```python
_redis = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
```

Con redis-py **8.1.0**, el default efectivo del pool asíncrono es **100**
conexiones (comprobado en el contenedor: `r.connection_pool.max_connections` →
`100`). Y el pool **no encola**: cuando no hay conexión libre lanza
`MaxConnectionsError` inmediatamente, así que la petición muere en 500.

### Medición (sección H)

```
concurrency=  50  errors=   0    10ms
concurrency= 100  errors=   0    55ms
concurrency= 150  errors=  50 ['MaxConnectionsError']  20ms
concurrency= 300  errors= 200 ['MaxConnectionsError']  27ms
```

La relación es exacta: `errores = concurrencia - 100`. Con 1000 operaciones en
vuelo, **900 fallaron** y solo 15 de los 50 suscriptores llegaron a abrir sus
slots.

Cada `authorize` y cada `heartbeat` toca Redis varias veces, así que el techo por
proceso de API está en el orden de ~100 peticiones simultáneas que usen Redis.
El caso realista no es el tráfico de crucero sino la **estampida**: tras un corte
o un reinicio de Redis todos los reproductores reconectan a la vez (ver F4).

### Cómo reproducirlo

```bash
docker exec nexora_api python /app/scripts/stress/conn_service_probe.py   # sección H
```

Mitigación: fijar `max_connections` explícitamente en `from_url` acorde a
workers × concurrencia esperada, y decidir si se prefiere encolar
(`BlockingConnectionPool`) a devolver 500.

---

## F3 — Deriva ORM↔migraciones: bloqueó los escenarios end-to-end · Severidad MEDIA (lane ajena)

A mitad de sesión, todo endpoint que carga un `Subscriber` empezó a devolver 500:

```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedColumn)
column subscribers.parental_pin_hash does not exist
```

`app/models/subscriber.py:34` declara `parental_pin_hash`; la BD está en la
revisión **010** y esa columna la crea la **012**, que existe pero no está
aplicada.

Además el grafo de migraciones tiene ahora **tres heads** — `010`, `012` y `013`,
las tres colgando de `009` (cuatro lanes trabajando en paralelo, documentado a
propósito en la cabecera de `013_epg_programmes.py`). Con varios heads,
`alembic upgrade head` no puede resolverse solo: hay que aplicar cada head o
fusionarlos.

No se tocó: es territorio de otras lanes y hay siete workers escribiendo. Se
deja anotado porque **mientras la BD siga en 010, el login de cliente está
caído** y ningún escenario end-to-end puede correrse.

Verificado que no es un worker recargando la API: reproducible durante ~40
minutos, con la misma traza, y sobreviviendo a un reinicio del contenedor.

Esta es exactamente la clase de fallo que persigue `tests/test_migration_schema_parity.py`
(P1.4) — un esquema construido desde migraciones que no coincide con el ORM. En
un despliegue limpio de producción, que se construye desde migraciones, el login
estaría roto igual.

---

## F4 — `/health` devuelve 500 en la primera petición tras reiniciar Redis · Severidad BAJA

Medido justo después de `docker restart nexora_redis`, sin reiniciar la API:

```
Internal Server Error                                                   <- intento 1
{"status":"ok","service":"nexora-api","version":"2.0.0","redis":"ok"}   <- intento 2
{"status":"ok","service":"nexora-api","version":"2.0.0","redis":"ok"}   <- intento 3
```

El pool se recupera solo, pero la primera petición se pierde. Importa porque
`/health` es justo lo que mira un orquestador o un balanceador: un healthcheck
con `retries: 1` marcaría la API como caída y la reiniciaría en cascada
precisamente cuando Redis acaba de volver. Conviene tolerancia a un fallo
aislado en el healthcheck, o un ping de calentamiento al reconectar.

---

## F5 — Nada libera una conexión antes del TTL · Severidad BAJA / informativa

No existe ningún endpoint de "parar reproducción". Barrido de rutas:

```
app/api/client/playback.py -> POST /authorize
app/api/client/playback.py -> GET  /{channel_id}
app/api/client/profile.py  -> POST /devices/heartbeat
...
(0 coincidencias de stop|close|disconnect|release en app/api/)
```

`close_connection()` solo se llama desde endpoints de **admin**
(`app/api/admin/sessions.py:121,149`, `app/api/admin/subscriptions.py:176`) y
desde la sonda interna de `stream_auth_service.py:641`. Nunca desde el cliente.

Es coherente con un diseño basado en TTL y **no genera slots zombie al hacer
zapping**, porque el miembro del ZSET es el `device_id`, no el canal (verificado:
20 aperturas seguidas → 1 slot). El efecto real es otro: al cambiar de
**aparato** (móvil → televisor), el slot del primero sigue ocupado hasta 180 s.
Con `max_connections=3` un usuario que pruebe tres aparatos seguidos se queda sin
huecos durante tres minutos. Merece al menos una nota de producto.

---

## Lo que aguantó (con números)

Todo lo de abajo se ejecutó de verdad contra el Redis real, con el código real.

### Atomicidad del límite bajo concurrencia real — SIN FALLOS

El script Lua hace limpieza + comprobación + alta en un solo paso servidor, y
resiste. 5 rondas de 12 aperturas **simultáneas** (`asyncio.gather`) de
dispositivos distintos, `max_connections=3`:

```
[PASS] round 1: exactly 3 of 12 concurrent opens granted — granted=3 zcard=3
[PASS] round 2 … 3 … 4 … 5   (idéntico)
```

A mayor escala, 50 suscriptores × 20 aperturas concurrentes:

```
[PASS] no subscriber exceeded max_connections under load — offenders=0 cards_max=3
```

Ni una sola vez se excedió el límite. La carrera que buscaba el escenario 4 **no
existe** por esta vía; el agujero está en el heartbeat (F1), no en el Lua.

### Zapping sobre un mismo dispositivo — SIN SLOTS ZOMBIE

```
[PASS] 20 opens of the SAME device all succeed — 20/20
[PASS] ZSET holds exactly 1 slot after 20 zaps — zcard=1
  20 opens in 12.7ms (0.63ms each)
```

No hay falsos 409 ni rastro acumulado. El sexto canal **no** da 409.

Caso peor documentado: si un cliente rotara su `device_id` en cada zap, quedaría
capado a los 3 slots con un solo espectador viendo. Hoy ningún cliente lo hace,
pero es la variante que convertiría el zapping en un problema.

### Caducidad por TTL — CORRECTA

```
[PASS] while they are alive, a 4th device is refused
[PASS] after expiry a new device gets a slot — opened=True
[PASS] expired members are purged by the open path — raw_zcard_before_open=3 zcard_after=1
```

Matiz medido: los miembros caducados **siguen físicamente en Redis** (`ZCARD`
crudo = 3) hasta que alguna operación toca la clave. Nada los expira de forma
proactiva. No es un fallo — `ZREMRANGEBYSCORE` corre dentro del camino de
apertura y de `count_active` — pero cualquier lectura directa del ZSET que no
pase por el servicio contará de más.

### Higiene de la clave

```
[PASS] open_connection sets a key TTL — ttl=240s
[PASS] close_connection removes the member — zcard=0
  key TTL after closing the last member: -2s (key gone)
```

### Reinicio de `api` — LIMPIO

```
docker restart took 1080ms
healthy after 3520ms total
{"status":"ok","service":"nexora-api","version":"2.0.0","redis":"ok"}
ZSET survived api restart: 1
```

3,5 s hasta responder sano. El estado vive en Redis, así que el ZSET sobrevive
intacto. Los tokens son JWT, de modo que un cliente no necesita volver a
autenticarse (pendiente de confirmar por HTTP, ver aviso).

### Reinicio *ordenado* de `redis` — SIN PÉRDIDA DE DATOS

Configuración: `appendonly no`, `save "60 1"` (snapshot RDB como mucho cada 60 s).

```
DBSIZE antes: 186        LASTSAVE antes: 1785544245
docker restart took 895ms
redis PONG after 1236ms
DBSIZE después: 187      LASTSAVE después: 1785544300
claves sembradas justo antes del reinicio: 2 de 2 sobrevivieron
```

`LASTSAVE` avanzó durante el apagado: `docker restart` manda SIGTERM y Redis
guarda el RDB antes de morir, así que **un reinicio ordenado no pierde nada**,
ni siquiera escrituras de hace un segundo. `authorize` vuelve a funcionar solo.

Lo que esto **no** cubre, y conviene decirlo: una caída *sucia* (OOM, `kill -9`,
corte de luz) sí perdería hasta 60 s de escrituras — tokens, grants y ZSET. Ese
caso no se probó (Redis compartido con otros siete workers).

### Rendimiento del camino de conexión

```
1000 llamadas a open_connection en 317ms  →  ~3150 ops/s
0,63 ms por apertura en el caso secuencial
```

El ZSET no es el cuello de botella; el límite lo pone el pool (F2).

---

## Cómo volver a correr todo esto

```bash
export NX_USER=testuser1
export NX_PASS='...'          # nunca en fichero

docker exec nexora_api python /app/scripts/stress/conn_service_probe.py   # ejecutado ✔
node scripts/stress/01_zapping.mjs                                        # pendiente (F3)
NX_DURATION_S=600 node scripts/stress/02_continuous_playback.mjs          # pendiente (F3)
node scripts/stress/03_restart_resilience.mjs --api                       # pendiente (F3)
node scripts/stress/04_concurrent_devices.mjs 5                           # pendiente (F3)
node scripts/stress/05_heartbeat_timeout.mjs --fast                       # pendiente (F3)
```

`03 --redis` y `03 --all` reinician Redis, que es compartido: úsalos solo cuando
nadie más esté trabajando sobre el stack.

---

## Siguientes pasos sugeridos

1. **F1 primero.** Que `extend_connection` no pueda crear miembros. Añadir un
   test de regresión: heartbeat de un dispositivo sin slot no debe aumentar el
   `ZCARD`.
2. **F2**: fijar `max_connections` del pool de Redis explícitamente y decidir
   entre encolar o fallar.
3. Desbloquear F3 (aplicar `012`/`013` o fusionar heads) y correr los cinco
   scripts end-to-end para confirmar F1 sobre HTTP y cerrar los escenarios que
   hoy quedan sin ejecutar.
4. Meter `conn_service_probe.py` en el pre-deploy: es rápido (~15 s), no depende
   de PostgreSQL y ya detecta F1 y F2.

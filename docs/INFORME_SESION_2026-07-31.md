# Informe de sesión — 2026-07-31

_Rama `feat/client-api-blockers`. Alcance autorizado: **solo local**. No se tocó producción:
ni SSH, ni despliegues, ni flags, ni merges._

Seis frentes en paralelo para cerrar el backlog que no dependía de una ventana de producción.
Todo lo que se afirma aquí está verificado contra el código, la base de datos o los contenedores
en el momento de escribirlo.

---

## 1. El hallazgo que justifica la sesión entera

**`plan_channels` habría dado 500 en producción.**

`migrations/versions/005_plan_channels.py` creaba `uq_plan_channels_plan_channel` con
`op.create_index(..., unique=True)` — un **índice único**. `plan_channel_service.py` ejecuta
`INSERT ... ON CONFLICT ON CONSTRAINT uq_plan_channels_plan_channel`, y Postgres exige una
**constraint** real para esa sintaxis: con un índice lanza `UndefinedObject`.

`plan_channels` es la lista blanca estricta que decide el entitlement — qué canal puede ver quién.
El camino de escritura entero estaba roto.

**Por qué los 345 tests no lo atrapaban:** `tests/conftest.py::db_session` construye el esquema con
`Base.metadata.create_all()` desde el modelo ORM, que sí declara `UniqueConstraint`. La suite
**nunca ejerce el camino de la migración real**. Este es el primer caso encontrado de divergencia
ORM↔migración; estructuralmente no puede ser el último. Anotado como **P1.4** en el roadmap.

**Por qué editar la 005 no bastaba:** producción está en **Alembic 007**, o sea la 005 ya corrió
allí con el índice roto, y Alembic no la reejecuta. Editarla solo arregla bases nuevas. Se añadió
**`008_fix_plan_channels_constraint.py`**, que consulta `pg_constraint`/`pg_indexes` en vez de
asumir el historial y cubre los tres estados posibles (ya tiene constraint → no-op; tiene índice →
`drop_index` + `create_unique_constraint`, en ese orden; no tiene ninguno → crea).

Verificada reproduciendo el bug real: base desechable migrada hasta 007 con el 005 roto, se ejecutó
el `ON CONFLICT` exacto del servicio (falló), se aplicó la 008 (funcionó), incluido el camino de
`DO UPDATE` con conflicto real. Ciclo `downgrade 007` → `upgrade head` limpio.

---

## 2. Dos ítems del roadmap que eran falsos

### El lockout de login ya existía
`AuthService._check_lockout` / `_record_failed_attempt` — contadores por usuario **y** por IP,
respuesta **423** con el TTL restante — llevan tiempo en producción. `docs/ROADMAP.md` lo listaba
como pendiente bajo `NX-AUTH`. Consecuencia práctica: poner el lockout "nuevo" detrás de un flag
en `off` habría **quitado** un control activo al desplegar. Lo que faltaba de verdad:

- **Auditoría de los fallos** (no había ninguna). Ahora `auth.login`, `auth.login_failed` y
  `auth.login_blocked` en `audit_log`, compatible con el trigger append-only de la 007.
- **El lockout por IP se alimenta de `X-Forwarded-For`** (`app/core/dependencies.py:72`), header
  que controla el cliente. No solo es evadible: es **abusable** — falsificando la IP de un tercero
  se le bloquea el panel 15 minutos. Detrás de CGNAT castiga a todos los que comparten salida.
- **El 423 con "Try again in {ttl}s"** le dice al atacante cuándo volver.

Bajo el flag nuevo (`LOGIN_LOCKOUT_ENABLED`, default = camino legacy intacto): contador solo por
usuario, respuesta **401 idéntica** a una credencial mala, y el contador se arma también para
usuarios inexistentes, así que una respuesta bloqueada no prueba nada sobre la cuenta.

### El "bypass" de `tc-mia` probablemente no es un bypass
Análisis completo en **`docs/ANALISIS_BYPASS_TCMIA.md`**. La hipótesis líder: el
`location /stream/tc-mia/` no existía en el vhost probado, la petición cayó al catch-all `/` y el
SPA respondió `try_files … /index.html` → **200 con HTML**, ni un byte de vídeo. Impacto de
**disponibilidad** (los canales de Miami no se ven en esa marca), no de seguridad.

Descartado que falte el `auth_request`: las **cinco** copias versionadas del bloque lo llevan, y dos
commits registran 401 *medido* en tc-mia. Causa estructural: `deploy/transcode/patch_nginx_tc_mia.py`
inserta con `re.search` **solo la primera** coincidencia (un vhost) y su guardián de idempotencia
(`if MARK in conf: sys.exit(0)`) impide replicarla al segundo dominio.

Falta **una sonda de solo lectura en el servidor** para cerrarlo (P0.7 del roadmap).

---

## 3. Entregado en la rama (sin desplegar)

| Frente | Qué |
|---|---|
| **P0.5 — alerting de nodos** | El monitor prueba **HLS firmado a través del edge** en vez de llamar a la API de gestión del origen, que el contenedor `api` no alcanza (timeout: solo nginx tiene ruta). Señal end-to-end real: ejercita gate + nodo + stream. Flags `NODE_PROBE_*`, default = comportamiento actual |
| **NX-AUTH** | Lockout endurecido + auditoría de login (arriba). Flag `LOGIN_LOCKOUT_*`, default = legacy |
| **Alembic 008** | Repara la constraint de `plan_channels` |
| **Panel admin — escrituras** | Verificadas de punta a punta: crear suscriptor, lista blanca de canales (los 4 endpoints), revocar sesión, y el scoping por reseller (ve 0 de 4 ajenos, 404 sin filtrar existencia, `plan_channels` admin-only → 403) |
| **Documentación** | 7 `.md` movidos de la raíz a `docs/` con `git mv` + rutas de `mcp_server/server.py` actualizadas |
| **Higiene** | 50 `.pyc` sacados del índice de git (estaban rastreados pese a `.gitignore`; viajaban a producción en el despliegue por copia). `.omc/` ignorado |

### El grant que no llegó a existir
El probe de nodos, al pasar por el gate real, sembraba un **grant de segmentos** como efecto
colateral. Se intentó el borrado simétrico y **no era targetizable**: la clave lleva el hash de la
IP que nginx observa en el subrequest, un valor que el backend nunca conoce desde su propia llamada
saliente. Adivinarlo con un truco de red habría fallado en silencio el día que cambie la topología.

Solución: el token del probe lleva un claim firmado `pb` y el gate **no le siembra grant**. No hay
ventana en la que el grant exista. Como `pb` viaja dentro del token firmado y no en la query string,
un cliente no puede pedir ese trato manipulando la URL. El test de regresión **se verificó en rojo**
antes de darlo por bueno.

---

## 4. Pendiente de decisión del dueño

1. **Fuga de `stream_key` / `source_url`** — `GET /api/admin/channels` los devuelve en crudo para
   los 41 canales, y lo consumen **admin y reseller**. El frontend nunca los pinta (hay comentarios
   en `CanalesView.tsx` reconociendo el riesgo) pero la respuesta de red sí los lleva, visible en
   devtools. `source_url` puede llevar usuario y contraseña del origen. ¿Enmascarar siempre, solo
   admin, o endpoint de "revelar" con auditoría? → **P0.8**
2. **Sonda de `tc-mia`** (P0.7) — una orden de solo lectura en el servidor cierra el caso.
3. **Valor de `STREAM_GRANT_MAX_LIFETIME_SECONDS`** (P0.2) — sigue en 0 = ilimitado.
4. **Ventana para desplegar la rama** (P0.6), con la 008 en el mismo despliegue.
5. **¿Límite de dispositivos por cliente** además de por plan? (implica migración de BD).

---

## 5. Estado del entorno local al cerrar

- **Suite: 345 pasan, 0 fallos** (verificado por el lead sobre el árbol con los cambios de las 6
  lanes juntas, no solo por cada worker sobre el suyo).
- Base `nexora` en **Alembic 008 (head)**, con datos de prueba del QA: suscriptor
  `qatest_worker_f`, usuario reseller `qa_reseller_f`, 2 canales en Test Plan, una sesión revocada.
- Contenedor `nexora_admin_test` corriendo en **5175** (`admin` / `Admin1234!`).
- **`nexora_transcoder` parado a propósito.** Estaba en crash-loop (340 reinicios): `ffmpeg` no
  logra abrir el input de la cabecera de Esmeraldas — **timeout TCP puro**, la firma de un firewall
  descartando paquetes, no un fallo de configuración. Esa cabecera **filtra por IP de origen** (ya
  documentado en `docs/PRUEBA_TRANSCODIFICACION.md`) y esta máquina no está en la lista blanca del
  NOC. Aviso añadido al `docker-compose.transcode.yml` para que no se relance por accidente.
  Efecto: `/hls/gamatv` no tendrá `index.m3u8`, así que GAMATV da 404 en el player local.
- Bases de test por lane (`nexora_test_l2`, `_l3`, `_l6`) creadas para paralelizar pytest sin
  colisión de esquema. `_l2` quedó con datos de la verificación de la 008.

⚠️ **`docker exec nexora_api pytest` no valida nada**: `./tests` no está montado y la imagen no
trae `requirements-dev`, así que corre la copia horneada. Usar el venv del host contra los puertos
publicados (`localhost:5433` / `localhost:6380`) con `TEST_DATABASE_URL` y `TEST_REDIS_URL`. → P1.5

# Contrato de la API de Administración — Nexora

Documento derivado **exclusivamente de la lectura del código** en `E:\WEBSITE\nexora_api`.
Cada afirmación no trivial cita `archivo:linea`. Lo que no se puede determinar leyendo el
código está marcado como **INCERTIDUMBRE**, no inventado.

Destinatario: equipos que van a construir el panel de administración (frontend) en paralelo.

---

## 0. Lo que hay que leer antes de escribir una sola línea de frontend

Cinco hechos que cambian el diseño del panel:

1. **Los mismos 30 handlers están montados en DOS rutas base**: `/api/v1/...` y `/api/admin/...`.
   No son surfaces distintas, es el mismo código. `app/api/admin/router.py:7-11` incluye los
   mismos módulos que `app/api/v1/router.py:6-10`. Usa **siempre `/api/admin`** (ver §1).
2. **No se pueden crear ni editar canales.** El catálogo es solo lectura (`app/api/admin/channels.py`).
3. **No existe ningún endpoint que toque `plan_channels`.** No se pueden asignar canales a un plan
   desde la API. Verificado por búsqueda global: `PlanChannel` solo aparece en modelos y en dos
   servicios de lectura (`channel_service.py:130`, `entitlement_service.py:133`). Ver §12.
4. **No se puede revocar una sesión individual en la práctica**: el endpoint existe
   (`DELETE /sessions/{jti}`) pero ningún response_model expone el `jti`. Ver §11.
5. **Los `reseller` ven y modifican TODO**, no solo lo suyo. No hay scoping por `created_by`. Ver §2.

---

## 1. Rutas base, montaje y duplicación

`app/main.py:161-166` monta seis routers:

| Router | Prefijo | Fichero |
|---|---|---|
| v1 | `/api/v1` | `app/api/v1/router.py:4` |
| admin | `/api/admin` | `app/api/admin/router.py:5` |
| stb | `/api/stb` | fuera de alcance |
| subscriber | `/api/subscriber` | fuera de alcance |
| client | `/api/client` | fuera de alcance |
| internal | `/internal/stream-auth` | fuera de alcance |

**El punto crítico.** `app/api/admin/router.py:2` importa los módulos de `app.api.v1` y los
incluye bajo `/api/admin`:

```python
from app.api.v1 import auth, users, subscribers, devices, plans   # admin/router.py:2
router = APIRouter(prefix="/api/admin")                            # admin/router.py:5
router.include_router(auth.router)                                 # admin/router.py:7
...
```

Consecuencia exacta:

- **30 handlers** (auth 4 + users 6 + subscribers 9 + devices 6 + plans 5) existen **duplicados**
  en `/api/v1/...` y `/api/admin/...`. Comportamiento idéntico, mismo código, misma autorización.
- **18 handlers** (channels 3 + flussonic 3 + metrics 4 + sessions 4 + subscriptions 4) existen
  **solo** bajo `/api/admin/...`. No hay equivalente en `/api/v1`.
- Total: **48 handlers distintos, 78 URLs reales**.

**Recomendación para el panel: usar únicamente `/api/admin`.** Es el único prefijo bajo el cual
está la superficie completa. `/api/v1` está etiquetado como *legacy compat* en `app/main.py:161`.

**Trampa asociada:** el rate limiter indexa por ruta literal, así que
`/api/v1/auth/login` y `/api/admin/auth/login` tienen contadores **separados**
(`app/middleware/rate_limit.py:16,19` y `key_rate_limit(ip, path)` en `rate_limit.py:57`).
Un atacante que agote uno no agota el otro.

### Exposición pública

nginx publica `location ^~ /api/` completo (`deploy/nginx/snippets/app-locations.conf:20`), y
`/docs`, `/redoc`, `/openapi.json` (`deploy/nginx/conf.d/nexoraplay.conf:67,71,75`).
La superficie admin es alcanzable desde internet; la autorización es la única barrera.

---

## 2. Autenticación y roles

### Roles

`app/models/user.py:10-12` define exactamente dos:

```python
class UserRole(str, enum.Enum):
    admin = "admin"
    reseller = "reseller"
```

Es un `str` Enum, así que en JSON siempre viaja como `"admin"` / `"reseller"`.

### Dependencias de autorización

`app/core/dependencies.py`:

| Dependencia | Línea | Qué exige |
|---|---|---|
| `_get_token_payload` | 32 | Bearer válido, `type` admin, `jti` no en blacklist. **No consulta la BD**: no comprueba que el usuario exista ni que esté activo. |
| `get_current_user` | 49 | Lo anterior + el usuario existe en BD y `is_active=True`. |
| `require_admin` | 60 | Lo anterior + `role == admin`. 403 `"Admin role required"` si no. |
| `require_admin_or_reseller` | 66 | Lo anterior + `role in (admin, reseller)`. 403 `"Insufficient permissions"` si no. |
| `stb_claims` | `app/api/stb/deps.py:39` | Token **STB** (`type=stb_access`), no token de admin. |

Como solo existen dos roles y ambos están en el conjunto, **`require_admin_or_reseller` no
rechaza a ningún usuario autenticado y activo**. Su único efecto real hoy es exigir sesión válida.

### Matriz de permisos: qué puede hacer cada rol

**Solo `admin`** (7 endpoints, vía `require_admin`):

- `GET/POST /users`, `GET/PATCH/DELETE /users/{user_id}` — `app/api/v1/users.py:22,35,50,63,78`
- `POST /plans`, `PATCH /plans/{plan_id}`, `DELETE /plans/{plan_id}` — `app/api/v1/plans.py:31,58,73`

**`admin` y `reseller` por igual** (todo lo demás). Esto incluye operaciones de alto impacto:

- Crear, editar, suspender y **borrar** suscriptores (`app/api/v1/subscribers.py:39,66,110,140`)
- Cambiar la contraseña de cualquier suscriptor (`app/api/v1/subscribers.py:82`)
- **Crear, renovar y cancelar suscripciones** (`app/api/admin/subscriptions.py:45,107,156`)
- Revocar sesiones IPTV (`app/api/admin/sessions.py:102,132`)
- Leer el log de auditoría completo, incluidos los logins de los admins (`app/api/admin/metrics.py:126`)
- Ver `stream_key` de todos los canales (`app/schemas/channel.py:41` en `ChannelAdminOut`)

**Sin rol específico, cualquier usuario autenticado:**

- `GET /auth/me` (`app/api/v1/auth.py:55`)
- `POST /users/me/change-password` (`app/api/v1/users.py:91`)
- `POST /auth/logout` (`app/api/v1/auth.py:43`, solo `_get_token_payload`)

**Sin autenticación:** `POST /auth/login`, `POST /auth/refresh`.

**RIESGO DE NEGOCIO — no hay scoping de reseller.** `Subscriber.created_by`
(`app/models/subscriber.py:37`) se rellena al crear (`app/api/v1/subscribers.py:43`) pero
**ningún listado ni ningún `get` lo filtra**. `SubscriberService.list_subscribers`
(`app/services/subscriber_service.py:33-49`) solo filtra por `status`. Un reseller ve la cartera
completa de todos los resellers y puede borrarla. Si el panel va a exponerse a resellers, esto
debe resolverse en backend antes, no ocultando botones en el frontend.

### Formato del token

`app/core/security.py:106-111`: `type=admin_access`, `aud=nexora-admin`,
`iss=nexora-api`, claims `sub` (UUID de usuario), `jti`, `role`, `iat`, `exp`. HS256.

TTL por defecto (`app/config.py:16-17`): access **30 minutos**, refresh **30 días**.
`expires_in` en la respuesta de login son **segundos** (`security.py:109`: `minutes * 60`).

Con `jwt_require_aud=false` (**valor por defecto**, `app/config.py:40`) la validación es laxa:
se aceptan también tokens legacy con `type="access"` y `aud`/`iss` no se comprueban
(`app/core/security.py:194-196`). Con el flag en `true` la validación es estricta.

---

## 3. Envoltorios de respuesta

`app/schemas/common.py` define tres formas, **y hay endpoints que no usan ninguna**.

```python
class ApiResponse(BaseModel, Generic[T]):        # common.py:7
    success: bool = True
    data: T | None = None
    message: str | None = None

class PaginatedResponse(BaseModel, Generic[T]):  # common.py:13
    success: bool = True
    data: list[T]
    total: int
    page: int
    page_size: int
    pages: int

class MessageResponse(BaseModel):                # common.py:28
    success: bool = True
    message: str
```

**Cuatro formas conviven en la superficie admin.** El cliente HTTP del panel no puede asumir
`response.data`:

| Forma | Ejemplo | Endpoints |
|---|---|---|
| `ApiResponse[T]` | `{"success":true,"data":{...},"message":null}` | la mayoría |
| `PaginatedResponse[T]` | `{"success":true,"data":[...],"total":..,...}` | solo `GET /users` y `GET /subscribers` |
| `MessageResponse` | `{"success":true,"message":"..."}` | todos los DELETE y las acciones |
| **Cruda, sin envoltorio** | `{...}` o `[...]` | ver lista abajo |

Endpoints **sin envoltorio** (el frontend debe tratarlos aparte):

- `POST /auth/login`, `POST /auth/refresh` → `TokenResponse` plano, **sin campo `success`** (`app/api/v1/auth.py:16,29`)
- `GET /auth/me` → dict crudo, **sin `response_model`** (`app/api/v1/auth.py:54-63`)
- `GET /channels` → **array desnudo** `list[ChannelAdminOut]` (`app/api/admin/channels.py:24`)
- `GET /channels/{id}` → objeto desnudo (`app/api/admin/channels.py:33`)
- `GET /channels/{id}/stream-status` → objeto desnudo (`app/api/admin/channels.py:46`)
- `GET /sessions/live` → **array desnudo** (`app/api/admin/sessions.py:51`)
- `GET /metrics`, `GET /nodes/health` → objeto / array desnudos (`app/api/admin/metrics.py:52,100`)
- `GET /alerts` → `{"active": [...]}`, sin `response_model` (`app/api/admin/metrics.py:109-116`)
- `GET /audit` → **array desnudo de dicts**, sin `response_model` (`app/api/admin/metrics.py:119`)
- `GET /flussonic/health`, `/flussonic/streams`, `/flussonic/streams/{name}` → desnudos (`app/api/admin/flussonic.py:37,63,96`)

---

## 4. Errores: **tres formas distintas**, y pueden salir del mismo endpoint

### Forma A — `NexoraException` (la mayoría del código de negocio)

`app/main.py:131-147` aplana el detalle:

```json
{"success": false, "error": "Subscriber not found"}
```

y si el detalle era estructurado, añade `reason_code`:

```json
{"success": false, "error": "Límite de dispositivos alcanzado...", "reason_code": "DEVICE_LIMIT_REACHED"}
```

Constructores en `app/core/exceptions.py`: `not_found` → 404, `already_exists` → 409,
`forbidden` → 403, `unauthorized` → 401 (+ header `WWW-Authenticate: Bearer`),
`bad_request` → 400, `locked` → 423, `rate_limited` → 429.

### Forma B — `HTTPException` de FastAPI sin envolver

`NexoraException` hereda de `HTTPException` (`exceptions.py:4`), pero el handler está registrado
para `NexoraException`, no para `HTTPException`. Los `raise HTTPException(...)` directos
**no pasan por el handler** y salen con el formato por defecto de FastAPI:

```json
{"detail": "Flussonic integration is not configured."}
```

Ocurre en: `app/api/admin/channels.py:62,74` y `app/api/admin/flussonic.py:75,82,103,107`.

**Trampa de primer orden:** `GET /channels/{id}/stream-status` puede devolver **dos 404 con
formas distintas** según la causa:
- canal inexistente → `not_found("Channel")` en `channels.py:70` → `{"success":false,"error":"Channel not found"}`
- stream inexistente en Flussonic → `HTTPException` en `channels.py:74` → `{"detail":"Stream 'x' not found in Flussonic."}`

El parser de errores del frontend debe leer `error ?? detail ?? "..."`, nunca solo uno.

### Forma C — Validación 422 de FastAPI

`{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`. Es un array, no un string.

### Forma D — Rate limit (429)

`app/middleware/rate_limit.py:63-68`, string JSON literal, antes de cualquier handler:
`{"success":false,"error":"Too many requests"}` + header `Retry-After: 60`.

### 500

`app/main.py:150-157`. En producción: `{"success": false, "error": "Internal server error"}`.
Con `debug=true` la excepción se re-lanza y el cliente recibe el traceback de Starlette.

---

## 5. Rate limiting y CORS (afecta al arranque del panel)

**Rate limit por IP y por ruta literal**, ventana de 60 s (`app/middleware/rate_limit.py:52-70`).
Límite global por defecto: **60 req/min** (`app/config.py:34`). Overrides relevantes
(`rate_limit.py:16-19`): `/api/v1/auth/login` 10, `/api/v1/auth/refresh` 20,
`/api/admin/auth/login` 10, `/api/admin/auth/refresh` 20.

Cabeceras en toda respuesta: `X-RateLimit-Limit`, `X-RateLimit-Remaining` (`rate_limit.py:71-72`).

> **60 req/min por IP es bajo para un panel.** Una pantalla que dispare 10-15 llamadas al montar
> y refresque cada pocos segundos agota el presupuesto. Además, **todos los usuarios detrás del
> mismo NAT comparten contador** (la IP se toma de `X-Forwarded-For`, `rate_limit.py:76-79`).

**Bloqueo de cuenta por IP.** `AuthService._record_failed_attempt` se invoca con el username **y
con la IP** (`app/services/auth_service.py:69-70`). Tras 5 fallos (`app/config.py:32`) se bloquea
**15 minutos** (`config.py:33`) y el login devuelve **423 Locked**. Es decir: cinco intentos
fallidos desde la IP de la oficina bloquean el login de *todos* los usuarios desde esa IP.

**CORS.** `app/main.py:111-125`. Orígenes permitidos **hardcodeados**:
`http://localhost:5173`, `http://127.0.0.1:5173`, `http://172.27.99.151:5173`,
`http://localhost:4173`, `http://127.0.0.1:4173`.
En `debug=true` se abre a `*` **pero con `allow_credentials=False`** (`main.py:121-122`).
Un panel servido desde cualquier otro origen será rechazado hasta que se edite `main.py`.

---

## 6. Auth — `/api/admin/auth/*` (4 endpoints)

| Método | Ruta | Auth | Respuesta |
|---|---|---|---|
| POST | `/api/admin/auth/login` | — | `TokenResponse` (plano) |
| POST | `/api/admin/auth/refresh` | — | `TokenResponse` (plano) |
| POST | `/api/admin/auth/logout` | Bearer (solo token válido) | `MessageResponse` |
| GET | `/api/admin/auth/me` | Bearer + usuario activo | dict crudo |

### POST `/auth/login` — `app/api/v1/auth.py:16`

Body `LoginRequest` (`app/schemas/auth.py:5`):

| Campo | Tipo | Validación |
|---|---|---|
| `username` | str | requerido, 1–64 |
| `password` | str | requerido, 1–128 |

Respuesta 200 `TokenResponse` (`app/schemas/auth.py:10`):

```json
{"access_token": "...", "refresh_token": "...", "token_type": "bearer", "expires_in": 1800}
```

Errores: **401** `"Invalid credentials"` (`auth_service.py:73`) — mismo mensaje para usuario
inexistente y contraseña mala, no distinguible. **401** `"Account is disabled"`
(`auth_service.py:76`). **423** `"Account locked. Try again in {N}s."` (`auth_service.py:41`).
**429** rate limit. **422** validación.

Efectos laterales: actualiza `last_login_at`/`last_login_ip` (`auth_service.py:87-91`) y escribe
`auth.login` en auditoría (`auth_service.py:93-95`).

### POST `/auth/refresh` — `app/api/v1/auth.py:29`

Body: `{"refresh_token": "..."}` (`app/schemas/auth.py:17`).

**TRAMPA CRÍTICA PARA UNA SPA — el refresh rota y revoca.** `auth_service.py:120` invalida el
refresh viejo *antes* de emitir el par nuevo. Si el panel dispara dos refresh concurrentes
(típico: tres peticiones reciben 401 a la vez y cada una lanza su refresh), **el segundo recibe
401 `"Refresh token expired or revoked"`** (`auth_service.py:117`) y el usuario sale de sesión.
El cliente HTTP **debe** serializar el refresh con un único promise compartido.

Errores: 401 `"Invalid refresh token"` / `"Malformed refresh token"` / `"Refresh token expired or revoked"`.

### POST `/auth/logout` — `app/api/v1/auth.py:40`

Body **opcional** (`body: RefreshRequest | None = None`, `auth.py:42`): se puede llamar sin cuerpo.
Si se envía `refresh_token`, también se revoca (`auth.py:49-50`).

Depende de `_get_token_payload`, **no** de `get_current_user` (`auth.py:43`): un usuario
desactivado o borrado puede seguir haciendo logout mientras su token sea criptográficamente válido.

Respuesta: `{"success": true, "message": "Logged out successfully"}`.

### GET `/auth/me` — `app/api/v1/auth.py:54`

**Sin `response_model`.** Devuelve un dict crudo, no un `ApiResponse`:

```json
{"id": "uuid-string", "username": "...", "email": "...", "role": "admin", "is_active": true}
```

`id` es **string**, no UUID objeto (`auth.py:58`: `str(user.id)`). No incluye `full_name`,
`created_at` ni `last_login_at`: para eso hace falta `GET /users/{id}`, que es **admin-only** →
**un reseller no puede consultar su propio perfil completo**.

---

## 7. Usuarios (admin/reseller de la plataforma) — 6 endpoints

Todos bajo `/api/admin/users`. Todos **admin-only** salvo el último.

| Método | Ruta | Rol | Respuesta | Código OK |
|---|---|---|---|---|
| GET | `/users` | admin | `PaginatedResponse[UserOut]` | 200 |
| POST | `/users` | admin | `ApiResponse[UserOut]` | **201** |
| GET | `/users/{user_id}` | admin | `ApiResponse[UserOut]` | 200 |
| PATCH | `/users/{user_id}` | admin | `ApiResponse[UserOut]` | 200 |
| DELETE | `/users/{user_id}` | admin | `MessageResponse` | 200 |
| POST | `/users/me/change-password` | cualquiera autenticado | `MessageResponse` | 200 |

`user_id` es `uuid.UUID` en la ruta: un valor no-UUID da **422**, no 404 (`app/api/v1/users.py:48`).

### GET `/users` — paginado — `app/api/v1/users.py:17`

Query: `page` (int, `ge=1`, default 1), `page_size` (int, `ge=1`, `le=200`, default 50).
`pages` se calcula en el endpoint (`users.py:26`). Sin filtros, sin búsqueda, sin orden.
**No hay `ORDER BY`** en `UserService.list_users` (`app/services/user_service.py:26-32`) → el
orden de página es el que devuelva Postgres, no garantizado ni estable entre páginas.

### POST `/users` — `app/api/v1/users.py:30`

Body `UserCreate` (`app/schemas/user.py:7`):

| Campo | Tipo | Validación | Default |
|---|---|---|---|
| `username` | str | req., 3–64, `^[a-zA-Z0-9_.-]+$` | — |
| `email` | EmailStr | requerido, validado | — |
| `password` | str | req., **8–128** | — |
| `full_name` | str? | ≤128 | null |
| `role` | `"admin"`\|`"reseller"` | — | **`reseller`** |
| `notes` | str? | sin límite | null |

Errores: **409** `"Username already exists"` (`user_service.py:36`), **409** `"Email already exists"`
(`user_service.py:39`).

### PATCH `/users/{user_id}` — `app/api/v1/users.py:57`

Body `UserUpdate` (`app/schemas/user.py:16`): `email`, `full_name`, `role`, `is_active`, `notes`.
Todos opcionales.

**TRAMPA: no se puede poner un campo a `null`.** `UserService.update` aplica
`data.model_dump(exclude_none=True)` (`user_service.py:55`). Enviar `{"notes": null}` es un
**no-op silencioso**, devuelve 200 con el valor viejo intacto. No hay forma de vaciar
`full_name` ni `notes` por la API. (`is_active: false` sí funciona: `False` no es `None`.)

**HUECO: `UserUpdate` no tiene campo de contraseña.** Un admin **no puede resetear** la
contraseña de otro usuario. La única vía es `POST /users/me/change-password`, que exige la
contraseña actual. Un reseller que la olvide necesita intervención en BD.

**No hay protecciones de integridad:** nada impide que un admin se auto-desactive, se auto-borre,
o degrade al último admin a reseller dejando la plataforma sin administradores.
(Verificado: `user_service.py:52-68` no comprueba nada de esto.)

### DELETE `/users/{user_id}` — `app/api/v1/users.py:73`

Borrado **físico** (`user_service.py:66-68`). Las FK que apuntan a `users` son `SET NULL`
(`subscribers.created_by` en `app/models/subscriber.py:38`, `subscriptions.created_by` en
`app/models/subscription.py:32`, `audit_logs.actor_id` en `app/models/audit.py:16`), así que el
borrado no falla, pero **anonimiza retroactivamente el historial**: las suscripciones pierden quién
las creó. `audit_logs.actor_username` es una columna de texto (`audit.py:18`) y sí sobrevive.

### POST `/users/me/change-password` — `app/api/v1/users.py:87`

Body `UserPasswordChange` (`app/schemas/user.py:24`): `current_password` (str, sin mínimo),
`new_password` (str, 8–128). **400** `"Current password is incorrect"` (`user_service.py:62`).
No emite entrada de auditoría (a diferencia del resto de mutaciones).

### `UserOut` — `app/schemas/user.py:29`

`id` (UUID), `username`, `email`, `full_name?`, `role`, `is_active`, `notes?`, `created_at`,
`updated_at`, `last_login_at?`, `last_login_ip?`. **Nunca** incluye `password_hash`.

---

## 8. Suscriptores (clientes finales IPTV) — 9 endpoints

Todos bajo `/api/admin/subscribers`, todos `require_admin_or_reseller`.

| Método | Ruta | Respuesta | Código OK |
|---|---|---|---|
| GET | `/subscribers` | `PaginatedResponse[SubscriberOut]` | 200 |
| POST | `/subscribers` | `ApiResponse[SubscriberOutFull]` | **201** |
| GET | `/subscribers/{sub_id}` | `ApiResponse[SubscriberOutFull]` | 200 |
| PATCH | `/subscribers/{sub_id}` | `ApiResponse[SubscriberOut]` | 200 |
| POST | `/subscribers/{sub_id}/set-password` | `MessageResponse` | 200 |
| GET | `/subscribers/{sub_id}/status` | `ApiResponse[SubscriberActiveStatus]` | 200 |
| POST | `/subscribers/{sub_id}/suspend` | `MessageResponse` | 200 |
| POST | `/subscribers/{sub_id}/activate` | `MessageResponse` | 200 |
| DELETE | `/subscribers/{sub_id}` | `MessageResponse` | 200 |

### TRAMPA: la forma de la respuesta cambia según el verbo

`SubscriberOutFull` (`app/schemas/subscriber.py:46`) extiende `SubscriberOut` con
**`activation_code`** y **`created_by`**.

- `POST /subscribers` → **Full** (`subscribers.py:34`)
- `GET /subscribers/{id}` → **Full** (`subscribers.py:49`)
- `GET /subscribers` (lista) → **no Full** (`subscribers.py:20`)
- `PATCH /subscribers/{id}` → **no Full** (`subscribers.py:60`)

Un `PATCH` optimista que reemplace el objeto en el store del frontend **borrará
`activation_code` y `created_by` del estado local**. Hay que hacer merge, o re-hacer el `GET`.

### GET `/subscribers` — el único filtro es `status`

Query (`app/api/v1/subscribers.py:22-24`):

| Param | Tipo | Validación | Default |
|---|---|---|---|
| `page` | int | `ge=1` | 1 |
| `page_size` | int | `ge=1`, `le=200` | 50 |
| `status` | enum | `active`\|`expired`\|`suspended`\|`banned` | null (todos) |

`SubscriberStatus` en `app/models/subscriber.py:10-14`.

**HUECO grave para el panel: no hay búsqueda ni ordenación.** `SubscriberService.list_subscribers`
(`app/services/subscriber_service.py:33-49`) no acepta texto libre ni `ORDER BY`. No se puede
buscar por `username`, `email`, `id_cedula` ni `phone`, aunque los tres primeros están indexados
en BD (`subscriber.py:23,27,30`). Con miles de suscriptores, la pantalla de "buscar cliente" —
la más usada de cualquier panel IPTV — **no se puede construir** sin añadir backend.
Igual que en usuarios, sin `ORDER BY` la paginación no es estable.

### POST `/subscribers` — `app/api/v1/subscribers.py:34`

Body `SubscriberCreate` (`app/schemas/subscriber.py:7`):

| Campo | Tipo | Validación |
|---|---|---|
| `username` | str | req., 3–64, `^[a-zA-Z0-9_.\-]+$` |
| `password` | str? | **6–128** (nota: los usuarios exigen 8) |
| `activation_code` | str? | ≤64 |
| `email` | EmailStr? | validado si se envía |
| `phone` | str? | ≤32 |
| `full_name` | str? | ≤128 |
| `id_cedula` | str? | ≤32 |
| `notes` | str? | — |

Reglas de negocio (`app/services/subscriber_service.py:51-73`):
- **400** `"Either password or activation_code is required"` si faltan ambos (línea 55).
- Si no se envía `activation_code`, **se genera uno automáticamente**: `secrets.token_urlsafe(12)`
  (línea 59). Viene en la respuesta `SubscriberOutFull.activation_code`.
- **409** `"Username already exists"` (línea 53).
- `created_by` se rellena con el `actor.id` del token (`subscribers.py:43`).

**Nota de seguridad para el panel:** `activation_code` es una credencial de acceso al servicio
(`app/services/stb_service.py:59-61` la acepta como alternativa a la contraseña). Se devuelve en
claro en `POST` y en cada `GET /subscribers/{id}`. Tratarla como secreto en la UI.

### PATCH `/subscribers/{sub_id}` — `app/api/v1/subscribers.py:60`

Body `SubscriberUpdate` (`app/schemas/subscriber.py:18`): `email`, `phone`, `full_name`,
`id_cedula`, `status`, `notes`. Mismo `exclude_none=True` (`subscriber_service.py:76`): **no se
puede vaciar un campo**.

**`status` se cambia por aquí**, y es la **única** vía para poner `banned` o `expired`: los
endpoints dedicados solo hacen `suspended` (`subscribers.py:114`) y `active` (`subscribers.py:129`).

**No se puede cambiar el `username`** ni el `activation_code` de un suscriptor existente.

### GET `/subscribers/{sub_id}/status` — `app/api/v1/subscribers.py:92`

`ApiResponse[SubscriberActiveStatus]` (`app/schemas/subscription.py:41`):

```json
{"success": true, "data": {
  "subscriber_id": "uuid", "username": "...", "is_active": true,
  "subscription_expires_at": "2026-08-01T...", "max_connections": 2,
  "max_devices": 3, "device_count": 1, "days_remaining": 12}}
```

**TRAMPA: devuelve 401, no 404, si el suscriptor no existe.**
`STBService.validate_active` lanza `unauthorized("Subscriber not found")`
(`app/services/stb_service.py:73`) porque el servicio está pensado para la superficie de
dispositivos. Un panel que interprete cualquier 401 como "token expirado" **cerrará la sesión del
admin** al abrir la ficha de un suscriptor borrado. Hay que distinguir por el cuerpo del error.

**Semántica de los campos cuando no hay suscripción activa** (`stb_service.py:99-108`):
`is_active=false`, `max_connections=0`, `max_devices=0`, `days_remaining=null`. Los ceros
significan "sin plan", **no** "plan que permite cero dispositivos". No mostrarlos como límites.

`days_remaining` es `(expires_at - now).days` (`stb_service.py:87`): un `timedelta` truncado.
Quedando 23 horas devuelve **0**, no 1.

`max_connections` (streams simultáneos) y `max_devices` (dispositivos registrados) son límites
**distintos** del plan (`app/models/plan.py:18-19`). No confundirlos.

### DELETE `/subscribers/{sub_id}` — `app/api/v1/subscribers.py:135`

Borrado **físico** con cascada en BD: `devices` (`app/models/device.py:16` CASCADE),
`subscriptions` (`app/models/subscription.py:16` CASCADE), `sessions`
(`app/models/session.py:22` CASCADE). Se pierde todo el historial del cliente.
No hay confirmación, ni "soft delete", ni endpoint de restauración. La UI debe exigir confirmación
explícita.

### `SubscriberOut` — `app/schemas/subscriber.py:31`

`id`, `username`, `email?`, `phone?`, `full_name?`, `id_cedula?`, `status`, `notes?`,
`created_at`, `updated_at`. Nunca expone `password_hash`.
`SubscriberOutFull` añade `activation_code?` y `created_by?`.

---

## 9. Suscripciones — 4 endpoints (solo `/api/admin`)

`app/api/admin/subscriptions.py:32` usa `prefix="/subscribers"`, así que las rutas cuelgan del
recurso suscriptor. **No existen bajo `/api/v1`.** Todos `require_admin_or_reseller`.

| Método | Ruta | Respuesta | Código OK |
|---|---|---|---|
| POST | `/api/admin/subscribers/{sub_id}/subscriptions` | `ApiResponse[SubscriptionOut]` | **201** |
| GET | `/api/admin/subscribers/{sub_id}/subscriptions` | `ApiResponse[list[SubscriptionOut]]` | 200 |
| POST | `/api/admin/subscribers/{sub_id}/subscriptions/{subscription_id}/renew` | `ApiResponse[SubscriptionOut]` | 200 |
| POST | `/api/admin/subscribers/{sub_id}/subscriptions/{subscription_id}/cancel` | `MessageResponse` | 200 |

**Sí se pueden crear suscripciones** — respuesta directa a la duda planteada. Lo que no hay es
listado global (§12).

### POST — crear — `app/api/admin/subscriptions.py:35`

Body `SubscriptionAdminCreate` (`app/schemas/subscription.py:14`):

| Campo | Tipo | Validación |
|---|---|---|
| `plan_id` | UUID | **requerido** |
| `renewal_note` | str? | ≤255 |

`subscriber_id` viene de la ruta, **no del body** (documentado en `subscription.py:15-17`).
`SubscriptionCreate` (`subscription.py:7`) **no se usa en ningún endpoint admin** — ignorarlo.

Lógica (`app/services/subscription_service.py:92-119`):
- 404 `"Subscriber not found"` (línea 38) / 404 `"Plan not found"` (línea 47).
- **400** `"Plan '{name}' is inactive and cannot be assigned"` si el plan está desactivado (línea 49).
- **La suscripción activa previa se desactiva silenciosamente** (`is_active=False`, líneas 106-108).
  Se conserva como histórico. La UI debe avisar antes de crear: es un reemplazo, no una adición.
- `starts_at = now`; `expires_at = now + plan.duration_days` (líneas 110-117).
  **El `starts_at` no es configurable** por este endpoint (`SubscriptionCreate.starts_at` existe en
  el esquema pero ese esquema no se usa). No se pueden dar de alta suscripciones con fecha futura.

### GET — historial — `app/api/admin/subscriptions.py:82`

Historial **completo** del suscriptor, ordenado por `starts_at DESC`
(`subscription_service.py:126-131`). **Sin paginación.** Incluye el plan embebido vía
`selectinload` (línea 128), así que `SubscriptionOut.plan` viene poblado aquí.

### POST — renovar — `app/api/admin/subscriptions.py:97`

Body `SubscriptionRenew` (`app/schemas/subscription.py:22`): `plan_id` (UUID?, si se omite
mantiene el plan actual), `renewal_note` (str?, ≤255). **El body es obligatorio** aunque ambos
campos sean opcionales: hay que enviar al menos `{}`.

Lógica (`subscription_service.py:133-171`):
- Extiende desde `expires_at` si aún es vigente, **o desde `now` si ya venció**: `base = max(base, now)` (línea 165).
- Suma `plan.duration_days` del plan **final** (el nuevo si se cambió).
- Siempre pone `is_active=True` (línea 168) → **`renew` resucita una suscripción cancelada**.
  Es la vía para reactivar sin crear registro nuevo.
- `renewal_note` solo se sobrescribe si no es `None` (línea 169).
- `created_by` se **sobrescribe** con el actor que renueva (línea 171): se pierde quién la creó.
- 404 `"Subscription not found"` también cuando la suscripción existe pero **pertenece a otro
  suscriptor** — deliberado, para no filtrar información (`subscription_service.py:57-59,71`).

### POST — cancelar — `app/api/admin/subscriptions.py:146`

**Sin body.** Efectos (`subscriptions.py:169-176`):
1. `is_active=False`, el registro se conserva.
2. **Revoca TODAS las sesiones IPTV activas del suscriptor**, no solo las de esta suscripción:
   marca `revoked_at`, borra claves Redis, borra playback tokens, cierra conexiones del ZSET.
3. Mensaje: `"Subscription cancelled. N active IPTV session(s) revoked."` — hay que parsear el
   texto para saber cuántas cayeron; **no viene como campo estructurado**.

**400** `"Subscription is already inactive"` si ya estaba cancelada (`subscription_service.py:181`).

### `SubscriptionOut` — `app/schemas/subscription.py:27`

`id`, `subscriber_id`, `plan_id`, `starts_at`, `expires_at`, `is_active`, `renewal_note?`,
`created_at`, **`plan: PlanOut | None`**.

**TRAMPA: `plan` puede ser `null` o venir poblado según el endpoint.** Se rellena por
`selectinload` (`subscription_service.py:174-180`, `126-131`), así que en la práctica viene
poblado en create/renew/list. En `cancel` no se devuelve el objeto. No asumir que siempre está;
`plan_id` sí está siempre.

**TRAMPA de nomenclatura: `is_active` significa cosas distintas.**
- `Subscription.is_active` es una **columna de BD** (`app/models/subscription.py:28`) que solo
  indica "no cancelada". **Una suscripción con `is_active=true` y `expires_at` en el pasado es
  perfectamente posible** — nada la actualiza automáticamente. Todas las consultas de negocio
  comprueban las dos cosas (`subscription_service.py:75-78`, `stb_service.py:31-35`).
  **El panel debe hacer lo mismo: `is_active && expires_at > now`.**
- `SessionOut.is_active` es **calculado en el esquema** (§11).
- `SubscriberActiveStatus.is_active` significa "tiene suscripción vigente ahora mismo".

---

## 10. Planes — 5 endpoints

| Método | Ruta | Rol | Respuesta | Código OK |
|---|---|---|---|---|
| GET | `/api/admin/plans` | admin **o reseller** | `ApiResponse[list[PlanOut]]` | 200 |
| POST | `/api/admin/plans` | **admin** | `ApiResponse[PlanOut]` | **201** |
| GET | `/api/admin/plans/{plan_id}` | admin **o reseller** | `ApiResponse[PlanOut]` | 200 |
| PATCH | `/api/admin/plans/{plan_id}` | **admin** | `ApiResponse[PlanOut]` | 200 |
| DELETE | `/api/admin/plans/{plan_id}` | **admin** | `MessageResponse` | 200 |

### GET `/plans` — `app/api/v1/plans.py:16`

**Sin paginación, sin query params.** Devuelve **todos** los planes, activos e inactivos:
`only_active=False` está **hardcodeado** en `plans.py:22`. `PlanService.list_plans`
(`app/services/plan_service.py:19-24`) soporta el filtro, pero el endpoint no lo expone.
El panel debe filtrar `is_active` en cliente. Tampoco hay `ORDER BY`.

### POST `/plans` — `app/api/v1/plans.py:26`

Body `PlanCreate` (`app/schemas/plan.py:7`):

| Campo | Tipo | Validación | Default |
|---|---|---|---|
| `name` | str | req., 2–128, **único** | — |
| `description` | str? | — | null |
| `max_connections` | int | `1..100` | **1** |
| `max_devices` | int | `1..50` | **2** |
| `duration_days` | int | `≥1` (sin máximo) | **30** |
| `price` | Decimal? | **`ge=0`** | null |
| `notes` | str? | — | null |

**409** `"Plan name already exists"` (`plan_service.py:29`).

### PATCH `/plans/{plan_id}` — `app/api/v1/plans.py:52`

Body `PlanUpdate` (`app/schemas/plan.py:17`): los mismos campos + `is_active`, todos opcionales.

**BUG/TRAMPA: `PlanUpdate.price` perdió la validación `ge=0`.**
`app/schemas/plan.py:13` → `price: Decimal | None = Field(None, ge=0)` (create)
`app/schemas/plan.py:23` → `price: Decimal | None = Field(None)`     (update)
**Se puede hacer PATCH con `price: -50` y la API lo acepta.** El frontend debe validar el mínimo
por su cuenta si quiere evitarlo.

Mismo `exclude_none=True` (`plan_service.py:37`): no se puede vaciar `description`/`notes`.

**Los cambios de plan afectan retroactivamente a las suscripciones vivas**: los límites se leen
del `Plan` en cada comprobación (`stb_service.py:29-40`, `device_service.py:36-46`), no se
congelan en la suscripción. Bajar `max_devices` deja fuera de norma a clientes existentes de
inmediato. `duration_days` en cambio solo afecta a futuras creaciones/renovaciones.

### DELETE `/plans/{plan_id}` — `app/api/v1/plans.py:68`

Borrado **físico** (`plan_service.py:40-43`).

**TRAMPA: si el plan tiene suscripciones, esto devuelve 500, no 409.**
`subscriptions.plan_id` es `ondelete="RESTRICT"` (`app/models/subscription.py:19`). El `flush`
provoca `IntegrityError`, que **no** es una `NexoraException`, así que cae en el handler genérico
(`app/main.py:150-157`) → `500 {"success": false, "error": "Internal server error"}`.
No hay forma de distinguirlo de un fallo real del servidor. **Recomendación de UI: desactivar
(`PATCH is_active:false`) en vez de borrar**, y presentar "eliminar" solo para planes sin uso.

Nota adicional: `plan_channels` sí es `CASCADE` (`app/models/plan_channel.py:24`), así que borrar
un plan borraría sus entitlements sin aviso.

### `PlanOut` — `app/schemas/plan.py:28`

`id`, `name`, `description?`, `max_connections`, `max_devices`, `duration_days`, `price?`,
`is_active`, `notes?`, `created_at`. **No incluye `updated_at`** aunque la columna existe
(`app/models/plan.py:29`).

**INCERTIDUMBRE — serialización de `price`.** Es `Decimal` en el esquema
(`app/schemas/plan.py:37`) sobre una columna `Numeric(10,2)` (`app/models/plan.py:22`). Pydantic v2
suele serializar `Decimal` a **string** en JSON (`"9.99"`), no a número. No lo he verificado
ejecutando. **El frontend debe aceptar ambos** (`typeof price === "string" ? parseFloat(price) : price`)
o comprobarlo contra la API real antes de dar por buena una asunción.

---

## 11. Dispositivos — 6 endpoints

| Método | Ruta | Auth | Respuesta | Código OK |
|---|---|---|---|---|
| GET | `/api/admin/devices/subscriber/{sub_id}` | admin/reseller | `ApiResponse[list[DeviceOut]]` | 200 |
| POST | `/api/admin/devices/register/{sub_id}` | admin/reseller | `ApiResponse[DeviceOut]` | **201** |
| POST | `/api/admin/devices/heartbeat` | **token STB** | `ApiResponse[dict]` | 200 |
| POST | `/api/admin/devices/{device_id}/block` | admin/reseller | `ApiResponse[DeviceOut]` | 200 |
| POST | `/api/admin/devices/{device_id}/unblock` | admin/reseller | `ApiResponse[DeviceOut]` | 200 |
| DELETE | `/api/admin/devices/{device_id}` | admin/reseller | `MessageResponse` | 200 |

### TRAMPA MAYOR: `device_id` significa dos cosas distintas

| Dónde | Tipo | Qué es |
|---|---|---|
| Ruta `/devices/{device_id}/block` | **UUID** | `Device.id`, la PK (`app/api/v1/devices.py:83`) |
| Body `DeviceRegister.device_id` | **str 6–128** | `Device.device_id`, la MAC / android_id (`app/schemas/device.py:19`) |
| Body `DeviceHeartbeat.device_id` | **str** | idem (`app/schemas/device.py:44`) |
| `DeviceOut.id` | UUID | la PK |
| `DeviceOut.device_id` | str | el identificador de hardware |
| `SessionOut.device_id` | **UUID** | FK a `Device.id` (`app/models/session.py:25`) |
| `LiveSessionOut.device_id` | **str que contiene un UUID** | `str(Session.device_id)` (`app/api/admin/sessions.py:73`) |

Es decir: el `device_id` que devuelve `/sessions/live` **no** es el `device_id` que acepta
`/devices/heartbeat`; es el `id` que acepta `/devices/{device_id}/block`. Confundirlos produce 404
o, peor, actuar sobre el dispositivo equivocado. El frontend debería renombrarlos en su capa de
tipos (`deviceUuid` vs `hardwareId`).

### TRAMPA: `POST /api/admin/devices/heartbeat` NO acepta el token de admin

`app/api/v1/devices.py:55` depende de `stb_claims`, que exige un token con `type=stb_access`
(`app/api/stb/deps.py:39-52`). Un token de admin da **401** `"Invalid or expired token"`.
Es un endpoint de dispositivo que quedó montado en la superficie admin por el include compartido
(`app/api/admin/router.py:10`). **Está en el OpenAPI del panel pero es inutilizable desde él.**
El comentario en `devices.py:57-69` explica la decisión (cerrar un IDOR). Con
`STB_AUTH_ENFORCE=false` (`app/config.py:75`) aceptaría peticiones **sin** Authorization, pero el
valor por defecto es `true`.

### GET `/devices/subscriber/{sub_id}` — `app/api/v1/devices.py:18`

Array completo, **sin paginación ni filtros** (`app/services/device_service.py:63-67`).
Acotado por `plan.max_devices` (≤50), así que es seguro.
**No hay endpoint para listar dispositivos globalmente** (§12).

### POST `/devices/register/{sub_id}` — `app/api/v1/devices.py:30`

Body `DeviceRegister` (`app/schemas/device.py:10`):

| Campo | Tipo | Validación |
|---|---|---|
| `device_id` | str | req., **6–128** |
| `mac_address` | str? | ≤32 |
| `model` | str? | ≤128 |
| `brand` | str? | ≤64 |
| `device_type` | str? | ≤32 — texto libre, **no** enum (valores usados: `android_tv`, `android`, `ios`, `mag`, `web`, `stb`, `app/models/device.py:33`) |
| `app_version` | str? | ≤32 |
| `os_version` | str? | ≤32, **truncado silenciosamente**, no rechazado (`app/schemas/device.py:28-40`) |
| `user_agent` | str? | sin límite |

Comportamiento (`app/services/device_service.py:70-155`):
- Si el `device_id` ya existe **para este suscriptor** → se **actualiza** y se devuelve, con
  **201** igualmente. No es un "create" real; es un upsert que siempre responde 201.
- Si el `device_id` existe **para otro suscriptor** → **403** `"Device registered to a different subscriber"` (línea 97).
- Si supera `plan.max_devices` → **409** con `reason_code: "DEVICE_LIMIT_REACHED"` (líneas 122-128).
  Es el único endpoint admin con `reason_code`, útil para mostrar un mensaje específico.
- **Si el suscriptor no tiene suscripción activa, el límite NO se aplica** (línea 117:
  `if sub_plan:`). Se pueden registrar dispositivos ilimitados a un cliente sin plan.

**HUECO/TRAMPA: el `device_secret` se genera pero no se devuelve.**
`device_service.py:140-153` crea un secreto de alta entropía y lo adjunta como
`device.plaintext_secret`. Pero el `response_model` de este endpoint es `ApiResponse[DeviceOut]`
(`devices.py:30`), y `DeviceOut` **no tiene ese campo** (`app/schemas/device.py:57`). El esquema
que sí lo expone, `DeviceRegisterResponse` (`app/schemas/device.py:77`), se usa **únicamente** en
la superficie de cliente (`app/api/client/profile.py:74`).

Consecuencia: con `DEVICE_SECRET_ENFORCE=true` (`app/config.py:42`) el dispositivo se crea con
`status="pending"` (`device_service.py:152`) y necesita el secreto para activarse — secreto que el
panel nunca ve, y **no hay endpoint admin de activación** (solo
`POST /api/client/profile/devices/activate`, que requiere token de suscriptor). **Un dispositivo
dado de alta desde el panel quedaría inutilizable.** Con el flag en `false` (valor por defecto) el
dispositivo nace `active` y esto no se manifiesta.

### block / unblock / delete

- `block` body `DeviceBlockRequest` (`app/schemas/device.py:48`): `reason` (str?, ≤255). **El body
  es obligatorio** aunque `reason` sea opcional: enviar `{}` como mínimo.
- `unblock` y `delete` **sin body**. `unblock` pone `is_blocked=False`.
- 404 `"Device not found"` si el UUID no existe (`device_service.py:56-61`).
- `delete` es borrado físico. `sessions.device_id` es `SET NULL` (`app/models/session.py:26`), así
  que las sesiones históricas sobreviven pero **pierden la referencia al dispositivo**.

### `DeviceOut` — `app/schemas/device.py:57`

`id`, `subscriber_id`, `device_id`, `mac_address?`, `model?`, `brand?`, `device_type?`,
`app_version?`, `os_version?`, `last_ip?`, **`status`**, **`is_blocked`**, `block_reason?`,
`last_seen_at?`, `registered_at`.

**TRAMPA: `status` e `is_blocked` son ortogonales.**
- `Device.status` (str, `app/models/device.py:48`): `"active"` | `"pending"` | `"revoked"` —
  ciclo de vida de la identidad fuerte (secreto).
- `Device.is_blocked` (bool, `app/models/device.py:51`): bloqueo administrativo manual.

Un dispositivo puede ser `status="active"` **y** `is_blocked=true`. La UI debe mostrar ambos.
`unblock` **no** cambia `status` y `activate` **no** cambia `is_blocked`.

Además, `Device.status` no tiene nada que ver con `Subscriber.status`, que es un enum distinto con
valores distintos (`active`/`expired`/`suspended`/`banned`). Mismo nombre de campo, dominios
disjuntos.

`DeviceOut` **no expone** `android_id`, `device_fingerprint`, `serial_hash`, `user_agent`,
`updated_at`, que sí existen en el modelo — inaccesibles para el panel.

---

## 12. Sesiones IPTV — 4 endpoints (solo `/api/admin`)

Todos `require_admin_or_reseller`.

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/api/admin/sessions/live` | `list[LiveSessionOut]` (**array desnudo**) |
| GET | `/api/admin/sessions/subscriber/{sub_id}` | `ApiResponse[list[SessionOut]]` |
| DELETE | `/api/admin/sessions/subscriber/{sub_id}` | `MessageResponse` |
| DELETE | `/api/admin/sessions/{jti}` | `MessageResponse` |

### GET `/sessions/live` — `app/api/admin/sessions.py:51`

Sesiones IPTV activas ahora (`revoked_at IS NULL AND expires_at > now`), ordenadas por
`created_at DESC`.

**`.limit(200)` HARDCODEADO** (`sessions.py:67`). Sin paginación, sin filtros, sin total.
Con más de 200 sesiones concurrentes **la pantalla miente en silencio**: muestra 200 y no hay
forma de saber que hay más ni de ver las restantes. Para un "monitor de concurrencia" — que suele
ser la razón de existir del panel — esto es un techo duro.

`LiveSessionOut` (`app/api/admin/sessions.py:38`, definido inline, no en `app/schemas/`):
`session_id` (str), `subscriber_id` (str), `subscriber_username` (str), `device_id` (str|null),
`ip_address` (str|null), `created_at`, `expires_at`, `last_heartbeat_at` (datetime|null).

Nótese que `session_id`/`subscriber_id`/`device_id` son **strings** aquí (`sessions.py:70-73`
hacen `str(...)`), mientras que en `SessionOut` son UUID. Formas distintas para los mismos datos.

### HUECO CRÍTICO: no se puede revocar una sesión concreta

`DELETE /sessions/{jti}` (`sessions.py:127`) espera el `access_token_jti`
(`session_service.py:98-104`, `app/models/session.py:30`).

**Ningún response_model expone ese campo:**
- `LiveSessionOut` devuelve `session_id = str(Session.id)` — la PK, **no** el jti (`sessions.py:70`).
- `SessionOut` (`app/schemas/session.py:6-19`) lista 11 campos y `access_token_jti` no está.

Por tanto el panel **no tiene forma de obtener el `jti`** y este endpoint es inalcanzable desde la
UI. Lo único que se puede hacer es la revocación **masiva por suscriptor**
(`DELETE /sessions/subscriber/{sub_id}`), que tira **todas** sus sesiones.

Traducción para el diseño: la pantalla "sesiones en vivo" puede listar, pero el botón
"cortar esta sesión" **no se puede implementar**; solo "cortar todas las de este cliente".

### TRAMPA: `DELETE /sessions/{jti}` devuelve 200 con el error dentro

`sessions.py:153-155`:

```python
if not ok:
    return MessageResponse(message="Session not found or already revoked")
return MessageResponse(message="Session revoked")
```

Ambos casos son **200 con `success: true`**. Distinguir éxito de fracaso obliga a comparar el
string del mensaje. No hay 404.

Lo mismo, en menor grado, en `DELETE /sessions/subscriber/{sub_id}`: devuelve
`"0 session(s) revoked"` con 200 (`sessions.py:124`); el conteo solo está en el texto.

### GET `/sessions/subscriber/{sub_id}` — `app/api/admin/sessions.py:83`

Query: `only_active` (bool, default **`true`**, `sessions.py:86`). Es un parámetro simple, no
`Query(...)`: FastAPI acepta `?only_active=false`, `0`, `no`. Con `false` devuelve **todo el
historial sin paginación** — para un cliente antiguo pueden ser cientos de filas.

**No valida que el suscriptor exista**: devuelve `{"success":true,"data":[]}` para un UUID
inventado (`session_service.py:265-278` no comprueba nada).

### `SessionOut` — `app/schemas/session.py:6`

`id`, `subscriber_id`, `device_id?`, `device_fingerprint?`, `ip_address?`, `user_agent?`,
`created_at`, `expires_at`, `last_heartbeat_at?`, `revoked_at?`, **`is_active`**.

**`is_active` es calculado, no una columna** (`app/schemas/session.py:21-28`):
`revoked_at is None and expires_at > now`, evaluado **en el momento de serializar**. Se calienta
con el reloj del servidor. Contrasta con `Subscription.is_active`, que sí es columna y **no**
mira `expires_at` (§9). Mismo nombre de campo, semánticas opuestas.

### Higiene automática

Una tarea de fondo (`app/main.py:32-57`) marca `revoked_at` en sesiones expiradas cada 15 minutos.
Las sesiones expiradas ya están excluidas de los listados activos; la tarea solo evita registros
fantasma. Duración por defecto de una sesión IPTV: **4 horas** (`app/services/session_service.py:35`).

---

## 13. Canales — 3 endpoints (solo `/api/admin`, **solo lectura**)

Todos `require_admin_or_reseller`, todos **sin envoltorio**.

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/api/admin/channels` | `list[ChannelAdminOut]` (array desnudo) |
| GET | `/api/admin/channels/{channel_id}` | `ChannelAdminOut` |
| GET | `/api/admin/channels/{channel_id}/stream-status` | `StreamStatusOut` |

### **HUECO: el catálogo es de solo lectura. No hay POST, PATCH ni DELETE.**

Verificado exhaustivamente: `app/api/admin/channels.py` solo declara tres `@router.get`
(líneas 24, 33, 46). El módulo lo declara explícitamente: *"Flussonic/Astra are NEVER modified
from here. This is local catalog only"* (`channels.py:2-5`).

**No se puede construir ninguna pantalla de alta/edición/borrado de canales.** El catálogo se
puebla por SQL, migración o script. Consecuencias directas para el panel:
- No hay "añadir canal", "cambiar el número", "subir logo", "asignar `stream_key`".
- `is_active` de un canal **no se puede alternar** desde la API → no hay "apagar un canal".
- `ChannelService` (`app/services/channel_service.py`) tampoco tiene métodos de escritura, así que
  no es solo el endpoint el que falta: es toda la capa.

### GET `/channels` — `app/api/admin/channels.py:24`

Todos los canales, activos e inactivos, ordenados por `number` (`channel_service.py:60-62`).
**Sin paginación ni filtros.** Un catálogo IPTV típico son cientos o miles de filas en una única
respuesta; el filtrado por categoría / búsqueda por nombre hay que hacerlo en cliente.

### `ChannelAdminOut` — `app/schemas/channel.py:31`

`id`, `channel_key`, `number`, `name`, `category?`, `logo_url?`, **`stream_key`**, `source_type`,
`source_url?`, `epg_id?`, `is_active`, `requires_subscription`, `created_at`, `updated_at`.

**No expone `flussonic_node` ni `hls_path`**, que sí existen en el modelo
(`app/models/channel.py:21-22`). Un canal se sirve desde un nodo concreto (`ec-main`, `co-main`,
`ec-quito`), pero **el panel no puede saber de cuál** → no se puede correlacionar
`/nodes/health` con los canales afectados por una caída. Hueco relevante para una pantalla de
operaciones.

`stream_key` sí se expone, y **también a los resellers**. Es el nombre del stream en Flussonic;
combinado con la URL del nodo permite construir la URL HLS. Valorar si debe verlo un reseller.

### GET `/channels/{id}/stream-status` — `app/api/admin/channels.py:46`

`StreamStatusOut` (`app/schemas/channel.py:6`): `stream_key`, `alive` (bool),
`client_count` (int), `input_alive` (bool), `flussonic_configured` (bool).

Errores — **tres formas distintas en un solo endpoint** (ya detallado en §4):
- **503** `{"detail": "Flussonic integration is not configured."}` (`channels.py:62`)
- **404** `{"success": false, "error": "Channel not found"}` (`channels.py:70`)
- **404** `{"detail": "Stream 'x' not found in Flussonic."}` (`channels.py:74`)

Es una llamada síncrona a Flussonic: si el nodo está lento, la petición se bloquea. **No llamar en
bucle sobre una lista de canales** — no hay endpoint de estado en lote (§15).

**INCERTIDUMBRE:** no he leído `app/integrations/flussonic_client.py`, así que no puedo afirmar el
timeout ni qué pasa si Flussonic tarda. Este endpoint, a diferencia de `/flussonic/streams`, **no
tiene `try/except`** alrededor de la llamada (`channels.py:72`), así que una excepción del cliente
saldría como **500 genérico**.

---

## 14. Métricas y observabilidad — 4 endpoints (solo `/api/admin`)

`app/api/admin/metrics.py:27` declara el router **sin prefijo**, así que estas rutas cuelgan
directamente de `/api/admin`. Todos `require_admin_or_reseller`.

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/api/admin/metrics` | `SystemMetrics` |
| GET | `/api/admin/nodes/health` | `list[NodeHealth]` |
| GET | `/api/admin/alerts` | `{"active": [...]}` (sin tipar) |
| GET | `/api/admin/audit` | array de dicts (sin tipar) |

### GET `/metrics` — `app/api/admin/metrics.py:52`

`SystemMetrics` (`metrics.py:31`):

```json
{
  "timestamp": "2026-07-27T...+00:00",
  "active_iptv_sessions": 41,
  "redis_healthy": true,
  "redis_latency_ms": 0.42,
  "postgres_healthy": true,
  "flussonic_configured": true,
  "flussonic_reachable": true,
  "playback": {
    "authorize_total": 0, "authorize_success": 0, "authorize_failure": 0,
    "failure_rate": 0.0, "failure_by_reason": {"CHANNEL_NOT_INCLUDED": 3}
  }
}
```

Trampas:
- **`postgres_healthy` está hardcodeado a `true`** (`metrics.py:93`). El razonamiento es que si la
  query anterior falló, el endpoint ya habría dado 500. **Nunca vale `false`** — no pintar un
  semáforo que no puede ponerse en rojo.
- `flussonic_reachable` es **`true` | `false` | `null`**; `null` significa "no comprobado porque no
  está configurado" (`metrics.py:38,82-84`). Tres estados, no dos.
- `playback` es `dict` sin esquema (`metrics.py:40`). La forma real la produce
  `MetricsService.playback_snapshot` (`app/services/metrics_service.py:36-51`). `failure_by_reason`
  tiene **claves dinámicas** (códigos normalizados a mayúsculas, ≤48 chars, `metrics_service.py:21-22`);
  el panel no puede asumir un conjunto fijo.
- Los contadores son **acumulados desde siempre**, sin TTL (`metrics_service.py:4-5`). `failure_rate`
  es histórico total, **no** una tasa reciente. Para una gráfica de tendencia hay que muestrear y
  derivar en el frontend.
- `active_iptv_sessions` se cuenta en BD (`metrics.py:62-68`), no en Redis.

### GET `/nodes/health` — `app/api/admin/metrics.py:100`

Array de `NodeHealth` (`metrics.py:42`): `node_id`, `host`, `region?`, `configured`, `reachable`,
`latency_ms?`, `stream_count?`.

Nodos posibles, **fijos en código** (`app/services/node_health.py:12-16`): `ec-main` (EC),
`co-main` (CO), `ec-quito` (EC). No configurables por API.

**Trampa:** `check_all_nodes` solo itera sobre `configured_node_ids()`
(`node_health.py:19-21,53-54`), que filtra los que tienen `base_url` no vacía. **Un nodo no
configurado no aparece en absoluto en el array**; el campo `configured` valdrá `true` en la
práctica para todas las filas devueltas. El panel no puede distinguir "nodo apagado" de "nodo que
nunca se configuró": simplemente no está.

`host` es solo `host:port` (`node_health.py:45`), nunca credenciales.
`stream_count` es `null` si el nodo no responde o si falló el listado (`node_health.py:38-41`).

Es una llamada síncrona y **secuencial** a cada nodo (`node_health.py:54` es un list comprehension
con `await`, no `gather`). Con tres nodos y uno caído, la respuesta tarda lo que tarde su timeout.

### GET `/alerts` — `app/api/admin/metrics.py:109`

Sin `response_model`. `{"active": [ {...} ]}`. Cada alerta (`app/services/alert_service.py:34-36`):
`{"kind": "node", "id": "co-main", "status": "down", "detail": "host=... configured=True"}`.

Solo existe hoy `kind="node"`. Las alertas las abre y cierra la tarea de fondo cada 2 minutos
(`app/main.py:29,60-76`), y **se resuelven solas** al recuperarse (`alert_service.py:39-42`).
**No hay endpoint para reconocer / silenciar / cerrar una alerta manualmente.** El array vacío es
el estado normal.

### GET `/audit` — `app/api/admin/metrics.py:119`

**Array desnudo de dicts**, sin `response_model` (`metrics.py:131-143`):
`id` (str), `actor_username` (str|null), `action` (str), `target_type` (str|null),
`target_id` (str|null), `details` (objeto JSON|null), `ip_address` (str|null),
`created_at` (str ISO|null).

Query params (`metrics.py:121-124`) — **ninguno usa `Query(...)`, así que no hay validación de rango**:

| Param | Tipo | Default | Notas |
|---|---|---|---|
| `action` | str? | null | **igualdad exacta**, no `LIKE` (`app/services/audit_service.py:24`) |
| `actor` | str? | null | igualdad exacta contra `actor_username` (`audit_service.py:26`) |
| `limit` | int | 50 | **se recorta en servidor a `[1, 200]`** (`audit_service.py:27`) |
| `offset` | int | 0 | negativos se recortan a 0 (`audit_service.py:27`) |

**Trampa:** `limit=1000` **no da error**, devuelve 200 filas en silencio. `limit=-5` devuelve 1.

**HUECO: no hay `total`.** No se puede construir un paginador con número de páginas; solo
"cargar más" hasta que vuelva un array más corto que el `limit`. Tampoco hay **filtro por rango
de fechas**, ni por `target_type`/`target_id` — no se puede responder "qué le pasó a este
suscriptor", que es la consulta natural sobre un audit log.

Acciones que se escriben (grep de `audit.log(`): `auth.login`, `user.create`, `user.update`,
`user.delete`, `subscriber.create`, `subscriber.update`, `subscriber.password_change`,
`subscriber.suspend`, `subscriber.activate`, `subscriber.delete`, `plan.create`, `plan.update`,
`plan.delete`, `device.register`, `device.block`, `device.unblock`, `device.delete`,
`subscription.create`, `subscription.renew`, `subscription.cancel`.
**No se auditan:** `auth.logout`, `users/me/change-password`, ni ninguna revocación de sesión
desde `/sessions/*`. La tabla es append-only a nivel de BD (`audit_service.py:20-21` menciona que
la migración 007 bloquea UPDATE/DELETE).

---

## 15. Flussonic — 3 endpoints (solo `/api/admin`, **solo lectura**)

Todos `require_admin_or_reseller`, todos sin envoltorio. Esquemas definidos **inline** en
`app/api/admin/flussonic.py:23,31`, no en `app/schemas/`.

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/api/admin/flussonic/health` | `FlussonicHealthOut` |
| GET | `/api/admin/flussonic/streams` | `list[FlussonicStreamItem]` |
| GET | `/api/admin/flussonic/streams/{stream_name}` | `FlussonicStreamItem` |

`FlussonicHealthOut`: `configured` (bool), `reachable` (bool), `base_url_host` (str, solo
`host:port`, nunca credenciales — `flussonic.py:51-54`).
Este endpoint **siempre devuelve 200**, incluso sin configurar (`flussonic.py:42-47`): devuelve
`configured=false, reachable=false, base_url_host=""`. No hay error que capturar.

`FlussonicStreamItem`: `name`, `alive`, `client_count`, `hls_url`.

Errores de `/streams`: **503** si no está configurado (`flussonic.py:74-77`), **502**
`"Flussonic unreachable: {exc}"` si la llamada falla (`flussonic.py:81-82`) — ambos con forma
`{"detail": ...}`. `/streams/{name}` da **503** o **404** (`flussonic.py:103,107`), pero **no
tiene `try/except`**: un fallo de red saldría como 500 genérico.

Trampas:
- Estos endpoints leen **solo el nodo primario**. `_flussonic = get_flussonic_client()` es un
  singleton de módulo (`flussonic.py:20`, `channels.py:21`, `metrics.py:28`), evaluado **en el
  import**: cambiar la configuración exige reiniciar el proceso. Para el estado multi-nodo hay que
  usar `/nodes/health` (§14), que sí itera por nodo.
- `hls_url` la construye el cliente (`flussonic.py:89`). **INCERTIDUMBRE:** no he leído
  `flussonic_client.py`, así que no puedo afirmar si esa URL es directamente reproducible desde el
  navegador o si requiere token de playback. No asumirlo.
- **No hay ningún endpoint de escritura hacia Flussonic** — está declarado como invariante de
  diseño (`flussonic.py:9`: *"All operations are READ-ONLY. Write methods raise RuntimeError"*).
  No se pueden arrancar, parar ni reconfigurar streams desde el panel.
- **No hay correlación canal ↔ stream.** `/flussonic/streams` devuelve nombres de stream y
  `/channels` devuelve `stream_key`; casarlos es trabajo del frontend, y `ChannelAdminOut` no dice
  en qué nodo vive el canal (§13).

---

## 16. Paginación — resumen operativo

**Solo 2 de los 48 endpoints paginan de verdad.**

| Endpoint | Paginación | Parámetros | Total | Orden |
|---|---|---|---|---|
| `GET /users` | ✅ `PaginatedResponse` | `page` (≥1), `page_size` (1–200) | ✅ `total`+`pages` | ❌ ninguno |
| `GET /subscribers` | ✅ `PaginatedResponse` | `page` (≥1), `page_size` (1–200) | ✅ `total`+`pages` | ❌ ninguno |
| `GET /audit` | ⚠️ offset/limit | `limit` (recortado a 200), `offset` | ❌ **sin total** | `created_at DESC` |
| `GET /sessions/live` | ❌ tope duro | — | ❌ | `created_at DESC`, **`LIMIT 200` fijo** |
| `GET /channels` | ❌ | — | ❌ | `number ASC` |
| `GET /plans` | ❌ | — | ❌ | ❌ |
| `GET /devices/subscriber/{id}` | ❌ | — | ❌ | ❌ |
| `GET /sessions/subscriber/{id}` | ❌ | `only_active` | ❌ | `created_at DESC` |
| `GET /subscribers/{id}/subscriptions` | ❌ | — | ❌ | `starts_at DESC` |
| `GET /flussonic/streams` | ❌ | — | ❌ | el de Flussonic |
| `GET /nodes/health` | ❌ | — | ❌ | orden fijo de `_NODES` |

### Riesgos concretos de rendimiento

1. **`GET /channels` sin paginación.** Un catálogo IPTV real son cientos o miles de canales, cada
   uno con 14 campos. Es la carga más pesada de la superficie admin y no hay forma de acotarla.
   Mitigación en frontend: cachear agresivamente y virtualizar la lista.
2. **`GET /sessions/live` capado a 200.** No es lento, es **incorrecto** por encima de 200 sesiones
   concurrentes: no hay indicador de truncamiento. Necesita cambio de backend.
3. **`GET /subscribers` pagina, pero sin `ORDER BY`.** `subscriber_service.py:44-47` aplica
   `OFFSET/LIMIT` sin orden determinista. Postgres no garantiza consistencia entre páginas:
   **pueden aparecer filas duplicadas o perderse filas al navegar**. Mismo problema en `GET /users`
   (`user_service.py:28`). Esto ya es un bug hoy, no solo a escala.
4. **`GET /audit` sin `total`.** Solo permite "cargar más", no un paginador numerado.
5. `GET /sessions/subscriber/{id}?only_active=false` devuelve el historial íntegro sin tope.

---

## 17. Huecos: qué necesita un panel completo y la API no ofrece

Ordenados por impacto sobre qué pantallas pueden existir.

### Bloqueantes — hay pantallas que directamente no se pueden construir

| # | Hueco | Evidencia | Pantalla imposible |
|---|---|---|---|
| 1 | **Sin CRUD de canales.** Solo `GET`. No hay create/update/delete ni en el endpoint ni en `ChannelService`. | `app/api/admin/channels.py:24,33,46`; `app/services/channel_service.py` sin métodos de escritura | Gestión de catálogo: alta de canal, editar número/nombre/logo, mapear `stream_key`, activar/desactivar |
| 2 | **Sin gestión de `plan_channels`.** Cero endpoints tocan la tabla. Solo lectura en `channel_service.py:130` y `entitlement_service.py:133`. | búsqueda global de `PlanChannel` | "Qué canales incluye este plan" y su editor. **Con `ENTITLEMENT_ENFORCE=true` la tabla decide quién ve qué**, y solo se puede poblar por SQL |
| 3 | **Sin búsqueda de suscriptores.** Solo filtro por `status`. `username`/`email`/`id_cedula` están indexados pero no expuestos. | `app/api/v1/subscribers.py:22-24`; `app/services/subscriber_service.py:33-49` | Buscador de clientes — la pantalla más usada de un panel IPTV |
| 4 | **Sin revocación de sesión individual usable.** El endpoint existe pero el `jti` no se expone en ningún response_model. | `app/api/admin/sessions.py:127` vs `sessions.py:38-48` y `app/schemas/session.py:6-19` | Botón "cortar esta sesión" en el monitor de concurrencia |
| 5 | **Sin reseteo de contraseña de usuarios admin/reseller.** `UserUpdate` no tiene campo password. | `app/schemas/user.py:16-21` | "Resetear contraseña" en la ficha de usuario |
| 6 | **Sin scoping de reseller.** `created_by` existe pero ningún listado filtra por él. | `app/services/subscriber_service.py:33-49` | Un panel multi-reseller seguro. Hoy cada reseller ve y borra la cartera de los demás |

### Importantes — la pantalla existe pero queda coja

| # | Hueco | Evidencia |
|---|---|---|
| 7 | **Sin listado global de suscripciones.** Solo por suscriptor. | `app/api/admin/subscriptions.py:82` |
| 8 | **Sin "próximos vencimientos"**. Consecuencia de (7): no se puede listar "suscripciones que vencen en 7 días", que es el motor comercial de un negocio IPTV. | — |
| 9 | **Sin dashboard agregado.** No hay contadores (suscriptores por estado, altas del mes, ingresos). `/metrics` es salud técnica, no negocio. | `app/api/admin/metrics.py:31-40` |
| 10 | **Sin listado global de dispositivos.** Solo por suscriptor. No se puede buscar "¿de quién es esta MAC?". | `app/api/v1/devices.py:18` |
| 11 | **Sin activación admin de dispositivos** ni exposición del `device_secret` en el registro admin. | `app/api/v1/devices.py:30` (`DeviceOut`) vs `app/schemas/device.py:77` (`DeviceRegisterResponse`, solo en cliente) |
| 12 | **Audit sin `total`, sin rango de fechas, sin filtro por target.** No se puede responder "historial de este suscriptor". | `app/api/admin/metrics.py:119-130`; `app/services/audit_service.py:13-28` |
| 13 | **Sin métricas históricas.** `/metrics` es un snapshot; los contadores de playback son acumulados desde siempre, sin ventana. | `app/services/metrics_service.py:4-5,44-50` |
| 14 | **Sin gestión de EPG.** El cliente lee EPG (`app/api/client/catalog.py:39`) pero no hay administración. | — |
| 15 | **Sin gestión de nodos Flussonic.** Los tres nodos están fijos en código y sus URLs en `.env`. | `app/services/node_health.py:12-16` |
| 16 | **Sin operaciones en lote.** Nada de "suspender N suscriptores", "renovar en masa", "estado de todos los streams". Cada acción es una petición, y el rate limit global es 60/min por IP. | `app/middleware/rate_limit.py`; ausencia de endpoints bulk |
| 17 | **Sin gestión ni silenciado de alertas.** Solo lectura; se abren y cierran solas. | `app/api/admin/metrics.py:109`; `app/services/alert_service.py:26-43` |
| 18 | **Sin ordenación configurable** en ningún listado. | §16 |
| 19 | **No se puede vaciar un campo opcional** en ningún PATCH (`exclude_none=True`). | `user_service.py:55`, `subscriber_service.py:76`, `plan_service.py:37` |
| 20 | **No se puede cambiar el `username`** de un suscriptor ni de un usuario. | `app/schemas/subscriber.py:18`, `app/schemas/user.py:16` |
| 21 | **`starts_at` no configurable** al crear suscripción → no hay altas con fecha futura. | `app/schemas/subscription.py:14-19` |
| 22 | **`ChannelAdminOut` no expone `flussonic_node`/`hls_path`** → no se puede correlacionar caída de nodo con canales afectados. | `app/schemas/channel.py:31-48` vs `app/models/channel.py:21-22` |
| 23 | **Sin protección de integridad de admins**: nada impide auto-borrado, auto-desactivación o quedarse sin admins. | `app/services/user_service.py:52-68` |

---

## 18. Trampas — lo que sorprendería a quien consuma la API a ciegas

Consolidado. Cada una está detallada en su sección.

### Rutas y montaje
1. **Doble montaje**: 30 handlers en `/api/v1` **y** `/api/admin`; 18 solo en `/api/admin`. Usa siempre `/api/admin`. (§1)
2. Los contadores de rate limit son **por ruta literal**: `/api/v1/auth/login` y `/api/admin/auth/login` son presupuestos separados. (§1)
3. `POST /api/v1/devices/register` está en la tabla de rate limits (`rate_limit.py:20`) pero **la ruta real lleva `/{sub_id}`**, y el matching es por igualdad exacta (`rate_limit.py:52`) → **ese límite de 5/min nunca se aplica**. Cae al global de 60. (§5)

### Formas de respuesta y de error
4. **Cuatro formas de respuesta** conviven: `ApiResponse`, `PaginatedResponse`, `MessageResponse` y **sin envoltorio**. No asumas `response.data`. (§3)
5. **Tres formas de error**: `{success,error[,reason_code]}`, `{detail: "..."}` y `{detail: [...]}` (422). El parser debe cubrir las tres. (§4)
6. `GET /channels/{id}/stream-status` puede devolver **dos 404 con formas distintas** según la causa. (§13)
7. `POST /auth/login` y `/auth/refresh` **no llevan campo `success`**. (§6)
8. `GET /auth/me`, `/alerts` y `/audit` **no tienen `response_model`** → no aparecen tipados en el OpenAPI; el frontend no puede generar tipos fiables. (§3)

### Estados 200 que en realidad son errores
9. `DELETE /sessions/{jti}` devuelve **200 `success:true`** con `"Session not found or already revoked"`. No hay 404. (§12)
10. `DELETE /sessions/subscriber/{id}` devuelve 200 con `"0 session(s) revoked"`; el conteo solo está en el texto. (§12)
11. `POST /subscribers/{id}/subscriptions/{id}/cancel` devuelve el número de sesiones cortadas **dentro del string** del mensaje. (§9)
12. `POST /devices/register/{sub_id}` devuelve **201** también cuando solo actualizó un dispositivo existente. (§11)
13. `GET /sessions/subscriber/{id}` devuelve `data: []` con 200 para un suscriptor inexistente, no 404. (§12)

### Códigos de estado que engañan
14. **`GET /subscribers/{id}/status` devuelve 401 (no 404)** si el suscriptor no existe. Un interceptor que trate 401 como "sesión caducada" **cerrará la sesión del admin**. (§8)
15. **`DELETE /plans/{id}` devuelve 500 (no 409)** si el plan tiene suscripciones (FK RESTRICT → `IntegrityError` → handler genérico). (§10)
16. Un path param no-UUID da **422**, no 404. (§7)

### Campos con el mismo nombre y distinto significado
17. **`device_id`**: UUID (`Device.id`) en rutas y en `SessionOut`; string de hardware en los bodies; `LiveSessionOut.device_id` es un **UUID serializado como string**. Tres cosas, un nombre. (§11)
18. **`is_active`**: columna en `Subscription` (solo "no cancelada", **no** mira `expires_at`); **calculado al serializar** en `SessionOut`; "tiene plan vigente" en `SubscriberActiveStatus`; "cuenta habilitada" en `User`. (§9, §12)
19. **`status`**: enum `active/expired/suspended/banned` en `Subscriber`; string `active/pending/revoked` en `Device`, **ortogonal a `is_blocked`**. (§8, §11)
20. **`max_connections` vs `max_devices`**: streams simultáneos vs dispositivos registrados. (§8)
21. En `SubscriberActiveStatus` sin plan, `max_connections=0` y `max_devices=0` significan **"sin plan"**, no "límite cero". (§8)

### Respuestas que cambian de forma
22. **`SubscriberOutFull` en POST y GET-detalle; `SubscriberOut` en lista y PATCH.** Una actualización optimista tras PATCH **borra `activation_code` y `created_by`** del estado local. (§8)
23. `SubscriptionOut.plan` viene poblado o `null` según el endpoint. (§9)
24. `flussonic_reachable` en `/metrics` tiene **tres estados**: `true`/`false`/`null`. (§14)

### Comportamientos silenciosos
25. **`exclude_none=True` en todos los PATCH**: enviar `{"notes": null}` es un **no-op que responde 200**. Imposible vaciar campos. (§7, §8, §10)
26. **Crear una suscripción desactiva la anterior sin avisar.** (§9)
27. **`renew` reactiva una suscripción cancelada** (`is_active=True` siempre) y **sobrescribe `created_by`**. (§9)
28. **`os_version` se trunca a 32 chars en silencio** en vez de dar 422. (§11)
29. `activation_code` **se autogenera** si no lo envías, y viaja en claro en las respuestas. Es una credencial. (§8)
30. **`GET /sessions/live` está capado a 200** sin ningún indicador de truncamiento. (§12)
31. **`GET /audit?limit=1000` devuelve 200 filas** sin error; `limit=-5` devuelve 1. (§14)
32. **`GET /nodes/health` omite por completo los nodos no configurados** — no se distinguen de los inexistentes. (§14)
33. **El límite `max_devices` no se aplica si el suscriptor no tiene suscripción activa.** (§11)

### Auth y sesión del panel
34. **El refresh rota y revoca el token anterior.** Dos refresh concurrentes → el segundo da 401 y expulsa al usuario. **Serializa el refresh en un único promise.** (§6)
35. **El bloqueo por intentos fallidos se aplica también por IP**: 5 fallos desde la IP de la oficina bloquean 15 min el login de **todos**. (§5)
36. **`POST /api/admin/devices/heartbeat` exige token STB, no de admin** → 401 con el token del panel. Está en el OpenAPI pero es inutilizable. (§11)
37. `POST /auth/logout` no valida que el usuario exista ni esté activo (usa `_get_token_payload`, no `get_current_user`). (§6)
38. Un **reseller no puede leer su propio perfil completo**: `GET /users/{id}` es admin-only y `/auth/me` devuelve 5 campos. (§6)
39. **CORS con orígenes hardcodeados** en `app/main.py:111-117`. Cualquier otro origen es rechazado sin tocar el código. En `debug=true` se abre a `*` **pero con `allow_credentials=False`**. (§5)
40. `/health` devuelve la clave **`version` dos veces** (`app/main.py:176` y `178`); en el JSON final gana `"2.0.0"`. Curiosidad, no problema.

### Rendimiento y consistencia
41. **`GET /users` y `GET /subscribers` paginan sin `ORDER BY`** → filas duplicadas o perdidas al pasar de página. Bug real, hoy. (§16)
42. **`GET /channels` sin paginación** — la respuesta más pesada de la superficie. (§13)
43. `GET /channels/{id}/stream-status` y `/nodes/health` hacen **llamadas de red síncronas** a Flussonic (y `/nodes/health` **secuenciales**, no en paralelo). No llamarlos en bucle ni en polling agresivo. (§13, §14)
44. Los contadores de `playback` en `/metrics` son **acumulados desde siempre**; `failure_rate` es histórico total, no reciente. (§14)

### Otros
45. **`PlanUpdate.price` perdió `ge=0`** (existe en `PlanCreate`): se aceptan precios negativos vía PATCH. (§10)
46. **Los cambios de plan afectan retroactivamente** a las suscripciones vivas: los límites se leen del `Plan` en cada comprobación. (§10)
47. **Borrar un usuario anonimiza el historial** (`created_by` → NULL en suscriptores y suscripciones). (§7)
48. **Borrar un suscriptor es cascada total** (dispositivos, suscripciones, sesiones). Sin soft-delete. (§8)
49. `PlanOut` no incluye `updated_at` aunque la columna existe. (§10)
50. `DeviceOut` no expone `android_id`, `device_fingerprint`, `user_agent` ni `updated_at`. (§11)
51. Los `_flussonic = get_flussonic_client()` son **singletons de módulo evaluados en el import**: cambiar configuración exige reiniciar. (§15)

---

## 19. Incertidumbres declaradas

No determinables leyendo solo el código que he revisado. **No asumir; verificar contra la API real.**

1. **Serialización de `Decimal` (`PlanOut.price`)** — Pydantic v2 tiende a emitir string (`"9.99"`).
   No verificado ejecutando. El frontend debe aceptar `string | number`.
2. **Timeouts y comportamiento de red de `FlussonicClient`** — no he leído
   `app/integrations/flussonic_client.py`. Desconocido: timeout, reintentos, y si una excepción en
   `GET /channels/{id}/stream-status` (sin `try/except`, `channels.py:72`) sale como 500.
3. **`hls_url` en `FlussonicStreamItem`** — no sé si es reproducible directamente desde el navegador
   o si requiere token de playback (`SIGNED_URL_ENFORCE`, `app/config.py:41`).
4. **Valores reales de configuración en producción** — todos los defaults citados salen de
   `app/config.py`. Los flags (`entitlement_enforce`, `jwt_require_aud`, `device_secret_enforce`,
   `catalog_entitlement_filter`, `stb_auth_enforce`, `debug`) vienen de `.env` y **cambian el
   comportamiento observable**. Confirmar los valores del entorno destino.
5. **Serialización exacta de datetimes** — son `DateTime(timezone=True)`; asumo ISO-8601 con offset,
   pero no lo he verificado contra una respuesta real. `LiveSessionOut` los declara `datetime`,
   mientras `/audit` los emite ya como string con `.isoformat()` (`metrics.py:140`).
6. **Estado real de la tabla `plan_channels`** — si está vacía y `ENTITLEMENT_ENFORCE` se activa,
   `EntitlementService` (`app/services/entitlement_service.py:133-136`) denegaría todo. No he
   consultado la BD.
7. **`GET /alerts` y `GET /audit` sin `response_model`** — la forma documentada aquí sale del código
   que construye el dict, no de un contrato declarado. Un cambio en el servicio la altera sin que
   el OpenAPI lo refleje.
8. **Comportamiento del array vacío en `/nodes/health`** — si ningún nodo está configurado devuelve
   `[]`; no he confirmado qué hace el frontend actual con eso.

---

## Apéndice — índice rápido de las 48 rutas

Prefijo asumido `/api/admin`. Marcadas con `†` las que **también** existen bajo `/api/v1`.

```
POST   /auth/login                                              † público
POST   /auth/refresh                                            † público
POST   /auth/logout                                             † bearer
GET    /auth/me                                                 † autenticado

GET    /users                                                   † admin        paginado
POST   /users                                                   † admin        201
GET    /users/{user_id}                                         † admin
PATCH  /users/{user_id}                                         † admin
DELETE /users/{user_id}                                         † admin
POST   /users/me/change-password                                † autenticado

GET    /subscribers                                             † admin|resell paginado
POST   /subscribers                                             † admin|resell 201
GET    /subscribers/{sub_id}                                    † admin|resell
PATCH  /subscribers/{sub_id}                                    † admin|resell
POST   /subscribers/{sub_id}/set-password                       † admin|resell
GET    /subscribers/{sub_id}/status                             † admin|resell  404→401
POST   /subscribers/{sub_id}/suspend                            † admin|resell
POST   /subscribers/{sub_id}/activate                           † admin|resell
DELETE /subscribers/{sub_id}                                    † admin|resell  cascada

GET    /devices/subscriber/{sub_id}                             † admin|resell
POST   /devices/register/{sub_id}                               † admin|resell 201
POST   /devices/heartbeat                                       † TOKEN STB
POST   /devices/{device_id}/block                               † admin|resell
POST   /devices/{device_id}/unblock                             † admin|resell
DELETE /devices/{device_id}                                     † admin|resell

GET    /plans                                                   † admin|resell
POST   /plans                                                   † ADMIN        201
GET    /plans/{plan_id}                                         † admin|resell
PATCH  /plans/{plan_id}                                         † ADMIN
DELETE /plans/{plan_id}                                         † ADMIN         500 si en uso

POST   /subscribers/{sub_id}/subscriptions                        admin|resell 201
GET    /subscribers/{sub_id}/subscriptions                        admin|resell
POST   /subscribers/{sub_id}/subscriptions/{subscription_id}/renew   admin|resell
POST   /subscribers/{sub_id}/subscriptions/{subscription_id}/cancel  admin|resell

GET    /metrics                                                   admin|resell
GET    /nodes/health                                              admin|resell
GET    /alerts                                                     admin|resell
GET    /audit                                                     admin|resell

GET    /sessions/live                                             admin|resell  LIMIT 200
GET    /sessions/subscriber/{sub_id}                              admin|resell
DELETE /sessions/subscriber/{sub_id}                              admin|resell
DELETE /sessions/{jti}                                            admin|resell  jti no obtenible

GET    /channels                                                  admin|resell
GET    /channels/{channel_id}                                     admin|resell
GET    /channels/{channel_id}/stream-status                       admin|resell

GET    /flussonic/health                                          admin|resell
GET    /flussonic/streams                                         admin|resell
GET    /flussonic/streams/{stream_name}                           admin|resell
```

Fuera de alcance de este documento (existen, pero no son superficie de administración):
`/api/stb/*` (dispositivos), `/api/client/*` (apps de suscriptor), `/api/subscriber/ping`,
`/internal/stream-auth/validate` (auth de borde para Flussonic), `/health`.

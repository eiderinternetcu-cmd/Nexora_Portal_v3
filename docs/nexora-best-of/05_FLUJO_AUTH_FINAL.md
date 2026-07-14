# 05 — Flujo final de autenticación (resumen)

> **Resumen ejecutivo.** La investigación profunda módulo-por-módulo (extracción legacy, matriz, riesgos por severidad, diseño de submódulos, endpoints, modelo de datos, Redis, JWT, flujos, backlog y pruebas) está en **[modules/01_AUTH_DEEP.md](modules/01_AUTH_DEEP.md)**. Este documento da la visión general.

---

## Qué decidimos (y por qué)

- **Argon2id** para todo hash — ya implementado en Nexora (cierra salt-fijo de Xtream y MD5 de Ministra). ✅
- **JWT con `aud` + `iss` + `type`** y **validación estricta por endpoint**: separación real admin/client/stb/playback por *audiencia*, no solo por string `type`. (Hoy falta `aud` → gap a cerrar.)
- **Refresh híbrido PG + Redis**: tabla `client_refresh_tokens` (hash + device + family) para reuse-detection y forense + Redis para ruta rápida. Rotación single-use.
- **Revocación unificada a allowlist**: la sesión debe existir en Redis ⇒ suspender/cancelar invalida al instante (admin migra de blacklist a allowlist).
- **Rutas admin estandarizadas** a `/api/admin/auth/*` (hoy `/api/v1/auth/*`).
- **Identidad de dispositivo fuerte**: `device_secret`/cert + activación; sin auto-provisión (MAC sola insuficiente).
- **IP real** desde proxy de confianza (no `X-Forwarded-For` arbitrario).
- **Auditoría inmutable** de login admin + `login_attempts` persistidos (forense que el legacy no tenía).
- **Nunca** credenciales en URL/logs/M3U; `playback_token` jamás como access.

## Estado en Nexora (alto nivel)

| Capacidad | Estado |
|---|---|
| Argon2id, JWT fijo, rotación de refresh, lockout, separación de routers, allowlist cliente, status check | ✅ |
| `aud`/`iss`, admin allowlist, `client_refresh_tokens` PG, `device_sessions`/logout-all, IP confiable, audit login | 🟡/⬜ |
| STB handshake endurecido, rutas `/api/admin/auth/*` formales, reuse-detection family | ⬜ |

## Submódulos del AuthService
ClientAuthService · AdminAuthService · StbAuthService · RefreshTokenService · DeviceSessionService · LoginRateLimitService · PasswordPolicyService · SessionRevocationService · AuthAuditService.
→ Responsabilidades, endpoints, tablas, Redis keys, reglas y pruebas: **[modules/01_AUTH_DEEP.md §3](modules/01_AUTH_DEEP.md)**.

## Endpoints (resumen)
```
Admin:   POST /api/admin/auth/{login,refresh,logout}   GET /api/admin/me
Cliente: POST /api/client/auth/{login,refresh,logout}  GET /api/client/profile
Devices: POST /api/client/devices/{register,heartbeat,logout-all}  GET /api/client/devices
STB:     POST /api/stb/auth/{handshake,profile,logout}
```
Detalle (request/response/status/validaciones/audit/riesgos): **[§4 del deep](modules/01_AUTH_DEEP.md)**.

## Flujo cliente (resumen)
```
login → rate-limit/lockout → Argon2id → status → (suscripción en playback) →
register device → access(client_access,aud) + refresh(rotativo, hash en PG) →
allowlist Redis → audit → {tokens}.  Playback = flujo separado (doc 06).
```

## Flujo admin (resumen)
```
login → rate-limit(user+ip) → Argon2id → rol/permisos → admin_session(PG)+allowlist →
audit(admin.login) → JWT admin_access(aud=nexora-admin)+refresh.  Token cliente NUNCA en rutas admin.
```

## JWT (resumen de claims)
`sub · jti · type · aud · iss · iat · exp`. Tipos/audiencias: `admin_access`/`nexora-admin`, `client_access`/`nexora-client`, `client_refresh`, `stb_access`/`nexora-stb`, `playback_token`/`nexora-playback`. Algoritmo fijo; rechazo si falta `aud`/`type`/`jti`; revocación por `jti`. → **[§7 del deep](modules/01_AUTH_DEEP.md)**.

## Riesgos legacy descartados
Salt-fijo/MD5, `admin/admin`, sesión=hash, IDOR de playback, credenciales en URL, sin rate-limit, MAC como identidad, sin audit, sin revocación, replay. Detalle por severidad: **[§9 del deep](modules/01_AUTH_DEEP.md)**.

## Backlog y pruebas
- **Backlog AUTH-001..020** (con AC/rollback): **[§10 del deep](modules/01_AUTH_DEEP.md)**.
- **Plan de pruebas (22 casos)** + checklist de aceptación: **[§11–12 del deep](modules/01_AUTH_DEEP.md)**.

---

> Tablas PostgreSQL (`admin_*`, `subscribers`, `subscriber_credentials`, `subscriptions`, `devices`, `device_sessions`, `client_refresh_tokens`, `login_attempts`, `audit_logs`) y Redis keys (`auth:client_session`, `auth:admin_session`, `auth:refresh`, `auth:device_heartbeat`, `auth:login_fail`, `rate:login:*`, `revoked:jti:*`): **[§5–6 del deep](modules/01_AUTH_DEEP.md)**.

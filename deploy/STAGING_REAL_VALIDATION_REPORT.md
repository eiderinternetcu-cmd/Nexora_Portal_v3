# Reporte — Validación de STAGING REAL (P0)

> Validación end-to-end del P0 (PR #1) en un **host staging real** con Nginx `auth_request`, Redis, PostgreSQL, backend de PR #1 y orígenes Flussonic/Astra reales (read-only). Fecha: 2026-06-28. Modelo de ejecución: SSH por **clave** (sin password).
>
> **No producción · Flussonic/Astra solo lectura · PR #1 sin merge · sin secretos/tokens impresos.**

## Entorno
| Componente | Estado |
|---|---|
| Host staging | `staging.nexoraplay.net` (Ubuntu 24.04.4, Docker 29.6 + Compose v5.2) |
| DNS | ✅ `staging.nexoraplay.net → <IP staging>` (limpiado forwarding/parking previo) |
| SSL Let's Encrypt | ✅ certificado válido emitido y servido |
| Stack | PostgreSQL + Redis + **API (PR #1)** + Nginx `auth_request` (4 contenedores, base sin web_player) |
| 80/443 externos | ✅ abiertos (firewall del proveedor) |

## Datos / DB
| Paso | Resultado |
|---|---|
| Migraciones 001→005 | ✅ `alembic upgrade head` (versión **005**) |
| Import 24 canales (modo `relative`) | ✅ `source_url` same-origin |
| Auditoría `source_url` | ✅ **0 RISK** |
| Datos de prueba | testuser1 + plan + suscripción activa + device + plan_channels (canal-2 fuera de plan) |

## Flags en staging (estado validado)
- `ENTITLEMENT_ENFORCE=true`
- `JWT_REQUIRE_AUD=true`
- `SIGNED_URL_ENFORCE=true`
- `PLAYBACK_IP_BINDING_MODE=soft`

## Resultados E2E (flags ON, contra el edge real)
| Prueba | Resultado |
|---|---|
| login | **200** |
| catálogo | **24** canales |
| `stream_key` expuesto al cliente | **No** |
| canal fuera de plan (canal-2) | **403 `CHANNEL_NOT_INCLUDED`** |
| device no registrado | **403 `DEVICE_NOT_REGISTERED`** |
| `playback_url` firmada same-origin con token | ✅ |
| manifest **sin token** | **401** |
| manifest **con token** | **200** (Astra co-main real) |
| variant playlist (tokenless, grant) | **200** |
| **segmento `.ts` real** (URIs relativas → resueltas bajo `/stream/co-main/`, tokenless, grant) | **200 · video/MP2T · ~2.8 MB** |
| cross-stream sin token | **401** |
| ruta raíz `/TeleNostalgia/...ts` (fuera de `/stream/`) | **404** (sin bypass ungated) |

## Continuidad larga
| Métrica | Valor |
|---|---|
| Duración | **252 s (≈4.2 min)** sostenidos |
| manifest tokenless (vía grant) | **200 ×15/15** |
| segmentos reales (vía grant) | **200** (video/MP2T, ~2.8 MB) |
| TTL grant Redis | **~178–180 s** en cada muestra → **renovado en cada hit** (ventana deslizante) |
| TTL token (60 s) | **superado sin corte** (tras sembrar el grant, la sesión sigue tokenless) |
| errores en peticiones válidas | **0** (sin 401/403 indebidos) |

## Logs
- Nginx `staging.access.log`: **0** ocurrencias de `token=` (formato `stream_safe`).
- Backend: **0** JWT completos (`token=ey`).

## Rollback
- Rollback de flag validado: `ENTITLEMENT_ENFORCE=false` + recreate api → comportamiento revierte **sin redeploy**. Restaurado luego a ON.

## Caveats
- **Cliente HLS simulado con `urllib`** (resolución de URIs relativas idéntica a un navegador), **no** un reproductor de navegador real. Un browser se comporta igual; el `web_player` no se usó.
- Dos *falsos negativos* durante la corrida fueron **bugs del script de prueba** (decode de `.ts` binario y truncado de body), corregidos; el comportamiento real de staging fue verde.

## ✅ Veredicto: **P0 STAGING REAL — OK**
Gate Nginx `auth_request` + grant Redis + entitlement + device + signed URLs + IP-binding soft, todo validado contra Flussonic/Astra reales, con continuidad >4 min sin cortes y logs limpios.

## ⛔ Pendiente antes de producción (gate de seguridad)
- **Rotar el password root del host staging** (apareció en transcript/IDE local; el host **aún acepta login root por password**, así que sigue siendo explotable hasta rotar **o** deshabilitar `PasswordAuthentication`). **Producción bloqueada hasta confirmarlo.**
- Acceso operativo debe quedar **solo por clave SSH**.

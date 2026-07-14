# 07 — Flujo STB / MAG compatible

> Cómo Nexora soporta set-top-boxes (estilo MAG/Stalker y STB genéricos) **sin** heredar las debilidades de Ministra (identidad por MAC, `auto_add_stb`, tokens MD5, IDOR). Reescritura limpia con identidad fuerte y la misma autorización central que app/web.

---

## Qué aprendemos de Ministra (C) — y qué corregimos

| Legacy Ministra | Problema | Nexora |
|---|---|---|
| Handshake → `access_token = md5(microtime+uniqid)` | predecible (no CSPRNG) | token aleatorio CSPRNG + JWT de device firmado |
| Identidad = **MAC** (cookie/param) | suplantable | `device_id` + **`device_secret`/cert** + serial; MAC solo informativa |
| `auto_add_stb=true` | auto-provisión silenciosa | **activación explícita** (código/aprobación); device nace `pending` |
| Heartbeat por MAC sin token | keep-alive spoofeable | heartbeat **autenticado** con device_token |
| `createLink` sin checar suscripción (**IDOR**) | acceso a cualquier canal | **mismo** `PlaybackAuthorizationService` que app/web |
| Núcleo `doAuth/handshake` **ofuscado** | ilegible/no auditable | implementación propia, legible, testeable |

---

## Identidad de dispositivo (modelo)

Tabla `devices.devices` (doc 04): `device_id` (externo) + `mac` + `serial_hash` + `cert_fingerprint`/`device_secret_ref` + `subscriber_id` + `status(active|blocked|pending)`.

- **Provisión:** el operador registra el device (o emite **activation code**) → `status=pending`.
- **Activación:** primer handshake con activation code o secret provisionado → `status=active`.
- **Re-binding** (cambio de MAC/hardware): requiere re-activación + rate-limit (evita abuso de sharing).

---

## Endpoints STB (Nexora)

```
POST /api/stb/handshake     → reto-respuesta firmado → device_token (JWT corto)
POST /api/stb/profile       → settings + cuenta + estado + tarifa (entitlements)
GET  /api/stb/channels      → lista ordenada de canales permitidos (∩ entitlements)
POST /api/stb/playback/authorize  → token + signed URL  (alias del authorize central)
POST /api/stb/heartbeat     → presencia + eventos pendientes
```
(Hoy `nexora_api` ya tiene un router `/api/stb` con heartbeat/register/connections/playback auth 🟡; este doc define el objetivo endurecido.)

---

## 1. Handshake (reto-respuesta, no MAC sola)

```
STB → POST /api/stb/handshake
      { device_id, mac, serial, nonce_client }
  1. device = lookup(device_id)
       - no existe ⇒ 403 "device_not_provisioned"  (NO auto-add)
       - status=blocked ⇒ 403 "device_blocked"
       - status=pending ⇒ exigir activation_code (paso aparte)
  2. challenge = server_nonce
  3. STB responde HMAC(device_secret, server_nonce + nonce_client)
  4. server verifica HMAC ⇒ identidad probada
  5. validar binding device↔subscriber + subscriber.status=active
  6. emitir device_token (JWT corto) ; presencia Redis nexora:device_seen:{id}
  ← 200 { device_token, expires_in }
```
**Mejora:** sin secreto compartido válido, no hay token. La MAC es un atributo, no la credencial.

## 2. get_profile (entitlements server-side)

```
POST /api/stb/profile  (Bearer device_token)
  → { account: { status, expires_at, days_remaining },
      settings: { parental_required, locale, ... },
      entitlements: { tv:[...], vod:[...] } }   # resuelto de subscriptions→packages→contents
```
El gate de entitlements vive aquí **y** se re-evalúa en authorize (defensa en profundidad). Cierra el IDOR: el STB nunca decide qué puede ver.

## 3. Lista de canales

```
GET /api/stb/channels?genre=&order=  (Bearer device_token)
  → canales = catálogo ∩ entitlements ∩ no-censurado(si parental sin PIN)
  → devuelve channel_key + número + nombre + logo  (NUNCA stream_key)
```

## 4. Reproducción (mismo authorize central)

```
POST /api/stb/playback/authorize { channel_key }  (Bearer device_token)
  → PlaybackAuthorizationService (doc 06): suscripción+entitlement+parental+device+concurrencia
  → { token, playback_url(https signed), expires_in }
```
**No hay** `create_link` propio del STB que evite la autorización. Un solo camino.

## 5. Heartbeat + eventos push

```
POST /api/stb/heartbeat  (Bearer device_token)
  → presencia (Redis) ; devuelve eventos: cut_off/cut_on, reboot, message, update_epg
```
Eventos desde `devices.commands` (NotificationService). Heartbeat **autenticado**.

## 6. Comandos remotos (operador → STB)

```
POST /api/admin/devices/{id}/command { type, payload }  → encola en devices.commands
   el STB los recibe en el siguiente heartbeat ; marca acked
```
Equivale a `SysEvent.sendCutOff/On` de C, pero con cola persistida y auditada.

---

## Compatibilidad con clientes existentes (opcional, F3)

Para no perder el ecosistema de apps que hablan **Xtream Codes** (contrato `player_api.php`), Nexora puede exponer un **`XtreamCompatService`** read-only:

- Traduce `player_api.php?...&action=get_live_streams` etc. → Client API interna.
- **NO** acepta credenciales en URL como secreto de larga vida: emite un token de sesión y signed URLs; el `username/password` Xtream se mapea a `subscriber` real, validado server-side.
- Las URLs del M3U generado son **signed URLs** (sin credenciales, origen oculto).
- Mismo `PlaybackAuthorizationService` detrás.

> Esto da compatibilidad de mercado **sin** reintroducir credenciales-en-URL ni IDOR.

---

## Diagrama textual (STB seguro)

```
[STB MAG] --handshake(device_id,serial,nonce + HMAC(device_secret))--> [Auth/Device]
     |<-- device_token (JWT corto) -----------------------------------------
     | --profile(token)--> entitlements (subs→packages→contents)
     | --channels(token)--> canales ∩ entitlements (channel_key only)
     | --authorize(channel_key)--> [PlaybackAuth] (suscripción+parental+device+concurrencia)
     |<-- token + playback_url (https firmado, origen oculto) --------------
     | --heartbeat(token) 120s--> presencia + eventos (cut_off/reboot/msg)
```

## Errores/HTTP
| Caso | HTTP |
|---|---|
| device no provisionado | 403 `device_not_provisioned` |
| device bloqueado | 403 `device_blocked` |
| HMAC inválido | 401 `handshake_failed` |
| suscripción/entitlement | 403 |
| concurrencia | 409 |

## Checklist de seguridad STB
- [ ] Sin auto-provisión: device nuevo = `pending`.
- [ ] Handshake con HMAC(device_secret/cert), no MAC sola.
- [ ] device_token corto; heartbeat autenticado.
- [ ] Toda reproducción por `authorize` central (anti-IDOR).
- [ ] `stream_key`/origen nunca expuestos.
- [ ] Rate-limit en handshake y re-binding de MAC.
- [ ] Eventos/commands auditados.

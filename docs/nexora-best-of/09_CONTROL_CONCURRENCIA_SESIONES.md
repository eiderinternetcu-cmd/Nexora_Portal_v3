# 09 — Control de concurrencia y sesiones

> Modelo superior al legacy (A: `COUNT(*)` no atómico; C: límite de duración solo en cliente). Redis como fuente rápida y atómica; PostgreSQL como auditoría. 🟢 ya existe gran parte en `nexora_api`.

---

## Requisitos (del encargo)

- Máximo de conexiones por **suscriptor**, por **dispositivo** y por **plan**.
- Cierre por inactividad; **heartbeat obligatorio**.
- Redis = verdad rápida; PostgreSQL = auditoría/histórico.
- Liberar sesión si el player cae; evitar doble reproducción no autorizada.
- **Tolerar zapping rápido**; **no** bloquear por falsos positivos.

---

## Por qué Redis y no `COUNT(*)`

- A contaba `user_activity_now` con `COUNT(*)` → **carrera**: dos authorize simultáneos leen N<max y ambos pasan ⇒ se excede el límite.
- C medía concurrencia por streamer (mejor) pero el **límite de duración** era client-side (bypass con cliente modificado).
- **Nexora:** operaciones **atómicas** en Redis (`ZADD`/`ZCARD`/`ZREM`) con TTL, evaluación server-side; histórico en PG para auditoría.

---

## Estructura en Redis (ya implementada 🟢)

```
nexora:active_conns:{subscriber_id}   ZSET  member=device_uuid  score=expire_unix
nexora:session:{session_jti}          cache de sesión IPTV (TTL 4h)
nexora:playback:{playback_jti}        token corto (TTL 60s)
nexora:session_playbacks:{ses}        SET de playback jtis (revocación masiva)
```

PostgreSQL: `sessions` (IPTV) 🟢 + `playback.sessions` histórico particionado ⬜.

---

## Algoritmo de apertura (open_connection) 🟢

```
authorize(subscriber, device, channel):
   now = epoch()
   key = nexora:active_conns:{subscriber}
   # 1. limpiar expirados (zapping/caídas) — atómico
   ZREMRANGEBYSCORE key 0 now
   # 2. ¿device ya tiene slot? (re-uso en zapping) 
   if ZSCORE key device:  → renovar score (no consume slot nuevo)   ← tolera zapping
   else:
       # 3. ¿hay cupo?
       if ZCARD key >= plan.max_connections:  → 409 "Max concurrent connections reached"
       ZADD key (now + SESSION_TTL) device
   EXPIRE key SESSION_TTL_MARGEN
   → crear/renovar sesión IPTV en PG (audit) + emitir token playback
```

**Claves del diseño:**
- **Zapping rápido:** si el mismo `device` re-autoriza otro canal, **renueva su score** en vez de consumir un slot nuevo → no falsos `409`.
- **Atomicidad:** `ZREMRANGEBYSCORE` + `ZCARD` + `ZADD` (idealmente en script Lua / `MULTI`) evita carreras.
- **Límite por plan:** `plan.max_connections` (no global). Límite por device implícito (1 slot por device en el ZSET). Límite por suscriptor = tamaño del ZSET.

> Hoy `ConnectionService.open_connection(sub, device, max)` ya implementa el núcleo (ZSET + TTL). Mejora pendiente: envolver en Lua para atomicidad total bajo alta concurrencia + reuse-by-device explícito.

---

## Heartbeat y expiración

```
Heartbeat (cada ~30–60s, autenticado por token):
   ZADD nexora:active_conns:{sub} (now + SESSION_TTL) device   # renueva score
   actualiza sesión IPTV (last_seen) ; presencia device
Expiración (sin heartbeat):
   el score vence ⇒ el device cae del ZSET en el próximo ZREMRANGEBYSCORE
   ⇒ slot liberado automáticamente (player caído, red caída)
Limpieza periódica (background task) 🟢:
   marca sesiones DB expiradas como revocadas (hygiene)  [cada 15 min]
```

**SESSION_TTL** sugerido: 90–120 s (alineado con `watchdog_timeout=120s` de C). El heartbeat debe ser < TTL para no caer.

---

## Cierre / revocación

| Evento | Acción |
|---|---|
| Logout | `ZREM` device + revocar tokens del device |
| Cancelar suscripción | revocar **todas** las sesiones IPTV del suscriptor + vaciar ZSET 🟢 |
| Bloqueo de device | `ZREM` device + revocar device_token |
| Reinicio Redis | ZSET vacío ⇒ authorize re-crea slots (degradación segura; no bloquea) |

---

## Casos borde (y cómo se manejan)

| Caso | Manejo |
|---|---|
| **Zapping rápido** (5 canales en 30 s) | mismo device renueva score; no consume slots nuevos → sin `409` falso |
| **Player cae sin logout** | score vence por TTL → slot liberado solo |
| **Red intermitente** | hls.js reintenta; heartbeat renueva; si supera TTL, reautoriza |
| **Reinicio backend** | sesiones en PG persisten; ZSET en Redis persiste (o se reconstruye) |
| **Reinicio Redis** | ZSET vacío → primer authorize re-puebla; PG mantiene auditoría |
| **Doble device mismo plan, max=1** | segundo device → `409` (esperado) |
| **Reloj desfasado** | usar epoch del servidor (no del cliente) |
| **Carrera de 2 authorize** | atomicidad Redis (Lua/MULTI) garantiza ≤ max |

---

## Métricas / observabilidad 🟢🟡
- `/api/admin/metrics` → sesiones activas, latencia Redis 🟢.
- `/api/admin/sessions/live` → sesiones IPTV en vivo (username, device, ip, heartbeat) 🟢.
- Añadir: contador `playback_concurrency_rejections` ⬜, gauge de ZSET por plan ⬜.

---

## Pruebas necesarias
- **Carrera:** N authorize concurrentes con `max=2` → exactamente 2 aceptados (resto `409`).
- **Zapping:** mismo device, 10 cambios en 20 s → 0 rechazos, 1 slot ocupado.
- **Timeout:** parar heartbeat > TTL → slot liberado; nuevo authorize OK.
- **Reinicio Redis:** durante reproducción → siguiente authorize funciona; sin doble cobro de slot.
- **Cancelación:** cancelar suscripción → sesiones revocadas y ZSET limpio (verificable).

## Estado vs objetivo
| Capacidad | Hoy | Objetivo |
|---|---|---|
| ZSET atómico + TTL | ✅ | envolver en Lua para atomicidad total |
| Límite por plan | ✅ (`max_connections`) | + por device explícito |
| Heartbeat | ✅ | autenticado por token (no MAC) |
| Limpieza zombie | ✅ (15 min) | + métrica de rechazos |
| Histórico | 🟡 `sessions` DB | `playback.sessions` particionada |

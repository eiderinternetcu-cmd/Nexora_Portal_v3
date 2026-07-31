# ROADMAP — Nexora API (lo que está PENDIENTE)

_Actualizado: 2026-07-31 (sesión de cierre de backlog local — ver `docs/INFORME_SESION_2026-07-31.md`)_

> Este documento lista **solo lo que falta**. Lo ya entregado está en la línea base de abajo.
> Ordenado por dependencias reales, no por deseo. Alineado con `docs/nexora-best-of/12_ROADMAP_PRIORIZADO.md`
> (hitos M1–M5) y `11_BACKLOG_IMPLEMENTACION.md` (IDs `NX-*`).
>
> ⚠️ Este fichero **sustituye** al PR #15 (`docs/roadmap-m1-m2-deployed`), que quedó obsoleto:
> su contenido está incorporado aquí y además corregido. Ciérralo en vez de mergearlo.

---

## Línea base (qué YA está hecho — no está pendiente)

| Área | Estado |
|---|---|
| Fases 1–3 (auth, Client API, catálogo, Flussonic, web player, multi-device) | ✅ |
| Fase 4 · Bloque 0 (M3U real, 24→43 canales, multi-nodo) · Bloque 1 (observabilidad base, hls.js hardening) | ✅ |
| Deploy producción `nexoraplay.net` (HTTPS, `/stream/*` same-origin, UFW lockdown) | ✅ |
| **PROD-2A/2B/2C** (entitlement, jwt-aud, signed-url + Nginx `auth_request` + grant) | ✅ en prod (validado 13 min, 396/396 req) |
| **Argon2id** para hashing de passwords | ✅ |
| **M1 — device secret (flag-gated) + grant hardening** | ✅ **en prod** (PRs #9–12, Alembic **006**) |
| **NX-CONC — concurrencia atómica (Lua)** | ✅ **en prod** (PR #12) |
| **M2 — métricas de playback + auditoría inmutable (trigger append-only) + correlation-id** | ✅ **en prod** (PR #13, Alembic **007**) |
| Web player multi-marca (Nexora + La Red por hostname) + certificado `tvdigital.laredtelco.com` | ✅ en prod |
| Panel de administración (`admin_panel/`, 8 secciones) — lecturas **y escrituras** verificadas | 🟡 en la rama, **sin desplegar** |
| Backend: `plan_channels` (4 endpoints), scoping por reseller, `/docs` cerrado, CORS configurable, listados con orden estable | 🟡 en la rama, **sin desplegar** |
| **P0.5 — probe de nodos vía HLS firmado** (flag `NODE_PROBE_MODE`, default legacy) | 🟡 en la rama, **sin activar** |
| **NX-AUTH — lockout endurecido + auditoría de login** (flag `LOGIN_LOCKOUT_ENABLED`, default legacy) | 🟡 en la rama, **sin activar** |
| **Alembic 008** — repara la constraint de `plan_channels` que la 005 creó como índice | 🟡 en la rama, **sin aplicar en prod** |
| **Alembic 009** — `audit_logs.user_agent` a `Text`; su `downgrade()` se **niega** en vez de truncar un rastro append-only | 🟡 en la rama, **sin aplicar en prod** |
| **P0.8 — secretos de canal fuera de los listados** + endpoint de revelar auditado | 🟡 en la rama |
| **P1.4 — test de paridad migración↔ORM** · **P1.5 — tests dentro del contenedor** | 🟡 en la rama |
| **P1.6 — caché de segmentos en el edge** (1 tirada al origen por N espectadores) | 🟡 en la rama, **sin desplegar** |
| **NX-NODE / NX-AUTH — claim `node` cerrado, IP de confianza** (flags, defaults legacy) | 🟡 en la rama, **sin activar** |
| **Familia de 500 por columnas acotadas** (`audit_logs`, `sessions`, `devices`, `users`) | 🟡 en la rama |

**Prod: Alembic 007. M1 ~95% y M2 ~90% desplegados.**
**% actual:** MVP streaming seguro+operable **~90%** · Visión completa OTT **~44%**.

### Correcciones al roadmap anterior (cosas que se daban por pendientes y no lo estaban, o al revés)

1. **El lockout de login YA existía y estaba vivo en producción** (`AuthService._check_lockout`,
   contadores por usuario e IP, respuesta 423). El roadmap lo listaba como pendiente. Lo que
   faltaba de verdad era la **auditoría de fallos** y corregir dos defectos del que ya había
   (ver P2b).
2. **El "bypass" de `tc-mia` probablemente no es un bypass.** Ver P0.7 — la explicación que
   encaja con toda la evidencia versionada es un `location` ausente, no un gate abierto.
3. **`docker exec nexora_api pytest` nunca ha corrido el código editado** (`./tests` no se monta
   y la imagen no trae `requirements-dev`). Cualquier verde reportado por esa vía en el pasado
   es sospechoso. Ver P1.5.

---

## Prioridades

🔴 **P0 — Bloqueante / cierre de M1** · 🟠 **P1 — Estabilización** · 🟡 **P2 — Endurecimiento** · 🟢 **P3+ — Crecimiento**

---

## 🔴 P0 — Cerrar M1 y desplegar lo que ya está construido

### P0.6 · Desplegar la rama `feat/client-api-blockers` — **el lote más grande sin desplegar**
40+ commits: panel de administración, `plan_channels`, scoping por reseller, `/docs` cerrado,
CORS por configuración, listados con orden estable, y lo de esta sesión. **Riesgo moderado**:
reinicia el servicio que autoriza el playback de clientes reales. Todo aditivo, suite en verde.
- **Requiere ventana de bajo tráfico y visto bueno del dueño.**
- Patrón (igual que el web player): SFTP de `app/` + `docker compose -f docker-compose.production.yml build api` + `up -d --no-deps api`. **NUNCA `--remove-orphans`.**
- ⚠️ **Aplicar Alembic 008 en el mismo despliegue.** Sin ella, `plan_channels` da 500 en prod
  (la 005 ya corrió allí creando un índice donde el código espera una constraint).
- Después: montar el panel de administración (contenedor + vhost; el nginx ya está factorizado).

### P0.1 · PROD-Fase 2D: IP-binding del playback token — **último ítem vivo de M1**
El token ya lleva el claim `cip` y el gate pasa `X-Real-IP` real al backend. Falta activar:
- `PLAYBACK_IP_BINDING_MODE=soft` → warn + permite. Observar mismatches varios días (los clientes móviles cambian de IP).
- Solo si la evidencia lo permite → `strict` (mismatch → 403).
- **Requiere autorización explícita por flag.** Rollback: quitar la línea de `.env.production` + recrear api.
- _Referencia:_ `deploy/RUNBOOK_PRODUCTION_P0.md` · **AC:** misma IP → 200; otra IP → 200+WARN (soft) / 403 (strict).

### P0.2 · Tope de vida del grant — falta DEFINIR el valor de producción
`STREAM_GRANT_MAX_LIFETIME_SECONDS` está implementado y desplegado, pero vale **0 = ilimitado**
(`app/config.py:117`). Mientras siga en 0, un grant renovado cada <180 s vive indefinidamente:
esa es, literalmente, la latencia de revocación del sistema. Decidir el valor (p. ej. 6 h) y activarlo.

### P0.5 · Alerting de nodos — implementado, falta activar
El backend **no alcanza los orígenes Flussonic** (`181.78.246.211:8002`, `38.210.187.13:8002` →
timeout); solo nginx tiene ruta. Por eso el health-check desde el backend nunca fue viable.
Implementada la opción recomendada: el monitor prueba **HLS firmado a través del edge**, señal
end-to-end real (ejercita gate + nodo + stream de una vez).
- Flags: `NODE_PROBE_MODE` (`origin` por defecto → `hls_signed`), `NODE_PROBE_EDGE_BASE_URL`,
  `NODE_PROBE_TIMEOUT_SECONDS`, `NODE_PROBE_STREAMS`.
- El token del probe lleva el claim firmado `pb`: el gate **no le siembra grant de segmentos**.
- **Pendiente:** activar el flag tras P0.6 y confirmar que `NODE_PROBE_EDGE_BASE_URL` resuelve
  desde el contenedor `api` en producción.

### P0.7 · ✅ CERRADO (2026-07-31) — `tc-mia` no era un bypass
La sonda de solo lectura en producción encontró el artefacto decisivo: la copia de seguridad
**previa a la reparación, aún en disco** (`stream-gate.conf.bak-20260727-1500`), vigente entre
las **03:17 y las 15:48 UTC del 27-jul**, con tres nodos con `auth_request` y **`tc-mia`
ausente** — exactamente el diferencial observado (3×401 + 1×200).

**No hubo exposición de vídeo**: sin `location` no hay `auth_request`, luego no hubo nada que
aprobar, y el destino del catch-all es el contenedor del web player, que no sirve segmentos ni
manifiestos. La petición nunca llegó a la torre de Miami. **Impacto: ~12,5 h sin canales de
Miami, en las dos marcas** (el snippet es compartido, así que la anotación imprecisa era la de
§3 de este roadmap, no el registro original que decía `nexoraplay.net`).

**Causa raíz: el procedimiento de copia, no el diseño del gate.** `c1f76e1` añadió `tc-mia` al
snippet a las 02:11; el despliegue de las 03:17 subió un snippet **sin** `tc-mia`. Un arreglo
que ya existía en git fue pisado por una copia rancia.

Quedan tres derivadas, abajo en **P0.9**.

### P0.9 · Derivadas de `tc-mia` — la clase de fallo sigue abierta
1. **No hay guardia genérica**: falta un `location ^~ /stream/` que devuelva 403 para nodos no
   declarados. Hoy no hay síntoma, pero **el próximo nodo que se olvide repite el 200 mudo**.
   La reparación del 27-jul arregló *el caso*, no *la clase*. Bloque exacto en
   `docs/ANALISIS_BYPASS_TCMIA.md` §8.5.
2. **Arreglar el procedimiento de despliegue** — comparar checksum/diff repo↔vivo antes de
   copiar. Es la causa raíz.
3. **Retirar el monolito de 939 líneas y los `patch_nginx_*.py`**: no están desplegados, son
   material muerto, y ya indujeron un diagnóstico equivocado una vez.
4. **Versionar el `nginx.conf` principal.** El `proxy_pass` de `/__stream_auth` lleva variables,
   lo que obliga a nginx a exigir un `resolver`; ese `resolver` vive en el `nginx.conf`
   principal, que **no está en el repo**. Si alguien recrea el contenedor, el gate no arranca.

### P0.7-histórico · El enunciado original (conservado)
`tc-mia` devolvió 200 a un stream sin token; los otros 3 nodos dan 401. El análisis completo
(`docs/ANALISIS_BYPASS_TCMIA.md`) concluye que la hipótesis líder **no es un bypass**: el
`location /stream/tc-mia/` no existía en el vhost probado, la petición cayó al catch-all del SPA
y `try_files … /index.html` devolvió **200 con HTML** — cero vídeo. Si se confirma, el impacto es
de **disponibilidad** (los canales de Miami no se ven en esa marca), no de seguridad.
- Causa estructural: `deploy/transcode/patch_nginx_tc_mia.py` inserta **solo la primera**
  coincidencia (un vhost) y su guardián de idempotencia impide replicarla al segundo.
- **Sonda discriminante** (solo lectura, en el servidor):
  `sudo docker exec nexora_nginx nginx -T 2>/dev/null | grep -nE 'server_name|location \^~ /stream/'`
  Si algún `server_name` no lleva los cuatro nodos → hipótesis confirmada.

### P0.8 · ✅ CERRADO (2026-07-31) — fuga de `stream_key` / `source_url`
Enmascarado siempre en los listados + endpoint de revelar solo admin y auditado. El barrido
encontró **tres fugas más** allá del listado: el detalle de canal, `stream-status` (que devolvía
el `stream_key` y además lo filtraba en el texto de su 404) y, la mayor,
**`/api/admin/flussonic/streams` abierto a reseller** — servía `name` (el namespace de la clave)
y una `hls_url` ya montada, o sea el catálogo entero sin necesitar el mapeo canal↔clave. Pasa a
`require_admin`.
- ⚠️ **Cambio de permisos, no solo de forma:** un reseller que use esa tabla para diagnosticar
  deja de verla. Confirmar con el dueño si sus resellers la usaban.

### P0.4 · `co-main` caído (externo)
El nodo Flussonic `co-main` (38.210.187.13) está **caído** → 4 canales sin servicio. Fuente externa.
→ Enlaza con P0.5 (alerta) y P2.2 (failover).

---

## 🟠 P1 — Estabilización de playback, entornos y verificación

### P1.4 · Paridad migración ↔ ORM — **el agujero que dejó pasar el bug de la 005**
`tests/conftest.py::db_session` construye el esquema con `Base.metadata.create_all()` desde el
modelo ORM. Consecuencia: **ninguna divergencia entre el ORM y lo que Alembic produce de verdad
puede ser detectada por la suite**, por muchos tests que haya. La 005 declaraba un índice único
donde el modelo declaraba una `UniqueConstraint`, y `ON CONFLICT ON CONSTRAINT` reventaba con 500
en cualquier base construida por migraciones — es decir, en producción. Los 345 tests seguían en verde.
→ Test que corra `alembic upgrade head` sobre una base efímera y compare el esquema resultante
contra `Base.metadata` (`sqlalchemy.inspect` o `alembic check`).

### P1.5 · Los tests no corren dentro del contenedor `api`
`docker-compose.yml` no monta `./tests` en `nexora_api` y la imagen no instala
`requirements-dev.txt`. `docker exec nexora_api pytest` ejecuta la copia horneada en la imagen,
no el código editado — un falso verde esperando a ocurrir, y el comando que la documentación
recomendaba. Mientras tanto, la vía correcta es el venv del host contra los puertos publicados
(`localhost:5433` / `localhost:6380`) con `TEST_DATABASE_URL` y `TEST_REDIS_URL` puestas.

> **Estado 2026-07-31:** P1.4, P1.5 y P1.6 quedaron **resueltos en la rama** (sin desplegar).
> El detalle de cada uno se conserva abajo porque explica *por qué* importaban.
> **P1.6 medido**: 12 peticiones concurrentes de un segmento frío → 1 MISS + 11 HIT, **1 tirada
> al origen y 12 autorizaciones**, en contenedores `nginx:1.27-alpine` (la versión de producción).
> **P1.4 encontró 3 divergencias nuevas** en su primera pasada, una de ellas un 500 vivo.

### P1.6 · ✅ Caché de segmentos en el edge — **el techo de escala real**
Hoy la cabecera escala con el número de **espectadores**, no de canales, y eso la mata antes que
cualquier otra cosa.

Cuatro hechos verificados que lo componen:
1. **El edge no es un redirector, es un relé.** `deploy/nginx/nexoraplay.conf:407` hace
   `proxy_pass` de cada segmento al origen. La regla arquitectónica "Nexora NO hace proxy de
   vídeo — el cliente reproduce directo del edge" **dejó de ser cierta** cuando `/stream/*` pasó
   a ser same-origin. Cada espectador consume ancho de 45.184.225.4 dos veces (entra del origen,
   sale al cliente).
2. **No hay caché**: cero `proxy_cache` en la config, `proxy_buffering off`. Diez espectadores
   del mismo canal son diez tiradas independientes contra la cabecera.
3. **La cabecera ya está justa**: 74 % de CPU y 191,9 Mbps de salida (revisión del 2026-07-27),
   con la nota de que a partir de ahí el cuello de botella deja de ser el transcodificado.
4. **~3 Mbps por conexión** de media (SD transcodificado 1500 kbps / maxrate 1700; el 720p a
   2500/2800; fuentes sin transcodificar 2–4 Mbps).

La cuenta que importa no es la de hoy (5 suscriptores) sino la de después: **100 suscriptores,
30 % de concurrencia en punta, 1,5 flujos de media ≈ 45 conexiones simultáneas ≈ 135 Mbps
adicionales** sobre los 191,9 que la cabecera ya sirve.

⚠️ **El límite por plan NO protege de esto.** Cincuenta clientes viendo el mismo partido con una
sola conexión cada uno producen el mismo problema. Lo que lo resuelve es cachear: los `.ts` de
HLS son inmutables, así que N espectadores de un canal colapsan en **una** tirada al origen —
la diferencia entre escalar con el número de canales o con el de espectadores.

`proxy_cache` **no salta el gate**: el `auth_request` se ejecuta en cada petición igual; lo que
se cachea es la respuesta del upstream, no la autorización. No se pierde control de acceso.
**AC:** N espectadores concurrentes del mismo canal → 1 sola conexión al origen; un token
inválido sigue dando 401 con el segmento en caché.

### P1.1 · Stress tests de playback (Fase 4 · Bloque 3 — nunca ejecutado)
Con métricas encendidas (`/api/admin/metrics`, `/api/admin/sessions/live`):
- Zapping rápido (5 canales en 30 s) → sesiones zombie y falsos 409.
- Playback continuo 3–6 h → memory leaks en browser, limpieza del ZSET.
- Reconexión de red (WiFi 30 s off) → retry de hls.js recupera.
- Reinicio de `api` y de `redis` → el cliente reconecta; `authorize` sigue funcionando.
- 3 usuarios simultáneos del mismo suscriptor → límite de devices + concurrencia del ZSET.
- Heartbeat timeout (3 min sin latir) → el ZSET expira y corta.

### P1.3 · Entorno de STAGING real (hoy NO existe)
Producción se validó **directo contra prod**. El runbook `deploy/RUNBOOK_STAGING_P0.md` está
escrito pero **sin ejecutar**.
- Servidor `staging.nexoraplay.net` (2.25.68.163, Ubuntu 24.04) provisto; ZeroTier unido a `633e31d8a2cf3c84`.
- 🔴 **Bloqueado:** el nodo `4c3f6acbc9` está en `ACCESS_DENIED` → autorizarlo en el controller
  self-hosted (`633e31d8a2` @ 35.209.188.59), no en ZeroTier Central.

---

## 🟡 P2 — Observabilidad y resiliencia

### P2.1 · Observabilidad extendida (`NX-MON`) — mayormente entregada en M2
Entregado: métricas de playback, auditoría inmutable, correlation-id.
Pendiente: alertas de stream/nodo caído (depende de **P0.5**), `/api/admin/streams` con
`is_active` (DB) vs `alive` (Flussonic), métricas Prometheus/OTel.

### P2.2 · Multi-Flussonic Registry + failover (`NX-FLU`)
`get_flussonic_node_client()` sigue siendo un stub y el `.env` lista los nodos a mano.
→ `app/integrations/flussonic_registry.py` formal (`node_id`, `base_url`, `region`, `priority`,
`is_healthy`) + health check periódico + **failover de nodo**. Sin geo-routing todavía.
**AC:** failover probado (relevante ya, por co-main).

---

## 🟡 P2b — Seguridad restante del MVP

| ID | Pendiente | AC |
|---|---|---|
| `NX-NODE` | ✅ **resuelto en la rama** (flag `PLAYBACK_NODE_BINDING_ENFORCE` + `PLAYBACK_NODE_ALLOWLIST`, defaults legacy). La arqueología mostró que la tolerancia fue *load-bearing* cuando se escribió —había dos emisores legítimos de tokens sin nodo, ya corregidos antes en esta rama— así que cerrarlo debería ser un no-op. **Falta:** activar el flag | Token sin claim `node` → 403, no 200 |
| `NX-AUTH` | ✅ **resuelto en la rama** (flags `CLIENT_IP_SOURCE` + `TRUSTED_PROXY_CIDRS`). Había **cinco** copias del resolutor roto, no una: arreglar solo `dependencies.py` habría dejado el rate limiter abierto. En modo `edge` las cabeceras solo se creen si el par es de confianza, y se lee el **último** salto de XFF, no el primero. **Falta:** activar los flags; MFA opcional | Credenciales malas → 401 uniforme; login admin auditado |
| `NX-PARITY` | **NUEVO, del test de paridad.** Dos divergencias ORM↔migración sin arreglar: **`plans.name`** repite el patrón índice-vs-constraint de la 005 (será un 500 el día que alguien haga `ON CONFLICT ON CONSTRAINT` sobre ese campo), y falta el índice **`ix_subscriptions_starts_at`** en producción — lo que significa que un `EXPLAIN` medido contra una base de test **no describe producción** | El test de paridad pasa sin entradas en `KNOWN_DIVERGENCES` |
| `NX-DEV` | Base hecha (flag `DEVICE_SECRET_ENFORCE`). _Falta:_ que el web player/STB guarden y presenten el secreto (otro repo) + rate-limit de re-binding + handshake HMAC opcional | Device sin secret válido no obtiene token |
| `NX-AUDIT` | Inmutabilidad ✅ (trigger, Alembic 007). Falta particionado + retención | Consulta filtrable; no se puede alterar |
| `NX-PARENTAL` | Control parental **PIN server-side** (`channels.censored`) | Canal adulto sin PIN → 403 |

**Residuales de login conocidos y aceptados** (preexistentes, decisión del dueño):
enumeración por *timing* (~50–100 ms: `if not user or not verify_password(...)` cortocircuita) y
`"Account is disabled"`, que confirma que la cuenta existe **y** que la contraseña era correcta.

---

## 🟢 P3 — Fase 2 (crecimiento de producto)

- `NX-EPG` — **EPG real** (hoy es mock en `catalog.py`): ingest async + cron, sin duplicados, sin XXE.
- `NX-RBAC` — RBAC admin + **resellers** (aislamiento por tenant; el scoping ya está en la rama).
  ⚠️ **Antes de activar cuentas de reseller:** los suscriptores con `created_by` nulo son
  invisibles para cualquier reseller — solo los ve el admin. Asignarles dueño con un UPDATE.
- `NX-NOTIF` — comandos/eventos push al device (entregados en el heartbeat, con ack).
- **Normalizar entitlements a paquetes** (`packages`/`plan_packages`/`package_contents`).
  _Divergencia consciente:_ hoy se resolvió con `plan_channels` (005+008), más simple.

---

## 🟢 P4 — Fase 3 (escala y monetización)

`NX-VOD` (VOD/series) · `NX-CATCHUP` (timeshift/DVR) · `NX-BILL` (billing idempotente) ·
`NX-ASTRA` (adapter Astra) · `NX-XC` (XtreamCompat read-only) · `NX-CDN` (multi-región/CDN) ·
`NX-IAC` (IaC + DR).

**Decisión de negocio (2026-07-31):** La Red **no** vende hoy servicios add-on por cliente
(VOD/Timeshift/VIP); se contemplan a futuro. No se construye el modelo ni la migración. El
entitlement se resuelve por plan, así que añadir una capa por-cliente después es aditivo.

**Decisión de negocio (2026-07-31): el límite de dispositivos queda POR PLAN — 5 en el plan
estándar, 10 en VIP.** No hay override por cliente, así que **no hace falta migración**:
`plans.max_devices` ya existe (`app/models/plan.py:19`) y es un cambio de datos, no de esquema.
Se aplica desde el panel de administración una vez desplegado, o con un UPDATE.

**Decisión de negocio (2026-07-31): conexiones simultáneas — 2 en el estándar, 4 en VIP.**
Ratio **2,5:1** respecto a los dispositivos, que sirve de regla para los planes futuros.

`max_devices` y `max_connections` son palancas distintas y no deben igualarse. Los dispositivos
son **comodidad** (registrar la tele, el móvil, la tablet); las conexiones son la **palanca
comercial**: si se igualan, el plan se vuelve divisible entre cinco hogares con un solo pago, y
esa reventa no necesita que nadie la organice — sale sola. El 2 tiene además valor diagnóstico:
muchos 409 de límite alcanzado en cuentas estándar señalan una cuenta compartida, señal que con
3 o más se pierde en el ruido.

**`NX-APPS` (Android TV / Mobile / iOS) está BLOQUEADO** por restricción del proyecto: no se
empieza hasta que **playback, sesiones y observabilidad** estén estables (⇒ P0 + P1 + P2.1 cerrados).

---

## Orden recomendado (grafo de dependencias)

```
P0.6 desplegar la rama (+ Alembic 008)   ← lo más valioso parado hoy
      │
      ├──► P0.5 activar el probe de nodos ──► P2.1 alertas ──► P2.2 failover
      ├──► P0.1 IP-binding soft ──► strict     ← cierra M1
      └──► P0.2 definir el tope del grant      ← cierra M1

P0.7 sonda tc-mia (5 min, solo lectura)  ← barato, hazlo ya
P0.8 decidir la fuga de source_url       ← barato, es una decisión

P1.6 cache de segmentos                  ← antes de crecer en clientes, no despues

P1.4 paridad migración↔ORM ──┐
P1.5 tests en el contenedor ─┴──► P1.1 stress + P1.3 staging
                                        └──► P2b seguridad (NX-NODE, NX-AUTH, NX-DEV)
                                                  └──► P3 (EPG, RBAC) ──► P4 (VOD, billing, APPS)
```

**Regla dura:** nada de apps nativas hasta cerrar P0+P1+P2.1.

---

## Riesgos abiertos

| Riesgo | Estado / mitigación |
|---|---|
| `plan_channels` da 500 en prod al desplegar | **Resuelto en la rama** (Alembic 008); el riesgo se cierra al aplicarla junto con P0.6 |
| Divergencia ORM↔migraciones invisible para los tests | **Abierto** → P1.4. Ya produjo un bug bloqueante |
| La cabecera escala con espectadores, no con canales (sin caché de segmentos) | **Resuelto en la rama** → P1.6, medido 1 tirada al origen por 12 espectadores; falta desplegar |
| Un despliegue puede pisar un arreglo que ya está en git | **Abierto** → P0.9.2. Ya pasó: causó 12,5 h sin canales de Miami |
| El gate no arranca si se recrea el contenedor de nginx | **Abierto** → P0.9.4. El `resolver` que exige `/__stream_auth` vive en un fichero no versionado |
| Columnas acotadas escritas sin recortar (500 en login y playback) | **Resuelto en la rama**; `_assert_fits()` impide que la divergencia vuelva en silencio |
| Revocación no corta streams en curso (grant auto-renovable) | Código listo; **falta definir el tope** → P0.2 |
| `strict` IP-binding rompe clientes móviles | Mitigado: escalonar `off → soft → strict` con observación (P0.1) |
| Canales de Miami invisibles en una marca | **Probable** → P0.7 lo confirma con una sonda |
| Credenciales del proveedor visibles en devtools del panel | **Abierto** → P0.8 |
| No hay staging → los flags se prueban en producción | **Abierto** → P1.3 |
| Fuentes IPTV externas caídas (co-main) | Conocido → alertas + failover (P0.4 / P2.2) |
| Producción NO es un repo git; el nginx vivo puede divergir del versionado | Comparar SIEMPRE antes de tocar (ya evitó borrar la ruta de tc-main) |

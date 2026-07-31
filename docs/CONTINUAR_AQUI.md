# Continuar aquí — punto de retoma

_Handoff a 2026-07-31. Detalle de la última sesión: `docs/INFORME_SESION_2026-07-31.md`.
Backlog completo y priorizado: `docs/ROADMAP.md`. Este archivo es solo "por dónde seguir"._

Rama: **`feat/client-api-blockers`**. Para retomar: `git checkout feat/client-api-blockers`.

---

## Estado en una línea

Web player multi-marca y certificado de `tvdigital.laredtelco.com` en producción. Panel de
administración, cambios de API, alerting de nodos y endurecimiento de login: **construidos,
probados y commiteados, sin desplegar**.

---

## Lo siguiente, en orden

### 1. Desplegar la rama a producción (necesita ventana + visto bueno)
Es lo más valioso que está parado. Reinicia el servicio que autoriza el playback de clientes
reales, así que en horario de bajo tráfico. Todo aditivo, **345 tests en verde**.

Patrón (igual que el web player): SFTP de `app/` al servidor +
`docker compose -f docker-compose.production.yml build api` + `up -d --no-deps api`.
**NUNCA `--remove-orphans`** (borra el stack de transcodificación).

⚠️ **Aplicar Alembic 008 en el mismo despliegue.** Sin ella, `plan_channels` responde **500** en
producción: la 005 ya corrió allí creando un índice donde el código espera una constraint. Es un
bug bloqueante que se atrapó a tiempo — ver §1 del informe de sesión.

Después: montar el panel de administración (contenedor + vhost nuevo, mismo patrón que tvdigital;
el nginx ya está factorizado).

### 2. Cerrar el caso `tc-mia` — cinco minutos, solo lectura
```
sudo docker exec nexora_nginx nginx -T 2>/dev/null | grep -nE 'server_name|location \^~ /stream/'
```
Si algún `server_name` no lista los cuatro nodos, queda confirmado que el 200 sin token era un
`location` ausente cayendo al SPA (200 con HTML, cero vídeo) y **no un bypass** — y entonces lo
urgente es que los canales de Miami no se ven en esa marca. Análisis: `docs/ANALISIS_BYPASS_TCMIA.md`.

### 3. Decidir la fuga de `source_url` en el panel
`GET /api/admin/channels` devuelve `stream_key` y `source_url` en crudo a admin **y reseller**.
No se pintan, pero viajan en la respuesta y son visibles en devtools; `source_url` puede llevar
credenciales del proveedor. ¿Enmascarar siempre, solo admin, o endpoint de "revelar" con auditoría?

### 4. Flags que quedan por activar (uno por vez, con observación y rollback)
- `PLAYBACK_IP_BINDING_MODE=soft` → `strict` (P0.1) — último ítem vivo de M1.
- `NODE_PROBE_MODE=hls_signed` (P0.5) — antes, confirmar que `NODE_PROBE_EDGE_BASE_URL` resuelve
  desde el contenedor `api` en producción.
- `LOGIN_LOCKOUT_ENABLED=true` (NX-AUTH).
- `STREAM_GRANT_MAX_LIFETIME_SECONDS` sigue en **0 = ilimitado**: mientras tanto, esa es la
  latencia de revocación real del sistema. Definir el valor (p. ej. 6 h).

### 5. Crear el hook de renovación de certbot en el host
Con dos certificados de fechas distintas, sin hook la caducidad falla intermitente por dominio.
Script listo en `deploy/RUNBOOK_EDGE_MULTIDOMINIO.md`.

---

## Decisiones de negocio
- ~~¿La Red vende servicios add-on (VOD/Timeshift) por cliente?~~ → **decidido 2026-07-31: no se
  venden hoy, se contemplan a futuro.** No construir el modelo ni la migración; queda en P4.
- ~~¿Límite de dispositivos por cliente, además de por plan?~~ → **decidido 2026-07-31: queda por
  plan. 5 dispositivos en el estándar, 10 en VIP.** Sin override por cliente, así que **no hace
  falta migración**: `plans.max_devices` ya existe. Es un cambio de datos, aplicable desde el
  panel de administración cuando se despliegue, o con un UPDATE sobre `plans`.
- **Pendiente y distinto:** `max_connections` (streams simultáneos) no se ha decidido. No es lo
  mismo que `max_devices` y hoy divergen — el plan de pruebas local tiene 3 conexiones y 10
  dispositivos. Los dispositivos son los aparatos registrados; las conexiones, lo que se puede
  reproducir a la vez, que es lo que carga los nodos y lo que factura ancho de banda.

---

## Notas de operación que NO se deben olvidar
- **Producción NO es git.** El código llega por copia; comparar el conf vivo contra el versionado
  ANTES de tocar (ya evitó borrar la ruta de tc-main una vez).
- **Antes de activar cuentas de reseller**: los suscriptores con `created_by` nulo quedan
  invisibles para cualquier reseller (solo el admin los ve). Asignarles dueño con un UPDATE.
- **`docker exec nexora_api pytest` NO valida nada** — `./tests` no está montado y la imagen no
  trae `requirements-dev`, así que corre la copia horneada. Usar el venv del host contra
  `localhost:5433` / `localhost:6380` con `TEST_DATABASE_URL` y `TEST_REDIS_URL` puestas.
- **La suite no puede detectar divergencias ORM↔migración** (`conftest.py` construye el esquema
  desde el ORM). Ya dejó pasar un bug bloqueante. → P1.4 del roadmap.
- **`nexora_transcoder` está parado a propósito**: la cabecera de Esmeraldas filtra por IP y esta
  máquina no está en la lista blanca del NOC. No relanzarlo sin resolver eso.
- Acceso SSH a producción: credencial en `.claude/settings.json` (bloque `nexora-ssh`), no en `.env`.

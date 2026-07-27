# Roadmap — Multi-marca + Panel de administración

_Estado a 2026-07-27. Rama `feat/client-api-blockers`, 35 commits por delante de `main`.
Todo lo que aquí se afirma está verificado contra el código o contra el servidor en el
momento de escribirlo._

Leyenda: ✅ desplegado en producción · 🟡 commiteado, sin desplegar · ⛔ bloqueado
(necesita una acción del dueño) · 🔵 diferido (necesita decisión de negocio o migración).

---

## 1. Qué corre HOY en producción (45.184.225.4)

| Pieza | Estado |
|---|---|
| Web player multi-marca (Nexora + La Red por hostname) | ✅ |
| Logo real de La Red, halo del logo corregido, pósters propios, pie `LAREDTELCO.COM` | ✅ |
| 5 cuentas creadas (`Miguel`, `Habib`, `Ivan`, `David`, `eigotax`), plan anual, 36 canales | ✅ |
| Canales transcodificados (tc-main gamatv/golden/ecuadortv/caracol/rcn, tc-mia Miami) | ✅ (los desplegó el otro frente) |
| API `nexora-api` v2.0.0 | ✅ (pero SIN los cambios de esta sesión — ver §3) |

El web player se desplegó por SFTP + `docker compose build web_player` + `up -d --no-deps
web_player`. Solo ese servicio se recreó cada vez; nunca se tocó la API, nginx ni el stack
de transcodificación.

---

## 2. Lo grande que quedó construido y verificado, SIN desplegar 🟡

Todo en la rama, con tests en verde corridos a mano.

### 2.1. Panel de administración (`admin_panel/`) — app nueva
- 8 secciones contra datos reales: Dashboard, Suscriptores (+ detalle), Planes,
  Dispositivos, Usuarios, Canales, Sesiones, Auditoría.
- Vite + React 19 + TS. Separada del player a propósito: no se envía el código de admin
  en el bundle de cada cliente. `localStorage` bajo `nexora.admin.*`, aislado del player.
- Seguridad de UI verificada: no se muestran `stream_key` en el listado ni `source_url`
  (puede llevar credenciales del proveedor).
- Probado en el navegador contra la API local: las 8 secciones cargan y leen bien.
- **NO probado**: las escrituras (crear suscriptor, guardar lista blanca, revocar sesión).
  Cargan y leen; escribir cambia datos reales y se dejó para validación con el dueño.

### 2.2. Backend — huecos de la API cerrados
- **`/docs`, `/redoc`, `/openapi.json` cerrados en producción** (`APP_ENV=production`), que
  hoy publican el mapa completo de la API incluido el endpoint del gate de playback.
- **CORS por configuración** (`CORS_ALLOW_ORIGINS`), antes hardcodeado a localhost.
- **`plan_channels`: 4 endpoints nuevos** (leer, reemplazo atómico, alta/baja). Antes esta
  tabla —la lista blanca estricta que decide el entitlement— no tenía NINGÚN endpoint; dar
  de alta un plan exigía SSH + script. Con test que prueba que un PUT cambia de verdad la
  respuesta de `can_watch_channel`.
- **Listados: `ORDER BY` estable** (paginaban sin orden → filas repetidas/perdidas),
  **búsqueda `q`** de suscriptores, **filtros rol/estado** en usuarios, **reposición de
  contraseña por un admin**.
- **Scoping por reseller**: un reseller ve/edita/borra SOLO sus suscriptores (hoy ve la
  cartera entera — agujero real). 404 ante un cliente ajeno, sin filtrar existencia.
- **Listado enriquecido**: caducidad, plan, días restantes y dueño en una sola consulta
  (arregla un N+1). **Exportar CSV** respetando scoping y filtros.
- 315 tests (desde 253 al inicio de la sesión). Arreglos validados por mutación.

### 2.3. Infraestructura de edge (nginx multi-dominio)
- Config factorizada `conf.d/` + `snippets/`, gate de streams en UNA sola copia, con los
  4 nodos (ec-main, co-main, tc-main, tc-mia).
- `default_server` explícito, `X-Forwarded-For` corregido (hoy es evadible el rate limit
  del login), `/docs` fuera de la cara pública.
- Archivo único generado `deploy/nginx/nexoraplay.conf` (939 líneas) que expande los
  include, validado en contenedor real. Restituye una ruta que el compose de producción
  todavía monta.
- Runbook: `deploy/RUNBOOK_EDGE_MULTIDOMINIO.md`.

---

## 3. Pendiente de desplegar, por orden de urgencia

### 3.1. ✅ CERTIFICADO de `tvdigital.laredtelco.com` — DESPLEGADO (2026-07-27)
Resuelto. El dominio nuevo ya sirve su propio certificado (`CN=tvdigital.laredtelco.com`,
verificado por SNI); el aviso de seguridad del navegador desapareció. `nexoraplay.net` sin
regresión (`/health` ok, su cert intacto), y el reload recargó de paso el cert renovado de
nexoraplay que llevaba días sin recargarse.

Se desplegó la config factorizada como archivo único (939 líneas, los 2 dominios y los 4
nodos de stream) sobre `deploy/nginx/nexoraplay.conf`, tras confirmar que era un
superconjunto estricto de la config viva (ninguna ruta caída) y `nginx -t` en el contenedor
vivo. Respaldo en `nexoraplay.conf.bak-antes-marca`.

⚠ HALLAZGO al verificar: `tc-mia` devolvió 200 a un stream SIN token (los otros 3 nodos dan
401). Posible bypass del gate en el nodo de Miami. NO lo introdujo este cambio (ya estaba en
la config viva con el mismo bloque). Es del frente de transcodificación. Verificar la lógica
de validación para node=tc-mia.

### 3.2. 🟡 API con los cambios de la sesión
Rebuild del contenedor `api` en producción. RIESGO MODERADO: reinicia el servicio que
autoriza el playback de clientes reales, y arrastra en un lote scoping + /docs + CORS +
plan_channels + listados. Todo es aditivo y está en 315 tests verdes, pero merece una
ventana de bajo tráfico y el visto bueno del dueño. Mismo patrón que el web player: SFTP de
`app/` + `docker compose build api` + `up -d --no-deps api`.
Antes: aplicar en producción los flags nuevos si se quieren (`CORS_ALLOW_ORIGINS`;
`APP_ENV=production` ya está puesto, así que /docs se cierra solo).

### 3.3. 🟡 Panel de administración
Necesita su propio contenedor + un vhost. El vhost depende de §3.1 (misma factorización de
nginx). Orden natural: cerrar §3.1, luego montar el panel.

---

## 4. Diferido — necesita DECISIÓN de negocio 🔵

Al comparar con el panel Legon, estas funciones de "cliente" quedaron fuera a propósito:

- **Servicios add-on por cliente (VOD, Timeshift, VIP)** — ¿La Red vende extras? Necesita
  modelo nuevo + migración de BD.
- **Límite de dispositivos por cliente** — hoy es por plan. ¿Se quiere override por cliente?
  Migración de BD.
- **Fecha de fin editable en línea / cuentas VIP sin caducidad** — se puede, es trabajo de
  API + UI.

Descartadas por no encajar en la arquitectura (somos Client API + Flussonic, no middleware
Stalker): huella/fingerprint, override de IP de portal, marquesina al STB, asignación de
proxy/restreamer, almacenes, mapas.

---

## 5. Deuda conocida / avisos operativos

- **Producción NO es un repo git.** El código llega por copia; el nginx vivo puede divergir
  del versionado. Comparar SIEMPRE antes de tocar. (Ya evitó borrar la ruta de tc-main.)
- **Mina de `--remove-orphans`.** Los contenedores de transcodificación viven en otro
  compose; `docker compose -f docker-compose.production.yml` los ve como huérfanos y sugiere
  borrarlos. Usar SIEMPRE `up -d --no-deps <servicio>`, nunca `--remove-orphans`.
- **No existe hook de renovación de certbot.** Con dos certificados de fechas distintas, el
  fallo de caducidad pasa a ser intermitente por dominio. Script listo en el runbook; hay
  que crearlo en el host.
- **Suscriptores huérfanos.** Los creados antes del registro de dueño (`created_by` nulo)
  quedan invisibles para cualquier reseller — solo el admin los ve. Antes de activar
  cuentas de reseller, asignarles dueño con un UPDATE.
- **Tasa de fallo de playback ~42% en local** (`NO_ACTIVE_SUBSCRIPTION_FOUND`) — son
  contadores del entorno de pruebas; medir en producción cuando se despliegue el dashboard.

---

## 6. Orden recomendado para cerrar

1. **Certificado** (§3.1) — es lo único que un cliente real ve mal. Cinco minutos.
2. **Probar las escrituras del panel** en local (crear suscriptor, guardar lista blanca,
   revocar sesión) antes de darlo por bueno.
3. **Desplegar la API** (§3.2) en ventana de bajo tráfico, con el visto bueno del dueño.
4. **Montar el panel** (§3.3) tras cerrar el certificado.
5. **Crear el hook de renovación de certbot** en el host.
6. Decidir §4 (add-on services / límite por cliente) si entra en el plan comercial.

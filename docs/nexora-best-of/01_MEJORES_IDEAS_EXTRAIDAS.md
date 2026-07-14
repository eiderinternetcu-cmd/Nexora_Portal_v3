# 01 — Mejores ideas extraídas

> Conceptos del dominio que **sí** vale la pena conservar (reimplementados limpios). Se cita la fuente (A/B/C) y el porqué. **El valor es la idea, no el código.**

---

## 1. Separar canal lógico ↔ links físicos ↔ streamer (C, refuerza A)

**Idea:** un canal es una entidad lógica; sus URLs de reproducción viven aparte (`ch_links`), y cada link puede mapearse a varios streamers (`ch_link_on_streamer`).

- **Por qué:** desacopla catálogo de infraestructura; permite múltiples orígenes/calidades por canal, prioridad y balanceo sin tocar el canal.
- **Nexora:** `catalog.channels` → `streaming.channel_streams` (url, priority, node_id, ua_filter, status) → selección de nodo en `FlussonicIntegrationService`. Ya existe el embrión: `channel.flussonic_node` + `hls_path` + routing multi-nodo.

## 2. Autorización de playback centralizada y previa a la firma (C, lección del IDOR)

**Idea:** un único punto (`getServicesByType` → debería gobernar `createLink`) decide entitlements.

- **Por qué:** en legacy el gate vivía en las listas/EPG pero **no** en la emisión de enlace → IDOR. Centralizar elimina la clase entera de bugs.
- **Nexora:** **PlaybackAuthorizationService** es el **único** emisor de URLs reproducibles. Ya implementado como `StreamAuthService.authorize` (valida subscriber+suscripción+device+concurrencia).

## 3. Jerarquía tarifa → paquete → servicio (C)

**Idea:** `tariff_plan` → `package_in_plan` (flag `optional`) → `services_package` (tipo tv/vod/radio) → `service_in_package` (ids de contenido). Paquetes obligatorios automáticos; opcionales suscribibles.

- **Por qué:** modelo flexible y probado para entitlements (combina planes base + add-ons).
- **Nexora:** `plans` + `packages` + `plan_packages(optional)` + `package_contents(content_type, content_id)` + `subscriptions`. Reemplaza el bouquet-JSON de Xtream por **FK reales**.

## 4. Catch-up por ventana de programa EPG (C)

**Idea:** mapear el archivo grabado a un programa EPG (`/media/<id>.mpg` → start/stop con corrección DST), eligiendo el storage menos cargado.

- **Por qué:** UX correcta de "ver lo que ya pasó" anclada a la guía, no a timestamps crudos.
- **Nexora:** `EPGService` + `StreamTokenService.mint(content=archive, from, to)`; TTL ajustado (no 8 h como el legacy).

## 5. Watchdog / heartbeat + cola de eventos push (C)

**Idea:** el dispositivo late cada N s; el servidor mantiene presencia (`keep_alive`) y devuelve **eventos pendientes** (cut_off/cut_on, reboot, update_epg, mensaje).

- **Por qué:** presencia en tiempo real + canal de comandos remotos sin polling pesado.
- **Nexora:** `DeviceService` (presencia en Redis) + `events(jsonb payload)` en PostgreSQL; heartbeat **autenticado con token** (no solo MAC). Ya existe `devices/heartbeat` + limpieza de zombies.

## 6. Catálogo de adapters de tokenización por proveedor (C)

**Idea:** Ministra ya tiene el **mapa** de esquemas de firma: nginx `secure_link`, Wowza SecureToken, Nimble `wmsAuthSign`, Akamai `hdnts`, EdgeCast, Xtream, Flussonic `?token=`.

- **Por qué:** es una **especificación gratis** de cómo firmar para cada edge. Se reutiliza el *patrón de adapter*, **no** el MD5/secretos default.
- **Nexora:** **StreamTokenService** con `TokenAdapter` por motor; HMAC-SHA256 con IP+sesión+TTL; secretos en Vault, rotables. MVP: adapter Flussonic.

## 7. Zonas de stream / selección por geo-carga (C, A load balancer)

**Idea:** `stream_zones`/`ips_in_zone` para elegir streamer por región; `streaming_servers` con carga/latencia.

- **Por qué:** base de multi-región y routing por cercanía/capacidad.
- **Nexora:** `streaming.nodes` + `stream_zones` + scheduling en control-plane con métricas en Redis. (Hoy: routing por `flussonic_node`; falta el registry formal — Fase 4.4.)

## 8. Compatibilidad de contrato con clientes Xtream (A)

**Idea:** el contrato `player_api.php`/`get.php`/`xmltv.php` es un estándar de facto que consumen muchísimas apps IPTV.

- **Por qué:** ofrecer un **adaptador de compatibilidad** (read-only, server-side, sin credenciales en URL) amplía el universo de clientes sin atarse al diseño legacy.
- **Nexora:** opcional — `XtreamCompatService` que traduce el contrato Xtream a la Client API interna, emitiendo signed URLs (no credenciales). Prioridad F3.

## 9. Abstracción de billing externo (C: OssWrapper)

**Idea:** interfaz `OssWrapper` que delega alta/baja de suscripción a un sistema externo (hooks HTTP).

- **Por qué:** desacopla el motor IPTV del billing; permite OSS propio, Stripe, PSP local.
- **Nexora:** `BillingProvider` plug-in con webhooks **idempotentes** y cola/retry (el legacy lo hacía síncrono y con `var_dump`). Prioridad F2/F3.

## 10. Feature-flags por suscriptor / módulos (C: user_modules)

**Idea:** `user_modules(restricted, disabled)` habilita/inhabilita módulos por cuenta.

- **Por qué:** control fino de funcionalidades por plan/cliente.
- **Nexora:** tabla N:M `subscriber_features` o flags en `subscriptions`; evaluado server-side.

## 11. Modelo de estados de stream observables (A)

**Idea:** A formaliza estados (`stopped/running/starting/down/on_demand/direct`).

- **Por qué:** base para health y alertas de canal.
- **Nexora:** estado **efímero** en Redis alimentado por health checks a Flussonic (`get_stream_status`), no PID en BD. Ya existe `/api/admin/nodes/health`.

## 12. Blueprint de despliegue y tuning (B)

**Idea:** B documenta puertos, tmpfs, tuning MariaDB (innodb 1G, 2000 conns), GeoLite2, arranque de servicios.

- **Por qué:** sirve como checklist de parámetros productivos — **pero** implementado con IaC, secretos gestionados y artefactos firmados.
- **Nexora:** `docker-compose.production.yml` + Nginx + Certbot ya existen; falta IaC formal y tuning versionado (Fase producción).

---

## Tabla síntesis — concepto → servicio Nexora

| Concepto legacy | Fuente | Servicio Nexora destino |
|---|---|---|
| canal→links→streamer | C/A | ChannelCatalogService + FlussonicIntegrationService |
| autorización previa a firma | C | PlaybackAuthorizationService |
| tarifa→paquete→servicio | C | PlanService / SubscriptionService |
| catch-up por EPG | C | EPGService + StreamTokenService |
| watchdog/eventos | C | DeviceService + NotificationService |
| adapters de token | C | StreamTokenService |
| zonas de stream | C/A | FlussonicIntegrationService |
| contrato Xtream | A | XtreamCompatService (opcional) |
| billing externo | C | BillingService (`BillingProvider`) |
| feature-flags | C | SubscriberService |
| estados de stream | A | MonitoringService |
| tuning/puertos | B | Producción / IaC |

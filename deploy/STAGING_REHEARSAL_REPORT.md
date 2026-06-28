# Reporte — Ensayo de STAGING del PR #1 (P0)

> Ejecución del runbook `deploy/RUNBOOK_STAGING_P0.md` como **ensayo de staging en Docker local** (Postgres + Redis + API del PR #1 + **Nginx real con `auth_request`**). Fecha: 2026-06-27. Rama: `feat/p0-auth-playback-entitlements` @ `d112193` (= PR #1 HEAD).
> **No se tocó producción. No se contactó Flussonic real** (se usó un upstream *stub* para validar la DECISIÓN del gate). No merge, no deploy, flags no aplicados en producción.

## Entorno del ensayo
- DB aislada `nexora_staging` (copia de la DB dev), Redis **db 1** (aislado de dev db 0).
- API staging en `:8001` (imagen `nexora_api-api`, código del PR #1).
- **Nginx staging** real en `:8080` con el bloque `auth_request` de `nexoraplay.stream-auth.example.conf`, subrequest → `/internal/stream-auth/validate`.
- Upstream de `/stream/*` = **stub local** (devuelve `#EXTM3U stub-ok`), **no** Flussonic.
- Stack **dev intacto** tras el ensayo (DB `nexora` sigue en migración 004; `plan_channels` ausente en dev).

## Resultados por paso

| # | Paso | Resultado |
|---|---|---|
| 2 | Backup DB | ✅ `staging_backup.sql` (69 KB) |
| 4 | `alembic upgrade head` | ✅ **real 004 → 005** (la DB dev estaba en 004) |
| 5 | Confirmar migración 005 | ✅ `alembic_version=005`, `plan_channels` creada |
| 6–7 | Seed + canales en plan | ✅ "Test Plan" con **24 canales** (idempotente) |
| 8 | Nginx `auth_request` (staging) | ✅ real, gate operativo |
| 9 | Flags OFF (baseline) | ✅ login/catálogo/authorize OK; out-of-plan permitido (sin enforce) |
| 10 | `ENTITLEMENT_ENFORCE=true` | ✅ out-of-plan → **403 CHANNEL_NOT_INCLUDED** |
| 11 | `JWT_REQUIRE_AUD=true` | ✅ login/tokens válidos (aud/iss) |
| 12 | `SIGNED_URL_ENFORCE=true` | ✅ `playback_url` lleva `?token=` |
| 13 | `PLAYBACK_IP_BINDING_MODE=soft` | ✅ IP distinta → **200 + warning** en logs (no rompe) |
| 14 | Login | ✅ 200, `device_registration=registered` |
| 15 | Catálogo | ✅ 24 canales, **`stream_key` NO expuesto** |
| 16 | `playback/authorize` | ✅ 200 (in-plan) |
| 17 | `playback_url` HTTPS same-origin + token | ✅ con base same-origin → `…/stream/co-main/<key>/index.m3u8?token=` (ver hallazgo 1) |
| 18 | Manifest `.m3u8` 200 | ✅ vía gate (con token) |
| 19 | Segmentos HLS 200 por grant | ✅ segmento sin token → 200 (grant Redis) |
| 20 | `/stream` sin token/grant → 401 | ✅ 401; cross-stream → 401 |
| 21 | Canal fuera de plan → 403 | ✅ CHANNEL_NOT_INCLUDED |
| 22 | Device no registrado → 403 | ✅ DEVICE_NOT_REGISTERED |
| 23 | Logs sin token | ✅ Nginx: 0 `token=` (formato `stream_safe`); backend: token por header, no en query |
| 24 | Rollback flags + Nginx | ✅ flags OFF → out-of-plan vuelve a 200; staging desmontado; dev intacto |

**Cadena completa del cliente (flujo de producción) verificada:** `playback_url` same-origin con token → Nginx `auth_request` → manifest **200** (siembra grant) → segmento sin token **200** (grant) → cross-stream **401**. Sin contactar Flussonic real.

## Hallazgos

1. **Dependencia de configuración (crítica) — same-origin.** `playback_url` solo pasa por el gate si `FLUSSONIC_*_BASE_URL` **y** el `source_url` almacenado apuntan a `https://<dominio>/stream/<node>`. La config de **producción** (`.env.production.example`) ya lo hace ✅. Pero el `source_url` del **import M3U** debe ser same-origin para **todos** los canales: si algún canal quedó con URL de origen directo (`http://<ip>:8002/...`), **ese canal se salta el gate**. En el ensayo, el `source_url` dev apuntaba al origen directo y hubo que corregirlo. → **Verificar en producción que todos los `source_url`/bases son same-origin.**
2. **Nginx de producción aún sin `auth_request`.** `deploy/nginx/nexoraplay.conf` sirve `/stream/*` con `proxy_pass` plano (sin gate). El `auth_request` solo está en el `.example.conf`. Para proteger producción hay que **integrar** los bloques. (Esperado; no aplicado por instrucción.)
3. **Flussonic real no ejercitado.** Se validó la DECISIÓN del gate con upstream stub; la entrega real de bytes HLS a través del gate (passthrough `proxy_pass`) no se probó. Riesgo bajo (el `proxy_pass` no cambia), pero conviene un **smoke test** real.
4. **Continuidad larga no cronometrada.** TTL token 60s vs TTL grant 180s + heartbeat: lógica correcta y probada puntualmente, pero falta una **sesión larga real** (varios minutos) con Flussonic.

## Veredicto

### Merge del PR #1 → **LISTO (a falta de revisión humana)**
- Código correcto; CI verde (61 tests); el ensayo confirma el comportamiento P0 **de extremo a extremo con Nginx `auth_request` real**.
- Merge es **seguro**: todos los flags por defecto **OFF** → cero cambio de comportamiento al fusionar.
- Decisión de merge queda al revisor humano (el PR sigue congelado por instrucción).

### Habilitación en PRODUCCIÓN → **AÚN NO** (pasos de despliegue, no de código)
1. Verificar que **todos** los `source_url`/bases en prod son **same-origin** (hallazgo 1).
2. **Integrar `auth_request`** en `nexoraplay.conf` (hallazgo 2).
3. **Smoke test con Flussonic real** (manifest + segmentos a través del gate) + **prueba de continuidad larga** (hallazgos 3–4).
4. Aplicar el **seed `plan_channels` ANTES** de `ENTITLEMENT_ENFORCE=true`.
5. Activar flags **gradualmente** (entitlement → JWT → signed-url → IP `soft`; `strict` solo si no rompe clientes móviles), con **rollback por flag** listo (verificado).
5. Idealmente sobre un **host staging separado** con Flussonic real (este ensayo fue Docker local con stub).

> Conclusión: **el código del PR #1 está validado y listo para merge tras revisión humana**; la **activación en producción** requiere los 6 pasos de despliegue anteriores, no cambios de código.

---

# Segundo ciclo — backfill same-origin + re-ensayo (PR #3)

> Cierra el **hallazgo 1** del primer ciclo: los `source_url` apuntaban al origen directo y se saltarían el gate. Trabajo de [`PR #3 — fix/channel-source-same-origin`]. Mismo entorno (Docker local, Redis db1, upstream **stub**, **sin Flussonic real**). No producción, no merge, no deploy.

## 1. Estado inicial (auditor contra `nexora_staging`)
- Canales auditados: **24** · OK: **0** · RISK: **24** · exit **2**.
- Motivo único: `source_url = http://<ip>:8002/<stream>/index.m3u8` (origen directo → no pasa por `/stream/<node>/`).

## 2. Fix aplicado en staging (backfill `--apply`)
- Script `scripts/backfill_channel_source_urls_same_origin.py`, modo default **`relative`**.
- Resultado: `source_url = /stream/<node>/<key>/index.m3u8` (same-origin).
- Mapeo IP→node aplicado: `38.210.187.13 → co-main`, `181.78.246.211 → ec-main`.
- **No** se tocaron hosts desconocidos (se reportan como RISK, no se modifican).
- **No** se modificó producción; solo la DB aislada `nexora_staging`.

## 3. Re-auditoría (tras backfill)
- Canales auditados: **24** · OK: **24** · RISK: **0** · exit **0**. ✅

## 4. Re-ensayo P0 con flags ON
Flags: `ENTITLEMENT_ENFORCE=true`, `JWT_REQUIRE_AUD=true`, `SIGNED_URL_ENFORCE=true`, `PLAYBACK_IP_BINDING_MODE=soft`.

| Comprobación | Resultado |
|---|---|
| login | **200** |
| catálogo | **24** canales |
| `stream_key` expuesto al cliente | **No** |
| authorize canal válido (in-plan) | **200** |
| `playback_url` | **same-origin** `/stream/<node>/<key>/index.m3u8?token=…` (token enmascarado) |
| manifest vía gate | **200** |
| segmento sin token (grant Redis) | **200** |
| segmento cross-stream | **401** |
| canal fuera de plan | **403** `CHANNEL_NOT_INCLUDED` |
| device no registrado | **403** `DEVICE_NOT_REGISTERED` |

→ El `playback_url` que produce el sistema (con el `source_url` ya corregido por el backfill) **enruta por el gate**, a diferencia del primer ciclo donde había que forzar la base same-origin a mano.

## 5. Nginx `ec-quito` (Quito Astra)
- Se agregó al **ejemplo** `deploy/nginx/nexoraplay.stream-auth.example.conf` una location gated `^~ /stream/ec-quito/` (auth_request → origen Quito). **No aplicado a producción.**
- `ec-quito` quedó cableado en código (config + `get_flussonic_node_client`) y reconocido por auditor/backfill.
- **Aún no hay canales** en `ec-quito`. Cuando se agreguen, el import (default `relative`) ya los dejará same-origin automáticamente.

## 6. Incidente de seguridad (detectado y remediado)
- Se detectaron **credenciales reales** de `ec-quito` colocadas en `.env.production.example` (archivo **versionado**) — en el **working tree**.
- Verificado: **NO fueron commiteadas ni pusheadas** (no aparecen en el historial de git).
- Remediación: las credenciales se movieron a **`.env` (gitignored)**; `.env.production.example` quedó con **placeholders vacíos** (resto del archivo: solo placeholders `CHANGE_ME_*`).
- Las credenciales **no se incluyen** en este reporte.
- Recomendación: **rotar esa credencial antes de producción** si era real (apareció en transcript/IDE local, aunque nunca en git).

## 7. Caveat (igual que el primer ciclo)
- Ensayo en **Docker/local con upstream stub**; **no** se contactó Flussonic/Astra real.
- Falta la prueba final en **host staging/producción real** con **Nginx real + Flussonic/Astra real** (manifest+segmentos reales, continuidad larga, y locations incl. `/stream/ec-quito/`).
- **No** se afirma “listo para producción”: el estado es **“listo para revisión humana y staging real”**.

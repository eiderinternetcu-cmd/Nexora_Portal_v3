# 07 — Subscribers / Plans / Devices / Entitlements (resumen)

> **Resumen ejecutivo.** La investigación profunda (comparativa legacy, estado actual, modelo de datos, endpoints, reglas, integración playback con reason codes, Redis/cache, riesgos, backlog ENTITLEMENT-001..030, pruebas y checklist) está en **[modules/03_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_DEEP.md](modules/03_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_DEEP.md)**.

---

## Gap crítico (la razón de este módulo)

**No existe `plan_channels`.** Hoy `playback/authorize` valida que la suscripción esté activa, pero **no** que el plan incluya el canal → un suscriptor activo puede ver **todos** los canales. Es el pendiente directo del Módulo 2. Se cierra con `plan_channels(plan_id, channel_id)` + `EntitlementService` consultado por el `PlaybackAuthorizationService`.

## Decisiones aprobadas

1. **`plan_channels` directo** (MVP); `packages/package_contents` → Fase 2.
2. **`subscriptions.status` enum**: `active / expired / cancelled / suspended` + ventana real `starts_at / ends_at`.
3. **`devices.status` enum**: `active / blocked / pending`.
4. **Login NO falla por device cap**; **`/devices/register` → 409 Conflict** al superar `max_devices`.
5. **`/playback/authorize` exige device registrado y activo** y consulta **`EntitlementService.can_watch_channel`**.
6. **PostgreSQL es la fuente de verdad**; Redis solo caché con invalidación (borrar, no solo expirar, en suspensión/cancelación/cambio de plan).
7. **`max_devices` ≠ `max_connections`** (registro vs concurrencia) — ya separados en el modelo.
8. Historiales (`subscriber_status_history`, `subscription_history`, `device_blocks` reversible, `device_history`) + auditoría de toda mutación admin.

## Estado en Nexora (alto nivel)

| Capacidad | Estado |
|---|---|
| subscriber.status enum, plans (max_conn/max_devices/is_active), subscriptions (starts/expires), create/renew/cancel, devices (is_blocked/fingerprint), concurrencia ZSET | ✅ |
| `plan_channels`, EntitlementService, entitlement en authorize, subscription.status enum, device.status enum, 409 en register, login desacoplado | ⬜ (MVP del módulo) |
| credentials separadas, history tables, entitlement cache, change-plan endpoint, packages, plan_vod/series | 🟡/⬜ (F2/F3) |

## Integración con Playback (reason codes)

`PlaybackAuthorizationService` → `EntitlementService.can_watch_channel(subscriber_id, device_id, channel_id)` → `{allow, reason_code}`. Orden de checks (primer fallo gana):
`SUBSCRIBER_SUSPENDED · SUBSCRIBER_DISABLED · SUBSCRIPTION_NOT_FOUND · SUBSCRIPTION_EXPIRED · PLAN_INACTIVE · CHANNEL_INACTIVE · CHANNEL_NOT_INCLUDED · DEVICE_NOT_REGISTERED · DEVICE_BLOCKED`. Concurrencia (`DEVICE_LIMIT_REACHED`/409) se evalúa después en Playback. **Deny → 403, sin firmar URL** (no toca Flussonic). → **[§7 del deep](modules/03_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_DEEP.md)**.

## Consulta de entitlement (1 query indexada)
```sql
SELECT 1 FROM subscriptions s
JOIN plans p ON p.id=s.plan_id AND p.is_active
JOIN plan_channels pc ON pc.plan_id=s.plan_id
WHERE s.subscriber_id=:sub AND s.status='active' AND s.expires_at>now()
  AND pc.channel_id=:channel LIMIT 1;
```
PK(plan_id,channel_id) + idx(channel_id) → O(log n). → **[§4 del deep](modules/03_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_DEEP.md)**.

## Backlog (orden MVP)
`ENT-001` migración plan_channels → `002` modelo → `003` EntitlementService → `004` integrar en authorize → `005` endpoints admin plan↔canales → `006` seed (plan anual = 24 canales) → `007` tests incluido/no incluido → `008` caché Redis + invalidación. Luego status/device enums, 409, history, change-plan, etc. → **[§10 del deep](modules/03_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_DEEP.md)**.

## Pruebas y checklist
22 casos (entitlement, devices, operación/rendimiento) + checklist de aceptación → **[§11–12 del deep](modules/03_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_DEEP.md)**.

---

> Modelo de datos completo (16 tablas con prioridad MVP/F2/F3), endpoints admin/client/interno, reglas de negocio (20) y Redis keys: en el documento profundo.

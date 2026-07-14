# 12 — Roadmap priorizado

> Fases ordenadas con dependencias, alineadas con el estado real de `nexora_api` (que ya está en **Fase 4** de su propio plan: estabilización + producción). Estimación orientativa para 2–3 ingenieros.

---

## Línea base: dónde está Nexora hoy

✅ Fases 1–3 del proyecto completas (auth, client API, catálogo, Flussonic integration, web player dockerizado, multi-device LAN).
🔧 **Fase 4 en curso:** observabilidad base, hls.js hardening, deploy producción `nexoraplay.net` (HTTPS, `/stream/*`, lockdown UFW). Pendiente: signed URLs activas, stress, observabilidad extendida, registry Flussonic formal.

Este "best-of" **no reinicia** el roadmap: lo **completa** integrando lo aprendido de las 3 auditorías.

---

## Mapa de fases

| Fase | Nombre | Servicios | Depende de | Prio | Estimación |
|---|---|---|---|---|---|
| **0** | Base ya existente | Auth, Subscriber, Subscription, Catalog, PlaybackAuth, Concurrency, Proxy, WebPlayer | — | ✅ | hecho |
| **1** | Hardening de seguridad MVP | Argon2id, device secret/cert, signed URL+IP-binding, anti-IDOR entitlement, audit login | 0 | 🔴 | 2–3 sem |
| **2** | Entitlements normalizados | Plan/packages/contents (Alembic 005), authorize por paquete | 1 | 🔴 | 1–2 sem |
| **3** | Stress + estabilización playback | pruebas de carga/zapping/reconexión; concurrencia Lua | 1 | 🔴 | 1–2 sem |
| **4** | Observabilidad extendida | Prometheus/OTel, alertas stream caído, métricas concurrencia | 0 | 🟠 | 1–2 sem |
| **5** | Multi-Flussonic Registry + failover | FlussonicIntegration formal, zonas, salud | 2 | 🟠 | 1–2 sem |
| **6** | RBAC + Admin + Audit inmutable + Resellers | Admin/Audit | 1 | 🟠 | 2–3 sem |
| **7** | EPG real | EPGService (ingest async+cron) | 2 | 🟠 | 2 sem |
| **8** | STB/MAG endurecido + Notification | Device/Notification | 1 | 🟠 | 2 sem |
| **9** | VOD / Series / Catch-up | VODService, DVR | 2,5,7 | 🟢 | 3–4 sem |
| **10** | Billing | BillingService | 6 | 🟢 | 2–3 sem |
| **11** | Astra + XtreamCompat | Astra/XtreamCompat | 5 | 🟢 | 2–3 sem |
| **12** | Apps nativas (TV/Mobile/iOS) | clientes | 1–3 estables | 🟢 | continuo |
| **13** | Producción avanzada (IaC, multi-región, DR) | infra | 1–6 | 🟢 | continuo |

---

## Grafo de dependencias (resumen)

```
[0 base] ──► [1 hardening] ──► [2 entitlements] ──► [5 Flussonic registry] ──► [9 VOD/catch-up]
   │              │                  │                       │
   │              ├──► [3 stress]    ├──► [7 EPG] ───────────┘
   │              ├──► [6 RBAC/Audit] ──► [10 Billing]
   │              └──► [8 STB/Notif]
   └──► [4 observabilidad] (transversal)         [11 Astra/XtreamCompat] ◄── [5]
                                                 [12 apps] ◄── (1–3 estables)
                                                 [13 producción] (transversal)
```

**Reglas de orden:**
- Seguridad (Fase 1) es **prerrequisito** de casi todo: signed URLs, device fuerte y anti-IDOR antes de abrir más superficie.
- **Apps nativas (12) NO empiezan** hasta que playback+sesiones+auth+observabilidad estén estables (restricción explícita del proyecto).
- Billing (10) tras RBAC/Audit (6). VOD (9) tras entitlements (2) + EPG (7) + registry (5).

---

## Hitos (definición de "listo")

| Hito | Criterio |
|---|---|
| **M1 – Playback seguro** | signed URL HMAC+IP activa, backend-auth Flussonic on, anti-IDOR por entitlement, Argon2id, device secret. |
| **M2 – Operación observable** | métricas+alertas de stream/concurrencia; auditoría de admin inmutable. |
| **M3 – Catálogo completo** | EPG real + multi-nodo con failover; parental PIN. |
| **M4 – Monetización** | VOD/series/catch-up + billing idempotente. |
| **M5 – Escala** | Astra/XtreamCompat, apps, multi-región/CDN, IaC+DR. |

---

## Riesgos de roadmap y mitigación

| Riesgo | Mitigación |
|---|---|
| Activar signed URLs rompe playback actual | feature-flag por canal; validar en staging antes de prod |
| Migración de entitlements (plan→paquetes) | Alembic reversible + datos de prueba; mantener compat temporal |
| Streams externos caídos (fuentes IPTV) | ya conocido; health checks + alertas; no bloquear catálogo |
| Deps de producción (DNS, cert, UFW) | runbook `PRODUCTION_NEXORAPLAY.md` + diagnóstico |
| Apps antes de estabilizar core | bloqueado por restricción de fase |

---

## Próximos 3 pasos concretos (recomendado)
1. **Cerrar M1:** verificar/forzar Argon2id, activar firma+IP-binding en `playback_url` con backend-auth Flussonic (el endpoint ya existe), y añadir entitlement por paquete (Alembic 005).
2. **Stress test** del playback con métricas encendidas (zapping, 3–6h, reconexión, reinicio Redis/backend).
3. **Observabilidad extendida + auditoría de login admin** para medir y rastrear lo anterior.

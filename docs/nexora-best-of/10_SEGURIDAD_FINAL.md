# 10 — Seguridad final

> Política de seguridad de Nexora. Para cada punto: **riesgo que mitiga · implementación · prioridad · cómo verificarlo**. Derivada del consenso de las tres auditorías. 🟢 ya presente en `nexora_api` · 🟡 parcial · ⬜ pendiente.

---

| # | Control | Riesgo que mitiga | Implementación | Prio | Verificación |
|---|---|---|---|---|---|
| 1 | **JWT access corto** | robo de token de larga vida | access 15m (admin)/sesión cliente; `type` claim; firma verificada | 🔴 🟡 | decodificar y comprobar `exp`; token viejo → 401 |
| 2 | **Refresh revocable + rotación** | reuso de refresh robado | refresh en Redis con jti; rotar en cada uso; reuse-detection | 🔴 🟢🟡 | reusar refresh rotado → revoca familia |
| 3 | **Playback tokens de vida corta** | replay de enlaces | JWT playback 60s + Redis `nexora:playback:{jti}` | 🔴 🟢 | esperar 60s → token inválido |
| 4 | **Signed URLs (HMAC)** | hotlink / acceso sin permiso | HMAC-SHA256 (content+node+exp+ip+ses); validado en edge | 🔴 🟡 | URL manipulada/expirada → 401 en edge |
| 5 | **Anti-hotlink / IP-binding** | compartir tokens | token ligado a `client_ip` + TTL corto | 🟠 ⬜ | misma URL desde otra IP → rechazada |
| 6 | **Rate-limit por IP/usuario** | fuerza bruta, enumeración | Redis token-bucket + Nginx `limit_req` | 🔴 🟡 | N peticiones → 429 |
| 7 | **Anti mixed-content** | bloqueo del player en HTTPS | `/stream/*` proxy HTTPS same-origin (origen oculto) | 🔴 🟢 | `playback_url` empieza por `https://` mismo dominio |
| 8 | **CORS controlado** | uso no autorizado cross-site | orígenes explícitos en prod, `allow_credentials` solo donde toca | 🟠 🟡 | preflight desde origen no listado → bloqueado |
| 9 | **Headers de seguridad** | clickjacking, sniffing | HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, CSP | 🟠 🟢🟡 | `curl -I` muestra headers (HSTS ya activo) |
| 10 | **Logs sin secretos** | fuga de credenciales/tokens | logging estructurado; enmascarar tokens/passwords | 🟠 🟡 | grep de logs no revela tokens |
| 11 | **No exponer IP origen Flussonic** | re-stream | solo signed URLs vía edge; `stream_key` interno | 🔴 🟢 | respuestas nunca contienen IP:puerto origen |
| 12 | **Validación estricta de payloads** | inyección, datos basura | Pydantic (límites realistas, p.ej. `os_version<=512`) | 🟠 🟢 | payload inválido → 422 |
| 13 | **Auditoría de acciones admin** | falta de trazabilidad | `audit.audit_log` append-only (login admin, cambios, tokens) | 🟠 🟡 | acción admin deja registro inmutable |
| 14 | **Separación admin/client/stb** | escalada entre superficies | routers `/api/admin`,`/api/client`,`/api/stb` con auth distinta | 🔴 🟢 | token de cliente no abre endpoints admin |
| 15 | **Firewall / UFW** | exposición de puertos internos | solo 80/443 públicos; 8000/5173/DB/Redis cerrados | 🔴 🟢 | nmap externo: solo 80/443 |
| 16 | **Nginx reverse proxy seguro** | superficie directa de la app | TLS, proxy headers, sin exponer upstreams | 🔴 🟢 | upstream no accesible directo |
| 17 | **Secretos por entorno/Vault** | secretos en repo/BD/disco | `.env`/Vault; nada en git; escaneo en CI | 🔴 🟡 | secret-scan en CI = 0 hallazgos |
| 18 | **Backups + DR** | pérdida de datos | PostgreSQL PITR + snapshots + runbook restore | 🟠 ⬜ | restore probado en staging |
| 19 | **Monitoreo** | ceguera operativa | Prometheus/Grafana/OTel; health de edges | 🟠 🟡 | dashboards + `/metrics` |
| 20 | **Alertas** | incidentes no detectados | alertas de stream caído, error de token, concurrencia, latencia | 🟠 ⬜ | simular caída → alerta dispara |

---

## Controles adicionales (de las auditorías)

| Control | Riesgo | Implementación | Prio |
|---|---|---|---|
| **Argon2id** (no MD5/salt-fijo) | crackeo de passwords | `passlib`/`argon2-cffi`; verificar el hash actual del proyecto | 🔴 |
| **Identidad device fuerte** | spoof de MAC, auto-add | device_secret/cert + activación explícita | 🔴 |
| **Concurrencia atómica** | exceso por carrera | Redis ZSET (+Lua) | 🔴 🟢 |
| **Mínimo privilegio BD** | SQLi → todo el motor | usuario DML acotado al schema; sin GRANT OPTION | 🟠 |
| **mTLS control-plane↔nodos** | tráfico interno no auth | mTLS + JWT de servicio | 🟠 ⬜ |
| **Parental PIN server-side** | acceso a adulto sin control | PIN validado en backend (no flag cliente) | 🟠 ⬜ |
| **Ingest EPG sin XXE/DoS** | XXE, disco | parser endurecido + límites | 🟢 (al construir EPG) |
| **Verificación de artefactos** | supply-chain | firmas/checksums (cosign), SBOM | 🟠 ⬜ |
| **Flussonic READ-ONLY** | modificar streaming prod | `_WriteBlocker` (ya existe); nunca create/update/delete | 🔴 🟢 |

---

## Política de secretos (resumen)

- **Nunca** en repo/BD/disco en claro. `.env` fuera de git (ya en `.gitignore`); credenciales Flussonic solo backend.
- Rotación periódica; secretos de firma (HMAC) por edge, rotables.
- En tablas solo `*_hash`/`*_ref` (referencia a Vault), nunca el valor.
- CI con **secret scanning** que bloquea el merge si detecta secretos.

## Cabeceras de seguridad recomendadas (Nginx)
```
Strict-Transport-Security: max-age=31536000; includeSubDomains        ✅ activo
X-Frame-Options: SAMEORIGIN                                            ✅
X-Content-Type-Options: nosniff                                        ✅
Referrer-Policy: strict-origin-when-cross-origin                       ✅
Content-Security-Policy: default-src 'self'; media-src 'self' blob:…   ⬜ afinar para hls.js
```

## Cómo se verifica la postura (resumen ejecutable)
1. `nmap` externo → solo 80/443.
2. `curl -I https://dominio` → headers de seguridad presentes.
3. authorize de canal no suscrito → `403` (anti-IDOR).
4. token expirado / IP distinta → edge `401` (cuando backend-auth activo).
5. respuestas API no contienen `stream_key` ni IP origen.
6. secret-scan en CI = 0.
7. pentest OWASP antes de producción real.

## Prioridad consolidada
- **P0:** Argon2id, signed URLs+IP-binding, anti-IDOR central, secretos fuera de repo, concurrencia atómica, separación de superficies, firewall, Flussonic read-only.
- **P1:** RBAC+audit inmutable, rate-limit/lockout formal, mTLS interno, parental PIN, monitoreo+alertas, backups/DR, verificación de artefactos.
- **P2:** CSP afinada, métricas extendidas, hardening de despliegue (IaC).

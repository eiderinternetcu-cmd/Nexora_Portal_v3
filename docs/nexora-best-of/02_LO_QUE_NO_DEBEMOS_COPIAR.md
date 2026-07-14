# 02 — Lo que NO debemos copiar

> Antipatrones y vulnerabilidades confirmados en las tres auditorías. Para cada uno: dónde aparece, por qué es peligroso, y la **mitigación Nexora**. Secretos enmascarados; sin pasos de explotación.

---

## A. Seguridad de credenciales y secretos

| # | Antipatrón legacy | Fuente | Riesgo | Mitigación Nexora |
|---|---|---|---|---|
| 1 | **Clave de cifrado fija** (XOR `config`) | A,B | Descifrado trivial de credenciales BD | Secretos en **Vault/env**; cifrado autenticado (AES-GCM) con clave gestionada (KMS); rotación |
| 2 | **Hash con salt fijo** (`$6$…$xtreamcodes$`) / **MD5 sin salt** | A,C | Precomputación/rainbow; mismo hash para misma clave | **Argon2id** con salt aleatorio por usuario; `verify` constant-time |
| 3 | **Admin por defecto** `admin/admin`, `admin/1` | B,C | Acceso trivial si no se cambia | Bootstrap con secreto **efímero** + cambio forzado en primer login; sin credencial persistente |
| 4 | **Secretos en disco/repo** (`/root/credentials.txt`, `config.ini` versionado, key en `L10n.php`) | B,C | Persistencia y fuga de secretos | Nada de secretos en repo/disco; gestor de secretos; escaneo en CI |
| 5 | **Sesión admin = el propio hash** en `$_SESSION['pass']` | C | Filtrar BD ⇒ suplantar admin sin clave | Tokens de sesión opacos/JWT en Redis; **nunca** guardar el hash; regenerar al login |
| 6 | **`ssh_password` de nodos en BD** | A,B | Compromiso BD = acceso SSH a la flota | Claves SSH (no password) en Vault; BD sin secretos |

## B. Autorización y playback (el núcleo)

| # | Antipatrón legacy | Fuente | Riesgo | Mitigación Nexora |
|---|---|---|---|---|
| 7 | **IDOR en `createLink`** (sin validar suscripción/parental/MAC) | C | Cualquier dispositivo reproduce cualquier canal (no suscrito/adulto) | **Toda** URL pasa por `PlaybackAuthorizationService`; ningún otro camino emite enlace |
| 8 | **Credenciales en la URL** de playback y en el M3U | A | user/pass en logs/proxies/historial | JWT de sesión + **signed URL** sin credenciales |
| 9 | **Tokens débiles** (MD5 de `microtime+uniqid`, secretos default `supersecret`/`defaultpassword`) | C | Predicción/forja de tokens | **HMAC-SHA256** con secreto fuerte en Vault; `random_bytes` CSPRNG donde aplique |
| 10 | **Sin IP-binding por defecto** (token sin `$remote_addr`) | C | Tokens compartibles dentro del TTL | Token ligado a **IP + sesión + TTL corto** (5–30 s live) |
| 11 | **Origen real del stream expuesto** (M3U revela IP origen) | A,C | Re-stream / hotlink | Origen **oculto** tras edge; solo signed URLs vía `/stream/*` HTTPS |
| 12 | **TTL excesivos** (catch-up 8 h, Akamai 30 000 s) | C | Ventana de replay amplia | TTL mínimos por tipo; renovación, no expiración larga |
| 13 | **Streams sin token** (`rtp://`/`udp://` passthrough) | C | Acceso sin autenticar | Todo origen detrás de auth/firma; nada passthrough |

## C. Identidad de dispositivo y sesiones

| # | Antipatrón legacy | Fuente | Riesgo | Mitigación Nexora |
|---|---|---|---|---|
| 14 | **Identidad solo por MAC** (suplantable) + `auto_add_stb=true` | C | Auto-provisión/suplantación de dispositivos | `device_id` + **`device_secret`/cert**; activación explícita; sin auto-add silencioso |
| 15 | **Heartbeat solo por MAC, sin token** | C | keep-alive spoofeable | Heartbeat **autenticado** con token de device |
| 16 | **Concurrencia por `COUNT(*)`** no atómica | A | Carreras: se exceden conexiones | Redis `INCR/DECR` con TTL (atómico) |
| 17 | **Límite de duración solo en cliente** (`player.js`) | C | Bypass con cliente modificado | Enforcement **server-side** (sesión + TTL + heartbeat) |
| 18 | **Borrado en cascada amplio** (eliminar MAG borra la línea) | A | Pérdida de datos por error | Operaciones acotadas; soft-delete + auditoría |

## D. Datos y modelo

| # | Antipatrón legacy | Fuente | Riesgo | Mitigación Nexora |
|---|---|---|---|---|
| 19 | **JSON/CSV en columnas TEXT** (`bouquet`, `sub_ch`, `allowed_ips`) | A,C | Sin integridad ni joins; duplicados | Tablas N:M normalizadas + FK; `jsonb` solo como cache |
| 20 | **Sin FKs** (MyISAM) / **MyISAM** | C | Huérfanos, sin transacciones, crash-unsafe | PostgreSQL + FK/constraints + transacciones |
| 21 | **`status` invertido** (0=activo,1=suspendido) | C | Errores operativos | **Enum explícito** (`active/suspended/expired`) |
| 22 | **FKs tipo string** (`service_id varchar` → id numérico) | C | Sin integridad referencial | FK reales + `content_type` enum (polimorfismo) |
| 23 | **`settings` de 1 fila / IDs secuenciales** | A,C | Rigidez / enumeración | `settings(key, value jsonb)`; **UUID** en API pública |
| 24 | **Logs sin TTL** (`played_*`, `user_log`) | C | Crecimiento ilimitado | Tablas **particionadas por fecha** + retención automática |
| 25 | **EPG sin `UNIQUE` ni límite de descarga** | C | Duplicados; DoS/disco por feed gigante | `UNIQUE(channel_id,start_at)`; límite tamaño/tiempo/rate; parser sin XXE |

## E. Infraestructura, despliegue y supply-chain

| # | Antipatrón legacy | Fuente | Riesgo | Mitigación Nexora |
|---|---|---|---|---|
| 26 | **Descarga de blobs sin verificar** (`curl\|python` como root) | B | Supply-chain: ejecución de código arbitrario | **IaC** (Terraform/Ansible) + artefactos firmados (cosign/GPG) + SBOM |
| 27 | **`chmod -R 0777`** | A,B | Escalada local, manipulación de binarios | Permisos mínimos (644/755), usuario dedicado, config 600 |
| 28 | **Usuario BD `ALL PRIVILEGES … WITH GRANT OPTION`** | A,B | Una SQLi compromete todo el motor | Mínimo privilegio (DML sobre el schema), sin global ni GRANT |
| 29 | **BD compartida main↔load balancers** | A,B | Blast radius total | Control-plane/data-plane con API autenticada (mTLS) |
| 30 | **tmpfs 90% RAM para HLS** | A,B | OOM con muchos streams | Media server gestionado + límites de recursos + autoscaling |
| 31 | **Bypass de licencia vía `/etc/hosts`** | B | **Riesgo legal**; enmascara salidas | Plataforma **propia y legal**; resolución DNS estándar |
| 32 | **`sudoers` NOPASSWD amplio**, Python 2.7 EOL, PHP 7.0 EOL | A,B,C | Superficie de privilegios / sin parches | Stack soportado; sudo acotado o nulo |
| 33 | **Watchdog `pid_monitor.php` por polling de BD** | A,B | Frágil, contención | Health checks + Prometheus + autoreparación |

## F. Código inseguro de aplicación (panel/legacy)

| # | Antipatrón legacy | Fuente | Riesgo | Mitigación Nexora |
|---|---|---|---|---|
| 34 | **SQLi** (`$_GET` en `LIKE`) y **XSS reflejado** (echo de `$_GET`) | C | Inyección / robo de sesión admin | ORM + consultas parametrizadas; escape de salida; validación Pydantic |
| 35 | **Sin CSRF** + acciones mutadoras por GET | A,C | Acciones forzadas | Tokens CSRF / métodos POST / SameSite; o API con JWT Bearer |
| 36 | **SSRF** (`file_get_contents`/`api.php` a host de BD; FlexCDN) | A,C | Peticiones a hosts internos | Lista blanca de destinos; cliente HTTP con timeouts y validación |
| 37 | **Command injection** (`exec("rm …")`), **upload por MIME de cliente** | C | RCE / subida maliciosa | `unlink`/argumentos como lista; validar contenido (`finfo`), fuera de webroot |
| 38 | **`var_dump`/trazas en producción**; directorios sin protección | C | Fuga de URLs/estado; listado de dirs | Logs estructurados sin secretos; nada de debug en prod |
| 39 | **Núcleo ofuscado / IonCube + `core.lic`** | C | Ilegible, no auditable, atado a licencia | Código propio legible; nada ofuscado/nulled |

---

## Regla de oro

> **Ningún artefacto legacy se migra como código.** Se migra el **conocimiento del dominio** (modelo de datos, flujos, puertos, parámetros) hacia el diseño limpio de Nexora. Todo lo de esta lista se considera **clase de error a prevenir por diseño**, verificable en CI y en revisión de seguridad ([10_SEGURIDAD_FINAL.md](10_SEGURIDAD_FINAL.md)).

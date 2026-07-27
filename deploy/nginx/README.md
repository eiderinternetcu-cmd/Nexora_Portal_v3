# Nginx — edge multi-marca

Un solo nginx sirve el mismo web player + API bajo varios dominios de marca.
Añadir una marca nueva debe costar **un archivo de ~20 líneas**, no una copia del
edge entero.

**Para dar de alta un dominio → [`../RUNBOOK_EDGE_MULTIDOMINIO.md`](../RUNBOOK_EDGE_MULTIDOMINIO.md).**
Este archivo explica cómo está montado el edge y qué **no** hay que deshacer.

---

# 🚨 SI ESTÁS EN UN INCIDENTE, EMPIEZA AQUÍ

```bash
docker ps --filter name=nexora_nginx --format '{{.Status}}'
docker logs --tail 30 nexora_nginx | grep -i emerg
```

| Lo que ves | Causa | Sección |
|---|---|---|
| `Restarting` en bucle | Config inválida + `restart: unless-stopped`. **Un restart con config rota tumba TODOS los dominios**, a diferencia de un reload. | [§1](#1-un-reload-nunca-tumba-nginx-un-restart-sí) |
| `[emerg] host not found in upstream "nexora_hls"` | El stack de **transcodificación está parado**. nginx no arranca → **caen todas las marcas**, no solo `tc-main`. | [§2](#2-nginx-no-arranca-si-la-transcodificación-está-parada) |
| `[emerg] unknown log format "stream_safe"` | Alguien renombró o reordenó `00-shared-http.conf`. | [§3](#3-el-prefijo-00--es-funcional) |
| Un dominio da aviso de certificado y otro no | Falta el reload tras la renovación. **Fallo intermitente POR DOMINIO.** | [§4](#4-no-hay-hook-de-renovación--pendiente-en-producción) |
| Un vhost nuevo "no se carga" y no hay ningún error | Producción monta **un archivo suelto**, no el directorio. Tus `.conf` nuevos no los lee nadie. | [§5](#5-producción-monta-un-archivo-no-el-directorio) |
| Faltan contenedores `nexora_tc_*` / `nexora_hls` | Alguien ejecutó `--remove-orphans`. | [§6](#6-mina-docker-compose-sugiere-destruir-la-transcodificación) |

**Reload seguro (el `&&` no es decorativo):**

```bash
docker exec nexora_nginx nginx -t && docker exec nexora_nginx nginx -s reload
```

**Rollback y recuperación de crashloop → [RUNBOOK §7](../RUNBOOK_EDGE_MULTIDOMINIO.md#7-rollback).**

---

# ⚠ Lo primero que hay que saber: el repo NO es producción

**`/opt/nexora_api` no es un repositorio git.** No hay `git pull`: el código llega
por copia (`rsync`/`scp`). **Cualquier deriva entre lo que corre y lo que está
versionado aquí es invisible desde el repositorio.**

Y hay una deriva viva ahora mismo:

| | Este repositorio | Producción en ejecución |
|---|---|---|
| Montaje nginx | `conf.d/` + `snippets/` (directorios) | `nexoraplay.conf` → `default.conf` (**archivo suelto**) |
| Estructura efectiva | factorizada | monolítica |

**Antes de tocar nada, averigua en qué mundo estás:**

```bash
docker inspect nexora_nginx --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

Si aparece `/etc/nginx/conf.d/default.conf` → mundo "archivo suelto", los
`.conf` de este árbol **no se están leyendo**. Ver [§5](#5-producción-monta-un-archivo-no-el-directorio).

---

## Estructura del repositorio

```
deploy/nginx/
├── conf.d/                     → destino: /etc/nginx/conf.d:ro (nginx auto-incluye por glob)
│   ├── 00-shared-http.conf     ← maps + log_format stream_safe. EL PREFIJO 00- ES FUNCIONAL (§3)
│   ├── 00-default-server.conf  ← catch-all :80 (+ reto ACME) y :443 (ssl_reject_handshake)
│   ├── laredtelco.conf         ← vhost fino: server_name + certs + HSTS
│   └── nexoraplay.conf         ← vhost fino: server_name + certs + HSTS
├── snippets/                   → destino: /etc/nginx/snippets:ro (FUERA de conf.d a propósito:
│   │                             cualquier .conf dentro de conf.d se incluye solo, y un snippet
│   │                             suelto de locations en scope http{} no parsea)
│   ├── tls-common.conf
│   ├── security-headers.conf
│   ├── proxy-common.conf
│   ├── app-locations.conf
│   └── stream-gate.conf        ← ÚNICA copia del control de autorización de /stream/*
└── staging/                    → NO se monta en producción. Ver §7.
    ├── nexoraplay.staging.conf
    └── nexoraplay.stream-auth.example.conf
```

---

# Las seis trampas — no las deshagas

## 1. Un reload nunca tumba nginx; un restart sí

Reproducido en `nginx:1.27-alpine` con una configuración deliberadamente rota:

| Acción con config inválida | Resultado medido |
|---|---|
| `nginx -s reload` | Reload **rechazado**. El proceso viejo **sigue sirviendo**; el certificado se seguía entregando por SNI después del fallo. |
| `docker restart` / `up -d` / reboot | Contenedor **muerto**: `Exited (1)`. Con `restart: unless-stopped` → **crashloop**, las dos marcas caídas. |

La diferencia no está en el riesgo del cambio, sino en si nginx tiene un proceso
viejo al que agarrarse. Por eso:

- **Nunca** `nginx -s reload` sin `nginx -t &&` delante. Sin el `&&`, un typo se
  despliega igual.
- **Valida siempre antes de cualquier cosa que recree el contenedor**, porque un
  reinicio del servidor ejecuta ese camino de madrugada sin que nadie mire.

Validar sin tocar producción → [RUNBOOK anexo A](../RUNBOOK_EDGE_MULTIDOMINIO.md#anexo-a--validar-sin-tocar-producción).

## 2. nginx no arranca si la transcodificación está parada

nginx resuelve los upstream de `proxy_pass` **al CARGAR la configuración**, no
por petición. En `snippets/stream-gate.conf`:

```nginx
location ^~ /stream/tc-main/ {
    proxy_pass http://nexora_hls:80/;    # ← nombre de contenedor
```

`nexora_hls` es un **nombre de contenedor**. Si el stack de transcodificación
está parado, nginx aborta y **caen TODOS los dominios**, no solo `tc-main`:

```
nginx: [emerg] host not found in upstream "nexora_hls" in /etc/nginx/snippets/stream-gate.conf:108
```

En cambio `tc-mia` usa una **IP literal** (`66.163.125.89`) y **no** crea esa
dependencia: nginx levanta aunque la torre de Miami esté apagada. Esa es toda la
diferencia entre las dos rutas.

**Consecuencia operativa: al reiniciar el servidor o el stack, levanta la
transcodificación ANTES que nginx.**

Desacoplarlo exigiría `resolver` + `proxy_pass` con variable, pero eso cambia el
tratamiento del URI (la variable **no** aplica el recorte de prefijo que hace la
`/` final) y obliga a un `rewrite` explícito. Es una ruta viva: merece su propia
ventana, no un arreglo de paso.

## 3. El prefijo `00-` es funcional

nginx incluye `conf.d/*.conf` **en orden alfabético** y resuelve `log_format` en
**tiempo de parseo**. Si un vhost hace `access_log ... stream_safe;` antes de que
nginx haya leído la directiva `log_format stream_safe`, el arranque aborta.

Reproducido renombrando `00-shared-http.conf` → `zz-shared-http.conf`:

```
nginx: [emerg] unknown log format "stream_safe" in /etc/nginx/snippets/stream-gate.conf:51
```

Con la definición dentro de `nexoraplay.conf`, `laredtelco.conf` (`l` < `n`) ya
rompía el arranque. **Si renombras ese archivo sin prefijo numérico, nginx deja
de arrancar en cuanto exista un vhost que ordene antes.**

Los `map` **sí** toleran referencia adelantada (se resuelven en tiempo de
petición); `log_format` **no**. Por eso conviven en el mismo archivo pero solo
uno impone el orden.

`00-default-server.conf` ordena **antes** que `00-shared-http.conf` (`d` < `s`),
así que **no puede usar `stream_safe`**. Si necesitas log con formato ahí, usa
`combined`.

## 4. No hay hook de renovación — PENDIENTE EN PRODUCCIÓN

**`/etc/letsencrypt/renewal-hooks/deploy/` está vacío** (verificado).

certbot renueva el certificado **en disco**, pero **nginx sirve desde memoria el
que cargó al arrancar**. Sin hook, nginx entrega el certificado viejo hasta que
alguien recarga a mano.

Ya está ocurriendo: nginx lleva 7 días levantado sirviendo un certificado que
caduca el **24/01/2027**, mientras `certbot certificates` reporta el
**17/10/2026**. Son certificados distintos.

> **Por qué esto empeora justo ahora:** con **un** dominio el fallo tardaba 90
> días y afectaba a todo a la vez, que al menos es evidente. Con **dos
> certificados de fechas distintas**, el fallo pasa a ser **intermitente y por
> dominio** — una marca bien, la otra rota. Es el peor modo de diagnosticar,
> porque el primer reflejo ("el servidor va bien, míralo") es cierto y despista.

Diagnóstico en una línea — **las fechas deben coincidir dominio a dominio**:

```bash
sudo certbot certificates | grep -E 'Certificate Name|Expiry Date'
for d in nexoraplay.net tvdigital.laredtelco.com; do
  printf '%s -> ' "$d"
  echo | openssl s_client -connect 45.184.225.4:443 -servername "$d" 2>/dev/null \
    | openssl x509 -noout -enddate
done
```

Arreglo (una sola vez, cubre también las marcas futuras) →
[RUNBOOK §8](../RUNBOOK_EDGE_MULTIDOMINIO.md#8-el-hook-de-renovación--pendiente-en-producción).

## 5. Producción monta un ARCHIVO, no el directorio

El compose que corre en el servidor monta:

```
./deploy/nginx/nexoraplay.conf  →  /etc/nginx/conf.d/default.conf
```

Un **archivo suelto**. Con eso, **ningún vhost nuevo se carga jamás**, por muchos
`.conf` que añadas a `conf.d/` en este repositorio — y **sin ningún mensaje de
error**, que es lo que lo hace caro.

`docker-compose.production.yml` en este árbol (líneas 17 y 21) ya monta los dos
directorios, pero **eso todavía no se ha aplicado al servidor**. Aplicarlo exige
**recrear el contenedor** (los montajes se fijan al crearlo), no basta un reload.

Mientras tanto, la estructura factorizada se despliega **generando un archivo
único que expande los `include`** y copiándolo a la ruta que el compose vivo
monta. El generador, probado, está en
[RUNBOOK §4A](../RUNBOOK_EDGE_MULTIDOMINIO.md#4a--archivo-único-generado--aplica-hoy).

> **Trampa derivada:** el compose antiguo monta `./deploy/nginx/nexoraplay.conf`,
> ruta que en este árbol **ya no existe** (se movió a `conf.d/`). Si alguien
> sincroniza el árbol nuevo y recrea el contenedor **sin actualizar el compose**,
> Docker crea un **directorio vacío** en esa ruta: `default.conf` deja de ser un
> archivo y nginx arranca sin ningún vhost. Comprueba con
> `ls -la /opt/nexora_api/deploy/nginx/nexoraplay.conf`.

## 6. MINA: docker compose sugiere destruir la transcodificación

Los contenedores de transcodificación (`nexora_hls`, `nexora_tc_*`) viven en
**otro** archivo compose (`docker-compose.transcode.production.yml`). Por eso
`docker compose -f docker-compose.production.yml` los ve como huérfanos y
**sugiere activamente borrarlos**:

```
Found orphan containers ([nexora_hls nexora_tc_gamatv ...]) for this project.
If you removed or renamed this service in your compose file, you can run this
command with the --remove-orphans flag to clean it up.
```

**Quien siga esa sugerencia de buena fe DESTRUYE el stack de transcodificación.**
El mensaje describe una situación ("borraste el servicio del compose") que aquí
es simplemente falsa.

```bash
# SIEMPRE:
docker compose -f docker-compose.production.yml up -d --no-deps nginx

# NUNCA en este servidor:
docker compose ... --remove-orphans
```

Y el daño no se queda en perder los canales transcodificados: sin `nexora_hls`,
nginx no puede resolver ese upstream y **tampoco arranca** ([§2](#2-nginx-no-arranca-si-la-transcodificación-está-parada)).
Una sugerencia aceptada sin pensar deja el edge entero abajo.

## 7. Por qué `staging/` está fuera de `conf.d/`

`nexoraplay.staging.conf` **redeclara `log_format stream_safe`** con un cuerpo
distinto. Al montar el directorio entero, tener ese archivo dentro de `conf.d/`
haría que nginx no arranque (`duplicate "stream_safe"`). Staging vive en
`deploy/nginx/staging/` y lo monta `docker-compose.staging.yml` por ruta
explícita. **No muevas nada de `staging/` a `conf.d/`.**

---

# Propiedades que no se deben romper

## El gate de `/stream/*` tiene UNA sola copia

`snippets/stream-gate.conf` es el control de autorización de playback y se
`include` desde todos los vhosts. **Para dar de alta un dominio no se copia: se
incluye.**

Si se duplica por dominio, la primera divergencia entre copias (un `error_page`
que falta, un `proxy_set_header` que cambia, una `location` nueva sin
`auth_request`) es un **agujero de autorización silencioso en una marca y no en
las otras** — y nadie lo nota, porque las demás siguen bien.

Dentro del archivo, tres invariantes que no se tocan sin revalidar:

- `set $stream_orig_uri` / `set $stream_token` van **antes** del `auth_request`.
  Los subrequests comparten el array de variables `set` del padre; en cambio
  `$request_uri` / `$arg_token` se resolverían contra el subrequest
  (`/__stream_auth`) y llegarían vacíos.
- `error_page 401` y `403` → `@stream_denied`.
- `access_log ... stream_safe` en las **tres** locations (las dos de `/stream/` y
  la de denegación). Si falta en una, el `?token=` acaba escrito en el log por
  defecto, que equivale a persistir credenciales.

Comprobación rápida (se espera `0`):

```bash
docker logs --tail 200 nexora_nginx | grep -c 'token=' || echo 0
```

## `proxy_set_header` y `add_header` NO se heredan

nginx **no fusiona** estas directivas entre niveles: cualquier `location{}` que
declare la suya **descarta todas** las del nivel superior.

- En `/stream/*` y `/__stream_auth` eso es **intencional**: al origen de vídeo
  solo se le manda lo imprescindible, y `Host` tiene que ser `$proxy_host`, no
  `$host`. **No lo "arregles".**
- Si añades un `add_header` en alguna location, **replica también** los de
  `snippets/security-headers.conf` o esa ruta se queda sin ellos.

## `X-Forwarded-For` = `$remote_addr`, nunca `$proxy_add_x_forwarded_for`

`$proxy_add_x_forwarded_for` **anexa** al header que mandó el cliente, y aguas
abajo la aplicación lee el **primer** elemento:

```
app/middleware/rate_limit.py  ->  _get_ip():  forwarded.split(",")[0]
app/api/client/playback.py    ->  _get_ip():  forwarded.split(",")[0]
```

Cualquiera podía mandar `X-Forwarded-For: 1.2.3.4` y hacerse pasar por esa IP,
volviendo **evadible el rate limiting del login** (una IP falsa distinta por
intento) y ensuciando la identidad de cliente que usa el gate de playback.

Es correcto **porque no hay CDN delante**: el registro A apunta directo al
servidor, así que `$remote_addr` ya es la IP real. Si algún día entra Cloudflare
u otro proxy → `set_real_ip_from` + `real_ip_header CF-Connecting-IP` +
`real_ip_recursive on` (instrucciones completas en `snippets/proxy-common.conf`).
Lo que **nunca** hay que hacer es volver a `$proxy_add_x_forwarded_for` confiando
en que el CDN limpia el header.

## Escalera de HSTS — un dominio nuevo arranca en `max-age=300`

HSTS es **irreversible del lado del cliente** durante todo el `max-age`. Si el
dominio nuevo tiene un problema de certificado y ya mandaste `max-age=31536000`,
cada navegador que lo visitó queda clavado en HTTPS durante un año y **no hay
nada que puedas hacer desde el servidor**. Ningún rollback lo deshace.

| Peldaño | Valor | Cuándo subir |
|---|---|---|
| 1 | `max-age=300` | En el alta. Radio de daño: 5 minutos. |
| 2 | `max-age=86400` | Tras ~48 h sirviendo HTTPS sin incidencias. |
| 3 | `max-age=31536000; includeSubDomains` | Solo tras ver la **primera renovación automática** completarse y recargar bien (~día 60). Antes no hay evidencia de que la renovación desatendida funcione en ese dominio. |

`includeSubDomains` solo en el peldaño 3: afecta a subdominios que puede que no
controlemos. `nexoraplay.net` está en el peldaño 3 por antigüedad;
`tvdigital.laredtelco.com`, en el 1. **Un dominio nuevo no copia el valor de
`nexoraplay.conf`.**

## El `default_server` es explícito a propósito

Sin `default_server` declarado, nginx nombra default al **primer `server{}` que
parsea**, es decir al que gane el orden alfabético de `conf.d/`. El
comportamiento del catch-all pasaría a depender de **cómo alguien decida llamar
al archivo de la marca siguiente**.

- El catch-all `:80` responde `444` (conexión cerrada, no revela qué se sirve
  aquí)… **excepto** en `/.well-known/acme-challenge/`.
- El catch-all `:443` usa `ssl_reject_handshake on` (nginx ≥ 1.19.4): un `Host`
  desconocido recibe `tlsv1 unrecognized name` en vez del certificado de la
  primera marca.

### ⚠ La excepción `/.well-known/` es una dependencia invisible del alta

El bloque `location ^~ /.well-known/acme-challenge/` de
`00-default-server.conf` es **quien responde el reto HTTP-01 de un dominio que
todavía no tiene vhost** — el caso exacto de cada alta, porque el vhost no puede
cargar sin certificado y el certificado necesita el reto.

Verificado: `Host: marca-futura.example` + `GET /.well-known/acme-challenge/<token>`
→ `200` con el contenido del token, sin ningún vhost para ese dominio.

**Si alguien pone un catch-all sin replicar esa excepción, el alta de cualquier
marca futura deja de funcionar** — y el síntoma será "certbot falla con 404",
meses después, cuando nadie recuerde que tocó el catch-all. **No borrar.**

---

## Documentación de la API (`/docs`, `/redoc`, `/openapi.json`)

**Decisión: no se replican en los vhosts de marca nueva.** `laredtelco.conf` nace
sin ellos. En `nexoraplay.conf` **siguen expuestos** como deuda conocida,
conservada a propósito para que el despliegue multi-marca fuera puramente
aditivo: retirarlos es el único cambio que rompería algo existente (una
herramienta o partner que consuma el esquema). Se retiran en un cambio propio,
tras comprobar en los access logs que nadie los usa.

Por qué hay que retirarlos, y por qué no basta con `auth_basic`:

- El esquema publica el mapa completo de la API, **incluido
  `/internal/stream-auth/validate`** y su contrato de parámetros. Es exactamente
  el mapa que necesita quien quiera atacar la autorización de playback
  endurecida en FASE 2C.
- `auth_basic` mueve el problema a "gestionar un htpasswd en el edge" (creación,
  rotación, quién lo tiene) y añade una credencial compartida más. Una allowlist
  de IP se rompe sola en cuanto alguien opera desde una IP doméstica dinámica, y
  su modo de fallo es "no puedo entrar", que se resuelve típicamente ampliando la
  allowlist hasta que deja de filtrar.
- El acceso legítimo **no necesita pasar por el edge público**: un túnel SSH da
  el mismo acceso sin abrir nada, y su autenticación ya existe y ya está
  auditada.
  ```bash
  ssh -L 8000:127.0.0.1:8000 <servidor>   # y luego http://127.0.0.1:8000/docs
  ```

**El arreglo real no es nginx** sino `docs_url=None, redoc_url=None,
openapi_url=None` cuando `APP_ENV == "production"` (`app/main.py`). Mientras eso
no se haga, la documentación sigue expuesta a cualquiera dentro de la red Docker;
nginx solo la esconde de Internet.

---

## Pendiente conocido: los streams de la marca nueva salen por el dominio antiguo

El gate de `/stream/` queda **inerte en `tvdigital.laredtelco.com`** mientras el
backend siga emitiendo `playback_url` apuntando a `nexoraplay.net`:
`_resolve_playback_url()` construye la URL desde `FLUSSONIC_PUBLIC_*_BASE_URL`, un
valor fijo, **sin mirar el `Host` de la petición**.

| Salida | Implicación |
|---|---|
| **A (recomendada)** — que `_resolve_playback_url()` use el host de la petición | Cada dominio se sirve a sí mismo. Cero CORS, cero cambios al añadir marcas. |
| **B** — dejar que los streams salgan por `nexoraplay.net` y abrir CORS en su `location /stream/` | Más rápido, pero abre cross-origin sobre la ruta endurecida en FASE 2C y obliga a revalidar el gate con preflights `OPTIONS`. |

Fuera del alcance de nginx: se arregla en `app/`.

---

## Notas varias

- `listen 443 ssl http2;` está **deprecado** desde nginx 1.25 (la imagen es
  1.27). Se usa `listen 443 ssl;` + `http2 on;`.
- La API se proxya **en el mismo origen** que el player a propósito: así las
  peticiones del player son same-origin y no dependen de la lista CORS del
  backend (`app/main.py`, `_WEB_ORIGINS`), que no incluye los dominios de
  producción. Cada marca nueva lo hereda sin tocar código.
- `apk add openssl` es necesario en los tests de laboratorio: la imagen
  `nginx:1.27-alpine` **no** trae openssl.

# Nginx — edge multi-marca

Un solo nginx sirve el mismo web player + API bajo varios dominios de marca.
Añadir una marca nueva debe costar **un archivo de ~20 líneas**, no una copia del edge entero.

```
deploy/nginx/
├── conf.d/                     → se monta en /etc/nginx/conf.d:ro   (nginx lo auto-incluye por glob)
│   ├── 00-shared-http.conf     ← maps + log_format stream_safe. EL PREFIJO 00- ES FUNCIONAL.
│   ├── 00-default-server.conf  ← catch-all :80 (+ reto ACME) y :443 (ssl_reject_handshake)
│   ├── laredtelco.conf         ← vhost fino: server_name + certs + HSTS
│   └── nexoraplay.conf         ← vhost fino: server_name + certs + HSTS
├── snippets/                   → se monta en /etc/nginx/snippets:ro (FUERA de conf.d a propósito:
│   │                             cualquier .conf dentro de conf.d se incluye solo, y un snippet
│   │                             suelto de locations en scope http{} no parsea)
│   ├── tls-common.conf
│   ├── security-headers.conf
│   ├── proxy-common.conf
│   ├── app-locations.conf
│   └── stream-gate.conf        ← ÚNICA copia del control de autorización de /stream/*
└── staging/                    → NO se monta en producción. Ver "Por qué staging está aparte".
    ├── nexoraplay.staging.conf
    └── nexoraplay.stream-auth.example.conf
```

---

## Tres trampas que ya nos han mordido — no las deshagas

### 1. El prefijo `00-` de `00-shared-http.conf` no es cosmético

nginx incluye `conf.d/*.conf` **en orden alfabético** y resuelve `log_format` en **tiempo de
parseo**. Si un vhost hace `access_log ... stream_safe;` antes de que nginx haya leído la
directiva `log_format stream_safe`, el arranque aborta:

```
nginx: [emerg] unknown log format "stream_safe" in /etc/nginx/conf.d/laredtelco.conf
```

Con la definición dentro de `nexoraplay.conf`, `laredtelco.conf` (`l` < `n`) rompía el arranque.
El prefijo `00-` garantiza que las definiciones compartidas se parsean primero.
**Si renombras ese archivo sin prefijo numérico, nginx deja de arrancar en cuanto exista un vhost
que ordene antes.** Los `map` sí toleran referencia adelantada; `log_format` no.

`00-default-server.conf` ordena **antes** que `00-shared-http.conf` (`d` < `s`), así que **no puede
usar `stream_safe`**. Si necesitas log con formato ahí, usa `combined`.

### 2. Se monta el DIRECTORIO, no un archivo

Antes se montaba `./deploy/nginx/nexoraplay.conf:/etc/nginx/conf.d/default.conf:ro` — un archivo
suelto. Con eso, **ningún vhost nuevo se carga jamás**, por muchos `.conf` que añadas al repo.
Ahora se montan los dos directorios (ver `docker-compose.production.yml`).

### 3. Por qué `staging/` está fuera de `conf.d/`

`nexoraplay.staging.conf` **redeclara `log_format stream_safe`** con un cuerpo distinto. Al montar
el directorio entero, tener ese archivo dentro de `conf.d/` haría que nginx no arranque
(`duplicate "stream_safe"`). Staging vive en `deploy/nginx/staging/` y lo monta
`docker-compose.staging.yml` por ruta explícita. **No muevas nada de `staging/` a `conf.d/`.**

---

## Alta de un dominio nuevo

Supongamos `tv.marcanueva.com`.

### Paso 0 — DNS

Registro `A` → `45.184.225.4`. Sin proxy naranja si es Cloudflare (rompe el reto HTTP-01 y además
cambiaría `$remote_addr`; ver el comentario sobre real_ip en `snippets/proxy-common.conf`).
Espera a que propague antes de pedir el certificado.

### Paso 1 — Certificado ANTES del vhost

Hay un bloqueo circular latente:

> el vhost no carga sin certificado (nginx no arranca si `ssl_certificate` apunta a una ruta
> inexistente) → el certificado necesita responder el reto HTTP-01 → el reto necesita que algo
> escuche en `:80` para ese Host.

**Quién rompe el ciclo:** el bloque `location ^~ /.well-known/acme-challenge/` de
`00-default-server.conf`. Al ser el `default_server` de `:80`, atiende Hosts que **todavía no
tienen vhost**. Esa location es una dependencia invisible del alta: no borrar.

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d tv.marcanueva.com
```

Verificado en laboratorio: `Host: marca-futura.example` + `GET /.well-known/acme-challenge/<token>`
→ `200` con el contenido del token, sin ningún vhost para ese dominio.

### Paso 2 — El vhost (~20 líneas útiles)

Copia `conf.d/laredtelco.conf` y cambia **solo**:

1. `server_name` (dos veces: bloque `:80` y bloque `:443`)
2. las tres rutas `ssl_*` a `/etc/letsencrypt/live/tv.marcanueva.com/`
3. el `add_header Strict-Transport-Security` → arranca en `max-age=300` (ver escalera abajo)

**No copies** `snippets/stream-gate.conf` ni las locations de la API: se incluyen. En particular el
gate de streams tiene que seguir teniendo **una sola copia** — es el control de autorización de
`/stream/*`, y dos copias que divergen es un agujero de autorización en una marca y no en las otras.

### Paso 3 — `nginx -t` OBLIGATORIO antes del reload

**Nunca** `nginx -s reload` a ciegas. Un `reload` con configuración inválida deja el proceso viejo
sirviendo, pero un `restart` (o cualquier reinicio del contenedor: `restart: unless-stopped`, un
reboot, un `docker compose up`) con configuración inválida **tumba todas las marcas a la vez**.

```bash
docker exec nexora_nginx nginx -t && docker exec nexora_nginx nginx -s reload
```

El `&&` no es decorativo: sin él, un typo se despliega igual.

Para validar **sin tocar producción** (lo que se hizo aquí), usa un contenedor desechable:

```bash
docker run --rm \
  -v "$PWD/deploy/nginx/conf.d:/etc/nginx/conf.d:ro" \
  -v "$PWD/deploy/nginx/snippets:/etc/nginx/snippets:ro" \
  --add-host nexora_api:127.0.0.1 --add-host nexora_web_player:127.0.0.1 \
  --entrypoint sh nginx:1.27-alpine -c '
    apk add --no-cache openssl >/dev/null 2>&1
    for d in nexoraplay.net tvdigital.laredtelco.com; do
      mkdir -p /etc/letsencrypt/live/$d
      openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=$d" \
        -keyout /etc/letsencrypt/live/$d/privkey.pem \
        -out /etc/letsencrypt/live/$d/fullchain.pem >/dev/null 2>&1
      cp /etc/letsencrypt/live/$d/fullchain.pem /etc/letsencrypt/live/$d/chain.pem
    done
    nginx -t'
```

(Certs autofirmados y upstreams resueltos por `--add-host`; añade el dominio nuevo a la lista.)

### Paso 4 — Escalera de HSTS

HSTS es **irreversible del lado del cliente** durante todo el `max-age`. Si el dominio nuevo
tiene un problema de certificado y ya mandaste `max-age=31536000`, cada navegador que lo visitó
queda clavado en HTTPS durante un año y **no hay nada que puedas hacer desde el servidor**.

| Peldaño | Valor | Cuándo subir |
|---|---|---|
| 1 | `max-age=300` | En el alta. Radio de daño: 5 minutos. |
| 2 | `max-age=86400` | Tras ~48 h sirviendo HTTPS sin incidencias. |
| 3 | `max-age=31536000; includeSubDomains` | Solo tras ver la **primera renovación automática** de certbot completarse y recargar bien (~día 60). Antes no hay evidencia de que la renovación desatendida funcione en ese dominio. |

`includeSubDomains` solo en el peldaño 3: afecta a subdominios que puede que no controlemos.

---

## Renovación automática: falta el `--deploy-hook` (PENDIENTE EN PRODUCCIÓN)

certbot renueva el certificado en disco, pero **nginx sigue sirviendo en memoria el certificado
viejo hasta que se le recarga**. Hoy no hay hook de recarga. Con un solo dominio el fallo tardaba
90 días en manifestarse; **con dos certificados con fechas de renovación distintas, la probabilidad
de servir un certificado caducado se duplica y el fallo es intermitente por dominio** (uno bien,
otro roto), que es mucho más difícil de diagnosticar.

Crear en el **host** (no en el contenedor):

```bash
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh >/dev/null <<'EOF'
#!/bin/sh
# certbot lo ejecuta SOLO cuando un certificado se ha renovado de verdad.
# nginx -t antes del reload: si la config esta rota, no se toca el nginx vivo.
docker exec nexora_nginx nginx -t && docker exec nexora_nginx nginx -s reload
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

Todo lo que hay en `renewal-hooks/deploy/` se aplica a **todos** los certificados, así que sirve
también para las marcas futuras sin tocar nada.

Ensayo sin renovar de verdad (no consume cuota de Let's Encrypt):

```bash
sudo certbot renew --dry-run
```

---

## Documentación de la API (`/docs`, `/redoc`, `/openapi.json`)

**Decisión: retiradas de la cara pública. No hay `location` para ellas en ningún vhost de marca.**
Caen en `location /` → web player.

Por qué retirar y no proteger con `auth_basic`/allowlist:

- `app/main.py` monta `docs_url="/docs"` / `redoc_url="/redoc"` **sin autenticación**. El esquema
  publica el mapa completo de la API, **incluido el endpoint del gate de streams**
  (`/internal/stream-auth/validate`) y su contrato de parámetros. Eso es exactamente el mapa que
  necesita quien quiera atacar la autorización de playback endurecida en FASE 2C.
- `auth_basic` mueve el problema a "gestionar un htpasswd en el edge" (creación, rotación,
  quién lo tiene) y añade una credencial compartida más. Una allowlist de IP se rompe sola en
  cuanto alguien opera desde una IP doméstica dinámica, y el modo de fallo es "no puedo entrar",
  que se resuelve típicamente ampliando la allowlist hasta que deja de filtrar.
- El acceso legítimo a la documentación **no necesita pasar por el edge público**: la API escucha
  en la red interna de Docker. Un túnel SSH da el mismo acceso sin abrir nada:
  ```bash
  ssh -L 8000:127.0.0.1:8000 <servidor>   # y luego http://127.0.0.1:8000/docs
  ```
  Autenticación: la del SSH, que ya existe y ya está auditada.

Verificado: `GET /docs`, `/redoc` y `/openapi.json` sobre el vhost devuelven la respuesta del web
player, no la de la API.

**Recomendación fuera de nginx** (no aplicada aquí, `app/` no es de este ámbito): poner
`docs_url=None, redoc_url=None, openapi_url=None` cuando `APP_ENV == "production"`. Mientras eso no
se haga, la documentación sigue expuesta a cualquiera que alcance el puerto 8000 por la red interna;
nginx solo la esconde de Internet.

---

## X-Forwarded-For

`snippets/proxy-common.conf` fija `X-Forwarded-For $remote_addr` (antes `$proxy_add_x_forwarded_for`).

`$proxy_add_x_forwarded_for` **anexa** al header que mandó el cliente, y aguas abajo
`app/middleware/rate_limit.py` y `app/api/client/playback.py` leen el **primer** elemento de la
lista → cualquiera podía mandar `X-Forwarded-For: 1.2.3.4` y hacerse pasar por esa IP, volviendo
**evadible el rate limiting del login** (una IP falsa distinta por intento).

Es correcto porque **no hay CDN delante**: el registro A apunta directo al servidor, así que
`$remote_addr` ya es la IP real. Si algún día se mete Cloudflare u otro proxy, ver el bloque de
instrucciones (`set_real_ip_from` + `real_ip_header CF-Connecting-IP` + `real_ip_recursive on`) en
`snippets/proxy-common.conf`. Lo que **nunca** hay que hacer es volver a `$proxy_add_x_forwarded_for`.

---

## Notas varias

- `listen 443 ssl http2;` está **deprecado** desde nginx 1.25 (la imagen es 1.27). Se usa
  `listen 443 ssl;` + `http2 on;`.
- El `default_server` es explícito. Sin él, nginx nombra default al primer `server{}` que parsea,
  es decir, al que gane el orden alfabético de `conf.d/` — el default pasaría a depender de cómo
  alguien decida llamar al archivo de la marca siguiente.
- El catch-all `:443` usa `ssl_reject_handshake on` (nginx ≥ 1.19.4): un Host desconocido recibe
  `tlsv1 unrecognized name` en vez del certificado de la primera marca.
- `access_log ... stream_safe` va en las **tres** locations del gate (las dos de `/stream/` y
  `@stream_denied`). Si falta en una, el `?token=` acaba escrito en el log por defecto.

# RUNBOOK — Alta de un dominio nuevo en el edge multidominio

> Procedimiento completo para servir una marca nueva desde el nginx de
> `45.184.225.4`, de DNS a verificación, ejecutable sin haber vivido la sesión en
> la que se escribió.
>
> Dominios servidos hoy: `nexoraplay.net`, `tvdigital.laredtelco.com`.

## Cómo leer las marcas de evidencia

Este runbook distingue tres niveles. No los mezcles al citarlo.

| Marca | Significado |
|---|---|
| **[VERIF-PROD]** | Comprobado contra el servidor de producción `45.184.225.4` en la sesión del 2026-07-26/27. |
| **[VERIF-LAB]** | Reproducido en un contenedor `nginx:1.27-alpine` desechable contra los archivos de este repositorio. El comando y su salida están en el anexo A. |
| **[RECOM]** | Recomendación razonada. **No ejecutada todavía.** |

---

## 0. Antes de nada: producción NO es este repositorio

**[VERIF-PROD] `/opt/nexora_api` no es un repositorio git.** No hay `git pull`. El
código llega por copia (`rsync`/`scp`, preservando mtimes).

Consecuencia que hay que interiorizar antes de tocar nada:

> **Cualquier diferencia entre lo que corre en el servidor y lo que está
> versionado aquí es invisible desde el repositorio.** `git status` limpio no
> significa "producción está al día". No existe ningún comando en tu máquina que
> te diga qué corre allí.

Esto no es teórico — ya hay una deriva viva, y es justo la que decide cómo se
carga la configuración:

| | Repositorio (este árbol) | Producción en ejecución |
|---|---|---|
| Montaje nginx | `./deploy/nginx/conf.d` → `/etc/nginx/conf.d` (directorio) | `./deploy/nginx/nexoraplay.conf` → `/etc/nginx/conf.d/default.conf` (**archivo suelto**) |
| Snippets | `./deploy/nginx/snippets` → `/etc/nginx/snippets` | no montado |
| Estructura efectiva | factorizada (`conf.d/` + `snippets/`) | un único archivo monolítico |

`docker-compose.production.yml` en el repositorio (líneas 17 y 21) ya monta los
directorios. **El servidor sigue corriendo el compose antiguo.** Por eso la
estructura factorizada **no se puede cargar hoy** sin recrear el contenedor con
el compose nuevo, y por eso este runbook tiene dos caminos en la sección 4.

**Primer paso de cualquier intervención — averigua qué monta el nginx vivo:**

```bash
docker inspect nexora_nginx --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

Si la salida contiene `/etc/nginx/conf.d/default.conf`, estás en el mundo
"archivo suelto" → **camino A**. Si contiene `/etc/nginx/conf.d` a secas →
**camino B**.

### Trampa derivada: la ruta del montaje antiguo ya no existe en el repo

El compose antiguo monta `./deploy/nginx/nexoraplay.conf`. En este árbol ese
archivo **ya no está ahí**: se movió a `deploy/nginx/conf.d/nexoraplay.conf`.

Si alguien sincroniza el árbol nuevo al servidor y recrea el contenedor **sin
actualizar también el compose**, Docker no encuentra el origen del bind y
—comportamiento estándar en hosts Linux— **crea un directorio vacío** en esa
ruta. `default.conf` pasa a ser un directorio y nginx arranca sin ninguna
configuración de vhost: los dos dominios dejan de responder.

*(Deducido del comportamiento de bind mounts en Linux, **no verificado en este
servidor**.)* Comprobación previa, barata:

```bash
ls -la /opt/nexora_api/deploy/nginx/nexoraplay.conf
# Si dice "directorio", el daño ya está hecho: bórralo y regenera el archivo (sección 4A).
```

---

## 1. Requisitos previos

Supongamos que damos de alta `tv.marcanueva.com`.

### 1.1 Registro DNS

Registro `A` → `45.184.225.4`.

**Sin proxy naranja si el DNS está en Cloudflare.** Dos razones independientes:

1. El proxy rompe el reto HTTP-01 tal y como está montado aquí (certbot
   `--webroot` sirve el token desde este servidor; con el proxy delante, Let's
   Encrypt habla con Cloudflare, no con nosotros).
2. `$remote_addr` pasaría a ser la IP del edge de Cloudflare, y **todos los
   clientes compartirían cubo de rate limit**. `snippets/proxy-common.conf` fija
   `X-Forwarded-For $remote_addr` precisamente porque no hay nada delante. Si
   algún día se mete un CDN, hay que añadir `set_real_ip_from` /
   `real_ip_header` / `real_ip_recursive` — está documentado en ese snippet.

### 1.2 Propagación

No pidas el certificado hasta que resuelva desde fuera. Let's Encrypt tiene
cuota (5 fallos por cuenta/hora), y un intento antes de tiempo la consume:

```bash
dig +short tv.marcanueva.com @1.1.1.1
dig +short tv.marcanueva.com @8.8.8.8
# Ambos deben devolver 45.184.225.4
```

### 1.3 Puerto 80 alcanzable

El reto HTTP-01 entra por `:80`. Si el firewall lo cierra, certbot falla con un
error de conexión que no dice "firewall".

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://tv.marcanueva.com/.well-known/acme-challenge/ping
# Se espera 404 (nginx responde y no encuentra el token). 000 o timeout = puerto cerrado.
```

Un `404` es buena señal: significa que nginx recibió la petición. Lo que no vale
es que no responda nadie.

---

## 2. Emisión del certificado — antes del vhost

### 2.1 El orden es obligatorio, no una preferencia

Hay un bloqueo circular:

> El vhost no carga sin certificado → **[VERIF-LAB]** nginx aborta al cargar la
> configuración si `ssl_certificate` apunta a una ruta inexistente
> (`cannot load certificate ... No such file or directory`) → el certificado
> necesita responder el reto HTTP-01 → el reto necesita que algo escuche en `:80`
> para ese `Host`.

**Primero el certificado. Después el vhost.** Al revés tumbas el edge entero:
nginx no arranca a medias, o carga toda la configuración o ninguna.

### 2.2 Quién responde el reto mientras el vhost no existe

Esta es la dependencia invisible del procedimiento.

Lo atiende el bloque `location ^~ /.well-known/acme-challenge/` del **servidor
`:80` por defecto** (`conf.d/00-default-server.conf`). Al ser `default_server`
de `:80`, recibe todos los `Host` que **todavía no tienen vhost** — que es
exactamente la situación de un dominio en proceso de alta.

**[VERIF-LAB]** Con `Host: marca-futura.example` (sin vhost alguno):

```
GET /.well-known/acme-challenge/testtoken  →  HTTP 200, cuerpo = contenido del token
```

> **Si alguien añade un catch-all `:80` sin replicar la excepción de
> `/.well-known/`, o cambia el `return 444` para que capture todo, el alta de
> cualquier marca futura deja de funcionar** — y el síntoma será "certbot falla
> con 404" en un momento en que nadie recordará que tocó el catch-all. El resto
> del catch-all sí devuelve `444` a propósito (**[VERIF-LAB]** conexión cerrada
> sin respuesta): no revela qué se sirve aquí.

### 2.3 Emitir

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d tv.marcanueva.com
```

`/var/www/certbot` está montado de solo lectura en el contenedor
(`docker-compose.production.yml`), así que certbot escribe en el host y nginx lo
lee. No hace falta entrar al contenedor.

Ensayo previo sin consumir cuota:

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d tv.marcanueva.com --dry-run
```

Comprobar que quedó emitido:

```bash
sudo certbot certificates | grep -A3 tv.marcanueva.com
sudo ls -l /etc/letsencrypt/live/tv.marcanueva.com/
# Deben existir fullchain.pem, privkey.pem y chain.pem
```

> **[VERIF-PROD] Que `certbot certificates` liste un certificado NO significa que
> nginx lo esté sirviendo.** Hoy mismo `tvdigital.laredtelco.com` está emitido y
> presente en `/etc/letsencrypt/`, pero nginx entrega el certificado de
> `nexoraplay.net` para ese dominio (comprobado por SNI). Todo cliente nuevo ve
> el aviso de seguridad del navegador. La sección 6.1 es la que zanja esto.

---

## 3. Escribir el vhost

Copia `deploy/nginx/conf.d/laredtelco.conf` a
`deploy/nginx/conf.d/marcanueva.conf` y cambia **solo tres cosas**:

1. `server_name` — **en los dos bloques** (`:80` y `:443`).
2. Las tres rutas `ssl_*` → `/etc/letsencrypt/live/tv.marcanueva.com/`.
3. El `add_header Strict-Transport-Security` → **arranca en `max-age=300`**.

**No copies** `snippets/stream-gate.conf` ni las locations de la API: se
`include`n. El gate de `/stream/*` tiene que seguir teniendo **una sola copia**.
Es el control de autorización de playback; dos copias que divergen con el tiempo
producen un agujero de autorización en una marca y no en las otras, y nadie lo
nota porque la otra marca sigue bien.

**No copies** las locations `/docs`, `/redoc` ni `/openapi.json` de
`nexoraplay.conf`. Existen ahí como deuda conocida y `laredtelco.conf` nace sin
ellas a propósito: el esquema publica el contrato de
`/internal/stream-auth/validate`, que es el mapa que necesita quien quiera atacar
el gate.

### Escalera de HSTS — por qué no se empieza en un año

HSTS es **irreversible del lado del cliente** durante todo el `max-age`. Si el
dominio nuevo tiene un problema de certificado y ya mandaste `max-age=31536000`,
cada navegador que lo visitó queda clavado en HTTPS durante un año y **no hay
nada que puedas hacer desde el servidor**. No es un valor que se pueda "revertir
desplegando".

| Peldaño | Valor | Cuándo subir |
|---|---|---|
| 1 | `max-age=300` | En el alta. Radio de daño: 5 minutos. |
| 2 | `max-age=86400` | Tras ~48 h sirviendo HTTPS sin incidencias. |
| 3 | `max-age=31536000; includeSubDomains` | Solo tras ver la **primera renovación automática** completarse **y recargar** (~día 60). Antes no hay evidencia de que la renovación desatendida funcione en ese dominio. |

`includeSubDomains` solo en el peldaño 3: afecta a subdominios que puede que no
controlemos.

### Antes de subir nada: valida en local

No hace falta tocar el servidor para saber si la configuración parsea. Anexo A.

---

## 4. Cargar la configuración

Dos caminos. **El A es el que aplica hoy.** El B es el destino.

### 4A · Archivo único generado — APLICA HOY

Mientras el compose de producción monte un archivo suelto, la única forma de
cargar la estructura factorizada es **generar un archivo único expandiendo los
`include`** y dejarlo en la ruta que ese compose monta.

Genera el archivo desde la raíz del repositorio (**[VERIF-LAB]** el resultado
pasa `nginx -t` con `snippets/` **sin montar**, que es la prueba de que la
expansión quedó completa):

```bash
cd /opt/nexora_api

# Orden deliberado: 00-shared-http.conf PRIMERO. log_format se resuelve en
# tiempo de parseo, así que su definición debe preceder a todo uso (sección 5.2).
for f in 00-shared-http.conf 00-default-server.conf nexoraplay.conf laredtelco.conf marcanueva.conf; do
  printf '\n# ===================== %s =====================\n' "$f"
  awk '
    /^[[:space:]]*include[[:space:]]+\/etc\/nginx\/snippets\// {
      match($0, /snippets\/[^;]+/); s = substr($0, RSTART, RLENGTH)
      print "    # ---- inline " s " ----"
      while ((getline line < ("deploy/nginx/" s)) > 0) print line
      close("deploy/nginx/" s); next
    }
    { print }
  ' "deploy/nginx/conf.d/$f"
done > /tmp/edge-generado.conf

# Comprobación de que no quedó ningún include sin expandir (debe imprimir 0):
grep -c 'include /etc/nginx/snippets' /tmp/edge-generado.conf
```

Añade el archivo de cada marca nueva a la lista del `for`. **Si olvidas añadirlo,
el dominio simplemente no se carga y no hay ningún error** — el fallo silencioso
más caro de este procedimiento.

Instala el archivo generado **en la ruta exacta que monta el compose vivo** (la
que devolvió `docker inspect` en la sección 0):

```bash
sudo cp /opt/nexora_api/deploy/nginx/nexoraplay.conf /root/edge-backup-$(date +%F-%H%M).conf   # rollback
sudo cp /tmp/edge-generado.conf /opt/nexora_api/deploy/nginx/nexoraplay.conf
```

Sí, el archivo se sigue llamando `nexoraplay.conf` aunque contenga todas las
marcas: **el nombre lo fija el compose que está corriendo**, no la lógica. Cambiarlo
exige recrear el contenedor, que es justo lo que el camino A evita.

Después → sección 5 (`nginx -t` y reload).

**Coste recurrente de este camino:** hay que regenerar y volver a copiar el
archivo en **cada** cambio de cualquier snippet o vhost. Es un paso manual que se
olvida. Por eso existe el camino B.

### 4B · Montar `conf.d/` + `snippets/` — el destino [RECOM]

El compose del repositorio ya está preparado (`docker-compose.production.yml`
líneas 17 y 21). Aplicarlo exige **recrear el contenedor**, no basta un reload:
los montajes se fijan al crear el contenedor.

```bash
cd /opt/nexora_api
# 1. Validar ANTES (anexo A) — al recrear no hay red de seguridad.
# 2. Recrear SOLO nginx:
docker compose -f docker-compose.production.yml up -d --no-deps nginx
```

> ### ⚠ MINA: no aceptes la sugerencia de `--remove-orphans`
>
> **[VERIF-PROD]** Los contenedores de transcodificación (`nexora_hls`,
> `nexora_tc_*`) viven en **otro** archivo compose
> (`docker-compose.transcode.production.yml`). Por eso
> `docker compose -f docker-compose.production.yml` los ve como huérfanos y
> **sugiere activamente borrarlos**:
>
> ```
> Found orphan containers ([nexora_hls nexora_tc_gamatv ...]) for this project.
> If you removed or renamed this service in your compose file, you can run this
> command with the --remove-orphans flag to clean it up.
> ```
>
> **Quien siga esa sugerencia de buena fe destruye el stack de
> transcodificación.** El mensaje describe una situación ("borraste el servicio
> del compose") que aquí es falsa. Usa **siempre** `--no-deps <servicio>` y
> **nunca** `--remove-orphans` en este servidor.
>
> Y no es solo perder los canales transcodificados: borrar `nexora_hls` deja a
> nginx sin poder resolver ese upstream, con lo que **nginx tampoco arranca** y
> caen todos los dominios (sección 5.3).

Una vez hecho el camino B, el A deja de aplicar y los cambios se despliegan
copiando archivos + reload, sin regenerar nada.

---

## 5. `nginx -t` obligatorio, y por qué reload > recrear

### 5.1 Nunca recargues a ciegas

```bash
docker exec nexora_nginx nginx -t && docker exec nexora_nginx nginx -s reload
```

**El `&&` no es decorativo.** Sin él, un error tipográfico se despliega igual y
el `reload` se ejecuta sobre una configuración que ya sabías que estaba rota.

### 5.2 La asimetría reload / restart — medida

Esta es la razón por la que todo este runbook prefiere `reload`:

| Acción con configuración inválida | Resultado **[VERIF-LAB]** |
|---|---|
| `nginx -s reload` | El reload se **rechaza**. El proceso viejo **sigue sirviendo** con la configuración anterior. Se comprobó que el certificado se seguía entregando por SNI después del fallo. |
| `docker restart` / `up -d` / reboot | El contenedor **muere**: `Exited (1)`. Con `restart: unless-stopped` entra en **crashloop**. |

Es decir: **un reload fallido no se nota; un restart fallido tira los dos
dominios a la vez** y sigue intentándolo en bucle. La diferencia no está en el
riesgo del cambio, sino en si nginx tiene un proceso viejo al que agarrarse.

Corolario operativo: **valida antes de cualquier cosa que recree el contenedor**,
porque un reinicio del servidor a las 4 de la mañana ejecuta ese camino sin que
nadie mire.

### 5.3 Las dos causas de "nginx no arranca" que ya nos han mordido

**a) Orden de carga y `log_format`.** nginx incluye `conf.d/*.conf` en **orden
alfabético** y resuelve `log_format` en **tiempo de parseo**. Un vhost que ordene
antes del archivo que define `stream_safe` aborta el arranque. **[VERIF-LAB]**
renombrando `00-shared-http.conf` a `zz-shared-http.conf`:

```
nginx: [emerg] unknown log format "stream_safe" in /etc/nginx/snippets/stream-gate.conf:51
```

Por eso el prefijo `00-`. **No renombres ese archivo sin prefijo numérico.** Los
`map` sí toleran referencia adelantada; `log_format` no. (En el camino A el
equivalente es el orden del `for`: `00-shared-http.conf` primero.)

**b) Upstream por nombre de contenedor.** nginx resuelve los upstream de
`proxy_pass` **al cargar la configuración**, no por petición. `nexora_hls` es un
nombre de contenedor, así que si el stack de transcodificación está parado,
nginx **no arranca** — y no cae solo `tc-main`, caen **todos los dominios**.
**[VERIF-LAB]**:

```
nginx: [emerg] host not found in upstream "nexora_hls" in /etc/nginx/snippets/stream-gate.conf:108
```

**[VERIF-PROD]** `tc-mia` usa una IP literal (`66.163.125.89`) y por eso **no**
crea esa dependencia: nginx levanta aunque la torre de Miami esté apagada. Es la
diferencia entre las dos rutas y explica por qué solo una es peligrosa.

Consecuencia para el orden de arranque: **si vas a reiniciar el servidor o el
stack, levanta la transcodificación antes que nginx**, o nginx entrará en
crashloop llevándose las marcas por delante.

---

## 6. Verificación posterior

No des el alta por buena hasta que las cuatro pasen.

### 6.1 SNI por dominio — el que atrapa el fallo pendiente

Es la comprobación central: confirma que **cada dominio recibe su propio
certificado**, no el de la primera marca.

```bash
for d in nexoraplay.net tvdigital.laredtelco.com tv.marcanueva.com; do
  echo "=== $d ==="
  echo | openssl s_client -connect 45.184.225.4:443 -servername "$d" 2>/dev/null \
    | openssl x509 -noout -subject -dates
done
```

**El `subject=CN=` debe coincidir con el dominio consultado en cada bloque.**
**[VERIF-LAB]** el formato de salida y la discriminación por SNI funcionan tal
cual; **[VERIF-PROD]** hoy `tvdigital.laredtelco.com` devuelve
`CN=nexoraplay.net`, que es exactamente el fallo pendiente.

Comprueba también que un `Host` desconocido **no** recibe el certificado de
ninguna marca (**[VERIF-LAB]**, gracias a `ssl_reject_handshake on`):

```bash
echo | openssl s_client -connect 45.184.225.4:443 -servername no-existe.example 2>&1 | grep -i unrecognized
# Se espera: tlsv1 unrecognized name  (alert 112)
```

### 6.2 El gate responde 401 sin token

El control de autorización de playback debe seguir cerrado en **todos** los
dominios, incluido el nuevo:

```bash
for d in nexoraplay.net tv.marcanueva.com; do
  for node in ec-main co-main tc-main tc-mia; do
    printf '%s %-8s -> ' "$d" "$node"
    curl -s -o /dev/null -w '%{http_code}\n' --resolve "$d:443:45.184.225.4" \
      "https://$d/stream/$node/canal/index.m3u8"
  done
done
```

**Se espera `401` en todas las filas.** Un `200` sin token es un agujero de
autorización; un `500` suele significar que el subrequest a la API falla (mira
`docker logs nexora_api`), no que el gate esté abierto.

El cuerpo debe ser el JSON de `@stream_denied`:

```bash
curl -s --resolve "tv.marcanueva.com:443:45.184.225.4" \
  "https://tv.marcanueva.com/stream/ec-main/canal/index.m3u8"
# {"success":false,"error":"playback token required or invalid"}
```

### 6.3 Los nodos de stream siguen sirviendo

El alta de un dominio no debe alterar las rutas existentes. Con un token válido,
los cuatro nodos deben seguir entregando manifiesto en el dominio **antiguo**:

```bash
TOKEN='<token de playback válido>'
for node in ec-main co-main tc-main tc-mia; do
  printf '%-8s -> ' "$node"
  curl -s -o /dev/null -w '%{http_code}\n' \
    "https://nexoraplay.net/stream/$node/canal/index.m3u8?token=$TOKEN"
done
# Se espera 200 en los cuatro.
```

Si `tc-main` falla y los demás no, mira el stack de transcodificación antes que
nginx (sección 5.3.b).

### 6.4 Salud de la aplicación y del contenedor

```bash
curl -s https://tv.marcanueva.com/api/health
docker ps --filter name=nexora_nginx --format '{{.Status}}'   # Up, no "Restarting"
docker logs --tail 50 nexora_nginx | grep -iE 'emerg|error' || echo 'sin errores'
```

Un `Restarting` recurrente es el crashloop de la sección 5.2 → ve directo al
rollback.

### 6.5 Que el token no acabe en el log

`access_log ... stream_safe` va en las **tres** locations del gate (las dos de
`/stream/` y `@stream_denied`). Si falta en una, el `?token=` se escribe en el
log por defecto, que equivale a persistir credenciales:

```bash
docker logs --tail 200 nexora_nginx | grep -c 'token=' || echo 0
# Se espera 0.
```

---

## 7. Rollback

Elige según el camino que usaste. **Ambos son de un solo paso** — esa es la razón
de guardar la copia antes de tocar.

### 7A · Si desplegaste por archivo único (camino 4A)

```bash
# 1. Restaurar la copia guardada en la sección 4A
sudo cp /root/edge-backup-<FECHA>.conf /opt/nexora_api/deploy/nginx/nexoraplay.conf

# 2. Validar y recargar
docker exec nexora_nginx nginx -t && docker exec nexora_nginx nginx -s reload
```

### 7B · Si el contenedor ya no arranca (crashloop)

Cuando nginx está en `Restarting`, `docker exec` no sirve: no hay proceso vivo al
que entrar. Hay que arreglar el archivo **desde el host** y luego recrear.

```bash
docker stop nexora_nginx
sudo cp /root/edge-backup-<FECHA>.conf /opt/nexora_api/deploy/nginx/nexoraplay.conf

# Validar el archivo restaurado SIN levantar el nginx de producción:
docker run --rm \
  -v /opt/nexora_api/deploy/nginx/nexoraplay.conf:/etc/nginx/conf.d/default.conf:ro \
  -v /etc/letsencrypt:/etc/letsencrypt:ro \
  --network nexora_net \
  nginx:1.27-alpine nginx -t

# Solo si el test pasa:
docker start nexora_nginx
```

`--network nexora_net` permite resolver `nexora_api`, `nexora_web_player` y
`nexora_hls` como en producción, que es donde falla el arranque (sección 5.3.b).

### 7C · Retirada rápida de un dominio sin tocar los demás

Si el problema es solo el dominio nuevo, no revientes el resto: quita **su**
vhost y recarga.

- **Camino A:** regenera el archivo único **omitiendo** `marcanueva.conf` de la
  lista del `for`, cópialo y recarga.
- **Camino B:** `sudo mv /opt/nexora_api/deploy/nginx/conf.d/marcanueva.conf /root/` y recarga.

En ambos casos, después: `docker exec nexora_nginx nginx -t && docker exec nexora_nginx nginx -s reload`.

> **Lo que el rollback NO deshace: HSTS.** Si el dominio llegó a servir un
> `max-age` largo, los navegadores que ya lo visitaron seguirán forzando HTTPS
> durante todo ese tiempo, hagas lo que hagas en el servidor. Es la razón entera
> de la escalera de la sección 3.

---

## 8. El hook de renovación — PENDIENTE EN PRODUCCIÓN

### 8.1 El fallo, medido

**[VERIF-PROD] `/etc/letsencrypt/renewal-hooks/deploy/` está vacío.**

certbot renueva el certificado **en disco**, pero **nginx sirve desde memoria el
que cargó al arrancar**. Sin un hook que recargue, nginx sigue entregando el
certificado viejo hasta que alguien lo recarga a mano.

Ya está pasando **[VERIF-PROD]**: nginx lleva 7 días levantado y sirve un
certificado que caduca el **24/01/2027**, mientras `certbot certificates` reporta
el **17/10/2026**. Son certificados distintos — uno en memoria, otro en disco.

Por qué esto empeora justo ahora, y no es una preocupación teórica:

> Con **un** dominio, el fallo tardaba 90 días en manifestarse y afectaba a todo
> a la vez, que al menos es evidente. Con **dos certificados de fechas de
> renovación distintas** (`nexoraplay.net` y `tvdigital.laredtelco.com`, emitido
> en esta sesión), el fallo pasa a ser **intermitente y por dominio**: una marca
> funciona y la otra da error de certificado. Ese es el peor modo posible de
> diagnosticar, porque el primer reflejo —"el servidor va bien, míralo"— es
> cierto y despista.

Y cada marca nueva añade una fecha de renovación más.

### 8.2 El arreglo [RECOM]

Crear en el **host** (no dentro del contenedor):

```bash
sudo mkdir -p /etc/letsencrypt/renewal-hooks/deploy

sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh >/dev/null <<'EOF'
#!/bin/sh
# certbot ejecuta este hook SOLO cuando un certificado se ha renovado de verdad.
#
# `nginx -t &&` antes del reload no es opcional: si la configuracion esta rota
# por un cambio anterior sin desplegar, este hook seria quien lo descubra a las
# 3 de la madrugada. Con el test delante, un reload malo simplemente no ocurre y
# nginx sigue sirviendo; sin el, el reload falla igual pero sin dejar rastro
# claro de por que.
docker exec nexora_nginx nginx -t && docker exec nexora_nginx nginx -s reload
EOF

sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

Todo lo que hay en `renewal-hooks/deploy/` se aplica a **todos** los
certificados, presentes y futuros. **Esto es lo que hace que las marcas nuevas no
requieran tocar nada más**: se instala una vez y cubre el alta N+1.

### 8.3 Verificar el hook

Ensayo que **no** consume cuota de Let's Encrypt:

```bash
sudo certbot renew --dry-run
```

> El `--dry-run` valida la renovación, pero **no ejecuta los deploy hooks** (solo
> se disparan en una renovación real). Para comprobar que el script en sí
> funciona, ejecútalo a mano:

```bash
sudo /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh && echo "hook OK"
```

Y como el fallo actual es "memoria ≠ disco", la verificación definitiva es
comparar ambos lados:

```bash
# Lo que hay en disco:
sudo certbot certificates | grep -E 'Certificate Name|Expiry Date'

# Lo que nginx sirve de verdad:
for d in nexoraplay.net tvdigital.laredtelco.com; do
  printf '%s -> ' "$d"
  echo | openssl s_client -connect 45.184.225.4:443 -servername "$d" 2>/dev/null \
    | openssl x509 -noout -enddate
done
```

**Las fechas deben coincidir dominio a dominio.** Si no coinciden, falta un
reload — y ese es precisamente el estado de producción hoy.

---

## Anexo A · Validar sin tocar producción

Todo lo marcado **[VERIF-LAB]** en este documento se comprobó así. Ejecútalo
desde la raíz del repositorio antes de subir cualquier cambio.

### A.1 Estructura factorizada (camino B)

```bash
docker run --rm \
  -v "$PWD/deploy/nginx/conf.d:/etc/nginx/conf.d:ro" \
  -v "$PWD/deploy/nginx/snippets:/etc/nginx/snippets:ro" \
  --add-host nexora_api:127.0.0.1 \
  --add-host nexora_web_player:127.0.0.1 \
  --add-host nexora_hls:127.0.0.1 \
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

Salida esperada: `syntax is ok` + `test is successful`.

**Añade el dominio nuevo a la lista del `for`**, o el test fallará con
`cannot load certificate` — que es, por cierto, la demostración de la
dependencia circular de la sección 2.1.

Los `--add-host` sustituyen a los contenedores reales: sin ellos el test falla
con `host not found in upstream`, que es el comportamiento de la sección 5.3.b.
`apk add openssl` es necesario: la imagen `nginx:1.27-alpine` no lo trae.

### A.2 Archivo único generado (camino A)

Genera el archivo como en la sección 4A y valídalo **sin montar `snippets/`** —
así se demuestra que la expansión quedó completa y no depende de nada externo:

```bash
docker run --rm \
  -v "/tmp/edge-generado.conf:/etc/nginx/conf.d/default.conf:ro" \
  --add-host nexora_api:127.0.0.1 \
  --add-host nexora_web_player:127.0.0.1 \
  --add-host nexora_hls:127.0.0.1 \
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

Si esto pasa, el archivo es autosuficiente y se puede copiar al servidor.

---

## Anexo B · Checklist del alta

```
[ ]  0. docker inspect nexora_nginx  ->  ¿camino A o B?
[ ]  1. Registro A -> 45.184.225.4, sin proxy naranja
[ ]  2. dig desde dos resolvers públicos coincide
[ ]  3. curl al /.well-known/ del dominio devuelve 404 (no timeout)
[ ]  4. certbot certonly --webroot  ->  certificado emitido
[ ]  5. ls /etc/letsencrypt/live/<dominio>/  ->  fullchain, privkey, chain
[ ]  6. vhost copiado; server_name (x2), rutas ssl_*, HSTS max-age=300
[ ]  7. Sin copiar: stream-gate, /docs, /redoc, /openapi.json
[ ]  8. Dominio añadido al `for` del generador (camino A) — FALLO SILENCIOSO
[ ]  9. Validación en contenedor desechable (anexo A) -> test is successful
[ ] 10. Copia de seguridad del archivo actual en /root/
[ ] 11. nginx -t && nginx -s reload   (con el &&)
[ ] 12. SNI: cada dominio devuelve SU CN
[ ] 13. Host desconocido -> tlsv1 unrecognized name
[ ] 14. Gate -> 401 sin token en los 4 nodos, en el dominio nuevo
[ ] 15. Nodos de stream con token -> 200 en el dominio antiguo
[ ] 16. docker ps -> Up, no Restarting
[ ] 17. logs sin 'token=' 
[ ] 18. Hook de renovación instalado (sección 8) — una sola vez
[ ] 19. Anotar fecha para subir el peldaño de HSTS (48 h / día 60)
```

---

## Referencias

- `deploy/nginx/README.md` — anatomía del edge y las trampas de la estructura.
- `deploy/nginx/conf.d/00-default-server.conf` — el catch-all que responde el reto ACME.
- `deploy/nginx/snippets/stream-gate.conf` — copia única del gate de `/stream/*`.
- `deploy/nginx/snippets/proxy-common.conf` — `X-Forwarded-For` y qué hacer si entra un CDN.
- `docker-compose.production.yml` — montajes del camino B.
- `docker-compose.transcode.production.yml` — los contenedores que `--remove-orphans` destruiría.

# Análisis — `tc-mia` devuelve 200 sin token

_Análisis estático sobre código y config versionada. Rama `feat/client-api-blockers`.
NO se tocó producción. Fecha: 2026-07-31._

> **CASO CERRADO — ver [§8](#8-cierre-medición-en-producción-2026-07-31).** La sonda A se
> ejecutó contra la config viva. El mecanismo de H1 (`location` ausente → SPA → 200 HTML)
> queda **confirmado por artefacto de producción**; el fallo ya estaba **reparado** antes de
> la medición. **No hubo fuga de vídeo**: impacto de disponibilidad, no de seguridad.
> Las §§ 3 y 4 se conservan tal cual se escribieron, sin retocar, como registro del
> razonamiento previo — pero **§8 corrige el «qué vhost» de H1**.

Hallazgo de origen: [`ROADMAP_SESION_MULTIMARCA_ADMIN.md`](ROADMAP_SESION_MULTIMARCA_ADMIN.md) §3.1.

---

## 0. Veredicto en tres líneas

1. **`tc-mia` NO ha perdido su `auth_request`**: las cinco copias versionadas del bloque lo
   llevan. La hipótesis "falta el gate en el bloque" queda **descartada para el repo**.
2. La explicación que mejor encaja es que **la petición no llegó a coincidir con ningún
   `location /stream/…`** en el vhost probado y cayó en el catch-all `location /` → web
   player → `try_files … /index.html` → **200 con `index.html`, no con vídeo**.
3. **La API sí tiene un hueco real y confirmado** —no valida `node` contra lista blanca, y el
   binding de nodo **falla abierto** cuando el token no trae el claim `node`— pero ese hueco
   **no puede producir un 200 sin token**, así que no es la causa de lo observado.

**¿Explotable hoy?** Bajo la hipótesis líder, **no**: lo que devuelve el 200 es HTML del SPA,
no segmentos de vídeo. El impacto real sería de **disponibilidad** (los tres canales de Miami
no se ven en ese dominio), no de fuga. Queda **una comprobación de un minuto** que lo cierra
del todo, en §6.

---

## 1. Observación (sin interpretar)

> `tc-mia` devolvió **200** a una petición de stream **sin token**; `ec-main`, `co-main` y
> `tc-main` devolvieron **401** en la misma verificación.

Lo que **no** consta en ningún artefacto del repo, y que importa:

- la **URL exacta** (¿qué dominio? ¿qué `stream_key`?),
- el **cuerpo** y el **`Content-Type`** de esa respuesta 200,
- el **momento** respecto al despliegue del fichero de 939 líneas.

Esa laguna es la que impide cerrar el caso al 100 % desde el repo.

---

## 2. Hipótesis evaluadas

| # | Hipótesis | Confianza | Fuerza de la evidencia | Estado |
|---|---|---|---|---|
| **H1** | El `location ^~ /stream/tc-mia/` **no existía en el vhost probado**; la petición cayó al catch-all `location /` → SPA → 200 `index.html` | **Alta** | Fuerte (artefacto + mecanismo verificado) | **Líder** |
| **H2** | El backend aprobó por **grant vivo en Redis** (`node+stream_key+IP`) sembrado por un manifiesto anterior con token | Media-baja | Moderada | Viva, pero exige dos coincidencias |
| **H3** | El backend **aprueba un `node` desconocido** por falta de lista blanca (hipótesis (b) del encargo) | **Descartada como causa** | Fuerte en contra | Es un hueco real, pero de otro tipo |
| **H4** | Al bloque de `tc-mia` le falta el `auth_request` (hipótesis (a) del encargo) | **Descartada en el repo** | Fuerte en contra | Puede seguir viva solo si el conf vivo diverge |

---

## 3. Evidencia

### H4 — "al bloque le falta el `auth_request`" → **descartada**

**En contra (concluyente, en el código versionado).** Las cinco copias del bloque llevan el
gate completo (`set` de las variables antes del subrequest, `auth_request`, y los dos
`error_page` hacia `@stream_denied`):

| Fichero:línea | Copia |
|---|---|
| `deploy/nginx/nexoraplay.conf:479` | vhost `nexoraplay.net` (fichero vivo de 939 líneas) |
| `deploy/nginx/nexoraplay.conf:902` | vhost `tvdigital.laredtelco.com` |
| `deploy/nginx/snippets/stream-gate.conf:125` | gate compartido (copia única) |
| `deploy/transcode/nginx-location-tc-mia.conf:10` | copia versionada del frente de transcodificación |
| `deploy/transcode/patch_nginx_tc_mia.py:17` | bloque que el script inserta en el conf vivo |

Los cuatro nodos son **idénticos** en la parte de autorización
(`deploy/nginx/nexoraplay.conf:398-497`); `tc-mia` solo se diferencia en el upstream (IP
literal `66.163.125.89:8088`) y en los timeouts.

**En contra (medidas históricas, tier 1).** Dos commits registran el gate **cerrado** en
`tc-mia`:

- `676d68e` (26 jul 22:55, alta de la torre): _"Verificado: sin token 401, con token 200"_.
- `c1f76e1` (27 jul 02:11): _"Comprobado que ahora mismo el gate esta vivo para los dos nodos
  (401 en tc-main/GAMATV y en **tc-mia/ESTRELLAS_CA**)"_ — contra `https://nexoraplay.net/…`.

**Matiz que mantiene un resquicio.** El conf vivo **no es el versionado**
(`CONTINUAR_AQUI.md`: _"Producción NO es git. El código llega por copia"_). H4 solo puede
revivir por divergencia del fichero vivo, no por el repo.

---

### H1 — la petición nunca tocó el gate → **hipótesis líder**

**A favor 1 — el mecanismo produce exactamente un 200, y está verificado.**
Si `/stream/tc-mia/…` no coincide con ningún `location /stream/…`, cae en el catch-all:

```nginx
# deploy/nginx/nexoraplay.conf:349
location / {
    proxy_pass http://nexora_web_player:80;
}
```

y el contenedor del web player es un SPA:

```nginx
# web_player/nginx.conf:15
try_files $uri $uri/ /index.html;
```

→ **200 con `index.html`** para cualquier ruta inexistente. No 404, no 401. **Es la firma
exacta de lo observado.**

**A favor 2 — el equipo predijo mal esta firma, y por eso se leyó como bypass.**
`c1f76e1` afirma que, al desplegar el refactor sin `tc-mia`, _"los tres canales de la torre de
Miami habrían devuelto **404**"_. Esa predicción es **incorrecta**: con el `try_files` del SPA
habrían devuelto **200**. Es decir, el síntoma que se catalogó como "bypass del gate" es
precisamente el síntoma de "el `location` no está". Un 200 se interpretó como agujero porque
se esperaba un 404.

**A favor 3 — el bloque de `tc-mia` se inserta UNA sola vez, y hay dos vhosts.**
`deploy/transcode/patch_nginx_tc_mia.py:47-51` ancla con `re.search`, que devuelve la
**primera** coincidencia:

```python
anchor = re.search(r"\n(    location / \{\n        proxy_pass http://nexora_web_player)", conf)
out = conf[: anchor.start(1)] + BLOCK + conf[anchor.start(1):]
```

y antes, en `:43-45`, hay un guardián de idempotencia que **impide corregirlo**:

```python
if MARK in conf:
    print("ya estaba: no se toca nada")
    sys.exit(0)
```

Con dos `server{}` en el fichero, el bloque entra **solo en el primero**; volver a ejecutar el
script tras dar de alta la segunda marca **no** lo añade al segundo, porque `MARK` ya aparece.
`patch_nginx_tc.py:42,47` (nodo `tc-main`) tiene el mismo patrón.

**A favor 4 — el histórico muestra que `tc-mia` nunca estuvo en el monolito versionado hasta
después del alta de la segunda marca.** Recuento de `location ^~ /stream/tc-mia/` en
`deploy/nginx/nexoraplay.conf` por commit:

| Commit | Líneas | `tc-mia` | `auth_request` |
|---|---|---|---|
| `762f1ba` (Fase 2C, PR #9) | 157 | **0** | 2 |
| `4f99925` (refactor a conf.d) | — | — | fichero borrado |
| `c1f76e1` | — | — | fichero borrado |
| `16ab8f2` (regenerado) | 939 | **2** | 15 |

Y el snippet compartido **tampoco** tenía `tc-mia` hasta `c1f76e1` — que es literalmente su
mensaje de commit: _"al snippet factorizado le faltaba tc-mia"_. Como el vhost de
`tvdigital.laredtelco.com` se construye incluyendo ese snippet
(`deploy/nginx/conf.d/laredtelco.conf:63`), **cualquier material derivado del snippet anterior
a `c1f76e1` da un vhost con `ec-main`, `co-main` y `tc-main`, pero sin `tc-mia`** — justo el
diferencial observado.

**A favor 5 — el propio repo documenta este modo de fallo, palabra por palabra.**
`deploy/nginx/nexoraplay.conf:355-364`:

> ⚠ PROPIEDAD DE SEGURIDAD: […] Si se duplica por dominio, la primera divergencia entre copias
> (un `error_page` que falta, un `proxy_set_header` que cambia, **una location nueva sin
> auth_request**) es un agujero de autorización silencioso **en una marca y no en las otras**.

**A favor 6 — reconcilia las dos medidas contradictorias sin descartar ninguna.**
`c1f76e1` midió **401** contra `nexoraplay.net` (vhost #1, que sí tiene el bloque). §3.1 midió
**200** mientras verificaba **la marca nueva** (vhost #2, que no lo tendría). Ambas medidas son
correctas; solo difieren en el vhost.

**En contra / hueco.** §3.1 afirma haber desplegado el fichero de 939 líneas —que **sí** lleva
`tc-mia` en los dos `server{}`— *antes* de observar el 200, y sostiene que _"ya estaba en la
config viva con el mismo bloque"_. Si el 200 se midió estrictamente **después** de ese
despliegue y recarga, H1 exige además que la recarga no llegara a ese vhost. Esa afirmación es
de **prosa, no de artefacto**: no hay volcado de `nginx -T` que la respalde, y es compatible
con haber mirado solo el vhost #1.

---

### H2 — grant vivo en Redis → **posible, pero exige dos coincidencias**

Es la **única** vía por la que el backend devuelve 2xx sin token
(`app/api/internal/stream_auth.py:115-119`):

```python
# Tokenless (segment): must be covered by a grant from a prior manifest.
if not (node and stream_key):
    raise NexoraException(401, "Missing playback token")
if not await svc.check_stream_grant(node, stream_key, hash_ip(client_ip)):
    raise NexoraException(401, "No stream authorization for this segment")
return {"ok": True}
```

**A favor.** `tc-mia` fue el nodo que el frente de transcodificación estuvo ejercitando **con
token** esos días (`676d68e`: _"con token 200"_), que es exactamente lo que siembra un grant
(`app/services/stream_auth_service.py:112`). Y los grants son **de vida ilimitada por
defecto**: `app/config.py:93` fija `stream_grant_max_lifetime_seconds = 0`, y
`stream_auth_service.py:499` **renueva el TTL en cada consulta**, así que un grant sobrevive
indefinidamente mientras se le siga pegando cada <180 s.

**En contra (fuerte).** El comando de verificación del runbook
(`deploy/RUNBOOK_EDGE_MULTIDOMINIO.md:435`) usa un `stream_key` **inexistente**:

```
"https://$d/stream/$node/canal/index.m3u8"
```

Con `stream_key=canal`, la cadena bajo H2 sería: grant inexistente → 401. Y **aunque hubiera
grant**, el upstream es el nginx de la torre sirviendo `/var/hls` con `autoindex off`
(`deploy/transcode/mia-tower-setup.sh:31-51`): `/canal/index.m3u8` no existe → **404**.
**Bajo H2, el código observable es 401 o 404 — nunca 200.**

**En contra (2).** El grant está cifrado por IP del cliente
(`app/redis_client.py:86`), así que exigiría además que el verificador hubiera reproducido
ese canal exacto desde esa misma IP en los minutos previos.

---

### H3 — "el backend aprueba un `node` que no conoce" → **descartada como causa, real como hueco**

**En contra como causa.** No existe rama permisiva para un `node` desconocido: la ruta sin
token pasa igualmente por `check_stream_grant`, que devuelve `False` si no hay clave. Un nodo
inventado da **401**, no 200. Comprobado: no hay lista blanca de nodos en `app/` (`grep` de
`ALLOWED_NODES|allowed_nodes|VALID_NODES|node_whitelist` → 0 resultados), pero su ausencia no
abre la puerta por sí sola.

**A favor como hueco independiente (esto sí es real).**
`app/services/stream_auth_service.py:427-431`:

```python
if stream_key is not None and payload.get("sk") != stream_key:
    raise NexoraException(403, "Playback token not valid for this stream")

if node is not None and payload.get("node") not in (None, node):
    raise NexoraException(403, "Playback token not valid for this node")
```

El binding de `stream_key` **falla cerrado** (si el claim es `None`, `None != "X"` → 403). El
de `node` **falla abierto**: `payload.get("node") in (None, node)` deja pasar **cualquier
nodo** cuando el token no trae el claim. Los tokens heredados/STB emitidos por
`create_token()` sin `node` (`stream_auth_service.py:502-561`) son, por tanto, válidos en
`tc-mia`. Y como `stream_auth.py:109` hace `g_node = out.get("node") or node`, ese token
siembra además un grant **a nombre de `tc-mia`**.

---

## 4. Ronda de refutación

**Mejor ataque a H1:** §3.1 dice que el fichero de 939 líneas —con `tc-mia` en los dos
`server{}`— ya estaba desplegado y validado con `nginx -t` cuando se observó el 200. Si eso es
literal, el `location` existía y H1 cae.

**Por qué H1 sigue en pie:** ese es un enunciado en prosa, sin volcado de la config efectiva, y
el propio párrafo mezcla dos afirmaciones distintas ("el bloque ya estaba en la config viva" se
comprobó sobre **un** vhost; "el 200 es de tc-mia" se midió sobre **otro**). Frente a eso, H1
se apoya en un mecanismo verificado en fichero (`web_player/nginx.conf:15` produce 200 para
rutas inexistentes) que **es la única vía identificada capaz de devolver 200 con un
`stream_key` que no existe en la torre**. H2, la alternativa, predice 401 o 404 con el comando
del runbook. H1 se mantiene como líder, pero **provisional**: se confirma o cae con la sonda de
§6, que cuesta un minuto.

---

## 5. Fix propuesto

### 5.1 nginx — impedir que `/stream/*` caiga nunca en el SPA _(la corrección que cierra la clase entera de fallo)_

Hoy, cualquier `/stream/<nodo-no-declarado>/…` —un nodo nuevo, una errata, o un nodo que falte
en **un** vhost— devuelve **200 con `index.html`** en vez de denegar. Añadir un cinturón
de seguridad en la copia única del gate, `deploy/nginx/snippets/stream-gate.conf`:

```nginx
# Cinturón: un /stream/<nodo> no declarado NO puede caer en el catch-all del SPA,
# que responde 200 con index.html (web_player/nginx.conf: try_files … /index.html)
# y disfraza un location ausente de "gate abierto". Prefijo más corto que los de
# cada nodo, así que solo actúa cuando ninguno coincide.
location ^~ /stream/ {
    access_log /dev/stdout stream_safe;
    return 401 '{"success":false,"error":"playback token required or invalid"}';
}
```

Se devuelve el mismo 401 que `@stream_denied` en vez de 404 a propósito: un 404 permitiría
enumerar qué nodos existen. Riesgo del cambio: nulo para las rutas vivas — `^~ /stream/` es un
prefijo **más corto** que `^~ /stream/tc-mia/`, y nginx elige siempre el prefijo más largo.

### 5.2 nginx — retirar los scripts de parcheo del conf vivo

`deploy/transcode/patch_nginx_tc.py` y `deploy/transcode/patch_nginx_tc_mia.py` son la causa
estructural: insertan **una** copia (`re.search` → primera coincidencia) y su guardián
`if MARK in conf: sys.exit(0)` **impide** añadirla a un segundo vhost. Sustituirlos por el
`include` del gate compartido, que es justo lo que ya hacen
`deploy/nginx/conf.d/nexoraplay.conf:50` y `deploy/nginx/conf.d/laredtelco.conf:63`. Marcar
ambos scripts como obsoletos para que nadie los vuelva a correr.

### 5.3 backend — que el binding de `node` falle cerrado

`app/services/stream_auth_service.py:430`:

```python
-        if node is not None and payload.get("node") not in (None, node):
+        # Simétrico con el binding de stream_key (línea 427): un token sin claim
+        # 'node' NO debe ser válido en cualquier nodo. Tras confirmar que los
+        # tokens STB/heredados ya lo llevan, quitar el flag.
+        if node is not None and payload.get("node") != node:
             raise NexoraException(403, "Playback token not valid for this node")
```

Va detrás de un flag (`playback_node_binding_enforce`, por defecto `False`) hasta comprobar en
logs que ningún emisor vivo manda tokens sin `node`. Sin esa comprobación, activarlo **corta
playback real**.

### 5.4 backend — validar `node` contra lista blanca

No existe ninguna. Rechazar con 401 cualquier `node` que no esté entre los configurados
(`ec-main`, `co-main`, `tc-main`, `tc-mia`, `ec-quito`) antes de tocar Redis, en
`app/api/internal/stream_auth.py`, tras `_extract()`. Cierra de paso la enumeración de nodos y
evita sembrar grants con nombres arbitrarios.

### 5.5 backend — acotar la vida de los grants

`app/config.py:93`: `stream_grant_max_lifetime_seconds = 0` (ilimitado) +
`stream_auth_service.py:499` (renueva TTL en cada consulta) = **un grant vive para siempre
mientras alguien lo consulte cada <180 s**, incluido quien nunca volvió a presentar un token
válido. Ponerlo en el TTL de sesión (14400 s) acota la latencia de revocación. Ya está previsto
en `docs/TODO_NEXT.md`.

### 5.6 backend — no correlacionar cuando la IP es desconocida

`app/api/internal/stream_auth.py:118` llama a `hash_ip(client_ip)` con `client_ip` posiblemente
`None`, y `app/core/security.py:35` devuelve entonces un hash **estable del vacío** — un
único cubo de grants compartido por todos los peticionarios sin IP. El propio docstring avisa:
_"callers should refuse correlation when the IP is unknown"_. El llamador no lo hace. Devolver
401 si `client_ip` es `None`.

### 5.7 nginx — escapar las variables del subrequest

`deploy/nginx/nexoraplay.conf:390` (y `snippets/stream-gate.conf:36`):

```nginx
proxy_pass http://nexora_api:8000/internal/stream-auth/validate?node=$stream_node_v&stream_key=$stream_key_v;
```

`$stream_node_v` se captura como `[^/]+` del URI y se interpola **sin escapar**, así que un
segmento con `&` o `=` inyecta parámetros arbitrarios en el endpoint interno
(`/stream/x&foo=bar/canal/…`). No es un bypass directo —seguiría haciendo falta el valor de un
token válido—, pero es una primitiva de contrabando de parámetros que conviene cerrar pasando
los valores por **cabecera** (`X-Stream-Node` / `X-Stream-Key`, que `_extract()` ya soporta en
`stream_auth.py:56-57`) en vez de por query string.

---

## 6. Cómo verificarlo en producción sin riesgo

Todo lo de abajo es **solo lectura**: no reinicia nada, no escribe nada, no recarga nginx.

### Sonda A — la que cierra el caso _(ejecutar primero)_

Vuelca la configuración **efectiva** del nginx vivo y lista, por vhost, qué locations de
`/stream/` existen realmente:

```bash
sudo docker exec nexora_nginx nginx -T 2>/dev/null \
  | grep -nE 'server_name|location \^~ /stream/'
```

**Lectura del resultado:**

- Si algún `server_name` **no** va seguido de los **cuatro** `location ^~ /stream/…` →
  **H1 confirmada**: es un `location` ausente, no un gate abierto. Fix = §5.1 + §5.2.
- Si los cuatro nodos aparecen bajo **todos** los `server_name` → H1 cae; pasar a la sonda C.

### Sonda B — el cuerpo del 200, que es lo que nunca se miró

El código HTTP por sí solo no distingue "SPA" de "manifiesto". El `Content-Type` sí:

```bash
curl -sS -o /tmp/tcmia.body -D /tmp/tcmia.hdr -w 'HTTP %{http_code}\n' \
  --resolve 'tvdigital.laredtelco.com:443:45.184.225.4' \
  'https://tvdigital.laredtelco.com/stream/tc-mia/ESTRELLAS_CA/index.m3u8'
grep -i '^content-type' /tmp/tcmia.hdr
head -c 120 /tmp/tcmia.body; echo
```

| Resultado | Significado | Gravedad |
|---|---|---|
| `200` + `text/html` + `<!doctype html>` | **H1**: el `location` falta en ese vhost. **No se fuga vídeo.** Los canales de Miami sencillamente **no se ven** en ese dominio | Disponibilidad, no seguridad |
| `200` + `application/vnd.apple.mpegurl` + `#EXTM3U` | **H2**: el gate corrió y **aprobó**. Fuga real de manifiesto | **Alta** — actuar ya |
| `401` + `{"success":false,…}` | Gate cerrado; el hallazgo ya está resuelto por el despliegue de §3.1 | Ninguna |

Repetir cambiando el dominio por `nexoraplay.net` y el nodo por `ec-main`: la comparación
entre dominios es la que aísla el vhost culpable.

### Sonda C — solo si la sonda B devuelve un `.m3u8`

Comprueba si hay grants vivos de `tc-mia` en Redis (lectura pura):

```bash
sudo docker exec nexora_redis redis-cli --scan --pattern 'nexora:stream_grant:tc-mia:*'
```

Si aparecen claves, H2 queda confirmada y el fix urgente es §5.5 (acotar la vida del grant),
no nginx.

### Matriz completa (la del runbook, §6.2)

```bash
for d in nexoraplay.net tvdigital.laredtelco.com; do
  for node in ec-main co-main tc-main tc-mia; do
    printf '%s %-8s -> ' "$d" "$node"
    curl -s -o /dev/null -w '%{http_code}\n' --resolve "$d:443:45.184.225.4" \
      "https://$d/stream/$node/canal/index.m3u8"
  done
done
```

Se espera **401 en las ocho filas**. Un 200 aquí, con `stream_key=canal` (que no existe en la
torre), **solo puede venir del SPA** — porque si el gate aprobara, el nginx de Miami
respondería 404 al no encontrar el fichero.

---

## 7. Incógnita crítica y estado

**Incógnita crítica:** el **`Content-Type` del 200** (y el vhost contra el que se midió). Es el
único dato que separa "location ausente" (sin impacto de seguridad) de "gate aprobando" (fuga
real), y no quedó registrado en ningún artefacto.

**Estado:** caso **no cerrado**, pero acotado a dos explicaciones con una sonda de un minuto
que las discrimina (§6, sondas A y B).

Independientemente de cuál resulte, **§5.1, §5.3, §5.4, §5.5 y §5.6 valen igual**: son huecos
confirmados por lectura de código, no dependen del veredicto.

---

## 8. Cierre: medición en producción (2026-07-31)

Ejecutada la **sonda A** de §6 con autorización puntual del dueño, **solo lectura**, sobre
`45.184.225.4`. No se escribió nada, no se recargó nginx, no se ejecutó la sonda B (el `curl`
de playback quedó expresamente fuera de la autorización).

### 8.1 Qué se midió

**Comando base** (§6, sonda A) y variantes del mismo `grep` para poder **atribuir cada
`location` a su vhost**:

```bash
sudo docker exec nexora_nginx nginx -T 2>/dev/null \
  | grep -nE 'configuration file|server_name|include |location \^~ /stream/|auth_request '
```

Salida (censurada; recortada a lo pertinente):

```
  1:# configuration file /etc/nginx/nginx.conf:
 32:    include /etc/nginx/conf.d/*.conf;
136:# configuration file /etc/nginx/conf.d/00-default-server.conf:
158:    server_name _;
186:    server_name _;
242:# configuration file /etc/nginx/conf.d/laredtelco.conf:
256:    server_name tvdigital.laredtelco.com;
273:    server_name tvdigital.laredtelco.com;
305:    include /etc/nginx/snippets/stream-gate.conf;      <-- vhost TLS marca nueva
429:# configuration file /etc/nginx/snippets/stream-gate.conf:
473:location ^~ /stream/ec-main/ {
477:    auth_request /__stream_auth;
496:location ^~ /stream/co-main/ {
499:    auth_request /__stream_auth;
529:location ^~ /stream/tc-main/ {
532:    auth_request /__stream_auth;
559:location ^~ /stream/tc-mia/ {
562:    auth_request /__stream_auth;
586:# configuration file /etc/nginx/conf.d/nexoraplay.conf:
599:    server_name nexoraplay.net www.nexoraplay.net;
618:    server_name nexoraplay.net www.nexoraplay.net;
636:    include /etc/nginx/snippets/stream-gate.conf;      <-- vhost TLS marca vieja
```

> ⚠ **Trampa de lectura, y es la que decide.** El recuento global engaña: los cuatro
> `location` aparecen **una sola vez** en el volcado porque `nginx -T` imprime **cada fichero
> una vez**, no una vez por `include`. No significa «solo un vhost los tiene». Hay que seguir
> los `include`: **ambos** vhosts TLS (líneas 305 y 636) incluyen **el mismo**
> `snippets/stream-gate.conf`, así que **los dos heredan los cuatro nodos con su
> `auth_request`**. Leer el recuento sin resolver los `include` habría dado el veredicto
> contrario.

### 8.2 ¿Diverge producción del repo?

**No, en lo que se midió.** Comparados los directivos (sin comentarios ni blancos), contra el
**commit `750992c` (HEAD de `feat/client-api-blockers`)** — no contra el working tree, ver el
aviso de abajo:

| Fichero | Repo @`750992c` | Vivo | Resultado |
|---|---|---|---|
| `snippets/stream-gate.conf` | 84 directivas | 84 directivas | **idénticos** |
| `conf.d/nexoraplay.conf` | 37 directivas | 37 directivas | **idénticos** |
| `conf.d/laredtelco.conf` | 28 directivas | 28 directivas | **idénticos** |

> ⚠ **La referencia es HEAD, y hay que decirlo.** Durante esta sesión el *working tree* recibió
> un cambio **sin commitear** de otro frente (P1.6, caché de segmentos): mete
> `include snippets/stream-cache.conf` en las cuatro locations de nodo y quita el
> `proxy_buffering off`. **Eso todavía NO está desplegado** — el snippet vivo tiene
> 4 × `proxy_buffering off` y 0 referencias a `stream-cache`. Comparar contra el working tree
> daría una divergencia **falsa**, atribuible a trabajo en vuelo y no a deriva de producción.
> Cuando P1.6 se despliegue, esta tabla habrá que rehacerla.

**Pero sí hay una divergencia estructural que importa:** lo desplegado es el layout
`conf.d/` + `snippets/`. El monolito versionado **`deploy/nginx/nexoraplay.conf` (939 líneas)
NO está desplegado** — el `conf.d/nexoraplay.conf` vivo tiene 78. Ese monolito, que duplica el
gate *inline* en cada `server{}`, es **material muerto en el repo** y es la fuente de la que
§3 dedujo el modelo equivocado (ver §8.4). Conviene retirarlo o marcarlo como obsoleto.

### 8.3 Veredicto: **refutada por la letra del criterio, confirmada en el mecanismo**

El criterio fijado de antemano era binario:

- *si algún `server_name` no lleva los cuatro nodos* → confirmada;
- *si los dos vhosts llevan los cuatro con su `auth_request`* → refutada.

Medido: **los dos vhosts llevan los cuatro con `auth_request`**. Por la letra del criterio,
**H1 queda REFUTADA**. Se respeta el criterio y no se mueve.

**Y sin embargo el criterio no tenía rama para lo que se encontró**: asumía que, si el fallo
había existido, **seguiría vivo**. No sigue vivo — **fue reparado antes de la medición**, y el
artefacto previo a la reparación **sigue en disco**. Ese artefacto responde la pregunta que el
criterio intentaba inferir del estado actual:

```bash
sudo docker exec nexora_nginx ls -l --full-time /etc/nginx/snippets/
```

```
-rw-rw-r--  1000 1000  7397  2026-07-27 15:48:45 +0000  stream-gate.conf
-rw-r--r--  root root  6046  2026-07-27 15:48:45 +0000  stream-gate.conf.bak-20260727-1500
-rw-rw-r--  1000 1000   884  2026-07-27 03:17:04 +0000  app-locations.conf
(… el resto de snippets y todo conf.d/: 2026-07-27 03:17 …)
```

Y el contenido de esa copia de seguridad —la que estuvo **en vigor** entre las 03:17 y las
15:48 del 27-jul— es concluyente:

```bash
sudo docker exec nexora_nginx grep -nE 'location \^~ /stream/|auth_request ' \
  /etc/nginx/snippets/stream-gate.conf.bak-20260727-1500
```

```
 44:location ^~ /stream/ec-main/ {
 48:    auth_request /__stream_auth;
 67:location ^~ /stream/co-main/ {
 70:    auth_request /__stream_auth;
100:location ^~ /stream/tc-main/ {
103:    auth_request /__stream_auth;
                      ← no hay tc-mia
```

**Tres nodos con gate, `tc-mia` ausente.** Es **exactamente** el diferencial observado
(3 × 401 + 1 × 200), en el fichero que producción estaba sirviendo.

Corrobora la datación el propio proceso: contenedor arrancado el `2026-07-27T12:57:33Z`, pero
los *workers* son ~3 h **más jóvenes** que el master → hubo un `reload` hacia las 15:5x, justo
después del `mtime` 15:48:45 del snippet. Esa recarga es la que aplicó el arreglo.

**Ventana de fallo acotada: 2026-07-27 ~03:17 → ~15:48 UTC (~12.5 h).**

### 8.4 Qué se deriva

1. **No fue un bypass.** La petición a `/stream/tc-mia/…` **nunca llegó al gate**: no había
   `location` que la capturase. Cayó en el catch-all `location /` de
   `snippets/app-locations.conf:25` → `proxy_pass http://nexora_web_player:80` →
   `try_files … /index.html` → **200 con HTML**.
2. **No hubo exposición de vídeo, y no hace falta el `curl` para afirmarlo.** Sin `location`
   no hay `auth_request`, luego no hubo nada que «aprobar»; y el destino del catch-all es el
   contenedor del web player, que **no sirve segmentos ni manifiestos**. La petición jamás se
   dirigió a la torre de Miami. Un 200 desde el SPA no puede contener vídeo. **H2 (grant vivo
   en Redis) queda descartada**: el gate ni siquiera corrió.
3. **Impacto real: disponibilidad.** Durante esas ~12.5 h los canales de la torre de Miami
   **no se veían en ninguna de las dos marcas**, devolviendo el HTML del SPA en vez del
   manifiesto.
4. **Corrección del «qué vhost»: la anotación imprecisa era la de §3, no la del registro.**
   §3 exigía que la prueba fuese contra la marca nueva, porque modeló el despliegue sobre el
   monolito y los scripts `patch_nginx_*.py` (que insertan el bloque **en un solo** `server{}`).
   Eso **no es lo desplegado**. Lo desplegado es **un snippet compartido**: al faltarle
   `tc-mia`, el fallo afectaba a **las dos marcas por igual**. Por tanto **el registro original
   («la prueba fue contra `nexoraplay.net`») es correcto y consistente**, y §3 «A favor 6»
   —que explicaba el conflicto como una diferencia *entre vhosts*— es **incorrecto**: la
   diferencia era **temporal**, no por dominio.
5. **Se reconcilia el 401 de `676d68e` (26-jul 22:55) sin descartarlo.** A esa hora aún no
   estaba desplegado el layout factorizado (llegó el 27-jul 03:17); regía la config anterior,
   que sí gateaba `tc-mia`. Secuencia: **401 (26-jul) → 200 (27-jul 03:17–15:48) → 401
   (desde 15:48)**. Ninguna medida del histórico era falsa.
6. **Causa raíz organizativa: se desplegó una copia rancia.** `c1f76e1` añadió `tc-mia` al
   snippet **en el repo a las 02:11**; el despliegue de las **03:17** —una hora *después*—
   subió un snippet **sin** `tc-mia`. Es el riesgo de «producción no es git» en estado puro:
   un arreglo que ya existía en el repo fue **pisado** por una copia manual anterior.
   El fallo no fue de diseño del gate, sino **del procedimiento de copia**.

### 8.5 Qué queda pendiente — decide el dueño, NO se ha aplicado nada

**El agujero de clase sigue abierto.** Verificado en esta misma lectura: **ni el repo ni la
config viva** tienen el cinturón de §5.1 (`grep -rnE 'location \^~ /stream/ \{' deploy/nginx/`
→ 0 resultados). Hoy no hay ningún nodo sin declarar, así que no hay síntoma; pero **el
próximo nodo que se dé de alta y se olvide vuelve a producir un 200 mudo** en vez de un 401.
La reparación del 27-jul arregló *el caso*, no *la clase*.

**Arreglo concreto propuesto (§5.1) — NO aplicado.** Añadir al final de
`deploy/nginx/snippets/stream-gate.conf`, y desplegarlo con el procedimiento habitual:

```nginx
# Cinturón: un /stream/<nodo> no declarado NO puede caer en el catch-all del SPA
# (snippets/app-locations.conf: location / -> web_player -> try_files … /index.html),
# que responde 200 con HTML y disfraza un `location` ausente de "gate abierto".
# Prefijo MÁS CORTO que los de cada nodo, así que solo actúa cuando ninguno coincide.
location ^~ /stream/ {
    access_log /dev/stdout stream_safe;
    default_type application/json;
    return 401 '{"success":false,"error":"playback token required or invalid"}';
}
```

Riesgo del cambio: **nulo para las rutas vivas** — nginx elige siempre el prefijo más largo,
y `^~ /stream/` es más corto que `^~ /stream/tc-mia/`. Devuelve 401 (no 404) a propósito, para
no permitir enumerar qué nodos existen.

**Además, y por orden de valor:**

| # | Acción | Por qué | Urgencia |
|---|---|---|---|
| 1 | Aplicar §5.1 (cinturón) | Cierra la clase entera de fallo, no solo este caso | Alta |
| 2 | Arreglar el **procedimiento de despliegue** (causa raíz, §8.4.6) | Un `git`-diff o checksum repo↔vivo antes de copiar habría evitado las 12.5 h | Alta |
| 3 | Retirar `deploy/nginx/nexoraplay.conf` (939 líneas) y los `patch_nginx_*.py` (§5.2) | Material muerto que ya indujo un diagnóstico equivocado | Media |
| 4 | Borrar `stream-gate.conf.bak-20260727-1500` del contenedor | Hoy **no se carga** (los `include` son por nombre exacto, no glob), pero es un fichero de config obsoleto en `/etc/nginx/snippets/` | Baja |
| 5 | §5.3–§5.6 (backend) | Huecos reales, **independientes** de este veredicto y **siguen abiertos** | Media |

Nada de lo anterior se ha ejecutado: la autorización de esta sesión era **de solo lectura**.

### 8.6 Lo que esta sonda **no** puede afirmar

- **No se verificó el `Content-Type` del 200 original.** La sonda B quedó fuera de la
  autorización y, además, **ya no es reproducible**: la ventana se cerró el 27-jul a las 15:48.
  La conclusión «era HTML del SPA» es **deductiva** (se sigue de la config vigente en la
  ventana + el destino del catch-all), no una medida directa. Es sólida, pero conviene
  registrarla como lo que es.
- **El `mtime` data la copia, no necesariamente la edición.** La ventana 03:17→15:48 se apoya
  en `mtime` + edad de los *workers*; ambos coinciden, pero ninguno es un log de auditoría.
- **No se revisó `access_log` de la ventana** para contar cuántas peticiones reales cayeron en
  el SPA. Sería la única vía de cuantificar el impacto de disponibilidad, y es lectura pura:
  queda como sonda opcional si el dueño quiere la cifra.

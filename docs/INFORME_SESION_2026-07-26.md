# Informe de sesión — 2026-07-26

Trabajo sobre `E:\WEBSITE`: `nexora_api`, `nexora_app`, `nexora_ios`, y análisis de las
plataformas legacy Stalker/Ministra y Xtream Codes.

---

## 1. Resumen ejecutivo

Se encontró y cerró **un fallo de autorización explotable en producción**: la superficie
de dispositivos emitía tokens de reproducción sin autenticación y sin comprobar
entitlements. Anulaba el control anti-IDOR que se creía activo desde PROD-2A.

Además se cerraron tres bloqueantes que impedían escribir un cliente nativo correcto
contra la Client API, y se endureció el cliente Flutter.

| | |
|---|---|
| Tests `nexora_api` al inicio | 124 |
| Tests `nexora_api` al cierre | **198** |
| Tests `nexora_app` al inicio | 1 |
| Tests `nexora_app` al cierre | **43** |
| Commits | 5 (ninguno pusheado) |

Nada se ha desplegado. Todos los cambios de comportamiento van tras flags cuyo valor
por defecto preserva el comportamiento actual de producción, salvo `STB_AUTH_ENFORCE`,
que se decidió **cerrado por defecto** por tratarse de un fallo de seguridad.

---

## 2. El hallazgo principal — IDOR en la superficie de dispositivos

### Qué pasaba

`POST /api/stb/auth/play` (`app/api/stb/playback.py`) no tenía **ninguna** dependencia de
autenticación. Sus únicas dependencias eran `get_db`, `get_redis` y el constructor del
servicio. El `subscriber_id` y el `device_id` llegaban **en el cuerpo de la petición**.

El docstring del módulo lo declaraba explícitamente:
*"None of these endpoints require admin credentials — they are device/player facing."*

Y la llamada al servicio era **posicional**:

```python
svc.authorize(body.subscriber_id, body.device_id, body.channel_id, ip, user_agent)
```

La firma real es
`authorize(subscriber_id, device_id_str, channel_id=None, ip=None, user_agent=None, channel_key=None, node=None)`,
de modo que `body.channel_id` caía en el parámetro interno `channel_id` —que es el
**`stream_key`**— y `channel_key` quedaba en `None`. En `stream_auth_service.py`:

```python
if channel_key is None:
    return          # EntitlementService no se consulta
```

### Alcance

- **`ENTITLEMENT_ENFORCE=true` era evitable.** El gate anti-IDOR activado en PROD-2A no
  se ejecutaba nunca por esta vía, con el flag encendido o apagado.
- El token emitido salía con `chn`, `node` y `cip` en `None`, así que también se saltaba
  el binding de nodo y el de IP.
- **Nginx lo publica**: `deploy/nginx/nexoraplay.conf` define `location ^~ /api/` sin
  excepción para `/api/stb/`.
- **Cobertura de test: cero.** Ninguna prueba tocaba esa superficie.
- `POST /api/stb/heartbeat` tenía el mismo patrón: renovaba el slot del ZSET de
  conexiones, tocaba la sesión IPTV y devolvía el plan y las conexiones activas del
  abonado, todo a partir de un `device_id` sin token.
- `POST /api/v1/devices/heartbeat` era el gemelo exacto y quedaba abierto. Cerrar solo
  la ruta `/api/stb/` **no cerraba el ataque**.

Es el mismo IDOR de `Itv::createLink` de Ministra que
`docs/nexora-best-of/02_LO_QUE_NO_DEBEMOS_COPIAR.md` §7 da por mitigado.

### Cómo se encontró

Tres agentes de análisis independientes llegaron al mismo fallo por caminos distintos,
sin que se les indicara. Se verificó después línea por línea de forma manual.

### Qué se hizo

- Se cablea la superficie de token STB que **ya existía sin usar** en el código
  (`TYPE_STB_ACCESS` / `AUD_STB`, marcada *"Reserved; wired in STB hardening"*).
- La identidad sale de los claims `sub`/`dev` del token. Los campos del cuerpo pasan a
  ser opcionales y, si vienen, deben coincidir: un desajuste es **403**, nunca una
  degradación silenciosa al valor del cuerpo.
- La vía STB resuelve el `channel_key` público a `(stream_key, node)` y llama por
  keyword, igual que la ruta del cliente, de modo que el entitlement se ejecuta y los
  claims quedan poblados.
- Flag `STB_AUTH_ENFORCE`, **default `True`**. La vía abierta es una escotilla explícita
  para firmware legacy hipotético y **solo reabre el agujero de identidad, nunca el de
  entitlement**. Un token presentado se valida siempre, con flag o sin él.
- La respuesta devolvía `result.channel_id`, que tras el arreglo es el `stream_key`
  interno. Ahora devuelve el `channel_key` público, respetando la regla del proyecto de
  no exponer `stream_key`.

`POST /api/stb/auth/validate` se dejó sin tocar: es un validador, no un emisor, y
exigirle token rompería el modelo de backend-auth. Solo se corrigió su docstring, que
afirmaba en falso que Flussonic llama a esa ruta —los tres Nginx desplegados apuntan a
`/internal/stream-auth/validate`.

### Riesgo de rotura

Se verificó que **ningún consumidor conocido usa `/api/stb/*`**: el web player habla con
`/api/client/*`; `nexora_app` y `nexora_ios` hablan con el portal Stalker PHP. Cero
coincidencias en `scripts/`, `tests/`, CI, Docker y `.env`.

No se puede descartar un STB legacy fuera del árbol de repositorios. Para ese caso,
`STB_AUTH_ENFORCE=false` es un rollback de una sola variable que conserva el arreglo de
entitlement.

---

## 3. Los tres bloqueantes del cliente nativo

### 3.1 El reissue de playback esquivaba el IP-binding

`GET /api/client/playback/{channel_id}` no pasaba por la función de firma, así que con
`SIGNED_URL_ENFORCE=true` —activo en producción— devolvía `playback_url` **sin
`?token=`**. Solo funcionaba porque el grant de segmentos de Redis cubría el hueco.

Y `create_token()` no recibía `channel_key`, `node` ni `client_ip`, así que los tokens
reemitidos salían con esos claims en `None`. Como la comprobación de IP exige que `cip`
sea truthy, **los tokens de reissue quedaban exentos del IP-binding**.

Consecuencia práctica: siendo el reissue la ruta dominante —uno cada ~45 s por stream—
**activar PROD-Fase 2D no habría tenido efecto real**.

El arreglo cierra el emisor. **La exención del validador sigue en pie por diseño**
(compatibilidad STB legacy) y hay que decidirla antes de poner `strict`.

Flag nuevo `PLAYBACK_REISSUE_ENTITLEMENT_CHECK` (default `False`): reevalúa el
entitlement en cada renovación, cerrando el hueco de perder un canal a mitad de sesión
sin que el stream se corte hasta 4 h después. Coste contado por lectura de código: 5
lecturas indexadas por reissue.

### 3.2 El `device_secret` se perdía en el login

El secreto se generaba al registrar el dispositivo durante el login y se **descartaba**.
Un `register` posterior con el mismo `device_id` entraba por la rama "dispositivo
existente" y devolvía `null`.

Resultado: **todo dispositivo dado de alta vía login no podía activarse nunca**, así que
`DEVICE_SECRET_ENFORCE` no era activable. Bloqueaba el ticket `NX-DEV`, que es
precisamente la identidad fuerte que sustituye a la identidad por MAC del mundo Stalker.

Arreglo aditivo: campo opcional en la respuesta del login, `null` cuando no aplica, de
modo que ningún cliente actual se rompe. Se emite **una sola vez**, al crear el
dispositivo. El objeto de resultado redacta el secreto y ambos tokens en su
representación, para que no se filtre por un traceback o una interpolación.

**Sin endpoint de rotación, deliberadamente**: una ruta protegida solo por el access
token colapsaría los dos factores en uno, y un token robado podría activar un
dispositivo del atacante.

### 3.3 `os_version` producía un 500

Aceptado con longitud 512 en el login pero mapeado a 32 en el DTO de registro, con
columna `String(32)`. Un `userAgent` de navegador supera ese límite: pasaba la validación
de entrada y reventaba con `ValidationError` → **500**.

Se trunca en el borde del DTO. El valor completo se conserva sin límite en
`devices.user_agent`. Los campos de identidad (`device_id`, `mac_address`) **no** se
truncan: un identificador mutilado en silencio es peor que un 422.

### 3.4 Extra — el catálogo no filtraba por plan

`GET /api/client/catalog/channels` ignoraba `plan_channels` y devolvía todos los canales
activos. El filtrado solo ocurría en playback, así que el cliente mostraba canales que al
pulsar Play daban `403 CHANNEL_NOT_INCLUDED`.

Flag `CATALOG_ENTITLEMENT_FILTER` (default `False`). Máximo 2 consultas con el flag
activo, 1 con él apagado, constante con el tamaño del catálogo.

Decisión de diseño: **sin suscripción vigente devuelve el catálogo completo, no lista
vacía**. Un usuario expirado necesita ver qué recupera al renovar; una rejilla vacía mata
la vía de renovación y es indistinguible de un backend roto. Ocultar no es un control de
seguridad: la puerta real sigue siendo el entitlement en el playback.

---

## 4. `nexora_app` (Flutter)

### Estado encontrado

Cliente Stalker/MAG para Android TV, 3.533 líneas de Dart, **un solo commit y sin
remote**. Se hacía pasar por un decodificador MAG250.

Cinco problemas:

1. **URL del portal cableada** a una IP de LAN. La app solo funcionaba en esa red.
2. **MAC compartida e inválida**: `00:1A:79:00:NX:01`. `NX` no es hexadecimal, y al ser
   constante, todas las instalaciones se identificaban como el mismo decodificador.
3. **Bypass de autorización**: si la resolución del enlace fallaba, devolvía la URL cruda
   y la reproducía igual.
4. **Errores tragados**: cuatro `catch` vacíos en 112 líneas.
5. **Sin autenticación de usuario**: la identidad *era* la MAC.

### Origen del bug de la MAC

La constante venía de un addon de Kodi (`STALKER\Nexora_decoded\...\gui.py`) donde era el
**fallback** de una función que leía la MAC real del hardware. El port a Flutter conservó
el fallback y perdió la lectura real. `NX` son las iniciales de Nexora. El cliente iOS
repite el mismo bug.

### Qué se entregó

- URL de portal configurable con persistencia y validación, y pantalla de ajustes que
  encaja con el estilo existente.
- **MAC derivada por HMAC-SHA256 de `ANDROID_ID`** con OUI `00:1A:79`. Sobrevive a
  reinstalaciones mientras se firme con la misma clave. Descarta anclas defectuosas
  conocidas y cae a aleatorio.
- **Despliegue por fases**: la MAC propia se calcula y se expone siempre, pero un usuario
  preexistente **sigue enviando la legacy** hasta que se apague el interruptor. Esto
  permite aprovisionar el portal antes de conmutar.
- `MethodChannel` nativo para `ANDROID_ID`, con timeout de 3 s para que un APK sin el
  lado nativo no cuelgue el arranque.
- `allowBackup="false"`: Auto Backup estaba activo por defecto y restaurar en otro equipo
  habría clonado la MAC.
- Corregido un bug preexistente: el botón "Reintentar" del launcher era un
  `GestureDetector` sin foco ni manejo de teclas, es decir, **inalcanzable con el mando**
  en un Android TV. Justo el escenario que provocaba la IP cableada.
- `ROADMAP.md` y `deploy/RUNBOOK_MAC_ROLLOUT.md` (4 fases con rollback por fase).

### El riesgo que motivó el despliegue por fases

Verificado leyendo el fuente del portal: `User::getByMac()` es igualdad exacta de cadena,
el índice `UNIQUE` sobre `users.mac` **fue eliminado** en `db/delta/12-auth.sql`, y
`auto_add_stb = true` es el default.

Hoy **todos los dispositivos comparten una única fila** en `users`. Al desplegar MAC por
dispositivo, cada uno se convierte en una cuenta autocreada **sin tarifa ni suscripción**
→ pérdida masiva de servicio el día del despliegue. No es un riesgo teórico: es el
comportamiento esperado.

Verificación: `flutter analyze` limpio, **43/43 tests**, APK debug compilando.

---

## 5. `nexora_ios` (SwiftUI)

12 archivos Swift, 1.743 líneas, **sin repositorio git y sin respaldo**.

Es un port del cliente Flutter: mismas cuatro acciones del portal en el mismo orden,
mismas cabeceras con idénticos valores literales, y el mismo comentario del fallback
carácter por carácter. Pero **lleva dos meses congelado**: los Swift son del 17 de mayo,
escritos en una sola sesión de ~18 minutos.

Tres cosas que condicionan su futuro:

- **No hay target de tvOS.** `TARGETED_DEVICE_FAMILY = "1,2"` (iPhone + iPad). Para un
  producto IPTV, la app de televisor sencillamente no existe.
- **`AVPlayer` solo reproduce HLS.** Ni MPEG-TS crudo ni RTMP, que sí soporta `media_kit`
  en Flutter. Si el portal entrega `.ts` en algún canal, en iOS no se ve.
- **No se puede firmar**: `CODE_SIGN_STYLE = Automatic` sin `DEVELOPMENT_TEAM`.

Migrar a la Client API son 2 a 3,5 semanas. La capa de red es la parte pequeña (3-4
días); lo caro es que **iOS nunca tuvo login, ni identidad de dispositivo, ni ajustes**, y
la Client API exige las tres.

**Decisión pendiente**: si `nexora_ios` sigue vivo o se sustituye por un build iOS de
Flutter, que ya resolvió identidad de dispositivo y configuración.

---

## 6. Análisis de las plataformas legacy

### 6.1 El portal desplegado no es Stalker

`server/load.php` del despliegue fue **reescrito** para lanzar un subproceso PHP-CLI a
través de `server/load_cli_runner.php`, fichero que **no existe en el Stalker original** y
cuya cabecera declara que sirve para *"bypasses ionCube Apache license restriction"*.

Ese shim:

- **Cortocircuita `create_link`** y devuelve `{url, id, cmd}` —con la clave `url`, que es
  la que los clientes leen— **sin ninguna autenticación**. `Itv::createLink()` nunca se
  ejecuta para `type=itv`.
- **Descarta el token del llamante**, ejecuta un handshake interno y reinyecta el token
  recién emitido. **Toda acción del portal corre como un STB recién enrolado.**

Junto con `enable_subscription=false`, `show_unsubscribed_tv_channels=true` y
`auto_add_stb=true` en `custom.ini`: el portal desplegado **no autentica ni autoriza
nada**, y entrega la URL real de todos los canales a quien la pida.

Corolario: los clientes nunca reciben una URL autorizada por ningún camino, y la lógica
de tokens temporales, `nginx_secure_link` y balanceo es **código inalcanzable**.

### 6.2 El portal no controla la concurrencia

No existe ninguna clave `max_sessions` en su configuración. Los clones de MAC solo se
registran en log, con `log_mac_clones = false` por defecto.

El único límite real lo impone **Flussonic**, mediante el callback
`server/api/chk_flussonic_tmp_link.php`, que devuelve `X-Unique: true`,
`X-Max-Sessions: 1`, `X-UserId`. **Esa es exactamente la pieza que `nexora_api`
sustituye.** El "token" que valida es un `md5(url+microtime+uniqid)` en caché con TTL de
**5 segundos**, sin firma ni claims.

### 6.3 Xtream Codes

Correcciones de partida: la carpeta `NEXORA\codigo xtream codes` **no es un cliente
Xtream, es Aptoide** (búsqueda literal de `player_api`, `xtream`, `bouquet`, `stream_id`
en los 19 MB de `.dex`: cero coincidencias). Y `player_api.php`, `get.php` y `xmltv.php`
**no existen en ningún punto de `E:\WEBSITE`** — lo que sí está es el panel de
administración y el instalador.

**Límite conocido**: el esquema JSON de respuesta de `player_api.php` no está en ninguna
fuente en disco. Para implementar compatibilidad real habría que capturarlo de un
servidor vivo. No se puede inventar desde el material disponible.

Lo que Xtream hace mejor que nosotros:

| | |
|---|---|
| **Bouquets múltiples por línea** | con orden de canales propio de cada paquete |
| **Categorías tipadas** | tabla real con `live`/`movie`; nosotros tenemos un string plano |
| **Formatos de salida por línea** | `ts`/`m3u8`/`rtmp` permitidos por línea |
| **Catch-up nativo por canal** | `tv_archive_duration`, `tv_archive_server_id` |
| **Multi-servidor de primera clase** | reparto por carga real, GeoIP, `force_server_id` |
| **Motor de plantillas único** | emite M3U, Enigma2 y JSON sin tocar código |

Dónde somos claramente mejores: concurrencia atómica con Lua frente a un `COUNT(*)` con
ventana de carrera que expulsa haciendo `kill -9` por SSH; integridad referencial frente
a JSON serializado en columnas de texto; y sobre todo, Xtream mete **usuario y contraseña
en el path de cada segmento `.ts`** sobre HTTP en claro, con un hash de traspaso entre
nodos que para HLS usa `valid_time = 0`, es decir, **nunca caduca**.

### 6.4 Lo mejor de Stalker que merece copiarse

**Catch-up anclado al programa EPG, no a un rango arbitrario.** Con márgenes
configurables antes y después, porque las parrillas mienten ±minutos — eso es experiencia
de campo. Y cuando hay un hueco entre programas, fabrica un pseudo-programa sintético
para que el reproductor no pierda continuidad.

**TTL diferenciado por naturaleza del evento.** Un `reboot` de hace tres días no debe
ejecutarse; un mensaje al usuario sí debe esperarle. Comandos de sistema ~2 ciclos de
heartbeat, mensajes días.

**La cadena tarifa→paquete→servicio**, y en particular dos piezas:
- `all_services`: el plan "todo incluido" no materializa 800 filas, y **un canal nuevo
  entra automáticamente** sin backfill.
- `optional`: un plan describe a la vez lo incluido y lo *comprable*. El upsell está en
  el esquema, no en el código.

**El health check prueba la URL firmada real** de un canal, no una URL ficticia. Hoy
nosotros podemos tener el nodo "sano" y todos los canales rotos.

**El corte no depende de que el dispositivo obedezca**: un evento `cut_off` invalida
además los tokens del usuario. El comando es UX; la seguridad va por otro lado.

### 6.5 El hueco silencioso más urgente

Sin equivalente a `all_services`, **cada canal nuevo exige un INSERT por plan o queda
invisible**. Y es un fallo silencioso: se publica el canal, nadie lo ve, nadie se entera.

Mitigación barata, antes de normalizar nada: `plans.all_channels boolean default false`
con cortocircuito en `entitlement_service`. Son ~10 líneas y replican el 80 % del valor.

---

## 7. El roadmap está desfasado en ambos sentidos

`docs/ROADMAP.md` da por **pendientes** cosas ya entregadas: la concurrencia atómica Lua
(P1.2), el contador de fallos de playback, las alertas de nodo caído, el `correlation_id`,
la inmutabilidad del `audit_log`, el lockout por intentos fallidos, la auditoría de login
admin, Argon2id, y los PRs #9/#10/#11 que ya están mergeados.

Y da por **hecho** lo que no lo está: dice que el hito M1 está cerrado en código y que
solo falta activar 2D. Faltaba además todo lo de este informe.

También conviene matizar que los estados "PROD-2A/2B/2C ✅" son estados del `.env` de
producción, no del código: en el repositorio los tres flags tienen default `False`.

**Recomendación**: reconciliarlo antes de usarlo como criterio para desbloquear
`NX-APPS`.

---

## 8. Decisiones pendientes

1. **`STREAM_GRANT_MAX_LIFETIME_SECONDS` sigue en `0` = ilimitado.** El mecanismo del
   tope absoluto existe y está desplegado, pero mientras no se fije un valor, revocar un
   suscriptor **no corta su stream en curso**. El roadmap lo da por "resuelto en código",
   lo cual es cierto, pero el riesgo sigue abierto en la práctica.

2. **La exención del validador para tokens sin `cip`/`node`.** El arreglo cierra el
   emisor; el validador los sigue dejando pasar por diseño, como compatibilidad STB. Hay
   que decidirlo **antes** de poner `PLAYBACK_IP_BINDING_MODE=strict`, o 2D seguirá
   siendo parcialmente evitable.

3. **Si `nexora_ios` sigue vivo** o se sustituye por un build iOS de Flutter.

4. **Cuándo desplegar los flags nuevos.** Los tres van con default que no cambia
   producción; `STB_AUTH_ENFORCE` es la excepción y va cerrado.

5. **La Fase B del despliegue de MAC exige activar `enable_api` en el portal.** Pero
   `api_auth_login` viene comentado en su configuración y el gestor solo valida
   credenciales si no están vacías: activarlo sin fijarlas **deja la gestión de cuentas
   abierta sin autenticación**. Hay que fijar las credenciales antes, no después.

6. **`NEXORA_MAC_SALT` debe congelarse** antes de la Fase A. Si cambia entre fases, todas
   las MAC aprovisionadas quedan inválidas en bloque.

---

## 9. Lo que NO está verificado

- **Nada se ha probado contra Flussonic ni Nginx reales.** El binding de nodo está
  razonado desde el código, no observado. Riesgo dependiente de datos: si algún canal
  tuviera `flussonic_node` incoherente con el nodo que Nginx pasa al validador, empezaría
  a dar 403. Merece una comprobación puntual antes de desplegar.
- **Nada se ha ejecutado en un Android TV real ni en emulador.** La ruta del
  `MethodChannel` y la estabilidad del `ANDROID_ID` tras reinstalar solo están cubiertas
  por mocks.
- **Ningún handshake real contra un portal Stalker**: que el portal acepte las MAC
  generadas sigue sin comprobarse.
- **Solo Windows + Python 3.14**, sin corrida de CI en Linux.
- El núcleo del portal está cifrado con ionCube: el contrato exacto del handshake y el
  almacenamiento del PIN parental **solo pueden inferirse por comportamiento**.

---

## 10. Deuda menor detectada, no corregida

- **`__pycache__` está versionado** en `nexora_api`. Cualquier commit que toque esos
  módulos arrastra `.pyc` binarios.
- **La suite es sensible al orden de colección en Windows.** La política
  `WindowsSelectorEventLoopPolicy` solo se fija como efecto colateral de un import, así
  que según el orden aparecen errores de `ProactorEventLoop` en psycopg. Convendría
  fijarla en `tests/conftest.py`.
- **Entrada muerta en el rate limit**: se mapea `/api/stb/register` pero la ruta real es
  `/api/stb/register/{sub_id}` y el lookup es de igualdad exacta, así que esa entrada no
  se aplica nunca.
- **`ec-quito` no tiene `location`** en el Nginx de producción, aunque sí en staging.
- **`hash_ip(None)`** produce un hash estable del valor vacío: si Nginx dejara de enviar
  `X-Real-IP`, todos los clientes compartirían el mismo grant de segmentos.
- **Campos muertos**: `channels.requires_subscription` y `channels.epg_id` se escriben y
  se exponen, pero ninguna ruta de autorización los lee.
- **El interruptor de corte del despliegue por fases** de `nexora_app` no tiene llamador
  en la interfaz: hoy solo se cambia recompilando.

---

## 11. Commits

Ninguno está pusheado. `nexora_app` no tiene remote configurado.

| Repo | Rama | Commit | Contenido |
|---|---|---|---|
| `nexora_api` | `feat/client-api-blockers` | `9d7800b` | IDOR cerrado: autenticación + entitlement en la superficie de dispositivos |
| `nexora_api` | `feat/client-api-blockers` | `89756a7` | Reissue firmado con `cip`/`node`, `device_secret` entregado, `os_version`, catálogo filtrable |
| `nexora_api` | `docs/fix-ui-pointer` | `c2e3f2f` | Corrección del puntero de UI |
| `nexora_app` | `feat/fase1-config-mac` | `201fb55` | MAC derivada de ANDROID_ID + despliegue por fases |
| `nexora_app` | `feat/fase1-config-mac` | `b67d404` | URL de portal configurable + MAC por dispositivo |

Respaldo de `nexora_app` antes de tocarlo: tag `pre-team-baseline` y bundle con todas las
refs.

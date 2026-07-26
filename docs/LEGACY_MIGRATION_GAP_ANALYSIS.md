# Análisis de brechas: abandono del portal Stalker legacy → Client API de Nexora

> Documento de análisis. No describe trabajo comprometido: describe el estado del código en disco
> en el momento de escribirlo (rama `feat/client-api-blockers`, con cambios sin commitear en el
> árbol de trabajo).

## Convenciones

Cada afirmación va marcada:

- **[V]** — VERIFICADA leyendo el fichero indicado en la línea indicada.
- **[I]** — INFERIDA: deducida del código pero no comprobable en ejecución (típicamente por ionCube
  o porque depende de la configuración real del servidor en producción).

## Fuentes analizadas

| Alias en este documento | Ruta absoluta |
|---|---|
| **Backend** | `E:\WEBSITE\nexora_api` |
| **Portal legacy** (Stalker prístino) | `E:\WEBSITE\STALKER\stalker_portal-5.0.0-r2\stalker_portal-5.0.0-r2\` |
| **Portal desplegado** (Nexora) | `E:\WEBSITE\NEXORA\nexora_portal-5.0.0-v1\nexora_portal-5.0.0-r2\` |
| **Cliente de referencia** | `E:\WEBSITE\nexora_api\web_player` |
| **Cliente legacy Flutter** | `E:\WEBSITE\nexora_app` |
| **Cliente legacy iOS** | `E:\WEBSITE\nexora_ios` |

---

## 0. Resumen ejecutivo

El portal legacy aparenta ser un sistema de derechos y de control de reproducción completo. En el
despliegue real no lo es: **no hay entitlements que preservar**, y **no hay control de acceso al
stream que replicar** más allá de un callback de 15 líneas que `nexora_api` ya sustituye con creces.

Lo que sí tiene el legacy y `nexora_api` no es **catálogo funcional**: EPG real, VOD, catch-up, nPVR,
radio, karaoke, control parental, favoritos server-side, búsqueda y multi-idioma. Esa es la brecha
real, y es de producto, no de seguridad.

La ruta de salida del legacy pasa **primero por el backend**. `NX-APPS` está formalmente bloqueado
hasta cerrar P0+P1+P2.1 (`docs/ROADMAP.md:129`, `docs/ROADMAP.md:151`) **[V]**.

---

## 1. Por qué el legacy da menos de lo que parece

### 1.1 La configuración desplegada desactiva el sistema de derechos

`E:\WEBSITE\NEXORA\nexora_portal-5.0.0-v1\nexora_portal-5.0.0-r2\server\custom.ini`, sección
`[billing]`, líneas 25-29 **[V]**:

```ini
[billing]
enable_subscription = false
show_unsubscribed_tv_channels = true
store_auth_data_on_stb = false
enable_tariff_plans = false
```

`custom.ini` sobreescribe `config.ini` (declarado en su propia cabecera, líneas 1-2) **[V]**.

### 1.2 La lógica de `itv.class.php` depende de ese flag

`...\server\lib\itv.class.php:1141-1149` **[V]** — el único punto donde se sustituye la `cmd` real por
un destino no reproducible:

```php
if (Config::get('enable_subscription') && (empty($this->response['data'][$i]['type']) || $this->response['data'][$i]['type'] != 'dvb')){
    if (in_array($this->response['data'][$i]['id'], $this->getAllUserChannelsIds()) || $this->stb->isModerator()){
        $this->response['data'][$i]['open'] = 1;
    }else{
        $this->response['data'][$i]['open'] = 0;
        $this->response['data'][$i]['cmd'] = 'udp://wtf?';
    }
}
```

Con `enable_subscription = false` ese bloque **nunca se ejecuta**. El filtrado por suscripción en la
consulta (`itv.class.php:507`, `:544`, `:722`) también queda anulado porque
`show_unsubscribed_tv_channels = true` **[V]**.

**Conclusión:** el despliegue actual entrega la URL real de todos los canales a cualquier MAC.
El sistema de derechos está efectivamente desactivado. **No hay entitlements que preservar en la
migración.** **[V]**

> El único `cmd = 'udp://wtf?'` que sí sigue activo es el de `itv.class.php:1192-1196`, y se dispara
> por ausencia de enlaces o por monitorización caída (`error = 'limit'`), no por derechos **[V]**.

### 1.3 DISCREPANCIA IMPORTANTE: el despliegue va más lejos que eso

El portal desplegado **no ejecuta el RPC original**. `server/load.php` fue reescrito: en lugar de
instanciar `Stb` + `DataLoader`, lanza un subproceso PHP-CLI y le pasa el entorno por stdin
(`...\server\load.php:25-52`, respuesta reemitida en `:69`) **[V]**.

Ese subproceso es `...\server\load_cli_runner.php`, un fichero que **no existe en el portal legacy
prístino** (comprobado: `E:\WEBSITE\STALKER\...\server\load_cli_runner.php` no existe; el `load.php`
legacy sí usa `DataLoader` en su línea 21) **[V]**. Su cabecera declara el motivo:
`"CLI runner for Stalker Middleware API - bypasses ionCube Apache license restriction"` (línea 2) **[V]**.

Dos consecuencias, ambas **[V]**:

1. **`load_cli_runner.php:33-41` cortocircuita `create_link` sin ninguna autenticación**:

   ```php
   // create_link: resolve URL directly from the cmd parameter, no auth needed
   if ($type === 'itv' && $action === 'create_link') {
       $cmd_raw = isset($request['cmd']) ? $request['cmd'] : '';
       $url = trim(preg_replace('/^ffrt\s+/i', '', $cmd_raw));
       echo json_encode(array(
           'js'   => array('url' => $url, 'id' => 0, 'cmd' => $cmd_raw),
           'text' => 'query counter: 0',
       ));
       exit;
   }
   ```

   El servidor devuelve **el `cmd` que le manda el propio cliente**, con el prefijo `ffrt` quitado.
   `Itv::createLink()` nunca llega a ejecutarse para `type=itv`.

2. **`load_cli_runner.php:45-76` descarta el token del llamante y fabrica uno nuevo por petición**:
   fuerza `$_COOKIE['token'] = ''`, ejecuta un `handshake` interno (`:48-61`), y reinyecta el token
   recién emitido en el contexto de la petición original (`:71-76`). Es decir, **toda acción del
   portal se ejecuta como un STB recién enrolado**, sea cual sea la identidad del llamante.

**Esto refuerza el punto 1.2 en lugar de contradecirlo:** el portal desplegado no solo no aplica
derechos, es que tampoco aplica autenticación. La superficie legacy no es un sistema que migrar,
es un sistema que apagar.

---

## 2. `create_link` está roto por contrato (y el parche lo empeora)

### 2.1 El contrato original no tiene clave `url` — VERIFICADO

`...\server\lib\itv.class.php`, `createLink()` declarado en la línea 70, único `return` en la 137;
el array se construye en las líneas 126-133 **[V]**:

```php
$res = array(
    'id'          => $ch_id,
    'cmd'         => empty($error) ? $cmd.$extra : '',
    'streamer_id' => $streamer_id,
    'link_id'     => $link_id,
    'load'        => $load,
    'error'       => empty($error) ? '' : $error
);
```

Seis claves. **No existe `url`.** La URL reproducible viaja en `cmd`, típicamente con prefijo de
solución (`itv.class.php:277`: `$solution = 'ffrt';`) **[V]**.

Bug colateral: `itv.class.php:135` es un `var_dump($res);` que ensucia la salida antes del JSON **[V]**.

### 2.2 El cliente MAG de referencia lee `cmd` — VERIFICADO

- `...\c\xpcom.common.js:763` — el callback recibe directamente el contenido de `js`, ya desenvuelto.
- `...\c\player.js:2492-2532` — `player.prototype.create_link` construye la petición.
- `...\c\player.js:2522` — `if (result.cmd && result.cmd.indexOf('://') === -1){ stb.Mount(result.cmd); }`
- `...\c\player.js:2553` — `var uri = item.cmd;` ← **línea decisiva: el reproductor MAG toma la URL de `cmd`**.
- `...\c\player.js:2555-2556` — usa `item.streamer_id` e `item.link_id` para el log de reproducción.
- `...\c\tv.js:1031-1050` — mismo patrón.

**Ningún cliente JS del portal lee `result.url`** **[V]**.

### 2.3 Los clientes legacy leen `js['url']` — VERIFICADO

Flutter — `E:\WEBSITE\nexora_app\lib\stalker_api.dart`, método `getStreamUrl()` en la línea 135 **[V]**:

```dart
// :146
final js = data['js'];
// :147-149
if (js is Map && js['url'] != null && (js['url'] as String).isNotEmpty) {
  return js['url'] as String;
}
// :151-154  — Fallback: strip ffrt prefix
return cmd.replaceFirst(RegExp(r'^ffrt\s+', caseSensitive: false), '').trim();
```

iOS — `E:\WEBSITE\nexora_ios\NexoraApp\Services\StalkerAPI.swift`, `streamURL(for:)` en la línea 74 **[V]**:

```swift
// :79-83
if let js = data["js"] as? [String: Any],
   let raw = js["url"] as? String, !raw.isEmpty,
   let streamURL = URL(string: raw) {
    return streamURL
}
// :84-89  — Fallback: strip ffrt prefix
```

Ninguna de las dos apps lee `cmd`, ni `error`, ni `link_id`, ni `streamer_id` **[V]**.

### 2.4 Corrección al diagnóstico de partida

El diagnóstico previsto era: *«los clientes siempre caen a su fallback local de quitar `ffrt`»*.
Eso es **cierto contra el portal prístino**, pero **falso contra el portal desplegado**:

| Escenario | Lo que devuelve el servidor | Lo que hacen Flutter/iOS |
|---|---|---|
| Portal prístino (`Itv::createLink()`) | `{id, cmd, streamer_id, link_id, load, error}` — sin `url` | caen al fallback `ffrt` local **[I]** |
| **Portal desplegado (`load_cli_runner.php:33-41`)** | `{url, id, cmd}` — con `url` fabricada **[V]** | leen `js['url']` y **no** caen al fallback **[V]** |

El efecto neto sobre el usuario final es el mismo — y es el que importa:

- **Los canales que dependen de token temporal, de `nginx_secure_link` o de balanceo de carga no
  pueden funcionar** **[V]**. Toda esa lógica vive en `itv.class.php:192-283` (balanceo en `:192-213`,
  token Wowza en `:216-228`, token Flussonic en `:229-236`, `nginx_secure_link` en `:237-240`,
  proxy `/ch/` en `:241-281`) y hoy es **código inalcanzable** para `type=itv`, porque el shim hace
  `exit` en la línea 40 antes de llegar a `DataLoader`.
- **Los errores del servidor se silencian**: `error = 'limit' | 'nothing_to_play' | 'link_fault'`
  nunca llegan al cliente, porque el shim no los produce y las apps tampoco leerían la clave **[V]**.
- **El "link" no está autorizado por nadie**: es un eco del parámetro de entrada **[V]**.

**Marca:** el contrato de `Itv::createLink()` es **[V]**. Que las apps caigan al fallback en
producción es **[I]** y, según el `load.php` que hay en disco, **probablemente no ocurre**: obtienen
una `url` fabricada. Lo verificable en disco es que **en ningún camino el cliente recibe una URL
autorizada por el servidor**.

---

## 3. El portal no controla la concurrencia; Flussonic sí (y mal)

### 3.1 No hay límite de sesiones por usuario en el portal — VERIFICADO

- No existe ninguna clave `max_sessions` de ámbito usuario en `config.ini` ni en `custom.ini` **[V]**.
- El único `max_sessions` del código es un campo **por streamer**, usado para calcular carga en el
  balanceador: `...\server\lib\streamserver.class.php:182` y `:188` **[V]**.
- Los clones de MAC solo se **registran**, y por defecto ni eso: `config.ini:223` →
  `log_mac_clones = false`, consumido en `...\server\lib\watchdog.class.php:24` **[V]**. El comentario
  de `config.ini:222` confirma que su único efecto es escribir en
  `/var/log/stalkerd/mac_clone_error.log` **[V]**.

### 3.2 El único límite real lo impone Flussonic, vía un callback trivial

`...\server\api\chk_flussonic_tmp_link.php` — el fichero completo son 15 líneas **[V]**:

```php
$uid = Itv::checkTemporaryLink(@$_GET['token']);

if (!$uid || empty($_GET['token'])){
    header($_SERVER["SERVER_PROTOCOL"]." 403 Forbidden");
}else{
    header("X-AuthDuration: 36000");
    header("X-Unique: true");
    header("X-Max-Sessions: 1");
    header("X-UserId: ".$uid);
    header($_SERVER["SERVER_PROTOCOL"]." 200 OK");
}
```

El límite de concurrencia es **`X-Max-Sessions: 1` en una cabecera HTTP**, delegado íntegramente a
Flussonic. El portal no lo modela, no lo persiste y no lo audita.

### 3.3 El "token" que valida no es un token

`...\server\lib\itv.class.php:332-345` **[V]**:

```php
private function createTemporaryLink($url){
    $key = md5($url.microtime(1).uniqid());
    $cache = Cache::getInstance();
    $result = $cache->set($key, $url, 0, Config::getSafe('tv_tmp_link_ttl', 5));
    ...
}
```

y `itv.class.php:347-350` **[V]**:

```php
public static function checkTemporaryLink($key){
    return Cache::getInstance()->get($key);
}
```

Propiedades del esquema, todas **[V]**:

| Propiedad | Valor |
|---|---|
| Construcción | `md5(url + microtime(1) + uniqid())` — hash de entropía, no firma |
| Firma criptográfica | **ninguna** |
| Claims | **ninguno** (el valor cacheado es la URL o el `stb->id`, nada más) |
| Validación | *lookup* en caché; existir = ser válido |
| TTL | **5 segundos** (`config.ini:416` → `tv_tmp_link_ttl = 5`) |
| Revocación | solo por expiración de la caché |
| Vinculación a IP / nodo / canal | **ninguna** |

Un token de 5 s sin firma implica que **el reloj y la caché son la única defensa**: si la caché se
purga, todo se cae; si se comparte el enlace en menos de 5 s, funciona para cualquiera.

### 3.4 Esta es exactamente la pieza que `nexora_api` sustituye

`nexora_api` reemplaza estas 15 líneas por un sistema de otra categoría. Comparativa
(todas las filas de la columna derecha **[V]**):

| Dimensión | Legacy (`chk_flussonic_tmp_link.php`) | `nexora_api` |
|---|---|---|
| Naturaleza del token | `md5()` opaco en caché | JWT firmado — `app/services/stream_auth_service.py:179-199` |
| TTL | 5 s | 60 s (`app/config.py:46`, `playback_token_expire_seconds`) |
| Claims | ninguno | `sub`, `dev`, `ses`, `chn`, `sk`, `node`, `cip`, `jti`, `iat`, `exp`, `type` |
| `aud` / `iss` | — | `aud=nexora-playback`, `iss=nexora-api`, verificados en `stream_auth_service.py:410-411` |
| Gate de acceso | Flussonic consulta el PHP | nginx `auth_request` → `deploy/nginx/nexoraplay.conf:95-107`, aplicado en `:109-127` y `:129-146` |
| Endpoint del gate | — | `app/api/internal/stream_auth.py:67-113` |
| Revocación | no | `SETEX nexora:playback:{jti}` (`stream_auth_service.py:219`) + índice por sesión (`:222-223`) |
| Vinculación a nodo | no | claim `node`, verificado en `stream_auth_service.py:430` |
| Vinculación a IP (token) | no | claim `cip = hash_ip()`, `stream_auth_service.py:433-440`, modo `off/soft/strict` |
| Vinculación a IP (segmentos) | no | **incondicional**: la IP forma parte de la clave Redis del grant (`app/redis_client.py:82-85`) |
| Límite de concurrencia | cabecera `X-Max-Sessions: 1` | ZSET Redis con script Lua atómico (`app/services/connection_service.py:28-38`), tope por plan |
| Aislamiento de superficies | no | `enforce_surface()` — un token admin/refresh no vale para playback |

---

## 4. Tabla de brechas reales

Qué tiene el portal legacy que `nexora_api` **no** tiene hoy.

La columna «existe en legacy» está verificada por la presencia de la clase de dominio y su interfaz
`stbapi` en `...\server\lib\` **[V]**. La columna «existe en Client API» está verificada por
inventario de `app/api/client/` (router en `app/api/client/router.py:6-9`: solo `auth`, `profile`,
`catalog`, `playback` — 13 endpoints en total) **[V]**.

| Capacidad | En legacy | En Client API | Ticket |
|---|---|---|---|
| **EPG rico** | `lib/epg.class.php` — 18 métodos públicos, incl. `getEpgForChannelsOnPeriod()` (:583), `getCurProgramAndFewNext()` (:513), `getWeek()` (:873), `getDataTable()` (:696); tabla `epg` con `time`/`time_to`/`duration`/`name`/`descr` (`db/delta/1-initial_schema.sql:165-175`); ingest programado vía `epg_setting` | **Mock**: `app/api/client/catalog.py:15-20` → `_MOCK_EPG` con **3 canales y 4 programas** hardcodeados; `:41` devuelve `[]` para cualquier otro canal; no hay modelo ni tabla EPG | **`NX-EPG`** (`docs/ROADMAP.md:118`) |
| **VOD** | `lib/vod.class.php`, `lib/video.class.php`, `lib/videocategory.class.php`, `lib/vclubinfo.class.php`, `lib/vclubadvertising.class.php`, `lib/stbapi/vod.class.php` | No (0 coincidencias de `vod` en `app/`) | **`NX-VOD`** (`docs/ROADMAP.md:127`, P4) |
| **Catch-up / timeshift** | `lib/tvarchive.class.php`, `lib/flussonictvarchive.class.php`, `lib/stbapi/tvarchive.class.php`; sección `[tv_archive]` en `config.ini:384` | No (0 coincidencias de `catchup`/`timeshift`) | **`NX-CATCHUP`** (`docs/ROADMAP.md:127`, P4) |
| **nPVR** | `lib/remotepvr.class.php`; interfaz `lib/stbapi/remotepvr.class.php` con **14 acciones** (ver nota 4.1) + `lib/pvr.class.php` local (2 acciones) + `lib/streamrecorder.class.php` | No | **sin ticket** |
| **Radio** | `lib/radio.class.php`, `lib/stbapi/radio.class.php`, `SysEvent::sendPlayRadioChannel()`, recurso REST v2 `restapiresourceradiochannels` | No | **sin ticket** |
| **Karaoke** | `lib/karaoke.class.php`, `lib/karaokemaster.class.php`, `lib/stbapi/karaoke.class.php`, tabla `karaoke` (`1-initial_schema.sql:177+`), recurso REST v2 con búsqueda | No | **sin ticket** |
| **Audio club** | `lib/audioclub.class.php`, `lib/stbapi/audioclub.class.php`, tabla `audio` | No | **sin ticket** |
| **Control parental** | `censored` / `getCensoredList()` / `getCensoredExcludeList()` en `itv.class.php:523-537`, `:976-980`, `:1089`; acciones `addToCensored()` / `delFromCensored()` en `lib/stbapi/itv.class.php`; PIN gestionado en `stb.class.php` (**cifrado**, ver §7) | No (0 coincidencias de `parental`/`censored` en `app/`) | **`NX-PARENTAL`** (`docs/ROADMAP.md:112`) |
| **Favoritos** | Server-side y persistentes: `fav_itv`, `fav_vclub`, `media_favorites`; acciones `setFav()`, `getFavIds()`, `getAllFavChannels()`, `setFavStatus()`; recursos REST v2 `restapiresourcetvfavorites` y `restapiresourcevideofavorites` | Solo **`localStorage`** del web player: `web_player/src/ui/PlayerView.tsx:40` (`nexora.web_player.favorites.v1`), lectura `:79`/`:671-679`, escritura `:173`, toggle `:281-286`. **Cero llamadas a la API** | **sin ticket** |
| **Búsqueda** | REST v2: `restapiresourcevideo.class.php:92-101` (name/o_name/actors/director/year), `restapiresourcekaraoke.class.php:30-37` (name/singer/genre) | No: `GET /catalog/channels` (`app/api/client/catalog.py:23-29`) no acepta parámetros; `ChannelService.list_active()` es un `select` plano ordenado por `number` | **sin ticket** |
| **Comandos al STB** | `lib/sysevent.class.php` — 19 métodos `send*`: mensajes, mensaje+vídeo, mensaje+reboot, `sendUpdateChannels`, `sendUpdateEpg`, `sendUpdateModules`, `sendPlayChannel`, `sendPlayRadioChannel`, `sendCutOff`/`sendCutOn`, `sendReboot`, `sendReloadPortal`, `sendShowMenu`, etc. | No. Existe el canal natural (`POST /profile/devices/heartbeat`, `app/api/client/profile.py:113`) pero solo devuelve el resultado de `DeviceService.heartbeat()`, sin comandos ni ack | **`NX-NOTIF`** (`docs/ROADMAP.md:120`) |
| **Multi-idioma** | **10 locales** compilados en `server/locale/`: `de, el, en, es, it, nl, pl, ru, sk, uk`; clase `lib/l10n.class.php` | No (0 coincidencias de `language`/`locale`/`i18n` en `app/`); mensajes de error hardcodeados en inglés | **sin ticket** |

### 4.1 Correcciones a la tabla de partida

- **nPVR: son 14 acciones, no 15.** La interfaz `...\server\lib\stbapi\remotepvr.class.php` declara
  exactamente 14 métodos **[V]**: `startRecNow`, `startRecDeferred`, `startDeferredRecordOnStb`,
  `startRecordOnStb`, `stopRec`, `stopRecordOnStb`, `stopRecDeferred`, `delRec`, `delRecordOnStb`,
  `createLink`, `updateRecordOnStbEndTime`, `setInternalId`, `getActiveRecordings`, `getOrderedList`.
  La implementación (`lib/remotepvr.class.php`) expone más métodos públicos, pero solo esos 14 son
  contrato. Si se cuenta también el PVR local (`lib/stbapi/pvr.class.php`: `getNewId`,
  `getOrderedList`) el total sube a 16 **[V]**.

- **Multi-idioma: los 10 locales son los del portal prístino.** El portal **desplegado** los reduce a
  uno: `custom.ini:18-20` deja `default_locale = es_ES.utf8` y un único
  `allowed_locales[Español]` **[V]**, sobreescribiendo los 9 de `config.ini:28-37` **[V]**. Los 10
  directorios de traducción siguen en `server/locale/` **[V]**. Es decir: la brecha de i18n frente al
  despliegue real es **de 1 idioma**, no de 10; la capacidad de 10 idiomas existe en el código base.

### 4.2 VOD, karaoke y radio existen de verdad

Los tiles «disponible pronto» del web player son **decisión del cliente**, no una limitación del
portal **[V]**:

- `web_player/src/ui/HomeView.tsx:235-247` — tres botones (`VOD`, `Karaoke`, `Radio`) que invocan
  `onUnavailable(...)`.
- `web_player/src/ui/PlayerView.tsx:419-429` — los mismos tres, cada uno con un
  `pushToast("... disponible pronto.")`.

En el portal, los tres módulos tienen implementación completa (§4). Ahora bien, dos de ellos están
desactivados por configuración y **el tercero no**: `config.ini:168-174` **[V]**:

```ini
disabled_modules[] = vclub
disabled_modules[] = karaoke
disabled_modules[] = cityinfo
disabled_modules[] = horoscope
disabled_modules[] = anecdote
disabled_modules[] = game.mastermind
disabled_modules[] = infoportal
```

**`vclub` (VOD) y `karaoke` sí están en `disabled_modules[]`. `radio` no lo está** **[V]** — la radio
está habilitada en el portal y aun así el web player la muestra como «próximamente». Es la brecha
más barata de cerrar de toda la tabla.

---

## 5. Trampas del modelo de datos legacy

Relevante solo si algún día hay que migrar datos. Todo verificado sobre
`...\db\delta\1-initial_schema.sql` y los deltas posteriores.

### 5.1 Los entitlements no son consultables por SQL

El modelo de suscripción activo por defecto guarda los canales del usuario en
`itv_subscription.sub_ch` como **base64 de un array serializado de PHP**.
`...\server\lib\itvsubscription.class.php` **[V]**:

```php
// :62   lectura
$sub_ch_arr = unserialize(System::base64_decode($sub_ch));
// :109  escritura
$data['sub_ch'] = System::base64_encode(serialize($data['sub_ch']));
```

Implicaciones: no se puede hacer `JOIN`, ni `WHERE canal IN (...)`, ni contar suscriptores por canal
sin des-serializar fila a fila en PHP. Cualquier migración necesita un script de PHP, no un `INSERT
INTO ... SELECT`.

### 5.2 `users.status` está invertido

Esquema: `1-initial_schema.sql:250` → `` `status` tinyint default 0 `` **[V]**.

Semántica real, en `...\server\administrator\users.php` **[V]**:

```php
// :348-358  get_user_color()
if ($status == 0){ $str = '<font color="green">On</font>'; }
else if ($status == 1){ $str = '<font color="red">Off</font>'; }

// :393-399  filtros de listado
case 'on':  add_where($where, " status=0 order by id"); break;
case 'off': add_where($where, " status=1 order by id"); break;
```

y `cut_off_user()` en `:334-346`, que pone `status = 1` al cortar y `0` al reactivar **[V]**.

**`0` = activo. `1` = cortado.** Es lo contrario de la convención habitual y de lo que asume
`nexora_api`. Una migración ingenua deja a todos los clientes activos cortados y viceversa.

> Agravante: dentro del mismo esquema hay otra tabla con columna `status` cuyo default es `1`
> (`1-initial_schema.sql:280`) **[V]**. La convención no es siquiera consistente dentro del fichero.

### 5.3 `users.mac` dejó de ser UNIQUE

Estado inicial: `1-initial_schema.sql:269` → `` UNIQUE KEY `mac` (`mac`) `` **[V]**.

Revertido en `...\db\delta\12-auth.sql:4-5` **[V]**:

```sql
ALTER TABLE `users` DROP INDEX `mac`;
ALTER TABLE `users` ADD KEY `mac` (`mac`);
```

Consecuencia: la MAC **no identifica** a un usuario. Puede haber N filas con la misma MAC. Cualquier
mapeo `MAC → subscriber` durante la migración necesita una regla de desempate explícita (¿la más
reciente por `last_active`? ¿la de mayor `id`?), y esa regla es una decisión de negocio, no técnica.

### 5.4 Todo es MyISAM, sin claves foráneas

Recuento sobre `1-initial_schema.sql` **[V]**:

| Motor | Tablas |
|---|---|
| MyISAM | **78** |
| MEMORY | 1 |
| InnoDB | **0** |

`FOREIGN KEY`: **0 coincidencias en todo `db/delta/`** **[V]**.

Implicaciones: sin transacciones, sin integridad referencial, sin rollback. Una migración parcial no
se puede deshacer y **es normal encontrar filas huérfanas** (favoritos de usuarios borrados,
suscripciones a canales inexistentes). El ETL debe asumir datos inconsistentes por defecto, no
tratarlo como excepción.

### 5.5 Favoritos en tres codificaciones incompatibles

Tres tablas, tres formatos distintos, ninguno intercambiable **[V]**:

| Tabla | Columna | Codificación | Evidencia |
|---|---|---|---|
| `fav_itv` | `fav_ch` (text) | `base64_encode(serialize($array))` | `lib/itv.class.php:432` |
| `fav_vclub` | `fav_video` (text) | `serialize($array)` — **sin base64** | `lib/vod.class.php:394` y `:414`; lectura `lib/user.class.php:188` |
| `media_favorites` | `favorites` | `base64_encode($string_crudo)` — **sin serialize** | `lib/mediafavorites.class.php:19`; lectura `:9` |

Además, tanto `fav_itv` como `fav_vclub` tienen `UNIQUE KEY (uid)`
(`1-initial_schema.sql:313` y `:375`) **[V]**: **una sola fila por usuario** con toda la lista dentro
de un `text`. No hay historial, no hay orden, no hay timestamps por favorito.

---

## 6. Recomendación técnica: usar la REST API v2 como referencia de dominio

El portal incluye una **REST API v2 orientada a recursos** en
`...\server\lib\restapi\v2\` — **54 ficheros, todos PHP en texto plano**, verificado leyendo la
cabecera de cada uno (los 54 empiezan por `<?php`, ninguno está ofuscado con ionCube) **[V]**.

Punto de entrada: `...\api\v2\index.php`, activado por `enable_api_v2` (que está a `true` en
`config.ini:299`) **[V]**:

```php
if (!Config::getSafe('enable_api_v2', false)){ echo "API v2 not enabled"; exit; }
$server = new RESTApiManager(new AuthAccessHandler());
$server->handleRequest();
```

Recursos disponibles como referencia de modelado **[V]**: `tv-channels`, `tv-genres`,
`tv-favorites`, `radio-channels`, `video`, `video-categories`, `video-genres`, `video-favorites`,
`karaoke`, `epg`, `pvr`, `users` — con sus documentos (`*document.class.php`), enlaces
(`*link.class.php`), colecciones, paginación (`restapicollection`, `restapicountcontroller`) y
búsqueda (`restapiresourcevideo.class.php:92-101`).

Ejemplo del valor que aporta: `restapitvchannellink.class.php` **[V]** implementa el mismo caso de
uso que `create_link`, pero **bien**:

- `:18` — `get(RESTApiRequest $request, $parent_id)`
- `:20-38` — valida usuario y pertenencia del canal, `RESTForbidden` en `:37`
- `:62` — `$url = $itv->getUrlByChannelId($parent_id, $link);`
- `:67-69` — normaliza el prefijo de solución con regex, en el **servidor**
- `:71` — devuelve la URL limpia

Es decir: la v2 ya hizo, en 2015, la limpieza de `ffrt` que hoy los clientes hacen a mano.

**Recomendación:** para diseñar los endpoints que faltan (`NX-EPG`, `NX-VOD`, `NX-CATCHUP`,
favoritos, búsqueda), **partir de `server/lib/restapi/v2/` como referencia de dominio** en lugar de
hacer ingeniería inversa sobre el RPC de `load.php`. Razones:

1. Es código legible; el RPC pasa por `dataloader.class.php`, que está cifrado (§7).
2. Está orientado a recursos, igual que la Client API — el mapeo mental es directo.
3. Modela permisos explícitamente (`RESTForbidden`), cosa que el RPC hace dentro del binario cifrado.
4. Da los **nombres de campo y las relaciones** ya validados contra el esquema real.

Advertencia: es referencia de **dominio**, no de **seguridad**. Su modelo de autorización
(`AuthAccessHandler` OAuth sobre el mismo `users`) no debe replicarse; `nexora_api` ya tiene un
modelo mejor.

---

## 7. Límite explícito del análisis: los ficheros cifrados con ionCube

`...\server\lib\core\` contiene **9 ficheros PHP cifrados con ionCube** más su licencia
`core.lic` **[V]**. Verificado leyendo la cabecera de cada uno: todos empiezan por el payload base64
característico de ionCube (`HR+cP...`) en lugar de por `<?php`.

| Fichero | Qué contiene | Impacto en el análisis |
|---|---|---|
| **`stb.class.php`** | **Todo `type=stb`**: handshake, `do_auth`, `get_profile`, gestión del PIN parental, singleton `Stb::getInstance()` | **Alto** |
| **`dataloader.class.php`** | El despacho `type/action` → clase y su ACL | **Alto** |
| `config.class.php` | `Config::get()` / `getSafe()` | Bajo (comportamiento evidente) |
| `mysql.class.php`, `mysqlresult.class.php`, `databaseresult.class.php` | Query builder | Bajo |
| `cache.class.php`, `cacheresult.class.php` | Backend de caché | Medio (afecta al TTL de §3.3) |
| `middleware.class.php` | Middleware de petición | Medio |

Consecuencias concretas, **todas [I]** por construcción:

1. **El contrato exacto del handshake solo puede inferirse por comportamiento.** Sé que existe y qué
   devuelve *aproximadamente*, porque `load_cli_runner.php:58-60` lo invoca y lee
   `$hs_result['token']`, y `:65` construye `array('token' => ..., 'random' => md5(uniqid('', true)))`
   **[V]** — pero eso es el shim reconstruyendo la respuesta, no el contrato original. Qué valida
   `Stb::getInstance()` sobre el token entrante, cómo lo asocia a la MAC, y qué hace `do_auth`, no es
   legible.
2. **El almacenamiento del PIN parental solo puede inferirse por comportamiento.** Se sabe que existe
   (hay acciones `addToCensored`/`delFromCensored` en la interfaz pública y columnas `censored` en
   `itv`), pero **dónde y cómo se guarda y compara el PIN** vive en `stb.class.php` **[I]**.
3. **La ACL del RPC es una caja negra.** Qué acciones requieren qué nivel de privilegio lo decide
   `dataloader.class.php`. Cualquier afirmación del tipo «esta acción está protegida» sobre el RPC
   legacy es, estrictamente, **[I]**.

**Implicación práctica para la migración:** no se debe intentar reproducir el handshake ni el modelo
de PIN legacy. Hay que **especificarlos de nuevo** en la Client API. `nexora_api` ya lo hizo para la
autenticación (`app/api/client/auth.py`) y `NX-PARENTAL` (`docs/ROADMAP.md:112`) lo hará para el PIN
— explícitamente sobre `channels.censored`, es decir, rediseñando en lugar de portar.

---

## 8. Bloqueantes para escribir los clientes nativos

Los tres se están corrigiendo **en esta misma rama** (`feat/client-api-blockers`).

> **Estado observado:** los tres aparecen **ya corregidos en el árbol de trabajo pero sin
> commitear** — `HEAD` sigue en `ebb166f` **[I]**. El árbol cambia en vivo mientras se escribe este
> documento. Lo que sigue describe el problema y el arreglo tal como se ven en disco; considérese
> **en curso** hasta que haya commit y tests verdes.

### 8.1 El reissue de playback no firmaba la URL

- **Endpoint:** `app/api/client/playback.py:127-167`, `GET /api/client/playback/{channel_id}` **[V]**.
- **Flag:** `app/config.py:41` → `signed_url_enforce: bool = False`
  (`True` → `playback_url` lleva `?token=` y `/stream/*` lo exige) **[V]**. En producción está a
  `true` (`docs/ROADMAP.md:21`, `deploy/RUNBOOK_PRODUCTION_P0.md:11`) **[V]**.
- **El bug:** el reissue devolvía `_resolve_playback_url(...)` sin pasar por el helper de firma
  `_maybe_sign()` (`playback.py:30-39`), que `POST /authorize` sí usa (`playback.py:123`) **[V]**.
- **El impacto:** con `SIGNED_URL_ENFORCE=true`, **toda URL reemitida salía sin `?token=` y el gate
  nginx la rechazaba**. Como el cliente renueva cada ~45 s, la reproducción moría al primer
  reissue **[I]**.
- **Estado:** `playback.py:166` ya usa `playback_url=_maybe_sign(base_url, result.token)` **[V]**.

### 8.2 Los tokens reemitidos salían sin `cip` / `node`

- **Emisor:** `StreamAuthService.create_token()` — `app/services/stream_auth_service.py:501-560` **[V]**.
- **El bug:** `create_token()` no recibía `channel_key`, `node` ni `ip`; llamaba a `_issue_jwt` solo
  con `stream_key=channel_id`, produciendo tokens **sin `cip`, sin `node` y sin `chn`** **[V]**.
- **El impacto — y es el punto importante:** el validador **exime** a los tokens que carecen de esos
  claims. `stream_auth_service.py:433-436` **[V]**:

  ```python
  # 7. IP binding (C-PROD-2). off → skip; soft → warn; strict → 403 on mismatch.
  mode = settings.playback_ip_binding_mode
  token_cip = payload.get("cip")
  if mode != "off" and token_cip and client_ip:
  ```

  El `and token_cip` significa que **un token sin `cip` salta el binding de IP incluso en
  `strict`**. Análogamente, `:430` (`if node is not None and payload.get("node") not in (None, node)`)
  hace que **un token sin `node` valga para cualquier nodo** **[V]**.

  Como el reissue es la ruta dominante (renovación cada ~45 s), el bug era un **bypass silencioso**:
  activar el IP-binding de PROD-Fase 2D no habría tenido ningún efecto sobre el tráfico real.
- **Estado:** `create_token()` ya acepta `channel_key`, `node` e `ip` (`:507-509`) y los propaga a
  `_issue_jwt` (`:552-554`); el endpoint los pasa desde `playback.py:151-158` **[V]**.
- **Nota:** la exención del validador **sigue en pie por diseño** (compatibilidad con STB legacy,
  documentada en `stream_auth_service.py:519-526`) **[V]**. El fix cierra el emisor, no el validador.
  Antes de poner `PLAYBACK_IP_BINDING_MODE=strict` conviene decidir si esa exención debe endurecerse.

  Corrección de nomenclatura: **el literal `PROD-2D` no existe en el repo** **[V]**. Lo que hay es
  `PROD-Fase 2D` (`docs/ROADMAP.md:40`, `TODO_NEXT.md:31`,
  `deploy/RUNBOOK_PRODUCTION_P0.md:12` y `:136`) y `C-PROD-2` (`stream_auth_service.py:433`).

### 8.3 El `device_secret` se perdía en el login

- **Flag:** `app/config.py:42` → `device_secret_enforce: bool = False` (`True` → el playback exige un
  device activado con secreto verificado) **[V]**. Consumidores: `app/services/device_service.py:139`
  y `app/services/stream_auth_service.py:110` **[V]**.
- **El bug:** el secreto se generaba (`device_service.py:125`), se hasheaba (`:138`) y se colgaba del
  objeto como atributo transitorio (`:143`), pero **el login no lo devolvía**. La ruta
  `POST /profile/devices/register` sí lo hacía (`app/api/client/profile.py:89`); la de login, no.
- **El impacto:** todo dispositivo registrado **en el login** quedaba permanentemente incapaz de
  activarse. Con `DEVICE_SECRET_ENFORCE=true` esos dispositivos no podrían reproducir nunca — lo que
  **impide activar el flag** y por tanto **bloquea `NX-DEV`** (`docs/ROADMAP.md:109`) **[V]**.
- **Estado:** `client_auth_service.py:167-174` ya devuelve un `ClientLoginResult` con
  `device_secret`; el endpoint lo propaga en `app/api/client/auth.py:50`; el schema lo declara en
  `app/schemas/client.py:32` **[V]**. La dataclass conserva `__iter__` (`:51-62`) para no romper el
  desempaquetado histórico de 5 valores, y redacta el secreto en `__repr__` (`:64-72`) **[V]**.

### 8.4 Hallazgo adicional (no estaba en el encargo)

El mismo trabajo introdujo `playback_reissue_entitlement_check: bool = False`
(`app/config.py:52-56`) **[V]**. Con el valor por defecto, **el reissue no reevalúa el entitlement**:
un plan que pierde un canal a mitad de sesión sigue reproduciéndolo. Merece decisión explícita antes
de producción.

---

## 9. Patrón de integración para clientes nativos

Destilado del web player, que es el único cliente que ya consume la Client API correctamente.
Todos los puntos **[V]** salvo donde se indique.

### 9.1 Identidad de dispositivo: el `device_id` sustituye a la MAC

- Generación y persistencia: `web_player/src/auth/tokenStore.ts:50-56` — `getDeviceId()` lee la clave
  `nexora.web_player.device_id.v1` de `localStorage`; si no existe genera `` `web-${randomId()}` ``.
  `randomId()` (`:14-18`) usa `crypto.randomUUID()` con fallback a 16 bytes aleatorios en hex.
- Se envía en: **login** (`web_player/src/api/nexoraClient.ts:41`, junto a `device_type`, `model`,
  `brand`, `app_version`, `os_version` en `:42-46`), `/playback/authorize` (`:117`), **reissue** como
  query param (`:124-126`) y **heartbeat** (`:132`).
- **No** se envía en refresh: el body de `/auth/refresh` es solo `{refresh_token}`
  (`nexoraClient.ts:65-72`, backend `app/api/client/auth.py:51-58`).
- El login registra el device con `raise_on_limit=False`
  (`app/services/client_auth_service.py:79-93`): superar el cupo **no rompe el login**, devuelve
  `device_registration = "limit_reached"`. El cliente debe leer ese campo y avisar al usuario.

**Para un cliente nativo:** generar un **UUID una sola vez** y persistirlo en **almacenamiento
seguro** (Keychain en iOS, Keystore/EncryptedSharedPreferences en Android) — no en preferencias en
claro y no derivado de identificadores del sistema (IDFV/ANDROID_ID cambian con reinstalaciones y
restauraciones, y arrastran problemas de privacidad). *Recomendación de diseño, no verificación.*

### 9.2 Refresh token: un solo uso, exige single-flight

- **Rotación atómica en backend:** `app/services/client_auth_service.py:129` →
  `stored = await self.redis.getdel(key_client_refresh(jti))`. Si no hay valor, `:130-131` lanza
  `unauthorized("Refresh token has been revoked or already used")`. `GETDEL` es atómico: **consumo
  único garantizado**.
- Antes de consumirlo se valida la superficie (`:119-120`, `enforce_surface(...)`): un token de admin
  o de playback es rechazado.
- TTLs: access **24 h** (`app/config.py:65`), refresh **90 d** (`app/config.py:66`).
- **Single-flight en el cliente:** `web_player/src/api/nexoraClient.ts:21` (campo `refreshPromise`),
  `:59` (`if (this.refreshPromise) return this.refreshPromise;`), `:77-79` (limpieza en `.finally`).
- Disparadores: proactivo por *skew* (`:139-146`, `tokenRefreshSkewSeconds` = 120 s por defecto,
  `web_player/src/api/config.ts:33-36`) y reactivo ante 401 **con un único reintento** (`:171-182`).

**Para un cliente nativo — crítico:** dos refrescos concurrentes **matan la sesión** (el segundo
encuentra la clave ya consumida y recibe 401). Hace falta un mutex **global al proceso**, no por
pantalla ni por instancia de cliente HTTP. Nota: el single-flight del web player es en memoria y
**no cubre múltiples pestañas** — un cliente nativo con varios procesos (widget, extensión de
reproducción en segundo plano, Picture-in-Picture) tiene el mismo riesgo y debe resolverlo
explícitamente.

### 9.3 Lo que mantiene viva la reproducción es el heartbeat, no el token

Este es el error conceptual más caro de cometer al escribir un cliente nativo.

- **El token de playback dura 60 s** (`app/config.py:46` → `playback_token_expire_seconds: int = 60`,
  aplicado en `stream_auth_service.py:183`). Autoriza una petición de manifest concreta. Nada más.
- **El slot de concurrencia vive en un ZSET de Redis con 180 s de ventana**:
  `app/services/connection_service.py:60-67` → `ZADD nexora:active_conns:{subscriber_id}` con
  `score = now + 180` y `EXPIRE key 240`. El TTL de 180 s es `heartbeat_ttl_seconds`
  (`app/config.py:45`).
- **El heartbeat es lo que renueva ese slot:** `POST /api/client/profile/devices/heartbeat`
  (`app/api/client/profile.py:113-132`) → `app/services/device_service.py:165-204`, que hace
  `SETEX nexora:heartbeat:{device_id} 60` (`:180`) y `extend_connection()` (`:183-184`).
- **Y el slot es condición de vida para todo lo demás:** `create_token()` exige slot activo
  (`stream_auth_service.py:538`, si no → 403 *"call /auth/play first"*) y `validate_stream_request()`
  también (`:421`).
- Intervalo del cliente: **45 000 ms** (`web_player/src/api/config.ts:25-28`, `web_player/.env:12`),
  con guard `inFlight` (`web_player/src/heartbeat/heartbeatRunner.ts:22-24`, `:33-35`), arrancado al
  empezar a reproducir (`PlayerView.tsx:247`, `:261-266`).

**Conclusión operativa:** sin heartbeat, el ZSET expira a los 180 s (tres latidos perdidos) y **ni el
reissue ni el gate funcionan**, por muy fresco que sea el JWT. El heartbeat debe seguir latiendo en
segundo plano y reanudarse inmediatamente al volver a primer plano.

Renovación del token en el cliente: `PlayerView.tsx:183-193` programa el renew a
`max(5s, (expires_in - skew) * 1000)` → con TTL 60 s y skew 15 s, **~45 s**; `:195-219` hace el
reissue, reconstruye la URL y llama a `hls.reload`, con **fallback a `/authorize`** si el reissue
falla (`:207-211`).

### 9.4 Los segmentos se autorizan por un grant de Redis atado a la IP — siempre

- **Gate nginx:** `deploy/nginx/nexoraplay.conf:95-107` define `location = /__stream_auth`
  (`internal`, `proxy_pass` al validador, cabeceras `X-Playback-Token` y `X-Real-IP`); se aplica con
  `auth_request` en `:109-127` (`/stream/ec-main/`) y `:129-146` (`/stream/co-main/`), con
  `set $stream_token $arg_token;` (`:112`, `:131`).
- **Validador:** `app/api/internal/stream_auth.py:67-113`. Con token → `validate_stream_request()`
  (`:87-89`) y **siembra del grant** (`:104-105`). Sin token (caso segmento) → solo pasa si ya hay
  grant (`:111-112`).
- **Clave del grant:** `app/redis_client.py:82-85` →
  `nexora:stream_grant:{node}:{stream_key}:{ip_hash}`.
- **TTL y ventana deslizante:** siembra con 180 s (`stream_auth_service.py:454-470`,
  `stream_auth_cache_ttl_seconds` en `app/config.py:49`); cada segmento hace `redis.expire(...)`
  (`:472-500`, concretamente `:499`) → **ventana deslizante**.

**El punto que define el diseño del cliente:**

| | ¿Condicionado por `PLAYBACK_IP_BINDING_MODE`? |
|---|---|
| IP-binding del **TOKEN** (claim `cip`) | **Sí** — `stream_auth_service.py:433-440`; con `off` (el default, `app/config.py:50`) se salta por completo |
| IP-binding del **GRANT** (segmentos) | **No — incondicional.** La IP es parte de la *clave* Redis (`stream_auth.py:105` y `:111`, ambas con `hash_ip(client_ip)`), sin ninguna lectura de `playback_ip_binding_mode` |

Es decir: **aunque `PLAYBACK_IP_BINDING_MODE=off`, los segmentos solo se sirven a la misma IP que
pidió el manifest.**

**Consecuencia para el cliente nativo:** un cambio de red rompe la reproducción. Escenarios reales:

- WiFi ↔ LTE/5G (el caso obvio),
- **dual-stack IPv4/IPv6** — el sistema puede cambiar de familia sin que cambie la «red» percibida,
- **CGNAT** — la IP pública puede rotar sin ninguna acción del usuario,
- VPN/Private Relay activándose o desactivándose.

El cliente **debe re-autorizar proactivamente al detectar el cambio** (`NWPathMonitor` en iOS,
`ConnectivityManager.NetworkCallback` en Android), sin esperar al fallo de segmento. Existe un
fallback de continuidad (`stream_auth.py:90-101`, gobernado por `stream_grant_token_fallback = True`
en `app/config.py:62`) pero opera en la dirección contraria: rescata un token caducado si el grant
sigue vivo, no un grant perdido.

> Detalle a vigilar: `hash_ip(None)` produce un hash estable del vacío
> (`app/core/security.py:31-35`). Si nginx no envía `X-Real-IP`, **todos los clientes comparten el
> mismo grant** **[V]**. Es una precaución de despliegue, no del cliente.

### 9.5 Sin cookies; la `playback_url` se pasa tal cual

- **No se usan cookies en ningún punto del flujo** — 0 coincidencias de `cookie`/`credentials:` en
  `web_player/src`, `app/main.py`, `app/api` y `deploy` **[V]**. La API usa `Authorization: Bearer`
  (`nexoraClient.ts:162-164`); `/stream/*` usa `?token=` en la URL (`nexoraplay.conf:112`).
- **La `playback_url` firmada va directa al reproductor**, sin inyectar cabeceras:
  `web_player/src/player/playbackUrl.ts:9` (`if (playback.playback_url) return playback.playback_url;`)
  y el resultado se pasa a `hls.load`/`hls.reload` en `PlayerView.tsx:240` y `:202` **[V]**.
- Esto es una **ventaja deliberada para clientes nativos**: `AVPlayer` (iOS) y `ExoPlayer` (Android)
  reproducen HLS directamente desde la URL sin necesidad de un *loader* personalizado ni de un cookie
  jar compartido con la capa HTTP.
- La URL **nunca lleva credenciales de Flussonic** (`app/api/client/playback.py:49-68`) **[V]**.

### 9.6 `playbackRenewal.ts` es código muerto — no usarlo como referencia

`web_player/src/player/playbackRenewal.ts:13` declara `export class PlaybackRenewal`. Grep de
`playbackRenewal`/`PlaybackRenewal` sobre todo `web_player/` (excluyendo `node_modules`, incluyendo
`.ts`, `.tsx`, `.js`, `.json`, `.html`): **una única coincidencia, la propia declaración. Cero
imports.** **[V]**

La renovación real está reimplementada *inline* en `PlayerView.tsx:183-219` y **no es equivalente**:
el módulo muerto **carece del fallback a `/authorize`** que sí tiene la versión viva (`:207-211`)
**[V]**. Tomarlo como referencia produciría un cliente que muere en cuanto falla un reissue.

### 9.7 Checklist del patrón

1. UUID de dispositivo generado una vez, en almacenamiento seguro. Enviarlo en login,
   `/playback/authorize`, reissue y heartbeat — **nunca** en refresh.
2. Persistir access (24 h) + refresh (90 d). Refresco proactivo con skew ~120 s y reactivo ante 401
   **con un solo reintento**. **Mutex global de proceso** — el refresh es de un solo uso.
3. Al reproducir: `POST /playback/authorize` → abre slot ZSET y sesión IPTV → usar `playback_url`
   **tal cual** en el reproductor HLS.
4. **Arrancar el heartbeat cada 45 s** a `/profile/devices/heartbeat`. Es lo que mantiene viva la
   sesión, no el JWT. Continuar en segundo plano; reanudar al volver a primer plano.
5. Renovar el token a `expires_in - 15 s` (~45 s) vía `GET /playback/{channel_key}?device_id=` y
   recargar la fuente HLS. **Fallback a `/authorize`** si el reissue falla.
6. Sin cookies. Bearer para la API, `?token=` en la URL de stream cuando `SIGNED_URL_ENFORCE=true`.
7. **Vigilar los cambios de ruta de red y re-autorizar proactivamente.** El grant de segmentos está
   atado a la IP siempre.
8. Leer `device_registration` de la respuesta de login: `"limit_reached"` no rompe el login pero sí
   la reproducción posterior.

---

## 10. Orden de migración propuesto

### 10.1 La restricción dura

`docs/ROADMAP.md:129` **[V]**:

> **`NX-APPS` (Android TV / Mobile / iOS) está BLOQUEADO** por restricción del proyecto: no se
> empieza hasta que **playback, sesiones y observabilidad** estén estables (⇒ requiere P0 + P1 + P2.1
> cerrados).

y `docs/ROADMAP.md:151` **[V]**: *«**Regla dura:** nada de apps nativas hasta cerrar P0+P1+P2.1.»*

Composición de esos bloques **[V]**:

| Bloque | Contenido | Estado observado |
|---|---|---|
| **P0.1** | PROD-Fase 2D: IP-binding del playback token, `off → soft → strict` (`ROADMAP.md:40-46`) | Único ítem vivo de M1. **Depende del fix §8.2** |
| **P0.2** | Hardening del grant (`:48`) | Hecho en código |
| **P0.3** | Deuda de versionado, merge PR #9→#10→#11 (`:54`) | Pendiente |
| **P0.4** | Nodo Flussonic `co-main` caído + alerta (`:60`, `NX-FLU`) | Pendiente |
| **P1.1** | Stress tests de playback (`:68`) | Pendiente |
| **P1.2** | Concurrencia atómica `NX-CONC` vía Lua (`:77`) | Ya implementado (commit `6de66d9`); ROADMAP desactualizado |
| **P1.3** | Entorno de STAGING real (`:81-85`) | Bloqueado por ZeroTier `ACCESS_DENIED` |
| **P2.1** | Observabilidad extendida `NX-MON` (`:91-96`) | Mayormente cubierto por el commit `1dab7e4`; ROADMAP desactualizado |

> Nota de higiene: `docs/ROADMAP.md:3` está fechado antes de varios commits ya en la rama, y `:23`
> menciona «Alembic **006**» cuando la cabeza real es **007** (`migrations/versions/`, cadena lineal
> 001→007 sin ramas) **[V]**. Conviene reconciliar el ROADMAP antes de usarlo como criterio de
> desbloqueo, porque hoy **infravalora el progreso real**.

### 10.2 Orden propuesto

**La ruta para salir del legacy pasa primero por el backend, no por las apps.** Escribir los clientes
nativos contra una Client API que aún no tiene EPG, VOD ni favoritos obliga a reescribirlos después.

**Fase A — Cerrar los bloqueantes de cliente (esta rama).** Los tres de §8. Sin ellos no se puede
activar `SIGNED_URL_ENFORCE`, ni `PLAYBACK_IP_BINDING_MODE=strict`, ni `DEVICE_SECRET_ENFORCE`; es
decir, no se puede endurecer nada. Además `NX-DEV` está bloqueado por §8.3.

**Fase B — Desbloquear `NX-APPS`.** Cerrar P0 (con 2D en `strict`, que §8.2 acaba de hacer
significativo), P1 y P2.1 según el ROADMAP. Reconciliar antes el ROADMAP con lo ya implementado.

**Fase C — Paridad de catálogo, por orden de coste/beneficio.** Sin esto, un cliente nativo no puede
sustituir al legacy aunque exista:

1. **`NX-EPG`** — la brecha más visible. Hoy son 3 canales y 4 programas mock
   (`catalog.py:15-20`). Es lo primero que nota un usuario. Referencia de dominio:
   `restapiresourceepg` + `restapiepgdocument` en la v2 (§6).
2. **Favoritos server-side** — sin ticket, coste bajo, elimina la divergencia entre dispositivos.
   Referencia: `restapiresourcetvfavorites`. **No portar las codificaciones legacy** (§5.5).
3. **Búsqueda** — sin ticket, coste bajo. Referencia:
   `restapiresourcevideo.class.php:92-101`.
4. **Radio** — sin ticket. Ya está habilitada en el portal (`radio` **no** está en
   `disabled_modules[]`, §4.2) y el web player la anuncia como «próximamente». Coste bajo,
   inconsistencia visible.
5. **`NX-PARENTAL`** — requiere rediseño, no portabilidad: el PIN legacy vive en código cifrado (§7).
6. **`NX-NOTIF`** — el heartbeat ya es el canal natural; falta el payload de comandos y el ack.

**Fase D — Módulos grandes.** `NX-VOD` y `NX-CATCHUP` (P4). Aquí sí conviene modelar desde la v2
(`restapiresourcevideo`, `restapiresourcepvr`, `restapitvarchive`) en lugar de improvisar.

**Fase E — Apagar el legacy.** Solo cuando C esté cerrada. Dado §1.3, **el portal desplegado no
aporta control de acceso alguno**: mantenerlo vivo en paralelo no es una red de seguridad, es
superficie de ataque. Cuanto antes se apague, mejor.

**Lo que explícitamente NO hay que migrar** (§1, §5, §7):

- Entitlements — no existen en el despliegue actual.
- El handshake y el modelo de PIN legacy — ilegibles, hay que especificarlos de nuevo.
- Las codificaciones de favoritos — tres formatos incompatibles.
- El esquema de token temporal de 5 s — ya sustituido, y por algo mejor.
- La semántica invertida de `users.status` — corregir en el ETL, no propagar.

---

## Anexo: índice de evidencia

### Portal desplegado — `E:\WEBSITE\NEXORA\nexora_portal-5.0.0-v1\nexora_portal-5.0.0-r2\`

| Ruta | Líneas | Qué demuestra |
|---|---|---|
| `server\custom.ini` | 18-20, 25-29 | Locales reducidos a 1; derechos desactivados |
| `server\config.ini` | 28-37, 168-174, 222-223, 299, 416 | 9 locales base; `disabled_modules`; `log_mac_clones=false`; `enable_api_v2=true`; `tv_tmp_link_ttl=5` |
| `server\load.php` | 25-52, 69 | Delegación a subproceso CLI |
| `server\load_cli_runner.php` | 2, 33-41, 45-76 | Bypass de ionCube; `create_link` sin auth; handshake forjado |
| `server\lib\itv.class.php` | 70, 126-137, 192-283, 332-350, 507-544, 1141-1149, 1192-1196 | Contrato de `createLink`; token temporal; gate de suscripción |
| `server\lib\itvsubscription.class.php` | 62, 109 | `base64(serialize())` de entitlements |
| `server\lib\mediafavorites.class.php` | 9, 19 | 3.ª codificación de favoritos |
| `server\lib\vod.class.php` | 394, 414 | 2.ª codificación de favoritos |
| `server\lib\user.class.php` | 188 | Lectura de `fav_vclub` |
| `server\lib\sysevent.class.php` | 16-185 | 19 comandos al STB |
| `server\lib\epg.class.php` | 41-1046 | 18 métodos de EPG |
| `server\lib\streamserver.class.php` | 182, 188 | `max_sessions` es por streamer |
| `server\lib\watchdog.class.php` | 24 | Clones de MAC solo se loguean |
| `server\lib\core\*.php` | — | 9 ficheros ionCube |
| `server\lib\stbapi\remotepvr.class.php` | — | 14 acciones nPVR |
| `server\lib\stbapi\itv.class.php` | — | 16 acciones ITV, incl. `addToCensored` |
| `server\lib\restapi\v2\` | 54 ficheros | REST v2 en texto plano |
| `server\lib\restapi\v2\restapitvchannellink.class.php` | 18-71 | Equivalente correcto de `create_link` |
| `server\api\chk_flussonic_tmp_link.php` | 1-15 | Callback de Flussonic completo |
| `server\administrator\users.php` | 334-358, 393-399 | `status` invertido |
| `api\v2\index.php` | 1-16 | Entrada de la REST v2 |
| `db\delta\1-initial_schema.sql` | 165-175, 228-283, 307-314, 369-377 | Esquema; 78 MyISAM; 0 FK |
| `db\delta\12-auth.sql` | 4-5 | `mac` deja de ser UNIQUE |
| `server\locale\` | 10 dirs | de, el, en, es, it, nl, pl, ru, sk, uk |

### Backend — `E:\WEBSITE\nexora_api\`

| Ruta | Líneas | Qué demuestra |
|---|---|---|
| `app\config.py` | 41, 42, 45, 46, 49, 50, 52-56, 61, 62, 65, 66 | Flags y TTLs |
| `app\api\client\router.py` | 4, 6-9 | Solo 4 sub-routers |
| `app\api\client\auth.py` | 28-51, 51-58, 67-79 | login/refresh/logout |
| `app\api\client\profile.py` | 32, 54, 64, 89, 93, 113-132 | Perfil, devices, heartbeat |
| `app\api\client\catalog.py` | 1, 15-20, 23-29, 41 | EPG mock; catálogo sin búsqueda |
| `app\api\client\playback.py` | 30-39, 49-68, 71-124, 127-167 | Authorize y reissue |
| `app\api\internal\stream_auth.py` | 28-38, 67-113 | Gate `auth_request` |
| `app\services\stream_auth_service.py` | 110, 179-199, 219-223, 385-450, 454-500, 501-560 | Claims, validación, grant |
| `app\services\client_auth_service.py` | 79-93, 111-174 | Login, rotación de refresh, `device_secret` |
| `app\services\connection_service.py` | 28-38, 44-67, 98-105 | ZSET de concurrencia |
| `app\services\device_service.py` | 125-143, 165-204 | `device_secret`, heartbeat |
| `app\redis_client.py` | 82-85 | Clave del grant, con IP |
| `app\core\security.py` | 31-35 | `hash_ip()` |
| `deploy\nginx\nexoraplay.conf` | 95-152 | Gate nginx |
| `migrations\versions\` | 001-007 | Cadena lineal, head 007 |
| `docs\ROADMAP.md` | 3, 21, 23, 40-46, 109-129, 151 | Tickets y bloqueo de `NX-APPS` |
| `web_player\src\auth\tokenStore.ts` | 4, 14-18, 50-56 | `device_id` |
| `web_player\src\api\nexoraClient.ts` | 21, 41-46, 59, 65-79, 117-146, 162-182 | Single-flight, cabeceras |
| `web_player\src\api\config.ts` | 25-28, 33-36 | Intervalo de heartbeat, skew |
| `web_player\src\heartbeat\heartbeatRunner.ts` | 22-35 | Guard `inFlight` |
| `web_player\src\ui\PlayerView.tsx` | 40, 79, 173, 183-219, 240-266, 281-286, 419-429, 671-679 | Renovación, favoritos, tiles |
| `web_player\src\player\playbackUrl.ts` | 9, 17-32 | URL directa al reproductor |
| `web_player\src\player\playbackRenewal.ts` | 13 | Código muerto |
| `web_player\src\ui\HomeView.tsx` | 235-247 | Tiles «próximamente» |

### Clientes legacy

| Ruta | Líneas | Qué demuestra |
|---|---|---|
| `E:\WEBSITE\nexora_app\lib\stalker_api.dart` | 135-154 | Lee `js['url']`, fallback `ffrt` |
| `E:\WEBSITE\nexora_ios\NexoraApp\Services\StalkerAPI.swift` | 74-89 | Lee `js["url"]`, fallback `ffrt` |
| `...\nexora_portal-...\c\player.js` | 2492-2556 | Cliente MAG lee `cmd` |
| `...\nexora_portal-...\c\xpcom.common.js` | 763 | Desenvoltura de `js` |

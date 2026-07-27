# Reproducción en navegador — diagnóstico y opciones

_2026-07-26_

Documento de decisión. Recoge por qué 18 de 35 canales no reproducen en navegador,
qué se midió, y qué opciones reales existen con sus costes. Todo lo marcado como
**medido** se verificó contra producción esta noche.

---

## 1. Son dos problemas distintos, no uno

### Problema A — 18 canales de TelecoWR: GOP mal formado

Entran por SRT desde `179.48.54.226` y `38.252.222.154`. Son **H.264 1080p**, que es
correcto, pero con **GOP de ~11 segundos y un único IDR**.

Medido comparando unidades NAL de un segmento HLS real:

| | TelecoWR (GOLDEN PLUS) | Fuente correcta (TeleNostalgia) |
|---|---|---|
| Fotogramas en el segmento | 330 | 150 |
| IDR | **1** | **10** |
| Intervalo entre IDR | ~11 s | 0,5 s |
| ¿Primer fotograma es IDR? | **NO** | SÍ |

Secuencia al inicio del segmento de TelecoWR:

```
AUD → SPS SPS PPS PPS SPS PPS SPS PPS → SEI → NAL 1 (slice no-IDR)
```

Error literal de Chrome:

```
PIPELINE_ERROR_DECODE: Failed to send video packet for decoding:
{timestamp=3803933 duration=16688 size=13620 is_key_frame=0 encrypted=0}
```

**Causa raíz:** la especificación W3C Media Source Extensions inicializa la bandera
`need random access point` a `true` y **descarta todo fotograma que no sea punto de
acceso aleatorio** hasta encontrar uno. Se reinicia al comienzo de cada segmento.
No es configurable: es el estándar que implementan todos los navegadores.

La documentación de Flussonic coincide: *"Segments in HLS always start with a
keyframe"*.

### Problema B — 80 canales de satélite vía Astra: códec MPEG-2

Astra recibe 7 transponders DVB-S y descifra con 6 softcams. Sus 80 canales
habilitados salen a `udp://239.0.x.x:1234` en **MPEG-2**, que es lo nativo de DVB.

Medido en el PMT de un segmento real de GAMATV:

```
stream_type=0x02  →  MPEG-2 video   (720x540)
stream_type=0x04  →  MPEG-2 audio (MP2)
```

Soporte en Chrome, medido:

| Códec | `MediaSource.isTypeSupported` | `canPlayType` |
|---|---|---|
| MPEG-2 (`mp2v`) | **false** | **""** |
| H.264 + AAC | true | `probably` |

El propio reproductor de Flussonic lo confirma en pantalla:
**"Could not play mp2v codec"**.

### Por qué VLC reproduce todo

VLC usa **libavcodec (FFmpeg)**, un decodificador completo en software: arranca a
mitad de GOP descartando hasta el siguiente IDR, y decodifica MPEG-2 sin problema.

**"En VLC funciona" es cierto y no demuestra nada** sobre la validez para OTT.
Cualquier reproductor basado en FFmpeg —VLC, MPV, `media_kit`— se comporta igual.

---

## 2. Restricciones reales

Medido en `181.78.246.211` (host `ser-esmeraldas`):

| | |
|---|---|
| Flussonic | v23.11.1, **79 streams**, `transcoder = True` |
| Dispositivos de transcodificación | **`[{name: "CPU Encoder", type: "cpu"}]` — sin GPU** |
| Carga | `cpu_usage 63-68%`, **`scheduler_load 100`**, `memory 36%` |
| Licencia | `NulledBySlaSerXDEV` — **crackeada, sin soporte del fabricante** |
| Cesbo Astra | commit 62b0c18e (2021). **No transcodifica** — es multiplexor DVB/IP |
| Ruta crítica | Flussonic hace `pushes: udp://235.2.2.2:*` → **moduladores → FTTH** |

Transcodificar en CPU 18 canales de 1080p son del orden de **18-36 núcleos**;
los 80 de satélite en SD, unos **40**. Con el planificador ya al 100% y el FTTH
colgando de la misma máquina, **no es viable**.

---

## 3. Opciones, ordenadas por relación coste/beneficio

### Opción 1 — Corregir el GOP en origen (TelecoWR) · COSTE CERO

Arregla el **Problema A** (18 canales) sin comprar nada ni tocar la cabecera. Los
feeds ya son H.264 1080p; solo falta un parámetro de encoder:

- Intervalo de fotograma clave **2 s** (60 fotogramas a 29,97 fps)
- **GOP cerrado con IDR** al inicio de cada GOP
- Dejar de duplicar SPS/PPS

En x264: `keyint=60 min-keyint=60 scenecut=0`.

Solicitud técnica ya redactada con toda la evidencia:
`E:\WEBSITE\SOLICITUD_TECNICA_TELECOWR.md`

**Es la única opción que no cuesta hardware.** Debe intentarse primero.

### Opción 2 — GPU en el servidor de Flussonic · COSTE MEDIO

Resuelve **A y B**. La documentación de Flussonic confirma soporte de **NVENC, QSV
y Jetson**; el parámetro es `transcoder ... vcodec=h264 acodec=aac` con `Device`
para seleccionar la tarjeta.

Una NVIDIA con NVENC mueve del orden de una decena de flujos 1080p sin cargar la
CPU, porque el encoder es un bloque de silicio dedicado e independiente de los
núcleos CUDA.

⚠️ **Dos condicionantes serios:**
- Instalar una GPU implica **parar la máquina que alimenta el FTTH**. Requiere
  ventana de mantenimiento planificada.
- Con la licencia crackeada **no hay soporte del fabricante** si el transcoder no
  arranca o falla. Habría que regularizar la licencia antes de depender de esto.

### Opción 3 — Servidor de transcodificación aparte · COSTE MEDIO-ALTO, RIESGO MÍNIMO

Una máquina nueva que lee de Flussonic o de Astra, transcodifica y publica HLS
limpio hacia el edge de Nexora.

**Ventaja decisiva:** no se toca la cabecera de producción en ningún momento. Es
el aislamiento que el negocio necesita — el FTTH nunca se ve afectado.

Coste: hardware nuevo, y ancho de banda si no está en la misma red.

### Opción 4 — Ruta de clientes nativos · COSTE EN DESARROLLO, CERO EN HARDWARE

`media_kit` (usado por `nexora_app`) es **libmpv, o sea FFmpeg**. Reproduce
**todo**: los 18 de GOP largo y los 80 en MPEG-2, sin transcodificar nada.

Es la única vía que entrega el catálogo completo **hoy** y sin comprar nada.

⚠️ **Bloqueante:** `nexora_app` habla con el portal Stalker, no con la Client API.
Necesita la migración (`NX-APPS`, estimada en 2-3 semanas y bloqueada por el
roadmap hasta cerrar P0+P1+P2.1).

Nota: **iOS nativo NO sirve** para esto. `AVPlayer` es estricto como MSE.

### Opción 5 — Entrega por M3U / Xtream a clientes FFmpeg · COSTE BAJO

VLC, TiviMate, IPTV Smarters y OTT Navigator usan FFmpeg: reproducen todo hoy.

Lista M3U ya generada: `E:\WEBSITE\nexora_canales_mpegts.m3u` (34 canales).

⚠️ **Tal como está, no tiene autorización de ningún tipo**: quien la reciba tiene
acceso permanente sin cuenta ni caducidad, y puede repartirla. Para pruebas
internas vale; para clientes, no.

La versión entregable es el ticket **`NX-XC`** (compatibilidad Xtream con
autorización). No está implementado; su v1 son cuatro acciones
(`user_info`/`server_info`, `get_live_categories`, `get_live_streams`, `get.php`).

### Opción 6 — Decodificación en el navegador con WebAssembly · DESCARTADA

`ffmpeg.wasm` es un port completo de FFmpeg a WASM y **sí decodifica MPEG-2**.
Pero son **31 MB de módulo** y decodificación por software en el hilo de
JavaScript. En un escritorio potente puede funcionar; **en un televisor o un móvil,
no**. No es una solución de producto.

### Opción 7 — VLC embebido en el navegador · IMPOSIBLE

El plugin web de VLC dependía de **NPAPI, que Chrome eliminó en 2015** y el resto
de navegadores después. No existe forma de embeber VLC en una página web moderna.

La versión viable de esta idea **es la Opción 4**: una app nativa con libmpv, que
es exactamente el mismo motor que usa VLC.

---

## 4. Recomendación

**Inmediato, coste cero:** enviar la solicitud a TelecoWR (Opción 1). Si aceptan,
los 18 canales funcionan sin comprar ni montar nada.

**Corto plazo:** no perseguir los 80 canales de satélite para web. Son MPEG-2 y
exigen transcodificación sí o sí; además son 720×540, un salto atrás en calidad
frente a los 1080p que ya tienes.

**Medio plazo, elegir uno:**
- Si el objetivo es **web**: Opción 3 (servidor aparte) por encima de la Opción 2,
  porque no arriesga el FTTH.
- Si el objetivo es **catálogo completo ya**: Opción 4 (app nativa), que no
  necesita hardware pero sí desbloquear `NX-APPS`.

**Independiente de todo lo anterior, y urgente:** regularizar la licencia de
Flussonic. Sin soporte del fabricante, cualquier avería en una cabecera que
alimenta FTTH de pago se afronta a ciegas.

---

## 5. Trabajo aplicado esta noche

**GAMATV reparado.** Estaba `disabled: true` apuntando a
`https://stream.esradioecuador.com/hls/stream.m3u8` — una URL de **radio**. Ahora
toma el satélite real vía el multicast de Astra:

```
input: udp://239.0.12.8:1234/10.2.1.1     alive: true, bytes_in: 5.063.133
```

Sirve para VLC y para moduladores. **No para navegador**, por ser MPEG-2.

**GAMA TV añadido al catálogo** como canal 44, con `hls_path = mpegts`, concedido
en los dos planes. Se entrega por el edge firmado
(`https://nexoraplay.net/stream/ec-main/GAMATV/mpegts`, HTTP 200, `video/mpeg`) en
lugar del enlace crudo, para no romper HTTPS ni saltarse la autorización.

**Recuperación de error de media del web player corregida** (commit `ee99e9a`,
desplegado). Seguía el patrón oficial de hls.js: recuperación **temporizada** en
vez de un único intento. Medido: de `bufferAppendError FATAL` a **0 fatales**.

**Respaldos:** `ROLLBACK_GAMATV.json` y `ROLLBACK_ECUADOR_TV.json`.

**Pendiente sin tocar:** `ECUADOR_TV` sigue con `retry_count` creciendo contra
`hlss://94.130.236.167:8081/shogun/index.m3u8`, muerta. Mismo arreglo posible.

---

## 6. Verificado / no verificado

**Verificado midiendo:** estructura NAL e IDR de segmentos reales; `stream_type` del
PMT; soporte de códecs en Chrome; capacidades y carga del servidor Flussonic;
ausencia de transcoder en Cesbo Astra; que los 18 que fallan vienen todos de
TelecoWR y ninguno de los que funcionan; que VLC reproduce ambos por el mismo edge.

**No verificado:** el rendimiento real de una GPU concreta en este servidor;
si TelecoWR aceptará el cambio; el coste exacto de la Opción 3; si hay derechos de
distribución OTT para los 80 canales de satélite (es decisión de negocio, no
técnica).

---

## Fuentes

- [W3C Media Source Extensions](https://w3c.github.io/media-source/) — bandera `need random access point`
- [hls.js API.md](https://github.com/video-dev/hls.js/blob/master/docs/API.md) — recuperación de error de media
- [hls.js #7321](https://github.com/video-dev/hls.js/issues/7321) — `bufferAppendError` recurrente con `lowLatencyMode`
- [Flussonic Transcoder](https://flussonic.com/doc/transcoder/) — sintaxis y aceleración por hardware
- [Flussonic HLS Playback](https://flussonic.com/doc/video-playback/hls-playback/)
- [Flussonic — What is a GOP](https://flussonic.com/glossary/gop)
- [ffmpeg.wasm](https://ffmpegwasm.netlify.app/) — 31 MB, decodificación por software
- [MDN — Web video codec guide](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Formats/Video_codecs)

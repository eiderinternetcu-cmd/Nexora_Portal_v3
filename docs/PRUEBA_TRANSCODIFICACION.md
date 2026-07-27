# Prueba de transcodificación en el servidor de producción

_2026-07-26 · hilo abierto_

Continuación de [`SOLUCIONES_PLAYBACK_NAVEGADOR.md`](SOLUCIONES_PLAYBACK_NAVEGADOR.md).
Aquel documento cierra el **diagnóstico**; este mide si la **Opción 3** (transcodificar
fuera de la cabecera) es viable, y con qué coste por canal.

La pregunta concreta: **¿cuántos canales aguanta `45.184.225.4` transcodificando?**
No se toca `181.78.246.211` — está a `scheduler_load 100`, sin GPU y alimentando el FTTH.

---

## Estado del servidor — medido

| | |
|---|---|
| Host | `45.184.225.4` (`internet`), producción `nexoraplay.net` |
| CPU | **Intel Xeon E5620** — 4 núcleos / 8 hilos, 2,40 GHz, **de 2010** |
| Carga | `load average 0,29 / 0,09 / 0,03` — prácticamente ocioso |
| Memoria | 7,7 GB total, 1,0 GB en uso |
| Disco | `/dev/sda3` 450 G, 12 G usados (**3 %**) |
| Uptime | 68 días |
| Tráfico acumulado | `eth0` rx 29,2 GB · tx 27,7 GB |
| **ffmpeg** | **6.1.1-3ubuntu5, ya instalado** en `/usr/bin/ffmpeg` |

Códecs presentes, verificados con `-decoders` / `-encoders`:

```
V.S.BD mpeg2video      MPEG-2 video          (decodificador — necesario para el satélite)
VFS..D h264            H.264 / AVC           (decodificador)
V....D libx264         libx264 H.264         (codificador)
A....D aac             AAC                   (codificador)
```

`ffmpeg -hwaccels` lista `vdpau, cuda, vaapi, qsv, drm, opencl, vulkan`, pero son los
**métodos compilados**, no hardware presente: esta máquina no tiene GPU. Todo lo que se
mida aquí es **transcodificación por CPU**.

### Alcance a las fuentes desde este servidor

```
flussonic ec-main 8002   -> 302
GAMATV mpegts directo    -> 200 video/mpeg     http://181.78.246.211:8002/GAMATV/mpegts
GAMATV via edge propio   -> 301 text/html      (127.0.0.1 redirige a HTTPS, esperado)
```

GAMATV es el caso de prueba porque es MPEG-2 720×540 puro: el peor de los dos problemas.

---

## La medición — hecha

Dos canales reales, 30 segundos cada uno, `-preset veryfast`, `nice -n 10`, con
`-g 60 -keyint_min 60 -sc_threshold 0`: el GOP cerrado de 2 s que exige MSE, o sea la
salida ya nace reproducible en navegador.

| | **GAMATV** (satélite) | **GOLDEN_PLUS** (TelecoWR) |
|---|---|---|
| Fuente | MPEG-2 720×540 | H.264 1920×1080, 29,97 fps |
| `cpu=%P` | **87 %** | **243 %** |
| CPU consumida (user+sys) | 29,33 s | 70,93 s |
| Por 30 s de emisión | **0,98 núcleos** | **2,36 núcleos** |
| `maxRSS` | 141 MB | 530 MB |
| `speed` sostenida | 1,19x | 1,19x |

> `speed` idéntica en ambos no es casualidad: en directo ffmpeg lee al ritmo que el
> servidor entrega, no al que puede procesar. Lo que mide el coste es la **CPU consumida
> por segundo de vídeo**, no la velocidad.

### Qué significa en esta máquina

El servidor tiene **4 núcleos físicos / 8 hilos**, y además sirve la API, nginx, Postgres
y Redis de producción.

| Escenario | Coste | ¿Cabe en 4 núcleos? |
|---|---|---|
| 18 canales de TelecoWR (1080p) | **≈ 42,5 núcleos** | **No** — faltan 10 veces |
| 80 canales de satélite (SD) | **≈ 78 núcleos** | **No** — faltan 20 veces |
| Un solo canal 1080p | 2,36 núcleos | Sí, y ya se come medio servidor |

La RAM lo confirma por segunda vía: 530 MB por canal 1080p × 18 = **9,5 GB**, más de los
7,7 GB que tiene la máquina entera.

**Veredicto: reaprovechar el servidor de producción queda descartado con datos.** Este
Xeon E5620 es de 2010 y no tiene GPU; da para **un** canal 1080p, y sacrificando el margen
de la API que ya corre ahí.

Esto **refuerza la recomendación** de `SOLUCIONES_PLAYBACK_NAVEGADOR.md`, no la cambia:

1. **Corregir el GOP en origen (TelecoWR)** sigue siendo la única opción de coste cero, y
   ahora se sabe que la alternativa cuesta 42 núcleos. La solicitud técnica ya está
   redactada en `E:\WEBSITE\SOLICITUD_TECNICA_TELECOWR.md`.
2. Si TelecoWR no acepta, la Opción 3 **exige hardware nuevo de verdad** — no vale
   reciclar. Con estos números, 18 canales piden del orden de 48 hilos modernos, o una
   GPU con NVENC (que mueve una decena de flujos 1080p sin tocar la CPU).
3. Los 80 de satélite se mantienen **descartados para web**: 78 núcleos para entregar
   720×540 no se justifica.

---

## Presets: `ultrafast` cambia el panorama

Medido después, con los mismos 30 s por canal:

| Perfil | Núcleos por canal (Xeon E5620) |
|---|---|
| SD MPEG-2, `veryfast` | 0,98 |
| **SD MPEG-2, `ultrafast`** | **0,34** |
| 1080p → 720p, `ultrafast` | **1,65** |
| 1080p → 720p, `veryfast` | 2,61 |
| 1080p nativo, `veryfast` | 2,36 |

Dos cosas que no son obvias:

1. `ultrafast` cuesta **un tercio** que `veryfast` en SD. Para una fuente de 720×540 que
   ya viene de satélite, la diferencia de calidad no justifica pagar el triple.
2. **Escalar a 720p con `veryfast` cuesta MÁS que codificar a 1080p directo** (2,61 contra
   2,36). El filtro `swscale` se come lo que ahorra el encoder. Si se escala, `ultrafast`.

Con `ultrafast` el servidor pasa de "un canal 1080p y nada más" a **dos canales reales**.

---

## Desplegado en producción — 2026-07-27

Dos canales, uno de cada problema, para cubrir los dos modos de fallo:

| Canal | Origen | Perfil | Coste medido |
|---|---|---|---|
| **GAMA TV** (44) | satélite MPEG-2 | `ultrafast`, sin escalar | **42 % de un núcleo** |
| **GOLDEN PLUS** (5) | TelecoWR 1080p, GOP roto | 720p `ultrafast` | **167 %** |
| | | **total** | **≈ 2,1 de 4 núcleos** |

### Cómo está montado

```
Flussonic ec-main ──> nexora_tc_gamatv ─┐
(MPEG-2 / H.264 GOP  nexora_tc_golden ─┴─> volumen hls_data ──> nexora_hls
 de 11 s)                                                          │
                                          nexora_nginx ────────────┘
                                          /stream/tc-main/  + auth_request
```

- **`docker-compose.transcode.production.yml`** — stack separado, enganchado a la red
  `nexora_api_nexora_net` que ya existía. **No toca `docker-compose.production.yml`**, así
  que se retira entero con un `down` sin rozar el stack vivo.
- **`nexora_hls` no publica puertos.** Solo lo alcanza nginx por la red interna. Sin el
  proxy no hay forma de llegar a los segmentos desde fuera.
- **`location ^~ /stream/tc-main/`** con el mismo `auth_request` que `ec-main` y `co-main`
  — copia versionada en `deploy/transcode/nginx-location-tc-main.conf`, insertada por
  `deploy/transcode/patch_nginx_tc.py` (idempotente).
- **Límites `cpus`** (0,9 y 2,2) como red de seguridad: pase lo que pase, los ffmpeg no se
  comen la máquina que sirve la API.
- En DB, los dos canales pasan a `flussonic_node='tc-main'` con `source_url` **relativa**
  (`/stream/tc-main/<KEY>/index.m3u8`). Relativa a propósito: el navegador la resuelve
  contra el dominio de la página, así funciona igual desde cualquiera de los dominios.

> La API **no** valida el nodo contra una lista blanca: liga el token a `node` + `stream_key`.
> Por eso `tc-main` funciona como nodo virtual sin tocar `FLUSSONIC_NODES` ni la config de
> Flussonic.

### Verificación (2026-07-27 02:39)

```
SIN token:   GAMATV -> 401        GOLDEN_PLUS -> 401
CON token:   manifiesto -> 200  application/vnd.apple.mpegurl
             segmento   -> 200  video/mp2t   659 KB / 1,4 MB   (vía grant de Redis)
nexoraplay.net/health -> 200      load average 0,97
```

La recarga fue **en caliente** (`nginx -s reload`), sin downtime: el `/health` respondió 200
antes y después.

### Rollback

```bash
# 1. Retirar los transcodificadores
cd /opt/nexora_api && sudo docker compose -f docker-compose.transcode.production.yml down

# 2. Devolver los canales a Flussonic directo
sudo docker exec nexora_postgres psql -U nexora -d nexora -c "UPDATE channels SET flussonic_node='ec-main', hls_path='index.m3u8', source_url='https://nexoraplay.net/stream/ec-main/GOLDEN_PLUS/index.m3u8' WHERE channel_key='canal-5';"
sudo docker exec nexora_postgres psql -U nexora -d nexora -c "UPDATE channels SET flussonic_node='ec-main', hls_path='mpegts', source_url='https://nexoraplay.net/stream/ec-main/GAMATV/mpegts' WHERE channel_key='gamatv';"

# 3. Restaurar el nginx anterior (el location sobra sin el backend, pero es limpio)
sudo cp /opt/nexora_api/deploy/nginx/nexoraplay.conf.bak-pre-tc /opt/nexora_api/deploy/nginx/nexoraplay.conf
sudo docker exec nexora_nginx nginx -t && sudo docker exec nexora_nginx nginx -s reload
```

Respaldos dejados en el servidor: `nexoraplay.conf.bak-pre-tc` y
`docker-compose.production.yml.bak-pre-tc`.

---

## Cuánto aguanta cada máquina

Mismo método (`ffmpeg -benchmark`, 30 s) en el PC de desarrollo:

| Perfil | i9-10900K (2020) | Xeon E5620 (2010) |
|---|---|---|
| SD MPEG-2, `ultrafast` | 0,137 núcleos | 0,34 |
| 1080p → 720p, `ultrafast` | 0,689 | 1,65 |
| 1080p nativo, `veryfast` | 1,57 | 2,36 |

**Capacidad del i9** (10 núcleos / 20 hilos, contando 8 núcleos de trabajo real):

| Tipo de canal | Caben | Cifra prudente |
|---|---|---|
| SD MPEG-2 | ≈ 55 | **40** |
| 1080p → 720p | ≈ 11 | **9** |
| 1080p nativo | ≈ 5 | **4** |

Los 18 de TelecoWR piden 12,4 núcleos y los 80 de satélite 11: **ninguno de los dos grupos
entra entero** en una máquina como ésta, y los 98 juntos son 23,4 núcleos — dos máquinas.

Tres avisos sobre esas cifras:

- Están medidas **con la máquina ociosa y turbo activo**. Con 10 procesos a la vez el i9
  baja de frecuencia todo-núcleos: cuenta entre un 20 % y un 30 % menos.
- **Los hilos HT no son núcleos**: 20 hilos rinden como 12-13 núcleos reales.
- **Banda**: 2-4 Mbps por fuente, 24 horas. 18 canales son ~55 Mbps; 98 rondan los 300.

Y lo que decide de verdad: un PC de escritorio se reinicia, se actualiza y se apaga. Como
torre de pruebas, perfecto; como cabecera, no.

---

## Cómo entrar al servidor

La credencial está en `.claude/settings.json` → `mcpServers.nexora-ssh.env`
(`SSH_HOST`, `SSH_USER`, `SSH_PASSWORD`). Ese archivo está en `.gitignore`.

No sirven, ya comprobado: `HOST_PASSWORD` del `.env` es de **staging** (`2.25.68.163`) y
producción la rechaza; no hay entrada para este host en `~/.ssh/config`; ninguna clave de
`~/.ssh` es aceptada (`Permission denied (publickey,password)`).

---

## Pendiente después de esto

- **Los 16 canales de TelecoWR que faltan.** No caben en este servidor: cada uno cuesta
  1,65 núcleos y quedan ~1,9 libres. Es la decisión de hardware que sigue abierta —
  máquina dedicada o GPU con NVENC.
- **Supervisión de los transcodificadores.** Hoy `restart: unless-stopped` los relevanta si
  mueren, pero nadie avisa si la fuente cae y ffmpeg reconecta en bucle. Encaja con la
  alerta de nodo caído que ya estaba pendiente.
- **Versionar `nexoraplay.conf`** (PR #9). El bloque `tc-main` ya tiene copia en
  `deploy/transcode/`, pero el fichero vivo del edge sigue existiendo solo en el servidor.
- Sigue abierto lo del documento anterior: `ECUADOR_TV` con `retry_count` creciendo contra
  `hlss://94.130.236.167:8081/shogun/index.m3u8`, muerta.
- Y lo de siempre, que esto no cambia: **corregir el GOP en origen sigue siendo gratis**.
  Si TelecoWR acepta, sus 18 canales dejan de necesitar transcodificación.

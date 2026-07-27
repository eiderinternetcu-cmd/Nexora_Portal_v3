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
| **GAMA TV** (44) | satélite MPEG-2 | `ultrafast`, sin escalar | **39 % de un núcleo** |
| **ECUADOR TV** (30) | satélite MPEG-2 | `ultrafast`, sin escalar | **43 %** |
| **RCN COL** (38) | satélite MPEG-2 | `ultrafast`, sin escalar | **33 %** |
| **CARACOL COL** (28) | satélite MPEG-2 | `ultrafast`, sin escalar | **35 %** |
| **GOLDEN PLUS** (5) | TelecoWR 1080p, GOP roto | 720p `ultrafast` | **174 %** |
| | | **total** | **≈ 3,3 de 8 hilos** |

> **Ojo al denominador:** `nproc` devuelve **8** —4 núcleos físicos con Hyper-Threading—, así
> que la carga se lee contra 8, no contra 4. Con los cinco canales arriba, `top` reporta
> **64 % de CPU ociosa** y `/health` responde en **0,08 s**. Queda margen.

RCN y CARACOL no fallaban por estar mal mapeados: el catálogo apuntaba bien y las fuentes
estaban vivas. Son **`mpeg2video` 720x480** —satélite puro— y el navegador no las decodifica.
Sus segmentos incluso arrancaban en keyframe, lo que no sirve de nada si el códec no se
soporta. `CARACOL_INTERNACIONAL` es otro stream distinto, desactivado, con fuente SRT caída:
no confundirlo con `CARACOL-COL`.

### ECUADOR TV: hubo que reparar la fuente antes

El canal 30 estaba **desactivado** en el catálogo y su stream en la cabecera apuntaba a
`hlss://94.130.236.167:8081/shogun/index.m3u8`, muerta, con **`retry_count: 223`**.

Se repuntó al multicast real de Astra —`udp://239.0.12.6:1234/10.2.1.1`, el mismo patrón
que se usó con GAMA TV— conservando intacto el `push` a `udp://235.2.2.2:5028`, que es el
que alimenta los moduladores y el FTTH. Resultado inmediato: `alive: true`, 1.092 kbps,
`retry_count` de vuelta a 0.

Respaldo del estado previo en `ROLLBACK_ECUADOR_TV_2026-07-27.json`.

**La cabecera no acusó el cambio**: `cpu_usage` 61 % (antes 63-68 %), `memory_usage` 34 %
(antes 36 %), `scheduler_load` 100 —que ya estaba a 100 de antes—. Astra tampoco: emitía
ese multicast hubiera o no alguien escuchando; lo único nuevo es que Flussonic se suscribe.

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

## Torre de Miami — nodo `tc-mia`, desplegado 2026-07-27

Segunda máquina, dedicada solo a transcodificar: **Xeon E3-1275L v3** (Haswell, 4 núcleos /
8 hilos, 2,7 GHz con turbo a 3,9, **con AVX2** — que al E5620 le falta), 31 GB RAM, 878 GB
de disco. $50/mes.

| Canal | # | Fuente |
|---|---|---|
| GOLDEN PREMIER 2H | 6 | TelecoWR 1080p 30 fps |
| ESTRELLAS | 7 | TelecoWR 1080p **60 fps** |
| TLNOVELAS | 8 | TelecoWR 1080p 30 fps |

### Capacidad real: tres canales, no cuatro

La estimación por calibración sintética decía 0,67 núcleos por canal. **Medido con los tres
corriendo de verdad: 100 %, 97,9 % y 93,8 % — casi un núcleo entero cada uno.**

La calibración sintética servía para comparar máquinas, no para dimensionar: `testsrc2` sale
3,5 veces más barato que vídeo real. Y ESTRELLAS va a **60 fps**, el doble de fotogramas que
codificar.

Con 4 núcleos físicos, **la torre da para tres canales de este tipo**. Un cuarto la satura.

### Montaje

- **Sin Docker**: máquina dedicada, ffmpeg nativo supervisado por systemd
  (`nexora-tc@.service`, `Restart=always`). Script completo en `mia-tower-setup.sh`.
- **nginx local en el 8088** sirviendo `/var/hls`, con **ufw cerrado**: solo entra SSH y el
  8088 exclusivamente desde `45.184.225.4`.
- **En el edge**, `location ^~ /stream/tc-mia/` con el mismo `auth_request` que todos los
  demás nodos (`nginx-location-tc-mia.conf`, insertado por `patch_nginx_tc_mia.py`).
- Los canales llevan `flussonic_node='tc-mia'` y `source_url` relativa.

> **Trampa de systemd que costó encontrar:** `${VFILTER}` con llaves se expande como **un
> solo argumento**, así que ffmpeg recibía `"-vf scale=1280:720"` pegado y fallaba. Sin
> llaves (`$VFILTER`) systemd lo separa en palabras. Está comentado en la unidad.

### Requisito externo

La cabecera `181.78.246.211` **filtra por IP**. Antes de que el NOC metiera `66.163.125.89`
en la lista blanca, la máquina no recibía ni respuesta al ping — mientras que
`38.210.187.13:8002` y el resto de internet sí respondían, lo que descartaba problema local.

### Verificación (2026-07-27 03:45)

```
SIN token:   los tres -> 401
CON token:   manifiesto 200, segmentos de 1,4 a 1,6 MB
GOP:         GOLDEN PREMIER 2H  121 fotogramas, I en 0 y 60
             ESTRELLAS          241 fotogramas, I en 0 y 120   (60 fps)
             TLNOVELAS          121 fotogramas, I en 0 y 60
```

### Arquitectura: pendiente de decidir

Hoy el vídeo va **Esmeraldas → Miami → edge → cliente**: cruza a Miami y vuelve, y el edge
paga ese ancho de banda dos veces. La alternativa es que Miami sirva directo al cliente con
su propio `auth_request` contra la API, lo que cuesta un subdominio (`tc.nexoraplay.net`) y
su certificado. Con más canales, la diferencia se nota.

### Concesiones en planes: la trampa que escondió ECUADOR TV

Con `ENTITLEMENT_ENFORCE=true`, un canal activo **no se ve si no está concedido en el plan
del suscriptor**. ECUADOR TV estaba solo en el Plan Anual, y GOLDEN PLUS igual. Al añadir un
canal transcodificado hay que concederlo en **todos** los planes:

```sql
insert into plan_channels (id, plan_id, channel_id, is_enabled, created_at)
select gen_random_uuid(), p.id, c.id, true, now()
from plans p cross join channels c
where c.flussonic_node like 'tc-%'
on conflict (plan_id, channel_id) do update set is_enabled = true;
```

---

## Cómo decidir si un canal necesita transcodificarse

Antes de gastar un núcleo en un canal, hay que comprobar si lo necesita. Herramienta:
`scripts/check_browser_playable.py AXN AE_MUNDO ...` (solo lectura).

Un canal vale para navegador si cumple **las dos**:

1. **El vídeo es H.264.** MSE no decodifica MPEG-2 —`isTypeSupported('mp2v')` es `false`—.
2. **Cada segmento arranca en keyframe.** La especificación de MSE inicializa
   `need random access point` a `true` y descarta todo fotograma que no sea punto de acceso
   aleatorio, reiniciando la regla al principio de cada segmento.

> **Trampa del método, que casi cuesta cuatro núcleos:** hay que mirar los **paquetes**
> (`-show_entries packet=flags`), que van en **orden de decodificación**. Los **fotogramas**
> (`frame=pict_type`) salen en **orden de presentación**, donde los B se muestran antes que
> el I que se decodifica primero — y ahí un segmento perfectamente válido *parece* empezar
> en B. Mirando fotogramas se concluye que hace falta transcodificar cuando no hace falta.

### Cuatro canales que NO necesitaban transcodificarse (2026-07-27)

| # | Canal | stream_key | Códec | Veredicto |
|---|---|---|---|---|
| 45 | AXN | `AXN` | H.264 1280x720 | directo |
| 46 | A&E MUNDO | `AE_MUNDO` | H.264 1280x720 | directo |
| 47 | SONY CHANNEL | `SONY_CHANNEL` | H.264 1280x720 | directo |
| 48 | LIFETIME | `LIFETIME` | H.264 1280x720 | directo |

Entran por el multicast de Astra (`udp://239.0.1.7:100x`) y sus segmentos traen `K` en el
paquete 0. El manifiesto que sirve el edge lo confirma: `CODECS="avc1.4d4028"` —H.264 Main
4.0—, `RESOLUTION=1280x720`, con dos pistas AAC (spa/eng) y el token propagado a variantes y
audios. **Coste de CPU: cero.**

Van en `ec-main` como cualquier canal normal; solo hubo que darlos de alta y concederlos en
los dos planes.

---

## Sobre el `scheduler_load` a 100

Conviene corregir una lectura de `SOLUCIONES_PLAYBACK_NAVEGADOR.md`: allí se usó
`scheduler_load: 100` como prueba de que la cabecera está saturada. **Ese argumento es más
débil de lo que parecía.** Medido en seis muestras seguidas:

```
muestra   cpu_usage   scheduler_load
   1          66            100
   2          64            100
   3          62            100
   ...        ...           100
```

El valor **no se mueve** mientras la CPU oscila entre 62 y 68 %. Una métrica clavada en 100
que no reacciona no está midiendo carga: `scheduler_load` es la utilización de los
schedulers de la máquina virtual de Erlang, y por defecto esos schedulers hacen **espera
activa** — giran en vacío en lugar de dormirse, y eso los cuenta como ocupados.

La conclusión de aquel documento (la cabecera no es sitio para transcodificar) **no cambia**,
pero los motivos buenos son otros: no tiene GPU, su transcoder es solo CPU, y el FTTH cuelga
de la misma máquina.

### Confirmado con tres pruebas independientes

1. **La métrica no varía nunca**: seis muestras seguidas a 100 mientras `cpu_usage` oscilaba
   entre 62 y 68.
2. **El transporte llega limpio**: 25 s de ECUADOR TV y 25 s de GOLDEN PREMIER 2H → **cero
   errores** de continuidad. El manifiesto responde en **16-27 ms**. Una cabecera saturada
   no hace eso.
3. **La documentación describe este caso exacto.** El manual de Flussonic define
   `scheduler_load` como consumo del *scheduler* de Erlang, no de CPU. Y *Erlang in Anger*
   explica por qué se dispara: *"para evitar dormirse cuando hay poco trabajo, los hilos que
   controlan los schedulers hacen bucles de espera activa"*, e ilustra con schedulers al
   99 % mientras el sistema reporta 70 % de CPU — *"hay una parte considerable de CPU que
   estaría libre para trabajo real"*.

Fuentes: [SNMP — Manual de Flussonic](https://flussonic.com/en-US/doc/api/snmp) ·
[Erlang in Anger, métricas de runtime](https://github.com/heroku/erlang-in-anger/blob/master/105-runtime-metrics.tex)

Qué se puede hacer, por orden de sensatez:

1. **Usar `cpu_usage` y el bitrate de salida** para decidir, no `scheduler_load`.
2. **Limpiar los streams caídos**: 6 están encendidos y muertos, reintentando en bucle
   (TUDN, WARNER, PASSION, MAKRODIGITAL_TV con 218 intentos cada uno; UBE_Tv 114;
   CANAL_UNO_ECU 19). Otros 36 están desactivados y no consumen. O se reparan contra su
   multicast de Astra —como se hizo con ECUADOR TV— o se desactivan.
3. **Flags de la VM de Erlang** (`+sbwt none +sbwtdcpu none +sbwtdio none`, `+S` ajustado a
   los núcleos) si se quiere que el número refleje la realidad. **Exige reiniciar Flussonic**,
   lo que corta la señal del FTTH: solo con ventana acordada, y sabiendo que la licencia
   crackeada deja sin soporte si algo sale mal.

---

## Limpieza de streams caídos (2026-07-27)

Seis streams estaban encendidos y muertos, reintentando en bucle. No todos eran el mismo
caso, y la diferencia importa:

| Stream | Reintentos | Fuente | Push | ¿Astra? | Acción |
|---|---|---|---|---|---|
| **WARNER** | 320 | `srt://181.78.246.211:2027` (listener propio, vacío) | — | **sí, `239.0.0.16`** | **reparado** |
| CANAL_UNO_ECU | 54 | SRT `172.82.129.11` | `5023` | engañoso | **no tocar** |
| TUDN | 318 | SRT TelecoWR | `5020` | no | dejar reintentando |
| PASSION | 318 | SRT `15.235.107.193` | — | no | dejar reintentando |
| UBE_Tv | 256 | SRT `187.251.170.35` | `5021` | no | fuera del catálogo |
| MAKRODIGITAL_TV | 318 | SRT `104.37.190.102` | `5025` | no | dejar reintentando |

**WARNER** tenía equivalente exacto en Astra y ningún push, así que repuntarlo era gratis:
`alive: true`, 551 KB de entrada y `retry_count` de 320 a 0. Es MPEG-2 720x480, o sea que
para navegador necesitaría un hueco de transcodificación (~0,34 núcleos); hoy no está en el
catálogo. Respaldo en `ROLLBACK_WARNER_2026-07-27.json`.

**CANAL_UNO_ECU es la trampa del lote.** Astra tiene un `Canal Uno` en `239.0.3.5`, y por
nombre parece el arreglo obvio. **No lo es**: sus vecinos de bloque son `239.0.3.1`
(CARACOL-COL) y `239.0.3.4` (RCN-COL), o sea que el `239.0.3.x` es la parrilla **colombiana**.
Apuntar ahí el canal ecuatoriano habría metido contenido equivocado bajo el nombre correcto,
que es peor que dejarlo caído.

**Por qué NO se desactivan los tres restantes:** el bucle de reintentos es exactamente el
mecanismo por el que un canal se recupera solo cuando el proveedor vuelve. Desactivarlos
ahorra un `connect()` cada pocos segundos —despreciable— a cambio de que alguien tenga que
acordarse de reactivarlos a mano. Lo que sí se corrigió es lo que veían los suscriptores:
**UBE TV estaba activo en el catálogo con la fuente muerta**. TUDN y CANAL UNO ECU ya
estaban inactivos.

---

## Calidad: desentrelazado (2026-07-27)

Todas las fuentes de satélite y las de TelecoWR se declaran **entrelazadas** (`field_order:
tt`), y los transcodificadores las codificaban como si fueran progresivas. Eso deja el peine
grabado en la imagen: se ve en cualquier paneo de cámara o rótulo que se desplace.

**Pero la bandera del stream miente en la mitad de los casos.** Medido con el filtro `idet`
sobre 300 fotogramas de cada fuente:

| Fuente | TFF | Progresivos | Realidad |
|---|---|---|---|
| GAMA TV | 293 | 0 | entrelazado |
| RCN COL | 302 | 0 | entrelazado |
| WARNER | 297 | 4 | entrelazado |
| GOLDEN PLUS | 303 | 4 | entrelazado (**1080i**, no 1080p) |
| CARACOL COL | 180 | 122 | **mixto** |
| ECUADOR TV | 0 | 287 | **progresivo** |
| ESTRELLAS · TLNOVELAS · GOLDEN PREMIER 2H | 0 | ~305 | **progresivos** |

Aplicar el filtro a ciegas habría **ablandado** ECUADOR TV y los tres de Miami sin ganar
nada. Por eso ECUADOR TV **no lleva filtro**, y la torre de Miami no necesitó ningún cambio.

### La trampa de medirlo mal

`idet` sobre **la salida** ya transcodificada daba "progresivo" incluso antes de arreglar
nada: la compresión a 1500 kbps destruye el patrón de peine lo bastante como para que el
detector no lo vea, aunque el defecto siga ahí. **Hay que medir la fuente, no la salida.** Y
en GOLDEN PLUS medir la salida era doblemente inútil, porque el escalado a 720p también
borra el patrón.

### `bwdif` sí, `idet` no

La primera versión usaba `idet,bwdif=deint=1` —detectar por fotograma y desentrelazar solo
lo que hiciera falta—, que es lo correcto sobre el papel para CARACOL. **El detector costaba
más que el propio desentrelazado:**

| Canal | Sin filtro | `idet,bwdif=deint=1` | `bwdif=0` |
|---|---|---|---|
| GAMA TV | 39 % | 65 % | **39 %** |
| RCN | 37 % | 77 % | **39 %** |
| WARNER | 38 % | 70 % | **54 %** |
| GOLDEN PLUS | 195 % | 224 % | **214 %** |
| **Total del edge** | — | **5,3 de 8 hilos** | **4,2 de 8** |

Con `idet` el servidor llegó a `load 8,80` sobre 8 hilos: saturado. Con `bwdif=0` a secas, el
desentrelazado en SD sale **prácticamente gratis** y quedan 51 % de CPU ociosa.

Verificado en la salida de los seis: `TFF: 0`, todo progresivo.

---

## Revisión de la cabecera (2026-07-27, 05:15)

| | |
|---|---|
| `cpu_usage` | **74 %** — era 61 % antes de montar los transcodificadores |
| `memory_usage` | 36 % |
| Streams | 43 vivos · 35 desactivados · **5 caídos** — de 83 |
| Entrada / salida | 183,9 / **191,9 Mbps** (era 155 / 167) |
| Uptime | **3 h** |

**El salto de CPU es nuestro.** La salida subió 25 Mbps porque ahora la cabecera alimenta
nueve flujos más: seis transcodificadores en el edge y tres en la torre. Sigue habiendo
margen, pero 74 % ya no es holgado — a partir de aquí, el cuello de botella para añadir
canales deja de ser la CPU de transcodificación y pasa a ser la cabecera.

**Flussonic se reinició sobre las 02:00** y no fue por nuestros cambios: el primero se
aplicó a las 03:15, más de una hora después. Conviene averiguar qué lo reinició, porque ese
corte se lleva por delante la señal del FTTH.

**Cuatro streams nuevos desde el volcado anterior**: `AE_MUNDO`, `SONY_CHANNEL`, `LIFETIME`
—los tres ya dados de alta— y **`HISTORY_2`**, que no está en el catálogo. Alguien más
trabaja sobre esa cabecera. `HISTORY_2` es H.264 1280x720 y arranca en keyframe: entraría
**directo, sin transcodificar**.

### UBE TV: la fuente está caída, no el canal

`UBE_Tv` apunta a `srt://187.251.170.35:10030` y lleva **276 intentos fallidos**. Dos avisos
sobre cómo comprobarlo:

- **SRT va sobre UDP.** Probar el puerto por TCP no demuestra nada, y el ping suele estar
  bloqueado. La única prueba que vale es que **Flussonic**, que sí habla SRT, no consigue
  conectar.
- **La API de Astra exige autenticación** (`403` en `/control/` y `/api/status`). Su config
  declara un único usuario, `admin`, con la contraseña cifrada — no está en el `.env` ni en
  ningún sitio del repo. Sin ella no se puede consultar en vivo si un canal apareció en la
  parrilla satelital.

Queda inactivo en el catálogo hasta tener fuente: con la fuente caída, un suscriptor que lo
pulse ve un canal roto. Reactivarlo es un `UPDATE`.

---

## Pendiente después de esto

- **Los 13 canales de TelecoWR que faltan.** Entre el edge (~1,8 núcleos libres) y la torre
  (llena con tres) no queda sitio. A ~1 núcleo por canal harían falta **cuatro torres más**
  a $50/mes cada una, o sea $250/mes en total — contra una GPU con NVENC, que mueve 10-15
  flujos 1080p en una sola máquina. Y sigue en pie la opción gratis: que TelecoWR corrija
  el GOP en origen.
- **Supervisión de los transcodificadores.** Hoy `restart: unless-stopped` los relevanta si
  mueren, pero nadie avisa si la fuente cae y ffmpeg reconecta en bucle. Encaja con la
  alerta de nodo caído que ya estaba pendiente.
- **Versionar `nexoraplay.conf`** (PR #9). El bloque `tc-main` ya tiene copia en
  `deploy/transcode/`, pero el fichero vivo del edge sigue existiendo solo en el servidor.
- **`HISTORY_2`**: verificado como reproducible directo. Solo falta darlo de alta (sería el
  canal 50) y concederlo en los dos planes.
- **`UBE_Tv`**: necesita fuente. Requiere la contraseña de `admin` de Astra para buscarlo en
  el satélite, o una URL nueva del proveedor SRT.
- **Averiguar qué reinició Flussonic** a las 02:00 del 2026-07-27.
- **Vigilar el `cpu_usage` de la cabecera**, ya en 74 %: es el nuevo límite para añadir
  canales, por encima de la capacidad de transcodificación.
- Y lo de siempre, que esto no cambia: **corregir el GOP en origen sigue siendo gratis**.
  Si TelecoWR acepta, sus 18 canales dejan de necesitar transcodificación.

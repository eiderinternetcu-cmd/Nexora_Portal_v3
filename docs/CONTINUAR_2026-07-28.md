# Para retomar — 2026-07-28

Estado al cierre de la madrugada del 27, y qué conviene hacer a continuación.
Detalle técnico completo en [`PRUEBA_TRANSCODIFICACION.md`](PRUEBA_TRANSCODIFICACION.md).

---

## Lo que quedó funcionando

**Trece canales nuevos o reparados en producción**, de dos que había:

| Nodo | Canales | Carga |
|---|---|---|
| `tc-main` (edge, `45.184.225.4`) | GOLDEN PLUS (5), CARACOL (28), ECUADOR TV (30), RCN (38), GAMA TV (44), WARNER (49) | **5,4 de 8 hilos**, 34 % ocioso |
| `tc-mia` (torre Miami, `66.163.125.89`) | GOLDEN PREMIER 2H (6), ESTRELLAS (7), TLNOVELAS (8) | ~2,9 de 3,2 — **llena** |
| `ec-main` (directo, sin transcodificar) | AXN (45), A&E MUNDO (46), SONY CHANNEL (47), LIFETIME (48) | coste cero |

Dos fuentes muertas resucitadas contra el multicast de Astra: **ECUADOR TV** (223 reintentos)
y **WARNER** (320). Ambas con respaldo `ROLLBACK_*.json` en el servidor.

Calidad: **desentrelazado** en los cinco canales que lo necesitaban de verdad, y **preset
`superfast`** en los SD — más calidad que `ultrafast` a 2500k con 40 % menos de ancho de banda.

---

## Riesgo abierto: el refactor de nginx

`deploy/nginx/` se factorizó en `conf.d/` + `snippets/` (commit `4f99925`), pero **el
contenedor sigue cargando el `nexoraplay.conf` monolítico** — el refactor está en disco, no
desplegado.

`snippets/stream-gate.conf` tenía `tc-main` pero le faltaba **`tc-mia`**: al desplegarlo, los
tres canales de Miami habrían devuelto 404. **Ya está corregido en este commit**, pero antes
de desplegar conviene comparar el conf vivo del servidor con el versionado, porque han
convergido dos trabajos distintos sobre el mismo archivo.

Comprobación de que el gate sigue vivo (401 = correcto, 404 = location perdido):

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://nexoraplay.net/stream/tc-main/GAMATV/index.m3u8
curl -s -o /dev/null -w '%{http_code}\n' https://nexoraplay.net/stream/tc-mia/ESTRELLAS_CA/index.m3u8
```

---

## Recomendaciones, por relación valor/esfuerzo

### 1. Quitar GOLDEN PLUS — 5 minutos, libera 2 núcleos

Consume **194 %**, más que tres canales SD juntos, y **su fuente entrega segmentos
truncados**: se ven de 128 KB junto a otros de 1,1 MB, o sea imagen congelada a ratos. El
transcodificador está bien; el feed de TelecoWR no.

Quitarlo deja el edge en ~3,4 de 8 y abre sitio para: cuatro canales SD más, o subir todos
los SD a `veryfast` (SSIM 0,943 contra 0,939).

### 2. Dar de alta HISTORY_2 — 2 minutos, coste cero

Ya verificado: **H.264 1280x720 y arranca en keyframe**, o sea entra directo por `ec-main`
sin transcodificar. Sería el canal 50. Mismo patrón que AXN:

```sql
insert into channels (...) values (..., 'history-2', 50, 'HISTORY 2', 'general', 'HISTORY_2',
  'flussonic', 'https://nexoraplay.net/stream/ec-main/HISTORY_2/index.m3u8', 'ec-main',
  'index.m3u8', true, true, now(), now());
-- y concederlo en TODOS los planes, o con ENTITLEMENT_ENFORCE=true no se ve
```

### 3. Las credenciales de Astra — bloquea dos cosas

Sin ellas no se puede consultar la parrilla satelital en vivo, y eso bloquea:
- **UBE TV**, que sigue sin fuente (SRT externo muerto, 276 reintentos) y desactivado.
- Descubrir qué otros canales hay en el satélite sin dar de alta — como pasó con AXN,
  A&E MUNDO, SONY CHANNEL y LIFETIME, que llevaban ahí sin que nadie los pusiera.

Guardarlas en `.env` junto a las de Flussonic (`ASTRA_URL`, `ASTRA_USER`, `ASTRA_PASSWORD`).

### 4. Supervisión de los transcodificadores

`restart: unless-stopped` los relevanta si mueren, pero **si una fuente cae, ffmpeg reconecta
en bucle y nadie se entera**. Encaja con la alerta de nodo caído que ya estaba pendiente en
el roadmap. Señal simple y fiable: que el `#EXT-X-MEDIA-SEQUENCE` de cada canal avance.

### 5. Decidir la ruta del vídeo de Miami

Hoy va **Esmeraldas → Miami → edge → cliente**: cruza a Miami y vuelve, y el edge paga ese
ancho de banda. Que Miami sirva directo cuesta un subdominio (`tc.nexoraplay.net`) con su
certificado y su propio `auth_request`. Con tres canales se nota poco; con más, bastante.

### 6. Averiguar qué reinició Flussonic

La cabecera se reinició sola sobre las 02:00 del 27. No fue por nuestros cambios —el primero
se aplicó a las 03:15—. Ese corte se lleva por delante la señal del FTTH.

---

## Lo que NO hay que hacer

- **No apuntar `CANAL_UNO_ECU` al `Canal Uno` de Astra.** Por nombre parece el arreglo obvio,
  pero el bloque `239.0.3.x` es la parrilla **colombiana** (`.1` CARACOL-COL, `.4` RCN-COL).
  Metería contenido equivocado bajo el nombre correcto, que es peor que dejarlo caído.
- **No desactivar los tres streams caídos que quedan** (TUDN, PASSION, MAKRODIGITAL_TV). Su
  bucle de reintentos es el mecanismo por el que se recuperan solos cuando el proveedor
  vuelve. Ninguno se ve en el catálogo, así que no molestan a nadie.
- **No usar `scheduler_load` para decidir nada.** Está clavado en 100 por la espera activa de
  Erlang. Lo que informa es `cpu_usage` — hoy en **74 %**, que ya es el límite real para
  añadir canales, por encima de la capacidad de transcodificación.
- **No añadir más canales 1080p a la torre de Miami.** Está a ~2,9 de 3,2 núcleos.

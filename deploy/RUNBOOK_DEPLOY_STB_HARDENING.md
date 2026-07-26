# RUNBOOK — Despliegue del endurecimiento de la superficie de dispositivos

> Despliega los commits `89756a7` (bloqueantes del cliente nativo) y `9d7800b`
> (autenticación + entitlement en `/api/stb/*` y `/api/v1/devices/heartbeat`).
>
> Estado al escribir esto: **producción corre `ebb166f`**. El arreglo está
> commiteado en local, **sin pushear y sin desplegar**.

---

## 0. DECISIÓN PREVIA — el repositorio es PÚBLICO

```
https://github.com/eiderinternetcu-cmd/Nexora_Portal_v3   →   visibility: PUBLIC
```

El commit `9d7800b` y `docs/INFORME_SESION_2026-07-26.md` describen con detalle un
fallo de autorización **que sigue vivo en el servidor**. Pushearlos antes de
desplegar publica el problema y su reproducción a cualquiera.

Hay que elegir **una** de estas tres antes de tocar `git push`:

| Opción | Qué implica |
|---|---|
| **A · Desplegar primero, pushear después** | El servidor se despliega desde un tar/rsync o desde un remote privado. Se pushea a GitHub cuando producción ya está parcheada. **Es la más segura.** |
| **B · Poner el repositorio en privado** | `gh repo edit --visibility private`. Un solo comando, reversible. Conviene igualmente: el repo contiene runbooks, IPs y topología de producción. |
| **C · Pushear con historial saneado** | Reescribir los mensajes de commit para no describir el vector, y publicar el informe cuando el parche esté desplegado. Más trabajo y fácil de hacer a medias. |

**Recomendación: B, y luego A.** Poner el repositorio en privado resuelve además
un problema que ya existía antes de esta sesión — hoy son públicas las IPs de los
orígenes Flussonic, la topología del edge y los runbooks de producción.

> Mientras esta decisión no se tome, **no ejecutes `git push`**.

---

## 1. Qué cambia en producción

Al desplegar, tres rutas pasan de responder **422** a responder **401** sin token:

- `POST /api/stb/auth/play`
- `POST /api/stb/auth/token`
- `POST /api/stb/heartbeat`
- `POST /api/v1/devices/heartbeat`

Todo lo demás **no cambia de comportamiento**. Los otros flags nuevos van con
default que preserva lo actual:

| Flag | Default | Efecto si se activa |
|---|---|---|
| `STB_AUTH_ENFORCE` | **`true`** | ← la única excepción: cerrado por defecto, por ser un fallo de seguridad |
| `PLAYBACK_REISSUE_ENTITLEMENT_CHECK` | `false` | Reevalúa entitlement en cada renovación |
| `CATALOG_ENTITLEMENT_FILTER` | `false` | El catálogo solo lista canales del plan |

No hay migración Alembic nueva: producción sigue en **007**.

---

## 2. Pre-requisitos

- [ ] Decisión del punto 0 tomada.
- [ ] Backup de `.env.production`.
- [ ] Confirmar que **ningún dispositivo real usa `/api/stb/*`**. Se verificó que
      no lo usan `web_player`, `nexora_app` ni `nexora_ios`, pero **no se puede
      descartar un STB fuera del árbol de repositorios**. Si existe alguno, se
      quedará sin servicio al desplegar → ver rollback.
- [ ] Preferentemente sin sesiones activas.

```bash
# Comprobación previa: ¿alguien está llamando a /api/stb/* hoy?
sudo docker logs nexora_nginx --since 24h 2>&1 | grep -c '/api/stb/'
sudo docker logs nexora_api   --since 24h 2>&1 | grep -c '/api/stb/'
```

**Si esos contadores no son 0, PARA** y averigua quién llama antes de seguir.

---

## 3. Despliegue

```bash
TS=$(date +%Y%m%d_%H%M%S)
sudo cp /opt/nexora_api/.env.production /opt/backups/env.production.bak-stb-$TS

cd /opt/nexora_api
git fetch --all
git log --oneline -1                 # anota el commit actual para el rollback
git checkout <rama-o-commit>         # el que corresponda tras la decisión del punto 0

sudo docker compose -f docker-compose.production.yml up -d --force-recreate --no-deps api
sudo docker logs nexora_api --tail 40
```

---

## 4. Validación

El criterio es **401, no 422**. Un 422 significa que la petición llegó a validar
el cuerpo, es decir, que no hay puerta de autenticación delante.

```bash
for p in /api/stb/auth/play /api/stb/auth/token /api/stb/heartbeat /api/v1/devices/heartbeat; do
  printf '%-34s ' "$p"
  curl -s -o /dev/null -w '%{http_code}\n' -X POST "https://nexoraplay.net$p" \
       -H 'Content-Type: application/json' -d '{}'
done
```

**Esperado: `401` en las cuatro.** Cualquier `422` significa que el despliegue no
tomó efecto.

Y que lo que ya funcionaba sigue funcionando:

```bash
curl -s -o /dev/null -w 'health:  %{http_code}\n' https://nexoraplay.net/health
curl -s -o /dev/null -w 'catalog: %{http_code}\n' https://nexoraplay.net/api/client/catalog/channels   # 401 (correcto)
```

- [ ] Login de un suscriptor real desde el web player.
- [ ] Reproducción de un canal, y que **siga reproduciendo pasados 3 minutos**
      (esto ejercita el reissue firmado y el grant de segmentos).
- [ ] Zapping entre dos canales sin 409.

---

## 5. Rollback

**Por flag, sin redesplegar** — reabre solo el agujero de identidad y **conserva
el arreglo del entitlement**:

```bash
echo 'STB_AUTH_ENFORCE=false' | sudo tee -a /opt/nexora_api/.env.production
cd /opt/nexora_api
sudo docker compose -f docker-compose.production.yml up -d --force-recreate --no-deps api
```

**Completo**, si algo más se rompiera:

```bash
cd /opt/nexora_api
git checkout ebb166f
sudo docker compose -f docker-compose.production.yml up -d --force-recreate --no-deps api
sudo cp /opt/backups/env.production.bak-stb-$TS /opt/nexora_api/.env.production
```

No hay que revertir ninguna migración: no se añadió ninguna.

---

## 6. Después del despliegue, y solo después

Con producción ya parcheada, estas quedan disponibles:

1. **Pushear y abrir PR** (según la decisión del punto 0).
2. **Activar `PLAYBACK_REISSUE_ENTITLEMENT_CHECK=true`** si se quiere que perder un
   canal del plan corte la reproducción en curso en vez de esperar a que expire la
   sesión (4 h). Cuesta 5 lecturas indexadas por renovación, una cada ~45 s por
   stream activo.
3. **PROD-Fase 2D — `PLAYBACK_IP_BINDING_MODE=soft`.** Ahora sí tiene sentido: antes
   de `9d7800b` los tokens reemitidos salían sin el claim `cip` y quedaban exentos,
   y siendo el reissue la ruta dominante, `soft` no habría observado casi nada.

   ⚠️ **Pendiente de decidir antes de `strict`:** el validador sigue dejando pasar
   por diseño los tokens *sin* `cip` ni `node`, como compatibilidad con STB legacy.
   El arreglo cerró el emisor, no el validador. Mientras esa exención siga en pie,
   `strict` es parcialmente evitable.
4. **Fijar `STREAM_GRANT_MAX_LIFETIME_SECONDS`** (p. ej. `21600` = 6 h). Hoy está en
   `0` = ilimitado, lo que significa que **revocar un suscriptor no corta su stream
   en curso** mientras siga pidiendo segmentos.

---

## 7. Sin verificar

- Nada de esto se ha probado contra el Nginx ni el Flussonic reales — el entorno de
  desarrollo no tiene ruta a los orígenes.
- **Riesgo dependiente de datos:** los tokens ahora llevan el claim `node`, y el
  validador rechaza si no coincide con el nodo que Nginx le pasa. Si alguna fila de
  `channels` tuviera `flussonic_node` incoherente, ese canal empezaría a dar 403.
  Antes de desplegar conviene un vistazo:

  ```bash
  sudo docker exec nexora_postgres psql -U nexora -d nexora -c \
    "SELECT flussonic_node, count(*) FROM channels WHERE is_active GROUP BY 1;"
  ```

  Los valores deben ser exactamente los que tienen `location` en
  `deploy/nginx/nexoraplay.conf`. **`ec-quito` no tiene `location` en el conf de
  producción** — si aparece en esa consulta, revísalo antes de desplegar.

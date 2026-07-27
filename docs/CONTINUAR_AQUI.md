# Continuar aquí — punto de retoma

_Handoff a 2026-07-27. Para el detalle completo: `docs/ROADMAP_SESION_MULTIMARCA_ADMIN.md`.
Este archivo es solo "por dónde seguir"._

Rama: **`feat/client-api-blockers`** (37 commits por delante de `main`, todo subido, árbol
limpio). Para retomar: `git checkout feat/client-api-blockers`.

---

## Estado en una línea

Web player multi-marca y **certificado de tvdigital.laredtelco.com** ya en producción.
Panel de administración y cambios de API: construidos, probados, **commiteados SIN
desplegar**.

---

## Lo siguiente, en orden

### 1. Verificar las ESCRITURAS del panel en local
El panel está probado leyendo, no escribiendo. Antes de desplegarlo, probar a mano:
crear un suscriptor, guardar una lista blanca de canales en un plan, revocar una sesión.
Es entorno local, es seguro.

- Panel corriendo en **http://127.0.0.1:5175** (contenedor `nexora_admin_test`). Login
  `admin` / `Admin1234!`.
- Si el contenedor ya no está, reconstruir:
  ```
  cd E:\WEBSITE\nexora_api\admin_panel
  docker build -t nexora_admin_panel:test .
  docker run -d --name nexora_admin_test --network nexora_api_nexora_net -p 5175:80 nexora_admin_panel:test
  ```
- API local en http://127.0.0.1:8000 (contenedor `nexora_api`, corre con `--reload`, ya
  tiene los cambios de la sesión).

### 2. Desplegar la API a producción (necesita ventana + visto bueno)
Reinicia el servicio que autoriza el playback de clientes reales. Todo aditivo, 315 tests
verdes, pero hacerlo en horario de bajo tráfico. Patrón (igual que el web player):
SFTP de `app/` al servidor + `docker compose -f docker-compose.production.yml build api` +
`up -d --no-deps api`. **NUNCA `--remove-orphans`** (borra el stack de transcodificación).

### 3. Montar el panel de administración en producción
Ya se puede: su vhost depende del nginx que se arregló al desplegar el certificado.
Necesita su contenedor + un `server` nuevo en la config de nginx (mismo patrón que
tvdigital).

### 4. Crear el hook de renovación de certbot en el host
Con dos certificados de fechas distintas, sin hook la caducidad falla intermitente por
dominio. Script listo en `deploy/RUNBOOK_EDGE_MULTIDOMINIO.md`.

### 5. Verificar el hallazgo tc-mia
`tc-mia` (torre Miami) devolvió 200 a un stream SIN token; los otros 3 nodos dan 401.
Posible bypass del gate. Preexistente, del frente de transcodificación. Ver el roadmap.

---

## Decisiones de negocio pendientes (no bloquean nada técnico)
- ¿La Red vende servicios add-on (VOD/Timeshift) por cliente? → habilita esa función +
  migración de BD.
- ¿Límite de dispositivos por cliente, además de por plan? → migración de BD.

---

## Notas de operación que NO se deben olvidar
- **Producción NO es git.** El código llega por copia; comparar el conf vivo contra el
  versionado ANTES de tocar (ya evitó borrar la ruta de tc-main una vez).
- **Antes de activar cuentas de reseller**: los suscriptores con `created_by` nulo quedan
  invisibles para cualquier reseller (solo el admin los ve). Asignarles dueño con un UPDATE.
- Acceso SSH a producción: credencial en `.claude/settings.json` (bloque `nexora-ssh`), no
  en `.env`. El helper de paramiko para correr comandos es de patrón conocido; si el
  scratchpad de la sesión ya no existe, se reescribe en 30 líneas (lee la credencial dentro,
  censura la salida, `sudo -S` por stdin).

# Nexora Web Player

Frontend Nexora-native para clientes web/TV. Consume exclusivamente:

- `/api/client/auth/*`
- `/api/client/catalog/*`
- `/api/client/playback/*`
- `/api/client/profile/*`

Player OTT nativo de Nexora — consume exclusivamente el Modern Client API.

## Flujo de reproducción (Flussonic)

```
1. POST /api/client/auth/login
   <- { access_token, refresh_token, subscriber_id }

2. GET /api/client/catalog/channels
   <- [{ id, channel_key, number, name, category, logo_url }]
      (stream_key NUNCA expuesto al cliente)

3. POST /api/client/playback/authorize { channel_id, device_id }
   <- {
        token: "<JWT 60s>",
        expires_in: 60,
        channel_id: "canal-1",
        subscriber_id: "...",
        playback_url: "http://181.78.246.211:8002/ECUADOR_TV/index.m3u8"
      }

4. hls.js.loadSource(playback_url)
   <- reproduce directo desde Flussonic
      Nexora no hace proxy de video.

5. POST /api/client/profile/devices/heartbeat cada 45s
   <- { subscription_active, active_connections }
```

## Configuración

Crear `web_player/.env` (ya existe con defaults):

```env
VITE_NEXORA_API_BASE_URL=
VITE_NEXORA_HEARTBEAT_INTERVAL_MS=45000
VITE_NEXORA_PLAYBACK_RENEW_SKEW_SECONDS=15
VITE_NEXORA_TOKEN_REFRESH_SKEW_SECONDS=120
VITE_NEXORA_APP_VERSION=web-player-0.1.0
```

`VITE_NEXORA_API_BASE_URL` vacío = usa el proxy de Vite (`/api -> http://localhost:8000`).

**Nunca agregar credenciales Flussonic aquí.** El backend las maneja internamente.

## Desarrollo

```bash
npm install
npm run dev      # http://localhost:5173
```

El proxy en `vite.config.ts` redirige `/api/*` al backend en `http://localhost:8000`:

```typescript
proxy: {
  "/api": { target: "http://localhost:8000", changeOrigin: true }
}
```

Esto elimina CORS en desarrollo — el backend debe estar corriendo en `:8000`.

## Build

```bash
npm run build    # dist/
npm run preview  # preview en :4173 (también con proxy)
```

## Credenciales de prueba

```
Username: testuser1
Password: NexoraTest123!
device_id: test-device-001
```

Suscripción activa hasta 2026-06-17.

## Estado de integración

| Feature | Estado |
|---------|--------|
| Login con tokens JWT | ✅ funcional |
| Catálogo 21 canales desde DB | ✅ funcional |
| Playback authorize + URL HLS | ✅ funcional |
| Heartbeat autenticado | ✅ funcional |
| Refresh token (rotación 90d) | ✅ funcional |
| Logout | ✅ funcional |
| hls.js reproducción en navegador | ✅ funcional |
| Error handling HLS (stream DOWN) | ⏳ pendiente |
| Signed URLs / backend-auth formal | ⏳ Fase 4 |

## Pendientes (Fase 4)

1. Manejo de errores: stream DOWN, 401 en HLS URL, timeout
2. Retry automático con backoff exponencial
3. Signed URLs — Flussonic backend-auth via `/api/stb/auth/validate`

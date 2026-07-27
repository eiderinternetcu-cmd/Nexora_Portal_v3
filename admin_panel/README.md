# Nexora Admin Panel

Panel de administracion de Nexora. App independiente, hermana de `web_player/`,
con el mismo stack: Vite + React + TypeScript, CSS plano, estado con hooks.
Sin Redux, sin Tailwind, sin librerias de UI.

## Arrancar en dev

```bash
cd admin_panel
npm install
npm run dev            # http://localhost:5174
```

La API tiene que estar levantada en `http://localhost:8000` (el proxy de Vite
manda `/api` alli). Puertos 5174/4174 para no chocar con el player (5173/4173):
puedes tener los dos corriendo a la vez.

```bash
npm run typecheck      # tsc --noEmit
npm run build          # tsc -b && vite build
npm run preview        # sirve dist/ en 4174
```

---

# Contrato para quien construye pantallas

Los cimientos (`src/api/`, `src/auth/`, `src/app/`, `src/ui/shell/`,
`src/ui/primitives/`, `src/styles/admin.css`) son estables. Tu pantalla vive en
`src/features/<seccion>/`.

## 1. Registrar una seccion

Unico punto de registro: **`src/app/sections.tsx`**. Ni `App.tsx` ni
`Sidebar.tsx` se tocan; la ruta, el enlace lateral y el guard salen de ahi.

```tsx
// 1. tu pantalla, sin props
// src/features/planes/PlanesView.tsx
export function PlanesView() { ... }

// 2. en src/app/sections.tsx
import { PlanesView } from "../features/planes/PlanesView";

// 3. cambia SOLO la linea `component:` de tu entrada
{ path: "/planes", label: "Planes", icon: Package, component: PlanesView },
```

Subrutas (`/suscriptores/:id`): anade otra entrada con `hidden: true` justo
despues de la ruta padre.

Permisos: `roles: ["admin"]` oculta el enlace y bloquea la entrada a los
reseller. Es solo ergonomia de UI — la autorizacion real la hace la API. Nunca
supongas que ocultar un enlace protege un dato.

Varios agentes editan `sections.tsx` a la vez: toca solo tu linea y tu import,
no reordenes el array.

Rutas ya registradas, todas con placeholder: `/dashboard`, `/suscriptores`,
`/planes`, `/dispositivos`, `/usuarios` (solo admin), `/canales`, `/sesiones`,
`/auditoria`.

## 2. Hablar con la API

```tsx
import { useApi, useAuth } from "../../auth/AuthContext";
import { messageForError } from "../../api/errors";
import type { Subscriber } from "../../api/types";

const api = useApi();
const { user, role, isAdmin } = useAuth();

const page = await api.listSubscribers({ page: 1, page_size: 50 });
page.data;   // Subscriber[]
page.total;  // number
```

- Cada metodo devuelve **el JSON ya parseado y tipado**. Fijate en la envoltura:
  `PaginatedResponse<T>` y `ApiResponse<T>` traen los datos en `.data`
  (en `ApiResponse` puede ser `null`); `channels`, `sessions/live`, `metrics`,
  `nodes/health` y `audit` devuelven el objeto o el array **desnudo**. Los tipos
  lo reflejan; no adivines.
- Auth, refresco de token y 401 estan resueltos. No pongas cabeceras a mano.
- Todo fallo lanza `ApiError` con `.status` y `.payload`. En la UI:
  `catch (err) { toast.error(messageForError(err)) }`.
- Un 401 irrecuperable cierra la sesion global y te manda al login solo.
- Si tu endpoint no tiene metodo propio, **no lo anadas a medias**: usa los
  genericos, que hacen exactamente lo mismo con la ruta relativa al prefijo.

```tsx
await api.get<ApiResponse<Foo>>("/foo", { query: { page: 1 } });
await api.post<MessageResponse>("/foo/1/bar", { reason: "x" });
await api.del<MessageResponse>("/foo/1");
```

Prefijo por defecto: `/api/admin`. Es el superconjunto: `/api/v1` es el alias
legacy del mismo router de auth/users/subscribers/devices/plans y **no** tiene
sessions, subscriptions, channels, metrics, alerts ni audit.

Los tipos de `src/api/types.ts` salen de los Pydantic reales de `app/schemas/`.
Si algo no cuadra, corrige ahi y avisa; no dupliques tipos en tu feature.

## 3. Primitivas de UI

Importa siempre desde `src/ui/primitives`:

```tsx
import {
  Button, Field, TextInput, Select, TextArea,
  DataTable, Modal, ConfirmDialog,
  StatusBadge, toneForSubscriberStatus, toneForBoolean,
  useToast,
} from "../../ui/primitives";
```

| Primitiva | Props principales |
|---|---|
| `Button` | `variant` `primary\|secondary\|ghost\|danger`, `size` `sm\|md`, `loading`, `icon`, `block`, + props de `<button>`. `type` es `"button"` por defecto: pon `type="submit"` en formularios. |
| `Field` | `label`, `error`, `hint`, `required`, `children` como **render prop** que recibe `{ id, aria-describedby, aria-invalid }` para colgarlos del control. |
| `TextInput` / `Select` / `TextArea` | props nativas del elemento, ya estilados. |
| `DataTable<T>` | `columns: Column<T>[]`, `rows: T[]`, `rowKey`, `loading`, `error`, `onRetry`, `emptyMessage`, `onRowClick`, `footer`, `caption`. Precedencia de estados: error > loading > vacio > filas. |
| `Column<T>` | `{ key, header, render: (row) => ReactNode, width?, align? }` |
| `Modal` | `open`, `title`, `onClose`, `footer`, `size` `sm\|md\|lg`, `dismissable`. |
| `ConfirmDialog` | `open`, `title`, `message`, `onConfirm`, `onCancel`, `confirmLabel`, `cancelLabel`, `tone` `default\|danger`, `busy`. |
| `StatusBadge` | `tone` `neutral\|success\|warning\|danger\|info`, `dot`. Usa `toneForSubscriberStatus(status)` para no divergir entre pantallas. |
| `useToast()` | `{ push, success, error, info, dismiss }`. El provider ya esta montado en `App`; no montes otro. |

Ejemplo tipico de listado:

```tsx
<DataTable
  columns={[
    { key: "user", header: "Usuario", render: (r) => r.username },
    { key: "status", header: "Estado", render: (r) => (
        <StatusBadge tone={toneForSubscriberStatus(r.status)} dot>{r.status}</StatusBadge>
      ) },
  ]}
  rows={rows}
  rowKey={(r) => r.id}
  loading={loading}
  error={error}
  onRetry={load}
/>
```

## 4. Estilos

Tokens en `src/styles/admin.css`: `--brand`, `--bg`, `--surface`, `--surface-2`,
`--surface-3`, `--text`, `--text-muted`, `--text-dim`, `--border`, `--ok`,
`--warn`, `--danger`, `--info`, `--radius*`. Los colores usados con
transparencia traen tambien su triplete `--x-rgb` para `rgba(var(--x-rgb), a)`.

Ese archivo es de los cimientos (tokens, reset, shell, primitivas). Para tu
pantalla crea `src/features/<seccion>/<seccion>.css`, importalo desde tu
componente y prefija las clases con el nombre de tu seccion.

## 5. Sesion

Claves de `localStorage`: **`nexora.admin.*`**, nunca `nexora.web_player.*`.
Compartirlas desloguearia al usuario del player al abrir el panel, y ademas los
tokens de una superficie no valen en la otra. No escribas en `localStorage`
para nada relacionado con la sesion; usa `useAuth()`.

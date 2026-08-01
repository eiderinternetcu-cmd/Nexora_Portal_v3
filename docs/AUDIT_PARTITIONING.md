# AUDIT_PARTITIONING.md — particionado y retención de `audit_logs`

_Migración: `migrations/versions/011_audit_logs_partition_and_retention.py`_
_Última actualización: 2026-08-01_

---

## Resumen

`audit_logs` es el rastro de auditoría administrativa. La migración **007** lo hizo
**append-only** con un trigger `BEFORE UPDATE OR DELETE` que lanza excepción: la tabla
solo puede crecer. La migración **011** añade lo que faltaba para que ese crecimiento
sea gestionable:

- particionado **RANGE mensual** por `created_at`;
- una **política de retención** que se despliega **desactivada**;
- creación **automática** de particiones futuras;
- una **partición DEFAULT** para que un mes sin partición nunca tumbe el login.

La regla que gobierna todo el diseño: **borrar particiones enteras es legítimo
(`DROP`, que es DDL y no dispara triggers de fila); editar filas no lo es.**

---

## 1. Método de particionado: ATTACH en sitio

Postgres no convierte una tabla existente en particionada con un `ALTER`. Había dos
caminos y se eligió el segundo:

| | Copiar y cambiar | **ATTACH en sitio (elegido)** |
|---|---|---|
| Datos | Reescribe cada fila | **No copia ni una fila** |
| Espacio | Necesita el doble | El mismo fichero de siempre |
| Riesgo | Ventana en la que se pueden perder o duplicar filas | Ninguna copia que pueda salir mal |

En producción esa tabla tiene el rastro real de auditoría y **perderlo sería peor que
no particionar**. `ATTACH` renombra la tabla actual, la mueve al esquema `audit_part`
y la engancha tal cual como primera partición del nuevo padre. Como el DDL de Postgres
es transaccional, un fallo en cualquier punto deja la tabla exactamente como estaba.

### ⚠️ Ventana de escritura bloqueada — decláralo en el cambio

La conversión mantiene un **`ACCESS EXCLUSIVE` sobre `audit_logs` durante toda la
migración**: mientras corre, **las escrituras a `audit_logs` se bloquean**. Como las
filas de auditoría se escriben en el camino de autenticación, **los logins se quedan
esperando** durante esa ventana (esperan, no fallan).

El coste lo domina **una única construcción de índice**: la PK del padre pasa a ser
`(id, created_at)` y ese índice no existe en la tabla vieja, así que Postgres lo
construye sobre las filas existentes durante el `ATTACH`. Todo lo demás (renombrar,
mover de esquema, catálogo) es O(1). Los cinco `ix_audit_logs_*` **no se reconstruyen**:
se crean antes en el padre y `ATTACH` adopta los idénticos que ya existían.

> Presupuesta la ventana como *«un btree sobre `audit_logs`»* y ejecútala en horario
> valle.

### La PK cambia

Postgres exige que toda restricción única de una tabla particionada contenga la clave
de partición: `PRIMARY KEY (id)` → **`PRIMARY KEY (id, created_at)`**.
`app/models/audit.py` se actualizó para que ORM y esquema migrado sigan coincidiendo.

Lo que se pierde: `id` por sí solo ya no está *forzado* como único (Postgres no puede
construir un índice único global que omita la clave de partición). En la práctica es
casi gratis — `id` es un UUIDv4 generado por la aplicación y el par sigue siendo único —
pero queda escrito porque «cambió la clave primaria» no es algo que deba descubrirse por
accidente.

---

## 2. Inmutabilidad: qué sobrevive y por qué

El trigger se crea **en el padre y antes de que exista ninguna partición**. Postgres 13+
**clona** los triggers `FOR EACH ROW` del padre a cada partición, incluidas las que se
adjunten o creen después. No hay ninguna lista que mantener.

Garantías, verificadas en `tests/test_audit_partitioning.py` (22 tests):

- `UPDATE` y `DELETE` se rechazan **por el padre** y **atacando directamente cada
  partición** (histórica, mensual y DEFAULT), sobre filas que existen de verdad;
- una partición **creada después** de la migración también queda protegida;
- el trigger clonado **no se puede quitar de una sola partición**: Postgres lo
  rechaza. Es *más* fuerte que antes de particionar — es todo o nada, en el padre.

```
nexora=> UPDATE audit_logs SET action='tampered';
ERROR:  audit_logs is append-only: UPDATE is not allowed
nexora=> DELETE FROM audit_part.audit_logs_default;
ERROR:  audit_logs is append-only: DELETE is not allowed
nexora=> DROP TRIGGER audit_logs_no_update_delete ON audit_part.audit_logs_2026_10;
ERROR:  cannot drop trigger audit_logs_no_update_delete on table
        audit_part.audit_logs_2026_10 because trigger audit_logs_no_update_delete
        on table audit_logs requires it
```

**Sin cambios respecto a antes:** el propietario de la tabla todavía puede
`ALTER TABLE ... DISABLE TRIGGER`. Eso ya era cierto con la tabla única de 007;
particionar no quita nada. Sigue siendo un acto deliberado y privilegiado.

### Verificación post-despliegue

```sql
-- Toda partición debe aparecer aquí. Si alguna falta, está desprotegida.
SELECT c.relname, t.tgenabled
FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
LEFT JOIN pg_trigger t
       ON t.tgrelid = c.oid AND t.tgname = 'audit_logs_no_update_delete'
WHERE i.inhparent = 'public.audit_logs'::regclass;
```

`tgenabled` debe ser `O` en todas. Cualquier `D` significa protección **deshabilitada**.

---

## 3. Retención: qué se conserva y durante cuánto

> Una auditoría con retención silenciosa es una auditoría en la que no se puede
> confiar. Por eso la política es explícita, consultable y **no borra nada por su
> cuenta**.

**Valores por defecto (conservadores, en `audit_part.retention_policy`):**

| Campo | Defecto | Significado |
|---|---|---|
| `enabled` | **`false`** | **No se borra NADA** hasta que una persona lo active |
| `retain_months` | **`84`** (7 años) | Horizonte habitual de conservación mercantil/fiscal |
| `premake_months` | `3` | Meses futuros que se crean por adelantado |

**Qué se conserva, en una frase:** *nada se borra jamás salvo que alguien active
`enabled`; una vez activo, una partición mensual se elimina solo cuando **todo** su
rango es más antiguo que `retain_months`.* La retención es por meses completos, así que
la garantía efectiva es «**al menos** `retain_months`».

Dos puertas independientes protegen cada borrado:

1. el flag `enabled` almacenado, y
2. el argumento `p_dry_run`, que **vale `true` por defecto** — la llamada descuidada
   informa de lo que *borraría* y no borra nada.

Además, la partición histórica anterior a 011 no tiene límite inferior (`FROM MINVALUE`),
así que eliminarla descartaría un tramo de historia de tamaño desconocido de golpe en
lugar de un mes. **Una ejecución rutinaria nunca la toca**; hace falta pedirlo
explícitamente con `p_include_unbounded => true`.

### Ver qué se borraría (no borra nada)

```sql
SELECT * FROM audit_part.apply_retention();      -- dry run
```

### Activar la retención (decisión deliberada)

```sql
UPDATE audit_part.retention_policy
   SET enabled = true,
       retain_months = 84,
       updated_at = now(),
       updated_by = 'nombre.apellido';
```

Revisa **siempre** el dry run antes. Después, `audit_part.maintain()` aplicará la
política en cada ejecución programada.

---

## 4. Particiones futuras, y el día que nadie creó la del mes que viene

Las filas de auditoría se escriben en el camino de autenticación. En una tabla
particionada, un `INSERT` que no encaja en ninguna partición falla con
`no partition of relation ... found for row` — es decir, **un trabajo de mantenimiento
olvidado se convertiría en una caída total del login**. Inaceptable para algo que solo
registra.

Dos defensas independientes:

**(a) Partición `DEFAULT`.** `audit_part.audit_logs_default` absorbe cualquier fila que
no encaje. El login sigue funcionando, la fila se escribe, se consulta por
`audit_logs` como cualquier otra y **es igual de inmutable** (hereda el trigger).

**(b) Creación anticipada.** `audit_part.ensure_partitions()` crea el mes actual y
`premake_months` por delante. La migración la ejecuta una vez, así que hay que
descuidar el mantenimiento **más de tres meses** para siquiera llegar a (a).

### Programar el mantenimiento

Una sola llamada, idempotente y segura desde varios procesos a la vez:

```sql
SELECT audit_part.maintain();   -- crea particiones + aplica retención (si está activa)
```

Desde la aplicación (`app/services/audit_service.py`):

```python
await AuditService(db).ensure_partitions()          # crea meses futuros
await AuditService(db).apply_retention(dry_run=True) # informe, no borra
```

Ambos métodos **no hacen nada** en una base construida desde el ORM
(`Base.metadata.create_all()`, que es como se levantan los tests): comprueban antes si
la maquinaria `audit_part` existe.

Conéctalo a cron, a un systemd timer o a `pg_cron`, **mensual o semanal**. La frecuencia
no es crítica gracias a (a) y (b).

### Runbook: consolidar filas que cayeron en `DEFAULT`

Si el mantenimiento se descuida lo suficiente, llegarán filas del mes *M* a la partición
`DEFAULT`. A partir de ahí, **crear la partición de *M* falla** (`SQLSTATE 23514`):
Postgres no deja que una partición nueva robe filas de la default.

`ensure_partition` **avisa y continúa** en lugar de abortar toda la ejecución. No hay
prisa: **las filas están seguras, se consultan igual y siguen siendo inmutables** donde
están. Dejarlas ahí es una opción perfectamente válida.

Consolidarlas es una operación manual y deliberada **a propósito**: sacar filas de la
partición DEFAULT significa borrarlas de ella, y eso es justo lo que 007 prohíbe.

**El detalle que hay que entender antes de ejecutar nada:** `DETACH PARTITION`
**elimina el trigger clonado**. En cuanto la partición se separa del padre deja de estar
protegida y acepta `DELETE` sin más — no hace falta (ni funciona) un
`DISABLE TRIGGER`. Es decir: **el `DETACH` del paso 1 *es* el momento en que se levanta
la garantía de 007**, y se restablece sola al reenganchar. Por eso este procedimiento
debe hacerse **en una ventana anunciada, en una única transacción y quedando
registrado**.

Ejemplo, consolidando el mes 2027-05 (verificado sobre una base real):

```sql
BEGIN;
SELECT count(*) FROM audit_part.audit_logs_default;   -- recuento antes

-- 1. Aislar la default. ⚠️ aquí desaparece el trigger clonado
ALTER TABLE public.audit_logs DETACH PARTITION audit_part.audit_logs_default;

-- 2. Crear el mes que faltaba (ya no hay solape con la default)
SELECT audit_part.ensure_partition('2027-05-01 00:00:00+00'::timestamptz);

-- 3. Mover las filas: el INSERT las enruta a su partición mensual...
INSERT INTO public.audit_logs
SELECT * FROM audit_part.audit_logs_default
WHERE created_at >= '2027-05-01+00' AND created_at < '2027-06-01+00';

-- ...y el DELETE ya está permitido porque la tabla está separada del padre
DELETE FROM audit_part.audit_logs_default
WHERE created_at >= '2027-05-01+00' AND created_at < '2027-06-01+00';

-- 4. Reenganchar: Postgres vuelve a clonar el trigger automáticamente
ALTER TABLE public.audit_logs ATTACH PARTITION audit_part.audit_logs_default DEFAULT;

SELECT tableoid::regclass, count(*) FROM audit_logs GROUP BY 1 ORDER BY 1;
COMMIT;
```

Comprueba **dentro de la misma transacción** que el total de filas no ha cambiado, y
después que la protección volvió:

```sql
SELECT tgname, tgparentid <> 0 AS clonado FROM pg_trigger
WHERE tgrelid = 'audit_part.audit_logs_default'::regclass AND NOT tgisinternal;
```

> Los pasos 1–4 son el único punto de todo este documento en el que la garantía de 007
> se levanta. Si no estás dispuesto a justificarlo por escrito, **deja las filas en
> `DEFAULT`**: ahí están seguras, se consultan igual y siguen siendo inmutables.

---

## 5. Referencia rápida

```sql
-- Particiones y sus rangos
SELECT c.relname, pg_get_expr(c.relpartbound, c.oid)
FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid
WHERE i.inhparent = 'public.audit_logs'::regclass ORDER BY 1;

-- Filas por partición
SELECT tableoid::regclass AS particion, count(*) FROM audit_logs GROUP BY 1 ORDER BY 1;

-- Política vigente
SELECT * FROM audit_part.retention_policy;
```

| Objeto | Para qué |
|---|---|
| `audit_part.ensure_partition(ts)` | Crea la partición del mes de `ts`. Idempotente |
| `audit_part.ensure_partitions(n)` | Mes actual + `n` por delante |
| `audit_part.apply_retention(dry_run, include_unbounded)` | Informa/elimina particiones caducadas |
| `audit_part.maintain()` | Las dos anteriores; la llamada para el planificador |
| `audit_part.retention_policy` | Configuración (fila única) |

**No hay nada de esto mapeado en el ORM, y es intencionado:** es gestión física de
almacenamiento. La aplicación sigue dirigiéndose a `public.audit_logs` y a nada más.
Por eso las particiones viven en `audit_part` y no en `public` — si estuvieran en
`public`, `Inspector.get_table_names()` las reflejaría y
`tests/test_migration_schema_parity.py` reportaría una divergencia fantasma nueva **cada
mes**.

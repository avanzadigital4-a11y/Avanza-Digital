# Adopción de Alembic — playbook (cuando el esquema se estabilice)

Este kit (`alembic.ini` + `migrations/`) está **inerte**. No se importa ni corre
en el arranque de la app. Hoy el sistema de migraciones vigente sigue siendo:

- `main.py` → `_aplicar_migracion(...)` (hoy **91** sentencias `ALTER TABLE`
  idempotentes que corren en cada boot), más `Base.metadata.create_all`.
- `check_migraciones.py` → script manual de reparación en el shell de Render
  (otras **10** `ALTER`).

## ¿Ya conviene migrar a Alembic?

Mi lectura honesta: **todavía no.** El gate que pusiste fue "cuando el esquema se
estabilice", y 91 + 10 = ~100 `ALTER` acumuladas, todavía creciendo, dicen que
el esquema sigue en movimiento. Mientras agregues columnas seguido, la lista
idempotente actual es más barata que mantener una cadena de revisiones Alembic.

La señal para dar el salto: cuando pasen **varias semanas sin agregar columnas**
y la lista de `_aplicar_migracion` deje de crecer. Ahí Alembic empieza a pagar
(renames, drops, cambios de tipo, backfills, y `downgrade` — nada de eso lo hace
el sistema actual, que solo sabe agregar columnas).

## Pasos de adopción (sobre la DB de prod que YA existe)

El punto delicado es no recrear tablas que ya están en producción. Por eso el
paso `stamp`.

1. **Instalar** (agregar a `requirements.txt`):
   ```
   alembic==1.13.2
   ```

2. **Generar la línea base** apuntando a un Postgres que refleje el esquema
   actual de prod (idealmente un dump restaurado, NO prod directo):
   ```bash
   export DATABASE_URL="postgresql://...."   # base con el esquema actual
   alembic revision --autogenerate -m "baseline esquema actual"
   ```
   Revisá el script generado en `migrations/versions/`. Debería describir el
   esquema actual completo. Si aparecen `ALTER`/`DROP` raros, es que la metadata
   y la DB difieren — corregí antes de seguir.

3. **Marcar prod como "ya en la línea base"** (sin ejecutar el SQL, solo sella la
   versión para que Alembic no intente recrear lo que ya existe):
   ```bash
   export DATABASE_URL="<URL real de prod>"
   alembic stamp head
   ```

4. **De acá en adelante**, cada cambio de esquema = una revisión:
   ```bash
   alembic revision --autogenerate -m "agrega columna X a aliados"
   # revisar el script
   alembic upgrade head
   ```

5. **Retirar el sistema viejo** (recién cuando Alembic esté validado en prod):
   - Sacar el bloque de `_aplicar_migracion(...)` / lista de `ALTER` de `main.py`.
   - Dejar `Base.metadata.create_all` solo para bootstrap local, o reemplazarlo
     por `alembic upgrade head` en el `build.sh` / `Procfile` de release.
   - Archivar `check_migraciones.py` (queda como referencia histórica).

## Notas específicas de este proyecto

- `env.py` resuelve la URL igual que `database.py` (lee `DATABASE_URL`, corrige
  `postgres://` → `postgresql://`, cae a SQLite local). No toques `alembic.ini`.
- `target_metadata = Base.metadata` con `import models`. Si creás un módulo de
  modelos nuevo, asegurate de que quede importado antes de autogenerar.
- `render_as_batch` está activado solo en SQLite (los `ALTER` ahí recrean la
  tabla). En Postgres no aplica.
- `compare_type` y `compare_server_default` están en `True` para que el
  autogenerate detecte cambios de tipo y de default.

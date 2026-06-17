# Backups de la base (Supabase free tier) — guía

Tu stack: frontend en Netlify, backend en Render, **base en Supabase free**.
El punto crítico: **el free tier de Supabase NO hace backups automáticos**. Si
borrás datos por accidente, corrés una migración mala o el proyecto se corrompe,
no hay forma de restaurar. Con comisiones, ventas y enlaces de pago adentro, eso
es justo lo que no podés permitirte.

Como todavía no es momento de pagar el plan Pro ($25/mes, que traería backups
diarios automáticos), armamos la alternativa gratis que la propia Supabase
recomienda: **exportar la base regularmente y guardarla off-site**.

## Lo que quedó automatizado (no tenés que hacer nada cada día)

`backup_db.py` + un job en el scheduler de Render hacen, **una vez por día a las
6 AM UTC (~3 AM Argentina)**, esto solo:

1. Vuelcan TODA la base a un archivo `.sql` (con Python puro vía SQLAlchemy —
   no usa `pg_dump`, que no existe en el runtime de Render).
2. Lo comprimen con gzip (queda en ~40% del tamaño).
3. Te lo mandan como adjunto al `ADMIN_EMAIL` usando la misma cadena de envío
   del sistema (Brevo → Resend).

Va envuelto en `con_lock`, así que si algún día falla, **Sentry te avisa**
(si activaste el DSN). Y como corre en el scheduler, **se hace solo para
siempre** — no depende de que te acuerdes ni de tener la compu prendida.

### Lo único que tenés que configurar (una vez)
- Confirmá que `ADMIN_EMAIL` apunta a un correo que revises (hoy:
  `contacto@avanzadigital.digital`). Si querés que el backup vaya a OTRA casilla
  distinta de las notificaciones, seteá `BACKUP_EMAIL` en Render.
- Asegurate de que `BREVO_API_KEY` (o `RESEND_API_KEY`) esté seteada en Render
  — son las mismas que ya usás para mandar emails, así que probablemente ya
  estén.

### El correo te llega solo. Guardalo.
Cada backup llega a tu inbox y queda ahí archivado (Gmail ya respalda tu correo
sin que hagas nada). Esa ES tu copia off-site. No borres esos correos; si querés,
armales un filtro/etiqueta "Backups Avanza" para tenerlos juntos.

## Cómo RESTAURAR un backup (cuando lo necesites)

1. Bajá el adjunto `avanza_backup_AAAAMMDD_HHMM.sql.gz` del email.
2. Descomprimilo:
   ```bash
   gunzip avanza_backup_20260613_0600.sql.gz
   ```
3. Cargalo en una base. **La primera vez, probá contra una base NUEVA**, no
   contra producción, para confirmar que todo vuelve bien:
   ```bash
   psql "postgresql://...tu-conexion-supabase..." < avanza_backup_20260613_0600.sql
   ```
   El dump arranca con `SET session_replication_role = replica`, que desactiva
   triggers/FKs durante la carga, así que entra de una sola pasada sin pelearse
   con el orden de las tablas.

> Probá una restauración de prueba **una vez ahora** (a una base nueva) para
> tener la certeza de que funciona. Un backup que nunca se restauró es solo una
> esperanza. Ya validé el round-trip en código, pero confirmarlo contra tu
> Supabase real te deja tranquilo.

## El límite que vas a encontrar algún día (y qué hacer)

Los adjuntos de email topan ~25 MB. Tu base hoy entra holgada (las tablas son
texto, livianas, y comprimidas pesan poquísimo). Pero si algún día el backup
comprimido supera los 20 MB, el job **no manda un adjunto roto**: detecta el
exceso y te manda un email avisando que es hora de migrar a almacenamiento
externo (Cloudflare R2/S3, gratis) o al plan Pro de Supabase.

Para entonces, lo más probable es que ya tengas ingresos del programa de
aliados y los $25/mes de Supabase Pro (backups diarios automáticos + Point-in-
Time Recovery, sin mantenimiento) sean la opción obvia. Hasta ese día, el
backup por email te cubre gratis.

## Resumen
- **Ahora:** confirmá `ADMIN_EMAIL`/`BREVO_API_KEY` en Render, y hacé una
  restauración de prueba una vez.
- **Cada día:** nada — el job lo hace solo y te llega al correo.
- **El día que crezca:** migrá a R2 o a Supabase Pro (el job te va a avisar).

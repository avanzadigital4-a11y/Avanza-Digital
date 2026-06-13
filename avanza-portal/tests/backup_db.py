"""
backup_db.py — Copia de seguridad diaria de Postgres por email (gratis).

POR QUÉ EXISTE:
  La base vive en Supabase free tier, que NO hace backups automáticos. Hay
  comisiones, ventas y enlaces de pago adentro: perderlos destruye la confianza
  de los aliados en cobrar. Supabase mismo recomienda que los proyectos free
  exporten sus datos regularmente y los guarden off-site.

CÓMO FUNCIONA:
  Un job del scheduler (en main.py, envuelto en con_lock → si falla, Sentry
  avisa) corre una vez al día, vuelca TODA la base a un .sql, lo comprime con
  gzip y lo manda como adjunto al ADMIN_EMAIL usando la misma cadena de envío
  que el resto del sistema (Brevo → Resend). El correo queda guardado en tu
  inbox, que ya está respaldado sin que hagas nada.

POR QUÉ NO USA pg_dump:
  El runtime de Render es Python puro y no trae el binario pg_dump. Este módulo
  serializa la base con SQLAlchemy/psycopg2 (que ya usás), así que no depende de
  binarios externos. El .sql resultante es restaurable con `psql < dump.sql`.

LÍMITE A TENER EN CUENTA:
  Los adjuntos de email topan ~25 MB. La base de texto (comisiones, ventas,
  aliados) entra de sobra comprimida, pero si algún día el dump supera el límite,
  el job lo detecta, NO manda un adjunto roto, y te avisa por email que es hora
  de pasar a almacenamiento externo (Cloudflare R2/S3) o al plan Pro de Supabase.

RESTAURAR un backup:
  1. Descargá el .sql.gz del email y descomprimilo:  gunzip avanza_backup_*.sql.gz
  2. Restaurá sobre una base (idealmente NUEVA, para probar primero):
     psql "postgresql://...supabase..." < avanza_backup_*.sql
  El dump arranca deshabilitando triggers y respeta el orden de las tablas por
  dependencias de FK, así que se puede cargar de una sola pasada.
"""
import base64
import gzip
import io
import os
from datetime import datetime, timezone

import httpx
from sqlalchemy import inspect, text

from database import engine

# Tope defensivo para el adjunto. Brevo/Resend aceptan hasta ~25-40 MB según
# proveedor; nos quedamos holgados en 20 MB de archivo COMPRIMIDO.
_MAX_ADJUNTO_BYTES = 20 * 1024 * 1024


def _es_postgres() -> bool:
    return engine.url.get_backend_name().startswith("postgres")


def _orden_tablas_por_fk(insp) -> list:
    """Orden topológico simple: tablas sin FK primero, luego las que dependen.

    Para un dump con FKs activas el orden importa. Igual el dump desactiva
    triggers durante la carga, así que esto es una mejora de robustez, no un
    requisito estricto.
    """
    tablas = insp.get_table_names()
    deps = {t: set() for t in tablas}
    for t in tablas:
        for fk in insp.get_foreign_keys(t):
            ref = fk.get("referred_table")
            if ref and ref in deps and ref != t:
                deps[t].add(ref)

    ordenadas, vistas = [], set()
    # iterar hasta colocar todas (grafo chico, sin ciclos esperables)
    while len(ordenadas) < len(tablas):
        progreso = False
        for t in tablas:
            if t in vistas:
                continue
            if deps[t] <= vistas:
                ordenadas.append(t)
                vistas.add(t)
                progreso = True
        if not progreso:
            # ciclo de FK (raro): agregamos las restantes en cualquier orden
            for t in tablas:
                if t not in vistas:
                    ordenadas.append(t)
                    vistas.add(t)
            break
    return ordenadas


def _sql_literal(v) -> str:
    """Serializa un valor Python a un literal SQL seguro para INSERT."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return "'\\x" + v.hex() + "'"  # bytea formato hex de Postgres
    if isinstance(v, datetime):
        return "'" + v.isoformat() + "'"
    # texto: escapar comillas simples duplicándolas
    s = str(v).replace("'", "''")
    return "'" + s + "'"


def generar_dump_sql() -> str:
    """Vuelca toda la base a un string SQL de INSERTs restaurable con psql.

    Recorre cada tabla por lotes (no carga todo en memoria de golpe) y arma
    sentencias INSERT. Encabeza el dump deshabilitando triggers para que el
    orden de carga y las FKs no bloqueen la restauración.
    """
    insp = inspect(engine)
    tablas = _orden_tablas_por_fk(insp)
    buf = io.StringIO()
    ahora = datetime.now(timezone.utc).isoformat()

    buf.write(f"-- Avanza Digital — backup lógico {ahora}\n")
    buf.write(f"-- Tablas: {len(tablas)} | motor: {engine.url.get_backend_name()}\n")
    buf.write("-- Restaurar: psql \"<DATABASE_URL>\" < este_archivo.sql\n\n")
    buf.write("SET session_replication_role = replica;  -- desactiva triggers/FK durante la carga\n\n")

    total_filas = 0
    with engine.connect() as conn:
        for tabla in tablas:
            cols = [c["name"] for c in insp.get_columns(tabla)]
            if not cols:
                continue
            col_list = ", ".join(f'"{c}"' for c in cols)
            buf.write(f"-- ─── {tabla} ───\n")

            # Lectura por lotes con server-side cursor lógico (offset simple;
            # las tablas del proyecto son chicas, así que es suficiente).
            res = conn.execution_options(stream_results=True).execute(
                text(f'SELECT {col_list} FROM "{tabla}"')
            )
            filas_tabla = 0
            lote = []
            for fila in res:
                valores = ", ".join(_sql_literal(v) for v in fila)
                lote.append(f"({valores})")
                filas_tabla += 1
                if len(lote) >= 500:
                    buf.write(f'INSERT INTO "{tabla}" ({col_list}) VALUES\n')
                    buf.write(",\n".join(lote))
                    buf.write(";\n")
                    lote = []
            if lote:
                buf.write(f'INSERT INTO "{tabla}" ({col_list}) VALUES\n')
                buf.write(",\n".join(lote))
                buf.write(";\n")
            buf.write(f"-- {filas_tabla} filas\n\n")
            total_filas += filas_tabla

    buf.write("SET session_replication_role = DEFAULT;  -- reactiva triggers/FK\n")
    buf.write(f"-- TOTAL: {total_filas} filas en {len(tablas)} tablas\n")
    return buf.getvalue()


def _enviar_email_con_adjunto(destinatario: str, asunto: str, cuerpo_html: str,
                              nombre_archivo: str, contenido_bytes: bytes) -> bool:
    """Envía un email con un adjunto binario. Cadena Brevo → Resend (igual que
    notificaciones.enviar_email, pero esa no soporta adjuntos y no la tocamos)."""
    b64 = base64.b64encode(contenido_bytes).decode()

    brevo_key = os.environ.get("BREVO_API_KEY", "")
    brevo_from = os.environ.get("BREVO_FROM", "no-reply@avanzadigital.digital")
    brevo_name = os.environ.get("BREVO_FROM_NAME", "Avanza Digital")
    if "<" in brevo_from and ">" in brevo_from:
        partes = brevo_from.split("<")
        brevo_name = partes[0].strip() or brevo_name
        brevo_from = partes[1].replace(">", "").strip()

    if brevo_key:
        try:
            resp = httpx.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": brevo_key, "Content-Type": "application/json"},
                json={
                    "sender": {"name": brevo_name, "email": brevo_from},
                    "to": [{"email": destinatario}],
                    "subject": asunto,
                    "htmlContent": cuerpo_html,
                    "attachment": [{"content": b64, "name": nombre_archivo}],
                },
                timeout=30.0,
            )
            if resp.status_code in (200, 201, 202):
                print(f"[BACKUP Brevo] OK → {destinatario} ({len(contenido_bytes)/1024:.0f} KB)")
                return True
            print(f"[BACKUP Brevo ERROR {resp.status_code}] {resp.text[:200]}")
        except Exception as e:
            print(f"[BACKUP Brevo EXCEPTION] {e}")

    resend_key = os.environ.get("RESEND_API_KEY", "")
    resend_from = os.environ.get("RESEND_FROM", "Avanza Digital <no-reply@avanzadigital.digital>")
    if resend_key:
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}",
                         "Content-Type": "application/json"},
                json={
                    "from": resend_from, "to": [destinatario],
                    "subject": asunto, "html": cuerpo_html,
                    "attachments": [{"filename": nombre_archivo, "content": b64}],
                },
                timeout=30.0,
            )
            if resp.status_code in (200, 202):
                print(f"[BACKUP Resend] OK → {destinatario} ({len(contenido_bytes)/1024:.0f} KB)")
                return True
            print(f"[BACKUP Resend ERROR {resp.status_code}] {resp.text[:200]}")
        except Exception as e:
            print(f"[BACKUP Resend EXCEPTION] {e}")

    print("[BACKUP] ❌ No se pudo enviar el backup por ningún proveedor.")
    return False


def ejecutar_backup() -> dict:
    """Genera el dump, lo comprime y lo manda por email. Devuelve un resumen.

    Es la función que llama el job del scheduler. Si la base es SQLite (local
    o tests) no hace nada: el backup solo tiene sentido contra el Postgres de
    producción.
    """
    if not _es_postgres():
        print("[BACKUP] Base no-Postgres (SQLite local/tests) — backup omitido.")
        return {"status": "skipped", "motivo": "no_postgres"}

    destino = os.environ.get("BACKUP_EMAIL") or os.environ.get("ADMIN_EMAIL", "")
    if not destino:
        print("[BACKUP] ⚠️ Sin ADMIN_EMAIL/BACKUP_EMAIL — no sé a dónde mandar el backup.")
        return {"status": "error", "motivo": "sin_destino"}

    sql = generar_dump_sql()
    crudo = sql.encode("utf-8")
    comprimido = gzip.compress(crudo, compresslevel=9)
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    nombre = f"avanza_backup_{fecha}.sql.gz"

    ratio = len(comprimido) / max(len(crudo), 1)
    print(f"[BACKUP] dump={len(crudo)/1024:.0f}KB → gz={len(comprimido)/1024:.0f}KB ({ratio:.0%})")

    # Si el comprimido excede el tope, NO mandamos un adjunto roto: avisamos.
    if len(comprimido) > _MAX_ADJUNTO_BYTES:
        aviso = (
            f"<div style='font-family:sans-serif;line-height:1.6'>"
            f"<h2>⚠️ Backup demasiado grande para email</h2>"
            f"<p>El backup comprimido pesa <b>{len(comprimido)/1024/1024:.1f} MB</b>, "
            f"por encima del límite de adjunto ({_MAX_ADJUNTO_BYTES/1024/1024:.0f} MB).</p>"
            f"<p>Es hora de mover los backups a almacenamiento externo "
            f"(Cloudflare R2/S3, gratis) o al plan Pro de Supabase (backups "
            f"automáticos). El backup de HOY no se guardó.</p></div>"
        )
        _enviar_email_con_adjunto(  # mandamos solo el aviso, sin adjunto
            destino, "⚠️ Backup Avanza superó el límite de email", aviso,
            "vacio.txt", b"backup omitido por tamano")
        return {"status": "too_big", "bytes": len(comprimido)}

    cuerpo = (
        f"<div style='font-family:sans-serif;line-height:1.6'>"
        f"<h2>🗄️ Backup diario de Avanza Digital</h2>"
        f"<p>Adjunto: <b>{nombre}</b> ({len(comprimido)/1024:.0f} KB comprimido).</p>"
        f"<p>Guardá este correo. Para restaurar: descomprimí con "
        f"<code>gunzip {nombre}</code> y cargá con "
        f"<code>psql \"&lt;DATABASE_URL&gt;\" &lt; avanza_backup_*.sql</code>.</p>"
        f"<p style='color:#777;font-size:.85rem'>Generado "
        f"{datetime.now(timezone.utc).isoformat()} UTC.</p></div>"
    )
    ok = _enviar_email_con_adjunto(
        destino, f"🗄️ Backup Avanza — {fecha}", cuerpo, nombre, comprimido)
    return {"status": "ok" if ok else "error",
            "archivo": nombre, "bytes": len(comprimido)}


def job_backup_diario():
    """Entry point del scheduler. Corre 1x/día (lo agenda main.py)."""
    return ejecutar_backup()
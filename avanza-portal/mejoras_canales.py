"""
mejoras_canales.py  ·  Migraciones idempotentes de las mejoras a Canal 1 / 2
================================================================================

Agrega, sin romper nada, las columnas nuevas a tablas YA existentes (aliados,
bolsa_leads, referidos). La tabla nueva `mentorias` la crea solo
Base.metadata.create_all() en el boot; acá NO se toca.

Sigue el mismo patrón que jarvis_setter.run_migrations(engine): correr una vez
al arrancar, después de create_all(). Es 100% idempotente — se puede correr en
cada deploy sin efectos. Soporta Postgres (prod) y SQLite (tests/local).

Llamar desde main.py, junto a las otras migraciones del boot:

    import mejoras_canales
    mejoras_canales.run_migrations(engine)
"""
from __future__ import annotations

import sys
from sqlalchemy import text


# (tabla, columna, tipo_sql, default_sql_o_None)
# Sin server-default en los flags de canal: se backfillean desde tipo_aliado
# (ver _backfill_canales). El modelo ORM ya trae defaults Python para inserts.
_COLUMNAS = [
    # --- ALIADOS: puente entre canales + rampa/mentor ---
    ("aliados", "canal1_habilitado",   "BOOLEAN",  None),
    ("aliados", "canal2_habilitado",   "BOOLEAN",  None),
    ("aliados", "canal_activo",        "VARCHAR",  "'canal1'"),
    ("aliados", "rampa_estado",        "VARCHAR",  "'nuevo'"),
    ("aliados", "rampa_recompensa_en", "TIMESTAMP", None),
    ("aliados", "primer_cierre_en",    "TIMESTAMP", None),
    ("aliados", "mentor_id",           "INTEGER",  None),
    ("aliados", "es_mentor",           "BOOLEAN",  "FALSE"),
    # --- BOLSA_LEADS: reciclado ---
    ("bolsa_leads", "intentos",            "INTEGER",  "0"),
    ("bolsa_leads", "reciclados",          "INTEGER",  "0"),
    ("bolsa_leads", "historial_intentos",  "TEXT",     "'[]'"),
    ("bolsa_leads", "cooldown_hasta",      "TIMESTAMP", None),
    # --- REFERIDOS: visibilidad de implementación (Canal 2) ---
    ("referidos", "estado_implementacion",    "VARCHAR",  "'sin_iniciar'"),
    ("referidos", "impl_actualizado_en",      "TIMESTAMP", None),
    ("referidos", "impl_eta",                 "VARCHAR",  None),
    ("referidos", "impl_historial",           "TEXT",     "'[]'"),
    ("referidos", "impl_alerta_estancado_en", "TIMESTAMP", None),
]


def _add_column(conn, dialect: str, tabla: str, col: str, tipo: str, default):
    """ADD COLUMN idempotente. Postgres usa IF NOT EXISTS; SQLite no lo soporta,
    así que se intenta el ALTER plano y se traga el error de columna duplicada."""
    default_clause = f" DEFAULT {default}" if default is not None else ""
    if dialect == "postgresql":
        conn.execute(text(
            f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {col} {tipo}{default_clause}"
        ))
        return
    # SQLite y otros: intentar y tolerar "duplicate column".
    try:
        conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}{default_clause}"))
    except Exception as e:
        if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
            return
        raise


def _backfill_canales(conn):
    """Rellena los flags de canal de los aliados existentes a partir de
    tipo_aliado. Solo toca filas con el flag en NULL (idempotente)."""
    # Todo aliado canal1 (o sin tipo) puede operar en la bolsa.
    conn.execute(text(
        "UPDATE aliados SET canal1_habilitado = "
        "CASE WHEN tipo_aliado IS NULL OR tipo_aliado = 'canal1' THEN TRUE ELSE FALSE END "
        "WHERE canal1_habilitado IS NULL"
    ))
    conn.execute(text(
        "UPDATE aliados SET canal2_habilitado = "
        "CASE WHEN tipo_aliado = 'canal2' THEN TRUE ELSE FALSE END "
        "WHERE canal2_habilitado IS NULL"
    ))
    conn.execute(text(
        "UPDATE aliados SET canal_activo = COALESCE(tipo_aliado, 'canal1') "
        "WHERE canal_activo IS NULL"
    ))


def run_migrations(engine) -> None:
    """Crea las columnas nuevas si no existen y backfillea los flags de canal.
    Idempotente. Llamar una vez al boot, DESPUÉS de Base.metadata.create_all()."""
    dialect = engine.dialect.name
    try:
        with engine.begin() as conn:
            for tabla, col, tipo, default in _COLUMNAS:
                _add_column(conn, dialect, tabla, col, tipo, default)
            _backfill_canales(conn)
        print("[MEJORAS] Migraciones OK (canales, rampa, reciclado, delivery)", flush=True)
    except Exception as e:
        print(f"[MEJORAS] Error en migraciones: {e}", file=sys.stderr, flush=True)
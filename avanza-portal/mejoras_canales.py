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
_COLUMNAS = [
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


def run_migrations(engine) -> None:
    """Crea las columnas nuevas (reciclado, delivery) si no existen.
    Idempotente. Llamar una vez al boot, DESPUÉS de Base.metadata.create_all()."""
    dialect = engine.dialect.name
    try:
        with engine.begin() as conn:
            for tabla, col, tipo, default in _COLUMNAS:
                _add_column(conn, dialect, tabla, col, tipo, default)
        print("[MEJORAS] Migraciones OK (reciclado, delivery)", flush=True)
    except Exception as e:
        print(f"[MEJORAS] Error en migraciones: {e}", file=sys.stderr, flush=True)
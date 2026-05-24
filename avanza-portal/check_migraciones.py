"""
check_migraciones.py — Diagnóstico y reparación de esquema PostgreSQL en producción.

CÓMO USARLO EN RENDER:
  1. Ir a tu servicio en Render → Shell
  2. python check_migraciones.py

Qué hace:
  - Verifica qué columnas existen en la tabla 'aliados'
  - Aplica las que faltan (idempotente: no rompe nada si ya existen)
  - Imprime un resumen de estado

Si el checkout tira 500, lo más probable es que una de estas columnas
no exista todavía en la DB de producción.
"""

import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("❌ DATABASE_URL no está configurada en el entorno.")
    sys.exit(1)

# Render expone postgres:// pero SQLAlchemy necesita postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError, ProgrammingError
except ImportError:
    print("❌ SQLAlchemy no está instalado. Corré: pip install sqlalchemy psycopg2-binary")
    sys.exit(1)

engine = create_engine(DATABASE_URL)


def columnas_existentes(tabla: str) -> set:
    """Devuelve el conjunto de columnas que ya existen en la tabla."""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :tabla"
        ), {"tabla": tabla})
        return {row[0] for row in result}


def aplicar_si_falta(sql: str, tabla: str, columna: str) -> str:
    """Aplica el ALTER TABLE solo si la columna no existe. Devuelve estado."""
    existentes = columnas_existentes(tabla)
    if columna in existentes:
        return f"  ✅ {tabla}.{columna} — ya existe"
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
        return f"  🔧 {tabla}.{columna} — APLICADA"
    except Exception as e:
        return f"  ❌ {tabla}.{columna} — ERROR: {e}"


# ─── COLUMNAS CRÍTICAS PARA EL CHECKOUT ─────────────────────────────────────
# Estas son las que causan el HTTP 500 si no existen.
# SQLAlchemy hace SELECT * al cargar un objeto Aliado y PostgreSQL falla
# con "UndefinedColumn" si el modelo declara columnas que la tabla no tiene.

CRITICAS = [
    # v2.1 — Las dos que causan el checkout 500
    ("ALTER TABLE aliados ADD COLUMN username VARCHAR UNIQUE",
     "aliados", "username"),
    ("ALTER TABLE aliados ADD COLUMN portal_publico_foto_url VARCHAR",
     "aliados", "portal_publico_foto_url"),
]

# ─── COLUMNAS RECIENTES (v2.0 → v2.4) ────────────────────────────────────────
RECIENTES = [
    ("ALTER TABLE aliados ADD COLUMN username_personalizado_en TIMESTAMP",
     "aliados", "username_personalizado_en"),
    ("ALTER TABLE aliados ADD COLUMN pais VARCHAR DEFAULT 'AR'",
     "aliados", "pais"),
    ("ALTER TABLE aliados ADD COLUMN rubros_especialidad TEXT DEFAULT '[]'",
     "aliados", "rubros_especialidad"),
    ("ALTER TABLE aliados ADD COLUMN whatsapp_numero VARCHAR",
     "aliados", "whatsapp_numero"),
    ("ALTER TABLE aliados ADD COLUMN onboarding_email_d1_en TIMESTAMP",
     "aliados", "onboarding_email_d1_en"),
    ("ALTER TABLE aliados ADD COLUMN onboarding_email_d3_en TIMESTAMP",
     "aliados", "onboarding_email_d3_en"),
    ("ALTER TABLE aliados ADD COLUMN onboarding_email_d7_en TIMESTAMP",
     "aliados", "onboarding_email_d7_en"),
]

# ─── TABLA jarvis_api_keys (para la API pública de JARVIS) ───────────────────
JARVIS_API_TABLE = """
CREATE TABLE IF NOT EXISTS jarvis_api_keys (
    id                  SERIAL PRIMARY KEY,
    aliado_id           INTEGER REFERENCES aliados(id) ON DELETE CASCADE,
    key_prefix          VARCHAR(20) NOT NULL,
    key_hash            VARCHAR(64) NOT NULL UNIQUE,
    nombre              VARCHAR(100) DEFAULT 'Mi API Key',
    activa              BOOLEAN DEFAULT TRUE,
    plan_tier           VARCHAR(20) DEFAULT 'starter',
    requests_hoy        INTEGER DEFAULT 0,
    requests_mes        INTEGER DEFAULT 0,
    limite_diario       INTEGER DEFAULT 200,
    ultimo_reset_diario DATE,
    ultima_peticion     TIMESTAMP,
    creada_en           TIMESTAMP DEFAULT NOW()
)
"""


def main():
    print("=" * 60)
    print("JARVIS — Diagnóstico de esquema PostgreSQL")
    print("=" * 60)

    # Verificar conexión
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ Conexión a PostgreSQL: OK\n")
    except Exception as e:
        print(f"❌ No se pudo conectar a la DB: {e}")
        sys.exit(1)

    # Columnas críticas para el checkout
    print("── Columnas críticas (checkout /crear) ──")
    errores_criticos = []
    for sql, tabla, col in CRITICAS:
        resultado = aplicar_si_falta(sql, tabla, col)
        print(resultado)
        if "ERROR" in resultado:
            errores_criticos.append(col)

    print()

    # Columnas recientes
    print("── Columnas v2.0–v2.4 ──")
    for sql, tabla, col in RECIENTES:
        print(aplicar_si_falta(sql, tabla, col))

    print()

    # Tabla de JARVIS API pública
    print("── Tabla jarvis_api_keys (API pública JARVIS) ──")
    try:
        with engine.connect() as conn:
            conn.execute(text(JARVIS_API_TABLE))
            conn.commit()
        print("  ✅ jarvis_api_keys — lista")
    except Exception as e:
        print(f"  ❌ jarvis_api_keys — ERROR: {e}")

    print()
    print("=" * 60)

    if errores_criticos:
        print(f"❌ ATENCIÓN: fallaron columnas críticas: {errores_criticos}")
        print("   El checkout seguirá tirando 500. Revisá permisos de la DB.")
        sys.exit(1)
    else:
        print("✅ Esquema OK. El checkout debería funcionar.")
        print()
        print("Si sigue tirando 500, el problema es otro. Verificá:")
        print("  1. MP_ACCESS_TOKEN configurado en Render → Environment")
        print("  2. BACKEND_PUBLIC_URL apunta al dominio correcto del backend")
        print("  3. Logs de Render en tiempo real mientras reproducís el error")


if __name__ == "__main__":
    main()
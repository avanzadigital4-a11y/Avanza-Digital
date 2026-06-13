"""
Alembic env.py — adaptado a avanza-portal.

Resuelve la URL igual que database.py:
  - lee DATABASE_URL del entorno (Render la inyecta),
  - corrige postgres:// → postgresql:// (SQLAlchemy 2.x),
  - cae a SQLite local si no está seteada.

Y apunta target_metadata a Base.metadata (importando todos los modelos), para
que `alembic revision --autogenerate` detecte el esquema real del proyecto.

INERTE hasta que lo corras a mano. No lo importa la app.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# ── Path al root del portal (donde viven database.py / models.py) ────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolver_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    # Fallback local idéntico a database.py
    return "sqlite:////tmp/avanza.db"


config.set_main_option("sqlalchemy.url", _resolver_url())

# ── Metadata objetivo: TODOS los modelos del proyecto ────────────────────────
# Importar models registra cada tabla en Base.metadata. Si agregás un módulo
# con modelos nuevos, asegurate de que esté importado (directa o indirectamente)
# antes de autogenerar, o Alembic no lo "verá".
from database import Base  # noqa: E402
import models  # noqa: E402,F401  (registra todas las tablas en Base.metadata)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Modo offline: emite SQL sin conectar (útil para revisar el script)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo online: conecta y aplica."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # En SQLite, los ALTER necesitan batch mode (recrea la tabla).
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

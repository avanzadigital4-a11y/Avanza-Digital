from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Railway inyecta DATABASE_URL automáticamente cuando agregás PostgreSQL.
# Si no existe (desarrollo local), cae a SQLite en /tmp.
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # Railway a veces pone "postgres://" en lugar de "postgresql://"
    # SQLAlchemy 2.x solo acepta "postgresql://"
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(DATABASE_URL)
else:
    # Fallback local
    DB_PATH = "/tmp/avanza.db"
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False}
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
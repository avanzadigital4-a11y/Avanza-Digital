"""
conftest.py – fixtures compartidos para todos los tests.

Problema: main.py en el módulo global hace:
  1. Base.metadata.create_all(bind=engine)   ← necesita connect() real
  2. _aplicar_migracion("ALTER TABLE ... ADD COLUMN x UNIQUE")
     └─ SQLite no soporta ADD COLUMN UNIQUE y el error message no matchea
        los tokens que _aplicar_migracion silencia → re-lanza → crash.

Solución: reemplazar engine.connect() con un wrapper que deja pasar todo
EXCEPTO ALTER TABLE ADD COLUMN (que swallows silenciosamente).
Esto permite que create_all funcione y que las migraciones sean no-ops.
"""

import os
import sys
from contextlib import contextmanager
from unittest.mock import patch
from sqlalchemy.exc import OperationalError

# ── 1. Path ───────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── 2. Env vars ANTES de importar cualquier módulo del proyecto ───────────────
os.environ["DATABASE_URL"]             = "sqlite:///./test_avanza.db"
os.environ["JWT_SECRET"]               = "test-secret-key-for-tests-only"
os.environ["ADMIN_API_KEY"]            = "test-admin-key"
os.environ["ENABLE_SCHEDULER"]         = "0"
os.environ["MP_ACCESS_TOKEN"]          = ""
os.environ["TRON_MNEMONIC"]            = ""
os.environ["AVANZA_INSECURE_WEBHOOKS"] = "1"
os.environ["AVANZA_CORS_OPEN"]         = "1"
os.environ["RESEND_API_KEY"]           = ""
os.environ["MAILERLITE_API_KEY"]       = ""
os.environ["TWILIO_ACCOUNT_SID"]       = ""
os.environ["TWILIO_AUTH_TOKEN"]        = ""

# ── 3. Engine SQLite de test ──────────────────────────────────────────────────
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

SQLITE_URL = "sqlite:///./test_avanza.db"
test_engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})

@event.listens_for(test_engine, "connect")
def _set_pragma(dbapi_conn, _rec):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()

# ── 4. Reemplazar engine de database.py ANTES de que main.py lo importe ───────
import database as _db_module
_db_module.engine = test_engine
_db_module.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# ── 5. Smart-connect: deja pasar todo EXCEPTO ALTER TABLE ADD COLUMN ──────────
#    main.py usa engine.connect() tanto para create_all (necesario) como para
#    las migraciones ALTER TABLE (que fallan en SQLite con UNIQUE). Este wrapper
#    intercepta execute() y swallows silenciosamente el error de SQLite para
#    esas sentencias específicas.

_real_connect = test_engine.connect

@contextmanager
def _safe_connect(*args, **kwargs):
    with _real_connect(*args, **kwargs) as conn:
        _orig_execute = conn.execute

        def _safe_execute(stmt, *a, **kw):
            sql = str(stmt).upper()
            if "ALTER TABLE" in sql and "ADD COLUMN" in sql:
                try:
                    return _orig_execute(stmt, *a, **kw)
                except (OperationalError, Exception):
                    return None  # silenciar: columna ya existe o no soportada
            return _orig_execute(stmt, *a, **kw)

        conn.execute = _safe_execute
        yield conn

# ── 6. Importar main.py con el smart-connect activo ──────────────────────────
with patch.object(test_engine, "connect", _safe_connect):
    import main as _main_mod

# Parchar _aplicar_migracion para llamadas futuras (ej. hot-reload en tests)
_main_mod._aplicar_migracion = lambda sql: None

# ── 7. Imports normales post-carga ────────────────────────────────────────────
import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

from database import Base, get_db
from models import (
    Aliado, Admin, Comision, Venta, PlanContinuidadActivo,
    TransaccionCredito, LeadBolsa,
)
from auth import crear_token

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")


# ── 8. Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db():
    """Tablas creadas desde cero, destruidas al final. DB limpia por test."""
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db):
    """FastAPI TestClient con override de get_db → sesión de test."""
    app = _main_mod.app

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def aliado(db):
    a = Aliado(
        codigo="AL-001",
        nombre="Test Aliado",
        email="aliado@test.com",
        whatsapp="+5491100000001",
        ciudad="Rosario",
        ref_code="testaliado",
        password_hash=pwd_context.hash("password123"),
        activo=True,
        creditos=100,
        nivel="BASIC",
        terminos_aceptados=True,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@pytest.fixture()
def aliado_con_sponsor(db, aliado):
    sub = Aliado(
        codigo="AL-002",
        nombre="Sub Aliado",
        email="sub@test.com",
        whatsapp="+5491100000002",
        ciudad="Córdoba",
        ref_code="subaliado",
        password_hash=pwd_context.hash("password123"),
        activo=True,
        creditos=100,
        nivel="BASIC",
        sponsor_id=aliado.id,
        terminos_aceptados=True,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@pytest.fixture()
def token_aliado(aliado):
    return crear_token(sub=aliado.codigo, tipo="aliado")


@pytest.fixture()
def token_admin():
    return crear_token(sub="admin-test", tipo="admin")


@pytest.fixture()
def admin_user(db):
    a = Admin(
        username="admin-test",
        password_hash=pwd_context.hash("adminpass"),
    )
    db.add(a)
    db.commit()
    return a


@pytest.fixture()
def lead_basico(db):
    l = LeadBolsa(
        empresa="Metalúrgica Test",
        rubro="Metalúrgica",
        telefono="+5493413000000",
        email="contacto@metaltest.com",
        estado="disponible",
        tier="basico",
        costo_creditos=0,
        score_calidad=70,
    )
    db.add(l)
    db.commit()
    db.refresh(l)
    return l


@pytest.fixture()
def lead_premium(db):
    l = LeadBolsa(
        empresa="Premium SA",
        rubro="Agro",
        telefono="+5493413111111",
        email="premium@test.com",
        estado="disponible",
        tier="premium",
        costo_creditos=50,
        score_calidad=90,
    )
    db.add(l)
    db.commit()
    db.refresh(l)
    return l


@pytest.fixture()
def plan_continuidad_activo(db, aliado):
    from main import PLANES_CONTINUIDAD
    plan_key = list(PLANES_CONTINUIDAD.keys())[0]
    precio = PLANES_CONTINUIDAD[plan_key]
    p = PlanContinuidadActivo(
        aliado_id=aliado.id,
        nombre_cliente="Cliente Recurrente SA",
        plan_continuidad=plan_key,
        precio_mensual_usd=float(precio),
        comision_pct=0.10,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ── 9. Patch pwd_context en main para usar sha256_crypt en tests ──────────────
#    bcrypt <= 3.x y passlib en Python 3.12 se llevan mal con passwords de test.
#    En tests no necesitamos la resistencia de bcrypt; sha256_crypt es correcto.
from passlib.context import CryptContext as _CryptContext

_fast_ctx = _CryptContext(schemes=["sha256_crypt"], deprecated="auto")
_main_mod.pwd_context   = _fast_ctx
_main_mod.hash_password = lambda p: _fast_ctx.hash(p)
_main_mod.verify_password = lambda plain, hashed: _fast_ctx.verify(plain, hashed)

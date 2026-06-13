"""
test_admin_2fa.py — Flujo completo de 2FA TOTP del admin.

Cubre: retrocompatibilidad (admin sin 2FA entra con user+pass), enrolamiento
en dos pasos (setup → activar), exigencia del código tras activar, rechazo de
código inválido, y desactivación protegida por código.
"""
import pyotp
import pytest
from auth import crear_token
from models import Admin


def _crear_admin(db, pwd_context, username="admin-2fa", password="adminpass"):
    a = Admin(username=username, password_hash=pwd_context.hash(password))
    db.add(a); db.commit(); db.refresh(a)
    return a


def _jwt(username):
    return crear_token(sub=username, tipo="admin")


def test_login_sin_2fa_sigue_funcionando(client, db):
    from tests.conftest import pwd_context
    _crear_admin(db, pwd_context, username="admin-noteff")
    r = client.post("/admin/login", json={"username": "admin-noteff", "password": "adminpass"})
    assert r.status_code == 200, r.text
    assert r.json()["tipo"] == "admin"


def test_flujo_2fa_completo(client, db):
    from tests.conftest import pwd_context
    admin = _crear_admin(db, pwd_context, username="admin-eff")
    auth = {"Authorization": f"Bearer {_jwt('admin-eff')}"}

    # 1) setup: genera secret + URI, todavía NO activo
    r = client.post("/admin/2fa/setup", headers=auth)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["ya_activo"] is False and "otpauth://" in r.json()["otpauth_uri"]

    # login todavía funciona sin código (no se activó aún)
    assert client.post("/admin/login", json={"username": "admin-eff", "password": "adminpass"}).status_code == 200

    # 2) activar con un código inválido → 401
    r = client.post("/admin/2fa/activar", headers=auth, json={"totp": "000000"})
    assert r.status_code == 401

    # 2b) activar con el código real de la app
    codigo = pyotp.TOTP(secret).now()
    r = client.post("/admin/2fa/activar", headers=auth, json={"totp": codigo})
    assert r.status_code == 200 and r.json()["status"] == "activado", r.text

    # 3) ahora el login SIN código falla
    r = client.post("/admin/login", json={"username": "admin-eff", "password": "adminpass"})
    assert r.status_code == 401 and "2FA" in r.json()["detail"]

    # 3b) login con código inválido falla
    r = client.post("/admin/login", json={"username": "admin-eff", "password": "adminpass", "totp": "123456"})
    assert r.status_code == 401

    # 3c) login con código válido entra
    codigo = pyotp.TOTP(secret).now()
    r = client.post("/admin/login", json={"username": "admin-eff", "password": "adminpass", "totp": codigo})
    assert r.status_code == 200 and r.json()["tipo"] == "admin", r.text

    # 4) desactivar sin código → 401; con código → ok
    assert client.post("/admin/2fa/desactivar", headers=auth, json={}).status_code == 401
    codigo = pyotp.TOTP(secret).now()
    r = client.post("/admin/2fa/desactivar", headers=auth, json={"totp": codigo})
    assert r.status_code == 200 and r.json()["status"] == "desactivado"

    # y el login vuelve a funcionar sin código
    assert client.post("/admin/login", json={"username": "admin-eff", "password": "adminpass"}).status_code == 200


def test_password_malo_no_pasa_aunque_haya_2fa(client, db):
    from tests.conftest import pwd_context
    admin = _crear_admin(db, pwd_context, username="admin-pw")
    admin.totp_secret = pyotp.random_base32()
    admin.totp_enabled = True
    db.commit()
    # password incorrecto: ni siquiera llega a pedir 2FA
    codigo = pyotp.TOTP(admin.totp_secret).now()
    r = client.post("/admin/login", json={"username": "admin-pw", "password": "incorrecta", "totp": codigo})
    assert r.status_code == 401 and "Credenciales" in r.json()["detail"]


def test_setup_rechaza_si_ya_activo(client, db):
    from tests.conftest import pwd_context
    admin = _crear_admin(db, pwd_context, username="admin-act")
    admin.totp_secret = pyotp.random_base32()
    admin.totp_enabled = True
    db.commit()
    auth = {"Authorization": f"Bearer {_jwt('admin-act')}"}
    r = client.post("/admin/2fa/setup", headers=auth)
    assert r.status_code == 409
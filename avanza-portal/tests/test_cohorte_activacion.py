"""
Tests de /admin/cohorte-activacion — la grilla de salud del programa que cruza
cohorte de registro (mes de alta) × hitos de activación.

Cubre:
  1. Agrupación por mes de registro y conteo de cada hito.
  2. La columna de fuga (≥umbral créditos Jarvis y 0 ventas) reusa la lógica de
     cohorte-fuga pero desglosada por cohorte; un aliado con venta NO cae en fuga
     aunque haya gastado créditos.
  3. Suspendidos (activo=False) se cuentan en su cohorte de registro histórica.
  4. Totales del programa consistentes con la suma de cohortes.
  5. Requiere auth de admin.
"""
import sys, os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import Aliado, LeadBolsa, TransaccionCredito, Venta
from passlib.context import CryptContext

AUTH = lambda tok: {"Authorization": f"Bearer {tok}"}
_pwd = CryptContext(schemes=["sha256_crypt"], deprecated="auto")


def _aliado(db, codigo, mes, *, login=False, activo=True):
    a = Aliado(
        codigo=codigo,
        nombre=f"Aliado {codigo}",
        email=f"{codigo.lower()}@test.com",
        ref_code=codigo.lower(),
        password_hash=_pwd.hash("x"),
        activo=activo,
        creado_en=datetime(mes[0], mes[1], 15, 10, 0, 0),
        ultimo_login=datetime(mes[0], mes[1], 20, 10, 0, 0) if login else None,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _captura(db, aliado):
    db.add(LeadBolsa(empresa="X", rubro="Metalúrgica", telefono="+5490000",
                     estado="reclamado", aliado_id=aliado.id))
    db.commit()


def _gasto_jarvis(db, aliado, creditos):
    db.add(TransaccionCredito(aliado_id=aliado.id, delta=-creditos,
                              motivo="jarvis_mensaje"))
    db.commit()


def _venta(db, aliado):
    db.add(Venta(aliado_id=aliado.id, nombre_cliente="Cliente", plan="pro",
                 valor_usd=1000, comision_pct=0.1, comision_usd=100,
                 confirmada=True, fecha_venta=datetime.now()))
    db.commit()


def _setup(db):
    # Cohorte ene-2026
    a1 = _aliado(db, "AL-1", (2026, 1), login=True)   # logueó + capturó + jarvis 100 + 0 ventas → FUGA
    _captura(db, a1)
    _gasto_jarvis(db, a1, 100)
    _aliado(db, "AL-2", (2026, 1))                    # registrado y nada más

    # Cohorte feb-2026
    a3 = _aliado(db, "AL-3", (2026, 2), login=True)   # logueó + capturó + jarvis 50 + venta → NO fuga
    _captura(db, a3)
    _gasto_jarvis(db, a3, 50)
    _venta(db, a3)
    _aliado(db, "AL-4", (2026, 2), login=True, activo=False)  # suspendido


def _cohorte(data, mes):
    return next(c for c in data["cohortes"] if c["cohorte"] == mes)


def test_agrupa_y_cuenta_hitos_por_cohorte(client, db, token_admin):
    _setup(db)
    r = client.get("/admin/cohorte-activacion", headers=AUTH(token_admin))
    assert r.status_code == 200
    data = r.json()

    ene = _cohorte(data, "2026-01")
    assert ene["registrados"] == 2
    assert ene["logueo"] == 1
    assert ene["capturo_lead"] == 1
    assert ene["uso_jarvis"] == 1
    assert ene["cerro_venta"] == 0
    assert ene["en_fuga"] == 1          # AL-1: 100 créditos, sin venta
    assert ene["suspendidos"] == 0
    assert ene["pct_capturo"] == 50.0   # 1 de 2

    feb = _cohorte(data, "2026-02")
    assert feb["registrados"] == 2
    assert feb["logueo"] == 2
    assert feb["capturo_lead"] == 1
    assert feb["cerro_venta"] == 1
    assert feb["en_fuga"] == 0          # AL-3 gastó Jarvis pero cerró venta
    assert feb["suspendidos"] == 1      # AL-4


def test_totales_consistentes(client, db, token_admin):
    _setup(db)
    data = client.get("/admin/cohorte-activacion", headers=AUTH(token_admin)).json()
    t = data["totales"]
    assert t["registrados"] == 4
    assert t["logueo"] == 3
    assert t["capturo_lead"] == 2
    assert t["uso_jarvis"] == 2
    assert t["cerro_venta"] == 1
    assert t["en_fuga"] == 1
    assert t["suspendidos"] == 1
    # suma de cohortes == totales
    assert sum(c["registrados"] for c in data["cohortes"]) == t["registrados"]
    assert sum(c["en_fuga"] for c in data["cohortes"]) == t["en_fuga"]


def test_umbral_fuga_parametrizable(client, db, token_admin):
    _setup(db)
    # Con umbral 200, AL-1 (gastó 100) ya no cuenta como fuga
    data = client.get("/admin/cohorte-activacion?umbral_fuga=200",
                      headers=AUTH(token_admin)).json()
    assert data["totales"]["en_fuga"] == 0


def test_requiere_auth_admin(client, db):
    _setup(db)
    r = client.get("/admin/cohorte-activacion")
    assert r.status_code in (401, 403)

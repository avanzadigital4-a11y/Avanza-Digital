"""
test_email_y_referidos.py — tests de los dos huecos nuevos.

Hueco 1: analítica de email (email_tracking.py + enviar_email taggeado).
Hueco 2: bono de activación de referidos (referidos_aliados.py).

Usa los fixtures de conftest.py (db, client, aliado, aliado_con_sponsor,
token_aliado, token_admin).
"""
import importlib

import notificaciones
import referidos_aliados
from models import Aliado, EmailEnviado, TransaccionCredito


# ════════════════════════════════════════════════════════════════════════════
#  HUECO 1 — ANALÍTICA DE EMAIL
# ════════════════════════════════════════════════════════════════════════════

def _forzar_track_base(monkeypatch):
    """enviar_email solo inyecta pixel si hay PORTAL_URL/BACKEND_PUBLIC_URL."""
    monkeypatch.setenv("PORTAL_URL", "https://portal.test")
    importlib.reload(notificaciones)


def test_enviar_email_taggeado_registra_y_trackea(db, monkeypatch):
    _forzar_track_base(monkeypatch)
    html_in = ('<div><a href="https://portal.test/portal.html">Volver</a>'
               '<a href="https://externo.com/x">Externo</a></div>')
    html_out = notificaciones._registrar_y_trackear(
        "closer@test.com", "Asunto", html_in, "inactividad_20d", aliado_id=None)

    reg = db.query(EmailEnviado).filter(EmailEnviado.campania == "inactividad_20d").first()
    assert reg is not None
    assert f"/e/o/{reg.token}.png" in html_out          # pixel inyectado
    assert f"/e/c/{reg.token}?u=" in html_out           # link interno reescrito
    assert "https://externo.com/x" in html_out          # link externo intacto


def test_enviar_email_sin_campania_no_registra(db, monkeypatch):
    _forzar_track_base(monkeypatch)
    antes = db.query(EmailEnviado).count()
    # Sin campania → comportamiento legacy, no escribe nada.
    notificaciones.enviar_email("x@test.com", "Hola", "<p>hola</p>")
    assert db.query(EmailEnviado).count() == antes


def test_pixel_apertura_marca_abierto(client, db):
    reg = EmailEnviado(token="tok-open", campania="onboarding_d1",
                       destinatario="x@test.com", asunto="A")
    db.add(reg); db.commit()

    r = client.get("/e/o/tok-open.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/gif"

    db.refresh(reg)
    assert reg.abierto_en is not None
    assert reg.aperturas == 1


def test_click_redirige_y_marca(client, db):
    reg = EmailEnviado(token="tok-click", campania="onboarding_d3",
                       destinatario="x@test.com", asunto="A")
    db.add(reg); db.commit()

    r = client.get("/e/c/tok-click", params={"u": "https://portal.test/portal.html"},
                   follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "https://portal.test/portal.html"

    db.refresh(reg)
    assert reg.click_en is not None
    assert reg.clicks == 1
    assert reg.abierto_en is not None  # un clic implica apertura


def test_click_open_redirect_bloqueado(client, db):
    """No debe convertirse en open-redirect: destino no http(s) → cae a '/'."""
    db.add(EmailEnviado(token="tok-evil", campania="x")); db.commit()
    r = client.get("/e/c/tok-evil", params={"u": "javascript:alert(1)"},
                   follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_metricas_email_admin(client, db, token_admin):
    db.add(EmailEnviado(token="m1", campania="inactividad_20d",
                        abierto_en=None, aperturas=0))
    db.add(EmailEnviado(token="m2", campania="inactividad_20d"))
    db.commit()
    # marcar una apertura
    reg = db.query(EmailEnviado).filter(EmailEnviado.token == "m1").first()
    from datetime import datetime
    reg.abierto_en = datetime.now(); reg.aperturas = 1
    db.commit()

    r = client.get("/admin/email/metricas",
                   headers={"Authorization": f"Bearer {token_admin}"})
    assert r.status_code == 200
    data = r.json()
    camps = {c["campania"]: c for c in data["campanias"]}
    assert "inactividad_20d" in camps
    assert camps["inactividad_20d"]["enviados"] == 2
    assert camps["inactividad_20d"]["abiertos"] == 1
    assert camps["inactividad_20d"]["tasa_apertura"] == 50.0


def test_metricas_email_requiere_admin(client, db, token_aliado):
    r = client.get("/admin/email/metricas",
                   headers={"Authorization": f"Bearer {token_aliado}"})
    assert r.status_code in (401, 403)


# ════════════════════════════════════════════════════════════════════════════
#  HUECO 2 — LOOP DE REFERIDOS ALIADO → ALIADO
# ════════════════════════════════════════════════════════════════════════════

def test_panel_invitacion(client, aliado, aliado_con_sponsor, token_aliado):
    r = client.get(f"/aliados/{aliado.codigo}/invitacion",
                   headers={"Authorization": f"Bearer {token_aliado}"})
    assert r.status_code == 200
    data = r.json()
    assert data["ref_code"] == aliado.ref_code
    assert f"ref={aliado.ref_code}" in data["invite_link"]
    assert data["stats"]["invitados"] == 1          # tiene un sub
    assert data["bono_por_activacion"] == referidos_aliados.BONUS_ACTIVACION


def test_bono_activacion_idempotente(db, aliado, aliado_con_sponsor):
    # El referido se "activa": al menos un login.
    aliado_con_sponsor.cantidad_logins = 2
    db.commit()

    creditos_antes = aliado.creditos or 0

    referidos_aliados.job_referidos_activacion()
    referidos_aliados.job_referidos_activacion()  # 2da corrida no debe duplicar

    db.refresh(aliado)
    txs = (db.query(TransaccionCredito)
           .filter(TransaccionCredito.aliado_id == aliado.id,
                   TransaccionCredito.motivo == referidos_aliados.MOTIVO_REF)
           .all())
    assert len(txs) == 1
    assert aliado.creditos == creditos_antes + referidos_aliados.BONUS_ACTIVACION


def test_bono_no_se_otorga_si_referido_no_activo(db, aliado, aliado_con_sponsor):
    # cantidad_logins = 0 → todavía no se activó → sin bono.
    aliado_con_sponsor.cantidad_logins = 0
    db.commit()

    referidos_aliados.job_referidos_activacion()

    txs = (db.query(TransaccionCredito)
           .filter(TransaccionCredito.motivo == referidos_aliados.MOTIVO_REF)
           .count())
    assert txs == 0
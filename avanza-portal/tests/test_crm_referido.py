"""
Tests v3.2:
  1. Puente CRM → Referido: registrar para venta en 1 click desde la ficha
     (crea Referido vinculado, deja actividad, idempotente, valida plan,
     bloquea Canal 2 — misma regla que /referidos/registrar).
  2. Eliminar prospecto endurecido: suelta las referencias de LeadBolsa,
     AuditoriaLog y Referido antes de borrar (en Postgres las FK rechazarían
     el delete) y deja la captura re-convertible.
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import (Aliado, Prospecto, ActividadProspecto, Referido,
                    AuditoriaLog, LeadBolsa, PLANES)

AUTH = lambda tok: {"Authorization": f"Bearer {tok}"}


def _crear_prospecto(db, aliado, nombre="Metalúrgica Test S.A.", **kw):
    p = Prospecto(aliado_id=aliado.id, nombre=nombre, contacto="Juan",
                  estado="respondio", **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


# ── 1: Puente CRM → Referido ──────────────────────────────────────────────────

class TestRegistrarReferidoDesdeCRM:

    def test_crea_referido_vinculado_y_actividad(self, client, db, aliado, token_aliado):
        p = _crear_prospecto(db, aliado)
        r = client.post(f"/prospectos/{p.id}/registrar-referido",
                        params={"plan": "Plan Pro"}, headers=AUTH(token_aliado))
        assert r.status_code == 200
        data = r.json()
        assert data["ya_existia"] is False
        assert data["plan"] == "Plan Pro"
        assert data["valor_plan"] == PLANES["Plan Pro"]
        # comisión estimada = precio * comisión del aliado (BASIC 10%)
        assert data["comision_estimada"] == round(PLANES["Plan Pro"] * aliado.comision_pct, 2)

        ref = db.query(Referido).filter(Referido.id == data["id_referido"]).one()
        assert ref.aliado_id == aliado.id
        assert ref.prospecto_id == p.id
        assert ref.nombre_cliente == p.nombre

        acts = db.query(ActividadProspecto).filter(
            ActividadProspecto.prospecto_id == p.id,
            ActividadProspecto.tipo == "sistema").all()
        assert any("Registrado para venta" in (a.descripcion or "") for a in acts)

        # plan_interes vacío se completa con el plan registrado
        db.refresh(p)
        assert p.plan_interes == "Plan Pro"

    def test_es_idempotente(self, client, db, aliado, token_aliado):
        p = _crear_prospecto(db, aliado)
        r1 = client.post(f"/prospectos/{p.id}/registrar-referido",
                         params={"plan": "Plan Base"}, headers=AUTH(token_aliado))
        r2 = client.post(f"/prospectos/{p.id}/registrar-referido",
                         params={"plan": "Plan Industrial"}, headers=AUTH(token_aliado))
        assert r1.status_code == 200 and r2.status_code == 200
        assert r2.json()["ya_existia"] is True
        assert r1.json()["id_referido"] == r2.json()["id_referido"]
        assert db.query(Referido).filter(Referido.prospecto_id == p.id).count() == 1

    def test_plan_invalido_400(self, client, db, aliado, token_aliado):
        p = _crear_prospecto(db, aliado)
        r = client.post(f"/prospectos/{p.id}/registrar-referido",
                        params={"plan": "Plan Inventado"}, headers=AUTH(token_aliado))
        assert r.status_code == 400

    def test_canal2_bloqueado(self, client, db, aliado, token_aliado):
        aliado.tipo_aliado = "canal2"
        db.commit()
        p = _crear_prospecto(db, aliado)
        r = client.post(f"/prospectos/{p.id}/registrar-referido",
                        params={"plan": "Plan Pro"}, headers=AUTH(token_aliado))
        assert r.status_code == 403
        aliado.tipo_aliado = "canal1"; db.commit()  # restaurar para otros tests

    def test_prospecto_ajeno_no_se_registra(self, client, db, aliado, token_aliado):
        otro = Aliado(codigo="OTRO-7", nombre="Otro", email="otro7@test.com",
                      ref_code="otro7", password_hash="x", activo=True)
        db.add(otro); db.commit(); db.refresh(otro)
        p = _crear_prospecto(db, otro)
        r = client.post(f"/prospectos/{p.id}/registrar-referido",
                        params={"plan": "Plan Pro"}, headers=AUTH(token_aliado))
        assert r.status_code == 404  # el helper oculta lo ajeno como 404

    def test_referido_id_aparece_en_listado(self, client, db, aliado, token_aliado):
        p = _crear_prospecto(db, aliado)
        client.post(f"/prospectos/{p.id}/registrar-referido",
                    params={"plan": "Plan Pro"}, headers=AUTH(token_aliado))
        r = client.get(f"/prospectos/aliado/{aliado.codigo}", headers=AUTH(token_aliado))
        assert r.status_code == 200
        fila = next(x for x in r.json() if x["id"] == p.id)
        assert fila["referido_id"] is not None


# ── 2: Eliminar prospecto endurecido ─────────────────────────────────────────

class TestEliminarProspecto:

    def test_eliminar_suelta_referencias_y_borra(self, client, db, aliado, token_aliado, lead_premium):
        # Captura que se convierte en prospecto
        client.post("/leads/capturar", params={
            "fuente": "recursos", "email": "del@x.com", "ref_code": aliado.ref_code})
        log = db.query(AuditoriaLog).filter(AuditoriaLog.email_capturado == "del@x.com").one()
        rcap = client.post(f"/capturas/{log.id}/convertir-prospecto", headers=AUTH(token_aliado))
        pid = rcap.json()["prospecto_id"]

        # Vincular además un lead de bolsa y un referido al mismo prospecto
        lead_premium.aliado_id = aliado.id
        lead_premium.estado = "reclamado"
        lead_premium.prospecto_id = pid
        db.commit()
        client.post(f"/prospectos/{pid}/registrar-referido",
                    params={"plan": "Plan Base"}, headers=AUTH(token_aliado))
        ref_id = db.query(Referido).filter(Referido.prospecto_id == pid).one().id

        # Eliminar
        r = client.delete(f"/prospectos/{pid}/eliminar", headers=AUTH(token_aliado))
        assert r.status_code == 200
        assert db.query(Prospecto).filter(Prospecto.id == pid).first() is None

        # Las filas vinculadas sobreviven, sueltas
        db.expire_all()
        assert db.query(LeadBolsa).filter(LeadBolsa.id == lead_premium.id).one().prospecto_id is None
        assert db.query(AuditoriaLog).filter(AuditoriaLog.id == log.id).one().prospecto_id is None
        # El Referido (atribución) NUNCA se borra: solo pierde el vínculo
        ref = db.query(Referido).filter(Referido.id == ref_id).one()
        assert ref.prospecto_id is None
        assert ref.aliado_id == aliado.id

        # La captura vuelve a ser convertible (el puente lo permite)
        r2 = client.post(f"/capturas/{log.id}/convertir-prospecto", headers=AUTH(token_aliado))
        assert r2.status_code == 200 and r2.json()["ya_existia"] is False

    def test_no_elimina_ajeno(self, client, db, aliado, token_aliado):
        otro = Aliado(codigo="OTRO-6", nombre="Otro", email="otro6@test.com",
                      ref_code="otro6", password_hash="x", activo=True)
        db.add(otro); db.commit(); db.refresh(otro)
        p = _crear_prospecto(db, otro)
        r = client.delete(f"/prospectos/{p.id}/eliminar", headers=AUTH(token_aliado))
        assert r.status_code == 404
        assert db.query(Prospecto).filter(Prospecto.id == p.id).first() is not None
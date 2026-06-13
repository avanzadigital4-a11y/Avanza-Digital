"""
Tests del puente Bolsa → CRM y mejoras de importación:
  1. Convertir lead reclamado en prospecto (copia datos, marca contactado).
  2. Conversión idempotente (segunda llamada devuelve el mismo prospecto).
  3. No se puede convertir un lead ajeno.
  4. /admin/bolsa/bulk omite duplicados (mismo teléfono o empresa+país).
  5. /admin/bolsa/verificar-duplicados marca contra la bolsa y dentro del lote.
  6. job_recordatorios_tareas marca recordatorio_enviado en tareas vencidas.
"""
import sys, os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import Aliado, LeadBolsa, Prospecto, ActividadProspecto


AUTH = lambda tok: {"Authorization": f"Bearer {tok}"}


# ── 1-3: Conversión lead → prospecto ─────────────────────────────────────────

class TestConvertirLeadEnProspecto:

    def _reclamar(self, client, lead, token):
        r = client.post(f"/bolsa/{lead.id}/reclamar", headers=AUTH(token))
        assert r.status_code == 200

    def test_convertir_crea_prospecto_y_linkea(self, client, db, aliado, lead_premium, token_aliado):
        self._reclamar(client, lead_premium, token_aliado)

        resp = client.post(f"/bolsa/{lead_premium.id}/convertir-prospecto", headers=AUTH(token_aliado))
        assert resp.status_code == 200
        data = resp.json()
        assert data["ya_existia"] is False
        pid = data["prospecto_id"]

        p = db.query(Prospecto).filter(Prospecto.id == pid).first()
        assert p is not None
        assert p.aliado_id == aliado.id
        assert p.nombre == lead_premium.empresa
        assert p.rubro == lead_premium.rubro
        assert p.telefono == lead_premium.telefono
        assert p.email == lead_premium.email

        db.refresh(lead_premium)
        assert lead_premium.prospecto_id == pid
        # El lead sale del reloj de 48hs (libera cupo de reclamos)
        assert lead_premium.estado == "contactado"

        # Timeline: quedó la actividad de sistema con el origen
        acts = db.query(ActividadProspecto).filter(
            ActividadProspecto.prospecto_id == pid,
            ActividadProspecto.tipo == "sistema",
        ).all()
        assert len(acts) == 1
        assert "Bolsa de Leads" in (acts[0].descripcion or "")

    def test_convertir_es_idempotente(self, client, db, aliado, lead_premium, token_aliado):
        self._reclamar(client, lead_premium, token_aliado)
        r1 = client.post(f"/bolsa/{lead_premium.id}/convertir-prospecto", headers=AUTH(token_aliado))
        r2 = client.post(f"/bolsa/{lead_premium.id}/convertir-prospecto", headers=AUTH(token_aliado))
        assert r1.status_code == 200 and r2.status_code == 200
        assert r2.json()["ya_existia"] is True
        assert r1.json()["prospecto_id"] == r2.json()["prospecto_id"]
        # No se duplicó el prospecto
        total = db.query(Prospecto).filter(Prospecto.aliado_id == aliado.id).count()
        assert total == 1

    def test_no_convierte_lead_ajeno(self, client, db, lead_premium, token_aliado):
        # El lead pertenece a OTRO aliado real (no al dueño del token)
        otro = Aliado(codigo="OTRO-1", nombre="Otro Aliado", email="otro@test.com",
                      ref_code="otroaliado", password_hash="x", activo=True)
        db.add(otro); db.commit(); db.refresh(otro)
        lead_premium.estado = "reclamado"
        lead_premium.aliado_id = otro.id
        db.commit()
        resp = client.post(f"/bolsa/{lead_premium.id}/convertir-prospecto", headers=AUTH(token_aliado))
        assert resp.status_code == 403


# ── 4-5: Duplicados en la carga masiva ───────────────────────────────────────

def _payload(empresa, telefono, pais="AR", rubro="Test"):
    return {"empresa": empresa, "rubro": rubro, "telefono": telefono, "pais": pais,
            "tier": "basico", "costo_creditos": 0, "score_calidad": 50}


class TestDuplicadosBulk:

    def test_bulk_omite_duplicados_contra_bolsa_y_dentro_del_lote(self, client, db, lead_basico, token_admin):
        # lead_basico ya existe en bolsa con telefono +5493413000000
        payload = {"leads": [
            _payload("Otra Empresa", "+54 9 341 300-0000"),   # dup por teléfono (mismos dígitos)
            _payload("Metalúrgica Test", "+5493419999999"),    # dup por empresa+país
            _payload("Empresa Nueva", "+5493415555555"),       # nueva
            _payload("Empresa Nueva", "+5493415555555"),       # repetida dentro del lote
        ]}
        resp = client.post("/admin/bolsa/bulk", json=payload, headers=AUTH(token_admin))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["duplicados_omitidos"] == 3

        # En la bolsa solo se agregó la nueva
        assert db.query(LeadBolsa).filter(LeadBolsa.empresa == "Empresa Nueva").count() == 1
        assert db.query(LeadBolsa).filter(LeadBolsa.empresa == "Otra Empresa").count() == 0

    def test_bulk_todo_duplicado_no_inserta_nada(self, client, db, lead_basico, token_admin):
        antes = db.query(LeadBolsa).count()
        payload = {"leads": [_payload("Metalúrgica Test", "+5493413000000")]}
        resp = client.post("/admin/bolsa/bulk", json=payload, headers=AUTH(token_admin))
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        assert resp.json()["duplicados_omitidos"] == 1
        assert db.query(LeadBolsa).count() == antes

    def test_bulk_requiere_auth_admin(self, client, lead_basico):
        resp = client.post("/admin/bolsa/bulk", json={"leads": [_payload("X", "+5491100000000")]})
        assert resp.status_code in (401, 403)

    def test_verificar_duplicados_endpoint(self, client, lead_basico, token_admin):
        payload = {"leads": [
            {"empresa": "Metalúrgica Test", "telefono": "+5493410000001", "pais": "AR"},  # dup x empresa+pais
            {"empresa": "Sin Relación", "telefono": "+5493413000000", "pais": "US"},      # dup x teléfono
            {"empresa": "Totalmente Nueva", "telefono": "+5493417777777", "pais": "AR"},  # no dup
            {"empresa": "Totalmente Nueva", "telefono": "+5493417777777", "pais": "AR"},  # dup dentro del lote
        ]}
        resp = client.post("/admin/bolsa/verificar-duplicados", json=payload, headers=AUTH(token_admin))
        assert resp.status_code == 200
        assert resp.json()["duplicados"] == [True, True, False, True]


# ── 6: Recordatorios de tareas vencidas ──────────────────────────────────────

class TestAutoConversionExitoso:
    """Marcar un lead contactado como 'exitoso' lo convierte solo al CRM."""

    def _reclamar(self, client, lead, token):
        r = client.post(f"/bolsa/{lead.id}/reclamar", headers=AUTH(token))
        assert r.status_code == 200

    def test_exitoso_convierte_automaticamente(self, client, db, aliado, lead_premium, token_aliado):
        self._reclamar(client, lead_premium, token_aliado)
        resp = client.patch(f"/bolsa/{lead_premium.id}/contactar?resultado=exitoso", headers=AUTH(token_aliado))
        assert resp.status_code == 200
        data = resp.json()
        assert data["convertido_a_crm"] is True
        assert data["prospecto_id"] is not None
        assert "CRM" in data["mensaje"]

        db.refresh(lead_premium)
        assert lead_premium.prospecto_id == data["prospecto_id"]
        p = db.query(Prospecto).filter(Prospecto.id == data["prospecto_id"]).first()
        assert p is not None and p.nombre == lead_premium.empresa
        assert p.estado == "contactado"

    def test_no_interesado_no_convierte(self, client, db, aliado, lead_premium, token_aliado):
        self._reclamar(client, lead_premium, token_aliado)
        resp = client.patch(f"/bolsa/{lead_premium.id}/contactar?resultado=no_interesado", headers=AUTH(token_aliado))
        assert resp.status_code == 200
        assert resp.json()["convertido_a_crm"] is False
        db.refresh(lead_premium)
        assert lead_premium.prospecto_id is None
        assert db.query(Prospecto).filter(Prospecto.aliado_id == aliado.id).count() == 0

    def test_exitoso_sobre_ya_convertido_no_duplica(self, client, db, aliado, lead_premium, token_aliado):
        self._reclamar(client, lead_premium, token_aliado)
        # Conversión manual primero
        r1 = client.post(f"/bolsa/{lead_premium.id}/convertir-prospecto", headers=AUTH(token_aliado))
        pid = r1.json()["prospecto_id"]
        # Marcar exitoso después: no debe crear otro prospecto
        resp = client.patch(f"/bolsa/{lead_premium.id}/contactar?resultado=exitoso", headers=AUTH(token_aliado))
        assert resp.status_code == 200
        assert resp.json()["convertido_a_crm"] is False  # ya existía
        assert resp.json()["prospecto_id"] == pid
        assert db.query(Prospecto).filter(Prospecto.aliado_id == aliado.id).count() == 1


class TestRecordatoriosTareas:

    def test_job_marca_tareas_vencidas(self, client, db, aliado):
        import main as main_mod

        p = Prospecto(aliado_id=aliado.id, nombre="Cliente Recordatorio", estado="contactado")
        db.add(p); db.commit(); db.refresh(p)

        vencida = ActividadProspecto(
            prospecto_id=p.id, aliado_id=aliado.id, tipo="tarea",
            descripcion="Llamar de nuevo", vence_en=datetime.now() - timedelta(hours=2),
            completada=False, recordatorio_enviado=False,
        )
        futura = ActividadProspecto(
            prospecto_id=p.id, aliado_id=aliado.id, tipo="tarea",
            descripcion="Mandar propuesta", vence_en=datetime.now() + timedelta(days=2),
            completada=False, recordatorio_enviado=False,
        )
        db.add_all([vencida, futura]); db.commit()

        # En tests no hay transporte de email: enviar_email loguea y retorna,
        # así que el job marca recordatorio_enviado igual que en prod con envío OK.
        main_mod.job_recordatorios_tareas()

        db.expire_all()
        assert db.get(ActividadProspecto, vencida.id).recordatorio_enviado is True
        assert db.get(ActividadProspecto, futura.id).recordatorio_enviado is False

    def test_job_no_reenvia_dos_veces(self, client, db, aliado):
        import main as main_mod

        p = Prospecto(aliado_id=aliado.id, nombre="Cliente Dos", estado="contactado")
        db.add(p); db.commit(); db.refresh(p)
        t = ActividadProspecto(
            prospecto_id=p.id, aliado_id=aliado.id, tipo="tarea",
            descripcion="Seguimiento", vence_en=datetime.now() - timedelta(hours=1),
            completada=False, recordatorio_enviado=True,  # ya avisado
        )
        db.add(t); db.commit()

        main_mod.job_recordatorios_tareas()  # no debe romper ni cambiar nada
        db.expire_all()
        assert db.get(ActividadProspecto, t.id).recordatorio_enviado is True
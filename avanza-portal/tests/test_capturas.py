"""
Tests de las mejoras v3.1:
  1. Captura de lead magnet con ref_code → log con nombre/teléfono + novedad al aliado.
  2. Dedupe auditoría: /auditorias/log + /leads/capturar con el mismo email = 1 sola captura.
  3. Bandeja Mis Capturas: listado, contador de no vistas, marcar-vistas.
  4. Puente Capturas → CRM: conversión en 1 click, idempotente, no ajena.
  5. /simulador/config expone planes/niveles desde models.py.
  6. Novedades: listado + marcar-leidas.
  7. Outreach: mensaje por rubro (fallback plantilla sin IA) y ownership.
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import Aliado, AuditoriaLog, Prospecto, ActividadProspecto, Novedad, PLANES

AUTH = lambda tok: {"Authorization": f"Bearer {tok}"}


# ── 1-2: Captura + dedupe ─────────────────────────────────────────────────────

class TestCapturaLeadMagnet:

    def test_capturar_guarda_datos_y_crea_novedad(self, client, db, aliado):
        r = client.post("/leads/capturar", params={
            "fuente": "recursos", "email": "lead@empresa.com",
            "nombre": "Juan Pérez", "telefono": "3424001122",
            "recurso": "guia-ventas", "ref_code": aliado.ref_code,
        })
        assert r.status_code == 200 and r.json()["ok"] is True

        log = db.query(AuditoriaLog).filter(AuditoriaLog.email_capturado == "lead@empresa.com").one()
        assert log.aliado_id == aliado.id
        assert log.nombre == "Juan Pérez"
        assert log.telefono == "3424001122"
        assert log.visto_en is None  # nueva, sin ver

        nov = db.query(Novedad).filter(Novedad.aliado_id == aliado.id, Novedad.tipo == "captura").all()
        assert len(nov) == 1

    def test_auditoria_mas_capturar_no_duplica(self, client, db, aliado):
        # La página de auditoría llama PRIMERO a /auditorias/log con email...
        r1 = client.post("/auditorias/log", params={
            "dominio": "empresa.com.ar", "score": 42,
            "ref_code": aliado.ref_code, "email": "dueno@empresa.com.ar",
        })
        assert r1.status_code == 200
        # ...y DESPUÉS a /leads/capturar con el mismo email (suscripción ML).
        r2 = client.post("/leads/capturar", params={
            "fuente": "auditoria", "email": "dueno@empresa.com.ar",
            "nombre": "Dueño Empresa", "ref_code": aliado.ref_code,
        })
        assert r2.status_code == 200

        logs = db.query(AuditoriaLog).filter(
            AuditoriaLog.email_capturado == "dueno@empresa.com.ar").all()
        assert len(logs) == 1                       # una sola captura
        assert logs[0].dominio == "empresa.com.ar"  # conserva el dominio auditado
        assert logs[0].score == 42
        assert logs[0].nombre == "Dueño Empresa"    # el 2do call completa el nombre

        # Un solo aviso in-app, no dos
        novs = db.query(Novedad).filter(Novedad.aliado_id == aliado.id, Novedad.tipo == "captura").all()
        assert len(novs) == 1


# ── 3: Bandeja Mis Capturas ───────────────────────────────────────────────────

class TestBandejaCapturas:

    def _capturar(self, client, aliado, email="lead@x.com"):
        client.post("/leads/capturar", params={
            "fuente": "recursos", "email": email, "ref_code": aliado.ref_code})

    def test_listado_y_no_vistas(self, client, db, aliado, token_aliado):
        self._capturar(client, aliado, "a@x.com")
        self._capturar(client, aliado, "b@x.com")

        r = client.get(f"/aliados/{aliado.codigo}/capturas", headers=AUTH(token_aliado))
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert data["no_vistas"] == 2
        emails = {c["email"] for c in data["capturas"]}
        assert emails == {"a@x.com", "b@x.com"}
        assert all(c["visto"] is False for c in data["capturas"])

    def test_marcar_vistas(self, client, db, aliado, token_aliado):
        self._capturar(client, aliado)
        r = client.post(f"/aliados/{aliado.codigo}/capturas/marcar-vistas", headers=AUTH(token_aliado))
        assert r.status_code == 200 and r.json()["marcadas"] == 1
        r2 = client.get(f"/aliados/{aliado.codigo}/capturas", headers=AUTH(token_aliado))
        assert r2.json()["no_vistas"] == 0

    def test_requiere_auth(self, client, aliado):
        r = client.get(f"/aliados/{aliado.codigo}/capturas")
        assert r.status_code == 401


# ── 4: Puente Capturas → CRM ──────────────────────────────────────────────────

class TestConvertirCaptura:

    def _captura(self, client, db, aliado):
        client.post("/leads/capturar", params={
            "fuente": "auditoria", "email": "hot@lead.com",
            "nombre": "Lead Caliente", "telefono": "3424111222",
            "ref_code": aliado.ref_code})
        return db.query(AuditoriaLog).filter(AuditoriaLog.email_capturado == "hot@lead.com").one()

    def test_convertir_crea_prospecto_y_linkea(self, client, db, aliado, token_aliado):
        log = self._captura(client, db, aliado)
        r = client.post(f"/capturas/{log.id}/convertir-prospecto", headers=AUTH(token_aliado))
        assert r.status_code == 200
        data = r.json()
        assert data["ya_existia"] is False
        pid = data["prospecto_id"]

        p = db.query(Prospecto).filter(Prospecto.id == pid).one()
        assert p.aliado_id == aliado.id
        assert p.email == "hot@lead.com"
        assert p.telefono == "3424111222"

        db.refresh(log)
        assert log.prospecto_id == pid
        assert log.visto_en is not None  # convertirla la marca como vista

        acts = db.query(ActividadProspecto).filter(
            ActividadProspecto.prospecto_id == pid,
            ActividadProspecto.tipo == "sistema").all()
        assert len(acts) == 1
        assert "Mis Capturas" in (acts[0].descripcion or "")

    def test_convertir_es_idempotente(self, client, db, aliado, token_aliado):
        log = self._captura(client, db, aliado)
        r1 = client.post(f"/capturas/{log.id}/convertir-prospecto", headers=AUTH(token_aliado))
        r2 = client.post(f"/capturas/{log.id}/convertir-prospecto", headers=AUTH(token_aliado))
        assert r1.status_code == 200 and r2.status_code == 200
        assert r2.json()["ya_existia"] is True
        assert r1.json()["prospecto_id"] == r2.json()["prospecto_id"]
        assert db.query(Prospecto).filter(Prospecto.aliado_id == aliado.id).count() == 1

    def test_no_convierte_captura_ajena(self, client, db, aliado, token_aliado):
        otro = Aliado(codigo="OTRO-9", nombre="Otro", email="otro9@test.com",
                      ref_code="otro9", password_hash="x", activo=True)
        db.add(otro); db.commit(); db.refresh(otro)
        client.post("/leads/capturar", params={
            "fuente": "recursos", "email": "ajeno@x.com", "ref_code": otro.ref_code})
        log = db.query(AuditoriaLog).filter(AuditoriaLog.email_capturado == "ajeno@x.com").one()
        r = client.post(f"/capturas/{log.id}/convertir-prospecto", headers=AUTH(token_aliado))
        assert r.status_code == 403


# ── 5: Simulador ──────────────────────────────────────────────────────────────

class TestSimuladorConfig:

    def test_expone_constantes_de_negocio(self, client):
        r = client.get("/simulador/config")
        assert r.status_code == 200
        data = r.json()
        assert data["planes"] == PLANES
        assert data["comision_recurrente_pct"] == 0.10
        assert data["niveles"]["ELITE"]["comision"] == 0.20
        assert "Plan Cuidado" in data["planes_continuidad"]


# ── 6: Novedades ──────────────────────────────────────────────────────────────

class TestNovedades:

    def test_listado_y_marcar_leidas(self, client, db, aliado, token_aliado):
        # Generar 1 novedad real vía captura
        client.post("/leads/capturar", params={
            "fuente": "recursos", "email": "n@x.com", "ref_code": aliado.ref_code})

        r = client.get(f"/aliados/{aliado.codigo}/novedades", headers=AUTH(token_aliado))
        assert r.status_code == 200
        data = r.json()
        assert data["no_leidas"] == 1
        assert data["novedades"][0]["tipo"] == "captura"
        assert data["novedades"][0]["tab"] == "capturas"

        r2 = client.post(f"/aliados/{aliado.codigo}/novedades/marcar-leidas", headers=AUTH(token_aliado))
        assert r2.status_code == 200
        r3 = client.get(f"/aliados/{aliado.codigo}/novedades", headers=AUTH(token_aliado))
        assert r3.json()["no_leidas"] == 0


# ── 7: Outreach IA (fallback plantilla en tests, sin GROQ_API_KEY) ────────────

class TestOutreach:

    def test_mensaje_bolsa_fallback_plantilla(self, client, db, aliado, lead_premium, token_aliado):
        lead_premium.estado = "reclamado"
        lead_premium.aliado_id = aliado.id
        lead_premium.rubro = "metalurgica"
        db.commit()
        r = client.post(f"/bolsa/{lead_premium.id}/mensaje-outreach", headers=AUTH(token_aliado))
        assert r.status_code == 200
        data = r.json()
        assert data["fuente"] in ("ia", "plantilla")
        assert lead_premium.empresa in data["mensaje"]
        assert len(data["mensaje"]) > 30

    def test_no_genera_para_lead_ajeno(self, client, db, aliado, lead_premium, token_aliado):
        otro = Aliado(codigo="OTRO-8", nombre="Otro", email="otro8@test.com",
                      ref_code="otro8", password_hash="x", activo=True)
        db.add(otro); db.commit(); db.refresh(otro)
        lead_premium.aliado_id = otro.id
        db.commit()
        r = client.post(f"/bolsa/{lead_premium.id}/mensaje-outreach", headers=AUTH(token_aliado))
        assert r.status_code == 403
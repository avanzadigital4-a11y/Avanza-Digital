"""
test_mejoras_canales.py  ·  Cobertura de las mejoras a Canal 1 / Canal 2
================================================================================

Cinco frentes:
  1. RAMPA (Canal 1): asignación de mentor + recompensa de primer cierre
     (créditos al mentee y al mentor) + IDEMPOTENCIA (no paga dos veces).
  2. RECICLADO (Canal 1): un lead no cerrado va a cooldown ('nurture') vía el
     hook del endpoint /bolsa/{id}/contactar; el job lo reactiva; tope → 'quemado'.
  3. DELIVERY (Canal 2): el referido convertido arranca su implementación y el
     aliado dueño la ve; ops la avanza.
  4. PUENTE: activar el otro canal habilita la bolsa (puede_canal1).
  5. REPARTO: proyección del split setter/closer (transparencia, no recálculo).

Usa los fixtures de conftest.py (db, client, aliado, lead_basico, token_admin...).
"""
from datetime import datetime, timedelta

from auth import crear_token
from models import Aliado, Mentoria, LeadBolsa, Referido


def _h(codigo):
    return {"Authorization": f"Bearer {crear_token(sub=codigo, tipo='aliado')}"}


def _h_admin():
    return {"Authorization": f"Bearer {crear_token(sub='admin-test', tipo='admin')}"}


def _mentor(db, codigo="AL-MNT", pais="AR"):
    m = Aliado(codigo=codigo, nombre="Mentor Senior", email=f"{codigo}@test.com",
               ref_code=codigo.lower(), activo=True, es_mentor=True, pais=pais,
               nivel="ELITE", creditos=0, terminos_aceptados=True)
    db.add(m); db.commit(); db.refresh(m)
    return m


# ════════════════════════ 1. RAMPA ══════════════════════════════════════════

def test_rampa_asigna_mentor_al_iniciar(db, aliado):
    import rampa
    mentor = _mentor(db)
    res = rampa.iniciar_rampa(db, aliado); db.commit()
    assert res["mentor_asignado"] is True
    db.refresh(aliado)
    assert aliado.mentor_id == mentor.id
    # Se abrió una mentoría activa.
    m = db.query(Mentoria).filter(Mentoria.mentee_id == aliado.id,
                                  Mentoria.estado == "activa").first()
    assert m is not None and m.mentor_id == mentor.id


def test_rampa_sin_mentores_no_rompe(db, aliado):
    import rampa
    res = rampa.iniciar_rampa(db, aliado); db.commit()
    assert res["mentor_asignado"] is False
    assert res["motivo"] == "sin_mentores"
    db.refresh(aliado)
    assert aliado.rampa_estado == "nuevo"   # arranca igual, sin mentor


def test_rampa_fallback_a_senior_sin_mentores_marcados(db, aliado):
    # Sin nadie marcado es_mentor, pero hay un aliado ELITE: la rampa lo usa
    # como mentor igual (self-bootstrap, sin configurar nada a mano).
    import rampa
    senior = Aliado(codigo="AL-ELITE", nombre="Crack", email="elite@test.com",
                    ref_code="alelite", activo=True, nivel="ELITE",
                    es_mentor=False, terminos_aceptados=True)
    db.add(senior); db.commit(); db.refresh(senior)
    res = rampa.iniciar_rampa(db, aliado); db.commit()
    assert res["mentor_asignado"] is True
    db.refresh(aliado)
    assert aliado.mentor_id == senior.id


def test_rampa_primer_cierre_premia_al_mentor_e_idempotente(db, aliado):
    import rampa
    mentor = _mentor(db)
    rampa.iniciar_rampa(db, aliado); db.commit()

    creditos_mentee_antes = aliado.creditos or 0
    res = rampa.procesar_primer_cierre(db, aliado.id); db.commit()
    assert res["ok"] is True
    db.refresh(aliado); db.refresh(mentor)

    # El debutante NO recibe créditos extra de la rampa (el bonus de primera
    # venta del sistema ya lo cubre). El MENTOR sí cobra su bonus.
    assert aliado.creditos == creditos_mentee_antes
    assert mentor.creditos == rampa.RECOMPENSA_MENTOR_CREDITOS
    assert aliado.rampa_estado == "primer_cierre"
    assert aliado.rampa_recompensa_en is not None
    m = db.query(Mentoria).filter(Mentoria.mentee_id == aliado.id).first()
    assert m.estado == "cerrada_cierre"

    # IDEMPOTENCIA: segundo llamado no vuelve a pagar al mentor.
    creditos_mentor_post = mentor.creditos
    res2 = rampa.procesar_primer_cierre(db, aliado.id); db.commit()
    assert res2["ok"] is False and res2["motivo"] == "ya_otorgado"
    db.refresh(mentor)
    assert mentor.creditos == creditos_mentor_post


def test_rampa_endpoint_devuelve_checklist(client, db, aliado, token_aliado):
    import rampa
    _mentor(db)
    rampa.iniciar_rampa(db, aliado); db.commit()
    r = client.get(f"/aliados/{aliado.codigo}/rampa", headers=_h(aliado.codigo))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estado"] == "nuevo"
    assert body["mentor"] is not None
    assert len(body["checklist"]) == 3
    assert 0 <= body["progreso_pct"] <= 100


# ════════════════════════ 2. RECICLADO ══════════════════════════════════════

def test_reciclado_no_contesto_va_a_cooldown_via_endpoint(client, db, aliado, lead_basico):
    # Lead reclamado por el aliado.
    lead_basico.estado = "reclamado"
    lead_basico.aliado_id = aliado.id
    lead_basico.fecha_reclamo = datetime.now()
    db.commit()

    r = client.patch(f"/bolsa/{lead_basico.id}/contactar?resultado=no_contesto",
                     headers=_h(aliado.codigo))
    assert r.status_code == 200, r.text

    db.refresh(lead_basico)
    # El hook lo mandó a nurture, NO de vuelta crudo a 'disponible'.
    assert lead_basico.estado == "nurture"
    assert lead_basico.cooldown_hasta is not None
    assert lead_basico.aliado_id is None
    assert lead_basico.intentos == 1


def test_reciclado_job_reactiva_tras_cooldown(db, aliado, lead_basico):
    import reciclado
    reciclado.registrar_intento(db, lead_basico, aliado.id, "no_contesto"); db.commit()
    assert lead_basico.estado == "nurture"
    # Forzar cooldown vencido y correr el job.
    lead_basico.cooldown_hasta = datetime.now() - timedelta(hours=1); db.commit()
    out = reciclado.procesar_cooldowns(db)
    assert out["reactivados"] == 1
    db.refresh(lead_basico)
    assert lead_basico.estado == "disponible"
    assert lead_basico.reciclados == 1


def test_reciclado_segundo_no_interesado_quema(db, aliado, lead_basico):
    import reciclado
    reciclado.registrar_intento(db, lead_basico, aliado.id, "no_interesado"); db.commit()
    assert lead_basico.estado == "nurture"
    r = reciclado.registrar_intento(db, lead_basico, aliado.id, "no_interesado"); db.commit()
    assert r.get("quemado") is True
    db.refresh(lead_basico)
    assert lead_basico.estado == "quemado"


def test_reciclado_historial_visible(client, db, aliado, lead_basico):
    import reciclado
    reciclado.registrar_intento(db, lead_basico, aliado.id, "no_contesto", nota="No atendió"); db.commit()
    r = client.get(f"/bolsa/{lead_basico.id}/historial", headers=_h(aliado.codigo))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intentos"] == 1
    assert len(body["historial"]) == 1
    assert body["historial"][0]["resultado"] == "no_contesto"


# ════════════════════════ 3. DELIVERY (Canal 2) ═════════════════════════════

def _referido_convertido(db, aliado, cliente="Taller Pérez"):
    r = Referido(aliado_id=aliado.id, nombre_cliente=cliente,
                 plan_elegido="Plan Pro", convertido=True)
    db.add(r); db.commit(); db.refresh(r)
    return r


def test_delivery_iniciar_pone_onboarding(db, aliado):
    import delivery
    ref = _referido_convertido(db, aliado)
    delivery.iniciar_implementacion(db, ref, commit=True)
    db.refresh(ref)
    assert ref.estado_implementacion == "onboarding"
    assert ref.impl_actualizado_en is not None


def test_delivery_aliado_ve_sus_entregas(client, db, aliado):
    import delivery
    ref = _referido_convertido(db, aliado)
    delivery.iniciar_implementacion(db, ref, commit=True)
    r = client.get(f"/aliados/{aliado.codigo}/entregas", headers=_h(aliado.codigo))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["entregas"][0]["estado"] == "onboarding"
    assert body["entregas"][0]["cliente"] == "Taller Pérez"


def test_delivery_ops_avanza_estado(client, db, aliado, admin_user):
    import delivery
    ref = _referido_convertido(db, aliado)
    delivery.iniciar_implementacion(db, ref, commit=True)
    r = client.post(f"/admin/entregas/{ref.id}/estado",
                    json={"estado": "entregado", "nota": "Listo"},
                    headers=_h_admin())
    assert r.status_code == 200, r.text
    db.refresh(ref)
    assert ref.estado_implementacion == "entregado"
    # Quedó timeline de los dos saltos (onboarding + entregado).
    import json
    assert len(json.loads(ref.impl_historial)) >= 2


# ════════════════════════ 4. PUENTE ENTRE CANALES ═══════════════════════════

def test_puente_activar_canal2(client, db, aliado):
    r = client.post(f"/aliados/{aliado.codigo}/canales/activar",
                    json={"canal": "canal2"}, headers=_h(aliado.codigo))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["canal2_habilitado"] is True
    db.refresh(aliado)
    assert aliado.puede_canal2 is True


def test_puente_canal2_activado_entra_a_bolsa(client, db, lead_basico):
    # Un aliado nacido canal2 (sin acceso a bolsa por defecto).
    a = Aliado(codigo="AL-C2", nombre="Contador", email="c2@test.com",
               ref_code="alc2", activo=True, tipo_aliado="canal2",
               canal1_habilitado=False, canal2_habilitado=True,
               nivel="BASIC", creditos=100, terminos_aceptados=True)
    db.add(a); db.commit(); db.refresh(a)

    # Sin canal1: la guarda de la bolsa lo bloquea.
    assert a.puede_canal1 is False

    # Activa canal1 y ahora sí puede reclamar.
    r = client.post(f"/aliados/{a.codigo}/canales/activar",
                    json={"canal": "canal1"}, headers=_h(a.codigo))
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.puede_canal1 is True


# ════════════════════════ 5. REPARTO (transparencia split) ══════════════════

def test_reparto_proyeccion_suma_el_bruto(client, db, aliado, aliado_con_sponsor):
    # Proyección del split: setter + closer = comisión total, por plan.
    r = client.get(f"/aliados/{aliado.codigo}/reparto/proyeccion",
                   params={"companero": aliado_con_sponsor.codigo},
                   headers=_h(aliado.codigo))
    assert r.status_code == 200, r.text
    body = r.json()
    tabla = body["si_cierra_companero"]["tabla"]
    assert len(tabla) > 0
    fila = tabla[0]
    # El total que paga Avanza no cambia: se reparte, no se suma.
    assert abs((fila["setter_usd"] + fila["closer_usd"]) - fila["comision_total_usd"]) < 0.02
"""
test_mejoras_canales.py  ·  Cobertura de las mejoras a Canal 1 / Canal 2
================================================================================

Cuatro frentes:
  1. RECICLADO (Canal 1): un lead no cerrado va a cooldown ('nurture') vía el
     hook del endpoint /bolsa/{id}/contactar; el job lo reactiva; tope → 'quemado'.
  2. DELIVERY (Canal 2): el referido convertido arranca su implementación y el
     aliado dueño la ve; ops la avanza.
  3. REPARTO: proyección del split setter/closer (transparencia, no recálculo).

Usa los fixtures de conftest.py (db, client, aliado, lead_basico, token_admin...).
"""
from datetime import datetime, timedelta

from auth import crear_token
from models import Aliado, LeadBolsa, Referido


def _h(codigo):
    return {"Authorization": f"Bearer {crear_token(sub=codigo, tipo='aliado')}"}


def _h_admin():
    return {"Authorization": f"Bearer {crear_token(sub='admin-test', tipo='admin')}"}


# ════════════════════════ 1. RECICLADO ══════════════════════════════════════

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


# ════════════════════════ 2. DELIVERY (Canal 2) ═════════════════════════════

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


# ════════════════════════ 3. REPARTO (transparencia split) ══════════════════

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
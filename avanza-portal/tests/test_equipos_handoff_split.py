"""
test_equipos_handoff_split.py  Bloque 2 de "Mi Equipo".

Dos cosas criticas:
  1. El handoff: el setter pasa un lead reclamado a un companero de equipo (closer),
     se reasigna el lead y se estampa la atribucion (setter + split).
  2. El SPLIT de la comision titular: cuando el plan trae setter, la comision se
     REPARTE (closer mayoria, setter su parte) sumando exacto el bruto, idempotente,
     y sin tocar el 5% del sponsor.
"""
from datetime import datetime

from auth import crear_token
from models import Equipo, LeadBolsa, PlanContinuidadActivo, Comision
from comisiones import _crear_comisiones_recurrentes_para_plan


def _h(codigo):
    return {"Authorization": f"Bearer {crear_token(sub=codigo, tipo='aliado')}"}


def _equipo_activo(db, a, b, split=0.40):
    eq = Equipo(aliado_a_id=a.id, aliado_b_id=b.id, estado="activo",
                setter_split_pct=split, confirmado_en=datetime.now())
    db.add(eq); db.commit(); db.refresh(eq)
    return eq


def _lead_reclamado(db, dueno_id, empresa="Acme SA"):
    l = LeadBolsa(empresa=empresa, rubro="Metalurgica", telefono="+5493410000000",
                  estado="reclamado", aliado_id=dueno_id, tier="basico",
                  costo_creditos=0, fecha_reclamo=datetime.now())
    db.add(l); db.commit(); db.refresh(l)
    return l


#  HANDOFF 

def test_handoff_reasigna_y_estampa(client, db, aliado, aliado_con_sponsor):
    # aliado = setter (AL-001), aliado_con_sponsor = closer (AL-002)
    _equipo_activo(db, aliado, aliado_con_sponsor, split=0.40)
    lead = _lead_reclamado(db, aliado.id)

    r = client.post(f"/aliados/{aliado.codigo}/equipo/handoff",
                    json={"lead_id": lead.id, "companero": aliado_con_sponsor.codigo},
                    headers=_h(aliado.codigo))
    assert r.status_code == 200, r.text

    db.refresh(lead)
    assert lead.aliado_id == aliado_con_sponsor.id      # ahora es del closer
    assert lead.setter_id == aliado.id                  # se recuerda quien fue el setter
    assert lead.setter_split_pct == 0.40


def test_handoff_sin_equipo_activo_falla(client, db, aliado, aliado_con_sponsor):
    lead = _lead_reclamado(db, aliado.id)  # sin equipo entre ellos
    r = client.post(f"/aliados/{aliado.codigo}/equipo/handoff",
                    json={"lead_id": lead.id, "companero": aliado_con_sponsor.codigo},
                    headers=_h(aliado.codigo))
    assert r.status_code == 409


def test_handoff_lead_ajeno_falla(client, db, aliado, aliado_con_sponsor):
    _equipo_activo(db, aliado, aliado_con_sponsor)
    lead = _lead_reclamado(db, aliado_con_sponsor.id)  # el lead es del closer, no del setter
    r = client.post(f"/aliados/{aliado.codigo}/equipo/handoff",
                    json={"lead_id": lead.id, "companero": aliado_con_sponsor.codigo},
                    headers=_h(aliado.codigo))
    assert r.status_code == 404


def test_handoff_lead_no_reclamado_falla(client, db, aliado, aliado_con_sponsor):
    _equipo_activo(db, aliado, aliado_con_sponsor)
    l = LeadBolsa(empresa="Disp SA", rubro="Metalurgica", telefono="+5493410000001",
                  estado="disponible", tier="basico", costo_creditos=0)
    db.add(l); db.commit(); db.refresh(l)
    r = client.post(f"/aliados/{aliado.codigo}/equipo/handoff",
                    json={"lead_id": l.id, "companero": aliado_con_sponsor.codigo},
                    headers=_h(aliado.codigo))
    assert r.status_code in (404, 409)


#  SPLIT DE COMISION (lo critico: la plata) 

def _plan(db, closer_id, cliente, setter_id=None, split=None, precio=100.0):
    p = PlanContinuidadActivo(
        aliado_id=closer_id, nombre_cliente=cliente,
        plan_continuidad="Plan Cuidado", precio_mensual_usd=precio, comision_pct=0.10,
        setter_id=setter_id, setter_split_pct=split)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_split_reparte_la_comision_titular(db, aliado, aliado_con_sponsor):
    closer, setter = aliado, aliado_con_sponsor
    p = _plan(db, closer.id, "Cliente X", setter_id=setter.id, split=0.40)
    now = datetime.now()
    _crear_comisiones_recurrentes_para_plan(db, p, now.month, now.year, now)
    db.commit()

    c_closer = db.query(Comision).filter(
        Comision.aliado_id == closer.id, Comision.nombre_cliente == "Cliente X").first()
    c_setter = db.query(Comision).filter(
        Comision.aliado_id == setter.id, Comision.nombre_cliente == "EQUIPO: Cliente X").first()

    assert c_closer is not None and c_setter is not None
    assert c_closer.comision_usd == 6.0    # closer: 60% de 10
    assert c_setter.comision_usd == 4.0    # setter: 40% de 10
    # El total no cambia: se reparte, no se suma.
    assert round(c_closer.comision_usd + c_setter.comision_usd, 2) == 10.0


def test_split_es_idempotente(db, aliado, aliado_con_sponsor):
    closer, setter = aliado, aliado_con_sponsor
    p = _plan(db, closer.id, "Cliente Y", setter_id=setter.id, split=0.40)
    now = datetime.now()
    _crear_comisiones_recurrentes_para_plan(db, p, now.month, now.year, now)
    db.commit()
    # Segunda corrida del mismo mes: no debe duplicar ni volver a reducir.
    _crear_comisiones_recurrentes_para_plan(db, p, now.month, now.year, now)
    db.commit()

    n_closer = db.query(Comision).filter(
        Comision.aliado_id == closer.id, Comision.nombre_cliente == "Cliente Y").count()
    n_setter = db.query(Comision).filter(
        Comision.aliado_id == setter.id, Comision.nombre_cliente == "EQUIPO: Cliente Y").count()
    assert n_closer == 1 and n_setter == 1

    c_closer = db.query(Comision).filter(
        Comision.aliado_id == closer.id, Comision.nombre_cliente == "Cliente Y").first()
    assert c_closer.comision_usd == 6.0    # no se redujo dos veces


def test_sin_setter_no_hay_split(db, aliado):
    p = _plan(db, aliado.id, "Solo Z")  # sin setter
    now = datetime.now()
    _crear_comisiones_recurrentes_para_plan(db, p, now.month, now.year, now)
    db.commit()

    c = db.query(Comision).filter(
        Comision.aliado_id == aliado.id, Comision.nombre_cliente == "Solo Z").first()
    assert c.comision_usd == 10.0   # comision completa, sin repartir
    assert db.query(Comision).filter(Comision.nombre_cliente == "EQUIPO: Solo Z").first() is None
"""
test_equipos_modelo_b.py  Split de SISTEMAS (one-shot) + Modelo B (override del
sponsor del setter), en las dos vias: checkout (sistemas) y motor recurrente
(mantenimiento).

Escenario: closer sin sponsor + setter cuyo sponsor es Pepito. En un deal de
equipo deben cobrar 3 (o 4) partes: closer, setter, y el sponsor de cada uno
que exista (Modelo B = 5% del deal completo por cada sponsor).
"""
from datetime import datetime
from passlib.context import CryptContext

from models import Aliado, Prospecto, LinkPago, Comision, PlanContinuidadActivo, PLANES
from comisiones import _crear_comisiones_recurrentes_para_plan
from checkout import _procesar_pago_confirmado

_pwd = CryptContext(schemes=["sha256_crypt"], deprecated="auto")


def _aliado(db, codigo, nombre, ref, sponsor_id=None):
    a = Aliado(codigo=codigo, nombre=nombre, email=f"{ref}@test.com",
               whatsapp="+54911" + codigo.replace("AL-", "0000"), ciudad="X",
               ref_code=ref, password_hash=_pwd.hash("x"), activo=True,
               nivel="BASIC", terminos_aceptados=True, sponsor_id=sponsor_id)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_split_sistemas_oneshot_con_modelo_b(db, aliado):
    closer = aliado  # AL-001, sin sponsor (asi no contamina)
    pepito = _aliado(db, "AL-200", "Pepito", "pepito")
    setter = _aliado(db, "AL-201", "Setter Sis", "settersis", sponsor_id=pepito.id)

    # Prospecto handed-off: del closer, con el setter atribuido.
    pro = Prospecto(aliado_id=closer.id, nombre="Cliente Sis",
                    setter_id=setter.id, setter_split_pct=0.40)
    db.add(pro); db.commit(); db.refresh(pro)

    plan = "Plan Industrial"          # 4900
    valor = PLANES[plan]
    # La comision se calcula con el pct del aliado AL MOMENTO de la venta; la
    # primera venta puede subir su nivel/pct, asi que lo capturamos antes.
    pct = closer.comision_pct or 0.10

    lp = LinkPago(aliado_id=closer.id, prospecto_id=pro.id, plan=plan, moneda="usd",
                  precio_usd=valor, checkout_url="http://x", processor="usdt", estado="activo")
    db.add(lp); db.commit(); db.refresh(lp)

    _procesar_pago_confirmado(db, closer.ref_code, plan, "Cliente Sis",
                              "usdt", "pay_sis_1", lp.id)
    db.commit()
    titular = round(valor * pct, 2)
    esperado_setter = round(titular * 0.40, 2)
    esperado_closer = round(titular - esperado_setter, 2)

    c_closer = db.query(Comision).filter(
        Comision.aliado_id == closer.id, Comision.nombre_cliente == "Cliente Sis").first()
    c_setter = db.query(Comision).filter(
        Comision.aliado_id == setter.id, Comision.nombre_cliente == "EQUIPO: Cliente Sis").first()
    c_pepito = db.query(Comision).filter(
        Comision.aliado_id == pepito.id, Comision.nombre_cliente == "RED EQUIPO: Cliente Sis").first()

    assert c_closer and c_setter and c_pepito
    assert c_closer.comision_usd == esperado_closer       # closer: 60% del titular
    assert c_setter.comision_usd == esperado_setter       # setter: 40% del titular
    assert c_pepito.comision_usd == round(valor * 0.05, 2)  # Modelo B: 5% del deal completo
    # El titular (closer+setter) suma exacto; el override del sponsor es aparte.
    assert round(c_closer.comision_usd + c_setter.comision_usd, 2) == titular


def test_modelo_b_sponsor_setter_en_mantenimiento(db, aliado):
    closer = aliado  # AL-001, sin sponsor
    pepito = _aliado(db, "AL-300", "Pepito M", "pepitom")
    setter = _aliado(db, "AL-301", "Setter Man", "setterman", sponsor_id=pepito.id)

    p = PlanContinuidadActivo(
        aliado_id=closer.id, nombre_cliente="Cli M", plan_continuidad="Plan Liderazgo",
        precio_mensual_usd=450.0, comision_pct=0.10,
        setter_id=setter.id, setter_split_pct=0.40)
    db.add(p); db.commit(); db.refresh(p)

    now = datetime.now()
    _crear_comisiones_recurrentes_para_plan(db, p, now.month, now.year, now)
    db.commit()

    # Pepito (sponsor del setter) cobra 5% de 450 = 22.5
    c_pepito = db.query(Comision).filter(
        Comision.aliado_id == pepito.id, Comision.nombre_cliente == "RED EQUIPO: Cli M").first()
    assert c_pepito is not None
    assert c_pepito.comision_usd == round(450.0 * 0.05, 2)

    # Y el split titular sigue: setter 40% de 45 = 18, closer 27.
    c_setter = db.query(Comision).filter(
        Comision.aliado_id == setter.id, Comision.nombre_cliente == "EQUIPO: Cli M").first()
    assert c_setter.comision_usd == 18.0
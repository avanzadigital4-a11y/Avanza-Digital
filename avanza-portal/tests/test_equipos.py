"""
test_equipos.py  Bloque 1 de la feature "Mi Equipo": formacion del vinculo.

Cubre: solicitar / aceptar / rechazar / ajustar split / disolver, mas los
guardrails (no equipo con uno mismo, no duplicar, split acotado a la banda,
solo el receptor acepta, solo miembros tocan el equipo).

Usa los fixtures de conftest: `aliado` (AL-001) y `aliado_con_sponsor` (AL-002).
El vinculo de sponsor entre ellos es irrelevante para equipos (son conceptos
distintos), justamente por eso sirven bien para probar.
"""
from auth import crear_token


def _h(codigo):
    """Header de auth para un aliado por su codigo."""
    return {"Authorization": f"Bearer {crear_token(sub=codigo, tipo='aliado')}"}


def _solicitar(client, de, a, split=None):
    body = {"companero": a.codigo}
    if split is not None:
        body["setter_split_pct"] = split
    return client.post(f"/aliados/{de.codigo}/equipo/solicitar",
                       json=body, headers=_h(de.codigo))


#  Flujo feliz 

def test_solicitar_crea_pendiente_y_la_ve_el_receptor(client, aliado, aliado_con_sponsor):
    r = _solicitar(client, aliado, aliado_con_sponsor)
    assert r.status_code == 200
    data = r.json()
    assert data["estado"] == "pendiente"
    eid = data["equipo_id"]

    # AL-002 la ve como solicitud recibida
    r2 = client.get(f"/aliados/{aliado_con_sponsor.codigo}/equipo",
                    headers=_h(aliado_con_sponsor.codigo))
    assert r2.status_code == 200
    recibidas = r2.json()["solicitudes_recibidas"]
    assert len(recibidas) == 1
    assert recibidas[0]["equipo_id"] == eid

    # AL-001 la ve como enviada
    r3 = client.get(f"/aliados/{aliado.codigo}/equipo", headers=_h(aliado.codigo))
    assert len(r3.json()["solicitudes_enviadas"]) == 1


def test_aceptar_activa_para_ambos(client, aliado, aliado_con_sponsor):
    eid = _solicitar(client, aliado, aliado_con_sponsor).json()["equipo_id"]

    r = client.post(f"/aliados/{aliado_con_sponsor.codigo}/equipo/{eid}/aceptar",
                    headers=_h(aliado_con_sponsor.codigo))
    assert r.status_code == 200
    assert r.json()["estado"] == "activo"

    # Ambos lo ven en activos
    for al in (aliado, aliado_con_sponsor):
        data = client.get(f"/aliados/{al.codigo}/equipo", headers=_h(al.codigo)).json()
        assert len(data["activos"]) == 1
        assert data["activos"][0]["equipo_id"] == eid


def test_rechazar_no_deja_equipo_activo(client, aliado, aliado_con_sponsor):
    eid = _solicitar(client, aliado, aliado_con_sponsor).json()["equipo_id"]
    r = client.post(f"/aliados/{aliado_con_sponsor.codigo}/equipo/{eid}/rechazar",
                    headers=_h(aliado_con_sponsor.codigo))
    assert r.status_code == 200
    data = client.get(f"/aliados/{aliado.codigo}/equipo", headers=_h(aliado.codigo)).json()
    assert data["activos"] == []


def test_disolver_termina_el_equipo(client, aliado, aliado_con_sponsor):
    eid = _solicitar(client, aliado, aliado_con_sponsor).json()["equipo_id"]
    client.post(f"/aliados/{aliado_con_sponsor.codigo}/equipo/{eid}/aceptar",
                headers=_h(aliado_con_sponsor.codigo))
    r = client.post(f"/aliados/{aliado.codigo}/equipo/{eid}/disolver",
                    headers=_h(aliado.codigo))
    assert r.status_code == 200
    data = client.get(f"/aliados/{aliado.codigo}/equipo", headers=_h(aliado.codigo)).json()
    assert data["activos"] == []


#  Split: banda y ajuste 

def test_split_se_acota_a_la_banda(client, aliado, aliado_con_sponsor):
    # Pide 0.90 (fuera de banda) -> debe quedar en el maximo 0.50
    eid = _solicitar(client, aliado, aliado_con_sponsor, split=0.90).json()["equipo_id"]
    recibidas = client.get(f"/aliados/{aliado_con_sponsor.codigo}/equipo",
                           headers=_h(aliado_con_sponsor.codigo)).json()["solicitudes_recibidas"]
    assert recibidas[0]["setter_split_pct"] == 0.50


def test_ajustar_split_actualiza_dentro_de_banda(client, aliado, aliado_con_sponsor):
    eid = _solicitar(client, aliado, aliado_con_sponsor).json()["equipo_id"]
    client.post(f"/aliados/{aliado_con_sponsor.codigo}/equipo/{eid}/aceptar",
                headers=_h(aliado_con_sponsor.codigo))
    r = client.post(f"/aliados/{aliado.codigo}/equipo/{eid}/split",
                    json={"setter_split_pct": 0.45}, headers=_h(aliado.codigo))
    assert r.status_code == 200
    assert r.json()["setter_split_pct"] == 0.45
    # 0.10 (debajo del minimo) -> clamp a 0.25
    r2 = client.post(f"/aliados/{aliado.codigo}/equipo/{eid}/split",
                     json={"setter_split_pct": 0.10}, headers=_h(aliado.codigo))
    assert r2.json()["setter_split_pct"] == 0.25


#  Guardrails 

def test_no_equipo_con_uno_mismo(client, aliado):
    r = _solicitar(client, aliado, aliado)
    assert r.status_code == 400


def test_no_duplicar_solicitud_pendiente(client, aliado, aliado_con_sponsor):
    _solicitar(client, aliado, aliado_con_sponsor)
    r = _solicitar(client, aliado, aliado_con_sponsor)
    assert r.status_code == 409


def test_no_duplicar_equipo_activo_ni_en_la_otra_direccion(client, aliado, aliado_con_sponsor):
    eid = _solicitar(client, aliado, aliado_con_sponsor).json()["equipo_id"]
    client.post(f"/aliados/{aliado_con_sponsor.codigo}/equipo/{eid}/aceptar",
                headers=_h(aliado_con_sponsor.codigo))
    # Ahora AL-002 intenta solicitarle a AL-001 (direccion inversa) -> 409
    r = _solicitar(client, aliado_con_sponsor, aliado)
    assert r.status_code == 409


def test_solo_el_receptor_puede_aceptar(client, aliado, aliado_con_sponsor):
    eid = _solicitar(client, aliado, aliado_con_sponsor).json()["equipo_id"]
    # El que envio (AL-001) NO puede aceptar su propia solicitud
    r = client.post(f"/aliados/{aliado.codigo}/equipo/{eid}/aceptar",
                    headers=_h(aliado.codigo))
    assert r.status_code == 403


def test_solo_miembros_pueden_disolver(client, aliado, aliado_con_sponsor, db):
    # Un tercer aliado ajeno al equipo
    from models import Aliado
    from passlib.context import CryptContext
    pwd = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
    ajeno = Aliado(codigo="AL-099", nombre="Ajeno", email="ajeno@test.com",
                   whatsapp="+5491100000099", ciudad="X", ref_code="ajeno",
                   password_hash=pwd.hash("x"), activo=True, nivel="BASIC",
                   terminos_aceptados=True)
    db.add(ajeno)
    db.commit()

    eid = _solicitar(client, aliado, aliado_con_sponsor).json()["equipo_id"]
    client.post(f"/aliados/{aliado_con_sponsor.codigo}/equipo/{eid}/aceptar",
                headers=_h(aliado_con_sponsor.codigo))
    r = client.post(f"/aliados/{ajeno.codigo}/equipo/{eid}/disolver",
                    headers=_h(ajeno.codigo))
    assert r.status_code == 403
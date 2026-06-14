"""
test_comunidad_foro.py — Camino B: foro de la comunidad.

Cubre categorías, pregunta resuelta + respuesta aceptada, estado de mejoras,
búsqueda/orden y los avisos (campanita) asociados.
"""
from auth import crear_token
from models import PostComunidad, ComentarioComunidad, Novedad


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def _post(client, tok, codigo, categoria, titulo, cuerpo):
    return client.post("/comunidad/post", headers=_auth(tok), json={
        "codigo_aliado": codigo, "categoria": categoria,
        "titulo": titulo, "cuerpo": cuerpo,
    })


# ─── CATEGORÍAS ──────────────────────────────────────────────────────────────

def test_crear_post_con_categoria(client, aliado, token_aliado, db):
    r = _post(client, token_aliado, aliado.codigo, "pregunta",
              "¿Cómo cargo un lead?", "No me queda claro el flujo de carga.")
    assert r.status_code == 200
    assert r.json()["categoria"] == "pregunta"
    p = db.query(PostComunidad).first()
    assert p.categoria == "pregunta"


def test_mejora_arranca_en_recibido(client, aliado, token_aliado, db):
    r = _post(client, token_aliado, aliado.codigo, "mejora",
              "Filtro por país en la bolsa", "Estaría bueno filtrar la bolsa por país.")
    assert r.status_code == 200
    p = db.query(PostComunidad).filter(PostComunidad.categoria == "mejora").first()
    assert p.estado_mejora == "recibido"


def test_feed_filtra_por_categoria_y_busca(client, aliado, token_aliado):
    _post(client, token_aliado, aliado.codigo, "pregunta", "Duda sobre comisiones", "cuerpo largo")
    _post(client, token_aliado, aliado.codigo, "victoria", "Cerré un industrial", "cuerpo largo")

    r = client.get("/comunidad/feed", params={"categoria": "pregunta"})
    cats = {p["categoria"] for p in r.json()["posts"]}
    assert cats == {"pregunta"}

    r = client.get("/comunidad/feed", params={"q": "industrial"})
    titulos = [p["titulo"] for p in r.json()["posts"]]
    assert any("industrial" in t.lower() for t in titulos)
    assert all("comisiones" not in t.lower() for t in titulos)


# ─── PREGUNTA RESUELTA + RESPUESTA ACEPTADA ──────────────────────────────────

def test_resolver_acepta_respuesta_y_notifica(client, aliado, aliado_con_sponsor,
                                               token_aliado, db):
    # aliado (AL-001) pregunta; aliado_con_sponsor (AL-002) responde.
    pid = _post(client, token_aliado, aliado.codigo, "pregunta",
                "¿Cuánto tarda el pago?", "Quiero saber los plazos.").json()["id"]

    tok2 = crear_token(sub=aliado_con_sponsor.codigo, tipo="aliado")
    cid = client.post(f"/comunidad/{pid}/comentario", headers=_auth(tok2),
                      json={"codigo_aliado": aliado_con_sponsor.codigo,
                            "cuerpo": "Tarda 48hs hábiles."}).json()["id"]

    # El autor (AL-001) acepta la respuesta de AL-002.
    r = client.post(f"/comunidad/{pid}/resolver", headers=_auth(token_aliado),
                    json={"resuelto": True, "comentario_id": cid})
    assert r.status_code == 200 and r.json()["resuelto"] is True

    p = db.query(PostComunidad).filter(PostComunidad.id == pid).first()
    c = db.query(ComentarioComunidad).filter(ComentarioComunidad.id == cid).first()
    assert p.resuelto is True
    assert c.aceptada is True

    # El que respondió (AL-002) recibe aviso de aceptación.
    nov = db.query(Novedad).filter(Novedad.aliado_id == aliado_con_sponsor.id,
                                   Novedad.tipo == "comunidad").all()
    assert any("acept" in (n.titulo or "").lower() for n in nov)


def test_solo_el_autor_resuelve(client, aliado, aliado_con_sponsor, token_aliado, db):
    pid = _post(client, token_aliado, aliado.codigo, "pregunta", "Pregunta X", "cuerpo largo").json()["id"]
    tok2 = crear_token(sub=aliado_con_sponsor.codigo, tipo="aliado")
    r = client.post(f"/comunidad/{pid}/resolver", headers=_auth(tok2), json={"resuelto": True})
    assert r.status_code == 403


def test_comentar_notifica_al_autor(client, aliado, aliado_con_sponsor, token_aliado, db):
    pid = _post(client, token_aliado, aliado.codigo, "pregunta", "Otra duda", "cuerpo largo").json()["id"]
    tok2 = crear_token(sub=aliado_con_sponsor.codigo, tipo="aliado")
    client.post(f"/comunidad/{pid}/comentario", headers=_auth(tok2),
                json={"codigo_aliado": aliado_con_sponsor.codigo, "cuerpo": "Mi respuesta."})
    nov = db.query(Novedad).filter(Novedad.aliado_id == aliado.id,
                                   Novedad.tipo == "comunidad").all()
    assert len(nov) >= 1


# ─── ESTADO DE MEJORAS (ADMIN) ───────────────────────────────────────────────

def test_admin_cambia_estado_mejora_y_notifica(client, aliado, token_aliado, token_admin, db):
    pid = _post(client, token_aliado, aliado.codigo, "mejora",
                "Modo oscuro", "Sería lindo un modo oscuro.").json()["id"]

    r = client.post(f"/admin/comunidad/{pid}/estado", headers=_auth(token_admin),
                    json={"estado": "planificado"})
    assert r.status_code == 200

    p = db.query(PostComunidad).filter(PostComunidad.id == pid).first()
    assert p.estado_mejora == "planificado"

    nov = db.query(Novedad).filter(Novedad.aliado_id == aliado.id,
                                   Novedad.tipo == "comunidad").all()
    assert any("sugerencia" in (n.titulo or "").lower() for n in nov)


def test_estado_invalido_rechazado(client, aliado, token_aliado, token_admin):
    pid = _post(client, token_aliado, aliado.codigo, "mejora", "Idea", "cuerpo largo").json()["id"]
    r = client.post(f"/admin/comunidad/{pid}/estado", headers=_auth(token_admin),
                    json={"estado": "cualquiercosa"})
    assert r.status_code == 400


def test_estado_requiere_admin(client, aliado, token_aliado):
    pid = _post(client, token_aliado, aliado.codigo, "mejora", "Idea2", "cuerpo largo").json()["id"]
    r = client.post(f"/admin/comunidad/{pid}/estado", headers=_auth(token_aliado),
                    json={"estado": "hecho"})
    assert r.status_code in (401, 403)
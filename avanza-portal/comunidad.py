"""
comunidad.py — Foro de la comunidad de aliados (Camino B)
================================================================================

Evolución del feed original a un foro estilo "Netlify Answers", PERO dentro del
portal (mismo login, misma base, mismas notificaciones). Sobre lo que ya existía
(posts con tipo, comentarios, likes, fijar, ocultar) se agrega:

  - CATEGORÍAS: pregunta (Q&A) · mejora (pedidos del portal) · charla · victoria.
  - PREGUNTA RESUELTA + RESPUESTA ACEPTADA: el autor marca el comentario que le
    sirvió; queda destacado y la pregunta pasa a "resuelta".
  - ESTADO DE MEJORAS: el admin mueve recibido → evaluacion → planificado →
    hecho/descartado, y el autor se entera (cierra el loop de feedback).
  - ORDEN Y BÚSQUEDA: recientes / sin responder / más votados + buscador.
  - AVISOS: usa la campanita + push que ya andan (notificaciones.py).

Compatibilidad: los posts viejos (sin `categoria`) derivan su categoría del
`tipo` legacy, así que el feed sigue mostrándolos sin migración de datos.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

import schemas
from auth import current_aliado_required, current_admin_required
from database import get_db
from models import Aliado, ComentarioComunidad, PostComunidad
from notificaciones import notificar_aliado, enviar_push_a_aliado

router = APIRouter(tags=["comunidad"])

CATEGORIAS = ("pregunta", "mejora", "charla", "victoria")
ESTADOS_MEJORA = ("recibido", "evaluacion", "planificado", "hecho", "descartado")

# Mapeos legacy <-> foro (para no romper datos ni el campo `tipo` existente).
_TIPO_DESDE_CAT = {"pregunta": "pregunta", "victoria": "win", "charla": "tip", "mejora": "tip"}
_CAT_DESDE_TIPO = {"pregunta": "pregunta", "win": "victoria", "tip": "charla"}


def _categoria_efectiva(p: PostComunidad) -> str:
    """Categoría real del post: la explícita, o la derivada del tipo legacy."""
    c = (p.categoria or "").strip()
    if c and c != "general":
        return c
    return _CAT_DESDE_TIPO.get(p.tipo or "", "charla")


def _serializar_post(p: PostComunidad, db: Session) -> dict:
    coms = (db.query(ComentarioComunidad)
            .filter(ComentarioComunidad.post_id == p.id)
            .order_by(ComentarioComunidad.aceptada.desc(),
                      ComentarioComunidad.creado_en.asc())
            .all())
    return {
        "id": p.id,
        "categoria": _categoria_efectiva(p),
        "tipo": p.tipo,
        "titulo": p.titulo,
        "cuerpo": p.cuerpo,
        "likes": p.likes or 0,
        "fijado": bool(p.fijado),
        "resuelto": bool(p.resuelto),
        "estado_mejora": p.estado_mejora,
        "autor": p.aliado.nombre.split()[0] if p.aliado else "—",
        "autor_codigo": p.aliado.codigo if p.aliado else None,
        "autor_nivel": p.aliado.nivel_calculado if p.aliado else None,
        "fecha": p.creado_en.strftime("%d/%m/%Y %H:%M") if p.creado_en else None,
        "n_comentarios": len(coms),
        "comentarios": [{
            "id": c.id,
            "autor": c.aliado.nombre.split()[0] if c.aliado else "—",
            "autor_codigo": c.aliado.codigo if c.aliado else None,
            "cuerpo": c.cuerpo,
            "aceptada": bool(c.aceptada),
            "fecha": c.creado_en.strftime("%d/%m/%Y %H:%M") if c.creado_en else None,
        } for c in coms],
    }


# ─── ENDPOINTS DE ALIADO ─────────────────────────────────────────────────────

@router.get("/comunidad/feed")
def ver_feed_comunidad(
    categoria: str = Query("", description="pregunta|mejora|charla|victoria (vacío = todas)"),
    orden: str = Query("recientes", description="recientes|sin_responder|mas_votados"),
    q: str = Query("", description="texto a buscar en título/cuerpo"),
    limit: int = Query(40, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Feed del foro. Filtra por categoría, ordena y busca. Los posts ocultos
    no se devuelven. Se filtra en Python (escala de comunidad lo permite) para
    soportar las categorías derivadas de posts viejos sin migrar datos."""
    base = (db.query(PostComunidad)
            .filter(PostComunidad.oculto == False)  # noqa: E712
            .order_by(PostComunidad.fijado.desc(), PostComunidad.creado_en.desc())
            .limit(300).all())

    cat = (categoria or "").strip().lower()
    qq = (q or "").strip().lower()

    posts = []
    for p in base:
        ce = _categoria_efectiva(p)
        if cat and cat in CATEGORIAS and ce != cat:
            continue
        if orden == "sin_responder" and not (ce == "pregunta" and not p.resuelto):
            continue
        if qq and qq not in (p.titulo or "").lower() and qq not in (p.cuerpo or "").lower():
            continue
        posts.append(p)

    if orden == "mas_votados":
        posts.sort(key=lambda p: (p.fijado, p.likes or 0), reverse=True)
    # 'recientes' y 'sin_responder' ya vienen ordenados por fijado+fecha desde la query.

    posts = posts[:limit]
    return {"posts": [_serializar_post(p, db) for p in posts]}


@router.post("/comunidad/post")
def crear_post(post: schemas.PostComunidadIn,
               aliado: Aliado = Depends(current_aliado_required),
               db: Session = Depends(get_db)):
    """Publica un post. La autoría sale del JWT (se ignora codigo_aliado del body).

    Acepta `categoria` (foro). Si no viene, se deriva del `tipo` legacy. Para
    mantener compatibilidad se sigue guardando `tipo`.
    """
    cat = (post.categoria or "").strip().lower()
    if not cat:
        cat = _CAT_DESDE_TIPO.get(post.tipo or "", "charla")
    if cat not in CATEGORIAS:
        raise HTTPException(400, "Categoría inválida.")
    if len(post.titulo.strip()) < 3 or len(post.cuerpo.strip()) < 5:
        raise HTTPException(400, "Título y cuerpo requeridos.")

    p = PostComunidad(
        aliado_id=aliado.id,
        tipo=_TIPO_DESDE_CAT.get(cat, "tip"),
        categoria=cat,
        titulo=post.titulo.strip()[:200],
        cuerpo=post.cuerpo.strip()[:3000],
        estado_mejora=("recibido" if cat == "mejora" else None),
    )
    db.add(p); db.commit(); db.refresh(p)
    return {"mensaje": "Post publicado.", "id": p.id, "categoria": cat}


@router.post("/comunidad/{id}/like")
def like_post(id: int,
              aliado: Aliado = Depends(current_aliado_required),
              db: Session = Depends(get_db)):
    """Like a un post. Requiere aliado autenticado (anti-spam)."""
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p:
        raise HTTPException(404, "Post no encontrado.")
    p.likes = (p.likes or 0) + 1
    db.commit()
    return {"likes": p.likes}


@router.post("/comunidad/{id}/comentario")
def comentar(id: int, com: schemas.ComentarioComunidadIn,
             aliado: Aliado = Depends(current_aliado_required),
             db: Session = Depends(get_db)):
    """Comenta un post. Autoría por JWT. Avisa al autor del post (campanita + push)."""
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p:
        raise HTTPException(404, "Post no encontrado.")
    if len(com.cuerpo.strip()) < 2:
        raise HTTPException(400, "Comentario vacío.")

    c = ComentarioComunidad(post_id=p.id, aliado_id=aliado.id, cuerpo=com.cuerpo.strip()[:1000])
    db.add(c)

    # Aviso al autor del post (si no se comentó a sí mismo).
    if p.aliado_id and p.aliado_id != aliado.id:
        quien = (aliado.nombre or "Alguien").split()[0]
        notificar_aliado(db, p.aliado_id, "comunidad",
                         f"{quien} respondió tu publicación",
                         f"«{(p.titulo or '')[:80]}»", tab="comunidad")
    db.commit()

    if p.aliado_id and p.aliado_id != aliado.id:
        try:
            enviar_push_a_aliado(db, p.aliado_id, "Nueva respuesta en la comunidad",
                                 f"{(aliado.nombre or 'Alguien').split()[0]} respondió tu publicación.", "/")
        except Exception:
            pass
    return {"mensaje": "Comentario publicado.", "id": c.id}


@router.post("/comunidad/{id}/resolver")
def resolver_post(id: int, body: schemas.ResolverPostIn,
                  aliado: Aliado = Depends(current_aliado_required),
                  db: Session = Depends(get_db)):
    """El AUTOR marca su pregunta como resuelta y, opcionalmente, acepta una
    respuesta (el comentario que le sirvió). Reabrir = resuelto=false."""
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p:
        raise HTTPException(404, "Post no encontrado.")
    if p.aliado_id != aliado.id:
        raise HTTPException(403, "Solo el autor de la pregunta puede marcarla.")

    p.resuelto = bool(body.resuelto)

    # Limpiar aceptadas previas en este post.
    db.query(ComentarioComunidad).filter(
        ComentarioComunidad.post_id == p.id,
        ComentarioComunidad.aceptada == True  # noqa: E712
    ).update({ComentarioComunidad.aceptada: False}, synchronize_session=False)

    aceptado = None
    if body.resuelto and body.comentario_id:
        aceptado = (db.query(ComentarioComunidad)
                    .filter(ComentarioComunidad.id == body.comentario_id,
                            ComentarioComunidad.post_id == p.id).first())
        if not aceptado:
            raise HTTPException(404, "Comentario no encontrado en este post.")
        aceptado.aceptada = True
        # Avisar al autor de la respuesta aceptada (si no es el mismo).
        if aceptado.aliado_id and aceptado.aliado_id != aliado.id:
            notificar_aliado(db, aceptado.aliado_id, "comunidad",
                             "Tu respuesta fue aceptada ✓",
                             f"Marcaron tu respuesta como la solución de «{(p.titulo or '')[:80]}».",
                             tab="comunidad")
    db.commit()

    if aceptado and aceptado.aliado_id and aceptado.aliado_id != aliado.id:
        try:
            enviar_push_a_aliado(db, aceptado.aliado_id, "Respuesta aceptada ✓",
                                 "Tu respuesta fue marcada como la solución.", "/")
        except Exception:
            pass
    return {"mensaje": "Pregunta actualizada.", "resuelto": p.resuelto}


# ─── ENDPOINTS DE ADMIN ──────────────────────────────────────────────────────

@router.post("/admin/comunidad/{id}/estado")
def admin_estado_mejora(id: int, body: schemas.EstadoMejoraIn,
                        db: Session = Depends(get_db),
                        _admin=Depends(current_admin_required)):
    """Admin: mueve el estado de un pedido de mejora y avisa al autor."""
    estado = (body.estado or "").strip().lower()
    if estado not in ESTADOS_MEJORA:
        raise HTTPException(400, f"Estado inválido. Usá: {', '.join(ESTADOS_MEJORA)}.")
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p:
        raise HTTPException(404, "Post no encontrado.")
    p.estado_mejora = estado

    etiqueta = {"recibido": "recibida", "evaluacion": "en evaluación",
                "planificado": "planificada", "hecho": "lista ✅",
                "descartado": "descartada"}.get(estado, estado)
    if p.aliado_id:
        notificar_aliado(db, p.aliado_id, "comunidad",
                         f"Tu sugerencia está {etiqueta}",
                         f"«{(p.titulo or '')[:80]}» pasó a estado: {etiqueta}.",
                         tab="comunidad")
    db.commit()

    if p.aliado_id:
        try:
            enviar_push_a_aliado(db, p.aliado_id, "Tu sugerencia avanzó",
                                 f"«{(p.titulo or '')[:60]}» → {etiqueta}.", "/")
        except Exception:
            pass
    return {"mensaje": f"Estado actualizado a {estado}.", "estado_mejora": estado}


@router.post("/admin/comunidad/{id}/fijar")
def admin_fijar_post(id: int, fijar: bool = True,
                     db: Session = Depends(get_db),
                     _admin=Depends(current_admin_required)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p:
        raise HTTPException(404, "Post no encontrado.")
    p.fijado = fijar; db.commit()
    return {"mensaje": "Post fijado." if fijar else "Post desfijado."}


@router.post("/admin/comunidad/{id}/ocultar")
def admin_ocultar_post(id: int, ocultar: bool = True,
                       db: Session = Depends(get_db),
                       _admin=Depends(current_admin_required)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p:
        raise HTTPException(404, "Post no encontrado.")
    p.oculto = ocultar; db.commit()
    return {"mensaje": "Post ocultado." if ocultar else "Post visible de nuevo."}
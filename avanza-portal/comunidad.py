"""
comunidad.py — Endpoints de la Comunidad de aliados (feed, posts, likes).

Segundo router migrado de main.py siguiendo el patrón de academia.py:
  - APIRouter sin prefix (las rutas viven en /comunidad/* y /admin/comunidad/*).
  - Reusa las dependencies de FastAPI (get_db, current_aliado_required, etc.).
  - Sin helpers de main.py → cero riesgo de import circular.

Activado desde main.py con `app.include_router(comunidad.router)`.

NOTA: el asistente IA de la comunidad (/comunidad/asistente-ia) sigue en
main.py porque depende del stack de Jarvis — migrarlo cuando se extraiga
ese dominio completo.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import schemas
from auth import current_aliado_required, current_admin_required
from database import get_db
from models import Aliado, ComentarioComunidad, PostComunidad

router = APIRouter(tags=["comunidad"])


# ─── ENDPOINTS DE ALIADO ─────────────────────────────────────────────────────

@router.get("/comunidad/feed")
def ver_feed_comunidad(limit: int = 30, db: Session = Depends(get_db)):
    """Feed público para todos los aliados (los no ocultos)."""
    posts = db.query(PostComunidad).filter(
        PostComunidad.oculto == False
    ).order_by(
        PostComunidad.fijado.desc(),
        PostComunidad.creado_en.desc()
    ).limit(limit).all()

    resultado = []
    for p in posts:
        coms = db.query(ComentarioComunidad).filter(
            ComentarioComunidad.post_id == p.id
        ).order_by(ComentarioComunidad.creado_en.asc()).all()
        resultado.append({
            "id": p.id,
            "tipo": p.tipo,
            "titulo": p.titulo,
            "cuerpo": p.cuerpo,
            "likes": p.likes or 0,
            "fijado": p.fijado,
            "autor": p.aliado.nombre.split()[0] if p.aliado else "—",
            "autor_codigo": p.aliado.codigo if p.aliado else None,
            "autor_nivel": p.aliado.nivel_calculado if p.aliado else None,
            "fecha": p.creado_en.strftime("%d/%m/%Y %H:%M") if p.creado_en else None,
            "comentarios": [
                {"autor": c.aliado.nombre.split()[0] if c.aliado else "—",
                 "cuerpo": c.cuerpo,
                 "fecha": c.creado_en.strftime("%d/%m/%Y %H:%M") if c.creado_en else None}
                for c in coms
            ],
        })
    return {"posts": resultado}


@router.post("/comunidad/post")
def crear_post(post: schemas.PostComunidadIn,
               aliado: Aliado = Depends(current_aliado_required),
               db: Session = Depends(get_db)):
    """Publica un post en la comunidad como el aliado autenticado.

    SECURITY: el campo `codigo_aliado` del body se ignora — la autoría
    siempre se toma del JWT para evitar suplantación.
    """
    if post.tipo not in ("tip", "win", "pregunta"):
        raise HTTPException(400, "Tipo inválido.")
    if len(post.titulo.strip()) < 3 or len(post.cuerpo.strip()) < 5:
        raise HTTPException(400, "Título y cuerpo requeridos.")
    p = PostComunidad(
        aliado_id=aliado.id, tipo=post.tipo,
        titulo=post.titulo.strip()[:200], cuerpo=post.cuerpo.strip()[:3000],
    )
    db.add(p); db.commit(); db.refresh(p)
    return {"mensaje": "Post publicado.", "id": p.id}


@router.post("/comunidad/{id}/like")
def like_post(id: int,
              aliado: Aliado = Depends(current_aliado_required),
              db: Session = Depends(get_db)):
    """Like a un post. Requiere aliado autenticado (anti-spam)."""
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.likes = (p.likes or 0) + 1
    db.commit()
    return {"likes": p.likes}


@router.post("/comunidad/{id}/comentario")
def comentar(id: int, com: schemas.ComentarioComunidadIn,
             aliado: Aliado = Depends(current_aliado_required),
             db: Session = Depends(get_db)):
    """Comenta un post como el aliado autenticado.

    SECURITY: `codigo_aliado` del body se ignora — autoría va por JWT.
    """
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    if len(com.cuerpo.strip()) < 2:
        raise HTTPException(400, "Comentario vacío.")
    c = ComentarioComunidad(
        post_id=p.id, aliado_id=aliado.id, cuerpo=com.cuerpo.strip()[:1000]
    )
    db.add(c); db.commit()
    return {"mensaje": "Comentario publicado."}


# ─── ENDPOINTS DE ADMIN ──────────────────────────────────────────────────────
# Además del middleware global de /admin/*, declaramos la dependency explícita
# (mismo criterio que academia.py: defensa doble, y el endpoint se documenta
# solo en /docs como protegido).

@router.post("/admin/comunidad/{id}/fijar")
def admin_fijar_post(id: int, fijar: bool = True,
                     db: Session = Depends(get_db),
                     _admin=Depends(current_admin_required)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.fijado = fijar; db.commit()
    return {"mensaje": "Post fijado." if fijar else "Post desfijado."}


@router.post("/admin/comunidad/{id}/ocultar")
def admin_ocultar_post(id: int, ocultar: bool = True,
                       db: Session = Depends(get_db),
                       _admin=Depends(current_admin_required)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.oculto = ocultar; db.commit()
    return {"mensaje": "Post ocultado." if ocultar else "Post visible de nuevo."}
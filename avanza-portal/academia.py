"""
routes/academia.py — Endpoints de la Academia (router migrado de ejemplo).

Este router demuestra el patrón a seguir para los otros dominios:
  1. APIRouter local con prefix opcional (acá NO uso prefix porque las rutas
     viven en /academia, /admin/academia, y /aliados/{codigo}/academia —
     prefijos distintos. Cuando un router tiene UN solo prefijo, usalo).
  2. Reusa las dependencies de FastAPI (get_db, current_admin_required, etc.).
  3. Si un endpoint necesita un helper que vive en main.py (_get_aliado,
     _ajustar_creditos, _admin_log), importalo desde main al USO, NO en
     el top del modulo, para evitar import circular. Eso es exactamente
     lo que hace `_helpers()` abajo.

NOTA: Mientras NO se llame a `app.include_router(academia.router)` desde
main.py, los endpoints del router quedan inertes. Por eso es seguro tener
este archivo y simultaneamente conservar los endpoints viejos en main.py
durante la transicion.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from auth import current_admin_required, verify_ownership_dep
from models import (
    Aliado, AcademiaModulo, AliadoModuloCompletado,
)


router = APIRouter(tags=["academia"])


# ─── SCHEMAS LOCALES DEL ROUTER ──────────────────────────────────────────────
# Cuando hagas el split definitivo, mover a schemas.py si se reusan, o
# dejarlos locales si son solo de este router.
class AcademiaModuloCreate(BaseModel):
    orden: int
    titulo: str
    descripcion: str | None = ""
    tipo: str
    url_contenido: str
    duracion_minutos: int = 10
    activo: bool = True


class AcademiaModuloUpdate(BaseModel):
    orden: int | None = None
    titulo: str | None = None
    descripcion: str | None = None
    tipo: str | None = None
    url_contenido: str | None = None
    duracion_minutos: int | None = None
    activo: bool | None = None


# ─── HELPERS ─────────────────────────────────────────────────────────────────
def _modulo_row(m: AcademiaModulo, completado: bool = False):
    return {
        "id": m.id,
        "orden": m.orden,
        "titulo": m.titulo,
        "descripcion": m.descripcion,
        "tipo": m.tipo,
        "url": m.url_contenido,
        "url_contenido": m.url_contenido,
        "duracion_minutos": m.duracion_minutos,
        "activo": m.activo,
        "completado": completado,
    }


def _helpers():
    """Import diferido para evitar el ciclo `routes -> main -> routes`."""
    from main import _get_aliado, _ajustar_creditos, BONUS_MODULO_COMPLETADO
    return _get_aliado, _ajustar_creditos, BONUS_MODULO_COMPLETADO


# ─── ENDPOINTS PUBLICOS ──────────────────────────────────────────────────────
@router.get("/academia/modulos")
def listar_modulos_academia(db: Session = Depends(get_db)):
    """Lista publica de modulos de la academia (solo activos)."""
    mods = db.query(AcademiaModulo).filter(AcademiaModulo.activo == True)\
        .order_by(AcademiaModulo.orden).all()
    return [_modulo_row(m) for m in mods]


# ─── ENDPOINTS DE ALIADO (autenticado) ───────────────────────────────────────
@router.get("/aliados/{codigo}/academia")
def academia_del_aliado(codigo: str,
                         db: Session = Depends(get_db),
                         _owner=Depends(verify_ownership_dep)):
    """Devuelve los modulos de la Academia para el aliado, en orden, con flag
    de completitud y resumen de progreso."""
    _get_aliado, _aj, BONUS = _helpers()
    a = _get_aliado(codigo, db)
    mods = db.query(AcademiaModulo).filter(AcademiaModulo.activo == True)\
        .order_by(AcademiaModulo.orden).all()
    completados_ids = {
        c.modulo_id for c in db.query(AliadoModuloCompletado).filter(
            AliadoModuloCompletado.aliado_id == a.id
        ).all()
    }
    completados = sum(1 for m in mods if m.id in completados_ids)
    return {
        "aliado": a.codigo,
        "total_modulos": len(mods),
        "modulos_completados": completados,
        "porcentaje": round(100 * completados / len(mods)) if mods else 0,
        "creditos_por_modulo": BONUS,
        "modulos": [
            _modulo_row(m, completado=(m.id in completados_ids))
            for m in mods
        ],
    }


@router.post("/aliados/{codigo}/academia/{modulo_id}/completar")
def completar_modulo_academia(codigo: str, modulo_id: int,
                               db: Session = Depends(get_db),
                               _owner=Depends(verify_ownership_dep)):
    """Marca un modulo de la Academia como completado por el aliado y otorga
    el bonus de creditos correspondiente. Idempotente: si ya estaba completado,
    no duplica creditos.
    """
    _get_aliado, _aj, BONUS = _helpers()
    a = _get_aliado(codigo, db)
    mod = db.query(AcademiaModulo).filter(
        AcademiaModulo.id == modulo_id,
        AcademiaModulo.activo == True,
    ).first()
    if not mod:
        raise HTTPException(404, "Modulo no encontrado o inactivo.")

    existente = db.query(AliadoModuloCompletado).filter(
        AliadoModuloCompletado.aliado_id == a.id,
        AliadoModuloCompletado.modulo_id == mod.id,
    ).first()
    if existente:
        return {
            "mensaje":          "Este modulo ya estaba completado.",
            "ya_completado":    True,
            "creditos_ganados": 0,
            "saldo":            a.creditos or 0,
            "modulo": {"id": mod.id, "titulo": mod.titulo},
        }

    completado = AliadoModuloCompletado(
        aliado_id          = a.id,
        modulo_id          = mod.id,
        creditos_otorgados = BONUS,
    )
    db.add(completado)
    _aj(db, a, BONUS, "modulo_completado", f"modulo:{mod.id}")
    db.commit()

    return {
        "mensaje":          f"Completaste '{mod.titulo}'! Te sumamos {BONUS} creditos.",
        "ya_completado":    False,
        "creditos_ganados": BONUS,
        "saldo":            a.creditos or 0,
        "modulo": {"id": mod.id, "titulo": mod.titulo},
    }


# ─── ENDPOINTS DE ADMIN ──────────────────────────────────────────────────────
@router.get("/admin/academia")
def admin_listar_modulos(db: Session = Depends(get_db),
                          _admin=Depends(current_admin_required)):
    """Version admin: devuelve TODOS los modulos (activos e inactivos)."""
    mods = db.query(AcademiaModulo).order_by(AcademiaModulo.orden).all()
    return [_modulo_row(m) for m in mods]


@router.post("/admin/academia")
def admin_crear_modulo(payload: AcademiaModuloCreate,
                        db: Session = Depends(get_db),
                        _admin=Depends(current_admin_required)):
    if payload.tipo not in ("video", "pdf", "texto"):
        raise HTTPException(400, "tipo debe ser 'video', 'pdf' o 'texto'.")
    m = AcademiaModulo(
        orden       = payload.orden,
        titulo      = payload.titulo,
        descripcion = payload.descripcion,
        tipo        = payload.tipo,
        url_contenido = payload.url_contenido,
        duracion_minutos = payload.duracion_minutos,
        activo      = payload.activo,
    )
    db.add(m); db.commit(); db.refresh(m)
    return _modulo_row(m)


@router.patch("/admin/academia/{id}")
def admin_editar_modulo(id: int, payload: AcademiaModuloUpdate,
                         db: Session = Depends(get_db),
                         _admin=Depends(current_admin_required)):
    m = db.query(AcademiaModulo).filter(AcademiaModulo.id == id).first()
    if not m:
        raise HTTPException(404, "Modulo no encontrado.")
    for campo in ("orden", "titulo", "descripcion", "tipo",
                  "url_contenido", "duracion_minutos", "activo"):
        val = getattr(payload, campo, None)
        if val is not None:
            setattr(m, campo, val)
    db.commit()
    return _modulo_row(m)


@router.delete("/admin/academia/{id}")
def admin_eliminar_modulo(id: int,
                           db: Session = Depends(get_db),
                           _admin=Depends(current_admin_required)):
    m = db.query(AcademiaModulo).filter(AcademiaModulo.id == id).first()
    if not m:
        raise HTTPException(404, "Modulo no encontrado.")
    db.delete(m); db.commit()
    return {"mensaje": "Modulo eliminado."}
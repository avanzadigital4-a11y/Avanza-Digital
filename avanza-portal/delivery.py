"""
delivery.py  ·  Visibilidad de implementación para Canal 2
================================================================================

QUE RESUELVE
------------
El riesgo de Canal 2 es asimétrico contra Canal 1. El aliado de Canal 1 arriesga
un lead; el contador/consultor de Canal 2 arriesga una relación que construyó en
AÑOS. Una sola implementación floja le quema un cliente de cartera. Por eso
necesita más garantía que el closer: ver en qué estado va la implementación de
SU cliente y que le avisen si algo se traba.

Este módulo le da ese tablero:
  - Estado de implementación por referido (sin_iniciar → onboarding →
    en_desarrollo → en_revision → entregado, + 'pausado').
  - Cada cambio deja rastro (timeline) y NOTIFICA al aliado que refirió.
  - Job de estancamiento: si un referido lleva demasiado sin moverse, avisa.

INTEGRACIÓN (ver INTEGRACION.md):
  - iniciar_implementacion(db, referido) cuando el referido convierte (Venta).
  - set_estado_implementacion(...) desde ops/admin a medida que avanza el delivery.
  - app.include_router(delivery.router) + scheduler procesar_estancados.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from auth import verify_ownership_dep, current_admin_required
from models import Referido, Aliado
from notificaciones import notificar_aliado

router = APIRouter(tags=["delivery"])

# ─── ESTADOS ─────────────────────────────────────────────────────────────────
# Orden del flujo principal. 'pausado' es lateral (no entra en el orden).
ESTADOS_IMPL = ["sin_iniciar", "onboarding", "en_desarrollo", "en_revision", "entregado"]
ESTADOS_VALIDOS = set(ESTADOS_IMPL) | {"pausado"}
ESTADO_LABEL = {
    "sin_iniciar":   "Sin iniciar",
    "onboarding":    "Onboarding (esperando datos del cliente)",
    "en_desarrollo": "En desarrollo",
    "en_revision":   "En revisión con el cliente",
    "entregado":     "Entregado",
    "pausado":       "Pausado",
}
ESTADOS_TERMINALES = {"entregado"}
# Días sin movimiento antes de gritar "estancado" (por estado).
DIAS_ESTANCADO = {
    "onboarding":    5,    # el cliente no manda los datos
    "en_desarrollo": 14,
    "en_revision":   7,
    "pausado":       10,
}


def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def _cargar(hist_json) -> list:
    try:
        v = json.loads(hist_json or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _label(estado):
    return ESTADO_LABEL.get(estado, estado or "—")


def set_estado_implementacion(db: Session, referido: Referido, nuevo: str,
                              nota: str = "", por: str = "ops",
                              eta: str = None, commit: bool = True) -> dict:
    """Cambia el estado de implementación de un referido, deja rastro en el
    timeline y AVISA al aliado que lo refirió. `por` = quién lo movió
    ('ops'|'admin'|'sistema'). Devuelve dict con el estado nuevo."""
    if nuevo not in ESTADOS_VALIDOS:
        raise HTTPException(400, f"Estado inválido: {nuevo}")

    anterior = referido.estado_implementacion or "sin_iniciar"
    ahora = datetime.now()

    hist = _cargar(referido.impl_historial)
    hist.append({
        "de": anterior, "a": nuevo, "por": por,
        "fecha": ahora.isoformat(timespec="minutes"),
        "nota": (nota or "")[:280] or None,
    })
    referido.impl_historial = json.dumps(hist[-40:], ensure_ascii=False)
    referido.estado_implementacion = nuevo
    referido.impl_actualizado_en = ahora
    if eta is not None:
        referido.impl_eta = (eta or "")[:60] or None
    # Reset del anti-spam de estancamiento al haber movimiento real.
    referido.impl_alerta_estancado_en = None

    # Avisar al aliado dueño del referido.
    if nuevo != anterior:
        if nuevo == "entregado":
            titulo = f"✅ Entregado: {referido.nombre_cliente}"
            cuerpo = "La implementación de tu cliente quedó entregada. Buen trabajo de cartera."
        elif nuevo == "pausado":
            titulo = f"⏸️ Pausado: {referido.nombre_cliente}"
            cuerpo = (nota or "La implementación quedó pausada. Te avisamos al retomar.")
        else:
            titulo = f"Tu cliente avanza: {referido.nombre_cliente}"
            cuerpo = f"Implementación ahora en: {_label(nuevo)}." + (f" {nota}" if nota else "")
        notificar_aliado(db, referido.aliado_id, "delivery", titulo, cuerpo, tab="entregas")

    if commit:
        db.commit()
    return {"referido_id": referido.id, "estado": nuevo, "label": _label(nuevo)}


def iniciar_implementacion(db: Session, referido: Referido, commit: bool = False) -> dict:
    """Arranca el delivery de un referido recién convertido: pasa a 'onboarding'.
    Idempotente (no retrocede si ya estaba más avanzado). NO commitea por
    default — pensado para colgarse del flujo de conversión existente."""
    actual = referido.estado_implementacion or "sin_iniciar"
    if actual != "sin_iniciar":
        return {"ok": False, "motivo": "ya_iniciado", "estado": actual}
    return {"ok": True, **set_estado_implementacion(
        db, referido, "onboarding", nota="Referido convertido — arranca onboarding.",
        por="sistema", commit=commit)}


def procesar_estancados(db: Session) -> dict:
    """Job: detecta referidos en un estado no terminal que llevan demasiado sin
    moverse y avisa una vez (al aliado y dejando sello anti-spam). Commitea."""
    ahora = datetime.now()
    candidatos = (db.query(Referido)
                  .filter(Referido.estado_implementacion.in_(list(DIAS_ESTANCADO.keys())))
                  .all())
    avisados = 0
    for r in candidatos:
        dias = DIAS_ESTANCADO.get(r.estado_implementacion)
        ref_fecha = r.impl_actualizado_en or r.registrado_en
        if not ref_fecha or (ahora - ref_fecha) < timedelta(days=dias):
            continue
        # Anti-spam: no re-avisar dentro de la misma ventana.
        if r.impl_alerta_estancado_en and (ahora - r.impl_alerta_estancado_en) < timedelta(days=dias):
            continue
        notificar_aliado(
            db, r.aliado_id, "delivery",
            f"⚠️ {r.nombre_cliente} lleva {dias}+ días sin moverse",
            f"La implementación está en '{_label(r.estado_implementacion)}' y no avanzó. "
            "Lo estamos mirando; si querés, escribinos.",
            tab="entregas",
        )
        r.impl_alerta_estancado_en = ahora
        avisados += 1
    if avisados:
        db.commit()
    return {"avisados": avisados}


# ─── ENDPOINTS: ALIADO ───────────────────────────────────────────────────────

@router.get("/aliados/{codigo}/entregas")
def mis_entregas(codigo: str, db: Session = Depends(get_db),
                 _owner=Depends(verify_ownership_dep)):
    """El aliado de Canal 2 ve la implementación de SUS clientes referidos:
    estado actual, ETA y timeline. Esto es lo que lo anima a poner su nombre."""
    a = _get_aliado(codigo, db)
    refs = (db.query(Referido)
            .filter(Referido.aliado_id == a.id, Referido.convertido == True)  # noqa: E712
            .order_by(Referido.registrado_en.desc()).all())

    items = []
    for r in refs:
        estado = r.estado_implementacion or "sin_iniciar"
        items.append({
            "referido_id": r.id,
            "cliente": r.nombre_cliente,
            "plan": r.plan_elegido,
            "estado": estado,
            "estado_label": _label(estado),
            "terminado": estado in ESTADOS_TERMINALES,
            "eta": r.impl_eta,
            "actualizado_en": r.impl_actualizado_en.strftime("%d/%m/%Y") if r.impl_actualizado_en else None,
            "timeline": _cargar(r.impl_historial),
        })

    en_curso = sum(1 for i in items if not i["terminado"])
    return {"total": len(items), "en_curso": en_curso,
            "entregados": len(items) - en_curso, "entregas": items,
            "flujo": [{"clave": e, "label": _label(e)} for e in ESTADOS_IMPL]}


# ─── ENDPOINTS: OPS / ADMIN ──────────────────────────────────────────────────

@router.get("/admin/entregas")
def admin_entregas(estado: str = "", db: Session = Depends(get_db),
                   _admin=Depends(current_admin_required)):
    """Tablero ops de todas las implementaciones en vuelo (filtro por estado)."""
    q = db.query(Referido).filter(Referido.convertido == True)  # noqa: E712
    if estado and estado in ESTADOS_VALIDOS:
        q = q.filter(Referido.estado_implementacion == estado)
    else:
        q = q.filter(Referido.estado_implementacion != "entregado")
    refs = q.order_by(Referido.impl_actualizado_en.asc().nullsfirst()).all()

    out = []
    for r in refs:
        aliado = db.query(Aliado).filter(Aliado.id == r.aliado_id).first()
        out.append({
            "referido_id": r.id, "cliente": r.nombre_cliente, "plan": r.plan_elegido,
            "estado": r.estado_implementacion or "sin_iniciar",
            "aliado": {"codigo": aliado.codigo, "nombre": aliado.nombre} if aliado else None,
            "actualizado_en": r.impl_actualizado_en.strftime("%d/%m/%Y") if r.impl_actualizado_en else None,
            "eta": r.impl_eta,
        })
    return {"total": len(out), "entregas": out,
            "estados": [{"clave": e, "label": _label(e)} for e in ESTADOS_IMPL] +
                       [{"clave": "pausado", "label": _label("pausado")}]}


@router.post("/admin/entregas/{referido_id}/estado")
def admin_mover_estado(referido_id: int, payload: dict = Body(...),
                       db: Session = Depends(get_db),
                       _admin=Depends(current_admin_required)):
    """Ops avanza el estado de una implementación. Notifica al aliado solo."""
    r = db.query(Referido).filter(Referido.id == referido_id).first()
    if not r:
        raise HTTPException(404, "Referido no encontrado.")
    nuevo = (payload.get("estado") or "").strip()
    res = set_estado_implementacion(
        db, r, nuevo, nota=payload.get("nota", ""), por="ops",
        eta=payload.get("eta"), commit=True)
    return {"status": "ok", **res}
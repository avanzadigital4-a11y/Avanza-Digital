"""
reparto_visibilidad.py  ·  Transparencia del split setter→closer (Canal 1)
================================================================================

OJO: el split setter→closer YA está implementado y es automático — corre en
checkout.py (cierre puntual) y en comisiones.py (recurrente). Este módulo NO
reconstruye eso. Agrega lo único que faltaba: VISIBILIDAD.

El riesgo del modelo setter→closer es que el setter no vea su parte hasta
después y desconfíe. Acá puede ver, ANTES de pasar el lead, cuánto le toca a
cada uno en cada plan. Esa transparencia es lo que hace que los buenos setters
se queden en vez de irse.

INTEGRACIÓN: app.include_router(reparto.router) en main.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from database import get_db
from auth import verify_ownership_dep
from models import Aliado, LeadBolsa, Equipo, PLANES, NIVELES

router = APIRouter(tags=["reparto"])

SETTER_SPLIT_DEFAULT = 0.40


def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def _comision_pct(aliado: Aliado) -> float:
    """% de comisión del aliado por su nivel (misma fuente que el resto del app)."""
    nivel = (aliado.nivel or "BASIC").upper()
    return NIVELES.get(nivel, NIVELES["BASIC"])["comision"]


def _split_de_equipo(db: Session, setter: Aliado, closer: Aliado):
    """Devuelve el setter_split_pct del equipo activo entre ambos, o el default."""
    eq = (db.query(Equipo)
          .filter(Equipo.estado == "activo",
                  or_(and_(Equipo.aliado_a_id == setter.id, Equipo.aliado_b_id == closer.id),
                      and_(Equipo.aliado_a_id == closer.id, Equipo.aliado_b_id == setter.id)))
          .first())
    if eq and eq.setter_split_pct is not None:
        return eq.setter_split_pct
    return SETTER_SPLIT_DEFAULT


def _tabla_reparto(split: float, comision_pct: float) -> list:
    """Para cada plan: comisión total del cierre y cómo se reparte."""
    filas = []
    for plan, precio in PLANES.items():
        comision_total = round(precio * comision_pct, 2)
        parte_setter = round(comision_total * split, 2)
        parte_closer = round(comision_total - parte_setter, 2)
        filas.append({
            "plan": plan,
            "precio_usd": precio,
            "comision_total_usd": comision_total,
            "setter_usd": parte_setter,
            "closer_usd": parte_closer,
        })
    return filas


@router.get("/aliados/{codigo}/reparto/proyeccion")
def proyeccion_generica(codigo: str, companero: str = "", db: Session = Depends(get_db),
                        _owner=Depends(verify_ownership_dep)):
    """Proyección de reparto contra un compañero (por su código). Muestra, plan
    por plan, cuánto cobraría cada uno si cierra el closer. Útil ANTES de
    cualquier handoff: las cartas sobre la mesa."""
    a = _get_aliado(codigo, db)
    if not companero:
        raise HTTPException(400, "Pasá el código del compañero (?companero=AL-0XX).")
    b = db.query(Aliado).filter(Aliado.codigo == companero.strip()).first()
    if not b:
        raise HTTPException(404, "No encontré ese aliado.")

    split = _split_de_equipo(db, a, b)
    # Proyección en ambos sentidos: el % de comisión depende de quién cierra.
    return {
        "split": {"setter": round(split * 100), "closer": round((1 - split) * 100)},
        "si_cierra_companero": {
            "closer": b.codigo, "closer_nivel": b.nivel,
            "tabla": _tabla_reparto(split, _comision_pct(b)),
        },
        "si_cerras_vos": {
            "closer": a.codigo, "closer_nivel": a.nivel,
            "tabla": _tabla_reparto(split, _comision_pct(a)),
        },
    }


@router.get("/aliados/{codigo}/reparto/lead/{lead_id}")
def proyeccion_lead(codigo: str, lead_id: int, db: Session = Depends(get_db),
                    _owner=Depends(verify_ownership_dep)):
    """Proyección de reparto para un lead concreto ya pasado a un closer (con
    atribución de setter estampada). Lo ven los dos: cuánto le toca a cada uno
    según el plan que cierre."""
    a = _get_aliado(codigo, db)
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")
    if a.id not in (lead.aliado_id, lead.setter_id):
        raise HTTPException(403, "Ese lead no es tuyo (ni como setter ni como closer).")

    split = lead.setter_split_pct if lead.setter_split_pct is not None else SETTER_SPLIT_DEFAULT
    closer = db.query(Aliado).filter(Aliado.id == lead.aliado_id).first()
    setter = db.query(Aliado).filter(Aliado.id == lead.setter_id).first() if lead.setter_id else None

    return {
        "lead": {"id": lead.id, "empresa": lead.empresa},
        "split": {"setter": round(split * 100), "closer": round((1 - split) * 100)},
        "setter": {"codigo": setter.codigo, "nombre": setter.nombre} if setter else None,
        "closer": {"codigo": closer.codigo, "nombre": closer.nombre} if closer else None,
        "tabla": _tabla_reparto(split, _comision_pct(closer) if closer else _comision_pct(a)),
        "nota": "El total que paga Avanza no cambia: se reparte, no se suma.",
    }
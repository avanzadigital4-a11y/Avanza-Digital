"""
equipos.py  Feature "Mi Equipo": setter + closer
================================================================================

QUE RESUELVE
------------
Hoy un aliado que prospecta/califica pero NO sabe cerrar (SDR, setter) no tiene
forma de monetizar: se registra, no factura y termina como "inactivo". Y un
closer que cierra bien no tiene quien le alimente reuniones calificadas para
escalar. "Mi Equipo" los junta: cualquiera puede armar equipo con cualquiera, y
en cada deal el que pasa el lead actua de SETTER y el que cierra de CLOSER.

BLOQUE 1 (este archivo): SOLO la formacion del equipo. No toca comisiones.
  - Solicitar / aceptar / rechazar equipo.
  - Ajustar el split (dentro de banda) y disolver.
El handoff del lead y el reparto de la comision viven en el Bloque 2 (aparte),
porque tocan el flujo de plata y se construyen con mas cuidado.

DISENO CLAVE
------------
- El vinculo es SIMETRICO: una fila por par. El rol (setter/closer) NO se fija
  en el equipo, se define por deal segun quien hace el handoff. Por eso guardamos
  un unico `setter_split_pct`: la fraccion de la comision del deal que se lleva
  el que actuo de setter. El closer se lleva el resto.
- El total que paga Avanza NO cambia: la comision se REPARTE, no se suma.
- Un aliado puede tener varios equipos (un closer con varios setters, etc.).
  Lo unico que no se permite es duplicar un vinculo activo/pendiente con el
  mismo companero.

Costo: $0. Reusa Aliado, notificaciones y la tabla nueva `equipos`.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from database import get_db
from auth import verify_ownership_dep
from models import Aliado, Equipo
from notificaciones import notificar_aliado

router = APIRouter(tags=["equipos"])

# Banda del split que se lleva el SETTER en cada deal de equipo.
# Default 0.40 (40%); ajustable entre 0.25 y 0.50. Cerrar es la parte mas
# dificil, por eso el closer se queda con mas; pero si el setter labura mucho
# puede subir hasta 50%. Topado en 50% para que el closer (recurso escaso)
# no deje de querer compartir.
SETTER_SPLIT_DEFAULT = 0.40
SETTER_SPLIT_MIN = 0.25
SETTER_SPLIT_MAX = 0.50


def _get_aliado(codigo, db):
    """Puente diferido al helper de main (evita ciclo de import al cargar)."""
    from main import _get_aliado as f
    return f(codigo, db)


def _clamp_split(valor) -> float:
    """Acota el split a la banda permitida. Si viene basura, usa el default."""
    try:
        v = round(float(valor), 2)
    except (TypeError, ValueError):
        return SETTER_SPLIT_DEFAULT
    return max(SETTER_SPLIT_MIN, min(SETTER_SPLIT_MAX, v))


def _otro_id(eq: Equipo, yo_id: int) -> int:
    """Devuelve el id del companero (el miembro que no soy yo)."""
    return eq.aliado_b_id if eq.aliado_a_id == yo_id else eq.aliado_a_id


def _es_miembro(eq: Equipo, yo_id: int) -> bool:
    return yo_id in (eq.aliado_a_id, eq.aliado_b_id)


def _aliado_min(db: Session, aliado_id: int) -> dict:
    a = db.query(Aliado).filter(Aliado.id == aliado_id).first()
    if not a:
        return {"codigo": None, "nombre": "(desconocido)"}
    return {"codigo": a.codigo, "nombre": a.nombre}


def _split_pct_int(eq: Equipo) -> dict:
    """Representacion legible del split: setter % / closer %."""
    s = eq.setter_split_pct if eq.setter_split_pct is not None else SETTER_SPLIT_DEFAULT
    return {"setter": round(s * 100), "closer": round((1 - s) * 100)}


#  GET: PANEL "MI EQUIPO" 

@router.get("/aliados/{codigo}/equipo")
def mi_equipo(codigo: str, db: Session = Depends(get_db),
              _owner=Depends(verify_ownership_dep)):
    """Devuelve los equipos activos del aliado, las solicitudes que recibio
    (para aceptar/rechazar) y las que envio (pendientes), mas la banda del
    split para que el front muestre las reglas."""
    a = _get_aliado(codigo, db)

    # Todos los vinculos donde participo, en cualquier rol.
    vinculos = (db.query(Equipo)
                .filter(or_(Equipo.aliado_a_id == a.id, Equipo.aliado_b_id == a.id))
                .order_by(Equipo.creado_en.desc())
                .all())

    activos, recibidas, enviadas = [], [], []
    for eq in vinculos:
        companero = _aliado_min(db, _otro_id(eq, a.id))
        base = {
            "equipo_id": eq.id,
            "companero": companero,
            "split": _split_pct_int(eq),
            "setter_split_pct": eq.setter_split_pct,
        }
        if eq.estado == "activo":
            base["desde"] = eq.confirmado_en.strftime("%d/%m/%Y") if eq.confirmado_en else None
            activos.append(base)
        elif eq.estado == "pendiente":
            # Si la solicitud la inicio el otro (yo soy aliado_b), la puedo aceptar.
            if eq.aliado_b_id == a.id:
                recibidas.append(base)
            else:
                enviadas.append(base)

    return {
        "activos": activos,
        "solicitudes_recibidas": recibidas,
        "solicitudes_enviadas": enviadas,
        "banda_split": {
            "min": SETTER_SPLIT_MIN,
            "max": SETTER_SPLIT_MAX,
            "default": SETTER_SPLIT_DEFAULT,
        },
    }


#  POST: SOLICITAR EQUIPO 

@router.post("/aliados/{codigo}/equipo/solicitar")
def solicitar_equipo(codigo: str, payload: dict = Body(...),
                     db: Session = Depends(get_db),
                     _owner=Depends(verify_ownership_dep)):
    """Manda una solicitud de equipo a otro aliado (por su codigo). Queda
    'pendiente' hasta que el otro la acepte."""
    a = _get_aliado(codigo, db)

    companero_codigo = (payload.get("companero") or payload.get("codigo") or "").strip()
    if not companero_codigo:
        raise HTTPException(400, "Falta el codigo del companero.")

    b = db.query(Aliado).filter(Aliado.codigo == companero_codigo,
                                Aliado.activo == True).first()
    if not b:
        raise HTTPException(404, "No encontre un aliado activo con ese codigo.")
    if b.id == a.id:
        raise HTTPException(400, "No podes armar equipo con vos mismo.")

    # No duplicar un vinculo activo o pendiente con el mismo companero (en
    # cualquier direccion).
    ya = (db.query(Equipo)
          .filter(
              Equipo.estado.in_(["activo", "pendiente"]),
              or_(
                  and_(Equipo.aliado_a_id == a.id, Equipo.aliado_b_id == b.id),
                  and_(Equipo.aliado_a_id == b.id, Equipo.aliado_b_id == a.id),
              ),
          ).first())
    if ya:
        msg = ("Ya tenes un equipo activo con esa persona."
               if ya.estado == "activo"
               else "Ya hay una solicitud pendiente con esa persona.")
        raise HTTPException(409, msg)

    split = _clamp_split(payload.get("setter_split_pct", SETTER_SPLIT_DEFAULT))

    eq = Equipo(
        aliado_a_id=a.id,
        aliado_b_id=b.id,
        estado="pendiente",
        setter_split_pct=split,
        creado_en=datetime.now(),
    )
    db.add(eq)
    db.flush()  # para tener eq.id

    notificar_aliado(
        db, b.id, "equipo",
        f"{a.nombre} te invito a hacer equipo",
        f"{a.nombre} quiere trabajar deals con vos. Split propuesto: setter "
        f"{_split_pct_int(eq)['setter']}% / closer {_split_pct_int(eq)['closer']}%. "
        f"Entra a Mi Equipo para aceptar.",
        tab="equipo",
    )
    db.commit()
    return {"status": "ok", "equipo_id": eq.id, "estado": "pendiente",
            "mensaje": f"Solicitud enviada a {b.nombre}."}


#  POST: ACEPTAR / RECHAZAR 

@router.post("/aliados/{codigo}/equipo/{equipo_id}/aceptar")
def aceptar_equipo(codigo: str, equipo_id: int,
                   db: Session = Depends(get_db),
                   _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    eq = db.query(Equipo).filter(Equipo.id == equipo_id).first()
    if not eq:
        raise HTTPException(404, "Solicitud no encontrada.")
    # Solo el receptor (aliado_b) puede aceptar, y solo si esta pendiente.
    if eq.aliado_b_id != a.id:
        raise HTTPException(403, "Solo quien recibio la solicitud puede aceptarla.")
    if eq.estado != "pendiente":
        raise HTTPException(409, "Esa solicitud ya no esta pendiente.")

    eq.estado = "activo"
    eq.confirmado_en = datetime.now()

    notificar_aliado(
        db, eq.aliado_a_id, "equipo",
        f"{a.nombre} acepto tu equipo",
        f"Ya pueden trabajar deals juntos. Cuando pases un lead a {a.nombre} "
        f"(o al reves), la comision se reparte segun el split acordado.",
        tab="equipo",
    )
    db.commit()
    return {"status": "ok", "estado": "activo",
            "mensaje": "Equipo activado."}


@router.post("/aliados/{codigo}/equipo/{equipo_id}/rechazar")
def rechazar_equipo(codigo: str, equipo_id: int,
                    db: Session = Depends(get_db),
                    _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    eq = db.query(Equipo).filter(Equipo.id == equipo_id).first()
    if not eq:
        raise HTTPException(404, "Solicitud no encontrada.")
    if eq.aliado_b_id != a.id:
        raise HTTPException(403, "Solo quien recibio la solicitud puede rechazarla.")
    if eq.estado != "pendiente":
        raise HTTPException(409, "Esa solicitud ya no esta pendiente.")

    eq.estado = "rechazado"
    db.commit()
    return {"status": "ok", "estado": "rechazado",
            "mensaje": "Solicitud rechazada."}


#  POST: AJUSTAR SPLIT 

@router.post("/aliados/{codigo}/equipo/{equipo_id}/split")
def ajustar_split(codigo: str, equipo_id: int, payload: dict = Body(...),
                  db: Session = Depends(get_db),
                  _owner=Depends(verify_ownership_dep)):
    """Cambia el split del equipo (dentro de la banda permitida). Cualquiera de
    los dos miembros puede ajustarlo; se notifica al otro para que este al
    tanto. Aplica a los deals FUTUROS; los ya cerrados mantienen su reparto."""
    a = _get_aliado(codigo, db)
    eq = db.query(Equipo).filter(Equipo.id == equipo_id).first()
    if not eq:
        raise HTTPException(404, "Equipo no encontrado.")
    if not _es_miembro(eq, a.id):
        raise HTTPException(403, "No sos miembro de ese equipo.")
    if eq.estado != "activo":
        raise HTTPException(409, "Solo se puede ajustar el split de un equipo activo.")

    nuevo = _clamp_split(payload.get("setter_split_pct"))
    eq.setter_split_pct = nuevo

    notificar_aliado(
        db, _otro_id(eq, a.id), "equipo",
        f"{a.nombre} ajusto el split del equipo",
        f"Nuevo reparto: setter {_split_pct_int(eq)['setter']}% / "
        f"closer {_split_pct_int(eq)['closer']}%. Aplica a los proximos deals.",
        tab="equipo",
    )
    db.commit()
    return {"status": "ok", "split": _split_pct_int(eq),
            "setter_split_pct": eq.setter_split_pct,
            "mensaje": "Split actualizado."}


#  POST: DISOLVER 

@router.post("/aliados/{codigo}/equipo/{equipo_id}/disolver")
def disolver_equipo(codigo: str, equipo_id: int,
                    db: Session = Depends(get_db),
                    _owner=Depends(verify_ownership_dep)):
    """Disuelve el equipo. Cualquiera de los dos puede. Los deals ya cerrados
    mantienen su reparto; los futuros dejan de repartirse."""
    a = _get_aliado(codigo, db)
    eq = db.query(Equipo).filter(Equipo.id == equipo_id).first()
    if not eq:
        raise HTTPException(404, "Equipo no encontrado.")
    if not _es_miembro(eq, a.id):
        raise HTTPException(403, "No sos miembro de ese equipo.")
    if eq.estado != "activo":
        raise HTTPException(409, "Ese equipo no esta activo.")

    eq.estado = "disuelto"
    eq.disuelto_en = datetime.now()

    notificar_aliado(
        db, _otro_id(eq, a.id), "equipo",
        f"{a.nombre} disolvio el equipo",
        "Dejaron de trabajar como equipo. Las comisiones ya cobradas no se tocan.",
        tab="equipo",
    )
    db.commit()
    return {"status": "ok", "estado": "disuelto",
            "mensaje": "Equipo disuelto."}


#  POST: HANDOFF DE LEAD (setter -> closer)  Bloque 2 

@router.post("/aliados/{codigo}/equipo/handoff")
def handoff_lead(codigo: str, payload: dict = Body(...),
                 db: Session = Depends(get_db),
                 _owner=Depends(verify_ownership_dep)):
    """El SETTER pasa un lead que reclamo a un COMPANERO de equipo (el closer)
    para que lo cierre. Reasigna el lead al closer y estampa la atribucion
    (setter + split del equipo) en el lead. Cuando el closer cierre y de de alta
    el plan pasando este lead_id, la comision se reparte automaticamente.

    payload: { lead_id: int, companero: "AL-0XX" }
    """
    from models import LeadBolsa
    setter = _get_aliado(codigo, db)

    lead_id = payload.get("lead_id")
    companero_codigo = (payload.get("companero") or payload.get("codigo") or "").strip()
    if not lead_id or not companero_codigo:
        raise HTTPException(400, "Falta lead_id o el codigo del companero.")

    closer = db.query(Aliado).filter(Aliado.codigo == companero_codigo,
                                     Aliado.activo == True).first()
    if not closer:
        raise HTTPException(404, "No encontre un aliado activo con ese codigo.")
    if closer.id == setter.id:
        raise HTTPException(400, "No podes pasarte un lead a vos mismo.")

    # Tiene que haber un equipo ACTIVO entre ambos.
    eq = (db.query(Equipo)
          .filter(Equipo.estado == "activo",
                  or_(and_(Equipo.aliado_a_id == setter.id, Equipo.aliado_b_id == closer.id),
                      and_(Equipo.aliado_a_id == closer.id, Equipo.aliado_b_id == setter.id)))
          .first())
    if not eq:
        raise HTTPException(409, "No tenes un equipo activo con esa persona.")

    # El lead debe estar reclamado por el setter.
    try:
        lead = db.query(LeadBolsa).filter(LeadBolsa.id == int(lead_id)).first()
    except (TypeError, ValueError):
        raise HTTPException(400, "lead_id invalido.")
    if not lead or lead.aliado_id != setter.id:
        raise HTTPException(404, "Ese lead no esta reclamado por vos.")
    if (lead.estado or "") != "reclamado":
        raise HTTPException(409, "Solo podes pasar un lead que tengas reclamado.")

    split = eq.setter_split_pct if eq.setter_split_pct is not None else SETTER_SPLIT_DEFAULT

    # Reasignar al closer + estampar la atribucion del setter.
    lead.aliado_id = closer.id
    lead.setter_id = setter.id
    lead.setter_split_pct = split
    lead.fecha_reclamo = datetime.now()

    notificar_aliado(
        db, closer.id, "equipo",
        f"{setter.nombre} te paso un lead",
        f"{setter.nombre} te paso \"{lead.empresa}\" para que lo cierres. "
        f"Si cierra, el reparto es setter {round(split * 100)}% / closer {round((1 - split) * 100)}%.",
        tab="capturas",
    )
    db.commit()
    return {"status": "ok", "mensaje": f"Lead pasado a {closer.nombre}.",
            "lead_id": lead.id, "split": _split_pct_int(eq)}
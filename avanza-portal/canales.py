"""
canales.py  ·  Puente entre Canal 1 y Canal 2 (una identidad, dos modos)
================================================================================

QUE RESUELVE
------------
Hoy los dos canales se leen como dos puertas separadas y un aliado es de uno o
del otro (tipo_aliado). Pero un closer de Canal 1 que con el tiempo arma cartera
debería poder referir (Canal 2), y un referidor de Canal 2 que quiere cerrar más
activo debería poder tocar la bolsa (Canal 1). Una sola identidad, dos modos:
sube el LTV por aliado y elimina la fricción del "¿yo qué soy?".

  - canal1_habilitado: acceso a la bolsa de leads.
  - canal2_habilitado: referir sobre cartera propia.
  - canal_activo:      modo en que está parado en el portal (solo UI).

Habilitar el otro canal es self-serve y gratis (no hay costo de ingreso en
ninguno). El backfill inicial sale de tipo_aliado (ver mejoras_canales.py).

INTEGRACIÓN: app.include_router(canales.router) en main.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from auth import verify_ownership_dep
from models import Aliado
from notificaciones import notificar_aliado

router = APIRouter(tags=["canales"])

CANAL_INFO = {
    "canal1": {
        "nombre": "Canal 1 · Bolsa de leads",
        "desbloquea": "Reclamás leads calificados con score IA y pitch sugerido. "
                      "Sin cartera previa.",
        "tab": "bolsa",
    },
    "canal2": {
        "nombre": "Canal 2 · Tu cartera",
        "desbloquea": "Referís a clientes que ya confían en vos y cobrás comisión "
                      "+ 10% recurrente. Sin exclusividad.",
        "tab": "red",
    },
}


def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def _estado(a: Aliado) -> dict:
    activo = a.canal_activo or (a.tipo_aliado or "canal1")
    return {
        "canal1_habilitado": bool(a.puede_canal1),
        "canal2_habilitado": bool(a.puede_canal2),
        "canal_activo": activo,
        "canales": [
            {"clave": c, **info, "habilitado": (a.puede_canal1 if c == "canal1" else a.puede_canal2)}
            for c, info in CANAL_INFO.items()
        ],
    }


def backfill_canales(db: Session) -> dict:
    """Setea los flags de canal desde tipo_aliado para aliados que los tengan en
    NULL. Idempotente. Complementa la migración SQL (por si algún aliado quedó
    sin backfillear). Commitea."""
    pendientes = (db.query(Aliado)
                  .filter((Aliado.canal1_habilitado == None) |   # noqa: E711
                          (Aliado.canal2_habilitado == None))
                  .all())
    for a in pendientes:
        tipo = a.tipo_aliado or "canal1"
        if a.canal1_habilitado is None:
            a.canal1_habilitado = (tipo == "canal1")
        if a.canal2_habilitado is None:
            a.canal2_habilitado = (tipo == "canal2")
        if not a.canal_activo:
            a.canal_activo = tipo
    if pendientes:
        db.commit()
    return {"backfilleados": len(pendientes)}


# ─── ENDPOINTS ───────────────────────────────────────────────────────────────

@router.get("/aliados/{codigo}/canales")
def mis_canales(codigo: str, db: Session = Depends(get_db),
                _owner=Depends(verify_ownership_dep)):
    """Estado de canales del aliado: qué tiene habilitado y en qué modo está."""
    a = _get_aliado(codigo, db)
    return _estado(a)


@router.post("/aliados/{codigo}/canales/activar")
def activar_canal(codigo: str, payload: dict = Body(...),
                  db: Session = Depends(get_db),
                  _owner=Depends(verify_ownership_dep)):
    """Habilita el otro canal para este aliado (self-serve). body: {canal}."""
    a = _get_aliado(codigo, db)
    canal = (payload.get("canal") or "").strip()
    if canal not in CANAL_INFO:
        raise HTTPException(400, "Canal inválido. Usá 'canal1' o 'canal2'.")

    ya = a.puede_canal1 if canal == "canal1" else a.puede_canal2
    if ya:
        return {"status": "ok", "ya_habilitado": True, **_estado(a)}

    if canal == "canal1":
        a.canal1_habilitado = True
    else:
        a.canal2_habilitado = True
    # Lo dejamos parado en el canal recién abierto.
    a.canal_activo = canal

    info = CANAL_INFO[canal]
    notificar_aliado(
        db, a.id, "canales",
        f"Activaste {info['nombre']}",
        info["desbloquea"], tab=info["tab"],
    )
    db.commit()
    return {"status": "ok", "activado": canal, **_estado(a)}


@router.post("/aliados/{codigo}/canales/modo")
def cambiar_modo(codigo: str, payload: dict = Body(...),
                 db: Session = Depends(get_db),
                 _owner=Depends(verify_ownership_dep)):
    """Cambia el modo activo (UI). Solo a un canal que tenga habilitado."""
    a = _get_aliado(codigo, db)
    canal = (payload.get("canal") or "").strip()
    if canal not in CANAL_INFO:
        raise HTTPException(400, "Canal inválido.")
    habilitado = a.puede_canal1 if canal == "canal1" else a.puede_canal2
    if not habilitado:
        raise HTTPException(409, "Ese canal no está habilitado. Activalo primero.")
    a.canal_activo = canal
    db.commit()
    return {"status": "ok", **_estado(a)}
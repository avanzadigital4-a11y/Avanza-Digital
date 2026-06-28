"""
reciclado.py  ·  Reciclado de leads de la bolsa (Canal 1)
================================================================================

QUE RESUELVE
------------
Hoy un lead trabajado y no cerrado vuelve CRUDO a la bolsa: el próximo que lo
reclama arranca a ciegas, sin saber que ya lo llamaron tres veces. La bolsa se
degrada y se quema la calidad — que es justo la ventaja del canal.

Este módulo le pone memoria a la bolsa:
  - Cada intento queda registrado (quién, cuándo, resultado, nota).
  - Un lead no cerrado NO reaparece al instante: entra en COOLDOWN ('nurture')
    y vuelve recién cuando se enfría, con su historial VISIBLE.
  - Tras N reciclados se RETIRA ('quemado') para no rotar basura para siempre.

Cooldowns por resultado (un "no contesto" se reintenta antes que un "no
interesado"; un abandono por vencimiento, casi enseguida).

INTEGRACIÓN (ver INTEGRACION.md) — todo son 1-línea en bolsa.py:
  - registrar_intento(...) en registrar_resultado()  (no_interesado / no_contesto)
  - registrar_intento(..., 'abandono') al liberar por vencimiento (job 48h) y al
    liberar a mano.
  - app.include_router(reciclado.router) + scheduler procesar_cooldowns.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from auth import current_aliado_required, current_admin_required
from models import LeadBolsa, Aliado

router = APIRouter(tags=["reciclado"])

# ─── PARÁMETROS DE NEGOCIO ───────────────────────────────────────────────────
MAX_RECICLADOS = 3                 # tras esto → 'quemado' (retirado de la bolsa)
COOLDOWN_HORAS = {
    "no_contesto":   72,           # 3 días: pudo estar ocupado, se reintenta
    "no_interesado": 24 * 21,      # 21 días: dijo que no, dejar respirar
    "abandono":      24,           # vencimiento/liberación: casi enseguida
}
# Un "no_interesado" pesa más: con 2 ya se quema aunque no llegue a MAX_RECICLADOS.
QUEMA_DIRECTA = {"no_interesado": 2}


def _cargar(hist_json) -> list:
    try:
        v = json.loads(hist_json or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _nombre_aliado(db, aliado_id):
    if not aliado_id:
        return None
    a = db.query(Aliado).filter(Aliado.id == aliado_id).first()
    return a.nombre if a else None


def registrar_intento(db: Session, lead: LeadBolsa, aliado_id, resultado: str,
                      nota: str = "") -> dict:
    """Registra un intento sobre el lead y decide su siguiente estado:
    cooldown ('nurture'), o retiro ('quemado'). NO hace commit (lo maneja el
    caller, que ya está dentro de su transacción).

    resultado: 'no_contesto' | 'no_interesado' | 'abandono'  (otros se ignoran,
    p.ej. 'exitoso' no recicla porque el lead convirtió).
    """
    if resultado not in COOLDOWN_HORAS:
        return {"reciclado": False, "motivo": "resultado_no_reciclable"}

    hist = _cargar(lead.historial_intentos)
    hist.append({
        "aliado_id": aliado_id,
        "aliado": _nombre_aliado(db, aliado_id),
        "fecha": datetime.now().isoformat(timespec="minutes"),
        "resultado": resultado,
        "nota": (nota or "")[:280] or None,
    })
    lead.historial_intentos = json.dumps(hist[-20:], ensure_ascii=False)
    lead.intentos = (lead.intentos or 0) + 1

    # ¿Se quema? Por tope de reciclados o por quema directa del resultado.
    intentos_de_este = sum(1 for h in hist if h.get("resultado") == resultado)
    if ((lead.reciclados or 0) >= MAX_RECICLADOS
            or intentos_de_este >= QUEMA_DIRECTA.get(resultado, 99)):
        lead.estado = "quemado"
        lead.aliado_id = None
        lead.fecha_reclamo = None
        lead.cooldown_hasta = None
        return {"reciclado": False, "quemado": True, "intentos": lead.intentos}

    # Si no, a enfriar: 'nurture' con cooldown. El job lo devuelve a 'disponible'.
    horas = COOLDOWN_HORAS[resultado]
    lead.estado = "nurture"
    lead.aliado_id = None
    lead.fecha_reclamo = None
    lead.resultado = None  # se limpia el resultado puntual; el historial queda
    lead.cooldown_hasta = datetime.now() + timedelta(hours=horas)
    return {"reciclado": True, "estado": "nurture",
            "vuelve_en_horas": horas, "intentos": lead.intentos}


def procesar_cooldowns(db: Session) -> dict:
    """Job: devuelve a 'disponible' los leads cuyo cooldown ya venció, contándolos
    como reciclados. SÍ hace commit (corre como tarea independiente del scheduler)."""
    ahora = datetime.now()
    pendientes = (db.query(LeadBolsa)
                  .filter(LeadBolsa.estado == "nurture",
                          LeadBolsa.cooldown_hasta != None,        # noqa: E711
                          LeadBolsa.cooldown_hasta <= ahora)
                  .all())
    n = 0
    for lead in pendientes:
        lead.estado = "disponible"
        lead.reciclados = (lead.reciclados or 0) + 1
        lead.cooldown_hasta = None
        lead.aliado_id = None
        lead.fecha_reclamo = None
        n += 1
    if n:
        db.commit()
    return {"reactivados": n}


def resumen_intentos(lead: LeadBolsa) -> dict:
    """Resumen legible del historial, para mostrarle al próximo que lo reclame
    así no arranca a ciegas."""
    hist = _cargar(lead.historial_intentos)
    return {
        "intentos": lead.intentos or 0,
        "reciclados": lead.reciclados or 0,
        "ultimo": hist[-1] if hist else None,
        "historial": hist,
    }


# ─── ENDPOINTS ───────────────────────────────────────────────────────────────

@router.get("/bolsa/{lead_id}/historial")
def historial_lead(lead_id: int, db: Session = Depends(get_db),
                   aliado: Aliado = Depends(current_aliado_required)):
    """El aliado ve el historial de intentos de un lead antes/después de
    reclamarlo. Transparencia = no repetir el laburo del anterior."""
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == lead_id).first()
    if not lead:
        return {"existe": False}
    return {"existe": True, "empresa": lead.empresa, "estado": lead.estado,
            **resumen_intentos(lead)}


@router.get("/admin/bolsa/reciclados")
def admin_reciclados(db: Session = Depends(get_db),
                     _admin=Depends(current_admin_required)):
    """Tablero ops: leads en cooldown y leads quemados (para auditar la salud
    de la bolsa y, si hace falta, reinyectar/retirar a mano)."""
    nurture = (db.query(LeadBolsa)
               .filter(LeadBolsa.estado == "nurture")
               .order_by(LeadBolsa.cooldown_hasta.asc()).all())
    quemados = (db.query(LeadBolsa)
                .filter(LeadBolsa.estado == "quemado")
                .order_by(LeadBolsa.id.desc()).limit(200).all())

    def fila(l):
        return {"id": l.id, "empresa": l.empresa, "rubro": l.rubro,
                "intentos": l.intentos or 0, "reciclados": l.reciclados or 0,
                "cooldown_hasta": l.cooldown_hasta.strftime("%d/%m %H:%M") if l.cooldown_hasta else None}

    return {
        "en_cooldown": [fila(l) for l in nurture],
        "quemados": [fila(l) for l in quemados],
        "totales": {"en_cooldown": len(nurture), "quemados": len(quemados)},
    }
"""
rampa.py  ·  Rampa de aliado nuevo + primer cierre asistido (Canal 1)
================================================================================

QUE RESUELVE
------------
El make-or-break de una red a comisión es el TIEMPO AL PRIMER CIERRE. El aliado
que no cierra en sus primeras 1-2 semanas se evapora en silencio — no por mal
producto, sino por rampa. Esto acompaña ese tramo:

  1. Al ingresar, se le asigna un MENTOR (aliado senior) para su primer deal.
  2. Hitos de bienvenida con un checklist claro (activado → primer lead → cierre).
  3. Al PRIMER CIERRE: se cierra la mentoría y el MENTOR cobra un bonus por
     haber acompañado. El debutante ya cobra el bonus de primera venta del
     sistema, así que la rampa no duplica créditos: suma el incentivo al mentor.

No inventa comisiones ni toca el flujo de plata: usa créditos (la moneda interna
que ya existe) y novedades in-app. El split setter→closer real corre por Equipo.

INTEGRACIÓN (ver INTEGRACION.md)
  - iniciar_rampa(db, aliado)            → al registrar un aliado nuevo.
  - procesar_primer_cierre(db, aliado_id)→ en checkout.py, cuando es_primera_venta.
  - app.include_router(rampa.router)     → en main.py.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from auth import verify_ownership_dep, current_admin_required
from models import Aliado, Mentoria
from notificaciones import notificar_aliado

router = APIRouter(tags=["rampa"])

# ─── PARÁMETROS DE NEGOCIO ───────────────────────────────────────────────────
# La rampa NO le paga créditos extra al debutante (el bonus de primera venta del
# sistema ya cumple esa función). El incentivo nuevo es para el MENTOR: cobra
# este bonus cuando su mentee cierra por primera vez.
RECOMPENSA_MENTOR_CREDITOS = 50    # al mentor cuando su mentee debuta

# Secuencia de estados de rampa (monótona; nunca retrocede).
RAMPA_ORDEN = ["nuevo", "activado", "primer_lead", "primer_cierre", "graduado"]
RAMPA_LABEL = {
    "nuevo":         "Recién ingresado",
    "activado":      "Onboarding completo",
    "primer_lead":   "Primer lead reclamado",
    "primer_cierre": "¡Primer cierre!",
    "graduado":      "Graduado",
}
# Checklist que ve el aliado en su panel de rampa.
RAMPA_CHECKLIST = [
    {"clave": "activado",      "texto": "Completá tu onboarding y aceptá los términos"},
    {"clave": "primer_lead",   "texto": "Reclamá tu primer lead de la bolsa"},
    {"clave": "primer_cierre", "texto": "Cerrá tu primer deal (tu mentor te acompaña)"},
]


def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def _ajustar_creditos(*args, **kwargs):
    from main import _ajustar_creditos as f
    return f(*args, **kwargs)


def _rank(estado: str) -> int:
    try:
        return RAMPA_ORDEN.index(estado or "nuevo")
    except ValueError:
        return 0


def avanzar_rampa(db: Session, aliado: Aliado, nuevo_estado: str) -> bool:
    """Avanza el estado de rampa solo hacia adelante. Devuelve True si cambió.
    NO hace commit (lo maneja el caller)."""
    if nuevo_estado not in RAMPA_ORDEN:
        return False
    if _rank(nuevo_estado) <= _rank(aliado.rampa_estado or "nuevo"):
        return False
    aliado.rampa_estado = nuevo_estado
    return True


# ─── ASIGNACIÓN DE MENTOR ────────────────────────────────────────────────────

def _elegir_mentor(db: Session, mentee: Aliado):
    """Elige el mentor con menos mentorías activas, preferentemente del mismo
    país. Devuelve un Aliado o None.

    Primero busca aliados marcados como mentor (es_mentor). Si no hay ninguno
    todavía, cae a aliados SENIOR (nivel alto o que ya tuvieron su primer
    cierre): así la rampa funciona desde el día uno sin configurar nada a mano.
    """
    candidatos = (db.query(Aliado)
                  .filter(Aliado.es_mentor == True,        # noqa: E712
                          Aliado.activo == True,           # noqa: E712
                          Aliado.id != mentee.id)
                  .all())
    if not candidatos:
        # Fallback: closers probados (PREMIUM/ELITE o con primer cierre hecho).
        candidatos = (db.query(Aliado)
                      .filter(Aliado.activo == True,        # noqa: E712
                              Aliado.id != mentee.id,
                              ((Aliado.nivel.in_(["PREMIUM", "ELITE"]))
                               | (Aliado.primer_cierre_en != None)))  # noqa: E711
                      .all())
    if not candidatos:
        return None

    def carga(m):
        n = (db.query(func.count(Mentoria.id))
             .filter(Mentoria.mentor_id == m.id, Mentoria.estado == "activa")
             .scalar()) or 0
        mismo_pais = 0 if (m.pais and mentee.pais and m.pais == mentee.pais) else 1
        return (n, mismo_pais, m.id)

    return sorted(candidatos, key=carga)[0]


def iniciar_rampa(db: Session, aliado: Aliado) -> dict:
    """Arranca la rampa de un aliado nuevo: estado inicial + asignación de mentor
    + mentoría abierta. Idempotente (no duplica mentoría activa). Best-effort:
    si no hay mentores, el aliado igual arranca su rampa sin mentor.
    NO hace commit."""
    if not aliado.rampa_estado:
        aliado.rampa_estado = "nuevo"

    ya = (db.query(Mentoria)
          .filter(Mentoria.mentee_id == aliado.id, Mentoria.estado == "activa")
          .first())
    if ya:
        return {"mentor_asignado": False, "motivo": "ya_tiene_mentoria"}

    mentor = _elegir_mentor(db, aliado)
    if not mentor:
        return {"mentor_asignado": False, "motivo": "sin_mentores"}

    aliado.mentor_id = mentor.id
    db.add(Mentoria(mentee_id=aliado.id, mentor_id=mentor.id, estado="activa",
                    abierta_en=datetime.now()))

    notificar_aliado(
        db, mentor.id, "rampa",
        f"Sos mentor de {aliado.nombre}",
        f"Le toca arrancar en la bolsa. Tu rol: acompañar su primer cierre. "
        f"Cuando debute, cobrás {RECOMPENSA_MENTOR_CREDITOS} créditos de bonus.",
        tab="rampa",
    )
    notificar_aliado(
        db, aliado.id, "rampa",
        f"Tu mentor es {mentor.nombre}",
        "Te asignamos un aliado senior para acompañar tu primer cierre. "
        "Entrá a Mi Rampa para ver tu checklist.",
        tab="rampa",
    )
    return {"mentor_asignado": True, "mentor": {"codigo": mentor.codigo, "nombre": mentor.nombre}}


# ─── PRIMER CIERRE ───────────────────────────────────────────────────────────

def procesar_primer_cierre(db: Session, aliado_id: int) -> dict:
    """Se llama en el momento del PRIMER cierre confirmado del aliado.
    Idempotente vía `rampa_recompensa_en`: si ya se otorgó, no hace nada.
    Otorga créditos al debutante, bonus al mentor, cierra la mentoría y notifica.
    NO hace commit (el caller — checkout — ya commitea su transacción)."""
    a = db.query(Aliado).filter(Aliado.id == aliado_id).first()
    if not a:
        return {"ok": False, "motivo": "aliado_inexistente"}
    if a.rampa_recompensa_en is not None:
        return {"ok": False, "motivo": "ya_otorgado"}

    ahora = datetime.now()
    a.primer_cierre_en = ahora
    a.rampa_recompensa_en = ahora
    avanzar_rampa(db, a, "primer_cierre")
    # Persistir los campos de rampa antes de cualquier refresh posterior
    # (p.ej. el _ajustar_creditos del bonus al mentor refresca a su objeto).
    db.flush()

    # El debutante NO recibe créditos extra acá: el bonus de primera venta del
    # sistema ya lo cubre. La rampa aporta el lado del MENTOR (incentivo a
    # acompañar) + el seguimiento. Acá solo lo felicitamos.
    notificar_aliado(
        db, a.id, "rampa",
        "🎉 ¡Tu primer cierre!",
        "Rompiste el hielo. El que sigue es más fácil — ya conocés el camino.",
        tab="rampa",
    )

    # Cerrar mentoría activa + bonus al mentor.
    mentor_bonus = 0
    m = (db.query(Mentoria)
         .filter(Mentoria.mentee_id == a.id, Mentoria.estado == "activa")
         .first())
    if m:
        m.estado = "cerrada_cierre"
        m.cerrada_en = ahora
        mentor = db.query(Aliado).filter(Aliado.id == m.mentor_id).first()
        if mentor:
            try:
                _ajustar_creditos(db, mentor, RECOMPENSA_MENTOR_CREDITOS,
                                  "rampa_bonus_mentor", ref=f"mentee:{a.id}")
                mentor_bonus = RECOMPENSA_MENTOR_CREDITOS
            except Exception as e:
                print(f"[RAMPA] no se pudo acreditar al mentor {mentor.id}: {e}")
            notificar_aliado(
                db, mentor.id, "rampa",
                f"Tu mentee {a.nombre} debutó · +{RECOMPENSA_MENTOR_CREDITOS} créditos",
                "Acompañaste un primer cierre. Bien ahí. Bonus acreditado.",
                tab="rampa",
            )

    return {"ok": True, "mentor_bonus": mentor_bonus}


# ─── ENDPOINTS ───────────────────────────────────────────────────────────────

@router.get("/aliados/{codigo}/rampa")
def mi_rampa(codigo: str, db: Session = Depends(get_db),
             _owner=Depends(verify_ownership_dep)):
    """Panel de rampa del aliado: estado, progreso, checklist y mentor."""
    a = _get_aliado(codigo, db)
    estado = a.rampa_estado or "nuevo"
    rank = _rank(estado)

    mentor_info = None
    if a.mentor_id:
        m = db.query(Aliado).filter(Aliado.id == a.mentor_id).first()
        if m:
            mentor_info = {"codigo": m.codigo, "nombre": m.nombre,
                           "whatsapp": m.whatsapp}

    checklist = [{
        "clave": item["clave"],
        "texto": item["texto"],
        "hecho": _rank(item["clave"]) <= rank,
    } for item in RAMPA_CHECKLIST]

    return {
        "estado": estado,
        "estado_label": RAMPA_LABEL.get(estado, estado),
        "progreso_pct": round(rank / (len(RAMPA_ORDEN) - 1) * 100),
        "graduado": estado in ("primer_cierre", "graduado"),
        "mentor": mentor_info,
        "checklist": checklist,
        "primer_cierre_en": a.primer_cierre_en.strftime("%d/%m/%Y") if a.primer_cierre_en else None,
    }


@router.get("/aliados/{codigo}/mentorias")
def mis_mentorias(codigo: str, db: Session = Depends(get_db),
                  _owner=Depends(verify_ownership_dep)):
    """Vista del MENTOR: sus mentees activos y debutados."""
    a = _get_aliado(codigo, db)
    filas = (db.query(Mentoria)
             .filter(Mentoria.mentor_id == a.id)
             .order_by(Mentoria.abierta_en.desc())
             .all())
    activas, cerradas = [], []
    for m in filas:
        mentee = db.query(Aliado).filter(Aliado.id == m.mentee_id).first()
        base = {
            "mentoria_id": m.id,
            "mentee": {"codigo": mentee.codigo, "nombre": mentee.nombre} if mentee else None,
            "mentee_estado": (mentee.rampa_estado if mentee else None),
            "abierta_en": m.abierta_en.strftime("%d/%m/%Y") if m.abierta_en else None,
        }
        (activas if m.estado == "activa" else cerradas).append(base)
    return {"es_mentor": bool(a.es_mentor), "activas": activas, "cerradas": cerradas}


@router.post("/aliados/{codigo}/rampa/reasignar-mentor")
def reasignar_mentor(codigo: str, db: Session = Depends(get_db),
                     _owner=Depends(verify_ownership_dep)):
    """El mentee pide otro mentor (el actual no respondió). Cierra la mentoría
    actual como 'cerrada_manual' y asigna uno nuevo."""
    a = _get_aliado(codigo, db)
    actual = (db.query(Mentoria)
              .filter(Mentoria.mentee_id == a.id, Mentoria.estado == "activa")
              .first())
    if actual:
        actual.estado = "cerrada_manual"
        actual.cerrada_en = datetime.now()
    a.mentor_id = None
    res = iniciar_rampa(db, a)
    db.commit()
    if not res.get("mentor_asignado"):
        return {"status": "ok", "reasignado": False,
                "mensaje": "No hay otro mentor disponible por ahora. Te avisamos cuando haya."}
    return {"status": "ok", "reasignado": True, "mentor": res["mentor"]}


@router.post("/admin/rampa/mentor/{codigo}")
def admin_set_mentor(codigo: str, payload: dict = Body(default={}),
                     db: Session = Depends(get_db),
                     _admin=Depends(current_admin_required)):
    """Admin habilita/inhabilita a un aliado como mentor."""
    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a:
        raise HTTPException(404, "Aliado no encontrado.")
    a.es_mentor = bool(payload.get("es_mentor", True))
    db.commit()
    return {"status": "ok", "codigo": a.codigo, "es_mentor": a.es_mentor}
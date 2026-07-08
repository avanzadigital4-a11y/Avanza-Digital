"""
eventos_uso.py — Tracking de uso del portal (todo click, submit y tab que
usan los aliados — nada queda afuera).

Objetivo: saber qué secciones/herramientas del portal se usan de verdad y
cuáles no las toca nadie, para poder decidir con datos qué simplificar o
sacar. Dos piezas:

  - POST /eventos/log      → recibe un evento desde portal.core.js (fire-and-
    forget, sin bloquear la UI). portal.core.js loguea de forma GENÉRICA
    cualquier click (botón, link, onclick, role="button") y cualquier
    submit de formulario, además del cambio de tab — no hace falta agregar
    nada a mano cada vez que se suma una función nueva al portal.
    Identifica al aliado por el JWT si viene logueado; si no, guarda el
    evento igual como anónimo ("sin_canal"). NUNCA debe romper la
    experiencia del aliado: cualquier error se traga silenciosamente.
  - GET  /admin/eventos-uso → métricas para el panel admin: por cada tab/
    feature, cuántos eventos totales y cuántos aliados ÚNICOS lo usaron
    alguna vez, con desglose por canal (canal1/canal2/sin_canal) — esto
    último es lo que realmente importa para decidir qué sacar — no
    "cuántos clicks" sino "cuánta gente lo usó alguna vez".

Rate-limited (3000/hora por IP) para que el tracking genérico de clicks no
permita abuso del endpoint, sin ahogar el uso normal del portal.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from auth import current_admin_required, _extraer_token, decodificar_token
from database import get_db
from models import Aliado, EventoUso
from rate_limit import limiter

router = APIRouter(tags=["eventos_uso"])


def _aliado_desde_token(request: Request, db: Session):
    """Best-effort: si viene un JWT de aliado válido, devuelve (aliado_id, canal).
    Cualquier problema (sin token, token vencido, etc.) → (None, None), sin
    levantar excepción — loguear el evento nunca debe fallar por esto."""
    try:
        token = _extraer_token(request)
        if not token:
            return None, None
        payload = decodificar_token(token)
        if payload.get("tipo") != "aliado":
            return None, None
        a = db.query(Aliado).filter(Aliado.codigo == payload.get("sub")).first()
        if not a:
            return None, None
        return a.id, a.tipo_aliado
    except Exception:
        return None, None


@router.post("/eventos/log")
@limiter.limit("3000/hour")
def log_evento(request: Request, payload: dict, db: Session = Depends(get_db)):
    """Guarda un evento de uso del portal. Siempre responde {"ok": true/false},
    nunca un error — un fallo acá no debe afectar al aliado."""
    evento = (payload.get("evento") or "").strip()[:40]
    detalle = (payload.get("detalle") or "").strip()[:120]
    if not evento or not detalle:
        return {"ok": False}

    aliado_id, canal = _aliado_desde_token(request, db)
    try:
        db.add(EventoUso(aliado_id=aliado_id, evento=evento, detalle=detalle, canal=canal))
        db.commit()
    except Exception:
        db.rollback()
        return {"ok": False}
    return {"ok": True}


@router.get("/admin/eventos-uso")
def admin_eventos_uso(dias: int = 90, db: Session = Depends(get_db),
                       _admin=Depends(current_admin_required)):
    """Resumen de uso del portal para el panel admin: por cada tab/feature,
    cuántos eventos y cuántos aliados únicos lo usaron en los últimos `dias`
    días (default 90), con desglose por canal (canal1/canal2/sin_canal) —
    clave para los tabs compartidos (Mi CRM, Ventas, etc.) donde si no se
    separa por canal, el uso de uno tapa al del otro."""
    desde = datetime.utcnow() - timedelta(days=dias)

    filas = (
        db.query(
            EventoUso.evento,
            EventoUso.detalle,
            sa_func.count(EventoUso.id).label("eventos"),
            sa_func.count(sa_func.distinct(EventoUso.aliado_id)).label("aliados_unicos"),
            sa_func.max(EventoUso.creado_en).label("ultimo_uso"),
        )
        .filter(EventoUso.creado_en >= desde)
        .group_by(EventoUso.evento, EventoUso.detalle)
        .order_by(sa_func.count(EventoUso.id).desc())
        .all()
    )

    # Mismo agrupado pero abriendo por canal, para armar el desglose por fila.
    filas_canal = (
        db.query(
            EventoUso.evento,
            EventoUso.detalle,
            EventoUso.canal,
            sa_func.count(EventoUso.id).label("eventos"),
            sa_func.count(sa_func.distinct(EventoUso.aliado_id)).label("aliados_unicos"),
        )
        .filter(EventoUso.creado_en >= desde)
        .group_by(EventoUso.evento, EventoUso.detalle, EventoUso.canal)
        .all()
    )
    por_canal_map = {}
    for fc in filas_canal:
        clave = (fc.evento, fc.detalle)
        canal = fc.canal or "sin_canal"
        por_canal_map.setdefault(clave, {})[canal] = {
            "eventos": fc.eventos,
            "aliados_unicos": fc.aliados_unicos,
        }

    total_aliados_activos = db.query(Aliado).filter(Aliado.activo == True).count()
    total_canal1 = db.query(Aliado).filter(Aliado.activo == True, Aliado.tipo_aliado == "canal1").count()
    total_canal2 = db.query(Aliado).filter(Aliado.activo == True, Aliado.tipo_aliado == "canal2").count()

    detalle_out = [
        {
            "evento": f.evento,
            "detalle": f.detalle,
            "eventos": f.eventos,
            "aliados_unicos": f.aliados_unicos,
            "ultimo_uso": f.ultimo_uso.strftime("%d/%m/%Y %H:%M") if f.ultimo_uso else None,
            "por_canal": por_canal_map.get((f.evento, f.detalle), {}),
        }
        for f in filas
    ]

    return {
        "dias": dias,
        "total_eventos": sum(f["eventos"] for f in detalle_out),
        "total_aliados_activos": total_aliados_activos,
        "total_canal1": total_canal1,
        "total_canal2": total_canal2,
        "detalle": detalle_out,
    }
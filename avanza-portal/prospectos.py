"""
prospectos.py — CRM de prospectos del aliado (pipeline completo).

Quinto router migrado de main.py (tramo 3 del split). Contiene:
  - CRUD del prospecto: crear, listar, nota, interesante, eliminar, bulk
    con dedup, toggle de piloto automático y resumen admin.
  - Pipeline spec §8: contactar → respondió → propuesta_enviada → estado
    manual ('pagado'/'comision_abonada' los setea el sistema de pagos).
  - CRM v3.0: timeline de actividades, tareas/recordatorios con
    recálculo de próxima acción, datos de contacto y "marcar ganado".
  - Empresa↔contactos: varios interlocutores por prospecto (SALTO 3).
  - Puente CRM→Referido: registrar para venta en 1 click (idempotente).

Los endpoints de IA sobre prospectos (perfilar, followup-ia, objecion-ia,
analizar-perdida, mensaje-outreach) siguen en main.py — dependen del stack
Jarvis/Groq y migran cuando se extraiga ese dominio.

Ownership: _get_prospecto_owned_or_admin garantiza que cada operación la
hace el dueño del prospecto (o un admin), devolviendo 404 (no 403) para no
filtrar existencia. main.py importa este helper para sus endpoints de IA.
"""
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import schemas
from auth import current_aliado_required, current_admin_required, verify_ownership_dep
from database import get_db
from models import (
    Aliado, Prospecto, ActividadProspecto, ContactoProspecto,
    Referido, LeadBolsa, AuditoriaLog, PLANES,
)

router = APIRouter(tags=["prospectos"])


# ── Puente diferido a helpers de main (evita import circular) ────────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


# ─── PROSPECTOS ──────────────────────────────────────────────────────────────

@router.post("/prospectos/crear")
def crear_prospecto(body: schemas.CrearProspectoIn | None = Body(default=None),
                    codigo_aliado: str = "",  # legacy
                    nombre: str = "", contacto: str = "",
                    plan_interes: str = "", rubro: str = "", nota: str = "",
                    aliado: Aliado = Depends(current_aliado_required),
                    db: Session = Depends(get_db)):
    """El aliado autenticado carga un prospecto nuevo.

    SECURITY: ya NO acepta `codigo_aliado` para asignar a otro aliado.
    El prospecto siempre se crea para el aliado del JWT.
    """
    if body is not None:
        nombre, contacto = body.nombre, body.contacto
        plan_interes, rubro, nota = body.plan_interes, body.rubro, body.nota
    if not nombre:
        raise HTTPException(400, "Falta nombre del prospecto.")
    p = Prospecto(aliado_id=aliado.id, nombre=nombre, contacto=contacto,
                  plan_interes=plan_interes, rubro=rubro or None, nota=nota)
    db.add(p); db.commit(); db.refresh(p)
    return {"mensaje": "Prospecto cargado.", "id": p.id, "nombre": p.nombre}


@router.get("/prospectos/aliado/{codigo}")
def listar_prospectos_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Portal: prospectos del aliado logueado."""
    a = _get_aliado(codigo, db)
    return [_prospecto_row(p) for p in sorted(a.prospectos, key=lambda x: x.creado_en, reverse=True)]


# ─── HELPER: obtener prospecto solo si pertenece al aliado del JWT ───────────
def _get_prospecto_owned(id: int, aliado: Aliado, db: Session) -> Prospecto:
    """Devuelve el Prospecto SOLO si pertenece al aliado del JWT (o el JWT es admin).
    Lanza 404 si no existe, 403 si pertenece a otro."""
    p = db.query(Prospecto).filter(Prospecto.id == id).first()
    if not p:
        raise HTTPException(404, "Prospecto no encontrado.")
    if p.aliado_id != aliado.id:
        # Para no leakear "existe pero no es tuyo", devolvemos 404 igual.
        raise HTTPException(404, "Prospecto no encontrado.")
    return p


def _get_prospecto_owned_or_admin(id: int, request: Request, db: Session) -> Prospecto:
    """Como _get_prospecto_owned pero acepta JWT admin además del dueño.
    Útil cuando no podemos saber a priori si el llamante es admin o aliado."""
    from auth import _extraer_token, decodificar_token
    p = db.query(Prospecto).filter(Prospecto.id == id).first()
    if not p:
        raise HTTPException(404, "Prospecto no encontrado.")
    token = _extraer_token(request)
    if not token:
        raise HTTPException(401, "Falta token.")
    try:
        payload = decodificar_token(token)
    except Exception:
        raise HTTPException(401, "Token inválido.")
    if payload.get("tipo") == "admin":
        return p
    if payload.get("tipo") == "aliado":
        a = db.query(Aliado).filter(Aliado.codigo == payload.get("sub")).first()
        if a and p.aliado_id == a.id:
            return p
    raise HTTPException(404, "Prospecto no encontrado.")


@router.patch("/prospectos/{id}/contactar")
def marcar_contactado(id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.estado = "contactado"
    p.fecha_contacto = datetime.now()
    db.commit()
    return {"mensaje": "Marcado como contactado.", "estado": p.estado}


@router.patch("/prospectos/{id}/respondio")
def marcar_respondio(id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.estado = "respondio"
    p.fecha_respuesta = datetime.now()
    if not p.fecha_contacto:
        p.fecha_contacto = datetime.now()
    db.commit()
    return {"mensaje": "Marcado como respondió.", "estado": p.estado}


@router.patch("/prospectos/{id}/propuesta-enviada")
def marcar_propuesta_enviada(id: int, request: Request, db: Session = Depends(get_db)):
    """Marca manualmente un prospecto como 'propuesta_enviada' (spec §8)."""
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.estado = "propuesta_enviada"
    if not p.fecha_contacto:
        p.fecha_contacto = datetime.now()
    db.commit()
    return {"mensaje": "Marcado como propuesta enviada.", "estado": p.estado}


@router.patch("/prospectos/{id}/estado")
def cambiar_estado_prospecto(id: int, request: Request,
                              body: schemas.CambiarEstadoProspectoIn | None = Body(default=None),
                              estado: str = "",
                              db: Session = Depends(get_db)):
    """Cambia el estado del prospecto dentro del pipeline del spec §8.
    Solo permite estados manuales; 'pagado' y 'comision_abonada' los setea el sistema."""
    if body is not None:
        estado = body.estado
    estados_manuales = {"registrado", "sin_contactar", "contactado", "respondio", "propuesta_enviada", "perdido"}
    if estado not in estados_manuales:
        raise HTTPException(
            400,
            f"Estado inválido o reservado para el sistema. "
            f"Estados manuales permitidos: {sorted(estados_manuales)}"
        )
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.estado = estado
    if estado in ("contactado", "respondio", "propuesta_enviada") and not p.fecha_contacto:
        p.fecha_contacto = datetime.now()
    if estado == "respondio":
        p.fecha_respuesta = datetime.now()
    db.commit()
    return {"mensaje": f"Estado cambiado a '{estado}'.", "estado": p.estado}


@router.patch("/prospectos/{id}/nota")
def actualizar_nota(id: int, request: Request,
                    body: schemas.ActualizarNotaIn | None = Body(default=None),
                    nota: str = "",
                    db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.nota = body.nota if body is not None else nota
    db.commit()
    return {"mensaje": "Nota guardada."}


@router.patch("/prospectos/{id}/interesante")
def toggle_interesante(id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.interesante = not p.interesante; db.commit()
    return {"interesante": p.interesante}


# ─── IMPORT MASIVO DE PROSPECTOS (ALIADO -> su CRM personal) ─────────────────
def _clave_prospecto_dup(nombre: str, contacto: str) -> set:
    """Claves de dedup para prospectos del propio aliado: nombre normalizado
    y/o telefono (solo digitos, >= 6)."""
    import re as _re
    claves = set()
    n = (nombre or "").strip().lower()
    if n:
        claves.add(("nom", n))
    tel = _re.sub(r"\D", "", contacto or "")
    if len(tel) >= 6:
        claves.add(("tel", tel))
    return claves


@router.post("/prospectos/bulk")
def crear_prospectos_bulk(body: schemas.ProspectosBulkIn,
                          aliado: Aliado = Depends(current_aliado_required),
                          db: Session = Depends(get_db)):
    """Importa una lista de prospectos al CRM personal del aliado de un saque.
    Deduplica contra los prospectos que el aliado ya tiene (mismo nombre o mismo
    telefono) y contra repetidos dentro del propio lote. Los duplicados se omiten
    y se reportan; el resto entra en 'sin_contactar'."""
    items = body.prospectos or []
    if not items:
        raise HTTPException(400, "La lista esta vacia.")
    indice = set()
    for p in aliado.prospectos:
        indice |= _clave_prospecto_dup(p.nombre, getattr(p, "contacto", "") or "")
    insertados = 0
    omitidos = []
    for it in items:
        nombre = (it.nombre or "").strip()
        if not nombre:
            continue
        claves = _clave_prospecto_dup(nombre, it.contacto)
        if claves & indice:
            omitidos.append(nombre)
            continue
        indice |= claves
        db.add(Prospecto(
            aliado_id=aliado.id,
            nombre=nombre,
            contacto=(it.contacto or "").strip(),
            plan_interes=(it.plan_interes or "").strip(),
            rubro=(it.rubro or "").strip() or None,
            nota=(it.nota or "").strip(),
        ))
        insertados += 1
    if insertados:
        db.commit()
    msg = f"{insertados} prospecto(s) importado(s)."
    if omitidos:
        msg += f" {len(omitidos)} duplicado(s) omitido(s)."
    return {"insertados": insertados, "omitidos": len(omitidos),
            "omitidos_detalle": omitidos[:50], "mensaje": msg}


@router.delete("/prospectos/{id}/eliminar")
def eliminar_prospecto(id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    # Soltar las referencias externas ANTES de borrar: en Postgres las FK sin
    # ON DELETE rechazarían el delete. Las filas vinculadas sobreviven sueltas:
    # - LeadBolsa/AuditoriaLog quedan re-convertibles (los puentes lo permiten).
    # - El Referido (atribución de venta) NUNCA se borra: solo pierde el vínculo.
    db.query(LeadBolsa).filter(LeadBolsa.prospecto_id == p.id)\
        .update({LeadBolsa.prospecto_id: None}, synchronize_session=False)
    db.query(AuditoriaLog).filter(AuditoriaLog.prospecto_id == p.id)\
        .update({AuditoriaLog.prospecto_id: None}, synchronize_session=False)
    db.query(Referido).filter(Referido.prospecto_id == p.id)\
        .update({Referido.prospecto_id: None}, synchronize_session=False)
    db.delete(p); db.commit()
    return {"mensaje": "Prospecto eliminado."}


@router.patch("/prospectos/{id}/piloto")
def toggle_piloto_automatico(id: int, request: Request,
                              body: schemas.TogglePilotoIn | None = Body(default=None),
                              activo: bool = False,
                              db: Session = Depends(get_db)):
    """Activa/desactiva el piloto automático de seguimiento."""
    if body is not None:
        activo = body.activo
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.piloto_automatico = activo
    db.commit()
    return {"piloto_automatico": p.piloto_automatico,
            "mensaje": "Piloto automático activado" if activo else "Piloto desactivado"}


@router.get("/admin/prospectos")
def admin_prospectos(db: Session = Depends(get_db),
                     _admin=Depends(current_admin_required)):
    """Admin: resumen de prospectos por aliado + lista completa.
    Incluye contadores del pipeline completo del spec §8."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    resumen = []
    for a in aliados:
        ps = a.prospectos
        if not ps:
            continue
        ultima = max((p.creado_en for p in ps), default=None)
        resumen.append({
            "codigo": a.codigo, "nombre": a.nombre,
            "total": len(ps),
            # Pipeline spec §8: registrado → contactado → propuesta_enviada → pagado → comision_abonada
            "sin_contactar":     sum(1 for p in ps if p.estado in ("sin_contactar", "registrado") or not p.estado),
            "contactados":       sum(1 for p in ps if p.estado == "contactado"),
            "respondieron":      sum(1 for p in ps if p.estado == "respondio"),
            "propuesta_enviada": sum(1 for p in ps if p.estado == "propuesta_enviada"),
            "pagados":           sum(1 for p in ps if p.estado == "pagado"),
            "comision_abonada":  sum(1 for p in ps if p.estado == "comision_abonada"),
            "interesantes":      sum(1 for p in ps if p.interesante),
            "ultima_actividad": ultima.strftime("%d/%m/%Y") if ultima else None,
            "prospectos": [_prospecto_row(p) for p in sorted(ps, key=lambda x: x.creado_en, reverse=True)],
        })
    resumen.sort(key=lambda x: x["ultima_actividad"] or "", reverse=True)
    totales = {
        "total":             sum(r["total"] for r in resumen),
        "sin_contactar":     sum(r["sin_contactar"] for r in resumen),
        "contactados":       sum(r["contactados"] for r in resumen),
        "respondieron":      sum(r["respondieron"] for r in resumen),
        "propuesta_enviada": sum(r["propuesta_enviada"] for r in resumen),
        "pagados":           sum(r["pagados"] for r in resumen),
        "comision_abonada":  sum(r["comision_abonada"] for r in resumen),
        "interesantes":      sum(r["interesantes"] for r in resumen),
    }
    return {"totales": totales, "por_aliado": resumen}




# ═══════════════════════════════════════════════════════════════════════════
# CRM v3.0 — Timeline de actividad + tareas/recordatorios por prospecto
# ═══════════════════════════════════════════════════════════════════════════
_ACT_TIPOS = {"nota", "llamada", "whatsapp", "email", "reunion", "tarea", "sistema"}

def _actividad_row(a):
    return {
        "id": a.id, "prospecto_id": a.prospecto_id, "tipo": a.tipo,
        "canal": a.canal, "descripcion": a.descripcion,
        "creado_en": a.creado_en.isoformat() if a.creado_en else None,
        "vence_en": a.vence_en.isoformat() if a.vence_en else None,
        "completada": bool(a.completada),
        "completada_en": a.completada_en.isoformat() if a.completada_en else None,
    }

def _parse_dt_crm(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", ""))
    except Exception:
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d")
        except Exception:
            return None

def _recalcular_proxima_accion(p, db):
    pend = (db.query(ActividadProspecto)
              .filter(ActividadProspecto.prospecto_id == p.id,
                      ActividadProspecto.tipo == "tarea",
                      ActividadProspecto.completada == False,
                      ActividadProspecto.vence_en != None)
              .order_by(ActividadProspecto.vence_en.asc())
              .first())
    p.proxima_accion_en = pend.vence_en if pend else None


@router.post("/prospectos/{id}/actividad")
def log_actividad(id: int, request: Request, tipo: str = "nota",
                  descripcion: str = "", canal: str = "",
                  db: Session = Depends(get_db)):
    """Registra una interacción en el timeline del prospecto (nota/llamada/whatsapp/email/reunion)."""
    p = _get_prospecto_owned_or_admin(id, request, db)
    tipo = (tipo or "nota").lower()
    if tipo not in _ACT_TIPOS or tipo == "tarea":
        tipo = "nota"  # las tareas se crean por /tarea, no por acá
    act = ActividadProspecto(prospecto_id=p.id, aliado_id=p.aliado_id,
                             tipo=tipo, canal=(canal or None),
                             descripcion=(descripcion or None))
    db.add(act); db.commit(); db.refresh(act)
    return {"mensaje": "Actividad registrada.", "actividad": _actividad_row(act)}


@router.get("/prospectos/{id}/actividades")
def listar_actividades(id: int, request: Request, db: Session = Depends(get_db)):
    """Timeline completo del prospecto (más reciente primero)."""
    p = _get_prospecto_owned_or_admin(id, request, db)
    acts = (db.query(ActividadProspecto)
              .filter(ActividadProspecto.prospecto_id == p.id)
              .order_by(ActividadProspecto.creado_en.desc())
              .all())
    return [_actividad_row(a) for a in acts]


@router.post("/prospectos/{id}/tarea")
def crear_tarea(id: int, request: Request, descripcion: str = "",
                vence_en: str = "", db: Session = Depends(get_db)):
    """Crea una tarea/recordatorio con fecha de vencimiento opcional (ISO o YYYY-MM-DD)."""
    p = _get_prospecto_owned_or_admin(id, request, db)
    if not (descripcion or "").strip():
        raise HTTPException(400, "La tarea necesita una descripción.")
    act = ActividadProspecto(prospecto_id=p.id, aliado_id=p.aliado_id,
                             tipo="tarea", descripcion=descripcion.strip(),
                             vence_en=_parse_dt_crm(vence_en), completada=False)
    db.add(act); db.commit()
    _recalcular_proxima_accion(p, db); db.commit(); db.refresh(act)
    return {"mensaje": "Tarea creada.", "actividad": _actividad_row(act)}


@router.patch("/actividades/{act_id}/completar")
def completar_tarea(act_id: int, request: Request, db: Session = Depends(get_db)):
    act = db.query(ActividadProspecto).filter(ActividadProspecto.id == act_id).first()
    if not act:
        raise HTTPException(404, "Actividad no encontrada.")
    p = _get_prospecto_owned_or_admin(act.prospecto_id, request, db)  # valida pertenencia
    act.completada = True
    act.completada_en = datetime.now()
    db.commit()
    _recalcular_proxima_accion(p, db); db.commit()
    return {"mensaje": "Tarea completada.", "id": act.id}


@router.post("/prospectos/{id}/seguimiento")
def registrar_seguimiento(id: int, request: Request,
                          detalle: str = "",
                          proxima_accion: str = "",
                          vence_en: str = "",
                          db: Session = Depends(get_db)):
    """Seguimiento en un solo paso, para el botón "Hice el seguimiento" del CRM.

    Resuelve la confusión típica del aliado que "registra lo que hizo y agenda
    lo siguiente" pero ve los contadores trabados: en una sola acción
      1. deja la actividad en el timeline (lo que se hizo),
      2. cierra TODAS las tareas abiertas (pendientes o vencidas) del prospecto,
      3. si se indica, agenda la próxima acción como tarea nueva con fecha,
      4. recalcula la próxima acción.
    Así el aliado cierra el pendiente y agenda el siguiente sin entrar a la Ficha,
    y los contadores de "tareas vencidas" / "sin próximo paso" reflejan su trabajo.
    """
    p = _get_prospecto_owned_or_admin(id, request, db)
    ahora = datetime.now()

    # 1. Timeline: qué se hizo.
    db.add(ActividadProspecto(
        prospecto_id=p.id, aliado_id=p.aliado_id, tipo="nota",
        descripcion=((detalle or "").strip() or "Hice un seguimiento."),
        creado_en=ahora,
    ))

    # 2. Cerrar tareas abiertas (pendientes o vencidas) de este prospecto.
    abiertas = (db.query(ActividadProspecto)
                  .filter(ActividadProspecto.prospecto_id == p.id,
                          ActividadProspecto.tipo == "tarea",
                          ActividadProspecto.completada == False)
                  .all())
    for t in abiertas:
        t.completada = True
        t.completada_en = ahora

    # 3. Agendar la próxima acción (opcional).
    if (proxima_accion or "").strip():
        db.add(ActividadProspecto(
            prospecto_id=p.id, aliado_id=p.aliado_id, tipo="tarea",
            descripcion=proxima_accion.strip(),
            vence_en=_parse_dt_crm(vence_en), completada=False,
        ))

    db.commit()
    _recalcular_proxima_accion(p, db); db.commit()

    pendientes = (db.query(ActividadProspecto)
                    .filter(ActividadProspecto.prospecto_id == p.id,
                            ActividadProspecto.tipo == "tarea",
                            ActividadProspecto.completada == False)
                    .count())
    return {
        "mensaje": "Seguimiento registrado.",
        "tareas_cerradas": len(abiertas),
        "tareas_pendientes": pendientes,
        "proxima_accion_en": p.proxima_accion_en.isoformat() if p.proxima_accion_en else None,
    }


# ── NOTA DE DISEÑO ────────────────────────────────────────────────────────────
# Este endpoint existe y funciona, pero NO tiene aún una pantalla en el portal
# que lo consuma. Es un hook deliberadamente pre-construido para un futuro
# "Panel de Tareas del Día" del aliado. NO es un bug ni un pendiente urgente;
# el contrato de API ya está definido y estable.
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/prospectos/tareas/pendientes")
def tareas_pendientes(request: Request,
                      aliado: Aliado = Depends(current_aliado_required),
                      db: Session = Depends(get_db)):
    """Todas las tareas pendientes del aliado (para recordatorios / dashboard), por vencimiento."""
    acts = (db.query(ActividadProspecto)
              .filter(ActividadProspecto.aliado_id == aliado.id,
                      ActividadProspecto.tipo == "tarea",
                      ActividadProspecto.completada == False)
              .order_by(ActividadProspecto.vence_en.asc())
              .all())
    out = []
    for a in acts:
        row = _actividad_row(a)
        pr = db.query(Prospecto).filter(Prospecto.id == a.prospecto_id).first()
        row["prospecto_nombre"] = pr.nombre if pr else ""
        out.append(row)
    return out


@router.patch("/prospectos/{id}/contacto-datos")
def actualizar_contacto_prospecto(id: int, request: Request,
                               email: str | None = None, telefono: str | None = None,
                               whatsapp: str | None = None, etiquetas: str | None = None,
                               valor_usd: float | None = None,
                               db: Session = Depends(get_db)):
    """Actualiza contacto estructurado, etiquetas y valor del deal (todos opcionales)."""
    p = _get_prospecto_owned_or_admin(id, request, db)
    if email is not None:     p.email = email.strip() or None
    if telefono is not None:  p.telefono = telefono.strip() or None
    if whatsapp is not None:  p.whatsapp = whatsapp.strip() or None
    if etiquetas is not None: p.etiquetas = etiquetas.strip() or None
    if valor_usd is not None: p.valor_usd = valor_usd
    db.commit()
    return {"mensaje": "Datos actualizados."}


# ── NOTA DE DISEÑO ────────────────────────────────────────────────────────────
# "Marcar ganado" es exclusivamente un cambio de estado de pipeline (CRM).
# NO crea comisión ni registro de venta. Eso lo dispara el flujo de pago
# (link de pago → verificación USDT/transferencia → job_generar_comisiones).
# Mantener separados estos dos conceptos es intencional: un prospecto puede
# cerrarse manualmente sin que el pago haya sido procesado todavía.
# ──────────────────────────────────────────────────────────────────────────────
@router.patch("/prospectos/{id}/ganado")
def marcar_ganado(id: int, request: Request, valor_usd: float | None = None,
                  motivo: str = "", db: Session = Depends(get_db)):
    """Marca el prospecto como GANADO (cierre manual) y lo registra en el timeline."""
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.estado = "ganado"
    p.fecha_cierre = datetime.now()
    if valor_usd is not None:
        p.valor_usd = valor_usd
    if (motivo or "").strip():
        p.motivo_cierre = motivo.strip()
    db.add(ActividadProspecto(
        prospecto_id=p.id, aliado_id=p.aliado_id, tipo="sistema",
        descripcion="Marcado como GANADO" + (f" — {motivo.strip()}" if (motivo or '').strip() else "")))
    db.commit()
    return {"mensaje": "Marcado como ganado.", "estado": p.estado}


@router.post("/prospectos/{id}/registrar-referido")
def registrar_referido_desde_crm(id: int, request: Request, plan: str = "",
                                 notas: str = "", db: Session = Depends(get_db)):
    """Puente CRM → Referido: registra el prospecto para venta en 1 click.

    Crea el Referido (el registro contractual que atribuye la venta al aliado,
    obligatorio ANTES de que el cliente pague) con los datos del prospecto,
    sin volver a tipear nada. Idempotente: si ya fue registrado, devuelve el
    referido existente. Mismas reglas de negocio que /referidos/registrar.
    """
    p = _get_prospecto_owned_or_admin(id, request, db)
    a = db.query(Aliado).filter(Aliado.id == p.aliado_id).first()
    if not a:
        raise HTTPException(404, "Aliado no encontrado.")
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "Referidos no disponibles para aliados Canal 2.")
    if plan not in PLANES:
        raise HTTPException(400, "Plan inválido. Elegí uno de los planes de sistema.")

    # Idempotencia: un prospecto se registra para venta UNA sola vez.
    existente = db.query(Referido).filter(Referido.prospecto_id == p.id).first()
    if existente:
        return {
            "mensaje": "Este lead ya estaba registrado para venta.",
            "id_referido": existente.id,
            "plan": existente.plan_elegido,
            "ya_existia": True,
        }

    notas_final = (notas or "").strip() or f"Registrado desde Mi CRM (lead #{p.id})."
    r = Referido(
        aliado_id=a.id,
        nombre_cliente=p.nombre,
        plan_elegido=plan,
        notas=notas_final,
        prospecto_id=p.id,
    )
    db.add(r)
    if not (p.plan_interes or "").strip():
        p.plan_interes = plan
    db.flush()
    db.add(ActividadProspecto(
        prospecto_id=p.id, aliado_id=p.aliado_id, tipo="sistema",
        descripcion=f"🔒 Registrado para venta — Referido #{r.id} ({plan}). "
                    f"La venta queda atribuida a tu cuenta."))
    db.commit()
    db.refresh(r)
    return {
        "mensaje": f"¡{p.nombre} registrado para venta! La atribución es tuya.",
        "id_referido": r.id,
        "plan": plan,
        "valor_plan": PLANES[plan],
        "comision_estimada": round(PLANES[plan] * a.comision_pct, 2),
        "ya_existia": False,
    }


# ═══════════════════════════════════════════════════════════════════════════
# SALTO 3 — Empresa ↔ contactos: varios interlocutores por prospecto
# ═══════════════════════════════════════════════════════════════════════════
def _contacto_row(c):
    return {
        "id": c.id, "prospecto_id": c.prospecto_id, "nombre": c.nombre,
        "rol": c.rol, "email": c.email, "telefono": c.telefono, "whatsapp": c.whatsapp,
    }


@router.get("/prospectos/{id}/contactos")
def listar_contactos(id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    cs = (db.query(ContactoProspecto)
            .filter(ContactoProspecto.prospecto_id == p.id)
            .order_by(ContactoProspecto.creado_en.asc())
            .all())
    return [_contacto_row(c) for c in cs]


@router.post("/prospectos/{id}/contactos")
def crear_contacto(id: int, request: Request, nombre: str = "", rol: str = "",
                   email: str = "", telefono: str = "", whatsapp: str = "",
                   db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    if not (nombre or "").strip():
        raise HTTPException(400, "El contacto necesita un nombre.")
    c = ContactoProspecto(
        prospecto_id=p.id, nombre=nombre.strip(), rol=(rol.strip() or None),
        email=(email.strip() or None), telefono=(telefono.strip() or None),
        whatsapp=(whatsapp.strip() or None))
    db.add(c); db.commit(); db.refresh(c)
    return {"mensaje": "Contacto agregado.", "contacto": _contacto_row(c)}


@router.delete("/contactos/{contacto_id}")
def eliminar_contacto(contacto_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.query(ContactoProspecto).filter(ContactoProspecto.id == contacto_id).first()
    if not c:
        raise HTTPException(404, "Contacto no encontrado.")
    _get_prospecto_owned_or_admin(c.prospecto_id, request, db)  # valida pertenencia
    db.delete(c); db.commit()
    return {"mensaje": "Contacto eliminado."}


def _prospecto_row(p):
    # --- INTELIGENCIA DE VENTAS: "Next Best Action" ---
    next_action = ""
    action_type = "primary" # Color de la alerta

    if p.estado == "sin_contactar":
        next_action = "🔥 Sugerencia: Romper el hielo. Enviale el link de la Auditoría Gratuita hoy."
        action_type = "amber"
    elif p.estado == "contactado":
        dias = 0
        if p.fecha_contacto:
            dias = (datetime.now() - p.fecha_contacto).days
        
        if dias >= 3:
            next_action = f"⚠️ Se enfría (hace {dias} días). Sugerencia: Mandá un mensaje de seguimiento ('¿Pudiste ver lo que te mandé?')."
            action_type = "red"
        else:
            next_action = "⏳ Esperando respuesta. Aún es pronto para insistir."
            action_type = "text-dim"
    elif p.estado == "respondio":
        next_action = "✅ ¡Lead Caliente! Tu objetivo ahora es llevarlo a una llamada o usar el Cotizador."
        action_type = "green"

    return {
        "id": p.id, "nombre": p.nombre, "contacto": p.contacto,
        "plan_interes": p.plan_interes, "estado": p.estado,
        "nota": p.nota, "interesante": p.interesante,
        "referido_id": p.referido.id if getattr(p, "referido", None) else None,
        "piloto_automatico": getattr(p, "piloto_automatico", False) or False,
        "fecha_contacto":  p.fecha_contacto.strftime("%d/%m/%Y") if p.fecha_contacto else None,
        "fecha_respuesta": p.fecha_respuesta.strftime("%d/%m/%Y") if p.fecha_respuesta else None,
        "creado_en": p.creado_en.strftime("%d/%m/%Y") if p.creado_en else None,
        "next_action": next_action,
        "action_type": action_type,
        # Perfilado IA (A)
        "rubro": getattr(p, "rubro", None),
        "tamano": getattr(p, "tamano", None),
        "urgencia": getattr(p, "urgencia", None),
        "score_ia": getattr(p, "score_ia", 0) or 0,
        "plan_recomendado": getattr(p, "plan_recomendado", None),
        "pitch_sugerido": getattr(p, "pitch_sugerido", None),
        "automation_paso": getattr(p, "automation_paso", 0) or 0,
        # CRM v3.0
        "email": getattr(p, "email", None),
        "telefono": getattr(p, "telefono", None),
        "whatsapp": getattr(p, "whatsapp", None),
        "valor_usd": getattr(p, "valor_usd", None),
        "etiquetas": getattr(p, "etiquetas", None),
        "fecha_cierre": p.fecha_cierre.strftime("%d/%m/%Y") if getattr(p, "fecha_cierre", None) else None,
        "motivo_cierre": getattr(p, "motivo_cierre", None),
        "proxima_accion_en": p.proxima_accion_en.isoformat() if getattr(p, "proxima_accion_en", None) else None,
        "tareas_pendientes": sum(1 for _a in (getattr(p, "actividades", None) or []) if _a.tipo == "tarea" and not _a.completada),
    }


# ─── DATOS DE PERFILADO DEL PROSPECTO ────────────────────────────────────────
# Movido desde main en el tramo 6: es CRM puro (setea rubro/tamaño/urgencia
# sin invocar IA). Vivía pegado al perfilado porque alimenta los campos que
# ese flujo lee, pero el dueño de la entidad Prospecto es este módulo.

@router.patch("/prospectos/{id}/datos")
def actualizar_datos_prospecto(id: int, request: Request,
                               body: schemas.ActualizarDatosProspectoIn | None = Body(default=None),
                               rubro: str = "",
                               tamano: str = "",
                               urgencia: str = "",
                               db: Session = Depends(get_db)):
    """Actualiza rubro/tamaño/urgencia sin perfilar."""
    if body is not None:
        rubro, tamano, urgencia = body.rubro, body.tamano, body.urgencia
    p = _get_prospecto_owned_or_admin(id, request, db)
    if rubro:    p.rubro = rubro
    if tamano:   p.tamano = tamano
    if urgencia: p.urgencia = urgencia
    db.commit()
    return {"mensaje": "Datos actualizados."}
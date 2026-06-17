"""
bolsa.py — Bolsa de Leads completa (dominio LeadBolsa).

Sexto router migrado de main.py (tramo 4 del split). Contiene:
  - Admin: carga simple (/admin/bolsa) y con tier (/admin/bolsa-v2), monitor
    con KPIs, revocar, borrado individual/bulk/total, verificación de
    duplicados para la preview del importador CSV y carga masiva con
    dedupe servidor (mismo teléfono, o misma empresa+país) + digest único
    por aliado. Todos con Depends(current_admin_required) explícito además
    del middleware (mismo criterio que academia/comunidad/mal_contacto).
  - Aliado: ver bolsa (tier básico) y marketplace (calificado/premium),
    reclamar con límite de 3 activos, /comprar (hoy reclamo gratuito con
    claim atómico anti-TOCTOU — los créditos quedaron solo para Jarvis IA),
    marcar contactado con auto-conversión al CRM en "exitoso", puente
    Bolsa → CRM en 1 click (idempotente) e historiales (aliado y admin).
  - La REGLA DE ORO (_aplicar_caducidad_bolsa): los leads reclamados >48h
    sin contactar vuelven a estar disponibles. main.py la importa diferido
    para /aliados/{codigo}/siguiente-accion.
  - LIMITE_RECLAMOS_ACTIVOS vive acá; main.py lo re-exporta por
    compatibilidad (tests/test_creditos.py lo importa de main).

Los endpoints de IA sobre la bolsa (perfilar-ia, mensaje-outreach) siguen
en main.py — dependen del stack Jarvis/Groq y migran con ese dominio.

Helpers compartidos de main (_get_aliado, PORTAL_URL) se acceden por import
diferido para evitar el ciclo main → bolsa → main.
"""
import sys
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import jarvis_canal1
import schemas
from auth import current_aliado_required, current_admin_required, verify_ownership_dep
from database import get_db
from models import Aliado, LeadBolsa, Prospecto, ActividadProspecto
from notificaciones import enviar_email

router = APIRouter(tags=["bolsa"])


# ── Puente diferido a helpers de main (evita import circular) ────────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def _tier_badge(tier: str) -> str:
    if tier == 'calificado':
        return '<span style="color:#fbbf24;">⭐</span>'
    elif tier == 'premium':
        return '<span style="color:#a78bfa;">💎</span>'
    return ''


# ─── BOLSA DE LEADS (ADMIN) ──────────────────────────────────────────────────

class LeadBolsaCreate(BaseModel):
    empresa: str
    rubro: str
    telefono: str
    email: str = ""

def _aplicar_caducidad_bolsa(db: Session):
    """LA REGLA DE ORO: Libera los leads reclamados hace más de 48h sin contactar"""
    limite = datetime.now() - timedelta(hours=48)
    vencidos = db.query(LeadBolsa).filter(
        LeadBolsa.estado == "reclamado",
        LeadBolsa.fecha_reclamo < limite
    ).all()
    
    for lead in vencidos:
        lead.estado = "disponible"
        lead.aliado_id = None
        lead.fecha_reclamo = None
    
    if vencidos:
        db.commit()

def _notificar_nuevo_lead_bolsa(db: Session, empresa: str, rubro: str, tier: str = "basico"):
    """Broadcast a todos los aliados Canal 1 activos con email cuando entra un lead nuevo."""
    from main import PORTAL_URL  # diferido: const de main (evita import circular)
    try:
        aliados = db.query(Aliado).filter(
            Aliado.activo == True,
            Aliado.email != None,
            Aliado.email != "",
            (Aliado.tipo_aliado == "canal1") | (Aliado.tipo_aliado == None),
        ).all()

        if not aliados:
            return

        tier_badge = {"calificado": "⭐ Calificado", "premium": "💎 Premium"}.get(tier, "")
        tier_line = f"<p style=\"margin:4px 0;\"><strong>Tier:</strong> {tier_badge}</p>" if tier_badge else ""

        for aliado in aliados:
            nombre = (aliado.nombre or "").split()[0] or "Aliado"
            html = f"""
            <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
              <h2 style="color:#4ade80;margin-bottom:8px;">🔔 Nuevo lead en la bolsa</h2>
              <p>Hola <strong>{nombre}</strong>, acaba de entrar un lead disponible para reclamar.</p>
              <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:16px;margin:16px 0;">
                <p style="margin:4px 0;"><strong>Empresa:</strong> {empresa}</p>
                <p style="margin:4px 0;"><strong>Rubro:</strong> {rubro or '—'}</p>
                {tier_line}
              </div>
              <p style="color:#94a3b8;font-size:.9rem;">Los leads se asignan al primero en reclamarlos. Entrá ahora para no perderlo.</p>
              <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ver la bolsa →</a>
              <p style="margin-top:24px;font-size:.8rem;color:#64748b;">Avanza Digital · Partner Network</p>
            </div>
            """
            enviar_email(aliado.email, f"🔔 Avanza: nuevo lead disponible — {empresa}", html)

        print(f"[NUEVO LEAD] Broadcast enviado a {len(aliados)} aliado(s) — empresa: {empresa}")
    except Exception as e:
        print(f"[NUEVO LEAD NOTIF ERROR] {e}")


@router.post("/admin/bolsa")
def cargar_lead_bolsa(lead: LeadBolsaCreate, db: Session = Depends(get_db),
                      _admin=Depends(current_admin_required)):
    nuevo = LeadBolsa(
        empresa=lead.empresa,
        rubro=lead.rubro,
        telefono=lead.telefono,
        email=lead.email,
        estado="disponible"
    )
    db.add(nuevo)
    db.commit()
    _notificar_nuevo_lead_bolsa(db, lead.empresa, lead.rubro)
    return {"mensaje": "Lead subido a la bolsa."}

@router.get("/admin/bolsa")
def monitor_bolsa(db: Session = Depends(get_db),
                  _admin=Depends(current_admin_required)):
    # 1. Limpiamos los leads vencidos antes de mostrar la data
    _aplicar_caducidad_bolsa(db) 
    
    # 2. Traemos todos los leads
    leads = db.query(LeadBolsa).order_by(LeadBolsa.fecha_carga.desc()).all()
    
    # 3. Calculamos KPIs
    total = len(leads)
    disponibles = sum(1 for l in leads if l.estado == "disponible")
    reclamados = sum(1 for l in leads if l.estado == "reclamado")
    contactados = sum(1 for l in leads if l.estado == "contactado")
    
    tasa = round((contactados / (reclamados + contactados)) * 100) if (reclamados + contactados) > 0 else 0

    # 4. Formateamos la tabla
    detalle = []
    for l in leads:
        tiempo_txt = ""
        if l.estado == "reclamado" and l.fecha_reclamo:
            horas = (datetime.now() - l.fecha_reclamo).total_seconds() / 3600
            tiempo_txt = f"{int(horas)}h / 48h"
            
        detalle.append({
            "id": l.id,
            "empresa": l.empresa,
            "rubro": l.rubro,
            "estado": l.estado,
            "asignado_a": l.aliado.nombre if l.aliado else None,
            "tiempo_transcurrido": tiempo_txt
        })

    return {
        "kpis": {
            "total": total,
            "disponibles": disponibles,
            "reclamados": reclamados,
            "tasa_contacto": tasa
        },
        "leads": detalle
    }

@router.post("/admin/bolsa/{id}/revocar")
def revocar_lead_bolsa(id: int, db: Session = Depends(get_db),
                       _admin=Depends(current_admin_required)):
    """Modo Dios: El admin quita el lead manualmente"""
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    
    lead.estado = "disponible"
    lead.aliado_id = None
    lead.fecha_reclamo = None
    db.commit()
    return {"mensaje": "Lead revocado con éxito"}


class BulkDeleteLeads(BaseModel):
    ids: list[int]


@router.delete("/admin/bolsa/bulk")
def eliminar_leads_bulk(payload: BulkDeleteLeads, db: Session = Depends(get_db),
                        _admin=Depends(current_admin_required)):
    """Elimina permanentemente múltiples leads de la bolsa (y los quita de los aliados que los tenían)."""
    deleted = 0
    for lead_id in payload.ids:
        lead = db.query(LeadBolsa).filter(LeadBolsa.id == lead_id).first()
        if lead:
            db.delete(lead)
            deleted += 1
    db.commit()
    return {"eliminados": deleted}


@router.delete("/admin/bolsa/all")
def eliminar_todos_los_leads(db: Session = Depends(get_db),
                             _admin=Depends(current_admin_required)):
    """Elimina TODOS los leads de la bolsa de una sola vez.
    Al borrar los registros LeadBolsa, desaparecen automáticamente de la
    bolsa de cualquier aliado que los tuviera reclamados (aliado_id queda huérfano)."""
    result = db.query(LeadBolsa).delete()
    db.commit()
    return {"eliminados": result, "mensaje": f"{result} lead(s) eliminados de la bolsa."}


@router.delete("/admin/bolsa/{id}")
def eliminar_lead_bolsa(id: int, db: Session = Depends(get_db),
                        _admin=Depends(current_admin_required)):
    """Elimina permanentemente un lead de la bolsa (se quita también de la bolsa de cualquier aliado que lo tuviera)."""
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    db.delete(lead)
    db.commit()
    return {"mensaje": "Lead eliminado."}


# ─── BOLSA DE LEADS (PORTAL ALIADO) ──────────────────────────────────────────

@router.get("/aliados/{codigo}/bolsa")
def ver_bolsa_aliado(codigo: str, pais: str = "", db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Muestra los leads disponibles y los que este aliado ya reclamó."""
    a = _get_aliado(codigo, db)
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "La bolsa de leads no está disponible para aliados Canal 2.")
    _aplicar_caducidad_bolsa(db) # Limpiamos antes de mostrar
    
    q_disponibles = db.query(LeadBolsa).filter(
        LeadBolsa.estado == "disponible",
        LeadBolsa.tier == "basico"
    )
    if pais:
        q_disponibles = q_disponibles.filter(LeadBolsa.pais == pais.upper())
    disponibles = q_disponibles.all()
    mis_reclamos = db.query(LeadBolsa).filter(LeadBolsa.aliado_id == a.id).order_by(LeadBolsa.fecha_reclamo.desc()).all()
    
    reclamos_formateados = []
    for l in mis_reclamos:
        horas_restantes = 0
        if l.estado == "reclamado" and l.fecha_reclamo:
            horas_pasadas = (datetime.now() - l.fecha_reclamo).total_seconds() / 3600
            horas_restantes = max(0, 48 - int(horas_pasadas))
            
        reclamos_formateados.append({
            "id": l.id, "empresa": l.empresa, "rubro": l.rubro,
            "nombre_contacto": l.nombre_contacto, "ciudad": l.ciudad,
            "telefono": l.telefono, "whatsapp": l.whatsapp, "email": l.email,
            "estado": l.estado, "horas_restantes": horas_restantes,
            "prospecto_id": l.prospecto_id,  # CRM bridge: != None si ya se convirtió
            # v1.6 — presencia digital
            "web": l.web, "instagram": l.instagram,
            "tiene_web": bool(l.tiene_web), "tiene_redes": bool(l.tiene_redes),
            "observacion": l.observacion,
        })
        
    return {
        "disponibles": [
            {
                "id": l.id, "empresa": l.empresa, "rubro": l.rubro,
                "ciudad": l.ciudad or "", "pais": l.pais or "AR",
                "tier": l.tier,
                "score_calidad": l.score_calidad,
                "costo_creditos": l.costo_creditos,
                # Teasers — mismos que en /bolsa/marketplace para que el front
                # use UN SOLO componente de tarjeta. Nunca exponer URLs/contacto.
                "tiene_web":         bool(l.tiene_web),
                "tiene_redes":       bool(l.tiene_redes),
                "tiene_contacto":    bool(l.nombre_contacto),
                "tiene_observacion": bool((l.observacion or "").strip()),
                "observacion":       l.observacion or "",
            }
            for l in disponibles
        ],
        "mis_reclamos": reclamos_formateados,
        "reclamos_activos": sum(1 for r in reclamos_formateados if r["estado"] == "reclamado"),
        "limite_reclamos": 3
    }

LIMITE_RECLAMOS_ACTIVOS = 3  # Máximo de reclamos simultáneos por aliado

@router.post("/bolsa/{id}/reclamar")
def reclamar_lead(id: int,
                  codigo_aliado: str = "",  # legacy compat
                  aliado: Aliado = Depends(current_aliado_required),
                  db: Session = Depends(get_db)):
    """Reclama un lead para el aliado autenticado.

    SECURITY: ya NO acepta `codigo_aliado` para asignar a otro aliado.
    Siempre usa el aliado del JWT.
    """
    a = aliado  # del token, no del query
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "Operación no disponible para aliados Canal 2.")

    # Verificar límite de reclamos activos simultáneos
    reclamos_activos = db.query(LeadBolsa).filter(
        LeadBolsa.aliado_id == a.id,
        LeadBolsa.estado == "reclamado"
    ).count()
    if reclamos_activos >= LIMITE_RECLAMOS_ACTIVOS:
        raise HTTPException(400, f"Límite alcanzado: ya tenés {LIMITE_RECLAMOS_ACTIVOS} leads reclamados activos. Marcá al menos uno como contactado antes de reclamar otro.")

    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id, LeadBolsa.estado == "disponible").first()
    if not lead:
        raise HTTPException(400, "El lead ya no está disponible. ¡Alguien fue más rápido!")

    lead.estado = "reclamado"
    lead.aliado_id = a.id
    lead.fecha_reclamo = datetime.now()
    db.commit()

    # WhatsApp Canal 1: si es el primer lead del aliado, mandar los 3 tips
    _n_leads = db.query(LeadBolsa).filter(LeadBolsa.aliado_id == a.id).count()
    if _n_leads == 1:
        try:
            jarvis_canal1.notificar_primer_lead(a, db)
        except Exception as _e:
            print(f"[CANAL1] Error notif primer lead: {_e}", file=sys.stderr)

    return {"mensaje": "¡Lead reclamado exitosamente!"}

@router.patch("/bolsa/{id}/contactar")
def contactar_lead_bolsa(id: int,
                         body: schemas.ContactarLeadIn | None = Body(default=None),
                         codigo_aliado: str = "",  # legacy
                         resultado: str = "exitoso",
                         aliado: Aliado = Depends(current_aliado_required),
                         db: Session = Depends(get_db)):
    """Marca un lead (que pertenece al aliado autenticado) como contactado."""
    if body is not None:
        resultado = body.resultado
    a = aliado
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "La bolsa de leads no está disponible para aliados Canal 2.")
    RESULTADOS_VALIDOS = {"exitoso", "no_interesado", "no_contesto"}
    if resultado not in RESULTADOS_VALIDOS:
        raise HTTPException(400, f"Resultado inválido. Opciones: {', '.join(RESULTADOS_VALIDOS)}")

    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id, LeadBolsa.aliado_id == a.id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado o no te pertenece.")

    lead.estado    = "contactado"
    lead.resultado = resultado

    # ── AUTO-CONVERSIÓN AL CRM ────────────────────────────────────────────────
    # Un contacto "exitoso" significa que hay una venta en marcha: el lead pasa
    # solo a Mi CRM como prospecto para trabajarlo con etapas, notas y tareas.
    # Defensivo: si la conversión falla por lo que sea, el marcado de contacto
    # se guarda igual (la conversión manual sigue disponible desde la tarjeta).
    convertido_a_crm = False
    prospecto_id = lead.prospecto_id
    if resultado == "exitoso" and not lead.prospecto_id:
        try:
            p = _crear_prospecto_desde_lead(db, a, lead)
            prospecto_id = p.id
            convertido_a_crm = True
        except Exception as e:
            print(f"[CRM AUTO-CONVERSIÓN] Falló para lead {lead.id}: {e}")

    db.commit()

    mensajes = {
        "exitoso":       ("¡Excelente! Lead marcado como exitoso y agregado a Mi CRM — seguilo desde ahí con etapas, notas y tareas."
                          if convertido_a_crm else
                          "¡Excelente! Lead marcado como exitoso. ¡A cerrar la venta!"),
        "no_interesado": "Anotado. El lead quedó marcado como no interesado.",
        "no_contesto":   "Anotado. Si conseguís contactarlo después, podés actualizar el estado.",
    }
    return {
        "mensaje": mensajes[resultado],
        "convertido_a_crm": convertido_a_crm,
        "prospecto_id": prospecto_id,
    }


def _crear_prospecto_desde_lead(db: Session, a: Aliado, lead: LeadBolsa) -> Prospecto:
    """Crea el Prospecto del CRM a partir de un lead de la bolsa, copia los datos
    de contacto, deja la actividad de sistema en el timeline y vincula el lead.
    NO hace commit (el caller decide la transacción)."""
    partes_nota = [f"Origen: Bolsa de Leads (lead #{lead.id}, tier {lead.tier or 'basico'})."]
    if lead.observacion:
        partes_nota.append(f"Observación del prospectador: {lead.observacion}")
    if lead.web:
        partes_nota.append(f"Web: {lead.web}")
    if lead.instagram:
        partes_nota.append(f"Redes: {lead.instagram}")

    ya_contactado = lead.estado == "contactado"
    p = Prospecto(
        aliado_id = a.id,
        nombre    = lead.empresa,
        contacto  = lead.nombre_contacto or lead.telefono or "",
        rubro     = lead.rubro,
        nota      = "\n".join(partes_nota),
        estado    = "contactado" if ya_contactado else "sin_contactar",
        fecha_contacto = datetime.now() if ya_contactado else None,
        # CRM v3.0 — contacto estructurado
        telefono  = lead.telefono,
        whatsapp  = lead.whatsapp or lead.telefono,
        email     = lead.email,
        # Atribucion de equipo (si el lead vino de un handoff setter->closer)
        setter_id = getattr(lead, "setter_id", None),
        setter_split_pct = getattr(lead, "setter_split_pct", None),
    )
    db.add(p)
    db.flush()  # necesitamos p.id antes del commit

    db.add(ActividadProspecto(
        prospecto_id = p.id,
        aliado_id    = a.id,
        tipo         = "sistema",
        descripcion  = f"Creado desde la Bolsa de Leads — {lead.empresa} ({lead.rubro or 's/rubro'}, tier {lead.tier or 'basico'}).",
    ))

    lead.prospecto_id = p.id
    return p


@router.post("/bolsa/{id}/convertir-prospecto")
def convertir_lead_en_prospecto(id: int,
                                aliado: Aliado = Depends(current_aliado_required),
                                db: Session = Depends(get_db)):
    """CRM bridge: convierte un lead reclamado de la bolsa en un Prospecto del
    CRM en un click, copiando empresa/contacto/teléfono/email/rubro/observación.

    - Idempotente: si el lead ya fue convertido, devuelve el prospecto existente.
    - Marca el lead como 'contactado' (sale del contador de 48hs y libera cupo).
    - Deja una actividad de sistema en el timeline del prospecto con el origen.
    """
    a = aliado
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "La bolsa de leads no está disponible para aliados Canal 2.")

    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")
    if lead.aliado_id != a.id:
        raise HTTPException(403, "Este lead no es tuyo.")

    # Idempotencia: si ya existe el prospecto vinculado, devolverlo.
    if lead.prospecto_id:
        existente = db.query(Prospecto).filter(Prospecto.id == lead.prospecto_id).first()
        if existente:
            return {
                "mensaje": "Este lead ya estaba en tu CRM.",
                "prospecto_id": existente.id,
                "ya_existia": True,
            }
        lead.prospecto_id = None  # el prospecto fue borrado: permitir reconvertir

    p = _crear_prospecto_desde_lead(db, a, lead)

    # El lead queda gestionado: sale del reloj de 48hs y libera el cupo de reclamos.
    if lead.estado == "reclamado":
        lead.estado = "contactado"
        if not lead.resultado:
            lead.resultado = "exitoso"

    db.commit()
    db.refresh(p)

    return {
        "mensaje": f"¡{lead.empresa} ya está en tu CRM!",
        "prospecto_id": p.id,
        "ya_existia": False,
    }



@router.get("/aliados/{codigo}/historial-bolsa")
def historial_bolsa_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Historial completo de leads de un aliado con estadísticas."""
    a = _get_aliado(codigo, db)
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "La bolsa de leads no está disponible para aliados Canal 2.")
    leads = db.query(LeadBolsa).filter(LeadBolsa.aliado_id == a.id).order_by(LeadBolsa.fecha_reclamo.desc()).all()

    total          = len(leads)
    exitosos       = sum(1 for l in leads if l.resultado == "exitoso")
    no_interesados = sum(1 for l in leads if l.resultado == "no_interesado")
    no_contestaron = sum(1 for l in leads if l.resultado == "no_contesto")
    activos        = sum(1 for l in leads if l.estado == "reclamado")
    tasa_exito     = round((exitosos / total * 100), 1) if total else 0

    return {
        "stats": {
            "total_reclamados": total,
            "exitosos": exitosos,
            "no_interesados": no_interesados,
            "no_contestaron": no_contestaron,
            "activos": activos,
            "tasa_exito": tasa_exito,
        },
        "leads": [
            {
                "id": l.id,
                "empresa": l.empresa,
                "rubro": l.rubro,
                "telefono": l.telefono,
                "estado": l.estado,
                "resultado": l.resultado,
                "fecha_reclamo": l.fecha_reclamo.strftime("%d/%m/%Y %H:%M") if l.fecha_reclamo else None,
            }
            for l in leads
        ]
    }


@router.get("/admin/historial-bolsa")
def historial_bolsa_admin(db: Session = Depends(get_db),
                          _admin=Depends(current_admin_required)):
    """Admin: resumen de rendimiento de todos los aliados en la bolsa."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    resumen = []
    for a in aliados:
        leads = [l for l in a.leads_bolsa]
        total = len(leads)
        if total == 0:
            continue
        exitosos = sum(1 for l in leads if l.resultado == "exitoso")
        resumen.append({
            "codigo": a.codigo,
            "nombre": a.nombre,
            "total_reclamados": total,
            "exitosos": exitosos,
            "no_interesados": sum(1 for l in leads if l.resultado == "no_interesado"),
            "no_contestaron": sum(1 for l in leads if l.resultado == "no_contesto"),
            "activos": sum(1 for l in leads if l.estado == "reclamado"),
            "tasa_exito": round(exitosos / total * 100, 1) if total else 0,
        })
    resumen.sort(key=lambda x: x["exitosos"], reverse=True)
    return {"aliados": resumen}


@router.get("/bolsa/marketplace")
def ver_marketplace(codigo_aliado: str = "",
                    pais: str = "",
                    aliado: Aliado = Depends(current_aliado_required),
                    db: Session = Depends(get_db)):
    """Lista los leads calificados/premium disponibles para reclamar GRATIS.

    Los leads ya no consumen créditos (los créditos son sólo para Jarvis IA),
    así que el costo viaja siempre en 0 por compatibilidad con el front.
    SECURITY: usa el aliado del JWT, no acepta `codigo_aliado` para spoofing.
    """
    a = aliado  # del JWT
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "El marketplace de leads no está disponible para aliados Canal 2.")
    _aplicar_caducidad_bolsa(db)
    q = db.query(LeadBolsa).filter(
        LeadBolsa.estado == "disponible",
        LeadBolsa.tier.in_(["calificado", "premium"])
    )
    if pais:
        q = q.filter(LeadBolsa.pais == pais.upper())
    leads = q.order_by(LeadBolsa.score_calidad.desc(), LeadBolsa.fecha_carga.desc()).all()

    return {
        "saldo_creditos": a.creditos or 0,
        "leads": [
            {
                "id": l.id,
                "empresa": l.empresa,
                "rubro": l.rubro,
                "ciudad": l.ciudad or "",
                "pais": l.pais or "AR",
                "tier": l.tier,
                "costo_creditos": 0,  # leads gratis: el costo quedó deprecado
                "score_calidad": l.score_calidad or 50,
                # Notas internas del admin que califica — texto público para el aliado
                # va por `observacion`. Mantenemos `notas` por compatibilidad con
                # el front viejo, pero el front nuevo debería leer `observacion`.
                "notas": l.notas_calificacion or "",
                "observacion": l.observacion or "",
                # ── TEASERS DE PRESENCIA DIGITAL Y ENRIQUECIMIENTO ─────────────
                # Booleans que el front muestra como pills "✓ Web", "✓ Redes",
                # etc. NUNCA exponer las URLs ni el nombre del contacto antes
                # de la compra — eso se desbloquea solo en /bolsa/{id}/comprar.
                "tiene_web":         bool(l.tiene_web),
                "tiene_redes":       bool(l.tiene_redes),
                "tiene_contacto":    bool(l.nombre_contacto),
                "tiene_observacion": bool((l.observacion or "").strip()),
            }
            for l in leads
        ]
    }


@router.post("/bolsa/{id}/comprar")
def comprar_lead(id: int,
                 background_tasks: BackgroundTasks,
                 codigo_aliado: str = "",  # legacy
                 aliado: Aliado = Depends(current_aliado_required),
                 db: Session = Depends(get_db)):
    """Reclama un lead calificado/premium SIN costo en créditos.

    Histórico: este endpoint cobraba créditos por los leads calificados y
    premium. Desde la unificación de la bolsa TODOS los leads son gratis y los
    créditos quedaron reservados exclusivamente para Jarvis IA. Mantenemos el
    endpoint (y la ruta `/comprar`) por compatibilidad con el front y los links
    viejos, pero ahora se comporta como un reclamo gratuito que desbloquea el
    contacto del lead.
    """
    a = aliado
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "El marketplace de leads no está disponible para aliados Canal 2.")
    lead = db.query(LeadBolsa).filter(
        LeadBolsa.id == id, LeadBolsa.estado == "disponible"
    ).first()
    if not lead:
        raise HTTPException(400, "Ese lead ya no está disponible.")

    reclamos_activos = db.query(LeadBolsa).filter(
        LeadBolsa.aliado_id == a.id, LeadBolsa.estado == "reclamado"
    ).count()
    if reclamos_activos >= LIMITE_RECLAMOS_ACTIVOS:
        raise HTTPException(400, f"Ya tenés {LIMITE_RECLAMOS_ACTIVOS} leads reclamados activos. Marcá al menos uno como contactado antes de reclamar otro.")

    # --- CLAIM ATÓMICO DEL LEAD (anti-TOCTOU) ─────────────────────────────────
    # Dos aliados pueden haber pasado las validaciones de arriba al mismo tiempo.
    # El UPDATE condicional WHERE estado='disponible' falla con rowcount=0 para
    # el segundo, así sólo uno se queda con el lead.
    from sqlalchemy import update as _sa_update
    res_claim = db.execute(
        _sa_update(LeadBolsa)
        .where(LeadBolsa.id == id, LeadBolsa.estado == "disponible")
        .values(
            estado="reclamado",
            aliado_id=a.id,
            fecha_reclamo=datetime.now(),
        )
    )
    if res_claim.rowcount == 0:
        # Otro aliado nos ganó de mano (race condition).
        raise HTTPException(409, "Otro aliado acaba de reclamar este lead — refrescá la bolsa.")

    db.commit()
    db.refresh(lead)

    return {
        "mensaje": "¡Lead reclamado gratis! Ya tenés el contacto desbloqueado.",
        "saldo_restante": a.creditos,
        "lead": {
            "id": lead.id, "empresa": lead.empresa, "rubro": lead.rubro,
            "telefono": lead.telefono, "email": lead.email,
        }
    }



class LeadBolsaCreateAdv(BaseModel):
    empresa: str
    rubro: str
    nombre_contacto: str = ""
    ciudad: str = ""
    pais: str = "AR"
    telefono: str
    whatsapp: str = ""
    email: str = ""
    tier: str = "basico"            # basico | calificado | premium
    costo_creditos: int = 0
    score_calidad: int = 50
    notas_calificacion: str = ""
    # v1.6 — presencia digital
    web: Optional[str] = None
    instagram: Optional[str] = None
    tiene_web: bool = False
    tiene_redes: bool = False
    observacion: Optional[str] = None


@router.post("/admin/bolsa-v2")
def cargar_lead_bolsa_v2(lead: LeadBolsaCreateAdv, db: Session = Depends(get_db),
                         _admin=Depends(current_admin_required)):
    """Carga un lead con tier/costo. Reemplaza a /admin/bolsa cuando querés tier."""
    if lead.tier not in ("basico", "calificado", "premium"):
        raise HTTPException(400, "Tier inválido. Usá: basico | calificado | premium")
    nuevo = LeadBolsa(
        empresa=lead.empresa, rubro=lead.rubro,
        nombre_contacto=lead.nombre_contacto or None,
        ciudad=lead.ciudad or None,
        pais=lead.pais or "AR",
        telefono=lead.telefono,
        whatsapp=lead.whatsapp or None,
        email=lead.email or None,
        estado="disponible",
        tier=lead.tier, costo_creditos=0,  # leads gratis: el costo quedó deprecado
        score_calidad=lead.score_calidad, notas_calificacion=lead.notas_calificacion,
        # v1.6 — presencia digital
        web=lead.web or None,
        instagram=lead.instagram or None,
        tiene_web=bool(lead.tiene_web),
        tiene_redes=bool(lead.tiene_redes),
        observacion=lead.observacion or None,
    )
    db.add(nuevo); db.commit()
    _notificar_nuevo_lead_bolsa(db, lead.empresa, lead.rubro, lead.tier)
    return {"mensaje": f"Lead cargado en tier '{lead.tier}'."}



# ─── BOLSA: CARGA MASIVA (CSV) ───────────────────────────────────────────────

def _claves_duplicado_lead(empresa: str, telefono: str, pais: str) -> set:
    """Claves de matching para detectar leads duplicados:
       - mismo teléfono (solo dígitos, si tiene >= 6) en cualquier país
       - misma empresa (normalizada) dentro del mismo país
    """
    import re as _re
    claves = set()
    tel = _re.sub(r"\D", "", telefono or "")
    if len(tel) >= 6:
        claves.add(("tel", tel))
    emp = (empresa or "").strip().lower()
    if emp:
        claves.add(("emp", emp, (pais or "AR").upper()))
    return claves


def _indice_duplicados_bolsa(db: Session) -> set:
    """Construye el set de claves de TODOS los leads ya existentes en la bolsa
    (cualquier estado: un lead reclamado o contactado sigue siendo el mismo
    contacto — recargarlo duplicaría el trabajo de los aliados)."""
    indice = set()
    for emp, tel, pais in db.query(LeadBolsa.empresa, LeadBolsa.telefono, LeadBolsa.pais).all():
        indice |= _claves_duplicado_lead(emp, tel, pais)
    return indice


class VerificarDuplicadosItem(BaseModel):
    empresa: str = ""
    telefono: str = ""
    pais: str = "AR"


class VerificarDuplicadosPayload(BaseModel):
    leads: list[VerificarDuplicadosItem]


@router.post("/admin/bolsa/verificar-duplicados")
def verificar_duplicados_bolsa(payload: VerificarDuplicadosPayload,
                               db: Session = Depends(get_db),
                               _admin=Depends(current_admin_required)):
    """Para la preview del importador CSV: devuelve, por cada lead enviado,
    si ya existe en la bolsa (mismo teléfono, o misma empresa en el mismo país).
    También marca duplicados DENTRO del propio lote (filas repetidas en el CSV)."""
    indice = _indice_duplicados_bolsa(db)
    vistos_lote = set()
    out = []
    for item in payload.leads:
        claves = _claves_duplicado_lead(item.empresa, item.telefono, item.pais)
        es_dup = bool(claves & indice) or bool(claves & vistos_lote)
        out.append(es_dup)
        vistos_lote |= claves
    return {"duplicados": out, "total_en_bolsa": db.query(LeadBolsa).count()}


class LeadBolsaBulkPayload(BaseModel):
    leads: list[LeadBolsaCreateAdv]

@router.post("/admin/bolsa/bulk")
def cargar_leads_bulk(payload: LeadBolsaBulkPayload, db: Session = Depends(get_db),
                      _admin=Depends(current_admin_required)):
    """Inserta una lista de leads de una vez y manda UN solo digest a los aliados.

    Defensa anti-duplicados: los leads que ya existen en la bolsa (mismo
    teléfono, o misma empresa+país) o que se repiten dentro del propio lote se
    OMITEN y se reportan en la respuesta — así subir dos veces el mismo CSV no
    ensucia la bolsa."""
    from main import PORTAL_URL  # diferido: const de main (evita import circular)
    if not payload.leads:
        raise HTTPException(400, "La lista de leads está vacía.")

    indice = _indice_duplicados_bolsa(db)
    insertados = []
    omitidos = []
    for lead in payload.leads:
        claves = _claves_duplicado_lead(lead.empresa, lead.telefono, lead.pais)
        if claves & indice:
            omitidos.append(lead.empresa)
            continue
        indice |= claves  # también deduplica dentro del mismo lote
        tier = lead.tier if lead.tier in ("basico", "calificado", "premium") else "basico"
        nuevo = LeadBolsa(
            empresa=lead.empresa, rubro=lead.rubro,
            nombre_contacto=lead.nombre_contacto or None,
            ciudad=lead.ciudad or None,
            pais=lead.pais or "AR",
            telefono=lead.telefono,
            whatsapp=lead.whatsapp or None,
            email=lead.email or None,
            estado="disponible",
            tier=tier, costo_creditos=0,  # leads gratis: el costo quedó deprecado
            score_calidad=lead.score_calidad, notas_calificacion=lead.notas_calificacion,
            # v1.6 — presencia digital
            web=lead.web or None,
            instagram=lead.instagram or None,
            tiene_web=bool(lead.tiene_web),
            tiene_redes=bool(lead.tiene_redes),
            observacion=lead.observacion or None,
        )
        db.add(nuevo)
        insertados.append(lead)

    if not insertados and omitidos:
        # Nada nuevo: todo el lote ya estaba en la bolsa.
        return {
            "mensaje": f"No se insertó ningún lead: los {len(omitidos)} del lote ya estaban en la bolsa.",
            "total": 0,
            "duplicados_omitidos": len(omitidos),
            "duplicados_detalle": omitidos[:50],
        }

    db.commit()

    # Un solo email por aliado con el resumen de todos los leads nuevos
    try:
        aliados = db.query(Aliado).filter(
            Aliado.activo == True,
            Aliado.email != None,
            Aliado.email != "",
            (Aliado.tipo_aliado == "canal1") | (Aliado.tipo_aliado == None),
        ).all()

        if aliados:
            filas_html = "".join(
                f"<tr style='border-bottom:1px solid #1e293b;'>"
                f"<td style='padding:8px 12px;font-weight:600;'>{l.empresa}</td>"
                f"<td style='padding:8px 12px;color:#94a3b8;'>{l.rubro or '—'}</td>"
                f"<td style='padding:8px 12px;'>"
                f"{_tier_badge(l.tier)}"
                f"</td></tr>"
                for l in insertados
            )
            for aliado in aliados:
                nombre = (aliado.nombre or "").split()[0] or "Aliado"
                html = f"""
                <div style="font-family:sans-serif;max-width:580px;margin:auto;padding:32px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
                  <h2 style="color:#4ade80;margin-bottom:4px;">🔔 {len(insertados)} leads nuevos en la bolsa</h2>
                  <p>Hola <strong>{nombre}</strong>, acaban de cargarse oportunidades disponibles para reclamar.</p>
                  <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:.9rem;">
                    <thead>
                      <tr style="background:#1e293b;color:#94a3b8;text-align:left;">
                        <th style="padding:8px 12px;">Empresa</th>
                        <th style="padding:8px 12px;">Rubro</th>
                        <th style="padding:8px 12px;">Tier</th>
                      </tr>
                    </thead>
                    <tbody>{filas_html}</tbody>
                  </table>
                  <p style="color:#94a3b8;font-size:.9rem;">Los leads se asignan al primero en reclamarlos.</p>
                  <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ver la bolsa →</a>
                  <p style="margin-top:24px;font-size:.8rem;color:#64748b;">Avanza Digital · Partner Network</p>
                </div>
                """
                enviar_email(aliado.email, f"🔔 Avanza: {len(insertados)} leads nuevos disponibles", html)

            print(f"[BULK LEAD] {len(insertados)} leads insertados. Digest enviado a {len(aliados)} aliado(s).")
    except Exception as e:
        print(f"[BULK LEAD NOTIF ERROR] {e}")

    return {
        "mensaje": f"{len(insertados)} leads cargados."
                   + (f" {len(omitidos)} duplicado(s) omitido(s)." if omitidos else ""),
        "total": len(insertados),
        "duplicados_omitidos": len(omitidos),
        "duplicados_detalle": omitidos[:50],
    }
"""
mal_contacto.py — Reportes de mal contacto sobre leads de la bolsa.

Tercer router migrado de main.py (tramo 2 del split). Flujo: el aliado dueño
de un lead reporta dentro de las 72hs que el contacto era inválido; el admin
revisa y, si aprueba, descarta el lead y libera el cupo de reclamo (y
reintegra créditos solo en leads históricos pagos — los leads nuevos son
gratis). Esto mantiene la confianza en el marketplace.

Helpers compartidos de main (_get_aliado, _ajustar_creditos, PORTAL_URL) se
acceden por import diferido — patrón documentado en academia.py.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import current_aliado_required, current_admin_required, verify_ownership_dep
from database import get_db
from models import Aliado, LeadBolsa, ReporteMalContacto
from notificaciones import ADMIN_EMAIL, enviar_email

router = APIRouter(tags=["mal-contacto"])


# ── Puentes diferidos a helpers de main (evitan import circular) ─────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def _ajustar_creditos(db, aliado, delta, motivo, ref=""):
    from main import _ajustar_creditos as f
    return f(db, aliado, delta, motivo, ref)


# ─── REPORTE DE MAL CONTACTO (devolución de créditos) ────────────────────────
# Si un aliado compra un lead premium y resulta que el contacto es inválido,
# puede reportarlo dentro de las 72hs. El admin valida y, si aprueba, devuelve
# 100% de los créditos. Esto mantiene la confianza en el marketplace: cada
# lead "malo" sin remediación es un argumento contra recargar.

REPORTE_MAL_CONTACTO_VENTANA_HS = 72
MOTIVOS_MAL_CONTACTO = (
    "no_atiende",        # llamado/whatsapp sin respuesta tras varios intentos
    "numero_invalido",   # el teléfono no existe / da error de operador
    "empresa_cerrada",   # cerró el negocio / quebró
    "datos_incorrectos", # rubro o info no coincide con la realidad
    "otro",              # texto libre obligatorio en `detalle`
)


class ReportarMalContactoIn(BaseModel):
    motivo: str
    detalle: Optional[str] = None


@router.post("/bolsa/{id}/reportar-mal-contacto")
def reportar_mal_contacto(id: int,
                          body: ReportarMalContactoIn,
                          aliado: Aliado = Depends(current_aliado_required),
                          db: Session = Depends(get_db)):
    """El aliado dueño del lead reporta que el contacto era inválido.
    Solo se acepta dentro de las 72hs posteriores al reclamo (lead.fecha_reclamo).
    Queda en estado 'pendiente' para que el admin revise. Si lo aprueba, se
    descarta el lead y se libera el cupo de reclamo del aliado (los leads son
    gratis; el reembolso de créditos sólo aplica a leads históricos pagos)."""
    a = aliado

    # Validar motivo
    if body.motivo not in MOTIVOS_MAL_CONTACTO:
        raise HTTPException(400, {
            "code": "motivo_invalido",
            "mensaje": f"Motivo debe ser uno de: {list(MOTIVOS_MAL_CONTACTO)}",
            "motivos_validos": list(MOTIVOS_MAL_CONTACTO),
        })
    if body.motivo == "otro" and not (body.detalle or "").strip():
        raise HTTPException(400, {
            "code": "detalle_requerido",
            "mensaje": "Si elegís 'otro' como motivo, contanos el detalle.",
        })

    # Validar que el lead exista y sea del aliado
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")
    if lead.aliado_id != a.id:
        raise HTTPException(403, "Este lead no es tuyo.")
    # Validar ventana de 72hs desde el reclamo
    if not lead.fecha_reclamo:
        raise HTTPException(400, "El lead no tiene fecha de reclamo registrada.")
    horas_desde_compra = (datetime.now() - lead.fecha_reclamo).total_seconds() / 3600
    if horas_desde_compra > REPORTE_MAL_CONTACTO_VENTANA_HS:
        raise HTTPException(400, {
            "code": "ventana_expirada",
            "mensaje": f"Solo podés reportar dentro de las {REPORTE_MAL_CONTACTO_VENTANA_HS}hs desde la compra. Pasaron {int(horas_desde_compra)}hs.",
            "horas_pasadas": int(horas_desde_compra),
            "ventana_hs": REPORTE_MAL_CONTACTO_VENTANA_HS,
        })

    # Idempotencia: un lead solo se puede reportar una vez (sin importar estado)
    existente = db.query(ReporteMalContacto).filter(
        ReporteMalContacto.aliado_id == a.id,
        ReporteMalContacto.lead_id == lead.id,
    ).first()
    if existente:
        raise HTTPException(400, {
            "code": "ya_reportado",
            "mensaje": f"Ya reportaste este lead. Estado actual: {existente.estado}.",
            "reporte_id": existente.id,
            "estado": existente.estado,
        })

    # Crear el reporte
    r = ReporteMalContacto(
        aliado_id = a.id,
        lead_id   = lead.id,
        motivo    = body.motivo,
        detalle   = (body.detalle or "").strip() or None,
        estado    = "pendiente",
    )
    db.add(r)
    db.commit()
    db.refresh(r)

    # Notificar al admin (no bloquea la respuesta — es un log para Gmail del admin)
    try:
        admin_email = ADMIN_EMAIL
        enviar_email(
            admin_email,
            f"[REPORTE MAL CONTACTO] {a.codigo} — Lead #{lead.id} ({lead.empresa})",
            f"""<div style="font-family:sans-serif;background:#0a0a0a;color:#fff;padding:24px;max-width:560px;">
              <h3 style="color:#fbbf24;">Reporte pendiente de revisión</h3>
              <p><strong>Aliado:</strong> {a.nombre} ({a.codigo}) — {a.email}</p>
              <p><strong>Lead:</strong> #{lead.id} — {lead.empresa} ({lead.rubro})</p>
              <p><strong>Tier del lead:</strong> {lead.tier}</p>
              <p><strong>Motivo:</strong> {r.motivo}</p>
              {f"<p><strong>Detalle:</strong> {r.detalle}</p>" if r.detalle else ""}
              <p style="font-size:.85rem;color:#a1a1aa;">Revisar en: /admin/reportes-mal-contacto</p>
            </div>"""
        )
    except Exception as e:
        print(f"[REPORTE MAL CONTACTO] Email admin falló: {e}")

    return {
        "mensaje": "Reporte enviado. Si lo aprobamos, descartamos el lead y te liberamos el cupo.",
        "reporte_id": r.id,
        "estado": r.estado,
        "creditos_a_devolver_si_aprobado": lead.costo_creditos or 0,  # legacy: 0 en leads nuevos
    }


@router.get("/aliados/{codigo}/reportes-mal-contacto")
def listar_reportes_aliado(codigo: str,
                            db: Session = Depends(get_db),
                            _owner=Depends(verify_ownership_dep)):
    """Lista los reportes que hizo este aliado (para mostrar en el portal)."""
    a = _get_aliado(codigo, db)
    reportes = db.query(ReporteMalContacto).filter(
        ReporteMalContacto.aliado_id == a.id
    ).order_by(ReporteMalContacto.creado_en.desc()).limit(50).all()
    return [
        {
            "id": r.id,
            "lead_id": r.lead_id,
            "motivo": r.motivo,
            "detalle": r.detalle,
            "estado": r.estado,
            "creditos_devueltos": r.creditos_devueltos,
            "creado_en": r.creado_en.isoformat() if r.creado_en else None,
            "resuelto_en": r.resuelto_en.isoformat() if r.resuelto_en else None,
        }
        for r in reportes
    ]


@router.get("/admin/reportes-mal-contacto")
def admin_listar_reportes(estado: str = "pendiente",
                          db: Session = Depends(get_db),
                          _admin=Depends(current_admin_required)):
    """Admin lista reportes (default solo pendientes). Estados válidos:
    pendiente, aprobado, rechazado, todos."""
    q = db.query(ReporteMalContacto)
    if estado != "todos":
        if estado not in ("pendiente", "aprobado", "rechazado"):
            raise HTTPException(400, "Estado inválido.")
        q = q.filter(ReporteMalContacto.estado == estado)
    reportes = q.order_by(ReporteMalContacto.creado_en.desc()).limit(200).all()

    out = []
    for r in reportes:
        lead = db.query(LeadBolsa).filter(LeadBolsa.id == r.lead_id).first()
        aliado = db.query(Aliado).filter(Aliado.id == r.aliado_id).first()
        out.append({
            "id": r.id,
            "estado": r.estado,
            "motivo": r.motivo,
            "detalle": r.detalle,
            "creditos_devueltos": r.creditos_devueltos,
            "creado_en": r.creado_en.isoformat() if r.creado_en else None,
            "resuelto_en": r.resuelto_en.isoformat() if r.resuelto_en else None,
            "resuelto_por": r.resuelto_por,
            "notas_admin": r.notas_admin,
            "aliado": {
                "id": aliado.id if aliado else None,
                "codigo": aliado.codigo if aliado else None,
                "nombre": aliado.nombre if aliado else None,
                "email": aliado.email if aliado else None,
            },
            "lead": {
                "id": lead.id if lead else None,
                "empresa": lead.empresa if lead else None,
                "rubro": lead.rubro if lead else None,
                "telefono": lead.telefono if lead else None,
                "costo_creditos": lead.costo_creditos if lead else None,
                "tier": lead.tier if lead else None,
            } if lead else None,
        })
    return out


class ResolverReporteIn(BaseModel):
    notas_admin: Optional[str] = None
    admin_username: Optional[str] = None  # opcional, queda como auditoría


@router.post("/admin/reportes-mal-contacto/{id}/aprobar")
def admin_aprobar_reporte(id: int,
                          body: ResolverReporteIn | None = Body(default=None),
                          db: Session = Depends(get_db),
                          _admin=Depends(current_admin_required)):
    """Aprueba un reporte: descarta el lead y libera el cupo de reclamo del
    aliado (lo manda a 'descartado' para que no vuelva al pool ni cuente como
    reclamo activo). Si el lead era histórico y costó créditos, los reintegra."""
    r = db.query(ReporteMalContacto).filter(ReporteMalContacto.id == id).first()
    if not r:
        raise HTTPException(404, "Reporte no encontrado.")
    if r.estado != "pendiente":
        raise HTTPException(400, f"Reporte ya estaba en estado '{r.estado}'.")

    from main import PORTAL_URL  # diferido: const de main, usada en el email

    aliado = db.query(Aliado).filter(Aliado.id == r.aliado_id).first()
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == r.lead_id).first()
    if not aliado or not lead:
        raise HTTPException(500, "Aliado o lead asociado no existe.")

    # Los leads nuevos son gratis: sólo hay créditos para devolver en leads
    # históricos (legacy) comprados cuando el marketplace cobraba créditos.
    creditos_a_devolver = lead.costo_creditos or 0
    if creditos_a_devolver > 0:
        _ajustar_creditos(
            db, aliado, creditos_a_devolver,
            "devolucion_lead_invalido", f"reporte:{r.id}:lead:{lead.id}"
        )

    # Marcar el lead como descartado (sale del flujo del aliado)
    lead.estado = "descartado"
    lead.resultado = f"Reportado mal contacto (motivo: {r.motivo})"

    # Marcar el reporte como aprobado
    r.estado = "aprobado"
    r.creditos_devueltos = creditos_a_devolver
    r.resuelto_en = datetime.now()
    r.notas_admin = (body.notas_admin if body else None) or "Aprobado."
    r.resuelto_por = (body.admin_username if body else None) or "admin"

    db.commit()

    # Notificar al aliado — el mensaje cambia según haya reembolso o sólo cupo
    try:
        if aliado.email:
            if creditos_a_devolver > 0:
                asunto = f"✅ Reporte aprobado: te devolvimos {creditos_a_devolver} créditos"
                bloque = f"""<h2 style=\"color:#4ade80;margin:0 0 12px;\">¡Te devolvimos los créditos!</h2>
                  <p style=\"color:#a1a1aa;line-height:1.6;\">Revisamos tu reporte sobre el lead <strong style=\"color:#fff;\">{lead.empresa}</strong> y le dimos la razón. Descartamos el lead, te liberamos el cupo y reintegramos <strong style=\"color:#4ade80;\">{creditos_a_devolver} créditos</strong> a tu saldo.</p>
                  <div style=\"background:#0f1d12;border:1px solid #14532d;border-radius:8px;padding:14px;margin:18px 0;\">
                    <p style=\"margin:0;color:#86efac;font-weight:700;\">Saldo nuevo: {aliado.creditos or 0} créditos</p>
                  </div>"""
            else:
                asunto = "✅ Reporte aprobado: te liberamos el cupo"
                bloque = f"""<h2 style=\"color:#4ade80;margin:0 0 12px;\">¡Listo, lo descartamos!</h2>
                  <p style=\"color:#a1a1aa;line-height:1.6;\">Revisamos tu reporte sobre el lead <strong style=\"color:#fff;\">{lead.empresa}</strong> y le dimos la razón. Lo sacamos de tus reclamos activos, así que <strong style=\"color:#86efac;\">ya podés reclamar otro lead</strong> en su lugar — sin costo, como siempre.</p>"""
            enviar_email(
                aliado.email,
                asunto,
                f"""<div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:32px;max-width:560px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                  {bloque}
                  <p style="color:#a1a1aa;font-size:.9rem;">Gracias por reportarlo — nos ayuda a mejorar la calidad de los leads.</p>
                  <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:8px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ver mi portal →</a>
                </div>"""
            )
    except Exception as e:
        print(f"[REPORTE APROBADO] Email aliado falló: {e}")

    return {
        "mensaje": ("Reporte aprobado: lead descartado y cupo liberado."
                    + (f" Se reintegraron {creditos_a_devolver} créditos." if creditos_a_devolver > 0 else "")),
        "reporte_id": r.id,
        "creditos_devueltos": creditos_a_devolver,
        "saldo_aliado": aliado.creditos or 0,
    }


@router.post("/admin/reportes-mal-contacto/{id}/rechazar")
def admin_rechazar_reporte(id: int,
                            body: ResolverReporteIn | None = Body(default=None),
                            db: Session = Depends(get_db),
                            _admin=Depends(current_admin_required)):
    """Rechaza un reporte: el lead sigue ocupando el cupo del aliado y no hay
    reintegro de créditos. Deja registro auditable."""
    r = db.query(ReporteMalContacto).filter(ReporteMalContacto.id == id).first()
    if not r:
        raise HTTPException(404, "Reporte no encontrado.")
    if r.estado != "pendiente":
        raise HTTPException(400, f"Reporte ya estaba en estado '{r.estado}'.")

    r.estado = "rechazado"
    r.resuelto_en = datetime.now()
    r.notas_admin = (body.notas_admin if body else None) or "Rechazado por admin."
    r.resuelto_por = (body.admin_username if body else None) or "admin"
    db.commit()

    # Notificar al aliado con el motivo del rechazo
    try:
        aliado = db.query(Aliado).filter(Aliado.id == r.aliado_id).first()
        lead = db.query(LeadBolsa).filter(LeadBolsa.id == r.lead_id).first()
        if aliado and aliado.email:
            empresa = lead.empresa if lead else f"#{r.lead_id}"
            enviar_email(
                aliado.email,
                f"Reporte revisado: {empresa}",
                f"""<div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:32px;max-width:560px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                  <h2 style="color:#fbbf24;margin:0 0 12px;">Revisamos tu reporte</h2>
                  <p style="color:#a1a1aa;line-height:1.6;">Sobre el lead <strong style="color:#fff;">{empresa}</strong>: después de revisar, decidimos no descartarlo. Sigue en tus reclamos activos.</p>
                  <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:14px;margin:18px 0;">
                    <p style="margin:0 0 6px;color:#a1a1aa;font-size:.85rem;text-transform:uppercase;">Nota del admin:</p>
                    <p style="margin:0;color:#fff;">{r.notas_admin}</p>
                  </div>
                  <p style="color:#a1a1aa;font-size:.9rem;">Si te parece que hubo un error, respondé este email y lo revisamos juntos.</p>
                </div>"""
            )
    except Exception as e:
        print(f"[REPORTE RECHAZADO] Email aliado falló: {e}")

    return {
        "mensaje": "Reporte rechazado.",
        "reporte_id": r.id,
        "estado": r.estado,
    }
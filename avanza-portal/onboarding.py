"""
onboarding.py — Onboarding de clientes post-venta (reemplazo de Tally).

Flujo:
  - GET  /onboarding                  → sirve el formulario (lee ?plan= en el front).
  - POST /onboarding/enviar           → recibe respuestas + archivos, guarda y avisa.
  - GET  /admin/onboarding            → lista de respuestas recibidas (admin).
  - GET  /admin/onboarding/{rid}      → detalle de una respuesta (admin).
  - GET  /admin/onboarding/archivo/{aid} → descarga un archivo subido (admin).

Almacenamiento de archivos: por ahora van como binario en PostgreSQL
(tabla onboarding_archivos). Es autónomo, sin dependencias externas, y
alcanza de sobra para el volumen de onboarding. Si algún día el volumen o
el peso crecen, lo único que hay que cambiar es la función _guardar_archivo()
para que suba a Cloudflare R2 / S3 y guarde la URL en vez del binario.

Se incluye en main.py con:  app.include_router(onboarding.router)
"""
import os
import json

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from auth import current_admin_required
from notificaciones import enviar_email, ADMIN_EMAIL
from models import OnboardingRespuesta, OnboardingArchivo, Aliado, LinkPago

router = APIRouter()

# Ruta del HTML del formulario. Dejá onboarding-avanza.html junto a este archivo
# (o ajustá la ruta). Igual que el resto de páginas estáticas del portal.
_FORM_HTML = os.path.join(os.path.dirname(__file__), "onboarding-avanza.html")

# Límite por archivo. Tally usaba 10 MB; mantenemos lo mismo.
_MAX_FILE_BYTES = 10 * 1024 * 1024

# Etiquetas legibles de cada plan, para el mail de aviso.
_PLAN_LABEL = {
    "base": "Plan Base",
    "pro": "Plan Pro",
    "industrial": "Plan Industrial",
    "360": "Estratégico 360",
}


# ─── SERVIR EL FORMULARIO ─────────────────────────────────────────────────────
@router.get("/onboarding")
def servir_formulario_onboarding():
    """Sirve el formulario. El plan se resuelve en el front por ?plan=…"""
    if not os.path.exists(_FORM_HTML):
        raise HTTPException(500, "Formulario de onboarding no encontrado en el servidor.")
    return FileResponse(_FORM_HTML)


# ─── RECIBIR RESPUESTAS ───────────────────────────────────────────────────────
@router.post("/onboarding/enviar")
async def recibir_onboarding(request: Request, db: Session = Depends(get_db)):
    """Recibe el formulario (multipart): respuestas en JSON + archivos.

    Campos del FormData:
      - plan         : 'base' | 'pro' | 'industrial' | '360'
      - respuestas   : JSON con todas las respuestas de texto/opción
      - token        : (opcional) para vincular con la venta/aliado a futuro
      - archivo_<id> : uno o más archivos por cada campo de subida
    """
    form = await request.form()
    plan = (form.get("plan") or "").strip()
    token = (form.get("token") or "").strip()

    try:
        respuestas = json.loads(form.get("respuestas") or "{}")
    except Exception:
        respuestas = {}

    # Vinculación opcional con la venta (si en el futuro pasamos un token en el link).
    aliado_id = None
    link_pago_id = None
    if token:
        lp = db.query(LinkPago).filter(LinkPago.external_ref.like(f"%|onbtok={token}")).first()
        if lp:
            link_pago_id = lp.id
            aliado_id = lp.aliado_id

    # Identidad del cliente: sale de las propias respuestas (igual que en Tally).
    cliente_nombre = (respuestas.get("empresa") or "").strip()
    cliente_email = (respuestas.get("email_consultas") or "").strip()

    r = OnboardingRespuesta(
        plan=plan,
        aliado_id=aliado_id,
        link_pago_id=link_pago_id,
        cliente_nombre=cliente_nombre or None,
        cliente_email=cliente_email or None,
        respuestas_json=json.dumps(respuestas, ensure_ascii=False),
    )
    db.add(r)
    db.commit()
    db.refresh(r)

    # Archivos
    guardados = []
    descartados = []
    for clave, valor in form.multi_items():
        if not clave.startswith("archivo_"):
            continue
        filename = getattr(valor, "filename", None)
        if not filename:
            continue
        data = await valor.read()
        if len(data) > _MAX_FILE_BYTES:
            descartados.append(f"{filename} (supera 10 MB)")
            continue
        _guardar_archivo(
            db,
            respuesta_id=r.id,
            campo=clave.replace("archivo_", ""),
            filename=filename,
            content_type=getattr(valor, "content_type", "") or "",
            data=data,
        )
        guardados.append(filename)
    db.commit()

    _notificar_admin(r, respuestas, guardados, descartados)
    return {"status": "ok", "id": r.id, "archivos_guardados": len(guardados)}


def _guardar_archivo(db, *, respuesta_id, campo, filename, content_type, data):
    """Punto único de almacenamiento. Hoy: binario en Postgres.
    Para migrar a R2/S3, reemplazar el cuerpo por una subida y guardar la URL."""
    arch = OnboardingArchivo(
        respuesta_id=respuesta_id,
        campo=campo,
        filename=filename,
        content_type=content_type,
        data=data,
    )
    db.add(arch)


def _notificar_admin(r, respuestas, guardados, descartados):
    """Mail a Avanza con el resumen completo de la respuesta."""
    plan_label = _PLAN_LABEL.get(r.plan, r.plan or "—")
    filas = ""
    for k, v in respuestas.items():
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v) if v else "—"
        v = (str(v) or "—").replace("<", "&lt;").replace(">", "&gt;")
        filas += (
            f'<tr><td style="padding:6px 10px;color:#a1a1aa;font-size:.82rem;'
            f'border-bottom:1px solid #222;vertical-align:top;white-space:nowrap;">{k}</td>'
            f'<td style="padding:6px 10px;color:#fff;font-size:.88rem;'
            f'border-bottom:1px solid #222;">{v}</td></tr>'
        )

    archivos_html = ""
    if guardados:
        items = "".join(f"<li>{f}</li>" for f in guardados)
        archivos_html = (
            f'<p style="margin:16px 0 4px;color:#4ade80;font-weight:700;">'
            f'📎 {len(guardados)} archivo(s) recibido(s):</p>'
            f'<ul style="margin:0;color:#a1a1aa;font-size:.85rem;">{items}</ul>'
            f'<p style="color:#71717a;font-size:.8rem;margin-top:6px;">'
            f'Descargalos desde el panel admin → Onboarding.</p>'
        )
    if descartados:
        archivos_html += (
            f'<p style="margin:12px 0 4px;color:#fbbf24;font-weight:700;">'
            f'⚠ Archivos descartados:</p><ul style="margin:0;color:#fcd34d;font-size:.85rem;">'
            + "".join(f"<li>{d}</li>" for d in descartados) + "</ul>"
        )

    cuerpo = f"""
    <div style="font-family:Inter,sans-serif;background:#050505;color:#fff;padding:28px;max-width:640px;margin:auto;border-radius:12px;">
      <p style="font-size:.72rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#3b82f6;margin:0 0 6px;">Nuevo onboarding · {plan_label}</p>
      <h1 style="font-size:1.4rem;font-weight:800;margin:0 0 4px;">{r.cliente_nombre or 'Cliente sin nombre'}</h1>
      <p style="color:#a1a1aa;font-size:.88rem;margin:0 0 18px;">{r.cliente_email or 'sin email'} · respuesta #{r.id}</p>
      <table style="width:100%;border-collapse:collapse;background:#111;border-radius:8px;overflow:hidden;">{filas}</table>
      {archivos_html}
    </div>"""
    try:
        enviar_email(ADMIN_EMAIL, f"📋 Onboarding {plan_label} — {r.cliente_nombre or 'nuevo cliente'}", cuerpo)
    except Exception as e:
        print(f"[ONBOARDING] No pude enviar el aviso al admin: {e}")


# ─── PANEL ADMIN ──────────────────────────────────────────────────────────────
@router.get("/admin/onboarding")
def admin_listar_onboarding(db: Session = Depends(get_db), _admin=Depends(current_admin_required)):
    rows = (
        db.query(OnboardingRespuesta)
        .order_by(OnboardingRespuesta.creado_en.desc())
        .limit(300)
        .all()
    )
    return [
        {
            "id": r.id,
            "plan": r.plan,
            "cliente_nombre": r.cliente_nombre,
            "cliente_email": r.cliente_email,
            "aliado_id": r.aliado_id,
            "archivos": len(r.archivos),
            "creado_en": r.creado_en.isoformat() if r.creado_en else None,
        }
        for r in rows
    ]


@router.get("/admin/onboarding/{rid}")
def admin_detalle_onboarding(rid: int, db: Session = Depends(get_db), _admin=Depends(current_admin_required)):
    r = db.query(OnboardingRespuesta).filter(OnboardingRespuesta.id == rid).first()
    if not r:
        raise HTTPException(404, "Respuesta no encontrada.")
    try:
        respuestas = json.loads(r.respuestas_json or "{}")
    except Exception:
        respuestas = {}
    return {
        "id": r.id,
        "plan": r.plan,
        "cliente_nombre": r.cliente_nombre,
        "cliente_email": r.cliente_email,
        "aliado_id": r.aliado_id,
        "link_pago_id": r.link_pago_id,
        "creado_en": r.creado_en.isoformat() if r.creado_en else None,
        "respuestas": respuestas,
        "archivos": [
            {"id": a.id, "campo": a.campo, "filename": a.filename, "content_type": a.content_type}
            for a in r.archivos
        ],
    }


@router.get("/admin/onboarding/archivo/{aid}")
def admin_descargar_archivo(aid: int, db: Session = Depends(get_db), _admin=Depends(current_admin_required)):
    a = db.query(OnboardingArchivo).filter(OnboardingArchivo.id == aid).first()
    if not a:
        raise HTTPException(404, "Archivo no encontrado.")
    return Response(
        content=a.data,
        media_type=a.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{a.filename}"'},
    )
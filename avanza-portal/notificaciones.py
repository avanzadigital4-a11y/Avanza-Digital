"""
notificaciones.py — Canales de notificación: email, novedades in-app y web push
================================================================================

Extraído de main.py como primer paso del split en módulos. Acá vive todo lo
que sea "avisarle algo a alguien", con sus tres canales:

  enviar_email()         → email transaccional con cadena de fallback
                           Brevo → Resend → SMTP → log.
  notificar_aliado()     → novedad in-app (la campanita del portal). Puede
                           disparar push inmediato según PUSH_TIPOS_INMEDIATOS.
  enviar_push_a_aliado() → web push a todos los dispositivos suscritos.

REGLA DE ORO (heredada del diseño original): ninguna de estas funciones rompe
el flujo principal. Si el proveedor de email está caído o el push falla, se
loguea y la operación de negocio sigue. Notificar es complementario, nunca
crítico.

main.py reimporta estos nombres, así que el resto del código (y los tests)
siguen usando `from main import enviar_email` o `main.enviar_email` sin cambios.
"""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from models import Novedad, PushSubscription

# ─── EMAIL: CONFIG ───────────────────────────────────────────────────────────
SMTP_HOST   = os.environ.get("SMTP_HOST", "")
SMTP_PORT   = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER   = os.environ.get("SMTP_USER", "")
SMTP_PASS   = os.environ.get("SMTP_PASS", "")
EMAIL_FROM  = os.environ.get("EMAIL_FROM", SMTP_USER)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "avanzadigital4@gmail.com")

# ─── RESEND (fallback de emails) ─────────────────────────────────────────────
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM    = os.environ.get("RESEND_FROM", "Avanza Digital <no-reply@avanzadigital.digital>")

# ─── BREVO (emails transaccionales — proveedor primario) ─────────────────────
# Free tier: 300 emails/día, 9.000/mes — permanente, sin tarjeta.
#
# IMPORTANTE: Brevo (y cualquier proveedor serio) rechaza enviar desde Gmail
# genérico — los emails enviados desde @gmail.com vía Brevo terminan en spam
# o son directamente rechazados (DMARC reject). Por defecto usamos un
# remitente del dominio propio. ANTES de que esto funcione hay que:
#   1) Verificar el dominio avanzadigital.digital en Brevo
#   2) Configurar los registros SPF, DKIM y DMARC en la zona DNS
BREVO_API_KEY   = os.environ.get("BREVO_API_KEY", "")
BREVO_FROM      = os.environ.get("BREVO_FROM", "no-reply@avanzadigital.digital")
BREVO_FROM_NAME = os.environ.get("BREVO_FROM_NAME", "Avanza Digital")


def enviar_email(destinatario: str, asunto: str, cuerpo_html: str):
    """Envía un email con cadena de fallback: Brevo → Resend → SMTP → log.

    Brevo es el proveedor primario (300 emails/día gratis, permanente).
    Resend queda como respaldo automático si Brevo falla por cualquier razón.
    SMTP es el último recurso si ambas APIs fallan.
    """
    # --- 1. BREVO (primario) ---
    if BREVO_API_KEY:
        try:
            # Parsear "Nombre <email>" o usar directo
            sender_email = BREVO_FROM
            sender_name  = BREVO_FROM_NAME
            if "<" in BREVO_FROM and ">" in BREVO_FROM:
                parts = BREVO_FROM.split("<")
                sender_name  = parts[0].strip()
                sender_email = parts[1].replace(">", "").strip()

            resp = httpx.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": BREVO_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "sender":      {"name": sender_name, "email": sender_email},
                    "to":          [{"email": destinatario}],
                    "subject":     asunto,
                    "htmlContent": cuerpo_html,
                },
                timeout=10.0,
            )
            if resp.status_code in (200, 201, 202):
                print(f"[EMAIL Brevo] OK → {destinatario} | {asunto}")
                return
            print(f"[EMAIL Brevo ERROR {resp.status_code}] {resp.text[:200]}")
        except Exception as e:
            print(f"[EMAIL Brevo EXCEPTION] {e}")

    # --- 2. RESEND (fallback automático) ---
    if RESEND_API_KEY:
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                         "Content-Type": "application/json"},
                json={"from": RESEND_FROM, "to": [destinatario],
                      "subject": asunto, "html": cuerpo_html},
                timeout=10.0,
            )
            if resp.status_code in (200, 202):
                print(f"[EMAIL Resend fallback] OK → {destinatario} | {asunto}")
                return
            print(f"[EMAIL Resend ERROR {resp.status_code}] {resp.text[:200]}")
        except Exception as e:
            print(f"[EMAIL Resend EXCEPTION] {e}")

    # --- 3. SMTP (último recurso) ---
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print(f"[EMAIL - sin transporte] Para: {destinatario} | Asunto: {asunto}")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"]    = EMAIL_FROM
        msg["To"]      = destinatario
        msg.attach(MIMEText(cuerpo_html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, destinatario, msg.as_string())
        print(f"[EMAIL SMTP fallback] Enviado a {destinatario}: {asunto}")
    except Exception as e:
        print(f"[EMAIL ERROR total] {e}")


# ─── WEB PUSH (notificaciones al celular) ────────────────────────────────────
try:
    from pywebpush import webpush, WebPushException
    _PUSH_OK = True
except Exception:
    _PUSH_OK = False

VAPID_PUBLIC  = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE_KEY", "").replace("\\n", "\n")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:soporte@avanzadigital.digital")
# Tipos de novedad que disparan push inmediato (opt-in por env, vacío = ninguno).
# Ej en Render: PUSH_TIPOS_INMEDIATOS="comision,venta"
_PUSH_TIPOS_INMEDIATOS = set(
    t.strip() for t in os.environ.get("PUSH_TIPOS_INMEDIATOS", "").split(",") if t.strip()
)


def enviar_push_a_aliado(db, aliado_id, titulo, cuerpo="", url="/"):
    """Manda un web-push a todos los dispositivos suscritos del aliado.
    No-op si faltan claves VAPID / la librería / suscripciones. Limpia las
    suscripciones muertas (404/410). Nunca rompe el flujo principal."""
    if not (_PUSH_OK and VAPID_PRIVATE and aliado_id):
        return
    try:
        subs = db.query(PushSubscription).filter(PushSubscription.aliado_id == aliado_id).all()
    except Exception:
        return
    if not subs:
        return
    import json as _json
    payload = _json.dumps({"title": titulo, "body": cuerpo, "url": url})
    muertas = []
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE,
                vapid_claims={"sub": VAPID_SUBJECT},
            )
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                muertas.append(sub)
        except Exception as e:
            print(f"[PUSH] error enviando a {aliado_id}: {e}")
    for s in muertas:
        try:
            db.delete(s)
        except Exception:
            pass
    if muertas:
        try:
            db.commit()
        except Exception:
            pass


# ─── NOVEDADES IN-APP (la campanita del portal) ──────────────────────────────

def notificar_aliado(db, aliado_id: int, tipo: str, titulo: str,
                     cuerpo: str = "", tab: str = None):
    """Crea una novedad in-app para el aliado (campanita del portal).

    Best-effort y NO hace commit: la transacción la maneja el caller (mismo
    patrón que _crear_prospecto_desde_lead). Si algo falla, no rompe el flujo
    principal — la novedad es complementaria, nunca crítica.
    """
    try:
        if not aliado_id:
            return
        db.add(Novedad(
            aliado_id=aliado_id, tipo=tipo,
            titulo=(titulo or "")[:200],
            cuerpo=(cuerpo or "")[:600] or None,
            tab=tab,
        ))
        try:
            if tipo in _PUSH_TIPOS_INMEDIATOS:
                enviar_push_a_aliado(db, aliado_id, titulo, cuerpo, "/")
        except Exception:
            pass
    except Exception as e:
        print(f"[NOVEDADES] No se pudo crear novedad para aliado {aliado_id}: {e}")

# ═══ ROUTER: ENDPOINTS DE PUSH Y NOVEDADES ═══════════════════════════════════
# Migrados de main.py en el segundo tramo del split. Este módulo ya era el
# dueño de la lógica de push/campanita; ahora también expone sus endpoints.
# main.py los activa con app.include_router(notificaciones.router).
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import schemas
from auth import current_aliado_required, verify_ownership_dep
from database import get_db
from models import Aliado

router = APIRouter(tags=["notificaciones"])


def _get_aliado(codigo, db):
    """Puente diferido al helper de main (evita el ciclo notificaciones↔main:
    este módulo se importa al inicio de main, antes de que exista el helper)."""
    from main import _get_aliado as f
    return f(codigo, db)


@router.get("/push/vapid-public")
def push_vapid_public():
    return {"public_key": VAPID_PUBLIC, "enabled": bool(_PUSH_OK and VAPID_PRIVATE and VAPID_PUBLIC)}


@router.post("/push/subscribe")
def push_subscribe(body: schemas.PushSubscribeIn,
                   aliado: Aliado = Depends(current_aliado_required),
                   db: Session = Depends(get_db)):
    existente = db.query(PushSubscription).filter(PushSubscription.endpoint == body.endpoint).first()
    if existente:
        existente.aliado_id = aliado.id
        existente.p256dh = body.p256dh
        existente.auth = body.auth
    else:
        db.add(PushSubscription(aliado_id=aliado.id, endpoint=body.endpoint,
                                p256dh=body.p256dh, auth=body.auth))
    db.commit()
    return {"ok": True}


@router.post("/push/unsubscribe")
def push_unsubscribe(body: schemas.PushUnsubscribeIn,
                     aliado: Aliado = Depends(current_aliado_required),
                     db: Session = Depends(get_db)):
    db.query(PushSubscription).filter(
        PushSubscription.endpoint == body.endpoint,
        PushSubscription.aliado_id == aliado.id,
    ).delete()
    db.commit()
    return {"ok": True}

# ─── NOVEDADES (campanita in-app) ────────────────────────────────────────────

@router.get("/aliados/{codigo}/novedades")
def listar_novedades_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Últimas novedades del aliado + contador de no leídas (campanita)."""
    a = _get_aliado(codigo, db)
    novedades = (db.query(Novedad)
                   .filter(Novedad.aliado_id == a.id)
                   .order_by(Novedad.creado_en.desc())
                   .limit(30)
                   .all())
    no_leidas = (db.query(Novedad)
                   .filter(Novedad.aliado_id == a.id, Novedad.leida == False)
                   .count())
    return {
        "no_leidas": no_leidas,
        "novedades": [{
            "id": n.id, "tipo": n.tipo, "titulo": n.titulo,
            "cuerpo": n.cuerpo, "tab": n.tab, "leida": n.leida,
            "fecha": n.creado_en.strftime("%d/%m %H:%M") if n.creado_en else None,
        } for n in novedades],
    }


@router.post("/aliados/{codigo}/novedades/marcar-leidas")
def marcar_novedades_leidas(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    (db.query(Novedad)
       .filter(Novedad.aliado_id == a.id, Novedad.leida == False)
       .update({Novedad.leida: True}, synchronize_session=False))
    db.commit()
    return {"ok": True}


@router.post("/push/test")
def push_test(aliado: Aliado = Depends(current_aliado_required),
              db: Session = Depends(get_db)):
    """Envía una push de prueba a los dispositivos del aliado que llama y
    devuelve un diagnóstico (claves activas, nº de suscripciones, enviadas,
    primer error) para depurar por qué no llegan las notificaciones."""
    if not (_PUSH_OK and VAPID_PRIVATE and VAPID_PUBLIC):
        falta = []
        if not _PUSH_OK: falta.append("libreria pywebpush")
        if not VAPID_PUBLIC: falta.append("VAPID_PUBLIC_KEY")
        if not VAPID_PRIVATE: falta.append("VAPID_PRIVATE_KEY")
        return {"ok": False, "enabled": False, "suscripciones": 0, "enviadas": 0,
                "error": "Push deshabilitado en el servidor. Falta: " + ", ".join(falta)}
    subs = db.query(PushSubscription).filter(PushSubscription.aliado_id == aliado.id).all()
    if not subs:
        return {"ok": False, "enabled": True, "suscripciones": 0, "enviadas": 0,
                "error": "No hay suscripción guardada para esta cuenta. Reactivá las notificaciones desde este dispositivo."}
    import json as _json
    payload = _json.dumps({"title": "Avanza Digital",
                           "body": "Notificación de prueba — el push funciona.", "url": "/"})
    enviadas, primer_error, muertas = 0, None, []
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload, vapid_private_key=VAPID_PRIVATE,
                vapid_claims={"sub": VAPID_SUBJECT},
            )
            enviadas += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                muertas.append(sub)
            if primer_error is None:
                primer_error = f"WebPushException {code or ''}: {e}".strip()
        except Exception as e:
            if primer_error is None:
                primer_error = f"{type(e).__name__}: {e}"
    for x in muertas:
        try: db.delete(x)
        except Exception: pass
    db.commit()
    return {"ok": enviadas > 0, "enabled": True, "suscripciones": len(subs),
            "enviadas": enviadas, "error": primer_error}
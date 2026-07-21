"""
contratacion.py — Solicitudes de contratación desde contratar.html (sitio público).

Reemplaza el envío directo a Google Apps Script: el formulario ahora le pega
a este endpoint, que solo manda un mail de aviso (mismo mecanismo que el
resto del sitio: enviar_email() con fallback Brevo → Resend → SMTP). No
persiste nada en la base a propósito, para mantenerlo simple e igual de
liviano que lo que había antes — si en algún momento quieren un historial
navegable en el admin, se puede sumar un modelo tipo OnboardingRespuesta.

Se expone en /solicitudes/contratar. El _redirects del sitio estático lo
proxea de forma transparente (mismo patrón que /leads/*, /auditorias/*, etc.),
así el navegador lo ve como same-origin y no hay que lidiar con CORS.

Se incluye en main.py con: app.include_router(contratacion.router)
"""
from fastapi import APIRouter, Request

from notificaciones import enviar_email, ADMIN_EMAIL
from rate_limit import limiter

router = APIRouter(tags=["contratacion"])


@router.post("/solicitudes/contratar")
@limiter.limit("20/hour")
def solicitud_contratar(
    request: Request,
    empresa: str = "",
    nombre_contacto: str = "",
    email: str = "",
    telefono: str = "",
    plan_seleccionado: str = "",
    precio_usd: str = "",
    codigo_orden: str = "",
):
    """Recibe la solicitud del formulario de contratar.html y avisa por mail.
    Nunca bloquea al usuario: si el mail falla, se loguea pero igual devuelve ok."""
    asunto = f"🧾 Nueva solicitud de contratación — {empresa or 'sin nombre'} ({codigo_orden or 's/orden'})"

    telefono_limpio = "".join(c for c in telefono if c.isdigit())

    cuerpo = f"""
    <div style="font-family:Inter,sans-serif;background:#050505;color:#fff;padding:28px;max-width:640px;margin:auto;border-radius:12px;">
      <p style="font-size:.72rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#3b82f6;margin:0 0 6px;">Contratar · {plan_seleccionado or 'Plan General'}</p>
      <h1 style="font-size:1.4rem;font-weight:800;margin:0 0 4px;">{empresa or 'Empresa sin nombre'}</h1>
      <p style="color:#a1a1aa;font-size:.88rem;margin:0 0 18px;">Orden {codigo_orden or '—'}</p>
      <table style="width:100%;border-collapse:collapse;background:#111;border-radius:8px;overflow:hidden;">
        <tr><td style="padding:6px 10px;color:#a1a1aa;font-size:.82rem;border-bottom:1px solid #222;">Responsable</td><td style="padding:6px 10px;color:#fff;font-size:.88rem;border-bottom:1px solid #222;">{nombre_contacto or '—'}</td></tr>
        <tr><td style="padding:6px 10px;color:#a1a1aa;font-size:.82rem;border-bottom:1px solid #222;">Email</td><td style="padding:6px 10px;color:#fff;font-size:.88rem;border-bottom:1px solid #222;">{email or '—'}</td></tr>
        <tr><td style="padding:6px 10px;color:#a1a1aa;font-size:.82rem;border-bottom:1px solid #222;">Teléfono</td><td style="padding:6px 10px;color:#fff;font-size:.88rem;border-bottom:1px solid #222;">{telefono or '—'}</td></tr>
        <tr><td style="padding:6px 10px;color:#a1a1aa;font-size:.82rem;">Precio</td><td style="padding:6px 10px;color:#fff;font-size:.88rem;">USD {precio_usd or '—'}</td></tr>
      </table>
      <p style="margin-top:18px;"><a href="https://wa.me/{telefono_limpio}" style="color:#3b82f6;">Escribirle por WhatsApp →</a></p>
    </div>
    """

    try:
        enviar_email(ADMIN_EMAIL, asunto, cuerpo)
    except Exception as e:
        print(f"[CONTRATAR] No pude enviar el aviso: {e}")

    return {"status": "ok"}
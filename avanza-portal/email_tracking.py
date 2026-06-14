"""
email_tracking.py — Hueco 1: analítica de email (apertura, clic, conversión)
================================================================================

PROBLEMA QUE RESUELVE
---------------------
El portal manda ~29 tipos de email (onboarding, inactividad, digest, etc.) y
hasta ahora NO había forma de saber si llegan, si se abren, si se clickean ni
si convierten. Este módulo agrega medición sin atarse a un proveedor: como
`enviar_email()` rota Brevo → Resend → SMTP, en vez de depender del webhook de
uno solo usamos tracking propio (pixel de apertura + redirect de clic) que
funciona pasen por el proveedor que pasen.

CÓMO FUNCIONA
-------------
1. `enviar_email(..., campania="inactividad_20d", aliado_id=a.id)` registra el
   envío en la tabla `emails_enviados`, inyecta un pixel 1x1 y reescribe los
   links al portal para que pasen por el redirect de clic. (Ver notificaciones.py)
2. Cuando el destinatario abre el mail → carga el pixel → GET /e/o/{token}.png
   → marcamos apertura.
3. Cuando clickea un botón del portal → GET /e/c/{token}?u=... → marcamos clic
   y lo redirigimos a destino.
4. El admin ve todo agregado por campaña en GET /admin/email/metricas, incluida
   la TASA DE REACTIVACIÓN de las secuencias de inactividad (¿el aliado al que
   le mandamos "te extrañamos" volvió a loguearse después?).

PRIVACIDAD / COSTO: $0. El pixel y el redirect son endpoints propios; no se
paga nada y no se agrega ninguna dependencia.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import current_admin_required
from database import get_db
from models import Aliado, EmailEnviado

router = APIRouter(tags=["email-analytics"])

# GIF transparente 1x1 — el pixel de apertura clásico.
_PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)
_PIXEL_HEADERS = {
    # Evitamos que el cliente cachee el pixel: si lo cachea, la 2da apertura
    # no pega al server y subcontamos.
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
}


@router.get("/e/o/{token}.png")
def email_open_pixel(token: str, db: Session = Depends(get_db)):
    """Pixel de apertura. Público (lo carga el cliente de mail). Nunca falla
    de cara al usuario: pase lo que pase devuelve el GIF."""
    try:
        reg = db.query(EmailEnviado).filter(EmailEnviado.token == token).first()
        if reg:
            if reg.abierto_en is None:
                reg.abierto_en = datetime.now()
            reg.aperturas = (reg.aperturas or 0) + 1
            db.commit()
    except Exception:
        pass
    return Response(content=_PIXEL_GIF, media_type="image/gif", headers=_PIXEL_HEADERS)


@router.get("/e/c/{token}")
def email_click_redirect(token: str, u: str = Query("", description="URL destino"),
                         db: Session = Depends(get_db)):
    """Redirect de clic. Marca el clic y manda al destino real. Solo redirige
    a http/https para no convertirse en un open-redirect."""
    try:
        reg = db.query(EmailEnviado).filter(EmailEnviado.token == token).first()
        if reg:
            if reg.click_en is None:
                reg.click_en = datetime.now()
            reg.clicks = (reg.clicks or 0) + 1
            # un clic implica apertura; si el pixel se bloqueó, igual contamos
            if reg.abierto_en is None:
                reg.abierto_en = datetime.now()
            db.commit()
    except Exception:
        pass
    destino = u or "/"
    if not destino.lower().startswith(("http://", "https://")):
        destino = "/"
    return RedirectResponse(url=destino, status_code=302)


@router.get("/admin/email/metricas")
def metricas_email(dias: int = Query(90, ge=1, le=365),
                   _admin=Depends(current_admin_required),
                   db: Session = Depends(get_db)):
    """Panel de analítica de email para el admin.

    Devuelve, por campaña (en los últimos `dias`):
      - enviados, abiertos, clickeados
      - tasa_apertura, tasa_clic
    Y para las secuencias de inactividad (campania que empieza con 'inactividad'),
    la TASA DE REACTIVACIÓN: % de aliados que volvieron a loguearse DESPUÉS de
    recibir el email. Esa es la métrica que dice si la secuencia sirve o no.
    """
    desde = datetime.now() - timedelta(days=dias)

    filas = (
        db.query(
            EmailEnviado.campania.label("campania"),
            func.count(EmailEnviado.id).label("enviados"),
            func.count(EmailEnviado.abierto_en).label("abiertos"),
            func.count(EmailEnviado.click_en).label("clickeados"),
        )
        .filter(EmailEnviado.enviado_en >= desde)
        .group_by(EmailEnviado.campania)
        .all()
    )

    def _pct(parte, total):
        return round(100.0 * parte / total, 1) if total else 0.0

    campanias = []
    for f in filas:
        camp = {
            "campania": f.campania,
            "enviados": f.enviados,
            "abiertos": f.abiertos,
            "clickeados": f.clickeados,
            "tasa_apertura": _pct(f.abiertos, f.enviados),
            "tasa_clic": _pct(f.clickeados, f.enviados),
        }

        # Reactivación: solo para secuencias de inactividad, que son las que
        # buscan que el aliado VUELVA. Conversión = login posterior al envío.
        if (f.campania or "").startswith("inactividad"):
            envios = (
                db.query(EmailEnviado.aliado_id, EmailEnviado.enviado_en, Aliado.ultimo_login)
                .join(Aliado, Aliado.id == EmailEnviado.aliado_id)
                .filter(
                    EmailEnviado.campania == f.campania,
                    EmailEnviado.enviado_en >= desde,
                    EmailEnviado.aliado_id.isnot(None),
                )
                .all()
            )
            con_aliado = len(envios)
            reactivados = sum(
                1 for e in envios
                if e.ultimo_login is not None and e.enviado_en is not None
                and e.ultimo_login > e.enviado_en
            )
            camp["reactivados"] = reactivados
            camp["tasa_reactivacion"] = _pct(reactivados, con_aliado)

        campanias.append(camp)

    campanias.sort(key=lambda c: c["enviados"], reverse=True)

    totales = {
        "enviados": sum(c["enviados"] for c in campanias),
        "abiertos": sum(c["abiertos"] for c in campanias),
        "clickeados": sum(c["clickeados"] for c in campanias),
    }
    totales["tasa_apertura"] = _pct(totales["abiertos"], totales["enviados"])
    totales["tasa_clic"] = _pct(totales["clickeados"], totales["enviados"])

    return {
        "ventana_dias": dias,
        "totales": totales,
        "campanias": campanias,
        "nota": ("La tasa de apertura subestima un poco (algunos clientes "
                 "bloquean imágenes). La de clic y la de reactivación son las "
                 "más confiables."),
    }
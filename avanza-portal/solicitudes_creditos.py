"""
solicitudes_creditos.py — Compra de paquetes de créditos (flujo manual).

Cuarto router migrado de main.py (tramo 2 del split). Contiene el flujo
completo: catálogo de paquetes, solicitud del aliado (congela el cambio blue
o fija USD), historial, registro del comprobante, y confirmación/rechazo del
admin que acredita los créditos vía _ajustar_creditos (con huella en
TransaccionCredito).

El job de expiración (job_expirar_solicitudes_creditos) sigue en main.py,
junto al resto del scheduler.

Helpers y constantes compartidos de main (_get_aliado, _ajustar_creditos,
_admin_log, obtener_tipo_de_cambio, DATOS_*, PORTAL_URL) se acceden por
import diferido — patrón documentado en academia.py.
"""
import random
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import schemas
from auth import current_admin_required, verify_ownership_dep
from database import get_db
from models import Aliado, SolicitudCompraCreditos, PAQUETES_CREDITOS
from notificaciones import enviar_email

router = APIRouter(tags=["creditos-compra"])


# ── Puentes diferidos a helpers de main (evitan import circular) ─────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def _ajustar_creditos(db, aliado, delta, motivo, ref=""):
    from main import _ajustar_creditos as f
    return f(db, aliado, delta, motivo, ref)


# ─── COMPRA DE PAQUETES DE CRÉDITOS (v1.7) ───────────────────────────────────
# Flujo manual:
#   1. Aliado elige paquete → POST /aliados/{cod}/solicitar-creditos
#   2. El backend congela el cambio blue, calcula precio_ars, genera código
#      único 'AVZ-XXXX' y devuelve datos bancarios + monto + expires_at.
#   3. Aliado transfiere por su cuenta y avisa por WhatsApp.
#   4. Admin verifica → POST /admin/solicitudes/{id}/confirmar
#      → llama _ajustar_creditos() y dispara email al aliado.
#   5. Si pasan SOLICITUD_CREDITOS_EXPIRACION_HS sin confirmar, el cron la
#      marca como 'expirada' (no se acreditó nada).

def _generar_codigo_referencia(db: Session, prefijo: str = "AVZ", max_intentos: int = 20) -> str:
    """Genera un código único tipo 'AVZ-A4F2' verificando contra la DB.

    Usamos solo caracteres no ambiguos (sin 0/O, 1/I/L) para que sea fácil
    leerlo del comprobante de la transferencia.
    """
    chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sin 0,O,1,I,L
    for _ in range(max_intentos):
        sufijo = ''.join(random.choices(chars, k=4))
        codigo = f"{prefijo}-{sufijo}"
        existe = db.query(SolicitudCompraCreditos).filter(
            SolicitudCompraCreditos.codigo_referencia == codigo
        ).first()
        if not existe:
            return codigo
    # Caso extremadamente improbable: 31^4 ≈ 923k combinaciones, fallback con timestamp
    raise HTTPException(500, "No se pudo generar código de referencia único. Reintentá.")


def _redondear_ars_arriba(monto: float, multiplo: int = 100) -> float:
    """Redondea ARS al alza al múltiplo indicado, para que el monto sea limpio
    de transferir y no quede vulnerable a micro-movimientos del blue."""
    import math
    return float(math.ceil(monto / multiplo) * multiplo)


def _paquete_a_dict_publico(paquete_id: str, paquete: dict, tipo_cambio: float) -> dict:
    """Formatea un paquete para respuesta API, calculando ARS al cambio dado."""
    precio_usd = paquete["precio_usd"]
    precio_ars = _redondear_ars_arriba(precio_usd * tipo_cambio)
    return {
        "id":           paquete_id,
        "nombre":       paquete["nombre"],
        "creditos":     paquete["creditos"],
        "descripcion":  paquete["descripcion"],
        "destacado":    paquete.get("destacado", False),
        "orden":        paquete.get("orden", 99),
        "precio_usd":   precio_usd,
        "precio_ars":   precio_ars,
        "usd_por_credito": round(precio_usd / paquete["creditos"], 4),
    }


def _solicitud_a_dict(s: SolicitudCompraCreditos, incluir_aliado: bool = False) -> dict:
    """Formatea una SolicitudCompraCreditos para respuesta API."""
    paquete = PAQUETES_CREDITOS.get(s.paquete_id, {})
    out = {
        "id":                s.id,
        "paquete_id":        s.paquete_id,
        "paquete_nombre":    paquete.get("nombre", s.paquete_id.title()),
        "creditos":          s.creditos,
        "moneda":            getattr(s, "moneda", "ars") or "ars",
        "precio_usd":        s.precio_usd,
        "precio_ars":        s.precio_ars,
        "tipo_cambio_blue":  s.tipo_cambio_blue,
        "codigo_referencia": s.codigo_referencia,
        "comprobante_url":   s.comprobante_url,
        "estado":            s.estado,
        "notas_admin":       s.notas_admin,
        "creado_en":         s.creado_en.isoformat() if s.creado_en else None,
        "expires_at":        s.expires_at.isoformat() if s.expires_at else None,
        "confirmado_en":     s.confirmado_en.isoformat() if s.confirmado_en else None,
    }
    if incluir_aliado and s.aliado:
        out["aliado"] = {
            "id":     s.aliado.id,
            "codigo": s.aliado.codigo,
            "nombre": s.aliado.nombre,
            "email":  s.aliado.email,
        }
    return out


# --- Endpoint público: listar paquetes con precio ARS al cambio actual ---
@router.get("/paquetes-creditos")
async def listar_paquetes_creditos():
    """Listado de paquetes de créditos con precio USD fijo + ARS al cambio del día.

    El ARS que devuelve es ORIENTATIVO. El precio real se congela al generar
    la solicitud (POST /aliados/{cod}/solicitar-creditos).
    """
    from main import obtener_tipo_de_cambio, SOLICITUD_CREDITOS_EXPIRACION_HS
    tc = await obtener_tipo_de_cambio()
    paquetes = [
        _paquete_a_dict_publico(pid, p, tc)
        for pid, p in PAQUETES_CREDITOS.items()
    ]
    paquetes.sort(key=lambda x: x["orden"])
    return {
        "paquetes":           paquetes,
        "tipo_cambio_blue":   tc,
        "moneda_referencia":  "ARS",
        "expira_en_hs":       SOLICITUD_CREDITOS_EXPIRACION_HS,
    }


# --- Endpoint del aliado: crear solicitud de compra ---
@router.post("/aliados/{codigo}/solicitar-creditos")
async def solicitar_creditos(codigo: str,
                              body: schemas.SolicitarCreditosIn,
                              db: Session = Depends(get_db),
                              _owner=Depends(verify_ownership_dep)):
    """El aliado genera una solicitud de compra. Se congela el cambio blue,
    se calcula precio_ars y se devuelve toda la info para que transfiera.

    Soporta dos monedas (v1.9):
      - 'ars': transferencia bancaria en pesos (cambio blue del momento, vigente 48hs).
      - 'usd': pago en dólares (USDT TRC20). Sin conversión.
    """
    from main import (obtener_tipo_de_cambio, SOLICITUD_CREDITOS_EXPIRACION_HS,
                      DATOS_BANCARIOS, DATOS_USD, DATOS_PAYONEER)
    a = _get_aliado(codigo, db)

    paquete = PAQUETES_CREDITOS.get(body.paquete_id)
    if not paquete:
        raise HTTPException(400, f"Paquete '{body.paquete_id}' no existe.")

    moneda = (body.moneda or "ars").lower()
    if moneda not in ("ars", "usd"):
        raise HTTPException(400, "Moneda inválida. Usar 'ars' o 'usd'.")

    # Anti-spam: si tiene >3 solicitudes pendientes, bloquear hasta que las resuelva.
    pendientes = db.query(SolicitudCompraCreditos).filter(
        SolicitudCompraCreditos.aliado_id == a.id,
        SolicitudCompraCreditos.estado == "pendiente",
    ).count()
    if pendientes >= 3:
        raise HTTPException(400, f"Tenés {pendientes} solicitudes pendientes. "
                                  "Esperá a que se confirmen o expiren antes de generar otra.")

    # Congelar precio en el momento de generar
    precio_usd  = paquete["precio_usd"]
    if moneda == "ars":
        tipo_cambio = await obtener_tipo_de_cambio()
        precio_ars  = _redondear_ars_arriba(precio_usd * tipo_cambio)
    else:
        # USD: no hay conversión. Guardamos placeholders coherentes para no romper
        # vistas históricas que asumen ARS (precio_ars y tipo_cambio_blue son NOT NULL).
        tipo_cambio = 1.0
        precio_ars  = precio_usd  # mismo número, distinta moneda según el campo `moneda`
    expires_at  = datetime.now() + timedelta(hours=SOLICITUD_CREDITOS_EXPIRACION_HS)
    codigo_ref  = _generar_codigo_referencia(db)

    sol = SolicitudCompraCreditos(
        aliado_id         = a.id,
        paquete_id        = body.paquete_id,
        creditos          = paquete["creditos"],
        moneda            = moneda,
        precio_usd        = precio_usd,
        tipo_cambio_blue  = tipo_cambio,
        precio_ars        = precio_ars,
        codigo_referencia = codigo_ref,
        estado            = "pendiente",
        expires_at        = expires_at,
    )
    db.add(sol); db.commit(); db.refresh(sol)

    # Texto del monto para emails y mensajes (depende de la moneda elegida)
    if moneda == "ars":
        monto_str = f"ARS {precio_ars:,.0f}"
        detalle_admin = f"<strong>ARS {precio_ars:,.0f}</strong> (USD {precio_usd:.0f} × ${tipo_cambio:.0f})"
    else:
        monto_str = f"USD {precio_usd:.2f}"
        detalle_admin = f"<strong>USD {precio_usd:.2f}</strong> · vía {DATOS_USD['metodo']}"

    # Notificar al admin que hay una nueva solicitud por revisar
    try:
        admin_email = DATOS_BANCARIOS.get("email_admin")
        if admin_email:
            html_admin = f"""
            <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
              <h2 style="color:#fbbf24;margin:0 0 8px;">💰 Nueva solicitud de compra de créditos</h2>
              <p><strong>{a.nombre}</strong> ({a.codigo}) generó una solicitud:</p>
              <ul style="line-height:1.8;">
                <li>Paquete: <strong>{paquete['nombre']}</strong> — {paquete['creditos']} créditos</li>
                <li>Moneda: <strong>{moneda.upper()}</strong></li>
                <li>Monto a recibir: {detalle_admin}</li>
                <li>Código de referencia: <code style="background:#1e293b;padding:2px 6px;border-radius:4px;">{codigo_ref}</code></li>
                <li>Vence: {expires_at.strftime('%d/%m/%Y %H:%M')}hs</li>
              </ul>
              <p style="color:#a1a1aa;font-size:.85rem;">Cuando llegue el pago, verificá el monto y confirmá desde el panel admin.</p>
            </div>
            """
            enviar_email(admin_email, f"💰 Solicitud {codigo_ref} ({moneda.upper()}): {a.nombre} quiere comprar {paquete['nombre']}", html_admin)
    except Exception as e:
        print(f"[SOLICITUD CREDITOS] Email admin falló: {e}")

    # Construir mensaje pre-armado de WhatsApp (URL-encoded)
    from urllib.parse import quote as _urlquote
    if moneda == "ars":
        wa_msg = (f"Hola! Soy {a.nombre} (código {a.codigo}). "
                  f"Acabo de transferir ARS {precio_ars:,.0f} para comprar el paquete {paquete['nombre']}. "
                  f"Código de referencia: {codigo_ref}")
    else:
        wa_msg = (f"Hola! Soy {a.nombre} (código {a.codigo}). "
                  f"Acabo de pagar USD {precio_usd:.2f} vía {DATOS_USD['metodo']} "
                  f"para comprar el paquete {paquete['nombre']}. "
                  f"Código de referencia: {codigo_ref}")
    wa_url = f"https://wa.me/{DATOS_BANCARIOS['whatsapp_link']}?text={_urlquote(wa_msg)}"

    # Datos de pago según moneda
    if moneda == "ars":
        datos_pago = {
            "tipo":              "transferencia_ars",
            "titular":           DATOS_BANCARIOS["titular"],
            "banco":             DATOS_BANCARIOS["banco"],
            "alias":             DATOS_BANCARIOS["alias"],
            "cbu":               DATOS_BANCARIOS["cbu"],
            "whatsapp_display":  DATOS_BANCARIOS["whatsapp_display"],
            "whatsapp_url":      wa_url,
        }
        instrucciones = {
            "monto_a_transferir":   f"ARS {precio_ars:,.0f}",
            "concepto_obligatorio": codigo_ref,
            "vence_en":             expires_at.isoformat(),
            "aviso":                "Confirmamos en menos de 24hs hábiles. Recibís email cuando se acrediten los créditos.",
            "politica":             "Los créditos no vencen y no son reembolsables (excepto error técnico).",
        }
    else:
        # v2.2 — Dos métodos USD disponibles: USDT y Payoneer. El aliado elige en el front.
        metodos_usd = [
            {
                "id":           "usdt",
                "metodo":       DATOS_USD["metodo"],
                "destinatario": DATOS_USD["destinatario"],
                "etiqueta_dest":DATOS_USD["etiqueta_dest"],
                "red":          DATOS_USD["red"],
                "notas":        DATOS_USD["notas"],
            },
            {
                "id":           "payoneer",
                "metodo":       DATOS_PAYONEER["metodo"],
                "destinatario": DATOS_PAYONEER["destinatario"],
                "etiqueta_dest":DATOS_PAYONEER["etiqueta_dest"],
                "red":          DATOS_PAYONEER["red"],
                "notas":        DATOS_PAYONEER["notas"],
                "banco":        DATOS_PAYONEER["banco"],
            },
        ]
        datos_pago = {
            "tipo":              "pago_usd",
            # Primer método por defecto (compatibilidad con vistas que leen estos campos sueltos)
            "metodo":            metodos_usd[0]["metodo"],
            "destinatario":      metodos_usd[0]["destinatario"],
            "etiqueta_dest":     metodos_usd[0]["etiqueta_dest"],
            "red":               metodos_usd[0]["red"],
            "notas":             metodos_usd[0]["notas"],
            "metodos_usd":       metodos_usd,
            "titular":           DATOS_BANCARIOS["titular"],
            "whatsapp_display":  DATOS_BANCARIOS["whatsapp_display"],
            "whatsapp_url":      wa_url,
        }
        instrucciones = {
            "monto_a_transferir":   f"USD {precio_usd:.2f}",
            "concepto_obligatorio": codigo_ref,
            "vence_en":             expires_at.isoformat(),
            "aviso":                "Confirmamos en menos de 24hs hábiles tras recibir el pago. Recibís email cuando se acrediten los créditos.",
            "politica":             "Los créditos no vencen y no son reembolsables (excepto error técnico).",
        }

    return {
        "solicitud":       _solicitud_a_dict(sol),
        # Mantenemos `datos_bancarios` por compatibilidad cuando moneda='ars'
        "datos_bancarios": datos_pago if moneda == "ars" else None,
        "datos_pago":      datos_pago,
        "instrucciones":   instrucciones,
    }


# --- Endpoint del aliado: ver historial de sus solicitudes ---
@router.get("/aliados/{codigo}/solicitudes-creditos")
def historial_solicitudes_creditos(codigo: str,
                                    limit: int = 20,
                                    db: Session = Depends(get_db),
                                    _owner=Depends(verify_ownership_dep)):
    """Últimas N solicitudes del aliado, más recientes primero."""
    a = _get_aliado(codigo, db)
    limit = max(1, min(limit, 100))
    solicitudes = db.query(SolicitudCompraCreditos).filter(
        SolicitudCompraCreditos.aliado_id == a.id
    ).order_by(SolicitudCompraCreditos.creado_en.desc()).limit(limit).all()
    return {"solicitudes": [_solicitud_a_dict(s) for s in solicitudes]}


# --- Endpoint del aliado: registrar URL del comprobante de transferencia ---
@router.post("/aliados/{codigo}/solicitudes/{sol_id}/comprobante")
def registrar_comprobante(codigo: str, sol_id: int,
                           body: schemas.RegistrarComprobanteIn,
                           db: Session = Depends(get_db),
                           _owner=Depends(verify_ownership_dep)):
    """El aliado pega la URL de su comprobante (Drive, Imgur, Photos, etc.)
    para que el admin lo vea más rápido cuando confirma."""
    a = _get_aliado(codigo, db)
    s = db.query(SolicitudCompraCreditos).filter(
        SolicitudCompraCreditos.id == sol_id,
        SolicitudCompraCreditos.aliado_id == a.id,
    ).first()
    if not s:
        raise HTTPException(404, "Solicitud no encontrada.")
    if s.estado != "pendiente":
        raise HTTPException(400, f"La solicitud está en estado '{s.estado}', no se puede modificar.")

    s.comprobante_url = body.comprobante_url.strip()
    db.commit()
    return {"mensaje": "Comprobante registrado.", "solicitud": _solicitud_a_dict(s)}


# --- Endpoint admin: listar solicitudes con filtros ---
@router.get("/admin/solicitudes-creditos")
def admin_listar_solicitudes(estado: str = "pendiente",
                              limit: int = 50,
                              db: Session = Depends(get_db),
                              _admin=Depends(current_admin_required)):
    """Listado para el panel admin. estado='all' devuelve todas."""
    limit = max(1, min(limit, 200))
    q = db.query(SolicitudCompraCreditos)
    if estado and estado != "all":
        if estado not in ("pendiente", "confirmada", "rechazada", "expirada"):
            raise HTTPException(400, "Estado inválido.")
        q = q.filter(SolicitudCompraCreditos.estado == estado)
    solicitudes = q.order_by(SolicitudCompraCreditos.creado_en.desc()).limit(limit).all()
    return {
        "solicitudes": [_solicitud_a_dict(s, incluir_aliado=True) for s in solicitudes],
        "filtros": {"estado": estado, "limit": limit},
        "pendientes_total": db.query(SolicitudCompraCreditos).filter(
            SolicitudCompraCreditos.estado == "pendiente"
        ).count(),
    }


# --- Endpoint admin: confirmar solicitud + acreditar créditos ---
@router.post("/admin/solicitudes-creditos/{sol_id}/confirmar")
def admin_confirmar_solicitud(sol_id: int,
                               request: Request,
                               admin: dict = Depends(current_admin_required),
                               db: Session = Depends(get_db)):
    """Marca como confirmada y acredita los créditos al aliado.

    IDEMPOTENTE: si la solicitud ya está confirmada, devuelve OK sin hacer nada.
    Esto protege contra doble click en el botón del admin.
    """
    from main import _admin_log, PORTAL_URL
    s = db.query(SolicitudCompraCreditos).filter(
        SolicitudCompraCreditos.id == sol_id
    ).first()
    if not s:
        raise HTTPException(404, "Solicitud no encontrada.")

    if s.estado == "confirmada":
        # Ya estaba confirmada → idempotencia, devolvemos OK sin acreditar de nuevo
        return {"mensaje": "Solicitud ya estaba confirmada.", "solicitud": _solicitud_a_dict(s)}

    if s.estado != "pendiente":
        raise HTTPException(400, f"No se puede confirmar una solicitud en estado '{s.estado}'.")

    a = db.query(Aliado).filter(Aliado.id == s.aliado_id).first()
    if not a:
        raise HTTPException(500, "Aliado de la solicitud no existe.")

    # Acreditar créditos usando el helper existente (deja huella en TransaccionCredito)
    _ajustar_creditos(db, a, s.creditos, "compra_paquete", f"solicitud:{s.id}:{s.codigo_referencia}")

    s.estado = "confirmada"
    s.confirmado_en = datetime.now()
    _admin_log(
        db, admin, request,
        accion="aprobar_solicitud_creditos",
        entidad="solicitud_creditos", entidad_id=s.id,
        detalle={
            "aliado_codigo": a.codigo,
            "creditos":      s.creditos,
            "precio_ars":    float(s.precio_ars or 0),
            "codigo_referencia": s.codigo_referencia,
        },
    )
    db.commit(); db.refresh(s)

    # Email al aliado
    try:
        if a.email:
            html = f"""
            <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
              <h2 style="color:#34d399;margin:0 0 8px;">✅ Créditos acreditados</h2>
              <p>Hola <strong>{(a.nombre or '').split()[0]}</strong>,</p>
              <p>Confirmamos tu transferencia de <strong>ARS {s.precio_ars:,.0f}</strong>. Acabamos de acreditar
              <strong style="color:#fbbf24;">{s.creditos} créditos</strong> a tu cuenta.</p>
              <p>Tu nuevo saldo: <strong>{a.creditos or 0} créditos</strong>.</p>
              <p style="color:#a1a1aa;font-size:.85rem;">Código de referencia: {s.codigo_referencia}</p>
              <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#fbbf24;color:#000;border-radius:8px;text-decoration:none;font-weight:700;">Ir a la bolsa de leads →</a>
            </div>
            """
            enviar_email(a.email, f"✅ {s.creditos} créditos acreditados — Avanza Digital", html)
    except Exception as e:
        print(f"[CONFIRMAR SOLICITUD] Email aliado falló: {e}")

    return {"mensaje": f"Solicitud confirmada. Se acreditaron {s.creditos} créditos.",
            "solicitud":   _solicitud_a_dict(s),
            "saldo_nuevo": a.creditos or 0}


# --- Endpoint admin: rechazar solicitud ---
@router.post("/admin/solicitudes-creditos/{sol_id}/rechazar")
def admin_rechazar_solicitud(sol_id: int,
                              request: Request,
                              body: schemas.RechazarSolicitudIn,
                              admin: dict = Depends(current_admin_required),
                              db: Session = Depends(get_db)):
    """Marca la solicitud como rechazada. NO acredita créditos."""
    from main import _admin_log, DATOS_BANCARIOS
    s = db.query(SolicitudCompraCreditos).filter(
        SolicitudCompraCreditos.id == sol_id
    ).first()
    if not s:
        raise HTTPException(404, "Solicitud no encontrada.")

    if s.estado != "pendiente":
        raise HTTPException(400, f"No se puede rechazar una solicitud en estado '{s.estado}'.")

    s.estado = "rechazada"
    s.notas_admin = body.motivo.strip()
    s.confirmado_en = datetime.now()
    _admin_log(
        db, admin, request,
        accion="rechazar_solicitud_creditos",
        entidad="solicitud_creditos", entidad_id=s.id,
        detalle={"motivo": body.motivo, "codigo_referencia": s.codigo_referencia},
    )
    db.commit(); db.refresh(s)

    # Email al aliado con el motivo
    try:
        a = db.query(Aliado).filter(Aliado.id == s.aliado_id).first()
        if a and a.email:
            html = f"""
            <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
              <h2 style="color:#f87171;margin:0 0 8px;">❌ Solicitud rechazada</h2>
              <p>Hola <strong>{(a.nombre or '').split()[0]}</strong>,</p>
              <p>Tu solicitud <code style="background:#1e293b;padding:2px 6px;border-radius:4px;">{s.codigo_referencia}</code> fue rechazada.</p>
              <p><strong>Motivo:</strong> {body.motivo}</p>
              <p style="color:#a1a1aa;font-size:.85rem;">Si creés que es un error, escribinos por WhatsApp al {DATOS_BANCARIOS['whatsapp_display']}.</p>
            </div>
            """
            enviar_email(a.email, f"❌ Solicitud {s.codigo_referencia} rechazada", html)
    except Exception as e:
        print(f"[RECHAZAR SOLICITUD] Email aliado falló: {e}")

    return {"mensaje": "Solicitud rechazada.", "solicitud": _solicitud_a_dict(s)}
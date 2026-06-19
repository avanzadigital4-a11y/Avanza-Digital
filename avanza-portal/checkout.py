"""
checkout.py — Checkout, links de pago y webhooks de cobro (DINERO).

Decimotercer router migrado de main.py (tramo 7 del split — sub-dominio
checkout/pagos). Migrado con máxima cautela: por acá entra la plata.
Contiene:
  - Creación de links de pago: MercadoPago (preferencia ARS al tipo de
    cambio blue) y USDT TRC20 (dirección derivada por índice desde la xpub
    de TRON), con expiración a 48hs, /checkout/ultimo-link, regeneración
    tras vencimiento e historial por aliado.
  - verificar_firma_mp: validación HMAC-SHA256 del webhook (spec §19),
    FAIL-CLOSED si falta MP_WEBHOOK_SECRET salvo AVANZA_INSECURE_WEBHOOKS=1.
  - /webhooks/mercadopago (+ /checkout/webhook legacy que delega): verifica
    firma, consulta el pago a la API de MP y delega en el helper común.
  - _procesar_pago_confirmado: EL helper del dinero. Idempotente por token
    [PID:payment_id] delimitado en notas (MP reenvía webhooks). Registra
    Venta + Comisión, 5% al sponsor, marca LinkPago pagado, actualiza el
    prospecto, bonus de primera venta (helper en main, dominio créditos),
    notifica por WhatsApp/email (copy IA con fallback) y manda el Tally de
    onboarding al cliente. Para planes de continuidad delega en
    _procesar_pago_continuidad_confirmado, que crea el PlanContinuidadActivo
    y la primera comisión vía el motor de comisiones.py.
    main.py lo re-exporta por compat (tests/test_checkout_y_webhooks.py lo
    importa de main).
  - /tipo-de-cambio (cotizador), /config/usdt y /admin/pagos (con
    Depends(current_admin_required) explícito, criterio de tramos previos).

Las constantes de pago (MP_*, USDT_*, TRON_*, BACKEND_PUBLIC_URL) y los
helpers transversales (obtener_tipo_de_cambio — también lo usa
solicitudes_creditos.py — y _aplicar_bonus_primera_venta, dominio créditos)
quedan en main y se acceden por import diferido.
"""
import hashlib
import hmac as hmac_lib
import json
import os
import sys
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

import groq_ai
import jarvis_canal1
from auth import current_aliado_required, current_admin_required, verify_ownership_dep
from comisiones import _crear_comisiones_recurrentes_para_plan
from database import get_db
from models import (
    Aliado, Comision, LinkPago, PlanContinuidadActivo, Prospecto, Referido, Venta,
    COMISION_RECURRENTE_PCT, PLANES, PLANES_CONTINUIDAD,
)
from notificaciones import enviar_email, notificar_aliado
from rate_limit import limiter

router = APIRouter(tags=["checkout"])


# ── Puentes diferidos a helpers de main (evitan import circular) ─────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def verificar_firma_mp(raw_body: bytes, headers, query_params) -> bool:
    """Verifica firma HMAC-SHA256 del webhook de Mercado Pago.
    MP envía el header x-signature con formato: `ts=<ts>,v1=<hash>`.
    El manifest firmado es: `id:<data.id>;request-id:<x-request-id>;ts:<ts>;`.

    FAIL-CLOSED: si MP_WEBHOOK_SECRET no está seteado, devuelve False salvo que
    AVANZA_INSECURE_WEBHOOKS=1 (modo dev local explícito). Esto previene que un
    deploy con env var faltante quede aceptando webhooks falsos.
    """
    from main import MP_WEBHOOK_SECRET  # diferido: const/helper de main (evita import circular)
    if not MP_WEBHOOK_SECRET:
        if os.environ.get("AVANZA_INSECURE_WEBHOOKS") == "1":
            print("[MP WEBHOOK] AVANZA_INSECURE_WEBHOOKS=1 — validación desactivada (SOLO DEV)")
            return True
        print("[MP WEBHOOK] ❌ MP_WEBHOOK_SECRET no seteada — rechazando webhook (fail-closed)")
        return False

    x_signature = headers.get("x-signature") or headers.get("X-Signature")
    x_request_id = headers.get("x-request-id") or headers.get("X-Request-Id") or ""
    if not x_signature:
        print("[MP WEBHOOK] Falta header x-signature")
        return False

    # Extraer ts y v1
    ts, v1 = None, None
    for parte in x_signature.split(","):
        parte = parte.strip()
        if parte.startswith("ts="):
            ts = parte.split("=", 1)[1]
        elif parte.startswith("v1="):
            v1 = parte.split("=", 1)[1]
    if not ts or not v1:
        print(f"[MP WEBHOOK] Formato de x-signature inválido: {x_signature}")
        return False

    # data.id puede venir por query string (?data.id=123) o en el body
    data_id = query_params.get("data.id") or ""
    if not data_id and raw_body:
        try:
            body_json = json.loads(raw_body)
            data_id = str(body_json.get("data", {}).get("id", ""))
        except Exception:
            pass

    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    hash_calc = hmac_lib.new(
        MP_WEBHOOK_SECRET.encode(),
        manifest.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac_lib.compare_digest(hash_calc, v1):
        print(f"[MP WEBHOOK] Firma inválida. Calc: {hash_calc[:16]}… Recibido: {v1[:16]}…")
        return False
    return True



# ─── CHECKOUT: MP (ARS) + USDT TRC20 (USD) ───────────────────────────────────
# Spec §2, §3, §4, §5: el aliado elige moneda. MP usa conversión blue en tiempo real.
# USDT cobra en USD fijo. Ambos links expiran en 48hs.

LINK_EXPIRATION_HOURS = 48


# v1.5 — Helpers unificados para soportar tanto planes one-shot (PLANES) como
# planes de continuidad mensuales (PLANES_CONTINUIDAD) en el mismo flujo de
# checkout/webhook.
def _es_plan_continuidad(plan: str) -> bool:
    return plan in PLANES_CONTINUIDAD


def _precio_de_plan(plan: str) -> float:
    """Devuelve el precio USD del plan, ya sea one-shot o continuidad.
    Para continuidad es el precio MENSUAL (lo que paga el cliente cada mes;
    el primer mes es lo que cobra el checkout)."""
    if plan in PLANES:
        return float(PLANES[plan])
    if plan in PLANES_CONTINUIDAD:
        return float(PLANES_CONTINUIDAD[plan])
    raise KeyError(f"Plan desconocido: {plan}")


async def _crear_link_mp(a: Aliado, plan: str, nombre_cliente: str, db: Session):
    """Crea una preferencia en MP con precio en ARS usando dolarapi blue del momento."""
    from main import (BACKEND_PUBLIC_URL, FAILURE_URL, MP_ACCESS_TOKEN,
                      SUCCESS_URL, obtener_tipo_de_cambio)  # diferido: const/helper de main (evita import circular)
    if not MP_ACCESS_TOKEN:
        raise HTTPException(503, "MP_ACCESS_TOKEN no está configurado.")

    valor_usd = _precio_de_plan(plan)
    tipo_cambio = await obtener_tipo_de_cambio()
    precio_ars = round(valor_usd * tipo_cambio, 2)
    external_ref = f"{a.ref_code}|{plan}|{nombre_cliente}"
    expires_at = datetime.now() + timedelta(hours=LINK_EXPIRATION_HOURS)

    preference_data = {
        "items": [{
            "title": f"Avanza Digital — {plan}",
            "quantity": 1,
            "unit_price": float(precio_ars),
            "currency_id": "ARS",
        }],
        "payer": {"name": nombre_cliente},
        "external_reference": external_ref,
        # FIX: usar isoformat() con timespec='milliseconds' para obtener el formato "2026-04-24T15:30:00.000-03:00"
        # que MP acepta sin ambigüedad. strftime('%z') daba "-0300" sin los dos puntos.
        "date_of_expiration": expires_at.astimezone().isoformat(timespec='milliseconds'),
        "back_urls": {
            "success": SUCCESS_URL,
            "failure": FAILURE_URL,
            "pending": FAILURE_URL,
        },
        "auto_return": "approved",
        # FIX: el webhook es un endpoint del BACKEND, no del portal frontend
        "notification_url": f"{BACKEND_PUBLIC_URL}/webhooks/mercadopago",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.mercadopago.com/checkout/preferences",
                json=preference_data,
                headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}",
                         "Content-Type": "application/json"},
            )
            if resp.status_code not in (200, 201):
                raise HTTPException(502, f"Error MercadoPago: {resp.text[:200]}")
            pref = resp.json()
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MP ERROR] Error de red al crear preferencia: {e}")
        raise HTTPException(502, f"No se pudo conectar con MercadoPago. Intentá de nuevo en unos segundos.")

    link = LinkPago(
        aliado_id    = a.id,
        plan         = plan,
        moneda       = "ars",
        precio_usd   = valor_usd,
        precio_ars   = precio_ars,
        tipo_cambio  = tipo_cambio,
        checkout_url = pref["init_point"],
        processor    = "mercadopago",
        external_ref = external_ref,
        expires_at   = expires_at,
        estado       = "activo",
    )
    db.add(link); db.commit(); db.refresh(link)

    return {
        "checkout_url": pref["init_point"],
        "link_id":      link.id,
        "moneda":       "ars",
        "plan":         plan,
        "precio_usd":   valor_usd,
        "precio_ars":   precio_ars,
        "tipo_cambio":  tipo_cambio,
        "processor":    "mercadopago",
        "expires_at":   expires_at.isoformat(),
        "aliado":       a.nombre,
        "fallback":     False,
    }


async def _crear_link_usdt(a, plan: str, nombre_cliente: str, db):
    """Crea un LinkPago USDT con dirección HD única derivada del ID del registro."""
    from main import TRON_MNEMONIC, TRON_XPUB, USDT_RED  # diferido: const/helper de main (evita import circular)
    from tronpy.keys import PrivateKey
    valor_usd    = _precio_de_plan(plan)
    external_ref = f"{a.ref_code}|{plan}|{nombre_cliente}"
    expires_at   = datetime.now() + timedelta(hours=LINK_EXPIRATION_HOURS)

    # Crear registro primero para obtener el ID (índice HD único)
    lp = LinkPago(
        aliado_id    = a.id,
        plan         = plan,
        moneda       = "usd",
        precio_usd   = valor_usd,
        checkout_url = "",
        processor    = "usdt",
        external_ref = external_ref,
        expires_at   = expires_at,
        estado       = "activo",
    )
    db.add(lp)
    db.flush()  # obtener lp.id sin commit

    if not (TRON_XPUB or TRON_MNEMONIC):
        db.rollback()
        raise HTTPException(503, "USDT no configurado: falta TRON_XPUB (recomendado) o TRON_MNEMONIC.")

    try:
        if TRON_XPUB:
            # ── CAMINO SEGURO: derivación solo-pública desde la xpub ──────────
            # El servidor NO tiene la semilla ni ninguna clave privada. Aunque
            # comprometan el server, no pueden mover fondos. Path resultante:
            # m/44'/195'/0'/0/{lp.id} — idéntico al esquema histórico, así que
            # las direcciones coinciden con la misma wallet de siempre.
            import tron_xpub
            usdt_address = tron_xpub.direccion_desde_xpub(TRON_XPUB, lp.id)
        else:
            # ── CAMINO LEGACY (INSEGURO): semilla en el servidor ──────────────
            # Mantener solo durante la migración. Generar la xpub offline con
            # `python generar_xpub_offline.py`, configurar TRON_XPUB y borrar
            # TRON_MNEMONIC de las variables de entorno del servidor.
            print("[USDT] ⚠️  Derivando con TRON_MNEMONIC (semilla en el server). "
                  "Migrá a TRON_XPUB y borrá la mnemónica del servidor.", file=sys.stderr)
            import hmac as _hmac, hashlib as _hashlib, struct as _struct
            from mnemonic import Mnemonic as _Mnemonic
            import ecdsa as _ecdsa

            _ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

            def _bip32_child(key_b, chain_b, idx, hardened):
                i = idx + (0x80000000 if hardened else 0)
                if hardened:
                    data = b'\x00' + key_b + _struct.pack('>I', i)
                else:
                    sk = _ecdsa.SigningKey.from_string(key_b, curve=_ecdsa.SECP256k1)
                    vk = sk.get_verifying_key().to_string()
                    prefix = b'\x02' if int.from_bytes(vk[32:], 'big') % 2 == 0 else b'\x03'
                    data = prefix + vk[:32] + _struct.pack('>I', i)
                Il = _hmac.new(chain_b, data, _hashlib.sha512).digest()
                child_key = (int.from_bytes(Il[:32], 'big') + int.from_bytes(key_b, 'big')) % _ORDER
                return child_key.to_bytes(32, 'big'), Il[32:]

            _seed  = _Mnemonic("english").to_seed(TRON_MNEMONIC)
            _I     = _hmac.new(b"Bitcoin seed", _seed, _hashlib.sha512).digest()
            _key, _chain = _I[:32], _I[32:]
            for _si, _sh in [(44, True), (195, True), (0, True), (0, False), (lp.id, False)]:
                _key, _chain = _bip32_child(_key, _chain, _si, _sh)

            usdt_address = PrivateKey(bytes.fromhex(_key.hex())).public_key.to_base58check_address()
    except Exception as e:
        db.rollback()
        raise HTTPException(503, f"Error generando dirección TRON: {e}")

    lp.usdt_address   = usdt_address
    lp.usdt_monto_exp = valor_usd
    lp.checkout_url   = f"tron:{usdt_address}?amount={valor_usd}"
    db.commit()
    db.refresh(lp)

    return {
        "tipo":         "usdt",
        "link_id":      lp.id,
        "checkout_url": lp.checkout_url,
        "direccion":    usdt_address,
        "red":          USDT_RED or "TRC20",
        "monto_usdt":   valor_usd,
        "moneda":       "usd",
        "plan":         plan,
        "precio_usd":   valor_usd,
        "processor":    "usdt",
        "expires_at":   expires_at.isoformat(),
        "aliado":       a.nombre,
        "fallback":     False,
    }



@router.post("/checkout/crear")
@limiter.limit("20/hour")
async def crear_checkout(request: Request, plan: str,
                         ref_code: str,
                         nombre_cliente: str = "Cliente",
                         cliente_email: str = "",
                         cliente_whatsapp: str = "",
                         moneda: str = "ars",
                         prospecto_id: int = None,
                         db: Session = Depends(get_db)):
    """Crea un link de pago. `moneda` = 'ars' (MercadoPago) o 'usd' (USDT TRC20).
    Spec §5: ambos flujos generan registros en links_pago con expiración a 48hs."""
    from main import MP_ACCESS_TOKEN, TRON_MNEMONIC, TRON_XPUB  # diferido: const/helper de main (evita import circular)
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not a:
        raise HTTPException(404, "Código de referido inválido.")
    if plan not in PLANES and plan not in PLANES_CONTINUIDAD:
        raise HTTPException(400, "Plan inválido.")

    moneda = (moneda or "ars").lower()
    if moneda not in ("ars", "usd"):
        raise HTTPException(400, "Moneda inválida. Usar 'ars' o 'usd'.")

    # Crear prospecto automáticamente si no existe uno con ese cliente reciente
    try:
        reciente = db.query(Prospecto).filter(
            Prospecto.aliado_id == a.id,
            Prospecto.nombre == nombre_cliente,
            Prospecto.creado_en >= datetime.now() - timedelta(hours=48),
        ).first()
        if not reciente and nombre_cliente and nombre_cliente != "Cliente":
            p = Prospecto(aliado_id=a.id, nombre=nombre_cliente,
                          plan_interes=plan, estado="propuesta_enviada",
                          nota=f"Auto-creado al generar link de pago ({moneda.upper()}). Email: {cliente_email or '—'} | WA: {cliente_whatsapp or '—'}")
            db.add(p); db.commit()
        elif reciente and (cliente_email or cliente_whatsapp):
            # Actualizar el prospecto existente con datos de contacto si los tenemos
            if not reciente.nota or "Email:" not in reciente.nota:
                reciente.nota = (reciente.nota or "") + f" | Email: {cliente_email or '—'} | WA: {cliente_whatsapp or '—'}"
                db.commit()
    except Exception as e:
        print(f"[CHECKOUT] No pude auto-crear prospecto: {e}")

    # Fallback si no hay credenciales configuradas
    if moneda == "ars" and not MP_ACCESS_TOKEN:
        return {
            "checkout_url": f"https://avanzadigital.digital/contratar?plan={plan}&ref={ref_code}",
            "fallback": True,
            "mensaje": "MercadoPago no activado. Configurar MP_ACCESS_TOKEN.",
        }
    if moneda == "usd" and not (TRON_XPUB or TRON_MNEMONIC):
        return {
            "checkout_url": f"https://avanzadigital.digital/contratar?plan={plan}&ref={ref_code}",
            "fallback": True,
            "mensaje": "USDT no activado. Configurar TRON_XPUB (ver generar_xpub_offline.py).",
        }

    if moneda == "ars":
        resultado = await _crear_link_mp(a, plan, nombre_cliente, db)
    else:
        resultado = await _crear_link_usdt(a, plan, nombre_cliente, db)

    # Atribucion de equipo: ligar el link al prospecto handed-off (si se paso) para que
    # al pagarse la comision de sistemas se reparta con el setter.
    if prospecto_id:
        try:
            _lid = resultado.get("link_id")
            if _lid:
                _lp = db.query(LinkPago).filter(LinkPago.id == _lid).first()
                if _lp:
                    _lp.prospecto_id = prospecto_id
                    db.commit()
        except Exception as _e:
            print(f"[CHECKOUT] No pude ligar prospecto al link: {_e}")

    # Guardar email y whatsapp del cliente en el LinkPago para recuperarlos
    # cuando llegue el webhook de pago confirmado y mandar el Tally correcto.
    if cliente_email or cliente_whatsapp:
        try:
            link_id = resultado.get("link_id")
            if link_id:
                lp = db.query(LinkPago).filter(LinkPago.id == link_id).first()
                if lp:
                    # Guardamos los datos en external_ref extendido (no-breaking)
                    # Formato: "ref_code|plan|nombre_cliente|email|whatsapp"
                    partes = (lp.external_ref or "").split("|")
                    while len(partes) < 3:
                        partes.append("")
                    if len(partes) == 3:
                        partes.append(cliente_email or "")
                        partes.append(cliente_whatsapp or "")
                        lp.external_ref = "|".join(partes)
                        db.commit()
        except Exception as e:
            print(f"[CHECKOUT] No pude guardar email/WA en LinkPago: {e}")

    return resultado


@router.get("/checkout/ultimo-link")
def checkout_ultimo_link(codigo: str = "", db: Session = Depends(get_db)):
    """Devuelve el último link de pago ACTIVO del aliado (buscado por su código),
    para que el cotizador lo recupere al reabrir. 404 si no hay ninguno (el front lo trata
    en silencio). Forma de respuesta esperada por recuperarUltimoLinkActivo() en portal.html."""
    if not codigo:
        raise HTTPException(404, "Sin código.")
    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a:
        raise HTTPException(404, "Aliado no encontrado.")
    lp = (db.query(LinkPago)
            .filter(LinkPago.aliado_id == a.id, LinkPago.estado == "activo")
            .order_by(LinkPago.created_at.desc())
            .first())
    if not lp:
        raise HTTPException(404, "Sin link previo.")
    return {
        "link_id":      lp.id,
        "checkout_url": lp.checkout_url,
        "moneda":       lp.moneda,
        "expires_at":   lp.expires_at.isoformat() if lp.expires_at else None,
        "plan":         lp.plan,
        "precio_usd":   lp.precio_usd,
        "precio_ars":   lp.precio_ars,
        "tipo_cambio":  lp.tipo_cambio,
    }


# ─── PAGOS MANUALES (USDT / Payoneer) — registro pendiente + confirmación admin ──
# El cliente paga por fuera (cripto / Payoneer): no hay webhook. Creamos un
# LinkPago "pendiente" con la atribución del aliado adentro, para que cuando
# Avanza verifique la transferencia, un solo click registre venta + comisión con
# el mismo helper del dinero (_procesar_pago_confirmado). Misma cadena que MP,
# pero la confirmación es manual. Lo usan tanto el cotizador (aliado) como la
# página de ventas (cliente): mismo endpoint, distinto disparador.

_METODOS_MANUALES = {"usdt", "payoneer"}


@router.post("/checkout/manual")
def crear_pago_manual(plan: str,
                      ref_code: str,
                      nombre_cliente: str = "Cliente",
                      metodo: str = "usdt",
                      cliente_email: str = "",
                      cliente_whatsapp: str = "",
                      prospecto_id: int = None,
                      db: Session = Depends(get_db)):
    """Crea un registro de pago PENDIENTE para un método manual (USDT/Payoneer).
    No genera link de cobro: deja guardada la atribución (aliado + plan + cliente)
    para que el admin la confirme cuando verifique la transferencia. Reusa un
    pendiente reciente (mismo aliado+plan+método, <48hs) para no duplicar."""
    metodo = (metodo or "usdt").lower()
    if metodo not in _METODOS_MANUALES:
        raise HTTPException(400, "Método inválido. Debe ser 'usdt' o 'payoneer'.")
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not a:
        raise HTTPException(404, "Código de referido inválido.")
    if plan not in PLANES and plan not in PLANES_CONTINUIDAD:
        raise HTTPException(400, "Plan inválido.")
    valor_usd = _precio_de_plan(plan)

    # external_ref con el mismo formato que /checkout/crear: ref|plan|cliente|email|wa
    ref_guardada = "|".join([ref_code, plan, nombre_cliente or "", cliente_email or "", cliente_whatsapp or ""])

    existente = (db.query(LinkPago)
                 .filter(LinkPago.aliado_id == a.id,
                         LinkPago.plan == plan,
                         LinkPago.processor == metodo,
                         LinkPago.external_ref == ref_guardada,
                         LinkPago.estado.in_(["pendiente", "reportado"]),
                         LinkPago.created_at >= datetime.now() - timedelta(hours=LINK_EXPIRATION_HOURS))
                 .order_by(LinkPago.created_at.desc())
                 .first())
    if existente:
        return {"link_id": existente.id, "precio_usd": existente.precio_usd,
                "estado": existente.estado, "reusado": True}

    lp = LinkPago(
        aliado_id    = a.id,
        prospecto_id = prospecto_id,
        plan         = plan,
        moneda       = "usd",
        precio_usd   = valor_usd,
        checkout_url = f"manual:{metodo}",
        processor    = metodo,            # 'usdt' | 'payoneer'
        external_ref = ref_guardada,
        estado       = "pendiente",
        expires_at   = datetime.now() + timedelta(hours=LINK_EXPIRATION_HOURS),
    )
    db.add(lp)
    db.commit()
    db.refresh(lp)
    return {"link_id": lp.id, "precio_usd": valor_usd, "estado": "pendiente", "reusado": False}


@router.post("/checkout/manual/{link_id}/reportar")
def reportar_pago_manual(link_id: int, db: Session = Depends(get_db)):
    """El cliente o el aliado avisa que ya transfirió. Marca el registro como
    'reportado' para que el admin lo priorice en el panel. No mueve plata."""
    lp = db.query(LinkPago).filter(LinkPago.id == link_id).first()
    if not lp:
        raise HTTPException(404, "Registro no encontrado.")
    if lp.processor not in _METODOS_MANUALES:
        raise HTTPException(400, "Este registro no es de pago manual.")
    if lp.estado == "pagado":
        return {"estado": "pagado", "mensaje": "Este pago ya fue confirmado."}
    lp.estado = "reportado"
    db.commit()
    try:
        notificar_aliado(
            db, lp.aliado_id, "sistema", "Pago reportado",
            f"Se reportó un pago {lp.processor.upper()} del {lp.plan}. Avanza verificará la "
            f"transferencia y se acreditará tu comisión.", tab="comisiones",
        )
    except Exception as _e:
        print(f"[PAGO MANUAL] No pude notificar al aliado: {_e}")
    return {"estado": "reportado", "mensaje": "Reportado. Avanza verificará la transferencia."}


@router.get("/checkout/exitoso")
def checkout_exitoso(ref: str = "", plan: str = "", payment_id: str = "", db: Session = Depends(get_db)):
    """Redirección post-pago de MP (legacy; mantener por compatibilidad con back_urls viejos)."""
    from main import PORTAL_URL  # diferido: const/helper de main (evita import circular)
    a = db.query(Aliado).filter(Aliado.ref_code == ref).first()
    if a and plan in PLANES:
        reciente = db.query(Referido).filter(
            Referido.aliado_id == a.id, Referido.plan_elegido == plan,
            Referido.registrado_en >= datetime.now() - timedelta(hours=48)
        ).first()
        if not reciente:
            r = Referido(aliado_id=a.id, nombre_cliente=f"Cliente Web (MP:{payment_id or '?'})",
                         plan_elegido=plan, notas="Auto-registrado vía checkout web")
            db.add(r); db.commit()
    return RedirectResponse(f"{PORTAL_URL}/portal.html?pago=ok&plan={plan}&ref={ref}")


# ─── HELPER COMÚN: procesar pago confirmado (MP o USDT) ─────────────────────
def _procesar_pago_continuidad_confirmado(db: Session,
                                          a: Aliado,
                                          plan: str,
                                          nombre_cliente: str,
                                          processor: str,
                                          payment_id: str,
                                          link_pago_id: int = None) -> dict:
    """Maneja un pago confirmado para un Plan de Continuidad.

    Crea (o reutiliza) un PlanContinuidadActivo para el cliente y dispara la
    primera comisión del mes en curso (titular 10% + sponsor 5% si tiene),
    para que el aliado no espere al cron del 1ro a ver su comisión.

    Idempotente vía token [PID:xxx] guardado en `notas` del PlanContinuidadActivo:
    si llega un segundo webhook con el mismo payment_id, lo detecta y no
    duplica.
    """
    from main import PORTAL_URL  # diferido: const/helper de main (evita import circular)
    pid_token = f"[PID:{payment_id}]"

    # Idempotencia: ya procesamos este payment_id?
    existing = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.aliado_id == a.id,
        PlanContinuidadActivo.notas.contains(pid_token),
    ).first()
    if existing:
        return {"status": "already_processed", "plan_continuidad_id": existing.id}

    precio = float(PLANES_CONTINUIDAD[plan])
    if processor == "mercadopago":
        modalidad = "MercadoPago"
    elif processor == "usdt":
        modalidad = "USDT (TRC20)"
    else:
        modalidad = processor.capitalize()
    notas = f"Alta automática vía {modalidad} {pid_token}"

    p = PlanContinuidadActivo(
        aliado_id=a.id,
        nombre_cliente=nombre_cliente,
        plan_continuidad=plan,
        precio_mensual_usd=precio,
        comision_pct=COMISION_RECURRENTE_PCT,
        notas=notas,
    )
    db.add(p)
    db.flush()  # para tener p.id y p.aliado disponibles

    # Primera comisión del mes en curso (titular + sponsor 5% si tiene),
    # idempotente vía el helper compartido.
    ahora = datetime.utcnow()
    creado = _crear_comisiones_recurrentes_para_plan(
        db, p, ahora.month, ahora.year, ahora,
    )

    # Marcar LinkPago como pagado
    if link_pago_id:
        lp = db.query(LinkPago).filter(LinkPago.id == link_pago_id).first()
        if lp:
            lp.estado = "pagado"

    db.commit()

    # Email al aliado — copy adaptado a recurrente
    try:
        comision_mensual = p.comision_mensual_usd
        nombre_corto = a.nombre.split()[0] if a.nombre else "Hola"
        cuerpo_email = f"""<div style="font-family:sans-serif;background:#050505;color:#fff;padding:32px;max-width:520px;margin:auto;border-radius:12px;">
          <div style="font-size:.78rem;color:#fbbf24;font-weight:700;letter-spacing:.6px;text-transform:uppercase;margin-bottom:8px;">🔁 Renta recurrente activada</div>
          <h2 style="color:#fbbf24;margin:0 0 16px 0;">¡{nombre_cliente} contrató {plan}!</h2>
          <p style="color:#e2e8f0;line-height:1.55;">Hola {nombre_corto}, tu cliente acaba de pagar el primer mes a través de <strong>{modalidad}</strong>. A partir de ahora cobrás todos los meses mientras lo mantenga activo.</p>
          <div style="background:#111;border:1px solid #fbbf2455;border-radius:8px;padding:16px;margin:20px 0;">
            <p style="margin:4px 0;"><strong>Plan:</strong> {plan}</p>
            <p style="margin:4px 0;"><strong>Cliente:</strong> {nombre_cliente}</p>
            <p style="margin:4px 0;"><strong>Cliente paga:</strong> USD {precio:,.0f}/mes</p>
            <p style="margin:4px 0;"><strong>Tu comisión:</strong> <span style="color:#fbbf24;font-size:1.3rem;font-weight:900;">USD {comision_mensual:,.0f}/mes</span></p>
            <p style="margin:4px 0;font-size:.85rem;color:#71717a;">El primer mes ya quedó como comisión pendiente. Cada 1ro del mes siguiente se acumula otra automáticamente.</p>
          </div>
          <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:8px;padding:12px 24px;background:#fbbf24;color:#000;border-radius:8px;text-decoration:none;font-weight:700;">Ver mis comisiones →</a>
        </div>"""
        if a.email:
            enviar_email(a.email, f"🔁 Nueva renta recurrente — {plan}", cuerpo_email)
    except Exception as e:
        print(f"[CONTINUIDAD EMAIL] No pude notificar al aliado: {e}")

    return {
        "status": "ok",
        "tipo": "continuidad",
        "plan_continuidad_id": p.id,
        "comision_mensual_usd": p.comision_mensual_usd,
        "primera_comision_creada": creado["titular"],
        "comision_sponsor_creada": creado["sponsor"],
    }


def _procesar_pago_confirmado(db: Session,
                              ref_code: str,
                              plan: str,
                              nombre_cliente: str,
                              processor: str,
                              payment_id: str,
                              link_pago_id: int = None) -> dict:
    """Registra venta + comisión + notifica. Idempotente vía payment_id en notas.
    El token [PID:xxx] es delimitado para evitar que payment_id='42' matchee con '142'.

    Soporta tanto planes one-shot (PLANES) como planes de continuidad
    (PLANES_CONTINUIDAD). Para continuidad delega en
    _procesar_pago_continuidad_confirmado."""
    from main import PORTAL_URL, _aplicar_bonus_primera_venta  # diferido: const/helper de main (evita import circular)
    if plan not in PLANES and plan not in PLANES_CONTINUIDAD:
        return {"status": "invalid_plan"}
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not a:
        return {"status": "aliado_not_found"}

    # Rama plan de continuidad
    if plan in PLANES_CONTINUIDAD:
        return _procesar_pago_continuidad_confirmado(
            db, a, plan, nombre_cliente, processor, payment_id, link_pago_id,
        )

    # Idempotencia robusta: buscamos el token delimitado [PID:xxx] en las notas.
    # MP reenvía webhooks habitualmente, así que esto es crítico.
    pid_token = f"[PID:{payment_id}]"
    existing = db.query(Venta).filter(
        Venta.aliado_id == a.id, Venta.notas.contains(pid_token)
    ).first()
    if existing:
        return {"status": "already_processed", "venta_id": existing.id}

    valor_usd = PLANES[plan]
    comision_pct = a.comision_pct
    comision_usd = round(valor_usd * comision_pct, 2)
    fecha_venta = datetime.now()
    if processor == "mercadopago":
        modalidad = "MercadoPago"
    elif processor == "usdt":
        modalidad = "USDT (TRC20)"
    else:
        modalidad = processor.capitalize()

    # Detectar primera venta ANTES de crear la nueva (para no contarla a sí misma).
    es_primera_venta_aliado = db.query(Venta).filter(
        Venta.aliado_id == a.id,
        Venta.confirmada == True,
    ).count() == 0

    # --- Registrar venta ---
    v = Venta(aliado_id=a.id, nombre_cliente=nombre_cliente, plan=plan,
              valor_usd=valor_usd, comision_pct=comision_pct, comision_usd=comision_usd,
              confirmada=True, pagada=False, fecha_venta=fecha_venta,
              modalidad_pago=modalidad, notas=f"Pago automático {modalidad} {pid_token}")
    db.add(v)
    db.flush()  # asignar v.id para la referencia del bonus

    # --- Registrar comisión (spec §9, §10: siempre sobre USD base) ---
    c = Comision(
        aliado_id      = a.id,
        link_pago_id   = link_pago_id,
        plan           = plan,
        monto_plan_usd = valor_usd,
        comision_pct   = comision_pct,
        comision_usd   = comision_usd,
        nombre_cliente = nombre_cliente,
        estado         = "pendiente",
        processor      = processor,
        fecha_pago     = fecha_venta,
    )
    db.add(c)
    notificar_aliado(
        db, a.id, "comision",
        f"💰 Nueva comisión: USD {comision_usd:,.0f}",
        f"{nombre_cliente} contrató {plan}. Tu comisión quedó pendiente de pago.",
        tab="comisiones",
    )

    # --- Comisión de red (5% para sponsor) ---
    if getattr(a, "sponsor", None):
        comision_sponsor = round(valor_usd * 0.05, 2)
        v_red = Venta(
            aliado_id=a.sponsor.id, nombre_cliente=f"♻️ RED: {a.nombre} ({modalidad}:{nombre_cliente})",
            plan=plan, valor_usd=valor_usd, comision_pct=0.05, comision_usd=comision_sponsor,
            confirmada=True, pagada=False, fecha_venta=fecha_venta,
            modalidad_pago=modalidad, notas=f"Ingreso pasivo {modalidad} {pid_token}"
        )
        db.add(v_red)
        c_red = Comision(
            aliado_id=a.sponsor.id, plan=plan,
            monto_plan_usd=valor_usd, comision_pct=0.05, comision_usd=comision_sponsor,
            nombre_cliente=f"RED: {a.nombre} ({nombre_cliente})",
            estado="pendiente", processor=processor, fecha_pago=fecha_venta,
        )
        db.add(c_red)
        a.sponsor.nivel = a.sponsor.nivel_calculado

    # --- SPLIT DE EQUIPO (sistemas/one-shot): si el link esta ligado a un prospecto
    # handed-off, repartir la comision titular con el setter. Modelo B para sponsors.
    # Best-effort: cualquier error NO rompe el pago (la comision del closer ya esta).
    try:
        _setter_id = None; _split = None
        if link_pago_id:
            _lp = db.query(LinkPago).filter(LinkPago.id == link_pago_id).first()
            if _lp and getattr(_lp, "prospecto_id", None):
                _pro = db.query(Prospecto).filter(Prospecto.id == _lp.prospecto_id).first()
                if _pro:
                    _setter_id = getattr(_pro, "setter_id", None)
                    _split = getattr(_pro, "setter_split_pct", None)
        if _setter_id and _split and _setter_id != a.id:
            _parte = round(float(comision_usd) * float(_split), 2)
            if _parte > 0:
                c.comision_usd = round(float(comision_usd) - _parte, 2)
                db.add(Comision(
                    aliado_id=_setter_id, plan=plan, monto_plan_usd=valor_usd,
                    comision_pct=round(float(comision_pct) * float(_split), 4),
                    comision_usd=_parte, nombre_cliente="EQUIPO: " + str(nombre_cliente),
                    estado="pendiente", processor=processor, fecha_pago=fecha_venta))
                notificar_aliado(db, _setter_id, "comision",
                    "Comision de equipo: USD %s" % format(_parte, ",.0f"),
                    "Tu closer cerro %s (%s). Te toca tu parte como setter." % (nombre_cliente, plan),
                    tab="comisiones")
                # Modelo B: el sponsor del setter tambien cobra su 5% del deal.
                _setter = db.query(Aliado).filter(Aliado.id == _setter_id).first()
                _sp = getattr(_setter, "sponsor", None) if _setter else None
                if _sp:
                    db.add(Comision(
                        aliado_id=_sp.id, plan=plan, monto_plan_usd=valor_usd,
                        comision_pct=0.05, comision_usd=round(valor_usd * 0.05, 2),
                        nombre_cliente="RED EQUIPO: " + str(nombre_cliente),
                        estado="pendiente", processor=processor, fecha_pago=fecha_venta))
    except Exception as _e:
        print(f"[CHECKOUT] split de equipo no aplicado: {_e}")

    # --- Actualizar LinkPago a pagado ---
    if link_pago_id:
        lp = db.query(LinkPago).filter(LinkPago.id == link_pago_id).first()
        if lp:
            lp.estado = "pagado"

    # --- Actualizar prospecto si existe ---
    try:
        prospecto = db.query(Prospecto).filter(
            Prospecto.aliado_id == a.id,
            Prospecto.nombre == nombre_cliente,
        ).order_by(Prospecto.creado_en.desc()).first()
        if prospecto:
            prospecto.estado = "pagado"
    except Exception as e:
        print(f"[PROCESAR PAGO] No pude actualizar prospecto: {e}")

    # BONUS PRIMERA VENTA — créditos al aliado y al sponsor (si tiene).
    # Se calcula antes del commit para que vaya en la misma transacción.
    bonus_info = None
    if es_primera_venta_aliado:
        bonus_info = _aplicar_bonus_primera_venta(db, a, v.id)

    _nivel_anterior_pago = a.nivel  # capturar antes de actualizar
    a.nivel = a.nivel_calculado
    db.commit()

    # WhatsApp Canal 1: notificar venta y posible subida de nivel
    try:
        jarvis_canal1.notificar_venta_y_nivel(a, _nivel_anterior_pago, db)
    except Exception as _e:
        print(f"[CANAL1] Error notif venta/nivel (checkout): {_e}", file=sys.stderr)

    # --- Notificación al aliado (spec §7) ---
    # Intentamos personalizar el email con IA: coaching del próximo movimiento.
    # Si Groq falla → usamos el template fijo de siempre.
    n_ventas_aliado = a.ventas_6_meses or 0
    es_primera_venta = (n_ventas_aliado <= 1)  # ya se actualizó arriba al sumar 1

    email_ia = groq_ai.personalizar_email_venta_cerrada_ia(
        aliado_nombre=a.nombre,
        cliente_nombre=nombre_cliente,
        plan=plan,
        comision_usd=comision_usd,
        es_primera_venta=es_primera_venta,
        ventas_totales_aliado=n_ventas_aliado,
    )

    if email_ia:
        # Convertimos texto plano en párrafos HTML
        parrafos = [pr.strip() for pr in email_ia["cuerpo_texto"].split("\n\n") if pr.strip()]
        cuerpo_html_inner = "".join(f"<p style='margin:0 0 12px 0;'>{pr.replace(chr(10), '<br>')}</p>" for pr in parrafos)
        asunto_email = email_ia["asunto"]
        # El bloque con el monto va abajo del coaching como dato hard.
        cuerpo_email = f"""<div style="font-family:sans-serif;background:#050505;color:#fff;padding:32px;max-width:520px;margin:auto;border-radius:12px;">
          <div style="font-size:.78rem;color:#a855f7;font-weight:700;letter-spacing:.6px;text-transform:uppercase;margin-bottom:8px;">✨ Mensaje personalizado</div>
          <h2 style="color:#4ade80;margin:0 0 16px 0;">¡Cerraste con {nombre_cliente}!</h2>
          <div style="color:#e2e8f0;line-height:1.55;">{cuerpo_html_inner}</div>
          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:16px;margin:20px 0;">
            <p style="margin:4px 0;"><strong>Plan:</strong> {plan}</p>
            <p style="margin:4px 0;"><strong>Cliente:</strong> {nombre_cliente}</p>
            <p style="margin:4px 0;"><strong>Tu comisión:</strong> <span style="color:#4ade80;font-size:1.3rem;font-weight:900;">USD {comision_usd:,.0f}</span></p>
            <p style="margin:4px 0;font-size:.85rem;color:#71717a;">Se abona en 24hs al CBU/alias registrado.</p>
          </div>
          <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:8px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ver mi portal →</a>
        </div>"""
    else:
        # Fallback: template fijo de siempre.
        asunto_email = f"💰 ¡Nuevo cliente cerrado! — {plan}"
        cuerpo_email = f"""<div style="font-family:sans-serif;background:#050505;color:#fff;padding:32px;max-width:520px;margin:auto;border-radius:12px;">
          <h2 style="color:#4ade80;">¡Tu cliente {nombre_cliente} acaba de pagar! 🎉</h2>
          <p>Hola <strong>{a.nombre.split()[0]}</strong>, llegó un pago a través de <strong>{modalidad}</strong>.</p>
          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:16px;margin:16px 0;">
            <p style="margin:4px 0;"><strong>Plan:</strong> {plan}</p>
            <p style="margin:4px 0;"><strong>Cliente:</strong> {nombre_cliente}</p>
            <p style="margin:4px 0;"><strong>Tu comisión:</strong> <span style="color:#4ade80;font-size:1.3rem;font-weight:900;">USD {comision_usd:,.0f}</span></p>
          </div>
          <p style="color:#71717a;font-size:.85rem;">Se te abona dentro de las 24hs al CBU/alias registrado.</p>
          <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ver mi portal →</a>
        </div>"""

    enviar_email(a.email, asunto_email, cuerpo_email)

    # ── EMAIL AL CLIENTE con link del formulario de onboarding (Tally) ────────
    # Se manda al email del cliente si lo tenemos guardado en el LinkPago.
    # El link del Tally se elige según el plan que pagó — cada plan tiene su
    # formulario específico con las preguntas correspondientes.
    TALLY_POR_PLAN = {
        "Plan Base":        "https://tally.so/r/EkXy0X",
        "Plan Pro":         "https://tally.so/r/obyX41",
        "Plan Industrial":  "https://tally.so/r/J92qxY",
        "Estrategico 360":  "https://tally.so/r/NpArxb",
    }
    tally_url = TALLY_POR_PLAN.get(plan, "")

    # Recuperar email y whatsapp del cliente desde el external_ref del LinkPago
    cliente_email_onboarding = ""
    cliente_whatsapp_onboarding = ""
    if link_pago_id:
        try:
            lp_check = db.query(LinkPago).filter(LinkPago.id == link_pago_id).first()
            if lp_check and lp_check.external_ref:
                partes = lp_check.external_ref.split("|")
                if len(partes) >= 4:
                    cliente_email_onboarding = partes[3]
                if len(partes) >= 5:
                    cliente_whatsapp_onboarding = partes[4]
        except Exception as e:
            print(f"[ONBOARDING EMAIL] No pude recuperar email del LinkPago: {e}")

    if cliente_email_onboarding and tally_url:
        nombre_corto_cliente = nombre_cliente.split()[0] if nombre_cliente else "Hola"
        html_cliente = f"""
        <div style="font-family:Inter,sans-serif;background:#fff;color:#111;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;">
          <div style="text-align:center;margin-bottom:32px;">
            <div style="font-size:2rem;">🎉</div>
            <h1 style="font-size:1.6rem;font-weight:900;margin:12px 0 6px;">¡Pago confirmado, {nombre_corto_cliente}!</h1>
            <p style="color:#555;font-size:.95rem;">Tu contratación del <strong>{plan}</strong> fue procesada exitosamente.</p>
          </div>

          <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:24px;margin-bottom:24px;">
            <h2 style="font-size:1.1rem;font-weight:800;margin:0 0 10px;color:#111;">El siguiente paso es tuyo 👇</h2>
            <p style="color:#444;line-height:1.6;margin:0 0 16px;">Para que podamos empezar a trabajar en tu proyecto, necesitamos que completes este formulario. <strong>Te toma menos de 5 minutos</strong> y es la información que usaremos para construir todo tu ecosistema digital.</p>
            <a href="{tally_url}" style="display:block;text-align:center;padding:16px 24px;background:#111;color:#fff;border-radius:8px;text-decoration:none;font-weight:800;font-size:1.05rem;">
              📋 Completar formulario de inicio →
            </a>
          </div>

          <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:16px;margin-bottom:24px;">
            <p style="margin:0;font-size:.88rem;color:#92400e;line-height:1.5;">
              ⚡ <strong>Importante:</strong> sin este formulario no podemos arrancar. Cuanto antes lo completes, antes tenés tu proyecto funcionando.
            </p>
          </div>

          <p style="color:#555;font-size:.88rem;line-height:1.6;">
            Si tenés alguna pregunta antes de completarlo, respondé este email o escribinos por WhatsApp. Estaremos en contacto dentro de las próximas <strong>24hs hábiles</strong>.
          </p>

          <div style="border-top:1px solid #e5e7eb;margin-top:24px;padding-top:16px;text-align:center;">
            <p style="font-size:.78rem;color:#9ca3af;margin:0;">Avanza Digital · {plan} · Tu aliado: {a.nombre}</p>
            {f'<p style="font-size:.78rem;color:#9ca3af;margin:4px 0;">WhatsApp de contacto: {cliente_whatsapp_onboarding}</p>' if cliente_whatsapp_onboarding else ''}
          </div>
        </div>
        """
        try:
            enviar_email(
                cliente_email_onboarding,
                f"✅ Pago confirmado — completá el formulario de inicio ({plan})",
                html_cliente
            )
            print(f"[ONBOARDING EMAIL] Enviado a {cliente_email_onboarding} con Tally {plan}")
        except Exception as e:
            print(f"[ONBOARDING EMAIL ERROR] {e}")
    elif not cliente_email_onboarding:
        # No tenemos el email del cliente — loggeamos para que el admin lo contacte manualmente
        print(f"[ONBOARDING SIN EMAIL] Venta {v.id} | Cliente: {nombre_cliente} | Plan: {plan} — no hay email del cliente para mandar Tally")

    return {"status": "ok", "venta_registrada": True, "comision_id": c.id,
            "comision_usd": comision_usd, "aliado": a.codigo,
            "primera_venta": es_primera_venta_aliado,
            "bonus_creditos": bonus_info}


# ─── WEBHOOK MERCADO PAGO (con verificación HMAC — spec §19) ─────────────────
@router.post("/webhooks/mercadopago")
async def webhook_mercadopago(request: Request, db: Session = Depends(get_db)):
    """Recibe notificaciones de MP. Verifica firma HMAC antes de procesar."""
    from main import MP_ACCESS_TOKEN  # diferido: const/helper de main (evita import circular)
    raw = await request.body()

    # --- 1. Verificar firma HMAC (bloqueante en producción) ---
    if not verificar_firma_mp(raw, request.headers, dict(request.query_params)):
        return JSONResponse(status_code=401, content={"status": "invalid_signature"})

    # --- 2. Parsear body ---
    try:
        body = json.loads(raw) if raw else {}
    except Exception:
        return {"status": "invalid_json"}

    if body.get("type") != "payment":
        return {"status": "ignored"}

    payment_id = body.get("data", {}).get("id")
    if not payment_id or not MP_ACCESS_TOKEN:
        return {"status": "no_payment_id"}

    # --- 3. Consultar detalles del pago ---
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}, timeout=10.0,
        )
        if resp.status_code != 200:
            # MP nos avisó de un pago pero su API falla al darnos el detalle:
            # falla real que hoy se perdía en los logs. La mandamos a Sentry
            # (si está activo) para no perder cobros que quedaron a medias.
            try:
                import sentry_sdk
                sentry_sdk.capture_message(
                    f"MP API error al consultar pago {payment_id}: HTTP {resp.status_code}",
                    level="error",
                )
            except Exception:
                pass
            return {"status": "error_mp", "http": resp.status_code}
        payment = resp.json()

    if payment.get("status") != "approved":
        return {"status": "not_approved", "mp_status": payment.get("status")}

    # --- 4. Extraer external_reference ---
    ext_ref = payment.get("external_reference", "") or ""
    parts = ext_ref.split("|", 2)
    if len(parts) < 2:
        return {"status": "invalid_ref"}
    ref_code, plan = parts[0], parts[1]
    nombre_cliente = parts[2] if len(parts) > 2 else "Cliente Web"

    # --- 5. Buscar el LinkPago asociado (si existe) ---
    lp = db.query(LinkPago).filter(
        LinkPago.external_ref == ext_ref,
        LinkPago.processor == "mercadopago",
    ).first()

    # --- 6. Delegar en helper común ---
    return _procesar_pago_confirmado(db, ref_code, plan, nombre_cliente,
                                     processor="mercadopago",
                                     payment_id=str(payment_id),
                                     link_pago_id=lp.id if lp else None)


# ─── LEGACY: /checkout/webhook (MP viejo) → delega en /webhooks/mercadopago ─
@router.post("/checkout/webhook")
async def checkout_webhook_legacy(request: Request, db: Session = Depends(get_db)):
    """Endpoint legacy — redirige internamente al nuevo handler de MP."""
    return await webhook_mercadopago(request, db)


# ─── TIPO DE CAMBIO (público, para el cotizador) ─────────────────────────────
@router.get("/tipo-de-cambio")
async def tipo_de_cambio():
    """Devuelve el tipo de cambio blue actual. El cotizador lo usa para mostrar
    precios en ARS orientativos al aliado."""
    from main import DOLARAPI_URL, obtener_tipo_de_cambio  # diferido: const/helper de main (evita import circular)
    tc = await obtener_tipo_de_cambio()
    return {"moneda": "ARS", "referencia": "blue", "venta": tc,
            "source": DOLARAPI_URL, "fetched_at": datetime.now().isoformat()}


# ─── REGENERAR LINK DE PAGO (spec §4: opción de regenerar tras vencimiento) ──
@router.post("/checkout/regenerar/{link_id}")
async def regenerar_link(link_id: int, db: Session = Depends(get_db)):
    """Regenera un link de pago vencido. Crea uno nuevo con datos del original."""
    lp_viejo = db.query(LinkPago).filter(LinkPago.id == link_id).first()
    if not lp_viejo:
        raise HTTPException(404, "Link de pago no encontrado.")
    if lp_viejo.estado == "pagado":
        raise HTTPException(400, "Este link ya fue pagado, no se puede regenerar.")
    a = lp_viejo.aliado
    if not a:
        raise HTTPException(404, "Aliado del link no encontrado.")
    nombre_cliente = "Cliente"
    if lp_viejo.external_ref and "|" in lp_viejo.external_ref:
        parts = lp_viejo.external_ref.split("|", 2)
        if len(parts) > 2:
            nombre_cliente = parts[2]

    lp_viejo.estado = "vencido"
    db.commit()

    if lp_viejo.moneda == "ars":
        return await _crear_link_mp(a, lp_viejo.plan, nombre_cliente, db)
    return await _crear_link_usdt(a, lp_viejo.plan, nombre_cliente, db)


# ─── HISTORIAL DE LINKS DE PAGO DEL ALIADO ───────────────────────────────────
@router.get("/aliados/{codigo}/links-pago")
def listar_links_pago_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Devuelve todos los links de pago generados por el aliado."""
    a = _get_aliado(codigo, db)
    links = db.query(LinkPago).filter(LinkPago.aliado_id == a.id)\
        .order_by(LinkPago.created_at.desc()).all()
    ahora = datetime.now()
    out = []
    for lp in links:
        estado = lp.estado
        # auto-computar "vencido" aunque el scheduler no haya corrido todavía
        if estado == "activo" and lp.expires_at and lp.expires_at < ahora:
            estado = "vencido"
        out.append({
            "id": lp.id, "plan": lp.plan, "moneda": lp.moneda,
            "precio_usd": lp.precio_usd, "precio_ars": lp.precio_ars,
            "tipo_cambio": lp.tipo_cambio, "processor": lp.processor,
            "checkout_url": lp.checkout_url, "estado": estado,
            "created_at": lp.created_at.isoformat() if lp.created_at else None,
            "expires_at": lp.expires_at.isoformat() if lp.expires_at else None,
        })
    return out


# ─ SIGUIENTE MEJOR ACCIÓN → ia_comercial.py ─────────────────────────────────


@router.get("/config/usdt")
def config_usdt_publico():
    """Endpoint público que devuelve la configuración de pago en USDT/USDC.
    Usado por el portal de aliados en el cotizador para mostrar instrucciones al cliente.
    No expone datos sensibles (solo la dirección de billetera y la red, que el cliente
    necesita ver para transferir).
    """
    from main import USDT_DIRECCION, USDT_RED  # diferido: const/helper de main (evita import circular)
    return {
        "activo":    bool(USDT_DIRECCION),
        "direccion": USDT_DIRECCION,
        "red":       USDT_RED or "TRC20",
        "metodo":    os.environ.get("USD_METODO", "USDT"),
    }


@router.get("/config/payoneer")
def config_payoneer_publico():
    """Endpoint público que devuelve los datos de cobro por Payoneer
    (email + transferencia bancaria en USD). Usado por el cotizador del portal
    para mostrar instrucciones al cliente. Son los mismos datos que ya muestra
    la página de ventas pública; no expone nada sensible más allá de lo que el
    cliente necesita para transferir.
    """
    from main import DATOS_PAYONEER  # diferido: const de main (evita import circular)
    _email = DATOS_PAYONEER.get("destinatario", "")
    _banco = DATOS_PAYONEER.get("banco") or {}
    return {
        "activo": bool(_email),
        "email":  _email,
        "banco": {
            "beneficiario": _banco.get("beneficiario", ""),
            "banco":        _banco.get("banco", ""),
            "direccion":    _banco.get("direccion", ""),
            "cuenta":       _banco.get("cuenta", ""),
            "tipo_cuenta":  _banco.get("tipo_cuenta", ""),
            "aba":          _banco.get("aba", ""),
            "swift":        _banco.get("swift", ""),
        },
    }


# (/alias/{ref_code} y la landing /p/{ref_code} migrados a portal_publico.py.)



# ─── PAGOS (ADMIN) ───────────────────────────────────────────────────────────

@router.get("/admin/pagos")
def admin_listar_pagos(db: Session = Depends(get_db),
                       _admin=Depends(current_admin_required)):
    """Lista todos los pagos recibidos (LinkPago con estado=pagado),
    ordenados del más reciente al más viejo."""
    pagos = db.query(LinkPago).filter(LinkPago.estado == "pagado")\
        .order_by(LinkPago.created_at.desc()).all()
    out = []
    for lp in pagos:
        aliado = lp.aliado
        out.append({
            "id": lp.id, "plan": lp.plan, "moneda": lp.moneda,
            "precio_usd": lp.precio_usd, "precio_ars": lp.precio_ars,
            "tipo_cambio": lp.tipo_cambio, "processor": lp.processor,
            "aliado_codigo": aliado.codigo if aliado else None,
            "aliado_nombre": aliado.nombre if aliado else "—",
            "created_at": lp.created_at.isoformat() if lp.created_at else None,
        })
    return out


@router.get("/admin/pagos/pendientes")
def admin_listar_pagos_pendientes(db: Session = Depends(get_db),
                                  _admin=Depends(current_admin_required)):
    """Pagos manuales (USDT/Payoneer) esperando verificación de Avanza.
    Los 'reportado' (el cliente ya avisó que transfirió) van primero."""
    pend = (db.query(LinkPago)
            .filter(LinkPago.processor.in_(list(_METODOS_MANUALES)),
                    LinkPago.estado.in_(["pendiente", "reportado"]))
            .order_by(LinkPago.created_at.desc()).all())
    pend.sort(key=lambda lp: 0 if lp.estado == "reportado" else 1)
    out = []
    for lp in pend:
        aliado = lp.aliado
        partes = (lp.external_ref or "").split("|")
        out.append({
            "id": lp.id, "plan": lp.plan, "precio_usd": lp.precio_usd,
            "metodo": lp.processor, "estado": lp.estado,
            "aliado_codigo": aliado.codigo if aliado else None,
            "aliado_nombre": aliado.nombre if aliado else "—",
            "nombre_cliente":   partes[2] if len(partes) > 2 else "",
            "cliente_email":    partes[3] if len(partes) > 3 else "",
            "cliente_whatsapp": partes[4] if len(partes) > 4 else "",
            "created_at": lp.created_at.isoformat() if lp.created_at else None,
        })
    return out


@router.post("/admin/pagos/{link_id}/confirmar")
def admin_confirmar_pago_manual(link_id: int, db: Session = Depends(get_db),
                                _admin=Depends(current_admin_required)):
    """Avanza verificó que la transferencia (USDT/Payoneer) llegó. Registra
    venta + comisión con el mismo helper del dinero y marca el link pagado.
    Idempotente: si se confirma dos veces no duplica comisión."""
    lp = db.query(LinkPago).filter(LinkPago.id == link_id).first()
    if not lp:
        raise HTTPException(404, "Registro no encontrado.")
    if lp.processor not in _METODOS_MANUALES:
        raise HTTPException(400, "Este registro no es de pago manual.")
    if lp.estado == "pagado":
        return {"status": "already_processed", "mensaje": "Ya estaba confirmado."}
    a = lp.aliado
    if not a:
        raise HTTPException(400, "El registro no tiene aliado asociado.")
    partes = (lp.external_ref or "").split("|")
    nombre_cli = partes[2] if len(partes) > 2 and partes[2] else "Cliente"

    res = _procesar_pago_confirmado(
        db, ref_code=a.ref_code, plan=lp.plan, nombre_cliente=nombre_cli,
        processor=lp.processor, payment_id=f"manual-{lp.id}", link_pago_id=lp.id,
    )
    if res.get("status") in ("invalid_plan", "aliado_not_found"):
        raise HTTPException(400, f"No se pudo registrar la venta: {res.get('status')}")

    # _procesar_pago_confirmado ya marca el LinkPago pagado y commitea; defensivo:
    if lp.estado != "pagado":
        lp.estado = "pagado"
        db.commit()
    return {"status": "ok", "comision_registrada": True, "resultado": res}
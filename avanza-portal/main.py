from fastapi import FastAPI, Depends, HTTPException, Request, status, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from sqlalchemy.exc import OperationalError, ProgrammingError
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from typing import Optional
from models import (
    Aliado, Admin, AdminAuditLog, Venta, Referido, Prospecto, AuditoriaLog, LeadBolsa,
    TransaccionCredito, PostComunidad, ComentarioComunidad, AutomationLog,
    LinkPago, Comision, AcademiaModulo, AliadoModuloCompletado,
    SolicitudCompraCreditos, ReporteMalContacto,
    PlanContinuidadActivo,
    PLANES, PAQUETES_CREDITOS, NIVELES, CUOTAS_RECARGO, REPUTACION_BADGES,
    PLANES_CONTINUIDAD, COMISION_RECURRENTE_PCT,
)
import random, string, os, smtplib, httpx, json, hmac as hmac_lib, hashlib, base64, sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler

from database import engine, get_db, Base
from auth import (
    crear_token, current_aliado_required, current_admin_required,
    verify_ownership_dep, ADMIN_API_KEY, JWT_SECRET,
    decodificar_token, decodificar_token_ignorando_exp,
    JWT_REFRESH_WINDOW_HOURS,
)
import schemas
import groq_ai  # IA opcional — si GROQ_API_KEY no está, todo cae a fallback heurístico

Base.metadata.create_all(bind=engine)


# ─── MIGRACIONES IDEMPOTENTES ────────────────────────────────────────────────
# Helper que solo traga errores de "columna ya existe" / "tabla no existe en
# orden equivocado". Cualquier otro error se propaga (DB caída, sintaxis, etc.).
_DUP_COL_TOKENS = (
    "already exists",            # postgres / sqlite moderno
    "duplicate column",          # sqlite
    "duplicate column name",     # sqlite alt
)

def _aplicar_migracion(sql: str) -> None:
    """Aplica un ALTER TABLE de forma idempotente.
    Solo silencia errores que indiquen 'columna ya existe'. Cualquier otro
    error (sintaxis, DB caída, permisos) sube y mata el proceso para no
    arrancar con esquema corrupto."""
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
    except (OperationalError, ProgrammingError) as e:
        msg = str(e).lower()
        if any(t in msg for t in _DUP_COL_TOKENS):
            return  # esperado: la columna ya existe
        # Error real: log y re-raise para que el deploy falle limpio
        print(f"[MIGRACIÓN ERROR] {sql} → {e}", file=sys.stderr)
        raise


# Migraciones legacy (orden cronológico de versiones)
_aplicar_migracion("ALTER TABLE aliados ADD COLUMN ultimo_login TIMESTAMP")
_aplicar_migracion("ALTER TABLE aliados ADD COLUMN cantidad_logins INTEGER DEFAULT 0")

# Migraciones para columnas nuevas de LeadBolsa y Red de Aliados
for col_sql in [
    "ALTER TABLE bolsa_leads ADD COLUMN resultado VARCHAR",
    "ALTER TABLE bolsa_leads ADD COLUMN notif_24h_enviada BOOLEAN DEFAULT FALSE",
    # Campos de contacto enriquecidos
    "ALTER TABLE bolsa_leads ADD COLUMN nombre_contacto VARCHAR",
    "ALTER TABLE bolsa_leads ADD COLUMN ciudad VARCHAR",
    "ALTER TABLE bolsa_leads ADD COLUMN whatsapp VARCHAR",
    "ALTER TABLE aliados ADD COLUMN sponsor_id INTEGER REFERENCES aliados(id)",
    # Inteligencia de ventas y checkout
    "ALTER TABLE prospectos ADD COLUMN rubro VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN tamano VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN urgencia VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN score_ia INTEGER DEFAULT 0",
    "ALTER TABLE aliados ADD COLUMN onboarding_completado BOOLEAN DEFAULT FALSE",
    # v1.3 — Prospecto (perfilado IA + piloto)
    "ALTER TABLE prospectos ADD COLUMN plan_recomendado VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN pitch_sugerido TEXT",
    "ALTER TABLE prospectos ADD COLUMN perfilado_en TIMESTAMP",
    "ALTER TABLE prospectos ADD COLUMN automation_paso INTEGER DEFAULT 0",
    "ALTER TABLE prospectos ADD COLUMN automation_ultimo_en TIMESTAMP",
    "ALTER TABLE prospectos ADD COLUMN automation_activa_desde TIMESTAMP",
    # v1.3 — Aliado (reputación + créditos + portal público)
    "ALTER TABLE aliados ADD COLUMN reputacion_score INTEGER DEFAULT 50",
    "ALTER TABLE aliados ADD COLUMN badges TEXT DEFAULT '[]'",
    "ALTER TABLE aliados ADD COLUMN reputacion_calculada_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN creditos INTEGER DEFAULT 0",
    "ALTER TABLE aliados ADD COLUMN portal_publico_activo BOOLEAN DEFAULT TRUE",
    "ALTER TABLE aliados ADD COLUMN portal_publico_titular VARCHAR",
    "ALTER TABLE aliados ADD COLUMN portal_publico_bio TEXT",
    "ALTER TABLE prospectos ADD COLUMN piloto_automatico BOOLEAN DEFAULT FALSE",
    # v1.5 — Comisiones ligadas a link de pago
    "ALTER TABLE comisiones ADD COLUMN link_pago_id INTEGER REFERENCES links_pago(id)",
    # v1.3 — Ventas (financiación)
    "ALTER TABLE ventas ADD COLUMN cuotas INTEGER DEFAULT 1",
    "ALTER TABLE ventas ADD COLUMN financiacion_pct FLOAT DEFAULT 0.0",
    # v1.3 — Bolsa (marketplace)
    "ALTER TABLE bolsa_leads ADD COLUMN tier VARCHAR DEFAULT 'basico'",
    "ALTER TABLE bolsa_leads ADD COLUMN costo_creditos INTEGER DEFAULT 0",
    "ALTER TABLE bolsa_leads ADD COLUMN score_calidad INTEGER DEFAULT 50",
    "ALTER TABLE bolsa_leads ADD COLUMN notas_calificacion TEXT",
    "ALTER TABLE aliados ADD COLUMN tipo_aliado VARCHAR DEFAULT 'canal1'",
    # v1.4 — Cobro de comisiones + contrato digital
    "ALTER TABLE aliados ADD COLUMN cbu_alias VARCHAR",
    "ALTER TABLE aliados ADD COLUMN terminos_aceptados BOOLEAN DEFAULT FALSE",
    "ALTER TABLE aliados ADD COLUMN terminos_aceptados_en TIMESTAMP",
    # v1.6 — Presencia digital en leads de bolsa
    "ALTER TABLE bolsa_leads ADD COLUMN web VARCHAR",
    "ALTER TABLE bolsa_leads ADD COLUMN instagram VARCHAR",
    "ALTER TABLE bolsa_leads ADD COLUMN tiene_web BOOLEAN DEFAULT FALSE",
    "ALTER TABLE bolsa_leads ADD COLUMN tiene_redes BOOLEAN DEFAULT FALSE",
    "ALTER TABLE bolsa_leads ADD COLUMN observacion TEXT",
    # v1.7 — Notificaciones de inactividad
    "ALTER TABLE aliados ADD COLUMN notif_inact_20d_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN notif_inact_30d_en TIMESTAMP",
    # v1.8 — País de lead (multi-país)
    "ALTER TABLE bolsa_leads ADD COLUMN pais VARCHAR DEFAULT 'AR'",
    # v1.9 — Compra de créditos en USD (aliados internacionales)
    "ALTER TABLE solicitudes_compra_creditos ADD COLUMN moneda VARCHAR DEFAULT 'ars'",
    # v2.0 — Onboarding por email (secuencia día 1, 3, 7)
    "ALTER TABLE aliados ADD COLUMN onboarding_email_d1_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN onboarding_email_d3_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN onboarding_email_d7_en TIMESTAMP",
    # Marca si el aliado ya personalizó su slug una vez. Si está NULL,
    # significa que el ref_code es autogenerado y todavía puede reclamarlo.
    "ALTER TABLE aliados ADD COLUMN username_personalizado_en TIMESTAMP",
]:
    _aplicar_migracion(col_sql)


# ─── EMAIL HELPER ─────────────────────────────────────────────────────────────
SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASS     = os.environ.get("SMTP_PASS", "")
EMAIL_FROM    = os.environ.get("EMAIL_FROM", SMTP_USER)
ADMIN_EMAIL   = os.environ.get("ADMIN_EMAIL", "avanzadigital4@gmail.com")

# ─── MERCADOPAGO ──────────────────────────────────────────────────────────────
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
MP_WEBHOOK_SECRET = os.environ.get("MP_WEBHOOK_SECRET", "")
# URL pública del BACKEND (donde viven los endpoints /webhooks/*). DEBE ser la URL real del backend.
# Los webhooks de Mercado Pago se configuran usando esta URL.
BACKEND_PUBLIC_URL = os.environ.get("BACKEND_PUBLIC_URL", "https://avanza-digital.onrender.com")
# URL del portal del aliado (para links en emails). Si backend y portal viven en el mismo dominio, coinciden.
PORTAL_URL      = os.environ.get("PORTAL_URL", BACKEND_PUBLIC_URL)

# ─── USDT TRC20 (TronGrid HD Wallet) ─────────────────────────────────────────
TRON_MNEMONIC       = os.environ.get("TRON_MNEMONIC", "")
USDT_CONTRACT       = os.environ.get("USDT_CONTRACT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
TRONGRID_API_KEY    = os.environ.get("TRONGRID_API_KEY", "")
USDT_TOLERANCIA_PCT = float(os.environ.get("USDT_TOLERANCIA_PCT", "0.01"))
USDT_CONFIRMACIONES = int(os.environ.get("USDT_CONFIRMACIONES_MIN", "19"))

# ─── RESEND (fallback de emails) ─────────────────────────────────────────────
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM    = os.environ.get("RESEND_FROM", "Avanza Digital <no-reply@avanzadigital.digital>")

# ─── BREVO (emails transaccionales — proveedor primario) ─────────────────────
# Free tier: 300 emails/día, 9.000/mes — permanente, sin tarjeta.
# Variable de entorno: BREVO_API_KEY
#
# IMPORTANTE: Brevo (y cualquier proveedor serio) rechaza enviar desde Gmail
# genérico — los emails enviados desde @gmail.com vía Brevo terminan en spam
# o son directamente rechazados (DMARC reject). Por defecto usamos un
# remitente del dominio propio. ANTES de que esto funcione hay que:
#   1) Verificar el dominio avanzadigital.digital en Brevo
#   2) Configurar los registros SPF, DKIM y DMARC en la zona DNS
# Sin esos pasos el From debe coincidir con un sender verificado en Brevo.
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_FROM    = os.environ.get("BREVO_FROM", "no-reply@avanzadigital.digital")
BREVO_FROM_NAME = os.environ.get("BREVO_FROM_NAME", "Avanza Digital")

# ─── DOLAR API ───────────────────────────────────────────────────────────────
DOLARAPI_URL = os.environ.get("DOLARAPI_URL", "https://dolarapi.com/v1/dolares/blue")
# DOLAR_FALLBACK: tipo de cambio ARS/USD usado cuando dolarapi.com no responde
# y tampoco hay ningún valor cacheado. Configurar en Render como variable de entorno.
# Ejemplo: DOLAR_FALLBACK=1250

# ─── FRONT URLS (para redirecciones post-pago) ───────────────────────────────
SUCCESS_URL = os.environ.get("CHECKOUT_SUCCESS_URL", "https://avanzadigital.digital/gracias")
FAILURE_URL = os.environ.get("CHECKOUT_FAILURE_URL", "https://avanzadigital.digital/error")

# ─── DATOS BANCARIOS PARA COMPRA DE CRÉDITOS (v1.7) ──────────────────────────
# Cuenta donde el aliado transfiere para comprar paquetes de créditos.
# El cobro es manual: el admin verifica la transferencia y confirma la
# solicitud desde el panel. Para cambiar la cuenta sin tocar código, usar
# las variables de entorno correspondientes.
DATOS_BANCARIOS = {
    "titular":          os.environ.get("BANK_TITULAR",  "Iván Darío Galarza"),
    "banco":            os.environ.get("BANK_NOMBRE",   "Naranja X"),
    "alias":            os.environ.get("BANK_ALIAS",    "avanza.digital"),
    "cbu":              os.environ.get("BANK_CBU",      "4530000800013725998554"),
    # WhatsApp para el botón "Ya transferí, avisar"
    "whatsapp_display": os.environ.get("BANK_WHATSAPP_DISPLAY", "+54 9 342 439 2759"),
    "whatsapp_link":    os.environ.get("BANK_WHATSAPP_LINK",    "5493424392759"),  # solo dígitos para wa.me
    # Email donde se notifican las nuevas solicitudes
    "email_admin":      os.environ.get("BANK_EMAIL_ADMIN", "avanzadigital4@gmail.com"),
}

# Vigencia de la solicitud de compra (horas). Pasado este tiempo, el cron la
# marca como 'expirada' y el aliado debe generar otra al cambio del momento.
SOLICITUD_CREDITOS_EXPIRACION_HS = int(os.environ.get("SOLICITUD_CREDITOS_EXPIRACION_HS", "48"))


# ─── DATOS DE PAGO EN USD PARA ALIADOS INTERNACIONALES (v1.9) ────────────────
# Para aliados de otros países que no pueden transferir en pesos.
# Configurable por env vars para no tocar código si cambia el método.
# El método puede ser USDT (TRC20), Wise, banco USD, etc.
# La verificación sigue siendo manual (admin confirma cuando llega el pago).
DATOS_USD = {
    "metodo":         os.environ.get("USD_METODO",       "USDT"),
    "destinatario":   os.environ.get("USD_DESTINATARIO", "avanzadigital4@gmail.com"),
    "etiqueta_dest":  os.environ.get("USD_ETIQUETA",     "Dirección USDT (TRC20)"),
    "red":            os.environ.get("USD_RED",          ""),  # ej: "TRC20" para USDT
    "notas":          os.environ.get("USD_NOTAS",        "Enviá el monto exacto en USD. Recibís los créditos cuando confirmamos el pago (24hs hábiles)."),
}

# ─── USDT/USDC para clientes finales ─────────────────────────────────────────
# USD_DESTINO y USD_RED configuran el método de pago cripto en la landing
# pública /p/{ref_code}. Si USD_DESTINO no está definido, cae a USD_DESTINATARIO.
USDT_DIRECCION = os.environ.get("USD_DESTINO", os.environ.get("USD_DESTINATARIO", ""))
USDT_RED       = os.environ.get("USD_RED", "TRC20")


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


# ─── DOLAR API: tipo de cambio blue en tiempo real ───────────────────────────
# Cache en memoria con TTL. Evita hammering a dolarapi cada vez que un aliado abre el cotizador.
# TTL de 5 min es buen balance entre frescura y carga externa.
_tc_cache = {"value": None, "fetched_at": None, "ttl_seconds": 300}

async def obtener_tipo_de_cambio() -> float:
    """Consulta dolarapi.com y devuelve el valor de venta del dólar blue.
    Se llama en el momento de generar el link de pago (no al abrir el cotizador).
    Cachea el resultado por 5 minutos para evitar llamadas innecesarias."""
    now = datetime.now()
    cached = _tc_cache["value"]
    fetched_at = _tc_cache["fetched_at"]
    if cached and fetched_at and (now - fetched_at).total_seconds() < _tc_cache["ttl_seconds"]:
        return cached
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(DOLARAPI_URL)
            if r.status_code == 200:
                data = r.json()
                venta = data.get("venta") or data.get("compra")
                if venta:
                    _tc_cache["value"] = float(venta)
                    _tc_cache["fetched_at"] = now
                    return float(venta)
    except Exception as e:
        print(f"[DOLAR API ERROR] {e}")
    # Fallback: preferir el último valor cacheado (aunque esté vencido) antes que el hardcoded.
    # Solo si nunca se pudo consultar dolarapi, usar el env DOLAR_FALLBACK como último recurso.
    if cached:
        print(f"[DOLAR API] Usando último valor cacheado (stale): {cached}")
        return cached
    return float(os.environ.get("DOLAR_FALLBACK", "1250"))



# ─── VERIFICACIÓN DE FIRMA HMAC EN WEBHOOK DE MERCADOPAGO ────────────────────
def verificar_firma_mp(raw_body: bytes, headers, query_params) -> bool:
    """Verifica firma HMAC-SHA256 del webhook de Mercado Pago.
    MP envía el header x-signature con formato: `ts=<ts>,v1=<hash>`.
    El manifest firmado es: `id:<data.id>;request-id:<x-request-id>;ts:<ts>;`.

    FAIL-CLOSED: si MP_WEBHOOK_SECRET no está seteado, devuelve False salvo que
    AVANZA_INSECURE_WEBHOOKS=1 (modo dev local explícito). Esto previene que un
    deploy con env var faltante quede aceptando webhooks falsos.
    """
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


# ─── SCHEDULER: NOTIFICACIÓN 24HS ────────────────────────────────────────────
def job_notificaciones_24h():
    """Corre cada hora. Detecta leads reclamados hace 24hs sin contactar y avisa al aliado."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        limite_inf = ahora - timedelta(hours=25)  # entre 24 y 25 hs
        limite_sup = ahora - timedelta(hours=24)

        pendientes = db.query(LeadBolsa).filter(
            LeadBolsa.estado == "reclamado",
            LeadBolsa.notif_24h_enviada == False,
            LeadBolsa.fecha_reclamo <= limite_sup,
            LeadBolsa.fecha_reclamo >= limite_inf,
        ).all()

        for lead in pendientes:
            if lead.aliado and lead.aliado.email:
                horas_rest = max(0, int(48 - (ahora - lead.fecha_reclamo).total_seconds() / 3600))
                html = f"""
                <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
                  <h2 style="color:#f59e0b;margin-bottom:8px;">⏰ ¡Te quedan {horas_rest} horas!</h2>
                  <p>Hola <strong>{lead.aliado.nombre}</strong>,</p>
                  <p>Reclamaste el lead <strong>{lead.empresa}</strong> hace 24 horas y todavía no lo marcaste como contactado.</p>
                  <p style="color:#f87171;">Si no actualizás su estado en las próximas <strong>{horas_rest} horas</strong>, el sistema lo devolverá a la bolsa pública automáticamente.</p>
                  <a href="https://avanza-digital-production.up.railway.app/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ir al Portal →</a>
                  <p style="margin-top:24px;font-size:.8rem;color:#64748b;">Avanza Digital · Partner Network</p>
                </div>
                """
                enviar_email(lead.aliado.email, f"⏰ Avanza: Tenés {horas_rest}hs para contactar a {lead.empresa}", html)
                lead.notif_24h_enviada = True
        db.commit()
    except Exception as e:
        print(f"[SCHEDULER ERROR] {e}")
    finally:
        db.close()

scheduler = BackgroundScheduler()
# Si ENABLE_SCHEDULER != "1", reemplazamos add_job y start por no-ops.
# Esto permite seguir cargando el módulo sin que ningún job se registre, y
# evita ejecuciones duplicadas cuando hay >1 worker (uvicorn --workers N o
# gunicorn con varios workers). Patrón: activar ENABLE_SCHEDULER=1 SOLO en
# una instancia (worker dedicado) y dejarlo en "0" en las demás.
ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "1") == "1"
if not ENABLE_SCHEDULER:
    print("[SCHEDULER] Desactivado por ENABLE_SCHEDULER != '1' — no se registran jobs.")
    scheduler.add_job = lambda *args, **kwargs: None  # type: ignore[assignment]
    scheduler.start   = lambda *args, **kwargs: None  # type: ignore[assignment]

scheduler.add_job(job_notificaciones_24h, "interval", hours=1)


# ─── SCHEDULER: LIBERACIÓN AUTOMÁTICA A 48HS ─────────────────────────────────
def job_liberar_leads_48h():
    """Corre cada 30 min. Libera leads reclamados hace >48hs que nunca fueron contactados.
    Devuelve el lead al pool, notifica al aliado y deja log para auditoría."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        limite = datetime.now() - timedelta(hours=48)
        vencidos = db.query(LeadBolsa).filter(
            LeadBolsa.estado == "reclamado",
            LeadBolsa.fecha_reclamo != None,
            LeadBolsa.fecha_reclamo < limite,
        ).all()

        for lead in vencidos:
            aliado = lead.aliado
            aliado_email = aliado.email if aliado else None
            aliado_nombre = aliado.nombre if aliado else "—"
            print(f"[LIBERACIÓN 48H] Lead '{lead.empresa}' (id={lead.id}) liberado. Aliado previo: {aliado_nombre} ({lead.aliado_id}) — reclamó el {lead.fecha_reclamo}")

            lead.estado = "disponible"
            lead.aliado_id = None
            lead.fecha_reclamo = None
            lead.notif_24h_enviada = False

            if aliado_email:
                # Calculamos contadores históricos para que la IA pueda detectar patrones.
                if aliado:
                    leads_perdidos = sum(
                        1 for l in (aliado.leads_bolsa or [])
                        if l.id != lead.id and l.resultado is None and l.estado == "disponible"
                    )
                    leads_exitosos = sum(
                        1 for l in (aliado.leads_bolsa or [])
                        if l.resultado == "exitoso"
                    )
                else:
                    leads_perdidos = 0
                    leads_exitosos = 0

                email_ia = groq_ai.personalizar_email_lead_liberado_ia(
                    aliado_nombre=aliado_nombre,
                    lead_empresa=lead.empresa,
                    lead_rubro=lead.rubro,
                    leads_perdidos_previos=leads_perdidos,
                    leads_exitosos_previos=leads_exitosos,
                )

                if email_ia:
                    parrafos = [pr.strip() for pr in email_ia["cuerpo_texto"].split("\n\n") if pr.strip()]
                    cuerpo_html_inner = "".join(f"<p style='margin:0 0 12px 0;'>{pr.replace(chr(10), '<br>')}</p>" for pr in parrafos)
                    asunto_lead = email_ia["asunto"]
                    html = f"""
                    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
                      <div style="font-size:.78rem;color:#a855f7;font-weight:700;letter-spacing:.6px;text-transform:uppercase;margin-bottom:8px;">✨ Mensaje personalizado</div>
                      <div style="line-height:1.55;">{cuerpo_html_inner}</div>
                      <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ir a la bolsa →</a>
                    </div>
                    """
                else:
                    # Fallback: template fijo
                    asunto_lead = f"🚨 Avanza: perdiste el lead {lead.empresa} (48hs sin contactar)"
                    html = f"""
                    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
                      <h2 style="color:#f87171;margin-bottom:8px;">🚨 Lead liberado automáticamente</h2>
                      <p>Hola <strong>{aliado_nombre.split()[0] if aliado_nombre else ''}</strong>,</p>
                      <p>El lead <strong>{lead.empresa}</strong> volvió a la bolsa porque pasaron más de 48 horas sin que lo marcaras como contactado.</p>
                      <p style="color:#a1a1aa;">Otros aliados ya pueden reclamarlo. Si tiene buen potencial, podés volver a tomarlo si sigue disponible.</p>
                      <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ir a la bolsa →</a>
                    </div>
                    """

                enviar_email(aliado_email, asunto_lead, html)

        db.commit()
    except Exception as e:
        print(f"[LIBERACIÓN 48H ERROR] {e}")
    finally:
        db.close()


# ─── SCHEDULER: EXPIRACIÓN DE LINKS DE PAGO ──────────────────────────────────
def job_expirar_links_pago():
    """Corre cada hora. Marca como 'vencido' los links de pago cuya fecha expires_at ya pasó."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        vencidos = db.query(LinkPago).filter(
            LinkPago.estado == "activo",
            LinkPago.expires_at != None,
            LinkPago.expires_at < ahora,
        ).all()
        for lp in vencidos:
            lp.estado = "vencido"
        if vencidos:
            print(f"[LINKS PAGO] {len(vencidos)} link(s) marcados como vencidos.")
        db.commit()
    except Exception as e:
        print(f"[LINKS PAGO ERROR] {e}")
    finally:
        db.close()



# ─── SCHEDULER: VERIFICACIÓN DE PAGOS USDT (TronGrid polling) ────────────────
def _verificar_link_usdt(db, lp):
    """Consulta TronGrid por transferencias TRC20 confirmadas a lp.usdt_address."""
    url     = f"https://api.trongrid.io/v1/accounts/{lp.usdt_address}/transactions/trc20"
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY} if TRONGRID_API_KEY else {}
    params  = {"contract_address": USDT_CONTRACT, "limit": 10, "only_confirmed": "true"}
    try:
        resp = __import__('httpx').get(url, headers=headers, params=params, timeout=10)
    except Exception as e:
        print(f"[USDT POLL] Timeout/error TronGrid link {lp.id}: {e}")
        return
    if resp.status_code != 200:
        print(f"[USDT POLL] TronGrid HTTP {resp.status_code} link {lp.id}")
        return

    for tx in resp.json().get("data", []):
        if tx.get("to") != lp.usdt_address:
            continue
        token_info = tx.get("token_info", {})
        if token_info.get("address") != USDT_CONTRACT:
            continue
        decimals   = int(token_info.get("decimals", 6))
        valor_usdt = int(tx.get("value", 0)) / (10 ** decimals)
        monto_min  = lp.usdt_monto_exp * (1 - USDT_TOLERANCIA_PCT)
        if valor_usdt < monto_min:
            continue
        # Verificar que la tx es posterior a la creación del link
        ts_ms = tx.get("block_timestamp", 0)
        if ts_ms:
            from datetime import timezone
            ts = __import__('datetime').datetime.utcfromtimestamp(ts_ms / 1000)
            if lp.created_at and ts < lp.created_at:
                continue
        tx_hash = tx.get("transaction_id", "unknown")
        print(f"[USDT POLL] ✅ Pago detectado: link {lp.id} | tx {tx_hash} | {valor_usdt:.2f} USDT")
        partes         = (lp.external_ref or "").split("|")
        ref_code       = partes[0] if len(partes) > 0 else ""
        plan           = partes[1] if len(partes) > 1 else lp.plan
        nombre_cliente = partes[2] if len(partes) > 2 else "Cliente"
        lp.usdt_tx_hash = tx_hash
        db.commit()
        _procesar_pago_confirmado(db, ref_code=ref_code, plan=plan,
                                  nombre_cliente=nombre_cliente,
                                  processor="usdt", payment_id=tx_hash,
                                  link_pago_id=lp.id)
        return


def job_verificar_pagos_usdt():
    """Corre cada 30s. Verifica pagos USDT pendientes consultando TronGrid."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora      = datetime.now()
        pendientes = db.query(LinkPago).filter(
            LinkPago.processor == "usdt",
            LinkPago.estado    == "activo",
            LinkPago.expires_at > ahora,
        ).all()
        if pendientes:
            print(f"[USDT POLL] Revisando {len(pendientes)} links activos...")
        for lp in pendientes:
            if lp.usdt_address:
                try:
                    _verificar_link_usdt(db, lp)
                except Exception as e:
                    print(f"[USDT POLL] Error link {lp.id}: {e}")
    finally:
        db.close()


scheduler.add_job(job_liberar_leads_48h, "interval", minutes=30)
scheduler.add_job(job_expirar_links_pago, "interval", hours=1)
scheduler.add_job(job_verificar_pagos_usdt, "interval", seconds=30)


# ─── SCHEDULER: NOTIFICACIONES DE INACTIVIDAD ────────────────────────────────
def job_notificaciones_inactividad():
    """Corre 1x/día. Envía emails escalonados a aliados inactivos:
       - Día 20: recordatorio amigable ("te extrañamos").
       - Día 30: advertencia de cuenta en riesgo.
       Usa columnas notif_inact_20d_en / notif_inact_30d_en para no spamear.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        corte_20d = ahora - timedelta(days=20)
        corte_30d = ahora - timedelta(days=30)
        # Ventana de re-envío: no mandar de nuevo hasta 25 días después
        no_repetir_antes = ahora - timedelta(days=25)

        aliados = db.query(Aliado).filter(Aliado.activo == True).all()

        enviados_20d = 0
        enviados_30d = 0

        for a in aliados:
            if not a.email:
                continue

            ultimo = getattr(a, "ultimo_login", None)
            # Fallback: si nunca entró, usamos fecha de creación como referencia.
            # Así un aliado que se registró pero nunca accedió también recibe
            # los avisos de inactividad a los 20 y 30 días.
            if not ultimo:
                ultimo = getattr(a, "creado_en", None)
            if not ultimo:
                continue

            dias_inactivo = (ahora - ultimo).days

            # ── AVISO 30 DÍAS: cuenta en riesgo + créditos de reactivación ──
            if dias_inactivo >= 30:
                notif_30d = getattr(a, "notif_inact_30d_en", None)
                # Evitar reenvío hasta 25 días después del último aviso
                if notif_30d and notif_30d > no_repetir_antes:
                    continue
                nombre_corto = a.nombre.split()[0] if a.nombre else "Aliado"

                # Reactivación con créditos: damos 50 créditos para que vuelvan
                # con ammo. La idempotencia ya está garantizada por la ventana
                # de 25 días del flag `notif_inact_30d_en`. Si vuelve a iniciar
                # sesión, el flag se resetea (ver login) y, si vuelve a quedar
                # inactivo, recibirá créditos otra vez en el próximo ciclo.
                BONUS_REACTIVACION = 50
                saldo_previo = a.creditos or 0
                try:
                    _ajustar_creditos(db, a, BONUS_REACTIVACION,
                                      "reactivacion", "inactividad_30d")
                    saldo_nuevo = a.creditos or 0
                except Exception as e:
                    print(f"[REACTIVACIÓN ERROR] {a.codigo}: {e}")
                    saldo_nuevo = saldo_previo

                html = f"""
                <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                  <div style="margin-bottom:24px;">
                    <span style="background:#7f1d1d;color:#fca5a5;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">⚠️ Cuenta en riesgo</span>
                  </div>
                  <h2 style="margin:0 0 12px;font-size:1.4rem;color:#f87171;">Hace {dias_inactivo} días que no entrás al portal</h2>
                  <p style="color:#a1a1aa;line-height:1.6;">Hola <strong style="color:#fff;">{nombre_corto}</strong>, notamos que tu cuenta en Avanza Digital lleva más de un mes sin actividad.</p>

                  <div style="background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.25);border-radius:8px;padding:18px;margin:20px 0;">
                    <p style="margin:0 0 6px;color:#c084fc;font-weight:700;font-size:1rem;">🎁 Te regalamos {BONUS_REACTIVACION} créditos para que vuelvas</p>
                    <p style="margin:0;color:#a1a1aa;font-size:.9rem;line-height:1.5;">Saldo nuevo: <strong style="color:#fff;">{saldo_nuevo} créditos</strong>. Usalos en el marketplace de leads premium del portal.</p>
                  </div>

                  <div style="background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:20px;margin:20px 0;">
                    <p style="margin:0 0 8px;font-weight:600;color:#f87171;">¿Qué puede pasar si no ingresás?</p>
                    <ul style="margin:0;padding-left:18px;color:#a1a1aa;line-height:1.8;">
                      <li>Los leads que tengas reclamados pueden liberarse automáticamente</li>
                      <li>Tu cuenta puede marcarse como inactiva y dejar de recibir nuevos leads</li>
                      <li>Podrías perder tu posición en la red de aliados</li>
                    </ul>
                  </div>
                  <p style="color:#a1a1aa;">Si estás pasando por un momento difícil o necesitás ayuda, respondé este email y te ayudamos.</p>
                  <a href="{PORTAL_URL}/portal.html" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:8px;">Reactivar mi cuenta →</a>
                  <p style="margin-top:28px;font-size:.75rem;color:#3f3f46;">Avanza Digital · Partner Network · Para darte de baja respondé este email con "baja".</p>
                </div>
                """
                enviar_email(a.email, f"🎁 {nombre_corto}, te dejamos {BONUS_REACTIVACION} créditos para que vuelvas", html)
                try:
                    a.notif_inact_30d_en = ahora
                except Exception:
                    pass
                db.commit()
                enviados_30d += 1

            # ── RECORDATORIO 20 DÍAS: amigable ───────────────────────────────
            elif dias_inactivo >= 20:
                notif_20d = getattr(a, "notif_inact_20d_en", None)
                if notif_20d and notif_20d > no_repetir_antes:
                    continue
                nombre_corto = a.nombre.split()[0] if a.nombre else "Aliado"
                html = f"""
                <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                  <div style="margin-bottom:24px;">
                    <span style="background:#1c1917;color:#fdba74;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">👋 Te extrañamos</span>
                  </div>
                  <h2 style="margin:0 0 12px;font-size:1.4rem;color:#fb923c;">¡{nombre_corto}, hace rato que no aparecés!</h2>
                  <p style="color:#a1a1aa;line-height:1.6;">Notamos que llevás <strong style="color:#fb923c;">{dias_inactivo} días</strong> sin ingresar al portal de Avanza Digital.</p>
                  <div style="background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:20px;margin:20px 0;">
                    <p style="margin:0 0 12px;font-weight:600;color:#fff;">Mientras estuviste ausente…</p>
                    <ul style="margin:0;padding-left:18px;color:#a1a1aa;line-height:1.8;">
                      <li>Nuevos leads están disponibles en la bolsa</li>
                      <li>Tus prospectos necesitan seguimiento</li>
                      <li>Puede haber comisiones pendientes de revisar</li>
                    </ul>
                  </div>
                  <a href="{PORTAL_URL}/portal.html" style="display:inline-block;padding:14px 28px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:8px;">Volver al portal →</a>
                  <p style="margin-top:28px;font-size:.75rem;color:#3f3f46;">Avanza Digital · Partner Network · ¿Preguntas? Respondé este email.</p>
                </div>
                """
                enviar_email(a.email, f"👋 {nombre_corto}, ¿todo bien? Hace {dias_inactivo} días que no entrás", html)
                try:
                    a.notif_inact_20d_en = ahora
                except Exception:
                    pass
                db.commit()
                enviados_20d += 1

        print(f"[INACTIVIDAD] Notificaciones enviadas — 20d: {enviados_20d}, 30d: {enviados_30d}")
    except Exception as e:
        print(f"[INACTIVIDAD ERROR] {e}")
    finally:
        db.close()


# ─── SCHEDULER: ESTIPENDIO MENSUAL ───────────────────────────────────────────
def job_estipendio_mensual():
    """Corre 1x/día. Solo ejecuta el día 1 de cada mes.
       Otorga 20 créditos a cada aliado activo con al menos un login en los
       últimos 30 días (definición operativa de "aliado activo").
       Idempotente: usa la referencia 'estipendio:YYYY-MM' como anti-duplicado.
       Si por alguna razón el cron corre dos veces el mismo día (deploy mid-day,
       reinicio del scheduler), no acredita dos veces.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        # SOLO el día 1 del mes
        if ahora.day != 1:
            return

        BONUS_ESTIPENDIO = 50
        VENTANA_ACTIVIDAD_DIAS = 30
        ref_mes = f"estipendio:{ahora.year}-{ahora.month:02d}"
        corte_actividad = ahora - timedelta(days=VENTANA_ACTIVIDAD_DIAS)

        aliados = db.query(Aliado).filter(Aliado.activo == True).all()
        otorgados = 0
        ya_recibidos = 0
        no_activos = 0

        for a in aliados:
            ultimo = getattr(a, "ultimo_login", None)
            if not ultimo or ultimo < corte_actividad:
                no_activos += 1
                continue

            # Anti-duplicado: ¿ya recibió estipendio de este mes?
            ya = db.query(TransaccionCredito).filter(
                TransaccionCredito.aliado_id == a.id,
                TransaccionCredito.motivo == "estipendio_mensual",
                TransaccionCredito.referencia == ref_mes,
            ).first()
            if ya:
                ya_recibidos += 1
                continue

            try:
                _ajustar_creditos(db, a, BONUS_ESTIPENDIO,
                                  "estipendio_mensual", ref_mes)
                otorgados += 1
            except Exception as e:
                print(f"[ESTIPENDIO ERROR] {a.codigo}: {e}")

        db.commit()
        print(f"[ESTIPENDIO {ref_mes}] Otorgados: {otorgados} · Ya recibidos: {ya_recibidos} · No activos: {no_activos}")
    except Exception as e:
        print(f"[ESTIPENDIO JOB ERROR] {e}")
    finally:
        db.close()


# ─── SCHEDULER: SECUENCIA DE ONBOARDING (DÍA 1, 3, 7) ────────────────────────
def job_onboarding_sequence():
    """Corre 1x/día. Manda emails educativos a aliados nuevos en su primera
    semana, escalonados:
       - Día 1 (24-48hs desde registro): "Probá un lead BÁSICO gratis"
       - Día 3: "¿Querés ver leads calificados? Te alcanza para 1-2"
       - Día 7: si NO compró ningún lead premium → "no desperdicies tus créditos"

    Cada email se manda una sola vez por aliado (flags onboarding_email_dN_en).
    Si la app estuvo caída y un aliado pasó del día 1 al día 3 sin que se le
    haya mandado el d1, igual recibe el d1 atrasado (mejor tarde que nunca).
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        aliados = db.query(Aliado).filter(Aliado.activo == True).all()
        enviados_d1 = enviados_d3 = enviados_d7 = 0

        for a in aliados:
            if not a.email or not a.creado_en:
                continue
            dias_desde_registro = (ahora - a.creado_en).days
            # Limitar la ventana: si pasó más de 14 días desde el registro, no
            # mandamos onboarding atrasado (probablemente el aliado ya entendió
            # cómo funciona el portal o está perdido por otras razones).
            if dias_desde_registro > 14:
                continue

            nombre_corto = a.nombre.split()[0] if a.nombre else "Aliado"

            # ── DÍA 1 ───────────────────────────────────────────────────────
            if dias_desde_registro >= 1 and not getattr(a, "onboarding_email_d1_en", None):
                html = f"""
                <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                  <span style="background:#1e3a8a;color:#93c5fd;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">Día 1 · Tu primer lead</span>
                  <h2 style="margin:18px 0 12px;font-size:1.4rem;color:#fff;">¡Bienvenido al portal, {nombre_corto}!</h2>
                  <p style="color:#a1a1aa;line-height:1.6;">Lo más importante para arrancar: <strong style="color:#fff;">no necesitás gastar tus créditos todavía</strong>.</p>
                  <div style="background:#0f1d12;border:1px solid #14532d;border-radius:8px;padding:18px;margin:20px 0;">
                    <p style="margin:0 0 6px;color:#86efac;font-weight:700;">🎁 Empezá con un lead BÁSICO gratis</p>
                    <p style="margin:0;color:#a1a1aa;line-height:1.5;">Los leads básicos no consumen créditos. Son ideales para que practiques el guión de venta sin gastar nada. Andá a la "Bolsa de Leads" y filtrá por tier "Básico".</p>
                  </div>
                  <p style="color:#a1a1aa;line-height:1.6;font-size:.92rem;">Tus 100 créditos de bienvenida son para los leads <strong style="color:#fff;">calificados</strong> y <strong style="color:#fff;">premium</strong> — esos los abrimos en el próximo email cuando ya hayas probado uno básico.</p>
                  <a href="{PORTAL_URL}/portal.html#bolsa" style="display:inline-block;padding:14px 28px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:12px;">Ver leads básicos →</a>
                </div>
                """
                try:
                    enviar_email(a.email, f"🎯 {nombre_corto}, así arrancás (sin gastar tus créditos)", html)
                    a.onboarding_email_d1_en = ahora
                    db.commit()
                    enviados_d1 += 1
                except Exception as e:
                    print(f"[ONBOARDING D1 ERROR] {a.codigo}: {e}")

            # ── DÍA 3 ───────────────────────────────────────────────────────
            if dias_desde_registro >= 3 and not getattr(a, "onboarding_email_d3_en", None):
                saldo = a.creditos or 0
                html = f"""
                <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                  <span style="background:#3b0764;color:#c084fc;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">Día 3 · Leads calificados</span>
                  <h2 style="margin:18px 0 12px;font-size:1.4rem;color:#fff;">Listo, ahora sí: leads calificados</h2>
                  <p style="color:#a1a1aa;line-height:1.6;">Tenés <strong style="color:#c084fc;">{saldo} créditos</strong> en tu saldo. Te alcanzan para 1-2 leads del tier "Calificado", que son contactos pre-filtrados por nosotros.</p>
                  <div style="background:#1a0a2e;border:1px solid #3b0764;border-radius:8px;padding:18px;margin:20px 0;">
                    <p style="margin:0 0 8px;color:#c084fc;font-weight:700;">Cómo elegir bien tu primer calificado:</p>
                    <ul style="margin:0;padding-left:18px;color:#a1a1aa;line-height:1.7;font-size:.92rem;">
                      <li>Mirá el <strong style="color:#fff;">rubro</strong>: elegí uno donde te sientas cómodo armando una propuesta</li>
                      <li>Mirá el <strong style="color:#fff;">score de calidad</strong>: arriba de 70 es seguro</li>
                      <li>Mirá si <strong style="color:#fff;">tiene web/redes</strong>: te da contexto para personalizar el pitch</li>
                    </ul>
                  </div>
                  <p style="color:#a1a1aa;line-height:1.5;font-size:.9rem;">Tip: si el contacto que comprás resulta inválido, podés reportarlo dentro de las 72hs y te devolvemos los créditos.</p>
                  <a href="{PORTAL_URL}/portal.html#bolsa" style="display:inline-block;padding:14px 28px;background:#a855f7;color:#fff;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:12px;">Ver leads calificados →</a>
                </div>
                """
                try:
                    enviar_email(a.email, f"⭐ {nombre_corto}, hora de probar un lead calificado", html)
                    a.onboarding_email_d3_en = ahora
                    db.commit()
                    enviados_d3 += 1
                except Exception as e:
                    print(f"[ONBOARDING D3 ERROR] {a.codigo}: {e}")

            # ── DÍA 7 ───────────────────────────────────────────────────────
            # Solo si NO compró ningún lead premium todavía (gastó 0 créditos en compra_lead).
            if dias_desde_registro >= 7 and not getattr(a, "onboarding_email_d7_en", None):
                gasto_premium = db.query(TransaccionCredito).filter(
                    TransaccionCredito.aliado_id == a.id,
                    TransaccionCredito.motivo == "compra_lead",
                ).count()
                if gasto_premium == 0:
                    saldo = a.creditos or 0
                    html = f"""
                    <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                      <span style="background:#1c1917;color:#fdba74;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">Día 7 · No los desperdicies</span>
                      <h2 style="margin:18px 0 12px;font-size:1.4rem;color:#fff;">{nombre_corto}, todavía tenés tus créditos sin usar</h2>
                      <p style="color:#a1a1aa;line-height:1.6;">Pasó una semana y tu saldo de <strong style="color:#fb923c;">{saldo} créditos</strong> sigue intacto. Eso es plata digital esperando por vos.</p>
                      <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:18px;margin:20px 0;">
                        <p style="margin:0 0 8px;color:#fff;font-weight:700;">¿Qué te frena?</p>
                        <p style="margin:0;color:#a1a1aa;line-height:1.6;font-size:.92rem;">Si no encontrás leads que te cierren por rubro/zona, respondé este email y te ayudamos. Si lo que falta es práctica, en la Academia hay guiones probados.</p>
                      </div>
                      <p style="color:#a1a1aa;line-height:1.5;font-size:.9rem;">Recordá: 1 lead premium cerrado paga 10x el costo en créditos. Es el riesgo más asimétrico del programa.</p>
                      <a href="{PORTAL_URL}/portal.html#bolsa" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:12px;">Ver leads premium →</a>
                    </div>
                    """
                    try:
                        enviar_email(a.email, f"⏳ {nombre_corto}, tus {saldo} créditos siguen sin usar", html)
                        a.onboarding_email_d7_en = ahora
                        db.commit()
                        enviados_d7 += 1
                    except Exception as e:
                        print(f"[ONBOARDING D7 ERROR] {a.codigo}: {e}")
                else:
                    # Ya compró premium, no hace falta el email de d7. Marcamos
                    # el flag para no chequear en cada corrida.
                    a.onboarding_email_d7_en = ahora
                    db.commit()

        if (enviados_d1 + enviados_d3 + enviados_d7) > 0:
            print(f"[ONBOARDING] Enviados — D1: {enviados_d1} · D3: {enviados_d3} · D7: {enviados_d7}")
    except Exception as e:
        print(f"[ONBOARDING JOB ERROR] {e}")
    finally:
        db.close()


# ─── SCHEDULER: COMISIONES RECURRENTES MENSUALES (v1.5) ──────────────────────
def job_generar_comisiones_recurrentes_mensual():
    """Corre 1x/día. Solo ejecuta el día 1 de cada mes.
       Genera la comisión recurrente del mes para cada Plan de Continuidad
       activo (titular 10% + sponsor 5% si tiene). Idempotente por
       (aliado_id, nombre_cliente, plan_label, mes, anio): si el aliado ya
       cobró su comisión de este mes (por ejemplo porque al firmar se generó
       la del mes en curso), el cron no la duplica.
       Si por alguna razón el cron corre dos veces el mismo día (deploy
       mid-day, reinicio del scheduler), no acredita dos veces.
       Al final manda un resumen al admin para que sepa qué transferir.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.utcnow()
        # SOLO el día 1 del mes
        if ahora.day != 1:
            return
        resumen = _generar_comisiones_recurrentes_del_mes(db, ahora.month, ahora.year)
        creadas_titular = resumen.get("creadas_titular", 0)
        creadas_sponsor = resumen.get("creadas_sponsor", 0)
        saltadas       = resumen.get("saltadas_por_idempotencia", 0)
        print(f"[RECURRENTE {ahora.year}-{ahora.month:02d}] "
              f"Titular: {creadas_titular} · "
              f"Sponsor: {creadas_sponsor} · "
              f"Saltadas: {saltadas}")

        # Email resumen al admin (no bloquea — si Gmail falla, igual quedan
        # las comisiones creadas en BD).
        try:
            admin_email = ADMIN_EMAIL
            detalle = resumen.get("detalle") or []
            total_pagar_titulares = sum(d.get("comision_titular_usd", 0) or 0
                                        for d in detalle if d.get("creado_titular"))
            total_pagar_sponsor   = sum(d.get("comision_sponsor_usd", 0) or 0
                                        for d in detalle if d.get("creado_sponsor"))
            total_pagar = round(total_pagar_titulares + total_pagar_sponsor, 2)
            mes_nombre = ahora.strftime("%B %Y").capitalize()

            filas_html = ""
            for d in detalle:
                if not (d.get("creado_titular") or d.get("creado_sponsor")):
                    continue
                filas_html += (
                    f"<tr>"
                    f"<td style='padding:6px 10px;border-bottom:1px solid #1f2937;'>{d.get('aliado_codigo') or '—'}</td>"
                    f"<td style='padding:6px 10px;border-bottom:1px solid #1f2937;'>{d.get('cliente') or '—'}</td>"
                    f"<td style='padding:6px 10px;border-bottom:1px solid #1f2937;'>{d.get('plan') or '—'}</td>"
                    f"<td style='padding:6px 10px;border-bottom:1px solid #1f2937;text-align:right;color:#fbbf24;font-weight:700;'>USD {d.get('comision_titular_usd', 0):,.2f}</td>"
                    f"<td style='padding:6px 10px;border-bottom:1px solid #1f2937;text-align:right;color:#a3a3a3;'>USD {d.get('comision_sponsor_usd', 0):,.2f}</td>"
                    f"</tr>"
                )

            cuerpo = f"""<div style="font-family:sans-serif;background:#0a0a0a;color:#fff;padding:24px;max-width:720px;margin:auto;">
              <h2 style="color:#fbbf24;margin:0 0 6px;">🔁 Comisiones recurrentes — {mes_nombre}</h2>
              <p style="margin:4px 0 18px;color:#a3a3a3;font-size:.9rem;">El cron del día 1 ya generó las comisiones de continuidad de este mes.</p>
              <div style="background:#111;border:1px solid #1f2937;border-radius:8px;padding:18px;margin-bottom:18px;">
                <div style="display:flex;gap:24px;flex-wrap:wrap;">
                  <div><div style="font-size:.7rem;color:#a3a3a3;text-transform:uppercase;letter-spacing:1px;">Titulares creadas</div><div style="font-size:1.8rem;font-weight:900;color:#fbbf24;">{creadas_titular}</div></div>
                  <div><div style="font-size:.7rem;color:#a3a3a3;text-transform:uppercase;letter-spacing:1px;">Sponsor (5% RED)</div><div style="font-size:1.8rem;font-weight:900;color:#a3a3a3;">{creadas_sponsor}</div></div>
                  <div><div style="font-size:.7rem;color:#a3a3a3;text-transform:uppercase;letter-spacing:1px;">Saltadas (ya existían)</div><div style="font-size:1.8rem;font-weight:900;color:#71717a;">{saltadas}</div></div>
                  <div><div style="font-size:.7rem;color:#a3a3a3;text-transform:uppercase;letter-spacing:1px;">Total a abonar</div><div style="font-size:1.8rem;font-weight:900;color:#4ade80;">USD {total_pagar:,.2f}</div></div>
                </div>
              </div>
              {("<table style='width:100%;border-collapse:collapse;font-size:.85rem;'><thead><tr style='background:#111;color:#a3a3a3;text-transform:uppercase;font-size:.72rem;letter-spacing:1px;'><th style='padding:8px 10px;text-align:left;'>Aliado</th><th style='padding:8px 10px;text-align:left;'>Cliente</th><th style='padding:8px 10px;text-align:left;'>Plan</th><th style='padding:8px 10px;text-align:right;'>Titular 10%</th><th style='padding:8px 10px;text-align:right;'>Sponsor 5%</th></tr></thead><tbody>" + filas_html + "</tbody></table>") if filas_html else "<p style='color:#71717a;'>No hubo comisiones nuevas este mes (probablemente ya estaban creadas).</p>"}
              <p style="margin-top:18px;font-size:.8rem;color:#71717a;">Las comisiones quedan en estado <strong>pendiente</strong>. Marcalas como abonadas desde el panel de Comisiones cuando hagas la transferencia.</p>
            </div>"""
            enviar_email(admin_email, f"🔁 Comisiones recurrentes generadas — {mes_nombre}", cuerpo)
        except Exception as e:
            print(f"[RECURRENTE EMAIL ADMIN] {e}", file=sys.stderr)
    except Exception as e:
        print(f"[RECURRENTE JOB ERROR] {e}", file=sys.stderr)
    finally:
        db.close()


scheduler.add_job(job_notificaciones_inactividad, "interval", hours=24)
scheduler.add_job(job_estipendio_mensual, "interval", hours=24)
scheduler.add_job(job_onboarding_sequence, "interval", hours=24)
scheduler.add_job(job_generar_comisiones_recurrentes_mensual, "interval", hours=24)
scheduler.start()


def _tier_badge(tier: str) -> str:
    if tier == 'calificado':
        return '<span style="color:#fbbf24;">⭐</span>'
    elif tier == 'premium':
        return '<span style="color:#a78bfa;">💎</span>'
    return ''

app = FastAPI(title="Avanza Partner Portal", version="1.5")

# ─── RATE LIMITING ───────────────────────────────────────────────────────────
# Usa slowapi (cliente in-memory por IP). Para multi-instancia mover a Redis.
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ─── CORS ────────────────────────────────────────────────────────────────────
# CORS_ORIGINS = lista CSV de orígenes permitidos. Default razonable.
# Para dev local podés agregar http://localhost:5500, http://127.0.0.1:5500, etc.
_default_origins = ",".join([
    "https://avanzadigital.digital",
    "https://www.avanzadigital.digital",
    "https://avanza-digital.onrender.com",
    "https://avanza-digital-production.up.railway.app",
])
_cors_env = os.environ.get("CORS_ORIGINS", _default_origins)
CORS_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()]

# AVANZA_CORS_OPEN=1 abre el CORS a todo el mundo (solo dev). Si está, log warning.
if os.environ.get("AVANZA_CORS_OPEN") == "1":
    print("[CORS] ⚠️  AVANZA_CORS_OPEN=1 — CORS abierto a *. Solo usar en dev.")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ─── RUTAS ADMIN ─────────────────────────────────────────────────────────────
# Solo se usan para el middleware de fallback con X-API-Key (legacy).
# Idealmente todas estas rutas también declararían `Depends(current_admin_required)`
# explícitamente, pero las que ya delegan en el middleware quedan cubiertas.
RUTAS_ADMIN = {
    ("POST",   "/aliados/crear"),
    ("POST",   "/admin/setup"),
    ("POST",   "/admin/login"),
    ("GET",    "/aliados"),
    ("GET",    "/aliados/suspendidos"),
    ("GET",    "/aliados/inactivos"),
    ("PATCH",  "/aliados/{codigo}/nivel"),
    ("POST",   "/aliados/{codigo}/suspender"),
    ("POST",   "/aliados/{codigo}/activar"),
    ("DELETE", "/aliados/{codigo}/eliminar"),
    ("GET",    "/referidos/pendientes"),
    ("POST",   "/ventas/registrar"),
    ("POST",   "/ventas/{id}/pagar"),
    ("GET",    "/dashboard"),
    ("GET",    "/admin/prospectos"),
    ("GET",    "/admin/auditorias"),
    ("POST",   "/admin/bolsa"),
    ("POST",   "/admin/bolsa-v2"),
    ("GET",    "/admin/bolsa"),
    ("POST",   "/admin/bolsa/{id}/revocar"),
    ("GET",    "/admin/historial-bolsa"),
    ("GET",    "/admin/reputacion/ranking"),
    ("POST",   "/admin/aliados/{codigo}/creditos"),
    ("POST",   "/admin/comunidad/{id}/fijar"),
    ("POST",   "/admin/comunidad/{id}/ocultar"),
    ("POST",   "/referidos/{id}/confirmar"),
    ("GET",    "/admin/comisiones"),
    ("POST",   "/admin/comisiones/{id}/abonar"),
    ("GET",    "/admin/pagos"),
    ("GET",    "/admin/programa/salud"),
    ("GET",    "/admin/academia"),
    ("POST",   "/admin/academia"),
    ("PATCH",  "/admin/academia/{id}"),
    ("DELETE", "/admin/academia/{id}"),
    # v1.7 — solicitudes de compra de créditos (cobro manual)
    ("GET",    "/admin/solicitudes-creditos"),
    ("POST",   "/admin/solicitudes-creditos/{sol_id}/confirmar"),
    ("POST",   "/admin/solicitudes-creditos/{sol_id}/rechazar"),
    # v1.7 — trigger manual del job de inactividad
    ("POST",   "/admin/notificar-inactivos"),
    # v2.0 — reportes de mal contacto (devolución de créditos)
    ("GET",    "/admin/reportes-mal-contacto"),
    ("POST",   "/admin/reportes-mal-contacto/{id}/aprobar"),
    ("POST",   "/admin/reportes-mal-contacto/{id}/rechazar"),
    # v2.0 — métricas de cohorte de fuga
    ("GET",    "/admin/cohorte-fuga"),
    ("GET",    "/admin/uso-creditos"),
}

def _es_ruta_admin(method: str, path: str) -> bool:
    segmentos_path = path.rstrip("/").split("/")
    for m, patron in RUTAS_ADMIN:
        if m != method:
            continue
        segmentos_patron = patron.rstrip("/").split("/")
        if len(segmentos_patron) != len(segmentos_path):
            continue
        if all(p == s or p.startswith("{") for p, s in zip(segmentos_patron, segmentos_path)):
            return True
    return False


@app.middleware("http")
async def verificar_auth_admin(request: Request, call_next):
    """Middleware de auth admin. Acepta:
      1. JWT en Authorization: Bearer ... con tipo='admin'  (preferido)
      2. X-API-Key === ADMIN_API_KEY (legacy, va a deprecarse)
    Si la ruta NO es admin, deja pasar.
    Importante: /admin/login NO requiere auth previa (es donde se obtiene el JWT)."""
    # Excepciones: /admin/login es público
    if request.url.path == "/admin/login":
        return await call_next(request)

    if not _es_ruta_admin(request.method, request.url.path):
        return await call_next(request)

    # 1) Intentar JWT
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(None, 1)[1].strip()
        try:
            from auth import decodificar_token
            payload = decodificar_token(token)
            if payload.get("tipo") == "admin":
                return await call_next(request)
        except Exception:
            pass  # caemos al fallback de API key

    # 2) Fallback: X-API-Key (legacy)
    if ADMIN_API_KEY:
        provided = request.headers.get("X-API-Key", "") or request.headers.get("x-api-key", "")
        if provided:
            import secrets as _secrets
            if _secrets.compare_digest(provided, ADMIN_API_KEY):
                return await call_next(request)

    # Sin auth válida
    if not ADMIN_API_KEY and not auth_header:
        return JSONResponse(status_code=503, content={
            "detail": "Auth admin no configurada. Setear ADMIN_API_KEY o usar /admin/login."
        })
    return JSONResponse(status_code=401, content={"detail": "Autenticación de admin inválida."})


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def hash_password(p): return pwd_context.hash(p)
def verify_password(plain, hashed): return pwd_context.verify(plain, hashed)


# ─── USERNAMES / SLUGS DE ALIADOS ────────────────────────────────────────────
# El ref_code de cada aliado se usa en URLs públicas:
#   - https://avanzadigital.digital/p/{ref_code}   (landing personal)
#   - https://avanzadigital.digital/alianzas?ref={ref_code}  (link de referido)
# Cuando el aliado elige su propio username (ej: "gonzaloasesor"),
# ese valor se guarda directamente en ref_code.

# Reservadas: nombres de rutas, áreas reservadas y términos sensibles.
# Bloqueamos también palabras tipo "admin"/"avanza" para evitar suplantación.
# NOTA: evitamos bloquear nombres comunes ("ivan", "juan", "maria") porque
# muchos aliados se llaman así. Solo bloqueamos identificadores que
# realmente impersonan a la marca o rompen el routing.
USERNAMES_RESERVADOS = frozenset({
    # rutas / áreas del sitio
    "admin", "api", "auth", "blog", "p", "alianzas", "aliados",
    "alianzas-canal1", "alianzas-canal2", "comenzar", "contratar",
    "cotizador", "demos", "descargas", "gracias", "gracias-aliado",
    "guia", "industrias", "leads-pymes", "logistica", "marketing",
    "marketing-rosario", "marketing-santa-fe", "metalurgica",
    "oil-gas", "automotriz", "agro", "clinica", "energia", "calidad",
    "tecnico", "auditoria-digital", "auditoria", "automatizar-ventas-pymes",
    "pago-unico-vs-alquiler-mensual", "politica", "portal", "recursos",
    "servicios", "sitemap", "robots", "favicon", "terminos", "terminos-aliados",
    "casos", "casos-exito", "checkout", "pago", "pagos", "checkout-success",
    "checkout-failure", "error", "404", "500", "register", "login", "signup",
    "logout", "webhook", "webhooks",
    # nombres comerciales propios — NO bloqueamos "ivan" solo, ese es nombre común
    "avanza", "avanzadigital", "avanza-digital", "ivanaranguren", "ivangalarza",
    "owner", "ceo", "fundador", "founder", "soporte",
    "support", "ayuda", "contacto", "contact",
    # genéricos peligrosos
    "test", "demo", "null", "undefined", "none", "void", "true", "false",
    "root", "system", "user", "users", "guest", "anonymous",
})

# Patrón de username permitido: 3-30 chars, lowercase, alfanumérico + guion.
# No puede empezar/terminar con guion ni tener guiones consecutivos.
import re
_USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?!-))*[a-z0-9]$")


def normalizar_username(u: str | None) -> str:
    """Normaliza el input antes de validar: trim, lowercase, sin acentos básicos."""
    if not u:
        return ""
    u = u.strip().lower()
    # Reemplazos básicos de acentos comunes en hispanos
    repl = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", " ": "-"}
    for k, v in repl.items():
        u = u.replace(k, v)
    return u


def validar_username(u: str) -> tuple[bool, str]:
    """Valida un username candidato. Retorna (ok, mensaje_error)."""
    if not u:
        return False, "El username es obligatorio."
    if len(u) < 3:
        return False, "Mínimo 3 caracteres."
    if len(u) > 30:
        return False, "Máximo 30 caracteres."
    if not _USERNAME_RE.match(u):
        return False, "Solo letras, números y guiones. No puede empezar/terminar con guion."
    if u in USERNAMES_RESERVADOS:
        return False, "Este nombre está reservado por el sistema."
    return True, ""


def username_disponible(db: Session, username: str, excluir_aliado_id: int | None = None) -> bool:
    """Chequea unicidad del username contra la columna ref_code (case-insensitive).
    Si `excluir_aliado_id` viene, ignora a ese aliado (caso de cambio de username)."""
    q = db.query(Aliado).filter(Aliado.ref_code == username)
    if excluir_aliado_id is not None:
        q = q.filter(Aliado.id != excluir_aliado_id)
    return q.first() is None


def generar_ref_code(nombre, db=None, username: str | None = None):
    """Genera el ref_code del aliado.

    - Si `username` viene y pasa validación + está disponible, se usa tal cual.
    - Si no, se autogenera con el algoritmo legacy (nombre[:6] + 4 dígitos).
    - El parámetro `db` es opcional pero necesario para chequear unicidad
      cuando el username viene; si no viene db, el caller debe haber
      validado disponibilidad antes.
    """
    if username:
        u = normalizar_username(username)
        ok, _ = validar_username(u)
        if ok and (db is None or username_disponible(db, u)):
            return u
    # Fallback: autogenerar
    base = nombre.split()[0].lower()[:6] if nombre else "ali"
    # Limpiar acentos del base
    base = normalizar_username(base) or "ali"
    # Garantizar que el base sea solo alfanumérico (sin guiones por seguridad)
    base = "".join(c for c in base if c.isalnum()) or "ali"
    return f"{base}{''.join(random.choices(string.digits, k=4))}"


def generar_codigo_aliado(db):
    # Buscamos el último aliado creado ordenando por ID de forma descendente
    ultimo_aliado = db.query(Aliado).order_by(Aliado.id.desc()).first()
    
    if not ultimo_aliado:
        return "AL-001"
    
    try:
        # Extraemos el número del último código (ej: de "AL-020" o "AL-21" sacamos el 20 o 21)
        numero_actual = int(ultimo_aliado.codigo.split('-')[1])
        siguiente_numero = numero_actual + 1
    except (IndexError, ValueError):
        # Si por alguna razón el código anterior tiene un formato diferente, usamos su ID como base segura
        siguiente_numero = ultimo_aliado.id + 1
        
    return f"AL-{str(siguiente_numero).zfill(3)}"


# ─── SALUD ───────────────────────────────────────────────────────────────────

@app.get("/")
def root(): return {"status": "Avanza Partner Portal activo", "version": "1.2"}

@app.get("/health")
def health():
    """Healthcheck público mínimo — para Render/Railway. Solo confirma que el
    proceso responde. NO toca DB para no romper el deploy si la DB tarda en
    levantar."""
    return {"status": "ok"}


# ─── HELPER: REGISTRAR ACCIÓN DE ADMIN EN BITÁCORA ───────────────────────────
def _admin_log(
    db: Session,
    admin: dict,
    request: Request,
    accion: str,
    entidad: str = None,
    entidad_id=None,
    detalle: dict = None,
):
    """Registra una acción admin sensible en admin_audit_log.

    `admin` es lo que devuelve `current_admin_required` — un dict con
    {via, username, ...}. Si por algún motivo viene incompleto, se loguea
    con 'desconocido' antes que perder el evento.

    Esta función NO hace commit (lo deja al caller para mantener atómico el
    flujo de "operación + auditoría" en la misma transacción). Si la
    operación falla y hay rollback, la entrada de auditoría también se
    revierte — eso es deseable: solo queremos auditar acciones que pasaron.
    """
    try:
        import json as _json
        detalle_str = _json.dumps(detalle, default=str, ensure_ascii=False) if detalle else None
        entry = AdminAuditLog(
            admin_username=(admin or {}).get("username") or "desconocido",
            via=(admin or {}).get("via"),
            accion=accion,
            entidad=entidad,
            entidad_id=str(entidad_id) if entidad_id is not None else None,
            detalle=detalle_str,
            ip=(request.client.host if request and request.client else None),
            user_agent=(request.headers.get("user-agent") if request else None),
        )
        db.add(entry)
    except Exception as e:
        # No queremos que un fallo de auditoría tumbe la operación principal.
        print(f"[ADMIN_AUDIT] Error registrando '{accion}': {e}")


# ─── HEALTHCHECK ADMIN — MÉTRICAS REALES ─────────────────────────────────────
@app.get("/admin/healthcheck")
def admin_healthcheck(
    admin: dict = Depends(current_admin_required),
    db: Session = Depends(get_db),
):
    """Healthcheck rico para el panel admin: estado del sistema + métricas
    operativas que importan para detectar problemas antes de que un aliado
    nos avise por WhatsApp.

    Diferencia con `/health`:
      - /health: para Render/uptime monitors (cheap, no tocar DB).
      - /admin/healthcheck: para el operador humano (queries reales).
    """
    now = datetime.now()
    desde_24h = now - timedelta(hours=24)
    desde_7d = now - timedelta(days=7)

    # --- Aliados ---
    aliados_total      = db.query(Aliado).count()
    aliados_activos    = db.query(Aliado).filter(Aliado.activo == True).count()
    aliados_login_24h  = db.query(Aliado).filter(Aliado.ultimo_login >= desde_24h).count()
    aliados_nuevos_7d  = db.query(Aliado).filter(Aliado.creado_en >= desde_7d).count()

    # --- Créditos circulando ---
    from sqlalchemy import func as _func
    total_creditos = db.query(_func.coalesce(_func.sum(Aliado.creditos), 0)).scalar() or 0

    # --- Bolsa de leads ---
    leads_disponibles = db.query(LeadBolsa).filter(LeadBolsa.estado == "disponible").count()
    leads_reclamados  = db.query(LeadBolsa).filter(LeadBolsa.estado == "reclamado").count()

    # --- Ventas ---
    ventas_confirmadas_7d = db.query(Venta).filter(
        Venta.confirmada == True, Venta.fecha_venta >= desde_7d
    ).count()
    ventas_pendientes_confirmar = db.query(Venta).filter(Venta.confirmada == False).count()

    # --- Comisiones ---
    comisiones_pendientes = db.query(Comision).filter(Comision.estado == "pendiente").count()
    monto_pendiente_usd = db.query(_func.coalesce(_func.sum(Comision.comision_usd), 0)).filter(
        Comision.estado == "pendiente"
    ).scalar() or 0.0

    # --- Solicitudes de compra de créditos ---
    solic_pendientes = db.query(SolicitudCompraCreditos).filter(
        SolicitudCompraCreditos.estado == "pendiente"
    ).count()

    # --- Reportes de mal contacto pendientes ---
    reportes_pendientes = db.query(ReporteMalContacto).filter(
        ReporteMalContacto.estado == "pendiente"
    ).count()

    # --- Última corrida visible de jobs cron ---
    # No tenemos un metadato directo, así que aproximamos: si en las últimas
    # 25hs no hubo NINGUNA TransaccionCredito automática (motivos no manuales),
    # el scheduler probablemente está caído. Heurística — útil para alertar.
    motivos_automaticos = ["estipendio", "bienvenida", "compra_lead", "primera_venta"]
    ultima_tx_auto = db.query(_func.max(TransaccionCredito.creado_en)).filter(
        TransaccionCredito.motivo.in_(motivos_automaticos)
    ).scalar()

    # --- Última acción admin registrada ---
    ultima_admin = db.query(_func.max(AdminAuditLog.creado_en)).scalar()

    # --- DB ping ---
    db_ok = True
    db_error = None
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_ok = False
        db_error = str(e)[:200]

    # Flags rojos para mostrar en el panel
    alertas = []
    if not db_ok:
        alertas.append({"nivel": "critico", "mensaje": "DB no responde a SELECT 1"})
    if ultima_tx_auto and (now - ultima_tx_auto) > timedelta(hours=25):
        alertas.append({"nivel": "warning",
                        "mensaje": f"No hubo transacciones automáticas en {(now - ultima_tx_auto).total_seconds()//3600:.0f}h — ¿scheduler caído?"})
    if not ultima_tx_auto:
        alertas.append({"nivel": "info",
                        "mensaje": "Nunca hubo transacciones automáticas (sistema nuevo o scheduler nunca corrió)."})
    if solic_pendientes >= 10:
        alertas.append({"nivel": "warning",
                        "mensaje": f"{solic_pendientes} solicitudes de compra pendientes — revisar panel."})
    if reportes_pendientes >= 5:
        alertas.append({"nivel": "warning",
                        "mensaje": f"{reportes_pendientes} reportes de mal contacto pendientes."})

    return {
        "fecha":   now.isoformat(),
        "status":  "ok" if db_ok and not [a for a in alertas if a["nivel"] == "critico"] else "degraded",
        "db":      {"ok": db_ok, "error": db_error},
        "aliados": {
            "total":       aliados_total,
            "activos":     aliados_activos,
            "login_24h":   aliados_login_24h,
            "nuevos_7d":   aliados_nuevos_7d,
        },
        "creditos": {"circulando_total": int(total_creditos)},
        "bolsa":   {"disponibles": leads_disponibles, "reclamados": leads_reclamados},
        "ventas":  {
            "confirmadas_7d":          ventas_confirmadas_7d,
            "pendientes_confirmar":    ventas_pendientes_confirmar,
        },
        "comisiones": {
            "pendientes":            comisiones_pendientes,
            "monto_pendiente_usd":   round(float(monto_pendiente_usd), 2),
        },
        "operaciones": {
            "solicitudes_credito_pendientes":   solic_pendientes,
            "reportes_mal_contacto_pendientes": reportes_pendientes,
        },
        "scheduler": {
            "ultima_tx_automatica": ultima_tx_auto.isoformat() if ultima_tx_auto else None,
        },
        "admin_audit": {
            "ultima_accion": ultima_admin.isoformat() if ultima_admin else None,
        },
        "alertas": alertas,
    }

# ─── DESCARGA DE MATERIALES (públicos) ───────────────────────────────────────
from fastapi.responses import RedirectResponse

@app.get("/brochure")
def descargar_brochure():
    """Redirige al brochure comercial en Drive o URL configurada."""
    url = os.environ.get("URL_BROCHURE", "https://avanzadigital.digital/alianzas#brochure")
    return RedirectResponse(url=url)

@app.get("/guion")
def descargar_guion():
    """Redirige al guión de ventas."""
    url = os.environ.get("URL_GUION", "https://avanzadigital.digital/alianzas#guion")
    return RedirectResponse(url=url)

@app.get("/contrato")
def ver_contrato():
    """Redirige al contrato de aliado."""
    url = os.environ.get("URL_CONTRATO", "https://avanzadigital.digital/alianzas#contrato")
    return RedirectResponse(url=url)


# ─── AUTO-REGISTRO PÚBLICO CON EFECTO RED ────────────────────────────────────

# ─── USERNAME / SLUG ENDPOINTS ───────────────────────────────────────────────

@app.get("/aliados/check-username/{username}")
@limiter.limit("30/minute")
def check_username_disponible(request: Request, username: str, db: Session = Depends(get_db)):
    """Endpoint público para chequear disponibilidad mientras el usuario tipea
    en el form de registro. Retorna formato uniforme para el frontend.

    Rate-limited (30/min por IP) para evitar abuso/scraping de usernames.
    """
    u = normalizar_username(username)
    ok, msg = validar_username(u)
    if not ok:
        return {"disponible": False, "valid": False, "razon": msg, "username": u}
    if not username_disponible(db, u):
        return {"disponible": False, "valid": True, "razon": "Ya está en uso.", "username": u}
    return {"disponible": True, "valid": True, "razon": "", "username": u}


@app.post("/aliados/me/cambiar-username")
def cambiar_username_aliado_actual(
    body: schemas.CambiarUsernameIn,
    db: Session = Depends(get_db),
    aliado: Aliado = Depends(current_aliado_required),
):
    """Permite a un aliado existente reclamar/cambiar su slug UNA SOLA VEZ.

    El ref_code viejo deja de funcionar, pero los registros históricos
    (sponsors, ventas, etc.) se conservan vía sponsor_id (no via ref_code).
    Si el aliado ya cambió su username una vez, debe contactar al admin.
    """
    if getattr(aliado, "username_personalizado_en", None):
        raise HTTPException(
            400,
            "Ya personalizaste tu link una vez. Si necesitás cambiarlo de nuevo, "
            "escribinos a soporte y lo coordinamos manualmente."
        )

    u = normalizar_username(body.username)
    ok, msg = validar_username(u)
    if not ok:
        raise HTTPException(400, f"Username inválido: {msg}")

    if not username_disponible(db, u, excluir_aliado_id=aliado.id):
        raise HTTPException(400, "Ese username ya está en uso. Probá con otro.")

    ref_code_viejo = aliado.ref_code
    aliado.ref_code = u
    aliado.username_personalizado_en = datetime.now()
    db.commit(); db.refresh(aliado)

    print(f"[USERNAME] {aliado.codigo}: {ref_code_viejo} → {u}")

    return {
        "ok": True,
        "ref_code_anterior": ref_code_viejo,
        "ref_code_nuevo": aliado.ref_code,
        "link_ref": f"{PORTAL_URL}/p/{aliado.ref_code}",
        "link_perfil": f"{PORTAL_URL}/p/{aliado.ref_code}",
        "mensaje": "¡Listo! Tu link personalizado quedó activo.",
    }


@app.post("/registrarse")
@limiter.limit("5/minute")
def auto_registro(request: Request, 
    background_tasks: BackgroundTasks,
    body: schemas.RegistroAliadoIn | None = Body(default=None),
    # === Compatibilidad legacy: query params ===
    # El frontend viejo manda todo por query string. Se mantiene por una
    # versión para no romper el portal mientras se actualiza.
    nombre: str = "", email: str = "", whatsapp: str = "",
    ciudad: str = "", perfil: str = "", password: str = "", dni: str = "",
    ref_sponsor: str = "",
    tipo_aliado: str = "canal1",
    acepto_terminos: bool = False,
    username: str = "",
    db: Session = Depends(get_db)
):
    """Registro self-serve público con sistema de Sub-Aliados.

    PREFERIR body JSON. Los query params siguen aceptándose como fallback
    para compatibilidad con el portal legacy, pero las contraseñas en
    query string aparecen en logs — migrar a body cuanto antes.
    """
    # Si vino body JSON, gana sobre query (más seguro).
    if body is not None:
        nombre, email, whatsapp = body.nombre, body.email, body.whatsapp
        password, dni = body.password, body.dni
        ciudad, perfil = body.ciudad, body.perfil
        ref_sponsor = body.ref_sponsor
        tipo_aliado = body.tipo_aliado
        acepto_terminos = body.acepto_terminos
        username = body.username or ""
    else:
        print("[REGISTRO] ⚠️  Recibido por query string — actualizar cliente a body JSON.")

    if not nombre or not email or not whatsapp or not password:
        raise HTTPException(400, "Nombre, email, WhatsApp y contraseña son obligatorios.")
    if len(password) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres.")
    if not acepto_terminos:
        raise HTTPException(400, "Debés aceptar los términos y condiciones del programa de aliados para continuar.")
    if db.query(Aliado).filter(Aliado.email == email).first():
        raise HTTPException(400, "Ya existe un aliado registrado con ese email.")

    # ─── USERNAME / SLUG ─────────────────────────────────────────────────────
    # Si el aliado eligió uno, validamos formato + unicidad.
    # Si no, se autogenera (mantiene compatibilidad con clientes legacy).
    username_normalizado = normalizar_username(username) if username else ""
    username_personalizado = False
    if username_normalizado:
        ok, msg = validar_username(username_normalizado)
        if not ok:
            raise HTTPException(400, f"Username inválido: {msg}")
        if not username_disponible(db, username_normalizado):
            raise HTTPException(400, "Ese username ya está en uso. Probá con otro.")
        username_personalizado = True

    ref_code_final = generar_ref_code(nombre, db=db, username=username_normalizado or None)

    # Buscar Sponsor si vino por invitación
    sponsor_id_db = None
    if ref_sponsor:
        sp = db.query(Aliado).filter(Aliado.ref_code == ref_sponsor).first()
        if sp:
            sponsor_id_db = sp.id

    a = Aliado(
        codigo       = generar_codigo_aliado(db),
        nombre       = nombre,
        email        = email,
        dni          = dni,
        whatsapp     = whatsapp,
        ciudad       = ciudad,
        perfil       = perfil,
        fecha_firma  = datetime.now().strftime("%d/%m/%Y"),
        ref_code     = ref_code_final,
        password_hash= hash_password(password),
        sponsor_id   = sponsor_id_db,
        tipo_aliado  = tipo_aliado if tipo_aliado in ("canal1", "canal2") else "canal1",
        terminos_aceptados = True,
        terminos_aceptados_en = datetime.now(),
    )
    # Si el aliado personalizó su slug, marcamos para impedir cambios futuros.
    if username_personalizado:
        try:
            a.username_personalizado_en = datetime.now()
        except Exception:
            # Si la columna no existe todavía (migración no corrió), seguimos.
            pass
    db.add(a); db.commit(); db.refresh(a)

    # 100 créditos de bienvenida — para que el aliado pueda explorar el
    # marketplace desde el primer día. Sin esto, ve "0 créditos" y la
    # función parece rota. Es señal de bienvenida, no costo real.
    _ajustar_creditos(db, a, 100, "bienvenida", "registro")
    db.commit()

    # Email de bienvenida — EN SEGUNDO PLANO (no bloquea la respuesta)
    # IMPORTANTE: este email NO debe mencionar el marketplace de créditos ni
    # invitar a gastarlos. El email del Día 1 (job_onboarding_sequence) ya cubre
    # ese ángulo correctamente. Aquí solo confirmamos el registro y damos el
    # primer paso accionable: reclamar un lead básico gratis.
    _nombre_corto = a.nombre.split()[0] if a.nombre else "Aliado"
    background_tasks.add_task(
        enviar_email,
        a.email,
        f"¡Bienvenido al Avanza Partner Network, {_nombre_corto}!",
        f"""
        <div style="font-family:Inter,sans-serif;background:#050505;color:#fff;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;">
          <h1 style="color:#f97316;font-size:1.6rem;margin-bottom:8px;">¡Ya sos Aliado Avanza! 🎉</h1>
          <p style="color:#a1a1aa;margin-bottom:28px;">Tu registro fue confirmado. Guardá estos datos para ingresar al portal.</p>

          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:20px;margin-bottom:24px;">
            <p style="margin:0 0 8px;font-size:.85rem;color:#71717a;text-transform:uppercase;letter-spacing:1px;">Tu código de aliado</p>
            <p style="margin:0;font-size:2rem;font-weight:900;color:#f97316;letter-spacing:2px;">{a.codigo}</p>
          </div>

          <p style="color:#a1a1aa;margin-bottom:8px;">Tu comisión arranca en <strong style="color:#fff;">10% (BASIC)</strong> y sube automáticamente con cada venta.</p>

          <div style="background:#0f1d12;border:1px solid #14532d;border-radius:8px;padding:18px;margin:20px 0;">
            <p style="margin:0 0 6px;color:#86efac;font-weight:700;">✅ Tu primer paso: reclamá un lead básico gratis</p>
            <p style="margin:0;color:#a1a1aa;line-height:1.6;font-size:.9rem;">
              En la Bolsa de Leads vas a encontrar contactos disponibles para trabajar — sin gastar nada.
              Los leads básicos son ideales para practicar el pitch y hacer tu primera venta.
            </p>
          </div>

          <p style="color:#a1a1aa;margin-bottom:8px;font-size:.9rem;font-weight:700;">Tu link de ventas (para clientes):</p>
          <p style="margin-bottom:28px;font-size:.9rem;"><a href="{PORTAL_URL}/p/{a.ref_code}" style="color:#3b82f6;">{PORTAL_URL}/p/{a.ref_code}</a></p>

          <a href="https://avanza-digital-production.up.railway.app/portal.html#bolsa" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;">Ver leads disponibles →</a>

          <p style="margin-top:32px;font-size:.8rem;color:#71717a;">Avanza Digital · Partner Network · Santa Fe, Argentina</p>
        </div>
        """
    )

    # Notificar al admin — EN SEGUNDO PLANO
    background_tasks.add_task(
        enviar_email,
        ADMIN_EMAIL,
        f"[NUEVO ALIADO] {a.nombre} — {a.codigo}",
        f"<p>Nuevo aliado auto-registrado:<br><strong>{a.nombre}</strong> — {a.email} — {a.whatsapp}<br>Perfil: {a.perfil or '—'} | Ciudad: {a.ciudad or '—'}<br>Código: {a.codigo} | Ref: {a.ref_code}</p>"
    )

    return _aliado_detalle(a, incluir_token=True)


# ─── ADMIN SETUP / LOGIN ─────────────────────────────────────────────────────

@app.post("/admin/setup")
def crear_admin_inicial(
    body: schemas.AdminSetupIn | None = Body(default=None),
    username: str = "", password: str = "",
    db: Session = Depends(get_db),
):
    """Crea el primer admin. Solo funciona si no existe ninguno.

    Protegido por el middleware admin (requiere X-API-Key o JWT admin) — pero
    pensado para el bootstrap inicial cuando no existe admin todavía.
    """
    if body is not None:
        username, password = body.username, body.password
    if not username or not password:
        raise HTTPException(400, "Faltan username y password.")
    if len(password) < 8:
        raise HTTPException(400, "La contraseña de admin debe tener al menos 8 caracteres.")
    if db.query(Admin).count() > 0:
        raise HTTPException(400, "Ya existe al menos un admin.")
    db.add(Admin(username=username, password_hash=hash_password(password)))
    db.commit()
    return {"mensaje": f"Admin '{username}' creado correctamente."}


@app.post("/admin/login")
@limiter.limit("10/minute")
def login_admin(request: Request, 
    body: schemas.AdminLoginIn | None = Body(default=None),
    username: str = "", password: str = "",
    db: Session = Depends(get_db),
):
    """Login de admin con username + password. Devuelve JWT tipo='admin'.

    Acepta body JSON (preferido) o query (legacy). Si no hay admins creados
    todavía, devuelve 503 con instrucciones (usar /admin/setup primero).
    """
    if body is not None:
        username, password = body.username, body.password

    if db.query(Admin).count() == 0:
        raise HTTPException(503, "No hay admins creados. Usar POST /admin/setup primero.")

    if not username or not password:
        raise HTTPException(400, "Faltan username y password.")

    admin = db.query(Admin).filter(Admin.username == username).first()
    # Comparación constant-time del password — siempre corre verify para no leakear si existe el user
    fake_hash = hash_password("dummy_password_for_timing")
    target_hash = admin.password_hash if admin else fake_hash
    ok = verify_password(password, target_hash)
    if not admin or not ok:
        raise HTTPException(401, "Credenciales inválidas.")

    token = crear_token(sub=admin.username, tipo="admin")
    return {"token": token, "tipo": "admin", "username": admin.username}


# ─── LOGIN ALIADO ─────────────────────────────────────────────────────────────

@app.post("/aliados/login")
@limiter.limit("10/minute")
def login_aliado(request: Request, 
    body: schemas.LoginAliadoIn | None = Body(default=None),
    codigo: str = "", password: str = "",
    db: Session = Depends(get_db),
):
    """Portal del aliado: login con código o email + contraseña.

    Acepta body JSON (preferido) o query string (legacy, queda en logs).
    Devuelve los datos del aliado + un JWT en el campo `token` para usar en
    `Authorization: Bearer ...` en requests subsiguientes.
    """
    if body is not None:
        codigo, password = body.codigo, body.password
    else:
        print("[LOGIN] ⚠️  Recibido por query string — migrar cliente a body JSON.")

    if not codigo or not password:
        raise HTTPException(400, "Faltan codigo y password.")

    # Buscar aliado activo por email o por código
    if "@" in codigo:
        a = db.query(Aliado).filter(Aliado.email == codigo.lower().strip(), Aliado.activo == True).first()
    else:
        a = db.query(Aliado).filter(Aliado.codigo == codigo, Aliado.activo == True).first()
    # Comparación constant-time del password (corre verify aunque no exista el aliado)
    fake_hash = hash_password("dummy_password_for_timing")
    target_hash = a.password_hash if a else fake_hash
    ok = verify_password(password, target_hash)
    if not a or not ok:
        # Mismo mensaje para no leakear si el código existe o no
        raise HTTPException(401, "Código o contraseña incorrectos.")

    # TRACKING (no bloquea login si falla)
    try:
        a.ultimo_login = datetime.now()
        a.cantidad_logins = (getattr(a, 'cantidad_logins', 0) or 0) + 1
        # Resetear flags de inactividad: si el aliado vuelve a quedar inactivo
        # en el futuro, el ciclo de 20d/30d arranca limpio desde este login.
        a.notif_inact_20d_en = None
        a.notif_inact_30d_en = None
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error guardando tracking de login: {e}")

    return _aliado_detalle(a, incluir_token=True)


# ─── REFRESH TOKEN ───────────────────────────────────────────────────────────
@app.post("/auth/refresh")
@limiter.limit("30/minute")
def refresh_token(request: Request, db: Session = Depends(get_db)):
    """Renueva un JWT que está por vencer (o vencido hace poco).

    Acepta el token en el header `Authorization: Bearer ...` aunque ya esté
    expirado, mientras la firma sea válida y no haya pasado más de
    JWT_REFRESH_WINDOW_HOURS desde el `iat`. Devuelve un nuevo token con la
    misma identidad y tipo.

    El front llama a este endpoint cuando recibe un 401 con detalle de token
    expirado, para no obligar al aliado a reloguearse. Si el refresh también
    falla → 401 y el front muestra el modal de login.
    """
    from jose import JWTError as _JWTError
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(401, "Falta token Bearer en header Authorization.")
    token = parts[1]

    try:
        payload = decodificar_token_ignorando_exp(token)
    except _JWTError:
        raise HTTPException(401, "Token con firma inválida — no se puede refrescar.")

    tipo = payload.get("tipo")
    sub  = payload.get("sub")
    iat  = payload.get("iat")
    if not tipo or not sub or not iat:
        raise HTTPException(401, "Token incompleto.")
    if tipo not in ("aliado", "admin"):
        raise HTTPException(401, "Tipo de token desconocido.")

    # iat puede venir como int (unix epoch) o como datetime serializado
    try:
        iat_dt = datetime.fromtimestamp(iat, tz=timezone.utc) if isinstance(iat, (int, float)) else datetime.fromisoformat(str(iat))
    except Exception:
        raise HTTPException(401, "Token con iat inválido.")
    edad = datetime.now(timezone.utc) - iat_dt
    if edad > timedelta(hours=JWT_REFRESH_WINDOW_HOURS):
        raise HTTPException(401, "El token está fuera de la ventana de refresh — reloguearse.")

    # Validar que el sujeto siga siendo válido en la DB.
    if tipo == "aliado":
        a = db.query(Aliado).filter(Aliado.codigo == sub, Aliado.activo == True).first()
        if not a:
            raise HTTPException(401, "Aliado del token no encontrado o inactivo.")
    else:  # admin
        adm = db.query(Admin).filter(Admin.username == sub).first()
        if not adm:
            raise HTTPException(401, "Admin del token no encontrado.")

    nuevo = crear_token(sub=sub, tipo=tipo)
    return {"token": nuevo, "tipo": tipo}


# ─── ALIADOS — RUTAS FIJAS (deben ir ANTES de /{codigo}) ─────────────────────

@app.get("/aliados/suspendidos")
def listar_suspendidos(db: Session = Depends(get_db)):
    return [_aliado_row(a) for a in db.query(Aliado).filter(Aliado.activo == False).all()]


@app.get("/aliados/inactivos")
def aliados_inactivos(dias: int = 30, db: Session = Depends(get_db)):
    """Aliados sin actividad en los últimos N días. Para sistema de reactivación."""
    corte = datetime.now() - timedelta(days=dias)
    resultado = []
    for a in db.query(Aliado).filter(Aliado.activo == True).all():
        ref_rec = any(r.registrado_en >= corte for r in a.referidos)
        vta_rec = any(v.fecha_venta and v.fecha_venta >= corte for v in a.ventas if v.confirmada)
        if not ref_rec and not vta_rec:
            fechas = ([r.registrado_en for r in a.referidos] +
                      [v.fecha_venta for v in a.ventas if v.confirmada and v.fecha_venta])
            ultimo = max(fechas) if fechas else None
            resultado.append({
                "codigo": a.codigo, "nombre": a.nombre,
                "whatsapp": a.whatsapp, "email": a.email,
                "ciudad": a.ciudad, "perfil": a.perfil,
                "nivel": a.nivel_calculado,
                "dias_inactivo": (datetime.now() - ultimo).days if ultimo else None,
                "total_ganado": round(a.total_ganado, 2),
                "ventas_totales": len([v for v in a.ventas if v.confirmada]),
                "fecha_firma": a.fecha_firma,
            })
    resultado.sort(key=lambda x: x["dias_inactivo"] or 9999, reverse=True)
    return {"filtro_dias": dias, "total": len(resultado), "aliados": resultado}


@app.post("/admin/notificar-inactivos")
def notificar_inactivos_manual(background_tasks: BackgroundTasks):
    """Dispara manualmente el job de notificaciones de inactividad (para pruebas desde admin)."""
    background_tasks.add_task(job_notificaciones_inactividad)
    return {"ok": True, "mensaje": "Job de inactividad lanzado en background. Revisá los logs."}


@app.get("/aliados")
def listar_aliados(db: Session = Depends(get_db)):
    return [_aliado_row(a) for a in db.query(Aliado).filter(Aliado.activo == True).all()]


@app.post("/aliados/crear")
def crear_aliado(body: schemas.CrearAliadoIn | None = Body(default=None),
                 nombre: str = "", email: str = "", whatsapp: str = "", ciudad: str = "",
                 dni: str = "", perfil: str = "", fecha_firma: str = "",
                 password: str = "avanza2026", db: Session = Depends(get_db)):
    """Admin crea un aliado manualmente. Acepta body JSON (preferido) o query (legacy)."""
    if body is not None:
        nombre, email, whatsapp, ciudad = body.nombre, body.email, body.whatsapp, body.ciudad
        dni, perfil, fecha_firma = body.dni, body.perfil, body.fecha_firma
        if body.password:
            password = body.password
    if not nombre or not email or not whatsapp or not ciudad:
        raise HTTPException(400, "Faltan nombre, email, whatsapp o ciudad.")
    if db.query(Aliado).filter(Aliado.email == email).first():
        raise HTTPException(400, "Ya existe un aliado con ese email.")
    a = Aliado(
        codigo=generar_codigo_aliado(db), nombre=nombre, email=email,
        dni=dni, whatsapp=whatsapp, ciudad=ciudad, perfil=perfil,
        fecha_firma=fecha_firma or datetime.now().strftime("%d/%m/%Y"),
        ref_code=generar_ref_code(nombre), password_hash=hash_password(password),
    )
    db.add(a); db.commit(); db.refresh(a)
    return {
        "mensaje": f"Aliado {a.codigo} creado", "codigo": a.codigo,
        "ref_code": a.ref_code, "password_inicial": password,
        "ref_code": a.ref_code,
        "link_ref": f"{PORTAL_URL}/p/{a.ref_code}",
        "link_perfil": f"{PORTAL_URL}/p/{a.ref_code}",
    }


# ─── ALIADOS — RUTAS CON {codigo} ────────────────────────────────────────────

@app.get("/aliados/me")
def aliado_me(aliado: Aliado = Depends(current_aliado_required), db: Session = Depends(get_db)):
    """Devuelve los datos del aliado autenticado via JWT. Usado para auto-login."""
    return _aliado_detalle(aliado)


@app.get("/aliados/{codigo}")
def ver_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a: raise HTTPException(404, "Aliado no encontrado.")
    return _aliado_detalle(a)


@app.post("/aliados/{codigo}/suspender")
def suspender_aliado(codigo: str, db: Session = Depends(get_db)):
    a = _get_aliado(codigo, db)
    a.activo = False; db.commit()
    return {"mensaje": f"{a.nombre} suspendido."}


@app.post("/aliados/{codigo}/activar")
def activar_aliado(codigo: str, db: Session = Depends(get_db)):
    a = _get_aliado(codigo, db)
    a.activo = True; db.commit()
    return {"mensaje": f"{a.nombre} reactivado."}


@app.delete("/aliados/{codigo}/eliminar")
def eliminar_aliado(codigo: str, db: Session = Depends(get_db)):
    """
    Borra un aliado de forma permanente, limpiando primero TODAS las tablas
    con FK hacia aliados.id.

    Cada paso usa un savepoint independiente: si alguna tabla todavía no existe
    en la BD de producción (ProgrammingError / OperationalError), ese paso se
    descarta silenciosamente y el resto continúa — evitando que un schema
    desactualizado aborte toda la transacción (que era el ProgrammingError que
    se veía en pantalla).
    """
    a = _get_aliado(codigo, db)
    aid = a.id

    def _sp(fn):
        """Ejecuta fn() en un savepoint; si falla por tabla/columna inexistente lo ignora."""
        sp = db.begin_nested()
        try:
            fn()
            sp.commit()
        except (ProgrammingError, OperationalError) as e:
            sp.rollback()
            print(f"[eliminar_aliado] SKIP (tabla/col faltante) — {type(e).__name__}: {e}", file=sys.stderr)

    try:
        # 1) Obtener IDs auxiliares dentro de savepoints (por si las tablas no existen aún)
        prospecto_ids: list = []
        post_ids: list = []

        def _get_aux():
            nonlocal prospecto_ids, post_ids
            prospecto_ids = [r[0] for r in db.query(Prospecto.id).filter(Prospecto.aliado_id == aid).all()]
            post_ids      = [r[0] for r in db.query(PostComunidad.id).filter(PostComunidad.aliado_id == aid).all()]
        _sp(_get_aux)

        # 2) Comentarios de comunidad (hijos de posts + propios del aliado)
        if post_ids:
            _sp(lambda: db.query(ComentarioComunidad)
                .filter(ComentarioComunidad.post_id.in_(post_ids))
                .delete(synchronize_session=False))
        _sp(lambda: db.query(ComentarioComunidad)
            .filter(ComentarioComunidad.aliado_id == aid)
            .delete(synchronize_session=False))

        # 3) Posts del aliado
        _sp(lambda: db.query(PostComunidad)
            .filter(PostComunidad.aliado_id == aid)
            .delete(synchronize_session=False))

        # 4) Comisiones (depende de LinkPago y Prospecto → va antes)
        _sp(lambda: db.query(Comision)
            .filter(Comision.aliado_id == aid)
            .delete(synchronize_session=False))
        if prospecto_ids:
            _sp(lambda: db.query(Comision)
                .filter(Comision.prospecto_id.in_(prospecto_ids))
                .delete(synchronize_session=False))

        # 5) Links de pago
        _sp(lambda: db.query(LinkPago)
            .filter(LinkPago.aliado_id == aid)
            .delete(synchronize_session=False))
        if prospecto_ids:
            _sp(lambda: db.query(LinkPago)
                .filter(LinkPago.prospecto_id.in_(prospecto_ids))
                .delete(synchronize_session=False))

        # 6) Logs de automatización
        _sp(lambda: db.query(AutomationLog)
            .filter(AutomationLog.aliado_id == aid)
            .delete(synchronize_session=False))
        if prospecto_ids:
            _sp(lambda: db.query(AutomationLog)
                .filter(AutomationLog.prospecto_id.in_(prospecto_ids))
                .delete(synchronize_session=False))

        # 7) Ventas
        _sp(lambda: db.query(Venta)
            .filter(Venta.aliado_id == aid)
            .delete(synchronize_session=False))

        # 8) Referidos
        _sp(lambda: db.query(Referido)
            .filter(Referido.aliado_id == aid)
            .delete(synchronize_session=False))

        # 9) Prospectos
        _sp(lambda: db.query(Prospecto)
            .filter(Prospecto.aliado_id == aid)
            .delete(synchronize_session=False))

        # 10) Transacciones de créditos
        _sp(lambda: db.query(TransaccionCredito)
            .filter(TransaccionCredito.aliado_id == aid)
            .delete(synchronize_session=False))

        # 11) Auditoría: preservar con aliado_id = NULL
        _sp(lambda: db.query(AuditoriaLog)
            .filter(AuditoriaLog.aliado_id == aid)
            .update({AuditoriaLog.aliado_id: None}, synchronize_session=False))

        # 12) Bolsa de leads: liberar → vuelven a estar disponibles
        _sp(lambda: db.query(LeadBolsa)
            .filter(LeadBolsa.aliado_id == aid)
            .update({
                LeadBolsa.aliado_id: None,
                LeadBolsa.estado: "disponible",
                LeadBolsa.fecha_reclamo: None,
            }, synchronize_session=False))

        # 13) Sub-aliados: solo desvincular sponsor, no borrar
        _sp(lambda: db.query(Aliado)
            .filter(Aliado.sponsor_id == aid)
            .update({Aliado.sponsor_id: None}, synchronize_session=False))

        # 14) Por fin: el aliado mismo
        db.delete(a)
        db.commit()
        return {"mensaje": f"{codigo} eliminado permanentemente."}

    except Exception as e:
        db.rollback()
        print(f"[eliminar_aliado] ERROR borrando {codigo}: {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo eliminar al aliado: {type(e).__name__}: {str(e)[:200]}"
        )


@app.patch("/aliados/{codigo}/nivel")
def cambiar_nivel(codigo: str,
                  body: schemas.CambiarNivelIn | None = Body(default=None),
                  nivel: str = "",
                  db: Session = Depends(get_db)):
    """Admin cambia el nivel de un aliado. (Protegido por middleware admin.)"""
    if body is not None:
        nivel = body.nivel
    if nivel not in NIVELES:
        raise HTTPException(400, f"Nivel inválido. Opciones: {list(NIVELES.keys())}")
    a = _get_aliado(codigo, db)
    anterior = a.nivel; a.nivel = nivel; db.commit()
    return {"mensaje": f"{a.nombre}: {anterior} → {nivel}", "comision": f"{NIVELES[nivel]['comision']*100:.0f}%"}


@app.get("/aliados/{codigo}/red")
def mi_red_comercial(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "Mi Red no está disponible para aliados Canal 2.")
    red = []
    total_pasivo = 0

    sub_aliados = getattr(a, "sub_aliados", [])
    for sub in sub_aliados:
        # Calcular cuánta plata generó este sub-aliado
        ventas_red = [v.comision_usd for v in a.ventas if f"RED: {sub.nombre}" in v.nombre_cliente]
        ganancia = sum(ventas_red)
        total_pasivo += ganancia
        
        fecha_ing = "Reciente"
        if getattr(sub, "creado_en", None):
            fecha_ing = sub.creado_en.strftime("%d/%m/%Y")
        elif getattr(sub, "fecha_firma", None):
            fecha_ing = sub.fecha_firma

        red.append({
            "nombre": sub.nombre,
            "ciudad": sub.ciudad or "Sin especificar",
            "nivel": sub.nivel_calculado,
            "fecha_ingreso": fecha_ing,
            "ganancia_pasiva": round(ganancia, 2)
        })
    
    red.sort(key=lambda x: x["ganancia_pasiva"], reverse=True)

    return {
        "sponsor": getattr(a, "sponsor").nombre if getattr(a, "sponsor", None) else None,
        "total_sub_aliados": len(red),
        "total_ganancia_pasiva": round(total_pasivo, 2),
        "detalle": red
    }


# ─── REFERIDOS ───────────────────────────────────────────────────────────────

@app.post("/referidos/registrar")
@limiter.limit("30/hour")
def registrar_referido(request: Request, body: schemas.RegistrarReferidoIn | None = Body(default=None),
                        ref_code: str = "", nombre_cliente: str = "", plan_elegido: str = "",
                        notas: str = "",
                        db: Session = Depends(get_db)):
    """Registra un referido público (NO requiere auth — el ref_code identifica
    al aliado). Acepta body JSON (preferido) o query (legacy)."""
    if body is not None:
        ref_code = body.ref_code
        nombre_cliente = body.nombre_cliente
        plan_elegido = body.plan_elegido
        notas = body.notas
    if not ref_code or not nombre_cliente or not plan_elegido:
        raise HTTPException(400, "Faltan ref_code, nombre_cliente o plan_elegido.")
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not a: raise HTTPException(404, "Código de referido inválido.")
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "Referidos no disponibles para aliados Canal 2.")
    if plan_elegido not in PLANES: raise HTTPException(400, f"Plan inválido.")
    r = Referido(aliado_id=a.id, nombre_cliente=nombre_cliente, plan_elegido=plan_elegido, notas=notas)
    db.add(r); db.commit(); db.refresh(r)
    return {
        "mensaje": "Referido registrado.", "id_referido": r.id,
        "aliado": a.nombre, "cliente": nombre_cliente, "plan": plan_elegido,
        "valor_plan": PLANES[plan_elegido],
        "comision_estimada": round(PLANES[plan_elegido] * a.comision_pct, 2),
        "registrado_en": r.registrado_en.strftime("%d/%m/%Y %H:%M"),
    }


@app.get("/referidos/pendientes")
def referidos_pendientes(db: Session = Depends(get_db)):
    return [
        {"id": r.id, "aliado": r.aliado.nombre, "aliado_codigo": r.aliado.codigo,
         "cliente": r.nombre_cliente, "plan": r.plan_elegido,
         "registrado_en": r.registrado_en.strftime("%d/%m/%Y %H:%M")}
        for r in db.query(Referido).filter(Referido.acuse_recibo == False).all()
    ]


@app.post("/referidos/{id}/confirmar")
def confirmar_referido(id: int, db: Session = Depends(get_db)):
    """Admin confirma manualmente un referido. (Protegido por middleware admin.)"""
    r = db.query(Referido).filter(Referido.id == id).first()
    if not r: raise HTTPException(404, "Referido no encontrado.")
    r.acuse_recibo = True; db.commit()
    return {"mensaje": f"Referido de '{r.nombre_cliente}' confirmado."}


# ─── VENTAS CON COMISIONES RED ───────────────────────────────────────────────

@app.post("/ventas/registrar")
def registrar_venta(body: schemas.RegistrarVentaIn | None = Body(default=None),
                    codigo_aliado: str = "", nombre_cliente: str = "", plan: str = "",
                    modalidad_pago: str = "ARS MEP", referido_id: int = None,
                    notas: str = "",
                    db: Session = Depends(get_db)):
    """Admin registra una venta manualmente. (Protegido por middleware admin.)"""
    if body is not None:
        codigo_aliado = body.codigo_aliado
        nombre_cliente = body.nombre_cliente
        plan = body.plan
        modalidad_pago = body.modalidad_pago
        referido_id = body.referido_id
        notas = body.notas
    if not codigo_aliado or not nombre_cliente or not plan:
        raise HTTPException(400, "Faltan codigo_aliado, nombre_cliente o plan.")
    a = _get_aliado(codigo_aliado, db)
    if plan not in PLANES: raise HTTPException(400, "Plan inválido.")
    valor = PLANES[plan]
    comision_usd = round(valor * a.comision_pct, 2)

    # Detectar si es la primera venta confirmada del aliado ANTES de agregar
    # la nueva, así no contamos a `v` a sí mismo.
    es_primera_venta = db.query(Venta).filter(
        Venta.aliado_id == a.id,
        Venta.confirmada == True,
    ).count() == 0

    # 1. Registrar Venta del Aliado que cerró
    v = Venta(aliado_id=a.id, referido_id=referido_id, nombre_cliente=nombre_cliente,
              plan=plan, valor_usd=valor, comision_pct=a.comision_pct,
              comision_usd=comision_usd, confirmada=True, pagada=False,
              fecha_venta=datetime.now(), modalidad_pago=modalidad_pago, notas=notas)
    db.add(v)
    db.flush()  # asignar v.id para usarlo en la referencia del bonus

    # 2. EFECTO RED: Si tiene Sponsor, le damos un 5% pasivo al Sponsor
    if getattr(a, "sponsor", None):
        comision_sponsor = round(valor * 0.05, 2) # Fijo 5% de Regalía
        v_red = Venta(
            aliado_id=a.sponsor.id, 
            referido_id=None,
            nombre_cliente=f"♻️ RED: {a.nombre} (Venta: {nombre_cliente})",
            plan=plan, 
            valor_usd=valor, 
            comision_pct=0.05,
            comision_usd=comision_sponsor, 
            confirmada=True, pagada=False,
            fecha_venta=datetime.now(), modalidad_pago=modalidad_pago, 
            notas=f"Ingreso pasivo por venta de tu sub-aliado {a.nombre}"
        )
        db.add(v_red)
        a.sponsor.nivel = a.sponsor.nivel_calculado

    if referido_id:
        ref = db.query(Referido).filter(Referido.id == referido_id).first()
        if ref: ref.convertido = True

    # 3. BONUS PRIMERA VENTA — créditos al aliado y al sponsor (si tiene).
    # Refuerza el loop "cerré → tengo más ammo para volver a cerrar".
    bonus_info = None
    if es_primera_venta:
        bonus_info = _aplicar_bonus_primera_venta(db, a, v.id)
    
    a.nivel = a.nivel_calculado
    db.commit()
    return {
        "mensaje": "Venta registrada.",
        "aliado": a.nombre,
        "nivel_nuevo": a.nivel_calculado,
        "valor_usd": valor,
        "comision_usd": comision_usd,
        "primera_venta": es_primera_venta,
        "bonus_creditos": bonus_info,
    }


@app.post("/ventas/{id}/pagar")
def marcar_pagada(id: int,
                  body: schemas.MarcarPagadaIn | None = Body(default=None),
                  modalidad: str = "ARS MEP",
                  db: Session = Depends(get_db)):
    """Admin marca una venta como pagada. (Protegido por middleware admin.)"""
    if body is not None:
        modalidad = body.modalidad
    v = db.query(Venta).filter(Venta.id == id).first()
    if not v: raise HTTPException(404, "Venta no encontrada.")
    v.pagada = True; v.fecha_pago = datetime.now(); v.modalidad_pago = modalidad
    db.commit()
    return {"mensaje": f"USD {v.comision_usd} pagados a {v.aliado.nombre}."}


# ─── DASHBOARD + LEADERBOARD ─────────────────────────────────────────────────

@app.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    ventas  = db.query(Venta).filter(Venta.confirmada == True).all()
    niveles = {"BASIC": 0, "SILVER": 0, "PREMIUM": 0, "ELITE": 0}
    for a in aliados: niveles[a.nivel_calculado] = niveles.get(a.nivel_calculado, 0) + 1
    leaderboard = sorted(
        [{"codigo": a.codigo, "nombre": a.nombre, "nivel": a.nivel_calculado,
          "ventas_6m": a.ventas_6_meses, "total_ganado": round(a.total_ganado, 2)}
         for a in aliados],
        key=lambda x: x["ventas_6m"], reverse=True
    )[:10]
    return {
        "total_aliados": len(aliados),
        "total_ventas": len(ventas),
        "total_vendido_usd": round(sum(v.valor_usd for v in ventas), 2),
        "total_comisiones_usd": round(sum(v.comision_usd for v in ventas), 2),
        "pendiente_pagar_usd": round(sum(v.comision_usd for v in ventas if not v.pagada), 2),
        "distribucion_niveles": niveles,
        "referidos_sin_confirmar": db.query(Referido).filter(Referido.acuse_recibo == False).count(),
        "leaderboard": leaderboard,
    }



# ─── PROSPECTOS ──────────────────────────────────────────────────────────────

@app.post("/prospectos/crear")
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


@app.get("/prospectos/aliado/{codigo}")
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


@app.patch("/prospectos/{id}/contactar")
def marcar_contactado(id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.estado = "contactado"
    p.fecha_contacto = datetime.now()
    db.commit()
    return {"mensaje": "Marcado como contactado.", "estado": p.estado}


@app.patch("/prospectos/{id}/respondio")
def marcar_respondio(id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.estado = "respondio"
    p.fecha_respuesta = datetime.now()
    if not p.fecha_contacto:
        p.fecha_contacto = datetime.now()
    db.commit()
    return {"mensaje": "Marcado como respondió.", "estado": p.estado}


@app.patch("/prospectos/{id}/propuesta-enviada")
def marcar_propuesta_enviada(id: int, request: Request, db: Session = Depends(get_db)):
    """Marca manualmente un prospecto como 'propuesta_enviada' (spec §8)."""
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.estado = "propuesta_enviada"
    if not p.fecha_contacto:
        p.fecha_contacto = datetime.now()
    db.commit()
    return {"mensaje": "Marcado como propuesta enviada.", "estado": p.estado}


@app.patch("/prospectos/{id}/estado")
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


@app.patch("/prospectos/{id}/nota")
def actualizar_nota(id: int, request: Request,
                    body: schemas.ActualizarNotaIn | None = Body(default=None),
                    nota: str = "",
                    db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.nota = body.nota if body is not None else nota
    db.commit()
    return {"mensaje": "Nota guardada."}


@app.patch("/prospectos/{id}/interesante")
def toggle_interesante(id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    p.interesante = not p.interesante; db.commit()
    return {"interesante": p.interesante}


@app.delete("/prospectos/{id}/eliminar")
def eliminar_prospecto(id: int, request: Request, db: Session = Depends(get_db)):
    p = _get_prospecto_owned_or_admin(id, request, db)
    db.delete(p); db.commit()
    return {"mensaje": "Prospecto eliminado."}


@app.patch("/prospectos/{id}/piloto")
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


@app.get("/admin/prospectos")
def admin_prospectos(db: Session = Depends(get_db)):
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


# ─── AUDITORÍAS ──────────────────────────────────────────────────────────────

@app.post("/auditorias/log")
@limiter.limit("60/hour")
def log_auditoria(request: Request, dominio: str, score: int, ref_code: str = "", email: str = "", db: Session = Depends(get_db)):
    """Guarda el log cuando se genera un reporte o se captura un email."""
    aliado_id = None
    if ref_code:
        a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
        if a:
            aliado_id = a.id
    
    log = AuditoriaLog(aliado_id=aliado_id, ref_code=ref_code, dominio=dominio, score=score, email_capturado=email)
    db.add(log)
    db.commit()
    return {"status": "ok"}


@app.get("/admin/auditorias")
def admin_auditorias(db: Session = Depends(get_db)):
    """Métricas de uso de la herramienta para el admin."""
    logs = db.query(AuditoriaLog).all()
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    
    usos_por_aliado = {}
    for log in logs:
        if log.aliado_id:
            if log.aliado_id not in usos_por_aliado:
                usos_por_aliado[log.aliado_id] = []
            usos_por_aliado[log.aliado_id].append({
                "dominio": log.dominio,
                "score": log.score,
                "email": log.email_capturado,
                "fecha": log.creado_en.strftime("%d/%m/%Y")
            })

    resumen_aliados = []
    for a in aliados:
        historial = usos_por_aliado.get(a.id, [])
        resumen_aliados.append({
            "codigo": a.codigo,
            "nombre": a.nombre,
            "usos_totales": len(historial),
            "ultimo_uso": historial[-1]["fecha"] if historial else None,
            "historial": historial
        })
    
    return {
        "total_auditorias": len(logs),
        "aliados_activos_uso": len([a for a in resumen_aliados if a["usos_totales"] > 0]),
        "aliados_sin_uso": len([a for a in resumen_aliados if a["usos_totales"] == 0]),
        "detalle": sorted(resumen_aliados, key=lambda x: x["usos_totales"], reverse=True)
    }


# ─── HELPERS PRIVADOS ────────────────────────────────────────────────────────

def _get_aliado(codigo, db):
    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a: raise HTTPException(404, "Aliado no encontrado.")
    return a

def _get_prospecto(id, db):
    p = db.query(Prospecto).filter(Prospecto.id == id).first()
    if not p: raise HTTPException(404, "Prospecto no encontrado.")
    return p

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
    }

def _aliado_row(a):
    return {
        "codigo": a.codigo, "nombre": a.nombre, "email": a.email,
        "whatsapp": a.whatsapp, "ciudad": a.ciudad, "perfil": a.perfil,
        "nivel": a.nivel_calculado, "ventas_6m": a.ventas_6_meses,
        "total_ganado": round(a.total_ganado, 2),
        "total_pendiente": round(a.total_pendiente, 2),
        "ref_code": a.ref_code, "fecha_firma": a.fecha_firma,
        "ultimo_login": a.ultimo_login.strftime("%d/%m/%Y %H:%M") if getattr(a, "ultimo_login", None) else "Nunca",
        "cantidad_logins": getattr(a, "cantidad_logins", 0),
        "cbu_alias": getattr(a, "cbu_alias", None),
        "terminos_aceptados": bool(getattr(a, "terminos_aceptados", False)),
        "terminos_aceptados_en": a.terminos_aceptados_en.strftime("%d/%m/%Y %H:%M") if getattr(a, "terminos_aceptados_en", None) else None,
        "tipo_aliado": getattr(a, "tipo_aliado", "canal1") or "canal1",
    }

def _aliado_detalle(a, incluir_token: bool = False):
    # MRR recurrente del aliado (10% sobre planes de continuidad activos).
    # Calculado on-the-fly: cantidad de queries baja, no vale la pena cachear.
    try:
        from sqlalchemy.orm import object_session
        sess = object_session(a)
        if sess is not None:
            activos_mrr = sess.query(PlanContinuidadActivo).filter(
                PlanContinuidadActivo.aliado_id == a.id,
                PlanContinuidadActivo.fecha_baja.is_(None),
            ).all()
            mrr_recurrente = round(sum(p.comision_mensual_usd for p in activos_mrr), 2)
        else:
            mrr_recurrente = 0.0
    except Exception:
        mrr_recurrente = 0.0

    out = {
        "codigo": a.codigo, "nombre": a.nombre, "email": a.email,
        "whatsapp": a.whatsapp, "ciudad": a.ciudad, "perfil": a.perfil,
        "nivel_actual": a.nivel, "nivel_calculado": a.nivel_calculado,
        "comision_pct": a.comision_pct * 100,
        "ventas_6m": a.ventas_6_meses, "total_ventas": len(a.ventas),
        "total_ganado": round(a.total_ganado, 2),
        "total_pendiente": round(a.total_pendiente, 2),
        "mrr_recurrente_usd": mrr_recurrente,
        "ref_code": a.ref_code,
        "link_ref":    f"{PORTAL_URL}/p/{a.ref_code}",
        "link_perfil": f"{PORTAL_URL}/p/{a.ref_code}",
        # Flag para el frontend: si es False, el aliado todavía puede
        # personalizar su slug una vez (botón "Personalizá tu link" visible).
        "username_personalizado": bool(getattr(a, "username_personalizado_en", None)),
        "portal_publico_activo": bool(getattr(a, "portal_publico_activo", True)),
        "tipo_aliado": getattr(a, "tipo_aliado", "canal1") or "canal1",
        "cbu_alias": getattr(a, "cbu_alias", None),
        "terminos_aceptados": bool(getattr(a, "terminos_aceptados", False)),
        "terminos_aceptados_en": a.terminos_aceptados_en.strftime("%d/%m/%Y %H:%M") if getattr(a, "terminos_aceptados_en", None) else None,
        "referidos": [{"cliente": r.nombre_cliente, "plan": r.plan_elegido,
                       "fecha": r.registrado_en.strftime("%d/%m/%Y"),
                       "confirmado": r.acuse_recibo, "convertido": r.convertido}
                      for r in a.referidos],
        "ventas": [{"cliente": v.nombre_cliente, "plan": v.plan,
                    "valor": v.valor_usd, "comision": v.comision_usd,
                    "pagada": v.pagada,
                    "fecha": v.fecha_venta.strftime("%d/%m/%Y") if v.fecha_venta else None}
                   for v in a.ventas if v.confirmada],
    }
    if incluir_token:
        out["token"] = crear_token(sub=a.codigo, tipo="aliado")
        out["token_tipo"] = "Bearer"
    return out

# ─── RANKING PÚBLICO (Gamificación) ──────────────────────────────────────────

@app.get("/leaderboard")
def obtener_leaderboard(db: Session = Depends(get_db)):
    """Ranking inteligente: no solo ventas, sino tasa de cierre, velocidad y ticket promedio."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    lista_reales = []

    for a in aliados:
        partes = a.nombre.split()
        nombre_corto = f"{partes[0]} {partes[1][0]}." if len(partes) > 1 else a.nombre

        ventas_conf = [v for v in a.ventas if v.confirmada]
        total_ventas = len(ventas_conf)
        total_refs   = len(a.referidos)

        tasa_cierre  = round((total_ventas / total_refs) * 100) if total_refs > 0 else 0
        ticket_prom  = round(sum(v.valor_usd for v in ventas_conf) / total_ventas) if total_ventas > 0 else 0

        meses_activos = 1
        if a.fecha_firma:
            try:
                fecha = datetime.strptime(a.fecha_firma, "%d/%m/%Y")
                meses_activos = max(1, (datetime.now() - fecha).days // 30)
            except Exception:
                pass
        velocidad = round(total_ventas / meses_activos, 1)

        lista_reales.append({
            "codigo": a.codigo, "nombre": nombre_corto,
            "nivel": a.nivel_calculado, "ventas_6m": a.ventas_6_meses,
            "total_ganado": round(a.total_ganado, 2),
            "tasa_cierre": tasa_cierre, "ticket_prom": ticket_prom, "velocidad": velocidad,
        })

    lista_ficticios = [
        {"codigo":"AL-991","nombre":"Martín G.","nivel":"ELITE","ventas_6m":12,"total_ganado":5850.0,"tasa_cierre":68,"ticket_prom":3200,"velocidad":2.0},
        {"codigo":"AL-842","nombre":"Sofía L.","nivel":"PREMIUM","ventas_6m":8,"total_ganado":3100.0,"tasa_cierre":55,"ticket_prom":2900,"velocidad":1.3},
        {"codigo":"AL-705","nombre":"Lucas P.","nivel":"PREMIUM","ventas_6m":5,"total_ganado":1950.0,"tasa_cierre":42,"ticket_prom":2400,"velocidad":0.8},
        {"codigo":"AL-613","nombre":"Camila R.","nivel":"SILVER","ventas_6m":3,"total_ganado":870.0,"tasa_cierre":30,"ticket_prom":1800,"velocidad":0.5},
    ]

    completo = sorted(lista_reales + lista_ficticios, key=lambda x: x["total_ganado"], reverse=True)
    for i, item in enumerate(completo):
        item["posicion"] = i + 1
    return completo


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
    from tronpy.keys import PrivateKey
    from hdwallet import HDWallet
    from hdwallet.symbols import TRX

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

    if not TRON_MNEMONIC:
        db.rollback()
        raise HTTPException(503, "TRON_MNEMONIC no configurado.")

    try:
        hw = HDWallet(cryptocurrency=TRX)
        hw.from_mnemonic(mnemonic=TRON_MNEMONIC, language="english")
        hw.from_path(f"m/44'/195'/0'/0/{lp.id}")
        privkey_hex  = hw.private_key()
        usdt_address = PrivateKey(bytes.fromhex(privkey_hex)).public_key.to_base58check_address()
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



@app.post("/checkout/crear")
@limiter.limit("20/hour")
async def crear_checkout(request: Request, plan: str,
                         ref_code: str,
                         nombre_cliente: str = "Cliente",
                         cliente_email: str = "",
                         cliente_whatsapp: str = "",
                         moneda: str = "ars",
                         db: Session = Depends(get_db)):
    """Crea un link de pago. `moneda` = 'ars' (MercadoPago) o 'usd' (USDT TRC20).
    Spec §5: ambos flujos generan registros en links_pago con expiración a 48hs."""
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
    if moneda == "usd" and not TRON_MNEMONIC:
        return {
            "checkout_url": f"https://avanzadigital.digital/contratar?plan={plan}&ref={ref_code}",
            "fallback": True,
            "mensaje": "USDT no activado. Configurar TRON_MNEMONIC.",
        }

    if moneda == "ars":
        resultado = await _crear_link_mp(a, plan, nombre_cliente, db)
    else:
        resultado = await _crear_link_usdt(a, plan, nombre_cliente, db)

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


@app.get("/checkout/exitoso")
def checkout_exitoso(ref: str = "", plan: str = "", payment_id: str = "", db: Session = Depends(get_db)):
    """Redirección post-pago de MP (legacy; mantener por compatibilidad con back_urls viejos)."""
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

    a.nivel = a.nivel_calculado
    db.commit()

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
@app.post("/webhooks/mercadopago")
async def webhook_mercadopago(request: Request, db: Session = Depends(get_db)):
    """Recibe notificaciones de MP. Verifica firma HMAC antes de procesar."""
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
@app.post("/checkout/webhook")
async def checkout_webhook_legacy(request: Request, db: Session = Depends(get_db)):
    """Endpoint legacy — redirige internamente al nuevo handler de MP."""
    return await webhook_mercadopago(request, db)


# ─── TIPO DE CAMBIO (público, para el cotizador) ─────────────────────────────
@app.get("/tipo-de-cambio")
async def tipo_de_cambio():
    """Devuelve el tipo de cambio blue actual. El cotizador lo usa para mostrar
    precios en ARS orientativos al aliado."""
    tc = await obtener_tipo_de_cambio()
    return {"moneda": "ARS", "referencia": "blue", "venta": tc,
            "source": DOLARAPI_URL, "fetched_at": datetime.now().isoformat()}


# ─── REGENERAR LINK DE PAGO (spec §4: opción de regenerar tras vencimiento) ──
@app.post("/checkout/regenerar/{link_id}")
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
@app.get("/aliados/{codigo}/links-pago")
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


# ─── SIGUIENTE MEJOR ACCIÓN ───────────────────────────────────────────────────

@app.get("/aliados/{codigo}/siguiente-accion")
def siguiente_accion(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Analiza la situación del aliado y devuelve la acción más urgente e impactante."""
    a = _get_aliado(codigo, db)
    _aplicar_caducidad_bolsa(db)
    acciones = []
    es_canal2 = (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2"

    # 1. Lead caliente: respondió pero no se cotizó
    respondieron = [p for p in a.prospectos if p.estado == "respondio"]
    if respondieron:
        mejor = max(respondieron, key=lambda p: p.fecha_respuesta or p.creado_en)
        acciones.append({
            "tipo": "cerrar_lead_caliente", "urgencia": 5, "icono": "⚡",
            "titulo": f"¡{mejor.nombre} está caliente!",
            "descripcion": "Respondió y está esperando tu propuesta. Usá el Cotizador y enviásela ahora — cada hora enfría el lead.",
            "accion_id": mejor.id, "boton": "Armar propuesta ahora", "tab": "cotizador",
            "color": "green"
        })

    # 2. Propuesta enviada sin respuesta (>= 3 dias)
    propuestas_sin_resp = [
        p for p in a.prospectos
        if p.estado == "propuesta_enviada" and p.fecha_contacto
        and (datetime.now() - p.fecha_contacto).days >= 3
    ]
    if propuestas_sin_resp:
        urgente = max(propuestas_sin_resp, key=lambda p: (datetime.now() - p.fecha_contacto).days)
        dias_esp = (datetime.now() - urgente.fecha_contacto).days
        acciones.append({
            "tipo": "seguimiento_propuesta", "urgencia": 4, "icono": "\U0001f4c4",
            "titulo": f"Seguimiento: {urgente.nombre} tiene tu propuesta",
            "descripcion": f"Enviaste la propuesta hace {dias_esp} días y no hubo respuesta. Un mensaje corto puede desbloquearla: \u2018¿Pudiste revisarla? Cualquier duda te aclaro.\u2019",
            "accion_id": urgente.id, "boton": "Ver Prospecto", "tab": "prospectos",
            "color": "amber"
        })

    # 3. Prospectos sin contactar
    sin_contactar = [p for p in a.prospectos if p.estado == "sin_contactar"]
    if sin_contactar:
        viejo = min(sin_contactar, key=lambda p: p.creado_en)
        dias = (datetime.now() - viejo.creado_en).days
        acciones.append({
            "tipo": "contactar_prospecto", "urgencia": 4, "icono": "🔥",
            "titulo": f"Contactá a {viejo.nombre}",
            "descripcion": f"Lleva {dias} día{'s' if dias != 1 else ''} sin contactar. Enviá el link de Auditoría gratuita para romper el hielo.",
            "accion_id": viejo.id, "boton": "Ir a Prospectos", "tab": "prospectos",
            "color": "amber"
        })

    # 3. Prospectos se enfrían (contactados sin respuesta >3 días)
    frios = [(p, (datetime.now() - p.fecha_contacto).days)
             for p in a.prospectos if p.estado == "contactado" and p.fecha_contacto
             and (datetime.now() - p.fecha_contacto).days >= 3]
    if frios:
        frio, dias_f = max(frios, key=lambda x: x[1])
        acciones.append({
            "tipo": "seguimiento", "urgencia": 3, "icono": "❄️",
            "titulo": f"Seguimiento urgente: {frio.nombre}",
            "descripcion": f"Hace {dias_f} días que no responde. Mandá un mensaje corto: '¿Pudiste ver lo que te envié?' Sin presionar.",
            "accion_id": frio.id, "boton": "Ver Prospectos", "tab": "prospectos",
            "color": "primary"
        })

    # 4. Leads disponibles en bolsa — SOLO Canal 1
    if not es_canal2:
        reclamos_activos = db.query(LeadBolsa).filter(
            LeadBolsa.aliado_id == a.id, LeadBolsa.estado == "reclamado"
        ).count()
        leads_disp = db.query(LeadBolsa).filter(LeadBolsa.estado == "disponible").count()
        if leads_disp > 0 and reclamos_activos < 3:
            acciones.append({
                "tipo": "reclamar_lead", "urgencia": 2, "icono": "🎯",
                "titulo": f"{leads_disp} lead{'s' if leads_disp > 1 else ''} disponible{'s' if leads_disp > 1 else ''} en la bolsa",
                "descripcion": "Hay clientes pre-filtrados esperando. Reclamá uno antes que otro aliado lo tome.",
                "boton": "Ver Bolsa de Leads", "tab": "bolsa",
                "color": "primary"
            })

    # 5. Sin prospectos — acción diferenciada por canal
    if not a.prospectos and a.ventas_6_meses == 0:
        if es_canal2:
            acciones.append({
                "tipo": "primer_prospecto_c2", "urgencia": 1, "icono": "🚀",
                "titulo": "Cargá tu primer cliente hoy",
                "descripcion": "Pensá en 3 clientes de tu cartera que no tienen presencia digital. Entrá al Selector de Rubro, elegí su industria y tenés el pitch listo en 30 segundos.",
                "boton": "Ir al Selector de Rubro", "tab": "selector-rubro",
                "color": "green"
            })
        else:
            acciones.append({
                "tipo": "prospectar", "urgencia": 1, "icono": "🚀",
                "titulo": "Cargá tu primer prospecto hoy",
                "descripcion": "Pensá en 3 empresas de tu entorno que podrían necesitar presencia digital. Agregalas y contactalas con el enlace de Auditoría.",
                "boton": "Agregar Prospecto", "tab": "prospectos",
                "color": "primary"
            })

    acciones.sort(key=lambda x: x["urgencia"], reverse=True)

    # ─── ENRIQUECIMIENTO IA — solo para la acción priorizada ─────────────────
    # Llamamos a Groq SOLO para la acción más urgente. Las otras 3 se quedan
    # con sus textos plantilla. Esto limita el volumen de requests a Groq y
    # mantiene la latencia baja.
    if acciones and acciones[0].get("accion_id"):
        principal = acciones[0]
        prospecto_obj = next(
            (pp for pp in a.prospectos if pp.id == principal["accion_id"]),
            None
        )
        if prospecto_obj is not None:
            # Calculamos `dias_relevantes` según el tipo de acción.
            dias = None
            if principal["tipo"] == "seguimiento_propuesta" and prospecto_obj.fecha_contacto:
                dias = (datetime.now() - prospecto_obj.fecha_contacto).days
            elif principal["tipo"] == "contactar_prospecto":
                dias = (datetime.now() - prospecto_obj.creado_en).days if prospecto_obj.creado_en else None
            elif principal["tipo"] == "seguimiento" and prospecto_obj.fecha_contacto:
                dias = (datetime.now() - prospecto_obj.fecha_contacto).days

            ia_msg = groq_ai.siguiente_accion_ia(
                tipo=principal["tipo"],
                prospecto_nombre=prospecto_obj.nombre,
                prospecto_rubro=prospecto_obj.rubro,
                prospecto_tamano=prospecto_obj.tamano,
                prospecto_urgencia=prospecto_obj.urgencia,
                dias_relevantes=dias,
                ultima_nota=prospecto_obj.nota,
                aliado_nombre=a.nombre,
            )
            if ia_msg:
                # Pisamos la descripción genérica con la personalizada por IA.
                principal["descripcion"] = ia_msg["descripcion"]
                # Y añadimos el mensaje listo para copiar/pegar como campo nuevo.
                principal["mensaje_sugerido"] = ia_msg["mensaje_sugerido"]
                principal["fuente"] = "ia"
            else:
                principal["fuente"] = "plantilla"

    # Stats del aliado para el contexto
    total_prospectos = len(a.prospectos)
    tasa_cierre_pct = 0
    if a.referidos:
        ventas_ok = len([v for v in a.ventas if v.confirmada])
        tasa_cierre_pct = round((ventas_ok / len(a.referidos)) * 100)

    return {
        "siguiente_accion": acciones[0] if acciones else None,
        "todas": acciones[:4],
        "stats": {
            "total_prospectos": total_prospectos,
            "calientes": len(respondieron),
            "sin_contactar": len(sin_contactar),
            "tasa_cierre": tasa_cierre_pct,
        }
    }


# ─── ONBOARDING DEL ALIADO ────────────────────────────────────────────────────

@app.get("/aliados/{codigo}/onboarding")
def estado_onboarding(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Retorna el progreso del checklist de onboarding del aliado."""
    a = _get_aliado(codigo, db)
    es_canal2 = (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2"

    # Orden deliberado: el aliado canal1 debe reclamar un lead básico ANTES de
    # cargar un referido. El lead básico es gratis, de riesgo cero, y da contexto
    # real sobre cómo funciona el programa antes de comprometer un contacto propio.
    pasos = [
        {"id": "registro", "titulo": "Te registraste", "completado": True},
        {"id": "cbu", "titulo": "Cargá tu CBU para cobrar", "completado": bool(getattr(a, "cbu_alias", None))},
    ]
    if not es_canal2:
        pasos.append({
            "id": "bolsa",
            "titulo": "Reclamaste un lead de la bolsa",
            "completado": db.query(LeadBolsa).filter(LeadBolsa.aliado_id == a.id).first() is not None,
        })
    pasos += [
        {"id": "prospecto",     "titulo": "Cargaste un prospecto",
         "completado": len(a.prospectos) > 0},
        {"id": "referido",      "titulo": "Registraste tu 1er referido",
         "completado": len(a.referidos) > 0},
        {"id": "primera_venta", "titulo": "Cerraste tu primera venta",
         "completado": a.ventas_6_meses > 0},
        {"id": "red",           "titulo": "Invitaste a tu primer sub-aliado",
         "completado": len(getattr(a, "sub_aliados", [])) > 0},
    ]
    completados = sum(1 for p in pasos if p["completado"])
    return {"pasos": pasos, "completados": completados, "total": len(pasos),
            "pct": round(completados / len(pasos) * 100)}


# ─── COACH DE ONBOARDING IA (Prioridad #10) ──────────────────────────────────
# Agregado al checklist estático: un consejo IA personalizado según la actividad
# real del aliado (no solo qué pasos tildó). Devuelve diagnóstico + siguiente
# paso + razón + plantilla opcional.

@app.get("/aliados/{codigo}/coach-onboarding")
def coach_onboarding(codigo: str, db: Session = Depends(get_db),
                     _owner=Depends(verify_ownership_dep)):
    """
    Devuelve un diagnóstico IA + siguiente paso accionable basado en el estado
    real del aliado (no solo el checklist).
    """
    a = _get_aliado(codigo, db)
    es_canal2 = (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2"

    # ─── Recolectar datos de actividad real ─────────────────────────────────
    ahora = datetime.now()
    dias_desde_registro = (ahora - a.creado_en).days if a.creado_en else 0
    ultimo_login_dias = (ahora - a.ultimo_login).days if getattr(a, "ultimo_login", None) else None

    prospectos = a.prospectos or []
    n_prosp = len(prospectos)
    n_sin_contactar = sum(1 for p in prospectos if p.estado == "sin_contactar")
    n_contactados   = sum(1 for p in prospectos if p.estado == "contactado")
    n_respondio     = sum(1 for p in prospectos if p.estado == "respondio")
    n_leads_bolsa   = db.query(LeadBolsa).filter(LeadBolsa.aliado_id == a.id).count() if not es_canal2 else 0
    n_ventas        = a.ventas_6_meses or 0
    n_sub_aliados   = len(getattr(a, "sub_aliados", []) or [])

    # ─── Reconstruir checklist para saber pct y pasos pendientes ─────────────
    pasos_check = [
        ("Registrarte", True),
        ("Registrar tu primer referido", len(a.referidos or []) > 0),
        ("Cargar un prospecto", n_prosp > 0),
    ]
    if not es_canal2:
        pasos_check.append(("Reclamar un lead de la bolsa", n_leads_bolsa > 0))
    pasos_check += [
        ("Cerrar tu primera venta", n_ventas > 0),
        ("Invitar a tu primer sub-aliado", n_sub_aliados > 0),
    ]
    completados = sum(1 for _, ok in pasos_check if ok)
    pct = round(completados / len(pasos_check) * 100) if pasos_check else 0
    pasos_pendientes = [t for t, ok in pasos_check if not ok]

    # ─── Llamar a IA ─────────────────────────────────────────────────────────
    ia = groq_ai.coach_onboarding_ia(
        aliado_nombre=a.nombre,
        dias_desde_registro=dias_desde_registro,
        es_canal2=es_canal2,
        tiene_prospectos=(n_prosp > 0),
        n_prospectos=n_prosp,
        n_prospectos_sin_contactar=n_sin_contactar,
        n_prospectos_contactados=n_contactados,
        n_prospectos_respondio=n_respondio,
        n_leads_bolsa_reclamados=n_leads_bolsa,
        n_ventas=n_ventas,
        n_sub_aliados=n_sub_aliados,
        ultimo_login_dias=ultimo_login_dias,
        checklist_pct=pct,
        pasos_pendientes=pasos_pendientes,
    )

    if ia:
        return {
            "modo": "ia",
            "diagnostico": ia["diagnostico"],
            "siguiente_paso": ia["siguiente_paso"],
            "razon": ia["razon"],
            "plantilla": ia["plantilla"],
            "checklist_pct": pct,
        }

    # ─── Fallback heurístico: árbol de decisión simple ───────────────────────
    if n_prosp == 0:
        diag = "No hay prospectos cargados todavía. Sin inputs no hay outputs."
        nxt  = "Cargá 3 prospectos hoy: empresas conocidas que no tengan presencia digital."
        razon = "Tener pipeline es la condición mínima para que cualquier otra cosa funcione."
        plantilla = ""
    elif n_sin_contactar > 0 and n_sin_contactar == n_prosp:
        diag = f"Cargaste {n_prosp} prospecto{'s' if n_prosp != 1 else ''} pero no contactaste a ninguno."
        nxt  = "Mandale el link de la auditoría gratuita al primero de la lista hoy mismo."
        razon = "Inventario sin acción se enfría en 7 días — perdés la ventana."
        plantilla = "Hola, soy [tu nombre]. Te paso un diagnóstico digital gratuito de tu empresa, toma 30 seg: [link]. Si te hace ruido lo que devuelve, hablamos."
    elif n_respondio > 0 and n_ventas == 0:
        diag = f"Tenés {n_respondio} prospecto{'s' if n_respondio != 1 else ''} que respondieron pero ninguna venta cerrada."
        nxt  = "Usá el Cotizador para mandarles una propuesta concreta esta semana."
        razon = "Una respuesta sin propuesta concreta enfría en 5 días."
        plantilla = ""
    elif n_contactados > n_respondio and (n_contactados - n_respondio) >= 3:
        diag = f"{n_contactados - n_respondio} prospectos contactados que no respondieron — necesitan re-enganche."
        nxt  = "Usá el botón 'Follow-up IA' en cada prospecto contactado hace más de 3 días."
        razon = "Sin follow-up sistemático, el 80% de los leads se enfrían."
        plantilla = ""
    elif not es_canal2 and n_leads_bolsa == 0:
        diag = "Hay leads disponibles en la bolsa que no estás reclamando."
        nxt  = "Entrá a la Bolsa y reclamá 1 lead que matchee tu rubro fuerte."
        razon = "La bolsa son clientes pre-calificados — costo cero comparado con prospectar en frío."
        plantilla = ""
    elif n_ventas == 0 and n_prosp >= 5:
        diag = "Pipeline lleno pero cero ventas — el problema está en cierre, no en captación."
        nxt  = "Revisá los prospectos perdidos con el botón 'Marcar perdido + analizar IA'."
        razon = "Diagnosticar pérdidas pasadas es más rápido que cargar más prospectos."
        plantilla = ""
    elif n_sub_aliados == 0 and n_ventas >= 1:
        diag = "Ya cerraste ventas pero tu red de sub-aliados está plana."
        nxt  = "Invitá a 1 conocido que ya esté en venta consultiva (consultor, agencia, contador)."
        razon = "Cada sub-aliado activo te suma ingresos pasivos sin más esfuerzo."
        plantilla = ""
    else:
        diag = f"Tu progreso del checklist está en {pct}%."
        nxt  = "Avanzá con el siguiente paso pendiente: " + (pasos_pendientes[0] if pasos_pendientes else "todo completo, mantené el ritmo.")
        razon = "Cada paso del checklist desbloquea capacidades nuevas del programa."
        plantilla = ""

    return {
        "modo": "fallback",
        "diagnostico": diag,
        "siguiente_paso": nxt,
        "razon": razon,
        "plantilla": plantilla,
        "checklist_pct": pct,
    }


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


@app.post("/admin/bolsa")
def cargar_lead_bolsa(lead: LeadBolsaCreate, db: Session = Depends(get_db)):
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

@app.get("/admin/bolsa")
def monitor_bolsa(db: Session = Depends(get_db)):
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

@app.post("/admin/bolsa/{id}/revocar")
def revocar_lead_bolsa(id: int, db: Session = Depends(get_db)):
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


@app.delete("/admin/bolsa/bulk")
def eliminar_leads_bulk(payload: BulkDeleteLeads, db: Session = Depends(get_db)):
    """Elimina permanentemente múltiples leads de la bolsa (y los quita de los aliados que los tenían)."""
    deleted = 0
    for lead_id in payload.ids:
        lead = db.query(LeadBolsa).filter(LeadBolsa.id == lead_id).first()
        if lead:
            db.delete(lead)
            deleted += 1
    db.commit()
    return {"eliminados": deleted}


@app.delete("/admin/bolsa/all")
def eliminar_todos_los_leads(db: Session = Depends(get_db)):
    """Elimina TODOS los leads de la bolsa de una sola vez.
    Al borrar los registros LeadBolsa, desaparecen automáticamente de la
    bolsa de cualquier aliado que los tuviera reclamados (aliado_id queda huérfano)."""
    result = db.query(LeadBolsa).delete()
    db.commit()
    return {"eliminados": result, "mensaje": f"{result} lead(s) eliminados de la bolsa."}


@app.delete("/admin/bolsa/{id}")
def eliminar_lead_bolsa(id: int, db: Session = Depends(get_db)):
    """Elimina permanentemente un lead de la bolsa (se quita también de la bolsa de cualquier aliado que lo tuviera)."""
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    db.delete(lead)
    db.commit()
    return {"mensaje": "Lead eliminado."}


# ─── BOLSA DE LEADS (PORTAL ALIADO) ──────────────────────────────────────────

@app.get("/aliados/{codigo}/bolsa")
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

@app.post("/bolsa/{id}/reclamar")
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
    return {"mensaje": "¡Lead reclamado exitosamente!"}

@app.patch("/bolsa/{id}/contactar")
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
    db.commit()

    mensajes = {
        "exitoso":       "¡Excelente! Lead marcado como exitoso. ¡A cerrar la venta!",
        "no_interesado": "Anotado. El lead quedó marcado como no interesado.",
        "no_contesto":   "Anotado. Si conseguís contactarlo después, podés actualizar el estado.",
    }
    return {"mensaje": mensajes[resultado]}


@app.get("/aliados/{codigo}/historial-bolsa")
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


@app.get("/admin/historial-bolsa")
def historial_bolsa_admin(db: Session = Depends(get_db)):
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

# ═══════════════════════════════════════════════════════════════════════════
# ═══ v1.3 — INTELIGENCIA DE VENTAS + REPUTACIÓN + MARKETPLACE + COMUNIDAD ══
# ═══════════════════════════════════════════════════════════════════════════

# ─── PERFILADO IA DE LEADS (A) ───────────────────────────────────────────────
# Heurística local — sin LLM, explicable, determinística.
# El aliado carga rubro/tamaño/urgencia → el sistema devuelve score + plan + pitch.

RUBROS_PLAN = {
    # Rubros que naturalmente necesitan más infraestructura digital
    "Metalúrgica / Manufactura":     ("Plan Industrial", "B2B técnico con ciclo largo de venta"),
    "Agro / Maquinaria agrícola":    ("Plan Industrial", "Sector con presupuesto pero poca presencia digital"),
    "Logística / Transporte":        ("Plan Pro",        "Necesita canales claros de contacto y cotización"),
    "Servicios B2B / Consultoría":   ("Plan Pro",        "Necesita autoridad online y generación de leads"),
    "Comercio / Retail B2B":         ("Plan Pro",        "Catálogo + presencia local"),
    "Construcción / Obras":          ("Plan Industrial", "Obra pública/privada, necesita respaldo digital"),
    "Salud / Clínicas":              ("Plan Pro",        "Pacientes investigan online antes de elegir"),
    "Educación / Capacitación":      ("Plan Pro",        "Captación online es crítica"),
    "Tecnología / Software":         ("Estrategico 360", "Mercado educado, espera excelencia digital"),
    "Otro":                          ("Plan Pro",        "Plan versátil para la mayoría"),
}

TAMANOS_MULT = {"micro": 0.6, "pyme": 1.0, "mediana": 1.25, "grande": 1.4}
URGENCIA_SCORE = {"baja": 10, "media": 25, "alta": 40}


def _perfilar_prospecto(p: Prospecto) -> dict:
    """Corazón del perfilado IA: intenta Groq primero, fallback a heurística."""

    # ─── INTENTO 1: IA real (Groq) ──────────────────────────────────────────
    ia = groq_ai.perfilar_lead_ia(
        empresa=p.nombre,
        rubro=p.rubro,
        tamano=p.tamano,
        urgencia=p.urgencia,
        estado=p.estado,
        nota_aliado=p.nota,
    )
    if ia:
        # Si el aliado fijó un plan_interes manual, lo respetamos por encima de la IA.
        if p.plan_interes and p.plan_interes in PLANES:
            ia["plan_recomendado"] = p.plan_interes
            ia["ticket_esperado"] = round(PLANES[p.plan_interes] * TAMANOS_MULT.get(p.tamano or "pyme", 1.0), 0)
        return ia

    # ─── INTENTO 2: Fallback heurístico (lo de siempre) ─────────────────────
    return _perfilar_prospecto_heuristico(p)


def _perfilar_prospecto_heuristico(p: Prospecto) -> dict:
    """Heurística determinística — el fallback de siempre cuando Groq no responde."""
    score = 20  # base

    # 1. Rubro → +20 si es rubro de alta necesidad, plan sugerido
    plan, razon_rubro = RUBROS_PLAN.get(p.rubro or "Otro", ("Plan Pro", "Plan versátil"))
    if p.rubro and p.rubro != "Otro":
        score += 20

    # 2. Urgencia pesa fuerte (hasta +40)
    score += URGENCIA_SCORE.get(p.urgencia or "media", 25)

    # 3. Tamaño ajusta expectativa de ticket
    mult = TAMANOS_MULT.get(p.tamano or "pyme", 1.0)

    # Si el tamaño es grande → empujar a plan superior
    if p.tamano == "grande" and plan == "Plan Pro":
        plan = "Plan Industrial"
    elif p.tamano == "grande" and plan == "Plan Industrial":
        plan = "Estrategico 360"
    elif p.tamano == "micro" and plan != "Plan Base":
        plan = "Plan Base"
        razon_rubro = "Empresa chica — empezar con Plan Base y escalar después"

    # 4. Si ya respondió un mensaje → bonus fuerte
    if p.estado == "respondio":
        score += 15
    elif p.estado == "contactado":
        score += 5

    # 5. Si tiene plan_interes manual del aliado, respetarlo con un boost
    if p.plan_interes and p.plan_interes in PLANES:
        plan = p.plan_interes
        score += 5

    # Normalizar ticket esperado
    ticket = PLANES.get(plan, 2900) * mult
    score = max(0, min(100, int(score)))

    # 6. Pitch sugerido
    pitch = _generar_pitch(p.nombre, p.rubro, p.tamano, p.urgencia, plan, ticket)

    return {
        "score": score,
        "plan_recomendado": plan,
        "pitch_sugerido": pitch,
        "ticket_esperado": round(ticket, 0),
        "razon": razon_rubro,
    }


def _generar_pitch(nombre: str, rubro: str, tamano: str, urgencia: str, plan: str, ticket: float) -> str:
    """Genera un pitch corto y accionable para WhatsApp/email."""
    apertura = {
        "alta": f"Hola, vi que {nombre} está creciendo rápido — les paso algo que puede ahorrarles tiempo.",
        "media": f"Hola, estuve revisando empresas del rubro {rubro or 'de ustedes'} y {nombre} me llamó la atención.",
        "baja": f"Hola, te paso info por si a futuro les sirve. Sin apuro.",
    }.get(urgencia or "media")

    dolor = {
        "Metalúrgica / Manufactura": "Muchas fábricas pierden contactos porque su web no genera confianza técnica.",
        "Agro / Maquinaria agrícola": "En el agro el cliente investiga mucho antes de llamar — la web define si te llaman o no.",
        "Logística / Transporte": "Los clientes B2B esperan poder cotizar rápido, sin esperar 2 días a que les llamen.",
        "Servicios B2B / Consultoría": "Si tu web no transmite autoridad en 5 segundos, el lead se va a la competencia.",
        "Salud / Clínicas": "El 80% de los pacientes googlean antes de sacar turno.",
        "Construcción / Obras": "Las obras grandes se eligen por respaldo — y el respaldo hoy se mide online.",
    }.get(rubro or "Otro", "Las empresas que no invierten en digital pierden hasta un 30% de oportunidades por mes.")

    cierre = {
        "Plan Base":        f"Arrancamos con el Plan Base (USD {int(PLANES['Plan Base'])}): sitio limpio + Google Business + métricas en 30 días.",
        "Plan Pro":         f"Te sugiero el Plan Pro (USD {int(PLANES['Plan Pro'])}): incluye captación activa de leads, no solo presencia.",
        "Plan Industrial":  f"Por el tamaño de {nombre} va el Plan Industrial (USD {int(PLANES['Plan Industrial'])}): sistema completo + ventas B2B.",
        "Estrategico 360":  f"Lo que encaja acá es un Estratégico 360 (USD {int(PLANES['Estrategico 360'])}): canal digital entero operando como una máquina.",
    }.get(plan, "")

    return f"{apertura}\n\n{dolor}\n\n{cierre}\n\n¿Te mando un diagnóstico gratis para que veas el estado actual?"


@app.post("/prospectos/{id}/perfilar")
def perfilar_prospecto(id: int, request: Request,
                       body: schemas.PerfilarProspectoIn | None = Body(default=None),
                       rubro: str = "",
                       tamano: str = "pyme",
                       urgencia: str = "media",
                       db: Session = Depends(get_db)):
    """Corre el perfilado IA sobre un prospecto y guarda el resultado."""
    if body is not None:
        rubro, tamano, urgencia = body.rubro, body.tamano, body.urgencia
    p = _get_prospecto_owned_or_admin(id, request, db)
    if rubro:
        p.rubro = rubro
    p.tamano = tamano
    p.urgencia = urgencia

    resultado = _perfilar_prospecto(p)
    p.score_ia = resultado["score"]
    p.plan_recomendado = resultado["plan_recomendado"]
    p.pitch_sugerido = resultado["pitch_sugerido"]
    p.perfilado_en = datetime.now()
    db.commit()

    return {
        "mensaje": "Prospecto perfilado.",
        "score": resultado["score"],
        "plan_recomendado": resultado["plan_recomendado"],
        "pitch_sugerido": resultado["pitch_sugerido"],
        "ticket_esperado": resultado["ticket_esperado"],
        "razon": resultado["razon"],
    }


@app.patch("/prospectos/{id}/datos")
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


# ─── PERFILADO IA DE LEADS DE LA BOLSA ──────────────────────────────────────
# Antes esto era 100% JavaScript con templates en portal.html. Ahora pasa por
# Groq y devuelve un pitch real personalizado por empresa. Si Groq falla,
# devolvemos el resultado del fallback heurístico (el front sigue funcionando).

@app.post("/bolsa/{lead_id}/perfilar-ia")
def perfilar_lead_bolsa(lead_id: int, request: Request,
                        rubro: str = "",
                        tamano: str = "pyme",
                        urgencia: str = "media",
                        db: Session = Depends(get_db),
                        _aliado=Depends(current_aliado_required)):
    """
    Perfilado IA para un lead de la Bolsa.
    El aliado solo necesita estar autenticado (no necesita haber reclamado el lead todavía
    — perfilar antes de reclamar es parte del flow para decidir si vale el costo).
    """
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")

    # Si el aliado no pasó rubro, usamos el del lead.
    rubro_efectivo = (rubro or "").strip() or (lead.rubro or "")
    tamano_ef = (tamano or "pyme").strip()
    urgencia_ef = (urgencia or "media").strip()

    # ─── Intento 1: Groq ─────────────────────────────────────────────────────
    ia = groq_ai.perfilar_lead_ia(
        empresa=lead.empresa,
        rubro=rubro_efectivo,
        tamano=tamano_ef,
        urgencia=urgencia_ef,
        ciudad=lead.ciudad,
        # v1.6 — presencia digital
        web=lead.web,
        instagram=lead.instagram,
        tiene_web=bool(lead.tiene_web),
        tiene_redes=bool(lead.tiene_redes),
        observacion=lead.observacion,
    )
    if ia:
        return {
            "modo": "ia",
            "score": ia["score"],
            "plan_recomendado": ia["plan_recomendado"],
            "pitch_sugerido": ia["pitch_sugerido"],
            "ticket_esperado": ia["ticket_esperado"],
            "razon": ia["razon"],
        }

    # ─── Intento 2: Fallback heurístico ──────────────────────────────────────
    # Reusamos la misma lógica del prospecto montando un objeto temporal.
    class _LeadShim:
        nombre   = lead.empresa
        rubro    = rubro_efectivo
        tamano   = tamano_ef
        urgencia = urgencia_ef
        estado   = "sin_contactar"
        plan_interes = None
        nota = None
    res = _perfilar_prospecto_heuristico(_LeadShim())
    res["modo"] = "fallback"
    return res


# ─── GENERADOR DE FOLLOW-UP IA (Prioridad #4) ────────────────────────────────
# El aliado abre un prospecto y pide "generame un mensaje de seguimiento".
# Devuelve un mensaje listo para copiar+pegar. El aliado puede pedir varias
# veces para regenerar — cada llamada es un request a Groq.

@app.post("/prospectos/{id}/followup-ia")
def generar_followup_prospecto(id: int, request: Request,
                                tono: str = "directo",
                                db: Session = Depends(get_db)):
    """
    Genera un mensaje de follow-up para un prospecto que no responde.
    Tono válido: 'amigable' | 'directo' | 'ultimo' | 'valor' (default: directo).
    """
    p = _get_prospecto_owned_or_admin(id, request, db)

    # Calcular días sin responder según el estado.
    dias = None
    if p.estado in ("contactado", "propuesta_enviada") and p.fecha_contacto:
        dias = (datetime.now() - p.fecha_contacto).days
    elif p.fecha_respuesta:
        dias = (datetime.now() - p.fecha_respuesta).days
    elif p.creado_en:
        dias = (datetime.now() - p.creado_en).days

    aliado_obj = p.aliado

    ia = groq_ai.generar_followup_ia(
        prospecto_nombre=p.nombre,
        rubro=p.rubro,
        tamano=p.tamano,
        plan_recomendado=p.plan_recomendado or p.plan_interes,
        dias_sin_responder=dias,
        ultima_nota=p.nota,
        aliado_nombre=aliado_obj.nombre if aliado_obj else None,
        tono=tono if tono in ("amigable", "directo", "ultimo", "valor") else "directo",
    )
    if ia:
        return {
            "modo": "ia",
            "mensaje": ia["mensaje"],
            "estrategia": ia["estrategia"],
            "tono": tono,
            "dias_sin_responder": dias,
        }

    # ─── Fallback heurístico — mensajes plantilla por tono ───────────────────
    nombre_corto = (p.nombre or "").split()[0] or "Hola"
    plantillas = {
        "amigable": f"¡Hola {nombre_corto}! ¿Cómo va? Quería retomar lo que estábamos charlando. ¿Tenés un momento esta semana para repasarlo?",
        "directo":  f"Hola {nombre_corto}, soy [tu nombre]. Te escribo para retomar la propuesta. ¿Avanzamos o lo pausamos por ahora?",
        "ultimo":   f"Hola {nombre_corto}, este es mi último mensaje para no hacerme pesado. Si no es el momento, perfecto — quedate con mi contacto para cuando quieras retomar.",
        "valor":    f"Hola {nombre_corto}, te paso un dato del rubro {p.rubro or 'tuyo'} que quizás te sirve aunque no avancemos: empresas similares pierden hasta 30% de consultas por temas digitales simples. ¿Te interesa que te muestre cómo evaluarlo?",
    }
    return {
        "modo": "fallback",
        "mensaje": plantillas.get(tono, plantillas["directo"]),
        "estrategia": "Mensaje plantilla — la IA no estaba disponible.",
        "tono": tono,
        "dias_sin_responder": dias,
    }


# ─── RESPUESTA A OBJECIONES IA (Prioridad #5) ────────────────────────────────
# El aliado pega la objeción que le dijeron y Groq devuelve cómo responder.

@app.post("/prospectos/{id}/objecion-ia")
def responder_objecion_prospecto(id: int, request: Request,
                                  objecion: str = "",
                                  db: Session = Depends(get_db)):
    """
    Genera una respuesta a una objeción. El aliado pasa el texto de la objeción
    como query param (URL-encoded).
    """
    p = _get_prospecto_owned_or_admin(id, request, db)
    obj_text = (objecion or "").strip()
    if not obj_text:
        raise HTTPException(400, "Falta el texto de la objeción (?objecion=...)")

    ia = groq_ai.responder_objecion_ia(
        objecion=obj_text,
        prospecto_nombre=p.nombre,
        rubro=p.rubro,
        tamano=p.tamano,
        plan_recomendado=p.plan_recomendado or p.plan_interes,
        ticket_esperado=(PLANES.get(p.plan_recomendado or "", 0)
                         * TAMANOS_MULT.get(p.tamano or "pyme", 1.0)) if p.plan_recomendado else None,
    )
    if ia:
        return {"modo": "ia", **ia}

    # ─── Fallback: respuestas plantilla por palabra clave ────────────────────
    bajo = obj_text.lower()
    if any(k in bajo for k in ("caro", "precio", "presupuesto", "no tengo plata")):
        return {
            "modo": "fallback",
            "respuesta": "Te entiendo. La pregunta no es cuánto cuesta sino cuánto te cuesta NO tenerlo. Empresas similares pierden 20-40% de consultas por mes por temas digitales. ¿Hacemos un diagnóstico rápido para medirlo en tu caso?",
            "explicacion": "Reformula precio a costo de oportunidad.",
            "siguiente_pregunta": "¿Cuántas consultas mensuales recibís hoy?",
        }
    if any(k in bajo for k in ("ya tengo", "tengo web", "tengo página", "ya hice")):
        return {
            "modo": "fallback",
            "respuesta": "Buenísimo. Tener algo es mejor que nada. La pregunta clave es: ¿cuántas consultas reales te trae al mes y cómo se compara con el potencial del rubro? A veces conviene ajustar lo que hay, otras conviene rehacerlo.",
            "explicacion": "Calificá la web actual antes de proponer reemplazo.",
            "siguiente_pregunta": "¿Cuándo se hizo y qué métricas tenés?",
        }
    if any(k in bajo for k in ("no es el momento", "más adelante", "en unos meses", "otro momento")):
        return {
            "modo": "fallback",
            "respuesta": "Lo respeto. Igualmente te propongo algo: una llamada corta de diagnóstico (15 min) para que cuando SÍ sea el momento ya tengas los datos en la mano. Sin compromiso.",
            "explicacion": "Mantené la puerta abierta sin presionar.",
            "siguiente_pregunta": "¿Te queda mejor esta o la próxima semana?",
        }
    if any(k in bajo for k in ("pensar", "voy a ver", "consultar")):
        return {
            "modo": "fallback",
            "respuesta": "Perfecto. Para que la pensada te sirva, ¿qué información te faltaría tener para decidir? Te la armo y te la mando.",
            "explicacion": "Convertí 'lo pienso' en una pregunta concreta.",
            "siguiente_pregunta": "¿Qué dato te falta para definirlo?",
        }
    return {
        "modo": "fallback",
        "respuesta": "Te entiendo. ¿Me podés contar un poco más de qué es lo que más te frena? Así te respondo con algo concreto y no con un genérico.",
        "explicacion": "Pediles que aterricen la objeción.",
        "siguiente_pregunta": "¿Qué es lo que más te hace dudar?",
    }


# ─── ANÁLISIS DE VENTA PERDIDA IA (Prioridad #8) ─────────────────────────────
# Cuando un prospecto se marca como 'perdido', el aliado puede pedir un
# diagnóstico IA del historial completo: qué pasó, qué se hizo mal, qué hacer
# distinto la próxima, y si se puede recuperar más adelante.

@app.post("/prospectos/{id}/analizar-perdida")
def analizar_venta_perdida(id: int, request: Request,
                            motivo: str = "",
                            db: Session = Depends(get_db)):
    """
    Analiza el historial de un prospecto perdido y devuelve diagnóstico IA.
    Antes de analizar, marca el estado como 'perdido' si todavía no lo está
    y guarda el motivo en la nota (anteponiendo "[PERDIDO] ...").
    """
    p = _get_prospecto_owned_or_admin(id, request, db)

    # 1. Estado anterior antes de cambiarlo (lo necesita el análisis).
    estado_anterior = p.estado

    # 2. Marcar como perdido si todavía no lo está.
    if p.estado != "perdido":
        p.estado = "perdido"

    # 3. Guardar motivo en nota (sin pisar lo que ya había).
    motivo_clean = (motivo or "").strip()
    if motivo_clean:
        prefix = "[PERDIDO]"
        if p.nota and prefix not in p.nota:
            p.nota = f"{prefix} {motivo_clean}\n---\n{p.nota}"
        elif not p.nota:
            p.nota = f"{prefix} {motivo_clean}"
        # Si ya tenía un [PERDIDO], no duplicamos.

    db.commit()

    # 4. Calcular días relevantes para el análisis.
    ahora = datetime.now()
    dias_pipeline = (ahora - p.creado_en).days if p.creado_en else None
    dias_contacto = (ahora - p.fecha_contacto).days if p.fecha_contacto else None
    dias_respuesta = (ahora - p.fecha_respuesta).days if p.fecha_respuesta else None

    ticket_esp = None
    if p.plan_recomendado and p.plan_recomendado in PLANES:
        mult = TAMANOS_MULT.get(p.tamano or "pyme", 1.0)
        ticket_esp = PLANES[p.plan_recomendado] * mult

    # 5. Llamar a Groq.
    ia = groq_ai.analizar_venta_perdida_ia(
        prospecto_nombre=p.nombre,
        rubro=p.rubro,
        tamano=p.tamano,
        urgencia_perfilada=p.urgencia,
        plan_recomendado=p.plan_recomendado or p.plan_interes,
        ticket_esperado=ticket_esp,
        estado_anterior=estado_anterior,
        dias_en_pipeline=dias_pipeline,
        fecha_contacto_dias=dias_contacto,
        fecha_respuesta_dias=dias_respuesta,
        pasos_piloto=p.automation_paso or 0,
        notas=p.nota,
        motivo_aliado=motivo_clean or None,
    )
    if ia:
        return {"modo": "ia", "estado": p.estado, **ia}

    # ─── Fallback: análisis heurístico básico ────────────────────────────────
    errores = []
    distinto = []
    podria_rec = False

    # Caso 1: nunca contactado o contactado pero nunca respondió
    if estado_anterior in ("sin_contactar", "contactado"):
        errores.append("El prospecto pudo nunca haber recibido un mensaje claro de valor.")
        if dias_pipeline and dias_pipeline > 14:
            errores.append(f"Estuvo {dias_pipeline} días en pipeline sin avanzar — el lead se enfrió.")
        distinto.append("Cargá menos prospectos pero contactá a todos en las primeras 48hs.")
        distinto.append("Usá el botón 'Follow-up IA' a los 3 días de no recibir respuesta.")
        que_paso = "El prospecto no avanzó del primer contacto. Probable que el mensaje inicial no haya conectado o no haya habido follow-up sistemático."
        podria_rec = True

    # Caso 2: respondió pero no se cerró
    elif estado_anterior in ("respondio", "propuesta_enviada"):
        errores.append("Hubo interés pero no se cerró — falta presión positiva o el plan no encajó.")
        errores.append("Posiblemente faltó calificar urgencia/presupuesto antes de mandar la propuesta.")
        distinto.append("Antes de enviar propuesta, validar urgencia y decisor real.")
        distinto.append("Después de propuesta, agendar fecha concreta para revisarla juntos.")
        que_paso = "El prospecto entró en conversación pero la propuesta no avanzó. Suele indicar falta de calificación previa o ausencia de próximo paso definido."
        podria_rec = True

    else:
        errores.append("No hay suficiente historial para diagnosticar con precisión.")
        distinto.append("Anotá siempre el motivo de la pérdida en la nota para futuras revisiones.")
        que_paso = "Pocos datos del historial — registrá más contexto al cerrar prospectos."

    return {
        "modo": "fallback",
        "estado": p.estado,
        "que_paso": que_paso,
        "errores_posibles": errores,
        "que_hacer_distinto": distinto,
        "podria_recuperarse": podria_rec,
        "mensaje_recuperacion": "" if not podria_rec else f"Hola, hace un tiempo charlamos sobre {p.nombre} y la presencia digital. ¿Cambió algo en estos meses? Te paso un diagnóstico actualizado sin compromiso.",
    }


# ─── ASISTENTE PARA POSTS DE COMUNIDAD (Prioridad #7) ────────────────────────
# El aliado escribe unos datos cortos en el composer y Groq genera título+cuerpo.

@app.post("/comunidad/asistente-ia")
def asistente_post_comunidad(request: Request,
                              tipo: str = "tip",
                              datos: str = "",
                              db: Session = Depends(get_db),
                              aliado=Depends(current_aliado_required)):
    """
    Genera {titulo, cuerpo} para un post de comunidad.
    tipo: 'win' | 'tip' | 'pregunta'
    datos: texto libre con los datos clave que aporta el aliado.
    """
    datos_text = (datos or "").strip()
    if not datos_text:
        raise HTTPException(400, "Necesito unos datos clave para redactar el post.")
    if tipo not in ("win", "tip", "pregunta"):
        raise HTTPException(400, "Tipo inválido. Usá: win, tip o pregunta.")

    ia = groq_ai.redactar_post_comunidad_ia(
        tipo=tipo,
        datos_clave=datos_text,
        aliado_nombre=aliado.nombre,
    )
    if ia:
        return {"modo": "ia", **ia}

    # Fallback básico — devolvemos un esqueleto con los datos del aliado al menos formateados.
    plantilla_titulo = {
        "win":      "Compartiendo un cierre",
        "tip":      "Tip de la semana",
        "pregunta": "Una consulta para la red",
    }[tipo]
    return {
        "modo": "fallback",
        "titulo": plantilla_titulo,
        "cuerpo": datos_text,
    }


# ─── SISTEMA DE REPUTACIÓN (C) ───────────────────────────────────────────────

def _calcular_reputacion(a: Aliado, db: Session) -> dict:
    """Calcula score 0-100 + badges del aliado.
    Factores (ponderados):
      - Tasa de cierre (40%)
      - Velocidad de contacto en bolsa (20%)
      - Tasa éxito en bolsa (20%)
      - Actividad reciente (10%)
      - Tamaño de red (10%)
    """
    ventas_conf = [v for v in a.ventas if v.confirmada]
    total_ventas = len(ventas_conf)
    total_refs = len(a.referidos)
    tasa_cierre = (total_ventas / total_refs) if total_refs > 0 else 0
    ticket_prom = (sum(v.valor_usd for v in ventas_conf) / total_ventas) if total_ventas else 0

    # Bolsa
    leads_bolsa = getattr(a, "leads_bolsa", [])
    exitosos = sum(1 for l in leads_bolsa if l.resultado == "exitoso")
    tasa_bolsa = (exitosos / len(leads_bolsa)) if leads_bolsa else 0

    # Actividad (últimos 30 días)
    corte = datetime.now() - timedelta(days=30)
    activo_reciente = (a.ultimo_login and a.ultimo_login >= corte) or \
                      any(r.registrado_en >= corte for r in a.referidos) or \
                      any(v.fecha_venta and v.fecha_venta >= corte for v in ventas_conf)

    # Red
    red_activa = sum(1 for sub in getattr(a, "sub_aliados", []) if sub.ventas_6_meses > 0)

    # Score
    score = 30  # base
    score += int(min(40, tasa_cierre * 100))        # hasta +40 por tasa cierre
    score += int(min(20, tasa_bolsa * 50))          # hasta +20 por éxito bolsa
    score += 10 if activo_reciente else 0
    score += min(10, red_activa * 3)                # hasta +10 por red activa
    score = max(0, min(100, score))

    # Badges
    badges = []
    if tasa_cierre >= 0.40 and total_ventas >= 2:
        badges.append("CLOSER")
    if ticket_prom >= 3500 and total_ventas >= 1:
        badges.append("TOP_TICKET")
    if activo_reciente and a.cantidad_logins and a.cantidad_logins >= 10:
        badges.append("FIEL")
    if red_activa >= 3:
        badges.append("EMBAJADOR")
    if tasa_bolsa >= 0.30 and len(leads_bolsa) >= 3:
        badges.append("BOLSA_MASTER")
    # "Rápido": reclaimó al menos 3 leads en < 6hs desde que entraron a la bolsa
    tiempos = []
    for l in leads_bolsa:
        if l.fecha_carga and l.fecha_reclamo:
            horas = (l.fecha_reclamo - l.fecha_carga).total_seconds() / 3600
            tiempos.append(horas)
    rapidos = sum(1 for h in tiempos if h <= 6)
    if rapidos >= 3:
        badges.append("RAPIDO")

    return {
        "score": score,
        "badges": badges,
        "factores": {
            "tasa_cierre": round(tasa_cierre * 100, 1),
            "ticket_prom": round(ticket_prom),
            "tasa_bolsa": round(tasa_bolsa * 100, 1),
            "activo_reciente": activo_reciente,
            "red_activa": red_activa,
        },
    }


@app.get("/aliados/{codigo}/reputacion")
def ver_reputacion(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    calc = _calcular_reputacion(a, db)
    # Persistir
    try:
        a.reputacion_score = calc["score"]
        a.badges = json.dumps(calc["badges"])
        a.reputacion_calculada_en = datetime.now()
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error guardando reputación: {e}")

    badges_full = [
        {"code": b, **REPUTACION_BADGES[b]}
        for b in calc["badges"] if b in REPUTACION_BADGES
    ]
    return {
        "codigo": a.codigo,
        "nombre": a.nombre,
        "score": calc["score"],
        "badges": badges_full,
        "factores": calc["factores"],
        "badges_disponibles": [
            {"code": code, **info} for code, info in REPUTACION_BADGES.items()
        ],
    }


@app.get("/admin/reputacion/ranking")
def ranking_reputacion(db: Session = Depends(get_db)):
    """Admin: ver todos los aliados rankeados por reputación."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    resultado = []
    for a in aliados:
        calc = _calcular_reputacion(a, db)
        resultado.append({
            "codigo": a.codigo,
            "nombre": a.nombre,
            "score": calc["score"],
            "badges": calc["badges"],
            **calc["factores"],
        })
    resultado.sort(key=lambda x: x["score"], reverse=True)
    return {"aliados": resultado}


# ─── PILOTO AUTOMÁTICO REAL (B) ──────────────────────────────────────────────
# Cada X horas corre el scheduler y envía el siguiente toque por email a los
# prospectos marcados como piloto_automatico = True.
#
# Secuencia: 3 toques espaciados 3 días cada uno, adaptados al plan recomendado.

PILOTO_INTERVALO_DIAS = 3  # días entre toques
PILOTO_MAX_PASOS = 3


def _render_mensaje_piloto(p: Prospecto, paso: int) -> tuple:
    """
    Devuelve (asunto, cuerpo_html) para el paso N del piloto automático.

    Estrategia:
      1. Intentamos generar asunto+cuerpo con Groq (personalizado por rubro/tamaño).
      2. Si Groq falla → caemos al template fijo de siempre (_render_mensaje_piloto_template).
      3. En ambos casos envolvemos el cuerpo en el HTML del email + disclaimer + CTA.
    """
    aliado = p.aliado
    nombre_aliado_corto = aliado.nombre.split()[0] if aliado and aliado.nombre else ""

    # ─── Intento 1: Groq ─────────────────────────────────────────────────────
    ia = groq_ai.generar_mensaje_piloto_ia(
        paso=paso,
        prospecto_nombre=p.nombre,
        rubro=p.rubro,
        tamano=p.tamano,
        plan_recomendado=p.plan_recomendado or p.plan_interes,
        aliado_nombre=aliado.nombre if aliado else None,
    )
    if ia:
        # Convertir texto plano a HTML — preservar saltos de párrafo dobles.
        cuerpo_texto = ia["cuerpo_texto"]
        parrafos = [pr.strip() for pr in cuerpo_texto.split("\n\n") if pr.strip()]
        cuerpo_html_inner = "<br><br>".join(p_.replace("\n", "<br>") for p_ in parrafos)

        # Agregamos CTA estándar (link al diagnóstico/whatsapp según paso) al final
        # solo si la IA no incluyó link explícito ya.
        cta = ""
        if aliado and aliado.ref_code and paso == 1 and "http" not in cuerpo_html_inner.lower():
            cta = (
                f"<br><br><a href='{PORTAL_URL}/p/{aliado.ref_code}' "
                f"style='color:#3b82f6;'>Te dejo este link al diagnóstico gratuito</a> por si te sirve."
            )
        elif aliado and aliado.whatsapp and paso == 3 and "http" not in cuerpo_html_inner.lower():
            cta = (
                f"<br><br>Si querés retomar: "
                f"<a href='https://wa.me/{aliado.whatsapp}' style='color:#3b82f6;'>"
                f"escribime por WhatsApp</a>."
            )

        firma = f"<br><br>— {aliado.nombre if aliado else ''}"

        return ia["asunto"], _envolver_email_piloto(cuerpo_html_inner + cta + firma, aliado)

    # ─── Intento 2: Fallback — template fijo ─────────────────────────────────
    return _render_mensaje_piloto_template(p, paso)


def _envolver_email_piloto(cuerpo_html_inner: str, aliado) -> str:
    """Envuelve el cuerpo en el contenedor HTML del email con disclaimer."""
    nombre_aliado = aliado.nombre if aliado else "Avanza Digital"
    return f"""
    <div style='font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:32px;
                max-width:560px;margin:0 auto;border-radius:12px;'>
      {cuerpo_html_inner}
      <hr style='margin:24px 0;border:none;border-top:1px solid #222;'>
      <p style='font-size:0.75rem;color:#71717a;'>
        Este mensaje fue enviado por el sistema de seguimiento automático de Avanza Digital en
        nombre de {nombre_aliado}. Para dejar de recibirlos, respondé 'BAJA' a este mail.
      </p>
    </div>
    """


def _render_mensaje_piloto_template(p: Prospecto, paso: int) -> tuple:
    """Templates fijos — fallback de siempre cuando Groq no responde."""
    aliado = p.aliado
    nombre_prospecto = p.nombre.split()[0] if p.nombre else "Hola"
    plan = p.plan_recomendado or p.plan_interes or "Plan Pro"

    if paso == 1:
        asunto = f"{nombre_prospecto}, te comparto un diagnóstico gratis"
        mensaje = (
            f"Hola {nombre_prospecto},<br><br>"
            f"Soy {aliado.nombre.split()[0]}. Hace unos días hablamos y quería retomar.<br><br>"
            f"Te dejo este <a href='{PORTAL_URL}/p/{aliado.ref_code}' "
            f"style='color:#3b82f6;'>diagnóstico gratuito</a> — toma 30 segundos y devuelve un "
            f"reporte con el estado real de tu presencia digital.<br><br>"
            f"Si te hace clic, hablamos.<br><br>"
            f"— {aliado.nombre}"
        )
    elif paso == 2:
        asunto = f"{nombre_prospecto}, un caso que quizás te sirve"
        mensaje = (
            f"Hola {nombre_prospecto},<br><br>"
            f"Te escribo por si te sirve este patrón que vemos mucho:<br><br>"
            f"Empresas del rubro {p.rubro or 'B2B'} suelen perder entre un 20% y un 40% de "
            f"consultas por problemas simples: sitios lentos, formularios rotos, o cero "
            f"captación activa. El {plan} resuelve exactamente eso.<br><br>"
            f"¿Te parece si armamos una llamada de 15 min esta semana para ver tu caso?<br><br>"
            f"— {aliado.nombre}"
        )
    else:  # paso 3 — cierre
        asunto = f"Último mensaje, {nombre_prospecto} — ¿cerramos o dejamos?"
        mensaje = (
            f"Hola {nombre_prospecto},<br><br>"
            f"Última vez que te escribo para no hacerme molesto. Si no es el momento, "
            f"perfecto — te dejo mi contacto guardado para cuando quieras retomar.<br><br>"
            f"Si sí lo es, te propongo agendar 15 min de llamada sin compromiso: "
            f"<a href='https://wa.me/{aliado.whatsapp}' style='color:#3b82f6;'>"
            f"escribime por WhatsApp</a>.<br><br>"
            f"— {aliado.nombre}"
        )

    return asunto, _envolver_email_piloto(mensaje, aliado)


def job_piloto_automatico():
    """Corre cada hora. Envía el siguiente toque a prospectos con piloto activo."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        candidatos = db.query(Prospecto).filter(
            Prospecto.piloto_automatico == True,
            Prospecto.estado != "respondio",  # si ya respondió, paramos
        ).all()

        for p in candidatos:
            # ¿Ya agotó los pasos?
            if (p.automation_paso or 0) >= PILOTO_MAX_PASOS:
                continue
            # ¿Cuándo tocamos la última vez?
            ultimo = p.automation_ultimo_en or p.automation_activa_desde or p.creado_en
            if not ultimo:
                continue
            horas_desde = (ahora - ultimo).total_seconds() / 3600
            if horas_desde < PILOTO_INTERVALO_DIAS * 24:
                continue
            # ¿Tenemos cómo contactarlo?
            if not p.contacto or "@" not in (p.contacto or ""):
                # Sin email no podemos hacer el toque automático aún
                # (WhatsApp sería una fase 2 — requiere integración)
                continue

            paso = (p.automation_paso or 0) + 1
            asunto, cuerpo = _render_mensaje_piloto(p, paso)

            try:
                enviar_email(p.contacto, asunto, cuerpo)
                p.automation_paso = paso
                p.automation_ultimo_en = ahora
                if paso == 1 and not p.fecha_contacto:
                    p.estado = "contactado"
                    p.fecha_contacto = ahora
                # Log
                log = AutomationLog(
                    prospecto_id=p.id, aliado_id=p.aliado_id, paso=paso,
                    canal="email", asunto=asunto, mensaje=cuerpo[:500], exitoso=True
                )
                db.add(log)
            except Exception as e:
                log = AutomationLog(
                    prospecto_id=p.id, aliado_id=p.aliado_id, paso=paso,
                    canal="email", asunto=asunto, mensaje=str(e)[:500], exitoso=False
                )
                db.add(log)

        db.commit()
    except Exception as e:
        print(f"[PILOTO ERROR] {e}")
    finally:
        db.close()


# Lo registramos en el scheduler que ya existe
scheduler.add_job(job_piloto_automatico, "interval", hours=1)


@app.get("/aliados/{codigo}/automation-log")
def ver_automation_log(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Historial de mensajes automáticos enviados a los prospectos de este aliado."""
    a = _get_aliado(codigo, db)
    logs = db.query(AutomationLog).filter(
        AutomationLog.aliado_id == a.id
    ).order_by(AutomationLog.creado_en.desc()).limit(50).all()
    # Mapear a nombre de prospecto
    resultado = []
    for l in logs:
        p = db.query(Prospecto).filter(Prospecto.id == l.prospecto_id).first()
        resultado.append({
            "id": l.id,
            "prospecto": p.nombre if p else "—",
            "paso": l.paso,
            "canal": l.canal,
            "asunto": l.asunto,
            "exitoso": l.exitoso,
            "fecha": l.creado_en.strftime("%d/%m/%Y %H:%M") if l.creado_en else None,
        })
    return {"logs": resultado}


# ─── MARKETPLACE DE LEADS + CRÉDITOS (D) ─────────────────────────────────────

def _ajustar_creditos(db: Session, aliado: Aliado, delta: int, motivo: str, ref: str = ""):
    """Helper: suma/resta créditos y registra transacción.

    ATÓMICO: usa UPDATE ... WHERE id=? AND creditos+delta>=0 para evitar race
    conditions cuando dos requests del mismo aliado intentan descontar a la vez
    (doble click en "Comprar lead", scripts maliciosos, retries, etc.).

    Si delta es NEGATIVO y el saldo cambió por debajo de lo necesario, lanza
    HTTPException 400 (que el caller puede capturar para devolver "saldo insuficiente").
    Si delta es POSITIVO siempre tiene éxito (no hay tope superior).
    """
    from sqlalchemy import update
    from fastapi import HTTPException

    res = db.execute(
        update(Aliado)
        .where(
            Aliado.id == aliado.id,
            # Postgres + SQLite: COALESCE para tratar NULL como 0.
            func.coalesce(Aliado.creditos, 0) + delta >= 0,
        )
        .values(creditos=func.coalesce(Aliado.creditos, 0) + delta)
    )
    if res.rowcount == 0:
        # Saldo cambió bajo nuestros pies o no alcanzaba. Solo es un error real
        # si era un débito; un crédito (delta>0) nunca debería fallar este check.
        if delta < 0:
            raise HTTPException(
                400,
                "Saldo insuficiente o cambió antes de procesar. Reintentá la operación.",
            )
        else:
            raise HTTPException(500, "Error inesperado ajustando créditos del aliado.")

    # Refrescamos el objeto en memoria para que el caller vea el valor nuevo.
    db.refresh(aliado)

    t = TransaccionCredito(aliado_id=aliado.id, delta=delta, motivo=motivo, referencia=ref)
    db.add(t)


# Bonus por cierre de la primera venta confirmada del aliado.
# Cierra el loop psicológico "vendí → tengo más ammo para volver a vender".
# Se aplica UNA SOLA VEZ por aliado: la idempotencia se garantiza con el check
# de `ventas_previas == 0` que cada caller hace antes de invocar este helper.
BONUS_PRIMERA_VENTA          = 200   # créditos al aliado que cerró
BONUS_SPONSOR_PRIMERA_VENTA  = 100   # créditos al sponsor (si existe)


def _aplicar_bonus_primera_venta(db: Session, aliado: Aliado, venta_id: int) -> dict:
    """Otorga el bonus de primera venta al aliado y, si tiene sponsor, también
    al sponsor. Devuelve un dict con detalle para que el endpoint llamador lo
    incluya en la respuesta JSON (útil para que el front muestre un toast).

    NO chequea si es realmente la primera venta — eso es responsabilidad del
    caller (porque ya tiene la query de ventas previas hecha por otros motivos).
    Llamar a este helper solo cuando se confirmó que ventas_previas == 0.
    """
    resultado = {
        "aliado_bonus":       BONUS_PRIMERA_VENTA,
        "aliado_saldo_nuevo": None,
        "sponsor_bonus":      0,
        "sponsor_codigo":     None,
    }

    # 1) Bonus al aliado que cerró
    _ajustar_creditos(db, aliado, BONUS_PRIMERA_VENTA,
                      "primera_venta", f"venta:{venta_id}")
    resultado["aliado_saldo_nuevo"] = (aliado.creditos or 0)

    # 2) Bonus al sponsor (si existe)
    sponsor = getattr(aliado, "sponsor", None)
    if sponsor is not None:
        _ajustar_creditos(db, sponsor, BONUS_SPONSOR_PRIMERA_VENTA,
                          "referido_primera_venta",
                          f"aliado:{aliado.id}:venta:{venta_id}")
        resultado["sponsor_bonus"]  = BONUS_SPONSOR_PRIMERA_VENTA
        resultado["sponsor_codigo"] = sponsor.codigo

    return resultado


@app.get("/aliados/{codigo}/creditos")
def ver_creditos(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    movimientos = db.query(TransaccionCredito).filter(
        TransaccionCredito.aliado_id == a.id
    ).order_by(TransaccionCredito.creado_en.desc()).limit(20).all()
    return {
        "saldo": a.creditos or 0,
        "movimientos": [
            {"delta": m.delta, "motivo": m.motivo, "ref": m.referencia,
             "fecha": m.creado_en.strftime("%d/%m/%Y %H:%M") if m.creado_en else None}
            for m in movimientos
        ]
    }


@app.post("/admin/aliados/{codigo}/creditos")
def admin_ajustar_creditos(codigo: str,
                            request: Request,
                            body: schemas.AjusteCreditosIn | None = Body(default=None),
                            delta: int = 0, motivo: str = "recarga_admin",
                            admin: dict = Depends(current_admin_required),
                            db: Session = Depends(get_db)):
    """Admin: asigna/quita créditos a un aliado. (Protegido por middleware admin.)"""
    if body is not None:
        delta, motivo = body.delta, body.motivo
    a = _get_aliado(codigo, db)
    saldo_antes = a.creditos or 0
    _ajustar_creditos(db, a, delta, motivo, "admin")
    _admin_log(
        db, admin, request,
        accion="ajustar_creditos",
        entidad="aliado", entidad_id=a.codigo,
        detalle={"delta": delta, "motivo": motivo,
                 "saldo_antes": saldo_antes, "saldo_despues": a.creditos},
    )
    db.commit()
    return {"mensaje": f"Saldo actualizado.", "nuevo_saldo": a.creditos}


@app.get("/bolsa/marketplace")
def ver_marketplace(codigo_aliado: str = "",
                    pais: str = "",
                    aliado: Aliado = Depends(current_aliado_required),
                    db: Session = Depends(get_db)):
    """Lista los leads calificados/premium disponibles con su costo en créditos.

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
    leads = q.order_by(LeadBolsa.costo_creditos.desc(), LeadBolsa.fecha_carga.desc()).all()

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
                "costo_creditos": l.costo_creditos or 0,
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


@app.post("/bolsa/{id}/comprar")
def comprar_lead(id: int,
                 background_tasks: BackgroundTasks,
                 codigo_aliado: str = "",  # legacy
                 aliado: Aliado = Depends(current_aliado_required),
                 db: Session = Depends(get_db)):
    """Compra un lead premium/calificado usando créditos del aliado autenticado."""
    a = aliado
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "El marketplace de leads no está disponible para aliados Canal 2.")
    lead = db.query(LeadBolsa).filter(
        LeadBolsa.id == id, LeadBolsa.estado == "disponible"
    ).first()
    if not lead:
        raise HTTPException(400, "Ese lead ya no está disponible.")

    if (lead.tier or "basico") == "basico":
        raise HTTPException(400, "Este lead es gratuito. Usá el endpoint /bolsa/{id}/reclamar.")

    costo = lead.costo_creditos or 0
    if (a.creditos or 0) < costo:
        # Error estructurado — el front lo agarra y lo muestra como modal con CTA,
        # no como toast rojo. La idea: convertir el "no podés" en una oferta clara.
        saldo_actual = a.creditos or 0
        raise HTTPException(400, detail={
            "code": "saldo_insuficiente",
            "mensaje": f"Te faltan {costo - saldo_actual} créditos para este lead.",
            "necesitas": costo,
            "tenes": saldo_actual,
            "faltan": costo - saldo_actual,
            "alternativas": [
                {
                    "tipo": "leads_basicos",
                    "label": "Ver leads básicos gratis",
                    "descripcion": "Los leads del tier básico no consumen créditos.",
                    "accion": "ir_a_bolsa_basicos",
                },
                {
                    "tipo": "recargar",
                    "label": "Recargar desde USD 10",
                    "descripcion": "Paquete Impulso: 100 créditos por USD 10.",
                    "accion": "abrir_modal_recarga",
                },
            ],
        })

    reclamos_activos = db.query(LeadBolsa).filter(
        LeadBolsa.aliado_id == a.id, LeadBolsa.estado == "reclamado"
    ).count()
    if reclamos_activos >= LIMITE_RECLAMOS_ACTIVOS:
        raise HTTPException(400, f"Ya tenés {LIMITE_RECLAMOS_ACTIVOS} leads reclamados activos.")

    # --- CLAIM ATÓMICO DEL LEAD (anti-TOCTOU) ─────────────────────────────────
    # Dos aliados pueden haber pasado las validaciones de arriba al mismo tiempo.
    # Acá nos aseguramos de que SOLO uno se quede con el lead: el UPDATE
    # condicional WHERE estado='disponible' falla con rowcount=0 para el segundo.
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
        # Otro aliado nos ganó de mano (race condition). Devolvemos 409
        # para que el front pueda refrescar la lista y mostrar un toast amable.
        raise HTTPException(409, "Otro aliado acaba de comprar este lead — refrescá la bolsa.")

    # Refrescamos la instancia local para mantener consistencia.
    db.refresh(lead)

    # Descuento atómico de créditos. Si por algún motivo extremo (compras
    # paralelas con varios leads) el saldo ya no alcanza, _ajustar_creditos
    # lanza 400. En ese caso revertimos el reclamo del lead antes de propagar.
    try:
        _ajustar_creditos(db, a, -costo, "compra_lead", f"lead:{lead.id}")
    except HTTPException:
        # Rollback explícito del reclamo del lead, para no dejarlo "vendido" sin cobrar.
        db.execute(
            _sa_update(LeadBolsa)
            .where(LeadBolsa.id == id, LeadBolsa.aliado_id == a.id)
            .values(estado="disponible", aliado_id=None, fecha_reclamo=None)
        )
        db.commit()
        raise

    db.commit()

    # --- AVISO SALDO BAJO (no bloquea la respuesta) ─────────────────────────
    # Mensaje de "rampa de salida": no espantar, recordar que los leads
    # `basico` siguen siendo gratis y el portal sigue 100% utilizable.
    UMBRAL_SALDO_BAJO = 30
    aviso_saldo_bajo = (a.creditos or 0) < UMBRAL_SALDO_BAJO

    if aviso_saldo_bajo and a.email:
        nombre_corto = a.nombre.split()[0] if a.nombre else "Aliado"
        saldo_actual = a.creditos or 0
        html_aviso = f"""
        <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
          <span style="background:#1c1917;color:#fdba74;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">💡 Heads up</span>
          <h2 style="margin:18px 0 12px;font-size:1.4rem;color:#fb923c;">Te quedan {saldo_actual} créditos, {nombre_corto}</h2>
          <p style="color:#a1a1aa;line-height:1.6;">Bien por reservar otro lead — pero queremos avisarte antes de que te encuentres con saldo en cero.</p>
          <div style="background:#0f1d12;border:1px solid #14532d;border-radius:8px;padding:18px;margin:20px 0;">
            <p style="margin:0 0 6px;font-weight:700;color:#86efac;">✅ Lo que NO cambia:</p>
            <p style="margin:0;color:#a1a1aa;line-height:1.6;">Los <strong style="color:#fff;">leads básicos siguen siendo 100% gratis</strong>. Los créditos solo se usan para acceder al tier calificado y premium del marketplace.</p>
          </div>
          <div style="background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:18px;margin:20px 0;">
            <p style="margin:0 0 8px;font-weight:600;color:#fff;">Si querés seguir con leads premium:</p>
            <p style="margin:0;color:#a1a1aa;line-height:1.6;font-size:.92rem;">El paquete más chico (Impulso) son 100 créditos por USD 10 — 1-2 leads premium que se pagan solos con la primera comisión que cierres.</p>
          </div>
          <a href="{PORTAL_URL}/portal.html" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:8px;">Ir al portal →</a>
          <p style="margin-top:28px;font-size:.75rem;color:#3f3f46;">Este aviso lo enviamos cuando tu saldo baja de {UMBRAL_SALDO_BAJO}. Avanza Digital · Partner Network.</p>
        </div>
        """
        background_tasks.add_task(
            enviar_email,
            a.email,
            f"💡 Te quedan {saldo_actual} créditos — pero el portal sigue activo",
            html_aviso,
        )

    return {
        "mensaje": f"¡Lead premium comprado! Te descontamos {costo} créditos.",
        "saldo_restante": a.creditos,
        "aviso_saldo_bajo": aviso_saldo_bajo,
        "umbral_saldo_bajo": UMBRAL_SALDO_BAJO,
        "lead": {
            "id": lead.id, "empresa": lead.empresa, "rubro": lead.rubro,
            "telefono": lead.telefono, "email": lead.email,
        }
    }


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


@app.post("/bolsa/{id}/reportar-mal-contacto")
def reportar_mal_contacto(id: int,
                          body: ReportarMalContactoIn,
                          aliado: Aliado = Depends(current_aliado_required),
                          db: Session = Depends(get_db)):
    """El aliado dueño del lead reporta que el contacto era inválido.
    Solo se acepta dentro de las 72hs posteriores a la compra (lead.fecha_reclamo).
    No devuelve créditos automáticamente — queda en estado 'pendiente' para
    que el admin revise."""
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
    if (lead.tier or "basico") == "basico":
        raise HTTPException(400, "Los leads básicos son gratis — no hay créditos que devolver.")

    # Validar ventana de 72hs desde la compra
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
              <p><strong>Costo del lead:</strong> {lead.costo_creditos} créditos</p>
              <p><strong>Motivo:</strong> {r.motivo}</p>
              {f"<p><strong>Detalle:</strong> {r.detalle}</p>" if r.detalle else ""}
              <p style="font-size:.85rem;color:#a1a1aa;">Revisar en: /admin/reportes-mal-contacto</p>
            </div>"""
        )
    except Exception as e:
        print(f"[REPORTE MAL CONTACTO] Email admin falló: {e}")

    return {
        "mensaje": "Reporte enviado. Te avisamos por email cuando lo revisemos.",
        "reporte_id": r.id,
        "estado": r.estado,
        "creditos_a_devolver_si_aprobado": lead.costo_creditos or 0,
    }


@app.get("/aliados/{codigo}/reportes-mal-contacto")
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


@app.get("/admin/reportes-mal-contacto")
def admin_listar_reportes(estado: str = "pendiente",
                          db: Session = Depends(get_db)):
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


@app.post("/admin/reportes-mal-contacto/{id}/aprobar")
def admin_aprobar_reporte(id: int,
                          body: ResolverReporteIn | None = Body(default=None),
                          db: Session = Depends(get_db)):
    """Aprueba un reporte: devuelve 100% de los créditos al aliado y libera
    el lead (lo manda a 'descartado' para que no vuelva al pool ni cuente
    como un reclamo activo)."""
    r = db.query(ReporteMalContacto).filter(ReporteMalContacto.id == id).first()
    if not r:
        raise HTTPException(404, "Reporte no encontrado.")
    if r.estado != "pendiente":
        raise HTTPException(400, f"Reporte ya estaba en estado '{r.estado}'.")

    aliado = db.query(Aliado).filter(Aliado.id == r.aliado_id).first()
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == r.lead_id).first()
    if not aliado or not lead:
        raise HTTPException(500, "Aliado o lead asociado no existe.")

    creditos_a_devolver = lead.costo_creditos or 0

    # Devolver créditos
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

    # Notificar al aliado
    try:
        if aliado.email:
            enviar_email(
                aliado.email,
                f"✅ Reporte aprobado: te devolvimos {creditos_a_devolver} créditos",
                f"""<div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:32px;max-width:560px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                  <h2 style="color:#4ade80;margin:0 0 12px;">¡Te devolvimos los créditos!</h2>
                  <p style="color:#a1a1aa;line-height:1.6;">Revisamos tu reporte sobre el lead <strong style="color:#fff;">{lead.empresa}</strong> y le dimos la razón. Acabamos de devolver <strong style="color:#4ade80;">{creditos_a_devolver} créditos</strong> a tu saldo.</p>
                  <div style="background:#0f1d12;border:1px solid #14532d;border-radius:8px;padding:14px;margin:18px 0;">
                    <p style="margin:0;color:#86efac;font-weight:700;">Saldo nuevo: {aliado.creditos or 0} créditos</p>
                  </div>
                  <p style="color:#a1a1aa;font-size:.9rem;">Gracias por reportarlo — nos ayuda a mejorar la calidad de los leads premium.</p>
                  <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:8px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ver mi portal →</a>
                </div>"""
            )
    except Exception as e:
        print(f"[REPORTE APROBADO] Email aliado falló: {e}")

    return {
        "mensaje": "Reporte aprobado y créditos devueltos.",
        "reporte_id": r.id,
        "creditos_devueltos": creditos_a_devolver,
        "saldo_aliado": aliado.creditos or 0,
    }


@app.post("/admin/reportes-mal-contacto/{id}/rechazar")
def admin_rechazar_reporte(id: int,
                            body: ResolverReporteIn | None = Body(default=None),
                            db: Session = Depends(get_db)):
    """Rechaza un reporte: NO devuelve créditos, deja registro auditable."""
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
                  <p style="color:#a1a1aa;line-height:1.6;">Sobre el lead <strong style="color:#fff;">{empresa}</strong>: después de revisar, decidimos no aprobar la devolución.</p>
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


# ─── MÉTRICAS DE COHORTE Y USO DE CRÉDITOS ───────────────────────────────────
# Endpoints solo-admin para detectar la cohorte de fuga (aliados que gastaron
# créditos sin cerrar venta) y para auditar el uso general del sistema.

@app.get("/admin/cohorte-fuga")
def admin_cohorte_fuga(umbral_gasto: int = 80,
                        db: Session = Depends(get_db)):
    """Devuelve los aliados que gastaron al menos `umbral_gasto` créditos
    en el marketplace y tienen 0 ventas confirmadas.
    Esta es la cohorte clave para detectar fuga: si son pocos (2-3 de 37),
    el problema es individual y conviene contactarlos uno por uno. Si son
    muchos, el problema es sistémico — calidad de leads, matching, o
    capacitación insuficiente — y meterles más créditos no va a arreglarlo.
    """
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    cohorte = []
    for a in aliados:
        # Total de créditos GASTADOS (sumar deltas negativos en compra_lead)
        gastados = db.query(TransaccionCredito).filter(
            TransaccionCredito.aliado_id == a.id,
            TransaccionCredito.motivo == "compra_lead",
        ).all()
        total_gastado = sum(-t.delta for t in gastados if t.delta < 0)

        # Ventas confirmadas
        ventas_confirmadas = db.query(Venta).filter(
            Venta.aliado_id == a.id,
            Venta.confirmada == True,
        ).count()

        if total_gastado >= umbral_gasto and ventas_confirmadas == 0:
            cohorte.append({
                "codigo": a.codigo,
                "nombre": a.nombre,
                "email": a.email,
                "whatsapp": a.whatsapp,
                "creditos_actuales": a.creditos or 0,
                "creditos_gastados": total_gastado,
                "leads_premium_comprados": len(gastados),
                "creado_en": a.creado_en.isoformat() if a.creado_en else None,
                "ultimo_login": a.ultimo_login.isoformat() if getattr(a, "ultimo_login", None) else None,
                "dias_desde_registro": (datetime.now() - a.creado_en).days if a.creado_en else None,
            })

    cohorte.sort(key=lambda x: x["creditos_gastados"], reverse=True)
    return {
        "umbral_gasto":       umbral_gasto,
        "total_en_cohorte":   len(cohorte),
        "total_aliados":      len(aliados),
        "porcentaje_fuga":    round(100 * len(cohorte) / len(aliados), 1) if aliados else 0,
        "interpretacion":     (
            "Cohorte chica → problema individual, contactá uno por uno." if len(cohorte) <= 3
            else "Cohorte grande → problema sistémico (calidad de leads o capacitación). Más créditos no arreglan."
        ),
        "aliados": cohorte,
    }


@app.get("/admin/uso-creditos")
def admin_uso_creditos(db: Session = Depends(get_db)):
    """Reporte completo del uso de créditos por aliado. Útil para auditar el
    sistema: quién acumuló saldo sin gastar, quién cerró ventas, quién está
    activo en el marketplace, etc."""
    aliados = db.query(Aliado).order_by(Aliado.codigo).all()
    filas = []
    total_otorgados = 0
    total_gastados = 0

    for a in aliados:
        txs = db.query(TransaccionCredito).filter(
            TransaccionCredito.aliado_id == a.id
        ).all()
        otorgados = sum(t.delta for t in txs if t.delta > 0)
        gastados  = sum(-t.delta for t in txs if t.delta < 0)
        ventas_confirmadas = db.query(Venta).filter(
            Venta.aliado_id == a.id,
            Venta.confirmada == True,
        ).count()

        # Desglose por motivo (solo créditos OTORGADOS, no descuentos)
        por_motivo = {}
        for t in txs:
            if t.delta <= 0:
                continue
            por_motivo[t.motivo] = por_motivo.get(t.motivo, 0) + t.delta

        total_otorgados += otorgados
        total_gastados  += gastados

        filas.append({
            "codigo":             a.codigo,
            "nombre":             a.nombre,
            "creditos_actuales":  a.creditos or 0,
            "total_otorgados":    otorgados,
            "total_gastados":     gastados,
            "ventas_confirmadas": ventas_confirmadas,
            "ratio_gasto_venta":  round(gastados / max(ventas_confirmadas, 1), 1),
            "por_motivo":         por_motivo,
            "activo":             bool(a.activo),
            "ultimo_login":       a.ultimo_login.isoformat() if getattr(a, "ultimo_login", None) else None,
        })

    return {
        "total_aliados":     len(aliados),
        "total_otorgados":   total_otorgados,
        "total_gastados":    total_gastados,
        "saldo_circulante":  sum(f["creditos_actuales"] for f in filas),
        "aliados":           filas,
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


@app.post("/admin/bolsa-v2")
def cargar_lead_bolsa_v2(lead: LeadBolsaCreateAdv, db: Session = Depends(get_db)):
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
        tier=lead.tier, costo_creditos=lead.costo_creditos,
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
@app.get("/paquetes-creditos")
async def listar_paquetes_creditos():
    """Listado de paquetes de créditos con precio USD fijo + ARS al cambio del día.

    El ARS que devuelve es ORIENTATIVO. El precio real se congela al generar
    la solicitud (POST /aliados/{cod}/solicitar-creditos).
    """
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
@app.post("/aliados/{codigo}/solicitar-creditos")
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
        datos_pago = {
            "tipo":              "pago_usd",
            "metodo":            DATOS_USD["metodo"],
            "destinatario":      DATOS_USD["destinatario"],
            "etiqueta_dest":     DATOS_USD["etiqueta_dest"],
            "red":               DATOS_USD["red"],
            "notas":             DATOS_USD["notas"],
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
@app.get("/aliados/{codigo}/solicitudes-creditos")
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
@app.post("/aliados/{codigo}/solicitudes/{sol_id}/comprobante")
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
@app.get("/admin/solicitudes-creditos")
def admin_listar_solicitudes(estado: str = "pendiente",
                              limit: int = 50,
                              db: Session = Depends(get_db)):
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
@app.post("/admin/solicitudes-creditos/{sol_id}/confirmar")
def admin_confirmar_solicitud(sol_id: int,
                               request: Request,
                               admin: dict = Depends(current_admin_required),
                               db: Session = Depends(get_db)):
    """Marca como confirmada y acredita los créditos al aliado.

    IDEMPOTENTE: si la solicitud ya está confirmada, devuelve OK sin hacer nada.
    Esto protege contra doble click en el botón del admin.
    """
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
@app.post("/admin/solicitudes-creditos/{sol_id}/rechazar")
def admin_rechazar_solicitud(sol_id: int,
                              request: Request,
                              body: schemas.RechazarSolicitudIn,
                              admin: dict = Depends(current_admin_required),
                              db: Session = Depends(get_db)):
    """Marca la solicitud como rechazada. NO acredita créditos."""
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


# --- Scheduler: expirar solicitudes pendientes vencidas ---
def job_expirar_solicitudes_creditos():
    """Corre cada hora. Marca como 'expirada' las solicitudes pendientes cuyo
    expires_at ya pasó. No acredita créditos. Manda email al aliado avisando."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        vencidas = db.query(SolicitudCompraCreditos).filter(
            SolicitudCompraCreditos.estado == "pendiente",
            SolicitudCompraCreditos.expires_at < ahora,
        ).all()
        for s in vencidas:
            s.estado = "expirada"
            s.confirmado_en = ahora
            # Email opcional al aliado
            try:
                a = db.query(Aliado).filter(Aliado.id == s.aliado_id).first()
                if a and a.email:
                    html = f"""
                    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
                      <h2 style="color:#a1a1aa;margin:0 0 8px;">⏱️ Solicitud expirada</h2>
                      <p>Hola <strong>{(a.nombre or '').split()[0]}</strong>,</p>
                      <p>La solicitud <code style="background:#1e293b;padding:2px 6px;border-radius:4px;">{s.codigo_referencia}</code> venció sin recibir transferencia.</p>
                      <p style="color:#a1a1aa;font-size:.9rem;">Si querés comprar el paquete, generá una nueva desde el portal — el monto se recalcula al cambio del día.</p>
                      <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ir al portal →</a>
                    </div>
                    """
                    enviar_email(a.email, f"⏱️ Solicitud {s.codigo_referencia} expirada", html)
            except Exception as e:
                print(f"[EXPIRAR SOLICITUDES] Email a aliado falló: {e}")
        if vencidas:
            print(f"[SOLICITUDES CRÉDITOS] {len(vencidas)} solicitud(es) marcada(s) como expirada(s).")
        db.commit()
    except Exception as e:
        print(f"[SOLICITUDES CRÉDITOS ERROR] {e}")
    finally:
        db.close()


scheduler.add_job(job_expirar_solicitudes_creditos, "interval", hours=1)


# ─── BOLSA: CARGA MASIVA (CSV) ───────────────────────────────────────────────

class LeadBolsaBulkPayload(BaseModel):
    leads: list[LeadBolsaCreateAdv]

@app.post("/admin/bolsa/bulk")
def cargar_leads_bulk(payload: LeadBolsaBulkPayload, db: Session = Depends(get_db)):
    """Inserta una lista de leads de una vez y manda UN solo digest a los aliados.
    Usar en lugar de llamar /admin/bolsa-v2 en loop desde el CSV importer."""
    if not payload.leads:
        raise HTTPException(400, "La lista de leads está vacía.")

    insertados = []
    for lead in payload.leads:
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
            tier=tier, costo_creditos=lead.costo_creditos,
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

    return {"mensaje": f"{len(insertados)} leads cargados.", "total": len(insertados)}


# ─── FINANCIACIÓN / CUOTAS (E) ───────────────────────────────────────────────

@app.get("/cotizador/cuotas")
def simular_cuotas(plan: str, cuotas: int = 1):
    """Simulador de cuotas. Devuelve cuota, total con recargo, recargo pct."""
    if plan not in PLANES:
        raise HTTPException(400, "Plan inválido.")
    if cuotas not in CUOTAS_RECARGO:
        raise HTTPException(400, f"Cuotas inválidas. Opciones: {list(CUOTAS_RECARGO.keys())}")
    base = PLANES[plan]
    recargo_pct = CUOTAS_RECARGO[cuotas]
    total = base * (1 + recargo_pct)
    valor_cuota = total / cuotas
    return {
        "plan": plan,
        "valor_base": base,
        "cuotas": cuotas,
        "recargo_pct": round(recargo_pct * 100, 1),
        "total_financiado": round(total, 2),
        "valor_cuota": round(valor_cuota, 2),
        "opciones": [
            {"cuotas": c, "recargo_pct": round(r * 100, 1),
             "total": round(base * (1 + r), 2),
             "valor_cuota": round(base * (1 + r) / c, 2)}
            for c, r in CUOTAS_RECARGO.items()
        ],
    }


# ─── COMUNIDAD INTERNA (F) ───────────────────────────────────────────────────

@app.get("/comunidad/feed")
def ver_feed_comunidad(limit: int = 30, db: Session = Depends(get_db)):
    """Feed público para todos los aliados (los no ocultos)."""
    posts = db.query(PostComunidad).filter(
        PostComunidad.oculto == False
    ).order_by(
        PostComunidad.fijado.desc(),
        PostComunidad.creado_en.desc()
    ).limit(limit).all()

    resultado = []
    for p in posts:
        coms = db.query(ComentarioComunidad).filter(
            ComentarioComunidad.post_id == p.id
        ).order_by(ComentarioComunidad.creado_en.asc()).all()
        resultado.append({
            "id": p.id,
            "tipo": p.tipo,
            "titulo": p.titulo,
            "cuerpo": p.cuerpo,
            "likes": p.likes or 0,
            "fijado": p.fijado,
            "autor": p.aliado.nombre.split()[0] if p.aliado else "—",
            "autor_codigo": p.aliado.codigo if p.aliado else None,
            "autor_nivel": p.aliado.nivel_calculado if p.aliado else None,
            "fecha": p.creado_en.strftime("%d/%m/%Y %H:%M") if p.creado_en else None,
            "comentarios": [
                {"autor": c.aliado.nombre.split()[0] if c.aliado else "—",
                 "cuerpo": c.cuerpo,
                 "fecha": c.creado_en.strftime("%d/%m/%Y %H:%M") if c.creado_en else None}
                for c in coms
            ],
        })
    return {"posts": resultado}


class PostCreate(BaseModel):
    codigo_aliado: str
    tipo: str = "tip"          # tip | win | pregunta
    titulo: str
    cuerpo: str


@app.post("/comunidad/post")
def crear_post(post: schemas.PostComunidadIn,
                aliado: Aliado = Depends(current_aliado_required),
                db: Session = Depends(get_db)):
    """Publica un post en la comunidad como el aliado autenticado.

    SECURITY: el campo `codigo_aliado` del body se ignora — la autoría
    siempre se toma del JWT para evitar suplantación.
    """
    if post.tipo not in ("tip", "win", "pregunta"):
        raise HTTPException(400, "Tipo inválido.")
    if len(post.titulo.strip()) < 3 or len(post.cuerpo.strip()) < 5:
        raise HTTPException(400, "Título y cuerpo requeridos.")
    p = PostComunidad(
        aliado_id=aliado.id, tipo=post.tipo,
        titulo=post.titulo.strip()[:200], cuerpo=post.cuerpo.strip()[:3000],
    )
    db.add(p); db.commit(); db.refresh(p)
    return {"mensaje": "Post publicado.", "id": p.id}


@app.post("/comunidad/{id}/like")
def like_post(id: int,
              aliado: Aliado = Depends(current_aliado_required),
              db: Session = Depends(get_db)):
    """Like a un post. Requiere aliado autenticado (anti-spam)."""
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.likes = (p.likes or 0) + 1
    db.commit()
    return {"likes": p.likes}


@app.post("/comunidad/{id}/comentario")
def comentar(id: int, com: schemas.ComentarioComunidadIn,
             aliado: Aliado = Depends(current_aliado_required),
             db: Session = Depends(get_db)):
    """Comenta un post como el aliado autenticado.

    SECURITY: `codigo_aliado` del body se ignora — autoría va por JWT.
    """
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    if len(com.cuerpo.strip()) < 2:
        raise HTTPException(400, "Comentario vacío.")
    c = ComentarioComunidad(
        post_id=p.id, aliado_id=aliado.id, cuerpo=com.cuerpo.strip()[:1000]
    )
    db.add(c); db.commit()
    return {"mensaje": "Comentario publicado."}


@app.post("/admin/comunidad/{id}/fijar")
def admin_fijar_post(id: int, fijar: bool = True, db: Session = Depends(get_db)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.fijado = fijar; db.commit()
    return {"mensaje": "Post fijado." if fijar else "Post desfijado."}

@app.get("/auditoria-digital")
def auditoria_digital_redirect():
    from fastapi.responses import FileResponse
    return FileResponse("auditoria-digital.html")

@app.post("/admin/comunidad/{id}/ocultar")
def admin_ocultar_post(id: int, ocultar: bool = True, db: Session = Depends(get_db)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.oculto = ocultar; db.commit()
    return {"mensaje": "Post ocultado." if ocultar else "Post visible de nuevo."}


# ─── PORTAL PÚBLICO POR ALIADO (G lite) ──────────────────────────────────────
# URL pública /p/{ref_code} que muestra una landing mínima con el nombre del
# aliado, los planes y el botón de pago con atribución automática.

from fastapi.responses import HTMLResponse, RedirectResponse

@app.get("/config/usdt")
def config_usdt_publico():
    """Endpoint público que devuelve la configuración de pago en USDT/USDC.
    Usado por el portal de aliados en el cotizador para mostrar instrucciones al cliente.
    No expone datos sensibles (solo la dirección de billetera y la red, que el cliente
    necesita ver para transferir).
    """
    return {
        "activo":    bool(USDT_DIRECCION),
        "direccion": USDT_DIRECCION,
        "red":       USDT_RED or "TRC20",
        "metodo":    os.environ.get("USD_METODO", "USDT"),
    }


@app.get("/alias/{ref_code}")
def alias_redirect(ref_code: str, db: Session = Depends(get_db)):
    """Redirige /alias/{ref_code} → /?ref={ref_code} si el aliado existe.
    Usado por el catch-all de _redirects para que cada aliado tenga una URL
    limpia (ej: avanzadigital.digital/gonzaloasesor) sin configuración manual.
    """
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code, Aliado.activo == True).first()
    if not a:
        raise HTTPException(status_code=404, detail="Aliado no encontrado")
    return RedirectResponse(url=f"https://avanzadigital.digital/?ref={ref_code}", status_code=301)

@app.get("/p/{ref_code}", response_class=HTMLResponse)
def portal_publico_aliado(ref_code: str, db: Session = Depends(get_db)):
    """Landing pública del aliado con su marca/bio y CTA de pago."""
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code, Aliado.activo == True).first()
    if not a or not a.portal_publico_activo:
        return HTMLResponse("<h1>Portal no disponible</h1>", status_code=404)

    titular = a.portal_publico_titular or a.nombre
    bio = a.portal_publico_bio or (f"Asesor digital · {a.ciudad}" if getattr(a, 'ciudad', None) else "Asesor digital — Partner de Avanza Digital")

    # WhatsApp de contacto: del aliado si tiene, si no el de Avanza
    _wa_raw = (a.whatsapp or "").strip().replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    _wa_avanza = "5493424392759"
    wa_contacto = _wa_raw if _wa_raw else _wa_avanza
    wa_titular_encoded = titular.replace(" ", "%20")

    PLAN_DETALLE = {
        "Plan Base": {
            "emoji": "🚀",
            "tagline": "Para arrancar en 7 días",
            "includes": ["Sitio web + cotizador automático", "Formulario de calificación de leads", "Panel de seguimiento básico", "3 meses de soporte técnico"],
            "ideal": "Empresa que quiere su primer canal digital funcionando rápido."
        },
        "Plan Pro": {
            "emoji": "⚡",
            "tagline": "El más elegido por PYMEs industriales",
            "badge": "MÁS POPULAR",
            "includes": ["Todo el Plan Base", "CRM liviano integrado", "Automatizaciones de seguimiento", "Integración WhatsApp Business", "3 meses de soporte técnico"],
            "ideal": "Empresa con equipo de ventas que necesita proceso replicable."
        },
        "Plan Industrial": {
            "emoji": "🏭",
            "tagline": "Sistema completo de adquisición B2B",
            "includes": ["Todo el Plan Pro", "Landing de empresa personalizada", "Cotizador por rubro y producto", "Dashboard de métricas avanzado", "Secuencias de email automatizadas", "3 meses de soporte técnico"],
            "ideal": "Empresa que quiere ser el referente digital de su rubro."
        },
        "Estrategico 360": {
            "emoji": "🎯",
            "tagline": "Transformación comercial integral",
            "includes": ["Todo el Plan Industrial", "Auditoría comercial inicial", "Estrategia de contenidos B2B", "Capacitación del equipo comercial", "Revisiones mensuales 90 días", "Soporte prioritario 6 meses"],
            "ideal": "Empresa que quiere rediseñar su área comercial completa."
        },
    }
    planes_html = ""
    for nombre_plan, precio in PLANES.items():
        det = PLAN_DETALLE.get(nombre_plan, {})
        emoji = det.get("emoji", "📦")
        tagline = det.get("tagline", "")
        badge_html = f'<span style="background:rgba(74,222,128,0.15);color:#4ade80;border:1px solid rgba(74,222,128,0.3);padding:3px 10px;border-radius:20px;font-size:.68rem;font-weight:800;letter-spacing:1px;text-transform:uppercase;margin-left:8px;">{det["badge"]}</span>' if det.get("badge") else ""
        includes_items = "".join(f'<li style="display:flex;gap:8px;align-items:flex-start;font-size:.82rem;color:#a1a1aa;margin-bottom:6px;"><span style="color:#4ade80;flex-shrink:0;">✓</span>{item}</li>' for item in det.get("includes", []))
        ideal = det.get("ideal", "")
        wa_plan_encoded = nombre_plan.replace(" ", "%20")
        _ideal_html = ('<p style="font-size:.78rem;color:#52525b;margin-bottom:16px;font-style:italic;">' + ideal + '</p>') if ideal else ''
        planes_html += f"""
        <div class="plan-card">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;margin-bottom:14px;">
            <div>
              <div style="font-size:1.5rem;margin-bottom:6px;">{emoji}</div>
              <h3 style="font-size:1.05rem;font-weight:900;margin-bottom:4px;">{nombre_plan}{badge_html}</h3>
              <p style="font-size:.8rem;color:#71717a;">{tagline}</p>
            </div>
            <div style="text-align:right;">
              <div style="font-size:1.8rem;font-weight:900;color:#e2e8f0;">USD {int(precio)}</div>
              <div style="font-size:.72rem;color:#71717a;">pago único</div>
            </div>
          </div>
          <ul style="list-style:none;padding:0;margin:0 0 16px;">{includes_items}</ul>
          {_ideal_html}
          <div style="display:flex;gap:10px;flex-wrap:wrap;">
            <button onclick="abrirModal(\'{nombre_plan}\',\'{ref_code}\')"
               style="flex:1;min-width:140px;padding:14px;background:#3b82f6;color:#fff;border-radius:8px;border:none;cursor:pointer;font-weight:800;font-size:.9rem;">
              Contratar {nombre_plan} →
            </button>
            <a href="https://wa.me/{wa_contacto}?text=Hola%20{wa_titular_encoded}%2C%20me%20interesa%20el%20{wa_plan_encoded}%20de%20Avanza%20Digital.%20%C2%BFPodemos%20hablar%3F"
               target="_blank"
               style="flex:1;min-width:140px;padding:14px;background:rgba(37,211,102,0.12);color:#25d366;border:1px solid rgba(37,211,102,0.3);border-radius:8px;cursor:pointer;font-weight:800;font-size:.9rem;text-decoration:none;text-align:center;display:inline-flex;align-items:center;justify-content:center;gap:6px;">
              💬 Consultar
            </a>
          </div>
        </div>
        """

    usdt_activo = bool(USDT_DIRECCION or TRON_MNEMONIC)

    usdt_activo = bool(USDT_DIRECCION)
    _btn_usd = (
        '<button class="moneda-btn" id="opt-usd" onclick="seleccionarMoneda(\'usd\')">'
        '<div class="icon">💵</div><div class="label">USD</div>'
        '<div class="sublabel">Transferencia</div></button>'
    )
    _btn_usdt = (
        '<button class="moneda-btn usdt" id="opt-usdt" onclick="seleccionarMoneda(\'usdt\')">'
        '<div class="icon">🪙</div><div class="label">USDT / USDC</div>'
        f'<div class="sublabel">{USDT_RED or "TRC20"}</div></button>'
        if usdt_activo else
        '<button class="moneda-btn" style="opacity:.35;cursor:not-allowed;" disabled>'
        '<div class="icon">🪙</div><div class="label">USDT no disponible</div>'
        '<div class="sublabel">Próximamente</div></button>'
    )
    # Precio de cada plan para mostrarlo en el step de USDT
    _plan_precios_js = ", ".join(f'"{k}": {int(v)}' for k, v in PLANES.items())
    _usdt_dir_js = USDT_DIRECCION.replace("'", "\\'")
    _usdt_red_js = (USDT_RED or "TRC20").replace("'", "\\'")

    html = f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{titular} · Sistema de ventas para tu empresa · Avanza Digital</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;line-height:1.6;}}
.wrap{{max-width:680px;margin:0 auto;padding:0 20px 60px;}}
.nav{{display:flex;align-items:center;justify-content:space-between;padding:18px 20px;border-bottom:1px solid rgba(255,255,255,0.06);max-width:680px;margin:0 auto;}}
.nav-logo{{font-size:.85rem;font-weight:700;color:#a1a1aa;text-decoration:none;}}
.nav-logo span{{color:#3b82f6;}}
.asesor-bar{{background:rgba(59,130,246,0.08);border-bottom:1px solid rgba(59,130,246,0.15);padding:10px 20px;text-align:center;font-size:.78rem;color:#93c5fd;font-weight:600;}}
.hero{{padding:52px 0 40px;text-align:center;}}
.hero-badge{{display:inline-block;background:rgba(74,222,128,0.12);color:#4ade80;padding:5px 14px;border-radius:20px;font-size:.72rem;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:20px;border:1px solid rgba(74,222,128,0.2);}}
.hero h1{{font-size:clamp(1.8rem,5vw,2.6rem);font-weight:900;line-height:1.15;margin-bottom:16px;}}
.hero h1 span{{color:#3b82f6;}}
.hero-sub{{color:#a1a1aa;font-size:1.05rem;max-width:520px;margin:0 auto 32px;}}
.hero-cta{{display:inline-block;padding:16px 32px;background:#3b82f6;color:#fff;border-radius:10px;font-weight:800;font-size:1rem;text-decoration:none;transition:background .2s;border:none;cursor:pointer;}}
.hero-cta:hover{{background:#2563eb;}}
.hero-social{{margin-top:20px;font-size:.8rem;color:#71717a;}}
.hero-social span{{color:#4ade80;font-weight:700;}}
.section{{padding:40px 0;}}
.section-label{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:#71717a;margin-bottom:12px;}}
.section h2{{font-size:1.5rem;font-weight:900;margin-bottom:16px;}}
.problem-list{{list-style:none;padding:0;}}
.problem-list li{{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:.92rem;color:#a1a1aa;}}
.problem-list li:last-child{{border-bottom:none;}}
.problem-list li::before{{content:"\u2717";color:#ef4444;font-weight:900;flex-shrink:0;margin-top:2px;}}
.benefit-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:20px;}}
@media(max-width:480px){{.benefit-grid{{grid-template-columns:1fr;}}}}
.benefit-card{{background:#0f0f0f;border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:18px 16px;}}
.benefit-icon{{font-size:1.4rem;margin-bottom:8px;}}
.benefit-title{{font-size:.88rem;font-weight:800;margin-bottom:4px;}}
.benefit-desc{{font-size:.78rem;color:#71717a;line-height:1.5;}}
.caso-card{{background:#0a0a0a;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin-bottom:12px;}}
.caso-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;flex-wrap:wrap;gap:8px;}}
.caso-empresa{{font-size:.88rem;font-weight:800;}}
.caso-rubro{{font-size:.7rem;color:#71717a;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-top:2px;}}
.caso-badge{{background:rgba(74,222,128,0.1);color:#4ade80;padding:4px 10px;border-radius:20px;font-size:.72rem;font-weight:700;white-space:nowrap;}}
.caso-resultado{{font-size:.85rem;color:#a1a1aa;line-height:1.5;}}
.caso-resultado strong{{color:#e2e8f0;}}
.divider{{border:none;border-top:1px solid rgba(255,255,255,0.06);margin:8px 0;}}
.planes-title{{font-size:1.4rem;font-weight:900;margin-bottom:6px;}}
.planes-sub{{color:#a1a1aa;font-size:.88rem;margin-bottom:24px;}}
.plan-card{{background:#0f0f0f;border:1px solid rgba(255,255,255,0.08);border-radius:14px;padding:22px 20px;margin-bottom:12px;transition:border-color .2s;}}
.plan-card:hover{{border-color:rgba(59,130,246,0.4);}}
.garantia-box{{background:rgba(74,222,128,0.05);border:1px solid rgba(74,222,128,0.15);border-radius:12px;padding:20px;text-align:center;margin-top:20px;}}
.garantia-box h3{{font-size:1rem;font-weight:800;color:#4ade80;margin-bottom:6px;}}
.garantia-box p{{font-size:.83rem;color:#a1a1aa;}}
.footer{{margin-top:48px;text-align:center;color:#3f3f46;font-size:.75rem;}}
.footer a{{color:#52525b;}}
.asesor-intro{{display:flex;gap:16px;align-items:flex-start;background:rgba(59,130,246,0.06);border:1px solid rgba(59,130,246,0.15);border-radius:14px;padding:22px 20px;margin-bottom:36px;}}
.asesor-avatar{{width:54px;height:54px;border-radius:50%;background:linear-gradient(135deg,#3b82f6,#1d4ed8);display:flex;align-items:center;justify-content:center;font-size:1.4rem;flex-shrink:0;}}
.asesor-intro-name{{font-size:1rem;font-weight:900;margin-bottom:4px;}}
.asesor-intro-badge{{font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#93c5fd;margin-bottom:6px;}}
.asesor-intro-bio{{font-size:.82rem;color:#a1a1aa;line-height:1.55;}}
#modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:100;align-items:center;justify-content:center;padding:16px;}}
.modal-box{{background:#111;border:1px solid #2a2a2a;border-radius:16px;padding:28px;width:100%;max-width:420px;}}
.step{{display:none;}}
.step.active{{display:block;}}
.moneda-options{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin:16px 0;}}
@media(max-width:420px){{.moneda-options{{grid-template-columns:1fr;}}}}
.moneda-btn{{padding:16px 12px;border-radius:10px;border:2px solid #2a2a2a;background:#1a1a1a;color:#e2e8f0;cursor:pointer;text-align:center;transition:all .2s;font-family:Inter,sans-serif;}}
.moneda-btn:hover{{border-color:#3b82f6;background:rgba(59,130,246,0.08);}}
.moneda-btn.selected{{border-color:#3b82f6;background:rgba(59,130,246,0.12);}}
.moneda-btn .icon{{font-size:1.6rem;margin-bottom:6px;}}
.moneda-btn .label{{font-weight:800;font-size:.9rem;}}
.moneda-btn .sublabel{{font-size:.72rem;color:#a1a1aa;margin-top:2px;}}
.moneda-btn.usdt .icon{{color:#26a17b;}}
.moneda-btn.usdt .icon{{color:#26a17b;}}
.moneda-btn.usdt.selected{{border-color:#26a17b;background:rgba(38,161,123,0.12);}}
.btn-cancel{{flex:1;padding:12px;border-radius:8px;border:1px solid #444;background:transparent;color:#aaa;cursor:pointer;font-size:.95rem;font-family:Inter,sans-serif;}}
.btn-primary{{flex:1;padding:12px;border-radius:8px;border:none;background:#3b82f6;color:#fff;cursor:pointer;font-weight:700;font-size:.95rem;font-family:Inter,sans-serif;}}
.btn-primary:disabled{{opacity:.6;cursor:not-allowed;}}
input[type=text]{{width:100%;padding:12px;border-radius:8px;border:1px solid #444;background:#1a1a1a;color:#fff;font-size:1rem;font-family:Inter,sans-serif;}}
input[type=text]:focus{{outline:none;border-color:#3b82f6;}}
</style></head><body>
<div class="asesor-bar">
  Estás siendo atendido por <strong style="color:#e2e8f0;">{titular}</strong> · Partner certificado de Avanza Digital
</div>
<nav class="nav">
  <a class="nav-logo" href="https://avanzadigital.digital">Avanza<span>Digital</span></a>
  <a href="#planes" class="hero-cta" style="padding:10px 20px;font-size:.85rem;">Ver planes →</a>
</nav>
<div class="wrap">
  <section class="asesor-intro">
    <div class="asesor-avatar">👤</div>
    <div>
      <div class="asesor-intro-badge">Partner certificado · Avanza Digital</div>
      <div class="asesor-intro-name">{titular}</div>
      <div class="asesor-intro-bio">{bio}</div>
    </div>
  </section>
  <section class="hero">
    <div class="hero-badge">Sistema de ventas B2B</div>
    <h1>Tu empresa merece <span>conseguir clientes</span> de forma sistemática</h1>
    <p class="hero-sub">Implementamos tu sistema digital de ventas en 30 días. Más presupuestos, más respuestas rápidas, más clientes. Sin depender de la suerte ni del boca a boca.</p>
    <a href="#planes" class="hero-cta">Elegir mi plan →</a>
    <p class="hero-social">Más de <span>40 PYMEs industriales</span> ya tienen su sistema funcionando</p>
  </section>

  <section class="section">
    <div class="section-label">El problema</div>
    <h2>¿Te suena alguna de estas situaciones?</h2>
    <ul class="problem-list">
      <li>Los presupuestos tardan días en salir y el cliente ya compró en otro lado</li>
      <li>Dependés del boca a boca — no tenés forma de conseguir clientes nuevos sistemáticamente</li>
      <li>Tu sitio web existe, pero no genera ninguna consulta real</li>
      <li>No sabés cuántos clientes potenciales perdés por mes por responder tarde</li>
      <li>Cada vendedor usa su propio método y no hay proceso replicable</li>
    </ul>
  </section>

  <hr class="divider">

  <section class="section">
    <div class="section-label">La solución</div>
    <h2>Un sistema que trabaja aunque vos no estés</h2>
    <div class="benefit-grid">
      <div class="benefit-card">
        <div class="benefit-icon">⚡</div>
        <div class="benefit-title">Respuestas en minutos</div>
        <div class="benefit-desc">Cotizador automático y formularios inteligentes que califican y responden consultas sin intervención humana.</div>
      </div>
      <div class="benefit-card">
        <div class="benefit-icon">🎯</div>
        <div class="benefit-title">Leads que cierran</div>
        <div class="benefit-desc">Cada consulta llega con el contexto completo: rubro, producto, urgencia y contacto directo al responsable.</div>
      </div>
      <div class="benefit-card">
        <div class="benefit-icon">📊</div>
        <div class="benefit-title">Métricas en tiempo real</div>
        <div class="benefit-desc">CRM liviano integrado. Sabés exactamente de dónde vienen tus clientes y cuánto vale cada canal.</div>
      </div>
      <div class="benefit-card">
        <div class="benefit-icon">🔁</div>
        <div class="benefit-title">Sistema replicable</div>
        <div class="benefit-desc">Proceso documentado que cualquier vendedor puede seguir. Dejás de depender de una sola persona.</div>
      </div>
    </div>
  </section>

  <hr class="divider">

  <section class="section">
    <div class="section-label">Resultados reales</div>
    <h2>Lo que lograron empresas como la tuya</h2>
    <div class="caso-card">
      <div class="caso-header">
        <div><div class="caso-empresa">Metalúrgica Balconi · Rafaela</div><div class="caso-rubro">Fabricación de estructuras</div></div>
        <div class="caso-badge">+47% conversión</div>
      </div>
      <p class="caso-resultado">Tenían el mismo problema de presupuestos que se perdían. En <strong>21 días</strong> implementaron el sistema. Primer trimestre: <strong>3 contratos nuevos</strong> desde canales digitales.</p>
    </div>
    <div class="caso-card">
      <div class="caso-header">
        <div><div class="caso-empresa">Transportes Oñate · Rosario</div><div class="caso-rubro">Logística y transporte</div></div>
        <div class="caso-badge">31hs → 4hs</div>
      </div>
      <p class="caso-resultado">Pasaron de tardar <strong>31 horas</strong> en responder cotizaciones a <strong>menos de 4 horas</strong>. Cerraron <strong>3 contratos nuevos</strong> el primer mes.</p>
    </div>
    <div class="caso-card">
      <div class="caso-header">
        <div><div class="caso-empresa">Soluciones Técnicas del Litoral · Paraná</div><div class="caso-rubro">Servicios técnicos industriales</div></div>
        <div class="caso-badge">USD 8.400 primer trimestre</div>
      </div>
      <p class="caso-resultado">En <strong>7 días</strong> activaron el Plan Base. En 20 días les entró la primera consulta digital. Primer trimestre: <strong>USD 8.400 en contratos nuevos</strong>.</p>
    </div>
  </section>

  <hr class="divider">

  <section class="section" id="planes">
    <div class="section-label">Planes</div>
    <p class="planes-title">Elegí el plan que le va a tu empresa</p>
    <p class="planes-sub">Implementación completa en 30 días. Pago único, sin costos ocultos.</p>
    {planes_html}
    <div class="garantia-box">
      <h3>✓ Garantía de 3 meses incluida</h3>
      <p>Todos los planes incluyen soporte técnico prioritario los primeros 90 días. Si algo no funciona, lo resolvemos nosotros.</p>
    </div>
  </section>

  <div class="footer">
    <p>Atendido por <strong style="color:#52525b;">{titular}</strong> · Partner certificado de Avanza Digital</p>
    <p style="margin-top:6px;">Tu pago queda atribuido automáticamente a tu asesor.<br>
    <a href="https://avanzadigital.digital" target="_blank">avanzadigital.digital</a> ·
    <a href="https://avanzadigital.digital/politica.html" target="_blank">Privacidad</a></p>
  </div>
</div>

<div id="modal-overlay" onclick="onOverlayClick(event)" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:100;align-items:center;justify-content:center;padding:16px;">
  <div class="modal-box" onclick="event.stopPropagation()">
    <div id="step-nombre" class="step active">
      <h3 style="margin:0 0 6px;font-size:1.1rem;font-weight:800;">Completá tus datos para continuar</h3>
      <p style="color:#a1a1aa;font-size:.85rem;margin:0 0 18px;">Te enviaremos el formulario de inicio del proyecto apenas se confirme el pago.</p>
      <label style="display:block;font-size:.75rem;color:#a1a1aa;margin-bottom:4px;font-weight:700;">Nombre completo *</label>
      <input id="modal-nombre" type="text" placeholder="Tu nombre completo" style="margin-bottom:12px;" onkeydown="if(event.key==='Enter') document.getElementById('modal-email').focus()">
      <label style="display:block;font-size:.75rem;color:#a1a1aa;margin-bottom:4px;font-weight:700;">Email *</label>
      <input id="modal-email" type="text" placeholder="tu@empresa.com" style="margin-bottom:12px;" onkeydown="if(event.key==='Enter') document.getElementById('modal-whatsapp').focus()">
      <label style="display:block;font-size:.75rem;color:#a1a1aa;margin-bottom:4px;font-weight:700;">WhatsApp (con código de país) *</label>
      <input id="modal-whatsapp" type="text" placeholder="+5491155556666" onkeydown="if(event.key==='Enter') irAPaso2()">
      <div style="display:flex;gap:10px;margin-top:18px;">
        <button class="btn-cancel" onclick="cerrarModal()">Cancelar</button>
        <button class="btn-primary" onclick="irAPaso2()">Siguiente →</button>
      </div>
    </div>
    <div id="step-moneda" class="step">
      <h3 style="margin:0 0 6px;font-size:1.1rem;font-weight:800;">¿Cómo querés pagar?</h3>
      <p style="color:#a1a1aa;font-size:.85rem;margin:0 0 4px;">Elegí tu moneda y método de pago.</p>
      <div class="moneda-options">
        <button class="moneda-btn ars selected" id="opt-ars" onclick="seleccionarMoneda('ars')">
          <div class="icon">🏦</div><div class="label">Pesos ARS</div><div class="sublabel">MercadoPago</div>
        </button>
        {_btn_usd}
        {_btn_usdt}
      </div>
      <div id="moneda-info" style="background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.2);border-radius:8px;padding:10px 14px;font-size:.82rem;color:#93c5fd;margin-bottom:14px;">
        🏦 Pagarás en <strong>pesos argentinos</strong> a través de <strong>MercadoPago</strong>.
      </div>
      <div style="display:flex;gap:10px;">
        <button class="btn-cancel" onclick="volverAPaso1()">← Volver</button>
        <button class="btn-primary" id="btn-pagar" onclick="confirmarContratacion()">Ir a pagar →</button>
      </div>
    </div>
    <div id="step-procesando" class="step" style="text-align:center;padding:12px 0;">
      <div style="font-size:2rem;margin-bottom:12px;">⏳</div>
      <p style="font-weight:700;font-size:1rem;margin:0 0 6px;">Generando tu link de pago…</p>
      <p style="color:#a1a1aa;font-size:.85rem;margin:0;">Serás redirigido en segundos.</p>
    </div>
    <div id="step-usdt" class="step" style="padding:4px 0;">
      <h3 style="margin:0 0 6px;font-size:1.05rem;font-weight:800;">🪙 Instrucciones de pago en USDT/USDC</h3>
      <p style="color:#a1a1aa;font-size:.82rem;margin:0 0 16px;">Realizá la transferencia y avisanos por WhatsApp para confirmar.</p>
      <div style="background:#1a1a1a;border:1px solid rgba(38,161,123,0.35);border-radius:10px;padding:14px;margin-bottom:14px;">
        <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap;">
          <span style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;min-width:56px;">Red</span>
          <span id="modal-usdt-red" style="color:#26a17b;font-weight:800;font-size:.9rem;background:rgba(38,161,123,0.1);border:1px solid rgba(38,161,123,0.3);padding:3px 10px;border-radius:20px;">{_usdt_red_js}</span>
        </div>
        <div style="margin-bottom:10px;">
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Monto exacto</div>
          <div id="modal-usdt-monto" style="font-size:1.4rem;font-weight:900;color:#e2e8f0;">USD —</div>
        </div>
        <div>
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Dirección de billetera</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <input type="text" id="modal-usdt-dir" value="{_usdt_dir_js}" readonly style="flex:1;min-width:140px;font-family:monospace;font-size:.75rem;background:#111;border:1px solid rgba(38,161,123,0.2);border-radius:6px;padding:9px;">
            <button onclick="copiarDirUSDT()" style="background:rgba(38,161,123,0.15);color:#26a17b;border:1px solid rgba(38,161,123,0.35);border-radius:6px;padding:0 12px;height:36px;font-weight:700;cursor:pointer;font-size:.8rem;white-space:nowrap;">
              <i class="fa-solid fa-copy"></i> Copiar
            </button>
          </div>
        </div>
      </div>
      <p style="font-size:.78rem;color:#71717a;margin:0 0 14px;line-height:1.5;padding:10px;background:rgba(0,0,0,0.4);border-radius:8px;border-left:2px solid rgba(38,161,123,0.5);">
        Enviá el monto exacto y avisale a <strong style="color:#e2e8f0;">{titular}</strong> por WhatsApp en cuanto realices la transferencia. Tu plan se activa en cuanto Avanza confirma el pago (hasta 24hs hábiles).
      </p>
      <div style="display:flex;gap:10px;">
        <button class="btn-cancel" onclick="volverAPaso1()">← Volver</button>
        <a href="https://wa.me/{wa_contacto}?text=Hola%2C+realic%C3%A9+la+transferencia+en+USDT+para+el+%7B%7Bplan%7D%7D" id="modal-usdt-wa-btn"
           target="_blank"
           style="flex:1;padding:12px;border-radius:8px;border:none;background:#25d366;color:#fff;cursor:pointer;font-weight:700;font-size:.95rem;text-decoration:none;text-align:center;display:flex;align-items:center;justify-content:center;gap:6px;">
          <i class="fa-brands fa-whatsapp"></i> Confirmar por WhatsApp
        </a>
      </div>
    </div>
  </div>
</div>

<script>
  const _PLAN_PRECIOS = {{{_plan_precios_js}}};
  let _plan = \'\', _ref = \'\', _moneda = \'ars\';
  function abrirModal(plan, ref) {{
    _plan = plan; _ref = ref; _moneda = \'ars\';
    mostrarPaso(\'step-nombre\');
    document.getElementById(\'modal-overlay\').style.display = \'flex\';
    setTimeout(() => document.getElementById(\'modal-nombre\').focus(), 60);
  }}
  function cerrarModal() {{
    document.getElementById(\'modal-overlay\').style.display = \'none\';
    [\'modal-nombre\',\'modal-email\',\'modal-whatsapp\'].forEach(id => {{
      const el = document.getElementById(id);
      if (el) {{ el.value = \'\'; el.style.borderColor = \'#444\'; }}
    }});
  }}
  function onOverlayClick(e) {{
    if (e.target === document.getElementById(\'modal-overlay\')) cerrarModal();
  }}
  function mostrarPaso(id) {{
    [\'step-nombre\',\'step-moneda\',\'step-procesando\',\'step-usdt\'].forEach(s => {{
      document.getElementById(s).classList.remove(\'active\');
    }});
    document.getElementById(id).classList.add(\'active\');
  }}
  function _marcarError(id) {{ const el = document.getElementById(id); el.style.borderColor = \'#ef4444\'; el.focus(); }}
  function _esEmailValido(s) {{ return /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(s); }}
  function _esWhatsappValido(s) {{ const limpio = s.replace(/[\\s\\-\\(\\)]/g, \'\'); return /^\\+?[0-9]{{8,15}}$/.test(limpio); }}
  function irAPaso2() {{
    const nombre = document.getElementById(\'modal-nombre\').value.trim();
    const email = document.getElementById(\'modal-email\').value.trim();
    const whatsapp = document.getElementById(\'modal-whatsapp\').value.trim();
    [\'modal-nombre\',\'modal-email\',\'modal-whatsapp\'].forEach(id => {{ document.getElementById(id).style.borderColor = \'#444\'; }});
    if (!nombre) {{ _marcarError(\'modal-nombre\'); return; }}
    if (!_esEmailValido(email)) {{ _marcarError(\'modal-email\'); return; }}
    if (!_esWhatsappValido(whatsapp)) {{ _marcarError(\'modal-whatsapp\'); return; }}
    mostrarPaso(\'step-moneda\');
  }}
  function volverAPaso1() {{ mostrarPaso(\'step-nombre\'); setTimeout(() => document.getElementById(\'modal-nombre\').focus(), 60); }}
  function seleccionarMoneda(m) {{
    _moneda = m;
    document.getElementById(\'opt-ars\').classList.toggle(\'selected\', m === \'ars\');
    const optUsd = document.getElementById(\'opt-usd\');
    if (optUsd) optUsd.classList.toggle(\'selected\', m === \'usd\');
    const optUsdt = document.getElementById(\'opt-usdt\');
    if (optUsdt) optUsdt.classList.toggle(\'selected\', m === \'usdt\');
    const info = document.getElementById(\'moneda-info\');
    if (m === \'ars\') {{
      info.style.background = \'rgba(59,130,246,0.08)\'; info.style.borderColor = \'rgba(59,130,246,0.2)\'; info.style.color = \'#93c5fd\';
      info.innerHTML = \'🏦 Pagarás en <strong>pesos argentinos</strong> a través de <strong>MercadoPago</strong>.\';
    }} else if (m === \'usd\') {{
      info.style.background = \'rgba(0,156,222,0.08)\'; info.style.borderColor = \'rgba(0,156,222,0.25)\'; info.style.color = \'#7dd3fc\';
      info.innerHTML = \'🪙 Pagarás en <strong>USDT</strong> (red <strong>TRC20</strong>). Te damos una dirección única para tu orden.\';
    }} else {{
      info.style.background = \'rgba(38,161,123,0.08)\'; info.style.borderColor = \'rgba(38,161,123,0.25)\'; info.style.color = \'#6ee7b7\';
      info.innerHTML = \'🪙 Pagarás en <strong>USDT/USDC</strong>. Transferencia directa a billetera cripto (confirmación manual en 24hs hábiles).\';
    }}
  }}
  function copiarDirUSDT() {{
    const el = document.getElementById(\'modal-usdt-dir\');
    if (!el) return;
    navigator.clipboard.writeText(el.value).then(() => {{
      el.style.borderColor = \'#26a17b\';
      setTimeout(() => el.style.borderColor = \'rgba(38,161,123,0.2)\', 1500);
    }}).catch(() => {{
      el.select(); document.execCommand(\'copy\');
    }});
  }}
  async function confirmarContratacion() {{
    const nombre = document.getElementById(\'modal-nombre\').value.trim();
    const email = document.getElementById(\'modal-email\').value.trim();
    const whatsapp = document.getElementById(\'modal-whatsapp\').value.trim();
    if (!nombre || !email || !whatsapp) {{ volverAPaso1(); return; }}

    // Flujo USDT: mostrar instrucciones de transferencia sin checkout automático
    if (_moneda === \'usdt\') {{
      const precio = _PLAN_PRECIOS[_plan] || \'—\';
      document.getElementById(\'modal-usdt-monto\').textContent = `USD ${{precio}}`;
      // Actualizar link WA con el plan y monto
      const waBtn = document.getElementById(\'modal-usdt-wa-btn\');
      if (waBtn) {{
        const texto = encodeURIComponent(`Hola, realicé la transferencia de USD ${{precio}} en USDT para el ${{_plan}} de Avanza Digital. Mi nombre: ${{nombre}}, email: ${{email}}`);
        waBtn.href = `https://wa.me/{wa_contacto}?text=${{texto}}`;
      }}
      mostrarPaso(\'step-usdt\');
      return;
    }}

    mostrarPaso(\'step-procesando\');
    try {{
      const url = `/checkout/crear?plan=${{encodeURIComponent(_plan)}}&ref_code=${{_ref}}&nombre_cliente=${{encodeURIComponent(nombre)}}&moneda=${{_moneda}}&cliente_email=${{encodeURIComponent(email)}}&cliente_whatsapp=${{encodeURIComponent(whatsapp)}}`;
      const res = await fetch(url, {{method:\'POST\'}});
      let data;
      try {{ data = await res.json(); }} catch(_) {{ data = {{}}; }}
      if (!res.ok) {{
        alert(data.detail || \'Error al generar el link de pago. Intentá de nuevo.\');
        mostrarPaso(\'step-moneda\');
      }} else if (data.checkout_url) {{
        window.location.href = data.checkout_url;
      }} else {{
        alert(\'Error al generar el link de pago. Intentá de nuevo.\');
        mostrarPaso(\'step-moneda\');
      }}
    }} catch(e) {{
      alert(\'Error de conexión. Revisá tu conexión e intentá de nuevo.\');
      mostrarPaso(\'step-moneda\');
    }}
  }}
</script>
</body></html>"""

    return HTMLResponse(html)


@app.patch("/aliados/{codigo}/portal-publico")
def configurar_portal_publico(codigo: str,
                              body: schemas.ActualizarPerfilIn | None = Body(default=None),
                              activo: bool = True,
                              titular: str = "",
                              bio: str = "",
                              db: Session = Depends(get_db),
                              _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    if body is not None:
        if body.portal_publico_activo is not None:
            a.portal_publico_activo = body.portal_publico_activo
        if body.portal_publico_titular is not None:
            a.portal_publico_titular = body.portal_publico_titular[:120] or None
        if body.portal_publico_bio is not None:
            a.portal_publico_bio = body.portal_publico_bio[:500] or None
    else:
        a.portal_publico_activo = activo
        if titular: a.portal_publico_titular = titular[:120]
        if bio:     a.portal_publico_bio = bio[:500]
    db.commit()
    return {
        "mensaje": "Portal público actualizado.",
        "url": f"/p/{a.ref_code}",
        "titular": a.portal_publico_titular,
        "bio": a.portal_publico_bio,
        "activo": a.portal_publico_activo,
    }

# ═════════════════════════════════════════════════════════════════════════════
# v1.4 — ENDPOINTS NUEVOS (CBU, comisiones, academia, admin)
# ═════════════════════════════════════════════════════════════════════════════

# ─── CBU / ALIAS DEL ALIADO (spec §11) ───────────────────────────────────────

class PerfilAliadoUpdate(BaseModel):
    cbu_alias: str | None = None

@app.patch("/aliado/perfil")
def actualizar_perfil_aliado(payload: PerfilAliadoUpdate,
                              aliado: Aliado = Depends(current_aliado_required),
                              db: Session = Depends(get_db)):
    """Actualiza el CBU/alias del aliado autenticado.

    SECURITY: Toma el aliado del JWT, ya NO acepta `?codigo=` como parámetro
    (era una via de hijack del CBU para redirigir comisiones).
    """
    if payload.cbu_alias is not None:
        aliado.cbu_alias = payload.cbu_alias.strip()[:120] or None
    db.commit()
    return {
        "mensaje": "Perfil actualizado.",
        "cbu_alias": aliado.cbu_alias,
    }


@app.patch("/aliados/{codigo}/cbu")
def actualizar_cbu(codigo: str,
                   body: schemas.ActualizarCBUIn | None = Body(default=None),
                   cbu_alias: str = "",
                   db: Session = Depends(get_db),
                   _owner=Depends(verify_ownership_dep)):
    """Alias alternativo para actualizar CBU. Acepta body o query (compat).
    Protegido con ownership: solo el dueño del JWT (o un admin) puede tocar
    este aliado. CRÍTICO — afecta a dónde se cobran las comisiones."""
    a = _get_aliado(codigo, db)
    nuevo = body.cbu_alias if body is not None else cbu_alias
    a.cbu_alias = (nuevo or "").strip()[:120] or None
    db.commit()
    return {"mensaje": "CBU/alias guardado.", "cbu_alias": a.cbu_alias}


# ─── PANEL DE COMISIONES POR ALIADO (spec §9, §16) ──────────────────────────

def _comision_row(c: Comision, cliente_fallback: str = ""):
    return {
        "id": c.id,
        "cliente": c.nombre_cliente or cliente_fallback or "—",
        "plan": c.plan,
        "monto_plan_usd": c.monto_plan_usd,
        "comision_usd": c.comision_usd,
        "comision_pct": c.comision_pct,
        "estado": c.estado,
        "processor": c.processor,
        "fecha_pago": c.fecha_pago.isoformat() if c.fecha_pago else None,
        "fecha_abono": c.fecha_abono.isoformat() if c.fecha_abono else None,
    }


@app.get("/aliado/comisiones")
def listar_comisiones_por_token(aliado: Aliado = Depends(current_aliado_required),
                                 db: Session = Depends(get_db)):
    """Comisiones del aliado autenticado.

    SECURITY (rev): la versión anterior tomaba el código directamente del header
    `Authorization: Bearer <codigo>` SIN validar firma — eso permitía a cualquiera
    listar comisiones ajenas con solo conocer el código. Ahora valida JWT firmado
    con HS256 contra JWT_SECRET y resuelve el aliado del subject del token.
    """
    comisiones = db.query(Comision).filter(Comision.aliado_id == aliado.id)\
        .order_by(Comision.fecha_pago.desc().nullslast() if hasattr(Comision.fecha_pago, "desc") else Comision.id.desc()).all()
    return [_comision_row(c) for c in comisiones]


@app.get("/aliados/{codigo}/comisiones")
def listar_comisiones_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Devuelve todas las comisiones del aliado (pendientes + abonadas)
    con totales agregados. Es la vista del panel de comisiones del portal.

    v1.5 — Suma:
      * mrr_recurrente_usd: USD/mes que está cobrando ahora (10% sobre planes
        de continuidad activos de sus clientes).
      * clientes_continuidad_activos: detalle por cliente activo.
    """
    a = _get_aliado(codigo, db)
    comisiones = db.query(Comision).filter(Comision.aliado_id == a.id)\
        .order_by(Comision.fecha_pago.desc().nullslast() if hasattr(Comision.fecha_pago, "desc") else Comision.id.desc()).all()

    items = [_comision_row(c) for c in comisiones]
    total_pendiente = round(sum(c.comision_usd for c in comisiones if c.estado == "pendiente"), 2)
    total_abonado   = round(sum(c.comision_usd for c in comisiones if c.estado == "abonada"), 2)

    # MRR recurrente — planes de continuidad activos de este aliado
    activos = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.aliado_id == a.id,
        PlanContinuidadActivo.fecha_baja.is_(None),
    ).order_by(PlanContinuidadActivo.fecha_alta.desc()).all()

    mrr = round(sum(p.comision_mensual_usd for p in activos), 2)
    clientes_continuidad = [{
        "id": p.id,
        "cliente": p.nombre_cliente,
        "plan": p.plan_continuidad,
        "precio_mensual": round(float(p.precio_mensual_usd), 2),
        "comision_mensual": p.comision_mensual_usd,
        "fecha_alta": p.fecha_alta.strftime("%d/%m/%y") if p.fecha_alta else "—",
    } for p in activos]

    return {
        "aliado": a.nombre,
        "codigo": a.codigo,
        "cbu_alias": a.cbu_alias,
        "total_pendiente_usd": total_pendiente,
        "total_abonado_usd":   total_abonado,
        "mrr_recurrente_usd":  mrr,
        "clientes_continuidad_activos": clientes_continuidad,
        "comisiones": items,
    }


# ─── COMISIONES — ADMIN (spec §12, §15) ──────────────────────────────────────

@app.get("/admin/comisiones")
def admin_listar_comisiones(estado: str = "", db: Session = Depends(get_db)):
    """Lista todas las comisiones del sistema, con datos del aliado para facilitar
    la transferencia. `estado` opcional: 'pendiente' | 'abonada'."""
    q = db.query(Comision)
    if estado in ("pendiente", "abonada"):
        q = q.filter(Comision.estado == estado)
    comisiones = q.order_by(Comision.fecha_pago.desc().nullslast() if hasattr(Comision.fecha_pago, "desc") else Comision.id.desc()).all()

    out = []
    for c in comisiones:
        aliado = c.aliado
        out.append({
            **_comision_row(c),
            "aliado_codigo": aliado.codigo if aliado else None,
            "aliado_nombre": aliado.nombre if aliado else "(aliado eliminado)",
            "aliado_email":  aliado.email if aliado else None,
            "aliado_cbu":    aliado.cbu_alias if aliado else None,
        })
    return out


@app.post("/admin/comisiones/{id}/abonar")
def admin_marcar_comision_abonada(id: int,
                                   confirmar_sin_cbu: bool = False,
                                   db: Session = Depends(get_db)):
    """Marca una comisión como abonada. Si el aliado no tiene CBU cargado, falla
    salvo que se pase `confirmar_sin_cbu=true` (spec §15)."""
    c = db.query(Comision).filter(Comision.id == id).first()
    if not c:
        raise HTTPException(404, "Comisión no encontrada.")
    if c.estado == "abonada":
        raise HTTPException(400, "Esta comisión ya está marcada como abonada.")

    aliado = c.aliado
    if not aliado:
        raise HTTPException(404, "Aliado asociado no encontrado.")

    # Spec §15: bloquear si no hay CBU, salvo override explícito
    if not aliado.cbu_alias and not confirmar_sin_cbu:
        raise HTTPException(
            400,
            f"El aliado {aliado.nombre} no tiene CBU/alias cargado. "
            "Pedile que lo cargue antes de abonar, o pasá confirmar_sin_cbu=true para forzar."
        )

    c.estado = "abonada"
    c.fecha_abono = datetime.now()

    # También marcar la venta correspondiente como pagada (si existe)
    try:
        venta = db.query(Venta).filter(
            Venta.aliado_id == aliado.id,
            Venta.plan == c.plan,
            Venta.nombre_cliente == c.nombre_cliente,
            Venta.pagada == False,
        ).order_by(Venta.fecha_venta.desc()).first()
        if venta:
            venta.pagada = True
            venta.fecha_pago = datetime.now()
    except Exception as e:
        print(f"[ADMIN ABONAR] No pude sincronizar venta: {e}")

    db.commit()

    # Notificar al aliado
    enviar_email(
        aliado.email,
        f"✅ Tu comisión de USD {c.comision_usd:,.0f} fue abonada",
        f"""<div style="font-family:sans-serif;background:#050505;color:#fff;padding:32px;max-width:520px;margin:auto;border-radius:12px;">
          <h2 style="color:#4ade80;">¡Comisión abonada! 💸</h2>
          <p>Hola <strong>{aliado.nombre.split()[0]}</strong>,</p>
          <p>Se transfirió tu comisión al CBU/alias registrado.</p>
          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:16px;margin:16px 0;">
            <p style="margin:4px 0;"><strong>Plan:</strong> {c.plan}</p>
            <p style="margin:4px 0;"><strong>Cliente:</strong> {c.nombre_cliente or '—'}</p>
            <p style="margin:4px 0;"><strong>Monto:</strong> <span style="color:#4ade80;font-size:1.3rem;font-weight:900;">USD {c.comision_usd:,.0f}</span></p>
            <p style="margin:4px 0;font-size:.85rem;color:#71717a;"><strong>Transferido a:</strong> {aliado.cbu_alias or '(marcado como abonado sin CBU registrado)'}</p>
          </div>
        </div>"""
    )

    return {"mensaje": "Comisión marcada como abonada.",
            "id": c.id, "estado": c.estado,
            "fecha_abono": c.fecha_abono.isoformat()}


# ─── PLANES DE CONTINUIDAD (v1.5) ────────────────────────────────────────────
# Suscripciones recurrentes mensuales de los clientes. Cada plan activo le
# genera al aliado correspondiente un 10% de comisión mensual mientras esté
# vivo. La generación de comisiones mensuales se dispara desde un job (ver
# `generar_comisiones_recurrentes_del_mes`) — admin puede invocarla manual
# o vía scheduler.

@app.post("/admin/continuidad/alta")
def admin_alta_continuidad(payload: dict, db: Session = Depends(get_db),
                           _admin=Depends(current_admin_required)):
    """Da de alta un Plan de Continuidad para un cliente, asociado a un aliado.

    payload esperado:
      - aliado_codigo: str (ej. 'AL-123')
      - nombre_cliente: str
      - cliente_email: str (opcional)
      - plan_continuidad: str (debe estar en PLANES_CONTINUIDAD)
      - precio_mensual_usd: float (opcional — default = precio del plan)
      - notas: str (opcional)
    """
    codigo = (payload.get("aliado_codigo") or "").strip()
    nombre = (payload.get("nombre_cliente") or "").strip()
    plan = (payload.get("plan_continuidad") or "").strip()

    if not codigo or not nombre or not plan:
        raise HTTPException(400, "Faltan campos obligatorios: aliado_codigo, nombre_cliente, plan_continuidad.")
    if plan not in PLANES_CONTINUIDAD:
        raise HTTPException(400, f"Plan inválido. Debe ser uno de: {list(PLANES_CONTINUIDAD.keys())}.")

    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a:
        raise HTTPException(404, f"Aliado {codigo} no encontrado.")

    precio = float(payload.get("precio_mensual_usd") or PLANES_CONTINUIDAD[plan])

    activo = PlanContinuidadActivo(
        aliado_id=a.id,
        nombre_cliente=nombre,
        cliente_email=(payload.get("cliente_email") or None),
        plan_continuidad=plan,
        precio_mensual_usd=precio,
        comision_pct=COMISION_RECURRENTE_PCT,
        notas=(payload.get("notas") or None),
    )
    db.add(activo)
    db.commit()
    db.refresh(activo)

    return {
        "mensaje": f"Plan {plan} activado para {nombre} bajo {a.nombre} ({a.codigo}).",
        "id": activo.id,
        "comision_mensual_usd": activo.comision_mensual_usd,
    }


@app.post("/admin/continuidad/{plan_id}/baja")
def admin_baja_continuidad(plan_id: int, payload: dict = None,
                           db: Session = Depends(get_db),
                           _admin=Depends(current_admin_required)):
    """Marca un plan de continuidad como dado de baja.

    Esto detiene la generación de comisiones recurrentes para ese cliente
    en próximos ciclos. Las comisiones ya generadas no se afectan.
    """
    p = db.query(PlanContinuidadActivo).filter(PlanContinuidadActivo.id == plan_id).first()
    if not p:
        raise HTTPException(404, "Plan de continuidad no encontrado.")
    if p.fecha_baja is not None:
        raise HTTPException(400, "Este plan ya está dado de baja.")

    p.fecha_baja = datetime.utcnow()
    p.motivo_baja = (payload or {}).get("motivo_baja") or None
    db.commit()
    return {"mensaje": "Plan dado de baja.", "id": p.id, "fecha_baja": p.fecha_baja.isoformat()}


@app.post("/admin/continuidad/{plan_id}/precio")
def admin_actualizar_precio_continuidad(plan_id: int, payload: dict,
                                        db: Session = Depends(get_db),
                                        _admin=Depends(current_admin_required)):
    """Actualiza el precio mensual de un PlanContinuidadActivo puntual.

    Útil cuando el cliente renegocia o cuando se sube de plan dentro del
    mismo registro (ej: pasa de Cuidado a Crecimiento sin cambiar el alta).
    El nuevo precio se aplica desde la PRÓXIMA comisión generada — las
    comisiones ya creadas en meses anteriores NO se modifican (mantiene
    trazabilidad del histórico).

    payload:
      - precio_mensual_usd: float (obligatorio, > 0)
      - plan_continuidad: str (opcional — si pasa, debe estar en PLANES_CONTINUIDAD)
      - motivo: str (opcional, queda en notas)
    """
    p = db.query(PlanContinuidadActivo).filter(PlanContinuidadActivo.id == plan_id).first()
    if not p:
        raise HTTPException(404, "Plan de continuidad no encontrado.")
    if p.fecha_baja is not None:
        raise HTTPException(400, "Plan dado de baja — no se puede actualizar precio.")

    nuevo_precio = payload.get("precio_mensual_usd")
    if nuevo_precio is None:
        raise HTTPException(400, "Falta precio_mensual_usd.")
    try:
        nuevo_precio = float(nuevo_precio)
    except (TypeError, ValueError):
        raise HTTPException(400, "precio_mensual_usd debe ser numérico.")
    if nuevo_precio <= 0:
        raise HTTPException(400, "precio_mensual_usd debe ser mayor a 0.")

    nuevo_plan = (payload.get("plan_continuidad") or "").strip()
    if nuevo_plan and nuevo_plan not in PLANES_CONTINUIDAD:
        raise HTTPException(400, f"Plan inválido. Debe ser uno de: {list(PLANES_CONTINUIDAD.keys())}.")

    precio_anterior = float(p.precio_mensual_usd)
    plan_anterior = p.plan_continuidad
    p.precio_mensual_usd = nuevo_precio
    if nuevo_plan:
        p.plan_continuidad = nuevo_plan

    # Anexar al histórico (sin pisar las notas existentes)
    motivo = (payload.get("motivo") or "").strip()
    sello = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    cambio = (f"[{sello}] precio: USD {precio_anterior:,.2f} → USD {nuevo_precio:,.2f}"
              + (f" · plan: {plan_anterior} → {nuevo_plan}" if nuevo_plan and nuevo_plan != plan_anterior else "")
              + (f" · motivo: {motivo}" if motivo else ""))
    p.notas = (p.notas + "\n" + cambio) if p.notas else cambio
    db.commit()
    return {
        "mensaje": "Precio actualizado. Aplica desde la próxima comisión generada.",
        "id": p.id,
        "plan": p.plan_continuidad,
        "precio_mensual_usd": round(nuevo_precio, 2),
        "comision_mensual_usd": p.comision_mensual_usd,
    }


@app.get("/admin/continuidad")
def admin_listar_continuidad(activos: bool = True, db: Session = Depends(get_db),
                             _admin=Depends(current_admin_required)):
    """Lista todos los planes de continuidad. Por default solo los activos."""
    q = db.query(PlanContinuidadActivo)
    if activos:
        q = q.filter(PlanContinuidadActivo.fecha_baja.is_(None))
    items = q.order_by(PlanContinuidadActivo.fecha_alta.desc()).all()
    out = []
    for p in items:
        out.append({
            "id": p.id,
            "aliado_codigo": p.aliado.codigo if p.aliado else None,
            "aliado_nombre": p.aliado.nombre if p.aliado else None,
            "cliente": p.nombre_cliente,
            "plan": p.plan_continuidad,
            "precio_mensual_usd": round(float(p.precio_mensual_usd), 2),
            "comision_mensual_usd": p.comision_mensual_usd,
            "fecha_alta": p.fecha_alta.strftime("%d/%m/%Y") if p.fecha_alta else "—",
            "fecha_baja": p.fecha_baja.strftime("%d/%m/%Y") if p.fecha_baja else None,
            "activo": p.activo,
        })
    return {"total": len(out), "items": out}


# ─── PLAN DE CONTINUIDAD — ENDPOINTS DEL ALIADO (auto-servicio) ──────────────
# v1.5 — el aliado puede dar de alta sus propias ventas de Plan de Continuidad
# desde el portal (sin esperar al admin) y darlas de baja cuando el cliente
# cancela. El alta dispara automáticamente la primera comisión del mes en curso.

@app.post("/aliado/continuidad/alta")
def aliado_alta_continuidad(payload: dict,
                            aliado: Aliado = Depends(current_aliado_required),
                            db: Session = Depends(get_db)):
    """El propio aliado da de alta un Plan de Continuidad para un cliente que
    cerró por fuera del checkout (transferencia directa, efectivo, otro medio).

    payload esperado:
      - nombre_cliente: str (obligatorio)
      - plan_continuidad: str (debe estar en PLANES_CONTINUIDAD)
      - cliente_email: str (opcional)
      - precio_mensual_usd: float (opcional — default = precio del plan)
      - notas: str (opcional)

    Crea automáticamente la primera comisión del mes en curso (10% titular +
    5% sponsor si tiene), igual que el flujo de pago automático. Cada 1ro del
    mes siguiente el cron acumula otra.
    """
    nombre = (payload.get("nombre_cliente") or "").strip()
    plan = (payload.get("plan_continuidad") or "").strip()

    if not nombre or not plan:
        raise HTTPException(400, "Faltan campos obligatorios: nombre_cliente, plan_continuidad.")
    if plan not in PLANES_CONTINUIDAD:
        raise HTTPException(400, f"Plan inválido. Debe ser uno de: {list(PLANES_CONTINUIDAD.keys())}.")

    precio = float(payload.get("precio_mensual_usd") or PLANES_CONTINUIDAD[plan])
    if precio <= 0:
        raise HTTPException(400, "Precio mensual inválido.")

    # Anti-duplicado defensivo: si ya hay un plan activo de este aliado para
    # este cliente y este plan, devolvemos el existente. Evita altas dobles
    # por doble-click o reintento.
    existente = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.aliado_id == aliado.id,
        PlanContinuidadActivo.nombre_cliente == nombre,
        PlanContinuidadActivo.plan_continuidad == plan,
        PlanContinuidadActivo.fecha_baja.is_(None),
    ).first()
    if existente:
        return {
            "status": "already_active",
            "id": existente.id,
            "comision_mensual_usd": existente.comision_mensual_usd,
            "mensaje": f"Ya tenés un {plan} activo para {nombre}.",
        }

    p = PlanContinuidadActivo(
        aliado_id=aliado.id,
        nombre_cliente=nombre,
        cliente_email=(payload.get("cliente_email") or None),
        plan_continuidad=plan,
        precio_mensual_usd=precio,
        comision_pct=COMISION_RECURRENTE_PCT,
        notas=(payload.get("notas") or "Alta directa desde portal del aliado"),
    )
    db.add(p)
    db.flush()

    ahora = datetime.utcnow()
    creado = _crear_comisiones_recurrentes_para_plan(
        db, p, ahora.month, ahora.year, ahora,
    )
    db.commit()

    return {
        "status": "ok",
        "id": p.id,
        "plan": p.plan_continuidad,
        "cliente": p.nombre_cliente,
        "precio_mensual_usd": round(precio, 2),
        "comision_mensual_usd": p.comision_mensual_usd,
        "primera_comision_creada": creado["titular"],
        "comision_sponsor_creada": creado["sponsor"],
        "mensaje": f"{plan} activado para {nombre}. Cobrás USD {p.comision_mensual_usd:,.0f}/mes mientras esté activo.",
    }


@app.get("/aliado/continuidad")
def aliado_listar_continuidad(incluir_bajas: bool = False,
                              aliado: Aliado = Depends(current_aliado_required),
                              db: Session = Depends(get_db)):
    """Lista los planes de continuidad del aliado autenticado. Por default
    solo los activos. Si incluir_bajas=true, incluye los dados de baja."""
    q = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.aliado_id == aliado.id,
    )
    if not incluir_bajas:
        q = q.filter(PlanContinuidadActivo.fecha_baja.is_(None))
    items = q.order_by(PlanContinuidadActivo.fecha_alta.desc()).all()
    out = []
    for p in items:
        out.append({
            "id": p.id,
            "cliente": p.nombre_cliente,
            "cliente_email": p.cliente_email,
            "plan": p.plan_continuidad,
            "precio_mensual_usd": round(float(p.precio_mensual_usd), 2),
            "comision_mensual_usd": p.comision_mensual_usd,
            "fecha_alta": p.fecha_alta.strftime("%d/%m/%Y") if p.fecha_alta else "—",
            "fecha_baja": p.fecha_baja.strftime("%d/%m/%Y") if p.fecha_baja else None,
            "activo": p.activo,
        })
    mrr = round(sum(p.comision_mensual_usd for p in items if p.activo), 2)
    return {"total": len(out), "items": out, "mrr_recurrente_usd": mrr}


@app.post("/aliado/continuidad/{plan_id}/baja")
def aliado_baja_continuidad(plan_id: int,
                            payload: dict = None,
                            aliado: Aliado = Depends(current_aliado_required),
                            db: Session = Depends(get_db)):
    """El aliado da de baja un plan suyo (cuando su cliente cancela).
    Detiene la generación de comisiones recurrentes para próximos meses.
    Las comisiones ya generadas no se afectan (se siguen pudiendo cobrar).
    """
    p = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.id == plan_id,
        PlanContinuidadActivo.aliado_id == aliado.id,  # <-- el aliado solo da de baja lo suyo
    ).first()
    if not p:
        raise HTTPException(404, "Plan de continuidad no encontrado o no te pertenece.")
    if p.fecha_baja is not None:
        raise HTTPException(400, "Este plan ya está dado de baja.")

    p.fecha_baja = datetime.utcnow()
    p.motivo_baja = (payload or {}).get("motivo_baja") or "Baja reportada por el aliado"
    db.commit()
    return {
        "status": "ok",
        "id": p.id,
        "fecha_baja": p.fecha_baja.isoformat(),
        "mensaje": f"{p.plan_continuidad} de {p.nombre_cliente} dado de baja. No se generan más comisiones recurrentes a partir del próximo mes.",
    }


# ─── HELPER: generar comisiones recurrentes de un mes ────────────────────────
# v1.5 — extraído del endpoint admin para que el scheduler y el flujo de alta
# (primera comisión al firmar) puedan reutilizar la misma lógica de creación
# idempotente. Toda creación de Comision recurrente en el sistema pasa por acá.
def _crear_comisiones_recurrentes_para_plan(db: Session,
                                            p: PlanContinuidadActivo,
                                            mes: int,
                                            anio: int,
                                            fecha_pago: datetime) -> dict:
    """Crea (si no existen) la comisión recurrente del aliado titular y la del
    sponsor (5%) para el plan de continuidad `p` en el mes/año dados.

    Idempotente por (aliado_id, nombre_cliente, plan_label, mes, anio).

    Devuelve {'titular': bool, 'sponsor': bool} indicando qué se creó.
    NO hace commit — el caller decide cuándo commitear.
    """
    from sqlalchemy import extract
    plan_label = f"{p.plan_continuidad} (recurrente)"
    creado = {"titular": False, "sponsor": False}

    # 1. Comisión del aliado que vendió (10%)
    ya_titular = db.query(Comision).filter(
        Comision.aliado_id == p.aliado_id,
        Comision.nombre_cliente == p.nombre_cliente,
        Comision.plan == plan_label,
        extract('month', Comision.fecha_pago) == mes,
        extract('year',  Comision.fecha_pago) == anio,
    ).first()
    if not ya_titular:
        c = Comision(
            aliado_id=p.aliado_id,
            plan=plan_label,
            monto_plan_usd=float(p.precio_mensual_usd),
            comision_pct=float(p.comision_pct),
            comision_usd=p.comision_mensual_usd,
            nombre_cliente=p.nombre_cliente,
            estado="pendiente",
            fecha_pago=fecha_pago,
        )
        db.add(c)
        creado["titular"] = True

    # 2. Comisión pasiva 5% al sponsor (RED) — paralelo al one-shot.
    # nombre_cliente lleva prefijo "RED:" para distinguirlo, igual que en las
    # ventas one-shot (ver registrar_venta y _procesar_pago_confirmado).
    aliado = p.aliado
    sponsor = getattr(aliado, "sponsor", None) if aliado else None
    if sponsor:
        cliente_red = f"RED: {aliado.nombre} ({p.nombre_cliente})"
        ya_sponsor = db.query(Comision).filter(
            Comision.aliado_id == sponsor.id,
            Comision.nombre_cliente == cliente_red,
            Comision.plan == plan_label,
            extract('month', Comision.fecha_pago) == mes,
            extract('year',  Comision.fecha_pago) == anio,
        ).first()
        if not ya_sponsor:
            comision_sponsor_usd = round(float(p.precio_mensual_usd) * 0.05, 2)
            c_red = Comision(
                aliado_id=sponsor.id,
                plan=plan_label,
                monto_plan_usd=float(p.precio_mensual_usd),
                comision_pct=0.05,
                comision_usd=comision_sponsor_usd,
                nombre_cliente=cliente_red,
                estado="pendiente",
                fecha_pago=fecha_pago,
            )
            db.add(c_red)
            creado["sponsor"] = True

    return creado


def _generar_comisiones_recurrentes_del_mes(db: Session, mes: int, anio: int) -> dict:
    """Itera todos los planes de continuidad activos y genera la comisión del
    mes/año dado para cada uno (titular + sponsor si corresponde).
    Idempotente. Hace commit antes de devolver.
    """
    if not (1 <= mes <= 12):
        raise ValueError(f"Mes inválido: {mes} (debe ser 1..12)")

    activos = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.fecha_baja.is_(None),
    ).all()

    ahora = datetime.utcnow()
    fecha_pago = datetime(
        anio, mes,
        ahora.day if (mes == ahora.month and anio == ahora.year) else 1,
    )

    creadas_titular = 0
    creadas_sponsor = 0
    saltadas_idempotencia = 0
    detalle = []

    for p in activos:
        creado = _crear_comisiones_recurrentes_para_plan(db, p, mes, anio, fecha_pago)
        if creado["titular"]:
            creadas_titular += 1
        else:
            saltadas_idempotencia += 1
        if creado["sponsor"]:
            creadas_sponsor += 1
        detalle.append({
            "aliado_codigo": p.aliado.codigo if p.aliado else None,
            "cliente": p.nombre_cliente,
            "plan": p.plan_continuidad,
            "comision_titular_usd": p.comision_mensual_usd,
            "comision_sponsor_usd": (round(float(p.precio_mensual_usd) * 0.05, 2)
                                     if (p.aliado and getattr(p.aliado, "sponsor", None))
                                     else 0.0),
            "creado_titular": creado["titular"],
            "creado_sponsor": creado["sponsor"],
        })

    db.commit()
    return {
        "mensaje": f"Generación recurrente {mes:02d}/{anio} OK.",
        "creadas_titular": creadas_titular,
        "creadas_sponsor": creadas_sponsor,
        "saltadas_por_idempotencia": saltadas_idempotencia,
        "detalle": detalle,
    }


@app.post("/admin/continuidad/generar-comisiones-mes")
def admin_generar_comisiones_recurrentes(payload: dict = None,
                                         db: Session = Depends(get_db),
                                         _admin=Depends(current_admin_required)):
    """Genera las comisiones del mes para todos los planes de continuidad activos.

    Idempotente por mes: si ya existe una Comision con plan='<plan> (recurrente)'
    y fecha_pago dentro del mismo mes/año para el mismo aliado y cliente, NO
    crea otra. Pensado para correrse 1 vez al mes (cron / scheduler / manual).

    Genera además el 5% pasivo al sponsor (si tiene), como en las ventas one-shot.

    Body opcional:
      - mes: int 1..12 (default = mes actual UTC)
      - anio: int (default = año actual UTC)
    """
    payload = payload or {}
    ahora = datetime.utcnow()
    mes  = int(payload.get("mes")  or ahora.month)
    anio = int(payload.get("anio") or ahora.year)
    if not (1 <= mes <= 12):
        raise HTTPException(400, "Mes inválido (debe ser 1..12).")
    return _generar_comisiones_recurrentes_del_mes(db, mes, anio)


# ─── PAGOS (ADMIN) ───────────────────────────────────────────────────────────

@app.get("/admin/pagos")
def admin_listar_pagos(db: Session = Depends(get_db)):
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


# ─�

# --- SALUD DEL PROGRAMA ---------------------------------------------------

@app.get("/admin/programa/salud")
def salud_programa(db: Session = Depends(get_db)):
    """Vista consolidada de salud del programa."""
    ahora = datetime.now()
    hace_7d  = ahora - timedelta(days=7)
    hace_30d = ahora - timedelta(days=30)

    todos_aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    total_aliados = len(todos_aliados)
    activos_7d = sum(
        1 for a in todos_aliados
        if getattr(a, "ultimo_login", None) and a.ultimo_login >= hace_7d
    )
    inactivos_30d = sum(
        1 for a in todos_aliados
        if not getattr(a, "ultimo_login", None) or a.ultimo_login < hace_30d
    )

    total_prospectos  = db.query(Prospecto).count()
    sin_contactar     = db.query(Prospecto).filter(Prospecto.estado == "sin_contactar").count()
    calientes         = db.query(Prospecto).filter(Prospecto.estado == "respondio").count()
    propuesta_enviada = db.query(Prospecto).filter(Prospecto.estado == "propuesta_enviada").count()

    leads_reclamados = db.query(LeadBolsa).filter(LeadBolsa.estado == "reclamado").count()
    leads_disponibles = db.query(LeadBolsa).filter(LeadBolsa.estado == "disponible").count()

    total_referidos = db.query(Referido).count()
    referidos_conv  = db.query(Referido).filter(Referido.convertido == True).count()
    tasa_conversion = round(referidos_conv / total_referidos * 100, 1) if total_referidos else 0.0

    ventas_7d = db.query(Venta).filter(
        Venta.confirmada == True,
        Venta.fecha_venta >= hace_7d
    ).all()
    ventas_semana_count = len(ventas_7d)
    ventas_semana_usd   = round(sum(v.valor_usd for v in ventas_7d), 2)

    alertas = []
    if total_aliados and inactivos_30d / total_aliados > 0.4:
        alertas.append({"nivel": "rojo", "msg": f"{inactivos_30d} aliados sin actividad en 30+ dias"})
    if leads_reclamados > 2:
        alertas.append({"nivel": "rojo", "msg": f"{leads_reclamados} leads reclamados bloqueados sin contactar"})
    if tasa_conversion < 5 and total_referidos >= 10:
        alertas.append({"nivel": "amber", "msg": f"Tasa de conversion baja: {tasa_conversion}%"})
    if calientes > 0:
        plu = "s" if calientes != 1 else ""
        alertas.append({"nivel": "amber", "msg": f"{calientes} prospecto{plu} caliente{plu} esperando propuesta"})
    if ventas_semana_count == 0:
        alertas.append({"nivel": "amber", "msg": "Sin ventas confirmadas en los ultimos 7 dias"})

    return {
        "generado_en": ahora.strftime("%d/%m/%Y %H:%M"),
        "aliados": {
            "total": total_aliados,
            "activos_7d": activos_7d,
            "inactivos_30d": inactivos_30d,
        },
        "prospectos": {
            "total": total_prospectos,
            "sin_contactar": sin_contactar,
            "calientes": calientes,
            "propuesta_enviada": propuesta_enviada,
        },
        "bolsa": {
            "leads_disponibles": leads_disponibles,
            "leads_reclamados": leads_reclamados,
        },
        "conversion": {
            "total_referidos": total_referidos,
            "convertidos": referidos_conv,
            "tasa_pct": tasa_conversion,
        },
        "ventas_7d": {
            "cantidad": ventas_semana_count,
            "usd": ventas_semana_usd,
        },
        "alertas": alertas,
    }


# ─ ACADEMIA: CONTENIDO DE ONBOARDING (spec §18) ────────────────────────────

class AcademiaModuloCreate(BaseModel):
    orden: int
    titulo: str
    descripcion: str | None = None
    tipo: str
    url_contenido: str | None = None
    duracion_minutos: int | None = None
    activo: bool = True


class AcademiaModuloUpdate(BaseModel):
    orden: int | None = None
    titulo: str | None = None
    descripcion: str | None = None
    tipo: str | None = None
    url_contenido: str | None = None
    duracion_minutos: int | None = None
    activo: bool | None = None


def _modulo_row(m: AcademiaModulo, completado: bool = False):
    return {
        "id": m.id,
        "orden": m.orden,
        "titulo": m.titulo,
        "descripcion": m.descripcion,
        "tipo": m.tipo,
        "url": m.url_contenido,
        "url_contenido": m.url_contenido,
        "duracion_minutos": m.duracion_minutos,
        "activo": m.activo,
        "completado": completado,
    }


@app.get("/academia/modulos")
def listar_modulos_academia(db: Session = Depends(get_db)):
    """Lista pública de módulos de la academia (solo activos)."""
    mods = db.query(AcademiaModulo).filter(AcademiaModulo.activo == True)\
        .order_by(AcademiaModulo.orden).all()
    return [_modulo_row(m) for m in mods]


@app.get("/admin/academia")
def admin_listar_modulos(db: Session = Depends(get_db)):
    """Versión admin: devuelve TODOS los módulos (activos e inactivos)."""
    mods = db.query(AcademiaModulo).order_by(AcademiaModulo.orden).all()
    return [_modulo_row(m) for m in mods]


@app.post("/admin/academia")
def admin_crear_modulo(payload: AcademiaModuloCreate, db: Session = Depends(get_db)):
    if payload.tipo not in ("video", "pdf", "texto"):
        raise HTTPException(400, "tipo debe ser 'video', 'pdf' o 'texto'.")
    m = AcademiaModulo(
        orden       = payload.orden,
        titulo      = payload.titulo,
        descripcion = payload.descripcion,
        tipo        = payload.tipo,
        url_contenido = payload.url_contenido,
        duracion_minutos = payload.duracion_minutos,
        activo      = payload.activo,
    )
    db.add(m); db.commit(); db.refresh(m)
    return _modulo_row(m)


@app.patch("/admin/academia/{id}")
def admin_editar_modulo(id: int, payload: AcademiaModuloUpdate, db: Session = Depends(get_db)):
    m = db.query(AcademiaModulo).filter(AcademiaModulo.id == id).first()
    if not m: raise HTTPException(404, "Módulo no encontrado.")
    for campo in ("orden", "titulo", "descripcion", "tipo",
                  "url_contenido", "duracion_minutos", "activo"):
        val = getattr(payload, campo, None)
        if val is not None:
            setattr(m, campo, val)
    db.commit()
    return _modulo_row(m)


@app.delete("/admin/academia/{id}")
def admin_eliminar_modulo(id: int, db: Session = Depends(get_db)):
    m = db.query(AcademiaModulo).filter(AcademiaModulo.id == id).first()
    if not m: raise HTTPException(404, "Módulo no encontrado.")
    db.delete(m); db.commit()
    return {"mensaje": "Módulo eliminado."}


# ─── SEMBRAR MÓDULOS INICIALES (idempotente) ─────────────────────────────────
def sembrar_modulos_academia():
    """Crea los 5 módulos mínimos del spec §18 si la tabla está vacía."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(AcademiaModulo).count() > 0:
            return
        modulos_default = [
            {"orden": 1, "titulo": "Bienvenida al Canal 1",
             "descripcion": "Introducción al programa de aliados y cómo funciona el sistema de comisiones.",
             "tipo": "texto",
             "url_contenido": "/academia/bienvenida",
             "duracion_minutos": 5},
            {"orden": 2, "titulo": "Cómo usar el portal y el cotizador",
             "descripcion": "Recorrida paso a paso del portal: prospectos, bolsa de leads y cotizador.",
             "tipo": "texto",
             "url_contenido": "/academia/portal",
             "duracion_minutos": 10},
            {"orden": 3, "titulo": "El guión de ventas paso a paso",
             "descripcion": "El pitch probado que usan los aliados de mejor performance.",
             "tipo": "pdf",
             "url_contenido": "/guion",
             "duracion_minutos": 15},
            {"orden": 4, "titulo": "Cómo calificar un prospecto",
             "descripcion": "Las 5 preguntas que tenés que hacer antes de armar una propuesta.",
             "tipo": "texto",
             "url_contenido": "/academia/calificar",
             "duracion_minutos": 8},
            {"orden": 5, "titulo": "Preguntas frecuentes y objeciones comunes",
             "descripcion": "Cómo responder a 'está caro', 'lo pienso', 'no tengo tiempo ahora'.",
             "tipo": "texto",
             "url_contenido": "/academia/objeciones",
             "duracion_minutos": 10},
        ]
        for m in modulos_default:
            db.add(AcademiaModulo(**m, activo=True))
        db.commit()
        print(f"[ACADEMIA] Sembrados {len(modulos_default)} módulos iniciales.")
    except Exception as e:
        print(f"[ACADEMIA SEMBRADO ERROR] {e}")
    finally:
        db.close()


# Ejecutar sembrado al iniciar (no bloquea si falla)
try:
    sembrar_modulos_academia()
except Exception as _e:
    pass


# ─── ONBOARDING v2: combinar checklist + módulos reales (spec §18) ───────────
# La ruta vieja /aliados/{codigo}/onboarding ya existe más arriba; sumamos una
# ruta complementaria que devuelve específicamente los módulos de la Academia.
@app.get("/aliados/{codigo}/academia")
def academia_del_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Devuelve los módulos de la Academia para el aliado, en orden, con flag
    de completitud y resumen de progreso."""
    a = _get_aliado(codigo, db)
    mods = db.query(AcademiaModulo).filter(AcademiaModulo.activo == True)\
        .order_by(AcademiaModulo.orden).all()
    completados_ids = {
        c.modulo_id for c in db.query(AliadoModuloCompletado).filter(
            AliadoModuloCompletado.aliado_id == a.id
        ).all()
    }
    completados = sum(1 for m in mods if m.id in completados_ids)
    return {
        "aliado": a.codigo,
        "total_modulos": len(mods),
        "modulos_completados": completados,
        "porcentaje": round(100 * completados / len(mods)) if mods else 0,
        "creditos_por_modulo": BONUS_MODULO_COMPLETADO,
        "modulos": [
            _modulo_row(m, completado=(m.id in completados_ids))
            for m in mods
        ],
    }


# Bonus por completar un módulo de Academia. Bajo a propósito: el incentivo
# real es que el aliado se forme antes de quemar créditos en leads premium.
BONUS_MODULO_COMPLETADO = 10


@app.post("/aliados/{codigo}/academia/{modulo_id}/completar")
def completar_modulo_academia(codigo: str, modulo_id: int,
                               db: Session = Depends(get_db),
                               _owner=Depends(verify_ownership_dep)):
    """Marca un módulo de la Academia como completado por el aliado y otorga
    el bonus de créditos correspondiente. Idempotente: si ya estaba completado,
    no duplica créditos.
    """
    a = _get_aliado(codigo, db)
    mod = db.query(AcademiaModulo).filter(
        AcademiaModulo.id == modulo_id,
        AcademiaModulo.activo == True,
    ).first()
    if not mod:
        raise HTTPException(404, "Módulo no encontrado o inactivo.")

    # Idempotencia: si ya estaba completado, devolvemos OK sin re-acreditar.
    existente = db.query(AliadoModuloCompletado).filter(
        AliadoModuloCompletado.aliado_id == a.id,
        AliadoModuloCompletado.modulo_id == mod.id,
    ).first()
    if existente:
        return {
            "mensaje":          "Este módulo ya estaba completado.",
            "ya_completado":    True,
            "creditos_ganados": 0,
            "saldo":            a.creditos or 0,
            "modulo": {"id": mod.id, "titulo": mod.titulo},
        }

    # Primera vez completándolo: registrar + acreditar
    completado = AliadoModuloCompletado(
        aliado_id          = a.id,
        modulo_id          = mod.id,
        creditos_otorgados = BONUS_MODULO_COMPLETADO,
    )
    db.add(completado)
    _ajustar_creditos(db, a, BONUS_MODULO_COMPLETADO,
                      "modulo_completado", f"modulo:{mod.id}")
    db.commit()

    return {
        "mensaje":          f"¡Completaste '{mod.titulo}'! Te sumamos {BONUS_MODULO_COMPLETADO} créditos.",
        "ya_completado":    False,
        "creditos_ganados": BONUS_MODULO_COMPLETADO,
        "saldo":            a.creditos or 0,
        "modulo": {"id": mod.id, "titulo": mod.titulo},
    }

# ─── RESET DE CONTRASEÑA DE ALIADO (admin) ───────────────────────────────────
@app.post("/admin/reset-password-aliado")
def reset_password_aliado(
    request: Request,
    email: str = Body(...),
    nueva_password: str = Body(...),
    _admin=Depends(current_admin_required),
    db: Session = Depends(get_db),
):
    """Resetea la contraseña de un aliado. Solo accesible por admin."""
    a = db.query(Aliado).filter(Aliado.email == email).first()
    if not a:
        raise HTTPException(404, "Aliado no encontrado.")
    a.password_hash = hash_password(nueva_password)
    db.commit()
    return {"ok": True, "aliado": a.nombre, "email": a.email}
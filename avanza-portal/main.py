from fastapi import FastAPI, Depends, HTTPException, Request, status, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from passlib.context import CryptContext
from datetime import datetime, timedelta
from pydantic import BaseModel
from models import (
    Aliado, Admin, Venta, Referido, Prospecto, AuditoriaLog, LeadBolsa,
    TransaccionCredito, PostComunidad, ComentarioComunidad, AutomationLog,
    LinkPago, Comision, AcademiaModulo,
    PLANES, NIVELES, CUOTAS_RECARGO, REPUTACION_BADGES
)
import random, string, os, smtplib, httpx, json, hmac as hmac_lib, hashlib, base64, sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler

from database import engine, get_db, Base
from auth import (
    crear_token, current_aliado_required, current_admin_required,
    verify_ownership_dep, ADMIN_API_KEY, JWT_SECRET,
)
import schemas

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
]:
    _aplicar_migracion(col_sql)


# ─── EMAIL HELPER ─────────────────────────────────────────────────────────────
SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASS     = os.environ.get("SMTP_PASS", "")
EMAIL_FROM    = os.environ.get("EMAIL_FROM", SMTP_USER)

# ─── MERCADOPAGO ──────────────────────────────────────────────────────────────
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
MP_WEBHOOK_SECRET = os.environ.get("MP_WEBHOOK_SECRET", "")
# URL pública del BACKEND (donde viven los endpoints /webhooks/*). DEBE ser la URL real del backend.
# Los webhooks de Mercado Pago y PayPal se configuran usando esta URL.
BACKEND_PUBLIC_URL = os.environ.get("BACKEND_PUBLIC_URL", "https://avanza-digital.onrender.com")
# URL del portal del aliado (para links en emails). Si backend y portal viven en el mismo dominio, coinciden.
PORTAL_URL      = os.environ.get("PORTAL_URL", BACKEND_PUBLIC_URL)

# ─── PAYPAL ───────────────────────────────────────────────────────────────────
PAYPAL_CLIENT_ID     = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
PAYPAL_WEBHOOK_ID    = os.environ.get("PAYPAL_WEBHOOK_ID", "")
PAYPAL_BASE_URL      = os.environ.get("PAYPAL_BASE_URL", "https://api-m.paypal.com")  # sandbox: https://api-m.sandbox.paypal.com

# ─── RESEND (emails transaccionales) ──────────────────────────────────────────
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM    = os.environ.get("RESEND_FROM", "Avanza Digital <no-reply@avanzadigital.digital>")

# ─── DOLAR API ───────────────────────────────────────────────────────────────
DOLARAPI_URL = os.environ.get("DOLARAPI_URL", "https://dolarapi.com/v1/dolares/blue")
# DOLAR_FALLBACK: tipo de cambio ARS/USD usado cuando dolarapi.com no responde
# y tampoco hay ningún valor cacheado. Configurar en Render como variable de entorno.
# Ejemplo: DOLAR_FALLBACK=1250

# ─── FRONT URLS (para redirecciones post-pago) ───────────────────────────────
SUCCESS_URL = os.environ.get("CHECKOUT_SUCCESS_URL", "https://avanzadigital.digital/gracias")
FAILURE_URL = os.environ.get("CHECKOUT_FAILURE_URL", "https://avanzadigital.digital/error")

def enviar_email(destinatario: str, asunto: str, cuerpo_html: str):
    """Envía un email. Preferimos Resend si hay API key; si no, SMTP; si nada, solo log."""
    # --- 1. Resend (preferido) ---
    if RESEND_API_KEY:
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                         "Content-Type": "application/json"},
                json={"from": RESEND_FROM, "to": [destinatario],
                      "subject": asunto, "html": cuerpo_html},
                timeout=10.0
            )
            if resp.status_code in (200, 202):
                print(f"[EMAIL Resend] OK → {destinatario} | {asunto}")
                return
            print(f"[EMAIL Resend ERROR {resp.status_code}] {resp.text[:200]}")
        except Exception as e:
            print(f"[EMAIL Resend ERROR] {e}")

    # --- 2. SMTP (fallback) ---
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
        print(f"[EMAIL SMTP] Enviado a {destinatario}: {asunto}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


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


# ─── PAYPAL: obtención de access token (se cachea mientras no expire) ────────
_paypal_token_cache = {"access_token": None, "expires_at": None}

async def obtener_paypal_token() -> str:
    """Obtiene un access_token de PayPal. Cachea hasta expiración."""
    global _paypal_token_cache
    now = datetime.now()
    if _paypal_token_cache["access_token"] and _paypal_token_cache["expires_at"] and now < _paypal_token_cache["expires_at"]:
        return _paypal_token_cache["access_token"]

    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise HTTPException(503, "PayPal no está configurado (faltan credenciales).")

    basic = base64.b64encode(f"{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}".encode()).decode()
    async with httpx.AsyncClient(timeout=12.0) as client:
        r = await client.post(
            f"{PAYPAL_BASE_URL}/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            content="grant_type=client_credentials"
        )
        if r.status_code != 200:
            raise HTTPException(502, f"PayPal token error: {r.text[:200]}")
        data = r.json()
        _paypal_token_cache["access_token"] = data["access_token"]
        # Renovamos 60s antes del vencimiento real por margen de seguridad
        _paypal_token_cache["expires_at"] = now + timedelta(seconds=max(60, int(data.get("expires_in", 3600)) - 60))
        return data["access_token"]


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


# ─── VERIFICACIÓN DE FIRMA DE WEBHOOK DE PAYPAL ──────────────────────────────
async def verificar_firma_paypal(headers, body_json) -> bool:
    """Verifica el webhook de PayPal llamando a su endpoint de verificación.

    FAIL-CLOSED: si PAYPAL_WEBHOOK_ID no está seteado, devuelve False salvo
    que AVANZA_INSECURE_WEBHOOKS=1 (modo dev local).
    """
    if not PAYPAL_WEBHOOK_ID:
        if os.environ.get("AVANZA_INSECURE_WEBHOOKS") == "1":
            print("[PAYPAL WEBHOOK] AVANZA_INSECURE_WEBHOOKS=1 — validación desactivada (SOLO DEV)")
            return True
        print("[PAYPAL WEBHOOK] ❌ PAYPAL_WEBHOOK_ID no seteada — rechazando webhook (fail-closed)")
        return False
    try:
        token = await obtener_paypal_token()
        payload = {
            "auth_algo":         headers.get("paypal-auth-algo") or headers.get("PAYPAL-AUTH-ALGO", ""),
            "cert_url":          headers.get("paypal-cert-url")  or headers.get("PAYPAL-CERT-URL", ""),
            "transmission_id":   headers.get("paypal-transmission-id") or headers.get("PAYPAL-TRANSMISSION-ID", ""),
            "transmission_sig":  headers.get("paypal-transmission-sig") or headers.get("PAYPAL-TRANSMISSION-SIG", ""),
            "transmission_time": headers.get("paypal-transmission-time") or headers.get("PAYPAL-TRANSMISSION-TIME", ""),
            "webhook_id":        PAYPAL_WEBHOOK_ID,
            "webhook_event":     body_json,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{PAYPAL_BASE_URL}/v1/notifications/verify-webhook-signature",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload
            )
            if r.status_code != 200:
                print(f"[PAYPAL WEBHOOK] Error API verify: {r.text[:200]}")
                return False
            return r.json().get("verification_status") == "SUCCESS"
    except Exception as e:
        print(f"[PAYPAL WEBHOOK] Excepción verify: {e}")
        return False


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
                html = f"""
                <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
                  <h2 style="color:#f87171;margin-bottom:8px;">🚨 Lead liberado automáticamente</h2>
                  <p>Hola <strong>{aliado_nombre.split()[0] if aliado_nombre else ''}</strong>,</p>
                  <p>El lead <strong>{lead.empresa}</strong> volvió a la bolsa porque pasaron más de 48 horas sin que lo marcaras como contactado.</p>
                  <p style="color:#a1a1aa;">Otros aliados ya pueden reclamarlo. Si tiene buen potencial, podés volver a tomarlo si sigue disponible.</p>
                  <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ir a la bolsa →</a>
                </div>
                """
                enviar_email(aliado_email, f"🚨 Avanza: perdiste el lead {lead.empresa} (48hs sin contactar)", html)

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


scheduler.add_job(job_liberar_leads_48h, "interval", minutes=30)
scheduler.add_job(job_expirar_links_pago, "interval", hours=1)
scheduler.start()


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

def generar_ref_code(nombre):
    base = nombre.split()[0].lower()[:6]
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
def health(): return {"status": "ok"}

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
        ref_code     = generar_ref_code(nombre),
        password_hash= hash_password(password),
        sponsor_id   = sponsor_id_db,
        tipo_aliado  = tipo_aliado if tipo_aliado in ("canal1", "canal2") else "canal1",
        terminos_aceptados = True,
        terminos_aceptados_en = datetime.now(),
    )
    db.add(a); db.commit(); db.refresh(a)

    # Email de bienvenida — EN SEGUNDO PLANO (no bloquea la respuesta)
    background_tasks.add_task(
        enviar_email,
        a.email,
        f"¡Bienvenido al Avanza Partner Network, {a.nombre.split()[0]}!",
        f"""
        <div style="font-family:Inter,sans-serif;background:#050505;color:#fff;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;">
          <h1 style="color:#f97316;font-size:1.6rem;margin-bottom:8px;">¡Ya sos Aliado Avanza! 🎉</h1>
          <p style="color:#a1a1aa;margin-bottom:28px;">Tu registro fue confirmado. Guardá estos datos para ingresar al portal.</p>
          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:20px;margin-bottom:24px;">
            <p style="margin:0 0 8px;font-size:.85rem;color:#71717a;text-transform:uppercase;letter-spacing:1px;">Tu código de aliado</p>
            <p style="margin:0;font-size:2rem;font-weight:900;color:#f97316;letter-spacing:2px;">{a.codigo}</p>
          </div>
          <p style="color:#a1a1aa;margin-bottom:8px;">Tu comisión arranca en <strong style="color:#fff;">10% (BASIC)</strong> y sube automáticamente con cada venta.</p>
          <p style="color:#a1a1aa;margin-bottom:28px;">Tu link de referido: <a href="https://avanzadigital.digital/alianzas?ref={a.ref_code}" style="color:#3b82f6;">/alianzas?ref={a.ref_code}</a></p>
          <a href="https://avanza-digital-production.up.railway.app/portal.html" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;">Ingresar al portal →</a>
          <p style="margin-top:32px;font-size:.8rem;color:#71717a;">Avanza Digital · Partner Network · Santa Fe, Argentina</p>
        </div>
        """
    )

    # Notificar al admin — EN SEGUNDO PLANO
    background_tasks.add_task(
        enviar_email,
        EMAIL_FROM or "avanzadigital4@gmail.com",
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
@limiter.limit("5/minute")
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
    """Portal del aliado: login con código + contraseña.

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

    # Buscar aliado activo con ese código
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
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error guardando tracking de login: {e}")

    return _aliado_detalle(a, incluir_token=True)


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
        "link_ref": f"https://avanzadigital.digital/alianzas?ref={a.ref_code}",
    }


# ─── ALIADOS — RUTAS CON {codigo} ────────────────────────────────────────────

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
    a = _get_aliado(codigo, db)
    db.query(Referido).filter(Referido.aliado_id == a.id).delete()
    db.query(Venta).filter(Venta.aliado_id == a.id).delete()
    db.delete(a); db.commit()
    return {"mensaje": f"{codigo} eliminado permanentemente."}


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
    
    # 1. Registrar Venta del Aliado que cerró
    v = Venta(aliado_id=a.id, referido_id=referido_id, nombre_cliente=nombre_cliente,
              plan=plan, valor_usd=valor, comision_pct=a.comision_pct,
              comision_usd=comision_usd, confirmada=True, pagada=False,
              fecha_venta=datetime.now(), modalidad_pago=modalidad_pago, notas=notas)
    db.add(v)

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
    
    a.nivel = a.nivel_calculado
    db.commit()
    return {"mensaje": "Venta registrada.", "aliado": a.nombre, "nivel_nuevo": a.nivel_calculado, "valor_usd": valor, "comision_usd": comision_usd}


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
    estados_manuales = {"registrado", "sin_contactar", "contactado", "respondio", "propuesta_enviada"}
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
    }

def _aliado_detalle(a, incluir_token: bool = False):
    out = {
        "codigo": a.codigo, "nombre": a.nombre, "email": a.email,
        "whatsapp": a.whatsapp, "ciudad": a.ciudad, "perfil": a.perfil,
        "nivel_actual": a.nivel, "nivel_calculado": a.nivel_calculado,
        "comision_pct": a.comision_pct * 100,
        "ventas_6m": a.ventas_6_meses, "total_ventas": len(a.ventas),
        "total_ganado": round(a.total_ganado, 2),
        "total_pendiente": round(a.total_pendiente, 2),
        "ref_code": a.ref_code,
        "link_ref":    f"https://avanzadigital.digital/alianzas?ref={a.ref_code}",
        "link_perfil": f"{PORTAL_URL}/p/{a.ref_code}",
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


# ─── CHECKOUT: MP (ARS) + PAYPAL (USD) ───────────────────────────────────────
# Spec §2, §3, §4, §5: el aliado elige moneda. MP usa conversión blue en tiempo real.
# PayPal cobra en USD fijo. Ambos links expiran en 48hs.

LINK_EXPIRATION_HOURS = 48


async def _crear_link_mp(a: Aliado, plan: str, nombre_cliente: str, db: Session):
    """Crea una preferencia en MP con precio en ARS usando dolarapi blue del momento."""
    if not MP_ACCESS_TOKEN:
        raise HTTPException(503, "MP_ACCESS_TOKEN no está configurado.")

    valor_usd = PLANES[plan]
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

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.mercadopago.com/checkout/preferences",
            json=preference_data,
            headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}",
                     "Content-Type": "application/json"},
            timeout=15.0,
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(502, f"Error MercadoPago: {resp.text[:200]}")
        pref = resp.json()

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


async def _crear_link_paypal(a: Aliado, plan: str, nombre_cliente: str, db: Session):
    """Crea una orden de PayPal en USD para pago directo en dólares."""
    valor_usd = PLANES[plan]
    external_ref = f"{a.ref_code}|{plan}|{nombre_cliente}"
    expires_at = datetime.now() + timedelta(hours=LINK_EXPIRATION_HOURS)

    token = await obtener_paypal_token()
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {"currency_code": "USD", "value": f"{valor_usd:.2f}"},
            "description": f"Avanza Digital — {plan}",
            "custom_id": external_ref[:127],  # PayPal limita a 127 chars
        }],
        "application_context": {
            "return_url":  SUCCESS_URL,
            "cancel_url":  FAILURE_URL,
            "brand_name":  "Avanza Digital",
            "user_action": "PAY_NOW",
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{PAYPAL_BASE_URL}/v2/checkout/orders",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=15.0,
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(502, f"Error PayPal: {resp.text[:200]}")
        orden = resp.json()

    approve = next((l["href"] for l in orden.get("links", []) if l.get("rel") == "approve"), None)
    if not approve:
        raise HTTPException(502, "PayPal no devolvió link de aprobación.")

    link = LinkPago(
        aliado_id    = a.id,
        plan         = plan,
        moneda       = "usd",
        precio_usd   = valor_usd,
        precio_ars   = None,
        tipo_cambio  = None,
        checkout_url = approve,
        processor    = "paypal",
        external_ref = orden.get("id") or external_ref,  # el order_id de PayPal es lo que usa el webhook
        expires_at   = expires_at,
        estado       = "activo",
    )
    db.add(link); db.commit(); db.refresh(link)

    return {
        "checkout_url": approve,
        "link_id":      link.id,
        "moneda":       "usd",
        "plan":         plan,
        "precio_usd":   valor_usd,
        "processor":    "paypal",
        "paypal_order_id": orden.get("id"),
        "expires_at":   expires_at.isoformat(),
        "aliado":       a.nombre,
        "fallback":     False,
    }


@app.post("/checkout/crear")
@limiter.limit("20/hour")
async def crear_checkout(request: Request, plan: str,
                         ref_code: str,
                         nombre_cliente: str = "Cliente",
                         moneda: str = "ars",
                         db: Session = Depends(get_db)):
    """Crea un link de pago. `moneda` = 'ars' (MP) o 'usd' (PayPal).
    Spec §5: ambos flujos generan registros en links_pago con expiración a 48hs."""
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not a:
        raise HTTPException(404, "Código de referido inválido.")
    if plan not in PLANES:
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
                          nota=f"Auto-creado al generar link de pago ({moneda.upper()})")
            db.add(p); db.commit()
    except Exception as e:
        print(f"[CHECKOUT] No pude auto-crear prospecto: {e}")

    # Fallback si no hay credenciales configuradas
    if moneda == "ars" and not MP_ACCESS_TOKEN:
        return {
            "checkout_url": f"https://avanzadigital.digital/contratar?plan={plan}&ref={ref_code}",
            "fallback": True,
            "mensaje": "MercadoPago no activado. Configurar MP_ACCESS_TOKEN.",
        }
    if moneda == "usd" and (not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET):
        return {
            "checkout_url": f"https://avanzadigital.digital/contratar?plan={plan}&ref={ref_code}",
            "fallback": True,
            "mensaje": "PayPal no activado. Configurar PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET.",
        }

    if moneda == "ars":
        return await _crear_link_mp(a, plan, nombre_cliente, db)
    else:
        return await _crear_link_paypal(a, plan, nombre_cliente, db)


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


# ─── HELPER COMÚN: procesar pago confirmado (MP o PayPal) ────────────────────
def _procesar_pago_confirmado(db: Session,
                              ref_code: str,
                              plan: str,
                              nombre_cliente: str,
                              processor: str,
                              payment_id: str,
                              link_pago_id: int = None) -> dict:
    """Registra venta + comisión + notifica. Idempotente vía payment_id en notas.
    El token [PID:xxx] es delimitado para evitar que payment_id='42' matchee con '142'."""
    if plan not in PLANES:
        return {"status": "invalid_plan"}
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not a:
        return {"status": "aliado_not_found"}

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
    modalidad = "MercadoPago" if processor == "mercadopago" else "PayPal"

    # --- Registrar venta ---
    v = Venta(aliado_id=a.id, nombre_cliente=nombre_cliente, plan=plan,
              valor_usd=valor_usd, comision_pct=comision_pct, comision_usd=comision_usd,
              confirmada=True, pagada=False, fecha_venta=fecha_venta,
              modalidad_pago=modalidad, notas=f"Pago automático {modalidad} {pid_token}")
    db.add(v)

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

    a.nivel = a.nivel_calculado
    db.commit()

    # --- Notificación al aliado (spec §7) ---
    enviar_email(a.email, f"💰 ¡Nuevo cliente cerrado! — {plan}",
        f"""<div style="font-family:sans-serif;background:#050505;color:#fff;padding:32px;max-width:520px;margin:auto;border-radius:12px;">
          <h2 style="color:#4ade80;">¡Tu cliente {nombre_cliente} acaba de pagar! 🎉</h2>
          <p>Hola <strong>{a.nombre.split()[0]}</strong>, llegó un pago a través de <strong>{modalidad}</strong>.</p>
          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:16px;margin:16px 0;">
            <p style="margin:4px 0;"><strong>Plan:</strong> {plan}</p>
            <p style="margin:4px 0;"><strong>Cliente:</strong> {nombre_cliente}</p>
            <p style="margin:4px 0;"><strong>Tu comisión:</strong> <span style="color:#4ade80;font-size:1.3rem;font-weight:900;">USD {comision_usd:,.0f}</span></p>
          </div>
          <p style="color:#71717a;font-size:.85rem;">Se te abona dentro de las 24hs al CBU/alias registrado.</p>
          <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ver mi portal →</a>
        </div>""")

    return {"status": "ok", "venta_registrada": True, "comision_id": c.id,
            "comision_usd": comision_usd, "aliado": a.codigo}


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


# ─── WEBHOOK PAYPAL (con verificación de firma — spec §6) ────────────────────
@app.post("/webhooks/paypal")
async def webhook_paypal(request: Request, db: Session = Depends(get_db)):
    """Recibe eventos de PayPal. Valida contra PayPal antes de procesar."""
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except Exception:
        return {"status": "invalid_json"}

    # --- 1. Verificar firma llamando a la API de PayPal ---
    if not await verificar_firma_paypal(request.headers, body):
        return JSONResponse(status_code=401, content={"status": "invalid_signature"})

    event_type = body.get("event_type", "")
    if event_type not in ("PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.APPROVED"):
        return {"status": "ignored", "event": event_type}

    # --- 2. Extraer datos del pago ---
    resource = body.get("resource", {}) or {}
    payment_id = resource.get("id", "")
    # El custom_id puede estar en distintos lugares dependiendo del evento
    custom_id = (resource.get("custom_id")
                 or (resource.get("purchase_units", [{}])[0].get("custom_id") if resource.get("purchase_units") else None)
                 or "")
    order_id = (resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
                or resource.get("id", ""))

    if not custom_id:
        return {"status": "no_custom_id"}

    parts = custom_id.split("|", 2)
    if len(parts) < 2:
        return {"status": "invalid_ref"}
    ref_code, plan = parts[0], parts[1]
    nombre_cliente = parts[2] if len(parts) > 2 else "Cliente Web"

    # --- 3. Buscar el LinkPago asociado por order_id ---
    lp = db.query(LinkPago).filter(
        LinkPago.external_ref == order_id,
        LinkPago.processor == "paypal",
    ).first()

    # --- 4. Procesar ---
    return _procesar_pago_confirmado(db, ref_code, plan, nombre_cliente,
                                     processor="paypal",
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
    return await _crear_link_paypal(a, lp_viejo.plan, nombre_cliente, db)


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
    pasos = [
        {"id": "registro",    "titulo": "Te registraste",           "completado": True},
        {"id": "referido",    "titulo": "Registraste tu 1er referido",
         "completado": len(a.referidos) > 0},
        {"id": "prospecto",   "titulo": "Cargaste un prospecto",
         "completado": len(a.prospectos) > 0},
    ]
    if not es_canal2:
        pasos.append({"id": "bolsa", "titulo": "Reclamaste un lead de la bolsa",
                      "completado": db.query(LeadBolsa).filter(LeadBolsa.aliado_id == a.id).first() is not None})
    pasos += [
        {"id": "primera_venta","titulo": "Cerraste tu primera venta",
         "completado": a.ventas_6_meses > 0},
        {"id": "red",         "titulo": "Invitaste a tu primer sub-aliado",
         "completado": len(getattr(a, "sub_aliados", [])) > 0},
    ]
    completados = sum(1 for p in pasos if p["completado"])
    return {"pasos": pasos, "completados": completados, "total": len(pasos),
            "pct": round(completados / len(pasos) * 100)}


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


# ─── BOLSA DE LEADS (PORTAL ALIADO) ──────────────────────────────────────────

@app.get("/aliados/{codigo}/bolsa")
def ver_bolsa_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Muestra los leads disponibles y los que este aliado ya reclamó."""
    a = _get_aliado(codigo, db)
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "La bolsa de leads no está disponible para aliados Canal 2.")
    _aplicar_caducidad_bolsa(db) # Limpiamos antes de mostrar
    
    disponibles = db.query(LeadBolsa).filter(LeadBolsa.estado == "disponible").all()
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
            "estado": l.estado, "horas_restantes": horas_restantes
        })
        
    return {
        "disponibles": [
            {
                "id": l.id, "empresa": l.empresa, "rubro": l.rubro,
                "ciudad": l.ciudad, "nombre_contacto": l.nombre_contacto,
                "tier": l.tier, "score_calidad": l.score_calidad,
                "costo_creditos": l.costo_creditos
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
    """Corazón del perfilado IA: calcula score 0-100, plan recomendado y pitch."""
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
    """Devuelve (asunto, cuerpo_html) para el paso N."""
    aliado = p.aliado
    nombre_prospecto = p.nombre.split()[0] if p.nombre else "Hola"
    plan = p.plan_recomendado or p.plan_interes or "Plan Pro"

    if paso == 1:
        asunto = f"{nombre_prospecto}, te comparto un diagnóstico gratis"
        mensaje = (
            f"Hola {nombre_prospecto},<br><br>"
            f"Soy {aliado.nombre.split()[0]}. Hace unos días hablamos y quería retomar.<br><br>"
            f"Te dejo este <a href='https://avanzadigital.digital/alianzas?ref={aliado.ref_code}' "
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

    html = f"""
    <div style='font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:32px;
                max-width:560px;margin:0 auto;border-radius:12px;'>
      {mensaje}
      <hr style='margin:24px 0;border:none;border-top:1px solid #222;'>
      <p style='font-size:0.75rem;color:#71717a;'>
        Este mensaje fue enviado por el sistema de seguimiento automático de Avanza Digital en
        nombre de {aliado.nombre}. Para dejar de recibirlos, respondé 'BAJA' a este mail.
      </p>
    </div>
    """
    return asunto, html


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
    """Helper: suma/resta créditos y registra transacción."""
    aliado.creditos = (aliado.creditos or 0) + delta
    if aliado.creditos < 0:
        aliado.creditos = 0
    t = TransaccionCredito(aliado_id=aliado.id, delta=delta, motivo=motivo, referencia=ref)
    db.add(t)


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
                            body: schemas.AjusteCreditosIn | None = Body(default=None),
                            delta: int = 0, motivo: str = "recarga_admin",
                            db: Session = Depends(get_db)):
    """Admin: asigna/quita créditos a un aliado. (Protegido por middleware admin.)"""
    if body is not None:
        delta, motivo = body.delta, body.motivo
    a = _get_aliado(codigo, db)
    _ajustar_creditos(db, a, delta, motivo, "admin")
    db.commit()
    return {"mensaje": f"Saldo actualizado.", "nuevo_saldo": a.creditos}


@app.get("/bolsa/marketplace")
def ver_marketplace(codigo_aliado: str = "",
                    aliado: Aliado = Depends(current_aliado_required),
                    db: Session = Depends(get_db)):
    """Lista los leads calificados/premium disponibles con su costo en créditos.

    SECURITY: usa el aliado del JWT, no acepta `codigo_aliado` para spoofing.
    """
    a = aliado  # del JWT
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "El marketplace de leads no está disponible para aliados Canal 2.")
    _aplicar_caducidad_bolsa(db)
    leads = db.query(LeadBolsa).filter(
        LeadBolsa.estado == "disponible",
        LeadBolsa.tier.in_(["calificado", "premium"])
    ).order_by(LeadBolsa.costo_creditos.desc(), LeadBolsa.fecha_carga.desc()).all()

    return {
        "saldo_creditos": a.creditos or 0,
        "leads": [
            {
                "id": l.id,
                "empresa": l.empresa,
                "rubro": l.rubro,
                "tier": l.tier,
                "costo_creditos": l.costo_creditos or 0,
                "score_calidad": l.score_calidad or 50,
                "notas": l.notas_calificacion or "",
            }
            for l in leads
        ]
    }


@app.post("/bolsa/{id}/comprar")
def comprar_lead(id: int,
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
        raise HTTPException(400, f"Saldo insuficiente. Necesitás {costo} créditos, tenés {a.creditos or 0}.")

    reclamos_activos = db.query(LeadBolsa).filter(
        LeadBolsa.aliado_id == a.id, LeadBolsa.estado == "reclamado"
    ).count()
    if reclamos_activos >= LIMITE_RECLAMOS_ACTIVOS:
        raise HTTPException(400, f"Ya tenés {LIMITE_RECLAMOS_ACTIVOS} leads reclamados activos.")

    lead.estado = "reclamado"
    lead.aliado_id = a.id
    lead.fecha_reclamo = datetime.now()
    _ajustar_creditos(db, a, -costo, "compra_lead", f"lead:{lead.id}")
    db.commit()

    return {
        "mensaje": f"¡Lead premium comprado! Te descontamos {costo} créditos.",
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
    telefono: str
    whatsapp: str = ""
    email: str = ""
    tier: str = "basico"            # basico | calificado | premium
    costo_creditos: int = 0
    score_calidad: int = 50
    notas_calificacion: str = ""


@app.post("/admin/bolsa-v2")
def cargar_lead_bolsa_v2(lead: LeadBolsaCreateAdv, db: Session = Depends(get_db)):
    """Carga un lead con tier/costo. Reemplaza a /admin/bolsa cuando querés tier."""
    if lead.tier not in ("basico", "calificado", "premium"):
        raise HTTPException(400, "Tier inválido. Usá: basico | calificado | premium")
    nuevo = LeadBolsa(
        empresa=lead.empresa, rubro=lead.rubro,
        nombre_contacto=lead.nombre_contacto or None,
        ciudad=lead.ciudad or None,
        telefono=lead.telefono,
        whatsapp=lead.whatsapp or None,
        email=lead.email or None,
        estado="disponible",
        tier=lead.tier, costo_creditos=lead.costo_creditos,
        score_calidad=lead.score_calidad, notas_calificacion=lead.notas_calificacion,
    )
    db.add(nuevo); db.commit()
    _notificar_nuevo_lead_bolsa(db, lead.empresa, lead.rubro, lead.tier)
    return {"mensaje": f"Lead cargado en tier '{lead.tier}'."}


# ─── BOLSA: CARGA MASIVA (CSV) ───────────────────────────────────────────────

class LeadBolsaBulkPayload(BaseModel):
    leads: List[LeadBolsaCreateAdv]

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
            telefono=lead.telefono,
            whatsapp=lead.whatsapp or None,
            email=lead.email or None,
            estado="disponible",
            tier=tier, costo_creditos=lead.costo_creditos,
            score_calidad=lead.score_calidad, notas_calificacion=lead.notas_calificacion,
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
                f"{'<span style=\"color:#fbbf24;\">⭐</span>' if l.tier == 'calificado' else '<span style=\"color:#a78bfa;\">💎</span>' if l.tier == 'premium' else ''}"
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


@app.post("/admin/comunidad/{id}/ocultar")
def admin_ocultar_post(id: int, ocultar: bool = True, db: Session = Depends(get_db)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.oculto = ocultar; db.commit()
    return {"mensaje": "Post ocultado." if ocultar else "Post visible de nuevo."}


# ─── PORTAL PÚBLICO POR ALIADO (G lite) ──────────────────────────────────────
# URL pública /p/{ref_code} que muestra una landing mínima con el nombre del
# aliado, los planes y el botón de pago con atribución automática.

from fastapi.responses import HTMLResponse

@app.get("/p/{ref_code}", response_class=HTMLResponse)
def portal_publico_aliado(ref_code: str, db: Session = Depends(get_db)):
    """Landing pública del aliado con su marca/bio y CTA de pago."""
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code, Aliado.activo == True).first()
    if not a or not a.portal_publico_activo:
        return HTMLResponse("<h1>Portal no disponible</h1>", status_code=404)

    titular = a.portal_publico_titular or a.nombre
    bio = a.portal_publico_bio or f"Asesor digital — Partner de Avanza Digital"

    planes_html = ""
    for nombre_plan, precio in PLANES.items():
        planes_html += f"""
        <div style='background:#111;border:1px solid #222;border-radius:12px;padding:24px;margin-bottom:16px;'>
          <div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;'>
            <div>
              <h3 style='font-size:1.1rem;font-weight:800;margin-bottom:4px;'>{nombre_plan}</h3>
              <p style='color:#a1a1aa;font-size:0.85rem;'>USD {int(precio)}</p>
            </div>
            <a href='/checkout/crear?plan={nombre_plan}&ref_code={ref_code}' method='post'
               style='padding:12px 20px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;'>
              Contratar →
            </a>
          </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{titular} · Avanza Digital</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&display=swap" rel="stylesheet">
<style>
body{{margin:0;font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;}}
.wrap{{max-width:640px;margin:0 auto;padding:48px 24px;}}
.hero{{text-align:center;margin-bottom:40px;}}
.hero h1{{font-size:2rem;font-weight:900;margin-bottom:8px;}}
.hero p{{color:#a1a1aa;font-size:1rem;}}
.badge{{display:inline-block;background:rgba(59,130,246,0.15);color:#93c5fd;padding:4px 12px;
       border-radius:20px;font-size:0.72rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;
       margin-bottom:16px;}}
.footer{{margin-top:48px;text-align:center;color:#71717a;font-size:0.78rem;}}
a.cta{{display:inline-block;padding:12px 20px;background:#3b82f6;color:#fff!important;
       border-radius:8px;text-decoration:none;font-weight:700;}}
</style></head><body>
<div class="wrap">
  <div class="hero">
    <div class="badge">Asesor certificado · Avanza Partner Network</div>
    <h1>{titular}</h1>
    <p>{bio}</p>
  </div>
  <h2 style="font-size:1.2rem;font-weight:800;margin-bottom:16px;">Planes disponibles</h2>
  {planes_html}
  <div class="footer">
    <p>Al contratar desde este link, tu pago queda atribuido automáticamente a {titular}.</p>
    <p style="margin-top:8px;"><a href="https://avanzadigital.digital" style="color:#3b82f6;">avanzadigital.digital</a></p>
  </div>
</div>
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
    con totales agregados. Es la vista del panel de comisiones del portal."""
    a = _get_aliado(codigo, db)
    comisiones = db.query(Comision).filter(Comision.aliado_id == a.id)\
        .order_by(Comision.fecha_pago.desc().nullslast() if hasattr(Comision.fecha_pago, "desc") else Comision.id.desc()).all()

    items = [_comision_row(c) for c in comisiones]
    total_pendiente = round(sum(c.comision_usd for c in comisiones if c.estado == "pendiente"), 2)
    total_abonado   = round(sum(c.comision_usd for c in comisiones if c.estado == "abonada"), 2)

    return {
        "aliado": a.nombre,
        "codigo": a.codigo,
        "cbu_alias": a.cbu_alias,
        "total_pendiente_usd": total_pendiente,
        "total_abonado_usd":   total_abonado,
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
    """Devuelve los módulos de la Academia para el aliado, en orden.
    (En esta primera versión no trackeamos completitud por aliado; si en el
    futuro se agrega, mantener la misma forma de respuesta.)"""
    a = _get_aliado(codigo, db)  # Solo para validar que el código existe
    mods = db.query(AcademiaModulo).filter(AcademiaModulo.activo == True)\
        .order_by(AcademiaModulo.orden).all()
    return {
        "aliado": a.codigo,
        "total_modulos": len(mods),
        "modulos": [_modulo_row(m) for m in mods],
    }
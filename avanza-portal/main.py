import sys
from fastapi import FastAPI, Depends, HTTPException, Request, status, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from sqlalchemy.exc import OperationalError, ProgrammingError
from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from pydantic import Field as PydField
import re
from typing import Optional
from models import (
    EmailEnviado, Equipo,
    PushSubscription,
    Aliado, Admin, AdminAuditLog, Venta, Referido, Prospecto, AuditoriaLog, LeadBolsa,
    TransaccionCredito, PostComunidad, ComentarioComunidad, AutomationLog, ActividadProspecto, ContactoProspecto,
    LinkPago, Comision, AcademiaModulo, AliadoModuloCompletado,
    SolicitudCompraCreditos, ReporteMalContacto,
    PlanContinuidadActivo, PasswordResetToken, Novedad,
    PLANES, PAQUETES_CREDITOS, NIVELES, REPUTACION_BADGES,
    PLANES_CONTINUIDAD, COMISION_RECURRENTE_PCT,
)
import random, string, os, httpx, json, hmac as hmac_lib, hashlib, base64, sys, secrets
from apscheduler.schedulers.background import BackgroundScheduler

from database import engine, get_db, Base
from auth import (
    crear_token, current_aliado_required, current_admin_required,
    verify_ownership_dep, ADMIN_API_KEY, JWT_SECRET,
    decodificar_token, decodificar_token_ignorando_exp,
    JWT_REFRESH_WINDOW_HOURS,
)
import schemas

# ─── BOOTSTRAP IA MULTI-PROVEEDOR (Gemini/Groq/OpenRouter reemplazan a Claude) ──
# DEBE correr ANTES de importar los módulos jarvis_*, porque cada uno lee
# ANTHROPIC_API_KEY en su propio import. install() configura el ruteo a los
# proveedores con free tier y deja pasar los guards. Es reversible: si no hay
# ninguna key gratis (GEMINI/GROQ/OPENROUTER), no toca nada y todo sigue como antes.
import jarvis_llm
jarvis_llm.install()

import groq_ai  # IA opcional — si GROQ_API_KEY no está, todo cae a fallback heurístico
import jarvis_routes         # JARVIS — motor de inteligencia comercial (multi-proveedor vía jarvis_llm)
import jarvis_flywheel       # Motor del Flywheel Colectivo (Sección 6)
import jarvis_whatsapp       # Integración WhatsApp con Twilio (Sección 8)
import jarvis_api_publica    # JARVIS API pública v1 — autenticación por API key (Sección 9)
import jarvis_integraciones  # Integraciones CRM/Gmail/Calendar/Slack (Sección 10)
import jarvis_canal1         # Secuencia WhatsApp Canal 1 (onboarding, disparos, recurrentes)
import jarvis_setter         # JARVIS Setter — embudo WhatsApp-first de cara al prospecto (Funnelchat-style)
import jarvis_contratos_routes  # Generador de contratos de servicio en PDF (WeasyPrint)
import job_lock                 # Lock distribuido anti-duplicación de jobs (tabla job_runs)
import backup_db                # Backup diario de Postgres por email (Supabase free no tiene backups)

Base.metadata.create_all(bind=engine)

# Mejoras Canal 1 / Canal 2: columnas nuevas (aliados/bolsa_leads/referidos) +
# backfill de canales. Idempotente — corre en cada boot sin efectos. Va DESPUÉS
# de create_all (que ya creó la tabla `mentorias`).
import mejoras_canales
mejoras_canales.run_migrations(engine)


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
# Atribucion de equipo (handoff setter->closer)  Bloque 2
_aplicar_migracion("ALTER TABLE bolsa_leads ADD COLUMN setter_id INTEGER")
_aplicar_migracion("ALTER TABLE bolsa_leads ADD COLUMN setter_split_pct FLOAT")
_aplicar_migracion("ALTER TABLE prospectos ADD COLUMN setter_id INTEGER")
_aplicar_migracion("ALTER TABLE prospectos ADD COLUMN setter_split_pct FLOAT")
_aplicar_migracion("ALTER TABLE planes_continuidad_activos ADD COLUMN setter_id INTEGER")
_aplicar_migracion("ALTER TABLE planes_continuidad_activos ADD COLUMN setter_split_pct FLOAT")
_aplicar_migracion("ALTER TABLE aliados ADD COLUMN notif_inact_55d_en TIMESTAMP")
_aplicar_migracion("ALTER TABLE aliados ADD COLUMN clics_reclutamiento INTEGER DEFAULT 0")
_aplicar_migracion("ALTER TABLE aliados ADD COLUMN pwa_instalada BOOLEAN DEFAULT FALSE")
_aplicar_migracion("ALTER TABLE aliados ADD COLUMN pwa_detectado_en TIMESTAMP")

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
    # v2.1 — Métodos de cobro internacionales
    "ALTER TABLE aliados ADD COLUMN payment_method VARCHAR",
    "ALTER TABLE aliados ADD COLUMN payment_info VARCHAR",
    # v1.6 — Presencia digital en leads de bolsa
    "ALTER TABLE bolsa_leads ADD COLUMN web VARCHAR",
    "ALTER TABLE bolsa_leads ADD COLUMN instagram VARCHAR",
    "ALTER TABLE bolsa_leads ADD COLUMN tiene_web BOOLEAN DEFAULT FALSE",
    "ALTER TABLE bolsa_leads ADD COLUMN tiene_redes BOOLEAN DEFAULT FALSE",
    "ALTER TABLE bolsa_leads ADD COLUMN observacion TEXT",
    # v1.7 — Notificaciones de inactividad
    "ALTER TABLE aliados ADD COLUMN notif_inact_20d_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN notif_inact_30d_en TIMESTAMP",
    # v1.8 — Suspensión / eliminación automática + baja voluntaria
    "ALTER TABLE aliados ADD COLUMN fecha_suspension_auto TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN fecha_eliminacion_programada TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN baja_voluntaria_solicitada_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN baja_voluntaria_motivo TEXT",
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
    # v2.1 — Slug personalizado y foto de perfil del aliado
    # SIN ESTAS DOS LÍNEAS el checkout tira HTTP 500: SQLAlchemy hace SELECT *
    # y PostgreSQL responde UndefinedColumn porque el modelo las declara
    # pero la tabla no las tiene todavía.
    "ALTER TABLE aliados ADD COLUMN username VARCHAR UNIQUE",
    "ALTER TABLE aliados ADD COLUMN portal_publico_foto_url VARCHAR",
    # v2.2 — País del aliado (expansión Latam: AR, MX, CO, CL, PE…)
    # Separado de bolsa_leads (que ya tiene su pais desde v1.8).
    "ALTER TABLE aliados ADD COLUMN pais VARCHAR DEFAULT 'AR'",
    # CRM bridge — conversión lead→prospecto y recordatorios de tareas
    "ALTER TABLE bolsa_leads ADD COLUMN prospecto_id INTEGER",
    "ALTER TABLE actividades_prospecto ADD COLUMN recordatorio_enviado BOOLEAN DEFAULT FALSE",
    # v2.3 — Rubros de especialidad para SEO local por país y ciudad
    # JSON array: ["metalurgica","agro","logistica","clinica","tecnico"]
    "ALTER TABLE aliados ADD COLUMN rubros_especialidad TEXT DEFAULT '[]'",
    # v2.4 — WhatsApp Business (Twilio) + Flywheel colectivo
    "ALTER TABLE aliados ADD COLUMN whatsapp_numero VARCHAR",
    # v2.5 — Secuencia WA Canal 1 (onboarding + inactividad + recurrentes)
    "ALTER TABLE aliados ADD COLUMN canal1_wa_bienvenida_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN canal1_wa_d1_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN canal1_wa_d3_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN canal1_wa_d7_en TIMESTAMP",
    # v2.6 — Cobro de comisiones: datos estructurados de transferencia
    # (banco/titular/tipo/número, formato según país) + qué tipo de dato
    # es payment_info cuando el método es wise (email/teléfono/wisetag).
    "ALTER TABLE aliados ADD COLUMN cobro_banco VARCHAR",
    "ALTER TABLE aliados ADD COLUMN cobro_titular VARCHAR",
    "ALTER TABLE aliados ADD COLUMN cobro_numero_cuenta VARCHAR",
    "ALTER TABLE aliados ADD COLUMN cobro_tipo_cuenta VARCHAR",
    "ALTER TABLE aliados ADD COLUMN payment_info_tipo VARCHAR",
    "ALTER TABLE aliados ADD COLUMN canal1_wa_inact7_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN canal1_wa_inact30_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN canal1_wa_semanal_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN canal1_wa_mensual_en TIMESTAMP",
    # v2.6 — JARVIS: gate de acceso (beta cerrada)
    "ALTER TABLE aliados ADD COLUMN jarvis_habilitado BOOLEAN DEFAULT FALSE",
    # v2.7 — JARVIS: prueba gratis de 7 días (fecha de fin del trial)
    "ALTER TABLE aliados ADD COLUMN jarvis_trial_fin TIMESTAMP",
    # v3.0 — CRM: contacto estructurado, valor, cierre, etiquetas, próxima acción
    "ALTER TABLE prospectos ADD COLUMN email VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN telefono VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN whatsapp VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN valor_usd FLOAT",
    "ALTER TABLE prospectos ADD COLUMN fecha_cierre TIMESTAMP",
    "ALTER TABLE prospectos ADD COLUMN motivo_cierre VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN etiquetas VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN proxima_accion_en TIMESTAMP",
    # v3.1 — Mis Capturas: bandeja de leads de magnets (auditoría/calculadora/recursos)
    # visible para el aliado + puente a CRM. SIN estas columnas, SQLAlchemy hace
    # SELECT * sobre auditorias_log y PostgreSQL tira UndefinedColumn.
    "ALTER TABLE auditorias_log ADD COLUMN nombre VARCHAR",
    "ALTER TABLE auditorias_log ADD COLUMN telefono VARCHAR",
    "ALTER TABLE auditorias_log ADD COLUMN prospecto_id INTEGER",
    "ALTER TABLE auditorias_log ADD COLUMN visto_en TIMESTAMP",
    # v3.2 — Puente CRM → Referido (registrar para venta en 1 click desde la ficha)
    "ALTER TABLE referidos ADD COLUMN prospecto_id INTEGER",
    # v1.6 — 2FA TOTP del admin (opt-in). totp_enabled arranca en FALSE: un admin
    # existente sigue entrando solo con usuario + contraseña hasta que lo active.
    "ALTER TABLE admins ADD COLUMN totp_secret VARCHAR",
    "ALTER TABLE admins ADD COLUMN totp_enabled BOOLEAN DEFAULT FALSE",
    # Camino B — Foro de la comunidad (categorías, resuelto, estado de mejoras)
    "ALTER TABLE comunidad_posts ADD COLUMN categoria VARCHAR DEFAULT 'general'",
    "ALTER TABLE comunidad_posts ADD COLUMN resuelto BOOLEAN DEFAULT FALSE",
    "ALTER TABLE comunidad_posts ADD COLUMN estado_mejora VARCHAR",
    "ALTER TABLE comunidad_comentarios ADD COLUMN aceptada BOOLEAN DEFAULT FALSE",
    # v3.3 — Canal 1: alerta de "muchos contactos, cero ventas" (campanita + WA)
    "ALTER TABLE aliados ADD COLUMN canal1_alerta_sin_venta_en TIMESTAMP",
    # v3.4 — Bolsa: fuente de verificación de cada dato de contacto (prospección IA:
    # "places", "web propia", "instagram", etc. — vacío si el campo no se confirmó)
    "ALTER TABLE bolsa_leads ADD COLUMN fuente_dato VARCHAR",
]:
    _aplicar_migracion(col_sql)


# ─── BACKFILL: 7 días de prueba gratis de JARVIS para aliados YA registrados ──
# Los aliados nuevos reciben el trial automáticamente al registrarse (default
# del modelo). A los que ya existían (columna NULL) les damos 7 días contados
# desde este deploy, así "cualquier aliado registrado" tiene su semana gratis.
# Best-effort e idempotente: sólo toca filas con jarvis_trial_fin IS NULL, y si
# algo falla NO corta el arranque.
try:
    from sqlalchemy import update as _update_backfill
    _fin_trial_backfill = datetime.now() + timedelta(days=7)
    with engine.connect() as _conn_bf:
        _conn_bf.execute(
            _update_backfill(Aliado)
            .where(Aliado.jarvis_trial_fin.is_(None))
            .values(jarvis_trial_fin=_fin_trial_backfill)
        )
        _conn_bf.commit()
except Exception as _e_bf:
    print(f"[BACKFILL jarvis_trial_fin] omitido: {_e_bf}", file=sys.stderr)


# ─── EMAIL / NOVEDADES / PUSH → notificaciones.py ────────────────────────────
# Todo el stack de notificaciones (email Brevo→Resend→SMTP, campanita in-app,
# web push VAPID) vive ahora en notificaciones.py. Reimportamos los nombres
# para que el resto del archivo y los tests sigan funcionando sin cambios.
from notificaciones import (
    enviar_email, notificar_aliado, enviar_push_a_aliado, ADMIN_EMAIL,
)

# ─ MAILERLITE → capturas.py ─────────────────────────────────────────────────
# La API key y los group IDs por fuente viven en capturas.py, único consumidor
# (los usa /leads/capturar para suscribir cada captura al grupo de su fuente).

# ─── MERCADOPAGO ──────────────────────────────────────────────────────────────
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
MP_WEBHOOK_SECRET = os.environ.get("MP_WEBHOOK_SECRET", "")
# URL pública del BACKEND (donde viven los endpoints /webhooks/*). DEBE ser la URL real del backend.
# Los webhooks de Mercado Pago se configuran usando esta URL.
BACKEND_PUBLIC_URL = os.environ.get("BACKEND_PUBLIC_URL", "https://avanza-digital.onrender.com")
# URL del portal del aliado (para links en emails). Si backend y portal viven en el mismo dominio, coinciden.
PORTAL_URL      = os.environ.get("PORTAL_URL", BACKEND_PUBLIC_URL)

# ─── USDT TRC20 (TronGrid HD Wallet) ─────────────────────────────────────────
# TRON_XPUB (RECOMENDADO): clave pública extendida de la cuenta m/44'/195'/0'.
# Permite derivar direcciones de cobro SIN tener la semilla en el servidor.
# Generarla offline con `python generar_xpub_offline.py`. Ver tron_xpub.py.
TRON_XPUB           = os.environ.get("TRON_XPUB", "").strip()
# TRON_MNEMONIC (LEGACY, INSEGURO): semilla completa en el servidor. Solo se
# usa como fallback si TRON_XPUB no está configurada. Migrar y borrar.
TRON_MNEMONIC       = os.environ.get("TRON_MNEMONIC", "")
USDT_CONTRACT       = os.environ.get("USDT_CONTRACT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
TRONGRID_API_KEY    = os.environ.get("TRONGRID_API_KEY", "")
USDT_TOLERANCIA_PCT = float(os.environ.get("USDT_TOLERANCIA_PCT", "0.01"))
USDT_CONFIRMACIONES = int(os.environ.get("USDT_CONFIRMACIONES_MIN", "19"))

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
    "email_admin":      os.environ.get("BANK_EMAIL_ADMIN", "contacto@avanzadigital.digital"),
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
    "destinatario":   os.environ.get("USD_DESTINO", os.environ.get("USD_DESTINATARIO", "avanzadigital4@gmail.com")),
    "etiqueta_dest":  os.environ.get("USD_ETIQUETA",     "Dirección USDT (TRC20)"),
    "red":            os.environ.get("USD_RED",          ""),  # ej: "TRC20" para USDT
    "notas":          os.environ.get("USD_NOTAS",        "Enviá el monto exacto en USD. Recibís los créditos cuando confirmamos el pago (24hs hábiles)."),
}

# ─── Payoneer (segundo método USD para compra de créditos) ───────────────────
DATOS_PAYONEER = {
    "metodo":        "Payoneer",
    "destinatario":  os.environ.get("PAYONEER_EMAIL", os.environ.get("USD_DESTINATARIO", "avanzadigital4@gmail.com")),
    "etiqueta_dest": "Email de Payoneer",
    "red":           "",
    "notas":         os.environ.get("PAYONEER_NOTAS", "Podés enviar el monto exacto en USD al email de Payoneer (si también usás Payoneer) o por transferencia bancaria a los datos de abajo, desde cualquier banco. Recibís los créditos cuando confirmamos el pago (24hs hábiles)."),
    # Datos para transferencia bancaria a la cuenta USD de Payoneer (Global Payment Service).
    # Permite que cualquier empresa/aliado envíe USD por wire desde cualquier banco, sin tener Payoneer.
    "banco": {
        "banco":        os.environ.get("PAYONEER_BANCO",        "Citibank"),
        "direccion":    os.environ.get("PAYONEER_BANCO_DIR",    "111 Wall Street, New York, NY 10043, USA"),
        "aba":          os.environ.get("PAYONEER_ABA",          "031100209"),
        "swift":        os.environ.get("PAYONEER_SWIFT",        "CITIUS33"),
        "cuenta":       os.environ.get("PAYONEER_CUENTA",       "70589260002438922"),
        "tipo_cuenta":  os.environ.get("PAYONEER_TIPO_CUENTA",  "CHECKING"),
        "beneficiario": os.environ.get("PAYONEER_BENEFICIARIO", "Iván Darío Galarza"),
    },
}

# ─── USDT/USDC para clientes finales ─────────────────────────────────────────
# USD_DESTINO y USD_RED configuran el método de pago cripto en la landing
# pública /p/{ref_code}. Si USD_DESTINO no está definido, cae a USD_DESTINATARIO.
USDT_DIRECCION = os.environ.get("USD_DESTINO", os.environ.get("USD_DESTINATARIO", ""))
USDT_RED       = os.environ.get("USD_RED", "TRC20")


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
# (verificar_firma_mp migrada a checkout.py — la usa solo el webhook de MP.
#  MP_WEBHOOK_SECRET queda acá: es config/env, núcleo permanente.)


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
                  <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ir al Portal →</a>
                  <p style="margin-top:24px;font-size:.8rem;color:#64748b;">Avanza Digital · Partner Network</p>
                </div>
                """
                enviar_email(lead.aliado.email, f"⏰ Avanza: Tenés {horas_rest}hs para contactar a {lead.empresa}", html)
                # Recordatorio también por WhatsApp (urgente, alta conversión). Usa
                # la misma bandera notif_24h_enviada → se intenta una sola vez. Solo
                # aplica a Canal 1 con número (la función lo valida internamente).
                try:
                    jarvis_canal1.notificar_lead_sin_contactar(lead.aliado, lead.empresa, horas_rest)
                except Exception as _e:
                    print(f"[24H WA] no se pudo enviar WA a aliado {lead.aliado.codigo}: {_e}")
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

# ─── SCHEDULER: MONITOREO DE MEMORIA (diagnóstico OOM del 19/07) ────────────
# Job de solo-lectura: no toca la BD, no abre conexiones, no interactúa con
# ninguna ruta que usen los aliados. Objetivo: ver en Logs (gratis) cómo
# evoluciona la memoria del proceso, ya que el gráfico de Memory en Metrics
# es solo para planes pagos. Sacar este job una vez resuelto el diagnóstico.
def _memoria_actual_mb():
    """RSS actual del proceso en MB, leyendo /proc (Linux — Render corre en
    contenedores Linux). Devuelve None si no se puede leer (p.ej. local en
    Windows/Mac durante desarrollo)."""
    try:
        with open("/proc/self/status") as f:
            for linea in f:
                if linea.startswith("VmRSS:"):
                    return int(linea.split()[1]) / 1024
    except Exception:
        return None
    return None

def job_log_memoria():
    import resource
    limite_mb = 512
    actual_mb = _memoria_actual_mb()
    pico_mb   = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # ru_maxrss: KB en Linux
    actual_txt = f"{actual_mb:.1f} MB ({actual_mb / limite_mb * 100:.0f}%)" if actual_mb is not None else "n/d"
    print(f"[MEMORIA] PID {os.getpid()} — actual: {actual_txt} | pico histórico: {pico_mb:.1f} MB / {limite_mb} MB")

scheduler.add_job(job_log_memoria, "interval", minutes=10)

scheduler.add_job(job_lock.con_lock(job_notificaciones_24h, "notificaciones_24h", 3600), "interval", hours=1)


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
    """Corre cada hora. Marca como 'vencido' los links de pago cuya fecha expires_at ya pasó.
    Incluye los pagos manuales 'pendiente' (USDT/Payoneer que nadie reportó = miró y no
    pagó), para que no se acumulen en el panel de admin. Los 'reportado' NO se vencen:
    son pagos que alguien afirmó haber hecho y el admin debe verificar sí o sí."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        vencidos = db.query(LinkPago).filter(
            LinkPago.estado.in_(["activo", "pendiente"]),
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
        from checkout import _procesar_pago_confirmado  # diferido: el helper del dinero vive en checkout.py
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


scheduler.add_job(job_lock.con_lock(job_liberar_leads_48h, "liberar_leads_48h", 1800), "interval", minutes=30)
scheduler.add_job(job_lock.con_lock(job_expirar_links_pago, "expirar_links_pago", 3600), "interval", hours=1)
scheduler.add_job(job_lock.con_lock(job_verificar_pagos_usdt, "verificar_pagos_usdt", 30), "interval", seconds=30)


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

            # ── AVISO 30 DÍAS: suspender cuenta + créditos de reactivación ──
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

                # ── SUSPENDER + programar eliminación a 30 días ──────────────
                fecha_elim = ahora + timedelta(days=30)
                a.activo = False
                try:
                    a.fecha_suspension_auto        = ahora
                    a.fecha_eliminacion_programada = fecha_elim
                except Exception:
                    pass

                fecha_elim_str = fecha_elim.strftime("%d/%m/%Y")

                html = f"""
                <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                  <div style="margin-bottom:24px;">
                    <span style="background:#7f1d1d;color:#fca5a5;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">⚠️ Cuenta suspendida</span>
                  </div>
                  <h2 style="margin:0 0 12px;font-size:1.4rem;color:#f87171;">Hace {dias_inactivo} días que no entrás — suspendimos tu cuenta</h2>
                  <p style="color:#a1a1aa;line-height:1.6;">Hola <strong style="color:#fff;">{nombre_corto}</strong>, como llevás más de 30 días sin actividad, tu cuenta quedó suspendida temporalmente.</p>

                  <div style="background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.25);border-radius:8px;padding:18px;margin:20px 0;">
                    <p style="margin:0 0 6px;color:#c084fc;font-weight:700;font-size:1rem;">🎁 Te regalamos {BONUS_REACTIVACION} créditos para que vuelvas</p>
                    <p style="margin:0;color:#a1a1aa;font-size:.9rem;line-height:1.5;">Saldo nuevo: <strong style="color:#fff;">{saldo_nuevo} créditos</strong>. Usalos en el marketplace de leads premium del portal.</p>
                  </div>

                  <div style="background:rgba(248,113,113,0.06);border:1px solid rgba(248,113,113,0.2);border-radius:8px;padding:18px;margin:20px 0;">
                    <p style="margin:0 0 6px;color:#f87171;font-weight:700;">📅 Tenés tiempo hasta el {fecha_elim_str}</p>
                    <p style="margin:0;color:#a1a1aa;font-size:.88rem;line-height:1.5;">Si no reactivás tu cuenta antes de esa fecha, será eliminada definitivamente junto a tu código de aliado y todo el historial. Respondé este email o escribinos por WhatsApp para reactivarla gratis.</p>
                  </div>

                  <a href="{PORTAL_URL}/portal.html" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:8px;">Reactivar mi cuenta →</a>
                  <p style="margin-top:28px;font-size:.75rem;color:#3f3f46;">Avanza Digital · Partner Network · ¿No recordás tu contraseña? Usá el enlace de recuperación en el portal.</p>
                </div>
                """
                enviar_email(a.email, f"⚠️ {nombre_corto}, suspendimos tu cuenta — reactivala antes del {fecha_elim_str}", html, campania="inactividad_30d", aliado_id=a.id)
                try:
                    enviar_push_a_aliado(db, a.id, "Cuenta suspendida", f"Llevás {dias_inactivo} días sin entrar. Reactivala gratis antes del {fecha_elim_str}.", "/")
                except Exception:
                    pass
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
                enviar_email(a.email, f"👋 {nombre_corto}, ¿todo bien? Hace {dias_inactivo} días que no entrás", html, campania="inactividad_20d", aliado_id=a.id)
                try:
                    enviar_push_a_aliado(db, a.id, "¿Todo bien?", f"Hace {dias_inactivo} días que no entrás. Hay leads esperándote.", "/")
                except Exception:
                    pass
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
                  <p style="color:#a1a1aa;line-height:1.6;">Lo más importante para arrancar: <strong style="color:#fff;">todos los leads de la bolsa son gratis</strong>. No gastás un solo crédito en reclamarlos.</p>
                  <div style="background:#0f1d12;border:1px solid #14532d;border-radius:8px;padding:18px;margin:20px 0;">
                    <p style="margin:0 0 6px;color:#86efac;font-weight:700;">🎁 Empezá con un lead BÁSICO gratis</p>
                    <p style="margin:0;color:#a1a1aa;line-height:1.5;">Reclamalo desde la "Bolsa de Leads". Ningún lead consume créditos — básicos, calificados y premium se reclaman gratis. Empezá por un básico para practicar el guión.</p>
                  </div>
                  <p style="color:#a1a1aa;line-height:1.6;font-size:.92rem;">Tus 100 créditos de bienvenida son para <strong style="color:#fff;">Jarvis IA</strong>: tu asistente de ventas (analiza leads, arma propuestas, redacta seguimientos y maneja objeciones). Los leads no gastan créditos.</p>
                  <a href="{PORTAL_URL}/portal.html#bolsa" style="display:inline-block;padding:14px 28px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:12px;">Ver leads básicos →</a>
                </div>
                """
                try:
                    enviar_email(a.email, f"🎯 {nombre_corto}, así arrancás (sin gastar tus créditos)", html, campania="onboarding_d1", aliado_id=a.id)
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
                  <p style="color:#a1a1aa;line-height:1.6;">Ya probaste un básico. Ahora dale a los del tier <strong style="color:#c084fc;">"Calificado"</strong>: contactos pre-filtrados por nosotros, también <strong style="color:#c084fc;">gratis</strong>, con mayor tasa de cierre.</p>
                  <div style="background:#1a0a2e;border:1px solid #3b0764;border-radius:8px;padding:18px;margin:20px 0;">
                    <p style="margin:0 0 8px;color:#c084fc;font-weight:700;">Cómo elegir bien tu primer calificado:</p>
                    <ul style="margin:0;padding-left:18px;color:#a1a1aa;line-height:1.7;font-size:.92rem;">
                      <li>Mirá el <strong style="color:#fff;">rubro</strong>: elegí uno donde te sientas cómodo armando una propuesta</li>
                      <li>Mirá el <strong style="color:#fff;">score de calidad</strong>: arriba de 70 es seguro</li>
                      <li>Mirá si <strong style="color:#fff;">tiene web/redes</strong>: te da contexto para personalizar el pitch</li>
                    </ul>
                  </div>
                  <p style="color:#a1a1aa;line-height:1.5;font-size:.9rem;">Tip: si un contacto que reclamás resulta inválido, podés reportarlo dentro de las 72hs y lo liberamos de tu cupo de reclamos activos.</p>
                  <a href="{PORTAL_URL}/portal.html#bolsa" style="display:inline-block;padding:14px 28px;background:#a855f7;color:#fff;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:12px;">Ver leads calificados →</a>
                </div>
                """
                try:
                    enviar_email(a.email, f"⭐ {nombre_corto}, hora de probar un lead calificado", html, campania="onboarding_d3", aliado_id=a.id)
                    a.onboarding_email_d3_en = ahora
                    db.commit()
                    enviados_d3 += 1
                except Exception as e:
                    print(f"[ONBOARDING D3 ERROR] {a.codigo}: {e}")

            # ── DÍA 7 ───────────────────────────────────────────────────────
            # Solo si todavía NO usó Jarvis IA (no gastó créditos en jarvis).
            if dias_desde_registro >= 7 and not getattr(a, "onboarding_email_d7_en", None):
                uso_jarvis = db.query(TransaccionCredito).filter(
                    TransaccionCredito.aliado_id == a.id,
                    TransaccionCredito.motivo.like("jarvis%"),
                ).count()
                if uso_jarvis == 0:
                    saldo = a.creditos or 0
                    html = f"""
                    <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                      <span style="background:#1c1917;color:#fdba74;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">Día 7 · Activá a Jarvis IA</span>
                      <h2 style="margin:18px 0 12px;font-size:1.4rem;color:#fff;">{nombre_corto}, todavía no usaste a Jarvis IA</h2>
                      <p style="color:#a1a1aa;line-height:1.6;">Pasó una semana y tu saldo de <strong style="color:#fb923c;">{saldo} créditos</strong> sigue intacto. Esos créditos son para Jarvis IA, tu asistente de ventas: que te analice un lead o te arme una propuesta antes de la próxima llamada.</p>
                      <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:18px;margin:20px 0;">
                        <p style="margin:0 0 8px;color:#fff;font-weight:700;">¿Qué te frena?</p>
                        <p style="margin:0;color:#a1a1aa;line-height:1.6;font-size:.92rem;">Pedile a Jarvis que analice uno de tus leads reclamados o que te arme el guión para un rubro puntual. Si lo que falta es práctica, en la Academia hay guiones probados.</p>
                      </div>
                      <p style="color:#a1a1aa;line-height:1.5;font-size:.9rem;">Recordá: los leads son gratis, y Jarvis IA te ayuda a cerrarlos. Una propuesta bien armada paga muchas veces lo que cuesta generarla.</p>
                      <a href="{PORTAL_URL}/portal.html#jarvis" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;margin-top:12px;">Abrir Jarvis IA →</a>
                    </div>
                    """
                    try:
                        enviar_email(a.email, f"⏳ {nombre_corto}, tus {saldo} créditos de Jarvis IA siguen sin usar", html, campania="onboarding_d7", aliado_id=a.id)
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
        from comisiones import _generar_comisiones_recurrentes_del_mes  # diferido: el motor vive en comisiones.py
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


scheduler.add_job(job_lock.con_lock(job_notificaciones_inactividad, "notificaciones_inactividad", 86400), "interval", hours=24)

def job_push_digest_diario():
    """Cron 9hs: a cada aliado con push suscrito, UN solo aviso combinando
    leads nuevos (ultimas 24h) + tareas vencidas. Batch para no spamear."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        hace_24h = ahora - timedelta(days=1)
        leads_nuevos = db.query(LeadBolsa).filter(
            LeadBolsa.estado == "disponible",
            LeadBolsa.fecha_carga >= hace_24h,
        ).count()
        aliado_ids = [r[0] for r in db.query(PushSubscription.aliado_id).distinct().all()]
        for aid in aliado_ids:
            try:
                vencidas = db.query(ActividadProspecto).filter(
                    ActividadProspecto.aliado_id == aid,
                    ActividadProspecto.tipo == "tarea",
                    ActividadProspecto.completada == False,
                    ActividadProspecto.vence_en != None,
                    ActividadProspecto.vence_en < ahora,
                ).count()
                partes = []
                if leads_nuevos:
                    plu = "s" if leads_nuevos != 1 else ""
                    partes.append(f"{leads_nuevos} lead{plu} nuevo{plu} en la bolsa")
                if vencidas:
                    plu = "s" if vencidas != 1 else ""
                    partes.append(f"{vencidas} seguimiento{plu} vencido{plu}")
                if not partes:
                    continue
                cuerpo = "Buen dia! Tenes " + " y ".join(partes) + "."
                enviar_push_a_aliado(db, aid, "Avanza Digital", cuerpo, "/")
            except Exception as e:
                print(f"[DIGEST PUSH] aliado {aid}: {e}")
    except Exception as e:
        print(f"[DIGEST PUSH] {e}")
    finally:
        db.close()

scheduler.add_job(job_lock.con_lock(job_push_digest_diario, "push_digest_diario", 86400), "cron", hour=9)
# Backup diario de la base por email a las 6 AM UTC (~3 AM ARG, baja actividad).
# con_lock evita duplicados entre instancias y manda el error a Sentry si falla.
scheduler.add_job(job_lock.con_lock(backup_db.job_backup_diario, "backup_db_diario", 86400), "cron", hour=6)
scheduler.add_job(job_lock.con_lock(job_estipendio_mensual, "estipendio_mensual", 86400), "interval", hours=24)


# ─── SCHEDULER: ELIMINACIÓN DEFINITIVA (bajas voluntarias + inactividad) ──────
def job_eliminacion_definitiva():
    """Corre 1x/día. Elimina definitivamente cuentas que alcanzaron su fecha
    programada de eliminación, ya sea por:
      a) Baja voluntaria (baja_voluntaria_solicitada_en + 30d).
      b) Suspensión automática por inactividad (fecha_eliminacion_programada).

    Solo actúa si activo=False (la cuenta debe estar suspendida previamente).
    Usa el mismo flujo de eliminación en cascada que el endpoint admin DELETE.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        eliminados = 0

        # ── AVISO "ULTIMA OPORTUNIDAD": faltan <= 5 dias para el borrado ──
        try:
            limite_aviso = ahora + timedelta(days=5)
            no_repetir_uo = ahora - timedelta(days=10)
            por_borrar = db.query(Aliado).filter(
                Aliado.activo == False,
                Aliado.fecha_eliminacion_programada != None,
                Aliado.fecha_eliminacion_programada > ahora,
                Aliado.fecha_eliminacion_programada <= limite_aviso,
            ).all()
            for a in por_borrar:
                ya = getattr(a, "notif_inact_55d_en", None)
                if ya and ya > no_repetir_uo:
                    continue
                fstr = a.fecha_eliminacion_programada.strftime("%d/%m/%Y")
                nombre_corto = (a.nombre or "aliado").split()[0]
                if a.email:
                    try:
                        enviar_email(
                            a.email,
                            f"\u23f3 {nombre_corto}, tu cuenta se elimina el {fstr} \u2014 reactivala gratis",
                            f"<div style='font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:32px;max-width:560px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;'>"
                            f"<h2 style='color:#f87171;margin:0 0 12px;'>Ultima oportunidad</h2>"
                            f"<p style='color:#a1a1aa;line-height:1.6;'>Hola <strong style='color:#fff;'>{nombre_corto}</strong>, tu cuenta de aliado se elimina definitivamente el <strong style='color:#f87171;'>{fstr}</strong> junto con tu codigo y todo el historial.</p>"
                            f"<p style='color:#a1a1aa;line-height:1.6;'>Para conservarla, solo tenes que <strong style='color:#fff;'>iniciar sesion</strong> antes de esa fecha. Es gratis y se reactiva al instante.</p>"
                            f"</div>",
                        )
                    except Exception:
                        pass
                try:
                    enviar_push_a_aliado(db, a.id, "Ultima oportunidad",
                                         f"Tu cuenta se elimina el {fstr}. Entra para reactivarla gratis.", "/")
                except Exception:
                    pass
                try:
                    a.notif_inact_55d_en = ahora
                except Exception:
                    pass
            db.commit()
        except Exception as e:
            print(f"[ULTIMA OPORTUNIDAD] {e}")

        candidatos = (
            db.query(Aliado)
            .filter(
                Aliado.activo == False,
                Aliado.fecha_eliminacion_programada != None,
                Aliado.fecha_eliminacion_programada <= ahora,
            )
            .all()
        )

        for a in candidatos:
            codigo = a.codigo
            nombre = a.nombre
            try:
                # Reutilizar la lógica de eliminación en cascada
                from main import eliminar_aliado  # misma sesión no aplica; llamamos directo
            except Exception:
                pass

            # Eliminación en cascada inline (misma lógica que el endpoint admin)
            aid = a.id
            def _sp(fn):
                sp = db.begin_nested()
                try:
                    fn()
                    sp.commit()
                except Exception as e:
                    sp.rollback()
                    print(f"[job_eliminacion] SKIP — {type(e).__name__}: {e}", file=sys.stderr)

            try:
                prospecto_ids = [r[0] for r in db.query(Prospecto.id).filter(Prospecto.aliado_id == aid).all()]
                post_ids      = [r[0] for r in db.query(PostComunidad.id).filter(PostComunidad.aliado_id == aid).all()]

                if post_ids:
                    _sp(lambda: db.query(ComentarioComunidad).filter(ComentarioComunidad.post_id.in_(post_ids)).delete(synchronize_session=False))
                _sp(lambda: db.query(ComentarioComunidad).filter(ComentarioComunidad.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(PostComunidad).filter(PostComunidad.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(Comision).filter(Comision.aliado_id == aid).delete(synchronize_session=False))
                if prospecto_ids:
                    _sp(lambda: db.query(Comision).filter(Comision.prospecto_id.in_(prospecto_ids)).delete(synchronize_session=False))
                _sp(lambda: db.query(LinkPago).filter(LinkPago.aliado_id == aid).delete(synchronize_session=False))
                if prospecto_ids:
                    _sp(lambda: db.query(LinkPago).filter(LinkPago.prospecto_id.in_(prospecto_ids)).delete(synchronize_session=False))
                _sp(lambda: db.query(AutomationLog).filter(AutomationLog.aliado_id == aid).delete(synchronize_session=False))
                if prospecto_ids:
                    _sp(lambda: db.query(AutomationLog).filter(AutomationLog.prospecto_id.in_(prospecto_ids)).delete(synchronize_session=False))
                _sp(lambda: db.query(Venta).filter(Venta.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(Referido).filter(Referido.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(Prospecto).filter(Prospecto.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(TransaccionCredito).filter(TransaccionCredito.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(AuditoriaLog).filter(AuditoriaLog.aliado_id == aid).update({AuditoriaLog.aliado_id: None}, synchronize_session=False))
                _sp(lambda: db.query(LeadBolsa).filter(LeadBolsa.aliado_id == aid).update({LeadBolsa.aliado_id: None, LeadBolsa.estado: "disponible", LeadBolsa.fecha_reclamo: None}, synchronize_session=False))
                _sp(lambda: db.query(Aliado).filter(Aliado.sponsor_id == aid).update({Aliado.sponsor_id: None}, synchronize_session=False))

                _sp(lambda: db.query(EmailEnviado).filter(EmailEnviado.aliado_id == aid).update({EmailEnviado.aliado_id: None}, synchronize_session=False))
                _sp(lambda: db.query(Equipo).filter((Equipo.aliado_a_id == aid) | (Equipo.aliado_b_id == aid)).delete(synchronize_session=False))
                _sp(lambda: db.query(Novedad).filter(Novedad.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(ReporteMalContacto).filter(ReporteMalContacto.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(AliadoModuloCompletado).filter(AliadoModuloCompletado.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(SolicitudCompraCreditos).filter(SolicitudCompraCreditos.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(PlanContinuidadActivo).filter(PlanContinuidadActivo.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(PasswordResetToken).filter(PasswordResetToken.aliado_id == aid).delete(synchronize_session=False))
                _sp(lambda: db.query(PushSubscription).filter(PushSubscription.aliado_id == aid).delete(synchronize_session=False))
                db.delete(a)
                db.commit()
                eliminados += 1
                print(f"[job_eliminacion] ✅ Eliminado: {codigo} ({nombre})")

                # Notificar al admin
                try:
                    enviar_email(
                        ADMIN_EMAIL,
                        f"🗑️ Cuenta eliminada automáticamente: {nombre} ({codigo})",
                        f"<p style='font-family:sans-serif;'>La cuenta <strong>{nombre}</strong> ({codigo}) fue eliminada definitivamente por el job automático de eliminación.</p>"
                    )
                except Exception:
                    pass

            except Exception as e:
                db.rollback()
                print(f"[job_eliminacion] ERROR eliminando {codigo}: {type(e).__name__}: {e}", file=sys.stderr)

        print(f"[job_eliminacion] Cuentas eliminadas hoy: {eliminados}")
    except Exception as e:
        print(f"[job_eliminacion] ERROR general: {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        db.close()


scheduler.add_job(job_lock.con_lock(job_eliminacion_definitiva, "eliminacion_definitiva", 86400), "interval", hours=24)
scheduler.add_job(job_lock.con_lock(job_onboarding_sequence, "onboarding_sequence", 86400), "interval", hours=24)
scheduler.add_job(job_lock.con_lock(job_generar_comisiones_recurrentes_mensual, "comisiones_recurrentes_mensual", 86400), "interval", hours=24)

# ─── CANAL 1 — Secuencia WhatsApp ────────────────────────────────────────────
# Envíos salientes de WhatsApp por Twilio: DESACTIVADOS por defecto.
# La cuenta trial topa en 50 ms/día (429) y, fuera de la ventana de 24hs,
# WhatsApp exige templates aprobados. El onboarding/reactivación ya va por
# email (Brevo). Para reactivar cuando haya un sender de WhatsApp real,
# poner ENABLE_CANAL1_WA=1 en las env de Render.
ENABLE_CANAL1_WA = os.environ.get("ENABLE_CANAL1_WA", "0") == "1"
# Onboarding: cada hora. Por WhatsApp solo se manda el toque de DÍA 1; la
# secuencia educativa D3/D7 va por email (job_onboarding_sequence) para no
# duplicar el mismo empujón en ambos canales el mismo día.
if ENABLE_CANAL1_WA: scheduler.add_job(job_lock.con_lock(jarvis_canal1.job_onboarding_wa, "canal1_onboarding_wa", 3600),  "interval", hours=1)
# Inactividad por WhatsApp: DESACTIVADA a propósito. La reactivación por
# inactividad (7d/30d) + suspensión la maneja el email (job_notificaciones_inactividad),
# así no mandamos el mismo recordatorio por dos canales. Si algún día se quiere
# reactivar el canal WA para esto, descomentar la línea de abajo.
# scheduler.add_job(jarvis_canal1.job_inactividad_wa, "interval", hours=6)
# Leads semanales: lunes 9hs Argentina (UTC-3 → UTC+0 = 12hs UTC)
if ENABLE_CANAL1_WA: scheduler.add_job(job_lock.con_lock(jarvis_canal1.job_semanal_wa, "canal1_semanal_wa", 604800),     "cron", day_of_week="mon", hour=12, minute=0)
# Ranking mensual: día 1 de cada mes, 10hs Argentina (13hs UTC)
if ENABLE_CANAL1_WA: scheduler.add_job(job_lock.con_lock(jarvis_canal1.job_mensual_wa, "canal1_mensual_wa", 86400),     "cron", day=1, hour=13, minute=0)
# Alerta "muchos contactos, cero ventas": campanita del portal SIEMPRE activa
# (no depende de ENABLE_CANAL1_WA — el WA de esta alerta puntual lo gatea la
# propia función jarvis_canal1.notificar_contactos_sin_venta). Corre una vez
# por día, 14hs Argentina (17hs UTC).
scheduler.add_job(job_lock.con_lock(jarvis_canal1.job_alerta_contactos_sin_venta, "canal1_alerta_sin_venta", 86400), "cron", hour=17, minute=0)
# ─── SETTER — Secuencia de seguimiento a prospectos inbound ──────────────────
# Reactiva prospectos en 'calificando' sin respuesta (máx 3 toques). Cada 2hs.
scheduler.add_job(job_lock.con_lock(jarvis_setter.job_seguimientos, "setter_seguimientos", 7200),   "interval", hours=2)
scheduler.start()
jarvis_flywheel.agregar_insights_flywheel_al_scheduler(scheduler, get_db)


# (_tier_badge migrado a bolsa.py — lo usa el digest del bulk de leads.)


# ─── OBSERVABILIDAD: Sentry (opcional, no-op sin DSN) ────────────────────────
# Captura errores no manejados en endpoints, jobs del scheduler y webhooks de
# pago — fallas que hoy solo viven en los logs de Render y pasan inadvertidas.
# Se activa SOLO si SENTRY_DSN está seteada; sin la variable, este bloque no
# hace nada (ni siquiera importa el paquete), así que local y los tests corren
# igual que siempre. El free tier de sentry.io alcanza de sobra.
#
# Setup en producción (Render): crear proyecto Python en sentry.io, copiar el
# DSN y setear las env vars:
#   SENTRY_DSN=https://...ingest.sentry.io/...
#   SENTRY_ENVIRONMENT=production        (opcional; default "production")
#   SENTRY_TRACES_SAMPLE_RATE=0.0        (opcional; 0 = sin performance tracing)
SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
            release=os.environ.get("RENDER_GIT_COMMIT", "") or None,
            # Performance tracing apagado por default (no consume cuota del free
            # tier); subilo a 0.1 si querés muestrear latencias más adelante.
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            integrations=[StarletteIntegration(), FastApiIntegration()],
            # No mandar el cuerpo de los requests: pueden traer datos de pago.
            send_default_pii=False,
        )
        print(f"[SENTRY] OK Inicializado (env={os.environ.get('SENTRY_ENVIRONMENT', 'production')})")
    except ImportError:
        print("[SENTRY] SENTRY_DSN seteada pero falta el paquete 'sentry-sdk'. "
              "Agregalo a requirements.txt. Continuando sin Sentry.")
    except Exception as _e:
        print(f"[SENTRY] No se pudo inicializar ({_e}). Continuando sin Sentry.")
else:
    print("[SENTRY] Sin SENTRY_DSN — observabilidad externa desactivada (ok en local/tests).")


app = FastAPI(title="Avanza Partner Portal", version="1.5")

# ─── RATE LIMITING ───────────────────────────────────────────────────────────
# Usa slowapi (cliente in-memory por IP). Para multi-instancia mover a Redis.
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# La instancia del limiter vive en rate_limit.py para que los routers
# migrados (capturas.py) puedan decorar endpoints sin importar main.
from rate_limit import limiter
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

# ─── JARVIS — Registrar rutas de inteligencia comercial ──────────────────────
# Los endpoints viven en /jarvis/* y corren en paralelo al resto del portal.
# Si ANTHROPIC_API_KEY no está configurada, los endpoints devuelven fallback.
jarvis_routes.register(
    app, get_db, current_aliado_required,
    # _ajustar_creditos se define más abajo en este archivo; el lambda lo
    # resuelve en tiempo de request (cuando ya existe), evitando NameError.
    lambda *a, **k: _ajustar_creditos(*a, **k),
)
jarvis_flywheel.register(app, get_db, current_aliado_required)
jarvis_whatsapp.register(
    app, get_db, current_aliado_required,
    lambda *a, **k: _ajustar_creditos(*a, **k),   # cobro de créditos en WhatsApp
)
jarvis_api_publica.register(app, get_db, current_aliado_required, engine=engine)
jarvis_integraciones.register_integration_routes(app, get_db, current_aliado_required)  # Fix: integraciones/estado
jarvis_setter.register(app, get_db, current_aliado_required)   # Setter: /l/{slug}, /jarvis/setter/*
jarvis_setter.run_migrations(engine)                           # Crea setter_sesiones / setter_enlaces si no existen

# ─── MIGRACIÓN: columnas de revisión admin en Referido (rechazado + nota) ────
# Idempotente vía IF NOT EXISTS, corre sola al boot (mismo patrón que arriba).
try:
    with engine.begin() as _conn:
        _conn.execute(text("ALTER TABLE referidos ADD COLUMN IF NOT EXISTS rechazado BOOLEAN DEFAULT FALSE"))
        _conn.execute(text("ALTER TABLE referidos ADD COLUMN IF NOT EXISTS nota_admin TEXT"))
        _conn.execute(text("ALTER TABLE referidos ADD COLUMN IF NOT EXISTS nota_admin_en TIMESTAMP"))
        _conn.execute(text("ALTER TABLE referidos ADD COLUMN IF NOT EXISTS email TEXT"))
        _conn.execute(text("ALTER TABLE referidos ADD COLUMN IF NOT EXISTS whatsapp TEXT"))
    print("[REFERIDOS] Migración OK (rechazado, nota_admin, nota_admin_en, email, whatsapp)", flush=True)
except Exception as _e:
    print(f"[REFERIDOS] Error en migración de columnas admin: {_e}", file=sys.stderr)
jarvis_contratos_routes.register(app, get_db, current_aliado_required)  # POST /ventas/{id}/contrato y /contratos/preview → PDF del contrato

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
    ("GET",    "/aliados/activos-ahora"),
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
    ("POST",   "/admin/bolsa/bulk"),
    ("POST",   "/admin/bolsa/verificar-duplicados"),
    ("GET",    "/admin/bolsa"),
    ("POST",   "/admin/bolsa/{id}/revocar"),
    ("GET",    "/admin/historial-bolsa"),
    ("GET",    "/admin/reputacion/ranking"),
    ("POST",   "/admin/aliados/{codigo}/creditos"),
    ("POST",   "/admin/comunidad/{id}/fijar"),
    ("POST",   "/admin/comunidad/{id}/ocultar"),
    ("POST",   "/referidos/{id}/confirmar"),
    ("POST",   "/referidos/{id}/rechazar"),
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
    # v1.8 — bajas voluntarias pendientes de eliminación definitiva
    ("GET",    "/admin/bajas-voluntarias"),
    # v2.0 — métricas de cohorte de fuga
    ("GET",    "/admin/cohorte-fuga"),
    # v2.x — cohorte de registro × activación (salud del programa)
    ("GET",    "/admin/cohorte-activacion"),
    ("GET",    "/admin/uso-creditos"),
    # v1.6 — 2FA TOTP del admin (gestión protegida por JWT admin)
    ("POST",   "/admin/2fa/setup"),
    ("POST",   "/admin/2fa/activar"),
    ("POST",   "/admin/2fa/desactivar"),
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

# hash_password / verify_password QUEDAN acá a propósito: tests/conftest.py
# patchea main.pwd_context y estas dos funciones (bcrypt+passlib se llevan mal
# en 3.12). cuenta.py y aliados.py las usan vía puente diferido, así el patch
# les aplica también.
def hash_password(p): return pwd_context.hash(p)
def verify_password(plain, hashed): return pwd_context.verify(plain, hashed)


# ─ USERNAMES / SLUGS → aliados.py ───────────────────────────────────────────
# Las invariantes de identidad del aliado (USERNAMES_RESERVADOS, validación y
# normalización de slugs, generar_ref_code, generar_codigo_aliado) viven en
# aliados.py; cuenta.py las importa de allá para el registro y los endpoints
# de username.

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
    """Redirige a los términos del programa de aliados."""
    url = os.environ.get("URL_CONTRATO", "https://avanzadigital.digital/terminos-aliados.html")
    return RedirectResponse(url=url)


# ─ REGISTRO, LOGINS Y TOKENS → cuenta.py ────────────────────────────────────
# Username check/cambio, /registrarse, /admin/setup, /admin/login,
# /aliados/login, /auth/refresh y /auth/recuperar+resetear viven en cuenta.py
# como APIRouter — ver include_router al final del archivo.

# ─ GESTIÓN DE ALIADOS → aliados.py ──────────────────────────────────────────
# CRUD completo (listar, crear, ver, suspender/activar, eliminar en cascada,
# nivel), listas admin (suspendidos, bajas voluntarias, inactivos + trigger
# del job), /aliados/me, solicitar-baja y Mi Red — todo en aliados.py.

# ─── REFERIDOS ───────────────────────────────────────────────────────────────

@app.post("/referidos/registrar")
@limiter.limit("30/hour")
def registrar_referido(request: Request, body: schemas.RegistrarReferidoIn | None = Body(default=None),
                        ref_code: str = "", nombre_cliente: str = "", plan_elegido: str = "",
                        notas: str = "", email: str = "", whatsapp: str = "",
                        db: Session = Depends(get_db)):
    """Registra un referido público (NO requiere auth — el ref_code identifica
    al aliado). Acepta body JSON (preferido) o query (legacy)."""
    if body is not None:
        ref_code = body.ref_code
        nombre_cliente = body.nombre_cliente
        plan_elegido = body.plan_elegido
        notas = body.notas
        email = body.email
        whatsapp = body.whatsapp
    if not ref_code or not nombre_cliente or not plan_elegido:
        raise HTTPException(400, "Faltan ref_code, nombre_cliente o plan_elegido.")
    email = (email or "").strip()
    whatsapp = (whatsapp or "").strip()
    if not email:
        raise HTTPException(400, "Falta el email del cliente. Es obligatorio para registrar el prospecto.")
    if not whatsapp:
        raise HTTPException(400, "Falta el WhatsApp del cliente. Es obligatorio para registrar el prospecto.")
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not a: raise HTTPException(404, "Código de referido inválido.")
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "Referidos no disponibles para aliados Canal 2.")
    if plan_elegido not in PLANES: raise HTTPException(400, f"Plan inválido.")
    r = Referido(aliado_id=a.id, nombre_cliente=nombre_cliente, plan_elegido=plan_elegido, notas=notas,
                 email=email, whatsapp=whatsapp)
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
        for r in db.query(Referido).filter(Referido.acuse_recibo == False, Referido.rechazado == False).all()
    ]


@app.post("/referidos/{id}/confirmar")
def confirmar_referido(id: int, body: dict = Body(default={}), db: Session = Depends(get_db)):
    """Admin confirma manualmente un referido, con nota opcional visible
    para el aliado. (Protegido por middleware admin.)"""
    nota = (body.get("nota") or "").strip() if body else ""
    r = db.query(Referido).filter(Referido.id == id).first()
    if not r: raise HTTPException(404, "Referido no encontrado.")
    r.acuse_recibo = True
    r.rechazado = False  # por si se había rechazado antes por error y se revierte
    if nota:
        r.nota_admin = nota
        r.nota_admin_en = datetime.utcnow()
        notificar_aliado(
            db, r.aliado_id, tipo="referido_revisado",
            titulo=f"✅ Referido confirmado: {r.nombre_cliente}",
            cuerpo=nota, tab="pipeline",
        )
    db.commit()
    return {"mensaje": f"Referido de '{r.nombre_cliente}' confirmado."}


@app.post("/referidos/{id}/rechazar")
def rechazar_referido(id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    """Admin marca 'No confirmar' un referido y deja una nota visible para
    el aliado (ej: 'Esto es un prospecto, registralo cuando el cliente ya
    dijo que sí al plan'). (Protegido por middleware admin.)"""
    nota = (body.get("nota") or "").strip()
    if len(nota) < 3:
        raise HTTPException(400, "La nota es obligatoria (mínimo 3 caracteres) para que el aliado entienda por qué no se confirmó.")
    r = db.query(Referido).filter(Referido.id == id).first()
    if not r: raise HTTPException(404, "Referido no encontrado.")
    r.rechazado = True
    r.acuse_recibo = True  # queda revisado, ya no aparece en "pendientes"
    r.nota_admin = nota
    r.nota_admin_en = datetime.utcnow()
    notificar_aliado(
        db, r.aliado_id, tipo="referido_revisado",
        titulo=f"❌ Referido no confirmado: {r.nombre_cliente}",
        cuerpo=nota, tab="pipeline",
    )
    db.commit()
    return {"mensaje": f"Referido de '{r.nombre_cliente}' marcado como no confirmado. El aliado verá tu nota."}


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

    # 2. EFECTO RED: Si tiene Sponsor, override variable según ventas propias
    # del aliado que cerró (§6.2 — antes era 5% fijo).
    if getattr(a, "sponsor", None):
        _ovr_pct = a.override_pct_para_sponsor
        comision_sponsor = round(valor * _ovr_pct, 2)
        v_red = Venta(
            aliado_id=a.sponsor.id, 
            referido_id=None,
            nombre_cliente=f"♻️ RED: {a.nombre} (Venta: {nombre_cliente})",
            plan=plan, 
            valor_usd=valor, 
            comision_pct=_ovr_pct,
            comision_usd=comision_sponsor, 
            confirmada=True, pagada=False,
            fecha_venta=datetime.now(), modalidad_pago=modalidad_pago, 
            notas=f"Ingreso pasivo por venta de tu sub-aliado {a.nombre}"
        )
        db.add(v_red)
        a.sponsor.nivel = a.sponsor.nivel_calculado

    if referido_id:
        ref = db.query(Referido).filter(Referido.id == referido_id).first()
        if ref:
            ref.convertido = True
            # Canal 2: arranca la visibilidad de implementación para el aliado.
            import delivery
            delivery.iniciar_implementacion(db, ref)

    # 3. BONUS PRIMERA VENTA — créditos al aliado y al sponsor (si tiene).
    # Refuerza el loop "cerré → tengo más ammo para volver a cerrar".
    bonus_info = None
    if es_primera_venta:
        bonus_info = _aplicar_bonus_primera_venta(db, a, v.id)
    
    _nivel_anterior_reg = a.nivel  # capturar antes de actualizar
    a.nivel = a.nivel_calculado
    db.commit()

    # WhatsApp Canal 1: notificar venta y posible subida de nivel
    try:
        jarvis_canal1.notificar_venta_y_nivel(a, _nivel_anterior_reg, db)
    except Exception as _e:
        print(f"[CANAL1] Error notif venta/nivel (admin): {_e}", file=sys.stderr)

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
        "referidos_sin_confirmar": db.query(Referido).filter(Referido.acuse_recibo == False, Referido.rechazado == False).count(),
        "leaderboard": leaderboard,
    }



# ─── PROSPECTOS → prospectos.py ──────────────────────────────────────────────
# Todo el CRM de prospectos (CRUD, pipeline, bulk, timeline de actividades,
# tareas, contactos por empresa y puente a Referido) vive en prospectos.py.
# Los endpoints de IA sobre prospectos (perfilar, followup-ia, objecion-ia,
# analizar-perdida) siguen acá porque dependen del stack Jarvis/Groq.


# ─ AUDITORÍAS Y MIS CAPTURAS → capturas.py ──────────────────────────────────
# Todo el dominio de lead magnets (log de auditorías, /leads/capturar con
# MailerLite, aviso de lead caliente, bandeja Mis Capturas y el puente
# Capturas → CRM) vive en capturas.py como APIRouter — ver include_router
# al final del archivo.

# (Endpoints de novedades/campanita migrados a notificaciones.py.)

# ─── SIMULADOR DE GANANCIAS (config pública de planes/niveles) ───────────────

@app.get("/simulador/config")
def simulador_config():
    """Datos para el Simulador de Ganancias del aliado: planes one-time,
    planes de continuidad, % recurrente y comisión por nivel. Fuente única:
    las constantes de negocio de models.py (cero duplicación en el frontend)."""
    return {
        "planes": PLANES,
        "planes_continuidad": PLANES_CONTINUIDAD,
        "comision_recurrente_pct": COMISION_RECURRENTE_PCT,
        "niveles": {k: {"comision": v["comision"], "requisito": v["requisito"]}
                    for k, v in NIVELES.items()},
    }


@app.get("/admin/jarvis/uso")  # FIX: decorador perdido en un edit anterior — ruta restaurada
def admin_jarvis_uso(_admin=Depends(current_admin_required)):
    """Uso del motor de IA (tokens, llamadas, caché, errores) por modelo.
    Datos en memoria desde el último reinicio. Para histórico persistente,
    ver jarvis_llm.register_usage_sink()."""
    stats = jarvis_llm.usage_stats()
    stats["proveedores_activos"] = jarvis_llm.active_provider_label()
    return stats


# (Métricas /admin/auditorias migradas a capturas.py.)


# ─── HELPERS PRIVADOS ────────────────────────────────────────────────────────

def _get_aliado(codigo, db):
    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a: raise HTTPException(404, "Aliado no encontrado.")
    return a

def _get_prospecto(id, db):
    p = db.query(Prospecto).filter(Prospecto.id == id).first()
    if not p: raise HTTPException(404, "Prospecto no encontrado.")
    return p

# (Timeline CRM, tareas, contactos y _prospecto_row migrados a prospectos.py.)

# (_aliado_row y _aliado_detalle migrados a aliados.py — los usan también
# el registro y los logins de cuenta.py.)

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


# ─ CHECKOUT Y WEBHOOKS DE COBRO → checkout.py ───────────────────────────────
# Links de pago (MP + USDT), /checkout/*, webhooks de MercadoPago con HMAC,
# _procesar_pago_confirmado (re-exportado abajo para los tests), tipo de
# cambio e historial de links viven en checkout.py como APIRouter — ver
# include_router al final. Las constantes de pago (MP_*, USDT_*, TRON_*,
# SUCCESS/FAILURE_URL, DOLARAPI_URL) y los helpers transversales
# (obtener_tipo_de_cambio, _aplicar_bonus_primera_venta) quedan acá.

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
        {"id": "cbu", "titulo": "Configurá tu método de cobro", "completado": bool(getattr(a, "cbu_alias", None) or getattr(a, "payment_info", None))},
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


# ─ INTELIGENCIA COMERCIAL → ia_comercial.py ─────────────────────────────────
# Coach de onboarding, outreach (bolsa + prospectos), perfilado (heurístico
# e IA), follow-up, objeciones, análisis de venta perdida, asistente de
# comunidad y reputación (aliado + ranking admin) viven en ia_comercial.py
# como APIRouter — ver include_router al final. PATCH /prospectos/{id}/datos
# se mudó a prospectos.py (CRM puro). El piloto automático queda acá: son
# jobs del scheduler.

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
scheduler.add_job(job_lock.con_lock(job_piloto_automatico, "piloto_automatico", 3600), "interval", hours=1)

# ═══════════════════════════════════════════════════════════════════════════
# SALTO 2 — Regla liviana sobre JARVIS: detectar leads fríos y generar tarea
# ═══════════════════════════════════════════════════════════════════════════
LEADS_FRIOS_DIAS = 3  # sin movimiento N días → se considera frío

# ── NOTA DE DISEÑO ────────────────────────────────────────────────────────────
# Las sugerencias de JARVIS generadas por el cron son PLANTILLAS POR ETAPA,
# no llamadas al LLM (Anthropic/Claude). Decisión de costo: job_revisar_leads_frios
# puede correr sobre cientos o miles de prospectos diariamente; una inferencia
# LLM por prospecto sería prohibitivamente cara. El LLM se reserva para
# interacciones explícitas iniciadas por el aliado (contexto bajo demanda,
# baja frecuencia). Esta función es determinística a propósito.
# ──────────────────────────────────────────────────────────────────────────────
def _sugerencia_lead_frio(p) -> str:
    e = p.estado
    if e == "sin_contactar":
        return "JARVIS: hacé el primer contacto hoy, antes de que se enfríe."
    if e == "contactado":
        return "JARVIS: mandale la propuesta o un caso de éxito de su rubro."
    if e == "propuesta_enviada":
        return "JARVIS: seguimiento corto preguntando si tuvo dudas con la propuesta."
    if e == "respondio":
        return "JARVIS: proponé cerrar — pasale el link de pago con tu atribución."
    return "JARVIS: retomá el contacto con un mensaje breve."

def job_revisar_leads_frios():
    """Diario. Lead abierto, sin tarea pendiente y sin movimiento hace N días
       → crea una tarea de reactivación (con sugerencia de JARVIS) + nota de sistema.
       Idempotente: si ya hay una tarea pendiente, no genera otra."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora  = datetime.now()
        limite = ahora - timedelta(days=LEADS_FRIOS_DIAS)
        abiertos = db.query(Prospecto).filter(
            Prospecto.estado.in_(["sin_contactar", "contactado", "propuesta_enviada", "respondio"])
        ).all()
        creadas = 0
        for p in abiertos:
            # ¿ya tiene una tarea pendiente? entonces no agregamos otra
            tiene_tarea = db.query(ActividadProspecto).filter(
                ActividadProspecto.prospecto_id == p.id,
                ActividadProspecto.tipo == "tarea",
                ActividadProspecto.completada == False
            ).first()
            if tiene_tarea:
                continue
            # última señal de actividad
            ult = db.query(ActividadProspecto).filter(
                ActividadProspecto.prospecto_id == p.id
            ).order_by(ActividadProspecto.creado_en.desc()).first()
            ultima = (ult.creado_en if ult else None) or p.fecha_contacto or p.creado_en
            if not ultima or ultima > limite:
                continue  # tuvo movimiento reciente
            dias = int((ahora - ultima).total_seconds() // 86400)
            db.add(ActividadProspecto(
                prospecto_id=p.id, aliado_id=p.aliado_id, tipo="tarea",
                descripcion=f"Reactivar — sin movimiento hace {dias} días. {_sugerencia_lead_frio(p)}",
                vence_en=ahora, completada=False))
            db.add(ActividadProspecto(
                prospecto_id=p.id, aliado_id=p.aliado_id, tipo="sistema",
                descripcion=f"JARVIS detectó este lead frío ({dias} días sin movimiento) y generó una tarea."))
            p.proxima_accion_en = ahora
            creadas += 1
        db.commit()
        if creadas:
            print(f"[LEADS-FRIOS] {creadas} tareas de reactivación generadas.")
    except Exception as e:
        print(f"[LEADS-FRIOS ERROR] {e}")
    finally:
        db.close()

scheduler.add_job(job_lock.con_lock(job_revisar_leads_frios, "revisar_leads_frios", 86400), "interval", hours=24)



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

    # Estado de la prueba gratis de JARVIS (7 días). Si el trial sigue vigente,
    # JARVIS es gratis y el frontend NO debe mostrar el paywall.
    _trial_fin = getattr(a, "jarvis_trial_fin", None)
    _ahora = datetime.now()
    _trial_activo = bool(_trial_fin and _ahora <= _trial_fin)
    _dias_restantes = 0
    if _trial_activo:
        _dias_restantes = max(0, (_trial_fin - _ahora).days + 1)

    return {
        "saldo": a.creditos or 0,
        "jarvis_trial_activo": _trial_activo,
        "jarvis_trial_dias_restantes": _dias_restantes,
        "jarvis_trial_fin": _trial_fin.isoformat() if _trial_fin else None,
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


# (Marketplace y /bolsa/{id}/comprar migrados a bolsa.py.)


# (Reportes de mal contacto migrados a mal_contacto.py — ver include_router al final.)


# ─── MÉTRICAS DE COHORTE Y USO DE CRÉDITOS ───────────────────────────────────
# Endpoints solo-admin para detectar la cohorte de fuga (aliados que gastaron
# créditos en Jarvis IA sin cerrar venta) y para auditar el uso general.
# NOTA: los leads son gratis; el ÚNICO sink de créditos es Jarvis IA.

@app.get("/admin/cohorte-fuga")
def admin_cohorte_fuga(umbral_gasto: int = 80,
                        db: Session = Depends(get_db)):
    """Devuelve los aliados que gastaron al menos `umbral_gasto` créditos
    en Jarvis IA y tienen 0 ventas confirmadas.
    Esta es la cohorte clave para detectar fuga: si son pocos (2-3 de 37),
    el problema es individual y conviene contactarlos uno por uno. Si son
    muchos, el problema es sistémico — calidad de los leads, capacitación o
    el propio Jarvis — y meterles más créditos no va a arreglarlo.
    """
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    cohorte = []
    for a in aliados:
        # Total de créditos GASTADOS en Jarvis IA (único sink de créditos)
        gastados = db.query(TransaccionCredito).filter(
            TransaccionCredito.aliado_id == a.id,
            TransaccionCredito.motivo.like("jarvis%"),
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
                "acciones_jarvis": len(gastados),
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
            else "Cohorte grande → problema sistémico (calidad de leads, Jarvis o capacitación). Más créditos no arreglan."
        ),
        "aliados": cohorte,
    }


@app.get("/admin/cohorte-activacion")
def admin_cohorte_activacion(umbral_fuga: int = 80,
                             db: Session = Depends(get_db)):
    """Salud del programa cruzando COHORTE DE REGISTRO (mes de alta) × ACTIVACIÓN.

    Es la extensión natural de /admin/cohorte-fuga: en lugar de un único segmento
    de fuga, para cada cohorte de registro muestra cuántos aliados avanzaron por
    cada hito de activación, y la fuga pasa a ser UNA columna más (no el centro).

    Hitos (se reportan como conteos absolutos, NO como funnel estricto: un aliado
    puede cerrar venta sin haber gastado créditos de Jarvis, p. ej.):
      registrados → logueó → capturó lead → usó Jarvis → cerró venta
    Más dos columnas de "salud": en_fuga (gastó ≥umbral en Jarvis y 0 ventas) y
    suspendidos (activo == False, dado de baja por inactividad o voluntaria).

    Nota: la cohorte de registro es histórica, así que incluye aliados suspendidos
    (a diferencia de cohorte-fuga, que solo mira activos).
    """
    aliados = db.query(Aliado).all()

    # ── Conjuntos de activación en queries agregadas (sin N+1) ──────────────
    # Capturó ≥1 lead: con leads gratis, reclamar es el primer acto real de trabajo.
    ids_captura = {
        r[0] for r in db.query(LeadBolsa.aliado_id)
        .filter(LeadBolsa.aliado_id.isnot(None)).distinct().all()
    }
    # Usó Jarvis IA: gastó créditos (único sink de créditos hoy).
    ids_jarvis = {
        r[0] for r in db.query(TransaccionCredito.aliado_id)
        .filter(TransaccionCredito.motivo.like("jarvis%"),
                TransaccionCredito.delta < 0).distinct().all()
    }
    # Gasto total de Jarvis por aliado (para el umbral de fuga).
    gasto_jarvis = {
        aid: -int(suma or 0)
        for aid, suma in db.query(
            TransaccionCredito.aliado_id, func.sum(TransaccionCredito.delta)
        ).filter(
            TransaccionCredito.motivo.like("jarvis%"),
            TransaccionCredito.delta < 0,
        ).group_by(TransaccionCredito.aliado_id).all()
    }
    # Cerró ≥1 venta confirmada.
    ids_venta = {
        r[0] for r in db.query(Venta.aliado_id)
        .filter(Venta.confirmada == True).distinct().all()
    }

    # ── Agrupar por cohorte de registro (mes YYYY-MM) ───────────────────────
    cohortes: dict = {}
    for a in aliados:
        mes = a.creado_en.strftime("%Y-%m") if a.creado_en else "sin_fecha"
        c = cohortes.setdefault(mes, {
            "cohorte": mes, "registrados": 0, "logueo": 0, "onboarding": 0,
            "capturo_lead": 0, "uso_jarvis": 0, "cerro_venta": 0,
            "en_fuga": 0, "suspendidos": 0,
        })
        c["registrados"] += 1
        if getattr(a, "ultimo_login", None):
            c["logueo"] += 1
        if getattr(a, "onboarding_completado", False):
            c["onboarding"] += 1
        if a.id in ids_captura:
            c["capturo_lead"] += 1
        if a.id in ids_jarvis:
            c["uso_jarvis"] += 1
        tiene_venta = a.id in ids_venta
        if tiene_venta:
            c["cerro_venta"] += 1
        if gasto_jarvis.get(a.id, 0) >= umbral_fuga and not tiene_venta:
            c["en_fuga"] += 1
        if not a.activo:
            c["suspendidos"] += 1

    def pct(n, d):
        return round(100 * n / d, 1) if d else 0.0

    filas = sorted(cohortes.values(),
                   key=lambda c: (c["cohorte"] == "sin_fecha", c["cohorte"]))
    for c in filas:
        d = c["registrados"]
        c["pct_logueo"]     = pct(c["logueo"], d)
        c["pct_capturo"]    = pct(c["capturo_lead"], d)
        c["pct_uso_jarvis"] = pct(c["uso_jarvis"], d)
        c["pct_cerro"]      = pct(c["cerro_venta"], d)
        c["pct_fuga"]       = pct(c["en_fuga"], d)
        # "Activado" = capturó al menos un lead (primer acto de trabajo real).
        c["pct_activacion"] = c["pct_capturo"]

    claves = ["registrados", "logueo", "onboarding", "capturo_lead",
              "uso_jarvis", "cerro_venta", "en_fuga", "suspendidos"]
    tot = {k: sum(c[k] for c in filas) for k in claves}
    d = tot["registrados"]
    totales = {
        **tot,
        "pct_logueo":     pct(tot["logueo"], d),
        "pct_capturo":    pct(tot["capturo_lead"], d),
        "pct_uso_jarvis": pct(tot["uso_jarvis"], d),
        "pct_cerro":      pct(tot["cerro_venta"], d),
        "pct_fuga":       pct(tot["en_fuga"], d),
        "pct_activacion": pct(tot["capturo_lead"], d),
    }

    return {
        "umbral_fuga": umbral_fuga,
        "definicion": {
            "logueo":       "ultimo_login no nulo (entró al menos una vez tras el alta)",
            "capturo_lead": "reclamó ≥1 lead de la bolsa (leads gratis = primer acto de trabajo)",
            "uso_jarvis":   "gastó créditos en Jarvis IA (motivo jarvis*, delta<0)",
            "cerro_venta":  "≥1 venta confirmada",
            "en_fuga":      f"gastó ≥{umbral_fuga} créditos en Jarvis y tiene 0 ventas, ahora desglosado por cohorte de registro",
        },
        "cohortes": filas,
        "totales": totales,
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


# (/admin/bolsa-v2 y su modelo migrados a bolsa.py.)


# ─── COMPRA DE PAQUETES DE CRÉDITOS → solicitudes_creditos.py ────────────────
# El flujo completo de compra de paquetes (paquetes, solicitar, comprobante,
# confirmar/rechazar admin) vive ahora en solicitudes_creditos.py. Acá queda
# solo el job de expiración (registrado en el scheduler más abajo).

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


scheduler.add_job(job_lock.con_lock(job_expirar_solicitudes_creditos, "expirar_solicitudes_creditos", 3600), "interval", hours=1)


# ─── CRM: RECORDATORIOS DE TAREAS VENCIDAS ───────────────────────────────────
# Las tareas del CRM (ActividadProspecto tipo='tarea' con vence_en) hasta ahora
# solo se veían dentro del portal. Este job manda UN email-digest por aliado
# cuando se le vencen tareas sin completar, y marca recordatorio_enviado para
# no spamear. Si el envío falla, NO se marca: se reintenta en la próxima corrida.

def job_recordatorios_tareas():
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        vencidas = (db.query(ActividadProspecto)
                      .filter(ActividadProspecto.tipo == "tarea",
                              ActividadProspecto.completada == False,
                              ActividadProspecto.vence_en != None,
                              ActividadProspecto.vence_en <= ahora,
                              ActividadProspecto.recordatorio_enviado == False)
                      .order_by(ActividadProspecto.vence_en.asc())
                      .all())
        if not vencidas:
            return

        # Agrupar por aliado → un solo digest cada uno
        por_aliado: dict[int, list] = {}
        for t in vencidas:
            if t.aliado_id:
                por_aliado.setdefault(t.aliado_id, []).append(t)

        enviados = 0
        for aliado_id, tareas in por_aliado.items():
            a = db.query(Aliado).filter(Aliado.id == aliado_id).first()
            if not a or not a.email:
                # Sin email no hay a quién avisar: marcamos para no acumular.
                for t in tareas:
                    t.recordatorio_enviado = True
                continue

            nombre_corto = (a.nombre or "Aliado").split()[0]
            filas = []
            for t in tareas[:10]:  # techo defensivo por digest
                p = db.query(Prospecto).filter(Prospecto.id == t.prospecto_id).first()
                empresa = p.nombre if p else f"Prospecto #{t.prospecto_id}"
                vencio = t.vence_en.strftime("%d/%m %H:%M") if t.vence_en else "—"
                filas.append(
                    f"<tr style='border-bottom:1px solid #1e293b;'>"
                    f"<td style='padding:8px 12px;font-weight:600;'>{empresa}</td>"
                    f"<td style='padding:8px 12px;color:#cbd5e1;'>{(t.descripcion or 'Tarea')[:120]}</td>"
                    f"<td style='padding:8px 12px;color:#f87171;white-space:nowrap;'>{vencio}</td></tr>"
                )
            extra = (f"<p style='color:#a1a1aa;font-size:.85rem;'>…y {len(tareas)-10} tarea(s) más en tu CRM.</p>"
                     if len(tareas) > 10 else "")

            html = f"""
            <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:36px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
              <span style="background:#1c1917;color:#fdba74;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">⏰ Recordatorio CRM</span>
              <h2 style="margin:18px 0 10px;font-size:1.35rem;color:#fff;">{nombre_corto}, tenés {len(tareas)} tarea{'s' if len(tareas) != 1 else ''} vencida{'s' if len(tareas) != 1 else ''}</h2>
              <p style="color:#a1a1aa;line-height:1.6;">La mitad de las ventas se cierran en el segundo contacto — no dejes enfriar estos seguimientos:</p>
              <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:.88rem;">
                <thead><tr style="background:#1e293b;color:#94a3b8;text-align:left;">
                  <th style="padding:8px 12px;">Prospecto</th><th style="padding:8px 12px;">Tarea</th><th style="padding:8px 12px;">Vencía</th>
                </tr></thead>
                <tbody>{''.join(filas)}</tbody>
              </table>
              {extra}
              <a href="{PORTAL_URL}/portal.html" style="display:inline-block;padding:13px 26px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:.95rem;">Abrir Mi CRM →</a>
              <p style="margin-top:24px;font-size:.74rem;color:#3f3f46;">Te avisamos una sola vez por tarea. Completala en el CRM para frenar los recordatorios. Avanza Digital · Partner Network.</p>
            </div>
            """
            try:
                enviar_email(
                    a.email,
                    f"⏰ {nombre_corto}: {len(tareas)} tarea{'s' if len(tareas) != 1 else ''} de seguimiento vencida{'s' if len(tareas) != 1 else ''}",
                    html,
                )
                for t in tareas:
                    t.recordatorio_enviado = True
                enviados += 1
                notificar_aliado(
                    db, a.id, "tarea",
                    f"⏰ Tenés {len(tareas)} tarea{'s' if len(tareas) != 1 else ''} vencida{'s' if len(tareas) != 1 else ''}",
                    "No dejes enfriar esos seguimientos — completalas en Mi CRM.",
                    tab="pipeline",
                )
            except Exception as e:
                print(f"[RECORDATORIOS CRM] Falló email a {a.codigo}: {e}")

        db.commit()
        if enviados:
            print(f"[RECORDATORIOS CRM] {enviados} digest(s) de tareas vencidas enviados.")
    except Exception as e:
        print(f"[RECORDATORIOS CRM ERROR] {e}")
    finally:
        db.close()


scheduler.add_job(job_lock.con_lock(job_recordatorios_tareas, "recordatorios_tareas", 1800), "interval", minutes=30)


# ─ BOLSA: CARGA MASIVA Y DUPLICADOS → bolsa.py ──────────────────────────────

# ─── FINANCIACIÓN / CUOTAS — removido (sistema de pago único / mantenimiento mensual) ──


# ─── COMUNIDAD INTERNA (F) ───────────────────────────────────────────────────

# (Endpoints de comunidad migrados a comunidad.py — ver include_router al final.)

@app.get("/auditoria-digital")
def auditoria_digital_redirect():
    from fastapi.responses import FileResponse
    return FileResponse("auditoria-digital.html")

@app.get("/calculadora-ineficiencia")
def calculadora_ineficiencia_redirect():
    from fastapi.responses import FileResponse
    return FileResponse("calculadora-ineficiencia.html")


# (Datos públicos del aliado /aliados/wa-publica/{ref_code} → portal_publico.py.)

# ── SEGURIDAD: este endpoint es PÚBLICO (lo usa la landing de auditoría sin
# login). La versión anterior aceptaba un `prompt` crudo del cliente y lo
# reenviaba a Groq — eso lo convertía en un proxy de LLM abierto: cualquiera
# podía usarlo para inferencia gratis o inyectar instrucciones arbitrarias.
# Ahora el cliente manda SOLO métricas estructuradas de Lighthouse (números
# acotados, strings cortos sanitizados) y el prompt se construye acá adentro.

class MetricasLighthouseIn(BaseModel):
    """Métricas medidas por PageSpeed/Lighthouse. Todo acotado y validado."""
    perf_d:   int = PydField(ge=0, le=100)
    perf_m:   int = PydField(ge=0, le=100)
    seo:      int = PydField(ge=0, le=100)
    access:   int = PydField(ge=0, le=100)
    bp:       int = PydField(ge=0, le=100)
    fcp:      str = PydField(default="", max_length=20)
    lcp:      str = PydField(default="", max_length=20)
    fcp_m:    str = PydField(default="", max_length=20)
    lcp_m:    str = PydField(default="", max_length=20)
    tbt:      str = PydField(default="", max_length=20)
    cls:      str = PydField(default="", max_length=20)
    tti:      str = PydField(default="", max_length=20)
    si:       str = PydField(default="", max_length=20)
    https:    bool = False
    viewport: bool = False
    metadesc: bool = False
    crawl:    bool = False
    fontsize: bool = False
    taptarg:  bool = False


class AuditoriaIARequest(BaseModel):
    domain: str = PydField(max_length=253)
    metrics: MetricasLighthouseIn


_DOMINIO_RE = re.compile(r"^[a-z0-9.\-]{3,253}$")


def _construir_prompt_auditoria(domain: str, mx: "MetricasLighthouseIn", overall: int) -> str:
    """Arma el prompt de la auditoría 100% del lado del servidor.

    Los strings de tiempos (fcp/lcp/etc) se sanitizan a un set chico de
    caracteres por si alguien intenta meter instrucciones en esos campos."""
    def t(v: str) -> str:
        # Solo dígitos, separadores y unidades — suficiente para "1.8 s" / "120 ms"
        return re.sub(r"[^0-9.,\smsh]", "", (v or ""))[:20].strip() or "s/d"

    return f"""Analizá estos datos REALES de Google Lighthouse para "{domain}" y generá un reporte útil para una PYME industrial argentina.

DATOS REALES MEDIDOS:
- Performance Desktop: {mx.perf_d}/100
- Performance Mobile: {mx.perf_m}/100
- SEO: {mx.seo}/100
- Accesibilidad: {mx.access}/100
- Buenas Prácticas: {mx.bp}/100
- FCP Desktop: {t(mx.fcp)} | LCP Desktop: {t(mx.lcp)}
- FCP Mobile: {t(mx.fcp_m)} | LCP Mobile: {t(mx.lcp_m)}
- TBT: {t(mx.tbt)} | CLS: {t(mx.cls)} | TTI: {t(mx.tti)} | Speed Index: {t(mx.si)}
- HTTPS activo: {mx.https}
- Viewport configurado: {mx.viewport}
- Meta description: {mx.metadesc}
- Indexable por Google: {mx.crawl}
- Fuentes legibles mobile: {mx.fontsize}
- Tap targets OK: {mx.taptarg}
- Score general calculado: {overall}/100

Devolvé SOLO JSON válido sin backticks con esta estructura exacta:
{{
  "overall_score": {overall},
  "score_title": "<título corto según el score>",
  "score_verdict": "<2-3 oraciones concretas sobre qué significan estos datos para el negocio B2B>",
  "meta_tags":[
    {{"icon":"fas fa-tachometer-alt","text":"Desktop: {mx.perf_d}/100"}},
    {{"icon":"fas fa-mobile-alt","text":"Mobile: {mx.perf_m}/100"}},
    {{"icon":"fas fa-search","text":"SEO: {mx.seo}/100"}}
  ],
  "categories":[
    {{"key":"perf_d","icon":"⚡","icon_color":"#f97316","name":"Velocidad Desktop","subtitle":"Google Lighthouse real","score":{mx.perf_d},
     "findings":[<4 hallazgos {{"type":"ok|warn|bad","text":"..."}} sobre performance desktop usando FCP {t(mx.fcp)}, LCP {t(mx.lcp)} y Speed Index {t(mx.si)}>]}},
    {{"key":"perf_m","icon":"📱","icon_color":"#a78bfa","name":"Performance Mobile","subtitle":"Experiencia en celular","score":{mx.perf_m},
     "findings":[<4 hallazgos sobre mobile: score, viewport ({mx.viewport}), tamaño de texto ({mx.fontsize}), tap targets ({mx.taptarg})>]}},
    {{"key":"seo","icon":"🔍","icon_color":"#3b82f6","name":"SEO y Visibilidad","subtitle":"Indexación real en Google","score":{mx.seo},
     "findings":[<4 hallazgos: score SEO, meta description ({mx.metadesc}), indexabilidad ({mx.crawl}), un hallazgo SEO B2B adicional>]}},
    {{"key":"security","icon":"🔒","icon_color":"#34d399","name":"Seguridad","subtitle":"HTTPS y buenas prácticas","score":{mx.bp},
     "findings":[<3 hallazgos: HTTPS ({mx.https}), buenas prácticas ({mx.bp}/100), un hallazgo de seguridad concreto>]}},
    {{"key":"access","icon":"♿","icon_color":"#60a5fa","name":"Accesibilidad","subtitle":"Usabilidad","score":{mx.access},
     "findings":[<3 hallazgos sobre accesibilidad y su impacto en conversión B2B>]}},
    {{"key":"leads","icon":"🎯","icon_color":"#4ade80","name":"Captación de Leads B2B","subtitle":"Estimación de conversión","score":<promedio de perf_m, seo y bp>,
     "findings":[<4 hallazgos: impacto de velocidad en conversión, captación orgánica estimada, conversión desde mobile, recomendación concreta de mejora>]}},
    {{"key":"comercial","icon":"💼","icon_color":"#fb7185","name":"Potencial Comercial","subtitle":"Competitividad digital","score":<número según análisis general>,
     "findings":[<4 hallazgos: posición competitiva, oportunidad de mayor impacto, propuesta de valor digital, recomendación estratégica B2B>]}}
  ],
  "recommendations":[<5 objetos {{"priority":"alta|media|baja","title":"...","description":"...","impact":"..."}}: 2 alta, 2 media, 1 baja, basados en los problemas reales detectados>],
  "checklist":[<12 objetos {{"item":"...","state":"ok|warn|bad","impact":"..."}} cubriendo: HTTPS, indexabilidad, meta description, performance desktop, performance mobile, responsive, LCP, SEO técnico, accesibilidad, buenas prácticas, formulario de contacto, CRM/seguimiento de leads — con state coherente con los datos medidos>]
}}"""


@app.post("/auditoria-ia")
@limiter.limit("6/hour")
def auditoria_ia(request: Request, body: AuditoriaIARequest):
    """Genera el análisis de IA para la auditoría digital usando Groq.

    El prompt se construye server-side a partir de métricas estructuradas
    (ver MetricasLighthouseIn). Rate limit agresivo: es un endpoint público
    que consume cuota de LLM — 6/hora por IP alcanza de sobra para el uso
    legítimo (una auditoría por sitio) y frena el abuso.
    """
    import groq_ai, json as _json

    domain = (body.domain or "").strip().lower()
    if not _DOMINIO_RE.match(domain):
        raise HTTPException(400, "Dominio inválido.")

    mx = body.metrics
    overall = round(mx.perf_d * 0.25 + mx.perf_m * 0.25 + mx.seo * 0.2
                    + mx.bp * 0.15 + mx.access * 0.15)
    prompt = _construir_prompt_auditoria(domain, mx, overall)

    system = (
        "Sos un experto en marketing digital B2B industrial en Argentina. "
        "Analizás datos reales de Google Lighthouse para PyMEs industriales argentinas. "
        "Devolvés ÚNICAMENTE el JSON solicitado, sin texto adicional, sin bloques de código."
    )

    raw = groq_ai._chat(
        prompt,
        system,
        model=groq_ai.GROQ_MODEL_QUALITY,
        max_tokens=1200,
        temperature=0.4,
        json_mode=True,
    )

    if not raw:
        raise HTTPException(status_code=503, detail="Groq no disponible — usá el fallback heurístico.")

    try:
        data = _json.loads(raw)
    except Exception:
        obj = groq_ai._extract_json(raw)
        if not obj:
            raise HTTPException(status_code=502, detail="Respuesta de IA no parseable.")
        data = obj

    return data

# ─ PORTAL PÚBLICO → portal_publico.py ───────────────────────────────────────
# /p/{ref_code} (landing completa), /alias/{ref_code} y la config del portal
# viven en portal_publico.py. Queda acá /config/usdt (dominio de pagos).

# (/config/usdt migrado a checkout.py.)

# (PATCH /aliados/{codigo}/portal-publico migrado a portal_publico.py.)

# ═════════════════════════════════════════════════════════════════════════════
# v1.4 — ENDPOINTS NUEVOS (CBU, comisiones, academia, admin)
# ═════════════════════════════════════════════════════════════════════════════

# ─ MÉTODO DE COBRO Y CBU → aliados.py ───────────────────────────────────────

# (/aliado/cambiar-password migrado a cuenta.py.)


# ─ COMISIONES Y CONTINUIDAD → comisiones.py ─────────────────────────────────
# Comisiones del aliado y admin (listar/abonar), Planes de Continuidad
# (admin + auto-servicio) y el motor de comisiones recurrentes
# (_crear_comisiones_recurrentes_para_plan / _generar_comisiones_recurrentes_
# del_mes) viven en comisiones.py. El job mensual del scheduler queda acá e
# importa el motor diferido.

# (/admin/pagos migrado a checkout.py.)

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


# ─ ACADEMIA → academia.py ────────────────────────────────────────────────────
# Los endpoints de la Academia (públicos, de aliado y de admin) viven ahora en
# academia.py como APIRouter — ver include_router al final del archivo. Acá
# quedan solo el sembrado inicial y la constante del bonus (que academia.py
# importa de forma diferida para evitar ciclos).

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


# Bonus por completar un módulo de Academia. Bajo a propósito: el incentivo
# real es que el aliado se forme antes de quemar créditos en leads premium.
# (Lo importa academia.py — los endpoints de la Academia viven allá.)
BONUS_MODULO_COMPLETADO = 10


# ─── RESET DE CONTRASEÑA DE ALIADO (admin) ───────────────────────────────────
@app.post("/admin/asignar-sponsor")
def asignar_sponsor(
    request: Request,
    aliado_email: str = Body(..., description="Email del aliado a reparar"),
    sponsor_codigo: str = Body(..., description="Código del aliado que debe figurar como sponsor"),
    force: bool = Body(False, description="Si True, sobreescribe un sponsor ya asignado"),
    _admin=Depends(current_admin_required),
    db: Session = Depends(get_db),
):
    """
    Asigna manualmente el sponsor_id a un aliado que se registró sin él
    (por el bug del link de reclutamiento).
    Temporal — puede eliminarse una vez corregidos todos los casos afectados.
    """
    aliado = db.query(Aliado).filter(Aliado.email == aliado_email).first()
    if not aliado:
        raise HTTPException(404, f"Aliado con email '{aliado_email}' no encontrado.")

    sponsor = db.query(Aliado).filter(Aliado.codigo == sponsor_codigo).first()
    if not sponsor:
        raise HTTPException(404, f"Sponsor con código '{sponsor_codigo}' no encontrado.")

    if aliado.id == sponsor.id:
        raise HTTPException(400, "Un aliado no puede ser su propio sponsor.")

    if aliado.sponsor_id is not None and not force:
        raise HTTPException(400,
            f"El aliado ya tiene un sponsor asignado (sponsor_id={aliado.sponsor_id}). "
            "Si querés reemplazarlo igualmente, agregá \"force\": true al body."
        )

    aliado.sponsor_id = sponsor.id
    db.commit()
    db.refresh(aliado)

    return {
        "ok": True,
        "aliado": {"id": aliado.id, "nombre": aliado.nombre, "email": aliado.email},
        "sponsor": {"id": sponsor.id, "nombre": sponsor.nombre, "codigo": sponsor.codigo},
        "mensaje": f"✅ '{aliado.nombre}' ahora figura en la red de '{sponsor.nombre}'.",
    }


@app.get("/admin/sin-sponsor")
def listar_sin_sponsor(
    request: Request,
    desde: str = None,
    hasta: str = None,
    _admin=Depends(current_admin_required),
    db: Session = Depends(get_db),
):
    """
    Lista todos los aliados que NO tienen sponsor asignado (sponsor_id IS NULL).
    Util para identificar quienes se registraron por el bug del link de reclutamiento.

    Parametros opcionales de query string:
      ?desde=2025-01-15   solo aliados registrados a partir de esa fecha
      ?hasta=2025-06-01   solo aliados registrados hasta esa fecha

    Temporal — puede eliminarse una vez corregidos todos los casos afectados.
    """
    q = db.query(Aliado).filter(Aliado.sponsor_id == None)

    if desde:
        try:
            desde_dt = datetime.strptime(desde, "%Y-%m-%d")
            q = q.filter(Aliado.creado_en >= desde_dt)
        except ValueError:
            raise HTTPException(400, "Formato de desde invalido. Usa YYYY-MM-DD.")

    if hasta:
        try:
            hasta_dt = datetime.strptime(hasta, "%Y-%m-%d") + timedelta(days=1)
            q = q.filter(Aliado.creado_en < hasta_dt)
        except ValueError:
            raise HTTPException(400, "Formato de hasta invalido. Usa YYYY-MM-DD.")

    aliados = q.order_by(Aliado.creado_en.asc()).all()

    return {
        "total": len(aliados),
        "aliados": [
            {
                "id":        a.id,
                "codigo":    a.codigo,
                "nombre":    a.nombre,
                "email":     a.email,
                "whatsapp":  a.whatsapp,
                "creado_en": a.creado_en.isoformat() if a.creado_en else None,
            }
            for a in aliados
        ],
    }


# (/admin/reset-password-aliado migrado a cuenta.py.)


# ─── ROUTERS MIGRADOS (split de main.py) ─────────────────────────────────────
# Primer tramo del split en módulos: cada dominio migrado va como APIRouter en
# su propio archivo, siguiendo el patrón documentado en academia.py.
# Se registran al final para garantizar que `app` y todos los helpers que los
# routers importan de forma diferida ya existen.
import academia
import aliados
import bolsa
import capturas
import checkout
import comisiones
import comunidad
import chat
import cuenta
import ia_comercial
import portal_publico
import mal_contacto
import solicitudes_creditos
import prospectos
import notificaciones as _notificaciones_mod  # ya importado arriba; acá solo el router
import email_tracking      # Hueco 1: analítica de email
import equipos             # Feature Mi Equipo (setter+closer)
import referidos_aliados   # Hueco 2: loop de reclutamiento aliado→aliado
import onboarding          # Onboarding de clientes post-venta (reemplazo de Tally)
import eventos_uso         # Tracking de uso del portal (tabs/botones) → /admin/eventos-uso
import contratacion        # Solicitudes de "Contratar" del sitio público → mail de aviso

app.include_router(academia.router)
app.include_router(bolsa.router)
app.include_router(capturas.router)
app.include_router(comunidad.router)
app.include_router(chat.router)
app.include_router(mal_contacto.router)
app.include_router(solicitudes_creditos.router)
app.include_router(prospectos.router)
app.include_router(_notificaciones_mod.router)
app.include_router(cuenta.router)
app.include_router(aliados.router)
app.include_router(portal_publico.router)
app.include_router(ia_comercial.router)
app.include_router(checkout.router)
app.include_router(comisiones.router)

# ─── HUECOS 1 y 2 ────────────────────────────────────────────────────────────
app.include_router(email_tracking.router)       # /e/o, /e/c, /admin/email/metricas
app.include_router(referidos_aliados.router)     # /aliados/{codigo}/red
app.include_router(equipos.router)               # /aliados/{codigo}/equipo
app.include_router(onboarding.router)             # /onboarding + /admin/onboarding
app.include_router(eventos_uso.router)            # /eventos/log + /admin/eventos-uso
app.include_router(contratacion.router)           # /solicitudes/contratar

# ─── MEJORAS CANAL 1 / CANAL 2 ───────────────────────────────────────────────
import reciclado, delivery, reparto_visibilidad  # noqa: E402
app.include_router(reciclado.router)              # /bolsa/{id}/historial, /admin/bolsa/reciclados
app.include_router(delivery.router)               # /aliados/{cod}/entregas, /admin/entregas
app.include_router(reparto_visibilidad.router)    # /aliados/{cod}/reparto/...

# Job: devolver a la bolsa los leads cuyo cooldown de reciclado ya venció.
def job_reciclar_cooldowns():
    db = next(get_db())
    try:
        reciclado.procesar_cooldowns(db)
    finally:
        db.close()

# Job: avisar al aliado de Canal 2 cuando la implementación de su cliente se estanca.
def job_delivery_estancados():
    db = next(get_db())
    try:
        delivery.procesar_estancados(db)
    finally:
        db.close()

scheduler.add_job(job_lock.con_lock(job_reciclar_cooldowns, "reciclar_cooldowns", 1800), "interval", minutes=30)
scheduler.add_job(job_lock.con_lock(job_delivery_estancados, "delivery_estancados", 3600), "interval", hours=6)

# Job diario: cuando un referido entra por primera vez, acreditamos el bono de
# activación a su sponsor (idempotente). Mismo patrón que los demás jobs.
scheduler.add_job(
    job_lock.con_lock(referidos_aliados.job_referidos_activacion, "referidos_activacion", 86400),
    "interval", hours=24,
)

# Los endpoints de IA sobre prospectos que siguen en este archivo usan el
# helper de ownership que ahora vive en prospectos.py:
from prospectos import _get_prospecto_owned_or_admin  # noqa: E402
from bolsa import LIMITE_RECLAMOS_ACTIVOS  # noqa: E402 — re-export por compat (tests y consumidores viejos)
from checkout import _procesar_pago_confirmado  # noqa: E402 — re-export por compat (tests/test_checkout_y_webhooks.py)
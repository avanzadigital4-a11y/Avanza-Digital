"""
jarvis_canal1.py — Secuencia completa de mensajes WhatsApp para aliados Canal 1

ESTRUCTURA DE LA SECUENCIA:
  🟢 ONBOARDING (por evento/tiempo, primeros días)
      - Al registrarse      → Bienvenida + código + link grupo WA
      - Día 1 (+24hs)       → "¿Ya viste la bolsa de leads?"
      - Día 3               → Tip de pitch + link al portal
      - Día 7               → Urgencia SILVER ("1 venta y subís a 12%")

  🔵 POR ACCIÓN (disparo inmediato)
      - Primer lead reclamado  → 3 claves para cerrarlo
      - Primera venta          → Sube a SILVER (12%)
      - Sube a PREMIUM         → 15% de comisión
      - Sube a ELITE           → 20% — máximo nivel
      - 7 días sin actividad   → Reactivación suave
      - 30 días sin actividad  → Último intento antes de marcar inactivo

  🟡 RECURRENTES (scheduler)
      - Lunes 9hs             → Leads nuevos disponibles esa semana
      - Día 1 de cada mes     → Ranking top aliados + posición del aliado

INTEGRACIÓN EN main.py:
  import jarvis_canal1

  # En el endpoint de registro, luego de crear el aliado:
  jarvis_canal1.notificar_bienvenida(aliado, db)

  # En el endpoint de reclamo de lead:
  jarvis_canal1.notificar_primer_lead(aliado, db)

  # En el endpoint de confirmación de venta (luego de actualizar nivel):
  jarvis_canal1.notificar_venta_y_nivel(aliado, nivel_anterior, db)

  # En el scheduler (agregar junto a los otros jobs):
  scheduler.add_job(jarvis_canal1.job_onboarding_wa,   "interval", hours=1)
  scheduler.add_job(jarvis_canal1.job_inactividad_wa,  "interval", hours=6)
  scheduler.add_job(jarvis_canal1.job_semanal_wa,      "cron", day_of_week="mon", hour=9)
  scheduler.add_job(jarvis_canal1.job_mensual_wa,      "cron", day=1, hour=10)

MIGRACIONES REQUERIDAS (ya incluidas en MIGRATION_SQL abajo):
  ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_bienvenida_en TIMESTAMP;
  ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_d1_en TIMESTAMP;
  ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_d3_en TIMESTAMP;
  ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_d7_en TIMESTAMP;
  ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_inact7_en TIMESTAMP;
  ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_inact30_en TIMESTAMP;
  ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_semanal_en TIMESTAMP;
  ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_mensual_en TIMESTAMP;

FILOSOFÍA DE FRECUENCIA:
  - Máximo 1 mensaje semanal en régimen normal.
  - Los disparos por acción se suman encima de ese límite (son noticias buenas).
  - Los mensajes de inactividad son excepciones controladas con timestamps.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

# ─── CONFIG ───────────────────────────────────────────────────────────────────

PORTAL_URL         = os.environ.get("PORTAL_URL", "https://portal.avanzadigital.com")
GRUPO_WHATSAPP_URL = os.environ.get("CANAL1_GRUPO_WA_URL", "https://chat.whatsapp.com/XXXXXXXX")

# Anti-spam / protección de la calidad del número en envíos masivos (semanal/mensual):
# - WA_BATCH_DELAY: segundos de pausa entre envíos (no disparar 140 de golpe).
# - WA_BATCH_MAX: tope de envíos por corrida (circuit-breaker; subilo por env
#   cuando la cuenta de Twilio salga del trial de 50/día).
WA_BATCH_DELAY = float(os.environ.get("CANAL1_WA_BATCH_DELAY", "1.5"))
WA_BATCH_MAX   = int(os.environ.get("CANAL1_WA_BATCH_MAX", "500"))

# ─── ALERTA "MUCHOS CONTACTOS, CERO VENTAS" ──────────────────────────────────
# Cuántas empresas contactadas (estado != 'sin_contactar' en el CRM) disparan
# la alerta si el aliado todavía no cerró ninguna venta confirmada.
CANAL1_UMBRAL_CONTACTOS_SIN_VENTA = int(os.environ.get("CANAL1_UMBRAL_CONTACTOS_SIN_VENTA", "8"))
# Es un aviso ÚNICO por aliado (no un recordatorio recurrente): si ya lo
# contactamos y no cerró, insistir cada X días no ayuda — el flag de
# idempotencia (canal1_alerta_sin_venta_en) alcanza para no repetirlo nunca
# más, sin importar cuánto tiempo pase ni cuántas empresas más contacte.
# Número de WhatsApp de Avanza Digital (soporte/contacto) al que redirige la
# alerta. Mismo número que ya se usa públicamente en el sitio como contacto
# oficial (ver DATOS_BANCARIOS['whatsapp_link'] en main.py), configurable
# aparte por si algún día conviene separar el número de soporte a aliados.
SOPORTE_WA_NUMERO = os.environ.get("AVANZA_SOPORTE_WA_NUMERO", "5493424392759")

# ─── SQL DE MIGRACIONES ───────────────────────────────────────────────────────

MIGRATION_SQL = [
    "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_bienvenida_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_d1_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_d3_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_d7_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_inact7_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_inact30_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_semanal_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_wa_mensual_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS canal1_alerta_sin_venta_en TIMESTAMP",
]

# ─── HELPER: ENVIAR Y LOGUEAR ─────────────────────────────────────────────────


def _enviar(numero: str, mensaje: str, etiqueta: str) -> bool:
    """
    Wrapper sobre jarvis_whatsapp.enviar_whatsapp con logging.
    Retorna True si el envío fue exitoso.
    """
    try:
        from jarvis_whatsapp import enviar_whatsapp  # noqa: PLC0415
        resultado = enviar_whatsapp(numero, mensaje)
        if resultado.get("ok"):
            print(f"[CANAL1] ✓ {etiqueta} → {numero} (sid: {resultado.get('sid')})", flush=True)
            return True
        else:
            print(
                f"[CANAL1] ✗ {etiqueta} → {numero}: {resultado.get('error')}",
                file=sys.stderr,
            )
            return False
    except Exception as exc:
        print(f"[CANAL1] Error enviando {etiqueta}: {exc}", file=sys.stderr)
        return False


def _numero(aliado) -> Optional[str]:
    """Devuelve el número de WhatsApp del aliado si está registrado."""
    return getattr(aliado, "whatsapp", None) or getattr(aliado, "whatsapp_numero", None)


def _es_canal1(aliado) -> bool:
    return (getattr(aliado, "tipo_aliado", "canal1") or "canal1") == "canal1"


# ─── MENSAJES: ONBOARDING ────────────────────────────────────────────────────


def _msg_bienvenida(aliado) -> str:
    nombre  = aliado.nombre.split()[0] if aliado.nombre else "hola"
    codigo  = aliado.ref_code or aliado.codigo or "—"
    return (
        f"👋 ¡Bienvenido al programa de aliados Avanza Digital, {nombre}!\n\n"
        f"Tu código de aliado es: *{codigo}*\n"
        f"Usalo en cada referido para que la comisión te llegue directo a vos.\n\n"
        f"📱 Sumate al grupo de aliados para tips, leads compartidos y soporte:\n"
        f"{GRUPO_WHATSAPP_URL}\n\n"
        f"🔗 Tu portal: {PORTAL_URL}/portal.html\n\n"
        f"Arrancás con *10% de comisión (nivel BASIC)*. Con 1 venta en 6 meses "
        f"subís a SILVER y ganás 12%. ¡Vamos! 🚀"
    )


def _msg_dia1(aliado) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"¿Todo bien, {nombre}? 👀\n\n"
        f"Acordate que tenés *1 lead básico gratis* esperándote en la bolsa de leads.\n\n"
        f"Es tu primer prospecto real — ya tiene empresa, rubro y contacto. "
        f"Solo tenés que reclamarlo y llamar.\n\n"
        f"👉 {PORTAL_URL}/portal.html#bolsa"
    )


def _msg_dia3(aliado) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"💡 Tip rápido para tu primer llamada, {nombre}:\n\n"
        f"No arrances con \"te llamo de Avanza Digital\". "
        f"Arrancá con el problema del cliente:\n\n"
        f"_\"Hola, ¿tiene un momento? Le llamo porque veo que [empresa] "
        f"no aparece en Google cuando busco [rubro] en [ciudad]...\"_\n\n"
        f"Eso abre la conversación. Después sí presentás la solución.\n\n"
        f"Más recursos en tu portal → {PORTAL_URL}/portal.html"
    )


def _msg_dia7(aliado) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"¿Cómo vas, {nombre}? 🎯\n\n"
        f"Recordatorio rápido: con *1 sola venta* en los próximos 6 meses "
        f"pasás a nivel *SILVER* y tu comisión sube de 10% a *12%*.\n\n"
        f"En un plan de USD 1.190 eso son *USD 20 más por venta*. "
        f"En 5 ventas son USD 100 extra.\n\n"
        f"¿Tenés leads en vista? Si querés te ayudo a armar el pitch."
    )


# ─── MENSAJES: POR ACCIÓN ────────────────────────────────────────────────────


def _msg_primer_lead(aliado) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"¡Buenísimo, {nombre}! Ya tenés tu primer lead reclamado. 🎯\n\n"
        f"3 claves para cerrarlo:\n\n"
        f"1️⃣ *Contactalo hoy* — la tasa de cierre cae a la mitad si esperás más de 24hs.\n"
        f"2️⃣ *Arrancá por el problema*, no por el producto. "
        f"\"¿Cuántos clientes perdiste por no tener presencia digital?\"\n"
        f"3️⃣ *Mandá el link de pago desde el portal* — le da seriedad "
        f"y vos cobrás la comisión automático.\n\n"
        f"¿Necesitás ayuda con el pitch? Respondé acá y te armo el guión. 💬"
    )


def _msg_primera_venta(aliado) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"🎉 *¡PRIMERA VENTA, {nombre.upper()}!*\n\n"
        f"Acabás de hacer tu primera venta confirmada. "
        f"A partir de ahora sos *nivel SILVER*.\n\n"
        f"✅ Tu comisión subió de 10% → *12%* en cada cierre.\n\n"
        f"Siguiente meta: 2 ventas en 6 meses = *PREMIUM (15%)*. "
        f"Ya tenés una, te falta una sola más.\n\n"
        f"¡Seguí así! 🚀"
    )


def _msg_subida_premium(aliado) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"💪 *¡Nivel PREMIUM, {nombre}!*\n\n"
        f"Confirmamos tu segunda venta. Pasaste al nivel *PREMIUM*.\n\n"
        f"💰 Comisión: *15%* en cada cierre desde ahora.\n\n"
        f"Siguiente meta: 5 ventas en 6 meses = *ELITE (20%)*. "
        f"Ya llevás 2. Tres más y llegás al máximo nivel del network.\n\n"
        f"Ver tu avance en el portal → {PORTAL_URL}/portal.html"
    )


def _msg_subida_elite(aliado) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"👑 *¡ELITE, {nombre}!*\n\n"
        f"Máximo nivel del programa. "
        f"A partir de ahora ganás *20% de comisión* en cada cierre.\n\n"
        f"En un plan Pro de USD 3.490 eso son *USD 580 por venta*.\n\n"
        f"Sos de los mejores aliados del network. "
        f"Si querés escalar más, hablemos de sub-aliados y estructura de red. 🏆"
    )


def _msg_alerta_contactos_sin_venta(aliado, n_contactados: int) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"Hola {nombre} 👀\n\n"
        f"Notamos que ya contactaste *{n_contactados} empresas* pero todavía no "
        f"lograste tu primera venta.\n\n"
        f"A veces el problema no es el esfuerzo, es el enfoque del pitch o el "
        f"tipo de empresa que estás eligiendo. Te ayudamos a destrabarlo.\n\n"
        f"👉 Escribinos y lo vemos juntos: https://wa.me/{SOPORTE_WA_NUMERO}"
        f"?text={_urlquote_wa('Hola, soy aliado Canal 1 (código ' + (aliado.ref_code or aliado.codigo or '') + ') y quiero ayuda para cerrar mi primera venta.')}"
    )


def _urlquote_wa(texto: str) -> str:
    from urllib.parse import quote as _quote  # noqa: PLC0415
    return _quote(texto, safe="")


def _msg_inactividad_7d(aliado) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"Hola {nombre}, hace 7 días que no te vemos por el portal 👀\n\n"
        f"¿Todo bien? Hay *leads nuevos* cargados esta semana que todavía "
        f"no fueron reclamados.\n\n"
        f"Si algo no está funcionando o tenés alguna duda, respondé acá. "
        f"Estamos.\n\n"
        f"👉 {PORTAL_URL}/portal.html#bolsa"
    )


def _msg_inactividad_30d(aliado) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    return (
        f"Hola {nombre} 👋\n\n"
        f"Hace 30 días que no ingresás al portal. "
        f"Lamentamos perderte.\n\n"
        f"Si querés retomar, tu cuenta sigue activa y hay leads nuevos. "
        f"Solo hace falta entrar:\n"
        f"👉 {PORTAL_URL}/portal.html\n\n"
        f"Si decidiste pausar por ahora, sin problema. "
        f"Podés reactivar cuando quieras. ¡Éxitos!"
    )


# ─── MENSAJES: RECURRENTES ───────────────────────────────────────────────────


def _msg_semanal(aliado, n_leads: int) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    if n_leads == 0:
        return (
            f"Buenos días {nombre} ☀️\n\n"
            f"Esta semana no hay leads nuevos en la bolsa todavía, "
            f"pero vas a recibir aviso en cuanto entren.\n\n"
            f"Mientras, ¿tenés prospectos propios para trabajar? "
            f"Te puedo ayudar a armar el pitch desde el portal."
        )
    leads_txt = f"{n_leads} lead{'s' if n_leads > 1 else ''} nuevo{'s' if n_leads > 1 else ''}"
    return (
        f"Buenos días {nombre} ☀️\n\n"
        f"Esta semana hay *{leads_txt}* disponibles en la bolsa.\n\n"
        f"Los leads básicos son gratis, los premium cuestan créditos. "
        f"Los primeros en reclamar se los llevan.\n\n"
        f"👉 {PORTAL_URL}/portal.html#bolsa"
    )


def _msg_mensual_ranking(aliado, posicion: int, total: int, ventas_propias: int) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    nivel  = getattr(aliado, "nivel", "BASIC")
    emoji_nivel = {"BASIC": "🌱", "SILVER": "⚡", "PREMIUM": "💎", "ELITE": "👑"}.get(nivel, "")
    return (
        f"📊 *Ranking mensual de aliados — {datetime.now().strftime('%B %Y').capitalize()}*\n\n"
        f"Tu posición: *#{posicion}* de {total} aliados activos {emoji_nivel}\n"
        f"Tus ventas este mes: *{ventas_propias}*\n"
        f"Nivel actual: *{nivel}*\n\n"
        f"El ranking completo está en tu portal:\n"
        f"👉 {PORTAL_URL}/portal.html#ranking\n\n"
        f"{'¡Arriba, que podés subir posiciones este mes! 💪' if posicion > 3 else '¡Estás en el top! Seguí así. 🏆'}"
    )


def _msg_lead_24h(aliado, empresa: str, horas_rest: int) -> str:
    nombre = aliado.nombre.split()[0] if aliado.nombre else ""
    empresa = empresa or "tu lead"
    return (
        f"⏰ {nombre}, ojo con tu lead\n\n"
        f"Reclamaste a *{empresa}* hace 24 h y todavía no lo marcaste como contactado.\n\n"
        f"Te quedan ~{horas_rest} h antes de que vuelva a la bolsa y lo agarre otro aliado. "
        f"Un llamado o un WhatsApp ahora puede ser la diferencia.\n\n"
        f"👉 Contactalo y actualizá el estado en el portal: {PORTAL_URL}/portal.html"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE DISPARO POR EVENTO (llamar desde los endpoints de main.py)
# ═══════════════════════════════════════════════════════════════════════════════


def notificar_bienvenida(aliado, db) -> bool:
    """
    Llamar inmediatamente después de crear el aliado (registro).
    Envía bienvenida + código + link al grupo WA.
    """
    if not _es_canal1(aliado):
        return False
    numero = _numero(aliado)
    if not numero:
        return False
    # Idempotencia: no reenviar si ya se mandó
    if getattr(aliado, "canal1_wa_bienvenida_en", None):
        return False

    ok = _enviar(numero, _msg_bienvenida(aliado), "bienvenida")
    if ok:
        try:
            aliado.canal1_wa_bienvenida_en = datetime.utcnow()
            db.commit()
        except Exception as exc:
            db.rollback()
            print(f"[CANAL1] Error guardando canal1_wa_bienvenida_en: {exc}", file=sys.stderr)
    return ok


def notificar_primer_lead(aliado, db) -> bool:
    """
    Llamar cuando el aliado reclama su primer lead de la bolsa.
    Envía los 3 tips para cerrar el lead.
    No tiene flag de idempotencia propio — se asume que el caller solo lo llama
    cuando es efectivamente el primer lead (n_leads_bolsa pasó de 0 a 1).
    """
    if not _es_canal1(aliado):
        return False
    numero = _numero(aliado)
    if not numero:
        return False
    return _enviar(numero, _msg_primer_lead(aliado), "primer_lead")


def notificar_lead_sin_contactar(aliado, empresa: str, horas_rest: int) -> bool:
    """
    Llamar desde el job de recordatorio de 24h (main.py) cuando un lead reclamado
    sigue sin contactar y está por liberarse. Recordatorio urgente por WhatsApp.
    La idempotencia la maneja el caller con la bandera notif_24h_enviada del lead.
    """
    if not _es_canal1(aliado):
        return False
    numero = _numero(aliado)
    if not numero:
        return False
    return _enviar(numero, _msg_lead_24h(aliado, empresa, horas_rest),
                   f"lead_24h aliado={aliado.id}")


def notificar_venta_y_nivel(aliado, nivel_anterior: str, db) -> bool:
    """
    Llamar después de confirmar una venta y actualizar el nivel del aliado.
    Detecta automáticamente si es primera venta o subida de nivel.

    nivel_anterior: el nivel ANTES de la venta confirmada ("BASIC", "SILVER", etc.)
    """
    if not _es_canal1(aliado):
        return False
    numero = _numero(aliado)
    if not numero:
        return False

    nivel_nuevo = getattr(aliado, "nivel", "BASIC")

    # Subida a ELITE
    if nivel_nuevo == "ELITE" and nivel_anterior != "ELITE":
        return _enviar(numero, _msg_subida_elite(aliado), "subida_elite")

    # Subida a PREMIUM
    if nivel_nuevo == "PREMIUM" and nivel_anterior not in ("PREMIUM", "ELITE"):
        return _enviar(numero, _msg_subida_premium(aliado), "subida_premium")

    # Primera venta (BASIC → SILVER)
    if nivel_nuevo == "SILVER" and nivel_anterior == "BASIC":
        return _enviar(numero, _msg_primera_venta(aliado), "primera_venta")

    # Venta confirmada sin cambio de nivel (ya es ELITE o venta adicional)
    # No enviamos nada extra para no saturar — la información está en el portal.
    return False


def notificar_contactos_sin_venta(aliado, n_contactados: int, db) -> bool:
    """
    Dispara la alerta de "muchas empresas contactadas, cero ventas": crea la
    novedad in-app (campanita del portal) SIEMPRE que el aliado sea Canal 1,
    y además manda WhatsApp si el canal WA está habilitado (ENABLE_CANAL1_WA)
    y el aliado tiene número cargado.

    No hace commit propio de la novedad (mismo patrón que notificar_aliado:
    la maneja el caller), pero SÍ commitea el timestamp de idempotencia acá
    porque el caller (job_alerta_contactos_sin_venta) es quien la dispara.
    """
    if not _es_canal1(aliado):
        return False

    try:
        from notificaciones import notificar_aliado  # noqa: PLC0415

        wa_link = (
            f"https://wa.me/{SOPORTE_WA_NUMERO}?text="
            + _urlquote_wa(
                "Hola, soy aliado Canal 1 (código "
                + (aliado.ref_code or aliado.codigo or "") + ") y quiero ayuda "
                "para cerrar mi primera venta."
            )
        )
        notificar_aliado(
            db, aliado.id, "alerta_sin_venta",
            f"Contactaste {n_contactados} empresas, vamos por tu primera venta 🎯",
            f"A veces el problema no es el esfuerzo, es el enfoque. "
            f"<a href=\"{wa_link}\" target=\"_blank\" rel=\"noopener\" "
            f"style=\"color:#f97316;font-weight:700;\">Escribinos por WhatsApp</a> "
            f"y lo destrabamos juntos.",
            tab="pipeline",
        )
    except Exception as exc:
        print(f"[CANAL1] Error creando novedad alerta_sin_venta aliado={aliado.id}: {exc}", file=sys.stderr)

    # WhatsApp directo al aliado: solo si el canal está habilitado (ver
    # ENABLE_CANAL1_WA en main.py) — el mismo criterio que el resto de la
    # secuencia de Canal 1, para no mandar por un canal que está apagado.
    numero = _numero(aliado)
    enviado_wa = False
    if os.environ.get("ENABLE_CANAL1_WA", "0") == "1" and numero:
        enviado_wa = _enviar(
            numero, _msg_alerta_contactos_sin_venta(aliado, n_contactados),
            f"alerta_sin_venta aliado={aliado.id}",
        )

    try:
        aliado.canal1_alerta_sin_venta_en = datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[CANAL1] Error guardando canal1_alerta_sin_venta_en: {exc}", file=sys.stderr)

    print(f"[CANAL1] alerta_sin_venta aliado={aliado.id} campanita=ok wa={'enviado' if enviado_wa else 'no'}", flush=True)
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# JOBS DEL SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════


def job_onboarding_wa() -> None:
    """
    Job horario. Verifica qué aliados Canal 1 necesitan el mensaje de día 1, 3 o 7.
    Idempotente: usa timestamps canal1_wa_dN_en para no reenviar.

    Agregar en main.py:
        scheduler.add_job(jarvis_canal1.job_onboarding_wa, "interval", hours=1)
    """
    try:
        from database import SessionLocal  # noqa: PLC0415
        from models import Aliado         # noqa: PLC0415
        db = SessionLocal()
        ahora = datetime.utcnow()

        aliados = (
            db.query(Aliado)
            .filter(Aliado.activo == True, Aliado.tipo_aliado == "canal1")  # noqa: E712
            .all()
        )

        enviados = 0
        for a in aliados:
            numero = _numero(a)
            if not numero:
                continue

            creado_en = getattr(a, "creado_en", None)
            if not creado_en:
                continue

            dias = (ahora - creado_en).days

            # Solo el toque de Día 1 por WhatsApp. La secuencia educativa de
            # Día 3 y Día 7 la cubre el email (job_onboarding_sequence en
            # main.py). Así no duplicamos el mismo empujón en ambos canales el
            # mismo día (evita sensación de spam).
            if dias >= 1 and not getattr(a, "canal1_wa_d1_en", None):
                if _enviar(numero, _msg_dia1(a), f"onboarding_d1 aliado={a.id}"):
                    a.canal1_wa_d1_en = ahora
                    enviados += 1

        if enviados:
            db.commit()
        print(f"[CANAL1] job_onboarding_wa: {enviados} mensajes enviados.", flush=True)

    except Exception as exc:
        print(f"[CANAL1] Error en job_onboarding_wa: {exc}", file=sys.stderr)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


def job_inactividad_wa() -> None:
    """
    Job cada 6 horas. Detecta aliados sin login en 7 días (reactivación suave)
    y 30 días (último intento).

    Condición de inactividad: ultimo_login o creado_en (el que sea más reciente).

    Agregar en main.py:
        scheduler.add_job(jarvis_canal1.job_inactividad_wa, "interval", hours=6)
    """
    try:
        from database import SessionLocal  # noqa: PLC0415
        from models import Aliado         # noqa: PLC0415
        db = SessionLocal()
        ahora = datetime.utcnow()
        hace7d  = ahora - timedelta(days=7)
        hace30d = ahora - timedelta(days=30)

        aliados = (
            db.query(Aliado)
            .filter(Aliado.activo == True, Aliado.tipo_aliado == "canal1")  # noqa: E712
            .all()
        )

        enviados = 0
        for a in aliados:
            numero = _numero(a)
            if not numero:
                continue

            # Último punto de actividad real
            ultimo = getattr(a, "ultimo_login", None) or getattr(a, "creado_en", None)
            if not ultimo:
                continue

            dias_inactivo = (ahora - ultimo).days

            # ── 30 días — último intento ──────────────────────────────────────
            if dias_inactivo >= 30:
                ultimo_30 = getattr(a, "canal1_wa_inact30_en", None)
                # Solo una vez por período de inactividad (mínimo 30 días entre intentos)
                if not ultimo_30 or (ahora - ultimo_30).days >= 30:
                    if _enviar(numero, _msg_inactividad_30d(a), f"inact_30d aliado={a.id}"):
                        a.canal1_wa_inact30_en = ahora
                        enviados += 1

            # ── 7 días — reactivación suave ───────────────────────────────────
            elif dias_inactivo >= 7:
                ultimo_7 = getattr(a, "canal1_wa_inact7_en", None)
                # Solo una vez por período (mínimo 14 días entre recordatorios de 7d)
                if not ultimo_7 or (ahora - ultimo_7).days >= 14:
                    if _enviar(numero, _msg_inactividad_7d(a), f"inact_7d aliado={a.id}"):
                        a.canal1_wa_inact7_en = ahora
                        enviados += 1

        if enviados:
            db.commit()
        print(f"[CANAL1] job_inactividad_wa: {enviados} mensajes enviados.", flush=True)

    except Exception as exc:
        print(f"[CANAL1] Error en job_inactividad_wa: {exc}", file=sys.stderr)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


def job_semanal_wa() -> None:
    """
    Job cron: lunes 9hs. Informa a cada aliado Canal 1 activo cuántos leads
    nuevos hay disponibles en la bolsa esa semana.

    "Nuevos" = cargados en los últimos 7 días con estado 'disponible'.

    Agregar en main.py:
        scheduler.add_job(jarvis_canal1.job_semanal_wa, "cron", day_of_week="mon", hour=9)
    """
    try:
        from database import SessionLocal  # noqa: PLC0415
        from models import Aliado, LeadBolsa  # noqa: PLC0415
        db = SessionLocal()
        ahora     = datetime.utcnow()
        hace7d    = ahora - timedelta(days=7)

        # Leads nuevos disponibles globalmente (no reclamados)
        n_leads_nuevos = (
            db.query(LeadBolsa)
            .filter(
                LeadBolsa.estado == "disponible",
                LeadBolsa.fecha_carga >= hace7d,
            )
            .count()
        )

        aliados = (
            db.query(Aliado)
            .filter(Aliado.activo == True, Aliado.tipo_aliado == "canal1")  # noqa: E712
            .all()
        )

        enviados = 0
        for a in aliados:
            if enviados >= WA_BATCH_MAX:
                print(f"[CANAL1] job_semanal_wa: tope {WA_BATCH_MAX}/corrida alcanzado, corto.", flush=True)
                break
            numero = _numero(a)
            if not numero:
                continue

            # No reenviar si ya se mandó un mensaje semanal esta semana
            ultimo_semanal = getattr(a, "canal1_wa_semanal_en", None)
            if ultimo_semanal and (ahora - ultimo_semanal).days < 6:
                continue

            if _enviar(numero, _msg_semanal(a, n_leads_nuevos), f"semanal aliado={a.id}"):
                a.canal1_wa_semanal_en = ahora
                enviados += 1
                db.commit()              # commit incremental: no perder progreso si corta
                time.sleep(WA_BATCH_DELAY)  # espaciar para no degradar el número

        if enviados:
            db.commit()
        print(f"[CANAL1] job_semanal_wa: {enviados} mensajes enviados ({n_leads_nuevos} leads nuevos).", flush=True)

    except Exception as exc:
        print(f"[CANAL1] Error en job_semanal_wa: {exc}", file=sys.stderr)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


def job_mensual_wa() -> None:
    """
    Job cron: día 1 de cada mes, 10hs. Envía ranking de top aliados y
    la posición individual de cada aliado Canal 1 activo.

    "Activo" para el ranking = al menos 1 venta confirmada en los últimos 90 días.

    Agregar en main.py:
        scheduler.add_job(jarvis_canal1.job_mensual_wa, "cron", day=1, hour=10)
    """
    try:
        from database import SessionLocal  # noqa: PLC0415
        from models import Aliado, Venta   # noqa: PLC0415
        db = SessionLocal()
        ahora     = datetime.utcnow()
        hace90d   = ahora - timedelta(days=90)

        # Construir ranking: aliados Canal 1 con ventas en últimos 90 días
        aliados_canal1 = (
            db.query(Aliado)
            .filter(Aliado.activo == True, Aliado.tipo_aliado == "canal1")  # noqa: E712
            .all()
        )

        # Score por aliado (ventas confirmadas en últimos 90 días)
        scores: list[tuple[int, int, object]] = []  # (aliado_id, n_ventas, aliado)
        for a in aliados_canal1:
            n = sum(
                1 for v in a.ventas
                if v.confirmada and v.fecha_venta and v.fecha_venta >= hace90d
            )
            scores.append((a.id, n, a))

        # Ordenar de mayor a menor
        scores.sort(key=lambda x: x[1], reverse=True)
        total_activos = len([s for s in scores if s[1] > 0]) or 1

        enviados = 0
        for posicion_0, (aliado_id, n_ventas_propias, a) in enumerate(scores):
            if enviados >= WA_BATCH_MAX:
                print(f"[CANAL1] job_mensual_wa: tope {WA_BATCH_MAX}/corrida alcanzado, corto.", flush=True)
                break
            numero = _numero(a)
            if not numero:
                continue

            # No reenviar si ya se mandó el mensual este mes
            ultimo_mensual = getattr(a, "canal1_wa_mensual_en", None)
            if ultimo_mensual and ultimo_mensual.month == ahora.month and ultimo_mensual.year == ahora.year:
                continue

            posicion_real = posicion_0 + 1
            msg = _msg_mensual_ranking(a, posicion_real, total_activos, n_ventas_propias)
            if _enviar(numero, msg, f"mensual aliado={a.id} pos={posicion_real}"):
                a.canal1_wa_mensual_en = ahora
                enviados += 1
                db.commit()              # commit incremental: no perder progreso si corta
                time.sleep(WA_BATCH_DELAY)  # espaciar para no degradar el número

        if enviados:
            db.commit()
        print(f"[CANAL1] job_mensual_wa: {enviados} mensajes enviados.", flush=True)

    except Exception as exc:
        print(f"[CANAL1] Error en job_mensual_wa: {exc}", file=sys.stderr)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


def job_alerta_contactos_sin_venta() -> None:
    """
    Job diario. Detecta aliados Canal 1 activos que contactaron muchas
    empresas (>= CANAL1_UMBRAL_CONTACTOS_SIN_VENTA prospectos con estado
    distinto de 'sin_contactar') pero todavía no tienen ninguna venta
    confirmada, y les dispara la alerta (campanita + WA opcional) con un
    link directo al WhatsApp de Avanza Digital.

    Es un aviso ÚNICO por aliado (no un recordatorio recurrente): una vez
    que canal1_alerta_sin_venta_en queda seteado, no se vuelve a mandar
    nunca más, sin importar cuánto tiempo pase — si ya lo contactamos y
    no cerró, insistir cada tantos días no ayuda. La única forma de que
    vuelva a estar "elegible" es que ese campo se resetee a mano.

    Agregar en main.py:
        scheduler.add_job(jarvis_canal1.job_alerta_contactos_sin_venta, "cron", hour=11)
    """
    try:
        from database import SessionLocal  # noqa: PLC0415
        from models import Aliado, Prospecto  # noqa: PLC0415
        db = SessionLocal()

        aliados = (
            db.query(Aliado)
            .filter(Aliado.activo == True, Aliado.tipo_aliado == "canal1")  # noqa: E712
            .all()
        )

        avisados = 0
        for a in aliados:
            # Ya vendió al menos una vez: no aplica la alerta.
            if a.ventas_propias_count > 0:
                continue

            # Ya se avisó una vez: no se repite, nunca más (a diferencia de
            # inactividad/onboarding, este es un aviso de una sola vez).
            if getattr(a, "canal1_alerta_sin_venta_en", None):
                continue

            n_contactados = (
                db.query(Prospecto)
                .filter(Prospecto.aliado_id == a.id, Prospecto.estado != "sin_contactar")
                .count()
            )
            if n_contactados < CANAL1_UMBRAL_CONTACTOS_SIN_VENTA:
                continue

            if notificar_contactos_sin_venta(a, n_contactados, db):
                avisados += 1

        print(f"[CANAL1] job_alerta_contactos_sin_venta: {avisados} aliado(s) avisados.", flush=True)

    except Exception as exc:
        print(f"[CANAL1] Error en job_alerta_contactos_sin_venta: {exc}", file=sys.stderr)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDAD: aplicar migraciones (llamar una vez al startup si corresponde)
# ═══════════════════════════════════════════════════════════════════════════════


def aplicar_migraciones(engine) -> None:
    """
    Aplica las columnas nuevas de seguimiento WA Canal 1.
    Es idempotente (usa IF NOT EXISTS). Llamar en main.py al startup:

        import jarvis_canal1
        jarvis_canal1.aplicar_migraciones(engine)
    """
    from sqlalchemy import text  # noqa: PLC0415
    with engine.connect() as conn:
        for sql in MIGRATION_SQL:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception as exc:
                print(f"[CANAL1] Migración ignorada ({sql[:60]}…): {exc}", file=sys.stderr)
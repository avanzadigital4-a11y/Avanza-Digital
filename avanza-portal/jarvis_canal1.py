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
from datetime import datetime, timedelta
from typing import Optional

# ─── CONFIG ───────────────────────────────────────────────────────────────────

PORTAL_URL         = os.environ.get("PORTAL_URL", "https://portal.avanzadigital.com")
GRUPO_WHATSAPP_URL = os.environ.get("CANAL1_GRUPO_WA_URL", "https://chat.whatsapp.com/XXXXXXXX")

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
        f"En un plan de USD 1.050 eso son *USD 20 más por venta*. "
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
        f"En un plan Pro de USD 2.900 eso son *USD 580 por venta*.\n\n"
        f"Sos de los mejores aliados del network. "
        f"Si querés escalar más, hablemos de sub-aliados y estructura de red. 🏆"
    )


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

            # Día 7 (prioridad primero para no sobreescribir con d3/d1)
            if dias >= 7 and not getattr(a, "canal1_wa_d7_en", None):
                if _enviar(numero, _msg_dia7(a), f"onboarding_d7 aliado={a.id}"):
                    a.canal1_wa_d7_en = ahora
                    enviados += 1

            # Día 3
            elif dias >= 3 and not getattr(a, "canal1_wa_d3_en", None):
                if _enviar(numero, _msg_dia3(a), f"onboarding_d3 aliado={a.id}"):
                    a.canal1_wa_d3_en = ahora
                    enviados += 1

            # Día 1 (+24hs)
            elif dias >= 1 and not getattr(a, "canal1_wa_d1_en", None):
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
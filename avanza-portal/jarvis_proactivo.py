"""
jarvis_proactivo.py — Módulo 9: JARVIS Proactivo

DISEÑO:
  JARVIS no espera que el aliado pregunte. Este módulo monitorea el negocio del aliado
  y genera alertas y borradores ANTES de que los pida.

  Flujo de uso:
    1. main.py llama a detectar_alertas() con los datos del aliado (desde la DB).
    2. JARVIS devuelve una lista de alertas priorizadas con acciones listas.
    3. Las alertas se muestran en el panel del portal y/o se envían por email/WhatsApp.
    4. Cada alerta incluye el borrador de la acción (email, mensaje, etc.) listo para revisar.

FUNCIONES PRINCIPALES:
  detectar_alertas()             → Detecta todas las situaciones que requieren acción proactiva
  generar_accion_proactiva()     → Para una alerta específica, genera el borrador completo
  evaluar_riesgo_churn()         → Evalúa si un cliente activo está en riesgo de irse
  generar_reactivacion()         → Draft de reactivación para un lead dormido
  generar_seguimiento_propuesta()→ Draft de seguimiento para una propuesta sin respuesta
  generar_check_in_cliente()     → Draft de check-in para un cliente activo sin actividad
  calcular_momento_optimo()      → Cuándo es el mejor momento para contactar (día/hora)

TRIGGERS DE PROACTIVIDAD (según el blueprint):
  - Lead sin contacto por más de X días → alerta + draft de reactivación
  - Propuesta enviada sin respuesta por más de Y días → alerta + seguimiento
  - Cliente activo sin actividad por más de Z días → alerta de churn + script de check-in
  - Nuevo lead en la bolsa que matchea historial de cierres → notificación con score
  - Fin de mes / trimestre → resumen de pipeline + proyección
  - El aliado no usó JARVIS en X días → briefing de estado + 3 acciones

INTEGRACIÓN EN main.py:
    import jarvis_proactivo
    jarvis_proactivo.register(app, get_db, current_aliado_required)

  Para el scheduler (cron):
    from jarvis_proactivo import procesar_alertas_todos_los_aliados
    # Llamar diariamente a las 7am
"""

from __future__ import annotations
import os, json, sys
from typing import Optional
from datetime import datetime, date, timedelta
from enum import Enum

# ─── CONFIG ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 18.0

# Umbrales por defecto (configurables por aliado en el futuro)
UMBRAL_LEAD_SIN_CONTACTO_DIAS    = 7    # Lead sin tocar → alerta
UMBRAL_PROPUESTA_SIN_RESPUESTA   = 5    # Propuesta fría → seguimiento
UMBRAL_CLIENTE_SIN_ACTIVIDAD     = 21   # Cliente durmiente → check-in
UMBRAL_JARVIS_SIN_USO_DIAS       = 4    # Aliado sin usar JARVIS → briefing
UMBRAL_REFERIDO_MESES_ACTIVO     = 3    # Cliente activo X meses → pedir referido


class TipoAlerta(str, Enum):
    LEAD_FRIO           = "lead_frio"
    PROPUESTA_SIN_RESP  = "propuesta_sin_respuesta"
    CLIENTE_CHURN       = "cliente_churn"
    LEAD_NUEVO_MATCH    = "lead_nuevo_match"
    REFERIDO            = "referido_potencial"
    FIN_MES             = "fin_mes"
    ALIADO_INACTIVO     = "aliado_inactivo"
    NOTICIA_PROSPECTO   = "noticia_prospecto"


def is_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


# ─── HELPERS INTERNOS ────────────────────────────────────────────────────────

def _chat(
    prompt: str,
    system: str,
    *,
    max_tokens: int = 800,
    temperature: float = 0.35,
    json_mode: bool = False,
) -> Optional[str]:
    """Llama a Claude. Devuelve texto o None. No lanza excepciones."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        system_final = system
        if json_mode:
            system_final = (
                system
                + "\n\nIMPORTANTE: Respondé ÚNICAMENTE con JSON válido. "
                "Sin texto antes ni después. Sin bloques de código markdown."
            )
        msg = client.messages.create(
            model=JARVIS_MODEL,
            max_tokens=max_tokens,
            system=system_final,
            messages=[{"role": "user", "content": prompt}],
            timeout=JARVIS_TIMEOUT,
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"[JARVIS PROACTIVO ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    print(f"[JARVIS PROACTIVO] No se pudo parsear JSON: {text[:200]}", file=sys.stderr)
    return None


# ─── MÓDULO 9A: DETECCIÓN DE ALERTAS ─────────────────────────────────────────

def detectar_alertas(
    aliado_nombre: str,
    aliado_rubros: list[str] | None = None,
    aliado_pais: str = "AR",
    # Prospectos/leads activos del aliado
    # Cada item: {"id": int, "empresa": str, "contacto": str, "dias_sin_contacto": int,
    #              "tiene_propuesta": bool, "dias_propuesta_sin_resp": int, "estado": str}
    prospectos: list[dict] | None = None,
    # Clientes activos
    # Cada item: {"id": int, "empresa": str, "meses_activo": int, "dias_sin_actividad": int}
    clientes_activos: list[dict] | None = None,
    # Leads nuevos en bolsa (sector/zona que matchea con el aliado)
    leads_nuevos_bolsa: list[dict] | None = None,
    # Uso del sistema
    dias_sin_usar_jarvis: int = 0,
    # Fecha actual (para detectar fin de mes / trimestre)
    fecha_hoy: date | None = None,
    # Nivel de ruido configurado por el aliado
    nivel_ruido: str = "medio",   # "bajo" | "medio" | "alto"
) -> list[dict]:
    """
    Detecta todas las situaciones que requieren una acción proactiva.

    Devuelve lista de alertas ordenadas por prioridad:
    [
        {
            "tipo": TipoAlerta,
            "prioridad": "critica" | "alta" | "media",
            "titulo": str,
            "descripcion": str,
            "entidad_id": int | None,
            "entidad_nombre": str,
            "dias": int,           # Días relevantes (sin contacto, sin respuesta, etc.)
            "accion_sugerida": str,  # Texto del botón en el portal
            "puede_autocompletar": bool  # ¿JARVIS puede generar el draft automáticamente?
        }
    ]
    """
    fecha_hoy = fecha_hoy or date.today()
    alertas = []

    # ── Leads fríos ──────────────────────────────────────────────────────────
    for p in (prospectos or []):
        dias = p.get("dias_sin_contacto", 0)
        estado = p.get("estado", "")
        if estado in ("cerrado", "perdido"):
            continue
        if dias >= UMBRAL_LEAD_SIN_CONTACTO_DIAS:
            prioridad = "critica" if dias >= 14 else "alta"
            if nivel_ruido == "bajo" and prioridad != "critica":
                continue
            alertas.append({
                "tipo": TipoAlerta.LEAD_FRIO,
                "prioridad": prioridad,
                "titulo": f"{p.get('empresa', '?')} — {dias} días sin contacto",
                "descripcion": f"El lead de {p.get('empresa', '?')} lleva {dias} días sin actividad. "
                               f"La probabilidad de enfriamiento sube significativamente después de los 10 días.",
                "entidad_id": p.get("id"),
                "entidad_nombre": p.get("empresa", ""),
                "contacto": p.get("contacto", ""),
                "dias": dias,
                "accion_sugerida": "Reactivar ahora",
                "puede_autocompletar": True,
            })

    # ── Propuestas sin respuesta ──────────────────────────────────────────────
    for p in (prospectos or []):
        if not p.get("tiene_propuesta"):
            continue
        dias_prop = p.get("dias_propuesta_sin_resp", 0)
        if dias_prop >= UMBRAL_PROPUESTA_SIN_RESPUESTA:
            prioridad = "critica" if dias_prop >= 10 else "alta"
            if nivel_ruido == "bajo" and prioridad != "critica":
                continue
            alertas.append({
                "tipo": TipoAlerta.PROPUESTA_SIN_RESP,
                "prioridad": prioridad,
                "titulo": f"Propuesta a {p.get('empresa', '?')} — {dias_prop} días sin respuesta",
                "descripcion": f"La propuesta enviada a {p.get('empresa', '?')} lleva {dias_prop} días sin respuesta. "
                               f"Es el momento ideal para un seguimiento estratégico.",
                "entidad_id": p.get("id"),
                "entidad_nombre": p.get("empresa", ""),
                "contacto": p.get("contacto", ""),
                "dias": dias_prop,
                "accion_sugerida": "Enviar seguimiento",
                "puede_autocompletar": True,
            })

    # ── Riesgo de churn en clientes activos ───────────────────────────────────
    for c in (clientes_activos or []):
        dias_sin_act = c.get("dias_sin_actividad", 0)
        if dias_sin_act >= UMBRAL_CLIENTE_SIN_ACTIVIDAD:
            alertas.append({
                "tipo": TipoAlerta.CLIENTE_CHURN,
                "prioridad": "alta",
                "titulo": f"{c.get('empresa', '?')} — cliente sin actividad ({dias_sin_act} días)",
                "descripcion": f"{c.get('empresa', '?')} es cliente activo hace {c.get('meses_activo', '?')} meses "
                               f"pero lleva {dias_sin_act} días sin actividad registrada. Riesgo de abandono silencioso.",
                "entidad_id": c.get("id"),
                "entidad_nombre": c.get("empresa", ""),
                "dias": dias_sin_act,
                "meses_activo": c.get("meses_activo", 0),
                "accion_sugerida": "Hacer check-in",
                "puede_autocompletar": True,
            })

    # ── Potencial de referido ─────────────────────────────────────────────────
    if nivel_ruido in ("medio", "alto"):
        for c in (clientes_activos or []):
            meses = c.get("meses_activo", 0)
            if meses >= UMBRAL_REFERIDO_MESES_ACTIVO and c.get("dias_sin_actividad", 0) < 15:
                alertas.append({
                    "tipo": TipoAlerta.REFERIDO,
                    "prioridad": "media",
                    "titulo": f"{c.get('empresa', '?')} — {meses} meses activo: momento ideal para pedir referido",
                    "descripcion": f"{c.get('empresa', '?')} cumple {meses} meses como cliente activo y está "
                                   f"en buen momento. Es el contexto ideal para pedir una recomendación sin incomodar.",
                    "entidad_id": c.get("id"),
                    "entidad_nombre": c.get("empresa", ""),
                    "meses_activo": meses,
                    "accion_sugerida": "Pedir referido",
                    "puede_autocompletar": True,
                })

    # ── Leads nuevos en bolsa que matchean ────────────────────────────────────
    if nivel_ruido in ("medio", "alto"):
        for lead in (leads_nuevos_bolsa or []):
            score = lead.get("score_estimado", 0)
            if score >= 70:
                alertas.append({
                    "tipo": TipoAlerta.LEAD_NUEVO_MATCH,
                    "prioridad": "alta" if score >= 80 else "media",
                    "titulo": f"Lead nuevo en tu zona — Score {score}/100",
                    "descripcion": f"Apareció un lead en la bolsa que matchea con tu historial de cierres: "
                                   f"{lead.get('sector', '?')} en {lead.get('zona', '?')}. "
                                   f"Score estimado: {score}/100.",
                    "entidad_id": lead.get("id"),
                    "entidad_nombre": lead.get("empresa", f"Lead {lead.get('sector', '?')}"),
                    "score": score,
                    "sector": lead.get("sector", ""),
                    "zona": lead.get("zona", ""),
                    "accion_sugerida": "Analizar lead",
                    "puede_autocompletar": False,
                })

    # ── Aliado sin usar JARVIS ────────────────────────────────────────────────
    if dias_sin_usar_jarvis >= UMBRAL_JARVIS_SIN_USO_DIAS and nivel_ruido == "alto":
        alertas.append({
            "tipo": TipoAlerta.ALIADO_INACTIVO,
            "prioridad": "media",
            "titulo": f"{dias_sin_usar_jarvis} días sin usar JARVIS — Revisá tu estado",
            "descripcion": f"Llevas {dias_sin_usar_jarvis} días sin consultar a JARVIS. "
                           f"Puede que haya leads fríos o propuestas sin seguimiento.",
            "entidad_id": None,
            "entidad_nombre": aliado_nombre,
            "dias": dias_sin_usar_jarvis,
            "accion_sugerida": "Ver estado del negocio",
            "puede_autocompletar": False,
        })

    # ── Fin de mes ────────────────────────────────────────────────────────────
    dias_para_fin_mes = (
        date(fecha_hoy.year, fecha_hoy.month % 12 + 1, 1) - timedelta(days=1) - fecha_hoy
    ).days if fecha_hoy.month < 12 else (date(fecha_hoy.year + 1, 1, 1) - timedelta(days=1) - fecha_hoy).days

    if dias_para_fin_mes <= 5:
        alertas.append({
            "tipo": TipoAlerta.FIN_MES,
            "prioridad": "alta",
            "titulo": f"Quedan {dias_para_fin_mes} días para cerrar el mes",
            "descripcion": f"Fin de mes en {dias_para_fin_mes} días. Es el momento de cerrar deals abiertos, "
                           f"hacer seguimiento de propuestas y proyectar el pipeline del próximo mes.",
            "entidad_id": None,
            "entidad_nombre": "",
            "dias": dias_para_fin_mes,
            "accion_sugerida": "Ver resumen de pipeline",
            "puede_autocompletar": False,
        })

    # Ordenar: críticas primero, luego altas, luego medias
    orden = {"critica": 0, "alta": 1, "media": 2}
    alertas.sort(key=lambda a: orden.get(a["prioridad"], 3))

    return alertas


# ─── MÓDULO 9B: GENERACIÓN DE ACCIONES ───────────────────────────────────────

def generar_reactivacion(
    aliado_nombre: str,
    aliado_ciudad: str = "",
    aliado_pais: str = "AR",
    aliado_rubros: list[str] | None = None,
    empresa_lead: str = "",
    contacto_nombre: str = "",
    contacto_cargo: str = "",
    dias_sin_contacto: int = 0,
    ultimo_tema: str = "",
    canal: str = "whatsapp",  # "whatsapp" | "email" | "linkedin"
    historial_previo: str = "",
) -> Optional[dict]:
    """
    Genera un mensaje de reactivación para un lead dormido.

    Devuelve:
    {
        "canal": str,
        "asunto": str | None,      # Solo para email
        "mensaje": str,
        "longitud_palabras": int,
        "tono": str,
        "tip_envio": str,          # Cuándo enviarlo, ej: "Martes 9-11am"
        "variante_b": str | None,  # Segunda opción si el aliado quiere elegir
    }
    """
    if not ANTHROPIC_API_KEY:
        return None

    rubros_str = ", ".join(aliado_rubros or ["general"])
    pais_nombre = {
        "AR": "Argentina", "MX": "México", "CO": "Colombia",
        "CL": "Chile", "PE": "Perú", "UY": "Uruguay",
    }.get(aliado_pais, aliado_pais)

    limites = {"whatsapp": 60, "linkedin": 100, "email": 200}
    limite = limites.get(canal, 100)

    system = f"""Sos JARVIS, el asistente de {aliado_nombre}, especialista en reactivación de leads.
Sector: {rubros_str}. País: {pais_nombre}.
Escribís como hablaría {aliado_nombre} — natural, sin plantillas evidentes, sin presión artificial.
Respondé siempre con JSON válido."""

    prompt = f"""Generá un mensaje de reactivación para un lead dormido.

DATOS:
- Empresa del lead: {empresa_lead}
- Contacto: {contacto_nombre} ({contacto_cargo})
- Días sin contacto: {dias_sin_contacto}
- Último tema tratado: {ultimo_tema or "no especificado"}
- Canal: {canal}
- Historial previo (resumen): {historial_previo or "primer o segundo contacto previo"}

REGLAS:
- Para WhatsApp: máximo {limite} palabras. Sin emojis de negocio forzados.
- Para email: máximo {limite} palabras. Con asunto específico (no genérico).
- Para LinkedIn: máximo {limite} palabras. Tono ejecutivo pero cálido.
- El mensaje debe sonar como una persona real, no como un template.
- NO usar frases como "espero que estés bien", "me pongo en contacto", "adjunto".
- Referenciá algo específico si hay historial. Si no, usá un ángulo de valor concreto.
- Terminá con una pregunta o propuesta concreta (no con "¿avanzamos?").

Devolvé:
1. canal: "{canal}"
2. asunto: asunto del email si es email, null si no
3. mensaje: el mensaje principal (máximo {limite} palabras)
4. longitud_palabras: cuenta de palabras del mensaje
5. tono: descripción del tono elegido (ej: "directo y concreto")
6. tip_envio: cuándo enviarlo para mejor tasa de respuesta (basado en sector y cargo)
7. variante_b: una segunda versión del mensaje con un ángulo diferente (mismo canal)

Respondé solo con JSON."""

    raw = _chat(prompt, system, max_tokens=700, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    result.setdefault("canal", canal)
    result.setdefault("asunto", None)
    result.setdefault("mensaje", "")
    result.setdefault("longitud_palabras", 0)
    result.setdefault("tono", "")
    result.setdefault("tip_envio", "Martes o miércoles, 9-11am")
    result.setdefault("variante_b", None)
    return result


def generar_seguimiento_propuesta(
    aliado_nombre: str,
    aliado_pais: str = "AR",
    aliado_rubros: list[str] | None = None,
    empresa_cliente: str = "",
    contacto_nombre: str = "",
    contacto_cargo: str = "",
    plan_propuesto: str = "",
    valor_propuesta: float = 0.0,
    dias_sin_respuesta: int = 0,
    ultimo_contacto_resumen: str = "",
    canal: str = "email",
) -> Optional[dict]:
    """
    Genera un seguimiento estratégico para una propuesta sin respuesta.

    Devuelve:
    {
        "canal": str,
        "asunto": str | None,
        "mensaje": str,
        "estrategia": str,    # Por qué este enfoque (no presión, sino valor)
        "señal_de_compra": bool,  # ¿El contexto sugiere que podrían estar cerca de cerrar?
        "riesgo_detectado": str | None,  # Algo que podría haber salido mal
        "tip_envio": str,
    }
    """
    if not ANTHROPIC_API_KEY:
        return None

    rubros_str = ", ".join(aliado_rubros or ["general"])
    pais_nombre = {
        "AR": "Argentina", "MX": "México", "CO": "Colombia",
        "CL": "Chile", "PE": "Perú", "UY": "Uruguay",
    }.get(aliado_pais, aliado_pais)

    system = f"""Sos JARVIS, el asistente de {aliado_nombre}. Sector: {rubros_str}. País: {pais_nombre}.
Generás seguimientos de propuestas que abren conversación, no que presionan.
El objetivo es volver a estar en el radar del cliente de forma valiosa, no ansiosa.
Respondé siempre con JSON válido."""

    prompt = f"""Generá el seguimiento para la propuesta de {empresa_cliente}.

DATOS:
- Empresa: {empresa_cliente}
- Contacto: {contacto_nombre} ({contacto_cargo})
- Plan propuesto: {plan_propuesto}
- Valor de la propuesta: ${valor_propuesta:,.0f} USD
- Días sin respuesta: {dias_sin_respuesta}
- Último contacto/reunión: {ultimo_contacto_resumen or "envío de propuesta"}
- Canal de seguimiento: {canal}

REGLAS:
- No mencionar "el silencio" ni "no recibí respuesta" — eso se lee como presión.
- Aportar algo nuevo: un dato, un caso relevante, una pregunta estratégica.
- Si han pasado más de 10 días, considerar un ángulo completamente diferente al de la propuesta original.
- Si son 5-10 días, un recordatorio suave con un valor agregado.
- Máximo 120 palabras para email, 60 para WhatsApp.

Devolvé:
1. canal: "{canal}"
2. asunto: asunto del email (solo para email), null si no
3. mensaje: el seguimiento completo
4. estrategia: 1 línea explicando la lógica del enfoque elegido
5. señal_de_compra: true si los datos sugieren que el cliente está interesado pero ocupado, false si podría haber perdido el interés
6. riesgo_detectado: si detectás algo que pudo haber salido mal (precio, timing, stakeholder wrong), describilo brevemente. null si no hay señales.
7. tip_envio: momento óptimo para enviarlo

Respondé solo con JSON."""

    raw = _chat(prompt, system, max_tokens=650, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    result.setdefault("canal", canal)
    result.setdefault("asunto", None)
    result.setdefault("mensaje", "")
    result.setdefault("estrategia", "")
    result.setdefault("señal_de_compra", False)
    result.setdefault("riesgo_detectado", None)
    result.setdefault("tip_envio", "Martes o jueves, 9-11am")
    return result


def generar_check_in_cliente(
    aliado_nombre: str,
    aliado_pais: str = "AR",
    empresa_cliente: str = "",
    contacto_nombre: str = "",
    meses_activo: int = 0,
    dias_sin_actividad: int = 0,
    plan_activo: str = "",
    ultimo_resultado_conocido: str = "",
    canal: str = "whatsapp",
) -> Optional[dict]:
    """
    Genera un check-in para un cliente activo que lleva tiempo sin actividad.

    Devuelve:
    {
        "canal": str,
        "asunto": str | None,
        "mensaje": str,
        "objetivo_real": str,     # Qué se busca con el check-in (más allá de "saber cómo está")
        "upsell_detectado": bool, # ¿Hay una oportunidad de venta adicional natural?
        "upsell_angulo": str | None,
    }
    """
    if not ANTHROPIC_API_KEY:
        return None

    system = f"""Sos JARVIS, el asistente de {aliado_nombre}. País: {aliado_pais}.
Generás check-ins de clientes que son genuinos, no de "venta disfrazada de cariño".
El cliente debe sentir que el aliado se acuerda de él porque le importa, no para venderle algo.
Si hay una oportunidad de upsell natural, la mencionás aparte para que el aliado decida si incluirla.
Respondé siempre con JSON válido."""

    prompt = f"""Generá un check-in para {empresa_cliente}, cliente activo de {aliado_nombre}.

DATOS:
- Empresa: {empresa_cliente}
- Contacto: {contacto_nombre}
- Meses como cliente activo: {meses_activo}
- Días sin actividad registrada: {dias_sin_actividad}
- Plan activo: {plan_activo}
- Último resultado conocido: {ultimo_resultado_conocido or "no especificado"}
- Canal: {canal}

REGLAS:
- El check-in debe ser corto: máximo 50 palabras para WhatsApp, 80 para email.
- Debe referirse a algo concreto (el tiempo que llevan trabajando juntos, el plan, algo del negocio).
- NO preguntar "¿cómo estás?" como apertura — es genérico.
- El CTA debe ser una pregunta abierta sobre resultados o una propuesta de reunión breve (15 min).
- Si hay oportunidad de upsell, no la metas en el mensaje. Solo la reportás en "upsell_angulo".

Devolvé:
1. canal: "{canal}"
2. asunto: asunto solo si es email, null si no
3. mensaje: el check-in completo
4. objetivo_real: en 1 línea, qué se busca realmente con este contacto
5. upsell_detectado: true si con {meses_activo} meses activos hay una oportunidad natural de renovación, upgrade o servicio adicional
6. upsell_angulo: si upsell_detectado es true, describí el ángulo en 1 línea. null si no hay.

Respondé solo con JSON."""

    raw = _chat(prompt, system, max_tokens=550, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    result.setdefault("canal", canal)
    result.setdefault("asunto", None)
    result.setdefault("mensaje", "")
    result.setdefault("objetivo_real", "")
    result.setdefault("upsell_detectado", False)
    result.setdefault("upsell_angulo", None)
    return result


def generar_pedido_referido(
    aliado_nombre: str,
    aliado_pais: str = "AR",
    empresa_cliente: str = "",
    contacto_nombre: str = "",
    meses_activo: int = 0,
    resultado_conocido: str = "",
    sector_objetivo: str = "",
    canal: str = "whatsapp",
) -> Optional[dict]:
    """
    Genera un mensaje para pedirle al cliente activo que recomiende a alguien.

    Devuelve:
    {
        "canal": str,
        "mensaje": str,
        "logica": str,    # Por qué este momento y este enfoque
        "que_ofrecer": str | None,  # Si hay algo concreto que ofrecer a cambio
    }
    """
    if not ANTHROPIC_API_KEY:
        return None

    system = f"""Sos JARVIS, el asistente de {aliado_nombre}. País: {aliado_pais}.
Generás mensajes de pedido de referidos que no incomodan porque se sienten naturales y honestos.
La clave: el cliente debe querer referir porque entiende el valor que recibió, no porque le pediste.
Respondé siempre con JSON válido."""

    prompt = f"""Generá un mensaje para pedir un referido a un cliente activo.

DATOS:
- Empresa cliente: {empresa_cliente}
- Contacto: {contacto_nombre}
- Meses activo: {meses_activo}
- Resultado conocido del cliente: {resultado_conocido or "no especificado"}
- Sector que busca {aliado_nombre}: {sector_objetivo or "similar al del cliente"}
- Canal: {canal}

REGLAS:
- Máximo 60 palabras para WhatsApp, 100 para email.
- Antes de pedir, reconocer lo que el cliente ya logró (si se conoce).
- Ser específico sobre a quién se le pide que recomiende (empresa del sector, colega, etc.).
- Hacer fácil que diga que sí: que el esfuerzo del cliente sea mínimo.
- No ofrecer descuento ni beneficio económico — suena transaccional.

Devolvé:
1. canal: "{canal}"
2. mensaje: el mensaje completo
3. logica: 1 línea explicando por qué este momento y enfoque son los adecuados
4. que_ofrecer: si hay algo no monetario que se pueda ofrecer (ej: "una llamada de consultoría gratis para su referido"), describilo. null si no hay nada natural.

Respondé solo con JSON."""

    raw = _chat(prompt, system, max_tokens=500, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    result.setdefault("canal", canal)
    result.setdefault("mensaje", "")
    result.setdefault("logica", "")
    result.setdefault("que_ofrecer", None)
    return result


# ─── MÓDULO 9C: MOMENTO ÓPTIMO ───────────────────────────────────────────────

def calcular_momento_optimo(
    sector_lead: str = "",
    cargo_contacto: str = "",
    zona: str = "",
    pais: str = "AR",
    historial_contactos_exitosos: list[dict] | None = None,
    # [{"dia_semana": "martes", "hora": 10, "resultado": "respondio"}]
) -> Optional[dict]:
    """
    Calcula cuándo es el mejor momento para contactar a un lead específico.

    Devuelve:
    {
        "dia_recomendado": str,
        "hora_inicio": int,
        "hora_fin": int,
        "razon": str,
        "dias_evitar": [str],
        "confianza": "alta" | "media" | "baja",
    }
    """
    if not ANTHROPIC_API_KEY:
        return None

    historial_str = json.dumps(historial_contactos_exitosos or [], ensure_ascii=False)

    system = """Sos JARVIS analizando el momento óptimo para contactar un lead.
Basás tu análisis en: sector, cargo, zona, historial del aliado y patrones generales del mercado LATAM.
Respondé siempre con JSON válido."""

    prompt = f"""Calculá el mejor momento para contactar a este lead.

DATOS:
- Sector: {sector_lead}
- Cargo del contacto: {cargo_contacto}
- Zona: {zona}, {pais}
- Historial de contactos exitosos del aliado en este sector: {historial_str}

Considerá:
- Los gerentes de producción industrial suelen tener reuniones de mañana, mejor contactar 9-11am.
- Los directivos responden mejor a fin de semana laboral (jueves-viernes).
- En el agro: evitar lunes (revisión de semana) y viernes (campo).
- En logística: martes y miércoles son los mejores días (lunes caos, jueves-viernes picos).
- En metalúrgica: martes y miércoles, 9-11am, antes de que el turno tarde tome el control.

Devolvé:
1. dia_recomendado: el mejor día de la semana (lunes, martes, etc.)
2. hora_inicio: hora de inicio del bloque recomendado (formato 24h, ej: 9)
3. hora_fin: hora de fin del bloque (ej: 11)
4. razon: por qué este momento (1-2 líneas, específico al sector y cargo)
5. dias_evitar: lista de días a evitar para este perfil
6. confianza: "alta" si hay historial propio, "media" si es por sector, "baja" si es estimación general

Respondé solo con JSON."""

    raw = _chat(prompt, system, max_tokens=400, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    result.setdefault("dia_recomendado", "martes")
    result.setdefault("hora_inicio", 9)
    result.setdefault("hora_fin", 11)
    result.setdefault("razon", "")
    result.setdefault("dias_evitar", ["lunes"])
    result.setdefault("confianza", "media")
    return result


# ─── REGISTER: inyecta las rutas en la app FastAPI ───────────────────────────

def register(app, get_db_func, auth_dep):
    """
    Llamar desde main.py:
        import jarvis_proactivo
        jarvis_proactivo.register(app, get_db, current_aliado_required)
    """
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
    from typing import Optional
    from sqlalchemy.orm import Session
    import json
    from datetime import date

    class ReactivacionRequest(BaseModel):
        prospecto_id: int
        canal: str = "whatsapp"

    class SeguimientoRequest(BaseModel):
        prospecto_id: int
        canal: str = "email"

    class CheckInRequest(BaseModel):
        cliente_id: int
        canal: str = "whatsapp"

    class ReferidoRequest(BaseModel):
        cliente_id: int
        canal: str = "whatsapp"
        sector_objetivo: Optional[str] = ""

    # ── Health check ──────────────────────────────────────────────────────────
    @app.get("/jarvis/proactivo/estado")
    def proactivo_estado():
        return {
            "activo": is_enabled(),
            "modulo": "JARVIS Proactivo (Módulo 9)",
            "funciones": [
                "detectar_alertas",
                "generar_reactivacion",
                "generar_seguimiento_propuesta",
                "generar_check_in_cliente",
                "generar_pedido_referido",
                "calcular_momento_optimo",
            ],
            "umbrales": {
                "lead_frio_dias": UMBRAL_LEAD_SIN_CONTACTO_DIAS,
                "propuesta_sin_resp_dias": UMBRAL_PROPUESTA_SIN_RESPUESTA,
                "cliente_inactivo_dias": UMBRAL_CLIENTE_SIN_ACTIVIDAD,
                "referido_meses_activo": UMBRAL_REFERIDO_MESES_ACTIVO,
            },
        }

    # ── Detectar alertas del aliado ───────────────────────────────────────────
    @app.get("/jarvis/proactivo/alertas")
    def endpoint_alertas(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Devuelve todas las alertas proactivas del aliado, ordenadas por prioridad.
        Se llama al abrir el portal y cada vez que el aliado quiera ver su estado.
        """
        try:
            rubros = []
            try:
                rubros_raw = getattr(aliado, "rubros_especialidad", "[]") or "[]"
                rubros = json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
            except Exception:
                pass

            hoy = date.today()

            # Construir lista de prospectos con días calculados
            prospectos_data = []
            for p in (getattr(aliado, "prospectos", []) or []):
                ultimo = getattr(p, "ultimo_contacto", None)
                dias_sin_c = 0
                if ultimo:
                    try:
                        dias_sin_c = (hoy - (ultimo.date() if hasattr(ultimo, "date") else ultimo)).days
                    except Exception:
                        pass

                propuesta_enviada = getattr(p, "propuesta_enviada", False)
                dias_prop = 0
                if propuesta_enviada:
                    fecha_prop = getattr(p, "fecha_propuesta", None)
                    if fecha_prop:
                        try:
                            dias_prop = (hoy - (fecha_prop.date() if hasattr(fecha_prop, "date") else fecha_prop)).days
                        except Exception:
                            pass

                prospectos_data.append({
                    "id": getattr(p, "id", None),
                    "empresa": getattr(p, "empresa", "?"),
                    "contacto": getattr(p, "nombre_contacto", "") or "",
                    "estado": getattr(p, "estado", "prospecto"),
                    "dias_sin_contacto": dias_sin_c,
                    "tiene_propuesta": bool(propuesta_enviada),
                    "dias_propuesta_sin_resp": dias_prop,
                })

            # Clientes activos (ventas confirmadas)
            clientes_data = []
            for v in (getattr(aliado, "ventas", []) or []):
                if not getattr(v, "confirmada", False):
                    continue
                fecha_v = getattr(v, "fecha", None)
                meses = 0
                if fecha_v:
                    try:
                        delta = hoy - (fecha_v.date() if hasattr(fecha_v, "date") else fecha_v)
                        meses = delta.days // 30
                    except Exception:
                        pass

                ultimo_act = getattr(v, "ultima_actividad", None)
                dias_sin_act = 0
                if ultimo_act:
                    try:
                        dias_sin_act = (hoy - (ultimo_act.date() if hasattr(ultimo_act, "date") else ultimo_act)).days
                    except Exception:
                        pass

                clientes_data.append({
                    "id": getattr(v, "id", None),
                    "empresa": getattr(v, "empresa_cliente", "?"),
                    "meses_activo": meses,
                    "dias_sin_actividad": dias_sin_act,
                })

            alertas = detectar_alertas(
                aliado_nombre=getattr(aliado, "nombre", ""),
                aliado_rubros=rubros,
                aliado_pais=getattr(aliado, "pais", "AR"),
                prospectos=prospectos_data,
                clientes_activos=clientes_data,
                leads_nuevos_bolsa=[],  # Se conecta a la bolsa de leads en la implementación completa
                fecha_hoy=hoy,
                nivel_ruido=getattr(aliado, "jarvis_nivel_ruido", "medio") or "medio",
            )

            return JSONResponse({
                "ok": True,
                "alertas": alertas,
                "total": len(alertas),
                "criticas": sum(1 for a in alertas if a["prioridad"] == "critica"),
                "altas": sum(1 for a in alertas if a["prioridad"] == "alta"),
                "medias": sum(1 for a in alertas if a["prioridad"] == "media"),
            })

        except Exception as e:
            print(f"[JARVIS PROACTIVO] Error en alertas: {e}", file=sys.stderr)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # ── Generar reactivación ──────────────────────────────────────────────────
    @app.post("/jarvis/proactivo/reactivacion")
    def endpoint_reactivacion(
        body: ReactivacionRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Genera un mensaje de reactivación para un lead específico."""
        try:
            # Buscar el prospecto por ID
            prospecto = None
            for p in (getattr(aliado, "prospectos", []) or []):
                if getattr(p, "id", None) == body.prospecto_id:
                    prospecto = p
                    break

            if not prospecto:
                return JSONResponse({"ok": False, "error": "Prospecto no encontrado"}, status_code=404)

            hoy = date.today()
            ultimo = getattr(prospecto, "ultimo_contacto", None)
            dias = 0
            if ultimo:
                try:
                    dias = (hoy - (ultimo.date() if hasattr(ultimo, "date") else ultimo)).days
                except Exception:
                    pass

            rubros = []
            try:
                rubros_raw = getattr(aliado, "rubros_especialidad", "[]") or "[]"
                rubros = json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
            except Exception:
                pass

            result = generar_reactivacion(
                aliado_nombre=getattr(aliado, "nombre", ""),
                aliado_ciudad=getattr(aliado, "ciudad", ""),
                aliado_pais=getattr(aliado, "pais", "AR"),
                aliado_rubros=rubros,
                empresa_lead=getattr(prospecto, "empresa", ""),
                contacto_nombre=getattr(prospecto, "nombre_contacto", ""),
                contacto_cargo=getattr(prospecto, "cargo_contacto", ""),
                dias_sin_contacto=dias,
                ultimo_tema=getattr(prospecto, "notas", "") or "",
                canal=body.canal,
            )

            if not result:
                return JSONResponse({"ok": False, "error": "JARVIS no disponible"}, status_code=503)

            return JSONResponse({"ok": True, "reactivacion": result})

        except Exception as e:
            print(f"[JARVIS PROACTIVO] Error en reactivación: {e}", file=sys.stderr)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # ── Generar seguimiento de propuesta ──────────────────────────────────────
    @app.post("/jarvis/proactivo/seguimiento-propuesta")
    def endpoint_seguimiento(
        body: SeguimientoRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Genera un seguimiento estratégico para una propuesta sin respuesta."""
        try:
            prospecto = None
            for p in (getattr(aliado, "prospectos", []) or []):
                if getattr(p, "id", None) == body.prospecto_id:
                    prospecto = p
                    break

            if not prospecto:
                return JSONResponse({"ok": False, "error": "Prospecto no encontrado"}, status_code=404)

            hoy = date.today()
            fecha_prop = getattr(prospecto, "fecha_propuesta", None)
            dias = 0
            if fecha_prop:
                try:
                    dias = (hoy - (fecha_prop.date() if hasattr(fecha_prop, "date") else fecha_prop)).days
                except Exception:
                    pass

            rubros = []
            try:
                rubros_raw = getattr(aliado, "rubros_especialidad", "[]") or "[]"
                rubros = json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
            except Exception:
                pass

            result = generar_seguimiento_propuesta(
                aliado_nombre=getattr(aliado, "nombre", ""),
                aliado_pais=getattr(aliado, "pais", "AR"),
                aliado_rubros=rubros,
                empresa_cliente=getattr(prospecto, "empresa", ""),
                contacto_nombre=getattr(prospecto, "nombre_contacto", ""),
                contacto_cargo=getattr(prospecto, "cargo_contacto", ""),
                plan_propuesto=getattr(prospecto, "plan_propuesto", ""),
                valor_propuesta=float(getattr(prospecto, "valor_propuesta", 0) or 0),
                dias_sin_respuesta=dias,
                canal=body.canal,
            )

            if not result:
                return JSONResponse({"ok": False, "error": "JARVIS no disponible"}, status_code=503)

            return JSONResponse({"ok": True, "seguimiento": result})

        except Exception as e:
            print(f"[JARVIS PROACTIVO] Error en seguimiento: {e}", file=sys.stderr)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # ── Generar check-in de cliente ───────────────────────────────────────────
    @app.post("/jarvis/proactivo/check-in")
    def endpoint_check_in(
        body: CheckInRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Genera un check-in para un cliente activo sin actividad."""
        try:
            venta = None
            for v in (getattr(aliado, "ventas", []) or []):
                if getattr(v, "id", None) == body.cliente_id:
                    venta = v
                    break

            if not venta:
                return JSONResponse({"ok": False, "error": "Cliente no encontrado"}, status_code=404)

            hoy = date.today()
            fecha_v = getattr(venta, "fecha", None)
            meses = 0
            if fecha_v:
                try:
                    delta = hoy - (fecha_v.date() if hasattr(fecha_v, "date") else fecha_v)
                    meses = delta.days // 30
                except Exception:
                    pass

            ultimo_act = getattr(venta, "ultima_actividad", None)
            dias_sin_act = 0
            if ultimo_act:
                try:
                    dias_sin_act = (hoy - (ultimo_act.date() if hasattr(ultimo_act, "date") else ultimo_act)).days
                except Exception:
                    pass

            result = generar_check_in_cliente(
                aliado_nombre=getattr(aliado, "nombre", ""),
                aliado_pais=getattr(aliado, "pais", "AR"),
                empresa_cliente=getattr(venta, "empresa_cliente", ""),
                contacto_nombre=getattr(venta, "nombre_contacto", ""),
                meses_activo=meses,
                dias_sin_actividad=dias_sin_act,
                plan_activo=getattr(venta, "plan", ""),
                canal=body.canal,
            )

            if not result:
                return JSONResponse({"ok": False, "error": "JARVIS no disponible"}, status_code=503)

            return JSONResponse({"ok": True, "check_in": result})

        except Exception as e:
            print(f"[JARVIS PROACTIVO] Error en check-in: {e}", file=sys.stderr)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
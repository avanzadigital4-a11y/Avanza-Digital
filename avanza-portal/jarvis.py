"""
jarvis.py — Motor de inteligencia JARVIS con Claude (Anthropic).

DISEÑO:
- Mismo patrón que groq_ai.py: si ANTHROPIC_API_KEY no está o Claude falla,
  TODAS las funciones devuelven None y el llamador usa su fallback heurístico.
- El producto NUNCA se cae por un problema con la IA.
- Timeout duro de 15 segundos.
- No se loggea la API key.

FUNCIONES PRINCIPALES:
  chat_jarvis()              → Módulo 1: Cerebro Comercial (conversación contextual)
  analizar_lead_bolsa()      → Módulo 2: Motor de Leads (análisis completo de LeadBolsa)
  perfilar_prospecto()       → Perfilado mejorado de Prospecto (reemplaza groq_ai versión)
  generar_followup()         → Comunicador: mensajes de seguimiento
  responder_objecion()       → Comunicador: manejo de objeciones
  generar_propuesta()        → Módulo 3: Propuesta comercial completa
"""

from __future__ import annotations
import os, json, sys, time
from typing import Optional, Any

# ─── CONFIG ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
from jarvis_config import JARVIS_MODEL, get_client  # config centralizada en jarvis_config.py
JARVIS_TIMEOUT    = 15.0

# Planes de Avanza — deben coincidir con models.PLANES
PLANES_AVANZA = {
    "Plan Base":       1190.0,
    "Plan Pro":        3490.0,
    "Plan Industrial": 6990.0,
    "Estrategico 360": 9999.0,
}


def is_enabled() -> bool:
    """¿Hay API key configurada?"""
    return bool(ANTHROPIC_API_KEY)


# ─── CORE: llamada a Claude ───────────────────────────────────────────────────

def _chat(
    prompt: str,
    system: str,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.4,
    json_mode: bool = False,
) -> Optional[str]:
    """
    Llama a Claude y devuelve el texto de respuesta, o None si algo falla.
    NUNCA lanza excepciones — None es la señal de 'usá tu fallback'.
    """
    if not ANTHROPIC_API_KEY:
        return None

    try:
        client = get_client()
        if client is None:
            return None

        system_final = system
        if json_mode:
            system_final = system + "\n\nIMPORTANTE: Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después. Sin bloques de código markdown."

        message = client.messages.create(
            model=JARVIS_MODEL,
            max_tokens=max_tokens,
            system=system_final,
            messages=[{"role": "user", "content": prompt}],
            timeout=JARVIS_TIMEOUT,
        )
        return message.content[0].text.strip()

    except Exception as e:
        print(f"[JARVIS ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _parse_json(text: str) -> Optional[dict]:
    """Parsea JSON con tolerancia a texto extra antes/después."""
    if not text:
        return None
    try:
        # Intento directo
        return json.loads(text)
    except Exception:
        pass
    try:
        # Buscar el primer { ... } en la respuesta
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    print(f"[JARVIS] No se pudo parsear JSON: {text[:200]}", file=sys.stderr)
    return None


# ─── CONSTRUCTOR DE CONTEXTO DEL ALIADO ──────────────────────────────────────

def _build_aliado_context(
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    aliado_pais: str = "AR",
    aliado_rubros: list[str] | None = None,
    aliado_nivel: str = "BASIC",
    aliado_ventas: int = 0,
    aliado_perfil: str = "",
) -> str:
    """Construye el bloque de contexto del aliado para el system prompt."""
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "general"
    pais_nombre = {
        "AR": "Argentina", "MX": "México", "CO": "Colombia",
        "CL": "Chile", "PE": "Perú", "UY": "Uruguay",
    }.get(aliado_pais, aliado_pais)

    nivel_desc = {
        "BASIC":   "aliado nuevo, menos de 1 venta confirmada",
        "SILVER":  "aliado activo, 1+ ventas confirmadas",
        "PREMIUM": "aliado experimentado, 2+ ventas en 6 meses",
        "ELITE":   "aliado top, 5+ ventas en 6 meses",
    }.get(aliado_nivel, "aliado activo")

    return f"""
IDENTIDAD DEL ALIADO:
- Nombre: {aliado_nombre or 'el aliado'}
- Ciudad: {aliado_ciudad or 'no especificada'}, {pais_nombre}
- Sectores de especialidad: {rubros_str}
- Nivel: {aliado_nivel} ({nivel_desc})
- Ventas confirmadas: {aliado_ventas}
- Perfil adicional: {aliado_perfil or 'sin datos adicionales'}
""".strip()


# ─── MÓDULO 1: CEREBRO COMERCIAL — Chat contextual ───────────────────────────

def chat_jarvis(
    mensaje_aliado: str,
    historial: list[dict] | None = None,
    *,
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    aliado_pais: str = "AR",
    aliado_rubros: list[str] | None = None,
    aliado_nivel: str = "BASIC",
    aliado_ventas: int = 0,
    aliado_perfil: str = "",
    ajuste_emocional: str = "",   # instrucción de tono desde jarvis_emocional
) -> Optional[dict]:
    """
    Módulo 1 — Cerebro Comercial.
    Conversación contextual con JARVIS usando el perfil completo del aliado.

    Retorna:
        {
          "respuesta": str,
          "confianza": "propio" | "sectorial" | "general",
          "accion_sugerida": str | None,
        }
    O None si Claude no está disponible.
    """
    if not ANTHROPIC_API_KEY:
        return None

    contexto = _build_aliado_context(
        aliado_nombre=aliado_nombre,
        aliado_ciudad=aliado_ciudad,
        aliado_pais=aliado_pais,
        aliado_rubros=aliado_rubros,
        aliado_nivel=aliado_nivel,
        aliado_ventas=aliado_ventas,
        aliado_perfil=aliado_perfil,
    )

    system = f"""Sos JARVIS, el asistente de inteligencia comercial de Avanza Digital.
Tu función es ayudar a los aliados de Avanza a vender más: analizar leads, preparar reuniones, generar comunicaciones, y dar inteligencia de mercado.

{contexto}

PLANES QUE VENDE AVANZA:
- Plan Base: $1.190 ARS/mes — para empresas chicas que necesitan presencia digital básica
- Plan Pro: $3.490 ARS/mes — el más vendido, incluye web + SEO + gestión de consultas
- Plan Industrial: $6.990 ARS/mes — para empresas medianas con necesidades técnicas específicas
- Estratégico 360: $9.999 ARS/mes — solución completa para empresas grandes

REGLAS DE OPERACIÓN:
1. Respondé siempre en español rioplatense (vos, che, etc.) para aliados de Argentina.
   Para otros países, adaptá el dialecto.
2. Sé directo y accionable. Cada respuesta debe terminar con algo que el aliado pueda HACER.
3. Si no tenés datos suficientes para una respuesta precisa, decilo y pedí lo que necesitás.
4. Cuando detectes una oportunidad no solicitada, mencionala brevemente al final.
5. Identificá si el aliado está en modo: prospección / preparando reunión / manejando objeción / cierre / postventa.
6. Respondé en JSON con este formato exacto:
{{
  "respuesta": "tu respuesta completa en texto natural",
  "confianza": "propio" | "sectorial" | "general",
  "accion_sugerida": "una acción concreta para hacer ahora, o null"
}}
"""

    # Construir historial de conversación
    messages = []
    if historial:
        for msg in historial[-6:]:  # Últimos 6 turnos para no exceder contexto
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": mensaje_aliado})

    try:
        client = get_client()
        if client is None:
            return None

        # Inyectar ajuste emocional al system prompt si corresponde
        system_final = system
        if ajuste_emocional:
            system_final += f"\n\n{ajuste_emocional}"
        system_final += "\n\nIMPORTANTE: Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."

        t_inicio = time.time()
        response = client.messages.create(
            model=JARVIS_MODEL,
            max_tokens=1500,
            system=system_final,
            messages=messages,
            timeout=JARVIS_TIMEOUT,
        )
        t_ms = int((time.time() - t_inicio) * 1000)
        raw = response.content[0].text.strip()
        parsed = _parse_json(raw)

        if parsed and "respuesta" in parsed:
            parsed["tiempo_ms"] = t_ms
            return parsed

        # Si no parseó bien, devolver la respuesta como texto plano
        return {
            "respuesta": raw,
            "confianza": "general",
            "accion_sugerida": None,
            "tiempo_ms": t_ms,
        }

    except Exception as e:
        print(f"[JARVIS chat_jarvis ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return None


# ─── MÓDULO 2: MOTOR DE LEADS — Análisis completo de LeadBolsa ───────────────

def analizar_lead_bolsa(
    empresa: str,
    rubro: str,
    ciudad: str = "",
    pais: str = "AR",
    nombre_contacto: str = "",
    tiene_web: bool = False,
    tiene_redes: bool = False,
    web: str = "",
    observacion: str = "",
    *,
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    aliado_pais: str = "AR",
    aliado_rubros: list[str] | None = None,
    aliado_ventas: int = 0,
) -> Optional[dict]:
    """
    Módulo 2 — Motor de Leads.
    Análisis completo de un lead de la bolsa con score, perfil del comprador,
    script de contacto y manejo de objeciones.

    Retorna:
        {
          "score": int (0-100),
          "temperatura": "frio" | "tibio" | "caliente",
          "plan_recomendado": str,
          "ticket_esperado": float,
          "razon": str,
          "perfil_comprador": str,
          "script_whatsapp": str,
          "objeciones": [ {"objecion": str, "respuesta": str}, ... ],
          "canal_recomendado": str,
          "momento_optimo": str,
          "proxima_accion": str,
        }
    O None si Claude no está disponible.
    """
    contexto_aliado = _build_aliado_context(
        aliado_nombre=aliado_nombre,
        aliado_ciudad=aliado_ciudad,
        aliado_pais=aliado_pais,
        aliado_rubros=aliado_rubros,
        aliado_ventas=aliado_ventas,
    )

    presencia = []
    if tiene_web and web:
        presencia.append(f"tiene web: {web}")
    elif tiene_web:
        presencia.append("tiene web (URL no especificada)")
    else:
        presencia.append("NO tiene web")
    if tiene_redes:
        presencia.append("tiene redes sociales")
    else:
        presencia.append("NO tiene redes sociales")
    presencia_str = ", ".join(presencia)

    pais_nombre = {
        "AR": "Argentina", "MX": "México", "CO": "Colombia",
        "CL": "Chile", "PE": "Perú", "UY": "Uruguay",
    }.get(pais, pais)

    prompt = f"""
Analizá este lead para el aliado de Avanza Digital.

DATOS DEL LEAD:
- Empresa: {empresa}
- Rubro/Sector: {rubro}
- Ciudad: {ciudad or 'no especificada'}, {pais_nombre}
- Contacto: {nombre_contacto or 'no especificado'}
- Presencia digital actual: {presencia_str}
- Observación adicional: {observacion or 'ninguna'}

CONTEXTO DEL ALIADO QUE VA A CONTACTAR:
{contexto_aliado}

PLANES DISPONIBLES:
- Plan Base: $1.190/mes (empresas chicas, arranque digital)
- Plan Pro: $3.490/mes (el más vendido — web + SEO + leads)
- Plan Industrial: $6.990/mes (empresas medianas con necesidades técnicas)
- Estratégico 360: $9.999/mes (empresas grandes, solución completa)

Generá un análisis completo con este JSON exacto:
{{
  "score": <número 0-100 que refleja el potencial real del lead>,
  "temperatura": "<frio|tibio|caliente>",
  "plan_recomendado": "<nombre exacto del plan>",
  "ticket_esperado": <precio del plan recomendado como número>,
  "razon": "<explicación de 1-2 oraciones del score y plan en español rioplatense>",
  "perfil_comprador": "<descripción del decisor típico en este tipo de empresa — qué le importa, qué le da miedo, qué lo motiva>",
  "script_whatsapp": "<mensaje listo para enviar por WhatsApp, máximo 60 palabras, tono natural, menciona un beneficio concreto para ese rubro>",
  "objeciones": [
    {{"objecion": "<objeción probable 1>", "respuesta": "<respuesta concreta y efectiva>"}},
    {{"objecion": "<objeción probable 2>", "respuesta": "<respuesta concreta y efectiva>"}},
    {{"objecion": "<objeción probable 3>", "respuesta": "<respuesta concreta y efectiva>"}}
  ],
  "canal_recomendado": "<WhatsApp|Email|LinkedIn|Llamada>",
  "momento_optimo": "<cuándo contactar y por qué>",
  "proxima_accion": "<qué hacer exactamente ahora mismo>"
}}
"""

    system = """Sos JARVIS, el motor de análisis de leads de Avanza Digital.
Tu análisis debe ser específico al rubro industrial latinoamericano, no genérico.
Conocés el ciclo de compra B2B industrial: decisiones lentas, múltiples aprobaciones, foco en ROI y confianza.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después. Sin bloques de código markdown."""

    raw = _chat(prompt, system, max_tokens=1500, temperature=0.3, json_mode=True)
    if not raw:
        return None

    parsed = _parse_json(raw)
    if not parsed or "score" not in parsed:
        return None

    # Asegurar tipos correctos
    parsed["score"] = max(0, min(100, int(parsed.get("score", 50))))
    parsed["ticket_esperado"] = float(parsed.get("ticket_esperado", 3490.0))
    if "objeciones" not in parsed:
        parsed["objeciones"] = []

    return parsed


# ─── MÓDULO 2 (PROSPECTO): Perfilado mejorado ────────────────────────────────

def perfilar_prospecto(
    empresa: str,
    rubro: str = "",
    tamano: str = "pyme",
    urgencia: str = "media",
    estado: str = "sin_contactar",
    nota_aliado: str = "",
    ciudad: str = "",
    *,
    aliado_nombre: str = "",
    aliado_rubros: list[str] | None = None,
) -> Optional[dict]:
    """
    Perfilado IA mejorado de un Prospecto.
    Drop-in replacement del groq_ai.perfilar_lead_ia() con más detalle.

    Retorna mismo formato que groq_ai para compatibilidad:
        { "score", "plan_recomendado", "pitch_sugerido", "ticket_esperado", "razon" }
    """
    prompt = f"""
Perfilá este prospecto para el aliado de Avanza Digital.

DATOS DEL PROSPECTO:
- Empresa: {empresa}
- Rubro: {rubro or 'no especificado'}
- Tamaño: {tamano}
- Urgencia del cliente: {urgencia}
- Estado actual: {estado}
- Nota del aliado: {nota_aliado or 'ninguna'}
- Ciudad: {ciudad or 'no especificada'}

ALIADO: {aliado_nombre or 'el aliado'} — especialidad: {', '.join(aliado_rubros) if aliado_rubros else 'general'}

PLANES:
- Plan Base: $1.190/mes
- Plan Pro: $3.490/mes (más vendido)
- Plan Industrial: $6.990/mes
- Estratégico 360: $9.999/mes

Devolvé exactamente este JSON:
{{
  "score": <número 0-100>,
  "plan_recomendado": "<nombre exacto del plan>",
  "pitch_sugerido": "<pitch de 2-3 oraciones listo para usar en primer contacto — específico al rubro, en español rioplatense>",
  "ticket_esperado": <precio del plan como número>,
  "razon": "<1 oración explicando el score y plan recomendado>"
}}
"""

    system = """Sos el motor de perfilado de JARVIS, especializado en ventas B2B industriales en Latinoamérica.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=600, temperature=0.3, json_mode=True)
    if not raw:
        return None

    parsed = _parse_json(raw)
    if not parsed or "score" not in parsed:
        return None

    parsed["score"] = max(0, min(100, int(parsed.get("score", 50))))
    parsed["ticket_esperado"] = float(parsed.get("ticket_esperado", 3490.0))
    return parsed


# ─── MÓDULO 4: COMUNICADOR — Follow-up ───────────────────────────────────────

def generar_followup(
    prospecto_nombre: str,
    rubro: str = "",
    tamano: str = "pyme",
    plan_recomendado: str = "",
    dias_sin_responder: int | None = None,
    ultima_nota: str = "",
    aliado_nombre: str = "",
    tono: str = "directo",
) -> Optional[dict]:
    """
    Genera un mensaje de follow-up personalizado.
    Drop-in replacement de groq_ai.generar_followup_ia() con mejor calidad.

    Retorna: { "mensaje": str, "estrategia": str }
    """
    dias_str = f"{dias_sin_responder} días sin responder" if dias_sin_responder else "tiempo sin responder no especificado"

    prompt = f"""
Generá un mensaje de follow-up para un prospecto que no responde.

CONTEXTO:
- Empresa prospecto: {prospecto_nombre}
- Rubro: {rubro or 'industrial'}
- Tamaño: {tamano}
- Plan conversado: {plan_recomendado or 'no definido'}
- Situación: {dias_str}
- Última nota del aliado: {ultima_nota or 'ninguna'}
- Aliado que escribe: {aliado_nombre or 'el aliado'}
- Tono requerido: {tono} (opciones: amigable / directo / ultimo / valor)

El mensaje debe ser:
- Máximo 80 palabras
- Natural, no corporativo
- Específico al contexto (no genérico)
- Con un CTA claro
- En español rioplatense

Devolvé este JSON exacto:
{{
  "mensaje": "<mensaje listo para copiar y pegar>",
  "estrategia": "<1 oración explicando la lógica del mensaje>"
}}
"""

    system = """Sos el módulo de comunicaciones de JARVIS para ventas B2B industriales en Argentina.
Escribís mensajes que suenan humanos, no de robot ni corporativos.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=400, temperature=0.5, json_mode=True)
    if not raw:
        return None

    return _parse_json(raw)


# ─── MÓDULO 4: COMUNICADOR — Objeciones ──────────────────────────────────────

def responder_objecion(
    objecion: str,
    prospecto_nombre: str = "",
    rubro: str = "",
    tamano: str = "pyme",
    plan_recomendado: str = "",
    ticket_esperado: float | None = None,
) -> Optional[dict]:
    """
    Genera una respuesta a una objeción específica.
    Drop-in replacement de groq_ai.responder_objecion_ia().

    Retorna: { "respuesta": str, "tipo_objecion": str, "tecnica": str }
    """
    ticket_str = f"${ticket_esperado:,.0f} ARS/mes" if ticket_esperado else "precio no definido"

    prompt = f"""
El prospecto dijo esta objeción: "{objecion}"

CONTEXTO:
- Empresa: {prospecto_nombre or 'el prospecto'}
- Rubro: {rubro or 'industrial'}
- Plan conversado: {plan_recomendado or 'no definido'} ({ticket_str})

Generá una respuesta efectiva que:
1. No sea defensiva ni agresiva
2. Reencuadre la objeción con valor concreto
3. Avance la conversación hacia la reunión o el sí
4. Sea en español rioplatense, máximo 60 palabras

Devolvé este JSON:
{{
  "respuesta": "<respuesta lista para enviar>",
  "tipo_objecion": "<precio|tiempo|no_lo_necesito|ya_tengo_proveedor|pensarlo|otro>",
  "tecnica": "<nombre de la técnica usada y por qué funciona acá>"
}}
"""

    system = """Sos el módulo de manejo de objeciones de JARVIS, especializado en ventas B2B industriales.
Conocés las objeciones típicas del mercado industrial latinoamericano.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=400, temperature=0.4, json_mode=True)
    if not raw:
        return None

    return _parse_json(raw)


# ─── MÓDULO 3: GENERADOR DE PROPUESTAS ───────────────────────────────────────

def generar_propuesta(
    empresa_cliente: str,
    rubro: str,
    nombre_contacto: str = "",
    plan: str = "Plan Pro",
    dolores_detectados: str = "",
    nota_aliado: str = "",
    *,
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
) -> Optional[dict]:
    """
    Módulo 3 — Generador de Propuestas.
    Genera el esqueleto de una propuesta comercial personalizada.

    Retorna:
        {
          "asunto_email": str,
          "introduccion": str,
          "propuesta_valor": str,
          "roi_estimado": str,
          "llamada_accion": str,
          "notas_aliado": str,
        }
    """
    ticket = PLANES_AVANZA.get(plan, 3490.0)

    prompt = f"""
Generá una propuesta comercial para presentarle a este prospecto.

DATOS:
- Empresa cliente: {empresa_cliente}
- Rubro: {rubro}
- Contacto: {nombre_contacto or 'decisor'}
- Plan a proponer: {plan} (${ticket:,.0f} ARS/mes)
- Dolores detectados: {dolores_detectados or 'no especificados'}
- Nota del aliado: {nota_aliado or 'ninguna'}
- Aliado que presenta: {aliado_nombre or 'el aliado'} ({aliado_ciudad or ''})

Devolvé este JSON:
{{
  "asunto_email": "<asunto del email de propuesta — específico, no genérico>",
  "introduccion": "<párrafo de apertura que conecta con el dolor específico del cliente>",
  "propuesta_valor": "<2-3 párrafos con el valor concreto del plan para este rubro>",
  "roi_estimado": "<estimación de ROI o resultado esperable en 90 días para este tipo de empresa>",
  "llamada_accion": "<cierre del email con CTA claro>",
  "notas_aliado": "<tips internos para el aliado sobre cómo presentar esta propuesta>"
}}
"""

    system = """Sos el módulo de propuestas de JARVIS para Avanza Digital.
Generás propuestas que se leen porque son específicas al rubro del cliente, no genéricas.
Sabés de marketing digital, presencia web, SEO y generación de leads para PYMES industriales.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1200, temperature=0.4, json_mode=True)
    if not raw:
        return None

    return _parse_json(raw)
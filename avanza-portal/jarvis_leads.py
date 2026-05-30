"""
jarvis_leads.py — Módulo 2: Motor de Leads de JARVIS

Implementa el Motor de Leads completo del Blueprint v2, Sección 3 Módulo 2.

DISEÑO:
  Mismo patrón defensivo del resto de JARVIS: si la IA o la BD falla,
  las funciones devuelven None y el llamador usa su fallback heurístico.
  El producto NUNCA se cae por un problema con la IA.
  Timeout duro de 20 segundos.

FUNCIONES PRINCIPALES:
  analizar_lead_completo()        → Análisis 360° de un lead de la bolsa con score,
                                    perfil del comprador, scripts multi-canal, objeciones
                                    y plan de ataque paso a paso.

  generar_battle_card()           → Inteligencia competitiva: dado un competidor
                                    conocido, arma un battle card con fortalezas,
                                    debilidades y estrategia de posicionamiento.

  analizar_propuesta_competidor() → El aliado sube/pega la propuesta de la competencia.
                                    JARVIS la analiza y genera contrapropuesta táctica.

  detectar_señales()              → Analiza un mensaje o email del prospecto y detecta
                                    señales de compra, señales de fuga y temperatura real.

  sugerir_siguiente_paso()        → Dado el estado actual de un lead (notas, etapa,
                                    días sin contacto), devuelve la acción concreta
                                    más efectiva para avanzar el cierre.

  puntuar_pipeline()              → Recibe una lista de leads y devuelve un ranking
                                    priorizado con score y acción sugerida para cada uno.
                                    Útil para el Dashboard de Inteligencia.

INTEGRACIÓN:
  Llamar directamente desde jarvis_routes.py o desde cualquier endpoint de FastAPI.
  Compatible con los modelos Prospecto y LeadBolsa de models.py.
"""

from __future__ import annotations
import os, json, sys
from typing import Optional, Any

# ─── CONFIG ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 20.0

PLANES_AVANZA = {
    "Plan Base":       1050.0,
    "Plan Pro":        2900.0,
    "Plan Industrial": 4900.0,
    "Estrategico 360": 7500.0,
}

PAISES = {
    "AR": "Argentina", "MX": "México", "CO": "Colombia",
    "CL": "Chile",     "PE": "Perú",   "UY": "Uruguay",
    "VE": "Venezuela",
}


def is_enabled() -> bool:
    """¿Hay API key configurada?"""
    return bool(ANTHROPIC_API_KEY)


# ─── CORE: llamada a Claude ───────────────────────────────────────────────────

def _chat(
    prompt: str,
    system: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.3,
    json_mode: bool = False,
) -> Optional[str]:
    """
    Llama a Claude y devuelve el texto de respuesta, o None si algo falla.
    NUNCA lanza excepciones — None es la señal de 'usá tu fallback'.
    """
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
        print(f"[JARVIS LEADS ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _parse_json(text: str) -> Optional[dict | list]:
    """Parsea JSON con tolerancia a texto extra antes/después."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        # Buscar primer objeto { }
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    try:
        # Buscar primer array [ ]
        start = text.find("[")
        end   = text.rfind("]") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    print(f"[JARVIS LEADS] No se pudo parsear JSON: {text[:200]}", file=sys.stderr)
    return None


def _build_aliado_context(
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    aliado_pais: str = "AR",
    aliado_rubros: list[str] | None = None,
    aliado_nivel: str = "BASIC",
    aliado_ventas: int = 0,
    aliado_perfil: str = "",
    adn_comercial: dict | None = None,
) -> str:
    """Construye el bloque de contexto del aliado para los prompts."""
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "general"
    pais_nombre = PAISES.get(aliado_pais, aliado_pais)
    nivel_desc = {
        "BASIC":   "aliado nuevo, menos de 1 venta confirmada",
        "SILVER":  "aliado activo, 1+ ventas confirmadas",
        "PREMIUM": "aliado experimentado, 2+ ventas en 6 meses",
        "ELITE":   "aliado top, 5+ ventas en 6 meses",
    }.get(aliado_nivel, "aliado activo")

    adn_str = ""
    if adn_comercial:
        ciclo   = adn_comercial.get("ciclo_promedio_dias", 0)
        ticket  = adn_comercial.get("ticket_promedio_ars", 0)
        tasa    = adn_comercial.get("tasa_cierre_historica", 0.0)
        objes   = ", ".join(adn_comercial.get("objeciones_frecuentes", [])) or "no registradas"
        args    = ", ".join(adn_comercial.get("argumentos_mas_efectivos", [])) or "no registrados"
        dia_ok  = adn_comercial.get("mejor_dia_contacto", "")
        hora_ok = adn_comercial.get("mejor_hora_contacto", "")
        adn_str = f"""
ADN COMERCIAL DEL ALIADO (de su historial):
- Ciclo promedio de cierre: {ciclo} días
- Ticket promedio: ${ticket:,.0f} ARS/mes
- Tasa de cierre histórica: {tasa:.0%}
- Objeciones que más recibe: {objes}
- Argumentos que más le funcionan: {args}
- Mejor día/hora de contacto: {dia_ok} {hora_ok}
""".strip()

    return f"""IDENTIDAD DEL ALIADO:
- Nombre: {aliado_nombre or 'el aliado'}
- Ciudad: {aliado_ciudad or 'no especificada'}, {pais_nombre}
- Sectores de especialidad: {rubros_str}
- Nivel: {aliado_nivel} ({nivel_desc})
- Ventas confirmadas: {aliado_ventas}
- Perfil adicional: {aliado_perfil or 'sin datos adicionales'}
{adn_str}""".strip()


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 1 — ANÁLISIS COMPLETO DE LEAD (el corazón del módulo)
# ═══════════════════════════════════════════════════════════════════════════════

def analizar_lead_completo(
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
    aliado_nivel: str = "BASIC",
    aliado_ventas: int = 0,
    aliado_perfil: str = "",
    adn_comercial: dict | None = None,
) -> Optional[dict]:
    """
    Módulo 2 — Motor de Leads. Análisis 360° de un lead de la bolsa.

    Va más allá del analizar_lead_bolsa() de jarvis.py: incorpora el ADN
    comercial del aliado, scripts multi-canal completos (WhatsApp, email,
    LinkedIn, llamada), y un plan de ataque paso a paso.

    Retorna:
        {
          "score": int (0-100),
          "temperatura": "frio" | "tibio" | "caliente",
          "fundamento_score": str,           # por qué ese score, con datos
          "plan_recomendado": str,
          "ticket_esperado": float,
          "confianza": "propio" | "sectorial" | "general",

          "perfil_comprador": {
            "cargo_probable": str,
            "que_le_importa": str,
            "que_le_da_miedo": str,
            "motivacion_real": str,
          },

          "scripts": {
            "whatsapp": str,                 # ≤60 palabras
            "email": {"asunto": str, "cuerpo": str},
            "linkedin": str,                 # ≤80 palabras
            "llamada": str,                  # script de 90 segundos
          },

          "objeciones": [
            {"objecion": str, "respuesta": str, "tecnica": str},
            ...                              # 4 objeciones
          ],

          "plan_ataque": [
            {"dia": int, "accion": str, "canal": str, "detalle": str},
            ...                              # 5 pasos
          ],

          "riesgos": [str, ...],             # 2-3 riesgos reales
          "oportunidades": [str, ...],       # 2-3 oportunidades
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
        aliado_nivel=aliado_nivel,
        aliado_ventas=aliado_ventas,
        aliado_perfil=aliado_perfil,
        adn_comercial=adn_comercial,
    )

    presencia_parts = []
    if tiene_web and web:
        presencia_parts.append(f"tiene web: {web}")
    elif tiene_web:
        presencia_parts.append("tiene web (URL no disponible)")
    else:
        presencia_parts.append("NO tiene web — oportunidad directa")
    presencia_parts.append("tiene redes sociales" if tiene_redes else "NO tiene redes sociales")
    presencia_str = "; ".join(presencia_parts)

    pais_nombre = PAISES.get(pais, pais)

    prompt = f"""Analizá este lead de la bolsa de Avanza Digital y generá un plan de ataque completo.

DATOS DEL LEAD:
- Empresa: {empresa}
- Rubro / Sector: {rubro}
- Ubicación: {ciudad or 'no especificada'}, {pais_nombre}
- Contacto probable: {nombre_contacto or 'no especificado'}
- Presencia digital: {presencia_str}
- Observación adicional: {observacion or 'ninguna'}

{contexto_aliado}

PLANES DISPONIBLES DE AVANZA:
- Plan Base: $1.050 ARS/mes (presencia digital básica)
- Plan Pro: $2.900 ARS/mes (el más vendido — web + SEO + leads)
- Plan Industrial: $4.900 ARS/mes (empresas medianas, necesidades técnicas)
- Estratégico 360: $7.500 ARS/mes (solución completa para empresas grandes)

Generá el análisis completo con este JSON exacto:
{{
  "score": <número 0-100 que refleja la probabilidad real de cierre>,
  "temperatura": "<frio|tibio|caliente>",
  "fundamento_score": "<explicación específica del score — por qué ese número, qué factores positivos y negativos pesaron>",
  "plan_recomendado": "<nombre exacto del plan de Avanza>",
  "ticket_esperado": <precio del plan recomendado como número>,
  "confianza": "<propio|sectorial|general>",

  "perfil_comprador": {{
    "cargo_probable": "<cargo del decisor más probable en este tipo de empresa>",
    "que_le_importa": "<qué métricas o resultados le importan realmente a este tipo de decisor>",
    "que_le_da_miedo": "<cuál es su mayor miedo al contratar algo nuevo>",
    "motivacion_real": "<qué lo movería a tomar acción HOY>"
  }},

  "scripts": {{
    "whatsapp": "<mensaje listo para WhatsApp — máximo 60 palabras, tono natural, específico al rubro, con un beneficio concreto y un CTA>",
    "email": {{
      "asunto": "<asunto del email — específico, no genérico, que llame la atención del decisor de este rubro>",
      "cuerpo": "<cuerpo del email — 100-150 palabras, párrafo de apertura con gancho, propuesta de valor, CTA claro>"
    }},
    "linkedin": "<mensaje de LinkedIn para el decisor — máximo 80 palabras, tono ejecutivo, sin ser agresivo>",
    "llamada": "<script de llamada de 90 segundos: apertura de 10 seg, propuesta de valor de 30 seg, pregunta de calificación de 20 seg, cierre para reunión de 30 seg>"
  }},

  "objeciones": [
    {{"objecion": "<objeción probable 1>", "respuesta": "<respuesta concreta en máximo 40 palabras>", "tecnica": "<nombre de la técnica usada>"}},
    {{"objecion": "<objeción probable 2>", "respuesta": "<respuesta concreta en máximo 40 palabras>", "tecnica": "<nombre de la técnica usada>"}},
    {{"objecion": "<objeción probable 3>", "respuesta": "<respuesta concreta en máximo 40 palabras>", "tecnica": "<nombre de la técnica usada>"}},
    {{"objecion": "<objeción probable 4>", "respuesta": "<respuesta concreta en máximo 40 palabras>", "tecnica": "<nombre de la técnica usada>"}}
  ],

  "plan_ataque": [
    {{"dia": 0, "accion": "<acción del día 0>", "canal": "<WhatsApp|Email|LinkedIn|Llamada>", "detalle": "<detalle específico de qué hacer exactamente>"}},
    {{"dia": 2, "accion": "<acción del día 2>", "canal": "<canal>", "detalle": "<detalle>"}},
    {{"dia": 5, "accion": "<acción del día 5>", "canal": "<canal>", "detalle": "<detalle>"}},
    {{"dia": 9, "accion": "<acción del día 9>", "canal": "<canal>", "detalle": "<detalle>"}},
    {{"dia": 15, "accion": "<acción del día 15>", "canal": "<canal>", "detalle": "<detalle>"}}
  ],

  "riesgos": [
    "<riesgo real 1 — concreto y específico al lead>",
    "<riesgo real 2>",
    "<riesgo real 3>"
  ],

  "oportunidades": [
    "<oportunidad específica 1 — qué hace especialmente atractivo este lead>",
    "<oportunidad específica 2>",
    "<oportunidad específica 3>"
  ],

  "canal_recomendado": "<WhatsApp|Email|LinkedIn|Llamada — el primer canal con su justificación>",
  "momento_optimo": "<cuándo contactar y por qué — día, hora, contexto>",
  "proxima_accion": "<una sola acción concreta para hacer en los próximos 30 minutos>"
}}"""

    system = """Sos el Motor de Leads de JARVIS para Avanza Digital.
Tu análisis es específico al sector industrial latinoamericano B2B — no genérico.
Conocés el ciclo de compra industrial: decisiones lentas, múltiples aprobaciones, foco en ROI y confianza.
Sabés que el 80% de los cierres industriales requieren 3+ contactos y que el primer mensaje es crítico.
Los scripts deben sonar como los escribió un humano — no un robot ni un template de curso de ventas.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después. Sin bloques de código markdown."""

    raw = _chat(prompt, system, max_tokens=2500, temperature=0.35, json_mode=True)
    if not raw:
        return None

    parsed = _parse_json(raw)
    if not parsed or "score" not in parsed:
        return None

    # Normalizar tipos
    parsed["score"]          = max(0, min(100, int(parsed.get("score", 50))))
    parsed["ticket_esperado"] = float(parsed.get("ticket_esperado", 2900.0))
    if "objeciones" not in parsed:
        parsed["objeciones"] = []
    if "plan_ataque" not in parsed:
        parsed["plan_ataque"] = []
    if "riesgos" not in parsed:
        parsed["riesgos"] = []
    if "oportunidades" not in parsed:
        parsed["oportunidades"] = []

    return parsed


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 2 — BATTLE CARD DE COMPETIDOR
# ═══════════════════════════════════════════════════════════════════════════════

def generar_battle_card(
    competidor_nombre: str,
    rubro: str = "",
    contexto_adicional: str = "",
    *,
    aliado_nombre: str = "",
    aliado_rubros: list[str] | None = None,
    aliado_pais: str = "AR",
) -> Optional[dict]:
    """
    Inteligencia competitiva: dado un competidor, genera un battle card completo.

    Usado cuando el aliado dice: "¿qué onda [competidor], que me aparece en
    todos lados?" o cuando prepara una reunión donde sabe que hay competencia.

    Retorna:
        {
          "competidor": str,
          "perfil_general": str,
          "fortalezas": [str, ...],
          "debilidades": [str, ...],
          "como_posicionarse": str,
          "argumentos_ganadores": [str, ...],
          "que_no_decir": [str, ...],        # qué evitar al compararse
          "cuando_perdes_vs_ellos": str,
          "cuando_ganas_vs_ellos": str,
          "script_si_el_cliente_los_menciona": str,
        }
    O None si Claude no está disponible.
    """
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "marketing digital industrial"
    pais_nombre = PAISES.get(aliado_pais, aliado_pais)

    prompt = f"""Generá un battle card para ayudar a un aliado de Avanza Digital a posicionarse
contra este competidor en reuniones de ventas.

COMPETIDOR A ANALIZAR: {competidor_nombre}
RUBRO / CONTEXTO: {rubro or 'marketing digital y presencia web para PYMES industriales'}
CONTEXTO ADICIONAL DEL ALIADO: {contexto_adicional or 'ninguno'}

EL ALIADO:
- Representa a Avanza Digital en {pais_nombre}
- Sectores de especialidad: {rubros_str}
- Avanza ofrece: web + SEO + gestión de leads + IA comercial para PYMES industriales B2B
- Ventaja de Avanza: especialización en industria, conocimiento de rubro, IA JARVIS incluida,
  soporte local, planes desde $1.050/mes hasta $7.500/mes

Generá el battle card con este JSON exacto:
{{
  "competidor": "{competidor_nombre}",
  "perfil_general": "<resumen de 2-3 oraciones de quién es este competidor, qué ofrece y a quién le vende>",
  "fortalezas": [
    "<fortaleza real 1 del competidor — ser honesto, no subestimar>",
    "<fortaleza real 2>",
    "<fortaleza real 3>"
  ],
  "debilidades": [
    "<debilidad real 1 del competidor — explotable en la conversación de ventas>",
    "<debilidad real 2>",
    "<debilidad real 3>"
  ],
  "como_posicionarse": "<estrategia clara de cómo diferenciarse — qué eje de diferenciación usar>",
  "argumentos_ganadores": [
    "<argumento 1 donde Avanza gana claramente — con dato o ángulo específico>",
    "<argumento 2>",
    "<argumento 3>"
  ],
  "que_no_decir": [
    "<cosa a evitar 1 — hablar mal de ellos directamente, por ejemplo>",
    "<cosa a evitar 2>"
  ],
  "cuando_perdes_vs_ellos": "<en qué escenarios concretos el competidor tiene ventaja — para prepararse>",
  "cuando_ganas_vs_ellos": "<en qué escenarios Avanza tiene ventaja clara y cómo activarla>",
  "script_si_el_cliente_los_menciona": "<cómo responder si el cliente dice 'ya estoy viendo a [competidor]' — máximo 50 palabras, sin atacar al competidor>"
}}"""

    system = """Sos el módulo de inteligencia competitiva de JARVIS para Avanza Digital.
Analizás competidores del ecosistema de marketing digital y presencia web para PYMES en Latinoamérica.
Sos honesto — reconocés las fortalezas reales de los competidores. No fabricás ventajas falsas.
La estrategia de diferenciación se basa en la especialización industrial de Avanza, no en atacar al competidor.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=1500, temperature=0.35, json_mode=True)
    if not raw:
        return None

    return _parse_json(raw)


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 3 — ANÁLISIS DE PROPUESTA DE COMPETIDOR
# ═══════════════════════════════════════════════════════════════════════════════

def analizar_propuesta_competidor(
    propuesta_texto: str,
    empresa_cliente: str = "",
    rubro: str = "",
    *,
    aliado_nombre: str = "",
    aliado_pais: str = "AR",
) -> Optional[dict]:
    """
    El aliado pega o transcribe la propuesta de un competidor que el cliente compartió.
    JARVIS la analiza y genera una contrapropuesta táctica.

    propuesta_texto: el texto de la propuesta del competidor (puede ser extracto o resumen)

    Retorna:
        {
          "resumen_propuesta": str,
          "precio_competidor": str,
          "fortalezas_propuesta": [str, ...],
          "debilidades_propuesta": [str, ...],
          "angulos_de_ataque": [str, ...],       # cómo atacar las debilidades
          "estrategia_contrapropuesta": str,
          "que_cambiar_en_tu_propuesta": [str, ...],
          "argumento_diferenciador_clave": str,
          "script_para_cliente": str,            # cómo hablar con el cliente sobre esto
        }
    O None si Claude no está disponible.
    """
    pais_nombre = PAISES.get(aliado_pais, aliado_pais)

    prompt = f"""Un cliente compartió la propuesta de un competidor con el aliado de Avanza Digital.
Analizala y generá una estrategia de contrapropuesta.

PROPUESTA DEL COMPETIDOR (texto / extracto):
---
{propuesta_texto}
---

CONTEXTO:
- Empresa cliente: {empresa_cliente or 'no especificada'}
- Rubro del cliente: {rubro or 'industrial'}
- Aliado de Avanza: {aliado_nombre or 'el aliado'} ({pais_nombre})

Avanza Digital ofrece: web profesional + SEO industrial + generación de leads + IA JARVIS.
Planes desde $1.050/mes a $7.500/mes. Especialización en sector industrial B2B latinoamericano.

Generá el análisis con este JSON exacto:
{{
  "resumen_propuesta": "<resumen de 2-3 oraciones de qué propone el competidor>",
  "precio_competidor": "<precio detectado o 'no especificado'>",
  "fortalezas_propuesta": [
    "<fortaleza real 1 — qué tiene de bueno la propuesta del competidor>",
    "<fortaleza real 2>",
    "<fortaleza real 3>"
  ],
  "debilidades_propuesta": [
    "<debilidad real 1 — qué le falta o qué es débil en la propuesta del competidor>",
    "<debilidad real 2>",
    "<debilidad real 3>"
  ],
  "angulos_de_ataque": [
    "<ángulo 1: cómo explotar una debilidad del competidor en la conversación con el cliente — concreto y específico>",
    "<ángulo 2>",
    "<ángulo 3>"
  ],
  "estrategia_contrapropuesta": "<estrategia completa: qué hacer con tu propuesta de Avanza para ganar esta licitación — qué cambiar, qué destacar, qué precio manejar>",
  "que_cambiar_en_tu_propuesta": [
    "<cambio 1 que hacer en la propuesta de Avanza para ganar>",
    "<cambio 2>",
    "<cambio 3>"
  ],
  "argumento_diferenciador_clave": "<el argumento más poderoso que tiene Avanza vs esta propuesta específica — en 1-2 oraciones>",
  "script_para_cliente": "<cómo hablar con el cliente sobre haber visto la propuesta del competidor — máximo 60 palabras, sin hablar mal de ellos, destacando la diferencia>"
}}"""

    system = """Sos el módulo de análisis competitivo de JARVIS para Avanza Digital.
Tu análisis es honesto: reconocés las fortalezas reales del competidor.
Las estrategias de contrapropuesta son específicas y accionables — no genéricas.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=1500, temperature=0.35, json_mode=True)
    if not raw:
        return None

    return _parse_json(raw)


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 4 — DETECCIÓN DE SEÑALES EN MENSAJE DEL PROSPECTO
# ═══════════════════════════════════════════════════════════════════════════════

def detectar_señales(
    mensaje_prospecto: str,
    empresa: str = "",
    etapa_actual: str = "",
    historial_resumido: str = "",
) -> Optional[dict]:
    """
    Analiza un mensaje, email o WhatsApp recibido del prospecto.
    Detecta temperatura real, señales de compra, señales de fuga
    y sugiere la respuesta táctica.

    mensaje_prospecto: el texto del mensaje recibido

    Retorna:
        {
          "temperatura": "frio" | "tibio" | "caliente",
          "delta_temperatura": "subió" | "bajó" | "igual",
          "señales_compra": [str, ...],
          "señales_fuga": [str, ...],
          "poder_decision": "decide" | "consulta" | "influye" | "desconocido",
          "urgencia_real": "alta" | "media" | "baja",
          "analisis": str,
          "respuesta_sugerida": str,       # respuesta táctica lista para enviar
          "proxima_accion": str,
        }
    O None si Claude no está disponible.
    """
    prompt = f"""Analizá este mensaje de un prospecto y detectá las señales que contiene.

MENSAJE DEL PROSPECTO:
---
{mensaje_prospecto}
---

CONTEXTO:
- Empresa del prospecto: {empresa or 'no especificada'}
- Etapa actual del proceso de venta: {etapa_actual or 'no especificada'}
- Historial resumido de la conversación: {historial_resumido or 'no disponible'}

Analizá el mensaje con este JSON exacto:
{{
  "temperatura": "<frio|tibio|caliente — temperatura real del lead después de este mensaje>",
  "delta_temperatura": "<subió|bajó|igual — cómo cambió vs. antes de este mensaje>",
  "señales_compra": [
    "<señal de compra detectada 1 — cita textual o parafraseo del mensaje que indica interés>",
    "<señal de compra 2 si existe, si no omitir>"
  ],
  "señales_fuga": [
    "<señal de fuga 1 — cita textual o parafraseo que indica alejamiento o resistencia>",
    "<señal de fuga 2 si existe, si no omitir>"
  ],
  "poder_decision": "<decide|consulta|influye|desconocido — qué poder de decisión parece tener quien escribe>",
  "urgencia_real": "<alta|media|baja — urgencia real, no declarada>",
  "analisis": "<análisis de 2-3 oraciones: qué revela realmente este mensaje, qué está pensando el prospecto>",
  "respuesta_sugerida": "<respuesta táctica para contestar este mensaje específico — máximo 60 palabras, lista para copiar y pegar>",
  "proxima_accion": "<qué hacer ahora con este prospecto — acción concreta y específica>"
}}"""

    system = """Sos el módulo de análisis de señales de JARVIS para Avanza Digital.
Leés mensajes de prospectos y detectás lo que no está escrito explícitamente.
Sabés distinguir interés genuino de cortesía, urgencia real de urgencia declarada.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=900, temperature=0.3, json_mode=True)
    if not raw:
        return None

    parsed = _parse_json(raw)
    if not parsed:
        return None

    # Normalizar listas vacías
    if "señales_compra" not in parsed:
        parsed["señales_compra"] = []
    if "señales_fuga" not in parsed:
        parsed["señales_fuga"] = []

    return parsed


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 5 — SIGUIENTE PASO ÓPTIMO
# ═══════════════════════════════════════════════════════════════════════════════

def sugerir_siguiente_paso(
    empresa: str,
    etapa: str,
    dias_sin_contacto: int = 0,
    ultima_nota: str = "",
    score_actual: int = 0,
    plan_conversado: str = "",
    *,
    aliado_nombre: str = "",
    aliado_rubros: list[str] | None = None,
) -> Optional[dict]:
    """
    Dado el estado actual de un lead, sugiere la acción más efectiva
    para avanzar el cierre. Usado en el Dashboard y en alertas proactivas.

    Retorna:
        {
          "accion": str,
          "canal": str,
          "urgencia": "inmediata" | "hoy" | "esta_semana",
          "razon": str,
          "mensaje_listo": str,       # listo para copiar y pegar
          "si_no_responde": str,      # plan B si no hay respuesta
        }
    O None si Claude no está disponible.
    """
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "general"

    dias_str = f"{dias_sin_contacto} días sin contacto"
    if dias_sin_contacto == 0:
        dias_str = "primer contacto (no contactado aún)"

    prompt = f"""Determiná cuál es el siguiente paso más efectivo para avanzar con este lead.

ESTADO ACTUAL DEL LEAD:
- Empresa: {empresa}
- Etapa del proceso: {etapa or 'no especificada'}
- {dias_str}
- Última nota del aliado: {ultima_nota or 'ninguna'}
- Score actual: {score_actual}/100
- Plan conversado: {plan_conversado or 'no definido'}

ALIADO: {aliado_nombre or 'el aliado'} — sectores: {rubros_str}

Devolvé este JSON exacto:
{{
  "accion": "<qué hacer exactamente — en 1 oración directa>",
  "canal": "<WhatsApp|Email|LinkedIn|Llamada|Visita>",
  "urgencia": "<inmediata|hoy|esta_semana>",
  "razon": "<por qué este paso es el más efectivo ahora — en 1-2 oraciones con lógica comercial>",
  "mensaje_listo": "<texto listo para copiar y pegar por el canal recomendado — máximo 70 palabras>",
  "si_no_responde": "<plan B: qué hacer si no hay respuesta en 48hs>"
}}"""

    system = """Sos el módulo de inteligencia de siguiente paso de JARVIS para Avanza Digital.
Tu recomendación debe ser específica, accionable y basada en el estado real del lead.
Sabés que en ventas industriales B2B la persistencia correcta es clave — no el spam.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=700, temperature=0.3, json_mode=True)
    if not raw:
        return None

    return _parse_json(raw)


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 6 — PRIORIZACIÓN DE PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def puntuar_pipeline(
    leads: list[dict],
    *,
    aliado_nombre: str = "",
    aliado_rubros: list[str] | None = None,
    max_leads: int = 15,
) -> Optional[list[dict]]:
    """
    Recibe una lista de leads/prospectos del pipeline del aliado y devuelve
    un ranking priorizado con score de urgencia y acción sugerida para cada uno.

    Usado en el Dashboard de Inteligencia para la vista de "qué hacer hoy".

    leads: lista de dicts con campos como:
        {
          "id": int,
          "empresa": str,
          "etapa": str,
          "dias_sin_contacto": int,
          "score_ia": int,            # score anterior si existe
          "plan_interes": str,
          "nota": str,
        }

    Retorna lista de:
        {
          "id": int,
          "empresa": str,
          "prioridad": int,           # 1 = más urgente
          "score_urgencia": int,      # 0-100 qué tan urgente es actuar HOY
          "razon": str,
          "accion_hoy": str,
          "canal": str,
          "riesgo_perdida": "alto" | "medio" | "bajo",
        }
    O None si Claude no está disponible.
    """
    if not leads:
        return []

    # Limitar para no exceder el contexto
    leads_a_analizar = leads[:max_leads]
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "general"

    leads_json = json.dumps(leads_a_analizar, ensure_ascii=False, default=str)

    prompt = f"""Priorizá este pipeline de leads para el aliado de Avanza Digital.
El objetivo es determinar en cuáles ACTUAR HOY y en qué orden.

ALIADO: {aliado_nombre or 'el aliado'} — sectores: {rubros_str}

PIPELINE ACTUAL (JSON):
{leads_json}

Para cada lead, generá una evaluación de urgencia.
El "score_urgencia" mide qué tan urgente es actuar HOY (no el potencial del lead, sino la urgencia de acción).
Devolvé un JSON array con este formato exacto para cada lead:
[
  {{
    "id": <id del lead, tal cual llegó>,
    "empresa": "<nombre de la empresa>",
    "prioridad": <número de orden 1=más urgente, sin repetir números>,
    "score_urgencia": <número 0-100>,
    "razon": "<por qué es urgente actuar (o no) hoy — 1 oración>",
    "accion_hoy": "<qué hacer exactamente hoy con este lead — 1 oración concreta>",
    "canal": "<WhatsApp|Email|LinkedIn|Llamada>",
    "riesgo_perdida": "<alto|medio|bajo>"
  }},
  ...
]

Ordená el array por prioridad (1 primero). Incluí todos los leads recibidos."""

    system = """Sos el módulo de priorización de pipeline de JARVIS para Avanza Digital.
Sabés que en ventas B2B industriales hay una ventana de atención limitada.
Tu priorización refleja la urgencia real de acción, no solo el valor del lead.
Un lead caliente que lleva 5 días sin contacto es más urgente que uno frío reciente.
Respondé ÚNICAMENTE con un JSON array válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=2000, temperature=0.2, json_mode=True)
    if not raw:
        return None

    parsed = _parse_json(raw)
    if not isinstance(parsed, list):
        return None

    return parsed
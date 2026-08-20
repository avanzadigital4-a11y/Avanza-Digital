"""
jarvis_documentos.py — Módulo 8: Inteligencia de Documentos

FUNCIONES:
  analizar_rfp()               → Analiza una solicitud de propuesta/licitación: requisitos, alertas y ángulo de respuesta
  analizar_propuesta_competencia() → Desmenuza una propuesta de un competidor y devuelve cómo ganarle
  analizar_contrato()          → Extrae cláusulas clave, riesgos y puntos a negociar de un contrato
  analizar_comunicado_prensa() → Detecta oportunidades de venta en novedades del mercado
  extraer_datos_contacto()     → Extrae nombre, cargo, email, teléfono de cualquier texto libre o tarjeta
  resumir_documento()          → Resume cualquier documento largo en un briefing ejecutivo accionable
  comparar_propuestas()        → Compara la propuesta de Avanza con la de un competidor para un prospecto dado
  detectar_oportunidad_documento() → Dado cualquier documento (news, reporte, PDF), detecta si hay oportunidad de venta

DISEÑO:
  Mismo patrón que el resto de los módulos JARVIS: si ANTHROPIC_API_KEY no
  está o falla, todas las funciones devuelven None. El producto nunca se cae.
  Timeout: 25 segundos (los documentos pueden ser extensos).

  Integración en main.py:
      import jarvis_documentos
      jarvis_documentos.register(app, get_db, current_aliado_required)
"""

from __future__ import annotations
import os, json, sys
from typing import Optional

# ─── CONFIG ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 25.0   # documentos pueden ser largos

# Límite de caracteres del documento que se manda al prompt
# (~6.000 tokens de contexto para el doc — el resto para razonamiento)
MAX_DOC_CHARS = 12_000


def is_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


# ─── HELPERS INTERNOS ────────────────────────────────────────────────────────

def _chat(
    prompt: str,
    system: str,
    *,
    max_tokens: int = 1400,
    temperature: float = 0.3,
    json_mode: bool = False,
) -> Optional[str]:
    """Llama a Claude. Devuelve el texto o None si algo falla. No lanza excepciones."""
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
        print(f"[JARVIS DOCUMENTOS ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _parse_json(text: str) -> Optional[dict]:
    """Parsea JSON con tolerancia a texto extra."""
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
    print(f"[JARVIS DOCUMENTOS] No se pudo parsear JSON: {text[:200]}", file=sys.stderr)
    return None


def _truncar(texto: str, max_chars: int = MAX_DOC_CHARS) -> str:
    """Trunca el documento con aviso si es demasiado largo."""
    if len(texto) <= max_chars:
        return texto
    mitad = max_chars // 2
    return (
        texto[:mitad]
        + f"\n\n[... DOCUMENTO TRUNCADO — {len(texto) - max_chars} caracteres omitidos ...]\n\n"
        + texto[-mitad:]
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MÓDULO 8 — INTELIGENCIA DE DOCUMENTOS
# ═══════════════════════════════════════════════════════════════════════════════


# ── 1. Análisis de RFP / Licitación ──────────────────────────────────────────

def analizar_rfp(
    texto_rfp: str,
    *,
    empresa_solicitante: str = "",
    aliado_nombre: str = "",
    aliado_rubros: list = None,
    aliado_ventas: int = 0,
) -> Optional[dict]:
    """
    Módulo 8 — Analiza una solicitud de propuesta (RFP), pedido de cotización o
    pliego de licitación. Devuelve lo que el aliado necesita saber antes de decidir
    si presenta o no.

    Retorna:
        {
          "empresa_solicitante": str,
          "tipo_documento": str,
          "requisitos_clave": list[str],
          "fechas_criticas": list[dict],  # [{evento, fecha}]
          "presupuesto_estimado": str,
          "criterios_adjudicacion": list[str],
          "alertas": list[str],           # red flags o requisitos que Avanza no cumple
          "angulo_de_respuesta": str,
          "decision_recomendada": str,    # "presentar" | "no presentar" | "presentar con precaución"
          "justificacion_decision": str,
          "checklist_respuesta": list[str],
        }
    """
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "industrial"
    doc = _truncar(texto_rfp)

    prompt = f"""
El aliado recibió esta solicitud de propuesta / licitación. Analizala completamente.

EMPRESA SOLICITANTE: {empresa_solicitante or "no especificada — inferila del documento"}
ALIADO QUE VA A RESPONDER: {aliado_nombre or "el aliado de Avanza Digital"}
SECTORES DEL ALIADO: {rubros_str}
VENTAS CONFIRMADAS DEL ALIADO: {aliado_ventas}

Avanza Digital ofrece: presencia web B2B, SEO, generación de leads, sistemas
de marketing digital para PYMES industriales. Planes $1.190 - $9.999 ARS/mes.

DOCUMENTO (RFP / LICITACIÓN / PEDIDO DE COTIZACIÓN):
---
{doc}
---

Tu análisis debe ser honesto — si la empresa no puede cumplir un requisito, lo decís.
No sos optimista para que el aliado se meta en algo que no puede ganar.

Devolvé este JSON:
{{
  "empresa_solicitante": "<nombre de la empresa que solicita — del documento>",
  "tipo_documento": "<RFP|licitación pública|pedido de cotización|otro>",
  "requisitos_clave": [
    "<requisito 1 — lo que el solicitante pide explícitamente>",
    "<requisito 2>",
    "<requisito 3>",
    "<requisito 4 si existe>",
    "<requisito 5 si existe>"
  ],
  "fechas_criticas": [
    {{"evento": "<nombre del hito>", "fecha": "<fecha o plazo>"}},
    {{"evento": "<evento 2>", "fecha": "<fecha 2>"}}
  ],
  "presupuesto_estimado": "<rango de presupuesto si se menciona — o 'no especificado'>",
  "criterios_adjudicacion": [
    "<criterio de adjudicación 1 — precio, calidad, experiencia, etc.>",
    "<criterio 2>"
  ],
  "alertas": [
    "<alerta 1 — requisito que puede ser problemático, red flag, o que Avanza no cumple>",
    "<alerta 2 si existe>",
    "<alerta 3 si existe>"
  ],
  "angulo_de_respuesta": "<cómo debería posicionar Avanza su propuesta para ganar este proceso — específico, no genérico>",
  "decision_recomendada": "<presentar|no presentar|presentar con precaución>",
  "justificacion_decision": "<por qué esa decisión — en 2-3 líneas honestas>",
  "checklist_respuesta": [
    "<ítem 1 que el aliado necesita preparar para responder — accionable>",
    "<ítem 2>",
    "<ítem 3>",
    "<ítem 4 si aplica>"
  ]
}}
"""

    system = """Sos el Módulo de Análisis de RFP de JARVIS para Avanza Digital.
Analizás solicitudes de propuesta con ojo crítico — identificás oportunidades reales y alertas honestas.
Si el aliado no debería presentar, se lo decís claramente. No sos un optimista.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1800, temperature=0.25, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 2. Análisis de propuesta de la competencia ────────────────────────────────

def analizar_propuesta_competencia(
    texto_propuesta: str,
    *,
    empresa_competidor: str = "",
    empresa_prospecto: str = "",
    aliado_nombre: str = "",
    aliado_rubros: list = None,
) -> Optional[dict]:
    """
    Módulo 8 — Analiza una propuesta de un competidor para un prospecto específico.
    Devuelve puntos fuertes del competidor, debilidades y cómo ganarle.

    Retorna:
        {
          "competidor": str,
          "propuesta_resumen": str,
          "puntos_fuertes": list[str],
          "debilidades": list[str],
          "precio_o_rango": str,
          "diferenciadores_avanza": list[str],
          "como_ganarle": str,
          "argumentos_especificos": list[str],
          "alertas": list[str],
        }
    """
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "industrial"
    doc = _truncar(texto_propuesta)

    prompt = f"""
El aliado consiguió la propuesta de un competidor para el mismo prospecto. Analizala.

COMPETIDOR: {empresa_competidor or "competidor no identificado — inferí del documento si es posible"}
PROSPECTO EN DISPUTA: {empresa_prospecto or "el prospecto"}
ALIADO DE AVANZA: {aliado_nombre or "el aliado"} — sectores: {rubros_str}

Avanza Digital ofrece: presencia web B2B, SEO, generación de leads, sistemas
de marketing digital para PYMES industriales. Planes $1.190 - $9.999 ARS/mes.

PROPUESTA DEL COMPETIDOR:
---
{doc}
---

Sé honesto: si el competidor tiene una propuesta mejor en algún punto, lo decís.
El objetivo es ganar el deal, no minimizar al competidor — si el competidor tiene
una fortaleza real, el aliado necesita saberlo para contraargumentar con hechos.

Devolvé este JSON:
{{
  "competidor": "<nombre del competidor — del documento o 'no identificado'>",
  "propuesta_resumen": "<qué propone el competidor en 3-4 líneas — sin juzgar>",
  "puntos_fuertes": [
    "<qué hace bien el competidor en esta propuesta — honesto>",
    "<punto fuerte 2>",
    "<punto fuerte 3 si existe>"
  ],
  "debilidades": [
    "<dónde es débil la propuesta del competidor — específico>",
    "<debilidad 2>",
    "<debilidad 3 si existe>"
  ],
  "precio_o_rango": "<precio o rango de precio del competidor — o 'no especificado'>",
  "diferenciadores_avanza": [
    "<en qué punto concreto Avanza Digital es superior para este prospecto>",
    "<diferenciador 2>",
    "<diferenciador 3>"
  ],
  "como_ganarle": "<estrategia concreta para ganar este deal frente a esta propuesta — en 2-3 líneas>",
  "argumentos_especificos": [
    "<argumento 1 que el aliado puede usar en la próxima conversación — basado en las debilidades del competidor>",
    "<argumento 2>",
    "<argumento 3>"
  ],
  "alertas": [
    "<riesgo real de perder este deal y por qué — si existe>",
    "<alerta 2 si existe>"
  ]
}}
"""

    system = """Sos el Módulo de Inteligencia Competitiva de JARVIS para Avanza Digital.
Analizás propuestas de competidores con objetividad — ni las minimizás ni las sobredimensionás.
El aliado necesita saber la verdad para ganar el deal, no para sentirse bien.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1600, temperature=0.25, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 3. Análisis de contrato ───────────────────────────────────────────────────

def analizar_contrato(
    texto_contrato: str,
    *,
    tipo_contrato: str = "",
    aliado_nombre: str = "",
    perspectiva: str = "aliado",   # "aliado" | "avanza" | "ambos"
) -> Optional[dict]:
    """
    Módulo 8 — Extrae cláusulas clave, riesgos y puntos a negociar de un contrato.
    No reemplaza al abogado — señala qué revisar con un profesional.

    Retorna:
        {
          "tipo_contrato": str,
          "partes_identificadas": list[str],
          "clausulas_clave": list[dict],    # [{clausula, texto_resumido, riesgo}]
          "obligaciones_aliado": list[str],
          "obligaciones_otra_parte": list[str],
          "clausulas_riesgo": list[dict],   # [{clausula, descripcion_riesgo, nivel: alto|medio|bajo}]
          "puntos_a_negociar": list[str],
          "clausulas_positivas": list[str],
          "recomendacion": str,
          "advertencia_legal": str,
        }
    """
    doc = _truncar(texto_contrato)

    prompt = f"""
Analizá este contrato y extraé lo más importante para el aliado.

TIPO DE CONTRATO: {tipo_contrato or "no especificado — inferí del documento"}
ALIADO / PARTE QUE ANALIZA: {aliado_nombre or "el aliado de Avanza Digital"}
PERSPECTIVA: {perspectiva}

CONTRATO:
---
{doc}
---

IMPORTANTE: Sos un asistente de análisis, no un abogado. Identificás riesgos y
cláusulas para que el aliado sepa QUÉ preguntarle a un profesional — no para
reemplazar el consejo legal.

Devolvé este JSON:
{{
  "tipo_contrato": "<tipo de contrato identificado>",
  "partes_identificadas": [
    "<parte 1 — nombre y rol>",
    "<parte 2 — nombre y rol>"
  ],
  "clausulas_clave": [
    {{
      "clausula": "<nombre o número de la cláusula>",
      "texto_resumido": "<qué dice en términos simples>",
      "riesgo": "<riesgo que implica — o 'sin riesgo aparente'>"
    }},
    {{
      "clausula": "<cláusula 2>",
      "texto_resumido": "<resumen>",
      "riesgo": "<riesgo>"
    }}
  ],
  "obligaciones_aliado": [
    "<qué está obligado a hacer el aliado según el contrato>",
    "<obligación 2>"
  ],
  "obligaciones_otra_parte": [
    "<qué está obligada a hacer la otra parte>",
    "<obligación 2>"
  ],
  "clausulas_riesgo": [
    {{
      "clausula": "<nombre o ubicación>",
      "descripcion_riesgo": "<por qué esta cláusula es riesgosa>",
      "nivel": "<alto|medio|bajo>"
    }}
  ],
  "puntos_a_negociar": [
    "<punto 1 que el aliado podría intentar modificar en la negociación>",
    "<punto 2>"
  ],
  "clausulas_positivas": [
    "<cláusula que favorece al aliado — por qué es buena>",
    "<cláusula positiva 2 si existe>"
  ],
  "recomendacion": "<qué debería hacer el aliado antes de firmar — en 2-3 líneas>",
  "advertencia_legal": "Este análisis es orientativo y no reemplaza el consejo de un abogado. Consultá con un profesional legal antes de firmar."
}}
"""

    system = """Sos el Módulo de Análisis de Contratos de JARVIS para Avanza Digital.
Extraés las partes importantes de contratos en lenguaje claro — sin jerga legal innecesaria.
Siempre recordás que sos un asistente, no un abogado, y señalás qué necesita revisión profesional.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=2000, temperature=0.2, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 4. Análisis de comunicado de prensa ──────────────────────────────────────

def analizar_comunicado_prensa(
    texto_comunicado: str,
    *,
    empresa_emisora: str = "",
    sector: str = "",
    aliado_nombre: str = "",
    aliado_rubros: list = None,
    aliado_ciudad: str = "",
) -> Optional[dict]:
    """
    Módulo 8 — Detecta oportunidades de venta en comunicados de prensa,
    noticias del sector, anuncios de expansión, nuevas licitaciones, etc.

    Retorna:
        {
          "empresa_emisora": str,
          "tipo_novedad": str,
          "resumen": str,
          "oportunidad_detectada": bool,
          "nivel_oportunidad": str,    # "alta" | "media" | "baja" | "ninguna"
          "razon_oportunidad": str,
          "empresas_afectadas": list[str],   # otras empresas que también pueden ser prospectos
          "mensaje_apertura": str,           # cómo arrancar la conversación con este contexto
          "timing": str,                     # cuándo contactar
          "alertas": list[str],
        }
    """
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "industrial"
    doc = _truncar(texto_comunicado)

    prompt = f"""
Analizá este comunicado de prensa o novedad del mercado y detectá oportunidades de venta.

EMPRESA EMISORA: {empresa_emisora or "identificala del comunicado"}
SECTOR: {sector or "industrial — identificalo del contexto"}
ALIADO: {aliado_nombre or "el aliado de Avanza Digital"} — ciudad: {aliado_ciudad or "Argentina"}
SECTORES DEL ALIADO: {rubros_str}

Avanza Digital ofrece: presencia web B2B, SEO, generación de leads, sistemas
de marketing digital para PYMES industriales. Planes $1.190 - $9.999 ARS/mes.

COMUNICADO / NOTICIA:
---
{doc}
---

Buscá señales de oportunidad: expansión de la empresa, nueva planta, nueva línea
de negocio, cambio de dirección, nueva licitación, certificación obtenida, entrada
a un nuevo mercado, cambio de management, etc.

Si hay oportunidad, explicá por qué y cómo aprovecharla.
Si no hay oportunidad, también decilo — no fuerces oportunidades que no existen.

Devolvé este JSON:
{{
  "empresa_emisora": "<nombre de la empresa que emitió el comunicado>",
  "tipo_novedad": "<expansión|nueva planta|cambio de management|licitación|certificación|otro>",
  "resumen": "<qué pasó según el comunicado — en 2-3 líneas neutrales>",
  "oportunidad_detectada": <true|false>,
  "nivel_oportunidad": "<alta|media|baja|ninguna>",
  "razon_oportunidad": "<por qué hay oportunidad — o por qué no la hay — específico>",
  "empresas_afectadas": [
    "<empresa o tipo de empresa que también puede beneficiarse de esta noticia — posibles prospectos derivados>",
    "<empresa 2 si existe>"
  ],
  "mensaje_apertura": "<cómo abrir la conversación con esta empresa usando esta novedad como contexto — 2-3 líneas concretas>",
  "timing": "<cuándo es el mejor momento para contactar basado en la noticia — inmediato / en X semanas / etc.>",
  "alertas": [
    "<algo que podría complicar el acercamiento — cambio de prioridades, problemas del sector, etc.>",
    "<alerta 2 si existe>"
  ]
}}
"""

    system = """Sos el Módulo de Inteligencia de Mercado de JARVIS para Avanza Digital.
Analizás noticias y comunicados del sector industrial buscando oportunidades de venta concretas.
No forzás oportunidades donde no las hay — la precisión vale más que el optimismo.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1400, temperature=0.3, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 5. Extracción de datos de contacto ───────────────────────────────────────

def extraer_datos_contacto(
    texto_libre: str,
    *,
    contexto: str = "",
) -> Optional[dict]:
    """
    Módulo 8 — Extrae datos de contacto de cualquier texto:
    tarjeta de visita fotografiada, firma de email, perfil de LinkedIn copiado,
    texto libre de una conversación, etc.

    Retorna:
        {
          "nombre": str,
          "cargo": str,
          "empresa": str,
          "email": str,
          "telefono": str,
          "linkedin": str,
          "ciudad": str,
          "pais": str,
          "sector_inferido": str,
          "confianza": str,          # "alta" | "media" | "baja" — qué tan seguro está JARVIS
          "datos_incompletos": list[str],   # campos que no se pudieron extraer
          "nota": str,
        }
    """
    doc = _truncar(texto_libre, max_chars=3000)

    prompt = f"""
Extraé todos los datos de contacto de este texto.

CONTEXTO (cómo se obtuvo el texto): {contexto or "no especificado"}

TEXTO:
---
{doc}
---

Extraé todos los datos que puedas. Si un campo no está presente, dejalo como
cadena vacía — no inventes datos.
Si hay ambigüedad (ej. dos nombres posibles), elegí el más probable y bajá la confianza.

Devolvé este JSON:
{{
  "nombre": "<nombre completo — o vacío si no está>",
  "cargo": "<cargo o título — o vacío>",
  "empresa": "<nombre de la empresa — o vacío>",
  "email": "<dirección de email — o vacío>",
  "telefono": "<número de teléfono con código de área — o vacío>",
  "linkedin": "<URL o usuario de LinkedIn — o vacío>",
  "ciudad": "<ciudad — o vacío>",
  "pais": "<país — o vacío>",
  "sector_inferido": "<sector del negocio inferido del cargo y empresa — o 'no determinado'>",
  "confianza": "<alta|media|baja — qué tan seguros estamos de los datos extraídos>",
  "datos_incompletos": [
    "<campo que no se pudo extraer con certeza>"
  ],
  "nota": "<algo relevante del texto que no entra en los campos anteriores — o vacío>"
}}
"""

    system = """Sos el Módulo de Extracción de Datos de JARVIS para Avanza Digital.
Extraés datos de contacto de cualquier texto con máxima precisión.
Si no está, no lo inventás — preferís campo vacío a dato incorrecto.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=700, temperature=0.1, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 6. Resumen ejecutivo de documento ────────────────────────────────────────

def resumir_documento(
    texto_documento: str,
    *,
    tipo_documento: str = "",
    objetivo_lectura: str = "",
    aliado_nombre: str = "",
    longitud_resumen: str = "medio",   # "corto" (3 líneas) | "medio" (media página) | "completo"
) -> Optional[dict]:
    """
    Módulo 8 — Resume cualquier documento largo en un briefing ejecutivo accionable.
    El aliado no tiene tiempo de leer — necesita los puntos que importan para su trabajo.

    Retorna:
        {
          "tipo_documento": str,
          "resumen_ejecutivo": str,
          "puntos_clave": list[str],
          "datos_relevantes": list[str],   # números, fechas, nombres importantes
          "lo_que_cambia": str,            # qué implica esto para el aliado
          "acciones_sugeridas": list[str],
        }
    """
    doc = _truncar(texto_documento)

    longitud_instr = {
        "corto": "3-5 líneas — solo lo esencial",
        "medio": "8-12 líneas — cobertura completa de lo importante",
        "completo": "15-20 líneas — cobertura exhaustiva",
    }.get(longitud_resumen, "8-12 líneas")

    prompt = f"""
Resumí este documento para un aliado comercial que no tiene tiempo de leerlo completo.

TIPO DE DOCUMENTO: {tipo_documento or "identificalo del contenido"}
OBJETIVO DE LECTURA: {objetivo_lectura or "entender qué implica este documento para su negocio de ventas"}
ALIADO: {aliado_nombre or "el aliado de Avanza Digital"}
LONGITUD DEL RESUMEN: {longitud_instr}

DOCUMENTO:
---
{doc}
---

Enfocate en lo que es relevante para un vendedor B2B en el mercado industrial argentino.
Ignorá los formalismos y el relleno — extraé los hechos que importan.

Devolvé este JSON:
{{
  "tipo_documento": "<tipo identificado>",
  "resumen_ejecutivo": "<resumen en la longitud pedida — accionable, no descriptivo>",
  "puntos_clave": [
    "<punto 1 — el más importante del documento>",
    "<punto 2>",
    "<punto 3>",
    "<punto 4 si existe>",
    "<punto 5 si existe>"
  ],
  "datos_relevantes": [
    "<dato numérico, fecha, nombre propio o hecho concreto relevante>",
    "<dato 2>",
    "<dato 3 si existe>"
  ],
  "lo_que_cambia": "<qué implica este documento para el aliado — en 1-2 líneas directas>",
  "acciones_sugeridas": [
    "<qué debería hacer el aliado después de leer esto>",
    "<acción 2 si aplica>"
  ]
}}
"""

    system = """Sos el Módulo de Resumen de Documentos de JARVIS para Avanza Digital.
Resumís documentos complejos en briefings que se leen en 30 segundos y son completamente accionables.
Filtrás el relleno y dejás solo lo que importa para un vendedor B2B industrial.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1400, temperature=0.3, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 7. Comparación de propuestas ─────────────────────────────────────────────

def comparar_propuestas(
    propuesta_avanza: str,
    propuesta_competidor: str,
    *,
    empresa_prospecto: str = "",
    sector: str = "",
    criterio_prospecto: str = "",   # qué valora más el prospecto
    aliado_nombre: str = "",
) -> Optional[dict]:
    """
    Módulo 8 — Compara la propuesta de Avanza con la de un competidor
    para un prospecto específico. Devuelve cómo presentar la diferencia.

    Retorna:
        {
          "tabla_comparacion": list[dict],  # [{aspecto, avanza, competidor, ganador}]
          "fortalezas_avanza": list[str],
          "debilidades_avanza": list[str],
          "veredicto": str,               # quién ganaría si el prospecto decide hoy
          "argumento_diferencial": str,   # el argumento principal para usar en la conversación
          "como_presentarla": str,        # cómo presentar la comparación sin destruir al competidor
          "precio_si_aplica": str,
        }
    """
    doc_avanza = _truncar(propuesta_avanza, max_chars=5000)
    doc_comp   = _truncar(propuesta_competidor, max_chars=5000)

    prompt = f"""
Comparás dos propuestas para el mismo prospecto. Devolvé un análisis honesto.

PROSPECTO: {empresa_prospecto or "la empresa en disputa"}
SECTOR: {sector or "industrial"}
QUÉ VALORA EL PROSPECTO: {criterio_prospecto or "no especificado — inferí del contexto"}
ALIADO: {aliado_nombre or "el aliado de Avanza Digital"}

PROPUESTA DE AVANZA DIGITAL:
---
{doc_avanza}
---

PROPUESTA DEL COMPETIDOR:
---
{doc_comp}
---

Sé objetivamente honesto. Si el competidor tiene una propuesta mejor en algún aspecto,
lo decís — el aliado necesita saberlo para mejorar o para contraargumentar con hechos.

Devolvé este JSON:
{{
  "tabla_comparacion": [
    {{
      "aspecto": "<dimensión de comparación — precio, alcance, soporte, experiencia, etc.>",
      "avanza": "<cómo está Avanza en este aspecto>",
      "competidor": "<cómo está el competidor>",
      "ganador": "<avanza|competidor|empate>"
    }},
    {{
      "aspecto": "<aspecto 2>",
      "avanza": "<...>",
      "competidor": "<...>",
      "ganador": "<...>"
    }},
    {{
      "aspecto": "<aspecto 3>",
      "avanza": "<...>",
      "competidor": "<...>",
      "ganador": "<...>"
    }}
  ],
  "fortalezas_avanza": [
    "<en qué supera Avanza al competidor para este prospecto — específico>",
    "<fortaleza 2>",
    "<fortaleza 3 si existe>"
  ],
  "debilidades_avanza": [
    "<en qué está por debajo — honesto>",
    "<debilidad 2 si existe>"
  ],
  "veredicto": "<quién ganaría si el prospecto decide hoy basado en las propuestas — y por qué>",
  "argumento_diferencial": "<el argumento principal que el aliado debería usar en la próxima conversación — el que más impacta para este prospecto>",
  "como_presentarla": "<cómo mostrar las diferencias sin destruir explícitamente al competidor — táctica de presentación>",
  "precio_si_aplica": "<comparación de precios si están especificados en ambas propuestas — o 'no disponible'>"
}}
"""

    system = """Sos el Módulo de Comparación de Propuestas de JARVIS para Avanza Digital.
Comparás propuestas con objetividad y sin sesgo — el aliado necesita la verdad para ganar deals.
La honestidad sobre las debilidades de Avanza es lo que permite mejorarlas.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1800, temperature=0.25, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 8. Detección de oportunidad en documento genérico ────────────────────────

def detectar_oportunidad_documento(
    texto_documento: str,
    *,
    tipo_fuente: str = "",     # "noticia", "reporte", "email", "pdf", "otro"
    aliado_nombre: str = "",
    aliado_rubros: list = None,
    aliado_ciudad: str = "",
) -> Optional[dict]:
    """
    Módulo 8 — Detector de oportunidades de venta en cualquier documento.
    El aliado pega cualquier cosa — JARVIS evalúa si hay una oportunidad y cómo aprovecharla.

    Retorna:
        {
          "tipo_documento_detectado": str,
          "resumen_en_una_linea": str,
          "hay_oportunidad": bool,
          "nivel": str,                # "alta" | "media" | "baja" | "ninguna"
          "empresa_objetivo": str,     # empresa a la que habría que contactar
          "razon": str,
          "como_entrar": str,          # ángulo de apertura específico
          "timing_recomendado": str,
          "otros_prospectos_derivados": list[str],
        }
    """
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "industrial"
    doc = _truncar(texto_documento)

    prompt = f"""
El aliado encontró este documento / texto. Analizá si hay una oportunidad de venta.

TIPO DE FUENTE: {tipo_fuente or "no especificado"}
ALIADO: {aliado_nombre or "el aliado de Avanza Digital"} — ciudad: {aliado_ciudad or "Argentina"}
SECTORES DEL ALIADO: {rubros_str}

Avanza Digital ofrece: presencia web B2B, SEO, generación de leads, sistemas
de marketing digital para PYMES industriales. Planes $1.190 - $9.999 ARS/mes.

DOCUMENTO:
---
{doc}
---

Evaluá si este documento revela una oportunidad para Avanza Digital.
Señales de oportunidad: empresa en expansión, empresa sin presencia digital visible,
nueva gerencia comercial, empresa certificándose para exportar, empresa licitando,
empresa anunciando nuevos productos/mercados, empresa que perdió un proveedor digital, etc.

Si no hay oportunidad real, decilo — no fuerces el análisis.

Devolvé este JSON:
{{
  "tipo_documento_detectado": "<qué tipo de documento es esto>",
  "resumen_en_una_linea": "<de qué trata el documento en una sola línea>",
  "hay_oportunidad": <true|false>,
  "nivel": "<alta|media|baja|ninguna>",
  "empresa_objetivo": "<empresa a la que habría que contactar — la que más se beneficiaría de Avanza>",
  "razon": "<por qué hay oportunidad — o por qué no — en 2-3 líneas>",
  "como_entrar": "<ángulo concreto de apertura: qué decir al primer contacto usando este documento como contexto>",
  "timing_recomendado": "<cuándo es el mejor momento para contactar — inmediato | en X días | etc.>",
  "otros_prospectos_derivados": [
    "<otra empresa o tipo de empresa que puede ser prospecto a partir de esta información>",
    "<prospecto derivado 2 si existe>"
  ]
}}
"""

    system = """Sos el Módulo de Detección de Oportunidades de JARVIS para Avanza Digital.
Evaluás cualquier documento buscando señales de oportunidad comercial.
No inventás oportunidades — si no hay, lo decís claramente.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1200, temperature=0.3, json_mode=True)
    return _parse_json(raw) if raw else None


# ═══════════════════════════════════════════════════════════════════════════════
# RUTAS FASTAPI — register()
# ═══════════════════════════════════════════════════════════════════════════════

def register(app, get_db_func, auth_dep):
    """
    Registra todos los endpoints de Inteligencia de Documentos en la app FastAPI.

    Llamar desde main.py:
        import jarvis_documentos
        jarvis_documentos.register(app, get_db, current_aliado_required)
    """
    import json as _json
    from fastapi import Depends, HTTPException
    from sqlalchemy.orm import Session
    from pydantic import BaseModel

    # ── Helper de contexto del aliado ─────────────────────────────────────────

    def _ctx(aliado_obj) -> dict:
        rubros = []
        try:
            rubros_raw = getattr(aliado_obj, "rubros_especialidad", "[]") or "[]"
            rubros = _json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
        except Exception:
            pass
        ventas_list = getattr(aliado_obj, "ventas", []) or []
        ventas_confirmadas = sum(1 for v in ventas_list if getattr(v, "confirmada", False))
        return {
            "aliado_nombre":  getattr(aliado_obj, "nombre", "") or "",
            "aliado_ciudad":  getattr(aliado_obj, "ciudad", "") or "",
            "aliado_rubros":  rubros,
            "aliado_ventas":  ventas_confirmadas,
        }

    # ── Schemas ──────────────────────────────────────────────────────────────

    class AnalizarRfpReq(BaseModel):
        texto_rfp: str
        empresa_solicitante: str = ""

    class AnalizarProyCompReq(BaseModel):
        texto_propuesta: str
        empresa_competidor: str = ""
        empresa_prospecto: str = ""

    class AnalizarContratoReq(BaseModel):
        texto_contrato: str
        tipo_contrato: str = ""
        perspectiva: str = "aliado"

    class AnalizarComunicadoReq(BaseModel):
        texto_comunicado: str
        empresa_emisora: str = ""
        sector: str = ""

    class ExtraerContactoReq(BaseModel):
        texto_libre: str
        contexto: str = ""

    class ResumirDocumentoReq(BaseModel):
        texto_documento: str
        tipo_documento: str = ""
        objetivo_lectura: str = ""
        longitud_resumen: str = "medio"   # corto | medio | completo

    class CompararPropuestasReq(BaseModel):
        propuesta_avanza: str
        propuesta_competidor: str
        empresa_prospecto: str = ""
        sector: str = ""
        criterio_prospecto: str = ""

    class DetectarOportunidadReq(BaseModel):
        texto_documento: str
        tipo_fuente: str = ""

    # ── Endpoints ─────────────────────────────────────────────────────────────

    @app.post("/jarvis/documentos/analizar-rfp")
    def ep_analizar_rfp(
        body: AnalizarRfpReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Analiza una solicitud de propuesta o licitación.
        Devuelve requisitos, alertas, ángulo de respuesta y recomendación de presentar o no.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = analizar_rfp(
            texto_rfp=body.texto_rfp,
            empresa_solicitante=body.empresa_solicitante,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_rubros=ctx["aliado_rubros"],
            aliado_ventas=ctx["aliado_ventas"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar el RFP")
        return {"ok": True, "analisis": resultado}

    @app.post("/jarvis/documentos/analizar-propuesta-competencia")
    def ep_analizar_propuesta_competencia(
        body: AnalizarProyCompReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Analiza una propuesta de un competidor: puntos fuertes, debilidades
        y cómo ganarle en el deal.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = analizar_propuesta_competencia(
            texto_propuesta=body.texto_propuesta,
            empresa_competidor=body.empresa_competidor,
            empresa_prospecto=body.empresa_prospecto,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_rubros=ctx["aliado_rubros"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar la propuesta")
        return {"ok": True, "analisis": resultado}

    @app.post("/jarvis/documentos/analizar-contrato")
    def ep_analizar_contrato(
        body: AnalizarContratoReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Extrae cláusulas clave, riesgos y puntos a negociar de un contrato.
        No reemplaza al abogado — señala qué revisar.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = analizar_contrato(
            texto_contrato=body.texto_contrato,
            tipo_contrato=body.tipo_contrato,
            aliado_nombre=ctx["aliado_nombre"],
            perspectiva=body.perspectiva,
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar el contrato")
        return {"ok": True, "analisis": resultado}

    @app.post("/jarvis/documentos/analizar-comunicado")
    def ep_analizar_comunicado(
        body: AnalizarComunicadoReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Detecta oportunidades de venta en comunicados de prensa o noticias del sector.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = analizar_comunicado_prensa(
            texto_comunicado=body.texto_comunicado,
            empresa_emisora=body.empresa_emisora,
            sector=body.sector,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_rubros=ctx["aliado_rubros"],
            aliado_ciudad=ctx["aliado_ciudad"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar el comunicado")
        return {"ok": True, "analisis": resultado}

    @app.post("/jarvis/documentos/extraer-contacto")
    def ep_extraer_contacto(
        body: ExtraerContactoReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Extrae datos de contacto de cualquier texto libre:
        tarjeta de visita, firma de email, perfil de LinkedIn, etc.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        resultado = extraer_datos_contacto(
            texto_libre=body.texto_libre,
            contexto=body.contexto,
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo extraer los datos")
        return {"ok": True, "contacto": resultado}

    @app.post("/jarvis/documentos/resumir")
    def ep_resumir_documento(
        body: ResumirDocumentoReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Resume cualquier documento largo en un briefing ejecutivo accionable.
        Soporta longitud: corto | medio | completo.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = resumir_documento(
            texto_documento=body.texto_documento,
            tipo_documento=body.tipo_documento,
            objetivo_lectura=body.objetivo_lectura,
            aliado_nombre=ctx["aliado_nombre"],
            longitud_resumen=body.longitud_resumen,
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo resumir el documento")
        return {"ok": True, "resumen": resultado}

    @app.post("/jarvis/documentos/comparar-propuestas")
    def ep_comparar_propuestas(
        body: CompararPropuestasReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Compara la propuesta de Avanza con la de un competidor para un prospecto dado.
        Devuelve tabla de comparación, veredicto y argumento diferencial.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = comparar_propuestas(
            propuesta_avanza=body.propuesta_avanza,
            propuesta_competidor=body.propuesta_competidor,
            empresa_prospecto=body.empresa_prospecto,
            sector=body.sector,
            criterio_prospecto=body.criterio_prospecto,
            aliado_nombre=ctx["aliado_nombre"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo comparar las propuestas")
        return {"ok": True, "comparacion": resultado}

    @app.post("/jarvis/documentos/detectar-oportunidad")
    def ep_detectar_oportunidad(
        body: DetectarOportunidadReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Detector de oportunidades en cualquier documento.
        El aliado pega lo que encontró — JARVIS evalúa si hay una oportunidad y cómo entrar.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = detectar_oportunidad_documento(
            texto_documento=body.texto_documento,
            tipo_fuente=body.tipo_fuente,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_rubros=ctx["aliado_rubros"],
            aliado_ciudad=ctx["aliado_ciudad"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar el documento")
        return {"ok": True, "oportunidad": resultado}
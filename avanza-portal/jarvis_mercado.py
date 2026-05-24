"""
jarvis_mercado.py — Módulo 5: Analista de Mercado + Módulo 8: Inteligencia de Documentos

MÓDULO 5 — ANALISTA DE MERCADO
  analizar_competidor()     → Battle card: fortalezas, debilidades, cómo posicionarse
  investigar_empresa()      → Ficha completa de un prospecto antes de una reunión
  radar_sectorial()         → Resumen de tendencias e insights para el sector del aliado
  mapear_oportunidades()    → Dónde hay más oportunidad en una zona/sector dado

MÓDULO 8 — INTELIGENCIA DE DOCUMENTOS
  analizar_rfp()            → Analiza una RFP/solicitud de cotización recibida
  analizar_propuesta_competencia() → Battle card desde la propuesta de un competidor
  analizar_comunicado()     → Detecta oportunidades comerciales en comunicados/noticias

DISEÑO:
  Mismo patrón que jarvis.py: si ANTHROPIC_API_KEY no está o falla, todas las
  funciones devuelven None y el llamador usa su fallback.
  Integración en main.py:
      import jarvis_mercado
      jarvis_mercado.register(app, get_db, current_aliado_required)
"""

from __future__ import annotations
import os, json, sys
from typing import Optional, Any

# ─── CONFIG ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 20.0   # documentos y búsquedas necesitan más tiempo


def is_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


# ─── HELPERS INTERNOS ────────────────────────────────────────────────────────

def _chat(
    prompt: str,
    system: str,
    *,
    max_tokens: int = 1200,
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
        print(f"[JARVIS MERCADO ERROR] {type(e).__name__}: {e}", file=sys.stderr)
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
    print(f"[JARVIS MERCADO] No se pudo parsear JSON: {text[:200]}", file=sys.stderr)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# MÓDULO 5 — ANALISTA DE MERCADO
# ═══════════════════════════════════════════════════════════════════════════════

def analizar_competidor(
    nombre_competidor: str,
    sector: str,
    *,
    info_adicional: str = "",
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
) -> Optional[dict]:
    """
    Módulo 5 — Análisis de competidor.
    Genera un battle card: fortalezas, debilidades y estrategia de posicionamiento.

    Retorna:
        {
          "resumen": str,
          "fortalezas": list[str],
          "debilidades": list[str],
          "como_posicionarse": list[str],
          "argumentos_diferenciadores": list[str],
          "riesgos": list[str],
          "mensaje_si_lo_mencionan": str,
        }
    """
    prompt = f"""
Analizá al competidor "{nombre_competidor}" que opera en el sector {sector}.

CONTEXTO DEL ALIADO:
- Nombre: {aliado_nombre or "el aliado de Avanza Digital"}
- Ciudad: {aliado_ciudad or "Argentina"}
- Sector principal: {sector}
- Información adicional conocida del competidor: {info_adicional or "ninguna"}

Avanza Digital es una agencia que vende presencia web, posicionamiento SEO,
generación de leads digitales y sistemas de marketing B2B industrial a PYMES.
Los planes van desde $1.050 hasta $7.500 ARS/mes. Diferencial clave: conocimiento
sectorial industrial, soporte personalizado, resultados medibles en 90 días.

Generá un battle card completo. Sé honesto: si el competidor tiene ventajas reales,
indicálas. El aliado necesita saber la verdad para prepararse bien.

Devolvé este JSON:
{{
  "resumen": "<2 líneas sobre quién es el competidor y en qué compite directamente>",
  "fortalezas": ["<fortaleza 1>", "<fortaleza 2>", "<fortaleza 3>"],
  "debilidades": ["<debilidad 1>", "<debilidad 2>", "<debilidad 3>"],
  "como_posicionarse": ["<táctica 1>", "<táctica 2>", "<táctica 3>"],
  "argumentos_diferenciadores": [
    "<argumento concreto que Avanza gana frente a este competidor>",
    "<argumento 2>",
    "<argumento 3>"
  ],
  "riesgos": ["<situación donde este competidor nos puede ganar>", "<riesgo 2>"],
  "mensaje_si_lo_mencionan": "<qué decir exactamente si el prospecto menciona a este competidor en la reunión, en español rioplatense, máximo 40 palabras>"
}}
"""

    system = """Sos el Módulo de Inteligencia Competitiva de JARVIS para Avanza Digital.
Analizás competidores del mercado de agencias y servicios digitales B2B industrial en LATAM.
Sos objetivo: reconocés las fortalezas del competidor pero encontrás siempre el ángulo de diferenciación.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1200, temperature=0.3, json_mode=True)
    return _parse_json(raw) if raw else None


def investigar_empresa(
    nombre_empresa: str,
    sector: str,
    *,
    ciudad: str = "",
    nombre_contacto: str = "",
    cargo_contacto: str = "",
    notas_previas: str = "",
) -> Optional[dict]:
    """
    Módulo 5 — Investigación de empresa prospecto.
    Genera una ficha completa para preparar una reunión.

    Retorna:
        {
          "perfil_empresa": str,
          "dolores_probables": list[str],
          "perfil_decisor": str,
          "preguntas_estrategicas": list[str],
          "cosas_no_decir": list[str],
          "angulo_de_entrada": str,
          "plan_recomendado": str,
          "nivel_de_confianza": str,
        }
    """
    prompt = f"""
Necesito preparar una reunión con esta empresa. Dame una ficha de inteligencia comercial.

EMPRESA: {nombre_empresa}
SECTOR: {sector}
CIUDAD: {ciudad or "Argentina"}
CONTACTO: {nombre_contacto or "no identificado"} — {cargo_contacto or "cargo desconocido"}
NOTAS PREVIAS: {notas_previas or "primera reunión, sin contacto previo"}

Avanza Digital vende: presencia web B2B, SEO industrial, generación de leads digitales,
sistemas de marketing para PYMES industriales. Tickets desde $1.050 hasta $7.500 ARS/mes.

Basate en el perfil típico de empresas de ese sector y tamaño para inferir dolores,
motivaciones y objeciones probables. Sé específico al sector.

Devolvé este JSON:
{{
  "perfil_empresa": "<descripción de 2-3 líneas del tipo de empresa, sus procesos y sus prioridades típicas>",
  "dolores_probables": [
    "<dolor 1 — específico para el sector>",
    "<dolor 2>",
    "<dolor 3>"
  ],
  "perfil_decisor": "<cómo piensa y qué le importa a alguien con ese cargo en ese tipo de empresa>",
  "preguntas_estrategicas": [
    "<pregunta 1 que abre la conversación y revela el dolor>",
    "<pregunta 2>",
    "<pregunta 3>",
    "<pregunta 4>",
    "<pregunta 5>"
  ],
  "cosas_no_decir": [
    "<frase o argumento que normalmente cierra la conversación con este perfil>",
    "<cosa a evitar 2>",
    "<cosa a evitar 3>"
  ],
  "angulo_de_entrada": "<cómo abrir la reunión — una frase o enfoque inicial que conecte con el dolor específico de esta empresa>",
  "plan_recomendado": "<qué plan de Avanza encaja mejor con este perfil y por qué — Plan Base / Pro / Industrial>",
  "nivel_de_confianza": "<alto|medio|bajo — qué tan confiable es esta ficha dada la info disponible, y por qué>"
}}
"""

    system = """Sos el Módulo de Investigación de Empresas de JARVIS para Avanza Digital.
Generás fichas de inteligencia comercial pre-reunión para aliados que venden servicios digitales B2B.
Conocés el comportamiento de decisores industriales en Argentina y LATAM.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1500, temperature=0.35, json_mode=True)
    return _parse_json(raw) if raw else None


def radar_sectorial(
    sector: str,
    *,
    region: str = "Argentina",
    aliado_rubros: list = None,
) -> Optional[dict]:
    """
    Módulo 5 — Radar sectorial semanal.
    Genera un resumen de tendencias, oportunidades y alertas del sector.

    Retorna:
        {
          "resumen_mercado": str,
          "tendencias": list[str],
          "oportunidades": list[str],
          "alertas": list[str],
          "insight_accionable": str,
          "momento_para_vender": str,
        }
    """
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else sector

    prompt = f"""
Generá un radar de inteligencia de mercado para el sector: {sector}
Región: {region}
Rubros relacionados del aliado: {rubros_str}

Avanza Digital ofrece servicios de marketing digital y presencia web a PYMES industriales.

Basate en tu conocimiento del sector industrial latinoamericano para generar
insights accionables sobre el estado actual del mercado. Identificá tendencias
macroeconómicas, de digitalización y de comportamiento de compra que apliquen.

Devolvé este JSON:
{{
  "resumen_mercado": "<2-3 líneas sobre el estado general del sector ahora mismo>",
  "tendencias": [
    "<tendencia 1 que crea oportunidad de venta para servicios digitales>",
    "<tendencia 2>",
    "<tendencia 3>"
  ],
  "oportunidades": [
    "<oportunidad concreta de prospección o cierre basada en el contexto del sector>",
    "<oportunidad 2>",
    "<oportunidad 3>"
  ],
  "alertas": [
    "<situación de mercado que podría dificultar las ventas o generar objeciones>",
    "<alerta 2>"
  ],
  "insight_accionable": "<1 acción concreta que el aliado debería hacer esta semana basada en este radar>",
  "momento_para_vender": "<alto|medio|bajo — evaluación del momento de mercado + justificación en 1 línea>"
}}
"""

    system = """Sos el Módulo de Análisis de Mercado de JARVIS para Avanza Digital.
Generás inteligencia de mercado para que aliados comerciales vendan servicios digitales a PYMES industriales en LATAM.
Tu análisis mezcla conocimiento del sector industrial, tendencias de digitalización B2B y comportamiento de compra regional.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.4, json_mode=True)
    return _parse_json(raw) if raw else None


def mapear_oportunidades(
    sector: str,
    region: str,
    *,
    tipo_empresa: str = "PYME industrial",
    historial_cierres: str = "",
) -> Optional[dict]:
    """
    Módulo 5 — Mapa de oportunidades geográficas/sectoriales.
    Responde dónde y con qué tipo de empresa hay más oportunidad ahora.

    Retorna:
        {
          "zonas_prioritarias": list[str],
          "perfiles_ideales": list[str],
          "estrategia_de_entrada": str,
          "script_apertura": str,
          "advertencias": list[str],
        }
    """
    prompt = f"""
El aliado quiere saber dónde hay más oportunidades de venta.

SECTOR OBJETIVO: {sector}
REGIÓN: {region}
TIPO DE EMPRESA OBJETIVO: {tipo_empresa}
HISTORIAL DE CIERRES DEL ALIADO: {historial_cierres or "sin datos específicos"}

Avanza Digital vende presencia web, SEO y generación de leads para PYMES industriales.

Basate en la estructura económica y el tejido industrial de la región para
identificar dónde hay más concentración de empresas del sector y mayor probabilidad
de que necesiten y puedan pagar servicios de marketing digital.

Devolvé este JSON:
{{
  "zonas_prioritarias": [
    "<zona 1 con justificación breve>",
    "<zona 2>",
    "<zona 3>"
  ],
  "perfiles_ideales": [
    "<perfil de empresa 1 que más probabilidad tiene de cerrar en esta combinación sector/región>",
    "<perfil 2>",
    "<perfil 3>"
  ],
  "estrategia_de_entrada": "<cómo prospeccionar en esta zona/sector — referidos, eventos, LinkedIn, base de datos>",
  "script_apertura": "<mensaje de primer contacto adaptado a este sector y región, máximo 60 palabras, español rioplatense>",
  "advertencias": [
    "<algo a tener en cuenta sobre esta zona/sector antes de prospectar>",
    "<advertencia 2>"
  ]
}}
"""

    system = """Sos el Módulo de Mapeo de Oportunidades de JARVIS para Avanza Digital.
Conocés el tejido industrial de Argentina y LATAM: dónde están las PYMES, qué sectores dominan en cada zona, y qué momento de digitalización vive cada segmento.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.35, json_mode=True)
    return _parse_json(raw) if raw else None


# ═══════════════════════════════════════════════════════════════════════════════
# MÓDULO 8 — INTELIGENCIA DE DOCUMENTOS
# ═══════════════════════════════════════════════════════════════════════════════

def analizar_rfp(
    contenido_rfp: str,
    *,
    empresa_solicitante: str = "",
    aliado_nombre: str = "",
    sector: str = "",
) -> Optional[dict]:
    """
    Módulo 8 — Análisis de solicitud de cotización / RFP.
    El aliado pega el texto del pedido y JARVIS lo analiza.

    Retorna:
        {
          "que_necesitan_exactamente": str,
          "plazo_detectado": str,
          "criterios_de_decision": list[str],
          "red_flags": list[str],
          "que_incluir_en_propuesta": list[str],
          "que_evitar": list[str],
          "plan_recomendado": str,
          "probabilidad_estimada": str,
          "valor_estrategico": str,
        }
    """
    prompt = f"""
Analizá esta solicitud de cotización / RFP recibida por un aliado de Avanza Digital.

EMPRESA SOLICITANTE: {empresa_solicitante or "no especificada"}
SECTOR: {sector or "industrial"}
ALIADO QUE RECIBIÓ LA SOLICITUD: {aliado_nombre or "aliado Avanza"}

CONTENIDO DE LA SOLICITUD:
---
{contenido_rfp}
---

Avanza Digital ofrece: presencia web B2B, SEO, generación de leads, sistemas de
marketing digital para PYMES industriales. Planes desde $1.050 a $7.500 ARS/mes.

Devolvé este JSON:
{{
  "que_necesitan_exactamente": "<en 2-3 líneas, qué está pidiendo realmente esta empresa — más allá de las palabras>",
  "plazo_detectado": "<fechas o plazos mencionados o implícitos en la solicitud>",
  "criterios_de_decision": [
    "<criterio 1 por el que van a elegir al proveedor>",
    "<criterio 2>",
    "<criterio 3>"
  ],
  "red_flags": [
    "<señal de alerta en la solicitud — precio como único criterio, plazos imposibles, pedidos irracionales>",
    "<red flag 2 si existe>"
  ],
  "que_incluir_en_propuesta": [
    "<elemento que DEBE estar en la propuesta para ganar esta licitación>",
    "<elemento 2>",
    "<elemento 3>"
  ],
  "que_evitar": [
    "<argumento o sección que debería evitarse en esta propuesta específica>",
    "<cosa a evitar 2>"
  ],
  "plan_recomendado": "<qué plan de Avanza encaja mejor con lo que piden y por qué>",
  "probabilidad_estimada": "<alta|media|baja — evaluación de probabilidad de ganar + justificación>",
  "valor_estrategico": "<alto|medio|bajo — vale la pena el esfuerzo de armar la propuesta + por qué>"
}}
"""

    system = """Sos el Módulo de Análisis de Documentos de JARVIS para Avanza Digital.
Analizás solicitudes de cotización y RFPs para identificar oportunidades reales, criterios de decisión y red flags.
Sos directo: si hay señales de que no vale la pena responder, lo decís claramente.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1400, temperature=0.3, json_mode=True)
    return _parse_json(raw) if raw else None


def analizar_propuesta_competencia(
    contenido_propuesta: str,
    *,
    nombre_competidor: str = "",
    empresa_prospecto: str = "",
    sector: str = "",
) -> Optional[dict]:
    """
    Módulo 8 — Análisis de propuesta de un competidor.
    El aliado pega o describe la propuesta que el prospecto le mostró de otro proveedor.

    Retorna:
        {
          "resumen_propuesta_competidor": str,
          "precio_detectado": str,
          "puntos_fuertes_competidor": list[str],
          "puntos_debiles_competidor": list[str],
          "donde_avanza_gana": list[str],
          "donde_avanza_pierde": list[str],
          "estrategia_contrapropuesta": str,
          "argumentos_para_reunion": list[str],
          "que_ofrecer_diferente": str,
        }
    """
    prompt = f"""
El prospecto le mostró al aliado la propuesta de un competidor. Analizala.

COMPETIDOR: {nombre_competidor or "competidor no identificado"}
EMPRESA PROSPECTO: {empresa_prospecto or "la empresa del prospecto"}
SECTOR: {sector or "industrial"}

CONTENIDO DE LA PROPUESTA DEL COMPETIDOR:
---
{contenido_propuesta}
---

Avanza Digital ofrece: presencia web B2B, SEO, generación de leads, sistemas de
marketing digital para PYMES industriales. Planes $1.050 - $7.500 ARS/mes.
Diferenciales: conocimiento sectorial industrial, soporte personalizado, resultados
medibles en 90 días, sin contratos largos obligatorios.

Sé honesto sobre las fortalezas del competidor — el aliado necesita saber la verdad.
Pero encontrá el ángulo real de diferenciación.

Devolvé este JSON:
{{
  "resumen_propuesta_competidor": "<qué está ofreciendo el competidor en 2-3 líneas>",
  "precio_detectado": "<precio o rango detectado, o 'no especificado'>",
  "puntos_fuertes_competidor": [
    "<lo que el competidor hace bien o propone bien>",
    "<punto fuerte 2>",
    "<punto fuerte 3>"
  ],
  "puntos_debiles_competidor": [
    "<lo que le falta, es vago, o es una promesa sin respaldo>",
    "<debilidad 2>",
    "<debilidad 3>"
  ],
  "donde_avanza_gana": [
    "<dimensión concreta donde Avanza es objetivamente mejor para este prospecto>",
    "<ventaja 2>",
    "<ventaja 3>"
  ],
  "donde_avanza_pierde": [
    "<dimensión donde el competidor tiene ventaja real — ser honestos>",
    "<desventaja 2 si existe>"
  ],
  "estrategia_contrapropuesta": "<enfoque táctico para la respuesta — qué cambiar, agregar o enfatizar para ganar>",
  "argumentos_para_reunion": [
    "<argumento concreto para decir en la reunión contra esta propuesta específica>",
    "<argumento 2>",
    "<argumento 3>"
  ],
  "que_ofrecer_diferente": "<qué incluir en la contrapropuesta que el competidor no tiene o no puede igualar>"
}}
"""

    system = """Sos el Módulo de Análisis Competitivo de JARVIS para Avanza Digital.
Analizás propuestas de competidores para ayudar a los aliados a ganar licitaciones y comparaciones.
Sos objetivo: reconocés las fortalezas del competidor porque la honestidad ayuda más que el wishful thinking.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1500, temperature=0.3, json_mode=True)
    return _parse_json(raw) if raw else None


def analizar_comunicado(
    contenido_comunicado: str,
    *,
    empresa_fuente: str = "",
    tipo_documento: str = "comunicado",
    sector: str = "",
    aliado_nombre: str = "",
) -> Optional[dict]:
    """
    Módulo 8 — Análisis de comunicados, noticias o contratos de prospectos.
    Detecta oportunidades comerciales implícitas.

    Retorna:
        {
          "resumen": str,
          "oportunidades_detectadas": list[str],
          "angulo_de_contacto": str,
          "mensaje_sugerido": str,
          "urgencia": str,
          "notas_internas": str,
        }
    """
    prompt = f"""
Analizá este {tipo_documento} de una empresa prospecto y detectá oportunidades comerciales.

EMPRESA: {empresa_fuente or "empresa prospecto"}
SECTOR: {sector or "industrial"}
ALIADO: {aliado_nombre or "aliado de Avanza Digital"}

CONTENIDO:
---
{contenido_comunicado}
---

Avanza Digital vende presencia web B2B, SEO, generación de leads y marketing digital
para PYMES industriales. El objetivo es detectar si hay algo en este documento que
justifique que el aliado se contacte con esta empresa (o refuerce el contacto existente).

Devolvé este JSON:
{{
  "resumen": "<de qué trata el documento en 1-2 líneas>",
  "oportunidades_detectadas": [
    "<oportunidad comercial concreta implícita en el documento — por qué justifica el contacto>",
    "<oportunidad 2 si existe>",
    "<oportunidad 3 si existe>"
  ],
  "angulo_de_contacto": "<cómo vincular el contenido del documento con lo que Avanza puede hacer por ellos>",
  "mensaje_sugerido": "<primer mensaje o apertura específica para contactar a esta empresa usando el contexto del documento, máximo 50 palabras, español rioplatense>",
  "urgencia": "<alta|media|baja — qué tan pronto conviene contactarlos basado en el documento + justificación>",
  "notas_internas": "<algo que el aliado debería saber antes de contactar, que no es obvio>"
}}
"""

    system = """Sos el Módulo de Análisis de Documentos de JARVIS para Avanza Digital.
Leés comunicados, noticias, contratos y notas de prensa de empresas industriales y detectás oportunidades comerciales.
Sos creativo pero concreto: solo señalás oportunidades que realmente existen en el texto.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.4, json_mode=True)
    return _parse_json(raw) if raw else None


# ═══════════════════════════════════════════════════════════════════════════════
# RUTAS FASTAPI — register()
# ═══════════════════════════════════════════════════════════════════════════════

def register(app, get_db_func, auth_dep):
    """
    Registra todos los endpoints de JARVIS Mercado + Documentos en la app FastAPI.

    Llamar desde main.py:
        import jarvis_mercado
        jarvis_mercado.register(app, get_db, current_aliado_required)
    """
    import json as _json
    from fastapi import Depends, HTTPException
    from sqlalchemy.orm import Session
    from pydantic import BaseModel

    # ── Schemas ──────────────────────────────────────────────────────────────

    class CompetidorRequest(BaseModel):
        nombre_competidor: str
        sector: str
        info_adicional: str = ""

    class InvestigarEmpresaRequest(BaseModel):
        nombre_empresa: str
        sector: str
        ciudad: str = ""
        nombre_contacto: str = ""
        cargo_contacto: str = ""
        notas_previas: str = ""

    class RadarRequest(BaseModel):
        sector: str
        region: str = "Argentina"

    class MapeoRequest(BaseModel):
        sector: str
        region: str
        tipo_empresa: str = "PYME industrial"

    class RFPRequest(BaseModel):
        contenido_rfp: str
        empresa_solicitante: str = ""
        sector: str = ""

    class PropuestaCompetenciaRequest(BaseModel):
        contenido_propuesta: str
        nombre_competidor: str = ""
        empresa_prospecto: str = ""
        sector: str = ""

    class ComunicadoRequest(BaseModel):
        contenido_comunicado: str
        empresa_fuente: str = ""
        tipo_documento: str = "comunicado"
        sector: str = ""

    # ── Helper para extraer contexto del aliado ───────────────────────────────

    def _ctx(aliado_obj) -> dict:
        rubros = []
        try:
            rubros_raw = getattr(aliado_obj, "rubros_especialidad", "[]") or "[]"
            rubros = _json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
        except Exception:
            pass
        return {
            "aliado_nombre": getattr(aliado_obj, "nombre", "") or "",
            "aliado_ciudad": getattr(aliado_obj, "ciudad", "") or "",
            "aliado_rubros": rubros,
        }

    # ── Módulo 5: Analista de Mercado ─────────────────────────────────────────

    @app.post("/jarvis/mercado/competidor")
    def jarvis_analizar_competidor(
        body: CompetidorRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Genera un battle card de un competidor."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = analizar_competidor(
            nombre_competidor=body.nombre_competidor,
            sector=body.sector,
            info_adicional=body.info_adicional,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar el competidor")
        return {"ok": True, "battle_card": resultado}

    @app.post("/jarvis/mercado/investigar-empresa")
    def jarvis_investigar_empresa(
        body: InvestigarEmpresaRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Ficha completa de un prospecto para preparar una reunión."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        resultado = investigar_empresa(
            nombre_empresa=body.nombre_empresa,
            sector=body.sector,
            ciudad=body.ciudad,
            nombre_contacto=body.nombre_contacto,
            cargo_contacto=body.cargo_contacto,
            notas_previas=body.notas_previas,
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo investigar la empresa")
        return {"ok": True, "ficha": resultado}

    @app.post("/jarvis/mercado/radar")
    def jarvis_radar_sectorial(
        body: RadarRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Radar de tendencias, oportunidades y alertas del sector del aliado."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = radar_sectorial(
            sector=body.sector,
            region=body.region,
            aliado_rubros=ctx["aliado_rubros"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el radar")
        return {"ok": True, "radar": resultado}

    @app.post("/jarvis/mercado/oportunidades")
    def jarvis_mapear_oportunidades(
        body: MapeoRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Mapa de dónde hay más oportunidad para prospectar."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        resultado = mapear_oportunidades(
            sector=body.sector,
            region=body.region,
            tipo_empresa=body.tipo_empresa,
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo mapear oportunidades")
        return {"ok": True, "mapa": resultado}

    # ── Módulo 8: Inteligencia de Documentos ──────────────────────────────────

    @app.post("/jarvis/doc/rfp")
    def jarvis_analizar_rfp(
        body: RFPRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Analiza una solicitud de cotización / RFP recibida."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = analizar_rfp(
            contenido_rfp=body.contenido_rfp,
            empresa_solicitante=body.empresa_solicitante,
            aliado_nombre=ctx["aliado_nombre"],
            sector=body.sector,
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar la solicitud")
        return {"ok": True, "analisis": resultado}

    @app.post("/jarvis/doc/propuesta-competencia")
    def jarvis_analizar_propuesta_competencia(
        body: PropuestaCompetenciaRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Analiza la propuesta de un competidor y genera estrategia de contra-propuesta."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        resultado = analizar_propuesta_competencia(
            contenido_propuesta=body.contenido_propuesta,
            nombre_competidor=body.nombre_competidor,
            empresa_prospecto=body.empresa_prospecto,
            sector=body.sector,
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar la propuesta")
        return {"ok": True, "analisis": resultado}

    @app.post("/jarvis/doc/comunicado")
    def jarvis_analizar_comunicado(
        body: ComunicadoRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Analiza un comunicado o noticia y detecta oportunidades comerciales."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = analizar_comunicado(
            contenido_comunicado=body.contenido_comunicado,
            empresa_fuente=body.empresa_fuente,
            tipo_documento=body.tipo_documento,
            sector=body.sector,
            aliado_nombre=ctx["aliado_nombre"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar el comunicado")
        return {"ok": True, "analisis": resultado}
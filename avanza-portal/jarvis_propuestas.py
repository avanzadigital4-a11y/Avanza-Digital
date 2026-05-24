"""
jarvis_propuestas.py — Módulo 3: Generador de Propuestas de JARVIS

Implementa el Generador de Propuestas completo del Blueprint v2, Sección 3 Módulo 3.

DISEÑO:
  Mismo patrón defensivo del resto de JARVIS: si la IA o la BD falla,
  las funciones devuelven None y el llamador usa su fallback heurístico.
  El producto NUNCA se cae por un problema con la IA.
  Timeout duro de 25 segundos (propuestas son las respuestas más largas).

FUNCIONES PRINCIPALES:
  generar_propuesta_completa()    → Genera las 3 versiones del Blueprint:
                                    técnica (para analíticos), ROI (para gerentes),
                                    ejecutiva (para directivos / 1 página).
                                    Incluye análisis de riesgo interno y
                                    notas de presentación para el aliado.

  generar_propuesta_rapida()      → Versión express de propuesta para cuando
                                    el aliado necesita algo en 2 minutos.
                                    Solo la versión más apropiada al perfil.

  estimar_roi()                   → Calcula y argumenta el ROI esperado para
                                    el cliente específico, basándose en el
                                    rubro, tamaño y plan elegido.
                                    Usado como sección standalone en cualquier
                                    propuesta o email.

  generar_email_propuesta()       → Genera el email de envío de propuesta
                                    (el que acompaña el PDF), personalizado
                                    al perfil del comprador detectado.

  evaluar_propuesta_propia()      → El aliado pega su borrador de propuesta.
                                    JARVIS la evalúa, detecta debilidades y
                                    sugiere mejoras concretas.

INTEGRACIÓN:
  Llamar directamente desde jarvis_routes.py o cualquier endpoint de FastAPI.
  Compatible con los modelos Prospecto y LeadBolsa de models.py.
  Las propuestas completas están listas para convertir a PDF con Puppeteer o WeasyPrint.
"""

from __future__ import annotations
import os, json, sys
from typing import Optional, Any

# ─── CONFIG ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 25.0  # propuestas son respuestas largas

PLANES_AVANZA = {
    "Plan Base":       1050.0,
    "Plan Pro":        2900.0,
    "Plan Industrial": 4900.0,
    "Estrategico 360": 7500.0,
}

CONTENIDOS_POR_PLAN = {
    "Plan Base": [
        "Sitio web profesional hasta 5 páginas",
        "Diseño adaptado al rubro",
        "Optimización SEO básica",
        "Formulario de contacto y WhatsApp integrado",
        "Alta en Google Maps y Google Business",
        "Panel de administración simple",
    ],
    "Plan Pro": [
        "Sitio web profesional ilimitado",
        "Diseño premium adaptado al sector industrial",
        "SEO avanzado con posicionamiento local y sectorial",
        "Sistema de captación de leads con CRM básico",
        "Integración WhatsApp Business con respuesta automática",
        "Formularios inteligentes de cotización",
        "Alta y gestión de Google Business Profile",
        "Informe mensual de performance y leads generados",
        "Soporte prioritario con SLA de 24hs",
    ],
    "Plan Industrial": [
        "Todo el Plan Pro incluido",
        "Catálogo de productos/servicios con fichas técnicas",
        "Sistema de cotización online automatizado",
        "Integración con CRM propio del cliente",
        "SEO industrial avanzado — posicionamiento por producto y proceso",
        "Landings específicas por línea de producto o servicio",
        "Campañas de Google Ads industriales básicas",
        "Informe quincenal con análisis de competencia",
        "Consultoría estratégica mensual (1hs)",
        "IA JARVIS para el equipo comercial incluida",
    ],
    "Estrategico 360": [
        "Todo el Plan Industrial incluido",
        "Estrategia digital integral a 12 meses",
        "Campañas de Google Ads y LinkedIn Ads industriales",
        "Automatización de marketing y nurturing de leads",
        "Video institucional y contenido premium",
        "Gestión de redes sociales profesional",
        "IA JARVIS completa con módulos avanzados",
        "Consultoría estratégica quincenal (2hs)",
        "Gerente de cuenta dedicado",
        "Reporte ejecutivo mensual con ROI medido",
    ],
}

PAISES = {
    "AR": "Argentina", "MX": "México", "CO": "Colombia",
    "CL": "Chile",     "PE": "Perú",   "UY": "Uruguay",
}


def is_enabled() -> bool:
    """¿Hay API key configurada?"""
    return bool(ANTHROPIC_API_KEY)


# ─── CORE: llamada a Claude ───────────────────────────────────────────────────

def _chat(
    prompt: str,
    system: str,
    *,
    max_tokens: int = 2000,
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
        print(f"[JARVIS PROPUESTAS ERROR] {type(e).__name__}: {e}", file=sys.stderr)
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
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    print(f"[JARVIS PROPUESTAS] No se pudo parsear JSON: {text[:200]}", file=sys.stderr)
    return None


def _get_contenido_plan(plan: str) -> str:
    """Devuelve los contenidos del plan como texto."""
    items = CONTENIDOS_POR_PLAN.get(plan, CONTENIDOS_POR_PLAN.get("Plan Pro", []))
    return "\n".join(f"  • {item}" for item in items)


def _build_aliado_context(
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    aliado_pais: str = "AR",
    aliado_rubros: list[str] | None = None,
) -> str:
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "marketing digital industrial"
    pais_nombre = PAISES.get(aliado_pais, aliado_pais)
    return (
        f"Aliado presentador: {aliado_nombre or 'el aliado'}, "
        f"{aliado_ciudad or ''} {pais_nombre}. "
        f"Especialidad: {rubros_str}."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 1 — PROPUESTA COMPLETA (3 VERSIONES)
# ═══════════════════════════════════════════════════════════════════════════════

def generar_propuesta_completa(
    empresa_cliente: str,
    rubro: str,
    nombre_contacto: str = "",
    cargo_contacto: str = "",
    plan: str = "Plan Pro",
    dolores_detectados: str = "",
    nota_aliado: str = "",
    ciudad_cliente: str = "",
    pais_cliente: str = "AR",
    *,
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    aliado_pais: str = "AR",
    aliado_rubros: list[str] | None = None,
) -> Optional[dict]:
    """
    Módulo 3 — Generador de Propuestas completo.
    Genera las 3 versiones del Blueprint v2: Técnica, ROI y Ejecutiva.

    Retorna:
        {
          "meta": {
            "empresa": str,
            "plan": str,
            "ticket": float,
            "contenidos": [str, ...],
          },

          "version_tecnica": {
            "titulo": str,
            "para_quien": str,          # descripción del perfil objetivo
            "introduccion": str,
            "metodologia": str,
            "entregables": [str, ...],
            "plazos": str,
            "soporte": str,
            "cierre": str,
          },

          "version_roi": {
            "titulo": str,
            "para_quien": str,
            "gancho_apertura": str,
            "problema_costo": str,      # cuánto le cuesta no tener esto
            "solucion_valor": str,
            "roi_estimado": str,        # números concretos para el rubro
            "casos_referencia": str,
            "garantia": str,
            "cierre": str,
          },

          "version_ejecutiva": {
            "titulo": str,
            "para_quien": str,
            "resumen_una_pagina": str,   # toda la propuesta en 200 palabras
            "tres_bullets": [str, ...],  # los 3 puntos clave
            "inversion": str,
            "proximo_paso": str,
          },

          "email_acompanamiento": {
            "asunto": str,
            "cuerpo": str,              # email para enviar junto al PDF
          },

          "notas_aliado": str,          # tips internos — NO van al cliente
          "analisis_riesgo": str,       # riesgos a anticipar — NO va al cliente
        }
    O None si Claude no está disponible.
    """
    ticket = PLANES_AVANZA.get(plan, 2900.0)
    contenido_plan = _get_contenido_plan(plan)
    contexto_aliado = _build_aliado_context(
        aliado_nombre=aliado_nombre,
        aliado_ciudad=aliado_ciudad,
        aliado_pais=aliado_pais,
        aliado_rubros=aliado_rubros,
    )
    pais_cliente_nombre = PAISES.get(pais_cliente, pais_cliente)

    prompt = f"""Generá una propuesta comercial completa en 3 versiones para presentarle
a este prospecto del sector industrial latinoamericano.

DATOS DEL CLIENTE:
- Empresa: {empresa_cliente}
- Rubro / Sector: {rubro}
- Contacto: {nombre_contacto or 'decisor'} {('— ' + cargo_contacto) if cargo_contacto else ''}
- Ubicación: {ciudad_cliente or 'no especificada'}, {pais_cliente_nombre}
- Dolores detectados: {dolores_detectados or 'no especificados — inferir del rubro'}
- Nota del aliado: {nota_aliado or 'ninguna'}

PLAN A PROPONER:
- Nombre: {plan}
- Inversión: ${ticket:,.0f} ARS/mes
- Contenidos incluidos:
{contenido_plan}

{contexto_aliado}

Generá el JSON completo con las 3 versiones y los elementos adicionales:
{{
  "meta": {{
    "empresa": "{empresa_cliente}",
    "plan": "{plan}",
    "ticket": {ticket},
    "contenidos": [<lista de strings con los contenidos del plan>]
  }},

  "version_tecnica": {{
    "titulo": "<título de la propuesta para perfil técnico — específico al rubro>",
    "para_quien": "<descripción del perfil objetivo de esta versión — 1 oración>",
    "introduccion": "<párrafo que conecta con el dolor técnico específico del rubro — 60-80 palabras>",
    "metodologia": "<descripción de la metodología de trabajo de Avanza — proceso, etapas, cómo se implementa — 80-100 palabras>",
    "entregables": [
      "<entregable concreto 1 con detalle técnico>",
      "<entregable 2>",
      "<entregable 3>",
      "<entregable 4>"
    ],
    "plazos": "<timeline de implementación con hitos — 40-60 palabras>",
    "soporte": "<descripción del soporte y SLA incluidos>",
    "cierre": "<párrafo de cierre técnico con CTA concreto — 30-40 palabras>"
  }},

  "version_roi": {{
    "titulo": "<título orientado a resultados — específico al rubro>",
    "para_quien": "<descripción del perfil objetivo — gerente comercial o de marketing>",
    "gancho_apertura": "<apertura con número o dato que genera atención — 30-40 palabras>",
    "problema_costo": "<cuánto le cuesta al cliente de este rubro NO tener presencia digital profesional — con estimaciones realistas — 60-80 palabras>",
    "solucion_valor": "<cómo Avanza resuelve el problema con resultados medibles — específico al rubro — 80-100 palabras>",
    "roi_estimado": "<estimación de ROI concreta para este rubro y tamaño de empresa: inversión, resultado esperado en 90 días, período de retorno — con números realistas — 60-80 palabras>",
    "casos_referencia": "<referencia a resultados de empresas similares del rubro — sin nombres si no hay datos reales, usar 'empresas del sector' — 40-50 palabras>",
    "garantia": "<qué garantiza Avanza: si no hay resultados medibles en X días, qué pasa — 30-40 palabras>",
    "cierre": "<cierre orientado a ROI con CTA concreto — 30-40 palabras>"
  }},

  "version_ejecutiva": {{
    "titulo": "<título ejecutivo — 1 página — para directivos que no tienen tiempo>",
    "para_quien": "<descripción del perfil objetivo — director, dueño, CEO>",
    "resumen_una_pagina": "<toda la propuesta en máximo 200 palabras: problema, solución, inversión, resultado — párrafos cortos, lenguaje ejecutivo>",
    "tres_bullets": [
      "<punto clave 1 — el beneficio más importante en 1 oración>",
      "<punto clave 2>",
      "<punto clave 3>"
    ],
    "inversion": "<frase sobre la inversión: precio, modalidad, qué incluye — máximo 30 palabras>",
    "proximo_paso": "<CTA ejecutivo: qué pasa si acepta — próximo paso concreto — máximo 20 palabras>"
  }},

  "email_acompanamiento": {{
    "asunto": "<asunto del email para enviar junto al PDF — específico, no genérico>",
    "cuerpo": "<email de acompañamiento del PDF — 120-160 palabras: apertura personal, referencia a la conversación previa, resumen del valor, CTA para reunión de cierre>"
  }},

  "notas_aliado": "<tips internos para el aliado sobre cómo presentar esta propuesta específica — qué destacar según el perfil del contacto, qué objeciones anticipar, cómo manejar el precio — 80-100 palabras — ESTO NO VA AL CLIENTE>",

  "analisis_riesgo": "<análisis de los principales riesgos de esta oportunidad: señales de alerta detectadas, probabilidad de cierre estimada, qué puede hacer que se pierda — 60-80 palabras — ESTO NO VA AL CLIENTE>"
}}"""

    system = """Sos el Módulo de Propuestas de JARVIS para Avanza Digital.
Generás propuestas que SE LEEN porque son específicas al rubro y al dolor del cliente — no son templates genéricos.
Conocés el sector industrial latinoamericano: metalurgia, agro, logística, construcción, frigorífico, tecnico, automotriz.
Sabés que un gerente de producción no lee lo mismo que un CEO, y un analítico no se convence igual que un emocional.
Las versiones no son el mismo texto en diferente longitud — son ENFOQUES DISTINTOS para perfiles distintos.
Los números de ROI deben ser realistas y específicos al rubro, no inflados.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después. Sin bloques de código markdown."""

    raw = _chat(prompt, system, max_tokens=3500, temperature=0.4, json_mode=True)
    if not raw:
        return None

    parsed = _parse_json(raw)
    if not parsed or "version_tecnica" not in parsed:
        return None

    # Asegurar que meta tiene los datos correctos
    if "meta" not in parsed:
        parsed["meta"] = {}
    parsed["meta"]["empresa"] = empresa_cliente
    parsed["meta"]["plan"]    = plan
    parsed["meta"]["ticket"]  = ticket
    if "contenidos" not in parsed["meta"]:
        parsed["meta"]["contenidos"] = CONTENIDOS_POR_PLAN.get(plan, [])

    return parsed


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 2 — PROPUESTA RÁPIDA (UNA SOLA VERSIÓN)
# ═══════════════════════════════════════════════════════════════════════════════

def generar_propuesta_rapida(
    empresa_cliente: str,
    rubro: str,
    plan: str = "Plan Pro",
    dolores_detectados: str = "",
    perfil_comprador: str = "roi",  # "tecnico" | "roi" | "ejecutivo"
    nota_aliado: str = "",
    *,
    aliado_nombre: str = "",
) -> Optional[dict]:
    """
    Versión express del generador de propuestas.
    Genera solo la versión más apropiada al perfil del comprador.
    Lista en menos tiempo — para cuando el aliado la necesita en 2 minutos.

    perfil_comprador: "tecnico" | "roi" | "ejecutivo"

    Retorna:
        {
          "asunto_email": str,
          "introduccion": str,
          "propuesta_valor": str,
          "roi_estimado": str,
          "llamada_accion": str,
          "inversion": str,
          "notas_aliado": str,
        }
    O None si Claude no está disponible.
    """
    ticket = PLANES_AVANZA.get(plan, 2900.0)
    contenido_plan = _get_contenido_plan(plan)

    perfiles_desc = {
        "tecnico":    "analítico técnico (jefe de planta, gerente de producción, IT) — foco en metodología, entregables y SLA",
        "roi":        "gerente comercial o de marketing — foco en retorno, leads generados y resultados medibles",
        "ejecutivo":  "directivo o dueño — foco en decisión simple, inversión vs. beneficio, brevedad máxima",
    }
    perfil_desc = perfiles_desc.get(perfil_comprador, perfiles_desc["roi"])

    prompt = f"""Generá una propuesta comercial rápida para este cliente del sector industrial.

CLIENTE:
- Empresa: {empresa_cliente}
- Rubro: {rubro}
- Dolores detectados: {dolores_detectados or 'inferir del rubro'}
- Nota del aliado: {nota_aliado or 'ninguna'}

PLAN: {plan} — ${ticket:,.0f} ARS/mes
CONTENIDOS:
{contenido_plan}

PERFIL DEL COMPRADOR AL QUE VA DIRIGIDA: {perfil_desc}

ALIADO: {aliado_nombre or 'el aliado de Avanza Digital'}

Devolvé este JSON exacto:
{{
  "asunto_email": "<asunto específico al rubro y al dolor — no genérico>",
  "introduccion": "<párrafo de apertura que conecta con el dolor específico — 50-70 palabras>",
  "propuesta_valor": "<valor central de {plan} para este rubro específico — 80-100 palabras, adaptado al perfil {perfil_comprador}>",
  "roi_estimado": "<qué resultado concreto puede esperar esta empresa en 90 días — con números realistas — 40-60 palabras>",
  "llamada_accion": "<CTA claro y específico — 20-30 palabras>",
  "inversion": "<frase sobre la inversión — precio, modalidad — 15-20 palabras>",
  "notas_aliado": "<1-2 tips internos de cómo presentar esta propuesta al perfil {perfil_comprador} — NO VA AL CLIENTE>"
}}"""

    system = """Sos el Módulo de Propuestas Rápidas de JARVIS para Avanza Digital.
Generás propuestas específicas al rubro — nunca genéricas.
La velocidad no compromete la calidad: cada propuesta debe sentirse personalizada.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=1200, temperature=0.4, json_mode=True)
    if not raw:
        return None

    return _parse_json(raw)


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 3 — ESTIMACIÓN DE ROI
# ═══════════════════════════════════════════════════════════════════════════════

def estimar_roi(
    empresa_cliente: str,
    rubro: str,
    plan: str = "Plan Pro",
    tamano_empresa: str = "pyme",  # "micro" | "pyme" | "mediana" | "grande"
    ticket_promedio_venta: float = 0.0,
    clientes_actuales: int = 0,
    contexto_adicional: str = "",
) -> Optional[dict]:
    """
    Calcula y argumenta el ROI esperado para el cliente específico.
    Devuelve números concretos y argumentación lista para usar en propuesta o email.

    Retorna:
        {
          "inversion_mensual": float,
          "inversion_anual": float,
          "resultado_esperado_90_dias": str,
          "leads_estimados_mes": str,
          "clientes_nuevos_estimados": str,
          "ingreso_adicional_estimado": str,
          "periodo_retorno": str,
          "roi_porcentual": str,
          "argumento_roi_corto": str,       # para usar en email — 1 oración
          "argumento_roi_largo": str,        # para usar en propuesta — 3-4 oraciones
          "disclaimer": str,                 # transparencia sobre las estimaciones
        }
    O None si Claude no está disponible.
    """
    ticket = PLANES_AVANZA.get(plan, 2900.0)

    ticket_venta_str = f"${ticket_promedio_venta:,.0f}" if ticket_promedio_venta > 0 else "no especificado"
    clientes_str = str(clientes_actuales) if clientes_actuales > 0 else "no especificado"

    prompt = f"""Calculá y argumentá el ROI esperado para este cliente de Avanza Digital.

CLIENTE:
- Empresa: {empresa_cliente}
- Rubro: {rubro}
- Tamaño: {tamano_empresa}
- Ticket promedio de una venta del cliente: {ticket_venta_str}
- Clientes activos actuales del cliente: {clientes_str}
- Contexto adicional: {contexto_adicional or 'ninguno'}

PLAN CONTRATADO: {plan} — ${ticket:,.0f} ARS/mes

TAREA:
Generá una estimación de ROI realista y honesta para este tipo de empresa en este rubro.
Usá benchmarks razonables del sector industrial latinoamericano.
No inflés los números — si los números no son convincentes solos, no los hagas convincentes con exageración.
Sé honesto sobre las estimaciones.

Devolvé este JSON exacto:
{{
  "inversion_mensual": {ticket},
  "inversion_anual": {ticket * 12},
  "resultado_esperado_90_dias": "<qué resultado concreto y medible es razonable esperar en 90 días para este rubro y tamaño — específico>",
  "leads_estimados_mes": "<rango de leads mensuales razonable para este rubro con el plan — ej: '8-15 consultas/mes' — con base>",
  "clientes_nuevos_estimados": "<rango de clientes nuevos razonable en los primeros 6 meses — con base en tasas de conversión típicas del sector>",
  "ingreso_adicional_estimado": "<ingreso adicional estimado anual si se asume el ticket de venta del cliente — solo si se proporcionó, si no: 'depende del ticket del cliente'>",
  "periodo_retorno": "<en cuántos meses es razonable que se recupere la inversión — con base>",
  "roi_porcentual": "<ROI porcentual estimado a 12 meses — solo si hay datos suficientes, si no: 'variable según ticket del cliente'>",
  "argumento_roi_corto": "<1 oración con el argumento de ROI para usar en un email — concreto, con números>",
  "argumento_roi_largo": "<3-4 oraciones con el argumento completo para propuesta — con números, con lógica, con benchmarks del sector>",
  "disclaimer": "<nota de transparencia honesta sobre las estimaciones — 1-2 oraciones: que son proyecciones basadas en benchmarks y los resultados reales dependen de factores propios de la empresa>"
}}"""

    system = """Sos el módulo de ROI de JARVIS para Avanza Digital.
Generás estimaciones HONESTAS y REALISTAS — no infladas para cerrar una venta.
Tu credibilidad depende de que los aliados puedan pararse con confianza detrás de estos números.
Usás benchmarks reales del sector industrial latinoamericano: tasas de conversión del 2-5% en digital industrial,
ciclos de compra de 30-90 días, tickets promedio de PYMES industriales argentinas.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=1200, temperature=0.25, json_mode=True)
    if not raw:
        return None

    parsed = _parse_json(raw)
    if not parsed:
        return None

    # Asegurar campos de inversión correctos
    parsed["inversion_mensual"] = ticket
    parsed["inversion_anual"]   = ticket * 12

    return parsed


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 4 — EMAIL DE ENVÍO DE PROPUESTA
# ═══════════════════════════════════════════════════════════════════════════════

def generar_email_propuesta(
    empresa_cliente: str,
    rubro: str,
    nombre_contacto: str = "",
    cargo_contacto: str = "",
    plan: str = "Plan Pro",
    resumen_reunion: str = "",
    dolores_detectados: str = "",
    *,
    aliado_nombre: str = "",
    aliado_pais: str = "AR",
) -> Optional[dict]:
    """
    Genera el email que acompaña el envío del PDF de propuesta.
    Personalizado al perfil del comprador y a lo que se conversó en la reunión.

    Retorna:
        {
          "asunto": str,
          "cuerpo": str,
          "posdata": str,           # P.D. táctica opcional
          "variante_seguimiento": { # email de seguimiento si no responde en 48hs
            "asunto": str,
            "cuerpo": str,
          }
        }
    O None si Claude no está disponible.
    """
    ticket = PLANES_AVANZA.get(plan, 2900.0)
    pais_nombre = PAISES.get(aliado_pais, aliado_pais)

    prompt = f"""Generá el email de envío de propuesta y su email de seguimiento.

CONTEXTO:
- Empresa cliente: {empresa_cliente}
- Rubro: {rubro}
- Contacto: {nombre_contacto or 'el decisor'} {('(' + cargo_contacto + ')') if cargo_contacto else ''}
- Plan propuesto: {plan} (${ticket:,.0f} ARS/mes)
- Resumen de la reunión previa: {resumen_reunion or 'tuvimos una reunión donde mostramos el plan'}
- Dolores detectados: {dolores_detectados or 'presencia digital limitada, necesita más leads'}
- Aliado que envía: {aliado_nombre or 'el aliado'} ({pais_nombre})

REGLAS DEL EMAIL:
- 120-160 palabras total (sin el asunto)
- Apertura: referencia específica a algo de la reunión o el rubro — nunca "espero que estés bien"
- Cuerpo: 1-2 oraciones de valor concreto para el rubro, link a la propuesta
- Cierre: CTA para reunión de 30 minutos para responder dudas
- Tono: profesional pero humano — no corporativo

Devolvé este JSON exacto:
{{
  "asunto": "<asunto del email — específico al rubro, genera expectativa>",
  "cuerpo": "<cuerpo completo del email — 120-160 palabras, ya formateado con saltos de línea \\n entre párrafos>",
  "posdata": "<P.D. táctica opcional que refuerza el valor o genera urgencia — 20-30 palabras, o null si no aplica>",
  "variante_seguimiento": {{
    "asunto": "<asunto del email de seguimiento si no responde en 48hs>",
    "cuerpo": "<email de seguimiento — 60-80 palabras, diferente ángulo, sin ser agresivo>"
  }}
}}"""

    system = """Sos el módulo de emails de propuesta de JARVIS para Avanza Digital.
Escribís emails que se leen porque son específicos y humanos — no templates corporativos.
La apertura nunca es "espero que estés bien" ni "adjunto la propuesta".
El email acompaña un PDF — su trabajo es que el cliente ABRA el PDF y QUIERA reunirse.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.45, json_mode=True)
    if not raw:
        return None

    return _parse_json(raw)


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN 5 — EVALUACIÓN DE PROPUESTA PROPIA
# ═══════════════════════════════════════════════════════════════════════════════

def evaluar_propuesta_propia(
    propuesta_texto: str,
    empresa_cliente: str = "",
    rubro: str = "",
    plan: str = "",
) -> Optional[dict]:
    """
    El aliado pega su borrador de propuesta.
    JARVIS la evalúa, detecta debilidades y sugiere mejoras concretas.

    propuesta_texto: el texto de la propuesta del aliado (borrador)

    Retorna:
        {
          "puntaje": int,                    # 0-100
          "fortalezas": [str, ...],
          "debilidades": [str, ...],
          "mejoras_criticas": [str, ...],    # las que más impacto tendrían
          "mejoras_opcionales": [str, ...],
          "evaluacion_general": str,
          "version_mejorada_apertura": str,  # reescritura de la apertura si es débil
          "version_mejorada_cta": str,       # reescritura del CTA si es débil
        }
    O None si Claude no está disponible.
    """
    prompt = f"""Evaluá esta propuesta comercial de un aliado de Avanza Digital y sugerí mejoras concretas.

PROPUESTA A EVALUAR:
---
{propuesta_texto}
---

CONTEXTO:
- Empresa cliente: {empresa_cliente or 'no especificada'}
- Rubro: {rubro or 'no especificado'}
- Plan propuesto: {plan or 'no especificado'}

Evaluá la propuesta con este JSON exacto:
{{
  "puntaje": <número 0-100 que refleja qué tan efectiva es la propuesta para cerrar una venta>,
  "fortalezas": [
    "<fortaleza real 1 de la propuesta — qué está bien hecho>",
    "<fortaleza 2>",
    "<fortaleza 3>"
  ],
  "debilidades": [
    "<debilidad 1 — qué está faltando o está mal enfocado>",
    "<debilidad 2>",
    "<debilidad 3>"
  ],
  "mejoras_criticas": [
    "<mejora crítica 1 — la que más impacto tendría en la tasa de cierre — concreta y accionable>",
    "<mejora crítica 2>",
    "<mejora crítica 3>"
  ],
  "mejoras_opcionales": [
    "<mejora opcional 1 — nice to have>",
    "<mejora opcional 2>"
  ],
  "evaluacion_general": "<evaluación general en 2-3 oraciones: qué tipo de comprador convence esta propuesta y por qué, qué le falta para ser excelente>",
  "version_mejorada_apertura": "<si la apertura de la propuesta es débil o genérica, reescribila — específica al rubro y al dolor del cliente. Si la apertura está bien, escribir 'La apertura está bien'>",
  "version_mejorada_cta": "<si el CTA final es débil o vago, reescribilo — concreto, con fecha o acción específica. Si el CTA está bien, escribir 'El CTA está bien'>"
}}"""

    system = """Sos el módulo de evaluación de propuestas de JARVIS para Avanza Digital.
Evaluás propuestas comerciales de marketing digital para PYMES industriales.
Tu evaluación es honesta y constructiva — no inflas el puntaje ni minimizás problemas.
Las mejoras sugeridas son CONCRETAS y ACCIONABLES — no generalidades.
Respondé ÚNICAMENTE con JSON válido. Sin texto antes ni después."""

    raw = _chat(prompt, system, max_tokens=1500, temperature=0.35, json_mode=True)
    if not raw:
        return None

    parsed = _parse_json(raw)
    if not parsed:
        return None

    if "puntaje" in parsed:
        parsed["puntaje"] = max(0, min(100, int(parsed["puntaje"])))

    return parsed
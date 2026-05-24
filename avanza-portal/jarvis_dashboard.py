"""
jarvis_dashboard.py — Módulo 7: Dashboard de Inteligencia

FUNCIONES PRINCIPALES:
  generar_briefing_matutino()   → "Iron Man Protocol": resumen del día al abrir el portal
  analizar_pipeline()           → Análisis de calidad real del pipeline (no solo volumen)
  detectar_oportunidades()      → Oportunidades no solicitadas que JARVIS detecta en los datos
  calcular_metricas_jarvis()    → JARVIS Score del aliado + métricas propietarias
  generar_resumen_semanal()     → Resumen de la semana + insights para la siguiente

DISEÑO:
  Mismo patrón que jarvis.py / jarvis_mercado.py:
    - Si ANTHROPIC_API_KEY no está o Claude falla, todas las funciones devuelven None.
    - El producto NUNCA se cae por un problema con la IA.
    - Timeout duro de 20 segundos.

INTEGRACIÓN EN main.py:
    import jarvis_dashboard
    jarvis_dashboard.register(app, get_db, current_aliado_required)
"""

from __future__ import annotations
import os, json, sys
from typing import Optional, Any
from datetime import datetime, date

# ─── CONFIG ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 20.0


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
        print(f"[JARVIS DASHBOARD ERROR] {type(e).__name__}: {e}", file=sys.stderr)
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
    print(f"[JARVIS DASHBOARD] No se pudo parsear JSON: {text[:200]}", file=sys.stderr)
    return None


def _dia_semana_es(fecha: date | None = None) -> str:
    """Devuelve el día de la semana en español."""
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    d = fecha or date.today()
    return dias[d.weekday()]


# ─── MÓDULO 7A: BRIEFING MATUTINO (Iron Man Protocol) ────────────────────────

def generar_briefing_matutino(
    aliado_nombre: str,
    aliado_ciudad: str = "",
    aliado_pais: str = "AR",
    aliado_rubros: list[str] | None = None,
    # Datos del negocio del aliado (extraídos de la DB antes de llamar)
    leads_activos: int = 0,
    leads_sin_contacto_7d: list[dict] | None = None,   # [{"empresa": ..., "dias": N}]
    propuestas_pendientes: list[dict] | None = None,    # [{"empresa": ..., "dias_sin_resp": N}]
    clientes_sin_actividad: list[dict] | None = None,   # [{"empresa": ..., "dias": N}]
    pipeline_total_usd: float = 0.0,
    tasa_cierre_historica: float = 0.0,
    leads_nuevos_bolsa: int = 0,
    # Métricas de uso de JARVIS
    propuestas_esta_semana: int = 0,
    emails_esta_semana: int = 0,
    reuniones_preparadas: int = 0,
) -> Optional[dict]:
    """
    Genera el briefing matutino del aliado: el "Iron Man Protocol".

    Devuelve:
    {
        "saludo": str,                 # Saludo personalizado con el nombre
        "hora_dia": str,               # "buenos días" / "buenas tardes" / "buenas noches"
        "resumen_negocio": str,        # Estado del negocio en 2-3 líneas
        "urgentes": [                  # Máximo 3 situaciones críticas
            {"icono": "🔴", "texto": str, "accion": str}
        ],
        "oportunidades": [             # Máximo 3 oportunidades detectadas
            {"icono": "🟡", "texto": str, "accion": str}
        ],
        "prioridad_del_dia": str,      # Una sola acción prioritaria del día
        "insight_jarvis": str,         # Algo que JARVIS aprendió / observó sobre el aliado
        "pipeline_real": float,        # Pipeline ajustado por tasa de cierre
        "score_dia": int,              # Score del día (0-100): qué tan bueno es hoy para vender
    }
    """
    if not ANTHROPIC_API_KEY:
        return None

    dia = _dia_semana_es()
    hora = datetime.now().hour
    hora_dia = "buenos días" if hora < 12 else ("buenas tardes" if hora < 20 else "buenas noches")

    rubros_str = ", ".join(aliado_rubros or ["general"])
    pipeline_real = pipeline_total_usd * (tasa_cierre_historica or 0.3)

    sin_contacto_str = ""
    if leads_sin_contacto_7d:
        items = [f"  - {l['empresa']}: {l['dias']} días sin contacto" for l in leads_sin_contacto_7d[:5]]
        sin_contacto_str = "Leads en riesgo de enfriamiento:\n" + "\n".join(items)

    propuestas_str = ""
    if propuestas_pendientes:
        items = [f"  - {p['empresa']}: {p['dias_sin_resp']} días sin respuesta" for p in propuestas_pendientes[:5]]
        propuestas_str = "Propuestas sin respuesta:\n" + "\n".join(items)

    clientes_str = ""
    if clientes_sin_actividad:
        items = [f"  - {c['empresa']}: {c['dias']} días sin actividad" for c in clientes_sin_actividad[:3]]
        clientes_str = "Clientes activos sin actividad reciente:\n" + "\n".join(items)

    system = f"""Sos JARVIS, el asistente de inteligencia comercial de {aliado_nombre}.
Hoy es {dia}. Generás el briefing matutino del portal: un resumen ejecutivo de 30 segundos.
Tu tono es directo, energético, sin vueltas. Nunca genérico.
El aliado trabaja en sectores: {rubros_str}. País: {aliado_pais}.
Respondé siempre con JSON válido, sin texto extra."""

    prompt = f"""Generá el briefing matutino para {aliado_nombre}.

DATOS DEL NEGOCIO HOY:
- Leads activos: {leads_activos}
- Pipeline total declarado: ${pipeline_total_usd:,.0f} USD
- Pipeline real estimado (ajustado por tasa {tasa_cierre_historica:.0%}): ${pipeline_real:,.0f} USD
- Leads nuevos en bolsa sin analizar: {leads_nuevos_bolsa}
- Tasa de cierre histórica: {tasa_cierre_historica:.0%}

SITUACIONES QUE NECESITAN ATENCIÓN:
{sin_contacto_str}
{propuestas_str}
{clientes_str}

ACTIVIDAD ESTA SEMANA:
- Propuestas generadas: {propuestas_esta_semana}
- Emails enviados: {emails_esta_semana}
- Reuniones preparadas: {reuniones_preparadas}

INSTRUCCIONES:
1. "saludo": saludo personalizado (usá el nombre, el día y la hora_dia)
2. "hora_dia": "{hora_dia}"
3. "resumen_negocio": 2 líneas con el estado real del negocio. Sé específico con los números.
4. "urgentes": hasta 3 situaciones críticas. Para cada una: icono 🔴, texto concreto (30 palabras max), accion sugerida (texto del botón, ej: "Reactivar MetalPro").
5. "oportunidades": hasta 3 oportunidades detectadas. Icono 🟡, texto, accion.
6. "prioridad_del_dia": UNA sola acción, la más importante del día. Directa, con empresa/nombre si aplica.
7. "insight_jarvis": observación inteligente basada en los datos. Algo que el aliado no vería sin JARVIS.
8. "pipeline_real": {pipeline_real:.2f}
9. "score_dia": número 0-100 representando qué tan buen día es hoy para vender (considera: día semana, pipeline, urgencias).

Respondé con JSON que siga exactamente esta estructura."""

    raw = _chat(prompt, system, max_tokens=900, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    # Asegurar campos obligatorios con defaults
    result.setdefault("saludo", f"Buenos días, {aliado_nombre}")
    result.setdefault("hora_dia", hora_dia)
    result.setdefault("resumen_negocio", "")
    result.setdefault("urgentes", [])
    result.setdefault("oportunidades", [])
    result.setdefault("prioridad_del_dia", "")
    result.setdefault("insight_jarvis", "")
    result.setdefault("pipeline_real", round(pipeline_real, 2))
    result.setdefault("score_dia", 70)
    result["pipeline_declarado"] = pipeline_total_usd
    result["dia_semana"] = dia
    return result


# ─── MÓDULO 7B: ANÁLISIS DE PIPELINE ─────────────────────────────────────────

def analizar_pipeline(
    aliado_nombre: str,
    aliado_rubros: list[str] | None = None,
    aliado_pais: str = "AR",
    tasa_cierre_historica: float = 0.0,
    pipeline: list[dict] | None = None,
    # Cada item del pipeline:
    # {
    #   "empresa": str, "etapa": str, "valor_usd": float,
    #   "dias_en_etapa": int, "ultimo_contacto_dias": int,
    #   "tiene_propuesta": bool, "proxima_accion": str
    # }
) -> Optional[dict]:
    """
    Analiza la calidad real del pipeline del aliado.

    Devuelve:
    {
        "pipeline_quality_index": int,   # 0-100: calidad real, no solo volumen
        "total_declarado": float,
        "total_ajustado": float,         # Ajustado por probabilidad de cierre real
        "leads_en_riesgo": [str],        # Empresas con señales de abandono
        "leads_calientes": [str],        # Empresas con señales de cierre próximo
        "velocity_promedio_dias": int,   # Días promedio por etapa
        "objecion_mas_costosa": str,     # Objeción que más leads le hace perder
        "etapa_cuello_botella": str,     # Dónde se traban más los deals
        "recomendaciones": [str],        # 3 acciones para mejorar el pipeline esta semana
        "forecast_30d": float,           # Revenue proyectado en 30 días
    }
    """
    if not ANTHROPIC_API_KEY or not pipeline:
        return None

    rubros_str = ", ".join(aliado_rubros or ["general"])
    pipeline_str = json.dumps(pipeline, ensure_ascii=False, indent=2)

    system = f"""Sos JARVIS, el analista de pipeline comercial de {aliado_nombre}.
Sectores: {rubros_str}. País: {aliado_pais}.
Tu análisis es basado en datos reales, nunca optimista sin fundamento.
Identificás patrones que el aliado no ve solo.
Respondé siempre con JSON válido."""

    prompt = f"""Analizá el pipeline de {aliado_nombre}.

PIPELINE ACTUAL:
{pipeline_str}

TASA DE CIERRE HISTÓRICA: {tasa_cierre_historica:.0%}

Analizá:
1. pipeline_quality_index (0-100): ¿Qué tan sano está el pipeline? Considerá distribución por etapas, días sin actividad, diversificación de clientes, y si los valores son realistas.
2. total_declarado: suma total del pipeline en USD.
3. total_ajustado: total ponderado por probabilidad real de cierre (usá la tasa histórica y el estado de cada deal).
4. leads_en_riesgo: lista de empresas con señales de abandono (sin contacto >14 días, estancados >30 días en misma etapa).
5. leads_calientes: lista de empresas con señales de cierre próximo (avanzados en etapa, con propuesta enviada, contacto reciente).
6. velocity_promedio_dias: días promedio que lleva cada deal en el pipeline.
7. objecion_mas_costosa: basado en las etapas y días, ¿en qué punto se pierden más deals?
8. etapa_cuello_botella: etapa donde más deals se quedan trabados.
9. recomendaciones: exactamente 3 acciones concretas para mejorar el pipeline esta semana.
10. forecast_30d: proyección realista de revenue en los próximos 30 días.

Respondé solo con JSON."""

    raw = _chat(prompt, system, max_tokens=1000, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    total = sum(d.get("valor_usd", 0) for d in pipeline)
    result.setdefault("pipeline_quality_index", 60)
    result.setdefault("total_declarado", total)
    result.setdefault("total_ajustado", total * (tasa_cierre_historica or 0.3))
    result.setdefault("leads_en_riesgo", [])
    result.setdefault("leads_calientes", [])
    result.setdefault("velocity_promedio_dias", 0)
    result.setdefault("objecion_mas_costosa", "No hay suficientes datos")
    result.setdefault("etapa_cuello_botella", "Desconocida")
    result.setdefault("recomendaciones", [])
    result.setdefault("forecast_30d", 0.0)
    return result


# ─── MÓDULO 7C: DETECCIÓN DE OPORTUNIDADES ───────────────────────────────────

def detectar_oportunidades(
    aliado_nombre: str,
    aliado_rubros: list[str] | None = None,
    aliado_pais: str = "AR",
    aliado_ciudad: str = "",
    # Historial resumido del aliado
    ultimos_cierres: list[dict] | None = None,   # [{"sector": str, "zona": str, "plan": str}]
    clientes_activos: list[dict] | None = None,  # [{"empresa": str, "meses_activo": int}]
    leads_bolsa_disponibles: list[dict] | None = None,  # [{"sector": str, "zona": str, "score_estimado": int}]
) -> Optional[dict]:
    """
    Detecta oportunidades que el aliado NO está viendo en sus datos.

    Devuelve:
    {
        "oportunidades": [
            {
                "tipo": str,              # "lead_match" | "upsell" | "referido" | "patron"
                "descripcion": str,
                "razon": str,             # Por qué JARVIS la detectó
                "accion_sugerida": str,
                "prioridad": "alta" | "media"
            }
        ],
        "patron_detectado": str,     # Patrón más relevante en el historial del aliado
        "segmento_caliente": str,    # Sector/zona con más oportunidades ahora
    }
    """
    if not ANTHROPIC_API_KEY:
        return None

    rubros_str = ", ".join(aliado_rubros or ["general"])
    cierres_str = json.dumps(ultimos_cierres or [], ensure_ascii=False)
    clientes_str = json.dumps(clientes_activos or [], ensure_ascii=False)
    leads_str = json.dumps(leads_bolsa_disponibles or [], ensure_ascii=False)

    system = f"""Sos JARVIS, el detector de oportunidades comerciales de {aliado_nombre}.
Sectores: {rubros_str}. País: {aliado_pais}, ciudad: {aliado_ciudad}.
Tu objetivo es detectar oportunidades que el aliado no ve solo, cruzando datos de cierres pasados, clientes actuales y leads disponibles.
Sos como un socio estratégico que mira el negocio desde afuera.
Respondé siempre con JSON válido."""

    prompt = f"""Detectá oportunidades no obvias para {aliado_nombre}.

ÚLTIMOS CIERRES DEL ALIADO:
{cierres_str}

CLIENTES ACTIVOS:
{clientes_str}

LEADS DISPONIBLES EN LA BOLSA:
{leads_str}

Analizá los patrones y devolvé:
1. oportunidades: lista de 2-4 oportunidades detectadas. Para cada una:
   - tipo: "lead_match" si un lead de la bolsa matchea con el historial de cierres,
           "upsell" si un cliente activo podría upgrade o servicio adicional,
           "referido" si un cliente con 3+ meses activo es candidato a pedir referidos,
           "patron" si detectás un patrón de zona/sector que el aliado debería aprovechar.
   - descripcion: qué es la oportunidad (máximo 40 palabras)
   - razon: por qué JARVIS la detectó (con datos concretos)
   - accion_sugerida: qué hacer ahora mismo
   - prioridad: "alta" o "media"

2. patron_detectado: el patrón más relevante en el historial del aliado (ej: "Tus últimos 4 cierres fueron metalúrgicas del norte bonaerense con ciclos <45 días")

3. segmento_caliente: el sector/zona donde más oportunidades hay ahora mismo para este aliado específico.

Respondé solo con JSON."""

    raw = _chat(prompt, system, max_tokens=900, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    result.setdefault("oportunidades", [])
    result.setdefault("patron_detectado", "")
    result.setdefault("segmento_caliente", "")
    return result


# ─── MÓDULO 7D: MÉTRICAS PROPIETARIAS JARVIS ─────────────────────────────────

def calcular_metricas_jarvis(
    aliado_nombre: str,
    # Datos de uso del sistema (últimos 30 días)
    consultas_jarvis: int = 0,
    propuestas_generadas: int = 0,
    propuestas_aceptadas_sin_editar: int = 0,  # Las que el aliado envió sin modificar
    leads_analizados: int = 0,
    leads_cerrados_post_analisis: int = 0,
    followups_generados: int = 0,
    tiempo_respuesta_promedio_hs: float = 0.0,
    datos_crm_completos: float = 0.0,    # % de leads con datos completos en CRM
    # Benchmarks del sector (para comparar)
    tiempo_respuesta_industria_hs: float = 8.0,
) -> Optional[dict]:
    """
    Calcula las métricas propietarias de JARVIS para el aliado.

    Devuelve:
    {
        "jarvis_score": int,              # 0-100: qué tan bien está usando JARVIS
        "jarvis_score_breakdown": dict,   # Componentes del score
        "tasa_aceptacion_drafts": float,  # % de drafts aceptados sin editar
        "eficiencia_vs_industria": str,   # "3.2x más rápido que el promedio"
        "mejor_modulo": str,              # Módulo que más usa / le genera más valor
        "oportunidad_mejora": str,        # Módulo subutilizado con mayor potencial
        "nivel_uso": str,                 # "Básico" | "Intermedio" | "Avanzado" | "Experto"
        "recomendacion_proxima_semana": str
    }
    """
    if not ANTHROPIC_API_KEY:
        return None

    # Calcular métricas básicas sin IA primero
    tasa_aceptacion = (
        round(propuestas_aceptadas_sin_editar / propuestas_generadas, 2)
        if propuestas_generadas > 0 else 0.0
    )
    eficiencia = (
        round(tiempo_respuesta_industria_hs / tiempo_respuesta_promedio_hs, 1)
        if tiempo_respuesta_promedio_hs > 0 else 1.0
    )
    tasa_conversion_leads = (
        round(leads_cerrados_post_analisis / leads_analizados, 2)
        if leads_analizados > 0 else 0.0
    )

    system = f"""Sos JARVIS calculando las métricas de uso del sistema para {aliado_nombre}.
Sos objetivo y honesto: si el aliado usa poco JARVIS, lo decís. Si lo usa bien, lo reconocés.
Respondé siempre con JSON válido."""

    prompt = f"""Calculá las métricas JARVIS para {aliado_nombre} (últimos 30 días).

DATOS DE USO:
- Consultas totales a JARVIS: {consultas_jarvis}
- Propuestas generadas: {propuestas_generadas}
- Propuestas aceptadas sin editar: {propuestas_aceptadas_sin_editar} ({tasa_aceptacion:.0%})
- Leads analizados: {leads_analizados}
- Leads cerrados después del análisis: {leads_cerrados_post_analisis} ({tasa_conversion_leads:.0%})
- Follow-ups generados: {followups_generados}
- Tiempo promedio de respuesta al lead: {tiempo_respuesta_promedio_hs:.1f}hs (industria: {tiempo_respuesta_industria_hs:.0f}hs)
- Datos completos en CRM: {datos_crm_completos:.0%}
- Eficiencia vs. industria: {eficiencia}x más rápido

Devolvé:
1. jarvis_score (0-100): basado en frecuencia de uso, calidad de datos, follow-up de leads y resultados obtenidos.
2. jarvis_score_breakdown: objeto con los 4 componentes del score y su puntaje parcial:
   "uso_frecuencia" (0-25), "calidad_datos" (0-25), "follow_up" (0-25), "resultados" (0-25)
3. tasa_aceptacion_drafts: {tasa_aceptacion}
4. eficiencia_vs_industria: frase corta (ej: "3.2x más rápido que el promedio del sector")
5. mejor_modulo: cuál módulo le genera más valor basado en los datos (Chat, Motor de Leads, Propuestas, Comunicador, etc.)
6. oportunidad_mejora: módulo subutilizado que le generaría más valor si lo usara más
7. nivel_uso: "Básico" | "Intermedio" | "Avanzado" | "Experto" (basado en consultas y diversidad de módulos)
8. recomendacion_proxima_semana: una acción concreta para mejorar el JARVIS Score la semana que viene

Respondé solo con JSON."""

    raw = _chat(prompt, system, max_tokens=700, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    result.setdefault("jarvis_score", 50)
    result.setdefault("jarvis_score_breakdown", {})
    result.setdefault("tasa_aceptacion_drafts", tasa_aceptacion)
    result.setdefault("eficiencia_vs_industria", f"{eficiencia}x más rápido que el promedio")
    result.setdefault("mejor_modulo", "Chat")
    result.setdefault("oportunidad_mejora", "Motor de Leads")
    result.setdefault("nivel_uso", "Básico")
    result.setdefault("recomendacion_proxima_semana", "")
    return result


# ─── MÓDULO 7E: RESUMEN SEMANAL ───────────────────────────────────────────────

def generar_resumen_semanal(
    aliado_nombre: str,
    aliado_rubros: list[str] | None = None,
    semana_numero: int = 0,
    # Actividad de la semana
    leads_analizados: int = 0,
    propuestas_enviadas: int = 0,
    reuniones_realizadas: int = 0,
    cierres_semana: int = 0,
    ingresos_semana: float = 0.0,
    # Comparativa con semana anterior
    leads_semana_anterior: int = 0,
    cierres_semana_anterior: int = 0,
    ingresos_semana_anterior: float = 0.0,
    # Lo que aprendió JARVIS esta semana del aliado
    scripts_mas_efectivos: list[str] | None = None,
    objeciones_nuevas: list[str] | None = None,
) -> Optional[dict]:
    """
    Genera el resumen semanal del aliado (enviado los lunes a las 7am).

    Devuelve:
    {
        "titulo": str,
        "resumen_ejecutivo": str,      # 3 líneas máximo
        "mejores_momentos": [str],     # Los 2-3 mejores resultados de la semana
        "aprendizajes": [str],         # Lo que JARVIS aprendió del aliado esta semana
        "comparativa": dict,           # vs semana anterior
        "objetivos_proxima_semana": [str],  # 3 objetivos para la semana que viene
        "frase_motivacional": str,     # Una frase potente y relevante para el aliado
    }
    """
    if not ANTHROPIC_API_KEY:
        return None

    rubros_str = ", ".join(aliado_rubros or ["general"])
    var_leads = leads_analizados - leads_semana_anterior
    var_cierres = cierres_semana - cierres_semana_anterior
    var_ingresos = ingresos_semana - ingresos_semana_anterior

    system = f"""Sos JARVIS preparando el resumen semanal para {aliado_nombre}.
Sectores: {rubros_str}.
Tu estilo es energético, directo y motivador. Siempre basado en datos reales.
Respondé siempre con JSON válido."""

    prompt = f"""Generá el resumen de la semana {semana_numero} para {aliado_nombre}.

ACTIVIDAD DE ESTA SEMANA:
- Leads analizados: {leads_analizados} ({'+' if var_leads >= 0 else ''}{var_leads} vs. semana anterior)
- Propuestas enviadas: {propuestas_enviadas}
- Reuniones realizadas: {reuniones_realizadas}
- Cierres logrados: {cierres_semana} ({'+' if var_cierres >= 0 else ''}{var_cierres} vs. semana anterior)
- Ingresos generados: ${ingresos_semana:,.0f} USD ({'+' if var_ingresos >= 0 else ''}${var_ingresos:,.0f} vs. semana anterior)

SCRIPTS MÁS EFECTIVOS ESTA SEMANA: {scripts_mas_efectivos or ["sin datos"]}
NUEVAS OBJECIONES DETECTADAS: {objeciones_nuevas or ["ninguna"]}

Devolvé:
1. titulo: título del resumen (ej: "Semana 22 — Buena semana de propuestas")
2. resumen_ejecutivo: 2-3 líneas con lo más importante. Sé específico con números.
3. mejores_momentos: lista de 2-3 logros de la semana (incluso si la semana no fue perfecta, buscá los positivos).
4. aprendizajes: lista de 2-3 cosas que JARVIS "aprendió" del aliado esta semana (de sus scripts, respuestas a objeciones, patrones de uso).
5. comparativa: objeto con las 3 métricas clave vs. semana anterior (mejor / igual / peor + porcentaje de cambio).
6. objetivos_proxima_semana: exactamente 3 objetivos concretos y medibles para la semana que viene.
7. frase_motivacional: una frase corta, potente y directa. Que sea del sector/contexto, no genérica.

Respondé solo con JSON."""

    raw = _chat(prompt, system, max_tokens=900, json_mode=True)
    result = _parse_json(raw)
    if not result:
        return None

    result.setdefault("titulo", f"Resumen semana {semana_numero}")
    result.setdefault("resumen_ejecutivo", "")
    result.setdefault("mejores_momentos", [])
    result.setdefault("aprendizajes", [])
    result.setdefault("comparativa", {})
    result.setdefault("objetivos_proxima_semana", [])
    result.setdefault("frase_motivacional", "")
    return result


# ─── REGISTER: inyecta las rutas en la app FastAPI ───────────────────────────

def register(app, get_db_func, auth_dep):
    """
    Llamar desde main.py:
        import jarvis_dashboard
        jarvis_dashboard.register(app, get_db, current_aliado_required)
    """
    from fastapi import Depends
    from fastapi.responses import JSONResponse
    from sqlalchemy.orm import Session
    import json

    # ── Health check ──────────────────────────────────────────────────────────
    @app.get("/jarvis/dashboard/estado")
    def dashboard_estado():
        return {
            "activo":  is_enabled(),
            "modulo":  "Dashboard de Inteligencia (Módulo 7)",
            "funciones": [
                "briefing_matutino",
                "analizar_pipeline",
                "detectar_oportunidades",
                "metricas_jarvis",
                "resumen_semanal",
            ],
        }

    # ── Briefing matutino ─────────────────────────────────────────────────────
    @app.post("/jarvis/dashboard/briefing")
    def endpoint_briefing(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Genera el briefing matutino del aliado.
        Se llama automáticamente al abrir el portal (primera carga del día).
        """
        try:
            rubros = []
            try:
                rubros_raw = getattr(aliado, "rubros_especialidad", "[]") or "[]"
                rubros = json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
            except Exception:
                pass

            ventas_list = getattr(aliado, "ventas", []) or []
            pipeline_usd = sum(
                float(getattr(v, "valor_usd", 0) or 0)
                for v in ventas_list
                if not getattr(v, "confirmada", False)
            )
            ventas_confirmadas = sum(1 for v in ventas_list if getattr(v, "confirmada", False))
            total_ventas = len(ventas_list)
            tasa = round(ventas_confirmadas / total_ventas, 2) if total_ventas > 0 else 0.0

            prospectos = getattr(aliado, "prospectos", []) or []
            from datetime import date, timedelta
            hoy = date.today()
            sin_contacto = []
            for p in prospectos:
                ultimo = getattr(p, "ultimo_contacto", None)
                if ultimo:
                    try:
                        dias = (hoy - (ultimo.date() if hasattr(ultimo, "date") else ultimo)).days
                        if dias >= 7:
                            sin_contacto.append({"empresa": getattr(p, "empresa", "?"), "dias": dias})
                    except Exception:
                        pass

            result = generar_briefing_matutino(
                aliado_nombre=getattr(aliado, "nombre", ""),
                aliado_ciudad=getattr(aliado, "ciudad", ""),
                aliado_pais=getattr(aliado, "pais", "AR"),
                aliado_rubros=rubros,
                leads_activos=len(prospectos),
                leads_sin_contacto_7d=sin_contacto[:5],
                pipeline_total_usd=pipeline_usd,
                tasa_cierre_historica=tasa,
            )

            if not result:
                return JSONResponse({"ok": False, "error": "JARVIS no disponible"}, status_code=503)

            return JSONResponse({"ok": True, "briefing": result})

        except Exception as e:
            print(f"[JARVIS DASHBOARD] Error en briefing: {e}", file=sys.stderr)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # ── Análisis de pipeline ──────────────────────────────────────────────────
    @app.post("/jarvis/dashboard/pipeline")
    def endpoint_pipeline(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Analiza la calidad real del pipeline del aliado."""
        try:
            rubros = []
            try:
                rubros_raw = getattr(aliado, "rubros_especialidad", "[]") or "[]"
                rubros = json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
            except Exception:
                pass

            ventas_list = getattr(aliado, "ventas", []) or []
            ventas_confirmadas = sum(1 for v in ventas_list if getattr(v, "confirmada", False))
            total = len(ventas_list)
            tasa = round(ventas_confirmadas / total, 2) if total > 0 else 0.0

            prospectos = getattr(aliado, "prospectos", []) or []
            from datetime import date
            hoy = date.today()
            pipeline_data = []
            for p in prospectos:
                ultimo = getattr(p, "ultimo_contacto", None)
                dias_ult = 0
                if ultimo:
                    try:
                        dias_ult = (hoy - (ultimo.date() if hasattr(ultimo, "date") else ultimo)).days
                    except Exception:
                        pass
                pipeline_data.append({
                    "empresa": getattr(p, "empresa", "?"),
                    "etapa": getattr(p, "estado", "prospecto"),
                    "valor_usd": float(getattr(p, "valor_estimado_usd", 0) or 0),
                    "ultimo_contacto_dias": dias_ult,
                    "tiene_propuesta": bool(getattr(p, "propuesta_enviada", False)),
                    "proxima_accion": getattr(p, "proxima_accion", "") or "",
                })

            result = analizar_pipeline(
                aliado_nombre=getattr(aliado, "nombre", ""),
                aliado_rubros=rubros,
                aliado_pais=getattr(aliado, "pais", "AR"),
                tasa_cierre_historica=tasa,
                pipeline=pipeline_data,
            )

            if not result:
                return JSONResponse({"ok": False, "error": "JARVIS no disponible"}, status_code=503)

            return JSONResponse({"ok": True, "analisis": result})

        except Exception as e:
            print(f"[JARVIS DASHBOARD] Error en pipeline: {e}", file=sys.stderr)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # ── Métricas JARVIS del aliado ────────────────────────────────────────────
    @app.get("/jarvis/dashboard/metricas")
    def endpoint_metricas(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Devuelve el JARVIS Score y métricas propietarias del aliado."""
        try:
            # Estos valores idealmente vienen de una tabla de uso en la DB.
            # Por ahora se calculan con los datos disponibles en el objeto aliado.
            result = calcular_metricas_jarvis(
                aliado_nombre=getattr(aliado, "nombre", ""),
                consultas_jarvis=getattr(aliado, "jarvis_consultas_mes", 0) or 0,
                propuestas_generadas=getattr(aliado, "jarvis_propuestas_generadas", 0) or 0,
            )

            if not result:
                return JSONResponse({"ok": False, "error": "JARVIS no disponible"}, status_code=503)

            return JSONResponse({"ok": True, "metricas": result})

        except Exception as e:
            print(f"[JARVIS DASHBOARD] Error en métricas: {e}", file=sys.stderr)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
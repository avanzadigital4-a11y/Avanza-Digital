"""
jarvis_memoria.py — Sistema de memoria de JARVIS (4 capas)

Implementa el modelo de memoria del Blueprint v2, Sección 4.

LAS 4 CAPAS:
  1. MEMORIA DE SESIÓN  — duración de la conversación (en RAM, no persiste)
  2. MEMORIA EPISÓDICA  — 90 días, conversaciones recientes y leads activos
  3. MEMORIA SEMÁNTICA  — permanente, ADN comercial del aliado
  4. MEMORIA COLECTIVA  — Flywheel anónimo (sección 6 del Blueprint — separado)

FUNCIONES PRINCIPALES:
  build_system_prompt()      → Construye el system prompt dinámico para Claude
  get_adn_aliado()           → Lee el perfil completo del aliado de la BD
  update_adn_aliado()        → Actualiza el ADN comercial con nuevos datos
  save_episodic_memory()     → Persiste una conversación o evento en la memoria episódica
  get_recent_context()       → Recupera contexto episódico de los últimos N días
  destilar_semana()          → Proceso de destilación semanal (corre domingo 3am vía APScheduler)
  get_jarvis_score()         → Score de uso de JARVIS del aliado (0-100)

MODELOS DE BD (migraciones necesarias):
  Aliado.jarvis_adn          → Text/JSON: ADN comercial estructurado
  Aliado.jarvis_estilo_perfil→ Text/JSON: perfil de estilo de escritura (usado por jarvis_comunicador)
  JarvisMemoriaEpisodica     → tabla nueva: eventos y conversaciones recientes
  JarvisPatronColectivo      → tabla nueva: contribuciones anónimas al Flywheel

DISEÑO:
  Mismo patrón defensivo del resto de JARVIS: si la IA o la BD falla, las funciones
  devuelven defaults seguros. El sistema nunca se cae por la memoria.
  El constructor de prompts funciona incluso con memoria vacía — solo es menos personalizado.
"""

from __future__ import annotations
import os, json, sys, hashlib
from datetime import datetime, timedelta
from typing import Optional, Any

# ─── CONFIG ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 20.0
EPISODIC_TTL_DAYS = 90   # días que vive la memoria episódica


def is_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


# ─── HELPER: llamada a Claude para destilación ────────────────────────────────

def _chat(
    prompt: str,
    system: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.2,
    json_mode: bool = False,
) -> Optional[str]:
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
        print(f"[JARVIS MEMORIA ERROR] {type(e).__name__}: {e}", file=sys.stderr)
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
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# CAPA 3 — ADN COMERCIAL DEL ALIADO (MEMORIA SEMÁNTICA PERMANENTE)
# ═══════════════════════════════════════════════════════════════════════════════

_ADN_DEFAULT: dict = {
    "sector_principal": "",
    "sectores_secundarios": [],
    "estilo_venta": "consultivo",         # directo | consultivo | técnico
    "tono_preferido": "profesional",      # formal | profesional | cercano
    "ciclo_promedio_dias": 0,
    "ticket_promedio_ars": 0,
    "tasa_cierre_historica": 0.0,
    "mejor_dia_contacto": "",
    "mejor_hora_contacto": "",
    "objeciones_frecuentes": [],          # las que más le aparecen
    "argumentos_mas_efectivos": [],       # los que más le funcionan
    "cierres_historicos_resumen": "",
    "alertas_aprendidas": [],             # cosas que históricamente no le funcionaron
    "leads_analizados_total": 0,
    "ultima_actualizacion": "",
}


def get_adn_aliado(aliado_obj) -> dict:
    """
    Lee el ADN comercial del aliado desde la BD.
    Si no existe o está vacío, devuelve el template por defecto.
    """
    try:
        raw = getattr(aliado_obj, "jarvis_adn", None) or "{}"
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            data = {}

        # Merge con el default — agrega campos nuevos sin pisar existentes
        adn = dict(_ADN_DEFAULT)
        adn.update(data)
        return adn
    except Exception as e:
        print(f"[JARVIS MEMORIA] Error leyendo ADN: {e}", file=sys.stderr)
        return dict(_ADN_DEFAULT)


def update_adn_aliado(
    aliado_obj,
    db_session,
    *,
    sector_principal: str = None,
    sectores_secundarios: list = None,
    estilo_venta: str = None,
    ciclo_promedio_dias: int = None,
    ticket_promedio_ars: float = None,
    objeciones_frecuentes: list = None,
    argumentos_mas_efectivos: list = None,
    cierres_historicos_resumen: str = None,
    alertas_aprendidas: list = None,
    leads_analizados_delta: int = 0,
    campos_extra: dict = None,
) -> bool:
    """
    Actualiza el ADN comercial del aliado en la BD.
    Solo actualiza los campos que se pasan (no pisa con None).
    Retorna True si guardó correctamente.
    """
    try:
        adn = get_adn_aliado(aliado_obj)

        if sector_principal is not None:
            adn["sector_principal"] = sector_principal
        if sectores_secundarios is not None:
            adn["sectores_secundarios"] = sectores_secundarios
        if estilo_venta is not None:
            adn["estilo_venta"] = estilo_venta
        if ciclo_promedio_dias is not None:
            adn["ciclo_promedio_dias"] = ciclo_promedio_dias
        if ticket_promedio_ars is not None:
            adn["ticket_promedio_ars"] = ticket_promedio_ars
        if objeciones_frecuentes is not None:
            adn["objeciones_frecuentes"] = objeciones_frecuentes
        if argumentos_mas_efectivos is not None:
            adn["argumentos_mas_efectivos"] = argumentos_mas_efectivos
        if cierres_historicos_resumen is not None:
            adn["cierres_historicos_resumen"] = cierres_historicos_resumen
        if alertas_aprendidas is not None:
            adn["alertas_aprendidas"] = alertas_aprendidas
        if leads_analizados_delta > 0:
            adn["leads_analizados_total"] = adn.get("leads_analizados_total", 0) + leads_analizados_delta
        if campos_extra:
            adn.update(campos_extra)

        adn["ultima_actualizacion"] = datetime.utcnow().isoformat()

        if hasattr(aliado_obj, "jarvis_adn"):
            aliado_obj.jarvis_adn = json.dumps(adn, ensure_ascii=False)
            db_session.commit()
            return True
        else:
            print(
                "[JARVIS MEMORIA] El modelo Aliado no tiene columna 'jarvis_adn'. "
                "Ejecutar la migración de BD primero.",
                file=sys.stderr
            )
            return False

    except Exception as e:
        print(f"[JARVIS MEMORIA] Error actualizando ADN: {e}", file=sys.stderr)
        try:
            db_session.rollback()
        except Exception:
            pass
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# CAPA 2 — MEMORIA EPISÓDICA (últimos 90 días)
# ═══════════════════════════════════════════════════════════════════════════════

def save_episodic_memory(
    aliado_id: int,
    tipo: str,
    contenido: dict,
    db_session,
    *,
    referencia_id: int = None,   # id del lead, prospecto o conversación relacionada
    referencia_tipo: str = None,  # "lead" | "prospecto" | "reunion" | "propuesta" | "chat"
    importancia: str = "normal",  # "alta" | "normal" | "baja"
) -> bool:
    """
    Persiste un evento en la memoria episódica del aliado.
    Los tipos más comunes:
      - "analisis_lead"   → resultado de analizar_lead_bolsa()
      - "propuesta"       → propuesta generada + a quién
      - "reunion"         → pre/post-reunión procesado
      - "cierre"          → venta confirmada
      - "objecion"        → objeción recibida y cómo se manejó
      - "chat_resumen"    → resumen de una sesión de chat con JARVIS
      - "comunicacion"    → email/WA/LinkedIn generado y enviado

    Retorna True si guardó, False si falló.
    """
    try:
        # Import dinámico para no romper si no están los modelos JARVIS aún
        from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
        from sqlalchemy.orm import Session

        contenido_str = json.dumps(contenido, ensure_ascii=False)
        ahora = datetime.utcnow()

        # Intentar usar el modelo de BD si existe
        try:
            from models import JarvisMemoriaEpisodica  # type: ignore
            evento = JarvisMemoriaEpisodica(
                aliado_id=aliado_id,
                tipo=tipo,
                contenido=contenido_str,
                referencia_id=referencia_id,
                referencia_tipo=referencia_tipo,
                importancia=importancia,
                creado_en=ahora,
                expira_en=ahora + timedelta(days=EPISODIC_TTL_DAYS),
            )
            db_session.add(evento)
            db_session.commit()
            return True
        except ImportError:
            # El modelo JarvisMemoriaEpisodica aún no existe — loguear y continuar
            print(
                "[JARVIS MEMORIA] Tabla JarvisMemoriaEpisodica no existe aún. "
                "Ejecutar migración de BD.",
                file=sys.stderr
            )
            return False

    except Exception as e:
        print(f"[JARVIS MEMORIA] Error guardando memoria episódica: {e}", file=sys.stderr)
        try:
            db_session.rollback()
        except Exception:
            pass
        return False


def get_recent_context(
    aliado_id: int,
    db_session,
    *,
    dias: int = 30,
    tipos: list[str] = None,
    importancia_minima: str = "normal",
    limite: int = 20,
) -> list[dict]:
    """
    Recupera eventos de la memoria episódica de los últimos N días.

    Retorna lista de dicts [{tipo, contenido, creado_en, importancia}] o [] si falla.
    """
    try:
        from models import JarvisMemoriaEpisodica  # type: ignore
        desde = datetime.utcnow() - timedelta(days=dias)

        query = db_session.query(JarvisMemoriaEpisodica).filter(
            JarvisMemoriaEpisodica.aliado_id == aliado_id,
            JarvisMemoriaEpisodica.creado_en >= desde,
        )

        if tipos:
            query = query.filter(JarvisMemoriaEpisodica.tipo.in_(tipos))

        imp_orden = {"alta": 0, "normal": 1, "baja": 2}
        if importancia_minima == "alta":
            query = query.filter(JarvisMemoriaEpisodica.importancia == "alta")

        eventos = query.order_by(
            JarvisMemoriaEpisodica.importancia.asc(),
            JarvisMemoriaEpisodica.creado_en.desc(),
        ).limit(limite).all()

        resultado = []
        for ev in eventos:
            try:
                contenido = json.loads(ev.contenido) if isinstance(ev.contenido, str) else ev.contenido
            except Exception:
                contenido = {"raw": str(ev.contenido)}
            resultado.append({
                "tipo": ev.tipo,
                "contenido": contenido,
                "creado_en": ev.creado_en.isoformat() if ev.creado_en else "",
                "importancia": ev.importancia,
                "referencia_tipo": ev.referencia_tipo,
            })
        return resultado

    except ImportError:
        return []
    except Exception as e:
        print(f"[JARVIS MEMORIA] Error leyendo memoria episódica: {e}", file=sys.stderr)
        return []


def _resumir_contexto_episodico(eventos: list[dict]) -> str:
    """Convierte la lista de eventos episódicos en un bloque de texto para el prompt."""
    if not eventos:
        return "Sin actividad reciente registrada."

    lineas = []
    for ev in eventos[:10]:  # Máximo 10 eventos para no inflar el prompt
        tipo = ev.get("tipo", "evento")
        creado = ev.get("creado_en", "")[:10]  # Solo la fecha
        importancia = ev.get("importancia", "normal")
        contenido = ev.get("contenido", {})

        # Extraer info clave del contenido según el tipo
        if tipo == "analisis_lead":
            empresa = contenido.get("empresa", "una empresa")
            score = contenido.get("score", "?")
            lineas.append(f"  [{creado}] Analizó lead: {empresa} (score {score}/100)")

        elif tipo == "propuesta":
            empresa = contenido.get("empresa_cliente", "un cliente")
            plan = contenido.get("plan", "Plan Avanza")
            lineas.append(f"  [{creado}] Generó propuesta para {empresa} ({plan})")

        elif tipo == "cierre":
            empresa = contenido.get("empresa_cliente", "un cliente")
            plan = contenido.get("plan", "")
            lineas.append(f"  [{creado}] 🟢 CIERRE: {empresa} — {plan}")

        elif tipo == "reunion":
            empresa = contenido.get("empresa_prospecto", "prospecto")
            temperatura = contenido.get("temperatura_lead", "")
            lineas.append(f"  [{creado}] Reunión con {empresa} — temperatura: {temperatura}")

        elif tipo == "comunicacion":
            tipo_com = contenido.get("tipo_comunicacion", "comunicación")
            empresa = contenido.get("empresa_prospecto", "un prospecto")
            lineas.append(f"  [{creado}] Generó {tipo_com} para {empresa}")

        elif tipo == "chat_resumen":
            resumen = contenido.get("resumen", "conversación con JARVIS")
            lineas.append(f"  [{creado}] Chat: {resumen[:80]}")

        else:
            resumen = str(contenido)[:80] if contenido else tipo
            lineas.append(f"  [{creado}] {tipo}: {resumen}")

    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTRUCTOR DINÁMICO DEL SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

def build_system_prompt(
    aliado_obj,
    db_session,
    *,
    sector_override: str = None,
    incluir_knowledge_graph: bool = True,
    incluir_contexto_episodico: bool = True,
    dias_contexto: int = 30,
    contexto_sesion: dict = None,
) -> str:
    """
    Construye el system prompt dinámico completo de JARVIS para un aliado.

    Estructura (5 capas del Blueprint):
      CAPA 1 — IDENTIDAD: quién es JARVIS para este aliado
      CAPA 2 — CONTEXTO DEL ALIADO: ADN comercial + datos del perfil
      CAPA 3 — CONOCIMIENTO SECTORIAL: Knowledge Graph del sector
      CAPA 4 — REGLAS DE OPERACIÓN: cómo debe comportarse
      CAPA 5 — CONTEXTO SITUACIONAL: memoria episódica + sesión actual

    El prompt se construye de forma defensiva — si falla alguna capa,
    las otras siguen funcionando.
    """
    # ── DATOS DEL ALIADO ─────────────────────────────────────────────────────
    aliado_nombre = getattr(aliado_obj, "nombre", "el aliado") or "el aliado"
    aliado_ciudad = getattr(aliado_obj, "ciudad", "Argentina") or "Argentina"
    aliado_pais   = getattr(aliado_obj, "pais", "AR") or "AR"
    aliado_nivel  = getattr(aliado_obj, "nivel", "BASIC") or "BASIC"
    aliado_tipo   = getattr(aliado_obj, "tipo_aliado", "canal1") or "canal1"

    paises = {"AR": "Argentina", "MX": "México", "CO": "Colombia",
              "CL": "Chile", "PE": "Perú", "UY": "Uruguay"}
    pais_nombre = paises.get(aliado_pais, aliado_pais)

    dialectos = {
        "AR": "español rioplatense",
        "MX": "español mexicano",
        "CO": "español colombiano",
        "CL": "español chileno",
        "PE": "español peruano",
        "UY": "español rioplatense",
    }
    dialecto = dialectos.get(aliado_pais, "español latinoamericano")

    try:
        rubros_raw = getattr(aliado_obj, "rubros_especialidad", "[]") or "[]"
        rubros = json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
    except Exception:
        rubros = []
    rubros_str = ", ".join(rubros) if rubros else "general"

    # ── ADN COMERCIAL ─────────────────────────────────────────────────────────
    adn = get_adn_aliado(aliado_obj)
    sector = sector_override or adn.get("sector_principal") or (rubros[0] if rubros else "")

    # ── PERFIL DE ESTILO (para comunicaciones) ────────────────────────────────
    try:
        estilo_raw = getattr(aliado_obj, "jarvis_estilo_perfil", "{}") or "{}"
        estilo = json.loads(estilo_raw) if isinstance(estilo_raw, str) else estilo_raw
    except Exception:
        estilo = {}

    estilo_desc = ""
    if estilo:
        partes = []
        if estilo.get("longitud_preferida"):
            partes.append(f"emails de ~{estilo['longitud_preferida']} palabras")
        if estilo.get("usa_emojis") is False:
            partes.append("sin emojis")
        elif estilo.get("usa_emojis") is True:
            partes.append("puede usar emojis con moderación")
        if estilo.get("evita"):
            partes.append(f"evita: {', '.join(estilo['evita'][:3])}")
        estilo_desc = "; ".join(partes) if partes else "sin preferencias específicas registradas"
    else:
        estilo_desc = "aún no registrado — usar tono profesional"

    # ── VENTAS CONFIRMADAS ────────────────────────────────────────────────────
    try:
        ventas_list = getattr(aliado_obj, "ventas", []) or []
        ventas_confirmadas = sum(1 for v in ventas_list if getattr(v, "confirmada", False))
    except Exception:
        ventas_confirmadas = 0

    # ── CAPA 3: KNOWLEDGE GRAPH ───────────────────────────────────────────────
    kg_block = ""
    if incluir_knowledge_graph and sector:
        try:
            from jarvis_knowledge import get_sector_prompt  # type: ignore
            kg_block = get_sector_prompt(sector)
        except ImportError:
            kg_block = f"Sector del aliado: {sector}. Knowledge Graph no disponible aún."
        except Exception as e:
            print(f"[JARVIS MEMORIA] Error cargando Knowledge Graph: {e}", file=sys.stderr)
            kg_block = ""

    # ── CAPA 5: CONTEXTO EPISÓDICO ────────────────────────────────────────────
    contexto_episodico_block = ""
    if incluir_contexto_episodico:
        try:
            eventos = get_recent_context(
                aliado_obj.id, db_session,
                dias=dias_contexto,
                importancia_minima="normal",
                limite=15,
            )
            contexto_episodico_block = _resumir_contexto_episodico(eventos)
        except Exception as e:
            print(f"[JARVIS MEMORIA] Error cargando contexto episódico: {e}", file=sys.stderr)
            contexto_episodico_block = "No se pudo cargar el contexto reciente."

    # ── CONTEXTO DE SESIÓN ────────────────────────────────────────────────────
    sesion_block = ""
    if contexto_sesion:
        partes = []
        if contexto_sesion.get("lead_activo"):
            partes.append(f"Lead activo en esta conversación: {contexto_sesion['lead_activo']}")
        if contexto_sesion.get("prospecto_activo"):
            partes.append(f"Prospecto en foco: {contexto_sesion['prospecto_activo']}")
        if contexto_sesion.get("objetivo"):
            partes.append(f"Objetivo de esta sesión: {contexto_sesion['objetivo']}")
        sesion_block = "\n".join(partes)

    # ── ADN EN TEXTO ─────────────────────────────────────────────────────────
    objeciones_top = ", ".join(adn.get("objeciones_frecuentes", [])[:3]) or "no registradas aún"
    argumentos_top = ", ".join(adn.get("argumentos_mas_efectivos", [])[:3]) or "no registrados aún"
    alertas = "\n  - ".join(adn.get("alertas_aprendidas", [])) if adn.get("alertas_aprendidas") else "ninguna"
    cierres_resumen = adn.get("cierres_historicos_resumen") or "sin historial de cierres registrado aún"

    # ── CONSTRUCCIÓN FINAL ────────────────────────────────────────────────────
    prompt = f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  CAPA 1 — IDENTIDAD                                                         ║
╚══════════════════════════════════════════════════════════════════════════════╝

Sos JARVIS, el sistema de inteligencia comercial de {aliado_nombre}.
No sos un chatbot. No sos un asistente genérico. Sos el copiloto comercial
específico de este aliado — conocés su historia, su sector y cómo vende.

Avanza Digital ofrece: presencia web B2B, SEO, generación de leads y sistemas
de marketing digital para PYMES industriales. Planes desde $1.050 a $7.500 ARS/mes.


╔══════════════════════════════════════════════════════════════════════════════╗
║  CAPA 2 — CONTEXTO DEL ALIADO (ADN COMERCIAL)                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

ALIADO: {aliado_nombre}
UBICACIÓN: {aliado_ciudad}, {pais_nombre}
NIVEL: {aliado_nivel} | TIPO: {aliado_tipo}
SECTORES: {rubros_str}
VENTAS CONFIRMADAS: {ventas_confirmadas}

PERFIL COMERCIAL:
  Sector principal: {sector or "no definido"}
  Estilo de venta: {adn.get('estilo_venta', 'consultivo')}
  Tono preferido: {adn.get('tono_preferido', 'profesional')}
  Ciclo promedio de cierre: {adn.get('ciclo_promedio_dias', 0) or 'no registrado'} días
  Ticket promedio: ${adn.get('ticket_promedio_ars', 0):,.0f} ARS
  Tasa de cierre histórica: {adn.get('tasa_cierre_historica', 0)*100:.0f}%
  Mejor día de contacto: {adn.get('mejor_dia_contacto') or 'no registrado'}
  Mejor hora de contacto: {adn.get('mejor_hora_contacto') or 'no registrado'}

OBJECIONES MÁS FRECUENTES QUE ENFRENTA: {objeciones_top}
ARGUMENTOS QUE MÁS LE FUNCIONAN: {argumentos_top}

HISTORIAL DE CIERRES:
  {cierres_resumen}

ALERTAS APRENDIDAS (cosas que históricamente NO le funcionaron):
  - {alertas}

PERFIL DE ESTILO DE ESCRITURA (para comunicaciones):
  {estilo_desc}


{(f"""╔══════════════════════════════════════════════════════════════════════════════╗
║  CAPA 3 — CONOCIMIENTO SECTORIAL                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝

{kg_block}
""") if kg_block else ""}

╔══════════════════════════════════════════════════════════════════════════════╗
║  CAPA 4 — REGLAS DE OPERACIÓN                                               ║
╚══════════════════════════════════════════════════════════════════════════════╝

1. NUNCA respondas de forma genérica. Si no tenés contexto del aliado, decílo
   explícitamente en lugar de inventar.

2. SIEMPRE identificá el estado del aliado en cada mensaje:
   - PROSPECCIÓN: está buscando nuevos leads o armando contactos
   - NEGOCIACIÓN: está en conversación activa con un prospecto
   - CIERRE: está por cerrar o presionando para que firmen
   - POST-VENTA: está atendiendo a un cliente activo
   - ADMINISTRACIÓN: está gestionando propuestas, CRM, seguimientos

3. CONFIANZA DECLARADA en cada respuesta que incluya datos:
   🟢 Contexto propio — basado en datos confirmados del aliado
   🟡 Contexto sectorial — basado en el Knowledge Graph
   🔵 Contexto red — basado en patrones anónimos de aliados similares
   🔴 Contexto general — JARVIS declara que no tiene datos suficientes

4. OPORTUNIDADES PROACTIVAS: Si detectás una oportunidad que el aliado no vio,
   mencionála brevemente al final de la respuesta como "💡 JARVIS detectó:"

5. ALERTAS: Si el aliado está haciendo algo que históricamente no le funcionó,
   advertílo ANTES de responder al pedido.

6. IDIOMA: {dialecto}. Adaptá el registro al tono del aliado — si escribe
   relajado, respondé sin formalidades; si escribe formal, respondé formal.

7. LONGITUD DE RESPUESTA: Calibrá según la complejidad del pedido. Para preguntas
   simples, 2-3 líneas. Para análisis complejos, estructura clara con secciones.

8. ACCIONES EMBEBIDAS: Cuando generés un email, propuesta u otro documento,
   terminá con una sección "ACCIONES SUGERIDAS" con pasos concretos inmediatos.


{(f"""╔══════════════════════════════════════════════════════════════════════════════╗
║  CAPA 5 — CONTEXTO SITUACIONAL                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

ACTIVIDAD RECIENTE DEL ALIADO (últimos {dias_contexto} días):
{contexto_episodico_block}

{f"SESIÓN ACTUAL:{chr(10)}{sesion_block}" if sesion_block else ""}
""") if contexto_episodico_block else ""}
""".strip()

    return prompt


# ═══════════════════════════════════════════════════════════════════════════════
# DESTILACIÓN SEMANAL (corre domingo 3am vía APScheduler)
# ═══════════════════════════════════════════════════════════════════════════════

def destilar_semana(aliado_id: int, db_session) -> Optional[dict]:
    """
    Proceso de destilación semanal para un aliado.
    Lee los eventos de los últimos 7 días y actualiza el ADN comercial.

    Corre automáticamente vía APScheduler. También se puede invocar manualmente.

    Retorna el dict de cambios detectados o None si falló.
    """
    if not is_enabled():
        return None

    try:
        from models import Aliado  # type: ignore
        aliado = db_session.query(Aliado).filter(Aliado.id == aliado_id).first()
        if not aliado:
            return None

        # Leer eventos de los últimos 7 días
        eventos = get_recent_context(
            aliado_id, db_session,
            dias=7,
            importancia_minima="baja",
            limite=50,
        )

        if not eventos:
            return None

        # Preparar el resumen de eventos para la destilación
        adn_actual = get_adn_aliado(aliado)
        eventos_str = _resumir_contexto_episodico(eventos)

        prompt = f"""
Analizá los eventos de la última semana de este aliado y actualizá su perfil comercial.

ADN ACTUAL DEL ALIADO:
{json.dumps(adn_actual, ensure_ascii=False, indent=2)}

EVENTOS DE LA ÚLTIMA SEMANA:
{eventos_str}

Basándote en los eventos, determiná si alguno de estos campos debe actualizarse:
- ciclo_promedio_dias: ¿los cierres de la semana sugieren un ciclo diferente?
- objeciones_frecuentes: ¿apareció una objeción nueva o se repitió alguna?
- argumentos_mas_efectivos: ¿qué argumentos se usaron en cierres exitosos?
- alertas_aprendidas: ¿algo que el aliado hizo que claramente no funcionó?
- mejor_dia_contacto / mejor_hora_contacto: ¿hay datos de esta semana?
- cierres_historicos_resumen: ¿hubo cierres esta semana para agregar al resumen?

Solo actualizá lo que hay evidencia real para actualizar.
Si no hay evidencia de un cambio, dejá el campo como null.

Devolvé este JSON:
{{
  "cambios_detectados": true/false,
  "resumen_semana": "<qué pasó esta semana en el negocio del aliado — 2-3 líneas>",
  "actualizaciones": {{
    "ciclo_promedio_dias": <número o null>,
    "objeciones_frecuentes": ["<objeción>" ...] o null,
    "argumentos_mas_efectivos": ["<argumento>" ...] o null,
    "alertas_aprendidas": ["<alerta>" ...] o null,
    "mejor_dia_contacto": "<día>" o null,
    "cierres_historicos_resumen": "<resumen actualizado>" o null
  }},
  "insight_para_briefing": "<algo interesante que JARVIS aprendió esta semana — para el briefing del lunes>",
  "prioridades_proxima_semana": [
    "<prioridad 1 basada en el estado actual del pipeline>",
    "<prioridad 2>",
    "<prioridad 3>"
  ]
}}
"""

        system = """Sos el Sistema de Destilación de Memoria de JARVIS para Avanza Digital.
Analizás la actividad semanal de un aliado y actualizás su perfil comercial.
Solo actualizás con evidencia real — no inventás cambios.
Respondé ÚNICAMENTE con JSON válido."""

        raw = _chat(prompt, system, max_tokens=1200, temperature=0.2, json_mode=True)
        resultado = _parse_json(raw) if raw else None

        if resultado and resultado.get("cambios_detectados") and resultado.get("actualizaciones"):
            act = resultado["actualizaciones"]

            update_adn_aliado(
                aliado, db_session,
                ciclo_promedio_dias=act.get("ciclo_promedio_dias"),
                objeciones_frecuentes=act.get("objeciones_frecuentes"),
                argumentos_mas_efectivos=act.get("argumentos_mas_efectivos"),
                alertas_aprendidas=act.get("alertas_aprendidas"),
                cierres_historicos_resumen=act.get("cierres_historicos_resumen"),
                campos_extra=(
                    {"mejor_dia_contacto": act["mejor_dia_contacto"]}
                    if act.get("mejor_dia_contacto") else {}
                ),
            )

        return resultado

    except Exception as e:
        print(f"[JARVIS MEMORIA] Error en destilación semanal aliado {aliado_id}: {e}", file=sys.stderr)
        return None


def destilar_todos(db_session) -> dict:
    """
    Ejecuta la destilación semanal para TODOS los aliados activos.
    Pensado para el scheduler del domingo a las 3am.

    Retorna un resumen de cuántos aliados se procesaron.
    """
    try:
        from models import Aliado  # type: ignore
        aliados = db_session.query(Aliado).filter(Aliado.activo == True).all()
    except Exception as e:
        print(f"[JARVIS MEMORIA] Error cargando aliados para destilación: {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}

    procesados = 0
    errores = 0

    for aliado in aliados:
        try:
            resultado = destilar_semana(aliado.id, db_session)
            if resultado:
                procesados += 1
        except Exception:
            errores += 1

    print(f"[JARVIS MEMORIA] Destilación completada: {procesados} aliados procesados, {errores} errores.")
    return {
        "ok": True,
        "procesados": procesados,
        "errores": errores,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE DE USO DE JARVIS
# ═══════════════════════════════════════════════════════════════════════════════

def get_jarvis_score(aliado_id: int, db_session) -> dict:
    """
    Calcula el JARVIS Score del aliado (0-100).
    Mide qué tan bien está usando JARVIS: uso, calidad de datos, follow-up.

    Retorna:
        {
          "score": int,
          "nivel": str,
          "dimensiones": dict,
          "recomendaciones": list[str],
        }
    """
    try:
        adn = None
        try:
            from models import Aliado  # type: ignore
            aliado = db_session.query(Aliado).filter(Aliado.id == aliado_id).first()
            if aliado:
                adn = get_adn_aliado(aliado)
        except Exception:
            pass

        # Eventos de los últimos 30 días
        eventos = get_recent_context(aliado_id, db_session, dias=30, limite=50)

        # ── Dimensión 1: Actividad (30 pts) ───────────────────────────────────
        n_eventos = len(eventos)
        if n_eventos >= 20:
            pts_actividad = 30
        elif n_eventos >= 10:
            pts_actividad = 20
        elif n_eventos >= 5:
            pts_actividad = 10
        else:
            pts_actividad = max(0, n_eventos * 2)

        # ── Dimensión 2: Completitud del ADN (25 pts) ─────────────────────────
        pts_adn = 0
        if adn:
            campos_relevantes = [
                "sector_principal", "estilo_venta", "ciclo_promedio_dias",
                "objeciones_frecuentes", "argumentos_mas_efectivos",
            ]
            for campo in campos_relevantes:
                val = adn.get(campo)
                if val and val != 0 and val != [] and val != "":
                    pts_adn += 5

        # ── Dimensión 3: Variedad de módulos usados (25 pts) ──────────────────
        tipos_usados = set(ev.get("tipo", "") for ev in eventos)
        modulos_objetivo = {"analisis_lead", "propuesta", "reunion", "comunicacion", "chat_resumen"}
        overlap = len(tipos_usados & modulos_objetivo)
        pts_variedad = min(25, overlap * 5)

        # ── Dimensión 4: Follow-up (20 pts) ───────────────────────────────────
        # Si hay cierres → máximo puntaje. Si hay leads analizados → parcial.
        cierres = sum(1 for ev in eventos if ev.get("tipo") == "cierre")
        leads   = sum(1 for ev in eventos if ev.get("tipo") == "analisis_lead")
        if cierres >= 2:
            pts_followup = 20
        elif cierres == 1:
            pts_followup = 15
        elif leads >= 5:
            pts_followup = 10
        elif leads >= 1:
            pts_followup = 5
        else:
            pts_followup = 0

        score_total = pts_actividad + pts_adn + pts_variedad + pts_followup

        # Nivel
        if score_total >= 80:
            nivel = "ELITE"
        elif score_total >= 60:
            nivel = "ACTIVO"
        elif score_total >= 40:
            nivel = "INICIANDO"
        else:
            nivel = "INACTIVO"

        # Recomendaciones
        recomendaciones = []
        if pts_actividad < 15:
            recomendaciones.append("Usar JARVIS más regularmente — el sistema aprende con el uso")
        if pts_adn < 15:
            recomendaciones.append("Completar el perfil comercial: sector, estilo de venta, ciclo de cierre")
        if pts_variedad < 15:
            recomendaciones.append("Explorar más módulos: copiloto de reuniones, comunicador, mercado")
        if pts_followup < 10:
            recomendaciones.append("Registrar cierres y resultados — el ADN mejora con cada cierre confirmado")

        return {
            "score": score_total,
            "nivel": nivel,
            "dimensiones": {
                "actividad":  {"pts": pts_actividad, "max": 30},
                "perfil_adn": {"pts": pts_adn,       "max": 25},
                "variedad":   {"pts": pts_variedad,  "max": 25},
                "follow_up":  {"pts": pts_followup,  "max": 20},
            },
            "recomendaciones": recomendaciones,
        }

    except Exception as e:
        print(f"[JARVIS MEMORIA] Error calculando JARVIS Score: {e}", file=sys.stderr)
        return {
            "score": 0,
            "nivel": "INACTIVO",
            "dimensiones": {},
            "recomendaciones": ["Error calculando el score — revisá la configuración"],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MIGRACIÓN DE BASE DE DATOS
# ═══════════════════════════════════════════════════════════════════════════════

MIGRATION_SQL = """
-- ──────────────────────────────────────────────────────────────────────────────
-- MIGRACIÓN JARVIS MEMORIA — ejecutar UNA sola vez
-- Compatible con PostgreSQL (Supabase) y SQLite
-- ──────────────────────────────────────────────────────────────────────────────

-- 1. Columnas nuevas en la tabla aliados

ALTER TABLE aliados ADD COLUMN IF NOT EXISTS jarvis_adn TEXT DEFAULT '{}';
ALTER TABLE aliados ADD COLUMN IF NOT EXISTS jarvis_estilo_perfil TEXT DEFAULT '{}';

-- 2. Tabla de memoria episódica

CREATE TABLE IF NOT EXISTS jarvis_memoria_episodica (
    id               SERIAL PRIMARY KEY,
    aliado_id        INTEGER NOT NULL REFERENCES aliados(id) ON DELETE CASCADE,
    tipo             VARCHAR(50) NOT NULL,
    contenido        TEXT NOT NULL,
    referencia_id    INTEGER,
    referencia_tipo  VARCHAR(50),
    importancia      VARCHAR(20) DEFAULT 'normal',
    creado_en        TIMESTAMP DEFAULT NOW(),
    expira_en        TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jarvis_episodica_aliado ON jarvis_memoria_episodica (aliado_id, creado_en DESC);
CREATE INDEX IF NOT EXISTS idx_jarvis_episodica_tipo   ON jarvis_memoria_episodica (aliado_id, tipo);
CREATE INDEX IF NOT EXISTS idx_jarvis_episodica_expira ON jarvis_memoria_episodica (expira_en);

-- 3. Tabla del Flywheel Colectivo (anónima)

CREATE TABLE IF NOT EXISTS jarvis_patron_colectivo (
    id               SERIAL PRIMARY KEY,
    sector           VARCHAR(50) NOT NULL,
    pais             VARCHAR(5)  NOT NULL DEFAULT 'AR',
    tipo_patron      VARCHAR(50) NOT NULL,
    patron_hash      VARCHAR(64),                   -- hash del contenido para deduplicar
    data             TEXT NOT NULL,                 -- JSON del patrón anonimizado
    contribuciones   INTEGER DEFAULT 1,
    creado_en        TIMESTAMP DEFAULT NOW(),
    actualizado_en   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_patron_sector ON jarvis_patron_colectivo (sector, tipo_patron);

-- 4. Limpieza automática de memoria episódica vencida (ejecutar periódicamente)

-- DELETE FROM jarvis_memoria_episodica WHERE expira_en < NOW();
"""


def get_migration_sql() -> str:
    """Retorna el SQL de migración listo para ejecutar en la BD."""
    return MIGRATION_SQL


def run_migration(db_session) -> bool:
    """
    Ejecuta la migración de BD desde Python.
    Útil para correr al iniciar la app si las tablas no existen.
    Retorna True si completó sin errores fatales.
    """
    try:
        from sqlalchemy import text
        statements = [
            s.strip()
            for s in MIGRATION_SQL.split(";")
            if s.strip() and not s.strip().startswith("--")
        ]
        for stmt in statements:
            if stmt:
                try:
                    db_session.execute(text(stmt))
                except Exception as e:
                    # Errores de "ya existe" son esperables y no son fatales
                    if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                        continue
                    print(f"[JARVIS MEMORIA] Advertencia en migración: {e}", file=sys.stderr)
        db_session.commit()
        print("[JARVIS MEMORIA] Migración completada.")
        return True
    except Exception as e:
        print(f"[JARVIS MEMORIA] Error en migración: {e}", file=sys.stderr)
        try:
            db_session.rollback()
        except Exception:
            pass
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# RUTAS FASTAPI — register()
# ═══════════════════════════════════════════════════════════════════════════════

def register(app, get_db_func, auth_dep):
    """
    Registra los endpoints de memoria de JARVIS en la app FastAPI.

    Llamar desde main.py:
        import jarvis_memoria
        jarvis_memoria.register(app, get_db, current_aliado_required)
    """
    import json as _json
    from fastapi import Depends, HTTPException
    from sqlalchemy.orm import Session
    from pydantic import BaseModel

    class UpdateAdnReq(BaseModel):
        sector_principal: str = None
        estilo_venta: str = None
        ciclo_promedio_dias: int = None
        ticket_promedio_ars: float = None
        objeciones_frecuentes: list = None
        argumentos_mas_efectivos: list = None

    class GuardarEventoReq(BaseModel):
        tipo: str
        contenido: dict
        referencia_id: int = None
        referencia_tipo: str = None
        importancia: str = "normal"

    @app.get("/jarvis/memoria/adn")
    def ep_get_adn(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Retorna el ADN comercial completo del aliado."""
        return {"ok": True, "adn": get_adn_aliado(aliado)}

    @app.post("/jarvis/memoria/adn")
    def ep_update_adn(
        body: UpdateAdnReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Actualiza campos del ADN comercial del aliado."""
        ok = update_adn_aliado(
            aliado, db,
            sector_principal=body.sector_principal,
            estilo_venta=body.estilo_venta,
            ciclo_promedio_dias=body.ciclo_promedio_dias,
            ticket_promedio_ars=body.ticket_promedio_ars,
            objeciones_frecuentes=body.objeciones_frecuentes,
            argumentos_mas_efectivos=body.argumentos_mas_efectivos,
        )
        if not ok:
            raise HTTPException(500, "No se pudo actualizar el ADN — verificar migración de BD")
        return {"ok": True, "adn": get_adn_aliado(aliado)}

    @app.post("/jarvis/memoria/evento")
    def ep_guardar_evento(
        body: GuardarEventoReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Guarda un evento en la memoria episódica del aliado."""
        ok = save_episodic_memory(
            aliado_id=aliado.id,
            tipo=body.tipo,
            contenido=body.contenido,
            db_session=db,
            referencia_id=body.referencia_id,
            referencia_tipo=body.referencia_tipo,
            importancia=body.importancia,
        )
        return {"ok": ok}

    @app.get("/jarvis/memoria/contexto")
    def ep_get_contexto(
        dias: int = 30,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Retorna el contexto episódico reciente del aliado."""
        eventos = get_recent_context(aliado.id, db, dias=min(dias, 90))
        return {"ok": True, "eventos": eventos, "total": len(eventos)}

    @app.get("/jarvis/memoria/score")
    def ep_jarvis_score(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Retorna el JARVIS Score del aliado (0-100)."""
        score = get_jarvis_score(aliado.id, db)
        return {"ok": True, **score}

    @app.post("/jarvis/memoria/destilar")
    def ep_destilar(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Fuerza una destilación inmediata para el aliado actual.
        Útil para testing — en producción corre automáticamente los domingos.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        resultado = destilar_semana(aliado.id, db)
        if resultado is None:
            raise HTTPException(502, "No se pudo destilar — verificar actividad reciente y API key")
        return {"ok": True, "destilacion": resultado}

    @app.get("/jarvis/memoria/migration-sql")
    def ep_migration_sql(
        aliado=Depends(auth_dep),
    ):
        """
        Retorna el SQL de migración necesario para activar el sistema de memoria.
        Ejecutar UNA sola vez en la BD de producción.
        """
        return {
            "ok": True,
            "instruccion": "Ejecutar este SQL en la base de datos de Supabase/Postgres una sola vez",
            "sql": get_migration_sql(),
        }
"""
jarvis_emocional.py — Capa de inteligencia emocional de JARVIS.
Sección 7 del Blueprint v2.

DISEÑO:
  - Detección de estado emocional del ALIADO a partir de su texto
  - Análisis de temperatura y señales del PROSPECTO (emails / mensajes pegados)
  - Ajuste de tono y formato de respuesta según el estado detectado
  - No lanza excepciones: siempre devuelve un resultado (puede ser "neutral" / vacío)

CLASES PRINCIPALES:
  EstadoAliado      → dataclass con resultado del análisis del aliado
  AnalisisProspecto → dataclass con resultado del análisis del prospecto
  JarvisEmocional   → motor principal (dos métodos públicos: analizar_aliado, analizar_prospecto)

USO DESDE jarvis_routes.py:
  from jarvis_emocional import JarvisEmocional, ajustar_tono_respuesta
  estado = JarvisEmocional.analizar_aliado(mensaje_aliado)
  ajuste = ajustar_tono_respuesta(estado)
"""

from __future__ import annotations

import os
import re
import sys
import json
from dataclasses import dataclass, field
from typing import Optional, Literal
from datetime import datetime, timedelta

# ─── CONFIG ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
TIMEOUT           = 12.0

# ─── TIPOS ───────────────────────────────────────────────────────────────────

EstadoAnim = Literal[
    "frustracion",      # "no sé cómo manejar esto", bloqueo
    "energia_alta",     # "¡cerré el deal!", confianza
    "decepcion",        # perdió propuesta, cliente rechazó
    "urgencia",         # respuestas cortas, "necesito ya"
    "agotamiento",      # respuestas monosilábicas, inactividad prolongada
    "neutro",           # estado base, sin señal emocional clara
    "coaching_needed",  # pide ayuda explícita, señales de duda profunda
]

TemperaturaLead = Literal[
    "caliente",   # señales claras de compra, urge cerrar
    "tibio",      # interés, pero sin urgencia
    "frio",       # respuestas evasivas, sin preguntas
    "perdido",    # señales de fuga, decisión ya tomada en otro lado
    "indefinido", # no hay suficiente información
]


# ─── DATACLASSES ─────────────────────────────────────────────────────────────

@dataclass
class EstadoAliado:
    estado: EstadoAnim = "neutro"
    confianza: float = 0.0            # 0.0 – 1.0
    señales_detectadas: list[str] = field(default_factory=list)
    ajuste_tono: str = ""             # instrucción para JARVIS al responder
    ajuste_formato: str = ""          # "corto", "paso_a_paso", "directo", "empático"
    oportunidad_momentum: bool = False # True → aprovechar energía para sugerir más acción
    fuente: str = "heuristica"        # "heuristica" | "claude"


@dataclass
class AnalisisProspecto:
    temperatura: TemperaturaLead = "indefinido"
    confianza: float = 0.0
    urgencia_real: bool = False          # dice "no urgente" pero pregunta por plazos
    señales_compra: list[str] = field(default_factory=list)
    señales_fuga: list[str] = field(default_factory=list)
    poder_decision: Literal["decide", "consulta", "indefinido"] = "indefinido"
    resumen_ejecutivo: str = ""          # 1–2 líneas para mostrar en el portal
    accion_recomendada: str = ""
    fuente: str = "heuristica"


# ─── SEÑALES HEURÍSTICAS ─────────────────────────────────────────────────────

_SEÑALES_FRUSTRACION = [
    r"no s[eé] (cómo|como|que|qué)",
    r"(estoy|está|me) (bloqueado|perdido|trabado|atascado)",
    r"no (puedo|logro|entiendo|sé)",
    r"(esto|eso) no (funciona|sirve|va)",
    r"no (me sale|me resulta|me cierra)",
    r"(qué|que) hago",
    r"ayuda(me)?",
    r"(no|nunca) (me) (compra|responde|contesta)",
]

_SEÑALES_ENERGIA = [
    r"(cerré|cerramos|firmé|firmamos)",
    r"(¡|!).*?(bien|genial|perfecto|excelente|lo logré)",
    r"(salió|salió|salió) (bien|genial|perfecto)",
    r"(deal|cliente|contrato).{0,20}(cerrado|firmado|confirmado)",
    r"(¡sí|si!|¡lo logré|lo logramos)",
    r"(metalpro|frigorífico|agro).{0,30}(cerr|firm|confirm)",
]

_SEÑALES_DECEPCION = [
    r"(perdí|perdimos|perdi).{0,30}(propuesta|cliente|deal|licitación)",
    r"(no (me|nos) eligieron|eligieron a otro|se fue con)",
    r"(rechazaron|descartaron|no (les|les) interesó)",
    r"(quedé|salí) afuera",
    r"fallé|fallo",
]

_SEÑALES_URGENCIA = [
    r"(urgente|urgent|ya|ahora|rápido|rapido|rapi)",
    r"(necesito|quiero).{0,15}(ya|ahora|hoy|pronto)",
    r"(me lo mand|mandame|enviame).{0,15}(ya|ahora|rápido)",
    r"(cuanto antes|lo antes posible|asap)",
]

_SEÑALES_AGOTAMIENTO = [
    r"^(ok|sí|si|dale|gracias|bueno)\.?$",
    r"^.{1,20}$",   # respuesta muy corta
]

_SEÑALES_COACHING = [
    r"(cómo|como) (le|les|lo) (digo|explico|presento|muestro|convenzo|vendo)",
    r"(qué|que) (le|les) (digo|pregunto|ofrezco)",
    r"(no sé|no se) (cómo|como) (arrancar|empezar|seguir|avanzar|presentar)",
    r"(me ayudás|me ayudas|ayudame) a",
    r"(qué|que) harías (vos|tú|tu) (en|si)",
]

# ── Señales prospecto ────────────────────────────────────────────────────────

_SC_COMPRA = [
    r"(cuándo|cuando).{0,30}(empezamos|arrancan|comienzan|implementan)",
    r"(cómo|como).{0,20}(funciona|sería|es el proceso|se hace)",
    r"(necesita|necesitamos).{0,20}(factura|remito|contrato|acuerdo|po|orden)",
    r"(quién|quien).{0,20}(contacto|habla|coordina|implementa)",
    r"(pueden|podemos).{0,20}(empezar|arrancar|avanzar|firmar)",
    r"(mi (jefe|gerente|director|cfo)).{0,30}(vio|revisó|le parece|dice)",
    r"(presupuesto|budget).{0,20}(aprobado|disponible|hay)",
]

_SC_FUGA = [
    r"(lo consulto|lo hablo|lo veo).{0,20}(con|a)",
    r"(después|despues|más adelante|en otro momento)",
    r"(estamos|estoy).{0,20}(evaluando|analizando|viendo).{0,30}(otras|otros|opciones|alternativas)",
    r"(necesito|necesitamos).{0,20}(pensarlo|pensarla|definir|decidir)",
    r"(por ahora|por el momento).{0,20}no",
    r"(se lo (paso|mando|envío)).{0,20}(a mi|al|jefe|gerente|director)",
    r"(no (es|va a ser)).{0,20}(posible|viable|el momento)",
]

_SC_URGENCIA_REAL = [
    r"(cuándo|cuando).{0,15}(podría|puede|pueden).{0,15}(estar|tenerlo|entregar)",
    r"(plazo|fecha).{0,20}(límite|tope|máxima|entrega)",
    r"(necesitamos).{0,15}(para|antes).{0,15}(el|del|de)",
    r"(para (ayer|la semana que viene|el lunes|antes del))",
]

_SC_DECIDE = [
    r"(yo (decido|firmo|apruebo|autorizo))",
    r"(la decisión (es mía|la tomo yo|depende de mí))",
    r"(soy (el responsable|quien decide|quien aprueba))",
    r"(gerente general|ceo|dueño|socio).{0,10}(soy|hablo)",
]

_SC_CONSULTA = [
    r"(tengo que (consultar|hablar|ver).{0,20}con)",
    r"(no (puedo|podría) (decidir|firmar|aprobar).{0,20}solo)",
    r"(hay (un|una|otro) (comité|directorio|junta|socio))",
    r"(mi (jefe|gerente|director|socio)).{0,20}(tiene que|debe|va a)",
]


# ─── HELPERS HEURÍSTICOS ─────────────────────────────────────────────────────

def _match_any(text: str, patterns: list[str]) -> list[str]:
    """Retorna las señales que matchean."""
    text_lower = text.lower()
    encontradas = []
    for pat in patterns:
        if re.search(pat, text_lower):
            encontradas.append(pat)
    return encontradas


def _estado_heuristico(texto: str) -> EstadoAliado:
    """
    Detección rápida (sin IA) basada en patrones regex.
    Devuelve el estado más probable.
    """
    puntos: dict[EstadoAnim, int] = {
        "frustracion": 0,
        "energia_alta": 0,
        "decepcion": 0,
        "urgencia": 0,
        "agotamiento": 0,
        "coaching_needed": 0,
        "neutro": 0,
    }

    señales_map: list[tuple[EstadoAnim, list[str]]] = [
        ("frustracion",    _SEÑALES_FRUSTRACION),
        ("energia_alta",   _SEÑALES_ENERGIA),
        ("decepcion",      _SEÑALES_DECEPCION),
        ("urgencia",       _SEÑALES_URGENCIA),
        ("agotamiento",    _SEÑALES_AGOTAMIENTO),
        ("coaching_needed", _SEÑALES_COACHING),
    ]

    todas_señales: list[str] = []
    for estado_nombre, patrones in señales_map:
        matches = _match_any(texto, patrones)
        if matches:
            puntos[estado_nombre] += len(matches) * 2
            todas_señales.extend([f"[{estado_nombre}] {m}" for m in matches])

    # Desempate: agotamiento solo si el texto es muy corto
    if len(texto.strip()) > 30 and puntos["agotamiento"] > 0:
        puntos["agotamiento"] = 0

    estado_final: EstadoAnim = max(puntos, key=lambda k: puntos[k])
    max_puntos = puntos[estado_final]

    if max_puntos == 0:
        estado_final = "neutro"

    confianza = min(1.0, max_puntos / 6.0)

    return EstadoAliado(
        estado=estado_final,
        confianza=confianza,
        señales_detectadas=todas_señales[:5],
        fuente="heuristica",
    )


def _prospecto_heuristico(texto: str) -> AnalisisProspecto:
    """
    Análisis de prospecto sin IA.
    """
    señales_compra  = _match_any(texto, _SC_COMPRA)
    señales_fuga    = _match_any(texto, _SC_FUGA)
    urgencia_real   = bool(_match_any(texto, _SC_URGENCIA_REAL))
    decide_matches  = _match_any(texto, _SC_DECIDE)
    consulta_matches= _match_any(texto, _SC_CONSULTA)

    # Temperatura
    if len(señales_compra) >= 2 and len(señales_fuga) == 0:
        temperatura: TemperaturaLead = "caliente"
        confianza = 0.75
    elif len(señales_compra) >= 1 and len(señales_fuga) <= 1:
        temperatura = "tibio"
        confianza = 0.60
    elif len(señales_fuga) >= 2:
        temperatura = "perdido"
        confianza = 0.65
    elif len(señales_fuga) >= 1:
        temperatura = "frio"
        confianza = 0.55
    else:
        temperatura = "indefinido"
        confianza = 0.30

    # Poder de decisión
    if decide_matches:
        poder: Literal["decide", "consulta", "indefinido"] = "decide"
    elif consulta_matches:
        poder = "consulta"
    else:
        poder = "indefinido"

    # Resumen
    resumen_map = {
        "caliente": "🔥 Lead caliente — señales claras de avance. Cerrá pronto.",
        "tibio":    "🟡 Lead tibio — interés real pero sin urgencia. Generá urgencia legítima.",
        "frio":     "🔵 Lead frío — respuestas evasivas. Revisá el enfoque.",
        "perdido":  "🔴 Lead en fuga — posible decisión tomada en otro lado. Actuá ahora o dejá ir.",
        "indefinido": "⚪ Sin datos suficientes. Pedí más contexto al aliado.",
    }

    accion_map = {
        "caliente": "Enviá propuesta + cierre esta semana.",
        "tibio":    "Generá urgencia legítima: caso de éxito + fecha límite real.",
        "frio":     "Cambiá el canal o el interlocutor. Revisá si es el decisor.",
        "perdido":  "Llamada directa. Si no hay respuesta en 48hs, pausá el lead.",
        "indefinido": "Pedí al aliado más contexto del intercambio.",
    }

    return AnalisisProspecto(
        temperatura=temperatura,
        confianza=confianza,
        urgencia_real=urgencia_real,
        señales_compra=[s.split("] ")[1] for s in señales_compra[:3]],
        señales_fuga=[s for s in señales_fuga[:3]],
        poder_decision=poder,
        resumen_ejecutivo=resumen_map[temperatura],
        accion_recomendada=accion_map[temperatura],
        fuente="heuristica",
    )


# ─── LLAMADA A CLAUDE (opcional, más precisa) ─────────────────────────────────

def _chat_claude(system: str, prompt: str) -> Optional[str]:
    """Llama a Claude para análisis emocional. Devuelve None si falla."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=JARVIS_MODEL,
            max_tokens=512,
            system=system + "\n\nIMPORTANTE: Respondé ÚNICAMENTE con JSON válido. Sin texto extra.",
            messages=[{"role": "user", "content": prompt}],
            timeout=TIMEOUT,
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"[JARVIS EMOCIONAL] Claude error: {e}", file=sys.stderr)
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


# ─── MOTOR PRINCIPAL ──────────────────────────────────────────────────────────

class JarvisEmocional:
    """
    Motor de inteligencia emocional de JARVIS.
    Todos los métodos son estáticos y no guardan estado.
    """

    # ── 1. ANÁLISIS DEL ALIADO ────────────────────────────────────────────────

    @staticmethod
    def analizar_aliado(
        texto: str,
        historial_sesion: Optional[list[dict]] = None,
        dias_sin_ingresar: int = 0,
        usar_claude: bool = True,
    ) -> EstadoAliado:
        """
        Detecta el estado emocional del aliado a partir de su texto.

        Args:
            texto:               Mensaje actual del aliado.
            historial_sesion:    Últimas N interacciones de la sesión (opcional).
            dias_sin_ingresar:   Días desde el último login del aliado.
            usar_claude:         Si True, usa Claude para análisis más preciso.

        Returns:
            EstadoAliado con estado, confianza y ajustes de tono.
        """
        # Señal extra: días sin ingresar
        base = _estado_heuristico(texto)

        if dias_sin_ingresar >= 3 and base.estado == "neutro":
            base.estado = "agotamiento"
            base.confianza = 0.5
            base.señales_detectadas.append(f"Sin ingresar {dias_sin_ingresar} días")

        # Si la confianza heurística es alta, no gastamos tokens
        if not usar_claude or base.confianza >= 0.75:
            base = _enriquecer_estado(base)
            return base

        # Análisis con Claude
        system = """Sos un psicólogo comercial especializado en ventas B2B industriales latinoamericanas.
Analizás el estado emocional de un vendedor (aliado) a partir de su mensaje.

Devolvé este JSON exacto:
{
  "estado": "frustracion|energia_alta|decepcion|urgencia|agotamiento|coaching_needed|neutro",
  "confianza": 0.0-1.0,
  "señales": ["señal 1", "señal 2"],
  "ajuste_tono": "instrucción breve para JARVIS",
  "ajuste_formato": "corto|paso_a_paso|directo|empático|normal",
  "oportunidad_momentum": true/false
}"""

        historial_str = ""
        if historial_sesion:
            ultimos = historial_sesion[-3:]
            historial_str = "\n\nÚltimos mensajes del aliado:\n" + "\n".join(
                f"- {m.get('rol','?')}: {str(m.get('contenido',''))[:100]}"
                for m in ultimos
            )

        prompt = f"Mensaje del aliado:\n\"{texto}\"{historial_str}"

        raw = _chat_claude(system, prompt)
        data = _parse_json(raw) if raw else None

        if data:
            resultado = EstadoAliado(
                estado=data.get("estado", base.estado),
                confianza=float(data.get("confianza", base.confianza)),
                señales_detectadas=data.get("señales", base.señales_detectadas),
                ajuste_tono=data.get("ajuste_tono", ""),
                ajuste_formato=data.get("ajuste_formato", "normal"),
                oportunidad_momentum=bool(data.get("oportunidad_momentum", False)),
                fuente="claude",
            )
        else:
            resultado = base

        return _enriquecer_estado(resultado)

    # ── 2. ANÁLISIS DEL PROSPECTO ─────────────────────────────────────────────

    @staticmethod
    def analizar_prospecto(
        texto_comunicacion: str,
        nombre_empresa: str = "",
        historial_lead: Optional[str] = None,
        usar_claude: bool = True,
    ) -> AnalisisProspecto:
        """
        Analiza un email / mensaje del prospecto para detectar temperatura y señales.

        Args:
            texto_comunicacion: Texto del email o mensaje recibido del prospecto.
            nombre_empresa:     Nombre de la empresa del prospecto (contexto).
            historial_lead:     Resumen previo del lead (opcional).
            usar_claude:        Si True, usa Claude.

        Returns:
            AnalisisProspecto con temperatura, señales y acción recomendada.
        """
        base = _prospecto_heuristico(texto_comunicacion)

        if not usar_claude or base.confianza >= 0.70:
            return base

        system = """Sos un experto en ventas B2B industriales latinoamericanas.
Analizás la comunicación de un prospecto para detectar temperatura del lead y señales de compra o fuga.

Devolvé este JSON exacto:
{
  "temperatura": "caliente|tibio|frio|perdido|indefinido",
  "confianza": 0.0-1.0,
  "urgencia_real": true/false,
  "señales_compra": ["señal 1", "señal 2"],
  "señales_fuga": ["señal 1"],
  "poder_decision": "decide|consulta|indefinido",
  "resumen_ejecutivo": "1-2 líneas con emoji",
  "accion_recomendada": "qué hacer ahora"
}"""

        contexto_empresa = f"Empresa: {nombre_empresa}\n" if nombre_empresa else ""
        historial_str = f"\nContexto previo del lead:\n{historial_lead}\n" if historial_lead else ""
        prompt = f"{contexto_empresa}{historial_str}\nComunicación del prospecto:\n\"\"\"\n{texto_comunicacion}\n\"\"\""

        raw = _chat_claude(system, prompt)
        data = _parse_json(raw) if raw else None

        if data:
            return AnalisisProspecto(
                temperatura=data.get("temperatura", base.temperatura),
                confianza=float(data.get("confianza", base.confianza)),
                urgencia_real=bool(data.get("urgencia_real", base.urgencia_real)),
                señales_compra=data.get("señales_compra", base.señales_compra),
                señales_fuga=data.get("señales_fuga", base.señales_fuga),
                poder_decision=data.get("poder_decision", base.poder_decision),
                resumen_ejecutivo=data.get("resumen_ejecutivo", base.resumen_ejecutivo),
                accion_recomendada=data.get("accion_recomendada", base.accion_recomendada),
                fuente="claude",
            )

        return base

    # ── 3. DETECCIÓN PROACTIVA ────────────────────────────────────────────────

    @staticmethod
    def señales_proactivas(
        dias_sin_ingresar: int,
        leads_sin_contacto: int,
        propuestas_sin_respuesta: int,
        clientes_sin_actividad: int,
    ) -> list[dict]:
        """
        Genera alertas proactivas basadas en métricas del aliado.
        No usa Claude — es lógica pura para activarse antes de la sesión.

        Returns:
            Lista de alertas con nivel, mensaje y accion_sugerida.
        """
        alertas = []

        if dias_sin_ingresar >= 5:
            alertas.append({
                "nivel": "critica",
                "tipo": "inactividad_aliado",
                "icono": "⚡",
                "mensaje": f"No ingresaste en {dias_sin_ingresar} días. Hay oportunidades esperando.",
                "accion": "Ver resumen de pipeline",
            })

        if leads_sin_contacto >= 3:
            alertas.append({
                "nivel": "alta",
                "tipo": "leads_frios",
                "icono": "🔴",
                "mensaje": f"{leads_sin_contacto} leads sin contacto. Algunos pueden estar eligiendo a la competencia.",
                "accion": "Ver leads dormidos",
            })

        if propuestas_sin_respuesta >= 2:
            alertas.append({
                "nivel": "alta",
                "tipo": "propuestas_fantasma",
                "icono": "📄",
                "mensaje": f"{propuestas_sin_respuesta} propuestas sin respuesta. Momento de hacer seguimiento.",
                "accion": "Generar seguimientos",
            })

        if clientes_sin_actividad >= 2:
            alertas.append({
                "nivel": "media",
                "tipo": "clientes_riesgo_churn",
                "icono": "🟡",
                "mensaje": f"{clientes_sin_actividad} clientes sin actividad reciente. Riesgo de churn.",
                "accion": "Ver clientes activos",
            })

        return alertas


# ─── HELPER: ENRIQUECER ESTADO ────────────────────────────────────────────────

def _enriquecer_estado(estado: EstadoAliado) -> EstadoAliado:
    """
    Agrega ajuste_tono y ajuste_formato basados en el estado,
    si Claude no los proveyó.
    """
    if estado.ajuste_tono and estado.ajuste_formato:
        return estado  # Claude ya los completó

    ajustes = {
        "frustracion": (
            "Primero validá la frustración del aliado en una línea. Luego ofrecé UNA solución concreta, no una lista.",
            "paso_a_paso",
            False,
        ),
        "energia_alta": (
            "Aprovechá el momentum. Felicitá brevemente y sugería la próxima acción agresiva.",
            "directo",
            True,
        ),
        "decepcion": (
            "Validá la derrota primero. No des lista de tareas. Luego ofreé aprender del caso.",
            "empático",
            False,
        ),
        "urgencia": (
            "Respuesta ultra-corta. Solo lo esencial. Sin preambles.",
            "corto",
            False,
        ),
        "agotamiento": (
            "Respuesta brevísima. Una sola acción prioritaria. Sin abrumar.",
            "corto",
            False,
        ),
        "coaching_needed": (
            "Modo tutor. Explicá paso a paso con ejemplos concretos del sector industrial.",
            "paso_a_paso",
            False,
        ),
        "neutro": (
            "Tono profesional estándar. Claro, directo, argentino.",
            "normal",
            False,
        ),
    }

    tono, formato, momentum = ajustes.get(estado.estado, ("", "normal", False))

    if not estado.ajuste_tono:
        estado.ajuste_tono = tono
    if not estado.ajuste_formato:
        estado.ajuste_formato = formato
    if not estado.oportunidad_momentum:
        estado.oportunidad_momentum = momentum

    return estado


# ─── FUNCIÓN DE AJUSTE DE SISTEMA PROMPT ─────────────────────────────────────

def ajustar_tono_respuesta(estado: EstadoAliado) -> str:
    """
    Devuelve un bloque de instrucciones para inyectar al system prompt de JARVIS
    según el estado emocional detectado del aliado.

    Usar desde jarvis.py / jarvis_routes.py:
        estado = JarvisEmocional.analizar_aliado(mensaje)
        ajuste = ajustar_tono_respuesta(estado)
        system_prompt = BASE_SYSTEM + f"\n\n{ajuste}"
    """
    if not estado or estado.estado == "neutro":
        return ""

    instrucciones = {
        "frustracion": (
            "AJUSTE EMOCIONAL DETECTADO: El aliado está frustrado o bloqueado.\n"
            "1. Validá su situación en máximo 1 oración empática.\n"
            "2. Ofrecé UNA sola solución concreta, no una lista.\n"
            "3. Usá tono calmado y de compañero de equipo, no de asistente.\n"
            "4. Terminá con UNA pregunta o acción específica."
        ),
        "energia_alta": (
            "AJUSTE EMOCIONAL DETECTADO: El aliado está con energía alta, acaba de cerrar o lograr algo.\n"
            "1. Felicitalo en 1 oración breve (no exageres).\n"
            "2. Aprovechá el momentum: sugería UNA acción comercial audaz.\n"
            "3. Tono: colega que festeja junto y ya piensa en el próximo nivel."
        ),
        "decepcion": (
            "AJUSTE EMOCIONAL DETECTADO: El aliado perdió una propuesta o cliente.\n"
            "1. Primero: validá la derrota. Una oración humana, no de manual.\n"
            "2. NO des lista de tareas todavía.\n"
            "3. Ofrecé aprender del caso: ¿qué podemos entender de esto?\n"
            "4. Al final, opcional: UNA acción de recuperación si el aliado parece listo."
        ),
        "urgencia": (
            "AJUSTE EMOCIONAL DETECTADO: El aliado necesita respuesta urgente.\n"
            "Regla: respuesta en máximo 5 líneas. Sin introducción. Sin resúmenes al final.\n"
            "Solo lo que necesita. Ya."
        ),
        "agotamiento": (
            "AJUSTE EMOCIONAL DETECTADO: El aliado parece agotado o con poco tiempo.\n"
            "1. Respuesta brevísima: máximo 3 párrafos cortos.\n"
            "2. UNA acción prioritaria máxima.\n"
            "3. No abrumes con opciones ni variantes.\n"
            "4. Tono directo y de confianza."
        ),
        "coaching_needed": (
            "AJUSTE EMOCIONAL DETECTADO: El aliado está pidiendo guía, no solo una respuesta.\n"
            "1. Adoptá modo tutor/coach comercial.\n"
            "2. Explicá el razonamiento detrás de la acción, no solo la acción.\n"
            "3. Usá ejemplos del sector industrial argentino cuando puedas.\n"
            "4. Preguntá si quiere practicar o simular la situación con vos."
        ),
    }

    return instrucciones.get(estado.estado, "")


# ─── ANÁLISIS DE TIMING ───────────────────────────────────────────────────────

def momento_optimo_contacto(
    sector: str,
    region: str = "argentina",
    historial_contactos: Optional[list[dict]] = None,
) -> dict:
    """
    Sugiere el mejor momento de contacto basado en sector y región.
    En el futuro esto se alimenta del Flywheel.

    Returns:
        dict con dia_semana, hora_inicio, hora_fin, razon.
    """
    # Tabla base (en el MVP: curada manualmente, luego viene del Flywheel)
    tabla_base = {
        "metalurgica":  {"dia": "martes",    "hora_inicio": "09:00", "hora_fin": "11:00", "razon": "Gerentes de planta revisan proveedores a mitad de semana"},
        "agro":         {"dia": "miercoles", "hora_inicio": "07:30", "hora_fin": "10:00", "razon": "Productores agropecuarios están más disponibles temprano"},
        "logistica":    {"dia": "lunes",     "hora_inicio": "10:00", "hora_fin": "12:00", "razon": "Lunes post-fin de semana, antes de que el ritmo operativo absorba todo"},
        "construccion": {"dia": "jueves",    "hora_inicio": "09:00", "hora_fin": "11:30", "razon": "Empresa constructora: jueves es reunión interna, viernes salen a obra"},
        "oil_gas":      {"dia": "martes",    "hora_inicio": "10:00", "hora_fin": "12:00", "razon": "Sector muy estructurado, martes y miércoles son los días de decisión"},
        "default":      {"dia": "martes",    "hora_inicio": "09:00", "hora_fin": "11:00", "razon": "Patrón general para B2B industrial en Argentina"},
    }

    sector_key = sector.lower().replace(" ", "_").replace("ú", "u").replace("ó", "o")
    data = tabla_base.get(sector_key, tabla_base["default"])

    # Si hay historial del aliado, usarlo en el futuro
    if historial_contactos:
        # TODO: analizar cuáles contactos del historial tuvieron mayor tasa de respuesta
        pass

    return {
        "sector": sector,
        "region": region,
        "dia_semana": data["dia"],
        "hora_inicio": data["hora_inicio"],
        "hora_fin": data["hora_fin"],
        "razon": data["razon"],
        "fuente": "conocimiento_base",
    }


# ─── SERIALIZACIÓN ────────────────────────────────────────────────────────────

def estado_to_dict(estado: EstadoAliado) -> dict:
    return {
        "estado": estado.estado,
        "confianza": round(estado.confianza, 2),
        "señales_detectadas": estado.señales_detectadas,
        "ajuste_tono": estado.ajuste_tono,
        "ajuste_formato": estado.ajuste_formato,
        "oportunidad_momentum": estado.oportunidad_momentum,
        "fuente": estado.fuente,
    }


def prospecto_to_dict(analisis: AnalisisProspecto) -> dict:
    return {
        "temperatura": analisis.temperatura,
        "confianza": round(analisis.confianza, 2),
        "urgencia_real": analisis.urgencia_real,
        "señales_compra": analisis.señales_compra,
        "señales_fuga": analisis.señales_fuga,
        "poder_decision": analisis.poder_decision,
        "resumen_ejecutivo": analisis.resumen_ejecutivo,
        "accion_recomendada": analisis.accion_recomendada,
        "fuente": analisis.fuente,
    }


# ─── SELF-TEST ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== TEST: Estado del aliado ===\n")

    casos_aliado = [
        "no sé cómo manejar esta objeción de precio, el cliente dice que es muy caro",
        "¡Cerré el de MetalPro! Firmaron esta mañana.",
        "Perdí la propuesta de Frigorífico del Plata, eligieron a otro.",
        "necesito el email YA, tengo la reunión en 20 minutos",
        "ok",
        "cómo le digo al cliente que su presupuesto es muy bajo para lo que pide?",
    ]

    for caso in casos_aliado:
        estado = JarvisEmocional.analizar_aliado(caso, usar_claude=False)
        print(f"INPUT:   {caso[:60]}")
        print(f"ESTADO:  {estado.estado} ({estado.confianza:.0%})")
        print(f"FORMATO: {estado.ajuste_formato}")
        print()

    print("\n=== TEST: Análisis de prospecto ===\n")

    casos_prospecto = [
        "¿Cuándo podrían empezar? Necesitamos tenerlo implementado antes del 15.",
        "Lo consulto con mi socio y te aviso la semana que viene.",
        "Interesante, gracias. Estamos evaluando varias opciones por ahora.",
        "¿Quién del equipo haría la implementación? ¿Necesitamos firmar algún contrato?",
    ]

    for caso in casos_prospecto:
        analisis = JarvisEmocional.analizar_prospecto(caso, usar_claude=False)
        print(f"INPUT:      {caso[:60]}")
        print(f"TEMP:       {analisis.temperatura} ({analisis.confianza:.0%})")
        print(f"RESUMEN:    {analisis.resumen_ejecutivo}")
        print(f"ACCIÓN:     {analisis.accion_recomendada}")
        print()
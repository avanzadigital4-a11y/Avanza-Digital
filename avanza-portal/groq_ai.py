"""
groq_ai.py — Cliente Groq + funciones de IA con fallback heurístico.

DISEÑO:
- La API key se lee de la variable de entorno GROQ_API_KEY.
- Si la key no está, está mal, o Groq falla / tarda demasiado / da rate limit:
  TODAS las funciones devuelven None y el llamador usa su fallback heurístico.
  Esto es crítico: el producto NO se cae nunca por un problema con Groq.
- Timeout duro de 8 segundos para no bloquear UX.
- No se loggea la API key. Solo se imprime el motivo del error en stderr.
"""

from __future__ import annotations
import os, json, sys, re, httpx
from typing import Optional, Any

# ─── CONFIG ──────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Modelo de calidad (perfilado / pitch). Devuelve JSON estricto.
GROQ_MODEL_QUALITY = os.environ.get("GROQ_MODEL_QUALITY", "llama-3.3-70b-versatile")
# Modelo rápido (siguiente acción, mensajes cortos).
GROQ_MODEL_FAST    = os.environ.get("GROQ_MODEL_FAST",    "llama-3.1-8b-instant")

GROQ_TIMEOUT = float(os.environ.get("GROQ_TIMEOUT", "8.0"))

# ─── LISTA OFICIAL DE PLANES — debe coincidir con models.PLANES ──────────────
_PLANES_VALIDOS = ("Plan Base", "Plan Pro", "Plan Industrial", "Estrategico 360")
_PRECIOS_PLANES = {
    "Plan Base":         1050.0,
    "Plan Pro":          2900.0,
    "Plan Industrial":   4900.0,
    "Estrategico 360":   7500.0,
}


def is_enabled() -> bool:
    """¿Hay API key configurada? Si no, todo cae a fallback."""
    return bool(GROQ_API_KEY)


# ─── CORE: llamada HTTP a Groq ───────────────────────────────────────────────

def _chat(prompt: str,
          system: str,
          *,
          model: str = GROQ_MODEL_QUALITY,
          max_tokens: int = 600,
          temperature: float = 0.4,
          json_mode: bool = False) -> Optional[str]:
    """
    Hace una llamada a Groq y devuelve el texto de respuesta, o None si algo falla.
    NUNCA lanza excepciones — devolver None es la señal de "usá tu fallback".
    """
    if not GROQ_API_KEY:
        return None

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }

    try:
        r = httpx.post(GROQ_URL, json=payload, headers=headers, timeout=GROQ_TIMEOUT)
        if r.status_code != 200:
            # Logueamos brevemente para debug, sin la key.
            try:
                detail = r.json()
                msg = detail.get("error", {}).get("message", "") or str(detail)[:200]
            except Exception:
                msg = (r.text or "")[:200]
            print(f"[groq_ai] HTTP {r.status_code}: {msg}", file=sys.stderr)
            return None
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        print(f"[groq_ai] timeout > {GROQ_TIMEOUT}s", file=sys.stderr)
        return None
    except Exception as e:
        # Cualquier otro problema de red, JSON inválido, etc.
        print(f"[groq_ai] error: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _extract_json(raw: str) -> Optional[dict]:
    """Saca un objeto JSON de la respuesta del modelo aunque venga con texto extra."""
    if not raw:
        return None
    # 1. Intento directo.
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 2. Intento extraer el primer { ... } balanceado.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


# ════════════════════════════════════════════════════════════════════════════
#   FUNCIÓN 1 — Perfilado de leads/prospectos con IA (Prioridades #1 y #2)
# ════════════════════════════════════════════════════════════════════════════

_PERFILADO_SYSTEM = """Sos un asistente experto en ventas B2B de servicios digitales en Argentina.
Tu trabajo: analizar un lead y devolver una recomendación accionable para un aliado comercial.

CONTEXTO DEL NEGOCIO (AvanzaDigital):
- Vendemos sitios web + sistemas digitales a PyMEs e industrias en Argentina.
- Planes disponibles (USD, único pago):
  • "Plan Base" (USD 1050): sitio simple, Google Business, métricas básicas. Para microempresas.
  • "Plan Pro" (USD 2900): sitio + captación de leads + automatizaciones básicas. Default versátil.
  • "Plan Industrial" (USD 4900): sistema completo B2B, catálogo técnico, integraciones. Para metalúrgicas, agro, construcción, logística grandes.
  • "Estrategico 360" (USD 7500): canal digital completo operando como una máquina de ventas. Para tech / empresas medianas-grandes que esperan excelencia.

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "score": <int 0-100>,
    "plan_recomendado": "<uno de: Plan Base, Plan Pro, Plan Industrial, Estrategico 360>",
    "ticket_esperado": <int en USD>,
    "razon": "<una sola frase explicando por qué ese plan, máximo 18 palabras>",
    "pitch_sugerido": "<mensaje WhatsApp listo para copiar y pegar, entre 100 y 200 palabras, 3 párrafos bien desarrollados. ESTRUCTURA OBLIGATORIA: Párrafo 1: apertura directa mencionando el nombre de la empresa y algo específico del rubro que demuestre que los conocés. Párrafo 2: mencioná un resultado concreto y verosímil que logramos en una empresa similar del mismo rubro, con números reales (ej: pasaron de 48hs a 6hs en tiempo de respuesta, o aumentaron un 35% las consultas en 60 días). Párrafo 3: ofrecé un diagnóstico gratuito y cerrá con una pregunta abierta específica del rubro, nunca con '¿te interesa?'. Español rioplatense, tono directo entre pares, sin 'estimado', sin 'cordiales saludos', sin emojis salvo uno opcional al inicio.>"
  }

CRITERIOS DE SCORE (0-100):
- 80-100: lead caliente con urgencia alta y rubro alineado, o ya respondió contacto previo.
- 60-79: rubro encaja bien, urgencia media-alta, tamaño consistente.
- 40-59: encaje razonable pero requiere educación / poco urgente.
- 20-39: rubro o tamaño problemáticos, baja urgencia.
- 0-19: muy mal encaje (ej: micro empresa con plan industrial).

CRITERIOS DE PITCH:
- Hablale al referente como un par. Nada de "estimado".
- Mencioná el nombre de la empresa en el primer párrafo.
- El dolor debe ser ESPECÍFICO del rubro: no "mejorar la eficiencia" sino el problema concreto que tienen las empresas de ese rubro.
- El caso de éxito del párrafo 2 debe tener números concretos y sonar real (inventalos verosímiles si no tenés uno real).
- No prometas resultados absolutos. Usá "logramos", "pasaron de X a Y", "redujeron en un Z%".
- Cerrá con una pregunta específica del rubro, NUNCA con "¿te interesa?" o "¿querés saber más?".
- Si el lead ya respondió un contacto previo, el pitch debe ser un follow-up corto referenciando eso.
"""

def perfilar_lead_ia(*, empresa: str,
                       rubro: Optional[str],
                       tamano: Optional[str],
                       urgencia: Optional[str],
                       estado: Optional[str] = None,
                       nota_aliado: Optional[str] = None,
                       ciudad: Optional[str] = None,
                       # v1.6 — presencia digital
                       web: Optional[str] = None,
                       instagram: Optional[str] = None,
                       tiene_web: bool = False,
                       tiene_redes: bool = False,
                       observacion: Optional[str] = None) -> Optional[dict]:
    """
    Devuelve {score, plan_recomendado, pitch_sugerido, ticket_esperado, razon}
    o None si Groq falla → el llamador usa su fallback heurístico.

    Acepta empresa libre (puede ser un nombre de prospecto o de empresa de la bolsa).
    """
    if not is_enabled():
        return None

    # Construir el contexto del lead en lenguaje natural.
    partes = [f"Empresa: {empresa or '(sin nombre)'}"]
    if rubro:    partes.append(f"Rubro: {rubro}")
    if tamano:   partes.append(f"Tamaño: {tamano}")
    if urgencia: partes.append(f"Urgencia detectada: {urgencia}")
    if ciudad:   partes.append(f"Ciudad: {ciudad}")
    # v1.6 — presencia digital
    if web:
        partes.append(f"Sitio web: {web}")
    elif tiene_web:
        partes.append("Tiene sitio web (URL no disponible en este tier)")
    if instagram:
        partes.append(f"Instagram/Facebook: {instagram}")
    elif tiene_redes:
        partes.append("Tiene redes sociales (usuario no disponible en este tier)")
    if observacion:
        partes.append(f"Observacion del prospectador: {observacion[:300]}")
    if estado:
        estado_legible = {
            "sin_contactar":      "todavía no fue contactado",
            "contactado":         "ya fue contactado pero no respondió",
            "respondio":          "RESPONDIÓ a un contacto previo (lead caliente)",
            "propuesta_enviada":  "ya tiene una propuesta enviada",
            "negociando":         "está en negociación",
            "vendido":            "ya cerró",
            "perdido":            "se perdió",
        }.get(estado, estado)
        partes.append(f"Estado actual: {estado_legible}")
    if nota_aliado:
        # Cortamos por las dudas para no inflar el prompt.
        partes.append(f"Nota del aliado: {nota_aliado[:300]}")

    user_prompt = (
        "Analizá este lead y devolveme la recomendación en JSON estricto:\n\n"
        + "\n".join(partes)
    )

    # v1.6 — refuerzo cuando hay presencia digital cargada
    if web or instagram:
        user_prompt += (
            "\n\nIMPORTANTE: Este lead tiene presencia digital cargada. En el pitch, "
            "menciona específicamente que revisaste su presencia online y conecta "
            "con lo que probablemente les falta mejorar según su rubro."
        )

    raw = _chat(user_prompt, _PERFILADO_SYSTEM,
                model=GROQ_MODEL_QUALITY,
                max_tokens=1200,
                temperature=0.5,
                json_mode=True)
    if not raw:
        return None

    obj = _extract_json(raw)
    if not obj:
        return None

    # ─── Validación + saneamiento ────────────────────────────────────────────
    try:
        score = int(obj.get("score", 0))
    except Exception:
        score = 0
    score = max(0, min(100, score))

    plan = str(obj.get("plan_recomendado", "Plan Pro")).strip()
    if plan not in _PLANES_VALIDOS:
        # Buscamos el más parecido por substring para tolerar "plan pro", "Plan PRO", etc.
        plan_norm = plan.lower()
        match = next((p for p in _PLANES_VALIDOS if p.lower() == plan_norm), None)
        if not match:
            match = next((p for p in _PLANES_VALIDOS if p.lower() in plan_norm or plan_norm in p.lower()), None)
        plan = match or "Plan Pro"

    try:
        ticket = float(obj.get("ticket_esperado", _PRECIOS_PLANES[plan]))
    except Exception:
        ticket = _PRECIOS_PLANES[plan]
    if ticket <= 0:
        ticket = _PRECIOS_PLANES[plan]

    razon = str(obj.get("razon", "")).strip() or "Recomendación basada en rubro y tamaño."
    pitch = str(obj.get("pitch_sugerido", "")).strip()
    if not pitch:
        return None  # sin pitch no hay valor → fallback

    return {
        "score":            score,
        "plan_recomendado": plan,
        "ticket_esperado":  round(ticket, 0),
        "razon":            razon,
        "pitch_sugerido":   pitch,
    }


# ════════════════════════════════════════════════════════════════════════════
#   FUNCIÓN 2 — Siguiente acción personalizada (Prioridad #3)
# ════════════════════════════════════════════════════════════════════════════

_SIGUIENTE_ACCION_SYSTEM = """Sos el coach comercial de un aliado de ventas en Argentina.
Tu trabajo: dada una situación concreta de un prospecto (no genérica), escribir
una recomendación accionable y un mensaje listo para copiar/pegar.

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "descripcion": "<máximo 2 oraciones explicando por qué importa actuar AHORA y cómo, mencionando el nombre del prospecto y datos específicos como días, rubro, etc.>",
    "mensaje_sugerido": "<mensaje de WhatsApp/email LISTO para copiar y pegar, español rioplatense, 1-3 oraciones, sin emojis salvo uno opcional, terminando con pregunta abierta. NO uses 'estimado', 'cordiales saludos' ni clichés. Si es follow-up, no te disculpes por insistir.>"
  }
- Hablale al aliado como un par (vos), no le des sermones.
- En el mensaje_sugerido, hablale al PROSPECTO (es lo que el aliado va a enviar).
- Adaptá el tono al rubro y estado: un metalúrgico no recibe el mismo mensaje que una clínica.
- Sé concreto. Nada de "espero te encuentres bien"."""

def siguiente_accion_ia(*, tipo: str,
                          prospecto_nombre: str,
                          prospecto_rubro: Optional[str] = None,
                          prospecto_tamano: Optional[str] = None,
                          prospecto_urgencia: Optional[str] = None,
                          dias_relevantes: Optional[int] = None,
                          ultima_nota: Optional[str] = None,
                          aliado_nombre: Optional[str] = None) -> Optional[dict]:
    """
    Devuelve {"descripcion", "mensaje_sugerido"} o None si Groq falla.

    `tipo` es el código interno de la acción:
      - cerrar_lead_caliente   → respondió, hay que mandar propuesta
      - seguimiento_propuesta  → propuesta enviada hace N días sin respuesta
      - contactar_prospecto    → carga vieja sin contactar
      - seguimiento            → contactado hace N días, se enfría
      - reclamar_lead          → hay leads en bolsa
      - prospectar             → no tiene nada cargado

    Para `reclamar_lead` y `prospectar` no llamamos a IA (son acciones genéricas
    sin prospecto específico) → el llamador no debería usar esta función ahí.
    """
    if not is_enabled():
        return None

    # Mapear tipo → instrucción concreta para el modelo.
    instrucciones_por_tipo = {
        "cerrar_lead_caliente":
            f"El prospecto {prospecto_nombre} RESPONDIÓ y está esperando que le mandes una propuesta. "
            "Cada hora enfría el lead. Escribí cómo el aliado debería actuar AHORA y un mensaje breve "
            "para enviarle al prospecto pidiéndole 5 minutos para presentarle la propuesta.",

        "seguimiento_propuesta":
            f"El aliado le mandó una propuesta a {prospecto_nombre} hace {dias_relevantes or 'varios'} días "
            "y NO hubo respuesta. No insistir con la propuesta entera; reactivar la conversación con un "
            "mensaje corto que invite a responder con poco esfuerzo (sí/no/dudas).",

        "contactar_prospecto":
            f"El prospecto {prospecto_nombre} está cargado hace {dias_relevantes or 'varios'} días pero "
            "todavía NO fue contactado. Escribí un mensaje frío de apertura que NO sea un sermón, que "
            "ofrezca un diagnóstico/auditoría gratis del estado digital actual de la empresa, y conecte "
            "con un dolor real del rubro específico.",

        "seguimiento":
            f"Al prospecto {prospecto_nombre} le escribiste hace {dias_relevantes or 'varios'} días y "
            "no respondió. Escribí un mensaje corto de re-enganche, sin presionar, dando una salida "
            "elegante (algo como 'si no es momento, decime y no insisto').",
    }
    instruccion = instrucciones_por_tipo.get(tipo)
    if not instruccion:
        return None  # tipo genérico, no vale la pena llamar a IA

    contexto = [instruccion, ""]
    contexto.append("Datos extra del prospecto:")
    contexto.append(f"- Nombre: {prospecto_nombre}")
    if prospecto_rubro:    contexto.append(f"- Rubro: {prospecto_rubro}")
    if prospecto_tamano:   contexto.append(f"- Tamaño: {prospecto_tamano}")
    if prospecto_urgencia: contexto.append(f"- Urgencia: {prospecto_urgencia}")
    if ultima_nota:        contexto.append(f"- Última nota interna del aliado: {ultima_nota[:250]}")
    if aliado_nombre:      contexto.append(f"- Nombre del aliado (para que firme si quiere): {aliado_nombre}")

    raw = _chat("\n".join(contexto), _SIGUIENTE_ACCION_SYSTEM,
                model=GROQ_MODEL_FAST,
                max_tokens=400,
                temperature=0.6,
                json_mode=True)
    if not raw:
        return None

    obj = _extract_json(raw)
    if not obj:
        return None

    desc = str(obj.get("descripcion", "")).strip()
    msg  = str(obj.get("mensaje_sugerido", "")).strip()
    if not desc or not msg:
        return None

    return {"descripcion": desc, "mensaje_sugerido": msg}


# ════════════════════════════════════════════════════════════════════════════
#   FUNCIÓN 3 — Generador de follow-up on-demand (Prioridad #4)
# ════════════════════════════════════════════════════════════════════════════

_FOLLOWUP_SYSTEM = """Sos el coach comercial de un aliado de ventas en Argentina.
Tu trabajo: dado un prospecto al que el aliado le escribió hace varios días sin
respuesta, generar un mensaje de follow-up listo para mandar por WhatsApp o email.

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "mensaje": "<el mensaje listo para copiar y pegar, español rioplatense, sin emojis salvo uno opcional al inicio>",
    "estrategia": "<una frase explicando por qué este enfoque puede destrabar la conversación, máximo 16 palabras>"
  }
- El mensaje debe ser breve (2-4 oraciones máximo).
- NUNCA empieces con "estimado" o "buen día estimado".
- NUNCA pidas disculpas por insistir ("perdón por molestar", "espero no ser pesado", etc).
- NUNCA repitas la propuesta entera. Asumí que ya la conoce.
- Cerrá con una pregunta abierta de bajo compromiso (sí/no, pulgar, "decime y no insisto").
- Si el aliado pidió un tono específico, respetalo.
- Adaptá el lenguaje al rubro: una metalúrgica recibe un tono más directo que una clínica."""

_TONO_LABELS = {
    "amigable":   "amigable y cálido, como hablándole a un colega",
    "directo":    "directo, sin vueltas, agendando si quiere o cerrando si no",
    "ultimo":     "este es el ÚLTIMO mensaje — dale una salida elegante para cerrar la conversación",
    "valor":      "aportando un dato/insight nuevo del rubro antes de pedir algo",
}

def generar_followup_ia(*, prospecto_nombre: str,
                          rubro: Optional[str] = None,
                          tamano: Optional[str] = None,
                          plan_recomendado: Optional[str] = None,
                          dias_sin_responder: Optional[int] = None,
                          ultimo_mensaje_aliado: Optional[str] = None,
                          ultima_nota: Optional[str] = None,
                          aliado_nombre: Optional[str] = None,
                          tono: str = "directo") -> Optional[dict]:
    """
    Genera un mensaje de follow-up para un prospecto que no responde.
    Devuelve {"mensaje", "estrategia"} o None si Groq falla.
    `tono`: 'amigable' | 'directo' | 'ultimo' | 'valor'
    """
    if not is_enabled():
        return None

    tono_desc = _TONO_LABELS.get(tono, _TONO_LABELS["directo"])

    bloques = [f"Prospecto: {prospecto_nombre}"]
    if rubro:                  bloques.append(f"Rubro: {rubro}")
    if tamano:                 bloques.append(f"Tamaño: {tamano}")
    if plan_recomendado:       bloques.append(f"Plan recomendado: {plan_recomendado}")
    if dias_sin_responder is not None:
        bloques.append(f"Días sin respuesta: {dias_sin_responder}")
    if ultimo_mensaje_aliado:
        bloques.append(f"Último mensaje que el aliado mandó: {ultimo_mensaje_aliado[:300]}")
    if ultima_nota:
        bloques.append(f"Nota interna del aliado: {ultima_nota[:250]}")
    if aliado_nombre:
        bloques.append(f"Nombre del aliado (puede firmar si quiere): {aliado_nombre}")
    bloques.append(f"Tono solicitado: {tono_desc}")

    user = (
        "Generá el follow-up para este prospecto:\n\n"
        + "\n".join(bloques)
    )

    raw = _chat(user, _FOLLOWUP_SYSTEM,
                model=GROQ_MODEL_FAST,
                max_tokens=350,
                temperature=0.7,
                json_mode=True)
    if not raw:
        return None
    obj = _extract_json(raw)
    if not obj:
        return None

    mensaje = str(obj.get("mensaje", "")).strip()
    estrategia = str(obj.get("estrategia", "")).strip()
    if not mensaje:
        return None
    return {"mensaje": mensaje, "estrategia": estrategia or "Mensaje breve para reactivar la conversación."}


# ════════════════════════════════════════════════════════════════════════════
#   FUNCIÓN 4 — Respuestas a objeciones en tiempo real (Prioridad #5)
# ════════════════════════════════════════════════════════════════════════════

_OBJECION_SYSTEM = """Sos un consultor experto en venta consultiva B2B de servicios digitales en Argentina.
Tu trabajo: dada una objeción que un prospecto le dijo a un aliado, redactar una
respuesta que reformule (no contradiga) y avance la conversación.

CONTEXTO DEL NEGOCIO (AvanzaDigital):
- Vendemos sitios web + sistemas digitales a PyMEs e industrias en Argentina.
- Planes en USD (pago único): Plan Base 1050, Plan Pro 2900, Plan Industrial 4900, Estratégico 360 7500.
- También se puede pagar en cuotas (3, 6, 12) con recargo. Aceptamos pesos.

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "respuesta": "<la respuesta que el aliado puede mandar al prospecto, español rioplatense, 2-5 oraciones>",
    "explicacion": "<una frase para el aliado explicando QUÉ está haciendo esa respuesta, máximo 20 palabras>",
    "siguiente_pregunta": "<una pregunta abierta breve que el aliado puede agregar para mantener el control de la conversación>"
  }
- NUNCA contradigas frontalmente al prospecto ('no, mirá, te equivocás').
- Reformulá la objeción reconociéndola, después aportás un ángulo nuevo, y cerrás invitando a un siguiente paso pequeño.
- Para "muy caro" → hablá de costo de oportunidad / ROI / opción de cuotas, no de descuento.
- Para "ya tengo web" → preguntá cuándo se hizo, qué métricas tienen, cuántas consultas trae por mes.
- Para "no es el momento" → ofrecé una conversación corta de diagnóstico para que tenga datos cuando SÍ sea momento.
- Para "lo voy a pensar" → preguntá qué información le falta para decidir.
- Adaptá al rubro: la respuesta a un metalúrgico no es la misma que a una clínica."""

def responder_objecion_ia(*, objecion: str,
                            prospecto_nombre: str = "",
                            rubro: Optional[str] = None,
                            tamano: Optional[str] = None,
                            plan_recomendado: Optional[str] = None,
                            ticket_esperado: Optional[float] = None) -> Optional[dict]:
    """
    Genera una respuesta a una objeción concreta.
    Devuelve {"respuesta", "explicacion", "siguiente_pregunta"} o None.
    """
    if not is_enabled():
        return None
    if not (objecion or "").strip():
        return None

    bloques = [f"Objeción que dijo el prospecto: \"{objecion.strip()[:400]}\""]
    if prospecto_nombre:  bloques.append(f"Nombre del prospecto: {prospecto_nombre}")
    if rubro:             bloques.append(f"Rubro: {rubro}")
    if tamano:            bloques.append(f"Tamaño: {tamano}")
    if plan_recomendado:  bloques.append(f"Plan que se le había recomendado: {plan_recomendado}")
    if ticket_esperado:   bloques.append(f"Ticket esperado: USD {int(ticket_esperado)}")

    user = "Redactá la respuesta del aliado a esta objeción:\n\n" + "\n".join(bloques)

    raw = _chat(user, _OBJECION_SYSTEM,
                model=GROQ_MODEL_QUALITY,
                max_tokens=500,
                temperature=0.5,
                json_mode=True)
    if not raw:
        return None
    obj = _extract_json(raw)
    if not obj:
        return None

    respuesta = str(obj.get("respuesta", "")).strip()
    explicacion = str(obj.get("explicacion", "")).strip()
    siguiente = str(obj.get("siguiente_pregunta", "")).strip()
    if not respuesta:
        return None

    return {
        "respuesta": respuesta,
        "explicacion": explicacion or "Reformula la objeción y avanza la conversación.",
        "siguiente_pregunta": siguiente or "",
    }


# ════════════════════════════════════════════════════════════════════════════
#   FUNCIÓN 5 — Asistente para posts de Comunidad (Prioridad #7)
# ════════════════════════════════════════════════════════════════════════════

_POST_SYSTEM = """Sos un copywriter de comunidad para una red de aliados de ventas en Argentina.
Tu trabajo: dado el tipo de post y unos datos clave, redactar un post auténtico y útil
para la comunidad de aliados (NO marketing externo — esto es entre colegas).

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "titulo": "<título corto y específico, NO clickbait, máximo 80 caracteres>",
    "cuerpo": "<post de 80-180 palabras, español rioplatense, párrafos cortos, sin emojis al final, sin '#hashtags', sin firma. Hablale a colegas, no a clientes finales.>"
  }
- Tipo "win" (cierre): contar el caso con números reales si los hay (ticket, días, rubro). Sin alardear.
- Tipo "tip": una idea concreta accionable. Estructura: "qué/por qué/cómo".
- Tipo "pregunta": pregunta clara con contexto suficiente para que otros respondan.
- NUNCA inventes datos que el aliado no proporcionó. Si faltan datos, escribí en términos generales sin números falsos.
- NUNCA uses frases como 'me gustaría compartir' o 'quería contarles'."""

def redactar_post_comunidad_ia(*, tipo: str,
                                  datos_clave: str,
                                  aliado_nombre: Optional[str] = None) -> Optional[dict]:
    """
    Genera {titulo, cuerpo} para un post de la comunidad.
    `tipo`: 'win' | 'tip' | 'pregunta'
    `datos_clave`: texto libre que el aliado escribió describiendo los datos.
    """
    if not is_enabled():
        return None
    tipo = (tipo or "").strip().lower()
    if tipo not in ("win", "tip", "pregunta"):
        return None
    if not (datos_clave or "").strip():
        return None

    tipo_desc = {
        "win":      "una victoria/cierre de venta para celebrar y enseñar a otros aliados",
        "tip":      "un tip o buena práctica útil para otros aliados",
        "pregunta": "una pregunta o duda para la comunidad",
    }[tipo]

    bloques = [
        f"Tipo de post: {tipo_desc}",
        f"Datos que aportó el aliado: {datos_clave.strip()[:600]}",
    ]
    if aliado_nombre:
        bloques.append(f"Aliado que firma (no incluir en el cuerpo, solo para tono): {aliado_nombre}")

    user = "Redactá el post para la comunidad con estos datos:\n\n" + "\n".join(bloques)

    raw = _chat(user, _POST_SYSTEM,
                model=GROQ_MODEL_QUALITY,
                max_tokens=600,
                temperature=0.7,
                json_mode=True)
    if not raw:
        return None
    obj = _extract_json(raw)
    if not obj:
        return None

    titulo = str(obj.get("titulo", "")).strip()
    cuerpo = str(obj.get("cuerpo", "")).strip()
    if not titulo or not cuerpo:
        return None
    # Cap razonable por las dudas
    if len(titulo) > 120:
        titulo = titulo[:117].rstrip() + "..."
    return {"titulo": titulo, "cuerpo": cuerpo}


# ════════════════════════════════════════════════════════════════════════════
#   FUNCIÓN 6 — Mensaje del Piloto Automático (Prioridad #6)
# ════════════════════════════════════════════════════════════════════════════
#
# El piloto envía emails automáticos espaciados X días. Antes los emails eran
# templates fijos (siempre iguales para todos). Ahora cada paso usa Groq
# con el contexto del prospecto. Si Groq falla, el llamador en main.py usa
# el template fijo de siempre.

_PILOTO_SYSTEM = """Sos un asistente que redacta emails comerciales DE SEGUIMIENTO en español rioplatense
para una red de aliados de servicios digitales en Argentina.

CONTEXTO:
- El email lo manda el sistema en NOMBRE del aliado humano (no del aliado directamente).
- Los emails son parte de una secuencia automática de 3 toques separados 3 días.
- El destinatario es alguien con quien el aliado YA HABLÓ una vez (no es frío puro).

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "asunto": "<asunto del email, máximo 70 caracteres, sin signos de admiración, sin 'RE:' ni 'FW:'>",
    "cuerpo": "<cuerpo del email en TEXTO PLANO sin HTML, 2-4 párrafos cortos, sin saludo formal tipo 'estimado'>"
  }
- El cuerpo NO debe incluir firma — el sistema agrega la firma con el nombre del aliado al final.
- El cuerpo NO debe incluir disclaimer de baja — el sistema lo agrega.
- NO uses emojis.
- NO incluyas links a menos que se pidan en las instrucciones del paso.
- Adaptá tono y dolor al rubro del prospecto.
- Paso 1: retomar conversación + ofrecer diagnóstico gratis.
- Paso 2: aportar dato/caso del rubro + invitar a llamada de 15 min.
- Paso 3: cierre elegante — última vez que escribís, dale salida ('si no es momento, perfecto')."""

def generar_mensaje_piloto_ia(*, paso: int,
                                  prospecto_nombre: str,
                                  rubro: Optional[str] = None,
                                  tamano: Optional[str] = None,
                                  plan_recomendado: Optional[str] = None,
                                  aliado_nombre: Optional[str] = None) -> Optional[dict]:
    """
    Genera (asunto, cuerpo_texto) para el paso N del piloto automático.
    Devuelve {"asunto", "cuerpo_texto"} o None si Groq falla.
    El cuerpo viene en TEXTO PLANO — el caller lo envuelve en HTML.
    """
    if not is_enabled():
        return None
    if paso not in (1, 2, 3):
        return None

    instrucciones_paso = {
        1: "Es el PRIMER toque. Retomar la conversación, ofrecer diagnóstico digital gratuito como excusa para reabrir el diálogo. No vendas todavía.",
        2: "Es el SEGUNDO toque (3 días después del primero, sin respuesta). Aportar un patrón/dato del rubro específico y proponer una llamada corta de 15 min.",
        3: "Es el TERCER y ÚLTIMO toque. Cierre elegante. Decir explícitamente que es la última vez y dar salida sin presión: 'si no es momento, lo dejamos así'.",
    }[paso]

    bloques = [
        f"Paso de la secuencia: {paso} de 3",
        f"Instrucciones: {instrucciones_paso}",
        f"Prospecto: {prospecto_nombre}",
    ]
    if rubro:             bloques.append(f"Rubro: {rubro}")
    if tamano:            bloques.append(f"Tamaño: {tamano}")
    if plan_recomendado:  bloques.append(f"Plan recomendado: {plan_recomendado}")
    if aliado_nombre:     bloques.append(f"Nombre del aliado (NO incluir en cuerpo, solo para tono): {aliado_nombre}")

    user = "Generá asunto y cuerpo del email:\n\n" + "\n".join(bloques)

    raw = _chat(user, _PILOTO_SYSTEM,
                model=GROQ_MODEL_FAST,
                max_tokens=500,
                temperature=0.6,
                json_mode=True)
    if not raw:
        return None
    obj = _extract_json(raw)
    if not obj:
        return None

    asunto = str(obj.get("asunto", "")).strip()
    cuerpo = str(obj.get("cuerpo", "")).strip()
    if not asunto or not cuerpo:
        return None
    # Cap defensivo
    if len(asunto) > 120:
        asunto = asunto[:117].rstrip() + "..."
    return {"asunto": asunto, "cuerpo_texto": cuerpo}


# ════════════════════════════════════════════════════════════════════════════
#   FUNCIÓN 7 — Análisis de venta perdida (Prioridad #8)
# ════════════════════════════════════════════════════════════════════════════

_VENTA_PERDIDA_SYSTEM = """Sos un coach senior de ventas B2B en Argentina, especializado en post-mortem.
Tu trabajo: dado el historial completo de un prospecto que se marcó como perdido,
devolver un diagnóstico ÚTIL y honesto — no un análisis genérico.

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "que_paso": "<1-2 oraciones explicando tu hipótesis principal de por qué se perdió, basándote en datos concretos del historial>",
    "errores_posibles": ["<error 1, frase corta y específica>", "<error 2>", "<error 3 — máximo 3 ítems>"],
    "que_hacer_distinto": ["<lección accionable 1>", "<lección 2>", "<lección 3>"],
    "podria_recuperarse": <true|false>,
    "mensaje_recuperacion": "<si podria_recuperarse=true, escribir un mensaje breve para reabrir en 30-60 días. Si false, string vacío.>"
  }
- NO seas blando ni motivacional. El aliado no quiere palmaditas — quiere saber qué falló.
- Basate en evidencia del historial. Si no hay evidencia para una hipótesis, no la inventes.
- Si el motivo del aliado es vago ('no le interesó'), tu trabajo es proponer hipótesis específicas.
- NO repitas lo que dice el motivo del aliado — explayalo y profundizá.
- Para "errores_posibles": pensá en errores típicos de venta consultiva (no calificó bien, mucho discurso poco preguntas, no detectó al decisor real, propuesta sin urgencia, ghosting sin follow-up sistemático).
- "podria_recuperarse" debe ser true si el motivo sugiere timing/presupuesto, false si fue rechazo claro de fit/necesidad."""

def analizar_venta_perdida_ia(*, prospecto_nombre: str,
                                  rubro: Optional[str] = None,
                                  tamano: Optional[str] = None,
                                  urgencia_perfilada: Optional[str] = None,
                                  plan_recomendado: Optional[str] = None,
                                  ticket_esperado: Optional[float] = None,
                                  estado_anterior: Optional[str] = None,
                                  dias_en_pipeline: Optional[int] = None,
                                  fecha_contacto_dias: Optional[int] = None,
                                  fecha_respuesta_dias: Optional[int] = None,
                                  pasos_piloto: Optional[int] = None,
                                  notas: Optional[str] = None,
                                  motivo_aliado: Optional[str] = None) -> Optional[dict]:
    """
    Devuelve {que_paso, errores_posibles[], que_hacer_distinto[], podria_recuperarse, mensaje_recuperacion}
    o None si Groq falla.
    """
    if not is_enabled():
        return None

    bloques = [f"Prospecto perdido: {prospecto_nombre}"]
    if rubro:                  bloques.append(f"- Rubro: {rubro}")
    if tamano:                 bloques.append(f"- Tamaño: {tamano}")
    if urgencia_perfilada:     bloques.append(f"- Urgencia detectada al perfilar: {urgencia_perfilada}")
    if plan_recomendado:       bloques.append(f"- Plan recomendado: {plan_recomendado}")
    if ticket_esperado:        bloques.append(f"- Ticket esperado: USD {int(ticket_esperado)}")
    if estado_anterior:        bloques.append(f"- Último estado antes de perderse: {estado_anterior}")
    if dias_en_pipeline is not None:
        bloques.append(f"- Días desde que se cargó: {dias_en_pipeline}")
    if fecha_contacto_dias is not None:
        bloques.append(f"- Días desde el primer contacto: {fecha_contacto_dias}")
    if fecha_respuesta_dias is not None:
        bloques.append(f"- Días desde la última respuesta del prospecto: {fecha_respuesta_dias}")
    if pasos_piloto is not None:
        bloques.append(f"- Toques automáticos enviados por el piloto: {pasos_piloto} de 3")
    if notas:
        bloques.append(f"- Notas internas del aliado: {notas[:500]}")
    if motivo_aliado:
        bloques.append(f"- Motivo aducido por el aliado al cerrarlo: {motivo_aliado[:400]}")
    else:
        bloques.append("- Motivo aducido por el aliado: (no especificó nada — interpretá del historial)")

    user = "Analizá esta venta perdida y devolveme el diagnóstico:\n\n" + "\n".join(bloques)

    raw = _chat(user, _VENTA_PERDIDA_SYSTEM,
                model=GROQ_MODEL_QUALITY,
                max_tokens=700,
                temperature=0.4,
                json_mode=True)
    if not raw:
        return None
    obj = _extract_json(raw)
    if not obj:
        return None

    que_paso = str(obj.get("que_paso", "")).strip()
    errores  = obj.get("errores_posibles", []) or []
    distinto = obj.get("que_hacer_distinto", []) or []
    if not isinstance(errores, list):  errores = [str(errores)]
    if not isinstance(distinto, list): distinto = [str(distinto)]
    errores  = [str(e).strip() for e in errores  if str(e).strip()][:5]
    distinto = [str(e).strip() for e in distinto if str(e).strip()][:5]

    if not que_paso or not errores or not distinto:
        return None

    podria_rec = bool(obj.get("podria_recuperarse", False))
    msg_rec = str(obj.get("mensaje_recuperacion", "")).strip() if podria_rec else ""

    return {
        "que_paso": que_paso,
        "errores_posibles": errores,
        "que_hacer_distinto": distinto,
        "podria_recuperarse": podria_rec,
        "mensaje_recuperacion": msg_rec,
    }


# ════════════════════════════════════════════════════════════════════════════
#   FUNCIONES 8-9 — Emails personalizados (Prioridad #9)
# ════════════════════════════════════════════════════════════════════════════
#
# Estas funciones generan asunto + cuerpo de texto. El caller en main.py
# envuelve el texto en HTML con el styling del email transaccional.

_EMAIL_VENTA_SYSTEM = """Sos un coach de ventas B2B que escribe emails transaccionales personales (no marketing).
Un aliado de la red Avanza Digital acaba de cerrar una venta. Tu trabajo:
escribir un email que combine celebración auténtica + UN coaching concreto sobre el próximo movimiento.

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "asunto": "<asunto en español rioplatense, máximo 70 caracteres>",
    "cuerpo": "<cuerpo en TEXTO PLANO, español rioplatense, 3-4 párrafos cortos. NO HTML, NO firma, NO disclaimer>"
  }
- Estructura sugerida del cuerpo:
  Párrafo 1: Felicitación corta y específica (mencionar el plan + cliente).
  Párrafo 2: UN coaching concreto del próximo movimiento basado en el plan/contexto del aliado.
    - Si el plan fue chico (Plan Base): sugerir prospectar para uno mediano usando este caso.
    - Si fue mediano (Plan Pro): sugerir red de referidos del cliente cerrado.
    - Si fue grande (Industrial/Estratégico): sugerir documentar el caso para usar en próximos pitches.
    - Si es la PRIMERA venta del aliado: enfatizar que el siguiente lead caliente debe entrar en el pipeline esta semana.
  Párrafo 3 (opcional): Una pregunta retórica o llamado a la acción concreto.
- NO uses emojis.
- NO incluyas mensaje de qué se le abonó (eso ya está en otra parte del email)."""

def personalizar_email_venta_cerrada_ia(*, aliado_nombre: str,
                                            cliente_nombre: str,
                                            plan: str,
                                            comision_usd: float,
                                            es_primera_venta: bool = False,
                                            ventas_totales_aliado: Optional[int] = None) -> Optional[dict]:
    """
    Devuelve {asunto, cuerpo_texto} para el email de notificación de venta cerrada.
    El caller envuelve `cuerpo_texto` en el HTML del email transaccional.
    """
    if not is_enabled():
        return None

    bloques = [
        f"Aliado: {aliado_nombre}",
        f"Cliente que cerró: {cliente_nombre}",
        f"Plan vendido: {plan}",
        f"Comisión: USD {int(comision_usd)}",
    ]
    if es_primera_venta:
        bloques.append("CONTEXTO IMPORTANTE: es la PRIMERA venta del aliado.")
    elif ventas_totales_aliado is not None:
        bloques.append(f"Ventas totales históricas del aliado: {ventas_totales_aliado}")

    user = "Generá asunto + cuerpo del email de venta cerrada:\n\n" + "\n".join(bloques)

    raw = _chat(user, _EMAIL_VENTA_SYSTEM,
                model=GROQ_MODEL_FAST,
                max_tokens=500,
                temperature=0.6,
                json_mode=True)
    if not raw: return None
    obj = _extract_json(raw)
    if not obj: return None

    asunto = str(obj.get("asunto", "")).strip()
    cuerpo = str(obj.get("cuerpo", "")).strip()
    if not asunto or not cuerpo:
        return None
    if len(asunto) > 120:
        asunto = asunto[:117].rstrip() + "..."
    return {"asunto": asunto, "cuerpo_texto": cuerpo}


_EMAIL_LEAD_LIBERADO_SYSTEM = """Sos un coach de ventas que escribe emails honestos pero constructivos.
Un aliado reclamó un lead de la bolsa y dejó pasar 48 horas sin contactarlo, así que el sistema
se lo quitó y lo devolvió a la bolsa para otros aliados.

Tu trabajo: avisarle de la pérdida sin hacer drama, identificar el patrón si hay datos para hacerlo,
y darle un consejo concreto para no repetirlo.

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "asunto": "<asunto en español rioplatense, máximo 70 caracteres, sin signos de admiración>",
    "cuerpo": "<cuerpo en TEXTO PLANO, 3 párrafos cortos. NO HTML, NO firma, NO disclaimer>"
  }
- Estructura del cuerpo:
  Párrafo 1: Avisar que el lead se liberó. Una frase. No te disculpes en nombre del sistema.
  Párrafo 2: Si hay patrón (ej: ya pasó antes), nombrarlo respetuosamente. Si es la primera vez, no inventar uno.
  Párrafo 3: UN consejo accionable concreto para la próxima vez (ej: 'reclamá solo si podés contactar en las primeras 4hs', 'configurá un recordatorio en tu calendario al reclamar').
- NO seas pasivo-agresivo.
- NO uses emojis ni signos de admiración.
- NO escribas un sermón."""

def personalizar_email_lead_liberado_ia(*, aliado_nombre: str,
                                            lead_empresa: str,
                                            lead_rubro: Optional[str] = None,
                                            leads_perdidos_previos: int = 0,
                                            leads_exitosos_previos: int = 0) -> Optional[dict]:
    """
    Devuelve {asunto, cuerpo_texto} para el email de lead liberado por timeout.
    """
    if not is_enabled():
        return None

    bloques = [
        f"Aliado: {aliado_nombre}",
        f"Empresa del lead que se liberó: {lead_empresa}",
    ]
    if lead_rubro:
        bloques.append(f"Rubro del lead: {lead_rubro}")
    bloques.append(f"Leads perdidos por timeout antes de este: {leads_perdidos_previos}")
    bloques.append(f"Leads cerrados exitosamente antes: {leads_exitosos_previos}")
    if leads_perdidos_previos == 0:
        bloques.append("CONTEXTO: es la PRIMERA vez que pierde un lead por timeout.")
    elif leads_perdidos_previos >= 2:
        bloques.append("CONTEXTO: hay PATRÓN — ya perdió varios por la misma causa.")

    user = "Generá asunto + cuerpo del email de lead liberado:\n\n" + "\n".join(bloques)

    raw = _chat(user, _EMAIL_LEAD_LIBERADO_SYSTEM,
                model=GROQ_MODEL_FAST,
                max_tokens=450,
                temperature=0.55,
                json_mode=True)
    if not raw: return None
    obj = _extract_json(raw)
    if not obj: return None

    asunto = str(obj.get("asunto", "")).strip()
    cuerpo = str(obj.get("cuerpo", "")).strip()
    if not asunto or not cuerpo:
        return None
    if len(asunto) > 120:
        asunto = asunto[:117].rstrip() + "..."
    return {"asunto": asunto, "cuerpo_texto": cuerpo}


# ════════════════════════════════════════════════════════════════════════════
#   FUNCIÓN 10 — Coach de onboarding (Prioridad #10)
# ════════════════════════════════════════════════════════════════════════════

_COACH_ONBOARDING_SYSTEM = """Sos un coach de ventas B2B en Argentina, mentor de aliados nuevos en una red comercial.
Tu trabajo: dado el estado actual del onboarding y la actividad de un aliado,
darle un consejo BREVE, específico y accionable para destrabar su próximo paso.

REGLAS DURAS:
- Devolvé EXACTAMENTE este JSON, sin nada más:
  {
    "diagnostico": "<1 oración honesta sobre dónde está parado el aliado, máximo 22 palabras>",
    "siguiente_paso": "<la UNA acción concreta que debería hacer hoy/esta semana, máximo 25 palabras>",
    "razon": "<por qué esa acción y no otra, máximo 18 palabras>",
    "plantilla": "<si la acción requiere un mensaje/prospecto/pitch, dar una plantilla mínima lista para usar (1-3 oraciones). Si no aplica, string vacío.>"
  }
- Hablale directo como un mentor, no con frases cliché ('seguí adelante', 'tu éxito está cerca').
- NO motivacional vacío. Datos concretos del aliado en juego.
- Adaptá según el cuello de botella REAL:
  - Sin prospectos → diagnóstico: 'no estás generando inputs'. Acción: cargar X prospectos hoy.
  - Con prospectos pero sin contactar → diagnóstico: 'tenés inventario muerto'. Acción: contactar al primero hoy.
  - Contactaste sin respuesta → diagnóstico: 'sin re-enganche, los leads se enfrían'. Acción: usar follow-up IA.
  - Sin reclamar leads de bolsa (canal 1) → diagnóstico: 'leads servidos sin tomar'. Acción: ir a la bolsa.
  - Sin sub-aliados → diagnóstico: 'tu red está plana'. Acción: invitar 1 conocido del rubro.
  - Cero ventas con muchos prospectos → 'pipeline lleno pero sin cierre'. Acción: revisar perfilado/objeciones."""

def coach_onboarding_ia(*, aliado_nombre: str,
                          dias_desde_registro: int,
                          es_canal2: bool,
                          tiene_prospectos: bool,
                          n_prospectos: int = 0,
                          n_prospectos_sin_contactar: int = 0,
                          n_prospectos_contactados: int = 0,
                          n_prospectos_respondio: int = 0,
                          n_leads_bolsa_reclamados: int = 0,
                          n_ventas: int = 0,
                          n_sub_aliados: int = 0,
                          ultimo_login_dias: Optional[int] = None,
                          checklist_pct: int = 0,
                          pasos_pendientes: Optional[list] = None) -> Optional[dict]:
    """
    Devuelve {diagnostico, siguiente_paso, razon, plantilla} o None si Groq falla.
    """
    if not is_enabled():
        return None

    bloques = [
        f"Aliado: {aliado_nombre}",
        f"Tipo de aliado: {'Canal 2 (sin acceso a bolsa)' if es_canal2 else 'Canal 1 (con bolsa de leads)'}",
        f"Días desde que se registró: {dias_desde_registro}",
        f"Checklist de onboarding completado: {checklist_pct}%",
    ]
    if pasos_pendientes:
        bloques.append("Pasos pendientes del checklist: " + ", ".join(pasos_pendientes[:6]))
    bloques += [
        f"Prospectos cargados: {n_prospectos}",
        f"  - Sin contactar: {n_prospectos_sin_contactar}",
        f"  - Contactados: {n_prospectos_contactados}",
        f"  - Respondieron: {n_prospectos_respondio}",
    ]
    if not es_canal2:
        bloques.append(f"Leads reclamados de la bolsa: {n_leads_bolsa_reclamados}")
    bloques += [
        f"Ventas cerradas: {n_ventas}",
        f"Sub-aliados invitados: {n_sub_aliados}",
    ]
    if ultimo_login_dias is not None:
        bloques.append(f"Días desde el último login: {ultimo_login_dias}")

    user = "Diagnosticá el estado del aliado y devolveme el consejo:\n\n" + "\n".join(bloques)

    raw = _chat(user, _COACH_ONBOARDING_SYSTEM,
                model=GROQ_MODEL_FAST,
                max_tokens=400,
                temperature=0.45,
                json_mode=True)
    if not raw: return None
    obj = _extract_json(raw)
    if not obj: return None

    diag  = str(obj.get("diagnostico", "")).strip()
    nxt   = str(obj.get("siguiente_paso", "")).strip()
    razon = str(obj.get("razon", "")).strip()
    plant = str(obj.get("plantilla", "")).strip()

    if not diag or not nxt:
        return None
    return {
        "diagnostico": diag,
        "siguiente_paso": nxt,
        "razon": razon or "Es lo de mayor leverage en tu situación actual.",
        "plantilla": plant,
    }
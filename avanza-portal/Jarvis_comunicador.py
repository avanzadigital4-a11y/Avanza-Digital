"""
jarvis_comunicador.py — Módulo 4: Comunicador Inteligente

Los 13 tipos de comunicación del Blueprint v2, más el sistema de
aprendizaje de estilo del aliado.

FUNCIONES:
  email_primer_contacto()     → 3 variantes de email frío (directo / técnico / consultivo)
  email_seguimiento()         → Seguimiento post-reunión o post-envío de propuesta
  email_reactivacion()        → Reactiva leads dormidos (personalizado por tiempo dormido)
  email_cierre()              → Email de cierre con urgencia real, no artificial
  email_objecion_especifica() → Respuesta a una objeción puntual recibida por email
  whatsapp_prospeccion()      → Mensaje de prospección ≤60 palabras
  whatsapp_seguimiento()      → Seguimiento cálido por WhatsApp
  linkedin_prospeccion()      → Mensaje de prospección en LinkedIn para decisores
  linkedin_comentario()       → Comentario estratégico en una publicación del prospecto
  propuesta_reunion()         → Pedido de reunión por cualquier canal
  respuesta_solicitud_cotizacion() → Respuesta a un RFQ / solicitud de precio
  agradecimiento_postcierre() → Mensaje que siembra la próxima venta
  pedido_referido()           → Cómo pedirle al cliente que recomiende, sin incomodar

SISTEMA DE ESTILO:
  registrar_edicion_estilo()  → Trackea cuando el aliado edita una comunicación generada
  obtener_perfil_estilo()     → Devuelve el perfil de estilo aprendido del aliado

DISEÑO:
  Mismo patrón que el resto de los módulos JARVIS: si ANTHROPIC_API_KEY no
  está o falla, todas las funciones devuelven None. El producto nunca se cae.
  Timeout: 20 segundos.

  Integración en main.py:
      import jarvis_comunicador
      jarvis_comunicador.register(app, get_db, current_aliado_required)
"""

from __future__ import annotations
import os, json, sys
from typing import Optional

# ─── CONFIG ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 20.0

# Límite de palabras para WhatsApp según el Blueprint
WA_MAX_PALABRAS = 60


def is_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


# ─── HELPERS INTERNOS ────────────────────────────────────────────────────────

def _chat(
    prompt: str,
    system: str,
    *,
    max_tokens: int = 1200,
    temperature: float = 0.45,
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
        print(f"[JARVIS COMUNICADOR ERROR] {type(e).__name__}: {e}", file=sys.stderr)
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
    print(f"[JARVIS COMUNICADOR] No se pudo parsear JSON: {text[:200]}", file=sys.stderr)
    return None


def _perfil_estilo_str(perfil_estilo: dict | None) -> str:
    """Convierte el perfil de estilo del aliado en instrucciones para el prompt."""
    if not perfil_estilo:
        return "Sin perfil de estilo específico — usá tono profesional rioplatense."
    partes = []
    if perfil_estilo.get("longitud_preferida"):
        partes.append(f"Longitud preferida: {perfil_estilo['longitud_preferida']} palabras.")
    if perfil_estilo.get("usa_emojis") is False:
        partes.append("Sin emojis.")
    elif perfil_estilo.get("usa_emojis") is True:
        partes.append("Puede usar emojis con moderación.")
    if perfil_estilo.get("prefiere_preguntas_abiertas"):
        partes.append("CTA preferida: pregunta abierta, no 'podemos avanzar'.")
    if perfil_estilo.get("evita"):
        evitar = ", ".join(f'"{e}"' for e in perfil_estilo["evita"])
        partes.append(f"Frases a EVITAR: {evitar}.")
    if perfil_estilo.get("tono_extra"):
        partes.append(f"Tono adicional: {perfil_estilo['tono_extra']}.")
    return " ".join(partes) if partes else "Sin restricciones de estilo específicas."


# ═══════════════════════════════════════════════════════════════════════════════
# MÓDULO 4 — LOS 13 TIPOS DE COMUNICACIÓN
# ═══════════════════════════════════════════════════════════════════════════════


# ── 1. Email de primer contacto frío ─────────────────────────────────────────

def email_primer_contacto(
    empresa_prospecto: str,
    sector: str,
    nombre_contacto: str = "",
    cargo_contacto: str = "",
    *,
    dolor_detectado: str = "",
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    aliado_rubros: list = None,
    aliado_ventas: int = 0,
    perfil_estilo: dict = None,
) -> Optional[dict]:
    """
    Tipo 1 — 3 variantes de email de primer contacto frío.
    Variantes: directa, técnica y consultiva.

    Retorna:
        {
          "variante_directa":   {"asunto": str, "cuerpo": str},
          "variante_tecnica":   {"asunto": str, "cuerpo": str},
          "variante_consultiva":{"asunto": str, "cuerpo": str},
          "recomendacion": str,
          "tip_envio": str,
        }
    """
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "industrial"
    estilo_str = _perfil_estilo_str(perfil_estilo)

    prompt = f"""
Necesito 3 variantes de email de primer contacto frío para este prospecto.

PROSPECTO:
- Empresa: {empresa_prospecto}
- Sector: {sector}
- Contacto: {nombre_contacto or "decisor principal"} — {cargo_contacto or "cargo no definido"}
- Dolor detectado / contexto: {dolor_detectado or "no especificado — inferí basándote en el sector"}

ALIADO QUE ENVÍA:
- Nombre: {aliado_nombre or "el aliado de Avanza"}
- Ciudad: {aliado_ciudad or "Argentina"}
- Sectores en los que opera: {rubros_str}
- Ventas confirmadas: {aliado_ventas}

Avanza Digital ofrece: presencia web B2B, SEO, generación de leads, sistemas
de marketing digital para PYMES industriales.

PERFIL DE ESTILO DEL ALIADO: {estilo_str}

Cada variante debe:
- Ser en español rioplatense
- No empezar con "Espero que estés bien" ni "Mi nombre es..."
- Tener asunto específico (no genérico)
- Mencionar algo concreto del sector del prospecto
- Tener una CTA de bajo compromiso (15 minutos, no "¿podemos avanzar?")
- Entre 80-120 palabras en el cuerpo

Las 3 variantes difieren en el ángulo:
1. DIRECTA: va al grano. Menciona el resultado concreto que otros del sector lograron.
2. TÉCNICA: habla el idioma del sector. Muestra que conoce el proceso del prospecto.
3. CONSULTIVA: hace una pregunta inteligente sobre el negocio del prospecto.

Devolvé este JSON:
{{
  "variante_directa": {{
    "asunto": "<asunto específico y directo>",
    "cuerpo": "<cuerpo del email — 80-120 palabras, español rioplatense>"
  }},
  "variante_tecnica": {{
    "asunto": "<asunto con vocabulario técnico del sector>",
    "cuerpo": "<cuerpo técnico — 80-120 palabras>"
  }},
  "variante_consultiva": {{
    "asunto": "<asunto que genera curiosidad con una pregunta>",
    "cuerpo": "<cuerpo consultivo — 80-120 palabras>"
  }},
  "recomendacion": "<cuál de las 3 vas primero y por qué — basado en el perfil del contacto>",
  "tip_envio": "<mejor momento y canal para el primer contacto con este perfil>"
}}
"""

    system = """Sos el Módulo de Comunicador Inteligente de JARVIS para Avanza Digital.
Escribís emails que se leen porque son específicos, relevantes y no suenan a template.
Cada email suena como una persona real con contexto, no como un bot de ventas.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1800, temperature=0.5, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 2. Email de seguimiento ───────────────────────────────────────────────────

def email_seguimiento(
    empresa_prospecto: str,
    nombre_contacto: str = "",
    *,
    motivo_seguimiento: str = "post-reunión",
    dias_sin_respuesta: int = 0,
    ultimo_contacto: str = "",
    propuesta_enviada: bool = False,
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    perfil_estilo: dict = None,
) -> Optional[dict]:
    """
    Tipo 2 — Email de seguimiento.
    Adapta el tono según cuántos días pasaron y qué ocurrió antes.

    Retorna:
        {
          "asunto": str,
          "cuerpo": str,
          "variante_whatsapp": str,   # ≤60 palabras
          "tip_seguimiento": str,
        }
    """
    estilo_str = _perfil_estilo_str(perfil_estilo)
    contexto_dias = ""
    if dias_sin_respuesta > 0:
        if dias_sin_respuesta <= 3:
            contexto_dias = "Pasaron pocos días — el tono debe ser liviano, sin presionar."
        elif dias_sin_respuesta <= 7:
            contexto_dias = "Una semana sin respuesta — tono neutro, recordar sin agobiar."
        elif dias_sin_respuesta <= 14:
            contexto_dias = "Dos semanas — mencionar brevemente la propuesta, preguntar si hay dudas."
        else:
            contexto_dias = f"{dias_sin_respuesta} días sin respuesta — usar reactivación, no seguimiento simple."

    prompt = f"""
Escribí un email de seguimiento.

PROSPECTO: {empresa_prospecto} — {nombre_contacto or "el contacto"}
MOTIVO: {motivo_seguimiento}
DÍAS SIN RESPUESTA: {dias_sin_respuesta} {contexto_dias}
ÚLTIMO CONTACTO / CONTEXTO: {ultimo_contacto or "no especificado"}
¿SE ENVIÓ PROPUESTA?: {"Sí" if propuesta_enviada else "No"}

ALIADO: {aliado_nombre or "el aliado"} ({aliado_ciudad or "Argentina"})
ESTILO: {estilo_str}

El email debe:
- Referenciar algo concreto del contacto anterior (no "te contacto de nuevo")
- No sonar desesperado ni robotizado
- Tener una CTA específica y de bajo compromiso
- Ser entre 80-130 palabras
- Español rioplatense

También generá una versión para WhatsApp de máximo 60 palabras.

Devolvé este JSON:
{{
  "asunto": "<asunto que hace referencia al contacto previo>",
  "cuerpo": "<cuerpo del email — 80-130 palabras>",
  "variante_whatsapp": "<versión ≤60 palabras para WhatsApp>",
  "tip_seguimiento": "<cuándo y cómo enviarlo — consejo del aliado>"
}}
"""

    system = """Sos el Módulo de Seguimiento de JARVIS para Avanza Digital.
Escribís emails de seguimiento que no parecen copy-paste de un CRM.
Cada seguimiento tiene que tener una razón real para existir — no solo "ver si vieron la propuesta".
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.45, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 3. Email de reactivación ──────────────────────────────────────────────────

def email_reactivacion(
    empresa_prospecto: str,
    nombre_contacto: str = "",
    *,
    dias_dormido: int,
    ultimo_motivo_perdida: str = "",
    sector: str = "",
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    perfil_estilo: dict = None,
) -> Optional[dict]:
    """
    Tipo 3 — Reactivación de leads dormidos.
    La estrategia cambia según el tiempo que lleva dormido el lead.

    Retorna:
        {
          "estrategia": str,
          "asunto": str,
          "cuerpo": str,
          "variante_whatsapp": str,
          "angulo_recomendado": str,
        }
    """
    estilo_str = _perfil_estilo_str(perfil_estilo)

    if dias_dormido <= 30:
        angulo = "cambio de contexto — algo nuevo que pasó en el mercado o en Avanza que justifica volver"
    elif dias_dormido <= 90:
        angulo = "nuevo ángulo — no repetir el mismo argumento, presentar un beneficio diferente"
    else:
        angulo = "inicio desde cero — reconocer el tiempo, no pedir disculpas, mostrar valor nuevo sin hacer referencia al intento anterior fallido"

    prompt = f"""
Escribí un email de reactivación para un lead dormido.

PROSPECTO: {empresa_prospecto} — {nombre_contacto or "el contacto"}
SECTOR: {sector or "industrial"}
DÍAS DORMIDO: {dias_dormido} días
ÚLTIMO MOTIVO (si se conoce): {ultimo_motivo_perdida or "desconocido — inferí la razón más probable"}
ÁNGULO SUGERIDO: {angulo}

ALIADO: {aliado_nombre or "el aliado"} ({aliado_ciudad or "Argentina"})
ESTILO: {estilo_str}

Regla clave: NO repetir el mismo argumento del primer contacto.
Si el lead está dormido, el argumento anterior no funcionó — hay que entrar por otro lado.

El email debe:
- Tener un ángulo nuevo y concreto (no "quería ver si seguís interesado")
- Ser entre 70-100 palabras
- Sonar humano, sin urgencia artificial
- Tener una CTA de muy bajo compromiso

Devolvé este JSON:
{{
  "estrategia": "<por qué este ángulo y no otro — justificación táctica en 1-2 líneas>",
  "asunto": "<asunto que no repita el anterior>",
  "cuerpo": "<cuerpo del email — 70-100 palabras>",
  "variante_whatsapp": "<versión ≤60 palabras para WhatsApp>",
  "angulo_recomendado": "<descripción del ángulo elegido para que el aliado lo entienda>"
}}
"""

    system = """Sos el Módulo de Reactivación de JARVIS para Avanza Digital.
Reactivás leads fríos con ángulos nuevos — nunca repetiendo el mismo argumento que no funcionó.
Sabés que la mejor reactivación parece que no es una reactivación.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.5, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 4. Email de cierre ────────────────────────────────────────────────────────

def email_cierre(
    empresa_prospecto: str,
    nombre_contacto: str = "",
    *,
    contexto_negociacion: str = "",
    objecion_pendiente: str = "",
    urgencia_real: str = "",
    plan_discutido: str = "",
    aliado_nombre: str = "",
    perfil_estilo: dict = None,
) -> Optional[dict]:
    """
    Tipo 4 — Email de cierre con urgencia real (no artificial).
    La urgencia debe surgir del contexto del negocio del prospecto, no del cupo.

    Retorna:
        {
          "asunto": str,
          "cuerpo": str,
          "urgencia_fundada": str,
          "plan_b": str,       # si el prospecto no responde
          "variante_whatsapp": str,
        }
    """
    estilo_str = _perfil_estilo_str(perfil_estilo)

    prompt = f"""
Escribí el email de cierre de esta negociación.

PROSPECTO: {empresa_prospecto} — {nombre_contacto or "el decisor"}
CONTEXTO DE LA NEGOCIACIÓN: {contexto_negociacion or "conversación avanzada, interés confirmado"}
OBJECIÓN PENDIENTE (si hay): {objecion_pendiente or "ninguna declarada"}
URGENCIA REAL DEL PROSPECTO: {urgencia_real or "no declarada — inferí una urgencia real del contexto, no inventada"}
PLAN DISCUTIDO: {plan_discutido or "Plan Avanza a definir"}

ALIADO: {aliado_nombre or "el aliado"}
ESTILO: {estilo_str}

REGLA FUNDAMENTAL: La urgencia debe surgir del negocio del prospecto, no de
"últimos cupos" o "precio que sube mañana". Si no hay urgencia real clara,
usá el costo de oportunidad de seguir sin el sistema.

El email debe:
- Resumir en 1 línea el valor acordado
- Si hay una objeción pendiente, resolverla sin extenderse
- Proponer el próximo paso concreto (firma, llamada de 15 min, start date)
- Ser entre 100-140 palabras

Devolvé este JSON:
{{
  "asunto": "<asunto de cierre — concreto y que genera acción>",
  "cuerpo": "<cuerpo del email — 100-140 palabras>",
  "urgencia_fundada": "<cuál es la urgencia real usada y por qué es legítima>",
  "plan_b": "<qué hace el aliado si no hay respuesta en 48hs — táctica concreta>",
  "variante_whatsapp": "<versión ≤60 palabras para WhatsApp>"
}}
"""

    system = """Sos el Módulo de Cierre de JARVIS para Avanza Digital.
Generás emails de cierre que no suenan a presión de vendedor de feria.
La urgencia siempre viene del negocio del prospecto, nunca es artificial.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.4, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 5. Email de gestión de objeción específica ────────────────────────────────

def email_objecion_especifica(
    empresa_prospecto: str,
    objecion_textual: str,
    nombre_contacto: str = "",
    *,
    contexto_previo: str = "",
    sector: str = "",
    aliado_nombre: str = "",
    perfil_estilo: dict = None,
) -> Optional[dict]:
    """
    Tipo 5 — Respuesta por email a una objeción específica recibida.

    Retorna:
        {
          "clasificacion_objecion": str,
          "asunto": str,
          "cuerpo": str,
          "estrategia_usada": str,
          "variante_whatsapp": str,
        }
    """
    estilo_str = _perfil_estilo_str(perfil_estilo)

    prompt = f"""
El prospecto planteó esta objeción. Escribí la respuesta por email.

PROSPECTO: {empresa_prospecto} — {nombre_contacto or "el contacto"}
SECTOR: {sector or "industrial"}
OBJECIÓN RECIBIDA (textual o parafraseada):
"{objecion_textual}"
CONTEXTO PREVIO: {contexto_previo or "no especificado"}

ALIADO: {aliado_nombre or "el aliado"}
ESTILO: {estilo_str}

Antes de escribir el email, clasificá la objeción real:
- PRECIO: "es caro / no tenemos presupuesto"
- TIEMPO: "no es el momento / estamos ocupados"
- PROVEEDOR: "ya tenemos alguien / no queremos cambiar"
- DUDA DE RESULTADO: "no sé si funciona para nosotros"
- PROCESO: "necesito consultarlo / no puedo decidir solo"
- EVASIÓN: "mandame info / lo vemos después"

La respuesta debe:
- Validar la objeción sin ceder en el valor
- Replantear la perspectiva con un dato o pregunta concreta
- No sonar defensiva ni desesperada
- Ser entre 80-120 palabras
- Terminar con una pregunta o propuesta de siguiente paso

Devolvé este JSON:
{{
  "clasificacion_objecion": "<tipo de objeción real detectada>",
  "asunto": "<asunto que retoma la conversación de forma natural>",
  "cuerpo": "<respuesta al email — 80-120 palabras>",
  "estrategia_usada": "<qué técnica de manejo de objeción se aplicó y por qué>",
  "variante_whatsapp": "<versión ≤60 palabras para WhatsApp>"
}}
"""

    system = """Sos el Módulo de Gestión de Objeciones de JARVIS para Avanza Digital.
Respondés a objeciones con lógica, no con scripts genéricos.
La respuesta valida el punto del prospecto y lo replantea — sin atacarlo, sin ceder.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.4, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 6. WhatsApp de prospección ────────────────────────────────────────────────

def whatsapp_prospeccion(
    empresa_prospecto: str,
    sector: str,
    nombre_contacto: str = "",
    *,
    dolor_detectado: str = "",
    casos_similares: str = "",
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
) -> Optional[dict]:
    """
    Tipo 6 — Mensaje de prospección WhatsApp. MÁXIMO 60 palabras. Siempre.

    Retorna:
        {
          "mensaje": str,           # ≤60 palabras
          "conteo_palabras": int,
          "variantes": list[str],   # 2 variantes adicionales también ≤60 palabras
          "tip_horario": str,
        }
    """
    prompt = f"""
Escribí un mensaje de prospección para WhatsApp. MÁXIMO 60 palabras — contá cada palabra.

PROSPECTO: {empresa_prospecto} ({sector}) — {nombre_contacto or "decisor"}
DOLOR O CONTEXTO: {dolor_detectado or "no especificado — inferí del sector"}
CASOS SIMILARES (si hay): {casos_similares or "no especificados"}
ALIADO: {aliado_nombre or "el aliado"} ({aliado_ciudad or "Argentina"})

Avanza Digital: presencia web B2B, SEO, leads, marketing digital para PYMES industriales.

REGLAS ABSOLUTAS:
- Máximo 60 palabras. No 61. No 65. 60.
- No empezar con "Hola, mi nombre es..." ni con "¿Cómo estás?"
- Tiene que haber una razón concreta para el mensaje (resultado, caso, noticia del sector)
- CTA de muy bajo compromiso (15 minutos, una pregunta, no "¿querés una reunión?")
- Sin emojis si el sector es técnico/industrial formal

Generá también 2 variantes, también ≤60 palabras, con un ángulo diferente.

Devolvé este JSON:
{{
  "mensaje": "<mensaje principal ≤60 palabras>",
  "conteo_palabras": <número exacto de palabras del mensaje principal>,
  "variantes": [
    "<variante 1 ≤60 palabras — ángulo diferente>",
    "<variante 2 ≤60 palabras — ángulo diferente>"
  ],
  "tip_horario": "<mejor hora y día para enviar a este perfil de sector>"
}}
"""

    system = """Sos el Módulo de WhatsApp Comercial de JARVIS para Avanza Digital.
Escribís mensajes de WhatsApp que tienen menos de 60 palabras y que generan respuesta.
Sabés que en WhatsApp la brevedad es respeto — un mensaje largo se ignora.
Contás las palabras antes de responder. Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=800, temperature=0.5, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 7. WhatsApp de seguimiento cálido ────────────────────────────────────────

def whatsapp_seguimiento(
    empresa_prospecto: str,
    nombre_contacto: str = "",
    *,
    contexto_previo: str = "",
    dias_desde_contacto: int = 0,
    aliado_nombre: str = "",
) -> Optional[dict]:
    """
    Tipo 7 — Seguimiento cálido por WhatsApp. También ≤60 palabras.

    Retorna:
        {
          "mensaje": str,
          "conteo_palabras": int,
          "tip": str,
        }
    """
    prompt = f"""
Escribí un mensaje de seguimiento para WhatsApp. MÁXIMO 60 palabras.

PROSPECTO: {empresa_prospecto} — {nombre_contacto or "el contacto"}
CONTEXTO PREVIO: {contexto_previo or "se tuvo un primer contacto"}
DÍAS DESDE EL ÚLTIMO CONTACTO: {dias_desde_contacto or "no especificado"}
ALIADO: {aliado_nombre or "el aliado"}

El mensaje debe:
- Referenciar el contacto anterior de forma natural (no "te escribía para hacer un seguimiento")
- Sonar como persona, no como bot de ventas
- Tener un pie para continuar la conversación
- MÁXIMO 60 palabras

Devolvé este JSON:
{{
  "mensaje": "<mensaje ≤60 palabras>",
  "conteo_palabras": <número exacto>,
  "tip": "<cuándo enviarlo y qué hacer si no responde>"
}}
"""

    system = """Sos el Módulo de Seguimiento WhatsApp de JARVIS para Avanza Digital.
Tus mensajes de seguimiento parecen escritos por una persona, no por un CRM.
Máximo 60 palabras. Siempre. Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=500, temperature=0.5, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 8. LinkedIn de prospección ────────────────────────────────────────────────

def linkedin_prospeccion(
    empresa_prospecto: str,
    nombre_contacto: str,
    cargo_contacto: str = "",
    sector: str = "",
    *,
    perfil_linkedin_info: str = "",
    aliado_nombre: str = "",
    aliado_empresa: str = "",
) -> Optional[dict]:
    """
    Tipo 8 — Mensaje de prospección en LinkedIn para decisores.
    LinkedIn tiene un límite de caracteres — el mensaje debe funcionar dentro de ese límite.

    Retorna:
        {
          "mensaje_conexion": str,     # nota de conexión ≤300 caracteres
          "mensaje_post_conexion": str,# primer mensaje después de conectar ≤500 caracteres
          "tip_perfil": str,
        }
    """
    prompt = f"""
Escribí el mensaje de prospección para LinkedIn.

PROSPECTO: {nombre_contacto} — {cargo_contacto or "decisor"} en {empresa_prospecto}
SECTOR: {sector or "industrial"}
INFO DEL PERFIL LINKEDIN (si se tiene): {perfil_linkedin_info or "no disponible"}

ALIADO: {aliado_nombre or "el aliado"} ({aliado_empresa or "Avanza Digital"})
Avanza Digital: presencia web B2B, SEO, generación de leads para PYMES industriales.

Generá 2 mensajes:
1. NOTA DE CONEXIÓN (cuando manda la solicitud de conexión): máximo 300 caracteres.
   Debe dar una razón concreta para conectar — no "quiero ampliar mi red".

2. MENSAJE POST-CONEXIÓN (después de que acepta): máximo 500 caracteres.
   Entra al punto de forma directa. Referencia algo específico del prospecto o su sector.
   No empieza con "Gracias por conectar".

Devolvé este JSON:
{{
  "mensaje_conexion": "<nota de conexión ≤300 caracteres>",
  "mensaje_post_conexion": "<mensaje post-conexión ≤500 caracteres>",
  "tip_perfil": "<cómo preparar el perfil del aliado antes de enviar — qué mirar del prospecto>"
}}
"""

    system = """Sos el Módulo LinkedIn de JARVIS para Avanza Digital.
Escribís mensajes de LinkedIn que no parecen spam de ventas.
La clave: personalización real + brevedad + razón concreta para conectar.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=700, temperature=0.5, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 9. Comentario estratégico en LinkedIn ─────────────────────────────────────

def linkedin_comentario(
    publicacion_texto: str,
    empresa_prospecto: str,
    nombre_autor: str = "",
    *,
    sector: str = "",
    aliado_nombre: str = "",
) -> Optional[dict]:
    """
    Tipo 9 — Comentario estratégico en una publicación del prospecto en LinkedIn.
    El comentario debe agregar valor, no vender directamente.

    Retorna:
        {
          "comentario": str,
          "angulo": str,
          "tip": str,
        }
    """
    prompt = f"""
Escribí un comentario para esta publicación de LinkedIn de un prospecto.

AUTOR: {nombre_autor or "el prospecto"} de {empresa_prospecto}
SECTOR: {sector or "industrial"}

PUBLICACIÓN:
---
{publicacion_texto}
---

ALIADO QUE VA A COMENTAR: {aliado_nombre or "el aliado de Avanza Digital"}

El comentario debe:
- Agregar valor real a lo que dijo el prospecto (no "¡Excelente publicación!")
- Mostrar que el aliado conoce el sector
- Ser entre 2-4 líneas
- Abrir naturalmente una puerta para una conversación posterior
- NO mencionar Avanza Digital ni intentar vender en el comentario

Devolvé este JSON:
{{
  "comentario": "<comentario estratégico — 2-4 líneas, sin mencionar el producto>",
  "angulo": "<qué ángulo usó el comentario y por qué abre una conversación>",
  "tip": "<qué hacer después de comentar — próximo paso sugerido>"
}}
"""

    system = """Sos el Módulo de LinkedIn Estratégico de JARVIS para Avanza Digital.
Escribís comentarios que muestran que el aliado sabe del sector — sin vender directamente.
El comentario perfecto genera curiosidad sobre quién lo escribió.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=600, temperature=0.5, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 10. Propuesta de reunión ──────────────────────────────────────────────────

def propuesta_reunion(
    empresa_prospecto: str,
    nombre_contacto: str = "",
    *,
    canal: str = "email",     # email | whatsapp | linkedin
    contexto: str = "",
    duracion_minutos: int = 15,
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    perfil_estilo: dict = None,
) -> Optional[dict]:
    """
    Tipo 10 — Propuesta de reunión por cualquier canal.

    Retorna:
        {
          "mensaje": str,
          "alternativas_horario": list[str],   # 2-3 opciones para facilitar el sí
          "tip": str,
        }
    """
    estilo_str = _perfil_estilo_str(perfil_estilo)

    prompt = f"""
Escribí un mensaje para proponer una reunión de {duracion_minutos} minutos.

PROSPECTO: {empresa_prospecto} — {nombre_contacto or "el decisor"}
CANAL: {canal}
CONTEXTO (por qué tiene sentido la reunión ahora): {contexto or "interés detectado en el producto"}
ALIADO: {aliado_nombre or "el aliado"} ({aliado_ciudad or "Argentina"})
ESTILO: {estilo_str}

Reglas según el canal:
- EMAIL: puede ser un poco más largo (80-100 palabras), con asunto propio
- WHATSAPP: máximo 60 palabras, sin asunto
- LINKEDIN: máximo 300 caracteres

El mensaje debe:
- Tener una razón concreta para la reunión (no "quiero presentarme")
- Proponer una duración específica (ya fijada: {duracion_minutos} min)
- Dar 2-3 opciones de horario para facilitar el sí

Si el canal es EMAIL, incluí un campo "asunto".

Devolvé este JSON:
{{
  "asunto": "<asunto del email — o null si no es email>",
  "mensaje": "<mensaje adaptado al canal>",
  "alternativas_horario": [
    "<opción 1 de horario — concreta>",
    "<opción 2>",
    "<opción 3>"
  ],
  "tip": "<cómo manejar la respuesta — si acepta / si propone otro horario / si ignora>"
}}
"""

    system = """Sos el Módulo de Agendamiento de JARVIS para Avanza Digital.
Proponés reuniones que se aceptan porque tienen una razón real y son de bajo compromiso.
La mejor propuesta de reunión no parece una propuesta de reunión.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=700, temperature=0.45, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 11. Respuesta a solicitud de cotización ───────────────────────────────────

def respuesta_solicitud_cotizacion(
    empresa_prospecto: str,
    solicitud_texto: str,
    nombre_contacto: str = "",
    *,
    plan_a_proponer: str = "",
    incluir_precio: bool = False,
    precio: float = 0,
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    perfil_estilo: dict = None,
) -> Optional[dict]:
    """
    Tipo 11 — Respuesta a un RFQ / solicitud de precio / cotización técnica.

    Retorna:
        {
          "asunto": str,
          "cuerpo": str,
          "estrategia_precio": str,   # cómo manejar el precio si lo piden directo
          "variante_whatsapp": str,
        }
    """
    estilo_str = _perfil_estilo_str(perfil_estilo)
    precio_str = f"${precio:,.0f} ARS/mes" if precio > 0 else "a definir según el diagnóstico"

    prompt = f"""
El prospecto solicitó una cotización. Escribí la respuesta.

PROSPECTO: {empresa_prospecto} — {nombre_contacto or "el contacto"}
SOLICITUD RECIBIDA:
---
{solicitud_texto}
---

PLAN A PROPONER: {plan_a_proponer or "el más adecuado para su perfil"}
¿INCLUIR PRECIO EN EL EMAIL?: {"Sí — precio: " + precio_str if incluir_precio else "No — guiar hacia la reunión de diagnóstico primero"}
ALIADO: {aliado_nombre or "el aliado"} ({aliado_ciudad or "Argentina"})
ESTILO: {estilo_str}

La respuesta debe:
- Confirmar que se recibió la solicitud y que se entendió el pedido
- Si se incluye precio: contextualizarlo con el valor, no solo dar el número
- Si no se incluye precio: proponer una reunión de diagnóstico como primer paso
- Mostrar que Avanza conoce el sector del prospecto
- Ser entre 100-150 palabras

Devolvé este JSON:
{{
  "asunto": "<asunto de la respuesta a la cotización>",
  "cuerpo": "<cuerpo del email — 100-150 palabras>",
  "estrategia_precio": "<cómo manejar si el prospecto insiste en el precio antes de la reunión>",
  "variante_whatsapp": "<versión ≤60 palabras para WhatsApp>"
}}
"""

    system = """Sos el Módulo de Cotizaciones de JARVIS para Avanza Digital.
Respondés a solicitudes de precio sin tirarlo al vacío — siempre con contexto de valor.
El precio sin contexto es solo un número que el prospecto va a comparar con el competidor más barato.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.4, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 12. Agradecimiento post-cierre ────────────────────────────────────────────

def agradecimiento_postcierre(
    empresa_cliente: str,
    nombre_contacto: str = "",
    *,
    plan_contratado: str = "",
    fecha_inicio: str = "",
    aliado_nombre: str = "",
    perfil_estilo: dict = None,
) -> Optional[dict]:
    """
    Tipo 12 — Mensaje de agradecimiento post-cierre que siembra la próxima venta.
    No es solo "gracias" — es el primer mensaje de la relación de largo plazo.

    Retorna:
        {
          "asunto": str,
          "cuerpo": str,
          "momento_referido": str,   # cuándo y cómo pedir referidos a este cliente
          "variante_whatsapp": str,
        }
    """
    estilo_str = _perfil_estilo_str(perfil_estilo)

    prompt = f"""
Escribí el mensaje de agradecimiento post-cierre para este nuevo cliente.

CLIENTE: {empresa_cliente} — {nombre_contacto or "el contacto"}
PLAN CONTRATADO: {plan_contratado or "Plan Avanza"}
FECHA DE INICIO: {fecha_inicio or "a confirmar"}
ALIADO: {aliado_nombre or "el aliado"}
ESTILO: {estilo_str}

El mensaje NO debe:
- Sonar como un "gracias por elegirnos" corporativo
- Ser una lista de próximos pasos técnicos
- Prometer resultados que no se pueden garantizar

El mensaje SÍ debe:
- Confirmar que la decisión fue correcta con una referencia concreta
- Establecer el próximo contacto (cuándo y para qué)
- Plantar una semilla para cuando sea el momento de pedir referidos
- Ser entre 80-120 palabras

Devolvé este JSON:
{{
  "asunto": "<asunto que celebra el inicio de la relación, no solo 'bienvenido'>",
  "cuerpo": "<cuerpo del email — 80-120 palabras>",
  "momento_referido": "<cuándo es el momento ideal para pedirle un referido a este cliente y cómo hacerlo>",
  "variante_whatsapp": "<versión ≤60 palabras para WhatsApp>"
}}
"""

    system = """Sos el Módulo Post-Cierre de JARVIS para Avanza Digital.
El mensaje de bienvenida a un cliente nuevo es la primera inversión en la relación de largo plazo.
No es un email de confirmación — es el inicio de la siguiente venta.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=900, temperature=0.5, json_mode=True)
    return _parse_json(raw) if raw else None


# ── 13. Pedido de referido ────────────────────────────────────────────────────

def pedido_referido(
    empresa_cliente: str,
    nombre_contacto: str = "",
    *,
    meses_como_cliente: int = 0,
    resultado_logrado: str = "",
    sector_objetivo: str = "",
    aliado_nombre: str = "",
    perfil_estilo: dict = None,
) -> Optional[dict]:
    """
    Tipo 13 — Cómo pedir un referido sin incomodar.
    Timing correcto + forma correcta + canal correcto.

    Retorna:
        {
          "momento_ideal": str,
          "canal_recomendado": str,
          "mensaje": str,
          "variante_whatsapp": str,
          "tip_manejo": str,         # cómo manejar "no conozco a nadie" o "sí, ¿a quién?"
        }
    """
    estilo_str = _perfil_estilo_str(perfil_estilo)

    prompt = f"""
El aliado quiere pedirle un referido a un cliente actual. Escribí el mensaje.

CLIENTE: {empresa_cliente} — {nombre_contacto or "el contacto"}
MESES COMO CLIENTE: {meses_como_cliente or "no especificado"}
RESULTADO LOGRADO (si se conoce): {resultado_logrado or "buenos resultados con el servicio"}
SECTOR AL QUE LE INTERESA EL REFERIDO: {sector_objetivo or "cualquier empresa similar"}
ALIADO: {aliado_nombre or "el aliado"}
ESTILO: {estilo_str}

Reglas del pedido de referido que no incomoda:
1. Primero validar el resultado — el cliente tiene que estar contento antes de preguntar
2. Hacer la solicitud específica (no "¿conocés a alguien?") — ¿conocés una empresa X en sector Y?
3. Hacerlo fácil: que el cliente no tenga que pensar mucho para ayudar
4. No ponerlo en la misma conversación donde se habla de facturación o problemas

El mejor timing: 60-90 días después del inicio, cuando hay un primer resultado visible.

Devolvé este JSON:
{{
  "momento_ideal": "<cuándo exactamente hacer este pedido — contexto, no solo 'cuando esté contento'>",
  "canal_recomendado": "<email|whatsapp|llamada — cuál y por qué para este cliente>",
  "mensaje": "<mensaje de pedido de referido — humano, específico, sin presión>",
  "variante_whatsapp": "<versión ≤60 palabras para WhatsApp>",
  "tip_manejo": "<qué decir si responde 'no conozco a nadie' o 'sí, ¿a quién buscan exactamente?'>"
}}
"""

    system = """Sos el Módulo de Referidos de JARVIS para Avanza Digital.
Pedís referidos de forma que el cliente quiera ayudar — no de forma que se sienta obligado.
El mejor pedido de referido es el que parece una consulta, no un pedido.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=900, temperature=0.5, json_mode=True)
    return _parse_json(raw) if raw else None


# ═══════════════════════════════════════════════════════════════════════════════
# SISTEMA DE APRENDIZAJE DE ESTILO
# ═══════════════════════════════════════════════════════════════════════════════

def registrar_edicion_estilo(
    aliado_id: int,
    tipo_comunicacion: str,
    texto_original: str,
    texto_editado: str,
    db_session,
) -> Optional[dict]:
    """
    Trackea cuando el aliado edita un texto generado por JARVIS.
    Detecta en qué dirección editó (más corto, más técnico, sin emojis, etc.)
    y retorna el delta aprendido.

    Los cambios se deben persistir en la columna jarvis_estilo_perfil del Aliado
    desde el llamador — esta función solo analiza el delta.

    Retorna:
        {
          "tipo_comunicacion": str,
          "longitud_original": int,
          "longitud_editada": int,
          "delta_longitud": int,
          "observaciones": list[str],
          "actualizar_perfil": dict,     # campos a mergear en el perfil de estilo
        }
    """
    if not texto_original or not texto_editado:
        return None

    palabras_orig = len(texto_original.split())
    palabras_edit = len(texto_editado.split())
    delta = palabras_edit - palabras_orig

    prompt = f"""
El aliado editó una comunicación generada por JARVIS. Analizá qué cambió y qué aprender.

TIPO: {tipo_comunicacion}

TEXTO ORIGINAL ({palabras_orig} palabras):
---
{texto_original[:800]}
---

TEXTO EDITADO ({palabras_edit} palabras):
---
{texto_editado[:800]}
---

DELTA DE LONGITUD: {delta:+d} palabras

Analizá:
1. ¿Se acortó o alargó? ¿Por qué probablemente?
2. ¿Cambió el tono (más formal / más cercano / más técnico)?
3. ¿Eliminó algo específico (saludos, despedidas, emojis, frases tipo)?
4. ¿Cambió el CTA (call to action)?
5. ¿Hay palabras o frases que el aliado siempre reemplaza?

Devolvé este JSON:
{{
  "observaciones": [
    "<observación 1 — qué cambió y qué significa para el estilo del aliado>",
    "<observación 2>",
    "<observación 3 si aplica>"
  ],
  "actualizar_perfil": {{
    "longitud_preferida": <número entero de palabras promedio — o null si no hay evidencia clara>,
    "usa_emojis": <true|false|null>,
    "prefiere_preguntas_abiertas": <true|false|null>,
    "evita": ["<frase o patrón que eliminó — si se detecta alguno>"],
    "tono_extra": "<descripción breve del ajuste de tono — o null>"
  }}
}}
"""

    system = """Sos el Sistema de Aprendizaje de Estilo de JARVIS para Avanza Digital.
Analizás diferencias entre textos para aprender el estilo de escritura del aliado.
Solo reportás lo que realmente cambió — no inferís si no hay evidencia.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=700, temperature=0.2, json_mode=True)
    resultado = _parse_json(raw) if raw else None

    if resultado:
        resultado["tipo_comunicacion"] = tipo_comunicacion
        resultado["longitud_original"] = palabras_orig
        resultado["longitud_editada"]  = palabras_edit
        resultado["delta_longitud"]    = delta

    return resultado


def obtener_perfil_estilo(aliado_obj) -> dict:
    """
    Extrae y retorna el perfil de estilo guardado del aliado.
    Espera que aliado_obj tenga un campo 'jarvis_estilo_perfil' (JSON string) o similar.
    Si no existe, retorna un perfil vacío por defecto.
    """
    try:
        raw = getattr(aliado_obj, "jarvis_estilo_perfil", None) or "{}"
        if isinstance(raw, str):
            return json.loads(raw)
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# RUTAS FASTAPI — register()
# ═══════════════════════════════════════════════════════════════════════════════

def register(app, get_db_func, auth_dep):
    """
    Registra todos los endpoints del Comunicador Inteligente en la app FastAPI.

    Llamar desde main.py:
        import jarvis_comunicador
        jarvis_comunicador.register(app, get_db, current_aliado_required)
    """
    import json as _json
    from fastapi import Depends, HTTPException
    from sqlalchemy.orm import Session
    from pydantic import BaseModel
    from typing import Optional as Opt, List

    # ── Helper de contexto del aliado ─────────────────────────────────────────

    def _ctx(aliado_obj) -> dict:
        rubros = []
        try:
            rubros_raw = getattr(aliado_obj, "rubros_especialidad", "[]") or "[]"
            rubros = _json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
        except Exception:
            pass
        return {
            "aliado_nombre":  getattr(aliado_obj, "nombre", "") or "",
            "aliado_ciudad":  getattr(aliado_obj, "ciudad", "") or "",
            "aliado_rubros":  rubros,
            "perfil_estilo":  obtener_perfil_estilo(aliado_obj),
        }

    # ── Schemas ──────────────────────────────────────────────────────────────

    class EmailPrimerContactoReq(BaseModel):
        empresa_prospecto: str
        sector: str
        nombre_contacto: str = ""
        cargo_contacto: str = ""
        dolor_detectado: str = ""

    class EmailSeguimientoReq(BaseModel):
        empresa_prospecto: str
        nombre_contacto: str = ""
        motivo_seguimiento: str = "post-reunión"
        dias_sin_respuesta: int = 0
        ultimo_contacto: str = ""
        propuesta_enviada: bool = False

    class EmailReactivacionReq(BaseModel):
        empresa_prospecto: str
        nombre_contacto: str = ""
        dias_dormido: int
        ultimo_motivo_perdida: str = ""
        sector: str = ""

    class EmailCierreReq(BaseModel):
        empresa_prospecto: str
        nombre_contacto: str = ""
        contexto_negociacion: str = ""
        objecion_pendiente: str = ""
        urgencia_real: str = ""
        plan_discutido: str = ""

    class EmailObjecionReq(BaseModel):
        empresa_prospecto: str
        objecion_textual: str
        nombre_contacto: str = ""
        contexto_previo: str = ""
        sector: str = ""

    class WhatsappProspeccionReq(BaseModel):
        empresa_prospecto: str
        sector: str
        nombre_contacto: str = ""
        dolor_detectado: str = ""
        casos_similares: str = ""

    class WhatsappSeguimientoReq(BaseModel):
        empresa_prospecto: str
        nombre_contacto: str = ""
        contexto_previo: str = ""
        dias_desde_contacto: int = 0

    class LinkedinProspeccionReq(BaseModel):
        empresa_prospecto: str
        nombre_contacto: str
        cargo_contacto: str = ""
        sector: str = ""
        perfil_linkedin_info: str = ""

    class LinkedinComentarioReq(BaseModel):
        publicacion_texto: str
        empresa_prospecto: str
        nombre_autor: str = ""
        sector: str = ""

    class PropuestaReunionReq(BaseModel):
        empresa_prospecto: str
        nombre_contacto: str = ""
        canal: str = "email"
        contexto: str = ""
        duracion_minutos: int = 15

    class RespuestaCotizacionReq(BaseModel):
        empresa_prospecto: str
        solicitud_texto: str
        nombre_contacto: str = ""
        plan_a_proponer: str = ""
        incluir_precio: bool = False
        precio: float = 0

    class AgradecimientoPostcierreReq(BaseModel):
        empresa_cliente: str
        nombre_contacto: str = ""
        plan_contratado: str = ""
        fecha_inicio: str = ""

    class PedidoRefevidoReq(BaseModel):
        empresa_cliente: str
        nombre_contacto: str = ""
        meses_como_cliente: int = 0
        resultado_logrado: str = ""
        sector_objetivo: str = ""

    class RegistrarEdicionReq(BaseModel):
        tipo_comunicacion: str
        texto_original: str
        texto_editado: str

    # ── Endpoints ─────────────────────────────────────────────────────────────

    @app.post("/jarvis/comunicador/email-primer-contacto")
    def ep_email_primer_contacto(
        body: EmailPrimerContactoReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """3 variantes de email frío (directa, técnica, consultiva) con recomendación."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = email_primer_contacto(
            empresa_prospecto=body.empresa_prospecto,
            sector=body.sector,
            nombre_contacto=body.nombre_contacto,
            cargo_contacto=body.cargo_contacto,
            dolor_detectado=body.dolor_detectado,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
            aliado_rubros=ctx["aliado_rubros"],
            perfil_estilo=ctx["perfil_estilo"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el email")
        return {"ok": True, "emails": resultado}

    @app.post("/jarvis/comunicador/email-seguimiento")
    def ep_email_seguimiento(
        body: EmailSeguimientoReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Email de seguimiento adaptado a días sin respuesta y contexto previo."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = email_seguimiento(
            empresa_prospecto=body.empresa_prospecto,
            nombre_contacto=body.nombre_contacto,
            motivo_seguimiento=body.motivo_seguimiento,
            dias_sin_respuesta=body.dias_sin_respuesta,
            ultimo_contacto=body.ultimo_contacto,
            propuesta_enviada=body.propuesta_enviada,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
            perfil_estilo=ctx["perfil_estilo"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el seguimiento")
        return {"ok": True, "seguimiento": resultado}

    @app.post("/jarvis/comunicador/email-reactivacion")
    def ep_email_reactivacion(
        body: EmailReactivacionReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Reactiva un lead dormido con un ángulo nuevo, no el mismo argumento."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = email_reactivacion(
            empresa_prospecto=body.empresa_prospecto,
            nombre_contacto=body.nombre_contacto,
            dias_dormido=body.dias_dormido,
            ultimo_motivo_perdida=body.ultimo_motivo_perdida,
            sector=body.sector,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
            perfil_estilo=ctx["perfil_estilo"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar la reactivación")
        return {"ok": True, "reactivacion": resultado}

    @app.post("/jarvis/comunicador/email-cierre")
    def ep_email_cierre(
        body: EmailCierreReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Email de cierre con urgencia real basada en el negocio del prospecto."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = email_cierre(
            empresa_prospecto=body.empresa_prospecto,
            nombre_contacto=body.nombre_contacto,
            contexto_negociacion=body.contexto_negociacion,
            objecion_pendiente=body.objecion_pendiente,
            urgencia_real=body.urgencia_real,
            plan_discutido=body.plan_discutido,
            aliado_nombre=ctx["aliado_nombre"],
            perfil_estilo=ctx["perfil_estilo"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el email de cierre")
        return {"ok": True, "cierre": resultado}

    @app.post("/jarvis/comunicador/email-objecion")
    def ep_email_objecion(
        body: EmailObjecionReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Responde una objeción específica con la estrategia correcta."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = email_objecion_especifica(
            empresa_prospecto=body.empresa_prospecto,
            objecion_textual=body.objecion_textual,
            nombre_contacto=body.nombre_contacto,
            contexto_previo=body.contexto_previo,
            sector=body.sector,
            aliado_nombre=ctx["aliado_nombre"],
            perfil_estilo=ctx["perfil_estilo"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar la respuesta a la objeción")
        return {"ok": True, "respuesta": resultado}

    @app.post("/jarvis/comunicador/whatsapp-prospeccion")
    def ep_whatsapp_prospeccion(
        body: WhatsappProspeccionReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Mensaje de prospección WhatsApp ≤60 palabras con 2 variantes."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = whatsapp_prospeccion(
            empresa_prospecto=body.empresa_prospecto,
            sector=body.sector,
            nombre_contacto=body.nombre_contacto,
            dolor_detectado=body.dolor_detectado,
            casos_similares=body.casos_similares,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el mensaje de WhatsApp")
        return {"ok": True, "whatsapp": resultado}

    @app.post("/jarvis/comunicador/whatsapp-seguimiento")
    def ep_whatsapp_seguimiento(
        body: WhatsappSeguimientoReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Seguimiento cálido por WhatsApp ≤60 palabras."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = whatsapp_seguimiento(
            empresa_prospecto=body.empresa_prospecto,
            nombre_contacto=body.nombre_contacto,
            contexto_previo=body.contexto_previo,
            dias_desde_contacto=body.dias_desde_contacto,
            aliado_nombre=ctx["aliado_nombre"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el seguimiento")
        return {"ok": True, "whatsapp": resultado}

    @app.post("/jarvis/comunicador/linkedin-prospeccion")
    def ep_linkedin_prospeccion(
        body: LinkedinProspeccionReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Nota de conexión LinkedIn (≤300 chars) + primer mensaje post-conexión (≤500 chars)."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = linkedin_prospeccion(
            empresa_prospecto=body.empresa_prospecto,
            nombre_contacto=body.nombre_contacto,
            cargo_contacto=body.cargo_contacto,
            sector=body.sector,
            perfil_linkedin_info=body.perfil_linkedin_info,
            aliado_nombre=ctx["aliado_nombre"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el mensaje de LinkedIn")
        return {"ok": True, "linkedin": resultado}

    @app.post("/jarvis/comunicador/linkedin-comentario")
    def ep_linkedin_comentario(
        body: LinkedinComentarioReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Comentario estratégico en publicación del prospecto — sin vender directamente."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = linkedin_comentario(
            publicacion_texto=body.publicacion_texto,
            empresa_prospecto=body.empresa_prospecto,
            nombre_autor=body.nombre_autor,
            sector=body.sector,
            aliado_nombre=ctx["aliado_nombre"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el comentario")
        return {"ok": True, "comentario": resultado}

    @app.post("/jarvis/comunicador/propuesta-reunion")
    def ep_propuesta_reunion(
        body: PropuestaReunionReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Propuesta de reunión adaptada al canal (email / whatsapp / linkedin)."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = propuesta_reunion(
            empresa_prospecto=body.empresa_prospecto,
            nombre_contacto=body.nombre_contacto,
            canal=body.canal,
            contexto=body.contexto,
            duracion_minutos=body.duracion_minutos,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
            perfil_estilo=ctx["perfil_estilo"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar la propuesta de reunión")
        return {"ok": True, "propuesta": resultado}

    @app.post("/jarvis/comunicador/respuesta-cotizacion")
    def ep_respuesta_cotizacion(
        body: RespuestaCotizacionReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Responde a un RFQ / solicitud de precio con contexto de valor."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = respuesta_solicitud_cotizacion(
            empresa_prospecto=body.empresa_prospecto,
            solicitud_texto=body.solicitud_texto,
            nombre_contacto=body.nombre_contacto,
            plan_a_proponer=body.plan_a_proponer,
            incluir_precio=body.incluir_precio,
            precio=body.precio,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
            perfil_estilo=ctx["perfil_estilo"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar la respuesta")
        return {"ok": True, "respuesta": resultado}

    @app.post("/jarvis/comunicador/agradecimiento-postcierre")
    def ep_agradecimiento_postcierre(
        body: AgradecimientoPostcierreReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Email de bienvenida post-cierre que siembra la próxima venta y el referido."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = agradecimiento_postcierre(
            empresa_cliente=body.empresa_cliente,
            nombre_contacto=body.nombre_contacto,
            plan_contratado=body.plan_contratado,
            fecha_inicio=body.fecha_inicio,
            aliado_nombre=ctx["aliado_nombre"],
            perfil_estilo=ctx["perfil_estilo"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el mensaje de bienvenida")
        return {"ok": True, "bienvenida": resultado}

    @app.post("/jarvis/comunicador/pedido-referido")
    def ep_pedido_referido(
        body: PedidoRefevidoReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Mensaje de pedido de referido — con timing y forma para no incomodar."""
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = pedido_referido(
            empresa_cliente=body.empresa_cliente,
            nombre_contacto=body.nombre_contacto,
            meses_como_cliente=body.meses_como_cliente,
            resultado_logrado=body.resultado_logrado,
            sector_objetivo=body.sector_objetivo,
            aliado_nombre=ctx["aliado_nombre"],
            perfil_estilo=ctx["perfil_estilo"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el pedido de referido")
        return {"ok": True, "referido": resultado}

    @app.post("/jarvis/comunicador/registrar-edicion")
    def ep_registrar_edicion(
        body: RegistrarEdicionReq,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Trackea una edición del aliado sobre un texto generado por JARVIS.
        Actualiza el perfil de estilo aprendido del aliado.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")

        delta = registrar_edicion_estilo(
            aliado_id=aliado.id,
            tipo_comunicacion=body.tipo_comunicacion,
            texto_original=body.texto_original,
            texto_editado=body.texto_editado,
            db_session=db,
        )
        if not delta:
            return {"ok": True, "delta": None, "mensaje": "Sin cambios detectables"}

        # Mergear el delta en el perfil existente del aliado
        perfil_actual = obtener_perfil_estilo(aliado)
        nuevos_datos = delta.get("actualizar_perfil", {})

        if nuevos_datos:
            # Mergear campo por campo — no pisar con None
            for k, v in nuevos_datos.items():
                if v is None:
                    continue
                if k == "evita" and isinstance(v, list):
                    existentes = perfil_actual.get("evita", [])
                    perfil_actual["evita"] = list(set(existentes + v))
                else:
                    perfil_actual[k] = v

            # Intentar guardar si el modelo tiene el campo
            try:
                if hasattr(aliado, "jarvis_estilo_perfil"):
                    aliado.jarvis_estilo_perfil = _json.dumps(perfil_actual, ensure_ascii=False)
                    db.commit()
            except Exception as e:
                print(f"[JARVIS COMUNICADOR] Error guardando perfil estilo: {e}", file=sys.stderr)
                db.rollback()

        return {"ok": True, "delta": delta, "perfil_actualizado": perfil_actual}

    @app.get("/jarvis/comunicador/perfil-estilo")
    def ep_perfil_estilo(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Retorna el perfil de estilo aprendido del aliado."""
        return {"ok": True, "perfil_estilo": obtener_perfil_estilo(aliado)}
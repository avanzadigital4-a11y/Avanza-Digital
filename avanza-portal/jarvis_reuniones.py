"""
jarvis_reuniones.py — Módulo 6: Copiloto de Reuniones

FUNCIONES:
  preparar_reunion()     → Ficha pre-reunión: perfil, preguntas, señales, ángulo de cierre
  procesar_memo_voz()    → Transcribe un voice memo post-reunión y extrae próximos pasos
  generar_followup_reunion() → Email de follow-up basado en lo que pasó en la reunión
  analizar_temperatura() → Analiza un mensaje/email del prospecto y detecta señales de compra o fuga
  resumen_reunion()      → Dado un texto libre de notas, genera resumen estructurado para el CRM

DISEÑO:
  Mismo patrón que jarvis.py: si ANTHROPIC_API_KEY no está o falla, todas las
  funciones devuelven None. El producto nunca se cae por la IA.
  Timeout: 20 segundos (el análisis de notas puede ser extenso).

  Integración en main.py:
      import jarvis_reuniones
      jarvis_reuniones.register(app, get_db, current_aliado_required)
"""

from __future__ import annotations
import os, json, sys
from typing import Optional

# ─── CONFIG ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
from jarvis_config import JARVIS_MODEL  # modelo centralizado en jarvis_config.py
JARVIS_TIMEOUT    = 20.0


def is_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


# ─── HELPERS INTERNOS ────────────────────────────────────────────────────────

def _chat(
    prompt: str,
    system: str,
    *,
    max_tokens: int = 1200,
    temperature: float = 0.35,
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
        print(f"[JARVIS REUNIONES ERROR] {type(e).__name__}: {e}", file=sys.stderr)
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
    print(f"[JARVIS REUNIONES] No se pudo parsear JSON: {text[:200]}", file=sys.stderr)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# MÓDULO 6 — COPILOTO DE REUNIONES
# ═══════════════════════════════════════════════════════════════════════════════

def preparar_reunion(
    empresa_prospecto: str,
    sector: str,
    *,
    nombre_contacto: str = "",
    cargo_contacto: str = "",
    historial_previo: str = "",
    objetivo_reunion: str = "",
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
    aliado_rubros: list = None,
    aliado_ventas: int = 0,
) -> Optional[dict]:
    """
    Módulo 6 — Preparación pre-reunión.
    Genera una ficha completa en 60 segundos para que el aliado llegue preparado.

    Retorna:
        {
          "ficha_empresa": str,
          "hipotesis_dolores": list[str],
          "preguntas_estrategicas": list[str],
          "cosas_no_decir": list[str],
          "propuesta_de_cierre": str,
          "pendientes_de_seguimiento": list[str],
          "score_oportunidad": int,
          "briefing_30_segundos": str,
        }
    """
    rubros_str = ", ".join(aliado_rubros) if aliado_rubros else "industrial"

    prompt = f"""
Preparame para una reunión de ventas. Necesito llegar listo en 60 segundos.

EMPRESA: {empresa_prospecto}
SECTOR: {sector}
CONTACTO: {nombre_contacto or "desconocido"} — {cargo_contacto or "cargo no definido"}
HISTORIAL PREVIO: {historial_previo or "primera reunión, sin contacto previo"}
OBJETIVO DE ESTA REUNIÓN: {objetivo_reunion or "primera presentación de Avanza Digital"}

ALIADO QUE VA A LA REUNIÓN: {aliado_nombre or "aliado"} — {aliado_ciudad or "Argentina"}
SECTORES EN LOS QUE EL ALIADO OPERA: {rubros_str}
VENTAS CONFIRMADAS DEL ALIADO: {aliado_ventas}

Avanza Digital ofrece: presencia web B2B, SEO, generación de leads, sistemas de
marketing digital para PYMES industriales. Planes $1.190 - $9.999 ARS/mes.

Devolvé este JSON:
{{
  "ficha_empresa": "<perfil en 2-3 líneas: tipo de empresa, procesos, prioridades típicas del sector>",
  "hipotesis_dolores": [
    "<dolor 1 probable para este tipo de empresa — específico al sector>",
    "<dolor 2>",
    "<dolor 3>"
  ],
  "preguntas_estrategicas": [
    "<pregunta 1 que abre la conversación y revela el dolor real>",
    "<pregunta 2>",
    "<pregunta 3>",
    "<pregunta 4>",
    "<pregunta 5>"
  ],
  "cosas_no_decir": [
    "<frase o argumento que suele cerrar la conversación con este perfil>",
    "<cosa a evitar 2>",
    "<cosa a evitar 3>"
  ],
  "propuesta_de_cierre": "<cómo intentar cerrar el siguiente paso en esta reunión específica — no el contrato, sí el próximo paso concreto>",
  "pendientes_de_seguimiento": [
    "<cosa que quedó pendiente del historial previo — o vacío si es primera reunión>"
  ],
  "score_oportunidad": <número entre 1 y 100 indicando qué tan buena es esta oportunidad>,
  "briefing_30_segundos": "<resumen ejecutivo de todo esto en 3-4 líneas para leer mientras se camina al auto — lo más importante antes de entrar>"
}}
"""

    system = """Sos el Módulo de Preparación de Reuniones de JARVIS para Avanza Digital.
Preparás aliados para reuniones de ventas B2B industrial en menos de 60 segundos.
Sos concreto, sin paja — cada pregunta y cada dato tiene que ser accionable.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1500, temperature=0.35, json_mode=True)
    return _parse_json(raw) if raw else None


def procesar_memo_voz(
    transcripcion: str,
    *,
    empresa_prospecto: str = "",
    aliado_nombre: str = "",
) -> Optional[dict]:
    """
    Módulo 6 — Procesamiento de voice memo post-reunión.
    El aliado graba 1-2 minutos después de salir y JARVIS extrae todo lo accionable.

    Parámetro 'transcripcion': texto del audio ya convertido a texto (o texto libre
    del aliado describiendo la reunión).

    Retorna:
        {
          "resumen_reunion": str,
          "acuerdos": list[str],
          "proximos_pasos": list[dict],   # [{accion, responsable, fecha_limite}]
          "compromisos_aliado": list[str],
          "temperatura_lead": str,
          "actualizacion_score": int,
          "notas_crm": str,
          "señales_detectadas": list[str],
        }
    """
    prompt = f"""
El aliado grabó este audio / memo después de una reunión. Procesalo y extraé todo lo accionable.

EMPRESA DEL PROSPECTO: {empresa_prospecto or "la empresa con la que se reunió"}
ALIADO: {aliado_nombre or "el aliado"}

TRANSCRIPCIÓN / NOTAS DE LA REUNIÓN:
---
{transcripcion}
---

Tu trabajo es:
1. Extraer qué pasó realmente en la reunión
2. Identificar todos los compromisos y próximos pasos
3. Detectar señales de temperatura del lead (¿está más caliente o más frío?)
4. Generar la nota de CRM lista para copiar

Devolvé este JSON:
{{
  "resumen_reunion": "<qué pasó en la reunión en 3-4 líneas — quién dijo qué, cómo fue el clima, qué se mostró>",
  "acuerdos": [
    "<acuerdo 1 al que se llegó explícitamente>",
    "<acuerdo 2 si existe>"
  ],
  "proximos_pasos": [
    {{
      "accion": "<qué hay que hacer>",
      "responsable": "<aliado|prospecto|ambos>",
      "fecha_limite": "<fecha o plazo mencionado, o 'a definir'>"
    }}
  ],
  "compromisos_aliado": [
    "<qué prometió el aliado en la reunión>",
    "<compromiso 2 si existe>"
  ],
  "temperatura_lead": "<caliente|tibio|frío|indefinido — evaluación del estado del lead post-reunión>",
  "actualizacion_score": <número entre 1 y 100 — nuevo score del lead basado en la reunión>,
  "notas_crm": "<nota completa lista para copiar al CRM — fecha, empresa, qué pasó, próximos pasos, temperatura>",
  "señales_detectadas": [
    "<señal de compra o de fuga detectada en la transcripción>",
    "<señal 2 si existe>",
    "<señal 3 si existe>"
  ]
}}
"""

    system = """Sos el Módulo Post-Reunión de JARVIS para Avanza Digital.
Procesás notas y voice memos de reuniones de ventas y extraés todo lo accionable.
Sos preciso con los compromisos: si el aliado prometió algo, lo capturás sin interpretarlo de más.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1500, temperature=0.3, json_mode=True)
    return _parse_json(raw) if raw else None


def generar_followup_reunion(
    resumen_reunion: str,
    empresa_prospecto: str,
    nombre_contacto: str = "",
    *,
    proximos_pasos: str = "",
    tono: str = "profesional",
    aliado_nombre: str = "",
    aliado_ciudad: str = "",
) -> Optional[dict]:
    """
    Módulo 6 — Email de follow-up post-reunión.
    Basado en lo que pasó, genera el email listo para enviar.

    Retorna:
        {
          "asunto": str,
          "cuerpo": str,
          "notas_aliado": str,
          "variante_whatsapp": str,
        }
    """
    prompt = f"""
Escribí el email de seguimiento post-reunión basado en lo que pasó.

EMPRESA: {empresa_prospecto}
CONTACTO: {nombre_contacto or "el contacto"}
QUÉ PASÓ EN LA REUNIÓN: {resumen_reunion}
PRÓXIMOS PASOS ACORDADOS: {proximos_pasos or "no especificados — inferí del resumen"}
TONO: {tono}
ALIADO QUE ENVÍA: {aliado_nombre or "el aliado"} ({aliado_ciudad or "Argentina"})

El email debe:
- Referenciar algo específico de la reunión (no empezar con "espero que estés bien")
- Confirmar los próximos pasos acordados con claridad
- Tener una CTA concreta (no vaga)
- Sonar como el aliado, no como un template corporativo
- Ser en español rioplatense, entre 100-150 palabras

También generá una versión corta para WhatsApp (máximo 60 palabras).

Devolvé este JSON:
{{
  "asunto": "<asunto del email — específico, no 'Seguimiento de reunión'>",
  "cuerpo": "<cuerpo completo del email, con saludo y cierre>",
  "notas_aliado": "<tips para el aliado sobre cómo o cuándo enviarlo — timing, personalización sugerida>",
  "variante_whatsapp": "<versión corta para WhatsApp, máximo 60 palabras>"
}}
"""

    system = """Sos el Módulo de Comunicaciones Post-Reunión de JARVIS para Avanza Digital.
Escribís emails de seguimiento que se leen porque son específicos y no genéricos.
Cada email suena como una persona real, no como un CRM automático.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1000, temperature=0.45, json_mode=True)
    return _parse_json(raw) if raw else None


def analizar_temperatura(
    mensaje_prospecto: str,
    *,
    empresa_prospecto: str = "",
    historial_conversacion: str = "",
) -> Optional[dict]:
    """
    Módulo 6 — Análisis de temperatura de un mensaje recibido.
    El aliado pega el email/WhatsApp que recibió y JARVIS lo interpreta.

    Retorna:
        {
          "temperatura": str,
          "señales_compra": list[str],
          "señales_fuga": list[str],
          "poder_de_decision": str,
          "interpretacion": str,
          "respuesta_recomendada": str,
          "urgencia_de_respuesta": str,
        }
    """
    prompt = f"""
El aliado recibió este mensaje del prospecto. Analizá la temperatura y qué significa.

EMPRESA: {empresa_prospecto or "el prospecto"}
HISTORIAL DE LA CONVERSACIÓN: {historial_conversacion or "no especificado"}

MENSAJE RECIBIDO:
---
{mensaje_prospecto}
---

Analizá las señales implícitas y explícitas. Sé específico sobre lo que el texto
revela — no lo que nos gustaría que dijera.

Devolvé este JSON:
{{
  "temperatura": "<caliente|tibio|frío|indefinido>",
  "señales_compra": [
    "<señal explícita o implícita de que el prospecto avanza hacia el sí>",
    "<señal 2 si existe>"
  ],
  "señales_fuga": [
    "<señal de que el prospecto se está escapando o enfriando>",
    "<señal 2 si existe>"
  ],
  "poder_de_decision": "<decide solo|consulta con alguien|no decide — basado en el texto>",
  "interpretacion": "<qué está diciendo realmente el prospecto, más allá de las palabras — en 2 líneas>",
  "respuesta_recomendada": "<qué responder y cómo — táctica concreta, no solo 'seguí el contacto'>",
  "urgencia_de_respuesta": "<inmediata (hoy)|alta (dentro de 24hs)|media (esta semana)|baja>"
}}
"""

    system = """Sos el Módulo de Análisis de Temperatura de JARVIS para Avanza Digital.
Leés mensajes de prospectos y detectás señales de compra y de fuga con precisión.
No sos optimista: si el mensaje dice que se está cerrando, lo decís claramente.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=900, temperature=0.3, json_mode=True)
    return _parse_json(raw) if raw else None


def resumen_reunion(
    notas_libres: str,
    empresa_prospecto: str,
    *,
    aliado_nombre: str = "",
    fecha_reunion: str = "",
) -> Optional[dict]:
    """
    Módulo 6 — Resumen estructurado de notas libres de una reunión.
    El aliado escribe sus notas como puede y JARVIS las convierte en un resumen de CRM.

    Retorna:
        {
          "resumen_ejecutivo": str,
          "estado_lead": str,
          "puntos_clave": list[str],
          "objeciones_planteadas": list[str],
          "respuestas_del_aliado": list[str],
          "proxima_accion": str,
          "fecha_proxima_accion": str,
          "nota_crm_completa": str,
        }
    """
    prompt = f"""
El aliado tomó estas notas de una reunión. Estructuralas para el CRM.

EMPRESA: {empresa_prospecto}
ALIADO: {aliado_nombre or "el aliado"}
FECHA: {fecha_reunion or "no especificada"}

NOTAS:
---
{notas_libres}
---

Convertí estas notas en un resumen estructurado limpio. Completá los vacíos con lo que
se puede inferir del contexto, pero indicá cuándo estás infiriendo.

Devolvé este JSON:
{{
  "resumen_ejecutivo": "<qué pasó en la reunión en 3 líneas — para leer en 10 segundos>",
  "estado_lead": "<activo-caliente|activo-tibio|activo-frío|pausado|perdido|cerrado>",
  "puntos_clave": [
    "<punto clave 1 de la reunión — lo más importante>",
    "<punto clave 2>",
    "<punto clave 3>"
  ],
  "objeciones_planteadas": [
    "<objeción que planteó el prospecto>",
    "<objeción 2 si existe>"
  ],
  "respuestas_del_aliado": [
    "<cómo respondió el aliado a la objeción — o qué quedó sin responder>",
    "<respuesta 2 si aplica>"
  ],
  "proxima_accion": "<qué hace el aliado o el prospecto como próximo paso concreto>",
  "fecha_proxima_accion": "<fecha o plazo mencionado, o 'a definir'>",
  "nota_crm_completa": "<nota completa y bien redactada, lista para copiar al CRM — incluye fecha, empresa, resumen, próximos pasos, estado>"
}}
"""

    system = """Sos el Módulo de Procesamiento de Notas de JARVIS para Avanza Digital.
Convertís notas desordenadas de reuniones en registros estructurados de CRM.
Sos claro sobre lo que está explícito en las notas y lo que estás infiriendo.
Respondé ÚNICAMENTE con JSON válido."""

    raw = _chat(prompt, system, max_tokens=1400, temperature=0.3, json_mode=True)
    return _parse_json(raw) if raw else None


# ═══════════════════════════════════════════════════════════════════════════════
# RUTAS FASTAPI — register()
# ═══════════════════════════════════════════════════════════════════════════════

def register(app, get_db_func, auth_dep):
    """
    Registra todos los endpoints del Copiloto de Reuniones en la app FastAPI.

    Llamar desde main.py:
        import jarvis_reuniones
        jarvis_reuniones.register(app, get_db, current_aliado_required)
    """
    import json as _json
    from fastapi import Depends, HTTPException
    from sqlalchemy.orm import Session
    from pydantic import BaseModel

    # ── Schemas ──────────────────────────────────────────────────────────────

    class PrepararReunionRequest(BaseModel):
        empresa_prospecto: str
        sector: str
        nombre_contacto: str = ""
        cargo_contacto: str = ""
        historial_previo: str = ""
        objetivo_reunion: str = ""

    class MemoVozRequest(BaseModel):
        transcripcion: str          # texto libre o transcripción del audio
        empresa_prospecto: str = ""

    class FollowupReunionRequest(BaseModel):
        resumen_reunion: str
        empresa_prospecto: str
        nombre_contacto: str = ""
        proximos_pasos: str = ""
        tono: str = "profesional"   # profesional | cercano | técnico | ejecutivo

    class TemperaturaRequest(BaseModel):
        mensaje_prospecto: str
        empresa_prospecto: str = ""
        historial_conversacion: str = ""

    class ResumenReunionRequest(BaseModel):
        notas_libres: str
        empresa_prospecto: str
        fecha_reunion: str = ""

    # ── Helper para extraer contexto del aliado ───────────────────────────────

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

    # ── Endpoints ─────────────────────────────────────────────────────────────

    @app.post("/jarvis/reunion/preparar")
    def jarvis_preparar_reunion(
        body: PrepararReunionRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Ficha pre-reunión: perfil de empresa, preguntas estratégicas,
        cosas a no decir, propuesta de cierre y briefing de 30 segundos.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = preparar_reunion(
            empresa_prospecto=body.empresa_prospecto,
            sector=body.sector,
            nombre_contacto=body.nombre_contacto,
            cargo_contacto=body.cargo_contacto,
            historial_previo=body.historial_previo,
            objetivo_reunion=body.objetivo_reunion,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
            aliado_rubros=ctx["aliado_rubros"],
            aliado_ventas=ctx["aliado_ventas"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo preparar la ficha de reunión")
        return {"ok": True, "ficha": resultado}

    @app.post("/jarvis/reunion/memo")
    def jarvis_procesar_memo(
        body: MemoVozRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Procesa notas o transcripción de voz post-reunión.
        Extrae acuerdos, próximos pasos, temperatura y nota de CRM lista.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = procesar_memo_voz(
            transcripcion=body.transcripcion,
            empresa_prospecto=body.empresa_prospecto,
            aliado_nombre=ctx["aliado_nombre"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo procesar el memo")
        return {"ok": True, "procesado": resultado}

    @app.post("/jarvis/reunion/followup")
    def jarvis_followup_reunion(
        body: FollowupReunionRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Genera el email de follow-up post-reunión y su versión corta para WhatsApp.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = generar_followup_reunion(
            resumen_reunion=body.resumen_reunion,
            empresa_prospecto=body.empresa_prospecto,
            nombre_contacto=body.nombre_contacto,
            proximos_pasos=body.proximos_pasos,
            tono=body.tono,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo generar el follow-up")
        return {"ok": True, "followup": resultado}

    @app.post("/jarvis/reunion/temperatura")
    def jarvis_temperatura_mensaje(
        body: TemperaturaRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Analiza un mensaje recibido del prospecto: temperatura, señales de compra
        o fuga, poder de decisión y respuesta recomendada.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        resultado = analizar_temperatura(
            mensaje_prospecto=body.mensaje_prospecto,
            empresa_prospecto=body.empresa_prospecto,
            historial_conversacion=body.historial_conversacion,
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo analizar el mensaje")
        return {"ok": True, "analisis": resultado}

    @app.post("/jarvis/reunion/resumen")
    def jarvis_resumen_reunion(
        body: ResumenReunionRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Convierte notas libres de una reunión en un resumen estructurado listo para el CRM.
        """
        if not is_enabled():
            raise HTTPException(503, "JARVIS no disponible")
        ctx = _ctx(aliado)
        resultado = resumen_reunion(
            notas_libres=body.notas_libres,
            empresa_prospecto=body.empresa_prospecto,
            aliado_nombre=ctx["aliado_nombre"],
            fecha_reunion=body.fecha_reunion,
        )
        if not resultado:
            raise HTTPException(502, "JARVIS no pudo estructurar las notas")
        return {"ok": True, "resumen": resultado}
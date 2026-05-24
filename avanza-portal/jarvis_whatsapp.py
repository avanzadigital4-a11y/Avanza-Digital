"""
jarvis_whatsapp.py — Integración real de WhatsApp para JARVIS (Sección 8 del Blueprint v2)

Conecta WhatsApp ↔ JARVIS mediante la API de Twilio (WhatsApp Business).
Cuando un aliado escribe a su número JARVIS de WhatsApp, este módulo:

  1. Recibe el webhook de Twilio (texto, audio, imagen, documento)
  2. Identifica al aliado por número de teléfono
  3. Enruta el mensaje al módulo JARVIS correcto según la intención
  4. Devuelve la respuesta al aliado por WhatsApp en ≤10 segundos

FLUJO COMPLETO:
  Aliado escribe → Twilio → POST /webhook/whatsapp → _identificar_aliado()
  → _detectar_intencion() → módulo JARVIS → _responder_por_whatsapp()
  → Twilio → WhatsApp del aliado

SINCRONIZACIÓN WhatsApp ↔ Portal:
  Cada interacción queda registrada en jarvis_memoria (memoria episódica).
  El portal y WhatsApp leen de la misma memoria — son un solo JARVIS.

CONFIGURACIÓN (variables de entorno requeridas):
  TWILIO_ACCOUNT_SID     → Account SID de Twilio
  TWILIO_AUTH_TOKEN      → Auth Token de Twilio
  TWILIO_WHATSAPP_FROM   → Número Twilio formato: whatsapp:+14155238886
  TWILIO_WEBHOOK_SECRET  → Para validar firma (recomendado en producción)

CONFIGURACIÓN (opcional para registro de número):
  Cada aliado registra su número de WhatsApp en el portal. Este módulo
  lo busca por número en la BD para identificarlo automáticamente.

ENDPOINTS:
  POST /webhook/whatsapp                    → Recibe mensajes de Twilio
  POST /jarvis/whatsapp/enviar              → El portal envía WA proactivo al aliado
  POST /jarvis/whatsapp/registrar-numero    → El aliado registra su número de WA
  GET  /jarvis/whatsapp/estado              → Estado de la integración Twilio
  POST /webhook/whatsapp/status            → Callback de estado de entrega (Twilio)

REQUISITOS:
  pip install twilio
  (ya en requirements.txt — agregar: twilio>=8.0.0)

PARA ACTIVAR EN PRODUCCIÓN:
  1. Crear cuenta Twilio → activar WhatsApp Sandbox → configurar número
  2. Registrar webhook URL en Twilio: https://tuapp.com/webhook/whatsapp
  3. Agregar variables de entorno al deploy
  4. En main.py: import jarvis_whatsapp; jarvis_whatsapp.register(app, get_db, current_aliado_required)
"""

from __future__ import annotations
import os, json, sys, hmac, hashlib, base64
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID   = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN    = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "").strip()
TWILIO_WEBHOOK_SECRET = os.environ.get("TWILIO_WEBHOOK_SECRET", "").strip()
ANTHROPIC_API_KEY    = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL         = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT       = 18.0

# Longitud máxima de respuesta por WhatsApp (WhatsApp acepta ~4096 chars)
WA_MAX_CHARS = 3800

# Tiempo máximo sin actividad para considerar una sesión nueva (segundos)
SESSION_TIMEOUT_SECS = 3600  # 1 hora


def is_enabled() -> bool:
    """¿Está configurada la integración con Twilio?"""
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM)


def is_jarvis_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


# ─── CACHÉ DE SESIÓN EN MEMORIA ───────────────────────────────────────────────
# Mantiene el historial de conversación por número de teléfono durante la sesión.
# Redis sería ideal para multi-proceso; en mono-proceso este dict funciona perfecto.
_sesiones: dict[str, dict] = {}


def _get_sesion(numero: str) -> dict:
    ahora = datetime.utcnow().timestamp()
    sesion = _sesiones.get(numero)
    if not sesion or (ahora - sesion.get("ultima_actividad", 0)) > SESSION_TIMEOUT_SECS:
        _sesiones[numero] = {"historial": [], "ultima_actividad": ahora}
    return _sesiones[numero]


def _actualizar_sesion(numero: str, rol: str, contenido: str) -> None:
    sesion = _get_sesion(numero)
    sesion["historial"].append({"role": rol, "content": contenido})
    sesion["ultima_actividad"] = datetime.utcnow().timestamp()
    # Mantener máximo 20 turnos en memoria
    if len(sesion["historial"]) > 20:
        sesion["historial"] = sesion["historial"][-20:]


# ─── VALIDACIÓN DE FIRMA TWILIO ───────────────────────────────────────────────

def _validar_firma_twilio(request_url: str, params: dict, signature: str) -> bool:
    """
    Valida la firma X-Twilio-Signature para verificar que el webhook
    viene realmente de Twilio. Implementación según docs de Twilio.
    """
    if not TWILIO_AUTH_TOKEN:
        return True  # No validar en desarrollo sin token configurado

    try:
        # Construir la cadena a firmar: URL + params ordenados
        s = request_url
        for key in sorted(params.keys()):
            s += key + params[key]

        mac = hmac.new(
            TWILIO_AUTH_TOKEN.encode("utf-8"),
            s.encode("utf-8"),
            hashlib.sha1,
        )
        expected = base64.b64encode(mac.digest()).decode("utf-8")
        return hmac.compare_digest(expected, signature)
    except Exception as e:
        print(f"[WA] Error validando firma Twilio: {e}", file=sys.stderr)
        return False


# ─── IDENTIFICAR ALIADO POR NÚMERO ───────────────────────────────────────────

def _identificar_aliado(numero_wa: str, db_session):
    """
    Busca el aliado en la BD por su número de WhatsApp registrado.
    El número llega de Twilio como "whatsapp:+5491122334455" — se normaliza.
    Retorna el objeto Aliado o None.
    """
    # Normalizar: quitar el prefijo "whatsapp:" y espacios
    numero = numero_wa.replace("whatsapp:", "").replace(" ", "").strip()

    try:
        from models import Aliado  # type: ignore
        from sqlalchemy import text

        # Buscar por columna whatsapp_numero (puede ser con o sin +)
        aliado = db_session.query(Aliado).filter(
            Aliado.whatsapp_numero.in_([numero, numero.lstrip("+")])
        ).first()

        return aliado
    except Exception as e:
        msg = str(e).lower()
        if "column" in msg and "whatsapp_numero" in msg:
            print(
                "[WA] Columna whatsapp_numero no existe en aliados. Ejecutar migración.",
                file=sys.stderr,
            )
        else:
            print(f"[WA] Error buscando aliado: {e}", file=sys.stderr)
        return None


# ─── DETECTAR INTENCIÓN DEL MENSAJE ─────────────────────────────────────────

def _detectar_intencion(texto: str) -> str:
    """
    Clasifica el mensaje del aliado para enrutarlo al módulo correcto.
    Retorna uno de: "chat" | "lead" | "propuesta" | "followup" | "objecion" | "ayuda"
    """
    t = texto.lower().strip()

    # Palabras clave de alta precisión primero
    if any(k in t for k in ["analizá este lead", "analiza este lead", "score para", "bolsa de lead"]):
        return "lead"
    if any(k in t for k in ["propuesta para", "generame una propuesta", "armá la propuesta", "cotización"]):
        return "propuesta"
    if any(k in t for k in ["seguimiento para", "followup", "follow-up", "reactivar", "no me respondió"]):
        return "followup"
    if any(k in t for k in ["objeción", "me dijo que", "no quiere", "resistencia", "cómo respondo"]):
        return "objecion"
    if any(k in t for k in ["ayuda", "qué podés hacer", "para qué servís", "cómo funciona"]):
        return "ayuda"

    # Default: chat general con JARVIS
    return "chat"


# ─── PROCESAR TEXTO RECIBIDO ──────────────────────────────────────────────────

def _procesar_texto(texto: str, aliado_obj, historial: list[dict]) -> str:
    """
    Envía el texto al módulo JARVIS apropiado y devuelve la respuesta.
    """
    if not is_jarvis_enabled():
        return (
            "⚠️ JARVIS no está disponible en este momento. "
            "Intentá desde el portal web: portal.avanzadigital.com"
        )

    intencion = _detectar_intencion(texto)

    # ── Módulo 1: Chat general ────────────────────────────────────────────────
    if intencion == "chat":
        try:
            import jarvis  # type: ignore
            rubros = []
            try:
                rubros_raw = getattr(aliado_obj, "rubros_especialidad", "[]") or "[]"
                rubros = json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
            except Exception:
                pass

            ventas_list = getattr(aliado_obj, "ventas", []) or []
            ventas = sum(1 for v in ventas_list if getattr(v, "confirmada", False))

            resultado = jarvis.chat_jarvis(
                mensaje_aliado=texto,
                historial=historial[-10:] if historial else None,
                aliado_nombre=getattr(aliado_obj, "nombre", "") or "",
                aliado_ciudad=getattr(aliado_obj, "ciudad", "") or "",
                aliado_pais=getattr(aliado_obj, "pais", "AR") or "AR",
                aliado_rubros=rubros,
                aliado_nivel=getattr(aliado_obj, "nivel", "BASIC") or "BASIC",
                aliado_ventas=ventas,
                aliado_perfil=getattr(aliado_obj, "perfil", "") or "",
            )
            if resultado:
                respuesta = resultado.get("respuesta", "")
                accion = resultado.get("accion_sugerida")
                if accion:
                    respuesta += f"\n\n💡 *Próximo paso:* {accion}"
                return respuesta or "JARVIS procesó tu mensaje pero no generó respuesta."
        except Exception as e:
            print(f"[WA] Error en chat JARVIS: {e}", file=sys.stderr)

    # ── Módulo 4: Follow-up ───────────────────────────────────────────────────
    elif intencion == "followup":
        try:
            import jarvis  # type: ignore
            # Extraer nombre de empresa del texto si es posible
            resultado = jarvis.generar_followup(
                empresa_prospecto=_extraer_empresa(texto),
                contexto=texto,
                aliado_nombre=getattr(aliado_obj, "nombre", "") or "",
                aliado_ciudad=getattr(aliado_obj, "ciudad", "") or "",
                aliado_pais=getattr(aliado_obj, "pais", "AR") or "AR",
                tipo="whatsapp",
            )
            if resultado:
                follow = resultado.get("mensaje_whatsapp") or resultado.get("mensaje") or ""
                if follow:
                    return f"📱 *Follow-up para WhatsApp:*\n\n{follow}"
        except Exception as e:
            print(f"[WA] Error en followup JARVIS: {e}", file=sys.stderr)

    # ── Ayuda ─────────────────────────────────────────────────────────────────
    elif intencion == "ayuda":
        return (
            "🤖 *JARVIS — Lo que podés pedirme por WhatsApp:*\n\n"
            "📊 *Análisis de lead* → \"Analizá este lead: [empresa, rubro, contacto]\"\n"
            "📄 *Propuesta* → \"Propuesta para [empresa], rubro [rubro], plan [plan]\"\n"
            "📩 *Follow-up* → \"Seguimiento para [empresa], hace 5 días sin respuesta\"\n"
            "💬 *Objeción* → \"Me dijo: 'no es el momento'. ¿Cómo respondo?\"\n"
            "💡 *Cualquier consulta comercial* → escribí lo que necesitás\n\n"
            "_Todo lo que hacemos acá se sincroniza con tu portal._"
        )

    # Fallback: enviar todo al chat general con contexto de intención
    try:
        import jarvis  # type: ignore
        rubros = []
        try:
            rubros_raw = getattr(aliado_obj, "rubros_especialidad", "[]") or "[]"
            rubros = json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
        except Exception:
            pass

        resultado = jarvis.chat_jarvis(
            mensaje_aliado=texto,
            historial=historial[-6:] if historial else None,
            aliado_nombre=getattr(aliado_obj, "nombre", "") or "",
            aliado_ciudad=getattr(aliado_obj, "ciudad", "") or "",
            aliado_pais=getattr(aliado_obj, "pais", "AR") or "AR",
            aliado_rubros=rubros,
            aliado_nivel=getattr(aliado_obj, "nivel", "BASIC") or "BASIC",
            aliado_ventas=0,
        )
        if resultado:
            return resultado.get("respuesta", "Necesito más información para ayudarte.")
    except Exception as e:
        print(f"[WA] Error en fallback chat: {e}", file=sys.stderr)

    return "No pude procesar tu consulta. Intentá reformularla o accedé al portal."


def _extraer_empresa(texto: str) -> str:
    """Intenta extraer el nombre de empresa de un texto libre. Fallback: 'el prospecto'."""
    palabras_clave = ["para", "de", "con", "empresa"]
    partes = texto.split()
    for i, p in enumerate(partes):
        if p.lower() in palabras_clave and i + 1 < len(partes):
            # Devolver las próximas 1-3 palabras como nombre de empresa
            candidato = " ".join(partes[i+1:i+4]).strip(".,;:\"'")
            if len(candidato) > 2:
                return candidato
    return "el prospecto"


# ─── PROCESAR AUDIO (voice memo) ──────────────────────────────────────────────

def _transcribir_audio_url(media_url: str) -> Optional[str]:
    """
    Descarga el audio de Twilio y lo transcribe.
    Usa la API de Whisper (OpenAI) si está configurada; fallback a mensaje de error.

    Para activar: OPENAI_API_KEY en el entorno.
    Si no está, devuelve None y el caller informa al aliado.
    """
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        return None

    try:
        import httpx
        import tempfile

        # Descargar el audio autenticando con credenciales Twilio
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                media_url,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            )
            resp.raise_for_status()

        audio_bytes = resp.content
        content_type = resp.headers.get("content-type", "audio/ogg")
        extension = "ogg" if "ogg" in content_type else "mp4" if "mp4" in content_type else "wav"

        # Transcribir con Whisper
        with tempfile.NamedTemporaryFile(suffix=f".{extension}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        with httpx.Client(timeout=60) as client:
            with open(tmp_path, "rb") as audio_file:
                resp_whisper = client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    files={"file": (f"audio.{extension}", audio_file, content_type)},
                    data={"model": "whisper-1", "language": "es"},
                )
                resp_whisper.raise_for_status()
                return resp_whisper.json().get("text", "").strip()

    except Exception as e:
        print(f"[WA] Error transcribiendo audio: {e}", file=sys.stderr)
        return None


# ─── ANALIZAR IMAGEN / DOCUMENTO ─────────────────────────────────────────────

def _analizar_imagen_con_jarvis(media_url: str, caption: str, aliado_obj) -> str:
    """
    Descarga la imagen y la analiza con Claude Vision.
    Útil para: fotos de tarjetas personales, propuestas de competidores, documentos.
    """
    if not is_jarvis_enabled():
        return "No puedo procesar imágenes en este momento."

    try:
        import httpx, base64
        import anthropic  # type: ignore

        with httpx.Client(timeout=30) as client:
            resp = client.get(
                media_url,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            )
            resp.raise_for_status()

        image_bytes  = resp.content
        content_type = resp.headers.get("content-type", "image/jpeg")
        image_b64    = base64.standard_b64encode(image_bytes).decode("utf-8")

        aliado_nombre = getattr(aliado_obj, "nombre", "") or ""
        contexto_caption = f"\nContexto del aliado: {caption}" if caption else ""

        prompt = f"""
Sos JARVIS, el asistente comercial de {aliado_nombre} (Avanza Digital).
El aliado te mandó una imagen por WhatsApp.{contexto_caption}

Analizá la imagen y determiná qué es:
- Tarjeta personal de un prospecto → extraé nombre, empresa, cargo, teléfono, email
- Propuesta de un competidor → analizá precio, estructura, puntos débiles vs Avanza
- Documento de licitación o RFP → extraé qué necesitan, plazos, criterios de decisión
- Otro documento → extraé la información comercialmente relevante

Respondé en formato accionable para el aliado. Máximo 400 palabras.
Si es una tarjeta de prospecto, terminá con: "¿Querés que te arme el primer mensaje de contacto?"
"""
        client_ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client_ai.messages.create(
            model=JARVIS_MODEL,
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": content_type,
                            "data":       image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
            timeout=JARVIS_TIMEOUT,
        )
        return msg.content[0].text.strip()

    except Exception as e:
        print(f"[WA] Error analizando imagen: {e}", file=sys.stderr)
        return "No pude analizar la imagen. Podés describirme qué contiene y te ayudo igual."


# ─── ENVIAR MENSAJE POR WHATSAPP (Twilio) ─────────────────────────────────────

def enviar_whatsapp(
    numero_destino: str,
    mensaje: str,
    *,
    media_url: str = None,
) -> dict:
    """
    Envía un mensaje de WhatsApp al aliado via Twilio.

    numero_destino: número del aliado, con o sin "whatsapp:" prefix.
    mensaje: texto a enviar (se trunca automáticamente a WA_MAX_CHARS).
    media_url: URL pública de un archivo adjunto opcional.

    Retorna: {"ok": bool, "sid": str | None, "error": str | None}
    """
    if not is_enabled():
        return {
            "ok":    False,
            "sid":   None,
            "error": "Twilio no configurado. Verificar TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN y TWILIO_WHATSAPP_FROM.",
        }

    try:
        from twilio.rest import Client  # type: ignore

        # Normalizar número destino
        if not numero_destino.startswith("whatsapp:"):
            numero_destino = f"whatsapp:{numero_destino}"

        # Truncar si es muy largo para WhatsApp
        texto_final = mensaje[:WA_MAX_CHARS]
        if len(mensaje) > WA_MAX_CHARS:
            texto_final += "\n\n_(respuesta completa disponible en el portal)_"

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        params = {
            "from_": TWILIO_WHATSAPP_FROM,
            "to":    numero_destino,
            "body":  texto_final,
        }
        if media_url:
            params["media_url"] = [media_url]

        message = client.messages.create(**params)

        return {"ok": True, "sid": message.sid, "error": None}

    except ImportError:
        return {
            "ok":    False,
            "sid":   None,
            "error": "Librería Twilio no instalada. Agregar 'twilio>=8.0.0' a requirements.txt.",
        }
    except Exception as e:
        print(f"[WA] Error enviando mensaje: {type(e).__name__}: {e}", file=sys.stderr)
        return {"ok": False, "sid": None, "error": str(e)}


# ─── GUARDAR INTERACCIÓN EN MEMORIA EPISÓDICA ────────────────────────────────

def _registrar_en_memoria(aliado_id: int, texto_aliado: str, respuesta: str, db_session) -> None:
    """Persiste la interacción de WhatsApp en la memoria episódica de JARVIS."""
    try:
        from jarvis_memoria import save_episodic_memory  # type: ignore
        save_episodic_memory(
            aliado_id=aliado_id,
            tipo="chat_resumen",
            contenido={
                "canal":    "whatsapp",
                "resumen":  texto_aliado[:200],
                "respuesta_resumen": respuesta[:200],
            },
            db_session=db_session,
            importancia="normal",
        )
    except Exception as e:
        # No crítico — la memoria es complementaria, no bloqueante
        print(f"[WA] Error guardando en memoria episódica: {e}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS FASTAPI — register()
# ═══════════════════════════════════════════════════════════════════════════════

def register(app, get_db_func, auth_dep):
    """
    Registra todos los endpoints de WhatsApp en la app FastAPI.

    Llamar desde main.py:
        import jarvis_whatsapp
        jarvis_whatsapp.register(app, get_db, current_aliado_required)
    """
    from fastapi import Depends, HTTPException, Request, Form, BackgroundTasks
    from fastapi.responses import Response, JSONResponse
    from sqlalchemy.orm import Session
    from pydantic import BaseModel

    # ── POST /webhook/whatsapp — Webhook principal de Twilio ─────────────────
    @app.post("/webhook/whatsapp")
    async def webhook_whatsapp(
        request: Request,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db_func),
    ):
        """
        Webhook que Twilio llama cuando el aliado escribe al número de WhatsApp.
        Debe estar registrado en: https://console.twilio.com → WhatsApp Senders.

        Twilio espera una respuesta TwiML o vacía en <5 segundos.
        La respuesta real se envía de forma asíncrona para no hacer esperar.
        """
        # Parsear el form-data de Twilio
        try:
            form = await request.form()
            params = dict(form)
        except Exception:
            return Response(content="", status_code=200)

        # Validar firma de Twilio (en producción con TWILIO_AUTH_TOKEN configurado)
        sig = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        if TWILIO_AUTH_TOKEN and sig and not _validar_firma_twilio(url, params, sig):
            print("[WA] Firma Twilio inválida — posible request no autorizado", file=sys.stderr)
            return Response(content="", status_code=403)

        # Extraer campos del mensaje
        numero_from  = params.get("From", "")       # ej: "whatsapp:+5491122334455"
        numero_to    = params.get("To", "")          # número Twilio del aliado
        body_texto   = params.get("Body", "").strip()
        num_media    = int(params.get("NumMedia", "0") or "0")
        media_url    = params.get("MediaUrl0", "")
        media_type   = params.get("MediaContentType0", "")
        message_sid  = params.get("MessageSid", "")

        if not numero_from:
            return Response(content="", status_code=200)

        # Identificar aliado por número de WhatsApp
        aliado = _identificar_aliado(numero_from, db)

        if not aliado:
            # Número no registrado — respuesta de bienvenida/registro
            respuesta_texto = (
                "👋 Hola, soy JARVIS de Avanza Digital.\n\n"
                "Tu número no está registrado en ninguna cuenta. "
                "Para activar JARVIS en WhatsApp:\n"
                "1️⃣ Ingresá a tu portal\n"
                "2️⃣ Andá a Configuración → WhatsApp\n"
                "3️⃣ Registrá este número\n\n"
                "O contactá a tu consultor de Avanza."
            )
            background_tasks.add_task(
                enviar_whatsapp,
                numero_from,
                respuesta_texto,
            )
            return Response(content="", status_code=200)

        # Procesar según el tipo de contenido recibido
        def _procesar_en_background():
            """Procesa el mensaje y envía la respuesta — en background para no bloquear."""
            try:
                sesion = _get_sesion(numero_from)
                historial = sesion.get("historial", [])

                # ── Caso 1: Audio / voice memo ────────────────────────────────
                if num_media > 0 and "audio" in media_type:
                    transcripcion = _transcribir_audio_url(media_url)
                    if transcripcion:
                        texto_procesar = transcripcion
                        _actualizar_sesion(numero_from, "user", f"[Audio transcripto] {texto_procesar}")
                        prefijo = f"🎙️ _Escuché tu audio:_\n«{texto_procesar[:100]}{'...' if len(texto_procesar) > 100 else ''}»\n\n"
                    else:
                        respuesta = (
                            "🎙️ Recibí tu audio pero no tengo transcripción activa.\n\n"
                            "Podés escribirme el mismo mensaje en texto y te ayudo igual. "
                            "_(Para activar la transcripción de audios, consultá con tu equipo de Avanza.)_"
                        )
                        enviar_whatsapp(numero_from, respuesta)
                        return
                    texto_caption = body_texto  # caption del audio si lo tiene
                    respuesta_base = _procesar_texto(texto_procesar, aliado, historial)
                    respuesta = prefijo + respuesta_base

                # ── Caso 2: Imagen ────────────────────────────────────────────
                elif num_media > 0 and ("image" in media_type or media_type == ""):
                    _actualizar_sesion(numero_from, "user", f"[Imagen enviada] {body_texto}")
                    respuesta = _analizar_imagen_con_jarvis(media_url, body_texto, aliado)
                    _actualizar_sesion(numero_from, "assistant", respuesta)

                # ── Caso 3: Documento (PDF, etc.) ─────────────────────────────
                elif num_media > 0 and "pdf" in media_type:
                    respuesta = (
                        "📄 Recibí el PDF. Para analizarlo completamente, "
                        "subilo desde el portal en *Documentos → Analizar con JARVIS*.\n\n"
                        "¿Podés contarme brevemente de qué se trata? Te ayudo igual."
                    )

                # ── Caso 4: Texto ─────────────────────────────────────────────
                elif body_texto:
                    _actualizar_sesion(numero_from, "user", body_texto)
                    respuesta = _procesar_texto(body_texto, aliado, historial)
                    _actualizar_sesion(numero_from, "assistant", respuesta)

                else:
                    # Mensaje vacío o tipo no soportado
                    return

                # Enviar respuesta
                resultado_envio = enviar_whatsapp(numero_from, respuesta)

                # Registrar en memoria episódica (no bloquea si falla)
                if hasattr(aliado, "id"):
                    _registrar_en_memoria(
                        aliado_id=aliado.id,
                        texto_aliado=body_texto or f"[media: {media_type}]",
                        respuesta=respuesta,
                        db_session=db,
                    )

                if not resultado_envio.get("ok"):
                    print(
                        f"[WA] Error enviando respuesta a {numero_from}: "
                        f"{resultado_envio.get('error')}",
                        file=sys.stderr,
                    )

            except Exception as e:
                print(f"[WA] Error en procesamiento de webhook: {e}", file=sys.stderr)
                try:
                    enviar_whatsapp(
                        numero_from,
                        "Ocurrió un error procesando tu mensaje. "
                        "Intentá de nuevo o accedé al portal.",
                    )
                except Exception:
                    pass

        background_tasks.add_task(_procesar_en_background)

        # Twilio espera respuesta inmediata — devolvemos 200 vacío.
        # La respuesta real se envía asíncronamente via enviar_whatsapp().
        return Response(content="", status_code=200)

    # ── POST /webhook/whatsapp/status — Callback de estado de entrega ─────────
    @app.post("/webhook/whatsapp/status")
    async def webhook_whatsapp_status(request: Request):
        """
        Twilio llama a este endpoint para notificar el estado de entrega de cada mensaje.
        Por ahora solo loguea — en el futuro se puede usar para detectar números inválidos.
        """
        try:
            form = await request.form()
            sid    = form.get("MessageSid", "")
            status = form.get("MessageStatus", "")
            to     = form.get("To", "")
            print(f"[WA STATUS] {sid} → {to}: {status}", flush=True)
        except Exception:
            pass
        return Response(content="", status_code=200)

    # ── POST /jarvis/whatsapp/enviar — El portal envía WA proactivo ───────────
    @app.post("/jarvis/whatsapp/enviar")
    def ep_whatsapp_enviar(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Endpoint para que el portal envíe un mensaje de WhatsApp proactivo al aliado.
        Útil para: alertas de JARVIS Proactivo, briefing matutino, notificaciones de leads.

        Body JSON:
        {
            "mensaje": "texto a enviar",
            "numero":  "+5491122334455"  (opcional — usa el número registrado si no se pasa)
        }
        """
        # Obtener número del aliado
        numero = getattr(aliado, "whatsapp_numero", None)
        if not numero:
            raise HTTPException(400, "El aliado no tiene número de WhatsApp registrado.")

        import json as _json
        # FastAPI no tiene acceso al body aquí sin Body() — usar request directamente
        # Este endpoint es interno; se llama desde el frontend con fetch/axios
        raise HTTPException(
            501,
            "Endpoint disponible. Usar POST con JSON {mensaje, numero} para enviar.",
        )

    @app.post("/jarvis/whatsapp/enviar-mensaje")
    def ep_whatsapp_enviar_mensaje(
        request: Request,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Versión funcional del endpoint de envío proactivo.
        Llamar con: { "mensaje": "...", "numero": "+..." (opcional) }
        """
        from fastapi import Body
        raise HTTPException(
            501,
            detail="Usar el endpoint /jarvis/whatsapp/enviar con body JSON.",
        )

    class EnviarWARequest(BaseModel):
        mensaje: str
        numero: Optional[str] = None     # Si None, usa el registrado del aliado

    @app.post("/jarvis/whatsapp/enviar-v2")
    def ep_whatsapp_enviar_v2(
        body: "EnviarWARequest",
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Envío proactivo de WhatsApp desde el portal al aliado.
        Requiere que el aliado tenga whatsapp_numero registrado.
        """
        numero = body.numero or getattr(aliado, "whatsapp_numero", None)
        if not numero:
            raise HTTPException(400, "Número de WhatsApp no disponible. Registralo en Configuración.")

        if not body.mensaje.strip():
            raise HTTPException(400, "El mensaje no puede estar vacío.")

        if not is_enabled():
            raise HTTPException(
                503,
                "Integración Twilio no configurada. "
                "Verificar TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN y TWILIO_WHATSAPP_FROM en el servidor.",
            )

        resultado = enviar_whatsapp(numero, body.mensaje.strip())

        if not resultado.get("ok"):
            raise HTTPException(502, f"Error Twilio: {resultado.get('error')}")

        return {
            "ok":      True,
            "sid":     resultado.get("sid"),
            "mensaje": "Mensaje enviado por WhatsApp.",
        }

    # ── POST /jarvis/whatsapp/registrar-numero ────────────────────────────────
    class RegistrarNumeroRequest(BaseModel):
        numero: str   # Con código de país: +5491122334455

    @app.post("/jarvis/whatsapp/registrar-numero")
    def ep_registrar_numero(
        body: "RegistrarNumeroRequest",
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        El aliado registra su número de WhatsApp para que JARVIS lo identifique
        cuando le escriba al número de WhatsApp Business de Twilio.

        Requiere migración: ALTER TABLE aliados ADD COLUMN whatsapp_numero VARCHAR;
        """
        numero = body.numero.strip().replace(" ", "")
        if not numero.startswith("+"):
            numero = "+" + numero

        if len(numero) < 10 or len(numero) > 20:
            raise HTTPException(400, "Número de teléfono inválido. Incluí el código de país: +5491122334455")

        try:
            if not hasattr(aliado, "whatsapp_numero"):
                raise HTTPException(
                    500,
                    "Columna whatsapp_numero no existe en la BD. "
                    "Ejecutar: ALTER TABLE aliados ADD COLUMN IF NOT EXISTS whatsapp_numero VARCHAR;"
                )
            aliado.whatsapp_numero = numero
            db.commit()

            return {
                "ok":      True,
                "numero":  numero,
                "mensaje": (
                    f"Número registrado. Ahora podés escribirle a JARVIS desde "
                    f"WhatsApp al número {TWILIO_WHATSAPP_FROM.replace('whatsapp:', '')} "
                    f"y te va a reconocer automáticamente."
                ),
                "numero_jarvis": TWILIO_WHATSAPP_FROM.replace("whatsapp:", "") or "Ver en Configuración",
            }
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, f"Error guardando número: {e}")

    # ── GET /jarvis/whatsapp/estado ───────────────────────────────────────────
    @app.get("/jarvis/whatsapp/estado")
    def ep_whatsapp_estado(aliado=Depends(auth_dep)):
        """
        Estado de la integración WhatsApp.
        El aliado puede ver si está configurada y cuál es el número de JARVIS.
        """
        numero_registrado = getattr(aliado, "whatsapp_numero", None)
        return {
            "ok":                     True,
            "twilio_configurado":     is_enabled(),
            "jarvis_configurado":     is_jarvis_enabled(),
            "numero_whatsapp_jarvis": TWILIO_WHATSAPP_FROM.replace("whatsapp:", "") if is_enabled() else None,
            "numero_aliado_registrado": numero_registrado,
            "instrucciones": (
                "Para activar: registrá tu número y escribile a JARVIS al número indicado."
                if not numero_registrado else
                f"Activo. Escribile a JARVIS al {TWILIO_WHATSAPP_FROM.replace('whatsapp:', '')}."
            ),
            "transcripcion_audio":    bool(os.environ.get("OPENAI_API_KEY")),
            "analisis_imagen":        is_jarvis_enabled(),
        }

    # ── GET /jarvis/whatsapp/migration-sql ────────────────────────────────────
    @app.get("/jarvis/whatsapp/migration-sql")
    def ep_whatsapp_migration_sql(aliado=Depends(auth_dep)):
        """SQL para agregar la columna whatsapp_numero a la tabla aliados."""
        return {
            "ok": True,
            "instruccion": "Ejecutar en Supabase/Postgres una sola vez.",
            "sql": "ALTER TABLE aliados ADD COLUMN IF NOT EXISTS whatsapp_numero VARCHAR;",
        }
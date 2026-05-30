"""
jarvis_setter.py — JARVIS Setter: embudo WhatsApp-first de cara al PROSPECTO

Esta es la pieza que faltaba en la integración de WhatsApp. Hasta ahora
`jarvis_whatsapp.py` resolvía el caso "el ALIADO le escribe a JARVIS" (asistente
del vendedor). Cuando un NÚMERO DESCONOCIDO escribía, el flujo moría con un
"no estás registrado".

Este módulo convierte ese número desconocido en una oportunidad: JARVIS actúa
como SETTER —atiende al prospecto, lo precalifica conversando, lo etiqueta, lo
puntúa y o bien lo agenda / escala al humano (que cierra), o lo nutre. Inspirado
en el modelo de Funnelchat, pero corriendo sobre el JARVIS propio de Avanza.

QUÉ APORTA (los frentes del benchmark Funnelchat):
  (a) Embudos nativos en WhatsApp con disparadores por PALABRA CLAVE
      → el prospecto escribe AGRO / METAL / LOGISTICA / PRECIO / DEMO / WEB
        y entra al embudo correcto, ya segmentado.
  (b) Agente IA tipo "setter" que precalifica y agenda solo
      → la IA recolecta rubro/tamaño/urgencia/dolor/contacto, puntúa con
        jarvis_leads, y cuando el lead está caliente lo escala al aliado/humano
        ("la IA prepara el pitch, vos cerrás").
  (c) Etiquetado y segmentación automática por respuesta
      → cada turno actualiza etiquetas (CALIFICADO, FRIO, AGRO, URGENTE, ...).
  (d) Enlaces de marca / acortados con tracking
      → crear_enlace() + redirect /l/{slug} con conteo de clics y ?ref.

NO ES INVASIVO:
  - No toca models.py: crea sus propias tablas con CREATE TABLE IF NOT EXISTS.
  - No toca el flujo del aliado: solo se engancha en la rama "número desconocido".
  - Reutiliza jarvis_whatsapp.enviar_whatsapp() para mandar mensajes.
  - Si ANTHROPIC_API_KEY o la BD fallan, usa fallback scripteado y nunca tira la app.

INTEGRACIÓN (3 pasos):

  1) jarvis_whatsapp.py — en el webhook, rama `if not aliado:` (número desconocido),
     antes del mensaje de "no registrado", delegá al setter:

        try:
            import jarvis_setter
            if jarvis_setter.is_enabled():
                resp = jarvis_setter.manejar_inbound_prospecto(
                    numero=numero_from, texto=body_texto, db=db
                )
                if resp:
                    background_tasks.add_task(enviar_whatsapp, numero_from, resp)
                    return Response(content="", status_code=200)
        except Exception as _e:
            print(f"[WA] setter no disponible: {_e}", file=sys.stderr)
     (Este parche ya quedó aplicado en jarvis_whatsapp.py de este repo.)

  2) main.py — registrar endpoints y migrar tablas (junto a los otros register()):

        import jarvis_setter
        jarvis_setter.register(app, get_db, current_aliado_required)
        jarvis_setter.run_migrations(engine)            # crea tablas si no existen

     y en el scheduler (junto a los jobs de canal1):

        scheduler.add_job(jarvis_setter.job_seguimientos, "interval", hours=2)

  3) Variables de entorno (todas opcionales, con defaults sanos):
        SETTER_ENABLED            = "1"  (default "1")
        SETTER_UMBRAL_CALIFICADO  = "65" (score >= => se escala al humano)
        AGENDA_URL                = link de agenda (Calendly/Cal.com)  [opcional]
        SETTER_HANDOFF_NUMERO     = WhatsApp humano fallback si no hay aliado asignado
        PORTAL_URL / BACKEND_PUBLIC_URL ya existentes para armar short links.
"""

from __future__ import annotations

import os
import re
import sys
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 18.0

SETTER_ON              = os.environ.get("SETTER_ENABLED", "1").strip() not in ("0", "false", "False", "")
UMBRAL_CALIFICADO      = int(os.environ.get("SETTER_UMBRAL_CALIFICADO", "65") or "65")
AGENDA_URL             = os.environ.get("AGENDA_URL", "").strip()
HANDOFF_NUMERO         = os.environ.get("SETTER_HANDOFF_NUMERO", "").strip()
PORTAL_URL             = os.environ.get("PORTAL_URL", os.environ.get("BACKEND_PUBLIC_URL", "https://avanzadigital.com")).strip().rstrip("/")
BASE_URL               = os.environ.get("BACKEND_PUBLIC_URL", PORTAL_URL).strip().rstrip("/")

# Cantidad de campos de calificación que disparan el scoring/decisión
CAMPOS_MINIMOS = 3

PAISES = {"AR": "Argentina", "MX": "México", "CO": "Colombia",
          "CL": "Chile", "PE": "Perú", "UY": "Uruguay"}


def is_enabled() -> bool:
    return SETTER_ON


def is_jarvis_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


# ═══════════════════════════════════════════════════════════════════════════════
# EMBUDOS POR PALABRA CLAVE
# ═══════════════════════════════════════════════════════════════════════════════
# Cada embudo mapea una intención/segmento a: rubro, landing de marca (para el
# short link con tracking) y una etiqueta base. La IA setter usa esto como contexto.

FUNNELS: dict[str, dict] = {
    "AGRO": {
        "rubro": "agro", "etiqueta": "AGRO",
        "landing": f"{PORTAL_URL}/agro.html",
        "gancho": "automatización de cotizaciones y seguimiento de clientes para el agro",
    },
    "METAL": {
        "rubro": "metalurgica", "etiqueta": "METALURGICA",
        "landing": f"{PORTAL_URL}/metalurgica.html",
        "gancho": "captación y cotización automática para metalúrgicas",
    },
    "LOGISTICA": {
        "rubro": "logistica", "etiqueta": "LOGISTICA",
        "landing": f"{PORTAL_URL}/logistica.html",
        "gancho": "seguimiento de operaciones y leads para logística",
    },
    "CONSTRUCCION": {
        "rubro": "construccion", "etiqueta": "CONSTRUCCION",
        "landing": f"{PORTAL_URL}/construccion.html",
        "gancho": "presupuestos y seguimiento de obra automatizados",
    },
    "DEMO": {
        "rubro": None, "etiqueta": "QUIERE_DEMO",
        "landing": f"{PORTAL_URL}/comenzar.html",
        "gancho": "una demo del sistema funcionando con tu caso",
    },
    "PRECIO": {
        "rubro": None, "etiqueta": "PREGUNTA_PRECIO",
        "landing": f"{PORTAL_URL}/pago-unico-vs-alquiler-mensual.html",
        "gancho": "los planes y la diferencia entre pago único y mensual",
    },
    "WEB": {
        "rubro": None, "etiqueta": "AUDITORIA",
        "landing": f"{PORTAL_URL}/auditoria-digital.html",
        "gancho": "una auditoría gratis de tu presencia digital",
    },
}

# Sinónimos → clave canónica de embudo
_ALIAS_FUNNEL = {
    "agro": "AGRO", "campo": "AGRO", "agropecuari": "AGRO",
    "metal": "METAL", "metalurgic": "METAL", "herrer": "METAL",
    "logistic": "LOGISTICA", "transporte": "LOGISTICA", "flota": "LOGISTICA",
    "construc": "CONSTRUCCION", "obra": "CONSTRUCCION", "corralon": "CONSTRUCCION",
    "demo": "DEMO", "probar": "DEMO", "prueba": "DEMO",
    "precio": "PRECIO", "costo": "PRECIO", "cuanto sale": "PRECIO", "cuánto sale": "PRECIO", "plan": "PRECIO",
    "web": "WEB", "auditoria": "WEB", "auditoría": "WEB", "pagina": "WEB", "página": "WEB",
}


def _detectar_embudo(texto: str) -> Optional[str]:
    """Devuelve la clave de embudo si el texto la dispara (palabra clave o sinónimo)."""
    t = (texto or "").lower().strip()
    if not t:
        return None
    # Palabra clave exacta (ej: el prospecto escribe solo "AGRO")
    if t.upper() in FUNNELS:
        return t.upper()
    for frag, clave in _ALIAS_FUNNEL.items():
        if frag in t:
            return clave
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# IA: llamada defensiva a Claude (mismo patrón que jarvis_leads._chat)
# ═══════════════════════════════════════════════════════════════════════════════

def _chat(prompt: str, system: str, *, max_tokens: int = 1100, json_mode: bool = True) -> Optional[str]:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        system_final = system
        if json_mode:
            system_final += ("\n\nIMPORTANTE: Respondé ÚNICAMENTE con JSON válido. "
                             "Sin texto antes ni después. Sin bloques de código markdown.")
        msg = client.messages.create(
            model=JARVIS_MODEL,
            max_tokens=max_tokens,
            system=system_final,
            messages=[{"role": "user", "content": prompt}],
            timeout=JARVIS_TIMEOUT,
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"[SETTER ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _parse_json(texto: str) -> Optional[dict]:
    if not texto:
        return None
    for intento in (texto, texto[texto.find("{"): texto.rfind("}") + 1] if "{" in texto else ""):
        try:
            val = json.loads(intento)
            if isinstance(val, dict):
                return val
        except Exception:
            continue
    print(f"[SETTER] No se pudo parsear JSON: {texto[:160]}", file=sys.stderr)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCIA — tablas propias del setter (no toca models.py)
# ═══════════════════════════════════════════════════════════════════════════════

MIGRATION_SQL = [
    """
    CREATE TABLE IF NOT EXISTS setter_sesiones (
        id              SERIAL PRIMARY KEY,
        numero          VARCHAR UNIQUE NOT NULL,
        nombre          VARCHAR,
        embudo          VARCHAR,
        rubro           VARCHAR,
        estado          VARCHAR DEFAULT 'calificando',
        score           INTEGER DEFAULT 0,
        etiquetas       TEXT    DEFAULT '[]',
        datos           TEXT    DEFAULT '{}',
        historial       TEXT    DEFAULT '[]',
        aliado_id       INTEGER,
        escalado        BOOLEAN DEFAULT FALSE,
        seguimiento_paso INTEGER DEFAULT 0,
        ultimo_inbound  TIMESTAMP,
        ultimo_outbound TIMESTAMP,
        creado_en       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS setter_enlaces (
        id          SERIAL PRIMARY KEY,
        slug        VARCHAR UNIQUE NOT NULL,
        destino     VARCHAR NOT NULL,
        ref_code    VARCHAR,
        aliado_id   INTEGER,
        clicks      INTEGER DEFAULT 0,
        creado_en   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]


def run_migrations(engine) -> None:
    """Crea las tablas del setter si no existen. Llamar una vez al boot desde main.py."""
    try:
        with engine.begin() as conn:
            for sql in MIGRATION_SQL:
                conn.execute(text(sql))
        print("[SETTER] Migraciones OK (setter_sesiones, setter_enlaces)", flush=True)
    except Exception as e:
        # SQLite (tests/local) no tiene SERIAL — fallback a INTEGER autoincrement
        try:
            with engine.begin() as conn:
                for sql in MIGRATION_SQL:
                    conn.execute(text(sql.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")))
            print("[SETTER] Migraciones OK (modo SQLite)", flush=True)
        except Exception as e2:
            print(f"[SETTER] Error en migraciones: {e} / {e2}", file=sys.stderr)


def _cargar_sesion(db, numero: str) -> dict:
    """Trae la sesión del prospecto o devuelve una vacía en memoria."""
    base = {"numero": numero, "nombre": None, "embudo": None, "rubro": None,
            "estado": "calificando", "score": 0, "etiquetas": [], "datos": {},
            "historial": [], "aliado_id": None, "escalado": False, "seguimiento_paso": 0}
    try:
        row = db.execute(
            text("SELECT nombre, embudo, rubro, estado, score, etiquetas, datos, "
                 "historial, aliado_id, escalado, seguimiento_paso "
                 "FROM setter_sesiones WHERE numero = :n"),
            {"n": numero},
        ).fetchone()
        if row:
            base.update({
                "nombre": row[0], "embudo": row[1], "rubro": row[2],
                "estado": row[3] or "calificando", "score": row[4] or 0,
                "etiquetas": _safe_json(row[5], []), "datos": _safe_json(row[6], {}),
                "historial": _safe_json(row[7], []), "aliado_id": row[8],
                "escalado": bool(row[9]), "seguimiento_paso": row[10] or 0,
            })
    except Exception as e:
        print(f"[SETTER] _cargar_sesion: {e}", file=sys.stderr)
    return base


def _guardar_sesion(db, s: dict) -> None:
    ahora = datetime.utcnow()
    params = {
        "numero": s["numero"], "nombre": s.get("nombre"), "embudo": s.get("embudo"),
        "rubro": s.get("rubro"), "estado": s.get("estado", "calificando"),
        "score": int(s.get("score", 0)), "etiquetas": json.dumps(s.get("etiquetas", []), ensure_ascii=False),
        "datos": json.dumps(s.get("datos", {}), ensure_ascii=False),
        "historial": json.dumps(s.get("historial", [])[-20:], ensure_ascii=False),
        "aliado_id": s.get("aliado_id"), "escalado": bool(s.get("escalado", False)),
        "seguimiento_paso": int(s.get("seguimiento_paso", 0)), "ahora": ahora,
    }
    try:
        existe = db.execute(text("SELECT 1 FROM setter_sesiones WHERE numero = :n"),
                            {"n": s["numero"]}).fetchone()
        if existe:
            db.execute(text(
                "UPDATE setter_sesiones SET nombre=:nombre, embudo=:embudo, rubro=:rubro, "
                "estado=:estado, score=:score, etiquetas=:etiquetas, datos=:datos, "
                "historial=:historial, aliado_id=:aliado_id, escalado=:escalado, "
                "seguimiento_paso=:seguimiento_paso, ultimo_inbound=:ahora WHERE numero=:numero"
            ), params)
        else:
            db.execute(text(
                "INSERT INTO setter_sesiones (numero, nombre, embudo, rubro, estado, score, "
                "etiquetas, datos, historial, aliado_id, escalado, seguimiento_paso, ultimo_inbound) "
                "VALUES (:numero,:nombre,:embudo,:rubro,:estado,:score,:etiquetas,:datos,"
                ":historial,:aliado_id,:escalado,:seguimiento_paso,:ahora)"
            ), params)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[SETTER] _guardar_sesion: {e}", file=sys.stderr)


def _safe_json(raw, default):
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or default)
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════════════════════════
# ASIGNACIÓN DE ALIADO (a quién se le entrega el lead caliente)
# ═══════════════════════════════════════════════════════════════════════════════

def _asignar_aliado(db, rubro: Optional[str], pais: str = "AR"):
    """Busca el mejor aliado activo para el rubro/país. Best-effort, nunca falla."""
    try:
        from models import Aliado  # type: ignore
        q = db.query(Aliado).filter(Aliado.activo == True)  # noqa: E712
        candidatos = q.filter(Aliado.pais == pais).all() or q.all()
        if not candidatos:
            return None
        if rubro:
            for a in candidatos:
                rubros = _safe_json(getattr(a, "rubros_especialidad", "[]"), [])
                if any(rubro.lower() in str(r).lower() for r in rubros):
                    return a
        # Fallback: el de mayor reputación
        candidatos.sort(key=lambda a: getattr(a, "reputacion_score", 0) or 0, reverse=True)
        return candidatos[0]
    except Exception as e:
        print(f"[SETTER] _asignar_aliado: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SHORT LINKS CON TRACKING (frente (d) del benchmark)
# ═══════════════════════════════════════════════════════════════════════════════

def crear_enlace(db, destino: str, *, ref_code: Optional[str] = None,
                 aliado_id: Optional[int] = None, slug: Optional[str] = None) -> str:
    """Crea un short link de marca con tracking y devuelve la URL corta."""
    slug = (slug or secrets.token_urlsafe(4)).replace("/", "").replace("+", "")[:12]
    if ref_code and "ref=" not in destino:
        sep = "&" if "?" in destino else "?"
        destino = f"{destino}{sep}ref={ref_code}"
    try:
        db.execute(text(
            "INSERT INTO setter_enlaces (slug, destino, ref_code, aliado_id) "
            "VALUES (:slug,:destino,:ref,:aliado_id)"
        ), {"slug": slug, "destino": destino, "ref": ref_code, "aliado_id": aliado_id})
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[SETTER] crear_enlace: {e}", file=sys.stderr)
        return destino  # si falla el tracking, al menos devolvemos el destino real
    return f"{BASE_URL}/l/{slug}"


# ═══════════════════════════════════════════════════════════════════════════════
# NÚCLEO: el setter conversa con el prospecto
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_SETTER = """\
Sos JARVIS, el setter comercial de Avanza Digital (LATAM). Avanza vende sistemas
de captación y automatización de ventas para pymes industriales (agro, metalúrgica,
logística, construcción, etc.). Hay dos modelos: pago único (código fuente propio)
y mensual.

Tu trabajo NO es cerrar la venta: es PRECALIFICAR conversando por WhatsApp y, cuando
el lead está caliente, prepararlo para que lo cierre un humano. Sos cálido, breve y
porteño-neutro (tuteo/voseo), 1-3 frases por turno, sin sonar a bot. Hacé UNA pregunta
por vez. Nunca inventes precios exactos ni prometas cosas; si preguntan precio, decí
que depende del caso y ofrecé agendar/derivar.

Tenés que ir completando estos campos: rubro, empresa, tamano (chico/mediano/grande),
urgencia (alta/media/baja/explorando), dolor (qué problema quieren resolver),
nombre (cómo lo/la llamamos). Pedí lo que falte de forma natural, sin interrogar.

Devolvé SIEMPRE un JSON con esta forma exacta:
{
  "respuesta": "texto a enviarle al prospecto por WhatsApp",
  "datos": {"rubro": "...", "empresa": "...", "tamano": "...", "urgencia": "...", "dolor": "...", "nombre": "..."},
  "etiquetas": ["MAYUSCULA_SNAKE", ...],
  "estado": "calificando|calificado|frio|agendar",
  "accion": "continuar|escalar|enviar_link|cerrar"
}
Reglas de accion/estado:
- "escalar"+"calificado": cuando ya hay rubro + urgencia (alta/media) + algún dolor claro.
  En "respuesta" avisale que lo vas a conectar con un especialista que le arma una propuesta.
- "enviar_link": cuando conviene mandarle material (demo/auditoría/planes). NO inventes la URL,
  el sistema la inyecta; solo dejá la respuesta lista para que se le anteponga/sume el link.
- "frio": si está solo curioseando o sin urgencia → respuesta breve + ofrecé material.
- "continuar": seguí calificando.
En "datos" devolvé SOLO los campos que pudiste inferir o confirmar (no inventes).
"""


def _opener(embudo_key: Optional[str], primera_vez: bool) -> Optional[str]:
    """Mensaje de apertura cuando recién entra (por palabra clave o saludo)."""
    if not primera_vez:
        return None
    if embudo_key and embudo_key in FUNNELS:
        gancho = FUNNELS[embudo_key]["gancho"]
        return (f"¡Hola! 👋 Soy JARVIS, de Avanza Digital. Vi que te interesa {gancho}. "
                f"Para orientarte bien: ¿de qué rubro es tu empresa y qué te gustaría resolver?")
    return ("¡Hola! 👋 Soy JARVIS, de Avanza Digital. Ayudamos a pymes a captar y cerrar "
            "más clientes con automatización. Contame: ¿a qué se dedica tu empresa?")


def _score_heuristico(datos: dict) -> int:
    """Fallback de scoring si jarvis_leads no está disponible."""
    score = 30
    urg = (datos.get("urgencia") or "").lower()
    score += {"alta": 35, "media": 20, "baja": 5}.get(urg, 0)
    if datos.get("rubro"):  score += 10
    if datos.get("dolor"):  score += 15
    tam = (datos.get("tamano") or "").lower()
    score += {"grande": 10, "mediano": 8, "chico": 4}.get(tam, 0)
    return max(0, min(100, score))


def _calcular_score(datos: dict, pais: str = "AR") -> int:
    """Usa el motor jarvis_leads si está; si no, heurística."""
    try:
        import jarvis_leads  # type: ignore
        if hasattr(jarvis_leads, "analizar_lead_completo") and jarvis_leads.is_enabled():
            res = jarvis_leads.analizar_lead_completo(
                empresa=datos.get("empresa") or "Prospecto",
                rubro=datos.get("rubro") or "general",
                contexto=json.dumps(datos, ensure_ascii=False),
                aliado_pais=pais,
            )
            if isinstance(res, dict) and isinstance(res.get("score"), (int, float)):
                return int(res["score"])
    except Exception as e:
        print(f"[SETTER] scoring jarvis_leads no disponible: {e}", file=sys.stderr)
    return _score_heuristico(datos)


def manejar_inbound_prospecto(numero: str, texto: str, db) -> Optional[str]:
    """
    Entrypoint llamado desde el webhook de WhatsApp cuando escribe un número
    NO registrado como aliado. Devuelve el texto a responderle al prospecto
    (o None si el setter decide no intervenir).
    """
    if not is_enabled():
        return None

    numero = (numero or "").replace("whatsapp:", "").strip()
    texto = (texto or "").strip()

    s = _cargar_sesion(db, numero)
    primera_vez = not s.get("historial")

    # Disparo por palabra clave → fija/actualiza el embudo y rubro
    embudo_key = _detectar_embudo(texto)
    if embudo_key:
        s["embudo"] = embudo_key
        if FUNNELS[embudo_key].get("rubro"):
            s["rubro"] = FUNNELS[embudo_key]["rubro"]
        et = FUNNELS[embudo_key]["etiqueta"]
        if et not in s["etiquetas"]:
            s["etiquetas"].append(et)

    s["historial"].append({"role": "user", "content": texto or "[mensaje vacío]"})

    # ── Si la IA no está disponible: apertura scripteada / pregunta por el campo faltante
    if not is_jarvis_enabled():
        resp = _opener(embudo_key, primera_vez) or _fallback_siguiente_pregunta(s["datos"])
        s["historial"].append({"role": "assistant", "content": resp})
        _guardar_sesion(db, s)
        return resp

    # ── Conversación con la IA setter
    contexto = {
        "embudo": s.get("embudo"),
        "datos_ya_capturados": s.get("datos", {}),
        "es_primer_mensaje": primera_vez,
        "ultimos_turnos": s["historial"][-8:],
        "mensaje_actual": texto,
    }
    raw = _chat(json.dumps(contexto, ensure_ascii=False), _SYSTEM_SETTER, json_mode=True)
    data = _parse_json(raw) if raw else None

    if not data:
        # Fallback total: apertura o siguiente pregunta
        resp = _opener(embudo_key, primera_vez) or _fallback_siguiente_pregunta(s["datos"])
        s["historial"].append({"role": "assistant", "content": resp})
        _guardar_sesion(db, s)
        return resp

    # Merge de datos capturados (sin pisar con vacíos)
    for k, v in (data.get("datos") or {}).items():
        if v and str(v).strip():
            s["datos"][k] = str(v).strip()
    if s["datos"].get("rubro") and not s.get("rubro"):
        s["rubro"] = s["datos"]["rubro"]
    if s["datos"].get("nombre") and not s.get("nombre"):
        s["nombre"] = s["datos"]["nombre"]

    # Etiquetas nuevas
    for et in (data.get("etiquetas") or []):
        et = str(et).strip().upper().replace(" ", "_")
        if et and et not in s["etiquetas"]:
            s["etiquetas"].append(et)

    respuesta = (data.get("respuesta") or "").strip()
    estado = (data.get("estado") or "calificando").strip()
    accion = (data.get("accion") or "continuar").strip()

    # Recalcular score cuando hay info suficiente
    campos_llenos = sum(1 for k in ("rubro", "tamano", "urgencia", "dolor") if s["datos"].get(k))
    if campos_llenos >= CAMPOS_MINIMOS:
        s["score"] = _calcular_score(s["datos"], pais="AR")

    # ── Decisión final
    pais = "AR"
    if accion == "enviar_link" or estado == "frio":
        link = _link_para_embudo(db, s)
        if link:
            respuesta = f"{respuesta}\n\n{link}".strip()
        if estado == "frio" and "FRIO" not in s["etiquetas"]:
            s["etiquetas"].append("FRIO")
        s["estado"] = estado if estado in ("frio", "calificando") else "calificando"

    elif accion == "escalar" or estado in ("calificado", "agendar") or s["score"] >= UMBRAL_CALIFICADO:
        if "CALIFICADO" not in s["etiquetas"]:
            s["etiquetas"].append("CALIFICADO")
        s["estado"] = "calificado"
        if not s.get("escalado"):
            aliado = _asignar_aliado(db, s.get("rubro"), pais)
            s["aliado_id"] = getattr(aliado, "id", None)
            _escalar_a_humano(db, s, aliado)
            s["escalado"] = True
        if AGENDA_URL:
            respuesta = f"{respuesta}\n\n📅 Si querés, agendá acá un horario: {AGENDA_URL}".strip()
    else:
        s["estado"] = "calificando"

    s["historial"].append({"role": "assistant", "content": respuesta})
    _guardar_sesion(db, s)
    return respuesta or "Dale, contame un poco más y te oriento."


def _link_para_embudo(db, s: dict) -> Optional[str]:
    """Arma un short link de marca con tracking hacia la landing del embudo/rubro."""
    embudo = s.get("embudo")
    destino = None
    if embudo and embudo in FUNNELS:
        destino = FUNNELS[embudo]["landing"]
    elif s.get("rubro"):
        for f in FUNNELS.values():
            if f.get("rubro") == s["rubro"]:
                destino = f["landing"]; break
    destino = destino or f"{PORTAL_URL}/comenzar.html"
    ref = None
    try:
        from models import Aliado  # type: ignore
        if s.get("aliado_id"):
            a = db.query(Aliado).get(s["aliado_id"])
            ref = getattr(a, "ref_code", None)
    except Exception:
        pass
    return crear_enlace(db, destino, ref_code=ref, aliado_id=s.get("aliado_id"))


def _fallback_siguiente_pregunta(datos: dict) -> str:
    """Sin IA: pregunta por el primer campo que falte."""
    if not datos.get("rubro"):
        return "Contame, ¿a qué rubro se dedica tu empresa?"
    if not datos.get("dolor"):
        return "¿Qué es lo que más te gustaría resolver hoy? (más clientes, cotizar más rápido, seguimiento…)"
    if not datos.get("urgencia"):
        return "¿Es algo que querés encarar ya o estás todavía mirando opciones?"
    return "Genial. Un especialista te va a contactar para armarte una propuesta a medida. 🙌"


# ═══════════════════════════════════════════════════════════════════════════════
# ESCALADO AL HUMANO (handoff con briefing)
# ═══════════════════════════════════════════════════════════════════════════════

def _escalar_a_humano(db, s: dict, aliado) -> None:
    """Notifica al aliado asignado (o al número fallback) con el briefing del lead."""
    d = s.get("datos", {})
    briefing = (
        "🔥 *Lead caliente de JARVIS Setter*\n\n"
        f"👤 {s.get('nombre') or d.get('nombre') or 'Sin nombre'}\n"
        f"📱 {s.get('numero')}\n"
        f"🏭 Rubro: {d.get('rubro') or s.get('rubro') or '—'}\n"
        f"🏢 Empresa: {d.get('empresa') or '—'}\n"
        f"📏 Tamaño: {d.get('tamano') or '—'}\n"
        f"⏱️ Urgencia: {d.get('urgencia') or '—'}\n"
        f"🎯 Dolor: {d.get('dolor') or '—'}\n"
        f"⭐ Score: {s.get('score', 0)}/100\n"
        f"🏷️ {', '.join(s.get('etiquetas', [])) or '—'}\n\n"
        "_JARVIS ya lo precalificó. Pasá a cerrar._"
    )
    numero_destino = None
    if aliado is not None:
        numero_destino = getattr(aliado, "whatsapp_numero", None) or getattr(aliado, "whatsapp", None)
    numero_destino = numero_destino or HANDOFF_NUMERO
    if not numero_destino:
        print("[SETTER] Lead calificado pero sin destino de handoff configurado.", file=sys.stderr)
        return
    try:
        import jarvis_whatsapp  # type: ignore
        jarvis_whatsapp.enviar_whatsapp(numero_destino, briefing)
    except Exception as e:
        print(f"[SETTER] _escalar_a_humano: {e}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
# SECUENCIAS POR INACTIVIDAD (scheduler)  — frente (c)/(d)
# ═══════════════════════════════════════════════════════════════════════════════

_SEGUIMIENTOS = [
    (timedelta(hours=4),  "¿Seguís por ahí? 😊 Si querés retomamos cuando te quede cómodo, sin apuro."),
    (timedelta(days=1),   "Te dejo un recurso por si te sirve mientras tanto 👇 Cualquier duda, escribime."),
    (timedelta(days=3),   "Última que te escribo para no ser pesado 🙌 Si más adelante querés ver cómo automatizar tu captación, acá estoy."),
]


def job_seguimientos(SessionLocal=None) -> None:
    """
    Job del scheduler: reactiva prospectos que quedaron en 'calificando' y sin
    respuesta. Máximo 3 toques. Llamar cada ~2h.
    """
    if not is_enabled():
        return
    db = None
    try:
        if SessionLocal is None:
            from database import SessionLocal as _SL  # type: ignore
            SessionLocal = _SL
        db = SessionLocal()
        ahora = datetime.utcnow()
        filas = db.execute(text(
            "SELECT numero, seguimiento_paso, ultimo_inbound, embudo, rubro, estado "
            "FROM setter_sesiones WHERE estado = 'calificando' AND escalado = FALSE "
            "AND seguimiento_paso < :max"
        ), {"max": len(_SEGUIMIENTOS)}).fetchall()

        import jarvis_whatsapp  # type: ignore
        for numero, paso, ultimo, embudo, rubro, _estado in filas:
            paso = paso or 0
            if paso >= len(_SEGUIMIENTOS):
                continue
            espera, mensaje = _SEGUIMIENTOS[paso]
            ult = ultimo if isinstance(ultimo, datetime) else _parse_ts(ultimo)
            if not ult or (ahora - ult) < espera:
                continue
            # Si el toque incluye recurso, sumar short link del embudo
            envio = mensaje
            if paso == 1:
                s = {"numero": numero, "embudo": embudo, "rubro": rubro, "aliado_id": None}
                link = _link_para_embudo(db, s)
                if link:
                    envio = f"{mensaje}\n{link}"
            jarvis_whatsapp.enviar_whatsapp(numero, envio)
            db.execute(text("UPDATE setter_sesiones SET seguimiento_paso = :p, "
                            "ultimo_outbound = :a WHERE numero = :n"),
                       {"p": paso + 1, "a": ahora, "n": numero})
        db.commit()
    except Exception as e:
        if db:
            db.rollback()
        print(f"[SETTER] job_seguimientos: {e}", file=sys.stderr)
    finally:
        if db:
            db.close()


def _parse_ts(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "").split(".")[0])
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS FASTAPI — register()
# ═══════════════════════════════════════════════════════════════════════════════

def register(app, get_db_func, auth_dep):
    """
    Registra endpoints del setter:
      GET  /l/{slug}                      → redirect público con tracking (sin auth)
      GET  /jarvis/setter/pipeline        → leads del setter (auth aliado/admin)
      POST /jarvis/setter/enlace          → crear short link de marca
      GET  /jarvis/setter/embudos         → ver embudos disponibles
    """
    from fastapi import Depends, HTTPException, Request
    from fastapi.responses import RedirectResponse
    from sqlalchemy.orm import Session
    from pydantic import BaseModel

    # ── Redirect público con tracking ────────────────────────────────────────
    @app.get("/l/{slug}")
    def ep_short_link(slug: str, db: Session = Depends(get_db_func)):
        try:
            row = db.execute(text("SELECT destino FROM setter_enlaces WHERE slug = :s"),
                             {"s": slug}).fetchone()
            if not row:
                return RedirectResponse(url=f"{PORTAL_URL}/", status_code=302)
            db.execute(text("UPDATE setter_enlaces SET clicks = clicks + 1 WHERE slug = :s"),
                       {"s": slug})
            db.commit()
            return RedirectResponse(url=row[0], status_code=302)
        except Exception as e:
            print(f"[SETTER] short link {slug}: {e}", file=sys.stderr)
            return RedirectResponse(url=f"{PORTAL_URL}/", status_code=302)

    # ── Pipeline del setter ───────────────────────────────────────────────────
    @app.get("/jarvis/setter/pipeline", tags=["JARVIS Setter"])
    def ep_pipeline(db: Session = Depends(get_db_func), aliado=Depends(auth_dep)):
        try:
            filas = db.execute(text(
                "SELECT numero, nombre, embudo, rubro, estado, score, etiquetas, "
                "escalado, aliado_id, creado_en FROM setter_sesiones "
                "ORDER BY score DESC, creado_en DESC LIMIT 200"
            )).fetchall()
        except Exception as e:
            raise HTTPException(500, f"Error leyendo pipeline: {e}")
        out = []
        for f in filas:
            out.append({
                "numero": f[0], "nombre": f[1], "embudo": f[2], "rubro": f[3],
                "estado": f[4], "score": f[5], "etiquetas": _safe_json(f[6], []),
                "escalado": bool(f[7]), "aliado_id": f[8], "creado_en": str(f[9]),
            })
        return {"ok": True, "total": len(out), "leads": out}

    # ── Crear short link ──────────────────────────────────────────────────────
    class EnlaceReq(BaseModel):
        destino: str
        slug: Optional[str] = None

    @app.post("/jarvis/setter/enlace", tags=["JARVIS Setter"])
    def ep_crear_enlace(body: EnlaceReq, db: Session = Depends(get_db_func),
                        aliado=Depends(auth_dep)):
        if not body.destino.startswith("http"):
            raise HTTPException(400, "El destino debe ser una URL completa (https://...).")
        ref = getattr(aliado, "ref_code", None)
        url = crear_enlace(db, body.destino, ref_code=ref,
                           aliado_id=getattr(aliado, "id", None), slug=body.slug)
        return {"ok": True, "url": url, "destino": body.destino}

    # ── Ver embudos ─────────────────────────────────────────────────────────--
    @app.get("/jarvis/setter/embudos", tags=["JARVIS Setter"])
    def ep_embudos(aliado=Depends(auth_dep)):
        return {"ok": True, "embudos": {
            k: {"rubro": v["rubro"], "etiqueta": v["etiqueta"], "landing": v["landing"]}
            for k, v in FUNNELS.items()
        }, "palabras_clave": list(FUNNELS.keys())}
"""
jarvis_routes.py — Endpoints de JARVIS para el portal de Avanza Digital.

CÓMO AGREGAR AL main.py:
    import jarvis_routes
    jarvis_routes.register(app, get_db, current_aliado_required)

ENDPOINTS:
    POST /jarvis/chat                     → Módulo 1: Conversación con JARVIS
    POST /jarvis/lead/{lead_id}/analizar  → Módulo 2: Análisis completo de lead de bolsa
    POST /jarvis/prospecto/{id}/analizar  → Módulo 2: Perfilado mejorado de prospecto
    POST /jarvis/propuesta                → Módulo 3: Generador de propuesta
    POST /jarvis/followup/{id}            → Módulo 4: Follow-up mejorado
    POST /jarvis/objecion/{id}            → Módulo 4: Respuesta a objeción mejorada
    GET  /jarvis/estado                   → ¿JARVIS está activo? (health check)
"""

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
import json

import jarvis


# ─── SCHEMAS ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    mensaje: str
    historial: Optional[list[dict]] = None


class PropuestaRequest(BaseModel):
    empresa_cliente: str
    rubro: str
    nombre_contacto: Optional[str] = ""
    plan: Optional[str] = "Plan Pro"
    dolores_detectados: Optional[str] = ""
    nota: Optional[str] = ""


# ─── HELPER: extraer datos del aliado autenticado ────────────────────────────

def _aliado_context(aliado_obj) -> dict:
    """Extrae del objeto Aliado los campos que JARVIS necesita."""
    rubros = []
    try:
        rubros_raw = getattr(aliado_obj, "rubros_especialidad", "[]") or "[]"
        rubros = json.loads(rubros_raw) if isinstance(rubros_raw, str) else rubros_raw
    except Exception:
        pass

    ventas_list = getattr(aliado_obj, "ventas", []) or []
    ventas_confirmadas = sum(1 for v in ventas_list if getattr(v, "confirmada", False))

    return {
        "aliado_nombre":  getattr(aliado_obj, "nombre", "") or "",
        "aliado_ciudad":  getattr(aliado_obj, "ciudad", "") or "",
        "aliado_pais":    getattr(aliado_obj, "pais", "AR") or "AR",
        "aliado_rubros":  rubros,
        "aliado_nivel":   getattr(aliado_obj, "nivel", "BASIC") or "BASIC",
        "aliado_ventas":  ventas_confirmadas,
        "aliado_perfil":  getattr(aliado_obj, "perfil", "") or "",
    }


# ─── REGISTER: inyecta las rutas en la app FastAPI ───────────────────────────

def register(app, get_db_func, auth_dep):
    """
    Llamar desde main.py:
        import jarvis_routes
        jarvis_routes.register(app, get_db, current_aliado_required)
    """

    # ── Health check ─────────────────────────────────────────────────────────
    @app.get("/jarvis/estado")
    def jarvis_estado():
        """Verifica si JARVIS (Claude) está configurado y activo."""
        return {
            "activo": jarvis.is_enabled(),
            "modelo": jarvis.JARVIS_MODEL if jarvis.is_enabled() else None,
            "mensaje": "JARVIS operativo 🟢" if jarvis.is_enabled() else "ANTHROPIC_API_KEY no configurada 🔴",
        }

    # ── Módulo 1: Chat con JARVIS ─────────────────────────────────────────────
    @app.post("/jarvis/chat")
    def jarvis_chat(
        body: ChatRequest,
        request: Request,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Conversación contextual con JARVIS.
        El aliado manda un mensaje y JARVIS responde con contexto de su perfil.
        """
        if not body.mensaje.strip():
            raise HTTPException(400, "El mensaje no puede estar vacío.")

        ctx = _aliado_context(aliado)

        resultado = jarvis.chat_jarvis(
            mensaje_aliado=body.mensaje,
            historial=body.historial,
            **ctx,
        )

        if resultado:
            return {
                "modo": "jarvis",
                "respuesta": resultado.get("respuesta", ""),
                "confianza": resultado.get("confianza", "general"),
                "accion_sugerida": resultado.get("accion_sugerida"),
            }

        # Fallback si Claude no está disponible
        return {
            "modo": "fallback",
            "respuesta": "JARVIS no está disponible en este momento. Revisá la configuración de la API key.",
            "confianza": "general",
            "accion_sugerida": None,
        }

    # ── Módulo 2: Análisis de lead de bolsa ──────────────────────────────────
    @app.post("/jarvis/lead/{lead_id}/analizar")
    def jarvis_analizar_lead(
        lead_id: int,
        request: Request,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Análisis completo JARVIS de un lead de la bolsa.
        Devuelve score, perfil del comprador, script WhatsApp y objeciones.
        """
        from models import LeadBolsa

        lead = db.query(LeadBolsa).filter(LeadBolsa.id == lead_id).first()
        if not lead:
            raise HTTPException(404, "Lead no encontrado.")

        ctx = _aliado_context(aliado)

        resultado = jarvis.analizar_lead_bolsa(
            empresa=lead.empresa,
            rubro=lead.rubro or "",
            ciudad=lead.ciudad or "",
            pais=lead.pais or "AR",
            nombre_contacto=lead.nombre_contacto or "",
            tiene_web=bool(lead.tiene_web),
            tiene_redes=bool(lead.tiene_redes),
            web=lead.web or "",
            observacion=lead.observacion or "",
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
            aliado_pais=ctx["aliado_pais"],
            aliado_rubros=ctx["aliado_rubros"],
            aliado_ventas=ctx["aliado_ventas"],
        )

        if resultado:
            return {"modo": "jarvis", **resultado}

        # Fallback al score existente del lead
        return {
            "modo": "fallback",
            "score": lead.score_calidad or 50,
            "temperatura": "tibio",
            "plan_recomendado": "Plan Pro",
            "ticket_esperado": 2900.0,
            "razon": "Análisis heurístico — JARVIS no disponible.",
            "perfil_comprador": "Decisor típico del sector.",
            "script_whatsapp": f"Hola, te contacto de Avanza Digital. Trabajamos con empresas del sector {lead.rubro or 'industrial'} y me gustaría mostrarte cómo podemos ayudar a {lead.empresa}. ¿Tenés 15 minutos esta semana?",
            "objeciones": [],
            "canal_recomendado": "WhatsApp",
            "momento_optimo": "Martes o miércoles entre 9 y 11am",
            "proxima_accion": "Enviar mensaje de primer contacto por WhatsApp.",
        }

    # ── Módulo 2: Perfilado mejorado de prospecto ─────────────────────────────
    @app.post("/jarvis/prospecto/{prospecto_id}/analizar")
    def jarvis_analizar_prospecto(
        prospecto_id: int,
        request: Request,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Perfilado JARVIS mejorado de un prospecto propio del aliado.
        Devuelve score, plan recomendado y pitch sugerido con más contexto.
        """
        from models import Prospecto

        p = db.query(Prospecto).filter(
            Prospecto.id == prospecto_id,
            Prospecto.aliado_id == aliado.id,
        ).first()
        if not p:
            raise HTTPException(404, "Prospecto no encontrado.")

        ctx = _aliado_context(aliado)

        resultado = jarvis.perfilar_prospecto(
            empresa=p.nombre,
            rubro=p.rubro or "",
            tamano=p.tamano or "pyme",
            urgencia=p.urgencia or "media",
            estado=p.estado or "sin_contactar",
            nota_aliado=p.nota or "",
            aliado_nombre=ctx["aliado_nombre"],
            aliado_rubros=ctx["aliado_rubros"],
        )

        if resultado:
            # Actualizar el prospecto en DB con los datos de JARVIS
            from datetime import datetime
            p.score_ia = resultado["score"]
            p.plan_recomendado = resultado["plan_recomendado"]
            p.pitch_sugerido = resultado["pitch_sugerido"]
            p.perfilado_en = datetime.now()
            db.commit()

            return {
                "modo": "jarvis",
                "score": resultado["score"],
                "plan_recomendado": resultado["plan_recomendado"],
                "pitch_sugerido": resultado["pitch_sugerido"],
                "ticket_esperado": resultado["ticket_esperado"],
                "razon": resultado["razon"],
            }

        raise HTTPException(503, "JARVIS no disponible. Intentá de nuevo en unos segundos.")

    # ── Módulo 3: Generador de propuesta ──────────────────────────────────────
    @app.post("/jarvis/propuesta")
    def jarvis_generar_propuesta(
        body: PropuestaRequest,
        request: Request,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Genera una propuesta comercial personalizada lista para enviar.
        """
        ctx = _aliado_context(aliado)

        resultado = jarvis.generar_propuesta(
            empresa_cliente=body.empresa_cliente,
            rubro=body.rubro,
            nombre_contacto=body.nombre_contacto or "",
            plan=body.plan or "Plan Pro",
            dolores_detectados=body.dolores_detectados or "",
            nota_aliado=body.nota or "",
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
        )

        if resultado:
            return {"modo": "jarvis", **resultado}

        raise HTTPException(503, "JARVIS no disponible. Intentá de nuevo.")

    # ── Módulo 4: Follow-up mejorado ──────────────────────────────────────────
    @app.post("/jarvis/followup/{prospecto_id}")
    def jarvis_followup(
        prospecto_id: int,
        tono: str = "directo",
        request: Request = None,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Genera un follow-up de mayor calidad que el endpoint existente.
        Tono: amigable | directo | ultimo | valor
        """
        from models import Prospecto
        from datetime import datetime

        p = db.query(Prospecto).filter(
            Prospecto.id == prospecto_id,
            Prospecto.aliado_id == aliado.id,
        ).first()
        if not p:
            raise HTTPException(404, "Prospecto no encontrado.")

        dias = None
        if p.fecha_contacto:
            dias = (datetime.now() - p.fecha_contacto).days

        ctx = _aliado_context(aliado)
        tono_valido = tono if tono in ("amigable", "directo", "ultimo", "valor") else "directo"

        resultado = jarvis.generar_followup(
            prospecto_nombre=p.nombre,
            rubro=p.rubro or "",
            tamano=p.tamano or "pyme",
            plan_recomendado=p.plan_recomendado or p.plan_interes or "",
            dias_sin_responder=dias,
            ultima_nota=p.nota or "",
            aliado_nombre=ctx["aliado_nombre"],
            tono=tono_valido,
        )

        if resultado:
            return {
                "modo": "jarvis",
                "mensaje": resultado.get("mensaje", ""),
                "estrategia": resultado.get("estrategia", ""),
                "tono": tono_valido,
                "dias_sin_responder": dias,
            }

        raise HTTPException(503, "JARVIS no disponible. Intentá de nuevo.")

    # ── Módulo 4: Respuesta a objeción mejorada ───────────────────────────────
    @app.post("/jarvis/objecion/{prospecto_id}")
    def jarvis_objecion(
        prospecto_id: int,
        objecion: str = "",
        request: Request = None,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Genera una respuesta a una objeción con mejor calidad que el endpoint existente.
        """
        from models import Prospecto, PLANES

        p = db.query(Prospecto).filter(
            Prospecto.id == prospecto_id,
            Prospecto.aliado_id == aliado.id,
        ).first()
        if not p:
            raise HTTPException(404, "Prospecto no encontrado.")

        obj_text = (objecion or "").strip()
        if not obj_text:
            raise HTTPException(400, "Falta el texto de la objeción (?objecion=...)")

        ticket = None
        if p.plan_recomendado and p.plan_recomendado in PLANES:
            ticket = float(PLANES[p.plan_recomendado])

        resultado = jarvis.responder_objecion(
            objecion=obj_text,
            prospecto_nombre=p.nombre,
            rubro=p.rubro or "",
            tamano=p.tamano or "pyme",
            plan_recomendado=p.plan_recomendado or p.plan_interes or "",
            ticket_esperado=ticket,
        )

        if resultado:
            return {
                "modo": "jarvis",
                "respuesta": resultado.get("respuesta", ""),
                "tipo_objecion": resultado.get("tipo_objecion", "otro"),
                "tecnica": resultado.get("tecnica", ""),
            }

        raise HTTPException(503, "JARVIS no disponible. Intentá de nuevo.")
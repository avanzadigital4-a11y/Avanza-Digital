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
from datetime import datetime
import json

import jarvis


# ─── COSTOS POR ACCIÓN (en créditos) — ver plan de monetización, sección 4 ────
COSTO_CHAT          = 1
COSTO_ANALISIS_LEAD = 5
COSTO_PROSPECTO     = 5
COSTO_PROPUESTA     = 12
COSTO_FOLLOWUP      = 2
COSTO_OBJECION      = 2


# ─── SCHEMAS ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    mensaje: str
    historial: Optional[list[dict]] = None
    # Campos del Centro de Comando v2 (opcionales para compatibilidad con versión anterior)
    estado_emocional: Optional[str] = "neutro"
    lead_activo: Optional[dict] = None

    class Config:
        extra = "allow"  # ignorar campos desconocidos sin lanzar 422


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

def register(app, get_db_func, auth_dep, ajustar_creditos_fn=None):
    """
    Llamar desde main.py:
        import jarvis_routes
        jarvis_routes.register(app, get_db, current_aliado_required, _ajustar_creditos)

    ajustar_creditos_fn: función de main.py con firma
        (db, aliado, delta: int, motivo: str, ref: str) -> None
    Es ATÓMICA (UPDATE ... WHERE creditos+delta>=0) y LANZA HTTPException(400)
    si el saldo no alcanza; NO hace commit (lo hace este módulo).
    Si es None, JARVIS responde sin cobrar (modo legacy/desarrollo).
    """

    # ── Gate de acceso: 7 días gratis, luego pago por créditos ───────────────
    def _en_trial(aliado) -> bool:
        """True si el aliado está dentro de su ventana de prueba gratis de JARVIS.

        Durante el trial JARVIS es gratis: no se cobra ni se bloquea. Así los
        créditos del aliado quedan intactos para la bolsa de leads esa semana.
        """
        fin = getattr(aliado, "jarvis_trial_fin", None)
        if not fin:
            return False
        try:
            return datetime.now() <= fin
        except Exception:
            return False

    def _verificar_acceso(aliado, costo: int):
        """402 si no hay créditos suficientes. Sin créditos = sin acceso.

        Excepción: durante los 7 días de prueba gratis (jarvis_trial_fin en el
        futuro) el acceso es libre y no se mira el saldo. Terminado el trial, lo
        que habilita el uso es haber pagado (tener créditos): el gate es por saldo.
        """
        if _en_trial(aliado):
            return  # prueba gratis activa → acceso libre
        if ajustar_creditos_fn is not None and costo > 0:
            saldo = getattr(aliado, "creditos", 0) or 0
            if saldo < costo:
                raise HTTPException(
                    402,
                    f"Créditos insuficientes: esta acción cuesta {costo} y tenés {saldo}.",
                )

    def _cobrar(db, aliado, costo: int, motivo: str) -> bool:
        """
        Descuenta `costo` créditos DESPUÉS de una respuesta exitosa de JARVIS.
        Durante la prueba gratis NO descuenta (los créditos quedan para la bolsa).
        Sólo se invoca cuando hubo resultado real → nunca cobra acciones
        fallidas (ese era el riesgo del esquema 'cobrar-antes-de-llamar').
        Ante una carrera de saldo no rompe la respuesta ya entregada: loguea y
        devuelve False. Devuelve True si efectivamente cobró.
        """
        if _en_trial(aliado):
            return False  # en prueba gratis no se cobra
        if ajustar_creditos_fn is None or costo <= 0:
            return False
        try:
            ajustar_creditos_fn(db, aliado, -costo, motivo, "jarvis")
            db.commit()
            return True
        except HTTPException:
            db.rollback()
            print(f"[JARVIS] No se pudo cobrar {costo}cr a aliado "
                  f"{getattr(aliado, 'id', '?')} ({motivo}): saldo cambió en carrera. "
                  f"Respuesta entregada igual.")
            return False
        except Exception as e:
            db.rollback()
            print(f"[JARVIS] Error cobrando créditos ({motivo}): {e}")
            return False

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

        _verificar_acceso(aliado, COSTO_CHAT)

        ctx = _aliado_context(aliado)

        # Ajuste de tono según estado emocional detectado en el frontend
        ajuste_emocional = ""
        if body.estado_emocional and body.estado_emocional != "neutro":
            from jarvis_emocional import JarvisEmocional, ajustar_tono_respuesta, EstadoAliado
            estado_obj = EstadoAliado(estado=body.estado_emocional)
            ajuste_emocional = ajustar_tono_respuesta(estado_obj)

        # Contexto del lead activo (si el panel derecho tiene uno seleccionado)
        contexto_lead = ""
        if body.lead_activo:
            lead = body.lead_activo
            contexto_lead = (
                f"\n\nLEAD ACTIVO EN EL PANEL: {lead.get('nombre','')} — "
                f"{lead.get('contacto','')} ({lead.get('cargo','')}) — "
                f"Sector: {lead.get('sector','')} — Score: {lead.get('score','')}/100"
            )

        resultado = jarvis.chat_jarvis(
            mensaje_aliado=body.mensaje + contexto_lead,
            historial=body.historial,
            ajuste_emocional=ajuste_emocional,
            **ctx,
        )

        if resultado:
            _cobrar(db, aliado, COSTO_CHAT, "jarvis_chat")
            return {
                "modo": "jarvis",
                "respuesta": resultado.get("respuesta", ""),
                "contexto": resultado.get("confianza", "general"),
                "confianza": resultado.get("confianza", "general"),
                "accion_sugerida": resultado.get("accion_sugerida"),
                "tiempo_ms": resultado.get("tiempo_ms"),
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

        _verificar_acceso(aliado, COSTO_ANALISIS_LEAD)

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
            _cobrar(db, aliado, COSTO_ANALISIS_LEAD, "jarvis_lead")
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

        _verificar_acceso(aliado, COSTO_PROSPECTO)

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

            # Cobro en transacción aparte: si el saldo cambió en carrera, el
            # análisis ya quedó guardado igual (no se pierde por el rollback).
            _cobrar(db, aliado, COSTO_PROSPECTO, "jarvis_prospecto")
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
        _verificar_acceso(aliado, COSTO_PROPUESTA)

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
            _cobrar(db, aliado, COSTO_PROPUESTA, "jarvis_propuesta")
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

        _verificar_acceso(aliado, COSTO_FOLLOWUP)

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
            _cobrar(db, aliado, COSTO_FOLLOWUP, "jarvis_followup")
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

        _verificar_acceso(aliado, COSTO_OBJECION)

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
            _cobrar(db, aliado, COSTO_OBJECION, "jarvis_objecion")
            return {
                "modo": "jarvis",
                "respuesta": resultado.get("respuesta", ""),
                "tipo_objecion": resultado.get("tipo_objecion", "otro"),
                "tecnica": resultado.get("tecnica", ""),
            }

        raise HTTPException(503, "JARVIS no disponible. Intentá de nuevo.")
"""
jarvis_api_publica.py — JARVIS API pública v1.

API REST autenticada por API key para que aliados white-label integren
JARVIS en sus propias apps, plugins de CRM o sitios web.

CÓMO REGISTRAR EN main.py:
    import jarvis_api_publica
    jarvis_api_publica.register(app, get_db, current_aliado_required)

ENDPOINTS PÚBLICOS (autenticados con X-JARVIS-API-Key):
    GET  /api/v1/estado                     → Health check (sin auth)
    POST /api/v1/chat                        → Módulo 1: Cerebro Comercial
    POST /api/v1/lead/analizar               → Módulo 2: Motor de Leads
    POST /api/v1/propuesta                   → Módulo 3: Generador de Propuestas
    POST /api/v1/comunicacion/followup       → Módulo 4: Follow-up
    POST /api/v1/comunicacion/objecion       → Módulo 4: Respuesta a objeción
    POST /api/v1/mercado/analizar            → Módulo 5: Analista de Mercado

ENDPOINTS DE GESTIÓN (autenticados con sesión de portal):
    GET  /api/v1/keys                        → Listar mis API keys
    POST /api/v1/keys                        → Generar nueva API key
    DELETE /api/v1/keys/{key_id}             → Revocar API key

AUTENTICACIÓN:
    Header: X-JARVIS-API-Key: jrv_live_xxxxxxxxxxxxxxxx

RATE LIMITING por plan:
    Starter   → 200 req/día
    Pro       → 1.000 req/día
    WhiteLabel → 5.000 req/día

FORMATO DE RESPUESTA:
    {
        "ok": true,
        "data": { ... },
        "uso": { "hoy": 12, "limite_dia": 200, "plan": "starter" }
    }
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
from datetime import date, datetime
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Column, Boolean, Date, DateTime, Integer, String, ForeignKey, text
from sqlalchemy.orm import Session, relationship

import jarvis

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────

LIMITE_DIARIO = {
    "starter":     200,
    "pro":        1000,
    "white_label": 5000,
}

API_KEY_PREFIX = "jrv_live_"  # Prefijo visible: identifica claves de producción


# ─── MODELOS DE DB ────────────────────────────────────────────────────────────

def _crear_tabla_api_keys(engine):
    """Crea la tabla jarvis_api_keys si no existe. Idempotente."""
    sql = """
    CREATE TABLE IF NOT EXISTS jarvis_api_keys (
        id                  SERIAL PRIMARY KEY,
        aliado_id           INTEGER REFERENCES aliados(id) ON DELETE CASCADE,
        key_prefix          VARCHAR(20)  NOT NULL,
        key_hash            VARCHAR(64)  NOT NULL UNIQUE,
        nombre              VARCHAR(100) DEFAULT 'Mi API Key',
        activa              BOOLEAN      DEFAULT TRUE,
        plan_tier           VARCHAR(20)  DEFAULT 'starter',
        requests_hoy        INTEGER      DEFAULT 0,
        requests_mes        INTEGER      DEFAULT 0,
        limite_diario       INTEGER      DEFAULT 200,
        ultimo_reset_diario DATE,
        ultima_peticion     TIMESTAMP,
        creada_en           TIMESTAMP    DEFAULT NOW()
    )
    """
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
    except Exception as e:
        print(f"[JARVIS API] Error creando tabla jarvis_api_keys: {e}", file=sys.stderr)


# ─── HELPERS: API KEY ─────────────────────────────────────────────────────────

def _generar_api_key() -> tuple[str, str, str]:
    """
    Genera una API key nueva.
    Retorna: (key_completa, prefijo_visible, hash_sha256)
    """
    raw = secrets.token_urlsafe(32)
    key_completa = f"{API_KEY_PREFIX}{raw}"
    prefijo = key_completa[:20]                               # "jrv_live_XXXXXXXXXX"
    key_hash = hashlib.sha256(key_completa.encode()).hexdigest()
    return key_completa, prefijo, key_hash


def _hashear(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _obtener_key_row(db: Session, api_key: str):
    """Busca la API key en la DB. Retorna el row o None."""
    h = _hashear(api_key)
    result = db.execute(
        text("SELECT * FROM jarvis_api_keys WHERE key_hash = :h AND activa = TRUE"),
        {"h": h}
    ).fetchone()
    return result


def _verificar_y_registrar(db: Session, api_key: str) -> dict:
    """
    Autentica la API key y registra el uso.
    Retorna el contexto del aliado + info de uso.
    Lanza HTTPException 401/429 si hay problema.
    """
    if not api_key:
        raise HTTPException(401, "API key requerida. Header: X-JARVIS-API-Key")

    row = _obtener_key_row(db, api_key)
    if not row:
        raise HTTPException(401, "API key inválida o revocada.")

    # Reset diario si corresponde
    hoy = date.today()
    ultimo_reset = row.ultimo_reset_diario
    requests_hoy = row.requests_hoy

    if ultimo_reset != hoy:
        db.execute(
            text("""
                UPDATE jarvis_api_keys
                SET requests_hoy = 0, ultimo_reset_diario = :hoy
                WHERE id = :id
            """),
            {"hoy": hoy, "id": row.id}
        )
        db.commit()
        requests_hoy = 0

    # Verificar rate limit
    limite = row.limite_diario or LIMITE_DIARIO.get(row.plan_tier, 200)
    if requests_hoy >= limite:
        raise HTTPException(
            429,
            f"Límite diario alcanzado ({limite} requests). "
            f"Resetea a las 00:00 UTC. Plan actual: {row.plan_tier}."
        )

    # Registrar uso
    db.execute(
        text("""
            UPDATE jarvis_api_keys
            SET requests_hoy = requests_hoy + 1,
                requests_mes = requests_mes + 1,
                ultima_peticion = NOW()
            WHERE id = :id
        """),
        {"id": row.id}
    )
    db.commit()

    # Obtener datos del aliado
    aliado = db.execute(
        text("SELECT nombre, ciudad, pais, rubros_especialidad, nivel FROM aliados WHERE id = :id"),
        {"id": row.aliado_id}
    ).fetchone()

    return {
        "aliado_id":    row.aliado_id,
        "aliado_nombre": aliado.nombre if aliado else "",
        "aliado_ciudad": aliado.ciudad if aliado else "",
        "aliado_pais":   aliado.pais if aliado else "AR",
        "aliado_rubros": [],
        "aliado_nivel":  aliado.nivel if aliado else "BASIC",
        "aliado_ventas": 0,
        "plan_tier":    row.plan_tier,
        "uso": {
            "hoy":       requests_hoy + 1,
            "limite_dia": limite,
            "plan":      row.plan_tier,
        }
    }


# ─── SCHEMAS DE REQUEST ───────────────────────────────────────────────────────

class ApiChatRequest(BaseModel):
    mensaje: str
    historial: Optional[list[dict]] = None
    contexto_white_label: Optional[dict] = None  # Para WL: datos del vendedor final

class ApiLeadRequest(BaseModel):
    empresa: str
    rubro: str
    ciudad: Optional[str] = ""
    pais: Optional[str] = "AR"
    nombre_contacto: Optional[str] = ""
    tiene_web: Optional[bool] = False
    tiene_redes: Optional[bool] = False
    web: Optional[str] = ""
    observacion: Optional[str] = ""

class ApiPropuestaRequest(BaseModel):
    empresa_cliente: str
    rubro: str
    nombre_contacto: Optional[str] = ""
    plan: Optional[str] = "Plan Pro"
    dolores_detectados: Optional[str] = ""
    nota: Optional[str] = ""

class ApiFollowupRequest(BaseModel):
    prospecto_nombre: str
    rubro: Optional[str] = ""
    tamano: Optional[str] = "pyme"
    plan_recomendado: Optional[str] = ""
    dias_sin_responder: Optional[int] = None
    ultima_nota: Optional[str] = ""
    tono: Optional[str] = "directo"  # amigable | directo | ultimo | valor

class ApiObjecionRequest(BaseModel):
    objecion: str
    prospecto_nombre: Optional[str] = ""
    rubro: Optional[str] = ""
    plan_recomendado: Optional[str] = ""

class ApiMercadoRequest(BaseModel):
    pregunta: str
    sector: Optional[str] = ""
    region: Optional[str] = ""

class ApiKeyCreateRequest(BaseModel):
    nombre: Optional[str] = "Mi API Key"
    plan_tier: Optional[str] = "starter"  # starter | pro | white_label


# ─── RESPUESTA ESTÁNDAR ───────────────────────────────────────────────────────

def _ok(data: dict, uso: dict) -> dict:
    return {"ok": True, "data": data, "uso": uso}

def _error(msg: str, status: int = 400):
    raise HTTPException(status, msg)


# ─── REGISTER ─────────────────────────────────────────────────────────────────

def register(app, get_db_func, auth_dep, engine=None):
    """
    Registra todos los endpoints de la API pública en la app FastAPI.

    Llamar desde main.py:
        import jarvis_api_publica
        from database import engine as db_engine
        jarvis_api_publica.register(app, get_db, current_aliado_required, engine=db_engine)
    """

    # Crear tabla al iniciar
    if engine:
        _crear_tabla_api_keys(engine)

    # ── Health check (sin auth) ───────────────────────────────────────────────
    @app.get("/api/v1/estado", tags=["JARVIS API Pública"])
    def api_estado():
        """Estado de la API pública de JARVIS. No requiere autenticación."""
        return {
            "ok": True,
            "version": "1.0",
            "jarvis_activo": jarvis.is_enabled(),
            "modelo": jarvis.JARVIS_MODEL if jarvis.is_enabled() else None,
            "docs": "https://avanzadigital.digital/api/v1/docs",
        }

    # ── Módulo 1: Chat (Cerebro Comercial) ───────────────────────────────────
    @app.post("/api/v1/chat", tags=["JARVIS API Pública"])
    def api_chat(
        body: ApiChatRequest,
        x_jarvis_api_key: str = Header(..., alias="X-JARVIS-API-Key"),
        db: Session = Depends(get_db_func),
    ):
        """
        Conversación con JARVIS con contexto del aliado.
        Útil para integrar el chatbot JARVIS en apps externas.
        """
        ctx = _verificar_y_registrar(db, x_jarvis_api_key)

        # White-label: si el aliado WL pasa contexto de su propio vendedor,
        # lo mezclamos con el perfil del aliado base.
        aliado_nombre = ctx["aliado_nombre"]
        if body.contexto_white_label:
            wl = body.contexto_white_label
            aliado_nombre = wl.get("nombre", aliado_nombre)

        resultado = jarvis.chat_jarvis(
            mensaje_aliado=body.mensaje,
            historial=body.historial,
            aliado_nombre=aliado_nombre,
            aliado_ciudad=ctx["aliado_ciudad"],
            aliado_pais=ctx["aliado_pais"],
            aliado_rubros=ctx["aliado_rubros"],
            aliado_nivel=ctx["aliado_nivel"],
            aliado_ventas=ctx["aliado_ventas"],
            aliado_perfil="",
        )

        if not resultado:
            raise HTTPException(503, "JARVIS no disponible en este momento.")

        return _ok({
            "respuesta":       resultado.get("respuesta", ""),
            "confianza":       resultado.get("confianza", "general"),
            "accion_sugerida": resultado.get("accion_sugerida"),
        }, ctx["uso"])

    # ── Módulo 2: Análisis de lead ────────────────────────────────────────────
    @app.post("/api/v1/lead/analizar", tags=["JARVIS API Pública"])
    def api_analizar_lead(
        body: ApiLeadRequest,
        x_jarvis_api_key: str = Header(..., alias="X-JARVIS-API-Key"),
        db: Session = Depends(get_db_func),
    ):
        """
        Análisis completo de un lead industrial.
        Devuelve score, perfil del comprador, script de primer contacto y objeciones.
        """
        ctx = _verificar_y_registrar(db, x_jarvis_api_key)

        resultado = jarvis.analizar_lead_bolsa(
            empresa=body.empresa,
            rubro=body.rubro,
            ciudad=body.ciudad or "",
            pais=body.pais or "AR",
            nombre_contacto=body.nombre_contacto or "",
            tiene_web=body.tiene_web or False,
            tiene_redes=body.tiene_redes or False,
            web=body.web or "",
            observacion=body.observacion or "",
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
            aliado_pais=ctx["aliado_pais"],
            aliado_rubros=ctx["aliado_rubros"],
            aliado_ventas=ctx["aliado_ventas"],
        )

        if not resultado:
            raise HTTPException(503, "JARVIS no disponible.")

        return _ok(resultado, ctx["uso"])

    # ── Módulo 3: Generador de propuestas ─────────────────────────────────────
    @app.post("/api/v1/propuesta", tags=["JARVIS API Pública"])
    def api_generar_propuesta(
        body: ApiPropuestaRequest,
        x_jarvis_api_key: str = Header(..., alias="X-JARVIS-API-Key"),
        db: Session = Depends(get_db_func),
    ):
        """
        Genera una propuesta comercial lista para enviar.
        """
        ctx = _verificar_y_registrar(db, x_jarvis_api_key)

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

        if not resultado:
            raise HTTPException(503, "JARVIS no disponible.")

        return _ok(resultado, ctx["uso"])

    # ── Módulo 4a: Follow-up ──────────────────────────────────────────────────
    @app.post("/api/v1/comunicacion/followup", tags=["JARVIS API Pública"])
    def api_followup(
        body: ApiFollowupRequest,
        x_jarvis_api_key: str = Header(..., alias="X-JARVIS-API-Key"),
        db: Session = Depends(get_db_func),
    ):
        """
        Genera un mensaje de seguimiento contextualizado.
        Tono: amigable | directo | ultimo | valor
        """
        ctx = _verificar_y_registrar(db, x_jarvis_api_key)

        tono = body.tono if body.tono in ("amigable", "directo", "ultimo", "valor") else "directo"

        resultado = jarvis.generar_followup(
            prospecto_nombre=body.prospecto_nombre,
            rubro=body.rubro or "",
            tamano=body.tamano or "pyme",
            plan_recomendado=body.plan_recomendado or "",
            dias_sin_responder=body.dias_sin_responder,
            ultima_nota=body.ultima_nota or "",
            aliado_nombre=ctx["aliado_nombre"],
            tono=tono,
        )

        if not resultado:
            raise HTTPException(503, "JARVIS no disponible.")

        return _ok({
            "mensaje":    resultado.get("mensaje", ""),
            "estrategia": resultado.get("estrategia", ""),
            "tono":       tono,
        }, ctx["uso"])

    # ── Módulo 4b: Respuesta a objeción ───────────────────────────────────────
    @app.post("/api/v1/comunicacion/objecion", tags=["JARVIS API Pública"])
    def api_objecion(
        body: ApiObjecionRequest,
        x_jarvis_api_key: str = Header(..., alias="X-JARVIS-API-Key"),
        db: Session = Depends(get_db_func),
    ):
        """
        Genera una respuesta táctica a una objeción de venta.
        """
        ctx = _verificar_y_registrar(db, x_jarvis_api_key)

        resultado = jarvis.responder_objecion(
            objecion=body.objecion,
            prospecto_nombre=body.prospecto_nombre or "",
            rubro=body.rubro or "",
            tamano="pyme",
            plan_recomendado=body.plan_recomendado or "",
            ticket_esperado=None,
        )

        if not resultado:
            raise HTTPException(503, "JARVIS no disponible.")

        return _ok({
            "respuesta":     resultado.get("respuesta", ""),
            "tipo_objecion": resultado.get("tipo_objecion", "otro"),
            "tecnica":       resultado.get("tecnica", ""),
        }, ctx["uso"])

    # ── Módulo 5: Analista de Mercado ─────────────────────────────────────────
    @app.post("/api/v1/mercado/analizar", tags=["JARVIS API Pública"])
    def api_mercado(
        body: ApiMercadoRequest,
        x_jarvis_api_key: str = Header(..., alias="X-JARVIS-API-Key"),
        db: Session = Depends(get_db_func),
    ):
        """
        Consulta al analista de mercado de JARVIS.
        Ejemplos: competidores, tendencias de sector, oportunidades geográficas.
        """
        ctx = _verificar_y_registrar(db, x_jarvis_api_key)

        # Usa el chat_jarvis con contexto de mercado
        prompt_mercado = (
            f"[CONSULTA DE MERCADO]\n"
            f"Sector: {body.sector or 'industrial'}\n"
            f"Región: {body.region or 'Argentina'}\n"
            f"Pregunta: {body.pregunta}"
        )

        resultado = jarvis.chat_jarvis(
            mensaje_aliado=prompt_mercado,
            historial=None,
            aliado_nombre=ctx["aliado_nombre"],
            aliado_ciudad=ctx["aliado_ciudad"],
            aliado_pais=ctx["aliado_pais"],
            aliado_rubros=ctx["aliado_rubros"],
            aliado_nivel=ctx["aliado_nivel"],
            aliado_ventas=ctx["aliado_ventas"],
            aliado_perfil="",
        )

        if not resultado:
            raise HTTPException(503, "JARVIS no disponible.")

        return _ok({
            "analisis":  resultado.get("respuesta", ""),
            "confianza": resultado.get("confianza", "general"),
        }, ctx["uso"])

    # ─────────────────────────────────────────────────────────────────────────
    # GESTIÓN DE API KEYS (autenticadas con sesión de portal, no con API key)
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/v1/keys", tags=["JARVIS API Keys"])
    def api_listar_keys(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Lista las API keys del aliado autenticado."""
        rows = db.execute(
            text("""
                SELECT id, key_prefix, nombre, activa, plan_tier,
                       requests_hoy, requests_mes, limite_diario,
                       ultima_peticion, creada_en
                FROM jarvis_api_keys
                WHERE aliado_id = :aid
                ORDER BY creada_en DESC
            """),
            {"aid": aliado.id}
        ).fetchall()

        return {
            "keys": [
                {
                    "id":             r.id,
                    "nombre":         r.nombre,
                    "prefijo":        r.key_prefix,     # "jrv_live_XXXXX" (no el full)
                    "activa":         r.activa,
                    "plan_tier":      r.plan_tier,
                    "requests_hoy":   r.requests_hoy,
                    "requests_mes":   r.requests_mes,
                    "limite_diario":  r.limite_diario,
                    "ultima_peticion": str(r.ultima_peticion) if r.ultima_peticion else None,
                    "creada_en":      str(r.creada_en),
                }
                for r in rows
            ]
        }

    @app.post("/api/v1/keys", tags=["JARVIS API Keys"])
    def api_crear_key(
        body: ApiKeyCreateRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Genera una nueva API key para el aliado.
        La key completa solo se muestra UNA VEZ. Guardala.
        """
        # Verificar límite de keys por aliado (máx. 5)
        count = db.execute(
            text("SELECT COUNT(*) FROM jarvis_api_keys WHERE aliado_id = :aid AND activa = TRUE"),
            {"aid": aliado.id}
        ).scalar()
        if count >= 5:
            raise HTTPException(400, "Límite de 5 API keys activas por aliado.")

        # Validar plan_tier según el plan del aliado
        plan_tier = body.plan_tier or "starter"
        if plan_tier not in LIMITE_DIARIO:
            plan_tier = "starter"

        key_completa, prefijo, key_hash = _generar_api_key()
        limite = LIMITE_DIARIO[plan_tier]

        db.execute(
            text("""
                INSERT INTO jarvis_api_keys
                    (aliado_id, key_prefix, key_hash, nombre, plan_tier, limite_diario, creada_en)
                VALUES
                    (:aid, :prefix, :hash, :nombre, :tier, :limite, NOW())
            """),
            {
                "aid":    aliado.id,
                "prefix": prefijo,
                "hash":   key_hash,
                "nombre": body.nombre or "Mi API Key",
                "tier":   plan_tier,
                "limite": limite,
            }
        )
        db.commit()

        return {
            "ok": True,
            "api_key": key_completa,   # ⚠️ Solo se muestra una vez
            "prefijo": prefijo,
            "plan_tier": plan_tier,
            "limite_diario": limite,
            "advertencia": "Guardá esta key ahora. No se puede recuperar después.",
            "uso_ejemplo": {
                "curl": (
                    f'curl -X POST https://avanza-digital.onrender.com/api/v1/chat \\\n'
                    f'  -H "X-JARVIS-API-Key: {key_completa}" \\\n'
                    f'  -H "Content-Type: application/json" \\\n'
                    f'  -d \'{{"mensaje": "¿Cómo abordo a una metalúrgica en San Nicolás?"}}\''
                )
            }
        }

    @app.delete("/api/v1/keys/{key_id}", tags=["JARVIS API Keys"])
    def api_revocar_key(
        key_id: int,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Revoca (desactiva) una API key del aliado."""
        result = db.execute(
            text("""
                UPDATE jarvis_api_keys
                SET activa = FALSE
                WHERE id = :id AND aliado_id = :aid
                RETURNING id
            """),
            {"id": key_id, "aid": aliado.id}
        ).fetchone()

        if not result:
            raise HTTPException(404, "API key no encontrada o no pertenece a este aliado.")

        db.commit()
        return {"ok": True, "mensaje": f"API key #{key_id} revocada."}

    @app.get("/api/v1/keys/{key_id}/uso", tags=["JARVIS API Keys"])
    def api_uso_key(
        key_id: int,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """Estadísticas de uso de una API key específica."""
        row = db.execute(
            text("""
                SELECT nombre, plan_tier, requests_hoy, requests_mes,
                       limite_diario, ultima_peticion, creada_en, activa
                FROM jarvis_api_keys
                WHERE id = :id AND aliado_id = :aid
            """),
            {"id": key_id, "aid": aliado.id}
        ).fetchone()

        if not row:
            raise HTTPException(404, "API key no encontrada.")

        return {
            "nombre":          row.nombre,
            "plan_tier":       row.plan_tier,
            "activa":          row.activa,
            "requests_hoy":    row.requests_hoy,
            "requests_mes":    row.requests_mes,
            "limite_diario":   row.limite_diario,
            "porcentaje_usado": round(
                (row.requests_hoy / row.limite_diario) * 100, 1
            ) if row.limite_diario else 0,
            "ultima_peticion": str(row.ultima_peticion) if row.ultima_peticion else None,
            "creada_en":       str(row.creada_en),
        }
"""
jarvis_flywheel.py — Motor de Aprendizaje Colectivo de JARVIS (Sección 6 del Blueprint v2)

El Flywheel es el mecanismo que convierte el uso acumulado de todos los aliados en
ventaja competitiva compartida. Funciona en dos direcciones:

  ALIMENTAR → cada módulo de JARVIS llama a contribuir_patron() para depositar
              señales anonimizadas (scores, tácticas efectivas, momentos óptimos).

  CONSUMIR  → jarvis_leads, jarvis_memoria y el chat consumen enriquecer_con_flywheel()
              para mejorar sus respuestas con lo que aprendió la red completa.

PRIVACIDAD GARANTIZADA:
  - Nunca se almacena nombre de empresa, contacto ni aliado.
  - Toda contribución pasa por _anonimizar() antes de persistir.
  - El hash SHA-256 sirve solo para deduplicar — no permite reidentificación.

INTEGRACIÓN CON EL RESTO DEL SISTEMA:
  Desde jarvis_leads.py:       flywheel.contribuir_patron(..., tipo="score_lead", ...)
  Desde jarvis_memoria.py:     flywheel.contribuir_patron(..., tipo="cierre", ...)
  Desde jarvis_routes.py:      flywheel.enriquecer_con_flywheel(sector, tipo_patron)
  Desde APScheduler (main.py): flywheel.agregar_insights_flywheel_al_scheduler(scheduler)

ENDPOINTS REGISTRADOS:
  GET  /jarvis/flywheel/stats          → métricas generales del Flywheel
  GET  /jarvis/flywheel/insights       → insights para el aliado autenticado
  POST /jarvis/flywheel/contribuir     → contribución manual (testing / admin)
  GET  /jarvis/flywheel/migration-sql  → SQL para crear la tabla si no existe

TABLA EN BD (ya definida en jarvis_memoria.py → MIGRATION_SQL):
  jarvis_patron_colectivo (
      id, sector, pais, tipo_patron, patron_hash,
      data (JSON), contribuciones, creado_en, actualizado_en
  )
"""

from __future__ import annotations
import os, json, sys, hashlib
from datetime import datetime, timedelta
from typing import Optional, Any

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JARVIS_MODEL      = "claude-sonnet-4-20250514"
JARVIS_TIMEOUT    = 15.0

# Tipos de patrón que el Flywheel rastrea
TIPOS_PATRON = {
    "score_lead":          "Score de lead y resultado posterior (cerrado/perdido/en curso)",
    "tactica_contacto":    "Canal y horario de primer contacto con resultado",
    "objecion_resuelta":   "Objeción recibida + táctica que la resolvió",
    "cierre":              "Señales del lead en el momento de cierre",
    "script_efectivo":     "Script o mensaje con tasa de respuesta medida",
    "ciclo_sector":        "Duración real del ciclo de venta por sector/tamaño",
    "señal_compra":        "Palabras/comportamientos que predicen cierre",
    "señal_fuga":          "Comportamientos que predicen abandono del lead",
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


def _hash_patron(data: dict) -> str:
    """SHA-256 del contenido anonimizado — para deduplicar sin reidentificar."""
    contenido = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(contenido.encode()).hexdigest()


def _anonimizar(raw: dict) -> dict:
    """
    Elimina cualquier campo identificable antes de persistir en el Flywheel.
    Lo que queda son patrones de comportamiento, no datos del aliado o del lead.
    """
    CAMPOS_PROHIBIDOS = {
        "empresa", "empresa_cliente", "empresa_lead", "nombre", "nombre_contacto",
        "email", "telefono", "whatsapp", "aliado_id", "aliado_nombre",
        "aliado_email", "prospecto_id", "lead_id", "contacto",
    }
    return {k: v for k, v in raw.items() if k.lower() not in CAMPOS_PROHIBIDOS}


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


def _chat(prompt: str, system: str, *, max_tokens: int = 800) -> Optional[str]:
    """Llama a Claude para sintetizar insights del Flywheel. Silencia todos los errores."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=JARVIS_MODEL,
            max_tokens=max_tokens,
            system=system + "\n\nIMPORTANTE: Respondé ÚNICAMENTE con JSON válido. Sin markdown.",
            messages=[{"role": "user", "content": prompt}],
            timeout=JARVIS_TIMEOUT,
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"[FLYWHEEL ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# ALIMENTAR EL FLYWHEEL — contribuir_patron()
# ═══════════════════════════════════════════════════════════════════════════════

def contribuir_patron(
    sector: str,
    tipo_patron: str,
    data: dict,
    db_session,
    *,
    pais: str = "AR",
    aliado_id: int = None,   # solo para log local, nunca va a la BD del flywheel
) -> bool:
    """
    Deposita un patrón anonimizado en el Flywheel colectivo.

    Llamar desde cualquier módulo de JARVIS cuando ocurre un evento relevante:

        # Después de analizar un lead:
        flywheel.contribuir_patron(
            sector="metalurgica",
            tipo_patron="score_lead",
            data={
                "score": 78,
                "tamaño_empresa": "50-100",
                "cargo_contacto": "Gerente de Producción",
                "resultado": "en_curso",   # actualizar a "cerrado" / "perdido" después
                "dias_ciclo": None,
            },
            db_session=db,
            pais="AR",
        )

        # Después de un cierre:
        flywheel.contribuir_patron(
            sector="agro",
            tipo_patron="cierre",
            data={
                "plan": "Plan Pro",
                "score_inicial": 72,
                "dias_desde_primer_contacto": 31,
                "canal_primer_contacto": "whatsapp",
                "señales_previas": ["preguntó por implementación", "pidió referencias"],
            },
            db_session=db,
        )

    Retorna True si guardó, False si falló (nunca lanza excepciones).
    """
    if not sector or not tipo_patron:
        return False

    try:
        # Anonimizar SIEMPRE antes de cualquier operación
        data_limpia = _anonimizar(data)
        data_limpia["sector"] = sector
        data_limpia["tipo_patron"] = tipo_patron

        patron_hash = _hash_patron(data_limpia)
        data_str    = json.dumps(data_limpia, ensure_ascii=False)

        try:
            from sqlalchemy import text
            # Upsert: si el hash ya existe, incrementar contribuciones
            # Si no existe, insertar nuevo patrón
            check = db_session.execute(
                text("""
                    SELECT id, contribuciones FROM jarvis_patron_colectivo
                    WHERE patron_hash = :hash AND sector = :sector AND tipo_patron = :tipo
                    LIMIT 1
                """),
                {"hash": patron_hash, "sector": sector, "tipo": tipo_patron},
            ).fetchone()

            if check:
                db_session.execute(
                    text("""
                        UPDATE jarvis_patron_colectivo
                        SET contribuciones = contribuciones + 1,
                            actualizado_en = NOW()
                        WHERE id = :id
                    """),
                    {"id": check[0]},
                )
            else:
                db_session.execute(
                    text("""
                        INSERT INTO jarvis_patron_colectivo
                            (sector, pais, tipo_patron, patron_hash, data, contribuciones,
                             creado_en, actualizado_en)
                        VALUES
                            (:sector, :pais, :tipo, :hash, :data, 1, NOW(), NOW())
                    """),
                    {
                        "sector": sector.lower()[:50],
                        "pais":   pais.upper()[:5],
                        "tipo":   tipo_patron[:50],
                        "hash":   patron_hash,
                        "data":   data_str,
                    },
                )

            db_session.commit()
            return True

        except Exception as db_err:
            # La tabla puede no existir aún — no es error fatal
            msg = str(db_err).lower()
            if "does not exist" in msg or "no such table" in msg:
                print(
                    "[FLYWHEEL] Tabla jarvis_patron_colectivo no existe. "
                    "Ejecutar migración desde jarvis_memoria.get_migration_sql()",
                    file=sys.stderr,
                )
            else:
                print(f"[FLYWHEEL] Error DB al contribuir patrón: {db_err}", file=sys.stderr)
            try:
                db_session.rollback()
            except Exception:
                pass
            return False

    except Exception as e:
        print(f"[FLYWHEEL] Error inesperado en contribuir_patron: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# CONSUMIR EL FLYWHEEL — enriquecer_con_flywheel()
# ═══════════════════════════════════════════════════════════════════════════════

def enriquecer_con_flywheel(
    sector: str,
    db_session,
    *,
    tipo_patron: str = None,
    pais: str = "AR",
    limite: int = 30,
) -> dict:
    """
    Lee patrones colectivos del Flywheel para un sector dado y devuelve
    un dict con insights listos para inyectar en el system prompt de JARVIS.

    Retorna siempre un dict (nunca None). Si no hay datos o falla, devuelve
    un dict con campos vacíos — el caller lo usa con seguridad.

    Resultado:
    {
        "tiene_datos": bool,
        "total_contribuciones": int,
        "score_promedio_leads_exitosos": float | None,
        "ciclo_promedio_dias": float | None,
        "mejor_canal_contacto": str | None,
        "mejor_horario_contacto": str | None,
        "objeciones_mas_comunes": list[str],
        "tacticas_mas_efectivas": list[str],
        "señales_compra_detectadas": list[str],
        "señales_fuga_detectadas": list[str],
        "insight_narrativo": str,          # texto para inyectar en el prompt
        "confianza": str,                  # "alta" | "media" | "baja"
    }
    """
    resultado_vacio = {
        "tiene_datos": False,
        "total_contribuciones": 0,
        "score_promedio_leads_exitosos": None,
        "ciclo_promedio_dias": None,
        "mejor_canal_contacto": None,
        "mejor_horario_contacto": None,
        "objeciones_mas_comunes": [],
        "tacticas_mas_efectivas": [],
        "señales_compra_detectadas": [],
        "señales_fuga_detectadas": [],
        "insight_narrativo": "",
        "confianza": "baja",
    }

    if not sector:
        return resultado_vacio

    try:
        from sqlalchemy import text

        # Construir query — filtra por sector, opcionalmente por tipo_patron
        params: dict = {"sector": sector.lower(), "pais": pais.upper(), "limite": limite}
        tipo_filter  = ""
        if tipo_patron:
            tipo_filter = "AND tipo_patron = :tipo"
            params["tipo"] = tipo_patron

        filas = db_session.execute(
            text(f"""
                SELECT tipo_patron, data, contribuciones
                FROM jarvis_patron_colectivo
                WHERE sector = :sector
                  AND (pais = :pais OR pais = 'LATAM')
                  {tipo_filter}
                ORDER BY contribuciones DESC, actualizado_en DESC
                LIMIT :limite
            """),
            params,
        ).fetchall()

        if not filas:
            return resultado_vacio

        # Parsear todos los patrones
        patrones: list[dict] = []
        total_contribuciones  = 0
        for fila in filas:
            try:
                data = json.loads(fila[1]) if isinstance(fila[1], str) else fila[1]
                data["_contribuciones"] = fila[2]
                patrones.append(data)
                total_contribuciones += fila[2]
            except Exception:
                pass

        if not patrones:
            return resultado_vacio

        # ── Extracción heurística de métricas ─────────────────────────────────
        scores_exitosos: list[float] = []
        ciclos: list[float]          = []
        canales: dict[str, int]      = {}
        horarios: dict[str, int]     = {}
        objeciones: dict[str, int]   = {}
        tacticas: dict[str, int]     = {}
        señales_compra: dict[str, int] = {}
        señales_fuga: dict[str, int]   = {}

        for p in patrones:
            contribuciones = p.get("_contribuciones", 1)

            # Scores de cierres exitosos
            if p.get("tipo_patron") == "cierre" and p.get("score_inicial"):
                try:
                    scores_exitosos.append(float(p["score_inicial"]))
                except Exception:
                    pass

            if p.get("tipo_patron") == "score_lead" and p.get("resultado") == "cerrado":
                try:
                    scores_exitosos.append(float(p["score"]))
                except Exception:
                    pass

            # Ciclos de venta
            for campo in ("dias_ciclo", "dias_desde_primer_contacto", "ciclo_dias"):
                val = p.get(campo)
                if val and isinstance(val, (int, float)) and 1 <= val <= 365:
                    ciclos.append(float(val))
                    break

            # Canal de contacto
            canal = p.get("canal_primer_contacto") or p.get("canal_contacto")
            if canal:
                canales[canal] = canales.get(canal, 0) + contribuciones

            # Horario
            horario = p.get("horario_contacto") or p.get("mejor_hora")
            if horario:
                horarios[horario] = horarios.get(horario, 0) + contribuciones

            # Objeciones
            if p.get("tipo_patron") == "objecion_resuelta":
                obj = p.get("objecion", "")
                if obj:
                    objeciones[obj] = objeciones.get(obj, 0) + contribuciones

            # Tácticas efectivas
            tactica = p.get("tactica") or p.get("argumento")
            if tactica and p.get("tipo_patron") in ("objecion_resuelta", "script_efectivo"):
                tacticas[tactica] = tacticas.get(tactica, 0) + contribuciones

            # Señales de compra/fuga
            if p.get("tipo_patron") == "señal_compra":
                señal = p.get("señal", "")
                if señal:
                    señales_compra[señal] = señales_compra.get(señal, 0) + contribuciones

            if p.get("tipo_patron") == "señal_fuga":
                señal = p.get("señal", "")
                if señal:
                    señales_fuga[señal] = señales_fuga.get(señal, 0) + contribuciones

            # Señales embebidas en cierres
            for s in p.get("señales_previas", []):
                señales_compra[s] = señales_compra.get(s, 0) + 1

        # ── Construir el resultado ────────────────────────────────────────────
        score_prom = round(sum(scores_exitosos) / len(scores_exitosos), 1) if scores_exitosos else None
        ciclo_prom = round(sum(ciclos) / len(ciclos), 1) if ciclos else None
        mejor_canal    = max(canales, key=canales.get) if canales else None
        mejor_horario  = max(horarios, key=horarios.get) if horarios else None
        top_objeciones = sorted(objeciones, key=objeciones.get, reverse=True)[:3]
        top_tacticas   = sorted(tacticas, key=tacticas.get, reverse=True)[:3]
        top_señales_c  = sorted(señales_compra, key=señales_compra.get, reverse=True)[:4]
        top_señales_f  = sorted(señales_fuga, key=señales_fuga.get, reverse=True)[:3]

        # Nivel de confianza según cantidad de contribuciones
        if total_contribuciones >= 50:
            confianza = "alta"
        elif total_contribuciones >= 15:
            confianza = "media"
        else:
            confianza = "baja"

        # Narrativa para inyectar en el prompt
        partes_narrativa: list[str] = []
        if score_prom:
            partes_narrativa.append(
                f"Los leads con score ≥{score_prom:.0f} en {sector} históricamente cierran."
            )
        if ciclo_prom:
            partes_narrativa.append(
                f"El ciclo promedio en {sector} es de {ciclo_prom:.0f} días."
            )
        if mejor_canal:
            partes_narrativa.append(
                f"El canal con mejor tasa de respuesta en {sector} es {mejor_canal}."
            )
        if mejor_horario:
            partes_narrativa.append(f"Mejor horario de contacto: {mejor_horario}.")
        if top_objeciones:
            partes_narrativa.append(
                f"Las objeciones más frecuentes son: {', '.join(top_objeciones)}."
            )
        if top_señales_c:
            partes_narrativa.append(
                f"Señales de compra detectadas en la red: {', '.join(top_señales_c)}."
            )

        insight_narrativo = " ".join(partes_narrativa) if partes_narrativa else ""

        return {
            "tiene_datos":                    True,
            "total_contribuciones":           total_contribuciones,
            "score_promedio_leads_exitosos":  score_prom,
            "ciclo_promedio_dias":            ciclo_prom,
            "mejor_canal_contacto":           mejor_canal,
            "mejor_horario_contacto":         mejor_horario,
            "objeciones_mas_comunes":         top_objeciones,
            "tacticas_mas_efectivas":         top_tacticas,
            "señales_compra_detectadas":      top_señales_c,
            "señales_fuga_detectadas":        top_señales_f,
            "insight_narrativo":              insight_narrativo,
            "confianza":                      confianza,
        }

    except Exception as e:
        msg = str(e).lower()
        if "does not exist" not in msg and "no such table" not in msg:
            print(f"[FLYWHEEL] Error en enriquecer_con_flywheel: {e}", file=sys.stderr)
        return resultado_vacio


# ═══════════════════════════════════════════════════════════════════════════════
# ENRIQUECER SCORE DE LEAD CON FLYWHEEL
# Función puntual para llamar desde jarvis_leads.py antes de devolver el score
# ═══════════════════════════════════════════════════════════════════════════════

def enriquecer_score_lead(
    score_base: int,
    sector: str,
    db_session,
    *,
    cargo_contacto: str = "",
    tamaño_empresa: str = "",
    canal_contacto: str = "",
    pais: str = "AR",
) -> dict:
    """
    Ajusta el score base de un lead usando el aprendizaje colectivo del Flywheel.

    Devuelve:
    {
        "score_final": int,            # score ajustado (0-100)
        "ajuste": int,                 # delta aplicado (puede ser negativo)
        "razon_ajuste": str,           # explicación del ajuste
        "contexto_flywheel": str,      # texto para incluir en el análisis del lead
        "confianza_flywheel": str,     # "alta" | "media" | "baja" | "sin_datos"
    }
    """
    fw = enriquecer_con_flywheel(sector, db_session, tipo_patron="score_lead", pais=pais)

    if not fw["tiene_datos"]:
        return {
            "score_final":        score_base,
            "ajuste":             0,
            "razon_ajuste":       "Sin datos del Flywheel para este sector aún.",
            "contexto_flywheel":  "",
            "confianza_flywheel": "sin_datos",
        }

    ajuste      = 0
    razones: list[str] = []

    # Ajuste 1: si el score base está por encima del promedio de cierres históricos
    score_prom = fw.get("score_promedio_leads_exitosos")
    if score_prom:
        if score_base >= score_prom + 10:
            ajuste  += 3
            razones.append(f"score significativamente sobre el umbral histórico de cierre ({score_prom:.0f})")
        elif score_base < score_prom - 15:
            ajuste  -= 4
            razones.append(f"score debajo del umbral histórico de cierre ({score_prom:.0f})")

    # Ajuste 2: canal de contacto óptimo
    mejor_canal = fw.get("mejor_canal_contacto")
    if mejor_canal and canal_contacto and canal_contacto.lower() == mejor_canal.lower():
        ajuste  += 2
        razones.append(f"canal óptimo ({mejor_canal}) según la red")

    # Clamp a 0-100
    score_final = max(0, min(100, score_base + ajuste))

    # Texto para el análisis
    contexto_parts: list[str] = []
    if fw["insight_narrativo"]:
        contexto_parts.append(f"🔵 Contexto red ({fw['confianza']} confianza): {fw['insight_narrativo']}")
    if fw["señales_compra_detectadas"]:
        contexto_parts.append(
            f"Señales de compra a monitorear: {', '.join(fw['señales_compra_detectadas'][:3])}"
        )

    return {
        "score_final":        score_final,
        "ajuste":             ajuste,
        "razon_ajuste":       "; ".join(razones) if razones else "Sin ajuste significativo.",
        "contexto_flywheel":  "\n".join(contexto_parts),
        "confianza_flywheel": fw["confianza"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SÍNTESIS CON IA — para el endpoint /jarvis/flywheel/insights
# ═══════════════════════════════════════════════════════════════════════════════

def sintetizar_insights_ia(sector: str, data_flywheel: dict) -> Optional[str]:
    """
    Usa Claude para convertir los patrones crudos del Flywheel en un párrafo
    de insights accionables para el aliado. Solo se llama si hay datos suficientes.
    """
    if not data_flywheel.get("tiene_datos") or not ANTHROPIC_API_KEY:
        return None

    prompt = f"""
Tenés datos del Flywheel colectivo de JARVIS para el sector "{sector}".
Basados en {data_flywheel['total_contribuciones']} contribuciones anonimizadas de aliados:

- Score promedio de leads que cierran: {data_flywheel.get('score_promedio_leads_exitosos') or 'sin datos'}
- Ciclo promedio de venta: {data_flywheel.get('ciclo_promedio_dias') or 'sin datos'} días
- Canal más efectivo: {data_flywheel.get('mejor_canal_contacto') or 'sin datos'}
- Objeciones más frecuentes: {', '.join(data_flywheel.get('objeciones_mas_comunes', [])) or 'sin datos'}
- Señales de compra detectadas: {', '.join(data_flywheel.get('señales_compra_detectadas', [])) or 'sin datos'}
- Tácticas más efectivas: {', '.join(data_flywheel.get('tacticas_mas_efectivas', [])) or 'sin datos'}

Generá un párrafo de 3-4 líneas con el insight más valioso para un aliado que vende
en este sector. Tono directo, accionable. En español rioplatense.

Respondé SOLO con el párrafo de texto, sin JSON, sin títulos, sin bullets.
"""
    system = "Sos el sistema de síntesis de inteligencia colectiva de JARVIS para Avanza Digital."
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=JARVIS_MODEL,
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            timeout=JARVIS_TIMEOUT,
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"[FLYWHEEL] Error en síntesis IA: {e}", file=sys.stderr)
        return data_flywheel.get("insight_narrativo") or None


# ═══════════════════════════════════════════════════════════════════════════════
# STATS GENERALES DEL FLYWHEEL (sin datos del aliado — para admin o portal)
# ═══════════════════════════════════════════════════════════════════════════════

def get_stats_globales(db_session) -> dict:
    """
    Estadísticas agregadas del Flywheel. No expone datos de ningún aliado.
    Útil para el dashboard de admin y para mostrarle al aliado que el sistema aprende.
    """
    try:
        from sqlalchemy import text
        fila = db_session.execute(
            text("""
                SELECT
                    COUNT(*) as total_patrones,
                    COALESCE(SUM(contribuciones), 0) as total_contribuciones,
                    COUNT(DISTINCT sector) as sectores_activos,
                    COUNT(DISTINCT pais) as paises_activos
                FROM jarvis_patron_colectivo
            """)
        ).fetchone()

        if not fila:
            return {"ok": True, "datos": False}

        sectores = db_session.execute(
            text("""
                SELECT sector, SUM(contribuciones) as total
                FROM jarvis_patron_colectivo
                GROUP BY sector
                ORDER BY total DESC
                LIMIT 10
            """)
        ).fetchall()

        tipos = db_session.execute(
            text("""
                SELECT tipo_patron, COUNT(*) as patrones, SUM(contribuciones) as total
                FROM jarvis_patron_colectivo
                GROUP BY tipo_patron
                ORDER BY total DESC
            """)
        ).fetchall()

        return {
            "ok":                    True,
            "datos":                 True,
            "total_patrones":        fila[0],
            "total_contribuciones":  fila[1],
            "sectores_activos":      fila[2],
            "paises_activos":        fila[3],
            "top_sectores":          [{"sector": r[0], "contribuciones": r[1]} for r in sectores],
            "por_tipo_patron":       [{"tipo": r[0], "patrones": r[1], "contribuciones": r[2]} for r in tipos],
        }

    except Exception as e:
        msg = str(e).lower()
        if "does not exist" in msg or "no such table" in msg:
            return {"ok": True, "datos": False, "mensaje": "Tabla no existe aún. Ejecutar migración."}
        print(f"[FLYWHEEL] Error en get_stats_globales: {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER — proceso semanal de síntesis de patrones
# ═══════════════════════════════════════════════════════════════════════════════

def _job_sintetizar_patrones_duplicados(db_session_factory):
    """
    Job semanal: consolida patrones similares con muchas contribuciones.
    Corre los lunes a las 2am. No es crítico — silencia todos los errores.
    """
    try:
        db = next(db_session_factory())
        from sqlalchemy import text
        # Limpiar patrones muy antiguos con pocas contribuciones (ruido)
        db.execute(
            text("""
                DELETE FROM jarvis_patron_colectivo
                WHERE contribuciones = 1
                  AND creado_en < NOW() - INTERVAL '30 days'
            """)
        )
        db.commit()
        print("[FLYWHEEL] Limpieza de patrones de bajo valor completada.", flush=True)
    except Exception as e:
        print(f"[FLYWHEEL] Error en job semanal: {e}", file=sys.stderr)


def agregar_insights_flywheel_al_scheduler(scheduler, db_session_factory):
    """
    Registra el job de mantenimiento del Flywheel en el APScheduler de main.py.

    Usar en main.py:
        import jarvis_flywheel
        jarvis_flywheel.agregar_insights_flywheel_al_scheduler(scheduler, get_db)
    """
    try:
        scheduler.add_job(
            func=_job_sintetizar_patrones_duplicados,
            args=[db_session_factory],
            trigger="cron",
            day_of_week="mon",
            hour=2,
            minute=0,
            id="flywheel_limpieza_semanal",
            replace_existing=True,
        )
        print("[FLYWHEEL] Job semanal de limpieza registrado (lunes 2am).", flush=True)
    except Exception as e:
        print(f"[FLYWHEEL] Error registrando job en scheduler: {e}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS FASTAPI — register()
# ═══════════════════════════════════════════════════════════════════════════════

def register(app, get_db_func, auth_dep):
    """
    Registra los endpoints del Flywheel en la app FastAPI.

    Llamar desde main.py:
        import jarvis_flywheel
        jarvis_flywheel.register(app, get_db, current_aliado_required)
    """
    from fastapi import Depends, HTTPException, Query
    from sqlalchemy.orm import Session
    from pydantic import BaseModel
    import json as _json

    class ContribuirRequest(BaseModel):
        sector: str
        tipo_patron: str
        data: dict
        pais: str = "AR"

    # ── GET /jarvis/flywheel/stats ────────────────────────────────────────────
    @app.get("/jarvis/flywheel/stats")
    def ep_flywheel_stats(
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Estadísticas globales del Flywheel.
        Muestra cuánto aprendió la red — sin exponer datos de ningún aliado.
        """
        return get_stats_globales(db)

    # ── GET /jarvis/flywheel/insights ─────────────────────────────────────────
    @app.get("/jarvis/flywheel/insights")
    def ep_flywheel_insights(
        sector: str = Query(..., description="Sector a consultar: metalurgica, agro, logistica, etc."),
        tipo_patron: str = Query(None, description="Filtrar por tipo de patrón (opcional)"),
        pais: str = Query("AR"),
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Insights colectivos para el sector del aliado.
        El aliado ve qué aprendió la red — nunca quién lo aportó.
        """
        data = enriquecer_con_flywheel(
            sector, db, tipo_patron=tipo_patron, pais=pais
        )

        # Síntesis narrativa con IA si hay datos suficientes
        narrativa_ia = None
        if data["tiene_datos"] and data["total_contribuciones"] >= 10:
            narrativa_ia = sintetizar_insights_ia(sector, data)

        return {
            "ok":            True,
            "sector":        sector,
            "flywheel":      data,
            "narrativa_ia":  narrativa_ia,
        }

    # ── POST /jarvis/flywheel/contribuir (testing / admin) ───────────────────
    @app.post("/jarvis/flywheel/contribuir")
    def ep_flywheel_contribuir(
        body: ContribuirRequest,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        """
        Contribución manual al Flywheel. Útil para testing o para que el aliado
        registre manualmente un resultado (ej: confirmación de cierre con datos).
        La anonimización es automática — nunca se guarda el aliado_id.
        """
        if body.tipo_patron not in TIPOS_PATRON:
            raise HTTPException(
                400,
                f"Tipo de patrón inválido. Válidos: {list(TIPOS_PATRON.keys())}"
            )

        ok = contribuir_patron(
            sector=body.sector,
            tipo_patron=body.tipo_patron,
            data=body.data,
            db_session=db,
            pais=body.pais,
            aliado_id=aliado.id,
        )

        if not ok:
            raise HTTPException(500, "No se pudo contribuir al Flywheel. Verificar migración de BD.")

        return {
            "ok":          True,
            "mensaje":     "Patrón anonimizado y contribuido al Flywheel colectivo.",
            "sector":      body.sector,
            "tipo_patron": body.tipo_patron,
        }

    # ── GET /jarvis/flywheel/migration-sql ───────────────────────────────────
    @app.get("/jarvis/flywheel/migration-sql")
    def ep_flywheel_migration_sql(aliado=Depends(auth_dep)):
        """
        Devuelve el SQL de migración para crear la tabla jarvis_patron_colectivo.
        Ya está incluido en jarvis_memoria.get_migration_sql() — este endpoint
        es solo para diagnóstico.
        """
        sql = """
CREATE TABLE IF NOT EXISTS jarvis_patron_colectivo (
    id               SERIAL PRIMARY KEY,
    sector           VARCHAR(50) NOT NULL,
    pais             VARCHAR(5)  NOT NULL DEFAULT 'AR',
    tipo_patron      VARCHAR(50) NOT NULL,
    patron_hash      VARCHAR(64),
    data             TEXT NOT NULL,
    contribuciones   INTEGER DEFAULT 1,
    creado_en        TIMESTAMP DEFAULT NOW(),
    actualizado_en   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_patron_sector ON jarvis_patron_colectivo (sector, tipo_patron);
CREATE INDEX IF NOT EXISTS idx_patron_hash   ON jarvis_patron_colectivo (patron_hash);
        """.strip()
        return {
            "ok":          True,
            "instruccion": "Ejecutar en Supabase/Postgres una sola vez. Ya está incluido en jarvis_memoria.get_migration_sql().",
            "sql":         sql,
        }

    # ── GET /jarvis/flywheel/tipos ─────────────────────────────────────────
    @app.get("/jarvis/flywheel/tipos")
    def ep_flywheel_tipos(aliado=Depends(auth_dep)):
        """Lista los tipos de patrón válidos para contribuir al Flywheel."""
        return {"ok": True, "tipos": TIPOS_PATRON}
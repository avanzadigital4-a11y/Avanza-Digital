"""
ia_comercial.py — Inteligencia comercial sobre el pipeline (Groq + heurísticas).

Undécimo router migrado de main.py (tramo 6 del split). Contiene los
asistentes de IA del portal — todos con el patrón "IA primero, fallback
heurístico determinístico si Groq no está o falla":
  - Siguiente Mejor Acción del aliado (prioriza leads calientes, follow-ups
    y caducidad de bolsa) y Coach de Onboarding (diagnóstico según actividad
    real, no solo el checklist tildado).
  - Outreach: primer mensaje de WhatsApp personalizado por rubro/observación,
    para leads de la bolsa y prospectos del CRM. GRATIS (no consume créditos):
    es el habilitador del primer contacto.
  - Perfilado: heurístico para prospectos (RUBROS_PLAN/TAMANOS/URGENCIA →
    score + plan + pitch) e IA para leads de la bolsa (pre-reclamo).
  - Follow-up IA, respuesta a objeciones e informe de venta perdida sobre
    prospectos (ownership vía _get_prospecto_owned_or_admin de prospectos.py).
  - Asistente de redacción para posts de comunidad.
  - Reputación: score 0-100 + badges del aliado y ranking admin (este último
    con Depends(current_admin_required) explícito, criterio de tramos previos).

El checklist /aliados/{codigo}/onboarding NO vive acá (no es IA; el coach
reconstruye el suyo inline). El piloto automático tampoco: son jobs del
scheduler y quedan en main. PATCH /prospectos/{id}/datos se mudó a
prospectos.py — es CRM puro, solo alimenta los campos que el perfilado lee.
"""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import groq_ai
import schemas
from auth import current_aliado_required, current_admin_required, verify_ownership_dep
from bolsa import _aplicar_caducidad_bolsa
from database import get_db
from models import Aliado, LeadBolsa, Prospecto, PLANES, REPUTACION_BADGES
from prospectos import _get_prospecto_owned_or_admin

router = APIRouter(tags=["ia_comercial"])


# ── Puente diferido a helpers de main (evita import circular) ────────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


# ─── SIGUIENTE MEJOR ACCIÓN ───────────────────────────────────────────────────

@router.get("/aliados/{codigo}/siguiente-accion")
def siguiente_accion(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Analiza la situación del aliado y devuelve la acción más urgente e impactante."""
    a = _get_aliado(codigo, db)
    _aplicar_caducidad_bolsa(db)  # la REGLA DE ORO vive en bolsa.py
    acciones = []
    es_canal2 = (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2"

    # 1. Lead caliente: respondió pero no se cotizó
    respondieron = [p for p in a.prospectos if p.estado == "respondio"]
    if respondieron:
        mejor = max(respondieron, key=lambda p: p.fecha_respuesta or p.creado_en)
        acciones.append({
            "tipo": "cerrar_lead_caliente", "urgencia": 5, "icono": "⚡",
            "titulo": f"¡{mejor.nombre} está caliente!",
            "descripcion": "Respondió y está esperando tu propuesta. Usá el Cotizador y enviásela ahora — cada hora enfría el lead.",
            "accion_id": mejor.id, "boton": "Armar propuesta ahora", "tab": "cotizador",
            "color": "green"
        })

    # 2. Propuesta enviada sin respuesta (>= 3 dias)
    propuestas_sin_resp = [
        p for p in a.prospectos
        if p.estado == "propuesta_enviada" and p.fecha_contacto
        and (datetime.now() - p.fecha_contacto).days >= 3
    ]
    if propuestas_sin_resp:
        urgente = max(propuestas_sin_resp, key=lambda p: (datetime.now() - p.fecha_contacto).days)
        dias_esp = (datetime.now() - urgente.fecha_contacto).days
        acciones.append({
            "tipo": "seguimiento_propuesta", "urgencia": 4, "icono": "\U0001f4c4",
            "titulo": f"Seguimiento: {urgente.nombre} tiene tu propuesta",
            "descripcion": f"Enviaste la propuesta hace {dias_esp} días y no hubo respuesta. Un mensaje corto puede desbloquearla: \u2018¿Pudiste revisarla? Cualquier duda te aclaro.\u2019",
            "accion_id": urgente.id, "boton": "Ver Prospecto", "tab": "prospectos",
            "color": "amber"
        })

    # 3. Prospectos sin contactar
    sin_contactar = [p for p in a.prospectos if p.estado == "sin_contactar"]
    if sin_contactar:
        viejo = min(sin_contactar, key=lambda p: p.creado_en)
        dias = (datetime.now() - viejo.creado_en).days
        acciones.append({
            "tipo": "contactar_prospecto", "urgencia": 4, "icono": "🔥",
            "titulo": f"Contactá a {viejo.nombre}",
            "descripcion": f"Lleva {dias} día{'s' if dias != 1 else ''} sin contactar. Enviá el link de Auditoría gratuita para romper el hielo.",
            "accion_id": viejo.id, "boton": "Ir a Prospectos", "tab": "prospectos",
            "color": "amber"
        })

    # 3. Prospectos se enfrían (contactados sin respuesta >3 días)
    frios = [(p, (datetime.now() - p.fecha_contacto).days)
             for p in a.prospectos if p.estado == "contactado" and p.fecha_contacto
             and (datetime.now() - p.fecha_contacto).days >= 3]
    if frios:
        frio, dias_f = max(frios, key=lambda x: x[1])
        acciones.append({
            "tipo": "seguimiento", "urgencia": 3, "icono": "❄️",
            "titulo": f"Seguimiento urgente: {frio.nombre}",
            "descripcion": f"Hace {dias_f} días que no responde. Mandá un mensaje corto: '¿Pudiste ver lo que te envié?' Sin presionar.",
            "accion_id": frio.id, "boton": "Ver Prospectos", "tab": "prospectos",
            "color": "primary"
        })

    # 4. Leads disponibles en bolsa — SOLO Canal 1
    if not es_canal2:
        reclamos_activos = db.query(LeadBolsa).filter(
            LeadBolsa.aliado_id == a.id, LeadBolsa.estado == "reclamado"
        ).count()
        leads_disp = db.query(LeadBolsa).filter(LeadBolsa.estado == "disponible").count()
        if leads_disp > 0 and reclamos_activos < 3:
            acciones.append({
                "tipo": "reclamar_lead", "urgencia": 2, "icono": "🎯",
                "titulo": f"{leads_disp} lead{'s' if leads_disp > 1 else ''} disponible{'s' if leads_disp > 1 else ''} en la bolsa",
                "descripcion": "Hay clientes pre-filtrados esperando. Reclamá uno antes que otro aliado lo tome.",
                "boton": "Ver Bolsa de Leads", "tab": "bolsa",
                "color": "primary"
            })

    # 5. Sin prospectos — acción diferenciada por canal
    if not a.prospectos and a.ventas_6_meses == 0:
        if es_canal2:
            acciones.append({
                "tipo": "primer_prospecto_c2", "urgencia": 1, "icono": "🚀",
                "titulo": "Cargá tu primer cliente hoy",
                "descripcion": "Pensá en 3 clientes de tu cartera que no tienen presencia digital. Entrá al Selector de Rubro, elegí su industria y tenés el pitch listo en 30 segundos.",
                "boton": "Ir al Selector de Rubro", "tab": "selector-rubro",
                "color": "green"
            })
        else:
            acciones.append({
                "tipo": "prospectar", "urgencia": 1, "icono": "🚀",
                "titulo": "Cargá tu primer prospecto hoy",
                "descripcion": "Pensá en 3 empresas de tu entorno que podrían necesitar presencia digital. Agregalas y contactalas con el enlace de Auditoría.",
                "boton": "Agregar Prospecto", "tab": "prospectos",
                "color": "primary"
            })

    acciones.sort(key=lambda x: x["urgencia"], reverse=True)

    # ─── ENRIQUECIMIENTO IA — solo para la acción priorizada ─────────────────
    # Llamamos a Groq SOLO para la acción más urgente. Las otras 3 se quedan
    # con sus textos plantilla. Esto limita el volumen de requests a Groq y
    # mantiene la latencia baja.
    if acciones and acciones[0].get("accion_id"):
        principal = acciones[0]
        prospecto_obj = next(
            (pp for pp in a.prospectos if pp.id == principal["accion_id"]),
            None
        )
        if prospecto_obj is not None:
            # Calculamos `dias_relevantes` según el tipo de acción.
            dias = None
            if principal["tipo"] == "seguimiento_propuesta" and prospecto_obj.fecha_contacto:
                dias = (datetime.now() - prospecto_obj.fecha_contacto).days
            elif principal["tipo"] == "contactar_prospecto":
                dias = (datetime.now() - prospecto_obj.creado_en).days if prospecto_obj.creado_en else None
            elif principal["tipo"] == "seguimiento" and prospecto_obj.fecha_contacto:
                dias = (datetime.now() - prospecto_obj.fecha_contacto).days

            ia_msg = groq_ai.siguiente_accion_ia(
                tipo=principal["tipo"],
                prospecto_nombre=prospecto_obj.nombre,
                prospecto_rubro=prospecto_obj.rubro,
                prospecto_tamano=prospecto_obj.tamano,
                prospecto_urgencia=prospecto_obj.urgencia,
                dias_relevantes=dias,
                ultima_nota=prospecto_obj.nota,
                aliado_nombre=a.nombre,
            )
            if ia_msg:
                # Pisamos la descripción genérica con la personalizada por IA.
                principal["descripcion"] = ia_msg["descripcion"]
                # Y añadimos el mensaje listo para copiar/pegar como campo nuevo.
                principal["mensaje_sugerido"] = ia_msg["mensaje_sugerido"]
                principal["fuente"] = "ia"
            else:
                principal["fuente"] = "plantilla"

    # Stats del aliado para el contexto
    total_prospectos = len(a.prospectos)
    tasa_cierre_pct = 0
    if a.referidos:
        ventas_ok = len([v for v in a.ventas if v.confirmada])
        tasa_cierre_pct = round((ventas_ok / len(a.referidos)) * 100)

    return {
        "siguiente_accion": acciones[0] if acciones else None,
        "todas": acciones[:4],
        "stats": {
            "total_prospectos": total_prospectos,
            "calientes": len(respondieron),
            "sin_contactar": len(sin_contactar),
            "tasa_cierre": tasa_cierre_pct,
        }
    }



# ─── COACH DE ONBOARDING IA (Prioridad #10) ──────────────────────────────────
# Agregado al checklist estático: un consejo IA personalizado según la actividad
# real del aliado (no solo qué pasos tildó). Devuelve diagnóstico + siguiente
# paso + razón + plantilla opcional.

@router.get("/aliados/{codigo}/coach-onboarding")
def coach_onboarding(codigo: str, db: Session = Depends(get_db),
                     _owner=Depends(verify_ownership_dep)):
    """
    Devuelve un diagnóstico IA + siguiente paso accionable basado en el estado
    real del aliado (no solo el checklist).
    """
    a = _get_aliado(codigo, db)
    es_canal2 = (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2"

    # ─── Recolectar datos de actividad real ─────────────────────────────────
    ahora = datetime.now()
    dias_desde_registro = (ahora - a.creado_en).days if a.creado_en else 0
    ultimo_login_dias = (ahora - a.ultimo_login).days if getattr(a, "ultimo_login", None) else None

    prospectos = a.prospectos or []
    n_prosp = len(prospectos)
    n_sin_contactar = sum(1 for p in prospectos if p.estado == "sin_contactar")
    n_contactados   = sum(1 for p in prospectos if p.estado == "contactado")
    n_respondio     = sum(1 for p in prospectos if p.estado == "respondio")
    n_leads_bolsa   = db.query(LeadBolsa).filter(LeadBolsa.aliado_id == a.id).count() if not es_canal2 else 0
    n_ventas        = a.ventas_6_meses or 0
    n_sub_aliados   = len(getattr(a, "sub_aliados", []) or [])

    # ─── Reconstruir checklist para saber pct y pasos pendientes ─────────────
    pasos_check = [
        ("Registrarte", True),
        ("Registrar tu primer referido", len(a.referidos or []) > 0),
        ("Cargar un prospecto", n_prosp > 0),
    ]
    if not es_canal2:
        pasos_check.append(("Reclamar un lead de la bolsa", n_leads_bolsa > 0))
    pasos_check += [
        ("Cerrar tu primera venta", n_ventas > 0),
        ("Invitar a tu primer sub-aliado", n_sub_aliados > 0),
    ]
    completados = sum(1 for _, ok in pasos_check if ok)
    pct = round(completados / len(pasos_check) * 100) if pasos_check else 0
    pasos_pendientes = [t for t, ok in pasos_check if not ok]

    # ─── Llamar a IA ─────────────────────────────────────────────────────────
    ia = groq_ai.coach_onboarding_ia(
        aliado_nombre=a.nombre,
        dias_desde_registro=dias_desde_registro,
        es_canal2=es_canal2,
        tiene_prospectos=(n_prosp > 0),
        n_prospectos=n_prosp,
        n_prospectos_sin_contactar=n_sin_contactar,
        n_prospectos_contactados=n_contactados,
        n_prospectos_respondio=n_respondio,
        n_leads_bolsa_reclamados=n_leads_bolsa,
        n_ventas=n_ventas,
        n_sub_aliados=n_sub_aliados,
        ultimo_login_dias=ultimo_login_dias,
        checklist_pct=pct,
        pasos_pendientes=pasos_pendientes,
    )

    if ia:
        return {
            "modo": "ia",
            "diagnostico": ia["diagnostico"],
            "siguiente_paso": ia["siguiente_paso"],
            "razon": ia["razon"],
            "plantilla": ia["plantilla"],
            "checklist_pct": pct,
        }

    # ─── Fallback heurístico: árbol de decisión simple ───────────────────────
    if n_prosp == 0:
        diag = "No hay prospectos cargados todavía. Sin inputs no hay outputs."
        nxt  = "Cargá 3 prospectos hoy: empresas conocidas que no tengan presencia digital."
        razon = "Tener pipeline es la condición mínima para que cualquier otra cosa funcione."
        plantilla = ""
    elif n_sin_contactar > 0 and n_sin_contactar == n_prosp:
        diag = f"Cargaste {n_prosp} prospecto{'s' if n_prosp != 1 else ''} pero no contactaste a ninguno."
        nxt  = "Mandale el link de la auditoría gratuita al primero de la lista hoy mismo."
        razon = "Inventario sin acción se enfría en 7 días — perdés la ventana."
        plantilla = "Hola, soy [tu nombre]. Te paso un diagnóstico digital gratuito de tu empresa, toma 30 seg: [link]. Si te hace ruido lo que devuelve, hablamos."
    elif n_respondio > 0 and n_ventas == 0:
        diag = f"Tenés {n_respondio} prospecto{'s' if n_respondio != 1 else ''} que respondieron pero ninguna venta cerrada."
        nxt  = "Usá el Cotizador para mandarles una propuesta concreta esta semana."
        razon = "Una respuesta sin propuesta concreta enfría en 5 días."
        plantilla = ""
    elif n_contactados > n_respondio and (n_contactados - n_respondio) >= 3:
        diag = f"{n_contactados - n_respondio} prospectos contactados que no respondieron — necesitan re-enganche."
        nxt  = "Usá el botón 'Follow-up IA' en cada prospecto contactado hace más de 3 días."
        razon = "Sin follow-up sistemático, el 80% de los leads se enfrían."
        plantilla = ""
    elif not es_canal2 and n_leads_bolsa == 0:
        diag = "Hay leads disponibles en la bolsa que no estás reclamando."
        nxt  = "Entrá a la Bolsa y reclamá 1 lead que matchee tu rubro fuerte."
        razon = "La bolsa son clientes pre-calificados — costo cero comparado con prospectar en frío."
        plantilla = ""
    elif n_ventas == 0 and n_prosp >= 5:
        diag = "Pipeline lleno pero cero ventas — el problema está en cierre, no en captación."
        nxt  = "Revisá los prospectos perdidos con el botón 'Marcar perdido + analizar IA'."
        razon = "Diagnosticar pérdidas pasadas es más rápido que cargar más prospectos."
        plantilla = ""
    elif n_sub_aliados == 0 and n_ventas >= 1:
        diag = "Ya cerraste ventas pero tu red de sub-aliados está plana."
        nxt  = "Invitá a 1 conocido que ya esté en venta consultiva (consultor, agencia, contador)."
        razon = "Cada sub-aliado activo te suma ingresos pasivos sin más esfuerzo."
        plantilla = ""
    else:
        diag = f"Tu progreso del checklist está en {pct}%."
        nxt  = "Avanzá con el siguiente paso pendiente: " + (pasos_pendientes[0] if pasos_pendientes else "todo completo, mantené el ritmo.")
        razon = "Cada paso del checklist desbloquea capacidades nuevas del programa."
        plantilla = ""

    return {
        "modo": "fallback",
        "diagnostico": diag,
        "siguiente_paso": nxt,
        "razon": razon,
        "plantilla": plantilla,
        "checklist_pct": pct,
    }


# ─ BOLSA DE LEADS → bolsa.py ────────────────────────────────────────────────
# El dominio LeadBolsa completo (admin CRUD + bulk + duplicados, reclamar,
# contactar con auto-conversión al CRM, puente convertir-prospecto,
# marketplace, comprar e historiales) vive en bolsa.py como APIRouter — ver
# include_router al final. Quedan acá los endpoints de IA sobre la bolsa
# (perfilar-ia, mensaje-outreach): migran con el dominio Jarvis/Groq.

# ─── OUTREACH IA: primer mensaje de WhatsApp según rubro/observación ──────────
# El click-to-WhatsApp mandaba siempre el mismo texto genérico. Acá el botón
# arma el mensaje según el rubro y la observación del lead — con IA (Groq) y
# fallback a plantillas por rubro si la IA no está disponible. Es GRATIS (no
# consume créditos): es el habilitador del primer contacto, donde se gana o
# pierde el lead.

_OUTREACH_GANCHOS = {
    "metalurgica":  "presupuestos que se pierden por demora en cotizar y seguimiento manual de clientes",
    "agro":         "cotizaciones y seguimiento de clientes del agro que se manejan a mano y se enfrían",
    "logistica":    "seguimiento de operaciones y consultas de clientes que se pierden entre llamados",
    "construccion": "presupuestos de obra que tardan días en salir y consultas que quedan sin responder",
    "clinica":      "turnos y consultas de pacientes que se pierden por gestión manual",
    "tecnico":      "pedidos de service y presupuestos que se traspapelan sin un sistema",
}
_OUTREACH_GANCHO_DEFAULT = "consultas de clientes que se pierden por procesos comerciales manuales"


def _plantilla_outreach(saludo: str, empresa: str, rubro: str) -> str:
    gancho = _OUTREACH_GANCHOS.get((rubro or "").lower().strip(), _OUTREACH_GANCHO_DEFAULT)
    return (f"{saludo}, te escribo de Avanza Digital. Trabajamos con empresas de tu rubro "
            f"resolviendo {gancho}. Vi {empresa} y creo que hay una oportunidad concreta "
            f"de mejorar eso. ¿Tenés 10 minutos esta semana para contarte cómo?")


def _generar_mensaje_outreach(aliado: Aliado, empresa: str, rubro: str = "",
                              observacion: str = "", nombre_contacto: str = "") -> dict:
    """Genera el primer mensaje de WhatsApp personalizado por rubro y
    observación. IA primero (groq_ai._chat devuelve None si falla → fallback
    a plantilla por rubro). Devuelve {mensaje, fuente: 'ia'|'plantilla'}."""
    nombre_pila = (nombre_contacto or "").split()[0] if nombre_contacto else ""
    saludo = f"Hola {nombre_pila}" if nombre_pila else "Hola"
    empresa = (empresa or "tu empresa").strip()

    system = (
        "Sos un vendedor B2B argentino experto en primer contacto por WhatsApp. "
        "Escribís mensajes de apertura para PyMEs en nombre de un aliado comercial de "
        "Avanza Digital (software de captación y automatización de ventas para PyMEs). "
        "Reglas estrictas: máximo 55 palabras, tono cercano y profesional con voseo "
        "argentino, sin emojis, sin promesas de cifras inventadas, mencioná un dolor "
        "concreto del rubro, terminá con UNA pregunta fácil de responder. "
        "Devolvé SOLO el mensaje, sin comillas ni texto adicional."
    )
    partes = [f"Empresa del prospecto: {empresa}."]
    if rubro:
        partes.append(f"Rubro: {rubro}.")
    if observacion:
        partes.append(f"Observación del prospectador sobre este lead: {observacion[:400]}")
    if nombre_pila:
        partes.append(f"El contacto se llama {nombre_pila} — saludalo por su nombre.")
    else:
        partes.append("No sabemos el nombre del contacto — arrancá con 'Hola'.")
    partes.append("El mensaje lo manda un asesor aliado de Avanza Digital, presentate como tal.")
    prompt = "\n".join(partes)

    try:
        import groq_ai as _ga
        texto = _ga._chat(prompt, system, max_tokens=220, temperature=0.6)
    except Exception:
        texto = None

    if texto:
        texto = texto.strip().strip('"').strip()
        # Guard: si la IA devolvió algo vacío o desmedido, caer a la plantilla.
        if 15 <= len(texto) <= 600:
            return {"mensaje": texto, "fuente": "ia"}

    return {"mensaje": _plantilla_outreach(saludo, empresa, rubro), "fuente": "plantilla"}


@router.post("/bolsa/{id}/mensaje-outreach")
def mensaje_outreach_bolsa(id: int,
                           aliado: Aliado = Depends(current_aliado_required),
                           db: Session = Depends(get_db)):
    """Primer mensaje de WhatsApp personalizado para un lead de la bolsa,
    según su rubro y la observación del prospectador. Gratis (sin créditos)."""
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")
    if lead.aliado_id != aliado.id:
        raise HTTPException(403, "Este lead no es tuyo.")
    return _generar_mensaje_outreach(
        aliado, lead.empresa, lead.rubro or "",
        lead.observacion or "", lead.nombre_contacto or "",
    )


@router.post("/prospectos/{id}/mensaje-outreach")
def mensaje_outreach_prospecto(id: int,
                               aliado: Aliado = Depends(current_aliado_required),
                               db: Session = Depends(get_db)):
    """Primer mensaje de WhatsApp personalizado para un prospecto del CRM."""
    p = db.query(Prospecto).filter(Prospecto.id == id).first()
    if not p:
        raise HTTPException(404, "Prospecto no encontrado.")
    if p.aliado_id != aliado.id:
        raise HTTPException(403, "Este prospecto no es tuyo.")
    return _generar_mensaje_outreach(
        aliado, p.nombre, p.rubro or "", p.nota or "", p.contacto or "",
    )


# (Historiales de la bolsa migrados a bolsa.py.)

# ═══════════════════════════════════════════════════════════════════════════
# ═══ v1.3 — INTELIGENCIA DE VENTAS + REPUTACIÓN + MARKETPLACE + COMUNIDAD ══
# ═══════════════════════════════════════════════════════════════════════════

# ─── PERFILADO IA DE LEADS (A) ───────────────────────────────────────────────
# Heurística local — sin LLM, explicable, determinística.
# El aliado carga rubro/tamaño/urgencia → el sistema devuelve score + plan + pitch.

RUBROS_PLAN = {
    # Rubros que naturalmente necesitan más infraestructura digital
    "Metalúrgica / Manufactura":     ("Plan Industrial", "B2B técnico con ciclo largo de venta"),
    "Agro / Maquinaria agrícola":    ("Plan Industrial", "Sector con presupuesto pero poca presencia digital"),
    "Logística / Transporte":        ("Plan Pro",        "Necesita canales claros de contacto y cotización"),
    "Servicios B2B / Consultoría":   ("Plan Pro",        "Necesita autoridad online y generación de leads"),
    "Comercio / Retail B2B":         ("Plan Pro",        "Catálogo + presencia local"),
    "Construcción / Obras":          ("Plan Industrial", "Obra pública/privada, necesita respaldo digital"),
    "Salud / Clínicas":              ("Plan Pro",        "Pacientes investigan online antes de elegir"),
    "Educación / Capacitación":      ("Plan Pro",        "Captación online es crítica"),
    "Tecnología / Software":         ("Estrategico 360", "Mercado educado, espera excelencia digital"),
    "Otro":                          ("Plan Pro",        "Plan versátil para la mayoría"),
}

TAMANOS_MULT = {"micro": 0.6, "pyme": 1.0, "mediana": 1.25, "grande": 1.4}
URGENCIA_SCORE = {"baja": 10, "media": 25, "alta": 40}


def _perfilar_prospecto(p: Prospecto) -> dict:
    """Corazón del perfilado IA: intenta Groq primero, fallback a heurística."""

    # ─── INTENTO 1: IA real (Groq) ──────────────────────────────────────────
    ia = groq_ai.perfilar_lead_ia(
        empresa=p.nombre,
        rubro=p.rubro,
        tamano=p.tamano,
        urgencia=p.urgencia,
        estado=p.estado,
        nota_aliado=p.nota,
    )
    if ia:
        # Si el aliado fijó un plan_interes manual, lo respetamos por encima de la IA.
        if p.plan_interes and p.plan_interes in PLANES:
            ia["plan_recomendado"] = p.plan_interes
            ia["ticket_esperado"] = round(PLANES[p.plan_interes] * TAMANOS_MULT.get(p.tamano or "pyme", 1.0), 0)
        return ia

    # ─── INTENTO 2: Fallback heurístico (lo de siempre) ─────────────────────
    return _perfilar_prospecto_heuristico(p)


def _perfilar_prospecto_heuristico(p: Prospecto) -> dict:
    """Heurística determinística — el fallback de siempre cuando Groq no responde."""
    score = 20  # base

    # 1. Rubro → +20 si es rubro de alta necesidad, plan sugerido
    plan, razon_rubro = RUBROS_PLAN.get(p.rubro or "Otro", ("Plan Pro", "Plan versátil"))
    if p.rubro and p.rubro != "Otro":
        score += 20

    # 2. Urgencia pesa fuerte (hasta +40)
    score += URGENCIA_SCORE.get(p.urgencia or "media", 25)

    # 3. Tamaño ajusta expectativa de ticket
    mult = TAMANOS_MULT.get(p.tamano or "pyme", 1.0)

    # Si el tamaño es grande → empujar a plan superior
    if p.tamano == "grande" and plan == "Plan Pro":
        plan = "Plan Industrial"
    elif p.tamano == "grande" and plan == "Plan Industrial":
        plan = "Estrategico 360"
    elif p.tamano == "micro" and plan != "Plan Base":
        plan = "Plan Base"
        razon_rubro = "Empresa chica — empezar con Plan Base y escalar después"

    # 4. Si ya respondió un mensaje → bonus fuerte
    if p.estado == "respondio":
        score += 15
    elif p.estado == "contactado":
        score += 5

    # 5. Si tiene plan_interes manual del aliado, respetarlo con un boost
    if p.plan_interes and p.plan_interes in PLANES:
        plan = p.plan_interes
        score += 5

    # Normalizar ticket esperado
    ticket = PLANES.get(plan, 2900) * mult
    score = max(0, min(100, int(score)))

    # 6. Pitch sugerido
    pitch = _generar_pitch(p.nombre, p.rubro, p.tamano, p.urgencia, plan, ticket)

    return {
        "score": score,
        "plan_recomendado": plan,
        "pitch_sugerido": pitch,
        "ticket_esperado": round(ticket, 0),
        "razon": razon_rubro,
    }


def _generar_pitch(nombre: str, rubro: str, tamano: str, urgencia: str, plan: str, ticket: float) -> str:
    """Genera un pitch corto y accionable para WhatsApp/email."""
    apertura = {
        "alta": f"Hola, vi que {nombre} está creciendo rápido — les paso algo que puede ahorrarles tiempo.",
        "media": f"Hola, estuve revisando empresas del rubro {rubro or 'de ustedes'} y {nombre} me llamó la atención.",
        "baja": f"Hola, te paso info por si a futuro les sirve. Sin apuro.",
    }.get(urgencia or "media")

    dolor = {
        "Metalúrgica / Manufactura": "Muchas fábricas pierden contactos porque su web no genera confianza técnica.",
        "Agro / Maquinaria agrícola": "En el agro el cliente investiga mucho antes de llamar — la web define si te llaman o no.",
        "Logística / Transporte": "Los clientes B2B esperan poder cotizar rápido, sin esperar 2 días a que les llamen.",
        "Servicios B2B / Consultoría": "Si tu web no transmite autoridad en 5 segundos, el lead se va a la competencia.",
        "Salud / Clínicas": "El 80% de los pacientes googlean antes de sacar turno.",
        "Construcción / Obras": "Las obras grandes se eligen por respaldo — y el respaldo hoy se mide online.",
    }.get(rubro or "Otro", "Las empresas que no invierten en digital pierden hasta un 30% de oportunidades por mes.")

    cierre = {
        "Plan Base":        f"Arrancamos con el Plan Base (USD {int(PLANES['Plan Base'])}): sitio limpio + Google Business + métricas en 30 días.",
        "Plan Pro":         f"Te sugiero el Plan Pro (USD {int(PLANES['Plan Pro'])}): incluye captación activa de leads, no solo presencia.",
        "Plan Industrial":  f"Por el tamaño de {nombre} va el Plan Industrial (USD {int(PLANES['Plan Industrial'])}): sistema completo + ventas B2B.",
        "Estrategico 360":  f"Lo que encaja acá es un Estratégico 360 (USD {int(PLANES['Estrategico 360'])}): canal digital entero operando como una máquina.",
    }.get(plan, "")

    return f"{apertura}\n\n{dolor}\n\n{cierre}\n\n¿Te mando un diagnóstico gratis para que veas el estado actual?"


@router.post("/prospectos/{id}/perfilar")
def perfilar_prospecto(id: int, request: Request,
                       body: schemas.PerfilarProspectoIn | None = Body(default=None),
                       rubro: str = "",
                       tamano: str = "pyme",
                       urgencia: str = "media",
                       db: Session = Depends(get_db)):
    """Corre el perfilado IA sobre un prospecto y guarda el resultado."""
    if body is not None:
        rubro, tamano, urgencia = body.rubro, body.tamano, body.urgencia
    p = _get_prospecto_owned_or_admin(id, request, db)
    if rubro:
        p.rubro = rubro
    p.tamano = tamano
    p.urgencia = urgencia

    resultado = _perfilar_prospecto(p)
    p.score_ia = resultado["score"]
    p.plan_recomendado = resultado["plan_recomendado"]
    p.pitch_sugerido = resultado["pitch_sugerido"]
    p.perfilado_en = datetime.now()
    db.commit()

    return {
        "mensaje": "Prospecto perfilado.",
        "score": resultado["score"],
        "plan_recomendado": resultado["plan_recomendado"],
        "pitch_sugerido": resultado["pitch_sugerido"],
        "ticket_esperado": resultado["ticket_esperado"],
        "razon": resultado["razon"],
    }


# ─── PERFILADO IA DE LEADS DE LA BOLSA ──────────────────────────────────────
# Antes esto era 100% JavaScript con templates en portal.html. Ahora pasa por
# Groq y devuelve un pitch real personalizado por empresa. Si Groq falla,
# devolvemos el resultado del fallback heurístico (el front sigue funcionando).

@router.post("/bolsa/{lead_id}/perfilar-ia")
def perfilar_lead_bolsa(lead_id: int, request: Request,
                        rubro: str = "",
                        tamano: str = "pyme",
                        urgencia: str = "media",
                        db: Session = Depends(get_db),
                        _aliado=Depends(current_aliado_required)):
    """
    Perfilado IA para un lead de la Bolsa.
    El aliado solo necesita estar autenticado (no necesita haber reclamado el lead todavía
    — perfilar antes de reclamar es parte del flow para decidir si vale el costo).
    """
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")

    # Si el aliado no pasó rubro, usamos el del lead.
    rubro_efectivo = (rubro or "").strip() or (lead.rubro or "")
    tamano_ef = (tamano or "pyme").strip()
    urgencia_ef = (urgencia or "media").strip()

    # ─── Intento 1: Groq ─────────────────────────────────────────────────────
    ia = groq_ai.perfilar_lead_ia(
        empresa=lead.empresa,
        rubro=rubro_efectivo,
        tamano=tamano_ef,
        urgencia=urgencia_ef,
        ciudad=lead.ciudad,
        # v1.6 — presencia digital
        web=lead.web,
        instagram=lead.instagram,
        tiene_web=bool(lead.tiene_web),
        tiene_redes=bool(lead.tiene_redes),
        observacion=lead.observacion,
    )
    if ia:
        return {
            "modo": "ia",
            "score": ia["score"],
            "plan_recomendado": ia["plan_recomendado"],
            "pitch_sugerido": ia["pitch_sugerido"],
            "ticket_esperado": ia["ticket_esperado"],
            "razon": ia["razon"],
        }

    # ─── Intento 2: Fallback heurístico ──────────────────────────────────────
    # Reusamos la misma lógica del prospecto montando un objeto temporal.
    class _LeadShim:
        nombre   = lead.empresa
        rubro    = rubro_efectivo
        tamano   = tamano_ef
        urgencia = urgencia_ef
        estado   = "sin_contactar"
        plan_interes = None
        nota = None
    res = _perfilar_prospecto_heuristico(_LeadShim())
    res["modo"] = "fallback"
    return res


# ─── GENERADOR DE FOLLOW-UP IA (Prioridad #4) ────────────────────────────────
# El aliado abre un prospecto y pide "generame un mensaje de seguimiento".
# Devuelve un mensaje listo para copiar+pegar. El aliado puede pedir varias
# veces para regenerar — cada llamada es un request a Groq.

@router.post("/prospectos/{id}/followup-ia")
def generar_followup_prospecto(id: int, request: Request,
                                tono: str = "directo",
                                db: Session = Depends(get_db)):
    """
    Genera un mensaje de follow-up para un prospecto que no responde.
    Tono válido: 'amigable' | 'directo' | 'ultimo' | 'valor' (default: directo).
    """
    p = _get_prospecto_owned_or_admin(id, request, db)

    # Calcular días sin responder según el estado.
    dias = None
    if p.estado in ("contactado", "propuesta_enviada") and p.fecha_contacto:
        dias = (datetime.now() - p.fecha_contacto).days
    elif p.fecha_respuesta:
        dias = (datetime.now() - p.fecha_respuesta).days
    elif p.creado_en:
        dias = (datetime.now() - p.creado_en).days

    aliado_obj = p.aliado

    ia = groq_ai.generar_followup_ia(
        prospecto_nombre=p.nombre,
        rubro=p.rubro,
        tamano=p.tamano,
        plan_recomendado=p.plan_recomendado or p.plan_interes,
        dias_sin_responder=dias,
        ultima_nota=p.nota,
        aliado_nombre=aliado_obj.nombre if aliado_obj else None,
        tono=tono if tono in ("amigable", "directo", "ultimo", "valor") else "directo",
    )
    if ia:
        return {
            "modo": "ia",
            "mensaje": ia["mensaje"],
            "estrategia": ia["estrategia"],
            "tono": tono,
            "dias_sin_responder": dias,
        }

    # ─── Fallback heurístico — mensajes plantilla por tono ───────────────────
    nombre_corto = (p.nombre or "").split()[0] or "Hola"
    plantillas = {
        "amigable": f"¡Hola {nombre_corto}! ¿Cómo va? Quería retomar lo que estábamos charlando. ¿Tenés un momento esta semana para repasarlo?",
        "directo":  f"Hola {nombre_corto}, soy [tu nombre]. Te escribo para retomar la propuesta. ¿Avanzamos o lo pausamos por ahora?",
        "ultimo":   f"Hola {nombre_corto}, este es mi último mensaje para no hacerme pesado. Si no es el momento, perfecto — quedate con mi contacto para cuando quieras retomar.",
        "valor":    f"Hola {nombre_corto}, te paso un dato del rubro {p.rubro or 'tuyo'} que quizás te sirve aunque no avancemos: empresas similares pierden hasta 30% de consultas por temas digitales simples. ¿Te interesa que te muestre cómo evaluarlo?",
    }
    return {
        "modo": "fallback",
        "mensaje": plantillas.get(tono, plantillas["directo"]),
        "estrategia": "Mensaje plantilla — la IA no estaba disponible.",
        "tono": tono,
        "dias_sin_responder": dias,
    }


# ─── RESPUESTA A OBJECIONES IA (Prioridad #5) ────────────────────────────────
# El aliado pega la objeción que le dijeron y Groq devuelve cómo responder.

@router.post("/prospectos/{id}/objecion-ia")
def responder_objecion_prospecto(id: int, request: Request,
                                  objecion: str = "",
                                  db: Session = Depends(get_db)):
    """
    Genera una respuesta a una objeción. El aliado pasa el texto de la objeción
    como query param (URL-encoded).
    """
    p = _get_prospecto_owned_or_admin(id, request, db)
    obj_text = (objecion or "").strip()
    if not obj_text:
        raise HTTPException(400, "Falta el texto de la objeción (?objecion=...)")

    ia = groq_ai.responder_objecion_ia(
        objecion=obj_text,
        prospecto_nombre=p.nombre,
        rubro=p.rubro,
        tamano=p.tamano,
        plan_recomendado=p.plan_recomendado or p.plan_interes,
        ticket_esperado=(PLANES.get(p.plan_recomendado or "", 0)
                         * TAMANOS_MULT.get(p.tamano or "pyme", 1.0)) if p.plan_recomendado else None,
    )
    if ia:
        return {"modo": "ia", **ia}

    # ─── Fallback: respuestas plantilla por palabra clave ────────────────────
    bajo = obj_text.lower()
    if any(k in bajo for k in ("caro", "precio", "presupuesto", "no tengo plata")):
        return {
            "modo": "fallback",
            "respuesta": "Te entiendo. La pregunta no es cuánto cuesta sino cuánto te cuesta NO tenerlo. Empresas similares pierden 20-40% de consultas por mes por temas digitales. ¿Hacemos un diagnóstico rápido para medirlo en tu caso?",
            "explicacion": "Reformula precio a costo de oportunidad.",
            "siguiente_pregunta": "¿Cuántas consultas mensuales recibís hoy?",
        }
    if any(k in bajo for k in ("ya tengo", "tengo web", "tengo página", "ya hice")):
        return {
            "modo": "fallback",
            "respuesta": "Buenísimo. Tener algo es mejor que nada. La pregunta clave es: ¿cuántas consultas reales te trae al mes y cómo se compara con el potencial del rubro? A veces conviene ajustar lo que hay, otras conviene rehacerlo.",
            "explicacion": "Calificá la web actual antes de proponer reemplazo.",
            "siguiente_pregunta": "¿Cuándo se hizo y qué métricas tenés?",
        }
    if any(k in bajo for k in ("no es el momento", "más adelante", "en unos meses", "otro momento")):
        return {
            "modo": "fallback",
            "respuesta": "Lo respeto. Igualmente te propongo algo: una llamada corta de diagnóstico (15 min) para que cuando SÍ sea el momento ya tengas los datos en la mano. Sin compromiso.",
            "explicacion": "Mantené la puerta abierta sin presionar.",
            "siguiente_pregunta": "¿Te queda mejor esta o la próxima semana?",
        }
    if any(k in bajo for k in ("pensar", "voy a ver", "consultar")):
        return {
            "modo": "fallback",
            "respuesta": "Perfecto. Para que la pensada te sirva, ¿qué información te faltaría tener para decidir? Te la armo y te la mando.",
            "explicacion": "Convertí 'lo pienso' en una pregunta concreta.",
            "siguiente_pregunta": "¿Qué dato te falta para definirlo?",
        }
    return {
        "modo": "fallback",
        "respuesta": "Te entiendo. ¿Me podés contar un poco más de qué es lo que más te frena? Así te respondo con algo concreto y no con un genérico.",
        "explicacion": "Pediles que aterricen la objeción.",
        "siguiente_pregunta": "¿Qué es lo que más te hace dudar?",
    }


# ─── ANÁLISIS DE VENTA PERDIDA IA (Prioridad #8) ─────────────────────────────
# Cuando un prospecto se marca como 'perdido', el aliado puede pedir un
# diagnóstico IA del historial completo: qué pasó, qué se hizo mal, qué hacer
# distinto la próxima, y si se puede recuperar más adelante.

@router.post("/prospectos/{id}/analizar-perdida")
def analizar_venta_perdida(id: int, request: Request,
                            motivo: str = "",
                            db: Session = Depends(get_db)):
    """
    Analiza el historial de un prospecto perdido y devuelve diagnóstico IA.
    Antes de analizar, marca el estado como 'perdido' si todavía no lo está
    y guarda el motivo en la nota (anteponiendo "[PERDIDO] ...").
    """
    p = _get_prospecto_owned_or_admin(id, request, db)

    # 1. Estado anterior antes de cambiarlo (lo necesita el análisis).
    estado_anterior = p.estado

    # 2. Marcar como perdido si todavía no lo está.
    if p.estado != "perdido":
        p.estado = "perdido"

    # 3. Guardar motivo en nota (sin pisar lo que ya había).
    motivo_clean = (motivo or "").strip()
    if motivo_clean:
        prefix = "[PERDIDO]"
        if p.nota and prefix not in p.nota:
            p.nota = f"{prefix} {motivo_clean}\n---\n{p.nota}"
        elif not p.nota:
            p.nota = f"{prefix} {motivo_clean}"
        # Si ya tenía un [PERDIDO], no duplicamos.

    db.commit()

    # 4. Calcular días relevantes para el análisis.
    ahora = datetime.now()
    dias_pipeline = (ahora - p.creado_en).days if p.creado_en else None
    dias_contacto = (ahora - p.fecha_contacto).days if p.fecha_contacto else None
    dias_respuesta = (ahora - p.fecha_respuesta).days if p.fecha_respuesta else None

    ticket_esp = None
    if p.plan_recomendado and p.plan_recomendado in PLANES:
        mult = TAMANOS_MULT.get(p.tamano or "pyme", 1.0)
        ticket_esp = PLANES[p.plan_recomendado] * mult

    # 5. Llamar a Groq.
    ia = groq_ai.analizar_venta_perdida_ia(
        prospecto_nombre=p.nombre,
        rubro=p.rubro,
        tamano=p.tamano,
        urgencia_perfilada=p.urgencia,
        plan_recomendado=p.plan_recomendado or p.plan_interes,
        ticket_esperado=ticket_esp,
        estado_anterior=estado_anterior,
        dias_en_pipeline=dias_pipeline,
        fecha_contacto_dias=dias_contacto,
        fecha_respuesta_dias=dias_respuesta,
        pasos_piloto=p.automation_paso or 0,
        notas=p.nota,
        motivo_aliado=motivo_clean or None,
    )
    if ia:
        return {"modo": "ia", "estado": p.estado, **ia}

    # ─── Fallback: análisis heurístico básico ────────────────────────────────
    errores = []
    distinto = []
    podria_rec = False

    # Caso 1: nunca contactado o contactado pero nunca respondió
    if estado_anterior in ("sin_contactar", "contactado"):
        errores.append("El prospecto pudo nunca haber recibido un mensaje claro de valor.")
        if dias_pipeline and dias_pipeline > 14:
            errores.append(f"Estuvo {dias_pipeline} días en pipeline sin avanzar — el lead se enfrió.")
        distinto.append("Cargá menos prospectos pero contactá a todos en las primeras 48hs.")
        distinto.append("Usá el botón 'Follow-up IA' a los 3 días de no recibir respuesta.")
        que_paso = "El prospecto no avanzó del primer contacto. Probable que el mensaje inicial no haya conectado o no haya habido follow-up sistemático."
        podria_rec = True

    # Caso 2: respondió pero no se cerró
    elif estado_anterior in ("respondio", "propuesta_enviada"):
        errores.append("Hubo interés pero no se cerró — falta presión positiva o el plan no encajó.")
        errores.append("Posiblemente faltó calificar urgencia/presupuesto antes de mandar la propuesta.")
        distinto.append("Antes de enviar propuesta, validar urgencia y decisor real.")
        distinto.append("Después de propuesta, agendar fecha concreta para revisarla juntos.")
        que_paso = "El prospecto entró en conversación pero la propuesta no avanzó. Suele indicar falta de calificación previa o ausencia de próximo paso definido."
        podria_rec = True

    else:
        errores.append("No hay suficiente historial para diagnosticar con precisión.")
        distinto.append("Anotá siempre el motivo de la pérdida en la nota para futuras revisiones.")
        que_paso = "Pocos datos del historial — registrá más contexto al cerrar prospectos."

    return {
        "modo": "fallback",
        "estado": p.estado,
        "que_paso": que_paso,
        "errores_posibles": errores,
        "que_hacer_distinto": distinto,
        "podria_recuperarse": podria_rec,
        "mensaje_recuperacion": "" if not podria_rec else f"Hola, hace un tiempo charlamos sobre {p.nombre} y la presencia digital. ¿Cambió algo en estos meses? Te paso un diagnóstico actualizado sin compromiso.",
    }


# ─── ASISTENTE PARA POSTS DE COMUNIDAD (Prioridad #7) ────────────────────────
# El aliado escribe unos datos cortos en el composer y Groq genera título+cuerpo.

@router.post("/comunidad/asistente-ia")
def asistente_post_comunidad(request: Request,
                              tipo: str = "tip",
                              datos: str = "",
                              db: Session = Depends(get_db),
                              aliado=Depends(current_aliado_required)):
    """
    Genera {titulo, cuerpo} para un post de comunidad.
    tipo: 'win' | 'tip' | 'pregunta'
    datos: texto libre con los datos clave que aporta el aliado.
    """
    datos_text = (datos or "").strip()
    if not datos_text:
        raise HTTPException(400, "Necesito unos datos clave para redactar el post.")
    if tipo not in ("win", "tip", "pregunta"):
        raise HTTPException(400, "Tipo inválido. Usá: win, tip o pregunta.")

    ia = groq_ai.redactar_post_comunidad_ia(
        tipo=tipo,
        datos_clave=datos_text,
        aliado_nombre=aliado.nombre,
    )
    if ia:
        return {"modo": "ia", **ia}

    # Fallback básico — devolvemos un esqueleto con los datos del aliado al menos formateados.
    plantilla_titulo = {
        "win":      "Compartiendo un cierre",
        "tip":      "Tip de la semana",
        "pregunta": "Una consulta para la red",
    }[tipo]
    return {
        "modo": "fallback",
        "titulo": plantilla_titulo,
        "cuerpo": datos_text,
    }


# ─── SISTEMA DE REPUTACIÓN (C) ───────────────────────────────────────────────

def _calcular_reputacion(a: Aliado, db: Session) -> dict:
    """Calcula score 0-100 + badges del aliado.
    Factores (ponderados):
      - Tasa de cierre (40%)
      - Velocidad de contacto en bolsa (20%)
      - Tasa éxito en bolsa (20%)
      - Actividad reciente (10%)
      - Tamaño de red (10%)
    """
    ventas_conf = [v for v in a.ventas if v.confirmada]
    total_ventas = len(ventas_conf)
    total_refs = len(a.referidos)
    tasa_cierre = (total_ventas / total_refs) if total_refs > 0 else 0
    ticket_prom = (sum(v.valor_usd for v in ventas_conf) / total_ventas) if total_ventas else 0

    # Bolsa
    leads_bolsa = getattr(a, "leads_bolsa", [])
    exitosos = sum(1 for l in leads_bolsa if l.resultado == "exitoso")
    tasa_bolsa = (exitosos / len(leads_bolsa)) if leads_bolsa else 0

    # Actividad (últimos 30 días)
    corte = datetime.now() - timedelta(days=30)
    activo_reciente = (a.ultimo_login and a.ultimo_login >= corte) or \
                      any(r.registrado_en >= corte for r in a.referidos) or \
                      any(v.fecha_venta and v.fecha_venta >= corte for v in ventas_conf)

    # Red
    red_activa = sum(1 for sub in getattr(a, "sub_aliados", []) if sub.ventas_6_meses > 0)

    # Score
    score = 30  # base
    score += int(min(40, tasa_cierre * 100))        # hasta +40 por tasa cierre
    score += int(min(20, tasa_bolsa * 50))          # hasta +20 por éxito bolsa
    score += 10 if activo_reciente else 0
    score += min(10, red_activa * 3)                # hasta +10 por red activa
    score = max(0, min(100, score))

    # Badges
    badges = []
    if tasa_cierre >= 0.40 and total_ventas >= 2:
        badges.append("CLOSER")
    if ticket_prom >= 3500 and total_ventas >= 1:
        badges.append("TOP_TICKET")
    if activo_reciente and a.cantidad_logins and a.cantidad_logins >= 10:
        badges.append("FIEL")
    if red_activa >= 3:
        badges.append("EMBAJADOR")
    if tasa_bolsa >= 0.30 and len(leads_bolsa) >= 3:
        badges.append("BOLSA_MASTER")
    # "Rápido": reclaimó al menos 3 leads en < 6hs desde que entraron a la bolsa
    tiempos = []
    for l in leads_bolsa:
        if l.fecha_carga and l.fecha_reclamo:
            horas = (l.fecha_reclamo - l.fecha_carga).total_seconds() / 3600
            tiempos.append(horas)
    rapidos = sum(1 for h in tiempos if h <= 6)
    if rapidos >= 3:
        badges.append("RAPIDO")

    return {
        "score": score,
        "badges": badges,
        "factores": {
            "tasa_cierre": round(tasa_cierre * 100, 1),
            "ticket_prom": round(ticket_prom),
            "tasa_bolsa": round(tasa_bolsa * 100, 1),
            "activo_reciente": activo_reciente,
            "red_activa": red_activa,
        },
    }


@router.get("/aliados/{codigo}/reputacion")
def ver_reputacion(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    calc = _calcular_reputacion(a, db)
    # Persistir
    try:
        a.reputacion_score = calc["score"]
        a.badges = json.dumps(calc["badges"])
        a.reputacion_calculada_en = datetime.now()
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error guardando reputación: {e}")

    badges_full = [
        {"code": b, **REPUTACION_BADGES[b]}
        for b in calc["badges"] if b in REPUTACION_BADGES
    ]
    return {
        "codigo": a.codigo,
        "nombre": a.nombre,
        "score": calc["score"],
        "badges": badges_full,
        "factores": calc["factores"],
        "badges_disponibles": [
            {"code": code, **info} for code, info in REPUTACION_BADGES.items()
        ],
    }


@router.get("/admin/reputacion/ranking")
def ranking_reputacion(db: Session = Depends(get_db),
                       _admin=Depends(current_admin_required)):
    """Admin: ver todos los aliados rankeados por reputación."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    resultado = []
    for a in aliados:
        calc = _calcular_reputacion(a, db)
        resultado.append({
            "codigo": a.codigo,
            "nombre": a.nombre,
            "score": calc["score"],
            "badges": calc["badges"],
            **calc["factores"],
        })
    resultado.sort(key=lambda x: x["score"], reverse=True)
    return {"aliados": resultado}
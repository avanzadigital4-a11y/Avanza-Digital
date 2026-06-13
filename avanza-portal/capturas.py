"""
capturas.py — Lead magnets y bandeja "Mis Capturas" (dominio AuditoriaLog).

Séptimo router migrado de main.py (tramo 4 del split). Contiene:
  - Entrada pública de leads: /auditorias/log (Auditoría Digital) y
    /leads/capturar (endpoint centralizado de todos los lead magnets, con
    dedupe de 15 min y suscripción a MailerLite). Ambos rate-limited con el
    limiter compartido de rate_limit.py.
  - Aviso instantáneo al aliado dueño del ref_code (_notificar_captura):
    campanita + email "lead caliente".
  - Bandeja Mis Capturas del aliado: listar, marcar vistas y el puente
    Capturas → CRM (convertir-prospecto, idempotente, mismo patrón que el
    puente Bolsa → CRM de bolsa.py).
  - Métricas admin de uso de la herramienta (/admin/auditorias).

Las constantes de MailerLite (API key + group IDs por fuente) viven acá:
este módulo es el único consumidor.

Helpers compartidos de main (_get_aliado, PORTAL_URL) se acceden por import
diferido para evitar el ciclo main → capturas → main.
"""
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from auth import current_aliado_required, current_admin_required, verify_ownership_dep
from database import get_db
from models import Aliado, AuditoriaLog, Prospecto, ActividadProspecto
from notificaciones import enviar_email, notificar_aliado
from rate_limit import limiter

router = APIRouter(tags=["capturas"])


# ── Puente diferido a helpers de main (evita import circular) ────────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


# ─── MAILERLITE ───────────────────────────────────────────────────────────────
# API key v2: https://app.mailerlite.com/integrations/api/
MAILERLITE_API_KEY = os.environ.get("MAILERLITE_API_KEY", "")
# Group IDs por fuente de captura (crear en MailerLite → Subscribers → Groups)
ML_GROUP_AUDITORIA = os.environ.get("ML_GROUP_AUDITORIA", "")   # leads de la herramienta de auditoría
ML_GROUP_RECURSOS   = os.environ.get("ML_GROUP_RECURSOS",   "")  # leads de la biblioteca de recursos
ML_GROUP_GUIA       = os.environ.get("ML_GROUP_GUIA",       "")  # leads de la guía de automatización


# ─── AUDITORÍAS ──────────────────────────────────────────────────────────────

_FUENTE_LABELS = {
    "auditoria": "Auditoría Digital",
    "recursos":  "Descarga de recurso",
    "guia":      "Guía descargada",
}


def _es_dominio(s: str) -> bool:
    """True si el string parece un dominio web (logs de auditoría usan el
    campo `dominio` para el sitio auditado; los demás magnets lo usan como
    nombre de fuente: 'recursos', 'guia', etc.)."""
    s = (s or "").lower()
    return "." in s and " " not in s and s not in _FUENTE_LABELS


def _fuente_captura(log) -> str:
    """Clave de fuente normalizada de una captura: auditoria|recursos|guia|otro."""
    d = (log.dominio or "").lower()
    if d in _FUENTE_LABELS:
        return d
    if _es_dominio(d) or (log.score or 0) > 0:
        return "auditoria"
    return d or "otro"


def _notificar_captura(db, aliado: Aliado, log: AuditoriaLog):
    """Lead caliente: alguien dejó su email en un lead magnet del aliado.
    Avisa al momento por email + campanita. NO hace commit (caller decide).

    Estos leads vienen por iniciativa propia del prospecto (corrió la
    auditoría / calculadora desde el link del aliado) — mucho más calientes
    que uno de la bolsa. La velocidad de contacto acá lo es todo.
    """
    if not aliado or not log or not log.email_capturado:
        return

    fuente = _fuente_captura(log)
    es_audit = fuente == "auditoria" and _es_dominio(log.dominio)
    if es_audit:
        que_hizo = f"corrió tu <strong>Auditoría Digital</strong> sobre <strong>{log.dominio}</strong>" + (
            f" (score: <strong>{log.score}/100</strong>)" if log.score else "")
        que_hizo_txt = f"corrió tu auditoría de {log.dominio}"
    elif fuente == "recursos" or fuente == "guia":
        que_hizo = "descargó un <strong>recurso</strong> desde tu enlace"
        que_hizo_txt = "descargó un recurso con tu enlace"
    else:
        que_hizo = f"usó tu <strong>{_FUENTE_LABELS.get(fuente, 'herramienta')}</strong>"
        que_hizo_txt = "usó una de tus herramientas"

    # ── Campanita in-app ──────────────────────────────────────────────────────
    notificar_aliado(
        db, aliado.id, "captura",
        "🔥 ¡Lead caliente capturado!",
        f"{log.email_capturado} {que_hizo_txt}. Contactalo ahora desde Mis Capturas.",
        tab="capturas",
    )

    # ── Email al momento ─────────────────────────────────────────────────────
    if not aliado.email:
        return
    from main import PORTAL_URL  # diferido: const de main (evita import circular)
    nombre_corto = (aliado.nombre or "Aliado").split()[0]
    datos = []
    if log.nombre:
        datos.append(f"<tr><td style='padding:6px 12px;color:#94a3b8;'>Nombre</td><td style='padding:6px 12px;font-weight:700;'>{log.nombre}</td></tr>")
    datos.append(f"<tr><td style='padding:6px 12px;color:#94a3b8;'>Email</td><td style='padding:6px 12px;font-weight:700;'>{log.email_capturado}</td></tr>")
    if log.telefono:
        datos.append(f"<tr><td style='padding:6px 12px;color:#94a3b8;'>Teléfono</td><td style='padding:6px 12px;font-weight:700;'>{log.telefono}</td></tr>")
    if es_audit and log.dominio:
        datos.append(f"<tr><td style='padding:6px 12px;color:#94a3b8;'>Sitio auditado</td><td style='padding:6px 12px;font-weight:700;'>{log.dominio}</td></tr>")
    if es_audit and log.score:
        datos.append(f"<tr><td style='padding:6px 12px;color:#94a3b8;'>Score</td><td style='padding:6px 12px;font-weight:700;color:#f87171;'>{log.score}/100</td></tr>")

    html = f"""
    <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:36px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
      <span style="background:#1c1917;color:#fdba74;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">🔥 Lead caliente</span>
      <h2 style="margin:18px 0 10px;font-size:1.35rem;color:#fff;">{nombre_corto}, alguien acaba de usar tu enlace</h2>
      <p style="color:#a1a1aa;line-height:1.6;">Hace minutos, un prospecto {que_hizo} y dejó sus datos. <strong style="color:#fff;">Vino solo, por tu link</strong> — es el lead más caliente que existe. Llamalo AHORA, antes de que se enfríe.</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:.9rem;background:#0a0a0a;border-radius:8px;">
        {''.join(datos)}
      </table>
      <a href="{PORTAL_URL}/portal.html" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:.95rem;">Abrir Mis Capturas →</a>
      <p style="margin-top:24px;font-size:.74rem;color:#3f3f46;">Desde Mis Capturas lo pasás a tu CRM en 1 click. Avanza Digital · Partner Network.</p>
    </div>
    """
    try:
        enviar_email(
            aliado.email,
            f"🔥 {nombre_corto}: un lead acaba de usar tu enlace — llamalo ahora",
            html,
        )
    except Exception as e:
        print(f"[CAPTURAS] Falló email de aviso a {aliado.codigo}: {e}")


@router.post("/auditorias/log")
@limiter.limit("60/hour")
def log_auditoria(request: Request, dominio: str, score: int, ref_code: str = "", email: str = "", db: Session = Depends(get_db)):
    """Guarda el log cuando se genera un reporte o se captura un email.
    Si hay email + aliado dueño del ref_code → aviso inmediato (Mis Capturas)."""
    aliado = None
    aliado_id = None
    if ref_code:
        a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
        if a:
            aliado = a
            aliado_id = a.id
    
    log = AuditoriaLog(aliado_id=aliado_id, ref_code=ref_code, dominio=dominio, score=score, email_capturado=email)
    db.add(log)
    if email and aliado:
        db.flush()
        _notificar_captura(db, aliado, log)
    db.commit()
    return {"status": "ok"}


@router.post("/leads/capturar")
@limiter.limit("30/hour")
async def capturar_lead(
    request: Request,
    fuente: str,          # "auditoria" | "recursos" | "guia"
    email: str,
    nombre: str = "",
    telefono: str = "",
    recurso: str = "",    # nombre del recurso descargado (solo para fuente=recursos)
    ref_code: str = "",
    db: Session = Depends(get_db),
):
    """
    Endpoint centralizado de captura de leads desde los lead magnets del sitio.
    1. Loguea en la BD.
    2. Suscribe en MailerLite al grupo correspondiente a la fuente.
    3. Devuelve {ok: true} siempre (los errores de ML no bloquean al usuario).
    """
    import httpx

    # ── 1. Registrar en BD ────────────────────────────────────────────────────
    aliado_obj = None
    aliado_id = None
    if ref_code:
        a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
        if a:
            aliado_obj = a
            aliado_id = a.id

    # Dedupe: la auditoría llama PRIMERO a /auditorias/log (con email) y después
    # a este endpoint. Si ya existe una captura reciente con el mismo email para
    # el mismo aliado/ref, reutilizamos esa fila (le completamos nombre/teléfono)
    # en vez de duplicar la captura y el aviso al aliado.
    log = None
    if email:
        hace_15m = datetime.now() - timedelta(minutes=15)
        q = db.query(AuditoriaLog).filter(
            AuditoriaLog.email_capturado == email,
            AuditoriaLog.creado_en >= hace_15m,
        )
        if aliado_id:
            q = q.filter(AuditoriaLog.aliado_id == aliado_id)
        else:
            q = q.filter(AuditoriaLog.ref_code == (ref_code or ""))
        log = q.order_by(AuditoriaLog.creado_en.desc()).first()

    if log:
        # Fila ya creada por /auditorias/log: completar datos faltantes, sin re-avisar.
        if nombre and not log.nombre:
            log.nombre = nombre
        if telefono and not log.telefono:
            log.telefono = telefono
        db.commit()
    else:
        log = AuditoriaLog(
            aliado_id=aliado_id,
            ref_code=ref_code,
            dominio=fuente,          # reutilizamos AuditoriaLog; 'dominio' = fuente para leads no-auditoria
            score=0,
            email_capturado=email,
            nombre=nombre or None,
            telefono=telefono or None,
        )
        db.add(log)
        if email and aliado_obj:
            db.flush()
            _notificar_captura(db, aliado_obj, log)
        db.commit()

    # ── 2. Suscribir en MailerLite ────────────────────────────────────────────
    if MAILERLITE_API_KEY:
        group_map = {
            "auditoria": ML_GROUP_AUDITORIA,
            "recursos":  ML_GROUP_RECURSOS,
            "guia":      ML_GROUP_GUIA,
        }
        group_id = group_map.get(fuente, "")

        ml_payload: dict = {
            "email": email,
            "resubscribe": True,
            "fields": {},
        }
        if nombre:
            ml_payload["fields"]["name"] = nombre
        if telefono:
            ml_payload["fields"]["phone"] = telefono
        if recurso:
            ml_payload["fields"]["last_resource"] = recurso

        try:
            async with httpx.AsyncClient(timeout=8) as client:
                # Primero crear/actualizar el suscriptor
                r = await client.post(
                    "https://connect.mailerlite.com/api/subscribers",
                    headers={
                        "Authorization": f"Bearer {MAILERLITE_API_KEY}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=ml_payload,
                )
                subscriber_id = None
                if r.status_code in (200, 201):
                    data = r.json()
                    subscriber_id = data.get("data", {}).get("id")

                # Luego asignar al grupo si hay group_id
                if subscriber_id and group_id:
                    await client.post(
                        f"https://connect.mailerlite.com/api/subscribers/{subscriber_id}/groups/{group_id}",
                        headers={
                            "Authorization": f"Bearer {MAILERLITE_API_KEY}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                    )
        except Exception as e:
            print(f"[MailerLite] Error suscribiendo {email}: {e}")

    return {"ok": True}


# ─── MIS CAPTURAS (bandeja de leads de magnets, por aliado) ──────────────────

def _captura_row(log: AuditoriaLog) -> dict:
    fuente = _fuente_captura(log)
    es_audit = fuente == "auditoria"
    return {
        "id":           log.id,
        "fuente":       fuente,
        "fuente_label": _FUENTE_LABELS.get(fuente, "Lead magnet"),
        "dominio":      log.dominio if (es_audit and _es_dominio(log.dominio)) else None,
        "score":        log.score or 0,
        "email":        log.email_capturado,
        "nombre":       log.nombre,
        "telefono":     log.telefono,
        "visto":        log.visto_en is not None,
        "prospecto_id": log.prospecto_id,   # != None si ya está en el CRM
        "fecha":        log.creado_en.strftime("%d/%m/%Y %H:%M") if log.creado_en else None,
        "creado_en":    log.creado_en.isoformat() if log.creado_en else None,
    }


@router.get("/aliados/{codigo}/capturas")
def listar_capturas_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Bandeja "Mis Capturas": leads que dejaron su email en los lead magnets
    del aliado (Auditoría Digital, Calculadora, descargas) usando su ref_code.

    Son los leads más calientes del sistema: vinieron solos, por iniciativa
    propia, desde el enlace del aliado. Antes solo los veía el admin."""
    a = _get_aliado(codigo, db)
    logs = (db.query(AuditoriaLog)
              .filter(AuditoriaLog.aliado_id == a.id,
                      AuditoriaLog.email_capturado != None,
                      AuditoriaLog.email_capturado != "")
              .order_by(AuditoriaLog.creado_en.desc())
              .limit(200)
              .all())
    no_vistas = sum(1 for l in logs if l.visto_en is None)
    return {
        "no_vistas": no_vistas,
        "total": len(logs),
        "capturas": [_captura_row(l) for l in logs],
    }


@router.post("/aliados/{codigo}/capturas/marcar-vistas")
def marcar_capturas_vistas(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Marca todas las capturas del aliado como vistas (apaga el badge)."""
    a = _get_aliado(codigo, db)
    ahora = datetime.now()
    actualizadas = (db.query(AuditoriaLog)
                      .filter(AuditoriaLog.aliado_id == a.id,
                              AuditoriaLog.email_capturado != None,
                              AuditoriaLog.email_capturado != "",
                              AuditoriaLog.visto_en == None)
                      .update({AuditoriaLog.visto_en: ahora}, synchronize_session=False))
    db.commit()
    return {"ok": True, "marcadas": actualizadas}


def _crear_prospecto_desde_captura(db: Session, a: Aliado, log: AuditoriaLog) -> Prospecto:
    """Crea el Prospecto del CRM a partir de una captura de lead magnet,
    deja la actividad de sistema en el timeline y vincula la captura.
    NO hace commit (el caller decide la transacción). Mismo patrón que
    _crear_prospecto_desde_lead (puente Bolsa → CRM)."""
    fuente = _fuente_captura(log)
    es_audit = fuente == "auditoria" and _es_dominio(log.dominio)

    partes_nota = []
    if es_audit:
        partes_nota.append(f"Origen: corrió tu Auditoría Digital sobre {log.dominio}"
                           + (f" (score {log.score}/100)." if log.score else "."))
        partes_nota.append("Lead caliente: vino solo desde tu enlace. El anzuelo de la "
                           "auditoría ya hizo su trabajo — retomá la conversación desde el reporte.")
    else:
        partes_nota.append(f"Origen: {_FUENTE_LABELS.get(fuente, 'lead magnet')} desde tu enlace (captura #{log.id}).")

    nombre_p = log.nombre or (es_audit and log.dominio) or log.email_capturado or f"Captura #{log.id}"

    p = Prospecto(
        aliado_id = a.id,
        nombre    = nombre_p,
        contacto  = log.nombre or log.email_capturado or "",
        nota      = "\n".join(partes_nota),
        estado    = "sin_contactar",
        email     = log.email_capturado,
        telefono  = log.telefono,
        whatsapp  = log.telefono,
    )
    db.add(p)
    db.flush()  # necesitamos p.id antes del commit

    detalle = (f"Creado desde Mis Capturas — corrió la Auditoría Digital de {log.dominio}"
               + (f" (score {log.score}/100)" if log.score else "")
               if es_audit else
               f"Creado desde Mis Capturas — {_FUENTE_LABELS.get(fuente, 'lead magnet')}")
    db.add(ActividadProspecto(
        prospecto_id = p.id,
        aliado_id    = a.id,
        tipo         = "sistema",
        descripcion  = detalle + ".",
    ))

    log.prospecto_id = p.id
    if log.visto_en is None:
        log.visto_en = datetime.now()
    return p


@router.post("/capturas/{id}/convertir-prospecto")
def convertir_captura_en_prospecto(id: int,
                                   aliado: Aliado = Depends(current_aliado_required),
                                   db: Session = Depends(get_db)):
    """Puente Capturas → CRM: convierte una captura de lead magnet en un
    Prospecto en 1 click (reusa el mismo patrón del puente Bolsa → CRM).

    - Idempotente: si la captura ya fue convertida, devuelve el prospecto existente.
    - Deja una actividad de sistema en el timeline con el origen.
    """
    a = aliado
    log = db.query(AuditoriaLog).filter(AuditoriaLog.id == id).first()
    if not log:
        raise HTTPException(404, "Captura no encontrada.")
    if log.aliado_id != a.id:
        raise HTTPException(403, "Esta captura no es tuya.")
    if not log.email_capturado:
        raise HTTPException(400, "Esta captura no tiene datos de contacto para convertir.")

    # Idempotencia: si ya existe el prospecto vinculado, devolverlo.
    if log.prospecto_id:
        existente = db.query(Prospecto).filter(Prospecto.id == log.prospecto_id).first()
        if existente:
            return {
                "mensaje": "Esta captura ya estaba en tu CRM.",
                "prospecto_id": existente.id,
                "ya_existia": True,
            }
        log.prospecto_id = None  # el prospecto fue borrado: permitir reconvertir

    p = _crear_prospecto_desde_captura(db, a, log)
    db.commit()
    db.refresh(p)

    return {
        "mensaje": f"¡{p.nombre} ya está en tu CRM!",
        "prospecto_id": p.id,
        "ya_existia": False,
    }



@router.get("/admin/auditorias")
def admin_auditorias(db: Session = Depends(get_db),
                     _admin=Depends(current_admin_required)):
    """Métricas de uso de la herramienta para el admin."""
    logs = db.query(AuditoriaLog).all()
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    
    usos_por_aliado = {}
    for log in logs:
        if log.aliado_id:
            if log.aliado_id not in usos_por_aliado:
                usos_por_aliado[log.aliado_id] = []
            usos_por_aliado[log.aliado_id].append({
                "dominio": log.dominio,
                "score": log.score,
                "email": log.email_capturado,
                "fecha": log.creado_en.strftime("%d/%m/%Y")
            })

    resumen_aliados = []
    for a in aliados:
        historial = usos_por_aliado.get(a.id, [])
        resumen_aliados.append({
            "codigo": a.codigo,
            "nombre": a.nombre,
            "usos_totales": len(historial),
            "ultimo_uso": historial[-1]["fecha"] if historial else None,
            "historial": historial
        })
    
    return {
        "total_auditorias": len(logs),
        "aliados_activos_uso": len([a for a in resumen_aliados if a["usos_totales"] > 0]),
        "aliados_sin_uso": len([a for a in resumen_aliados if a["usos_totales"] == 0]),
        "detalle": sorted(resumen_aliados, key=lambda x: x["usos_totales"], reverse=True)
    }
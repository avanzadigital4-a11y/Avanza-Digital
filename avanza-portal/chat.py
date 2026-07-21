"""
chat.py — Mensajería de Comunidad Avanza: sala general + directos entre aliados
================================================================================

Dos superficies sobre la misma tabla `chat_mensajes` (ver models.ChatMensaje):

  1) SALA GENERAL: canal grupal abierto, cualquier aliado autenticado puede
     leer y escribir. Nace para reemplazar los grupos que los aliados ya
     arman por fuera del portal (ver hilo "Solo activos a la empresa" en
     Comunidad, donde Victor termina pasando su número de WhatsApp en un post
     público y varios aliados nuevos piden que los agreguen). A propósito NO
     está pensada como reemplazo del foro de comunidad.py — el foro sigue
     siendo el lugar para preguntas/mejoras/victorias con historial y
     búsqueda; la sala es charla en vivo, tipo grupo.

  2) DIRECTOS (DM): mensajería 1 a 1 sin sistema de "solicitudes" — cualquier
     aliado puede escribirle a cualquier otro directamente, como ya hacen por
     WhatsApp. No hay bloqueo/reporte en v1; la moderación admin ya existe
     vía `oculto`, mismo criterio que comunidad.py.

Sin websockets (igual que el resto del portal): el frontend hace polling. Los
endpoints de sala aceptan `despues_de` para traer solo mensajes nuevos.

AVISOS: los DMs usan notificar_aliado_multicanal (WhatsApp → email →
campanita/push) porque un mensaje directo es señal fuerte de que alguien
espera respuesta de una persona puntual. La SALA GENERAL, a propósito, NO
dispara WhatsApp/email por cada mensaje — sería spam para todo el grupo cada
vez que alguien escribe. Si más adelante hace falta, la opción más sana es un
resumen diario ("hubo actividad en la sala"), no un aviso por mensaje.

NO SE FILTRAN NÚMEROS DE TELÉFONO NI CONTACTOS en ningún mensaje, a propósito:
los aliados ya los comparten hoy para armar sus propios grupos y cortarles
eso empeoraría la experiencia sin resolver el problema de fondo (que es que
no tenían dónde chatear adentro del portal).
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

import schemas
from auth import current_admin_required, current_aliado_required
from database import get_db
from models import Aliado, ChatMensaje
from notificaciones import notificar_aliado_multicanal

router = APIRouter(tags=["chat"])

# Dejar la lista abierta a más salas más adelante (ej. una por equipo) sin
# tener que tocar los endpoints: alcanza con sumar el nombre acá.
SALAS_VALIDAS = ("general",)
LIMITE_SALA = 150
LIMITE_DM = 200

PORTAL_URL = (os.environ.get("PORTAL_URL", os.environ.get("BACKEND_PUBLIC_URL", ""))
              .strip().rstrip("/")) or "el portal"


def _serializar_mensaje(m: ChatMensaje) -> dict:
    return {
        "id": m.id,
        "sala": m.sala,
        "remitente_codigo": m.remitente.codigo if m.remitente else None,
        "remitente_nombre": (m.remitente.nombre.split()[0]
                             if m.remitente and m.remitente.nombre else "—"),
        "remitente_nivel": m.remitente.nivel_calculado if m.remitente else None,
        "cuerpo": m.cuerpo,
        "leido": bool(m.leido),
        "fecha": m.creado_en.strftime("%d/%m/%Y %H:%M") if m.creado_en else None,
    }


# ─── SALA GENERAL ────────────────────────────────────────────────────────────

@router.get("/chat/sala/{sala}")
def ver_sala(
    sala: str,
    despues_de: int = Query(0, description="id de mensaje: trae solo mensajes con id mayor (polling incremental)"),
    limit: int = Query(LIMITE_SALA, ge=1, le=300),
    aliado: Aliado = Depends(current_aliado_required),
    db: Session = Depends(get_db),
):
    """Mensajes de una sala grupal, del más viejo al más nuevo."""
    if sala not in SALAS_VALIDAS:
        raise HTTPException(404, "Sala inexistente.")
    q = db.query(ChatMensaje).filter(
        ChatMensaje.sala == sala, ChatMensaje.oculto == False  # noqa: E712
    )
    if despues_de:
        q = q.filter(ChatMensaje.id > despues_de)
    msgs = q.order_by(ChatMensaje.id.desc()).limit(limit).all()
    msgs.reverse()
    return {"sala": sala, "mensajes": [_serializar_mensaje(m) for m in msgs]}


@router.post("/chat/sala/{sala}/mensaje")
def enviar_mensaje_sala(
    sala: str, body: schemas.ChatMensajeIn,
    aliado: Aliado = Depends(current_aliado_required),
    db: Session = Depends(get_db),
):
    if sala not in SALAS_VALIDAS:
        raise HTTPException(404, "Sala inexistente.")
    cuerpo = body.cuerpo.strip()
    if not cuerpo:
        raise HTTPException(400, "Mensaje vacío.")

    m = ChatMensaje(sala=sala, remitente_id=aliado.id, cuerpo=cuerpo[:4000])
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"mensaje": "Enviado.", "id": m.id}


# ─── DIRECTOS (DM) ───────────────────────────────────────────────────────────

@router.get("/chat/conversaciones")
def listar_conversaciones(
    aliado: Aliado = Depends(current_aliado_required),
    db: Session = Depends(get_db),
):
    """Una fila por cada aliado con quien tengo al menos un DM, con el último
    mensaje y el conteo de no leídos. Se arma en Python (mismo criterio de
    escala que comunidad.py: "el feed sigue mostrándolos sin migración de
    datos") — agrupar "el otro" de una tabla remitente/destinatario en SQL
    puro es más lío del que el volumen actual justifica."""
    msgs = (db.query(ChatMensaje)
            .filter(ChatMensaje.sala.is_(None),
                    ChatMensaje.oculto == False,  # noqa: E712
                    or_(ChatMensaje.remitente_id == aliado.id,
                        ChatMensaje.destinatario_id == aliado.id))
            .order_by(ChatMensaje.creado_en.desc())
            .limit(1000)
            .all())

    ultimo_por_otro: dict[int, ChatMensaje] = {}
    no_leidos: dict[int, int] = {}
    for m in msgs:
        otro_id = m.destinatario_id if m.remitente_id == aliado.id else m.remitente_id
        if otro_id not in ultimo_por_otro:
            ultimo_por_otro[otro_id] = m  # ya viene ordenado desc: el primero es el más nuevo
        if m.destinatario_id == aliado.id and not m.leido:
            no_leidos[otro_id] = no_leidos.get(otro_id, 0) + 1

    otros_ids = list(ultimo_por_otro.keys())
    otros = ({a.id: a for a in db.query(Aliado).filter(Aliado.id.in_(otros_ids)).all()}
             if otros_ids else {})

    conversaciones = []
    for otro_id, ultimo in ultimo_por_otro.items():
        otro = otros.get(otro_id)
        if not otro:
            continue
        conversaciones.append({
            "codigo": otro.codigo,
            "nombre": otro.nombre.split()[0] if otro.nombre else "—",
            "nivel": otro.nivel_calculado,
            "ultimo_mensaje": ultimo.cuerpo[:120],
            "ultimo_es_mio": ultimo.remitente_id == aliado.id,
            "fecha": ultimo.creado_en.strftime("%d/%m/%Y %H:%M") if ultimo.creado_en else None,
            "no_leidos": no_leidos.get(otro_id, 0),
        })
    conversaciones.sort(key=lambda c: c["fecha"] or "", reverse=True)
    return {"conversaciones": conversaciones, "total_no_leidos": sum(no_leidos.values())}


@router.get("/chat/dm/{codigo}")
def ver_dm(
    codigo: str,
    limit: int = Query(LIMITE_DM, ge=1, le=500),
    aliado: Aliado = Depends(current_aliado_required),
    db: Session = Depends(get_db),
):
    """Historial con un aliado puntual. Abrir el chat marca como leído lo que
    me mandó (mismo criterio que cualquier app de mensajería)."""
    otro = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not otro:
        raise HTTPException(404, "Aliado no encontrado.")
    if otro.id == aliado.id:
        raise HTTPException(400, "No podés chatear con vos mismo.")

    msgs = (db.query(ChatMensaje)
            .filter(ChatMensaje.sala.is_(None),
                    ChatMensaje.oculto == False,  # noqa: E712
                    or_(and_(ChatMensaje.remitente_id == aliado.id, ChatMensaje.destinatario_id == otro.id),
                        and_(ChatMensaje.remitente_id == otro.id, ChatMensaje.destinatario_id == aliado.id)))
            .order_by(ChatMensaje.id.desc())
            .limit(limit)
            .all())
    msgs.reverse()

    pendientes = [m for m in msgs if m.destinatario_id == aliado.id and not m.leido]
    if pendientes:
        for m in pendientes:
            m.leido = True
        db.commit()

    return {
        "codigo": otro.codigo,
        "nombre": otro.nombre.split()[0] if otro.nombre else "—",
        "mensajes": [_serializar_mensaje(m) for m in msgs],
    }


@router.post("/chat/dm/{codigo}/mensaje")
def enviar_dm(
    codigo: str, body: schemas.ChatMensajeIn,
    aliado: Aliado = Depends(current_aliado_required),
    db: Session = Depends(get_db),
):
    otro = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not otro:
        raise HTTPException(404, "Aliado no encontrado.")
    if otro.id == aliado.id:
        raise HTTPException(400, "No podés escribirte a vos mismo.")

    cuerpo = body.cuerpo.strip()
    if not cuerpo:
        raise HTTPException(400, "Mensaje vacío.")

    m = ChatMensaje(remitente_id=aliado.id, destinatario_id=otro.id, cuerpo=cuerpo[:4000])
    db.add(m)

    quien = (aliado.nombre or "Alguien").split()[0]
    notificar_aliado_multicanal(
        db, otro.id, "chat_dm", f"{quien} te escribió",
        cuerpo[:140], tab="chat",
        mensaje_whatsapp=(
            f"💬 {quien} te escribió en Comunidad Avanza:\n\n"
            f"«{cuerpo[:200]}»\n\nRespondé acá: {PORTAL_URL}"
        ),
    )
    db.commit()
    db.refresh(m)
    return {"mensaje": "Enviado.", "id": m.id}


# ─── ADMIN: moderación ───────────────────────────────────────────────────────

@router.post("/admin/chat/{id}/ocultar")
def admin_ocultar_mensaje(
    id: int, ocultar: bool = True,
    db: Session = Depends(get_db),
    _admin=Depends(current_admin_required),
):
    m = db.query(ChatMensaje).filter(ChatMensaje.id == id).first()
    if not m:
        raise HTTPException(404, "Mensaje no encontrado.")
    m.oculto = ocultar
    db.commit()
    return {"mensaje": "Mensaje ocultado." if ocultar else "Mensaje visible de nuevo."}
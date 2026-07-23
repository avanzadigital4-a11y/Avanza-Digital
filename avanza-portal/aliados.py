"""
aliados.py — Entidad Aliado: helpers de identidad, serializers y gestión.

Octavo router migrado de main.py (tramo 5 del split). Contiene:
  - Invariantes de identidad del aliado: USERNAMES_RESERVADOS, validación y
    normalización de usernames/slugs, generar_ref_code y generar_codigo_aliado.
    cuenta.py los importa de acá (registro y endpoints de username).
  - Serializers canónicos _aliado_row / _aliado_detalle (los usan también el
    registro y los logins de cuenta.py).
  - Gestión admin: listar, crear (con email de credenciales), suspendidos,
    bajas voluntarias pendientes, inactivos + trigger manual del job,
    suspender/activar, eliminar en cascada con savepoints tolerantes a
    schema viejo, y cambio de nivel. Todos con Depends(current_admin_required)
    explícito además del middleware (criterio de los tramos anteriores).
  - Cuenta del aliado: /aliados/me, solicitar-baja (suspensión inmediata +
    eliminación a 30 días), ver {codigo}, Mi Red (sub-aliados + pasivo),
    método de cobro (/aliado/perfil) y CBU/alias legacy.

ORDEN DE RUTAS: /aliados/me, /aliados/suspendidos e /aliados/inactivos están
declarados ANTES que /aliados/{codigo} a propósito — comparten forma y FastAPI
matchea en orden de registro. No reordenar.

Los endpoints de credenciales (registro, logins, refresh, recuperar/resetear,
cambiar password) viven en cuenta.py; el portal público en portal_publico.py.

hash_password queda en main (tests/conftest.py lo monkeypatchea) y se accede
por puente diferido, igual que _get_aliado, _ajustar_creditos y PORTAL_URL.
"""
import os
import random
import re
import string
import sys
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

import schemas
from auth import (
    crear_token, current_aliado_required, current_admin_required,
    verify_ownership_dep,
)
from database import get_db
from models import (
    AcademiaModulo, AliadoModuloCompletado, EmailEnviado, Equipo, PasswordResetToken,
    PushSubscription, ReporteMalContacto, SolicitudCompraCreditos,
    ActividadProspecto, Aliado, AuditoriaLog, AutomationLog,
    ComentarioComunidad, Comision, ContactoProspecto, EventoUso, LeadBolsa, LinkPago,
    NIVELES, Novedad, PlanContinuidadActivo, PostComunidad,
    Prospecto, Referido, TransaccionCredito, Venta,
)
from notificaciones import ADMIN_EMAIL, enviar_email

router = APIRouter(tags=["aliados"])


# ── Puentes diferidos a helpers de main (evitan import circular) ─────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def hash_password(p):
    from main import hash_password as f  # en main: conftest lo patchea en tests
    return f(p)


def _ajustar_creditos(*args, **kwargs):
    from main import _ajustar_creditos as f
    return f(*args, **kwargs)


# ─── USERNAMES / SLUGS DE ALIADOS ────────────────────────────────────────────
# El ref_code de cada aliado se usa en URLs públicas:
#   - https://avanzadigital.digital/p/{ref_code}   (landing personal)
#   - https://avanzadigital.digital/alianzas?ref={ref_code}  (link de referido)
# Cuando el aliado elige su propio username (ej: "gonzaloasesor"),
# ese valor se guarda directamente en ref_code.

# Reservadas: nombres de rutas, áreas reservadas y términos sensibles.
# Bloqueamos también palabras tipo "admin"/"avanza" para evitar suplantación.
# NOTA: evitamos bloquear nombres comunes ("ivan", "juan", "maria") porque
# muchos aliados se llaman así. Solo bloqueamos identificadores que
# realmente impersonan a la marca o rompen el routing.
USERNAMES_RESERVADOS = frozenset({
    # rutas / áreas del sitio
    "admin", "api", "auth", "blog", "p", "alianzas", "aliados",
    "alianzas-canal1", "alianzas-canal2", "comenzar", "contratar",
    "cotizador", "demos", "descargas", "gracias", "gracias-aliado",
    "guia", "industrias", "leads-pymes", "logistica", "marketing",
    "marketing-rosario", "marketing-santa-fe", "metalurgica",
    "oil-gas", "automotriz", "agro", "clinica", "energia", "calidad",
    "tecnico", "auditoria-digital", "auditoria", "automatizar-ventas-pymes",
    "pago-unico-vs-alquiler-mensual", "politica", "portal", "recursos",
    "servicios", "sitemap", "robots", "favicon", "terminos", "terminos-aliados",
    "casos", "casos-exito", "checkout", "pago", "pagos", "checkout-success",
    "checkout-failure", "error", "404", "500", "register", "login", "signup",
    "logout", "webhook", "webhooks",
    # nombres comerciales propios — NO bloqueamos "ivan" solo, ese es nombre común
    "avanza", "avanzadigital", "avanza-digital", "ivanaranguren", "ivangalarza",
    "owner", "ceo", "fundador", "founder", "soporte",
    "support", "ayuda", "contacto", "contact",
    # genéricos peligrosos
    "test", "demo", "null", "undefined", "none", "void", "true", "false",
    "root", "system", "user", "users", "guest", "anonymous",
})

# Patrón de username permitido: 3-30 chars, lowercase, alfanumérico + guion.
# No puede empezar/terminar con guion ni tener guiones consecutivos.
_USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?!-))*[a-z0-9]$")


def normalizar_username(u: str | None) -> str:
    """Normaliza el input antes de validar: trim, lowercase, sin acentos básicos."""
    if not u:
        return ""
    u = u.strip().lower()
    # Reemplazos básicos de acentos comunes en hispanos
    repl = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", " ": "-"}
    for k, v in repl.items():
        u = u.replace(k, v)
    return u


def validar_username(u: str) -> tuple[bool, str]:
    """Valida un username candidato. Retorna (ok, mensaje_error)."""
    if not u:
        return False, "El username es obligatorio."
    if len(u) < 3:
        return False, "Mínimo 3 caracteres."
    if len(u) > 30:
        return False, "Máximo 30 caracteres."
    if not _USERNAME_RE.match(u):
        return False, "Solo letras, números y guiones. No puede empezar/terminar con guion."
    if u in USERNAMES_RESERVADOS:
        return False, "Este nombre está reservado por el sistema."
    return True, ""


def username_disponible(db: Session, username: str, excluir_aliado_id: int | None = None) -> bool:
    """Chequea unicidad del username contra la columna ref_code (case-insensitive).
    Si `excluir_aliado_id` viene, ignora a ese aliado (caso de cambio de username)."""
    q = db.query(Aliado).filter(Aliado.ref_code == username)
    if excluir_aliado_id is not None:
        q = q.filter(Aliado.id != excluir_aliado_id)
    return q.first() is None


def generar_ref_code(nombre, db=None, username: str | None = None):
    """Genera el ref_code del aliado.

    - Si `username` viene y pasa validación + está disponible, se usa tal cual.
    - Si no, se autogenera con el algoritmo legacy (nombre[:6] + 4 dígitos).
    - El parámetro `db` es opcional pero necesario para chequear unicidad
      cuando el username viene; si no viene db, el caller debe haber
      validado disponibilidad antes.
    """
    if username:
        u = normalizar_username(username)
        ok, _ = validar_username(u)
        if ok and (db is None or username_disponible(db, u)):
            return u
    # Fallback: autogenerar
    base = nombre.split()[0].lower()[:6] if nombre else "ali"
    # Limpiar acentos del base
    base = normalizar_username(base) or "ali"
    # Garantizar que el base sea solo alfanumérico (sin guiones por seguridad)
    base = "".join(c for c in base if c.isalnum()) or "ali"
    return f"{base}{''.join(random.choices(string.digits, k=4))}"


def generar_codigo_aliado(db):
    # Buscamos el último aliado creado ordenando por ID de forma descendente
    ultimo_aliado = db.query(Aliado).order_by(Aliado.id.desc()).first()
    
    if not ultimo_aliado:
        return "AL-001"
    
    try:
        # Extraemos el número del último código (ej: de "AL-020" o "AL-21" sacamos el 20 o 21)
        numero_actual = int(ultimo_aliado.codigo.split('-')[1])
        siguiente_numero = numero_actual + 1
    except (IndexError, ValueError):
        # Si por alguna razón el código anterior tiene un formato diferente, usamos su ID como base segura
        siguiente_numero = ultimo_aliado.id + 1
        
    return f"AL-{str(siguiente_numero).zfill(3)}"



def _aliado_row(a):
    return {
        "codigo": a.codigo, "nombre": a.nombre, "email": a.email,
        "whatsapp": a.whatsapp, "ciudad": a.ciudad, "perfil": a.perfil,
        "nivel": a.nivel_calculado, "ventas_6m": a.ventas_6_meses,
        "total_ganado": round(a.total_ganado, 2),
        "total_pendiente": round(a.total_pendiente, 2),
        "ref_code": a.ref_code, "fecha_firma": a.fecha_firma,
        "ultimo_login": a.ultimo_login.strftime("%d/%m/%Y %H:%M") if getattr(a, "ultimo_login", None) else "Nunca",
        "cantidad_logins": getattr(a, "cantidad_logins", 0),
        "cbu_alias": getattr(a, "cbu_alias", None),
        "payment_method": getattr(a, "payment_method", None),
        "payment_info": getattr(a, "payment_info", None),
        "payment_info_tipo": getattr(a, "payment_info_tipo", None),
        "cobro_banco": getattr(a, "cobro_banco", None),
        "cobro_titular": getattr(a, "cobro_titular", None),
        "cobro_numero_cuenta": getattr(a, "cobro_numero_cuenta", None),
        "cobro_tipo_cuenta": getattr(a, "cobro_tipo_cuenta", None),
        "pais": getattr(a, "pais", None) or "AR",
        "terminos_aceptados": bool(getattr(a, "terminos_aceptados", False)),
        "terminos_aceptados_en": a.terminos_aceptados_en.strftime("%d/%m/%Y %H:%M") if getattr(a, "terminos_aceptados_en", None) else None,
        "tipo_aliado": getattr(a, "tipo_aliado", "canal1") or "canal1",
    }

def _aliado_detalle(a, incluir_token: bool = False):
    from main import PORTAL_URL  # diferido: const de main (evita import circular)
    # MRR recurrente del aliado (10% sobre planes de continuidad activos).
    # Calculado on-the-fly: cantidad de queries baja, no vale la pena cachear.
    try:
        from sqlalchemy.orm import object_session
        sess = object_session(a)
        if sess is not None:
            activos_mrr = sess.query(PlanContinuidadActivo).filter(
                PlanContinuidadActivo.aliado_id == a.id,
                PlanContinuidadActivo.fecha_baja.is_(None),
            ).all()
            mrr_recurrente = round(sum(p.comision_mensual_usd for p in activos_mrr), 2)
        else:
            mrr_recurrente = 0.0
    except Exception:
        mrr_recurrente = 0.0

    out = {
        "codigo": a.codigo, "nombre": a.nombre, "email": a.email,
        "whatsapp": a.whatsapp, "ciudad": a.ciudad, "perfil": a.perfil,
        "nivel_actual": a.nivel, "nivel_calculado": a.nivel_calculado,
        "comision_pct": a.comision_pct * 100,
        "ventas_6m": a.ventas_6_meses, "total_ventas": len(a.ventas),
        "total_ganado": round(a.total_ganado, 2),
        "total_pendiente": round(a.total_pendiente, 2),
        "mrr_recurrente_usd": mrr_recurrente,
        "ref_code": a.ref_code,
        "link_ref":    f"{PORTAL_URL}/p/{a.ref_code}",
        "link_perfil": f"{PORTAL_URL}/p/{a.ref_code}",
        # Flag para el frontend: si es False, el aliado todavía puede
        # personalizar su slug una vez (botón "Personalizá tu link" visible).
        "username_personalizado": bool(getattr(a, "username_personalizado_en", None)),
        "portal_publico_activo": bool(getattr(a, "portal_publico_activo", True)),
        "tipo_aliado": getattr(a, "tipo_aliado", "canal1") or "canal1",
        "cbu_alias": getattr(a, "cbu_alias", None),
        "payment_method": getattr(a, "payment_method", None),
        "payment_info": getattr(a, "payment_info", None),
        "payment_info_tipo": getattr(a, "payment_info_tipo", None),
        "cobro_banco": getattr(a, "cobro_banco", None),
        "cobro_titular": getattr(a, "cobro_titular", None),
        "cobro_numero_cuenta": getattr(a, "cobro_numero_cuenta", None),
        "cobro_tipo_cuenta": getattr(a, "cobro_tipo_cuenta", None),
        "pais": getattr(a, "pais", None) or "AR",
        "terminos_aceptados": bool(getattr(a, "terminos_aceptados", False)),
        "terminos_aceptados_en": a.terminos_aceptados_en.strftime("%d/%m/%Y %H:%M") if getattr(a, "terminos_aceptados_en", None) else None,
        "referidos": [{"id": r.id, "cliente": r.nombre_cliente, "plan": r.plan_elegido,
                       "fecha": r.registrado_en.strftime("%d/%m/%Y"),
                       "confirmado": r.acuse_recibo, "convertido": r.convertido,
                       "rechazado": bool(getattr(r, "rechazado", False)),
                       "nota_admin": getattr(r, "nota_admin", None),
                       "cliente_email": (getattr(r, "email", None) or (r.prospecto.email if r.prospecto else None)),
                       "cliente_whatsapp": (getattr(r, "whatsapp", None) or (r.prospecto.whatsapp if r.prospecto else None))}
                      for r in a.referidos],
        "ventas": [{"cliente": v.nombre_cliente, "plan": v.plan,
                    "valor": v.valor_usd, "comision": v.comision_usd,
                    "pagada": v.pagada,
                    "fecha": v.fecha_venta.strftime("%d/%m/%Y") if v.fecha_venta else None}
                   for v in a.ventas if v.confirmada],
    }
    if incluir_token:
        out["token"] = crear_token(sub=a.codigo, tipo="aliado")
        out["token_tipo"] = "Bearer"
    return out


@router.get("/aliados/suspendidos")
def listar_suspendidos(db: Session = Depends(get_db),
                       _admin=Depends(current_admin_required)):
    ahora = datetime.now()
    result = []
    for a in db.query(Aliado).filter(Aliado.activo == False).all():
        row = _aliado_row(a)
        fecha_elim = getattr(a, "fecha_eliminacion_programada", None)
        row["fecha_eliminacion_programada"] = fecha_elim.isoformat() if fecha_elim else None
        row["dias_restantes_para_eliminar"] = (fecha_elim - ahora).days if fecha_elim else None
        result.append(row)
    return result


@router.get("/aliados/activos-ahora")
def aliados_activos_ahora(db: Session = Depends(get_db),
                          _admin=Depends(current_admin_required)):
    """Aliados con actividad en el portal en los últimos minutos ('en línea
    ahora'). Se apoya en `ultimo_login`, que se refresca en cada request
    autenticado del aliado (ver `registrar_actividad` en auth.py) y además
    recibe un heartbeat cada 60s desde el portal (GET /aliados/ping) mientras
    la pestaña está abierta. Ventana de 5 min = margen sobre ese heartbeat."""
    VENTANA_MIN = 5
    corte = datetime.now() - timedelta(minutes=VENTANA_MIN)
    activos = (
        db.query(Aliado)
        .filter(Aliado.activo == True, Aliado.ultimo_login != None, Aliado.ultimo_login >= corte)
        .order_by(Aliado.ultimo_login.desc())
        .all()
    )
    return {
        "cantidad": len(activos),
        "ventana_minutos": VENTANA_MIN,
        "aliados": [
            {
                "codigo": a.codigo,
                "nombre": a.nombre,
                "nivel": a.nivel,
                "hace_segundos": int((datetime.now() - a.ultimo_login).total_seconds()),
            }
            for a in activos
        ],
    }


@router.get("/admin/bajas-voluntarias")
def listar_bajas_voluntarias(db: Session = Depends(get_db),
                             _admin=Depends(current_admin_required)):
    """Lista aliados que solicitaron baja voluntaria y aún no fueron eliminados.
    Muestra los días restantes para la eliminación definitiva.
    Solo accesible para admins.
    """
    ahora = datetime.now()
    aliados = (
        db.query(Aliado)
        .filter(
            Aliado.baja_voluntaria_solicitada_en != None,
            Aliado.fecha_eliminacion_programada != None,
            Aliado.fecha_eliminacion_programada > ahora,
        )
        .order_by(Aliado.fecha_eliminacion_programada)
        .all()
    )
    result = []
    for a in aliados:
        dias_restantes = (a.fecha_eliminacion_programada - ahora).days
        row = _aliado_row(a)
        row["baja_voluntaria_solicitada_en"] = a.baja_voluntaria_solicitada_en.isoformat() if a.baja_voluntaria_solicitada_en else None
        row["fecha_eliminacion_programada"]  = a.fecha_eliminacion_programada.isoformat() if a.fecha_eliminacion_programada else None
        row["baja_voluntaria_motivo"]        = a.baja_voluntaria_motivo
        row["dias_restantes_para_eliminar"]  = dias_restantes
        result.append(row)
    return result


@router.get("/aliados/inactivos")
def aliados_inactivos(dias: int = 30, db: Session = Depends(get_db),
                      _admin=Depends(current_admin_required)):
    """Aliados sin actividad en los últimos N días. Para sistema de reactivación."""
    corte = datetime.now() - timedelta(days=dias)
    resultado = []
    for a in db.query(Aliado).filter(Aliado.activo == True).all():
        ref_rec = any(r.registrado_en >= corte for r in a.referidos)
        vta_rec = any(v.fecha_venta and v.fecha_venta >= corte for v in a.ventas if v.confirmada)
        if not ref_rec and not vta_rec:
            fechas = ([r.registrado_en for r in a.referidos] +
                      [v.fecha_venta for v in a.ventas if v.confirmada and v.fecha_venta])
            ultimo = max(fechas) if fechas else None
            resultado.append({
                "codigo": a.codigo, "nombre": a.nombre,
                "whatsapp": a.whatsapp, "email": a.email,
                "ciudad": a.ciudad, "perfil": a.perfil,
                "nivel": a.nivel_calculado,
                "dias_inactivo": (datetime.now() - ultimo).days if ultimo else None,
                "total_ganado": round(a.total_ganado, 2),
                "ventas_totales": len([v for v in a.ventas if v.confirmada]),
                "fecha_firma": a.fecha_firma,
            })
    resultado.sort(key=lambda x: x["dias_inactivo"] or 9999, reverse=True)
    return {"filtro_dias": dias, "total": len(resultado), "aliados": resultado}


@router.post("/admin/notificar-inactivos")
def notificar_inactivos_manual(background_tasks: BackgroundTasks,
                               _admin=Depends(current_admin_required)):
    """Dispara manualmente el job de notificaciones de inactividad (para pruebas desde admin)."""
    from main import job_notificaciones_inactividad  # diferido: el job vive junto al scheduler en main
    background_tasks.add_task(job_notificaciones_inactividad)
    return {"ok": True, "mensaje": "Job de inactividad lanzado en background. Revisá los logs."}


@router.get("/aliados")
def listar_aliados(db: Session = Depends(get_db),
                   _admin=Depends(current_admin_required)):
    return [_aliado_row(a) for a in db.query(Aliado).filter(Aliado.activo == True).all()]


@router.post("/aliados/crear")
def crear_aliado(background_tasks: BackgroundTasks,
                 body: schemas.CrearAliadoIn | None = Body(default=None),
                 nombre: str = "", email: str = "", whatsapp: str = "", ciudad: str = "",
                 dni: str = "", perfil: str = "", fecha_firma: str = "",
                 password: str = "avanza2026", db: Session = Depends(get_db),
                 _admin=Depends(current_admin_required)):
    """Admin crea un aliado manualmente. Acepta body JSON (preferido) o query (legacy)."""
    from main import PORTAL_URL  # diferido: const de main (evita import circular)
    if body is not None:
        nombre, email, whatsapp, ciudad = body.nombre, body.email, body.whatsapp, body.ciudad
        dni, perfil, fecha_firma = body.dni, body.perfil, body.fecha_firma
        if body.password:
            password = body.password
    if not nombre or not email or not whatsapp or not ciudad:
        raise HTTPException(400, "Faltan nombre, email, whatsapp o ciudad.")
    if db.query(Aliado).filter(Aliado.email == email).first():
        raise HTTPException(400, "Ya existe un aliado con ese email.")
    a = Aliado(
        codigo=generar_codigo_aliado(db), nombre=nombre, email=email,
        dni=dni, whatsapp=whatsapp, ciudad=ciudad, perfil=perfil,
        fecha_firma=fecha_firma or datetime.now().strftime("%d/%m/%Y"),
        ref_code=generar_ref_code(nombre), password_hash=hash_password(password),
    )
    db.add(a); db.commit(); db.refresh(a)
    _ajustar_creditos(db, a, 100, "bienvenida", "creacion_admin")
    db.commit()

    # ── Email de bienvenida al aliado con sus credenciales ─────────────────
    # El admin crea la cuenta pero el aliado nunca sabía que existía.
    # Este email le avisa que tiene acceso y le da sus datos de ingreso.
    _nombre_corto_adm = a.nombre.split()[0] if a.nombre else "Aliado"
    background_tasks.add_task(
        enviar_email,
        a.email,
        f"¡Tu cuenta Avanza Partner Network está lista, {_nombre_corto_adm}!",
        f"""
        <div style="font-family:Inter,sans-serif;background:#050505;color:#fff;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;">
          <h1 style="color:#f97316;font-size:1.6rem;margin-bottom:8px;">¡Ya sos Aliado Avanza! 🎉</h1>
          <p style="color:#a1a1aa;margin-bottom:28px;">Tu cuenta fue creada. A continuación tus datos de acceso al portal.</p>

          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:20px;margin-bottom:16px;">
            <p style="margin:0 0 4px;font-size:.85rem;color:#71717a;text-transform:uppercase;letter-spacing:1px;">Tu código de aliado</p>
            <p style="margin:0;font-size:2rem;font-weight:900;color:#f97316;letter-spacing:2px;">{a.codigo}</p>
          </div>

          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:20px;margin-bottom:24px;">
            <p style="margin:0 0 4px;font-size:.85rem;color:#71717a;text-transform:uppercase;letter-spacing:1px;">Email de acceso</p>
            <p style="margin:0 0 12px;font-size:1rem;color:#fff;">{a.email}</p>
            <p style="margin:0 0 4px;font-size:.85rem;color:#71717a;text-transform:uppercase;letter-spacing:1px;">Contraseña inicial</p>
            <p style="margin:0;font-size:1.2rem;font-weight:700;color:#86efac;letter-spacing:1px;">{password}</p>
            <p style="margin:8px 0 0;font-size:.8rem;color:#71717a;">Podés cambiarla desde tu perfil una vez que ingreses.</p>
          </div>

          <p style="color:#a1a1aa;margin-bottom:8px;">Tu comisión arranca en <strong style="color:#fff;">10% (BASIC)</strong> y sube automáticamente con cada venta.</p>

          <p style="color:#a1a1aa;margin-bottom:8px;font-size:.9rem;font-weight:700;">Tu link de ventas (para clientes):</p>
          <p style="margin-bottom:28px;font-size:.9rem;"><a href="{PORTAL_URL}/p/{a.ref_code}" style="color:#3b82f6;">{PORTAL_URL}/p/{a.ref_code}</a></p>

          <a href="{PORTAL_URL}/portal.html" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;">Ingresar al portal →</a>

          <p style="margin-top:32px;font-size:.8rem;color:#71717a;">Avanza Digital · Partner Network · Santa Fe, Argentina</p>
        </div>
        """
    )

    return {
        "mensaje": f"Aliado {a.codigo} creado", "codigo": a.codigo,
        "ref_code": a.ref_code, "password_inicial": password,
        "link_ref": f"{PORTAL_URL}/p/{a.ref_code}",
        "link_perfil": f"{PORTAL_URL}/p/{a.ref_code}",
        "email_enviado": True,
    }


# ─── ALIADOS — RUTAS CON {codigo} ────────────────────────────────────────────

@router.get("/aliados/me")
def aliado_me(aliado: Aliado = Depends(current_aliado_required), db: Session = Depends(get_db)):
    """Devuelve los datos del aliado autenticado via JWT. Usado para auto-login."""
    return _aliado_detalle(aliado)


@router.get("/aliados/ping")
def aliado_ping(aliado: Aliado = Depends(current_aliado_required)):
    """Heartbeat liviano: no hace nada más que 'tocar' la actividad del aliado
    (current_aliado_required ya refresca ultimo_login con su debounce interno).
    Lo llama portal.core.js cada 60s mientras la pestaña está abierta, para que
    el admin pueda ver quién está en el portal ahora mismo."""
    return {"ok": True}


@router.post("/aliados/me/solicitar-baja")
def solicitar_baja_voluntaria(
    body: schemas.SolicitarBajaVoluntariaIn,
    aliado: Aliado = Depends(current_aliado_required),
    db: Session  = Depends(get_db),
):
    """El aliado pide la baja de su propia cuenta desde el portal.

    Flujo:
      - La cuenta se suspende de inmediato (activo=False).
      - Se guarda la fecha de solicitud y el motivo opcional.
      - En 30 días, job_eliminacion_bajas_voluntarias la elimina definitivamente.
      - El aliado recibe un email de confirmación con instrucciones para cancelar.
      - El admin recibe un email con los datos del aliado + motivo.

    No se borra nada en este momento: el aliado tiene 30 días para arrepentirse
    contactando a soporte. Pasado ese plazo, la eliminación es irreversible.
    """
    if not aliado.activo:
        raise HTTPException(400, "Tu cuenta ya está suspendida o dada de baja.")

    ahora = datetime.now()
    aliado.activo                       = False
    aliado.baja_voluntaria_solicitada_en = ahora
    aliado.baja_voluntaria_motivo       = (body.motivo or "").strip() or None
    aliado.fecha_eliminacion_programada = ahora + timedelta(days=30)
    db.commit()

    nombre_corto = aliado.nombre.split()[0]
    motivo_txt   = aliado.baja_voluntaria_motivo or "No especificado"
    fecha_elim   = aliado.fecha_eliminacion_programada.strftime("%d/%m/%Y")

    # ── Email al aliado ──
    html_aliado = f"""
    <div style="font-family:'Inter',sans-serif;max-width:560px;margin:0 auto;background:#0a0a0a;color:#e4e4e7;padding:32px;border-radius:12px;">
      <h2 style="margin:0 0 16px;font-size:1.3rem;color:#f87171;">
        ⚠️ Solicitud de baja recibida, {nombre_corto}
      </h2>
      <p style="color:#a1a1aa;line-height:1.6;margin-bottom:16px;">
        Recibimos tu solicitud para darte de baja del programa de aliados de Avanza Digital.
        Tu acceso al portal ha sido <strong style="color:#f87171;">suspendido de inmediato</strong>.
      </p>
      <div style="background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.25);border-radius:8px;padding:16px;margin-bottom:20px;">
        <p style="margin:0;font-size:.9rem;line-height:1.6;">
          📅 <strong>Tu cuenta será eliminada definitivamente el {fecha_elim}.</strong><br>
          Hasta esa fecha, si te arrepentís, escribinos a 
          <a href="mailto:contacto@avanzadigital.digital" style="color:var(--primary,#3b82f6);">contacto@avanzadigital.digital</a>
          o por WhatsApp y reactivamos tu cuenta sin perder nada.
        </p>
      </div>
      <p style="font-size:.8rem;color:#52525b;margin-top:24px;">
        Avanza Digital · Partner Network · Si no fuiste vos quien solicitó esto, 
        respondé este email urgente.
      </p>
    </div>
    """
    try:
        enviar_email(aliado.email, f"Solicitud de baja recibida — tu cuenta se eliminará el {fecha_elim}", html_aliado)
    except Exception as e:
        print(f"[BAJA_VOLUNTARIA] No se pudo enviar email al aliado {aliado.codigo}: {e}", file=sys.stderr)

    # ── Email al admin ──
    html_admin = f"""
    <div style="font-family:'Inter',sans-serif;max-width:560px;margin:0 auto;background:#0a0a0a;color:#e4e4e7;padding:32px;border-radius:12px;">
      <h2 style="margin:0 0 16px;font-size:1.2rem;color:#fb923c;">
        🚪 Aliado solicitó baja voluntaria
      </h2>
      <table style="width:100%;font-size:.88rem;border-collapse:collapse;">
        <tr><td style="padding:6px 0;color:#a1a1aa;">Nombre:</td><td style="padding:6px 0;font-weight:700;">{aliado.nombre}</td></tr>
        <tr><td style="padding:6px 0;color:#a1a1aa;">Código:</td><td style="padding:6px 0;">{aliado.codigo}</td></tr>
        <tr><td style="padding:6px 0;color:#a1a1aa;">Email:</td><td style="padding:6px 0;">{aliado.email}</td></tr>
        <tr><td style="padding:6px 0;color:#a1a1aa;">WhatsApp:</td><td style="padding:6px 0;">{aliado.whatsapp or '—'}</td></tr>
        <tr><td style="padding:6px 0;color:#a1a1aa;">Nivel:</td><td style="padding:6px 0;">{aliado.nivel}</td></tr>
        <tr><td style="padding:6px 0;color:#a1a1aa;">Motivo:</td><td style="padding:6px 0;font-style:italic;">{motivo_txt}</td></tr>
        <tr><td style="padding:6px 0;color:#a1a1aa;">Eliminar el:</td><td style="padding:6px 0;color:#f87171;font-weight:700;">{fecha_elim}</td></tr>
      </table>
      <p style="font-size:.78rem;color:#52525b;margin-top:20px;">
        Para cancelar la baja, reactivá la cuenta desde el panel admin antes del {fecha_elim}.
      </p>
    </div>
    """
    try:
        enviar_email(ADMIN_EMAIL, f"🚪 Baja voluntaria: {aliado.nombre} ({aliado.codigo})", html_admin)
    except Exception as e:
        print(f"[BAJA_VOLUNTARIA] No se pudo enviar email admin: {e}", file=sys.stderr)

    return {
        "ok": True,
        "mensaje": f"Baja solicitada. Tu cuenta fue suspendida y será eliminada definitivamente el {fecha_elim}. Podés cancelar escribiéndonos antes de esa fecha.",
        "fecha_eliminacion": fecha_elim,
    }


@router.get("/aliados/{codigo}")
def ver_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a: raise HTTPException(404, "Aliado no encontrado.")
    return _aliado_detalle(a)


@router.post("/aliados/{codigo}/pwa-status")
def registrar_pwa_status(codigo: str, standalone: bool,
                          db: Session = Depends(get_db),
                          _owner=Depends(verify_ownership_dep)):
    """Best-effort: registra si el aliado está corriendo el portal en modo
    standalone (PWA instalada). Lo llama el frontend en cada carga de página
    (ver _esStandalone() en portal.capturas.js). No rompe nada si falla —
    es solo tracking, no bloquea el uso del portal.
    """
    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a:
        return {"ok": False}
    if standalone:
        a.pwa_instalada = True
    a.pwa_detectado_en = datetime.now()
    db.commit()
    return {"ok": True}


@router.post("/aliados/{codigo}/suspender")
def suspender_aliado(codigo: str, db: Session = Depends(get_db),
                     _admin=Depends(current_admin_required)):
    a = _get_aliado(codigo, db)
    a.activo = False
    # Igual que la suspensión automática por inactividad: si nadie la
    # reactiva antes de 60 días, el job_eliminacion_definitiva la borra
    # en cascada (mismo campo que ya usa ese job para decidir qué borrar).
    try:
        a.fecha_suspension_auto        = datetime.now()
        a.fecha_eliminacion_programada = datetime.now() + timedelta(days=60)
    except Exception:
        pass
    db.commit()
    return {"mensaje": f"{a.nombre} suspendido. Se eliminará automáticamente en 60 días si no se reactiva."}


@router.post("/aliados/{codigo}/activar")
def activar_aliado(codigo: str, db: Session = Depends(get_db),
                   _admin=Depends(current_admin_required)):
    a = _get_aliado(codigo, db)
    a.activo = True
    # Cancelar cualquier eliminación programada (por inactividad o suspensión manual)
    try:
        a.fecha_eliminacion_programada = None
        a.fecha_suspension_auto        = None
    except Exception:
        pass
    db.commit()
    return {"mensaje": f"{a.nombre} reactivado."}


@router.delete("/aliados/{codigo}/eliminar")
def eliminar_aliado(codigo: str, db: Session = Depends(get_db),
                    _admin=Depends(current_admin_required)):
    """
    Borra un aliado de forma permanente, limpiando primero TODAS las tablas
    con FK hacia aliados.id.

    Cada paso usa un savepoint independiente: si alguna tabla todavía no existe
    en la BD de producción (ProgrammingError / OperationalError), ese paso se
    descarta silenciosamente y el resto continúa — evitando que un schema
    desactualizado aborte toda la transacción (que era el ProgrammingError que
    se veía en pantalla).
    """
    a = _get_aliado(codigo, db)
    aid = a.id

    def _sp(fn):
        """Ejecuta fn() en un savepoint; si falla por tabla/columna inexistente lo ignora."""
        sp = db.begin_nested()
        try:
            fn()
            sp.commit()
        except (ProgrammingError, OperationalError) as e:
            sp.rollback()
            print(f"[eliminar_aliado] SKIP (tabla/col faltante) — {type(e).__name__}: {e}", file=sys.stderr)

    try:
        # 1) Obtener IDs auxiliares dentro de savepoints (por si las tablas no existen aún)
        prospecto_ids: list = []
        post_ids: list = []

        def _get_aux():
            nonlocal prospecto_ids, post_ids
            prospecto_ids = [r[0] for r in db.query(Prospecto.id).filter(Prospecto.aliado_id == aid).all()]
            post_ids      = [r[0] for r in db.query(PostComunidad.id).filter(PostComunidad.aliado_id == aid).all()]
        _sp(_get_aux)

        # 2) Comentarios de comunidad (hijos de posts + propios del aliado)
        if post_ids:
            _sp(lambda: db.query(ComentarioComunidad)
                .filter(ComentarioComunidad.post_id.in_(post_ids))
                .delete(synchronize_session=False))
        _sp(lambda: db.query(ComentarioComunidad)
            .filter(ComentarioComunidad.aliado_id == aid)
            .delete(synchronize_session=False))

        # 3) Posts del aliado
        _sp(lambda: db.query(PostComunidad)
            .filter(PostComunidad.aliado_id == aid)
            .delete(synchronize_session=False))

        # 4) Comisiones (depende de LinkPago y Prospecto → va antes)
        _sp(lambda: db.query(Comision)
            .filter(Comision.aliado_id == aid)
            .delete(synchronize_session=False))
        if prospecto_ids:
            _sp(lambda: db.query(Comision)
                .filter(Comision.prospecto_id.in_(prospecto_ids))
                .delete(synchronize_session=False))

        # 5) Links de pago
        _sp(lambda: db.query(LinkPago)
            .filter(LinkPago.aliado_id == aid)
            .delete(synchronize_session=False))
        if prospecto_ids:
            _sp(lambda: db.query(LinkPago)
                .filter(LinkPago.prospecto_id.in_(prospecto_ids))
                .delete(synchronize_session=False))

        # 6) Logs de automatización
        _sp(lambda: db.query(AutomationLog)
            .filter(AutomationLog.aliado_id == aid)
            .delete(synchronize_session=False))
        if prospecto_ids:
            _sp(lambda: db.query(AutomationLog)
                .filter(AutomationLog.prospecto_id.in_(prospecto_ids))
                .delete(synchronize_session=False))

        # 7) Ventas
        _sp(lambda: db.query(Venta)
            .filter(Venta.aliado_id == aid)
            .delete(synchronize_session=False))

        # 8) Referidos
        _sp(lambda: db.query(Referido)
            .filter(Referido.aliado_id == aid)
            .delete(synchronize_session=False))

        # 8.5) Hijos de prospectos — DEBEN limpiarse ANTES que los prospectos.
        #      El delete masivo del paso 9 saltea el cascade del ORM, así que las
        #      FK a nivel BD (actividades_prospecto / contactos_prospecto, ambas
        #      NOT NULL) bloquean el borrado si no se vacían primero.
        if prospecto_ids:
            _sp(lambda: db.query(ActividadProspecto)
                .filter(ActividadProspecto.prospecto_id.in_(prospecto_ids))
                .delete(synchronize_session=False))
            _sp(lambda: db.query(ContactoProspecto)
                .filter(ContactoProspecto.prospecto_id.in_(prospecto_ids))
                .delete(synchronize_session=False))
            # Referencias nullable a estos prospectos → desvincular (preservar fila)
            _sp(lambda: db.query(Novedad)
                .filter(Novedad.prospecto_id.in_(prospecto_ids))
                .update({Novedad.prospecto_id: None}, synchronize_session=False))
            _sp(lambda: db.query(AuditoriaLog)
                .filter(AuditoriaLog.prospecto_id.in_(prospecto_ids))
                .update({AuditoriaLog.prospecto_id: None}, synchronize_session=False))
            _sp(lambda: db.query(Referido)
                .filter(Referido.prospecto_id.in_(prospecto_ids))
                .update({Referido.prospecto_id: None}, synchronize_session=False))

        # 9) Prospectos
        _sp(lambda: db.query(Prospecto)
            .filter(Prospecto.aliado_id == aid)
            .delete(synchronize_session=False))

        # 10) Transacciones de créditos
        _sp(lambda: db.query(TransaccionCredito)
            .filter(TransaccionCredito.aliado_id == aid)
            .delete(synchronize_session=False))

        # 11) Auditoría: preservar con aliado_id = NULL
        _sp(lambda: db.query(AuditoriaLog)
            .filter(AuditoriaLog.aliado_id == aid)
            .update({AuditoriaLog.aliado_id: None}, synchronize_session=False))

        # 12) Bolsa de leads: liberar → vuelven a estar disponibles
        _sp(lambda: db.query(LeadBolsa)
            .filter(LeadBolsa.aliado_id == aid)
            .update({
                LeadBolsa.aliado_id: None,
                LeadBolsa.estado: "disponible",
                LeadBolsa.fecha_reclamo: None,
            }, synchronize_session=False))

        # 13) Sub-aliados: solo desvincular sponsor, no borrar
        _sp(lambda: db.query(Aliado)
            .filter(Aliado.sponsor_id == aid)
            .update({Aliado.sponsor_id: None}, synchronize_session=False))

        # 13.5) Resto de FKs directas a aliados.id que faltaban limpiar.
        #       Sin estas, el commit final disparaba ForeignKeyViolation
        #       (p.ej. emails_enviados, equipos, password_reset_tokens...).
        # emails_enviados: preservar métricas de campaña → solo desvincular (col nullable)
        _sp(lambda: db.query(EmailEnviado)
            .filter(EmailEnviado.aliado_id == aid)
            .update({EmailEnviado.aliado_id: None}, synchronize_session=False))
        # Equipos setter/closer: el aliado puede ser A o B → borrar el vínculo
        _sp(lambda: db.query(Equipo)
            .filter((Equipo.aliado_a_id == aid) | (Equipo.aliado_b_id == aid))
            .delete(synchronize_session=False))
        # Tablas NOT NULL (no se pueden nulear → se borran):
        _sp(lambda: db.query(Novedad)
            .filter(Novedad.aliado_id == aid)
            .delete(synchronize_session=False))
        _sp(lambda: db.query(ReporteMalContacto)
            .filter(ReporteMalContacto.aliado_id == aid)
            .delete(synchronize_session=False))
        _sp(lambda: db.query(AliadoModuloCompletado)
            .filter(AliadoModuloCompletado.aliado_id == aid)
            .delete(synchronize_session=False))
        _sp(lambda: db.query(SolicitudCompraCreditos)
            .filter(SolicitudCompraCreditos.aliado_id == aid)
            .delete(synchronize_session=False))
        _sp(lambda: db.query(PlanContinuidadActivo)
            .filter(PlanContinuidadActivo.aliado_id == aid)
            .delete(synchronize_session=False))
        _sp(lambda: db.query(PasswordResetToken)
            .filter(PasswordResetToken.aliado_id == aid)
            .delete(synchronize_session=False))
        _sp(lambda: db.query(PushSubscription)
            .filter(PushSubscription.aliado_id == aid)
            .delete(synchronize_session=False))
        # eventos_uso: tracking de uso del portal (col nullable) → solo desvincular,
        # preserva las estadísticas agregadas de qué se usa en el portal.
        _sp(lambda: db.query(EventoUso)
            .filter(EventoUso.aliado_id == aid)
            .update({EventoUso.aliado_id: None}, synchronize_session=False))

        # 14) Por fin: el aliado mismo
        db.delete(a)
        db.commit()
        return {"mensaje": f"{codigo} eliminado permanentemente."}

    except Exception as e:
        db.rollback()
        print(f"[eliminar_aliado] ERROR borrando {codigo}: {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo eliminar al aliado: {type(e).__name__}: {str(e)[:200]}"
        )


@router.patch("/aliados/{codigo}/nivel")
def cambiar_nivel(codigo: str,
                  body: schemas.CambiarNivelIn | None = Body(default=None),
                  nivel: str = "",
                  db: Session = Depends(get_db),
                  _admin=Depends(current_admin_required)):
    """Admin cambia el nivel de un aliado. (Protegido por middleware admin.)"""
    if body is not None:
        nivel = body.nivel
    if nivel not in NIVELES:
        raise HTTPException(400, f"Nivel inválido. Opciones: {list(NIVELES.keys())}")
    a = _get_aliado(codigo, db)
    anterior = a.nivel; a.nivel = nivel; db.commit()
    return {"mensaje": f"{a.nombre}: {anterior} → {nivel}", "comision": f"{NIVELES[nivel]['comision']*100:.0f}%"}


_MESES_ABBR = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _claves_ultimos_n_meses(n: int, hoy: datetime) -> list[str]:
    """Devuelve las últimas `n` claves 'YYYY-MM' en orden cronológico (más vieja
    primero), terminando en el mes de `hoy`. Sirve para que el histórico siempre
    muestre `n` barras aunque algunos meses no tengan ventas."""
    claves = []
    y, m = hoy.year, hoy.month
    for _ in range(n):
        claves.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(claves))


@router.get("/aliados/{codigo}/red")
def mi_red_comercial(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    # Nota: Mi Red está disponible para Canal 1 y Canal 2 por igual. El override
    # pasivo del sponsor (checkout.py / main.py / comisiones.py) nunca distinguió
    # canal al generarse — un aliado Canal 2 que recluta sub-aliados ya cobraba
    # ese override aunque antes no pudiera verlo acá (era solo una restricción de
    # visibilidad en este endpoint, no del cálculo de comisiones).
    red = []
    total_pasivo = 0
    suma_override_pct = 0.0

    # Ventana móvil: un sub cuenta como "activado" si ingresó en los últimos N días.
    DIAS_VENTANA_ACTIVO = int(os.environ.get("DIAS_VENTANA_ACTIVO", "7"))
    corte_activo = datetime.now() - timedelta(days=DIAS_VENTANA_ACTIVO)

    # Total de módulos activos de la Academia, para el progreso por sub-aliado
    # (una sola query fuera del loop, no por cada sub-aliado). También se manda
    # el listado completo (id/orden/título) para que el sponsor pueda ver el
    # detalle módulo por módulo de cada sub-aliado sin pegarle al backend de nuevo.
    modulos_academia = (
        db.query(AcademiaModulo)
        .filter(AcademiaModulo.activo == True)
        .order_by(AcademiaModulo.orden.asc())
        .all()
    )
    total_modulos_academia = len(modulos_academia)

    # Histórico mensual (últimos 6 meses) de ventas de la red, para el mini
    # gráfico de tendencia. Se arma en base a las mismas comisiones "RED:" que
    # ya se usan para calcular la ganancia pasiva de cada sub-aliado — no es
    # una query nueva, solo se le suma fecha_venta a lo que ya se recorre.
    HOY = datetime.now()
    claves_historico = _claves_ultimos_n_meses(6, HOY)
    historico = {k: {"ventas": 0, "ganancia": 0.0} for k in claves_historico}

    sub_aliados = getattr(a, "sub_aliados", [])
    for sub in sub_aliados:
        # Calcular cuánta plata generó este sub-aliado (y de paso, alimentar el histórico mensual)
        ventas_red_obj = [v for v in a.ventas if f"RED: {sub.nombre}" in v.nombre_cliente]
        ganancia = sum(v.comision_usd for v in ventas_red_obj)
        total_pasivo += ganancia

        for v in ventas_red_obj:
            if v.fecha_venta:
                clave = v.fecha_venta.strftime("%Y-%m")
                if clave in historico:
                    historico[clave]["ventas"] += 1
                    historico[clave]["ganancia"] += v.comision_usd

        fecha_ing = "Reciente"
        if getattr(sub, "creado_en", None):
            fecha_ing = sub.creado_en.strftime("%d/%m/%Y")
        elif getattr(sub, "fecha_firma", None):
            fecha_ing = sub.fecha_firma

        # Estado de actividad para que el sponsor siga a cada invitado.
        logins = int(getattr(sub, "cantidad_logins", 0) or 0)
        ventas_6m = int(getattr(sub, "ventas_6_meses", 0) or 0)
        ult = getattr(sub, "ultimo_login", None)

        # ¿Ingresó alguna vez? ¿Ingresó dentro de la ventana móvil?
        entro_alguna_vez = (ult is not None) or (logins >= 1)
        try:
            activo_reciente = (ult is not None) and (ult >= corte_activo)
        except TypeError:
            # Defensa por si ult viniera tz-aware: compará en naive.
            activo_reciente = (ult is not None) and (ult.replace(tzinfo=None) >= corte_activo)

        if not entro_alguna_vez:
            estado = "sin_activar"          # se registró pero nunca ingresó
        elif not activo_reciente:
            estado = "inactivo"            # ingresó antes, pero hace +N días que no vuelve
        elif ventas_6m == 0:
            estado = "activado_sin_vender"  # activo y sin ventas todavía
        else:
            estado = "vendiendo"            # activo y generando ventas

        ultimo_login_fmt = ult.strftime("%d/%m/%Y") if ult else "Nunca"

        # Ventas propias (de por vida, no ventana de 6m) y % de override que
        # ESTE aliado (sponsor) cobra por ellas — ver Aliado.override_pct_para_sponsor.
        ventas_propias = sub.ventas_propias_count
        override_pct = round(sub.override_pct_para_sponsor * 100, 1)
        override_pct = int(override_pct) if override_pct == int(override_pct) else override_pct

        # Progreso de Academia del sub-aliado (módulos completados / total activos).
        # Además del conteo, mandamos los IDs puntuales completados para que el
        # sponsor pueda ver el detalle módulo por módulo (qué le falta) y sepa
        # a cuál empujar primero en vez de adivinar.
        modulos_completados_ids = [
            row.modulo_id for row in
            db.query(AliadoModuloCompletado.modulo_id).filter(
                AliadoModuloCompletado.aliado_id == sub.id
            ).all()
        ]
        academia_completados = len(modulos_completados_ids)

        suma_override_pct += override_pct

        red.append({
            "nombre": sub.nombre,
            "ciudad": sub.ciudad or "Sin especificar",
            "nivel": sub.nivel_calculado,
            "fecha_ingreso": fecha_ing,
            "cantidad_logins": logins,
            "ultimo_login": ultimo_login_fmt,
            "activado": activo_reciente,
            "entro_alguna_vez": entro_alguna_vez,
            "ventas_6m": ventas_6m,
            "ventas_count": ventas_propias,
            "override_pct": override_pct,
            "academia_completados": academia_completados,
            "academia_total": total_modulos_academia,
            "academia_modulos_completados": modulos_completados_ids,
            "estado": estado,
            "whatsapp": (getattr(sub, "whatsapp", "") or ""),
            "ganancia_pasiva": round(ganancia, 2)
        })

    red.sort(key=lambda x: x["ganancia_pasiva"], reverse=True)

    total = len(red)
    activados = sum(1 for s in red if s["activado"])
    inactivos = sum(1 for s in red if s["estado"] == "inactivo")
    nunca_entro = sum(1 for s in red if s["estado"] == "sin_activar")
    vendiendo = sum(1 for s in red if s["estado"] == "vendiendo")

    # Override promedio: da una foto rápida de en qué tier está parada la red
    # en general (calculado en backend, no en frontend, mismo criterio que
    # override_pct por sub-aliado — para evitar inconsistencias).
    override_promedio = round(suma_override_pct / total, 1) if total else 0
    override_promedio = int(override_promedio) if override_promedio == int(override_promedio) else override_promedio

    historico_mensual = [
        {
            "mes": clave,
            "label": _MESES_ABBR[int(clave.split("-")[1]) - 1],
            "ventas": historico[clave]["ventas"],
            "ganancia": round(historico[clave]["ganancia"], 2),
        }
        for clave in claves_historico
    ]

    return {
        "sponsor": getattr(a, "sponsor").nombre if getattr(a, "sponsor", None) else None,
        "total_sub_aliados": total,
        "activados": activados,
        "inactivos": inactivos,
        "sin_activar": nunca_entro,
        "vendiendo": vendiendo,
        "ventana_dias": DIAS_VENTANA_ACTIVO,
        "total_ganancia_pasiva": round(total_pasivo, 2),
        "override_promedio": override_promedio,
        "historico_mensual": historico_mensual,
        "academia_modulos": [
            {"id": m.id, "orden": m.orden, "titulo": m.titulo} for m in modulos_academia
        ],
        "reclutamiento": {
            "clics": int(getattr(a, "clics_reclutamiento", 0) or 0),
            "registros": total,
            # Conversión registro → activación (no clic → registro): de los que
            # se registraron en tu red, qué % llegó a activarse (entrar al portal).
            "tasa_activacion": round((activados / total) * 100) if total else None,
        },
        "detalle": red
    }



# ─── CBU / ALIAS DEL ALIADO (spec §11) ───────────────────────────────────────
#
# v2.6 — Mejoras a métodos de cobro (LatAm). Los 5 métodos existentes se
# mantienen (`usdt_trc20`, `airtm`, `wise`, `transferencia`, `payoneer`), pero
# cada uno ahora pide y valida el dato que realmente lo identifica:
#   - transferencia: banco + titular + tipo de cuenta + número, en las
#     columnas `cobro_*`, validado según el formato del país del aliado
#     (`FORMATOS_CUENTA_POR_PAIS`).
#   - usdt_trc20: dirección de wallet en `payment_info` (regex TRC20).
#   - wise: `payment_info` + `payment_info_tipo` ("email"/"telefono"/"wisetag"),
#     porque Wise identifica destinatarios por cualquiera de los tres.
#   - payoneer / airtm: email en `payment_info` (ambos solo documentan email
#     como identificador de cuenta, a diferencia de Wise).
# Se sigue pagando 100% manual (un admin transfiere leyendo estos datos); no
# hay integración real con Wise/Payoneer/Airtm/Mercado Pago (ver
# mejoras-metodos-cobro.md, sección "Por qué NO automatizamos").

FORMATOS_CUENTA_POR_PAIS = {
    "AR": {"label": "CBU o alias",         "min_len": 6,  "max_len": 22, "solo_numeros": False},
    "MX": {"label": "CLABE",               "min_len": 18, "max_len": 18, "solo_numeros": True},
    "CO": {"label": "N° de cuenta",        "min_len": 8,  "max_len": 20, "solo_numeros": True},
    "PE": {"label": "CCI",                 "min_len": 20, "max_len": 20, "solo_numeros": True},
    "CL": {"label": "N° de cuenta",        "min_len": 6,  "max_len": 20, "solo_numeros": True},
    # completar según prioridad real (ver consultas en mejoras-metodos-cobro.md,
    # sección "Próximo paso antes de programar"): países sin entrada acá caen
    # en "_default", un campo de texto libre con validación mínima de largo.
    "_default": {"label": "N° de cuenta bancaria", "min_len": 4, "max_len": 30, "solo_numeros": False},
}

TIPOS_CUENTA_VALIDOS = {"ahorro", "corriente", "vista", "nomina", "otra"}

_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RE_TELEFONO_INTL = re.compile(r"^\+[1-9]\d{7,14}$")   # formato internacional: +código país + número
_RE_WISETAG = re.compile(r"^@\S+$")

# Alfabeto base58 (Bitcoin/Tron): sin 0, O, I, l para evitar confusión visual.
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_RE_USDT_TRC20_FORMATO = re.compile(rf"^T[{_BASE58_ALPHABET}]{{33}}$")


def _validar_usdt_trc20(direccion: str) -> None:
    """Valida formato TRC20: empieza con T, 34 caracteres, alfabeto base58.

    No hace validación de checksum criptográfico completa (requeriría
    replicar el algoritmo de decodificación base58check de Tron); valida
    longitud y alfabeto, que cubre el error más común (typos, pegar una
    dirección de otra red, caracteres inválidos)."""
    if not _RE_USDT_TRC20_FORMATO.match(direccion):
        raise HTTPException(
            400,
            "Dirección USDT TRC20 inválida. Debe empezar con 'T' y tener 34 "
            "caracteres (verificá que sea red TRC20, no ERC20 ni BEP20).",
        )


def _validar_email(valor: str, contexto: str) -> None:
    if not _RE_EMAIL.match(valor):
        raise HTTPException(400, f"Email inválido para {contexto}.")


def _validar_wise_info(valor: str, tipo: str | None) -> None:
    tipo = (tipo or "").strip().lower()
    if tipo == "email":
        _validar_email(valor, "Wise")
    elif tipo == "telefono":
        if not _RE_TELEFONO_INTL.match(valor):
            raise HTTPException(400, "Teléfono de Wise inválido. Formato internacional, ej: +5491122334455.")
    elif tipo == "wisetag":
        if not _RE_WISETAG.match(valor):
            raise HTTPException(400, "Wisetag inválido. Debe empezar con '@' y no tener espacios.")
    else:
        raise HTTPException(400, "Para Wise indicá payment_info_tipo: 'email', 'telefono' o 'wisetag'.")


def _validar_transferencia(pais: str, banco: str | None, titular: str | None,
                            tipo_cuenta: str | None, numero: str | None) -> None:
    if not (banco and titular and numero):
        raise HTTPException(400, "Para transferencia bancaria completá banco, titular y número de cuenta.")
    if tipo_cuenta and tipo_cuenta.strip().lower() not in TIPOS_CUENTA_VALIDOS:
        raise HTTPException(400, f"Tipo de cuenta no válido. Opciones: {', '.join(sorted(TIPOS_CUENTA_VALIDOS))}")

    fmt = FORMATOS_CUENTA_POR_PAIS.get((pais or "").strip().upper(), FORMATOS_CUENTA_POR_PAIS["_default"])
    numero_limpio = numero.strip()
    if fmt["solo_numeros"] and not numero_limpio.isdigit():
        raise HTTPException(400, f"{fmt['label']} debe contener solo números.")
    if not (fmt["min_len"] <= len(numero_limpio) <= fmt["max_len"]):
        raise HTTPException(
            400,
            f"{fmt['label']} debe tener entre {fmt['min_len']} y {fmt['max_len']} caracteres "
            f"(tiene {len(numero_limpio)}).",
        )


class PerfilAliadoUpdate(BaseModel):
    cbu_alias: str | None = None
    payment_method: str | None = None
    payment_info: str | None = None
    # "email" | "telefono" | "wisetag" — solo aplica cuando payment_method == "wise"
    payment_info_tipo: str | None = None
    # Solo aplican cuando payment_method == "transferencia"
    cobro_banco: str | None = None
    cobro_titular: str | None = None
    cobro_numero_cuenta: str | None = None
    cobro_tipo_cuenta: str | None = None

@router.patch("/aliado/perfil")
def actualizar_perfil_aliado(payload: PerfilAliadoUpdate,
                              aliado: Aliado = Depends(current_aliado_required),
                              db: Session = Depends(get_db)):
    """Actualiza el método de cobro del aliado autenticado.

    Acepta payment_method + payment_info (nuevo, internacional) o cbu_alias (legacy).
    SECURITY: Toma el aliado del JWT, ya NO acepta `?codigo=` como parámetro
    (era una via de hijack del CBU para redirigir comisiones).

    Cada método pide y valida el dato correcto (ver sección 3 de
    mejoras-metodos-cobro.md) en vez de un único campo de texto libre:
      - transferencia → cobro_banco/cobro_titular/cobro_tipo_cuenta/cobro_numero_cuenta,
        validados según el formato del país del aliado.
      - usdt_trc20 → payment_info = dirección de wallet (regex TRC20).
      - wise → payment_info + payment_info_tipo (email/telefono/wisetag).
      - payoneer / airtm → payment_info = email.
    """
    VALID_METHODS = {"usdt_trc20", "airtm", "wise", "transferencia", "payoneer"}

    method = None
    if payload.payment_method is not None:
        method = (payload.payment_method or "").strip().lower()
        if method and method not in VALID_METHODS:
            raise HTTPException(400, f"Método no válido. Opciones: {', '.join(VALID_METHODS)}")
    else:
        method = (getattr(aliado, "payment_method", None) or "").strip().lower() or None

    # ── Validaciones específicas por método ──────────────────────────────
    if method == "usdt_trc20" and payload.payment_info is not None:
        info = (payload.payment_info or "").strip()
        if info:
            _validar_usdt_trc20(info)

    elif method == "wise" and payload.payment_info is not None:
        info = (payload.payment_info or "").strip()
        if info:
            _validar_wise_info(info, payload.payment_info_tipo)

    elif method in ("payoneer", "airtm") and payload.payment_info is not None:
        info = (payload.payment_info or "").strip()
        if info:
            _validar_email(info, "payoneer" if method == "payoneer" else "Airtm")

    elif method == "transferencia":
        # Toma valores nuevos si vienen en el payload, si no los ya guardados,
        # para poder validar el conjunto completo aunque el request solo
        # actualice un subconjunto de campos.
        banco = payload.cobro_banco if payload.cobro_banco is not None else getattr(aliado, "cobro_banco", None)
        titular = payload.cobro_titular if payload.cobro_titular is not None else getattr(aliado, "cobro_titular", None)
        tipo_cuenta = payload.cobro_tipo_cuenta if payload.cobro_tipo_cuenta is not None else getattr(aliado, "cobro_tipo_cuenta", None)
        numero = payload.cobro_numero_cuenta if payload.cobro_numero_cuenta is not None else getattr(aliado, "cobro_numero_cuenta", None)
        if any([payload.cobro_banco, payload.cobro_titular, payload.cobro_numero_cuenta, payload.cobro_tipo_cuenta]):
            _validar_transferencia(getattr(aliado, "pais", None) or "AR", banco, titular, tipo_cuenta, numero)

    # ── Persistir campos ──────────────────────────────────────────────────
    if payload.payment_method is not None:
        setattr(aliado, "payment_method", method or None)

    if payload.payment_info is not None:
        setattr(aliado, "payment_info", (payload.payment_info or "").strip()[:300] or None)

    if payload.payment_info_tipo is not None:
        tipo = (payload.payment_info_tipo or "").strip().lower()
        setattr(aliado, "payment_info_tipo", tipo or None)

    if payload.cobro_banco is not None:
        setattr(aliado, "cobro_banco", payload.cobro_banco.strip()[:120] or None)
    if payload.cobro_titular is not None:
        setattr(aliado, "cobro_titular", payload.cobro_titular.strip()[:120] or None)
    if payload.cobro_numero_cuenta is not None:
        setattr(aliado, "cobro_numero_cuenta", payload.cobro_numero_cuenta.strip()[:60] or None)
    if payload.cobro_tipo_cuenta is not None:
        setattr(aliado, "cobro_tipo_cuenta", (payload.cobro_tipo_cuenta or "").strip().lower()[:20] or None)

    # cbu_alias: mantener por compatibilidad con admin y endpoints legacy
    if payload.cbu_alias is not None:
        aliado.cbu_alias = payload.cbu_alias.strip()[:300] or None
    elif method == "transferencia" and any([payload.cobro_banco, payload.cobro_titular, payload.cobro_numero_cuenta]):
        # Auto-generar cbu_alias legible para el admin a partir de los campos estructurados
        partes = [p for p in [
            getattr(aliado, "cobro_banco", None),
            getattr(aliado, "cobro_titular", None) and f"Titular: {getattr(aliado, 'cobro_titular', None)}",
            getattr(aliado, "cobro_tipo_cuenta", None),
            getattr(aliado, "cobro_numero_cuenta", None),
        ] if p]
        aliado.cbu_alias = f"[Transf. bancaria] {' · '.join(partes)}"[:300] or None
    elif payload.payment_method and payload.payment_info:
        # Auto-generar cbu_alias legible para el admin
        labels = {"usdt_trc20":"USDT TRC20","airtm":"Airtm","wise":"Wise","transferencia":"Transf. bancaria","payoneer":"Payoneer"}
        label = labels.get((payload.payment_method or "").lower(), payload.payment_method or "")
        aliado.cbu_alias = f"[{label}] {(payload.payment_info or '').strip()}"[:300] or None

    db.commit()
    return {
        "mensaje": "Perfil actualizado.",
        "cbu_alias": aliado.cbu_alias,
        "payment_method": getattr(aliado, "payment_method", None),
        "payment_info": getattr(aliado, "payment_info", None),
        "payment_info_tipo": getattr(aliado, "payment_info_tipo", None),
        "cobro_banco": getattr(aliado, "cobro_banco", None),
        "cobro_titular": getattr(aliado, "cobro_titular", None),
        "cobro_numero_cuenta": getattr(aliado, "cobro_numero_cuenta", None),
        "cobro_tipo_cuenta": getattr(aliado, "cobro_tipo_cuenta", None),
    }


@router.patch("/aliados/{codigo}/cbu")
def actualizar_cbu(codigo: str,
                   body: schemas.ActualizarCBUIn | None = Body(default=None),
                   cbu_alias: str = "",
                   db: Session = Depends(get_db),
                   _owner=Depends(verify_ownership_dep)):
    """Alias alternativo para actualizar CBU. Acepta body o query (compat).
    Protegido con ownership: solo el dueño del JWT (o un admin) puede tocar
    este aliado. CRÍTICO — afecta a dónde se cobran las comisiones."""
    a = _get_aliado(codigo, db)
    nuevo = body.cbu_alias if body is not None else cbu_alias
    a.cbu_alias = (nuevo or "").strip()[:120] or None
    db.commit()
    return {"mensaje": "CBU/alias guardado.", "cbu_alias": a.cbu_alias}
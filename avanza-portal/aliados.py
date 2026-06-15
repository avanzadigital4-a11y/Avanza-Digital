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
    ActividadProspecto, Aliado, AuditoriaLog, AutomationLog,
    ComentarioComunidad, Comision, ContactoProspecto, LeadBolsa, LinkPago,
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
        "terminos_aceptados": bool(getattr(a, "terminos_aceptados", False)),
        "terminos_aceptados_en": a.terminos_aceptados_en.strftime("%d/%m/%Y %H:%M") if getattr(a, "terminos_aceptados_en", None) else None,
        "referidos": [{"cliente": r.nombre_cliente, "plan": r.plan_elegido,
                       "fecha": r.registrado_en.strftime("%d/%m/%Y"),
                       "confirmado": r.acuse_recibo, "convertido": r.convertido}
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
    return [_aliado_row(a) for a in db.query(Aliado).filter(Aliado.activo == False).all()]


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
          <a href="mailto:avanzadigital4@gmail.com" style="color:var(--primary,#3b82f6);">avanzadigital4@gmail.com</a>
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


@router.post("/aliados/{codigo}/suspender")
def suspender_aliado(codigo: str, db: Session = Depends(get_db),
                     _admin=Depends(current_admin_required)):
    a = _get_aliado(codigo, db)
    a.activo = False; db.commit()
    return {"mensaje": f"{a.nombre} suspendido."}


@router.post("/aliados/{codigo}/activar")
def activar_aliado(codigo: str, db: Session = Depends(get_db),
                   _admin=Depends(current_admin_required)):
    a = _get_aliado(codigo, db)
    a.activo = True; db.commit()
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


@router.get("/aliados/{codigo}/red")
def mi_red_comercial(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    if (getattr(a, "tipo_aliado", "canal1") or "canal1") == "canal2":
        raise HTTPException(403, "Mi Red no está disponible para aliados Canal 2.")
    red = []
    total_pasivo = 0

    sub_aliados = getattr(a, "sub_aliados", [])
    for sub in sub_aliados:
        # Calcular cuánta plata generó este sub-aliado
        ventas_red = [v.comision_usd for v in a.ventas if f"RED: {sub.nombre}" in v.nombre_cliente]
        ganancia = sum(ventas_red)
        total_pasivo += ganancia
        
        fecha_ing = "Reciente"
        if getattr(sub, "creado_en", None):
            fecha_ing = sub.creado_en.strftime("%d/%m/%Y")
        elif getattr(sub, "fecha_firma", None):
            fecha_ing = sub.fecha_firma

        # Estado de actividad para que el sponsor siga a cada invitado.
        logins = int(getattr(sub, "cantidad_logins", 0) or 0)
        ventas_6m = int(getattr(sub, "ventas_6_meses", 0) or 0)
        if logins == 0:
            estado = "sin_activar"          # se registró pero nunca ingresó
        elif ventas_6m == 0:
            estado = "activado_sin_vender"  # ya ingresó pero todavía no vendió
        else:
            estado = "vendiendo"            # ingresó y ya genera ventas

        ult = getattr(sub, "ultimo_login", None)
        ultimo_login_fmt = ult.strftime("%d/%m/%Y") if ult else "Nunca"

        red.append({
            "nombre": sub.nombre,
            "ciudad": sub.ciudad or "Sin especificar",
            "nivel": sub.nivel_calculado,
            "fecha_ingreso": fecha_ing,
            "cantidad_logins": logins,
            "ultimo_login": ultimo_login_fmt,
            "activado": logins >= 1,
            "ventas_6m": ventas_6m,
            "estado": estado,
            "whatsapp": (getattr(sub, "whatsapp", "") or ""),
            "ganancia_pasiva": round(ganancia, 2)
        })

    red.sort(key=lambda x: x["ganancia_pasiva"], reverse=True)

    total = len(red)
    activados = sum(1 for s in red if s["activado"])
    vendiendo = sum(1 for s in red if s["estado"] == "vendiendo")

    return {
        "sponsor": getattr(a, "sponsor").nombre if getattr(a, "sponsor", None) else None,
        "total_sub_aliados": total,
        "activados": activados,
        "sin_activar": total - activados,
        "vendiendo": vendiendo,
        "total_ganancia_pasiva": round(total_pasivo, 2),
        "detalle": red
    }



# ─── CBU / ALIAS DEL ALIADO (spec §11) ───────────────────────────────────────

class PerfilAliadoUpdate(BaseModel):
    cbu_alias: str | None = None
    payment_method: str | None = None
    payment_info: str | None = None

@router.patch("/aliado/perfil")
def actualizar_perfil_aliado(payload: PerfilAliadoUpdate,
                              aliado: Aliado = Depends(current_aliado_required),
                              db: Session = Depends(get_db)):
    """Actualiza el método de cobro del aliado autenticado.

    Acepta payment_method + payment_info (nuevo, internacional) o cbu_alias (legacy).
    SECURITY: Toma el aliado del JWT, ya NO acepta `?codigo=` como parámetro
    (era una via de hijack del CBU para redirigir comisiones).
    """
    VALID_METHODS = {"usdt_trc20", "airtm", "wise", "transferencia", "payoneer"}

    if payload.payment_method is not None:
        method = (payload.payment_method or "").strip().lower()
        if method and method not in VALID_METHODS:
            raise HTTPException(400, f"Método no válido. Opciones: {', '.join(VALID_METHODS)}")
        setattr(aliado, "payment_method", method or None)

    if payload.payment_info is not None:
        setattr(aliado, "payment_info", (payload.payment_info or "").strip()[:300] or None)

    # cbu_alias: mantener por compatibilidad con admin y endpoints legacy
    if payload.cbu_alias is not None:
        aliado.cbu_alias = payload.cbu_alias.strip()[:300] or None
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
"""
cuenta.py — Credenciales y sesión: registro, logins, tokens y contraseñas.

Noveno router migrado de main.py (tramo 5 del split). Contiene:
  - Username/slug: check público de disponibilidad (rate-limited) y cambio
    one-shot del slug del aliado autenticado.
  - /registrarse: alta self-serve con sponsor (Mi Red), username opcional,
    créditos de bienvenida, WhatsApp Canal 1 y emails en background.
  - Admin: /admin/setup (bootstrap, solo si no hay admins) y /admin/login.
  - /aliados/login (código o email) con comparación constant-time y tracking.
  - /auth/refresh: renueva JWT vencido dentro de la ventana de refresh.
  - /auth/recuperar + /auth/resetear: reset por token de un solo uso (1h),
    siempre 200 en recuperar para no enumerar usuarios.
  - /aliado/cambiar-password (verifica la actual) y el reset admin por email.

Todos los endpoints sensibles van rate-limited con el limiter compartido de
rate_limit.py. /admin/login NO lleva Depends(current_admin_required) — es el
endpoint donde se obtiene el JWT (el middleware también lo exime).

Los helpers de identidad (normalizar/validar username, generar_ref_code,
generar_codigo_aliado) y los serializers (_aliado_detalle) viven en
aliados.py — este módulo los importa de allá (dependencia unidireccional
cuenta → aliados). hash_password/verify_password quedan en main porque
tests/conftest.py los monkeypatchea: se accede por puente diferido para que
el patch aplique también acá.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import jarvis_canal1
import schemas
from aliados import (
    _aliado_detalle, generar_codigo_aliado, generar_ref_code,
    normalizar_username, username_disponible, validar_username,
)
from auth import (
    JWT_REFRESH_WINDOW_HOURS, crear_token, current_aliado_required,
    current_admin_required, decodificar_token_ignorando_exp,
)
from database import get_db
from models import Admin, Aliado, PasswordResetToken
from notificaciones import ADMIN_EMAIL, enviar_email
from rate_limit import limiter

router = APIRouter(tags=["cuenta"])


# ── Puentes diferidos a helpers de main (evitan import circular) ─────────────

def hash_password(p):
    from main import hash_password as f  # en main: conftest lo patchea en tests
    return f(p)


def verify_password(plain, hashed):
    from main import verify_password as f  # ídem
    return f(plain, hashed)


def _ajustar_creditos(*args, **kwargs):
    from main import _ajustar_creditos as f
    return f(*args, **kwargs)


# ─── AUTO-REGISTRO PÚBLICO CON EFECTO RED ────────────────────────────────────

# ─── USERNAME / SLUG ENDPOINTS ───────────────────────────────────────────────

@router.get("/aliados/check-username/{username}")
@limiter.limit("30/minute")
def check_username_disponible(request: Request, username: str, db: Session = Depends(get_db)):
    """Endpoint público para chequear disponibilidad mientras el usuario tipea
    en el form de registro. Retorna formato uniforme para el frontend.

    Rate-limited (30/min por IP) para evitar abuso/scraping de usernames.
    """
    u = normalizar_username(username)
    ok, msg = validar_username(u)
    if not ok:
        return {"disponible": False, "valid": False, "razon": msg, "username": u}
    if not username_disponible(db, u):
        return {"disponible": False, "valid": True, "razon": "Ya está en uso.", "username": u}
    return {"disponible": True, "valid": True, "razon": "", "username": u}


@router.post("/aliados/me/cambiar-username")
def cambiar_username_aliado_actual(
    body: schemas.CambiarUsernameIn,
    db: Session = Depends(get_db),
    aliado: Aliado = Depends(current_aliado_required),
):
    """Permite a un aliado existente reclamar/cambiar su slug UNA SOLA VEZ.

    El ref_code viejo deja de funcionar, pero los registros históricos
    (sponsors, ventas, etc.) se conservan vía sponsor_id (no via ref_code).
    Si el aliado ya cambió su username una vez, debe contactar al admin.
    """
    from main import PORTAL_URL  # diferido: const de main (evita import circular)
    if getattr(aliado, "username_personalizado_en", None):
        raise HTTPException(
            400,
            "Ya personalizaste tu link una vez. Si necesitás cambiarlo de nuevo, "
            "escribinos a soporte y lo coordinamos manualmente."
        )

    u = normalizar_username(body.username)
    ok, msg = validar_username(u)
    if not ok:
        raise HTTPException(400, f"Username inválido: {msg}")

    if not username_disponible(db, u, excluir_aliado_id=aliado.id):
        raise HTTPException(400, "Ese username ya está en uso. Probá con otro.")

    ref_code_viejo = aliado.ref_code
    aliado.ref_code = u
    aliado.username_personalizado_en = datetime.now()
    db.commit(); db.refresh(aliado)

    print(f"[USERNAME] {aliado.codigo}: {ref_code_viejo} → {u}")

    return {
        "ok": True,
        "ref_code_anterior": ref_code_viejo,
        "ref_code_nuevo": aliado.ref_code,
        "link_ref": f"{PORTAL_URL}/p/{aliado.ref_code}",
        "link_perfil": f"{PORTAL_URL}/p/{aliado.ref_code}",
        "mensaje": "¡Listo! Tu link personalizado quedó activo.",
    }


@router.post("/registrarse")
@limiter.limit("5/minute")
def auto_registro(request: Request, 
    background_tasks: BackgroundTasks,
    body: schemas.RegistroAliadoIn | None = Body(default=None),
    # === Compatibilidad legacy: query params ===
    # El frontend viejo manda todo por query string. Se mantiene por una
    # versión para no romper el portal mientras se actualiza.
    nombre: str = "", email: str = "", whatsapp: str = "",
    ciudad: str = "", perfil: str = "", password: str = "", dni: str = "",
    ref_sponsor: str = "",
    tipo_aliado: str = "canal1",
    acepto_terminos: bool = False,
    username: str = "",
    db: Session = Depends(get_db)
):
    """Registro self-serve público con sistema de Sub-Aliados.

    PREFERIR body JSON. Los query params siguen aceptándose como fallback
    para compatibilidad con el portal legacy, pero las contraseñas en
    query string aparecen en logs — migrar a body cuanto antes.
    """
    from main import PORTAL_URL  # diferido: const de main (evita import circular)
    # Si vino body JSON, gana sobre query (más seguro).
    if body is not None:
        nombre, email, whatsapp = body.nombre, body.email, body.whatsapp
        password, dni = body.password, body.dni
        ciudad, perfil = body.ciudad, body.perfil
        ref_sponsor = body.ref_sponsor
        tipo_aliado = body.tipo_aliado
        acepto_terminos = body.acepto_terminos
        username = body.username or ""
    else:
        print("[REGISTRO] ⚠️  Recibido por query string — actualizar cliente a body JSON.")

    if not nombre or not email or not whatsapp or not password:
        raise HTTPException(400, "Nombre, email, WhatsApp y contraseña son obligatorios.")
    if len(password) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres.")
    if not acepto_terminos:
        raise HTTPException(400, "Debés aceptar los términos y condiciones del programa de aliados para continuar.")
    if db.query(Aliado).filter(Aliado.email == email).first():
        raise HTTPException(400, "Ya existe un aliado registrado con ese email.")

    # ─── USERNAME / SLUG ─────────────────────────────────────────────────────
    # Si el aliado eligió uno, validamos formato + unicidad.
    # Si no, se autogenera (mantiene compatibilidad con clientes legacy).
    username_normalizado = normalizar_username(username) if username else ""
    username_personalizado = False
    if username_normalizado:
        ok, msg = validar_username(username_normalizado)
        if not ok:
            raise HTTPException(400, f"Username inválido: {msg}")
        if not username_disponible(db, username_normalizado):
            raise HTTPException(400, "Ese username ya está en uso. Probá con otro.")
        username_personalizado = True

    ref_code_final = generar_ref_code(nombre, db=db, username=username_normalizado or None)

    # Buscar Sponsor si vino por invitación
    sponsor_id_db = None
    if ref_sponsor:
        sp = db.query(Aliado).filter(Aliado.ref_code == ref_sponsor).first()
        if sp:
            sponsor_id_db = sp.id

    a = Aliado(
        codigo       = generar_codigo_aliado(db),
        nombre       = nombre,
        email        = email,
        dni          = dni,
        whatsapp     = whatsapp,
        ciudad       = ciudad,
        perfil       = perfil,
        fecha_firma  = datetime.now().strftime("%d/%m/%Y"),
        ref_code     = ref_code_final,
        password_hash= hash_password(password),
        sponsor_id   = sponsor_id_db,
        tipo_aliado  = tipo_aliado if tipo_aliado in ("canal1", "canal2") else "canal1",
        terminos_aceptados = True,
        terminos_aceptados_en = datetime.now(),
    )
    # Si el aliado personalizó su slug, marcamos para impedir cambios futuros.
    if username_personalizado:
        try:
            a.username_personalizado_en = datetime.now()
        except Exception:
            # Si la columna no existe todavía (migración no corrió), seguimos.
            pass
    db.add(a); db.commit(); db.refresh(a)

    # 100 créditos de bienvenida — para que el aliado pueda explorar el
    # marketplace desde el primer día. Sin esto, ve "0 créditos" y la
    # función parece rota. Es señal de bienvenida, no costo real.
    _ajustar_creditos(db, a, 100, "bienvenida", "registro")
    db.commit()

    # Rampa: asigna mentor y abre mentoría para acompañar el primer cierre.
    import rampa
    rampa.iniciar_rampa(db, a); db.commit()

    # WhatsApp de bienvenida Canal 1 — EN SEGUNDO PLANO
    # Envía: código de aliado + link grupo WA + primer paso accionable
    if os.environ.get("ENABLE_CANAL1_WA", "0") == "1":
        background_tasks.add_task(jarvis_canal1.notificar_bienvenida, a, db)

    # Email de bienvenida — EN SEGUNDO PLANO (no bloquea la respuesta)
    # IMPORTANTE: este email NO debe mencionar el marketplace de créditos ni
    # invitar a gastarlos. El email del Día 1 (job_onboarding_sequence) ya cubre
    # ese ángulo correctamente. Aquí solo confirmamos el registro y damos el
    # primer paso accionable: reclamar un lead básico gratis.
    _nombre_corto = a.nombre.split()[0] if a.nombre else "Aliado"
    background_tasks.add_task(
        enviar_email,
        a.email,
        f"¡Bienvenido al Avanza Partner Network, {_nombre_corto}!",
        f"""
        <div style="font-family:Inter,sans-serif;background:#050505;color:#fff;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;">
          <h1 style="color:#f97316;font-size:1.6rem;margin-bottom:8px;">¡Ya sos Aliado Avanza! 🎉</h1>
          <p style="color:#a1a1aa;margin-bottom:28px;">Tu registro fue confirmado. Guardá estos datos para ingresar al portal.</p>

          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:20px;margin-bottom:24px;">
            <p style="margin:0 0 8px;font-size:.85rem;color:#71717a;text-transform:uppercase;letter-spacing:1px;">Tu código de aliado</p>
            <p style="margin:0;font-size:2rem;font-weight:900;color:#f97316;letter-spacing:2px;">{a.codigo}</p>
          </div>

          <p style="color:#a1a1aa;margin-bottom:8px;">Tu comisión arranca en <strong style="color:#fff;">10% (BASIC)</strong> y sube automáticamente con cada venta.</p>

          <div style="background:#0f1d12;border:1px solid #14532d;border-radius:8px;padding:18px;margin:20px 0;">
            <p style="margin:0 0 6px;color:#86efac;font-weight:700;">✅ Tu primer paso: reclamá un lead básico gratis</p>
            <p style="margin:0;color:#a1a1aa;line-height:1.6;font-size:.9rem;">
              En la Bolsa de Leads vas a encontrar contactos disponibles para trabajar — sin gastar nada.
              Los leads básicos son ideales para practicar el pitch y hacer tu primera venta.
            </p>
          </div>

          <p style="color:#a1a1aa;margin-bottom:8px;font-size:.9rem;font-weight:700;">Tu link de ventas (para clientes):</p>
          <p style="margin-bottom:28px;font-size:.9rem;"><a href="{PORTAL_URL}/p/{a.ref_code}" style="color:#3b82f6;">{PORTAL_URL}/p/{a.ref_code}</a></p>

          <a href="{PORTAL_URL}/portal.html#bolsa" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;">Ver leads disponibles →</a>

          <p style="margin-top:32px;font-size:.8rem;color:#71717a;">Avanza Digital · Partner Network · Santa Fe, Argentina</p>
        </div>
        """
    )

    # Notificar al admin — EN SEGUNDO PLANO
    background_tasks.add_task(
        enviar_email,
        ADMIN_EMAIL,
        f"[NUEVO ALIADO] {a.nombre} — {a.codigo}",
        f"<p>Nuevo aliado auto-registrado:<br><strong>{a.nombre}</strong> — {a.email} — {a.whatsapp}<br>Perfil: {a.perfil or '—'} | Ciudad: {a.ciudad or '—'}<br>Código: {a.codigo} | Ref: {a.ref_code}</p>"
    )

    # ── ACTIVACIÓN INMEDIATA ─────────────────────────────────────────────
    # El alta ya auto-loguea (incluir_token=True más abajo), así que ESTE
    # ingreso ES el primer login del aliado. Si no lo contamos, el que se
    # registra y sigue usando el portal con la sesión abierta queda con
    # cantidad_logins=0 → aparece "Sin activar" en la Mi Red de su sponsor
    # aunque ya esté reclamando leads y trabajando. Lo marcamos una sola vez,
    # acá en el alta. No bloquea el registro si llegara a fallar.
    try:
        a.ultimo_login = datetime.now()
        a.cantidad_logins = (getattr(a, "cantidad_logins", 0) or 0) + 1
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[REGISTRO] No se pudo marcar la activación inicial: {e}")

    return _aliado_detalle(a, incluir_token=True)


# ─── ADMIN SETUP / LOGIN ─────────────────────────────────────────────────────

@router.post("/admin/setup")
def crear_admin_inicial(
    body: schemas.AdminSetupIn | None = Body(default=None),
    username: str = "", password: str = "",
    db: Session = Depends(get_db),
    _admin=Depends(current_admin_required),
):
    """Crea el primer admin. Solo funciona si no existe ninguno.

    Protegido por el middleware admin (requiere X-API-Key o JWT admin) — pero
    pensado para el bootstrap inicial cuando no existe admin todavía.
    """
    if body is not None:
        username, password = body.username, body.password
    if not username or not password:
        raise HTTPException(400, "Faltan username y password.")
    if len(password) < 8:
        raise HTTPException(400, "La contraseña de admin debe tener al menos 8 caracteres.")
    if db.query(Admin).count() > 0:
        raise HTTPException(400, "Ya existe al menos un admin.")
    db.add(Admin(username=username, password_hash=hash_password(password)))
    db.commit()
    return {"mensaje": f"Admin '{username}' creado correctamente."}


@router.post("/admin/login")
@limiter.limit("10/minute")
def login_admin(request: Request, 
    body: schemas.AdminLoginIn | None = Body(default=None),
    username: str = "", password: str = "",
    db: Session = Depends(get_db),
):
    """Login de admin con username + password. Devuelve JWT tipo='admin'.

    Acepta body JSON (preferido) o query (legacy). Si no hay admins creados
    todavía, devuelve 503 con instrucciones (usar /admin/setup primero).
    """
    if body is not None:
        username, password = body.username, body.password

    if db.query(Admin).count() == 0:
        raise HTTPException(503, "No hay admins creados. Usar POST /admin/setup primero.")

    if not username or not password:
        raise HTTPException(400, "Faltan username y password.")

    admin = db.query(Admin).filter(Admin.username == username).first()
    # Comparación constant-time del password — siempre corre verify para no leakear si existe el user
    fake_hash = hash_password("dummy_password_for_timing")
    target_hash = admin.password_hash if admin else fake_hash
    ok = verify_password(password, target_hash)
    if not admin or not ok:
        raise HTTPException(401, "Credenciales inválidas.")

    # ── 2FA TOTP (solo si el admin lo activó) ────────────────────────────────
    # Si totp_enabled, el body debe traer un `totp` de 6 dígitos válido. Un
    # admin sin 2FA pasa de largo (retrocompatible). El código se acepta con
    # una ventana de ±1 intervalo (30s) para tolerar desfase de reloj.
    if getattr(admin, "totp_enabled", False):
        codigo_totp = ""
        if body is not None:
            codigo_totp = (getattr(body, "totp", "") or "").strip()
        else:
            codigo_totp = (request.query_params.get("totp", "") or "").strip()
        if not codigo_totp:
            # 401 con pista para que el cliente sepa pedir el código (no es un
            # error de credenciales: usuario y contraseña ya validaron).
            raise HTTPException(401, "2FA requerido: enviá el código de tu app autenticadora en el campo 'totp'.")
        import pyotp
        if not pyotp.TOTP(admin.totp_secret).verify(codigo_totp, valid_window=1):
            raise HTTPException(401, "Código 2FA inválido o expirado.")

    token = crear_token(sub=admin.username, tipo="admin")
    return {"token": token, "tipo": "admin", "username": admin.username}


# ─── 2FA TOTP DEL ADMIN (opt-in) ─────────────────────────────────────────────
# Flujo de enrolamiento en dos pasos para evitar lockouts:
#   1) POST /admin/2fa/setup   → genera (o reusa) el secret y devuelve la
#      otpauth:// URI para que el admin la cargue en Google Authenticator/Authy.
#      NO activa el 2FA todavía: el admin sigue entrando con user+pass.
#   2) POST /admin/2fa/activar → el admin manda el primer código de su app; si
#      es válido, recién ahí totp_enabled pasa a True. Desde el próximo login
#      el código será obligatorio.
# Desactivar requiere código válido (no alcanza con estar logueado) para que un
# token robado no pueda apagar la protección.

def _admin_actual(request, db):
    """Resuelve el Admin del JWT que pasó current_admin_required."""
    from auth import _extraer_token, decodificar_token
    tok = _extraer_token(request)
    if tok:
        try:
            payload = decodificar_token(tok)
            if payload.get("tipo") == "admin":
                return db.query(Admin).filter(Admin.username == payload.get("sub")).first()
        except Exception:
            pass
    return None


@router.post("/admin/2fa/setup")
@limiter.limit("5/minute")
def admin_2fa_setup(request: Request, db: Session = Depends(get_db),
                    _admin=Depends(current_admin_required)):
    """Genera el secret TOTP y devuelve la URI para la app autenticadora.

    Idempotente mientras 2FA no esté activado: si ya hay un secret a medio
    configurar, lo reusa (no rota el QR cada vez). Si el 2FA YA está activo,
    no permite re-generar sin desactivar primero (evita pisar el secret en uso).
    """
    import pyotp
    admin = _admin_actual(request, db)
    if not admin:
        raise HTTPException(401, "No se pudo identificar al admin (¿usás API key legacy? El 2FA requiere login JWT).")

    if admin.totp_enabled:
        raise HTTPException(409, "El 2FA ya está activo. Desactivalo primero si querés regenerar el código.")

    if not admin.totp_secret:
        admin.totp_secret = pyotp.random_base32()
        db.commit()

    uri = pyotp.TOTP(admin.totp_secret).provisioning_uri(
        name=admin.username, issuer_name="Avanza Admin"
    )
    return {
        "otpauth_uri": uri,         # pegar en un generador de QR o cargar a mano
        "secret": admin.totp_secret,  # para ingreso manual en la app
        "ya_activo": False,
        "siguiente_paso": "Escaneá la URI en Google Authenticator/Authy y confirmá con POST /admin/2fa/activar enviando el código.",
    }


@router.post("/admin/2fa/activar")
@limiter.limit("5/minute")
def admin_2fa_activar(request: Request,
                      body: dict = Body(default=None),
                      db: Session = Depends(get_db),
                      _admin=Depends(current_admin_required)):
    """Confirma el primer código y activa el 2FA. Body: {"totp": "123456"}."""
    import pyotp
    admin = _admin_actual(request, db)
    if not admin:
        raise HTTPException(401, "No se pudo identificar al admin.")
    if not admin.totp_secret:
        raise HTTPException(400, "Primero generá el secret con POST /admin/2fa/setup.")
    if admin.totp_enabled:
        return {"status": "ya_estaba_activo"}

    codigo = ((body or {}).get("totp") or "").strip()
    if not codigo:
        raise HTTPException(400, "Falta el código 'totp'.")
    if not pyotp.TOTP(admin.totp_secret).verify(codigo, valid_window=1):
        raise HTTPException(401, "Código inválido. Verificá la hora de tu dispositivo y reintentá.")

    admin.totp_enabled = True
    db.commit()
    return {"status": "activado", "mensaje": "2FA activo. El próximo login pedirá el código."}


@router.post("/admin/2fa/desactivar")
@limiter.limit("5/minute")
def admin_2fa_desactivar(request: Request,
                         body: dict = Body(default=None),
                         db: Session = Depends(get_db),
                         _admin=Depends(current_admin_required)):
    """Apaga el 2FA. Requiere un código válido (no basta con el JWT) para que
    un token robado no pueda desactivar la protección."""
    import pyotp
    admin = _admin_actual(request, db)
    if not admin:
        raise HTTPException(401, "No se pudo identificar al admin.")
    if not admin.totp_enabled:
        return {"status": "no_estaba_activo"}

    codigo = ((body or {}).get("totp") or "").strip()
    if not codigo or not pyotp.TOTP(admin.totp_secret).verify(codigo, valid_window=1):
        raise HTTPException(401, "Necesitás un código 2FA válido para desactivarlo.")

    admin.totp_enabled = False
    admin.totp_secret = None
    db.commit()
    return {"status": "desactivado"}

@router.post("/aliados/login")
@limiter.limit("10/minute")
def login_aliado(request: Request, 
    body: schemas.LoginAliadoIn | None = Body(default=None),
    codigo: str = "", password: str = "",
    db: Session = Depends(get_db),
):
    """Portal del aliado: login con código o email + contraseña.

    Acepta body JSON (preferido) o query string (legacy, queda en logs).
    Devuelve los datos del aliado + un JWT en el campo `token` para usar en
    `Authorization: Bearer ...` en requests subsiguientes.
    """
    if body is not None:
        codigo, password = body.codigo, body.password
    else:
        print("[LOGIN] ⚠️  Recibido por query string — migrar cliente a body JSON.")

    if not codigo or not password:
        raise HTTPException(400, "Faltan codigo y password.")

    # Buscar aliado activo por email o por código
    if "@" in codigo:
        a = db.query(Aliado).filter(Aliado.email == codigo.lower().strip(), Aliado.activo == True).first()
    else:
        a = db.query(Aliado).filter(Aliado.codigo == codigo, Aliado.activo == True).first()
    # Comparación constant-time del password (corre verify aunque no exista el aliado)
    fake_hash = hash_password("dummy_password_for_timing")
    target_hash = a.password_hash if a else fake_hash
    ok = verify_password(password, target_hash)
    if not a or not ok:
        # Mismo mensaje para no leakear si el código existe o no
        raise HTTPException(401, "Código o contraseña incorrectos.")

    # TRACKING (no bloquea login si falla)
    try:
        a.ultimo_login = datetime.now()
        a.cantidad_logins = (getattr(a, 'cantidad_logins', 0) or 0) + 1
        # Resetear flags de inactividad: si el aliado vuelve a quedar inactivo
        # en el futuro, el ciclo de 20d/30d arranca limpio desde este login.
        a.notif_inact_20d_en = None
        a.notif_inact_30d_en = None
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error guardando tracking de login: {e}")

    return _aliado_detalle(a, incluir_token=True)


# ─── REFRESH TOKEN ───────────────────────────────────────────────────────────
@router.post("/auth/refresh")
@limiter.limit("30/minute")
def refresh_token(request: Request, db: Session = Depends(get_db)):
    """Renueva un JWT que está por vencer (o vencido hace poco).

    Acepta el token en el header `Authorization: Bearer ...` aunque ya esté
    expirado, mientras la firma sea válida y no haya pasado más de
    JWT_REFRESH_WINDOW_HOURS desde el `iat`. Devuelve un nuevo token con la
    misma identidad y tipo.

    El front llama a este endpoint cuando recibe un 401 con detalle de token
    expirado, para no obligar al aliado a reloguearse. Si el refresh también
    falla → 401 y el front muestra el modal de login.
    """
    from jose import JWTError as _JWTError
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(401, "Falta token Bearer en header Authorization.")
    token = parts[1]

    try:
        payload = decodificar_token_ignorando_exp(token)
    except _JWTError:
        raise HTTPException(401, "Token con firma inválida — no se puede refrescar.")

    tipo = payload.get("tipo")
    sub  = payload.get("sub")
    iat  = payload.get("iat")
    if not tipo or not sub or not iat:
        raise HTTPException(401, "Token incompleto.")
    if tipo not in ("aliado", "admin"):
        raise HTTPException(401, "Tipo de token desconocido.")

    # iat puede venir como int (unix epoch) o como datetime serializado
    try:
        iat_dt = datetime.fromtimestamp(iat, tz=timezone.utc) if isinstance(iat, (int, float)) else datetime.fromisoformat(str(iat))
    except Exception:
        raise HTTPException(401, "Token con iat inválido.")
    edad = datetime.now(timezone.utc) - iat_dt
    if edad > timedelta(hours=JWT_REFRESH_WINDOW_HOURS):
        raise HTTPException(401, "El token está fuera de la ventana de refresh — reloguearse.")

    # Validar que el sujeto siga siendo válido en la DB.
    if tipo == "aliado":
        a = db.query(Aliado).filter(Aliado.codigo == sub, Aliado.activo == True).first()
        if not a:
            raise HTTPException(401, "Aliado del token no encontrado o inactivo.")
    else:  # admin
        adm = db.query(Admin).filter(Admin.username == sub).first()
        if not adm:
            raise HTTPException(401, "Admin del token no encontrado.")

    nuevo = crear_token(sub=sub, tipo=tipo)
    return {"token": nuevo, "tipo": tipo}


@router.post("/auth/recuperar")
@limiter.limit("5/minute")
def solicitar_reset_contrasena(
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Solicita recuperación de contraseña. Envía un email con un link de un solo uso.

    Acepta { "email": "..." }
    Siempre devuelve 200 aunque el email no exista (evita enumeración de usuarios).
    El link expira en 1 hora.
    """
    from main import PORTAL_URL  # diferido: const de main (evita import circular)
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "Falta el campo 'email'.")

    aliado = db.query(Aliado).filter(Aliado.email == email, Aliado.activo == True).first()
    if aliado:
        # Invalidar tokens anteriores pendientes para este aliado
        db.query(PasswordResetToken).filter(
            PasswordResetToken.aliado_id == aliado.id,
            PasswordResetToken.usado == False,
        ).update({"usado": True})
        db.commit()

        token_raw = secrets.token_urlsafe(32)
        expira = datetime.now(timezone.utc) + timedelta(hours=1)
        prt = PasswordResetToken(
            aliado_id=aliado.id,
            token=token_raw,
            expira_en=expira,
        )
        db.add(prt)
        db.commit()

        nombre_corto = aliado.nombre.split()[0] if aliado.nombre else "Aliado"
        link = f"{PORTAL_URL}/recuperar.html?reset_token={token_raw}"
        background_tasks.add_task(
            enviar_email,
            aliado.email,
            "🔑 Avanza — Recuperá tu contraseña",
            f"""
            <div style="font-family:Inter,sans-serif;background:#050505;color:#fff;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;">
              <h1 style="color:#f97316;font-size:1.4rem;margin-bottom:8px;">Recuperar contraseña</h1>
              <p style="color:#a1a1aa;margin-bottom:24px;">Hola <strong style="color:#fff;">{nombre_corto}</strong>, recibimos tu solicitud para resetear la contraseña de tu cuenta.</p>
              <p style="color:#a1a1aa;margin-bottom:24px;">Hacé clic en el botón para crear una nueva contraseña. El link es válido por <strong style="color:#fff;">1 hora</strong>.</p>
              <a href="{link}" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;">Resetear mi contraseña →</a>
              <p style="margin-top:28px;color:#71717a;font-size:.85rem;">Si no pediste esto, ignorá este email. Tu contraseña no cambia hasta que uses el link.</p>
              <p style="margin-top:24px;font-size:.8rem;color:#71717a;">Avanza Digital · Partner Network · Santa Fe, Argentina</p>
            </div>
            """
        )

    # Siempre 200 — no revelar si el email existe o no
    return {"mensaje": "Si ese email corresponde a una cuenta activa, vas a recibir las instrucciones en los próximos minutos."}


@router.post("/auth/resetear")
@limiter.limit("10/minute")
def resetear_contrasena(
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Resetea la contraseña usando el token de recuperación.

    Acepta { "token": "...", "nueva_password": "..." }
    El token es de un solo uso y expira en 1 hora.
    """
    token_raw = (body.get("token") or "").strip()
    nueva = (body.get("nueva_password") or "").strip()

    if not token_raw or not nueva:
        raise HTTPException(400, "Faltan 'token' y/o 'nueva_password'.")
    if len(nueva) < 6:
        raise HTTPException(400, "La nueva contraseña debe tener al menos 6 caracteres.")

    prt = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token_raw,
        PasswordResetToken.usado == False,
    ).first()

    if not prt:
        raise HTTPException(400, "Token inválido o ya utilizado.")

    # Comparar con datetime naive/aware correctamente
    ahora = datetime.now(timezone.utc)
    expira = prt.expira_en
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=timezone.utc)
    if ahora > expira:
        prt.usado = True
        db.commit()
        raise HTTPException(400, "El link de recuperación expiró. Solicitá uno nuevo.")

    aliado = db.query(Aliado).filter(Aliado.id == prt.aliado_id, Aliado.activo == True).first()
    if not aliado:
        raise HTTPException(400, "Cuenta no encontrada o inactiva.")

    aliado.password_hash = hash_password(nueva)
    prt.usado = True
    db.commit()

    return {"mensaje": "Contraseña actualizada correctamente. Ya podés iniciar sesión con tu nueva contraseña."}





@router.post("/aliado/cambiar-password")
@limiter.limit("10/minute")
def cambiar_password_aliado(
    request: Request,
    body: dict = Body(...),
    aliado: Aliado = Depends(current_aliado_required),
    db: Session = Depends(get_db),
):
    """Permite al aliado autenticado cambiar su propia contraseña.

    Acepta { "password_actual": "...", "nueva_password": "..." }.
    Verifica la contraseña actual antes de permitir el cambio.
    """
    actual = (body.get("password_actual") or "").strip()
    nueva  = (body.get("nueva_password")  or "").strip()

    if not actual or not nueva:
        raise HTTPException(400, "Faltan 'password_actual' y/o 'nueva_password'.")
    if len(nueva) < 6:
        raise HTTPException(400, "La nueva contraseña debe tener al menos 6 caracteres.")
    if not verify_password(actual, aliado.password_hash):
        raise HTTPException(400, "La contraseña actual es incorrecta.")

    aliado.password_hash = hash_password(nueva)
    db.commit()
    return {"mensaje": "Contraseña actualizada correctamente."}


@router.post("/admin/reset-password-aliado")
def reset_password_aliado(
    request: Request,
    email: str = Body(...),
    nueva_password: str = Body(...),
    _admin=Depends(current_admin_required),
    db: Session = Depends(get_db),
):
    """Resetea la contraseña de un aliado. Solo accesible por admin."""
    a = db.query(Aliado).filter(Aliado.email == email).first()
    if not a:
        raise HTTPException(404, "Aliado no encontrado.")
    a.password_hash = hash_password(nueva_password)
    db.commit()
    return {"ok": True, "aliado": a.nombre, "email": a.email}
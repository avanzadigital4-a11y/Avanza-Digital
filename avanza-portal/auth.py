"""
auth.py — Autenticación y autorización con JWT
================================================
- Emisión / validación de JWT (HS256).
- Dependencies para FastAPI:
    * current_aliado_required → exige token válido tipo 'aliado' o 'admin'.
    * verify_ownership(codigo) → además chequea que el JWT corresponde al `codigo` del path
                                  (admins pueden acceder a cualquiera).
    * current_admin_required → exige JWT tipo 'admin' (o, durante el período de
                                migración, X-API-Key válida).

JWT_SECRET DEBE estar seteado como env var en producción. Si falta y ENV=production,
el módulo aborta el arranque (fail-loud). En dev, genera un secret en memoria con
warning (esto invalida los tokens en cada redeploy y NO debe usarse en prod).
"""
import os
import secrets
import sys
import warnings
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
from models import Admin, Aliado

# ─── CONFIG ──────────────────────────────────────────────────────────────────
JWT_ALGORITHM = "HS256"
# 7 días por defecto: para un portal B2B esto es razonable (no es un banco)
# y elimina la fricción de tokens caducados cada 24h. Si se necesita más
# seguridad, bajalo a 24 y usá refresh tokens activos.
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "168"))  # 7 días

# Ventana durante la cual un token expirado todavía puede ser refrescado
# (controla cuánto puede "dormir" un aliado offline antes de tener que loguearse
# de nuevo a mano). Por defecto: 30 días.
JWT_REFRESH_WINDOW_HOURS = int(os.environ.get("JWT_REFRESH_WINDOW_HOURS", "720"))

# ─── TRACKING DE ACTIVIDAD ────────────────────────────────────────────────────
# Cada request autenticado de un aliado "toca" su actividad. Así "Último acceso"
# y "Visitas" en el admin reflejan CUALQUIER ingreso real (abrir el portal,
# reclamar/contactar un lead, ver la bolsa, etc.), NO solo el formulario de
# login. Antes el contador subía únicamente en /aliados/login y en el auto-
# registro: el que entraba con la sesión guardada o por un token quedaba en
# "Nunca / 0 visitas" aunque estuviera trabajando, y no sumaba en Mi Red.
#   - ultimo_login se refresca como mucho cada ACTIVIDAD_DEBOUNCE_MIN minutos
#     (evita escribir en la base en cada click).
#   - cantidad_logins ("Visitas") suma 1 por sesión nueva: si pasaron más de
#     ACTIVIDAD_SESION_MIN minutos desde la última actividad, es una visita nueva.
ACTIVIDAD_DEBOUNCE_MIN = int(os.environ.get("ACTIVIDAD_DEBOUNCE_MIN", "2"))
ACTIVIDAD_SESION_MIN   = int(os.environ.get("ACTIVIDAD_SESION_MIN", "30"))

_jwt_secret_env = os.environ.get("JWT_SECRET", "").strip()
_env_name = os.environ.get("ENV", "").lower().strip() or os.environ.get("ENVIRONMENT", "").lower().strip()
_is_production = _env_name in ("production", "prod")

if not _jwt_secret_env:
    if _is_production:
        # Fail-loud: en producción NO arrancamos sin JWT_SECRET. Generar un
        # secret en memoria significa que todos los aliados se desloguean en
        # cada redeploy (Render reinicia solo a veces) — eso es trampa silenciosa.
        print(
            "[AUTH] FATAL: JWT_SECRET no configurada en producción. "
            "Configurá JWT_SECRET como variable de entorno antes de levantar el servidor.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Dev local: fallback inseguro con aviso.
    JWT_SECRET = secrets.token_urlsafe(64)
    warnings.warn(
        "[AUTH] JWT_SECRET no configurada — generada en memoria. "
        "Los tokens se invalidarán en cada reinicio. "
        "Solo aceptable en desarrollo local.",
        stacklevel=2,
    )
else:
    JWT_SECRET = _jwt_secret_env

# Compatibilidad legacy: ADMIN_API_KEY sigue funcionando como fallback de auth admin
# (se removerá en la próxima versión cuando admin.html migre 100% a JWT).
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "").strip()


# ─── EMISIÓN ─────────────────────────────────────────────────────────────────
def crear_token(*, sub: str, tipo: str, extra: Optional[dict] = None) -> str:
    """Emite un JWT firmado con HS256.
    `sub` = identificador del sujeto (codigo del aliado o username del admin).
    `tipo` = 'aliado' | 'admin'.
    """
    if tipo not in ("aliado", "admin"):
        raise ValueError("tipo debe ser 'aliado' o 'admin'")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "tipo": tipo,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decodificar_token(token: str) -> dict:
    """Decodifica y valida firma+expiración. Lanza JWTError si es inválido."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def decodificar_token_ignorando_exp(token: str) -> dict:
    """Decodifica un token IGNORANDO su expiración. Usado SOLO por /auth/refresh
    para permitir extender tokens que acaban de vencer (dentro de la ventana de
    refresh). Sigue validando la firma — un token con firma inválida no se
    puede refrescar.
    """
    return jwt.decode(
        token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
        options={"verify_exp": False},
    )


# ─── DEPENDENCIES ────────────────────────────────────────────────────────────
def _extraer_token(request: Request) -> Optional[str]:
    """Saca el token del header `Authorization: Bearer <token>`."""
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if not auth:
        return None
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def current_payload_required(request: Request) -> dict:
    """Devuelve el payload del JWT o 401."""
    token = _extraer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta token de autenticación (Authorization: Bearer ...).",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decodificar_token(token)
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido o expirado.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    return payload


def _minutos_desde(dt) -> Optional[float]:
    """Minutos transcurridos desde `dt` hasta ahora. Tolerante a naive/aware."""
    if dt is None:
        return None
    try:
        ahora = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()
        return (ahora - dt).total_seconds() / 60.0
    except Exception:
        return None


def registrar_actividad(db: Session, a: Aliado) -> None:
    """Marca actividad real del aliado en CADA request autenticado.

    Refresca `ultimo_login` (con debounce para no escribir en cada click) e
    incrementa `cantidad_logins` cuando arranca una sesión nueva. Es la fuente
    de verdad de "Último acceso" y "Visitas" en el admin y de los "activados"
    de Mi Red. Best-effort: si la escritura falla hace rollback y sigue, nunca
    rompe el request del aliado."""
    ult = getattr(a, "ultimo_login", None)
    gap = _minutos_desde(ult)

    es_sesion_nueva  = (ult is None) or (gap is not None and gap >= ACTIVIDAD_SESION_MIN)
    necesita_refresh = (ult is None) or (gap is None) or (gap >= ACTIVIDAD_DEBOUNCE_MIN)
    if not necesita_refresh:
        return

    try:
        a.ultimo_login = datetime.now()
        if es_sesion_nueva:
            a.cantidad_logins = (getattr(a, "cantidad_logins", 0) or 0) + 1
        # Vuelve a estar activo: limpiar avisos de inactividad para que el ciclo
        # 20d/30d/55d arranque limpio desde esta actividad.
        a.notif_inact_20d_en = None
        a.notif_inact_30d_en = None
        a.notif_inact_55d_en = None
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[ACTIVIDAD] no se pudo registrar ingreso de {getattr(a, 'codigo', '?')}: {e}")


def current_aliado_required(
    payload: dict = Depends(current_payload_required),
    db: Session = Depends(get_db),
) -> Aliado:
    """Devuelve el Aliado dueño del token. Admin token NO sirve acá; usar
    `verify_ownership` si querés que el admin también pueda."""
    if payload.get("tipo") != "aliado":
        raise HTTPException(403, "Token de tipo distinto a 'aliado'.")
    codigo = payload.get("sub")
    if not codigo:
        raise HTTPException(401, "Token sin subject.")
    a = db.query(Aliado).filter(Aliado.codigo == codigo, Aliado.activo == True).first()
    if not a:
        raise HTTPException(401, "Aliado del token no encontrado o inactivo.")
    # Registrar el ingreso real (precisión de "Último acceso" / "Visitas").
    registrar_actividad(db, a)
    return a


def verify_ownership(codigo_path: str):
    """Factory de dependency que valida que el JWT pertenezca al `codigo_path`
    (o sea un admin). Uso:

        @app.get("/aliados/{codigo}/algo")
        def endpoint(codigo: str, _=Depends(verify_ownership_dep)):
            ...

    Nota: en FastAPI los dependencies-factory necesitan envoltorio. Acá usamos
    `verify_ownership_dep` directo, que lee `codigo` del path con Request.
    """
    raise NotImplementedError("Usar verify_ownership_dep directamente.")


def verify_ownership_dep(
    request: Request,
    payload: dict = Depends(current_payload_required),
) -> dict:
    """Lee `codigo` del path-param y valida que el token corresponda
    a ese aliado, o que sea un admin."""
    codigo_path = request.path_params.get("codigo")
    if not codigo_path:
        # Si la ruta no tiene {codigo}, no aplica este dependency.
        return payload

    tipo = payload.get("tipo")
    sub = payload.get("sub")

    if tipo == "admin":
        return payload  # admins entran a cualquiera
    if tipo == "aliado" and sub == codigo_path:
        return payload

    raise HTTPException(403, "No tenés permisos sobre este aliado.")


def current_admin_required(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Acepta un JWT de admin O una X-API-Key válida (legacy).
    Devuelve un dict con info del admin. Después de migrar admin.html a JWT,
    se puede remover el fallback de X-API-Key.
    """
    # 1) JWT
    token = _extraer_token(request)
    if token:
        try:
            payload = decodificar_token(token)
            if payload.get("tipo") == "admin":
                return {"via": "jwt", "username": payload.get("sub"), "payload": payload}
        except JWTError:
            pass  # caemos al fallback

    # 2) Fallback legacy: X-API-Key
    if ADMIN_API_KEY:
        provided = request.headers.get("X-API-Key", "") or request.headers.get("x-api-key", "")
        if provided and secrets.compare_digest(provided, ADMIN_API_KEY):
            return {"via": "api_key", "username": "legacy-admin"}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Se requiere autenticación de administrador.",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ─── HASHING SEGURO PARA COMPARACIÓN DE STRINGS ──────────────────────────────
def safe_str_eq(a: str, b: str) -> bool:
    """Comparación de strings resistente a timing attacks."""
    return secrets.compare_digest(a or "", b or "")
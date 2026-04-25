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

JWT_SECRET DEBE estar seteado como env var en producción. Si falta, se genera
uno aleatorio en memoria y se loguea un warning — eso invalida los tokens en
cada redeploy y NO debe usarse en prod.
"""
import os
import secrets
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
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

_jwt_secret_env = os.environ.get("JWT_SECRET", "").strip()
if not _jwt_secret_env:
    # Fallback inseguro: token random en memoria. Solo dev local.
    JWT_SECRET = secrets.token_urlsafe(64)
    warnings.warn(
        "[AUTH] JWT_SECRET no configurada — generada en memoria. "
        "Los tokens se invalidarán en cada redeploy. "
        "Configurar JWT_SECRET en producción.",
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
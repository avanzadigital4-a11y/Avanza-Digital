"""
schemas.py — Pydantic models para request bodies
=================================================
Mueve los datos sensibles (passwords, DNI, CBU, etc.) a body JSON en lugar
de query string, donde quedaban en logs/historial/referrers.

Todos los models tienen `extra = "ignore"` para tolerar campos extra del cliente.
"""
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ─── BASE ────────────────────────────────────────────────────────────────────
class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ─── REGISTRO + LOGIN ALIADO ─────────────────────────────────────────────────
class RegistroAliadoIn(_Base):
    nombre: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    whatsapp: str = Field(..., min_length=4, max_length=40)
    password: str = Field(..., min_length=6, max_length=200)
    ciudad: str = ""
    perfil: str = ""
    dni: str = ""
    ref_sponsor: str = ""
    tipo_aliado: str = "canal1"
    acepto_terminos: bool = False


class LoginAliadoIn(_Base):
    codigo: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=1, max_length=200)


class CrearAliadoIn(_Base):
    """Endpoint admin para crear aliados manualmente."""
    nombre: str
    email: EmailStr
    whatsapp: str
    ciudad: str
    dni: str = ""
    perfil: str = ""
    fecha_firma: str = ""
    password: Optional[str] = None  # si no viene, se genera


# ─── ADMIN ───────────────────────────────────────────────────────────────────
class AdminSetupIn(_Base):
    username: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=8, max_length=200)


class AdminLoginIn(_Base):
    username: str
    password: str


# ─── PROSPECTOS ──────────────────────────────────────────────────────────────
class CrearProspectoIn(_Base):
    nombre: str = Field(..., min_length=1, max_length=200)
    contacto: str = ""
    plan_interes: str = ""
    rubro: str = ""
    nota: str = ""


class ActualizarNotaIn(_Base):
    nota: str = Field("", max_length=4000)


class CambiarEstadoProspectoIn(_Base):
    estado: str


class ActualizarDatosProspectoIn(_Base):
    rubro: str = ""
    tamano: str = ""
    urgencia: str = ""


class PerfilarProspectoIn(_Base):
    rubro: str = ""
    tamano: str = "pyme"
    urgencia: str = "media"


class TogglePilotoIn(_Base):
    activo: bool


# ─── REFERIDOS / VENTAS ──────────────────────────────────────────────────────
class RegistrarReferidoIn(_Base):
    """Recibe ref_code en body (no query) para no leakear códigos por logs."""
    ref_code: str
    nombre_cliente: str = Field(..., min_length=1, max_length=200)
    plan_elegido: str
    notas: str = ""


class RegistrarVentaIn(_Base):
    codigo_aliado: str
    nombre_cliente: str
    plan: str
    modalidad_pago: str = "ARS MEP"
    referido_id: Optional[int] = None
    notas: str = ""


class CambiarNivelIn(_Base):
    nivel: str


class MarcarPagadaIn(_Base):
    modalidad: str = "ARS MEP"


# ─── PERFIL DEL ALIADO ───────────────────────────────────────────────────────
class ActualizarPerfilIn(_Base):
    portal_publico_titular: Optional[str] = None
    portal_publico_bio: Optional[str] = None
    portal_publico_activo: Optional[bool] = None


class ActualizarCBUIn(_Base):
    cbu_alias: str = Field(..., min_length=3, max_length=80)


# ─── BOLSA / MARKETPLACE ─────────────────────────────────────────────────────
class ContactarLeadIn(_Base):
    resultado: str = "exitoso"


# ─── COMUNIDAD ───────────────────────────────────────────────────────────────
class PostComunidadIn(_Base):
    codigo_aliado: str
    tipo: str = "tip"
    titulo: str = Field(..., min_length=1, max_length=200)
    cuerpo: str = Field(..., min_length=1, max_length=10000)


class ComentarioComunidadIn(_Base):
    codigo_aliado: str
    cuerpo: str = Field(..., min_length=1, max_length=2000)


# ─── CHECKOUT ────────────────────────────────────────────────────────────────
class CrearCheckoutIn(_Base):
    plan: str
    ref_code: str
    nombre_cliente: str = "Cliente"
    moneda: str = "ars"


# ─── ACADEMIA (ADMIN) ────────────────────────────────────────────────────────
class AcademiaModuloIn(_Base):
    orden: int
    titulo: str
    descripcion: str = ""
    tipo: str
    url_contenido: str = ""
    duracion_minutos: Optional[int] = None
    activo: bool = True


# ─── CRÉDITOS (ADMIN) ────────────────────────────────────────────────────────
class AjusteCreditosIn(_Base):
    delta: int
    motivo: str
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Aliado(Base):
    __tablename__ = "aliados"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String, unique=True, index=True)       # AL-001, AL-002, etc.
    nombre = Column(String, nullable=False)
    dni = Column(String)
    email = Column(String, unique=True, index=True)
    whatsapp = Column(String)
    ciudad = Column(String)
    perfil = Column(String)
    fecha_firma = Column(String)
    nivel = Column(String, default="BASIC")                # BASIC, SILVER, PREMIUM, ELITE
    password_hash = Column(String)                         # Para login del aliado
    activo = Column(Boolean, default=True)
    ref_code = Column(String, unique=True)                 # Código ?ref= único
    creado_en = Column(DateTime, default=func.now())
    
    # --- TRACKING DE LOGIN ---
    ultimo_login = Column(DateTime, nullable=True)
    cantidad_logins = Column(Integer, default=0)

    ventas = relationship("Venta", back_populates="aliado")
    referidos = relationship("Referido", back_populates="aliado")
    prospectos = relationship("Prospecto", back_populates="aliado")

    @property
    def comision_pct(self):
        niveles = {"BASIC": 0.10, "SILVER": 0.12, "PREMIUM": 0.15, "ELITE": 0.20}
        return niveles.get(self.nivel, 0.10)

    @property
    def ventas_6_meses(self):
        from datetime import datetime, timedelta
        hace_6_meses = datetime.now() - timedelta(days=180)
        return sum(1 for v in self.ventas if v.fecha_venta and v.fecha_venta >= hace_6_meses and v.confirmada)

    @property
    def nivel_calculado(self):
        v = self.ventas_6_meses
        if v >= 5:
            return "ELITE"
        elif v >= 2:
            return "PREMIUM"
        elif v >= 1:
            return "SILVER"
        return "BASIC"

    @property
    def total_ganado(self):
        return sum(v.comision_usd for v in self.ventas if v.confirmada)

    @property
    def total_pendiente(self):
        return sum(v.comision_usd for v in self.ventas if v.confirmada and not v.pagada)


class Referido(Base):
    """Registro PREVIO al pago — el paso crítico del contrato"""
    __tablename__ = "referidos"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"))
    nombre_cliente = Column(String, nullable=False)
    plan_elegido = Column(String, nullable=False)          # Plan Base, Pro, Industrial, Estratégico 360
    notas = Column(Text)
    registrado_en = Column(DateTime, default=func.now())
    acuse_recibo = Column(Boolean, default=False)          # Avanza confirmó el registro
    convertido = Column(Boolean, default=False)            # Se cerró la venta

    aliado = relationship("Aliado", back_populates="referidos")
    venta = relationship("Venta", back_populates="referido", uselist=False)


class Venta(Base):
    __tablename__ = "ventas"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"))
    referido_id = Column(Integer, ForeignKey("referidos.id"), nullable=True)
    nombre_cliente = Column(String, nullable=False)
    plan = Column(String, nullable=False)
    valor_usd = Column(Float, nullable=False)
    comision_pct = Column(Float, nullable=False)
    comision_usd = Column(Float, nullable=False)
    confirmada = Column(Boolean, default=False)            # Admin confirmó la venta
    pagada = Column(Boolean, default=False)
    fecha_venta = Column(DateTime)
    fecha_pago = Column(DateTime, nullable=True)
    modalidad_pago = Column(String, nullable=True)         # ARS MEP, USD Wise, USDT
    notas = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=func.now())

    aliado = relationship("Aliado", back_populates="ventas")
    referido = relationship("Referido", back_populates="venta")


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    password_hash = Column(String)
    creado_en = Column(DateTime, default=func.now())


class Prospecto(Base):
    """Prospecto cargado por un aliado — CRM mínimo"""
    __tablename__ = "prospectos"

    id          = Column(Integer, primary_key=True, index=True)
    aliado_id   = Column(Integer, ForeignKey("aliados.id"))
    nombre      = Column(String, nullable=False)      # Nombre empresa/persona
    contacto    = Column(String)                      # WhatsApp / email / teléfono
    plan_interes= Column(String)                      # Plan que le interesaría
    estado      = Column(String, default="sin_contactar")  # sin_contactar | contactado | respondio
    nota        = Column(Text)                        # Nota libre del aliado
    interesante = Column(Boolean, default=False)      # Flag 🔥
    fecha_contacto  = Column(DateTime, nullable=True)
    fecha_respuesta = Column(DateTime, nullable=True)
    creado_en   = Column(DateTime, default=func.now())

    aliado = relationship("Aliado", back_populates="prospectos")


class AuditoriaLog(Base):
    """Registro de uso de la herramienta de auditoría gratuita"""
    __tablename__ = "auditorias_log"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), nullable=True)
    ref_code = Column(String, index=True)
    dominio = Column(String, index=True)
    score = Column(Integer)
    email_capturado = Column(String, nullable=True)
    creado_en = Column(DateTime, default=func.now())

    aliado = relationship("Aliado")


# Planes y precios según contrato v3
PLANES = {
    "Plan Base":         1050.0,
    "Plan Pro":          2900.0,
    "Plan Industrial":   4900.0,
    "Estrategico 360":   7500.0,
}

NIVELES = {
    "BASIC":   {"comision": 0.10, "requisito": 0,  "bono": False},
    "SILVER":  {"comision": 0.12, "requisito": 1,  "bono": True},
    "PREMIUM": {"comision": 0.15, "requisito": 2,  "bono": False},
    "ELITE":   {"comision": 0.20, "requisito": 5,  "bono": False},
}

# --- Agregar al final de models.py ---

class LeadBolsa(Base):
    __tablename__ = "bolsa_leads"

    id = Column(Integer, primary_key=True, index=True)
    empresa = Column(String, nullable=False)
    rubro = Column(String, nullable=False)
    telefono = Column(String, nullable=False)
    email = Column(String, nullable=True)
    estado = Column(String, default="disponible") # Estados: disponible, reclamado, contactado
    aliado_id = Column(Integer, ForeignKey("aliados.id"), nullable=True)
    fecha_carga = Column(DateTime, default=datetime.now)
    fecha_reclamo = Column(DateTime, nullable=True)

    # Relación para saber quién lo reclamó
    aliado = relationship("Aliado", backref="leads_bolsa")
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Numeric
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
from datetime import datetime


# ─── ALIADO ──────────────────────────────────────────────────────────────────
class Aliado(Base):
    __tablename__ = "aliados"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String, unique=True, index=True)
    nombre = Column(String, nullable=False)
    dni = Column(String)
    email = Column(String, unique=True, index=True)
    whatsapp = Column(String)
    ciudad = Column(String)
    perfil = Column(String)
    fecha_firma = Column(String)
    nivel = Column(String, default="BASIC")
    password_hash = Column(String)
    activo = Column(Boolean, default=True)
    ref_code = Column(String, unique=True)
    creado_en = Column(DateTime, default=func.now())

    # --- SISTEMA DE RED (SUB-ALIADOS) ---
    sponsor_id = Column(Integer, ForeignKey("aliados.id"), nullable=True)
    sponsor = relationship("Aliado", remote_side=[id], backref="sub_aliados")

    # --- TRACKING DE LOGIN ---
    ultimo_login = Column(DateTime, nullable=True)
    cantidad_logins = Column(Integer, default=0)

    # --- ONBOARDING ---
    onboarding_completado = Column(Boolean, default=False)

    # --- REPUTACIÓN ---
    reputacion_score = Column(Integer, default=50)
    badges = Column(Text, default="[]")
    reputacion_calculada_en = Column(DateTime, nullable=True)

    # --- CRÉDITOS PARA MARKETPLACE ---
    creditos = Column(Integer, default=0)

    # --- PORTAL PÚBLICO ---
    portal_publico_activo = Column(Boolean, default=True)
    portal_publico_titular = Column(String, nullable=True)
    portal_publico_bio = Column(Text, nullable=True)

    # --- CANAL DE ALIADO ---
    tipo_aliado = Column(String, default="canal1")

    # --- COBRO DE COMISIONES (NUEVO) ---
    cbu_alias = Column(String, nullable=True)

    # --- CONTRATO DIGITAL (NUEVO) ---
    terminos_aceptados = Column(Boolean, default=False)
    terminos_aceptados_en = Column(DateTime, nullable=True)

    # --- NOTIFICACIONES DE INACTIVIDAD ---
    notif_inact_20d_en = Column(DateTime, nullable=True)
    notif_inact_30d_en = Column(DateTime, nullable=True)

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


# ─── REFERIDO ────────────────────────────────────────────────────────────────
class Referido(Base):
    __tablename__ = "referidos"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"))
    nombre_cliente = Column(String, nullable=False)
    plan_elegido = Column(String, nullable=False)
    notas = Column(Text)
    registrado_en = Column(DateTime, default=func.now())
    acuse_recibo = Column(Boolean, default=False)
    convertido = Column(Boolean, default=False)

    aliado = relationship("Aliado", back_populates="referidos")
    venta = relationship("Venta", back_populates="referido", uselist=False)


# ─── VENTA ───────────────────────────────────────────────────────────────────
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
    confirmada = Column(Boolean, default=False)
    pagada = Column(Boolean, default=False)
    fecha_venta = Column(DateTime)
    fecha_pago = Column(DateTime, nullable=True)
    modalidad_pago = Column(String, nullable=True)
    notas = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=func.now())

    # --- FINANCIACIÓN ---
    cuotas = Column(Integer, default=1)
    financiacion_pct = Column(Float, default=0.0)

    aliado = relationship("Aliado", back_populates="ventas")
    referido = relationship("Referido", back_populates="venta")


# ─── ADMIN ───────────────────────────────────────────────────────────────────
class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    password_hash = Column(String)
    creado_en = Column(DateTime, default=func.now())


# ─── PROSPECTO ───────────────────────────────────────────────────────────────
class Prospecto(Base):
    __tablename__ = "prospectos"

    id          = Column(Integer, primary_key=True, index=True)
    aliado_id   = Column(Integer, ForeignKey("aliados.id"))
    nombre      = Column(String, nullable=False)
    contacto    = Column(String)
    plan_interes= Column(String)
    estado      = Column(String, default="sin_contactar")
    nota        = Column(Text)
    interesante = Column(Boolean, default=False)
    fecha_contacto  = Column(DateTime, nullable=True)
    fecha_respuesta = Column(DateTime, nullable=True)
    creado_en   = Column(DateTime, default=func.now())

    # --- PERFILADO ---
    rubro       = Column(String, nullable=True)
    tamano      = Column(String, nullable=True)
    urgencia    = Column(String, nullable=True)
    score_ia    = Column(Integer, default=0)
    plan_recomendado = Column(String, nullable=True)
    pitch_sugerido = Column(Text, nullable=True)
    perfilado_en = Column(DateTime, nullable=True)

    # --- PILOTO AUTOMÁTICO ---
    piloto_automatico = Column(Boolean, default=False)
    automation_paso = Column(Integer, default=0)
    automation_ultimo_en = Column(DateTime, nullable=True)
    automation_activa_desde = Column(DateTime, nullable=True)

    aliado = relationship("Aliado", back_populates="prospectos")


# ─── AUDITORÍA LOG ───────────────────────────────────────────────────────────
class AuditoriaLog(Base):
    __tablename__ = "auditorias_log"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), nullable=True)
    ref_code = Column(String, index=True)
    dominio = Column(String, index=True)
    score = Column(Integer)
    email_capturado = Column(String, nullable=True)
    creado_en = Column(DateTime, default=func.now())

    aliado = relationship("Aliado")


# ─── BOLSA DE LEADS ──────────────────────────────────────────────────────────
class LeadBolsa(Base):
    __tablename__ = "bolsa_leads"

    id = Column(Integer, primary_key=True, index=True)
    empresa = Column(String, nullable=False)
    rubro = Column(String, nullable=False)
    nombre_contacto = Column(String, nullable=True)
    ciudad = Column(String, nullable=True)
    telefono = Column(String, nullable=False)
    whatsapp = Column(String, nullable=True)
    email = Column(String, nullable=True)
    estado = Column(String, default="disponible")
    resultado = Column(String, nullable=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), nullable=True)
    fecha_carga = Column(DateTime, default=datetime.now)
    fecha_reclamo = Column(DateTime, nullable=True)
    notif_24h_enviada = Column(Boolean, default=False)

    # --- MARKETPLACE ---
    tier = Column(String, default="basico")
    costo_creditos = Column(Integer, default=0)
    score_calidad = Column(Integer, default=50)
    notas_calificacion = Column(Text, nullable=True)

    # --- PRESENCIA DIGITAL (v1.6) ---
    web = Column(String, nullable=True)
    instagram = Column(String, nullable=True)
    tiene_web = Column(Boolean, default=False)
    tiene_redes = Column(Boolean, default=False)
    observacion = Column(Text, nullable=True)

    aliado = relationship("Aliado", backref="leads_bolsa")


# ─── TRANSACCIÓN DE CRÉDITOS ─────────────────────────────────────────────────
class TransaccionCredito(Base):
    __tablename__ = "transacciones_credito"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True)
    delta = Column(Integer, nullable=False)
    motivo = Column(String, nullable=False)
    referencia = Column(String, nullable=True)
    creado_en = Column(DateTime, default=func.now())

    aliado = relationship("Aliado")


# ─── COMUNIDAD ───────────────────────────────────────────────────────────────
class PostComunidad(Base):
    __tablename__ = "comunidad_posts"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"))
    tipo = Column(String, default="tip")
    titulo = Column(String, nullable=False)
    cuerpo = Column(Text, nullable=False)
    likes = Column(Integer, default=0)
    fijado = Column(Boolean, default=False)
    oculto = Column(Boolean, default=False)
    creado_en = Column(DateTime, default=func.now())

    aliado = relationship("Aliado")


class ComentarioComunidad(Base):
    __tablename__ = "comunidad_comentarios"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("comunidad_posts.id"))
    aliado_id = Column(Integer, ForeignKey("aliados.id"))
    cuerpo = Column(Text, nullable=False)
    creado_en = Column(DateTime, default=func.now())

    post = relationship("PostComunidad", backref="comentarios")
    aliado = relationship("Aliado")


# ─── AUTOMATION LOG ──────────────────────────────────────────────────────────
class AutomationLog(Base):
    __tablename__ = "automation_log"

    id = Column(Integer, primary_key=True, index=True)
    prospecto_id = Column(Integer, ForeignKey("prospectos.id"), index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True)
    paso = Column(Integer, nullable=False)
    canal = Column(String, default="email")
    asunto = Column(String, nullable=True)
    mensaje = Column(Text, nullable=True)
    exitoso = Column(Boolean, default=True)
    creado_en = Column(DateTime, default=func.now())


# ─── LINK DE PAGO (NUEVO) ────────────────────────────────────────────────────
class LinkPago(Base):
    __tablename__ = "links_pago"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True)
    prospecto_id = Column(Integer, ForeignKey("prospectos.id"), nullable=True)
    plan = Column(String, nullable=False)
    moneda = Column(String, nullable=False)           # 'ars' | 'usd'
    precio_usd = Column(Float, nullable=False)
    precio_ars = Column(Float, nullable=True)
    tipo_cambio = Column(Float, nullable=True)
    checkout_url = Column(Text, nullable=False)
    processor = Column(String, nullable=False)        # 'mercadopago' | 'paypal'
    external_ref = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime, nullable=True)
    estado = Column(String, default="activo")         # 'activo' | 'vencido' | 'pagado'

    aliado = relationship("Aliado")
    prospecto = relationship("Prospecto")


# ─── COMISIÓN (NUEVO) ────────────────────────────────────────────────────────
class Comision(Base):
    __tablename__ = "comisiones"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True)
    prospecto_id = Column(Integer, ForeignKey("prospectos.id"), nullable=True)
    link_pago_id = Column(Integer, ForeignKey("links_pago.id"), nullable=True)
    plan = Column(String, nullable=False)
    monto_plan_usd = Column(Float, nullable=False)
    comision_pct = Column(Float, nullable=False)
    comision_usd = Column(Float, nullable=False)
    nombre_cliente = Column(String, nullable=True)
    estado = Column(String, default="pendiente")      # 'pendiente' | 'abonada'
    processor = Column(String, nullable=True)         # 'mercadopago' | 'paypal'
    fecha_pago = Column(DateTime, nullable=True)
    fecha_abono = Column(DateTime, nullable=True)
    creado_en = Column(DateTime, default=func.now())

    aliado = relationship("Aliado")
    prospecto = relationship("Prospecto")
    link_pago = relationship("LinkPago")


# ─── ACADEMIA MÓDULOS (NUEVO) ────────────────────────────────────────────────
class AcademiaModulo(Base):
    __tablename__ = "academia_modulos"

    id = Column(Integer, primary_key=True, index=True)
    orden = Column(Integer, nullable=False)
    titulo = Column(String, nullable=False)
    descripcion = Column(Text, nullable=True)
    tipo = Column(String, nullable=False)             # 'video' | 'pdf' | 'texto'
    url_contenido = Column(Text, nullable=True)
    duracion_minutos = Column(Integer, nullable=True)
    activo = Column(Boolean, default=True)
    creado_en = Column(DateTime, default=func.now())


# ─── SOLICITUD DE COMPRA DE CRÉDITOS (v1.7) ──────────────────────────────────
class SolicitudCompraCreditos(Base):
    """Solicitud de compra de un paquete de créditos por transferencia bancaria.

    Flujo manual v1: el aliado elige un paquete, ve los datos bancarios + un
    código de referencia único, transfiere por su cuenta y avisa por WhatsApp.
    El admin verifica el monto contra `precio_ars` y confirma la solicitud, lo
    que dispara `_ajustar_creditos()` con motivo='compra_paquete'.

    Estados:
        pendiente   → recién creada, esperando pago + confirmación admin
        confirmada  → admin verificó la transferencia y acreditó los créditos
        rechazada   → admin marcó como inválida (con motivo en notas_admin)
        expirada    → pasaron 48hs sin confirmación, no se acreditó nada

    Idempotencia: la confirmación verifica que el estado siga 'pendiente' antes
    de acreditar. Doble click en "Confirmar" no acredita el doble.
    """
    __tablename__ = "solicitudes_compra_creditos"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True, nullable=False)

    # --- IDENTIFICACIÓN DEL PAQUETE (snapshot al crear) ---
    paquete_id = Column(String, nullable=False)       # 'impulso' | 'acelerador' | 'despegue'
    creditos   = Column(Integer, nullable=False)      # denormalizado: cantidad del paquete

    # --- PRECIO CONGELADO AL MOMENTO DE GENERAR LA SOLICITUD ---
    precio_usd       = Column(Float, nullable=False)  # precio del paquete en USD
    tipo_cambio_blue = Column(Float, nullable=False)  # cotización blue de dolarapi
    precio_ars       = Column(Float, nullable=False)  # = precio_usd × tipo_cambio, redondeado al alza

    # --- IDENTIFICACIÓN PARA CONCILIACIÓN ---
    codigo_referencia = Column(String, unique=True, index=True, nullable=False)  # ej: 'AVZ-A4F2'
    comprobante_url   = Column(Text, nullable=True)   # URL/path del comprobante subido (opcional)

    # --- ESTADO Y TIMESTAMPS ---
    estado        = Column(String, default="pendiente", index=True)   # ver docstring
    notas_admin   = Column(Text, nullable=True)       # motivo de rechazo o comentario interno
    creado_en     = Column(DateTime, default=func.now())
    expires_at    = Column(DateTime, nullable=False)  # creado_en + 48hs
    confirmado_en = Column(DateTime, nullable=True)   # cuándo el admin confirmó/rechazó/expiró

    aliado = relationship("Aliado")


# ─── CONSTANTES DE NEGOCIO ───────────────────────────────────────────────────
PLANES = {
    "Plan Base":         1050.0,
    "Plan Pro":          2900.0,
    "Plan Industrial":   4900.0,
    "Estrategico 360":   7500.0,
}

# Paquetes de créditos para recargar saldo en el marketplace de leads.
# Anclados en USD; el precio ARS se calcula al cambio del día con dolarapi
# blue al momento de generar la solicitud (ver SolicitudCompraCreditos).
# La clave del dict es el `paquete_id` que viaja por API.
PAQUETES_CREDITOS = {
    "impulso": {
        "nombre":       "Impulso",
        "creditos":     100,
        "precio_usd":   10.0,
        "descripcion":  "Para arrancar a explorar la bolsa de leads.",
        "destacado":    False,
        "orden":        1,
    },
    "acelerador": {
        "nombre":       "Acelerador",
        "creditos":     300,
        "precio_usd":   25.0,
        "descripcion":  "El más elegido. 17% de descuento sobre Impulso.",
        "destacado":    True,    # se marca como recomendado en el UI
        "orden":        2,
    },
    "despegue": {
        "nombre":       "Despegue",
        "creditos":     1000,
        "precio_usd":   70.0,
        "descripcion":  "Para aliados activos. 30% de descuento sobre Impulso.",
        "destacado":    False,
        "orden":        3,
    },
}

NIVELES = {
    "BASIC":   {"comision": 0.10, "requisito": 0,  "bono": False},
    "SILVER":  {"comision": 0.12, "requisito": 1,  "bono": True},
    "PREMIUM": {"comision": 0.15, "requisito": 2,  "bono": False},
    "ELITE":   {"comision": 0.20, "requisito": 5,  "bono": False},
}

CUOTAS_RECARGO = {
    1:  0.00,
    3:  0.08,
    6:  0.15,
    12: 0.28,
}

REPUTACION_BADGES = {
    "CLOSER":        {"label": "Closer",        "icono": "🎯", "desc": "Tasa de cierre ≥ 40%"},
    "RAPIDO":        {"label": "Rápido",        "icono": "⚡", "desc": "Contacta leads en < 6hs"},
    "FIEL":          {"label": "Fiel",          "icono": "🔥", "desc": "30+ días consecutivos activo"},
    "TOP_TICKET":    {"label": "Top Ticket",    "icono": "💎", "desc": "Ticket promedio ≥ USD 3.500"},
    "EMBAJADOR":     {"label": "Embajador",     "icono": "👑", "desc": "3+ sub-aliados vendiendo"},
    "BOLSA_MASTER":  {"label": "Bolsa Master",  "icono": "🏆", "desc": "Tasa de éxito en bolsa ≥ 30%"},
}
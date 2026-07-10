from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Numeric, LargeBinary
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
from datetime import datetime, timedelta


# ─── RED DE SUB-ALIADOS — OVERRIDE PASIVO DEL SPONSOR ────────────────────────
# Marcadores de nombre_cliente que identifican comisiones de RED (pasivas/override,
# acreditadas al SPONSOR por ventas de su sub-aliado). Se usan para excluirlas al
# contar "ventas propias" de un aliado — si no, un aliado que también recluta
# inflaría su propio conteo con el override que recibe de sus sub-aliados.
_MARCADORES_RED = ("RED:", "RED EQUIPO:")

# Niveles de override pasivo del sponsor según ventas PROPIAS (confirmadas, de
# por vida) del sub-aliado. Ordenado de mayor a menor mínimo: se toma el primer
# tier que el sub-aliado alcanza o supera. Sube solo, nunca baja — mismo criterio
# que BASIC→ELITE. Ver §6.2 de terminos-aliados.html (cualquier cambio acá debe
# reflejarse también ahí, con el preaviso de 15 días que prevé esa cláusula).
OVERRIDE_TIERS = [
    (5, 0.12),
    (3, 0.10),
    (1, 0.07),
    (0, 0.05),
]


def calcular_override_pct(ventas_count: int) -> float:
    """Devuelve el % de override (como fracción, ej. 0.07) que corresponde a un
    sub-aliado con `ventas_count` ventas propias confirmadas."""
    v = ventas_count or 0
    for minimo, pct in OVERRIDE_TIERS:
        if v >= minimo:
            return pct
    return 0.05


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
    # Clics recibidos en /alianzas?ref={ref_code} (lo incrementa POST /track/clic-reclutamiento,
    # llamado desde alianzas.html al detectar el parámetro ?ref= en la URL).
    clics_reclutamiento = Column(Integer, default=0)

    # --- TRACKING DE LOGIN ---
    ultimo_login = Column(DateTime, nullable=True)
    cantidad_logins = Column(Integer, default=0)

    # --- TRACKING DE INSTALACIÓN PWA ---
    # pwa_instalada es un flag "ratchet": una vez True, no se vuelve a poner
    # False (si desinstala, no nos enteramos — solo sabemos si ALGUNA VEZ
    # corrió standalone). pwa_detectado_en es la última vez que el frontend
    # reportó su estado, instalado o no (sirve para saber si el dato es viejo).
    pwa_instalada = Column(Boolean, default=False)
    pwa_detectado_en = Column(DateTime, nullable=True)

    # --- ONBOARDING ---
    onboarding_completado = Column(Boolean, default=False)
    # Flags para la secuencia de emails de onboarding (día 1 / 3 / 7).
    # SIN estas columnas en el modelo SQLAlchemy no las carga ni las guarda,
    # haciendo que el scheduler reenvíe los emails cada 24h indefinidamente.
    onboarding_email_d1_en = Column(DateTime, nullable=True)
    onboarding_email_d3_en = Column(DateTime, nullable=True)
    onboarding_email_d7_en = Column(DateTime, nullable=True)

    # --- REPUTACIÓN ---
    reputacion_score = Column(Integer, default=50)
    badges = Column(Text, default="[]")
    reputacion_calculada_en = Column(DateTime, nullable=True)

    # --- CRÉDITOS PARA MARKETPLACE ---
    creditos = Column(Integer, default=0)

    # --- JARVIS (gate de acceso: beta cerrada) ---
    # Default False: nadie usa JARVIS hasta habilitarlo. Para la beta se
    # habilitan 3-5 aliados a mano. Al abrir a todos, cambiar default a True.
    jarvis_habilitado = Column(Boolean, default=False)

    # --- JARVIS: prueba gratis de 7 días ---
    # Hasta esta fecha, JARVIS es gratis (no descuenta créditos ni bloquea).
    # Pasada la fecha, vuelve a cobrar créditos por acción y aplica el paywall.
    # El default (callable) corre en cada INSERT → todo aliado nuevo, por
    # cualquier vía de registro, arranca con 7 días de prueba sin tocar nada más.
    # Los aliados ya registrados se rellenan al arranque (backfill en main.py).
    jarvis_trial_fin = Column(DateTime, default=lambda: datetime.now() + timedelta(days=7))

    # --- PORTAL PÚBLICO ---
    portal_publico_activo = Column(Boolean, default=True)
    portal_publico_titular = Column(String, nullable=True)
    portal_publico_bio = Column(Text, nullable=True)
    portal_publico_foto_url = Column(String, nullable=True)  # URL de foto de perfil del aliado

    # --- EXPANSIÓN LATAM ---
    # ISO 3166-1 alpha-2: AR, MX, CO, CL, PE, UY, PY, BO, EC, VE...
    pais = Column(String, default="AR", index=True)
    # JSON array de strings: ["metalurgica","agro","logistica","clinica","tecnico"]
    # Permite posicionar el portal /p/{ref_code} en búsquedas por rubro y ciudad.
    rubros_especialidad = Column(Text, default="[]")

    # --- CANAL DE ALIADO ---
    # `tipo_aliado` queda como el canal PRIMARIO/origen (compatibilidad).
    # El puente entre canales (canales.py) usa los flags de abajo: un aliado
    # puede operar en los dos canales a la vez con una sola identidad.
    tipo_aliado = Column(String, default="canal1")

    # --- COBRO DE COMISIONES (NUEVO) ---
    cbu_alias = Column(String, nullable=True)
    # v2.1 — Métodos de cobro internacionales. OJO: estas dos columnas ya
    # existían en la tabla (ver ALTER TABLE en main.py) pero no estaban
    # mapeadas acá — sin el Column, setattr()/db.commit() en aliados.py no
    # las persistía de verdad (quedaban solo en el objeto en memoria durante
    # el request). Mapearlas es lo que hace que se guarden en Postgres.
    payment_method = Column(String, nullable=True)
    payment_info = Column(String, nullable=True)
    # v2.6 — Cobro de comisiones internacional (mejoras-metodos-cobro):
    # datos estructurados para `transferencia` (banco/titular/tipo/número,
    # formato según `pais`) y aclaración de qué es `payment_info` para wise
    # (email / teléfono / wisetag).
    cobro_banco = Column(String, nullable=True)
    cobro_titular = Column(String, nullable=True)
    cobro_numero_cuenta = Column(String, nullable=True)
    cobro_tipo_cuenta = Column(String, nullable=True)
    payment_info_tipo = Column(String, nullable=True)

    # --- CONTRATO DIGITAL (NUEVO) ---
    terminos_aceptados = Column(Boolean, default=False)
    terminos_aceptados_en = Column(DateTime, nullable=True)

    # --- NOTIFICACIONES DE INACTIVIDAD ---
    notif_inact_20d_en = Column(DateTime, nullable=True)
    notif_inact_30d_en = Column(DateTime, nullable=True)
    notif_inact_55d_en = Column(DateTime, nullable=True)

    # --- CANAL 1: ALERTA "MUCHOS CONTACTOS, CERO VENTAS" ---
    # Último aviso (campanita + WA opcional) por contactar muchas empresas sin
    # haber cerrado la primera venta. Ver jarvis_canal1.job_alerta_contactos_sin_venta.
    canal1_alerta_sin_venta_en = Column(DateTime, nullable=True)

    # --- SUSPENSIÓN Y ELIMINACIÓN AUTOMÁTICA POR INACTIVIDAD ---
    # Día 30 sin login → cuenta suspendida (activo=False) + este campo se setea.
    # Día 60 sin login  (= fecha_suspension_auto + 30d) → eliminación definitiva.
    fecha_suspension_auto     = Column(DateTime, nullable=True)
    fecha_eliminacion_programada = Column(DateTime, nullable=True)

    # --- BAJA VOLUNTARIA ---
    # El aliado pidió la baja desde el portal. La cuenta se suspende en el momento
    # y se elimina definitivamente a los 30 días (tiempo de gracia / arrepentimiento).
    baja_voluntaria_solicitada_en = Column(DateTime, nullable=True)
    baja_voluntaria_motivo        = Column(Text, nullable=True)

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

    @property
    def ventas_propias_count(self):
        """Cantidad de ventas CONFIRMADAS y propias del aliado (excluye las
        comisiones de RED/override que también se guardan como Venta, pero a
        nombre de su sponsor — ver _MARCADORES_RED). Es la base para calcular
        qué % de override cobra SU sponsor por sus ventas (ver §6.2 de
        terminos-aliados.html)."""
        return sum(
            1 for v in self.ventas
            if v.confirmada and not any(m in (v.nombre_cliente or "") for m in _MARCADORES_RED)
        )

    @property
    def override_pct_para_sponsor(self):
        """% (como fracción, ej. 0.07) que cobra MI sponsor por mis ventas,
        según MI propia cantidad de ventas. Sube solo, nunca baja. No confundir
        con `comision_pct`, que es lo que cobro YO por mis propias ventas."""
        return calcular_override_pct(self.ventas_propias_count)


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
    # --- REVISIÓN ADMIN (no confirmar + nota visible al aliado) ---
    # rechazado=True cuando el admin marca "No confirmar" (ej: el referido
    # se registró antes de que el cliente dijera que sí al plan). El aliado
    # ve nota_admin en su panel para saber por qué no se confirmó.
    rechazado = Column(Boolean, default=False)
    nota_admin = Column(Text, nullable=True)
    nota_admin_en = Column(DateTime, nullable=True)
    # Puente CRM → Referido: si el referido se creó desde la ficha de un
    # prospecto del CRM, acá queda el vínculo (idempotencia + badge en ficha).
    prospecto_id = Column(Integer, ForeignKey("prospectos.id"), nullable=True)

    aliado = relationship("Aliado", back_populates="referidos")
    venta = relationship("Venta", back_populates="referido", uselist=False)
    prospecto = relationship("Prospecto", back_populates="referido")

    # --- VISIBILIDAD DE IMPLEMENTACIÓN (delivery.py) ---
    # El aliado de Canal 2 arriesga una relación de años: necesita ver en qué
    # estado va la implementación de SU cliente, no quedar a ciegas tras referir.
    # Estados: sin_iniciar → onboarding → en_desarrollo → en_revision → entregado
    # (+ 'pausado' como estado lateral). Cada cambio deja rastro y avisa al aliado.
    # La venta vinculada se resuelve por la relación `venta` ya existente
    # (Venta.referido_id), no se duplica el FK para no ambiguar el join.
    estado_implementacion = Column(String, default="sin_iniciar", index=True)
    impl_actualizado_en = Column(DateTime, nullable=True)
    impl_eta = Column(String, nullable=True)            # ETA legible, ej "2 semanas"
    impl_historial = Column(Text, default="[]")          # JSON timeline de cambios
    impl_alerta_estancado_en = Column(DateTime, nullable=True)  # último aviso de estancamiento


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
    # 2FA TOTP (opt-in). totp_secret guarda la semilla base32; totp_enabled
    # solo se pone en True tras verificar el primer código (evita lockout por
    # un secret a medio configurar). Si totp_enabled es False, el login se
    # comporta como siempre (solo usuario + contraseña).
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)


# ─── AUDITORÍA DE ACCIONES ADMIN ─────────────────────────────────────────────
class AdminAuditLog(Base):
    """Bitácora de acciones sensibles del panel admin.

    Se escribe cuando un admin: aprueba/rechaza una solicitud de créditos,
    ajusta créditos manualmente, da de baja a un aliado, edita una venta,
    cambia el nivel de un aliado, libera leads, etc.

    Inmutable por diseño (no se debe UPDATE/DELETE — solo INSERT). El campo
    `entidad`/`entidad_id` permite filtrar por "todo lo que pasó con el
    aliado X" o "toda la historia de la solicitud Y".

    NO usar para acciones del aliado mismo (ese flujo es el de TransaccionCredito
    + AutomationLog) ni para auditorías de leads (eso es AuditoriaLog).
    """
    __tablename__ = "admin_audit_log"

    id            = Column(Integer, primary_key=True, index=True)
    admin_username = Column(String, index=True, nullable=False)
    via           = Column(String, nullable=True)   # 'jwt' | 'api_key'
    accion        = Column(String, index=True, nullable=False)   # 'aprobar_solicitud', 'ajustar_creditos', etc.
    entidad       = Column(String, index=True, nullable=True)    # 'aliado' | 'solicitud_creditos' | 'lead' | 'venta' | ...
    entidad_id    = Column(String, index=True, nullable=True)    # id o codigo
    detalle       = Column(Text,   nullable=True)                # JSON serializado con los campos antes/después
    ip            = Column(String, nullable=True)
    user_agent    = Column(String, nullable=True)
    creado_en     = Column(DateTime, default=func.now(), index=True)


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
    # --- ATRIBUCION DE EQUIPO (handoff setter->closer) ---
    setter_id = Column(Integer, nullable=True)  # id del aliado setter (sin FK para no ambiguar relaciones)
    setter_split_pct = Column(Float, nullable=True)
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

    # --- CRM v3.0: contacto estructurado, valor, cierre, etiquetas, próxima acción ---
    email             = Column(String, nullable=True)
    telefono          = Column(String, nullable=True)
    whatsapp          = Column(String, nullable=True)
    valor_usd         = Column(Float, nullable=True)
    fecha_cierre      = Column(DateTime, nullable=True)
    motivo_cierre     = Column(String, nullable=True)
    etiquetas         = Column(String, nullable=True)
    proxima_accion_en = Column(DateTime, nullable=True)

    aliado      = relationship("Aliado", back_populates="prospectos")
    actividades = relationship("ActividadProspecto", back_populates="prospecto", cascade="all, delete-orphan")
    contactos   = relationship("ContactoProspecto", back_populates="prospecto", cascade="all, delete-orphan")
    # Referido vinculado (si el aliado ya lo registró para venta desde la ficha).
    # selectin: 1 sola query extra al listar el pipeline, sin N+1.
    referido    = relationship("Referido", back_populates="prospecto", uselist=False, lazy="selectin")


# ─── ACTIVIDAD DE PROSPECTO (CRM v3.0: timeline + tareas) ─────────────────────
class ActividadProspecto(Base):
    __tablename__ = "actividades_prospecto"

    id            = Column(Integer, primary_key=True, index=True)
    prospecto_id  = Column(Integer, ForeignKey("prospectos.id"), index=True, nullable=False)
    aliado_id     = Column(Integer, ForeignKey("aliados.id"), index=True)
    tipo          = Column(String, nullable=False, default="nota")   # nota|llamada|whatsapp|email|reunion|tarea|sistema
    canal         = Column(String, nullable=True)
    descripcion   = Column(Text, nullable=True)
    creado_en     = Column(DateTime, default=func.now())
    # --- solo para tipo 'tarea' ---
    vence_en      = Column(DateTime, nullable=True)
    completada    = Column(Boolean, default=False)
    completada_en = Column(DateTime, nullable=True)
    # True cuando el job de recordatorios ya avisó por email que venció.
    recordatorio_enviado = Column(Boolean, default=False)

    prospecto = relationship("Prospecto", back_populates="actividades")

# ─── CONTACTO DE PROSPECTO (CRM v3.0 · Salto 3: varios interlocutores) ────────
class ContactoProspecto(Base):
    __tablename__ = "contactos_prospecto"

    id           = Column(Integer, primary_key=True, index=True)
    prospecto_id = Column(Integer, ForeignKey("prospectos.id"), index=True, nullable=False)
    nombre       = Column(String, nullable=False)
    rol          = Column(String, nullable=True)   # ej: Dueño, Compras, Técnico
    email        = Column(String, nullable=True)
    telefono     = Column(String, nullable=True)
    whatsapp     = Column(String, nullable=True)
    creado_en    = Column(DateTime, default=func.now())

    prospecto = relationship("Prospecto", back_populates="contactos")


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

    # --- MIS CAPTURAS (bandeja del aliado) ---
    # Datos extra que deja el lead en el magnet (antes solo iban a MailerLite).
    nombre   = Column(String, nullable=True)
    telefono = Column(String, nullable=True)
    # Puente a CRM: si el aliado convirtió esta captura en prospecto, acá queda
    # el link (idempotencia + botón "Ver en Mi CRM"), igual que en LeadBolsa.
    prospecto_id = Column(Integer, ForeignKey("prospectos.id"), nullable=True)
    # Cuándo el aliado vio la captura en su bandeja (badge de "nuevas").
    visto_en = Column(DateTime, nullable=True)

    aliado = relationship("Aliado")


# ─── NOVEDADES (centro de notificaciones in-app del aliado) ──────────────────
class Novedad(Base):
    """Aviso in-app para el aliado (campanita del portal). Complementa los
    emails: leads capturados, comisiones nuevas, tareas vencidas, etc.

    tipo: 'captura' | 'comision' | 'tarea' | 'sistema' | ...
    tab:  tab del portal a abrir al hacer click (ej: 'capturas', 'comisiones').
    """
    __tablename__ = "novedades"

    id        = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True, nullable=False)
    tipo      = Column(String, default="sistema")
    titulo    = Column(String, nullable=False)
    cuerpo    = Column(Text, nullable=True)
    tab       = Column(String, nullable=True)
    leida     = Column(Boolean, default=False)
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
    pais = Column(String, default="AR", index=True)
    telefono = Column(String, nullable=False)
    whatsapp = Column(String, nullable=True)
    email = Column(String, nullable=True)
    estado = Column(String, default="disponible")
    resultado = Column(String, nullable=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), nullable=True)
    fecha_carga = Column(DateTime, default=datetime.now)
    fecha_reclamo = Column(DateTime, nullable=True)
    notif_24h_enviada = Column(Boolean, default=False)

    # --- RECICLADO (reciclado.py) ---
    # Un lead trabajado y no cerrado NO vuelve crudo a la bolsa: registra el
    # intento, entra en cooldown ('nurture') y reaparece con su historial visible
    # para que el próximo no arranque a ciegas. Tras N reciclados → 'quemado'.
    # estado puede valer además: 'nurture' (en cooldown) | 'quemado' (retirado).
    intentos = Column(Integer, default=0)
    reciclados = Column(Integer, default=0)
    historial_intentos = Column(Text, default="[]")  # JSON: [{aliado, fecha, resultado, nota}]
    cooldown_hasta = Column(DateTime, nullable=True)
    # --- ATRIBUCION DE EQUIPO (handoff setter->closer) ---
    setter_id = Column(Integer, nullable=True)  # id del aliado setter (sin FK para no ambiguar relaciones)
    setter_split_pct = Column(Float, nullable=True)

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

    # --- CRM BRIDGE (v2.x) ---
    # Si el aliado convirtió este lead en un prospecto del CRM, acá queda el
    # link. Permite mostrar "Ver en Mi CRM" y hacer la conversión idempotente.
    prospecto_id = Column(Integer, ForeignKey("prospectos.id"), nullable=True)

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


class ReporteMalContacto(Base):
    """Reporte de un aliado indicando que un lead premium que compró tenía
    información de contacto inválida (teléfono inexistente, email rebotando,
    empresa cerrada, etc.). El admin valida y, si aprueba, se devuelven los
    créditos al saldo del aliado.

    Estados:
        pendiente   → recién creado, esperando revisión del admin
        aprobado    → admin validó, créditos devueltos vía _ajustar_creditos()
        rechazado   → admin descartó el reporte (con motivo en notas_admin)

    Reglas de negocio:
        - Solo se puede reportar dentro de las 72hs posteriores a la compra
        - Solo un reporte por (aliado_id, lead_id): unicidad lógica chequeada
          en el endpoint, no por constraint DB (para mantener historial limpio
          si se rechaza y se vuelve a reportar — pero v1 no lo permite)
    """
    __tablename__ = "reportes_mal_contacto"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True, nullable=False)
    lead_id = Column(Integer, ForeignKey("bolsa_leads.id"), index=True, nullable=False)
    motivo = Column(String, nullable=False)         # 'no_atiende' | 'numero_invalido' | 'empresa_cerrada' | 'datos_incorrectos' | 'otro'
    detalle = Column(Text, nullable=True)            # texto libre del aliado
    estado = Column(String, default="pendiente")     # pendiente | aprobado | rechazado
    creditos_devueltos = Column(Integer, default=0)  # se llena al aprobar
    creado_en = Column(DateTime, default=func.now())
    resuelto_en = Column(DateTime, nullable=True)
    resuelto_por = Column(String, nullable=True)     # username del admin que resolvió
    notas_admin = Column(Text, nullable=True)        # justificación de la decisión

    aliado = relationship("Aliado")
    lead = relationship("LeadBolsa")


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

    # --- FORO (Camino B) ---
    # categoria: 'pregunta' | 'mejora' | 'charla' | 'victoria'. Para posts viejos
    # (sin categoria) se deriva de `tipo` en el feed. `resuelto` aplica a preguntas;
    # `estado_mejora` aplica a pedidos de mejora del portal.
    categoria = Column(String, default="general", index=True)
    resuelto = Column(Boolean, default=False)
    estado_mejora = Column(String, nullable=True)   # recibido|evaluacion|planificado|hecho|descartado

    aliado = relationship("Aliado")


class ComentarioComunidad(Base):
    __tablename__ = "comunidad_comentarios"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("comunidad_posts.id"))
    aliado_id = Column(Integer, ForeignKey("aliados.id"))
    cuerpo = Column(Text, nullable=False)
    aceptada = Column(Boolean, default=False)   # respuesta aceptada por el autor de la pregunta
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
    processor = Column(String, nullable=False)        # 'mercadopago' | 'usdt'
    external_ref   = Column(String, nullable=True, index=True)
    usdt_address   = Column(String, nullable=True)            # dirección HD derivada para esta orden
    usdt_monto_exp = Column(Float,  nullable=True)            # monto USDT exacto esperado
    usdt_tx_hash   = Column(String, nullable=True, index=True) # txid cuando se confirma
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
    processor = Column(String, nullable=True)         # 'mercadopago' | 'usdt'
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


class AliadoModuloCompletado(Base):
    """Tracking de módulos de la Academia completados por aliado.
    Idempotente por par (aliado_id, modulo_id) — un módulo se completa una sola
    vez y otorga créditos una sola vez. El campo `creditos_otorgados` queda como
    auditoría histórica (si en el futuro cambia el monto del bonus, los registros
    viejos conservan lo que ya cobraron).
    """
    __tablename__ = "aliado_modulo_completado"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True, nullable=False)
    modulo_id = Column(Integer, ForeignKey("academia_modulos.id"), index=True, nullable=False)
    completado_en = Column(DateTime, default=func.now())
    creditos_otorgados = Column(Integer, default=0)

    aliado = relationship("Aliado")
    modulo = relationship("AcademiaModulo")


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

    # --- MONEDA DE PAGO ---
    # 'ars' = transferencia bancaria local (CBU/alias). Para aliados de Argentina.
    # 'usd' = pago en dólares (PayPal/Wise/USDT/banco USD). Para aliados internacionales.
    moneda = Column(String, default="ars", nullable=False, index=True)

    # --- PRECIO CONGELADO AL MOMENTO DE GENERAR LA SOLICITUD ---
    precio_usd       = Column(Float, nullable=False)  # precio del paquete en USD
    tipo_cambio_blue = Column(Float, nullable=False)  # cotización blue de dolarapi (1.0 si moneda='usd')
    precio_ars       = Column(Float, nullable=False)  # = precio_usd × tipo_cambio (igual a precio_usd si moneda='usd')

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


# ─── PLAN DE CONTINUIDAD (suscripciones recurrentes mensuales) ───────────────
# v1.5 — Cuando un cliente contrata un Plan de Continuidad mensual (Cuidado /
# Crecimiento / Escala / Liderazgo), se crea un registro acá. Mientras esté
# activo (fecha_baja IS NULL), el aliado correspondiente cobra el 10% mensual.
#
# Cada ciclo mensual genera una entrada en la tabla `comisiones` existente
# (con plan=`<nombre_plan> (recurrente)`), reutilizando toda la infraestructura
# de pago/abono que ya está en uso. Este modelo NO reemplaza a Comision; es
# la fuente que dispara cada Comision recurrente mensualmente.
class PlanContinuidadActivo(Base):
    __tablename__ = "planes_continuidad_activos"

    id = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True, nullable=False)
    nombre_cliente = Column(String, nullable=False)
    cliente_email = Column(String, nullable=True)            # opcional, para conciliar
    plan_continuidad = Column(String, nullable=False)        # 'Plan Cuidado' | 'Plan Crecimiento' | 'Plan Escala' | 'Plan Liderazgo'
    precio_mensual_usd = Column(Float, nullable=False)       # precio que paga el cliente cada mes
    comision_pct = Column(Float, default=0.10, nullable=False)  # fijo 10% para el aliado
    fecha_alta = Column(DateTime, default=func.now(), nullable=False)
    # --- ATRIBUCION DE EQUIPO (handoff setter->closer) ---
    setter_id = Column(Integer, nullable=True)  # id del aliado setter (sin FK para no ambiguar relaciones)
    setter_split_pct = Column(Float, nullable=True)
    fecha_baja = Column(DateTime, nullable=True)             # NULL = activo. Si tiene fecha, está dado de baja.
    motivo_baja = Column(Text, nullable=True)
    notas = Column(Text, nullable=True)

    aliado = relationship("Aliado")

    @property
    def activo(self) -> bool:
        return self.fecha_baja is None

    @property
    def comision_mensual_usd(self) -> float:
        return round(float(self.precio_mensual_usd) * float(self.comision_pct), 2)


# ─── RESET DE CONTRASEÑA ─────────────────────────────────────────────────────
class PasswordResetToken(Base):
    """Token de un solo uso para recuperación de contraseña de aliados.

    Flujo:
        1. POST /auth/recuperar  → genera token, lo guarda acá, envía email.
        2. El aliado abre el link con ?token=XXX en portal.html.
        3. POST /auth/resetear   → valida token (no usado, no expirado), cambia hash.

    El token se invalida en cuanto se usa (usado=True). No se borra físicamente
    para mantener trazabilidad de cuándo se usó cada reset.
    """
    __tablename__ = "password_reset_tokens"

    id        = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), nullable=False, index=True)
    token     = Column(String, unique=True, index=True, nullable=False)  # secrets.token_urlsafe(32)
    expira_en = Column(DateTime, nullable=False)   # creado_en + 1 hora
    usado     = Column(Boolean, default=False)
    creado_en = Column(DateTime, default=func.now())

    aliado = relationship("Aliado")


# ─── CONSTANTES DE NEGOCIO ───────────────────────────────────────────────────
PLANES = {
    "Plan Base":         1050.0,
    "Plan Pro":          2900.0,
    "Plan Industrial":   4900.0,
    "Estrategico 360":   7500.0,
}

# Planes de continuidad mensuales (suscripción). El aliado cobra 10% mensual
# del precio mientras el cliente mantenga el plan activo.
PLANES_CONTINUIDAD = {
    "Plan Cuidado":       80.0,
    "Plan Crecimiento":  170.0,
    "Plan Escala":       280.0,
    "Plan Liderazgo":    450.0,
}
COMISION_RECURRENTE_PCT = 0.10

# Paquetes de créditos para recargar saldo y usar Jarvis IA (asistente de
# ventas: análisis de leads, propuestas, seguimientos, objeciones, etc.).
# Los leads de la bolsa NO consumen créditos: son todos gratis.
# Anclados en USD; el precio ARS se calcula al cambio del día con dolarapi
# blue al momento de generar la solicitud (ver SolicitudCompraCreditos).
# La clave del dict es el `paquete_id` que viaja por API.
PAQUETES_CREDITOS = {
    "impulso": {
        "nombre":       "Impulso",
        "creditos":     100,
        "precio_usd":   10.0,
        "descripcion":  "Para arrancar a usar Jarvis IA en tus ventas.",
        "destacado":    False,
        "orden":        1,
    },
    "acelerador": {
        "nombre":       "Acelerador",
        "creditos":     300,
        "precio_usd":   25.0,
        "descripcion":  "El más elegido para Jarvis IA. 17% de descuento sobre Impulso.",
        "destacado":    True,    # se marca como recomendado en el UI
        "orden":        2,
    },
    "despegue": {
        "nombre":       "Despegue",
        "creditos":     1000,
        "precio_usd":   70.0,
        "descripcion":  "Para aliados que usan Jarvis IA a full. 30% de descuento sobre Impulso.",
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

REPUTACION_BADGES = {
    "CLOSER":        {"label": "Closer",        "icono": "🎯", "desc": "Tasa de cierre ≥ 40%"},
    "RAPIDO":        {"label": "Rápido",        "icono": "⚡", "desc": "Contacta leads en < 6hs"},
    "FIEL":          {"label": "Fiel",          "icono": "🔥", "desc": "30+ días consecutivos activo"},
    "TOP_TICKET":    {"label": "Top Ticket",    "icono": "💎", "desc": "Ticket promedio ≥ USD 3.500"},
    "EMBAJADOR":     {"label": "Embajador",     "icono": "👑", "desc": "3+ sub-aliados vendiendo"},
    "BOLSA_MASTER":  {"label": "Bolsa Master",  "icono": "🏆", "desc": "Tasa de éxito en bolsa ≥ 30%"},
}


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id        = Column(Integer, primary_key=True, index=True)
    aliado_id = Column(Integer, ForeignKey("aliados.id"), index=True)
    endpoint  = Column(Text, unique=True)
    p256dh    = Column(String)
    auth      = Column(String)
    creado_en = Column(DateTime, default=datetime.now)

# ─── EMAIL TRACKING (Hueco 1: analítica de correos) ──────────────────────────
class EmailEnviado(Base):
    """Un registro por cada email transaccional TAGGEADO con una campaña.
    Lo escribe enviar_email() SOLO cuando se le pasa `campania` (los correos
    sueltos sin tag no generan overhead). Mide apertura (pixel) y clic
    (redirect), y permite correlacionar con reactivación. Ver email_tracking.py.
    """
    __tablename__ = "emails_enviados"

    id           = Column(Integer, primary_key=True, index=True)
    token        = Column(String, unique=True, index=True, nullable=False)
    campania     = Column(String, index=True, nullable=False)   # ej 'inactividad_20d'
    aliado_id    = Column(Integer, ForeignKey("aliados.id"), index=True, nullable=True)
    destinatario = Column(String, nullable=True)
    asunto       = Column(String, nullable=True)
    enviado_en   = Column(DateTime, default=func.now(), index=True)
    abierto_en   = Column(DateTime, nullable=True)
    aperturas    = Column(Integer, default=0)
    click_en     = Column(DateTime, nullable=True)
    clicks       = Column(Integer, default=0)

    aliado = relationship("Aliado")

#  EQUIPOS (setter + closer)  Bloque 1: formacion del vinculo 
# Un Equipo es un vinculo SIMETRICO entre dos aliados que trabajan deals juntos.
# El rol (setter/closer) NO se fija aca: se define por deal segun la direccion
# del handoff (Bloque 2). Por eso guardamos un unico `setter_split_pct`: la
# fraccion de la comision del deal que se lleva el que actuo de setter; el closer
# se lleva el resto. El total que paga Avanza NO cambia: se reparte, no se suma.
class Equipo(Base):
    __tablename__ = "equipos"

    id = Column(Integer, primary_key=True, index=True)
    # aliado_a = quien envio la solicitud; aliado_b = quien la recibe/acepta.
    # El orden a/b es solo de origen; el vinculo es simetrico.
    aliado_a_id = Column(Integer, ForeignKey("aliados.id"), index=True, nullable=False)
    aliado_b_id = Column(Integer, ForeignKey("aliados.id"), index=True, nullable=False)

    # 'pendiente' (espera que b acepte) | 'activo' | 'rechazado' | 'disuelto'
    estado = Column(String, default="pendiente", index=True)

    # Fraccion de la comision que se lleva el SETTER en cada deal de equipo.
    # Default 0.40; ajustable 0.25-0.50 (la banda se valida en el router).
    setter_split_pct = Column(Float, default=0.40)

    creado_en     = Column(DateTime, default=func.now())
    confirmado_en = Column(DateTime, nullable=True)   # cuando b acepto
    disuelto_en   = Column(DateTime, nullable=True)

    aliado_a = relationship("Aliado", foreign_keys=[aliado_a_id])
    aliado_b = relationship("Aliado", foreign_keys=[aliado_b_id])


# ─── ONBOARDING (reemplazo de Tally) ─────────────────────────────────────────
# Respuestas del formulario de inicio que completa el cliente tras pagar, más
# los archivos que sube (logo, fotos, Excel, etc.). El binario de los archivos
# vive en Postgres; para migrar a R2/S3 se cambia _guardar_archivo() en
# onboarding.py y el campo `data` pasa a guardar la URL.
class OnboardingRespuesta(Base):
    __tablename__ = "onboarding_respuestas"

    id              = Column(Integer, primary_key=True, index=True)
    plan            = Column(String)                                  # base | pro | industrial | 360
    aliado_id       = Column(Integer, ForeignKey("aliados.id"), nullable=True)
    link_pago_id    = Column(Integer, nullable=True)                  # vínculo opcional con la venta
    cliente_nombre  = Column(String, nullable=True)
    cliente_email   = Column(String, nullable=True)
    respuestas_json = Column(Text)                                    # todas las respuestas en JSON
    creado_en       = Column(DateTime, default=func.now())

    archivos = relationship(
        "OnboardingArchivo",
        back_populates="respuesta",
        cascade="all, delete-orphan",
    )


class OnboardingArchivo(Base):
    __tablename__ = "onboarding_archivos"

    id           = Column(Integer, primary_key=True, index=True)
    respuesta_id = Column(Integer, ForeignKey("onboarding_respuestas.id"))
    campo        = Column(String)                  # id del campo del form (ej: logo_fotos)
    filename     = Column(String)
    content_type = Column(String, nullable=True)
    data         = Column(LargeBinary)             # binario del archivo
    subido_en    = Column(DateTime, default=func.now())

    respuesta = relationship("OnboardingRespuesta", back_populates="archivos")


class EventoUso(Base):
    """Tracking de uso del portal: qué tabs abren los aliados y en qué
    botones/herramientas clave clickean. Alimenta el panel admin "Uso del
    Portal" para ver qué se usa mucho y qué no se usa nunca (candidato a
    sacar). Best-effort: nunca debe romper la experiencia del aliado si
    falla el insert (ver eventos_uso.py)."""

    __tablename__ = "eventos_uso"

    id         = Column(Integer, primary_key=True, index=True)
    aliado_id  = Column(Integer, ForeignKey("aliados.id"), nullable=True, index=True)
    evento     = Column(String, nullable=False)   # "tab_view" | "click"
    detalle    = Column(String, nullable=False)   # nombre del tab o id del botón/feature
    canal      = Column(String, nullable=True)    # tipo_aliado del aliado al momento del evento
    creado_en  = Column(DateTime, default=func.now(), index=True)
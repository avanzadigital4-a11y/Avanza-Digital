# -*- coding: utf-8 -*-
"""
jarvis_contratos.py — Generador de contratos de prestación de servicios (PDF).

Mismo mecanismo que jarvis_propuestas.py: toma datos (cliente + plan + datos
fiscales) y devuelve un documento armado. Acá el documento final es un PDF
renderizado con WeasyPrint a partir de una plantilla HTML+CSS.

Uso típico desde una ruta FastAPI (ver jarvis_contratos_routes.py):

    import jarvis_contratos as contratos
    datos = contratos.datos_desde_venta(venta, extra={...campos fiscales...})
    pdf_bytes = contratos.render_contrato_pdf(datos)

El Anexo I (entregables) se autocompleta desde PLAN_INFO, que replica
CONTENIDOS_POR_PLAN / PLANES_AVANZA de jarvis_propuestas.py. Si más adelante
querés una única fuente de verdad, mové PLAN_INFO a jarvis_config.py y
que ambos módulos lo importen de ahí.

Nota: el texto legal es una base profesional pero NO sustituye la revisión
de un abogado matriculado. Revisalo antes de usarlo en producción.
"""
from __future__ import annotations

import os
import datetime
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# 1) DATOS DE LA EMPRESA (constantes) — editá una sola vez acá o por variables
#    de entorno. Idealmente esto va a jarvis_config.py.
# ──────────────────────────────────────────────────────────────────────────────
AVANZA = {
    "razon_social": os.environ.get("AVANZA_RAZON_SOCIAL", "Avanza Digital"),
    "cuit":         os.environ.get("AVANZA_CUIT", "[CUIT de Avanza]"),
    "domicilio":    os.environ.get("AVANZA_DOMICILIO", "Santa Fe, Argentina"),
    "representante":os.environ.get("AVANZA_REPRESENTANTE", "[representante de Avanza]"),
    "cargo":        os.environ.get("AVANZA_CARGO", "Titular"),
    "email":        os.environ.get("AVANZA_EMAIL", "avanzadigital4@gmail.com"),
    "jurisdiccion": os.environ.get("AVANZA_JURISDICCION", "Santa Fe"),
    "web":          "avanzadigital.digital",
    # Logo opcional: ruta a un PNG/SVG local o data-URI. Si no existe, se usa
    # un wordmark tipográfico.
    "logo_path":    os.environ.get("AVANZA_LOGO_PATH", ""),
}

# ──────────────────────────────────────────────────────────────────────────────
# 2) INFO POR PLAN — precio, plazo, garantía, capacitación y entregables.
#    (Replica jarvis_propuestas.PLANES_AVANZA + CONTENIDOS_POR_PLAN.)
# ──────────────────────────────────────────────────────────────────────────────
PLAN_INFO = {
    "Plan Base": {
        "precio": 1050.0,
        "plazo_dias": 7,
        "garantia": "90 (noventa) días corridos",
        "capacitacion": 0,
        "sla": False,
        "entregables": [
            "Sitio web profesional hasta 5 páginas",
            "Diseño adaptado al rubro",
            "Optimización SEO básica",
            "Formulario de contacto y WhatsApp integrado",
            "Alta en Google Maps y Google Business",
            "Panel de administración simple",
        ],
    },
    "Plan Pro": {
        "precio": 2900.0,
        "plazo_dias": 14,
        "garantia": "90 (noventa) días corridos",
        "capacitacion": 1,
        "sla": False,
        "entregables": [
            "Sitio web profesional ilimitado",
            "Diseño premium adaptado al sector industrial",
            "SEO avanzado con posicionamiento local y sectorial",
            "Sistema de captación de leads con CRM básico",
            "Integración WhatsApp Business con respuesta automática",
            "Formularios inteligentes de cotización",
            "Alta y gestión de Google Business Profile",
            "Informe mensual de performance y leads generados",
            "Soporte prioritario con SLA de 24 hs",
        ],
    },
    "Plan Industrial": {
        "precio": 4900.0,
        "plazo_dias": 21,
        "garantia": "90 (noventa) días corridos",
        "capacitacion": 1,
        "sla": True,
        "entregables": [
            "Todo el Plan Pro incluido",
            "Catálogo de productos/servicios con fichas técnicas",
            "Sistema de cotización online automatizado",
            "Integración con CRM propio del cliente",
            "SEO industrial avanzado — posicionamiento por producto y proceso",
            "Landings específicas por línea de producto o servicio",
            "Campañas de Google Ads industriales básicas",
            "Informe quincenal con análisis de competencia",
            "Consultoría estratégica mensual (1 h)",
            "IA JARVIS para el equipo comercial incluida",
            "Panel de métricas en tiempo real",
        ],
    },
    "Estrategico 360": {
        "precio": 7500.0,
        "plazo_dias": 30,
        "garantia": "12 (doce) meses",   # garantía extendida prometida en el brochure
        "capacitacion": 2,
        "sla": True,
        "entregables": [
            "Todo el Plan Industrial incluido",
            "Desarrollo a medida / intranet",
            "Integración con ERP (Tango, SAP) — bidireccional",
            "Automatización condicional y Lead Scoring",
            "Estrategia digital integral a 12 meses",
            "Campañas de Google Ads y LinkedIn Ads industriales",
            "Automatización de marketing y nurturing de leads",
            "IA JARVIS completa con módulos avanzados",
            "Consultoría estratégica quincenal (2 h)",
            "Gerente de cuenta dedicado",
            "Reporte ejecutivo mensual con ROI medido",
            "Garantía extendida (12 meses) y Soporte con SLA",
        ],
    },
}

# Aliases tolerantes para el campo `plan` (que puede venir con/sin acento, etc.)
_ALIASES = {
    "base": "Plan Base",
    "planbase": "Plan Base",
    "pro": "Plan Pro",
    "planpro": "Plan Pro",
    "industrial": "Plan Industrial",
    "planindustrial": "Plan Industrial",
    "estrategico360": "Estrategico 360",
    "estrategico": "Estrategico 360",
    "planestrategico360": "Estrategico 360",
    "estrategico360grados": "Estrategico 360",
}

MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def normalizar_plan(plan: Optional[str]) -> str:
    """Mapea cualquier variante del nombre del plan a la clave canónica."""
    if not plan:
        return "Plan Pro"
    key = _strip_accents(plan).lower().replace(" ", "").replace("-", "")
    key = key.replace("°", "").replace("º", "")
    if key in _ALIASES:
        return _ALIASES[key]
    # match laxo por substring
    for alias, canon in _ALIASES.items():
        if alias in key:
            return canon
    return plan if plan in PLAN_INFO else "Plan Pro"


# ──────────────────────────────────────────────────────────────────────────────
# 3) NÚMERO A LETRAS (es) — para "monto en letras". Cubre enteros hasta millones.
# ──────────────────────────────────────────────────────────────────────────────
_UNID = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
         "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis",
         "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós",
         "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete",
         "veintiocho", "veintinueve"]
_DECENAS = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta",
            "ochenta", "noventa"]
_CENTENAS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos",
             "seiscientos", "setecientos", "ochocientos", "novecientos"]


def _centenas_a_letras(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "cien"
    c, r = divmod(n, 100)
    out = _CENTENAS[c] if c else ""
    if r:
        if r < 30:
            dec = _UNID[r]
        else:
            d, u = divmod(r, 10)
            dec = _DECENAS[d] + (f" y {_UNID[u]}" if u else "")
        out = (out + " " + dec).strip()
    return out.strip()


def numero_a_letras(n: int) -> str:
    """Entero positivo → palabras en español. Fallback seguro a str(n)."""
    try:
        n = int(round(float(n)))
    except Exception:
        return str(n)
    if n == 0:
        return "cero"
    partes = []
    millones, resto = divmod(n, 1_000_000)
    miles, cent = divmod(resto, 1000)
    if millones:
        partes.append("un millón" if millones == 1
                       else f"{numero_a_letras(millones)} millones")
    if miles:
        partes.append("mil" if miles == 1 else f"{_centenas_a_letras(miles)} mil")
    if cent:
        partes.append(_centenas_a_letras(cent))
    return " ".join(p for p in partes if p).strip()


# ──────────────────────────────────────────────────────────────────────────────
# 4) MODELO DE DATOS DEL CONTRATO
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class DatosContrato:
    # Cliente (lo que el aliado completa en el mini-formulario)
    cliente_razon_social: str = ""
    cliente_cuit: str = ""
    cliente_domicilio: str = ""
    cliente_representante: str = ""
    cliente_cargo: str = ""
    cliente_email: str = ""

    # Operación
    plan: str = "Plan Pro"
    precio_usd: Optional[float] = None          # si None, se toma de PLAN_INFO
    moneda: str = "USD"                          # "USD" | "ARS"
    precio_ars: Optional[float] = None
    tipo_cambio: Optional[float] = None
    factura_tipo: str = "B"                      # "A" | "B"
    iva_incluido: bool = True
    forma_pago: str = "pago único, sin costo mensual obligatorio"
    anticipo_pct: int = 100                      # 100 = todo por adelantado
    link_pago: str = ""

    # Lugar y fecha de firma
    ciudad: str = "Santa Fe"
    fecha: Optional[datetime.date] = None        # default: hoy

    # Overrides opcionales
    plazo_dias: Optional[int] = None
    garantia: Optional[str] = None
    capacitacion: Optional[int] = None
    entregables: Optional[list[str]] = None

    # Metadatos
    aliado_nombre: str = ""
    venta_id: Optional[int] = None

    def __post_init__(self):
        self.plan = normalizar_plan(self.plan)
        info = PLAN_INFO.get(self.plan, PLAN_INFO["Plan Pro"])
        if self.precio_usd is None:
            self.precio_usd = info["precio"]
        if self.plazo_dias is None:
            self.plazo_dias = info["plazo_dias"]
        if self.garantia is None:
            self.garantia = info["garantia"]
        if self.capacitacion is None:
            self.capacitacion = info["capacitacion"]
        if self.entregables is None:
            self.entregables = list(info["entregables"])
        if self.fecha is None:
            self.fecha = datetime.date.today()


def datos_desde_venta(venta, extra: Optional[dict] = None) -> DatosContrato:
    """
    Construye DatosContrato a partir de un objeto ORM `Venta`
    (models.Venta: nombre_cliente, plan, valor_usd, aliado...) más los campos
    fiscales que el aliado completa en `extra`.
    """
    extra = extra or {}
    aliado = getattr(venta, "aliado", None)
    fecha = extra.get("fecha")
    if isinstance(fecha, str) and fecha:
        try:
            fecha = datetime.date.fromisoformat(fecha)
        except Exception:
            fecha = None

    return DatosContrato(
        cliente_razon_social = extra.get("cliente_razon_social") or getattr(venta, "nombre_cliente", "") or "",
        cliente_cuit         = extra.get("cliente_cuit", ""),
        cliente_domicilio    = extra.get("cliente_domicilio", ""),
        cliente_representante= extra.get("cliente_representante", ""),
        cliente_cargo        = extra.get("cliente_cargo", ""),
        cliente_email        = extra.get("cliente_email", ""),
        plan                 = getattr(venta, "plan", "Plan Pro") or "Plan Pro",
        precio_usd           = extra.get("precio_usd", getattr(venta, "valor_usd", None)),
        moneda               = extra.get("moneda", "USD"),
        precio_ars           = extra.get("precio_ars"),
        tipo_cambio          = extra.get("tipo_cambio"),
        factura_tipo         = extra.get("factura_tipo", "B"),
        iva_incluido         = bool(extra.get("iva_incluido", True)),
        forma_pago           = extra.get("forma_pago", "pago único, sin costo mensual obligatorio"),
        anticipo_pct         = int(extra.get("anticipo_pct", 100)),
        link_pago            = extra.get("link_pago", ""),
        ciudad               = extra.get("ciudad", "Santa Fe"),
        fecha                = fecha,
        plazo_dias           = extra.get("plazo_dias"),
        garantia             = extra.get("garantia"),
        capacitacion         = extra.get("capacitacion"),
        entregables          = extra.get("entregables"),
        aliado_nombre        = getattr(aliado, "nombre", "") if aliado else extra.get("aliado_nombre", ""),
        venta_id             = getattr(venta, "id", None),
    )


# ──────────────────────────────────────────────────────────────────────────────
# 5) HELPERS DE PRESENTACIÓN
# ──────────────────────────────────────────────────────────────────────────────
def _esc(s) -> str:
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _campo(valor: str, etiqueta: str) -> str:
    """Muestra el valor o, si está vacío, un marcador a completar a mano."""
    valor = (valor or "").strip()
    if valor:
        return f"<strong>{_esc(valor)}</strong>"
    return f'<span class="pendiente">[completar: {_esc(etiqueta)}]</span>'


def _precio_str(d: DatosContrato) -> str:
    usd = f"USD {d.precio_usd:,.0f}".replace(",", ".")
    letras = numero_a_letras(d.precio_usd)
    base = f"{usd} (dólares estadounidenses {letras})"
    if d.moneda == "ARS" and d.precio_ars:
        ars = f"ARS {d.precio_ars:,.0f}".replace(",", ".")
        tc = f" — tipo de cambio de referencia: {d.tipo_cambio:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".") if d.tipo_cambio else ""
        base += f", pagadero en pesos por {ars}{tc}"
    return base


def _logo_html() -> str:
    path = AVANZA["logo_path"]
    if path and (path.startswith("data:") or os.path.exists(path)):
        src = path if path.startswith("data:") else f"file://{os.path.abspath(path)}"
        return f'<img class="logo-img" src="{src}" alt="Avanza Digital">'
    return ('<div class="wordmark">» Avanza <span>Digital</span></div>'
            '<div class="tagline">Sistemas comerciales automatizados B2B</div>')


# ──────────────────────────────────────────────────────────────────────────────
# 6) CSS (impresión / WeasyPrint)
# ──────────────────────────────────────────────────────────────────────────────
CSS = """
@page {
  size: A4;
  margin: 22mm 20mm 20mm 20mm;
  @bottom-center {
    content: "Avanza Digital · avanzadigital.digital · Santa Fe, Argentina";
    font-size: 7.5pt; color: #9aa3b2; letter-spacing: .3px;
  }
  @bottom-right {
    content: "Página " counter(page) " de " counter(pages);
    font-size: 7.5pt; color: #9aa3b2;
  }
}
* { box-sizing: border-box; }
body {
  font-family: "DejaVu Sans", "Inter", Arial, sans-serif;
  font-size: 9.6pt; line-height: 1.5; color: #1f2733; margin: 0;
}
.header { border-bottom: 2px solid #1463ff; padding-bottom: 10px; margin-bottom: 16px; }
.logo-img { height: 34px; }
.wordmark { font-size: 18pt; font-weight: 800; color: #0b1b3a; letter-spacing: -.5px; }
.wordmark span { color: #1463ff; }
.tagline { font-size: 8pt; color: #6b7280; letter-spacing: 2px; text-transform: uppercase; margin-top: 2px; }
h1 { font-size: 13pt; text-align: center; color: #0b1b3a; margin: 10px 0 2px; letter-spacing: .3px; }
.subtitulo { text-align: center; font-size: 8.5pt; color: #6b7280; margin: 0 0 16px; }
.intro { text-align: justify; margin-bottom: 12px; }
.partes { background: #f5f8ff; border: 1px solid #dbe6ff; border-radius: 6px; padding: 10px 12px; margin-bottom: 14px; }
.partes p { margin: 4px 0; }
h2 { font-size: 9.6pt; color: #0b1b3a; margin: 14px 0 4px; border-left: 3px solid #1463ff; padding-left: 8px; }
p { margin: 5px 0; text-align: justify; }
ul { margin: 4px 0 8px 0; padding-left: 18px; }
li { margin: 2px 0; }
.pendiente { color: #c2410c; background: #fff4ed; padding: 0 3px; border-radius: 3px; font-weight: 600; }
.firma-tabla { width: 100%; border-collapse: collapse; margin-top: 26px; }
.firma-tabla td { width: 50%; border: 1px solid #cbd5e1; vertical-align: top; padding: 14px 12px; font-size: 8.6pt; }
.firma-tabla .rol { font-weight: 700; color: #0b1b3a; margin-bottom: 26px; }
.firma-linea { border-top: 1px solid #94a3b8; margin-top: 22px; padding-top: 3px; }
.anexo { page-break-before: always; }
.anexo h1 { text-align: left; font-size: 12pt; border-bottom: 1px solid #dbe6ff; padding-bottom: 6px; }
.anexo-meta { background: #f5f8ff; border: 1px solid #dbe6ff; border-radius: 6px; padding: 8px 12px; margin: 8px 0 12px; font-size: 9pt; }
.disclaimer { margin-top: 16px; font-size: 7.6pt; color: #94a3b8; font-style: italic; text-align: center; }
"""


# ──────────────────────────────────────────────────────────────────────────────
# 7) PLANTILLA — devuelve el HTML completo del contrato
# ──────────────────────────────────────────────────────────────────────────────
def render_html(d: DatosContrato) -> str:
    fecha = d.fecha
    dia = fecha.day
    mes = MESES_ES[fecha.month]
    anio = fecha.year
    precio_txt = _precio_str(d)
    iva_txt = ("Los importes consignados incluyen el IVA correspondiente."
               if d.iva_incluido else
               "A los importes consignados deberá adicionarse el IVA que corresponda según la normativa vigente.")
    cambio_txt = ("" if d.moneda == "ARS" else
                  " En caso de abonarse en pesos argentinos, la conversión se realizará al tipo de cambio "
                  "vendedor del Banco de la Nación Argentina vigente al día del efectivo pago.")
    sla_txt = (" Para el plan contratado se establece un Acuerdo de Nivel de Servicio (SLA) de respuesta "
               "de hasta 24 (veinticuatro) horas hábiles ante incidencias críticas reportadas durante el período de garantía."
               if PLAN_INFO.get(d.plan, {}).get("sla") else "")
    cap = d.capacitacion or 0
    cap_txt = (f"AVANZA brindará {cap} ({numero_a_letras(cap)}) sesión(es) de capacitación al equipo de EL CLIENTE "
               "sobre el uso y la operación del sistema entregado."
               if cap > 0 else
               "El plan contratado no incluye sesiones de capacitación presenciales; AVANZA entregará la documentación "
               "de uso correspondiente. Sesiones adicionales podrán contratarse por separado.")
    anticipo_txt = ("El precio se abonará en un 100% (cien por ciento) por adelantado, contra emisión de la factura, "
                    "dando inicio la implementación una vez acreditado el pago."
                    if d.anticipo_pct >= 100 else
                    f"El precio se abonará en un {d.anticipo_pct}% en concepto de anticipo a la firma y el "
                    f"{100 - d.anticipo_pct}% restante contra entrega y aceptación del sistema. La implementación dará "
                    "comienzo una vez acreditado el anticipo.")
    link = (f" a través del enlace de pago provisto por el asesor de AVANZA ({_esc(d.link_pago)})"
            if d.link_pago else
            " a través del enlace de pago provisto por el asesor de AVANZA, o por el medio que AVANZA indique")

    entregables_html = "".join(f"<li>{_esc(e)}</li>" for e in d.entregables)

    logo = _logo_html()

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>

<div class="header">{logo}</div>

<h1>CONTRATO DE PRESTACIÓN DE SERVICIOS</h1>
<p class="subtitulo">Implementación de sistema comercial digital</p>

<p class="intro">En la ciudad de {_campo(d.ciudad, "ciudad")}, a los <strong>{dia}</strong> días del mes de
<strong>{mes}</strong> de <strong>{anio}</strong>, entre las partes que a continuación se identifican, se celebra el
presente Contrato de Prestación de Servicios (en adelante, el «Contrato»):</p>

<div class="partes">
  <p><strong>EL PRESTADOR:</strong> {_esc(AVANZA['razon_social'])}, CUIT {_esc(AVANZA['cuit'])}, con domicilio en
     {_esc(AVANZA['domicilio'])}, representada en este acto por {_esc(AVANZA['representante'])}
     ({_esc(AVANZA['cargo'])}) (en adelante, «AVANZA»).</p>
  <p><strong>EL CLIENTE:</strong> {_campo(d.cliente_razon_social, "razón social del cliente")},
     CUIT {_campo(d.cliente_cuit, "CUIT del cliente")}, con domicilio en {_campo(d.cliente_domicilio, "domicilio del cliente")},
     representada en este acto por {_campo(d.cliente_representante, "representante")}
     {('— ' + _esc(d.cliente_cargo)) if d.cliente_cargo else ''} (en adelante, «EL CLIENTE»).</p>
</div>

<p>AVANZA y EL CLIENTE se denominarán conjuntamente «las Partes». Las Partes acuerdan celebrar el presente Contrato
conforme a las siguientes cláusulas:</p>

<h2>CLÁUSULA PRIMERA — OBJETO</h2>
<p>AVANZA se obliga a diseñar, desarrollar e implementar para EL CLIENTE un sistema comercial digital correspondiente
al <strong>{_esc(d.plan)}</strong>, con el alcance y los entregables que se detallan en la Cláusula Segunda y en el
Anexo I, que forma parte integrante de este Contrato.</p>

<h2>CLÁUSULA SEGUNDA — ALCANCE Y ENTREGABLES</h2>
<p>El servicio comprende los entregables correspondientes al plan contratado, detallados en el <strong>Anexo I</strong>.
Cualquier desarrollo, funcionalidad o requerimiento no incluido expresamente en dicho anexo será considerado fuera de
alcance y, de ser solicitado, presupuestado por separado.</p>

<h2>CLÁUSULA TERCERA — PLAZO DE IMPLEMENTACIÓN</h2>
<p>AVANZA entregará el sistema en un plazo estimado de <strong>{d.plazo_dias}</strong> días hábiles, contados a partir
de (i) la acreditación del pago conforme a la Cláusula Cuarta y (ii) la entrega por parte de EL CLIENTE de la totalidad
de los contenidos, accesos y aprobaciones necesarios. Las demoras imputables a EL CLIENTE en la provisión de dichos
elementos extenderán el plazo en igual medida.</p>

<h2>CLÁUSULA CUARTA — PRECIO Y FORMA DE PAGO</h2>
<p>El precio total del servicio es de <strong>{precio_txt}</strong>, en concepto de {_esc(d.forma_pago)}. {iva_txt}{cambio_txt}</p>
<p>{anticipo_txt} El pago se efectuará{link}. EL CLIENTE recibirá la factura tipo <strong>{_esc(d.factura_tipo)}</strong>
correspondiente.</p>
<p>Nota: los planes de mantenimiento y evolución posteriores son opcionales y se contratan por separado, sin permanencia
mínima; no son requisito para el funcionamiento del sistema.</p>

<h2>CLÁUSULA QUINTA — ACEPTACIÓN DE ENTREGABLES</h2>
<p>Entregado el sistema, EL CLIENTE dispondrá de un plazo de 7 (siete) días corridos para revisarlo y notificar por
medio fehaciente las observaciones que correspondan al alcance del Anexo I. El plan incluye hasta 2 (dos) rondas de
ajustes sobre dichas observaciones. Transcurrido el plazo sin observaciones, o puesto el sistema en uso productivo por
EL CLIENTE, los entregables se tendrán por aceptados de conformidad, iniciándose el cómputo del período de garantía.</p>

<h2>CLÁUSULA SEXTA — PROPIEDAD DEL CÓDIGO Y LOS ENTREGABLES</h2>
<p>Una vez abonado el precio total, los derechos patrimoniales sobre el <strong>desarrollo a medida</strong> realizado
específicamente para EL CLIENTE (código fuente y entregables) quedan en propiedad del 100% de EL CLIENTE, sin licencias
de uso, alquileres ni pagos recurrentes obligatorios a favor de AVANZA. Quedan exceptuados los componentes, librerías,
frameworks y software de terceros (incluyendo soluciones de código abierto), que se entregan y continúan rigiéndose bajo
sus respectivas licencias de origen, sin que su titularidad pueda ser cedida. AVANZA conserva la propiedad de su
conocimiento técnico (know-how) y de los componentes genéricos y reutilizables de su autoría. AVANZA entregará el código
y los accesos correspondientes al finalizar la implementación.</p>

<h2>CLÁUSULA SÉPTIMA — HOSTING, DOMINIO, SSL Y CORREO</h2>
<p>El precio del plan <strong>no incluye</strong> el alojamiento (hosting), el registro o renovación del dominio, el
certificado de seguridad (SSL) ni las casillas de correo corporativo. EL CLIENTE podrá (i) alojar el sistema en su propia
infraestructura, o (ii) contratar el plan de mantenimiento e infraestructura de AVANZA (Plan Cuidado), de carácter
mensual y opcional. El dominio se registrará a nombre de EL CLIENTE. Hasta tanto EL CLIENTE provea su entorno de
alojamiento o contrate dicho plan, AVANZA no garantiza la disponibilidad en línea del sistema.</p>

<h2>CLÁUSULA OCTAVA — SERVICIOS Y SOFTWARE DE TERCEROS</h2>
<p>El funcionamiento de determinadas funcionalidades puede depender de servicios, plataformas o software de terceros
(por ejemplo: pasarelas de pago, WhatsApp Business API, servicios de inteligencia artificial, Google, y sistemas de
gestión o ERP como Tango o SAP). La contratación, las licencias, las cuentas y los costos —iniciales o recurrentes— de
dichos terceros son a cargo exclusivo de EL CLIENTE. AVANZA no responde por cambios de precio, condiciones,
disponibilidad o discontinuación de tales servicios de terceros.</p>

<h2>CLÁUSULA NOVENA — OBLIGACIONES DE LAS PARTES</h2>
<p><strong>De AVANZA:</strong> ejecutar el servicio conforme al alcance del Anexo I y en el plazo pactado; entregar el
código fuente y la documentación de acceso al finalizar; y brindar la capacitación y el soporte de garantía previstos en
este Contrato.</p>
<p><strong>De EL CLIENTE:</strong> abonar el precio en la forma y plazo establecidos; proveer en tiempo los contenidos,
accesos, marca y aprobaciones necesarios; y designar un responsable de contacto para la coordinación del proyecto.</p>

<h2>CLÁUSULA DÉCIMA — CAPACITACIÓN</h2>
<p>{cap_txt}</p>

<h2>CLÁUSULA UNDÉCIMA — GARANTÍA Y SOPORTE</h2>
<p>AVANZA garantiza el correcto funcionamiento del sistema entregado por un período de <strong>{_esc(d.garantia)}</strong>
desde la aceptación, corrigiendo sin cargo los defectos atribuibles al desarrollo.{sla_txt} Quedan excluidos de la
garantía los daños derivados de modificaciones realizadas por terceros, mal uso, o causas ajenas a AVANZA. Finalizado el
período de garantía, las correcciones, el mantenimiento y la evolución podrán contratarse mediante bolsas de horas o
planes de mantenimiento mensual, a la tarifa horaria vigente de AVANZA.</p>

<h2>CLÁUSULA DUODÉCIMA — PROTECCIÓN DE DATOS PERSONALES</h2>
<p>Las Partes se obligan a tratar los datos personales conforme a la Ley Nacional N.º 25.326 de Protección de Datos
Personales y normativa concordante. Respecto de los datos de los contactos y clientes finales captados a través del
sistema, EL CLIENTE reviste el carácter de responsable de la base de datos, y AVANZA actúa como encargada del tratamiento
únicamente durante la implementación y en la medida necesaria para prestar el servicio, sin utilizarlos para fines
propios. Finalizado el Contrato, AVANZA pondrá tales datos a disposición de EL CLIENTE y procederá a su eliminación de
sus entornos de trabajo, salvo obligación legal de conservación.</p>

<h2>CLÁUSULA DECIMOTERCERA — CONFIDENCIALIDAD</h2>
<p>Las Partes se obligan a mantener la confidencialidad de toda información a la que accedan con motivo de este Contrato,
y a no divulgarla ni utilizarla para fines distintos a su ejecución, obligación que subsistirá tras la finalización del
Contrato.</p>

<h2>CLÁUSULA DECIMOCUARTA — LIMITACIÓN DE RESPONSABILIDAD</h2>
<p>La responsabilidad total de AVANZA frente a EL CLIENTE, por cualquier concepto derivado de este Contrato, se limita al
monto efectivamente abonado por EL CLIENTE. AVANZA no responderá por lucro cesante ni por daños indirectos. Las garantías
de disponibilidad (uptime) o niveles de servicio publicadas con fines comerciales corresponden exclusivamente a los
planes de mantenimiento mensual y no resultan exigibles bajo la modalidad de pago único aquí contratada.</p>

<h2>CLÁUSULA DECIMOQUINTA — INDEMNIDAD POR CONTENIDOS DE EL CLIENTE</h2>
<p>EL CLIENTE declara que los contenidos, marcas, logotipos, textos e imágenes que provea para el desarrollo son de su
titularidad o cuenta con licencia suficiente para su uso, y mantendrá indemne a AVANZA frente a cualquier reclamo de
terceros derivado de dichos materiales.</p>

<h2>CLÁUSULA DECIMOSEXTA — FUERZA MAYOR</h2>
<p>Ninguna de las Partes será responsable por el incumplimiento o demora derivados de caso fortuito o fuerza mayor.
La Parte afectada lo notificará a la otra a la brevedad, suspendiéndose los plazos mientras dure el impedimento.</p>

<h2>CLÁUSULA DECIMOSÉPTIMA — CESIÓN</h2>
<p>Ninguna de las Partes podrá ceder su posición contractual ni los derechos y obligaciones emergentes de este Contrato
sin el consentimiento previo y por escrito de la otra Parte.</p>

<h2>CLÁUSULA DECIMOCTAVA — VIGENCIA Y RESCISIÓN</h2>
<p>El Contrato rige desde su firma hasta el cumplimiento total de las obligaciones de las Partes y la finalización del
período de garantía. Cualquiera de las Partes podrá rescindirlo ante el incumplimiento grave de la otra, previa
intimación por medio fehaciente y un plazo de 10 (diez) días para subsanar. En caso de rescisión por causa imputable a
EL CLIENTE luego de iniciada la implementación, AVANZA podrá retener los montos correspondientes a las tareas ya
ejecutadas.</p>

<h2>CLÁUSULA DECIMONOVENA — MODIFICACIONES</h2>
<p>Toda modificación al presente Contrato deberá constar por escrito y ser firmada por ambas Partes para tener validez.</p>

<h2>CLÁUSULA VIGÉSIMA — LEY APLICABLE Y JURISDICCIÓN</h2>
<p>El presente Contrato se rige por las leyes de la República Argentina. Para toda controversia derivada del mismo, las
Partes se someten a la jurisdicción de los tribunales ordinarios de {_esc(AVANZA['jurisdiccion'])}, renunciando a
cualquier otro fuero o jurisdicción que pudiera corresponder.</p>

<h2>CLÁUSULA VIGÉSIMA PRIMERA — DOMICILIOS Y NOTIFICACIONES</h2>
<p>Las Partes constituyen domicilio en los indicados en el encabezamiento, donde se tendrán por válidas todas las
notificaciones, así como en los correos electrónicos que declaren a tal efecto
(AVANZA: {_esc(AVANZA['email'])}{('; EL CLIENTE: ' + _esc(d.cliente_email)) if d.cliente_email else ''}).</p>

<p style="margin-top:12px;">En prueba de conformidad, las Partes firman dos ejemplares de un mismo tenor y a un solo
efecto, en el lugar y fecha indicados.</p>

<table class="firma-tabla">
  <tr>
    <td>
      <div class="rol">P/ AVANZA DIGITAL</div>
      <div class="firma-linea">Aclaración: {_esc(AVANZA['representante'])}</div>
      <div>Cargo: {_esc(AVANZA['cargo'])}</div>
      <div>DNI/CUIT: {_esc(AVANZA['cuit'])}</div>
      <div>Fecha: ____ / ____ / ______</div>
    </td>
    <td>
      <div class="rol">P/ EL CLIENTE</div>
      <div class="firma-linea">Aclaración: {_esc(d.cliente_representante) or '________________________'}</div>
      <div>Cargo: {_esc(d.cliente_cargo) or '________________________'}</div>
      <div>DNI/CUIT: {_esc(d.cliente_cuit) or '________________________'}</div>
      <div>Fecha: ____ / ____ / ______</div>
    </td>
  </tr>
</table>

<!-- ANEXO I -->
<div class="anexo">
  <h1>ANEXO I — DETALLE DEL PLAN CONTRATADO</h1>
  <div class="anexo-meta">
    <strong>Plan:</strong> {_esc(d.plan)} &nbsp;·&nbsp;
    <strong>Precio:</strong> USD {d.precio_usd:,.0f} &nbsp;·&nbsp;
    <strong>Plazo:</strong> {d.plazo_dias} días hábiles &nbsp;·&nbsp;
    <strong>Garantía:</strong> {_esc(d.garantia)}
  </div>
  <p><strong>Entregables incluidos:</strong></p>
  <ul>{entregables_html}</ul>
  <p class="disclaimer">Documento generado automáticamente por Avanza Digital. Modelo de base; ante dudas, consulte
  asesoramiento legal.</p>
</div>

</body></html>"""
    return html


# ──────────────────────────────────────────────────────────────────────────────
# 8) RENDER A PDF (WeasyPrint)
# ──────────────────────────────────────────────────────────────────────────────
def render_contrato_pdf(datos: DatosContrato) -> bytes:
    """Devuelve los bytes del PDF del contrato listo para descargar/enviar."""
    from weasyprint import HTML  # import perezoso: no rompe el arranque si falta
    html = render_html(datos)
    return HTML(string=html).write_pdf()


def nombre_archivo(datos: DatosContrato) -> str:
    base = (datos.cliente_razon_social or "cliente").strip()
    base = _strip_accents(base).lower()
    base = "".join(c if c.isalnum() else "_" for c in base).strip("_") or "cliente"
    return f"contrato_avanza_{base}_{datos.plan.replace(' ', '_').lower()}.pdf"
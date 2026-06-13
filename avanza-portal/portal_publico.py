"""
portal_publico.py — Landing pública del aliado (/p/{ref_code}) y satélites.

Décimo router migrado de main.py (tramo 5 del split). Contiene:
  - GET /p/{ref_code}: la landing personal del aliado (HTML completo inline:
    marca/bio/foto, planes con precios, modal de pago ARS/USD con datos de
    MercadoPago, USDT y Payoneer, SEO/OG/JSON-LD). Respeta el flag
    portal_publico_activo y devuelve 404 si está apagado.
  - GET /alias/{ref_code}: redirect 301 a /?ref= para URLs limpias.
  - GET /aliados/wa-publica/{ref_code}: datos públicos del aliado para la
    barra/card de la Auditoría Digital (sin auth).
  - PATCH /aliados/{codigo}/portal-publico: el aliado configura su landing
    (activo, titular, bio, foto https-only). Protegido por ownership.

Las constantes de pago (USDT_DIRECCION, USDT_RED, DATOS_PAYONEER) siguen en
main y se importan diferido dentro de /p/ — migran con el dominio de
checkout/pagos en su propio tramo.
"""
import os

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

import schemas
from auth import verify_ownership_dep
from database import get_db
from models import Aliado, PLANES, PLANES_CONTINUIDAD

router = APIRouter(tags=["portal_publico"])


# ── Puente diferido a helpers de main (evita import circular) ────────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


# ── Endpoint público: datos del aliado para la auditoría (barra + card) ───────
@router.get("/aliados/wa-publica/{ref_code}")
def aliado_wa_publica(ref_code: str, db: Session = Depends(get_db)):
    """
    Devuelve datos públicos del aliado para mostrar en la auditoría digital.
    No requiere autenticación.
    """
    a = db.query(Aliado).filter(
        Aliado.ref_code == ref_code,
        Aliado.activo == True
    ).first()
    if not a:
        return {
            "es_aliado": False,
            "nombre": None,
            "whatsapp": None,
            "foto_url": None,
            "titular": None,
            "bio": None,
        }
    titular = getattr(a, "portal_publico_titular", None) or a.nombre
    bio = getattr(a, "portal_publico_bio", None) or (
        f"Asesor digital · {a.ciudad}" if getattr(a, "ciudad", None) else "Partner Oficial · Avanza Digital"
    )
    foto_url = getattr(a, "portal_publico_foto_url", None)
    return {
        "es_aliado": True,
        "nombre": a.nombre,
        "titular": titular,
        "bio": bio,
        "whatsapp": a.whatsapp,
        "foto_url": foto_url,
        "ref_code": a.ref_code,
    }



@router.get("/alias/{ref_code}")
def alias_redirect(ref_code: str, db: Session = Depends(get_db)):
    """Redirige /alias/{ref_code} → /?ref={ref_code} si el aliado existe.
    Usado por el catch-all de _redirects para que cada aliado tenga una URL
    limpia (ej: avanzadigital.digital/gonzaloasesor) sin configuración manual.
    """
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code, Aliado.activo == True).first()
    if not a:
        raise HTTPException(status_code=404, detail="Aliado no encontrado")
    return RedirectResponse(url=f"https://avanzadigital.digital/?ref={ref_code}", status_code=301)



@router.get("/p/{ref_code}", response_class=HTMLResponse)
def portal_publico_aliado(ref_code: str, db: Session = Depends(get_db)):
    """Landing pública del aliado con su marca/bio y CTA de pago."""
    # Diferido: constantes de pago de main (evita import circular; migran con checkout en su tramo).
    from main import (DATOS_PAYONEER, TRON_MNEMONIC, TRON_XPUB,
                      USDT_DIRECCION, USDT_RED)
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code, Aliado.activo == True).first()
    if not a or not a.portal_publico_activo:
        return HTMLResponse("<h1>Portal no disponible</h1>", status_code=404)

    titular = a.portal_publico_titular or a.nombre
    bio = a.portal_publico_bio or (f"Asesor digital · {a.ciudad}" if getattr(a, 'ciudad', None) else "Asesor digital — Partner de Avanza Digital")
    foto_url = getattr(a, 'portal_publico_foto_url', None)
    _iniciales = "".join([w[0] for w in (titular or "A").split()[:2]]).upper() or "A"
    if foto_url:
        avatar_hero_html = f'<img src="{foto_url}" alt="{titular}" style="width:100%;height:100%;border-radius:50%;object-fit:cover;">'
    else:
        avatar_hero_html = f'<span style="font-size:2.3rem;font-weight:800;color:#fff;">{_iniciales}</span>'

    # ── Rubros y país para SEO ──────────────────────────────────────────────
    import json as _json
    _pais = getattr(a, 'pais', None) or 'AR'
    _pais_nombres = {
        'AR':'Argentina','MX':'México','CO':'Colombia','CL':'Chile',
        'PE':'Perú','UY':'Uruguay','PY':'Paraguay','BO':'Bolivia',
        'EC':'Ecuador','VE':'Venezuela',
    }
    _pais_nombre = _pais_nombres.get(_pais, _pais)
    _rubros_raw = getattr(a, 'rubros_especialidad', '[]') or '[]'
    try:
        _rubros = _json.loads(_rubros_raw)
    except Exception:
        _rubros = []
    _rubros_labels = {
        'metalurgica':'Metalúrgica','agro':'Agroindustria','logistica':'Logística',
        'clinica':'Clínicas y Salud','tecnico':'Servicios Técnicos','construccion':'Construcción',
        'transporte':'Transporte','alimentos':'Alimentos','textil':'Textil','otro':'Otros rubros',
    }
    _rubros_display = [_rubros_labels.get(r, r.title()) for r in _rubros]
    _rubros_seo = ', '.join(_rubros_display) if _rubros_display else 'PYMEs industriales'
    _ciudad = getattr(a, 'ciudad', None) or ''
    # Título SEO: "Gonzalo García · Asesor digital en metalúrgica y agro · Rosario, Argentina"
    _seo_title = f"{titular} · Asesor digital en {_rubros_seo} · {_ciudad + ', ' if _ciudad else ''}{_pais_nombre} | Avanza Digital"[:120]
    _seo_desc = f"{titular} es partner oficial de Avanza Digital en {_pais_nombre}. Especialista en digitalización de {_rubros_seo}. Contáctalo para implementar tu sistema de ventas B2B."[:160]
    # Badges de rubros para mostrar en el portal
    _rubros_badges_html = ''.join(
        f'<span style="display:inline-block;background:rgba(59,130,246,0.12);color:#93c5fd;border:1px solid rgba(59,130,246,0.2);padding:3px 10px;border-radius:20px;font-size:.72rem;font-weight:600;margin:3px 3px 3px 0;">{r}</span>'
        for r in _rubros_display
    ) if _rubros_display else ''

    # WhatsApp de contacto: del aliado si tiene, si no el de Avanza
    _wa_raw = (a.whatsapp or "").strip().replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    _wa_avanza = "5493424392759"
    wa_contacto = _wa_raw if _wa_raw else _wa_avanza
    wa_titular_encoded = titular.replace(" ", "%20")

    PLAN_DETALLE = {
        "Plan Base": {
            "emoji": "🚀",
            "tagline": "Para arrancar en 7 días",
            "includes": ["Sitio web + cotizador automático", "Formulario de calificación de leads", "Panel de seguimiento básico", "3 meses de soporte técnico"],
            "ideal": "Empresa que quiere su primer canal digital funcionando rápido."
        },
        "Plan Pro": {
            "emoji": "⚡",
            "tagline": "El más elegido por PYMEs industriales",
            "badge": "MÁS POPULAR",
            "includes": ["Todo el Plan Base", "CRM liviano integrado", "Automatizaciones de seguimiento", "Integración WhatsApp Business", "3 meses de soporte técnico"],
            "ideal": "Empresa con equipo de ventas que necesita proceso replicable."
        },
        "Plan Industrial": {
            "emoji": "🏭",
            "tagline": "Sistema completo de adquisición B2B",
            "includes": ["Todo el Plan Pro", "Landing de empresa personalizada", "Cotizador por rubro y producto", "Dashboard de métricas avanzado", "Secuencias de email automatizadas", "3 meses de soporte técnico"],
            "ideal": "Empresa que quiere ser el referente digital de su rubro."
        },
        "Estrategico 360": {
            "emoji": "🎯",
            "tagline": "Transformación comercial integral",
            "includes": ["Todo el Plan Industrial", "Auditoría comercial inicial", "Estrategia de contenidos B2B", "Capacitación del equipo comercial", "Revisiones mensuales 90 días", "Soporte prioritario 6 meses"],
            "ideal": "Empresa que quiere rediseñar su área comercial completa."
        },
    }
    # ── Grilla de planes estilo home (4 en una línea) con selector ──────────
    # Cada tarjeta tiene dos vistas: "Pago Único" (Sistema, desde PLANES) y
    # "Suscripción" (Mantenimiento mensual, desde PLANES_CONTINUIDAD).
    def _fmt_usd(v):
        return f"{int(v):,}".replace(",", ".")

    PLANES_VENTA = [
        {
            "key": "Plan Base", "accent": "",
            # Pago Único
            "u_badge": "Presencia Activa", "u_badge_style": "background:rgba(52,211,153,0.12);color:#34d399;border:1px solid rgba(52,211,153,0.3);",
            "u_emoji": "🏗️", "u_name": "PLAN BASE", "u_price": PLANES["Plan Base"],
            "u_desc": "Para pymes que hoy no generan consultas.",
            "u_items": ["Landing page profesional", "Mensaje comercial estratégico", "Formulario de contacto + WhatsApp", "Captación básica de leads", "Diseño responsive", "Presencia en Google Maps", "Configuración inicial de métricas"],
            "u_result": "Empiezan a recibir consultas.",
            # Suscripción
            "m_badge": "Estabilidad", "m_badge_style": "background:rgba(255,255,255,0.06);color:#a1a1aa;border:1px solid rgba(255,255,255,0.15);",
            "m_emoji": "🛡️", "m_name": "PLAN 1 — CUIDADO", "m_price": PLANES_CONTINUIDAD["Plan Cuidado"], "m_plan": "Plan Cuidado",
            "m_desc": "Para empresas que quieren estabilidad técnica.",
            "m_items": ["Hosting profesional de alta velocidad", "Dominio profesional (.com, .com.ar o local) y Certificado SSL", "Backups automáticos semanales", "Seguridad y monitoreo 24/7", "Soporte técnico por fallas del sistema", "Reporte básico mensual"],
        },
        {
            "key": "Plan Pro", "accent": "pop",
            "u_badge": "Generación de Leads", "u_badge_style": "background:rgba(59,130,246,0.15);color:#3b82f6;border:1px solid rgba(59,130,246,0.35);",
            "u_emoji": "⚡", "u_name": "PLAN PRO", "u_price": PLANES["Plan Pro"],
            "u_desc": "Para empresas que quieren un canal comercial.",
            "u_items": ["Todo lo del Plan Base, más:", "Web multi-sección (hasta 6 páginas)", "Lead magnet o formulario avanzado", "Integración con CRM / Email Marketing", "Automatización de respuesta", "Copywriting orientado a ventas"],
            "u_result": "Leads calificados y seguimiento.",
            "m_badge": "Más Popular", "m_badge_style": "background:rgba(59,130,246,0.15);color:#3b82f6;border:1px solid rgba(59,130,246,0.35);",
            "m_emoji": "🚀", "m_name": "PLAN 2 — CRECIMIENTO", "m_price": PLANES_CONTINUIDAD["Plan Crecimiento"], "m_plan": "Plan Crecimiento",
            "m_desc": "Para empresas que quieren más consultas calificadas.",
            "m_items": ["Todo lo del Plan Cuidado, más:", "1 Ajuste mensual de optimización", "Revisión de formularios y CTA", "Ajuste de textos comerciales", "Métricas de conversión mensuales", "Reunión trimestral de estrategia"],
        },
        {
            "key": "Plan Industrial", "accent": "",
            "u_badge": "Sistema Comercial", "u_badge_style": "background:rgba(250,204,21,0.12);color:#facc15;border:1px solid rgba(250,204,21,0.3);",
            "u_emoji": "🏭", "u_name": "PLAN INDUSTRIAL", "u_price": PLANES["Plan Industrial"],
            "u_desc": "Para empresas con procesos complejos.",
            "u_items": ["Todo lo del Plan Pro, más:", "Arquitectura de embudo de ventas", "Formularios segmentados", "Integración con CRM avanzado", "Panel de métricas en tiempo real", "Capacitación básica al equipo"],
            "u_result": "Web integrada al área comercial.",
            "m_badge": "Ventas Pro", "m_badge_style": "background:rgba(59,130,246,0.12);color:#60a5fa;border:1px solid rgba(59,130,246,0.3);",
            "m_emoji": "📈", "m_name": "PLAN 3 — ESCALA", "m_price": PLANES_CONTINUIDAD["Plan Escala"], "m_plan": "Plan Escala",
            "m_desc": "Para empresas con equipo de ventas activo.",
            "m_items": ["Todo lo del Plan Crecimiento, más:", "2 Ajustes mensuales de optimización", "Integración y revisión técnica de CRM", "Automatizaciones de seguimiento", "Revisión profunda de embudos", "Reporte avanzado de rendimiento"],
        },
        {
            "key": "Estrategico 360", "accent": "estr",
            "u_badge": "Transformación Total", "u_badge_style": "background:rgba(168,85,247,0.18);color:#c084fc;border:1px solid rgba(168,85,247,0.4);",
            "u_emoji": "🌐", "u_name": "ESTRATÉGICO 360", "u_price": PLANES["Estrategico 360"],
            "u_desc": "Para empresas complejas con visión de liderazgo.",
            "u_items": ["Todo lo del Plan Industrial, más:", "Desarrollo a medida (Cotizador / Intranet)", "Integración bidireccional (Tango, SAP, CRMs)", "Automatización condicional y Lead Scoring", "Plan estratégico B2B y Auditoría comercial", "Garantía extendida (12 meses) y Soporte SLA", "Creación de Lead Magnet y contenidos"],
            "u_result": "Ecosistema corporativo autónomo.",
            "m_badge": "Dirección Externa", "m_badge_style": "background:rgba(168,85,247,0.15);color:#c084fc;border:1px solid rgba(168,85,247,0.35);",
            "m_emoji": "👑", "m_name": "PLAN 4 — LIDERAZGO", "m_price": PLANES_CONTINUIDAD["Plan Liderazgo"], "m_plan": "Plan Liderazgo",
            "m_desc": "Tu departamento digital y estratégico tercerizado.",
            "m_items": ["Todo lo del Plan Escala, más:", "Soporte técnico prioritario (SLA 4hs)", "4 Ajustes mensuales de optimización", "Reunión estratégica quincenal", "Gestión de campañas de automatización", "Mantenimiento de integraciones ERP complejas"],
        },
    ]

    planes_cards = ""
    for p in PLANES_VENTA:
        accent = p["accent"]
        card_cls = ("pv-card " + accent).strip()
        btn_cls = "pv-btn-main estr" if accent == "estr" else "pv-btn-main"
        # Items (Pago Único)
        u_items_html = "".join(f"<li>{it}</li>" for it in p["u_items"])
        u_items_html += f'<li class="pv-result"><strong>Resultado:</strong> {p["u_result"]}</li>'
        # Items (Suscripción)
        m_items_html = "".join(f"<li>{it}</li>" for it in p["m_items"])
        # WhatsApp links
        _wa_u_msg = f"Hola%20{wa_titular_encoded}%2C%20me%20interesa%20el%20{p['key'].replace(' ', '%20')}%20(USD%20{int(p['u_price'])}%2C%20pago%20%C3%BAnico)%20de%20Avanza%20Digital.%20%C2%BFPodemos%20hablar%3F"
        _wa_u = f"https://wa.me/{wa_contacto}?text={_wa_u_msg}"
        _wa_m_msg = f"Hola%20{wa_titular_encoded}%2C%20me%20interesa%20el%20{p['m_plan'].replace(' ', '%20')}%20(USD%20{int(p['m_price'])}%2Fmes%2C%20mantenimiento)%20de%20Avanza%20Digital.%20%C2%BFPodemos%20hablar%3F"
        _wa_m = f"https://wa.me/{wa_contacto}?text={_wa_m_msg}"
        planes_cards += f"""
        <div class="{card_cls}">
          <div class="pv-unique">
            <span class="pv-badge" style="{p['u_badge_style']}">{p['u_badge']}</span>
            <h3 class="pv-name">{p['u_name']} {p['u_emoji']}</h3>
            <div class="pv-price">USD {_fmt_usd(p['u_price'])} <span>final</span></div>
            <p class="pv-desc">{p['u_desc']}</p>
            <ul class="pv-list">{u_items_html}</ul>
            <div class="pv-actions">
              <button class="{btn_cls}" onclick="abrirModal('{p['key']}','{ref_code}')">⚡ Contratar</button>
              <a class="pv-btn-wa" href="{_wa_u}" target="_blank" rel="noopener">💬 Consultar</a>
            </div>
          </div>
          <div class="pv-monthly" style="display:none;">
            <span class="pv-badge" style="{p['m_badge_style']}">{p['m_badge']}</span>
            <h3 class="pv-name">{p['m_name']} {p['m_emoji']}</h3>
            <div class="pv-price">USD {_fmt_usd(p['m_price'])} <span>/mes</span></div>
            <p class="pv-desc">{p['m_desc']}</p>
            <ul class="pv-list">{m_items_html}</ul>
            <div class="pv-actions">
              <button class="{btn_cls}" onclick="abrirModal('{p['m_plan']}','{ref_code}')">⚡ Contratar</button>
              <a class="pv-btn-wa" href="{_wa_m}" target="_blank" rel="noopener">💬 Consultar</a>
            </div>
          </div>
        </div>
        """
    planes_html = f'<div class="planes-grid-4">{planes_cards}</div>'

    usdt_activo = bool(USDT_DIRECCION or TRON_XPUB or TRON_MNEMONIC)

    usdt_activo = bool(USDT_DIRECCION)
    _btn_usd = ''  # Eliminado: el flujo USD cripto se maneja con el botón USDT/TRC20
    _btn_usdt = (
        '<button class="moneda-btn usdt" id="opt-usdt" onclick="seleccionarMoneda(\'usdt\')">'
        '<div class="icon">🪙</div><div class="label">USDT / USDC</div>'
        f'<div class="sublabel">{USDT_RED or "TRC20"}</div></button>'
        if usdt_activo else
        '<button class="moneda-btn" style="opacity:.35;cursor:not-allowed;" disabled>'
        '<div class="icon">🪙</div><div class="label">USDT no disponible</div>'
        '<div class="sublabel">Próximamente</div></button>'
    )
    _btn_payoneer = (
        '<button class="moneda-btn payoneer" id="opt-payoneer" onclick="seleccionarMoneda(\'payoneer\')">'
        '<div class="icon">💳</div><div class="label">USD Payoneer</div>'
        '<div class="sublabel">Transferencia USD</div></button>'
    )
    # Precio de cada plan para mostrarlo en el step de USDT
    # Precios para el JS del modal: incluye planes de sistema (PLANES) y
    # los mensuales de mantenimiento (PLANES_CONTINUIDAD) para que USDT,
    # Payoneer y MercadoPago muestren el monto correcto en ambos casos.
    _precios_todos = {**PLANES, **PLANES_CONTINUIDAD}
    _plan_precios_js = ", ".join(f'"{k}": {int(v)}' for k, v in _precios_todos.items())
    # Set de planes mensuales para que el JS sepa cuáles son "por mes".
    _planes_mensuales_js = ", ".join(f'"{k}": 1' for k in PLANES_CONTINUIDAD.keys())

    # ── Datos de cobro por MercadoPago (transferencia manual, solo mensuales) ──
    _mp_titular = os.environ.get("MP_TITULAR", "Iván Darío Galarza")
    _mp_alias   = os.environ.get("MP_ALIAS",   "avanzadigital")
    _mp_cvu     = os.environ.get("MP_CVU",     "0000003100061989560327")
    _usdt_dir_js = USDT_DIRECCION.replace("'", "\\'")
    _usdt_red_js = (USDT_RED or "TRC20").replace("'", "\\'")

    # ── URLs canónicas y Open Graph ────────────────────────────────────────
    SITE_BASE = "https://avanzadigital.digital"
    _canonical_url = f"{SITE_BASE}/p/{ref_code}"
    _og_image = foto_url if foto_url else f"{SITE_BASE}/og-default.png"

    # ── JSON-LD: LocalBusiness para rich snippets de Google ─────────────
    import json as _json_ld
    _ld_address = {}
    if _ciudad:
        _ld_address["addressLocality"] = _ciudad
    if _pais:
        _ld_address["addressCountry"] = _pais
    _ld_same_as = []
    if _wa_raw:
        _ld_same_as.append(f"https://wa.me/{_wa_raw}")
    _ld_data = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": titular,
        "description": _seo_desc,
        "url": _canonical_url,
        "image": _og_image,
        "areaServed": _pais_nombre,
        "knowsAbout": _rubros_display if _rubros_display else ["Digitalización de PYMEs", "Marketing B2B"],
        "memberOf": {
            "@type": "Organization",
            "name": "Avanza Digital",
            "url": SITE_BASE
        },
    }
    if _ld_address:
        _ld_data["address"] = {"@type": "PostalAddress", **_ld_address}
    if _ld_same_as:
        _ld_data["sameAs"] = _ld_same_as
    _ld_json = _json_ld.dumps(_ld_data, ensure_ascii=False)

    # ── Datos de Payoneer para la página de ventas: email + transferencia bancaria USD ──
    _py_email = DATOS_PAYONEER.get("destinatario", "")
    _pyb = DATOS_PAYONEER.get("banco") or {}
    def _pyb_row(label, val, mono=True, copiable=False):
        if not val:
            return ""
        _font = "monospace" if mono else "inherit"
        _val_js = str(val).replace(chr(92), chr(92)*2).replace("'", chr(92)+"'")
        _btn = (f'<button type="button" onclick="copiarTexto(this,&#39;{_val_js}&#39;)" '
                'style="background:rgba(255,163,26,0.15);color:#ffa31a;border:1px solid rgba(255,163,26,0.35);border-radius:6px;padding:0 10px;height:32px;font-weight:700;cursor:pointer;font-size:.74rem;white-space:nowrap;flex-shrink:0;">'
                '<i class="fa-solid fa-copy"></i></button>') if copiable else ""
        return (f'<div style="margin-bottom:9px;">'
                f'<div style="font-size:.66rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px;">{label}</div>'
                f'<div style="display:flex;gap:8px;align-items:center;">'
                f'<div style="flex:1;min-width:120px;font-family:{_font};font-size:.82rem;color:#e2e8f0;word-break:break-all;">{val}</div>'
                f'{_btn}</div></div>')
    if _pyb.get("cuenta") or _pyb.get("aba") or _pyb.get("swift"):
        _payoneer_banco_html = (
            '<div style="background:#1a1a1a;border:1px solid rgba(255,163,26,0.35);border-radius:10px;padding:14px;margin-bottom:14px;">'
            '<div style="font-size:.72rem;font-weight:800;color:#ffa31a;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">'
            '<i class="fa-solid fa-building-columns"></i> Opción 2 · Transferencia bancaria en USD (desde cualquier banco)</div>'
            + _pyb_row("Beneficiario", _pyb.get("beneficiario"), mono=False, copiable=True)
            + _pyb_row("Banco", _pyb.get("banco"), mono=False)
            + _pyb_row("Dirección del banco", _pyb.get("direccion"), mono=False)
            + _pyb_row("Número de cuenta", _pyb.get("cuenta"), copiable=True)
            + _pyb_row("Tipo de cuenta", _pyb.get("tipo_cuenta"), mono=False)
            + _pyb_row("ABA / Routing number", _pyb.get("aba"), copiable=True)
            + _pyb_row("Código SWIFT / BIC", _pyb.get("swift"), copiable=True)
            + '</div>'
        )
    else:
        _payoneer_banco_html = ""

    # ── Bloques de conversión añadidos (lenguaje neutro) ───────────────────
    # CSS extra para calculadora de pérdida, tabla comparativa y FAQ.
    _extra_css = (
        ".cp-box{background:linear-gradient(135deg,rgba(59,130,246,0.08),rgba(99,102,241,0.05));border:1px solid rgba(59,130,246,0.2);border-radius:16px;padding:26px 22px;margin:28px 0;}"
        ".cp-box h3{font-size:1.25rem;font-weight:900;margin-bottom:6px;}"
        ".cp-box .cp-intro{font-size:.9rem;color:#a1a1aa;margin-bottom:20px;}"
        ".cp-field{margin-bottom:14px;}"
        ".cp-field label{display:block;font-size:.8rem;font-weight:600;color:#cbd5e1;margin-bottom:6px;}"
        ".cp-field input{width:100%;padding:12px 14px;background:#0a0a0a;border:1px solid rgba(255,255,255,0.12);border-radius:8px;color:#fff;font-size:1rem;font-family:inherit;}"
        ".cp-field input:focus{outline:none;border-color:#3b82f6;}"
        ".cp-calc-btn{width:100%;padding:14px;margin-top:6px;background:#3b82f6;color:#fff;border:none;border-radius:8px;font-weight:800;font-size:.95rem;cursor:pointer;transition:background .2s;}"
        ".cp-calc-btn:hover{background:#2563eb;}"
        ".cp-result{display:none;margin-top:20px;text-align:center;padding:22px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);border-radius:12px;}"
        ".cp-result .cp-lead{font-size:.78rem;color:#fca5a5;text-transform:uppercase;letter-spacing:1px;font-weight:700;}"
        ".cp-result .cp-monto{font-size:2.3rem;font-weight:900;color:#f87171;margin:8px 0;}"
        ".cp-result .cp-foot{font-size:.82rem;color:#a1a1aa;}"
        ".tabla-comp{width:100%;border-collapse:separate;border-spacing:0;margin-top:18px;font-size:.86rem;border-radius:14px;overflow:hidden;border:1px solid rgba(255,255,255,0.07);}"
        ".tabla-comp th,.tabla-comp td{padding:13px 16px;text-align:left;border-bottom:1px solid rgba(255,255,255,0.06);}"
        ".tabla-comp tbody tr:last-child td{border-bottom:none;}"
        ".tabla-comp thead th{font-size:.68rem;text-transform:uppercase;letter-spacing:1px;font-weight:800;padding:14px 16px;}"
        ".tabla-comp th.col-feat{color:#71717a;background:#0a0a0a;}"
        ".tabla-comp td.col-feat{color:#cbd5e1;background:#0a0a0a;font-size:.83rem;}"
        ".tabla-comp th.col-avanza{color:#fff;background:rgba(59,130,246,0.22);border-left:2px solid rgba(59,130,246,0.5);border-right:2px solid rgba(59,130,246,0.5);border-top:2px solid rgba(59,130,246,0.5);}"
        ".tabla-comp td.col-avanza{color:#e2e8f0;font-weight:700;background:rgba(59,130,246,0.07);border-left:2px solid rgba(59,130,246,0.25);border-right:2px solid rgba(59,130,246,0.25);}"
        ".tabla-comp tbody tr:last-child td.col-avanza{border-bottom:2px solid rgba(59,130,246,0.5);}"
        ".tabla-comp th.col-trad{color:#52525b;background:#0a0a0a;}"
        ".tabla-comp td.col-trad{color:#52525b;background:#050505;}"
        ".tabla-comp tbody tr:hover td{filter:brightness(1.12);}"
        ".tc-check{color:#4ade80;margin-right:6px;font-weight:900;}"
        ".tc-cross{color:#ef4444;margin-right:6px;font-weight:900;}"
        ".tc-avanza-header{display:flex;flex-direction:column;align-items:flex-start;gap:4px;}"
        ".tc-avanza-badge{display:inline-block;background:#3b82f6;color:#fff;font-size:.58rem;font-weight:900;letter-spacing:1px;text-transform:uppercase;padding:2px 8px;border-radius:20px;}"
        ".faq-item{border:1px solid rgba(255,255,255,0.08);border-radius:10px;margin-bottom:10px;background:#0a0a0a;overflow:hidden;}"
        ".faq-item summary{padding:16px 18px;font-weight:700;font-size:.9rem;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:10px;}"
        ".faq-item summary::-webkit-details-marker{display:none;}"
        ".faq-item summary::after{content:'+';color:#3b82f6;font-weight:900;font-size:1.2rem;flex-shrink:0;}"
        ".faq-item[open] summary::after{content:'\\2212';}"
        ".faq-item .faq-body{padding:0 18px 16px;font-size:.86rem;color:#a1a1aa;line-height:1.6;}"
    )

    # Calculadora de pérdida interactiva (misma lógica que el home: semanas=50).
    _calc_html = """
  <div class="cp-box">
    <span class="section-label">🧮 Calculadora gratuita</span>
    <h3>Calcula la pérdida real de tu empresa por procesos de venta manuales.</h3>
    <p class="cp-intro">Te entregamos plan de recuperación y proyección a 3 años.</p>
    <div class="cp-field">
      <label>Cantidad de vendedores</label>
      <input type="number" id="cp-vendedores" min="0" placeholder="Ej: 3">
    </div>
    <div class="cp-field">
      <label>Horas por semana perdidas en tareas manuales (buscar precios, armar PDFs)</label>
      <input type="number" id="cp-horas" min="0" placeholder="Ej: 6">
    </div>
    <div class="cp-field">
      <label>Costo promedio por hora del vendedor (USD)</label>
      <input type="number" id="cp-costo" min="0" placeholder="Ej: 8">
    </div>
    <button class="cp-calc-btn" onclick="calcularPerdida()">Calcular impacto anual &rarr;</button>
    <div class="cp-result" id="cp-resultado">
      <div class="cp-lead">Tu empresa pierde cada año</div>
      <div class="cp-monto" id="cp-monto">USD 0</div>
      <div class="cp-foot">Solo en tareas administrativas manuales. Un sistema digital recupera buena parte de ese tiempo.</div>
    </div>
  </div>
  <script>
  function calcularPerdida(){
    var v = parseFloat(document.getElementById('cp-vendedores').value) || 0;
    var h = parseFloat(document.getElementById('cp-horas').value) || 0;
    var c = parseFloat(document.getElementById('cp-costo').value) || 0;
    var anual = v * h * c * 50;
    var fmt = new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', minimumFractionDigits:0});
    document.getElementById('cp-monto').innerText = fmt.format(anual);
    document.getElementById('cp-resultado').style.display = 'block';
  }
  </script>
"""

    # Tabla comparativa (Avanza vs agencia tradicional).
    _tabla_html = """
  <hr class="divider">
  <section class="section">
    <div class="section-label">Por qué elegir este sistema</div>
    <h2>Avanza vs. una agencia web tradicional</h2>
    <div style="overflow-x:auto;margin-top:18px;">
    <table class="tabla-comp">
      <thead>
        <tr>
          <th class="col-feat">Característica</th>
          <th class="col-avanza">
            <div class="tc-avanza-header">
              <span class="tc-avanza-badge">✦ Recomendado</span>
              Avanza Digital
            </div>
          </th>
          <th class="col-trad">Agencia tradicional</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="col-feat">Objetivo principal</td>
          <td class="col-avanza"><span class="tc-check">✓</span>Generación de clientes</td>
          <td class="col-trad"><span class="tc-cross">✗</span>Diseño estético</td>
        </tr>
        <tr>
          <td class="col-feat">Propiedad del código</td>
          <td class="col-avanza"><span class="tc-check">✓</span>100% tuyo (pago único)</td>
          <td class="col-trad"><span class="tc-cross">✗</span>Licencia de alquiler</td>
        </tr>
        <tr>
          <td class="col-feat">Integración WhatsApp y CRM</td>
          <td class="col-avanza"><span class="tc-check">✓</span>Conexión nativa</td>
          <td class="col-trad"><span class="tc-cross">✗</span>Plugin básico</td>
        </tr>
        <tr>
          <td class="col-feat">Tiempo de implementación</td>
          <td class="col-avanza"><span class="tc-check">✓</span>7 a 30 días</td>
          <td class="col-trad"><span class="tc-cross">✗</span>2 a 3 meses</td>
        </tr>
        <tr>
          <td class="col-feat">Cotizaciones</td>
          <td class="col-avanza"><span class="tc-check">✓</span>Automáticas</td>
          <td class="col-trad"><span class="tc-cross">✗</span>Manuales</td>
        </tr>
      </tbody>
    </table>
    </div>
  </section>
"""

    # Preguntas frecuentes (acordeón sin JS, lenguaje neutro).
    _faq_html = """
  <hr class="divider">
  <section class="section">
    <div class="section-label">Preguntas frecuentes</div>
    <h2>Lo que más nos consultan</h2>
    <details class="faq-item"><summary>¿El sistema es mío o lo alquilo?</summary><div class="faq-body">Es 100% tuyo. Trabajamos con modelo de pago único: una vez implementado, el código y la plataforma te pertenecen, a diferencia de las agencias que cobran un alquiler mensual para que sigas usando tu propia web.</div></details>
    <details class="faq-item"><summary>¿Cuánto tarda la implementación?</summary><div class="faq-body">Depende del plan. El Plan Base puede estar funcionando en 7 días, y los planes más completos entre 20 y 30 días. Te damos una fecha concreta antes de empezar.</div></details>
    <details class="faq-item"><summary>¿Necesito conocimientos técnicos?</summary><div class="faq-body">No. Nos encargamos de todo el proceso técnico y te entregamos un panel simple para que veas tus consultas y clientes. Además, incluye 3 meses de soporte.</div></details>
    <details class="faq-item"><summary>¿Hay costos mensuales obligatorios?</summary><div class="faq-body">No son obligatorios. El sistema es de pago único. Si lo deseas, puedes sumar un plan de mantenimiento mensual (hosting, dominio, seguridad y mejoras continuas), pero es totalmente opcional y sin permanencia.</div></details>
    <details class="faq-item"><summary>¿Sirve para mi rubro?</summary><div class="faq-body">Trabajamos con PYMEs industriales y de servicios B2B: metalúrgica, agro, logística, construcción, servicios técnicos y más. Si tienes dudas, escríbeme y lo vemos juntos.</div></details>
  </section>
"""

    # ── CSS de la grilla de planes (4 en línea) + selector Suscripción/Pago Único ──
    # String normal (no f-string): NO requiere doblar llaves.
    _planes_css = """
.pv-toggle{display:flex;align-items:center;justify-content:center;gap:14px;margin:6px auto 8px;flex-wrap:wrap;}
.pv-toggle .lbl{font-size:.82rem;font-weight:700;color:#71717a;transition:color .2s;}
.pv-toggle .lbl.active{color:#fff;}
.pv-switch{position:relative;display:inline-block;width:52px;height:28px;flex-shrink:0;}
.pv-switch input{opacity:0;width:0;height:0;}
.pv-slider{position:absolute;cursor:pointer;inset:0;background:#3b82f6;border-radius:28px;transition:.3s;}
.pv-slider:before{content:"";position:absolute;height:22px;width:22px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.3s;}
.pv-switch input:checked + .pv-slider:before{transform:translateX(24px);}
.pv-switch input:not(:checked) + .pv-slider{background:#3f3f46;}
.pv-hint{text-align:center;font-size:.82rem;color:#a1a1aa;max-width:560px;margin:0 auto 6px;line-height:1.5;}
.pv-hint b{color:#93c5fd;cursor:pointer;}
.pv-hint b.static{color:#cbd5e1;cursor:default;}

.planes-grid-4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-items:stretch;width:100vw;max-width:1240px;position:relative;left:50%;transform:translateX(-50%);padding:0 20px;margin:16px 0 10px;}
@media(max-width:1080px){.planes-grid-4{grid-template-columns:repeat(2,1fr);max-width:700px;}}
@media(max-width:560px){.planes-grid-4{grid-template-columns:1fr;}}

.pv-card{background:#0f0f0f;border:1px solid rgba(255,255,255,0.08);border-radius:14px;padding:20px 18px;display:flex;flex-direction:column;transition:border-color .2s,transform .2s;}
.pv-card:hover{border-color:rgba(59,130,246,0.45);transform:translateY(-3px);}
.pv-card.pop{border-color:rgba(59,130,246,0.55);box-shadow:0 0 24px rgba(59,130,246,0.12);}
.pv-card.estr{border-color:#a855f7;box-shadow:0 0 24px rgba(168,85,247,0.15);}
.pv-card > div{display:flex;flex-direction:column;flex:1;}
.pv-badge{align-self:flex-start;font-size:.62rem;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:4px 10px;border-radius:20px;margin-bottom:12px;}
.pv-name{font-size:1rem;font-weight:900;margin-bottom:10px;line-height:1.2;}
.pv-price{font-size:1.7rem;font-weight:900;color:#fff;margin-bottom:2px;line-height:1.1;}
.pv-price span{font-size:.72rem;font-weight:600;color:#71717a;}
.pv-desc{font-size:.78rem;color:#71717a;margin-bottom:14px;min-height:34px;}
.pv-list{list-style:none;padding:0;margin:0 0 16px;flex:1;}
.pv-list li{display:flex;gap:7px;align-items:flex-start;font-size:.78rem;color:#a1a1aa;margin-bottom:7px;line-height:1.4;}
.pv-list li:before{content:"✓";color:#4ade80;font-weight:900;flex-shrink:0;}
.pv-list li.pv-result{color:#e2e8f0;font-weight:600;margin-top:2px;}
.pv-list li.pv-result:before{content:"★";color:#facc15;}
.pv-actions{display:flex;flex-direction:column;gap:8px;margin-top:auto;}
.pv-btn-main{padding:11px;background:#3b82f6;color:#fff;border:none;border-radius:8px;cursor:pointer;font-weight:800;font-size:.84rem;font-family:Inter,sans-serif;text-decoration:none;text-align:center;transition:background .2s;}
.pv-btn-main:hover{background:#2563eb;}
.pv-btn-main.estr{background:#a855f7;}
.pv-btn-main.estr:hover{background:#9333ea;}
.pv-btn-wa{padding:11px;background:rgba(37,211,102,0.1);color:#25d366;border:1px solid rgba(37,211,102,0.3);border-radius:8px;cursor:pointer;font-weight:800;font-size:.84rem;text-decoration:none;text-align:center;transition:background .2s;}
.pv-btn-wa:hover{background:rgba(37,211,102,0.18);}
.legal-small-print-v2{text-align:center;font-size:.72rem;color:#52525b;max-width:620px;margin:14px auto 0;line-height:1.5;}

/* ── Sección Google Maps ── */
.maps-box{background:#0f0f0f;border:1px solid rgba(255,255,255,0.08);border-radius:16px;padding:28px 22px;display:flex;flex-wrap:wrap;gap:28px;align-items:center;}
.maps-col{flex:1 1 300px;min-width:0;}
.maps-pill{display:inline-flex;align-items:center;gap:8px;background:rgba(74,222,128,0.08);border:1px solid rgba(74,222,128,0.18);border-radius:50px;padding:6px 14px;font-size:.76rem;color:#4ade80;font-weight:700;margin-bottom:16px;}
.maps-col h3{font-size:1.25rem;font-weight:900;color:#fff;margin-bottom:10px;}
.maps-col p.lead{color:#a1a1aa;font-size:.9rem;line-height:1.6;margin-bottom:16px;}
.maps-list{list-style:none;padding:0;margin:0;}
.maps-list li{display:flex;gap:9px;align-items:flex-start;font-size:.85rem;color:#a1a1aa;margin-bottom:9px;line-height:1.45;}
.maps-list li:before{content:"\\f3c5";font-family:"Font Awesome 6 Free";font-weight:900;color:#4ade80;flex-shrink:0;font-size:.78rem;margin-top:2px;}
.maps-card{border-radius:12px;padding:16px 18px;}
.maps-card h4{display:flex;align-items:center;gap:10px;font-size:.92rem;color:#fff;font-weight:800;margin-bottom:6px;}
.maps-card p{color:#a1a1aa;font-size:.83rem;line-height:1.5;margin:0;}
.maps-cta{display:inline-flex;align-items:center;gap:8px;margin-top:6px;padding:13px 24px;background:#25d366;color:#04210f;border-radius:10px;font-weight:800;font-size:.9rem;text-decoration:none;transition:opacity .2s;}
.maps-cta:hover{opacity:.9;}
"""

    # Selector Suscripción / Pago Único (encabezado de la grilla).
    _toggle_html = """
  <div class="pv-toggle">
    <span class="lbl" id="pv-lbl-sub">Suscripción (Mantenimiento)</span>
    <label class="pv-switch"><input type="checkbox" id="pv-pricing-toggle" checked><span class="pv-slider"></span></label>
    <span class="lbl active" id="pv-lbl-unique">Pago Único (Sistema)</span>
  </div>
  <p class="pv-hint">¿Buscas <b class="static">hosting, soporte y mejoras continuas</b>? Cambia el selector a <b onclick="pvVerMensual()">«Suscripción (Mantenimiento)»</b> para ver los planes mensuales. <span style="display:block;margin-top:10px;color:#cbd5e1;">Los planes de <b>Sistemas</b> son <b>pago único</b> y el código queda <b>100% tuyo</b>. El <b>hosting, dominio y SSL</b> se gestionan desde el <b>Plan Cuidado</b> (USD 80/mes) — o lo alojás en tu propio servidor. No te atás a nosotros.</span></p>
"""

    # Sección de presencia en Google Maps (f-string: doblar llaves si hubiera, aquí no hay).
    _maps_html = f"""
  <hr class="divider">
  <section class="section">
    <div class="section-label">Posicionamiento local</div>
    <h2>Tu empresa, en el mapa de Google</h2>
    <p style="color:#a1a1aa;font-size:.92rem;margin-bottom:20px;">Cuando alguien busca tu rubro o "proveedor industrial" en tu zona, el mapa de Google aparece <strong style="color:#e2e8f0;">antes</strong> que cualquier web. Te ponemos ahí — y bien.</p>
    <div class="maps-box">
      <div class="maps-col">
        <span class="maps-pill"><i class="fa-brands fa-google"></i> Perfil de Empresa en Google</span>
        <h3>Qué hacemos por tu negocio</h3>
        <p class="lead">Creamos y optimizamos tu Perfil de Empresa en Google para que aparezcas en el mapa y en las búsquedas locales — el canal de captación más subestimado del B2B industrial.</p>
        <ul class="maps-list">
          <li>Alta y verificación del <strong style="color:#cbd5e1;">Perfil de Empresa en Google</strong></li>
          <li>Categorías, servicios y zona de cobertura optimizadas</li>
          <li>Fotos, horarios y ficha completa que genera confianza</li>
          <li>Sistema para pedir y responder <strong style="color:#cbd5e1;">reseñas</strong> (el factor #1 del ranking local)</li>
          <li>Conexión con tu CRM: cada consulta del mapa entra como lead</li>
        </ul>
      </div>
      <div class="maps-col" style="display:flex;flex-direction:column;gap:12px;">
        <div class="maps-card" style="background:rgba(59,130,246,0.05);border:1px solid rgba(59,130,246,0.15);">
          <h4><i class="fa-solid fa-location-dot" style="color:#60a5fa;"></i> Te encuentran cuando te buscan</h4>
          <p>Apareces en las búsquedas de tu ciudad y región, justo cuando hay intención de compra.</p>
        </div>
        <div class="maps-card" style="background:rgba(74,222,128,0.05);border:1px solid rgba(74,222,128,0.15);">
          <h4><i class="fa-solid fa-arrow-trend-up" style="color:#4ade80;"></i> Más llamadas y mensajes directos</h4>
          <p>El cliente llama, pide cómo llegar o escribe por WhatsApp desde el mismo mapa.</p>
        </div>
        <div class="maps-card" style="background:rgba(251,146,60,0.05);border:1px solid rgba(251,146,60,0.15);">
          <h4><i class="fa-solid fa-star" style="color:#fb923c;"></i> Reputación que vende</h4>
          <p>Reseñas reales que construyen confianza antes del primer contacto.</p>
        </div>
        <a class="maps-cta" href="https://wa.me/{wa_contacto}?text=Hola%20{wa_titular_encoded}%2C%20quiero%20poner%20mi%20empresa%20en%20Google%20Maps." target="_blank" rel="noopener"><i class="fa-brands fa-whatsapp"></i> Quiero estar en Google Maps</a>
      </div>
    </div>
  </section>
"""

    # JS del selector (string normal: no doblar llaves).
    _planes_js = """
<script>
(function(){
  var t = document.getElementById('pv-pricing-toggle');
  if(!t) return;
  function apply(){
    var unique = t.checked;
    document.querySelectorAll('.pv-unique').forEach(function(e){ e.style.display = unique ? 'flex' : 'none'; });
    document.querySelectorAll('.pv-monthly').forEach(function(e){ e.style.display = unique ? 'none' : 'flex'; });
    var lu = document.getElementById('pv-lbl-unique'), ls = document.getElementById('pv-lbl-sub');
    if(lu) lu.classList.toggle('active', unique);
    if(ls) ls.classList.toggle('active', !unique);
  }
  t.addEventListener('change', apply);
  apply();
})();
function pvVerMensual(){ var t=document.getElementById('pv-pricing-toggle'); if(t){ t.checked=false; t.dispatchEvent(new Event('change')); } }
</script>
"""

    html = f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><path d=%22M10 20 L40 50 L10 80 L30 80 L60 50 L30 20 Z%22 fill=%22%230044cc%22/><path d=%22M45 20 L75 50 L45 80 L65 80 L95 50 L65 20 Z%22 fill=%22%2300cccc%22/></svg>">
<title>{_seo_title}</title>
<meta name="description" content="{_seo_desc}">
<link rel="canonical" href="{_canonical_url}">
<meta property="og:type" content="website">
<meta property="og:url" content="{_canonical_url}">
<meta property="og:title" content="{_seo_title}">
<meta property="og:description" content="{_seo_desc}">
<meta property="og:image" content="{_og_image}">
<meta property="og:locale" content="es_AR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_seo_title}">
<meta name="twitter:description" content="{_seo_desc}">
<meta name="twitter:image" content="{_og_image}">
<meta name="robots" content="index, follow">
<script type="application/ld+json">{_ld_json}</script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;line-height:1.6;}}
.wrap{{max-width:680px;margin:0 auto;padding:0 20px 60px;}}
.nav{{display:flex;align-items:center;justify-content:space-between;padding:18px 20px;border-bottom:1px solid rgba(255,255,255,0.06);max-width:680px;margin:0 auto;}}
.nav-logo{{font-size:.85rem;font-weight:700;color:#a1a1aa;text-decoration:none;}}
.nav-logo span{{color:#3b82f6;}}
.asesor-bar{{background:rgba(59,130,246,0.08);border-bottom:1px solid rgba(59,130,246,0.15);padding:10px 20px;text-align:center;font-size:.78rem;color:#93c5fd;font-weight:600;}}
.hero{{padding:52px 0 40px;text-align:center;}}
.hero-badge{{display:inline-block;background:rgba(74,222,128,0.12);color:#4ade80;padding:5px 14px;border-radius:20px;font-size:.72rem;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:20px;border:1px solid rgba(74,222,128,0.2);}}
.hero h1{{font-size:clamp(1.8rem,5vw,2.6rem);font-weight:900;line-height:1.15;margin-bottom:16px;}}
.hero h1 span{{color:#3b82f6;}}
.hero-sub{{color:#a1a1aa;font-size:1.05rem;max-width:520px;margin:0 auto 32px;}}
.hero-cta{{display:inline-block;padding:16px 32px;background:#3b82f6;color:#fff;border-radius:10px;font-weight:800;font-size:1rem;text-decoration:none;transition:background .2s;border:none;cursor:pointer;}}
.hero-cta:hover{{background:#2563eb;}}
.nav-partner-pill{{font-size:.7rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#93c5fd;background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.25);padding:5px 12px;border-radius:50px;}}
.hero-aliado{{position:relative;padding:50px 0 34px;text-align:center;}}
.hero-aliado::before{{content:'';position:absolute;inset:0;background:radial-gradient(circle at 50% 0%,rgba(59,130,246,0.10),transparent 60%);pointer-events:none;}}
.hero-avatar{{position:relative;width:104px;height:104px;border-radius:50%;margin:0 auto 18px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#3b82f6,#1d4ed8);border:3px solid rgba(255,255,255,0.1);box-shadow:0 8px 40px rgba(59,130,246,0.25);overflow:visible;}}
.hero-verified{{position:absolute;bottom:2px;right:2px;width:30px;height:30px;border-radius:50%;background:#4ade80;color:#04210f;display:flex;align-items:center;justify-content:center;font-size:.85rem;font-weight:900;border:3px solid #050505;}}
.hero-aliado-name{{font-size:clamp(2rem,6vw,2.7rem);font-weight:900;letter-spacing:-1px;line-height:1.1;margin-bottom:8px;}}
.hero-aliado-role{{color:#a1a1aa;font-size:1rem;font-weight:500;margin-bottom:16px;}}
.hero-aliado-role b{{color:#93c5fd;font-weight:700;}}
.hero-rubros{{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:20px;}}
.hero-aliado-bio{{color:#a1a1aa;font-size:1.05rem;font-weight:300;max-width:540px;margin:0 auto 28px;line-height:1.6;}}
.hero-cta-row{{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin-bottom:18px;}}
.hero-cta-wa{{display:inline-flex;align-items:center;gap:8px;padding:16px 28px;background:rgba(37,211,102,0.12);color:#25d366;border:1px solid rgba(37,211,102,0.3);border-radius:10px;font-weight:800;font-size:1rem;text-decoration:none;transition:background .2s;}}
.hero-cta-wa:hover{{background:rgba(37,211,102,0.2);}}
.hero-social{{margin-top:20px;font-size:.8rem;color:#71717a;}}
.hero-social span{{color:#4ade80;font-weight:700;}}
.section{{padding:40px 0;}}
.section-label{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:#71717a;margin-bottom:12px;}}
.section h2{{font-size:1.5rem;font-weight:900;margin-bottom:16px;}}
.problem-list{{list-style:none;padding:0;}}
.problem-list li{{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:.92rem;color:#a1a1aa;}}
.problem-list li:last-child{{border-bottom:none;}}
.problem-list li::before{{content:"\u2717";color:#ef4444;font-weight:900;flex-shrink:0;margin-top:2px;}}
.benefit-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:20px;}}
@media(max-width:480px){{.benefit-grid{{grid-template-columns:1fr;}}}}
.benefit-card{{background:#0f0f0f;border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:18px 16px;}}
.benefit-icon{{font-size:1.4rem;margin-bottom:8px;}}
.benefit-title{{font-size:.88rem;font-weight:800;margin-bottom:4px;}}
.benefit-desc{{font-size:.78rem;color:#71717a;line-height:1.5;}}
.caso-card{{background:#0a0a0a;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin-bottom:12px;}}
.caso-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;flex-wrap:wrap;gap:8px;}}
.caso-empresa{{font-size:.88rem;font-weight:800;}}
.caso-rubro{{font-size:.7rem;color:#71717a;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-top:2px;}}
.caso-badge{{background:rgba(74,222,128,0.1);color:#4ade80;padding:4px 10px;border-radius:20px;font-size:.72rem;font-weight:700;white-space:nowrap;}}
.caso-resultado{{font-size:.85rem;color:#a1a1aa;line-height:1.5;}}
.caso-resultado strong{{color:#e2e8f0;}}
.divider{{border:none;border-top:1px solid rgba(255,255,255,0.06);margin:8px 0;}}
.planes-title{{font-size:1.4rem;font-weight:900;margin-bottom:6px;}}
.planes-sub{{color:#a1a1aa;font-size:.88rem;margin-bottom:24px;}}
.plan-card{{background:#0f0f0f;border:1px solid rgba(255,255,255,0.08);border-radius:14px;padding:22px 20px;margin-bottom:12px;transition:border-color .2s;}}
.plan-card:hover{{border-color:rgba(59,130,246,0.4);}}
.garantia-box{{background:rgba(74,222,128,0.05);border:1px solid rgba(74,222,128,0.15);border-radius:12px;padding:20px;text-align:center;margin-top:20px;}}
.garantia-box h3{{font-size:1rem;font-weight:800;color:#4ade80;margin-bottom:6px;}}
.garantia-box p{{font-size:.83rem;color:#a1a1aa;}}
.resultados-reales-box{{margin-top:32px;padding-top:28px;border-top:1px solid rgba(255,255,255,0.06);}}
.footer{{margin-top:48px;text-align:center;color:#3f3f46;font-size:.75rem;}}
.footer a{{color:#52525b;}}
.audit-cta-box{{background:rgba(59,130,246,0.05);border:1px solid rgba(59,130,246,0.18);border-radius:14px;padding:28px 22px;margin:8px 0 28px;text-align:center;}}
.audit-cta-box .audit-label{{font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#93c5fd;margin-bottom:10px;display:block;}}
.audit-cta-box h3{{font-size:1.15rem;font-weight:900;color:#e2e8f0;margin:0 0 8px;}}
.audit-cta-box p{{font-size:.85rem;color:#a1a1aa;max-width:460px;margin:0 auto 18px;line-height:1.55;}}
.audit-btn{{display:inline-flex;align-items:center;gap:8px;padding:13px 26px;background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff;border-radius:10px;font-weight:800;font-size:.9rem;text-decoration:none;transition:opacity .2s;}}
.audit-btn:hover{{opacity:.88;}}
.audit-free-tag{{display:block;font-size:.72rem;color:#71717a;margin-top:10px;}}
.asesor-intro{{display:flex;gap:16px;align-items:flex-start;background:rgba(59,130,246,0.06);border:1px solid rgba(59,130,246,0.15);border-radius:14px;padding:22px 20px;margin-bottom:36px;}}
.asesor-avatar{{width:54px;height:54px;border-radius:50%;background:linear-gradient(135deg,#3b82f6,#1d4ed8);display:flex;align-items:center;justify-content:center;font-size:1.4rem;flex-shrink:0;}}
.asesor-intro-name{{font-size:1rem;font-weight:900;margin-bottom:4px;}}
.asesor-intro-badge{{font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#93c5fd;margin-bottom:6px;}}
.asesor-intro-bio{{font-size:.82rem;color:#a1a1aa;line-height:1.55;}}
#modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:100;align-items:center;justify-content:center;padding:16px;}}
.modal-box{{background:#111;border:1px solid #2a2a2a;border-radius:16px;padding:28px;width:100%;max-width:420px;max-height:88vh;overflow-y:auto;-webkit-overflow-scrolling:touch;}}
.step{{display:none;}}
.step.active{{display:block;}}
.moneda-options{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin:16px 0;}}
@media(max-width:480px){{.moneda-options{{grid-template-columns:1fr;}}}}
.moneda-btn{{padding:16px 12px;border-radius:10px;border:2px solid #2a2a2a;background:#1a1a1a;color:#e2e8f0;cursor:pointer;text-align:center;transition:all .2s;font-family:Inter,sans-serif;}}
.moneda-btn:hover{{border-color:#3b82f6;background:rgba(59,130,246,0.08);}}
.moneda-btn.selected{{border-color:#3b82f6;background:rgba(59,130,246,0.12);}}
.moneda-btn .icon{{font-size:1.6rem;margin-bottom:6px;}}
.moneda-btn .label{{font-weight:800;font-size:.9rem;}}
.moneda-btn .sublabel{{font-size:.72rem;color:#a1a1aa;margin-top:2px;}}
.moneda-btn.usdt .icon{{color:#26a17b;}}
.moneda-btn.usdt.selected{{border-color:#26a17b;background:rgba(38,161,123,0.12);}}
.moneda-btn.payoneer .icon{{color:#ffa31a;}}
.moneda-btn.payoneer:hover{{border-color:#ffa31a;background:rgba(255,163,26,0.08);}}
.moneda-btn.payoneer.selected{{border-color:#ffa31a;background:rgba(255,163,26,0.12);}}
.btn-cancel{{flex:1;padding:12px;border-radius:8px;border:1px solid #444;background:transparent;color:#aaa;cursor:pointer;font-size:.95rem;font-family:Inter,sans-serif;}}
.btn-primary{{flex:1;padding:12px;border-radius:8px;border:none;background:#3b82f6;color:#fff;cursor:pointer;font-weight:700;font-size:.95rem;font-family:Inter,sans-serif;}}
.btn-primary:disabled{{opacity:.6;cursor:not-allowed;}}
input[type=text]{{width:100%;padding:12px;border-radius:8px;border:1px solid #444;background:#1a1a1a;color:#fff;font-size:1rem;font-family:Inter,sans-serif;}}
input[type=text]:focus{{outline:none;border-color:#3b82f6;}}
</style><style>{_extra_css}</style><style>{_planes_css}</style></head><body>
<nav class="nav">
  <a class="nav-logo" href="https://avanzadigital.digital">Avanza<span>Digital</span></a>
  <span class="nav-partner-pill">Partner Oficial</span>
</nav>
<div class="wrap">
  <section class="hero hero-aliado">
    <div class="hero-avatar">{avatar_hero_html}<span class="hero-verified" title="Partner verificado de Avanza Digital">✓</span></div>
    <h1 class="hero-aliado-name">{titular}</h1>
    <p class="hero-aliado-role">Partner Oficial de <b>Avanza Digital</b>{f' · {_ciudad}' if _ciudad else ''}</p>
    {f'<div class="hero-rubros">{_rubros_badges_html}</div>' if _rubros_badges_html else ''}
    <p class="hero-aliado-bio">{bio}</p>
    <div class="hero-cta-row">
      <a href="#planes" class="hero-cta">Ver planes y precios →</a>
      <a href="https://wa.me/{wa_contacto}?text=Hola%20{wa_titular_encoded}%2C%20quiero%20coordinar%20un%20diagn%C3%B3stico%20de%2015%20minutos%20para%20mi%20empresa." class="hero-cta-wa" target="_blank" rel="noopener">Hablemos por WhatsApp</a>
    </div>
    <p class="hero-social">Más de <span>40 PYMEs industriales</span> ya tienen su sistema funcionando</p>
  </section>

  <div class="audit-cta-box">
    <span class="audit-label">📊 Diagnóstico gratuito · Sin registro</span>
    <h3>¿Cómo está la presencia digital de tu empresa hoy?</h3>
    <p>Analizamos tu sitio web en segundos: velocidad, SEO, captación de leads y conversión. Resultado instantáneo con plan de acción personalizado.</p>
    <a href="https://avanzadigital.digital/auditoria-digital.html?ref={ref_code}" class="audit-btn" target="_blank">
      📋 Hacer diagnóstico gratis →
    </a>
    <span class="audit-free-tag">Gratis · Sin spam · Resultado en menos de 30 segundos</span>
  </div>

{_calc_html}

  <section class="section">
    <div class="section-label">El problema</div>
    <h2>¿Te suena alguna de estas situaciones?</h2>
    <ul class="problem-list">
      <li>Los presupuestos tardan días en salir y el cliente ya compró en otro lado</li>
      <li>Dependes del boca a boca y no tienes forma de conseguir clientes nuevos de manera sistemática</li>
      <li>Tu sitio web existe, pero no genera ninguna consulta real</li>
      <li>No sabes cuántos clientes potenciales pierdes por mes por responder tarde</li>
      <li>Cada vendedor usa su propio método y no hay proceso replicable</li>
    </ul>
  </section>

  <hr class="divider">

  <section class="section">
    <div class="section-label">La solución</div>
    <h2>Un sistema que trabaja aunque tú no estés</h2>
    <div class="benefit-grid">
      <div class="benefit-card">
        <div class="benefit-icon">⚡</div>
        <div class="benefit-title">Respuestas en minutos</div>
        <div class="benefit-desc">Cotizador automático y formularios inteligentes que califican y responden consultas sin intervención humana.</div>
      </div>
      <div class="benefit-card">
        <div class="benefit-icon">🎯</div>
        <div class="benefit-title">Leads que cierran</div>
        <div class="benefit-desc">Cada consulta llega con el contexto completo: rubro, producto, urgencia y contacto directo al responsable.</div>
      </div>
      <div class="benefit-card">
        <div class="benefit-icon">📊</div>
        <div class="benefit-title">Métricas en tiempo real</div>
        <div class="benefit-desc">CRM liviano integrado. Sabes exactamente de dónde vienen tus clientes y cuánto vale cada canal.</div>
      </div>
      <div class="benefit-card">
        <div class="benefit-icon">🔁</div>
        <div class="benefit-title">Sistema replicable</div>
        <div class="benefit-desc">Proceso documentado que cualquier vendedor puede seguir. Dejas de depender de una sola persona.</div>
      </div>
    </div>
  </section>

{_tabla_html}
  <hr class="divider">


{_maps_html}


  <section class="section planes-section" id="planes">
    <div class="section-label" style="text-align:center;">Planes</div>
    <p class="planes-title" style="text-align:center;">Elige tu nivel de crecimiento</p>
    <p class="planes-sub" style="text-align:center;max-width:560px;margin:0 auto 14px;">Desde <strong style="color:#cbd5e1;">estabilidad técnica</strong> hasta <strong style="color:#cbd5e1;">transformación digital completa</strong>. Implementación en 30 días, sin costos ocultos.</p>
    {_toggle_html}
    {planes_html}
    <div class="legal-small-print-v2">Cambios fuera del alcance especificado se cotizan por separado bajo demanda. No es un servicio de diseño ilimitado: es un sistema vivo con límites operativos para garantizar la calidad del soporte.</div>
    <div class="garantia-box">
      <h3>✓ Garantía de 3 meses incluida</h3>
      <p>Todos los planes incluyen soporte técnico prioritario los primeros 90 días. Si algo no funciona, lo resolvemos nosotros.</p>
    </div>

    <div class="resultados-reales-box">
      <div class="section-label" style="text-align:center;margin-bottom:14px;">Resultados reales</div>
      <p class="planes-title" style="text-align:center;font-size:1.15rem;margin-bottom:20px;">Lo que lograron empresas como la tuya</p>
      <div class="caso-card">
        <div class="caso-header">
          <div><div class="caso-empresa">Metalúrgica Balconi · Rafaela</div><div class="caso-rubro">Fabricación de estructuras</div></div>
          <div class="caso-badge">+47% conversión</div>
        </div>
        <p class="caso-resultado">Tenían el mismo problema de presupuestos que se perdían. En <strong>21 días</strong> implementaron el sistema. Primer trimestre: <strong>3 contratos nuevos</strong> desde canales digitales.</p>
      </div>
      <div class="caso-card">
        <div class="caso-header">
          <div><div class="caso-empresa">Transportes Oñate · Rosario</div><div class="caso-rubro">Logística y transporte</div></div>
          <div class="caso-badge">31hs → 4hs</div>
        </div>
        <p class="caso-resultado">Pasaron de tardar <strong>31 horas</strong> en responder cotizaciones a <strong>menos de 4 horas</strong>. Cerraron <strong>3 contratos nuevos</strong> el primer mes.</p>
      </div>
      <div class="caso-card">
        <div class="caso-header">
          <div><div class="caso-empresa">Soluciones Técnicas del Litoral · Paraná</div><div class="caso-rubro">Servicios técnicos industriales</div></div>
          <div class="caso-badge">USD 8.400 primer trimestre</div>
        </div>
        <p class="caso-resultado">En <strong>7 días</strong> activaron el Plan Base. En 20 días les entró la primera consulta digital. Primer trimestre: <strong>USD 8.400 en contratos nuevos</strong>.</p>
      </div>
    </div>
{_faq_html}
  </section>
{_planes_js}

  <div class="footer">
    <p>Atendido por <strong style="color:#52525b;">{titular}</strong> · Partner Oficial de Avanza Digital</p>
    <p style="margin-top:6px;">Tu pago queda atribuido automáticamente a tu asesor.<br>
    <a href="https://avanzadigital.digital" target="_blank">avanzadigital.digital</a> ·
    <a href="https://avanzadigital.digital/politica.html" target="_blank">Privacidad</a></p>
  </div>
</div>

<div id="modal-overlay" onclick="onOverlayClick(event)" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:100;align-items:center;justify-content:center;padding:16px;">
  <div class="modal-box" onclick="event.stopPropagation()">
    <div id="step-nombre" class="step active">
      <h3 style="margin:0 0 6px;font-size:1.1rem;font-weight:800;">Completa tus datos para continuar</h3>
      <p style="color:#a1a1aa;font-size:.85rem;margin:0 0 18px;">Te enviaremos el formulario de inicio del proyecto apenas se confirme el pago.</p>
      <label style="display:block;font-size:.75rem;color:#a1a1aa;margin-bottom:4px;font-weight:700;">Nombre completo *</label>
      <input id="modal-nombre" type="text" placeholder="Tu nombre completo" style="margin-bottom:12px;" onkeydown="if(event.key==='Enter') document.getElementById('modal-email').focus()">
      <label style="display:block;font-size:.75rem;color:#a1a1aa;margin-bottom:4px;font-weight:700;">Email *</label>
      <input id="modal-email" type="text" placeholder="tu@empresa.com" style="margin-bottom:12px;" onkeydown="if(event.key==='Enter') document.getElementById('modal-whatsapp').focus()">
      <label style="display:block;font-size:.75rem;color:#a1a1aa;margin-bottom:4px;font-weight:700;">WhatsApp (con código de país) *</label>
      <input id="modal-whatsapp" type="text" placeholder="+5491155556666" onkeydown="if(event.key==='Enter') irAPaso2()">
      <div style="display:flex;gap:10px;margin-top:18px;">
        <button class="btn-cancel" onclick="cerrarModal()">Cancelar</button>
        <button class="btn-primary" onclick="irAPaso2()">Siguiente →</button>
      </div>
    </div>
    <div id="step-moneda" class="step">
      <h3 style="margin:0 0 6px;font-size:1.1rem;font-weight:800;">¿Cómo quieres pagar?</h3>
      <p style="color:#a1a1aa;font-size:.85rem;margin:0 0 4px;">Elige tu moneda y método de pago.</p>
      <div class="moneda-options">
        <button class="moneda-btn ars selected" id="opt-ars" onclick="seleccionarMoneda('ars')">
          <div class="icon">🏦</div><div class="label">Pesos ARS</div><div class="sublabel">MercadoPago</div>
        </button>
        {_btn_usd}
        {_btn_usdt}
        {_btn_payoneer}
      </div>
      <div id="moneda-info" style="background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.2);border-radius:8px;padding:10px 14px;font-size:.82rem;color:#93c5fd;margin-bottom:14px;">
        🏦 Pagarás en <strong>pesos argentinos</strong> a través de <strong>MercadoPago</strong>.
      </div>
      <div style="display:flex;gap:10px;">
        <button class="btn-cancel" onclick="volverAPaso1()">← Volver</button>
        <button class="btn-primary" id="btn-pagar" onclick="confirmarContratacion()">Ir a pagar →</button>
      </div>
    </div>
    <div id="step-procesando" class="step" style="text-align:center;padding:12px 0;">
      <div style="font-size:2rem;margin-bottom:12px;">⏳</div>
      <p style="font-weight:700;font-size:1rem;margin:0 0 6px;">Generando tu link de pago…</p>
      <p style="color:#a1a1aa;font-size:.85rem;margin:0;">Serás redirigido en segundos.</p>
    </div>
    <div id="step-usdt" class="step" style="padding:4px 0;">
      <h3 style="margin:0 0 6px;font-size:1.05rem;font-weight:800;">🪙 Instrucciones de pago en USDT/USDC</h3>
      <p style="color:#a1a1aa;font-size:.82rem;margin:0 0 16px;">Realizá la transferencia y avisanos por WhatsApp para confirmar.</p>
      <div style="background:#1a1a1a;border:1px solid rgba(38,161,123,0.35);border-radius:10px;padding:14px;margin-bottom:14px;">
        <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap;">
          <span style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;min-width:56px;">Red</span>
          <span id="modal-usdt-red" style="color:#26a17b;font-weight:800;font-size:.9rem;background:rgba(38,161,123,0.1);border:1px solid rgba(38,161,123,0.3);padding:3px 10px;border-radius:20px;">{_usdt_red_js}</span>
        </div>
        <div style="margin-bottom:10px;">
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Monto exacto</div>
          <div id="modal-usdt-monto" style="font-size:1.4rem;font-weight:900;color:#e2e8f0;">USD —</div>
        </div>
        <div>
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Dirección de billetera</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <input type="text" id="modal-usdt-dir" value="{_usdt_dir_js}" readonly style="flex:1;min-width:140px;font-family:monospace;font-size:.75rem;background:#111;border:1px solid rgba(38,161,123,0.2);border-radius:6px;padding:9px;">
            <button onclick="copiarDirUSDT()" style="background:rgba(38,161,123,0.15);color:#26a17b;border:1px solid rgba(38,161,123,0.35);border-radius:6px;padding:0 12px;height:36px;font-weight:700;cursor:pointer;font-size:.8rem;white-space:nowrap;">
              <i class="fa-solid fa-copy"></i> Copiar
            </button>
          </div>
        </div>
      </div>
      <p style="font-size:.78rem;color:#71717a;margin:0 0 14px;line-height:1.5;padding:10px;background:rgba(0,0,0,0.4);border-radius:8px;border-left:2px solid rgba(38,161,123,0.5);">
        Enviá el monto exacto y avisale a <strong style="color:#e2e8f0;">{titular}</strong> por WhatsApp en cuanto realices la transferencia. Tu plan se activa en cuanto Avanza confirma el pago (hasta 24hs hábiles).
      </p>
      <div style="display:flex;gap:10px;">
        <button class="btn-cancel" onclick="volverAPaso1()">← Volver</button>
        <a href="https://wa.me/{wa_contacto}?text=Hola%2C+realic%C3%A9+la+transferencia+en+USDT+para+el+%7B%7Bplan%7D%7D" id="modal-usdt-wa-btn"
           target="_blank"
           style="flex:1;padding:12px;border-radius:8px;border:none;background:#25d366;color:#fff;cursor:pointer;font-weight:700;font-size:.95rem;text-decoration:none;text-align:center;display:flex;align-items:center;justify-content:center;gap:6px;">
          <i class="fa-brands fa-whatsapp"></i> Confirmar por WhatsApp
        </a>
      </div>
    </div>
    <div id="step-payoneer" class="step" style="padding:4px 0;">
      <h3 style="margin:0 0 6px;font-size:1.05rem;font-weight:800;">💳 Instrucciones de pago por Payoneer</h3>
      <p style="color:#a1a1aa;font-size:.82rem;margin:0 0 16px;">Realizá la transferencia y avisanos por WhatsApp para confirmar.</p>
      <div style="background:#1a1a1a;border:1px solid rgba(255,163,26,0.35);border-radius:10px;padding:14px;margin-bottom:14px;">
        <div style="margin-bottom:10px;">
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Monto exacto</div>
          <div id="modal-payoneer-monto" style="font-size:1.4rem;font-weight:900;color:#e2e8f0;">USD —</div>
        </div>
        <div>
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Opción 1 · Email de Payoneer</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <input type="text" id="modal-payoneer-email" value="{_py_email}" readonly
              style="flex:1;min-width:140px;font-family:monospace;font-size:.85rem;background:#111;border:1px solid rgba(255,163,26,0.2);border-radius:6px;padding:9px;color:#ffa31a;">
            <button onclick="copiarEmailPayoneer()" style="background:rgba(255,163,26,0.15);color:#ffa31a;border:1px solid rgba(255,163,26,0.35);border-radius:6px;padding:0 12px;height:36px;font-weight:700;cursor:pointer;font-size:.8rem;white-space:nowrap;">
              <i class="fa-solid fa-copy"></i> Copiar
            </button>
          </div>
        </div>
      </div>
      {_payoneer_banco_html}
      <p style="font-size:.78rem;color:#71717a;margin:0 0 14px;line-height:1.5;padding:10px;background:rgba(0,0,0,0.4);border-radius:8px;border-left:2px solid rgba(255,163,26,0.5);">
        Dos formas de pagar: enviá el monto exacto al <strong style="color:#e2e8f0;">email de Payoneer</strong> (si también usas Payoneer) o haz una <strong style="color:#e2e8f0;">transferencia bancaria en USD</strong> a los datos de arriba desde cualquier banco. Avisale a <strong style="color:#e2e8f0;">{titular}</strong> por WhatsApp en cuanto realices la transferencia. Tu plan se activa en cuanto Avanza confirma el pago (hasta 24hs hábiles).
      </p>
      <div style="display:flex;gap:10px;">
        <button class="btn-cancel" onclick="volverAPaso1()">← Volver</button>
        <a href="https://wa.me/{wa_contacto}" id="modal-payoneer-wa-btn"
           target="_blank"
           style="flex:1;padding:12px;border-radius:8px;border:none;background:#25d366;color:#fff;cursor:pointer;font-weight:700;font-size:.95rem;text-decoration:none;text-align:center;display:flex;align-items:center;justify-content:center;gap:6px;">
          <i class="fa-brands fa-whatsapp"></i> Confirmar por WhatsApp
        </a>
      </div>
    </div>
    <div id="step-mp-mensual" class="step" style="padding:4px 0;">
      <h3 style="margin:0 0 6px;font-size:1.05rem;font-weight:800;">🏦 Pago mensual por MercadoPago</h3>
      <p style="color:#a1a1aa;font-size:.82rem;margin:0 0 16px;">Transferí desde tu banco o billetera a los datos de abajo y avisanos por WhatsApp para activar tu plan.</p>
      <div style="background:#1a1a1a;border:1px solid rgba(59,130,246,0.35);border-radius:10px;padding:14px;margin-bottom:14px;">
        <div style="margin-bottom:12px;">
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Monto mensual</div>
          <div id="modal-mp-monto" style="font-size:1.4rem;font-weight:900;color:#e2e8f0;">$ —</div>
          <div id="modal-mp-monto-ref" style="font-size:.74rem;color:#71717a;margin-top:2px;">—</div>
        </div>
        <div style="margin-bottom:10px;">
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Titular de la cuenta</div>
          <div style="font-size:.92rem;color:#e2e8f0;font-weight:700;">{_mp_titular}</div>
        </div>
        <div style="margin-bottom:10px;">
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Alias</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <input type="text" id="modal-mp-alias" value="{_mp_alias}" readonly style="flex:1;min-width:140px;font-family:monospace;font-size:.85rem;background:#111;border:1px solid rgba(59,130,246,0.2);border-radius:6px;padding:9px;color:#93c5fd;">
            <button onclick="copiarTexto(this,'{_mp_alias}')" style="background:rgba(59,130,246,0.15);color:#93c5fd;border:1px solid rgba(59,130,246,0.35);border-radius:6px;padding:0 12px;height:36px;font-weight:700;cursor:pointer;font-size:.8rem;white-space:nowrap;"><i class="fa-solid fa-copy"></i> Copiar</button>
          </div>
        </div>
        <div>
          <div style="font-size:.72rem;font-weight:700;color:#a1a1aa;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">CVU</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <input type="text" id="modal-mp-cvu" value="{_mp_cvu}" readonly style="flex:1;min-width:140px;font-family:monospace;font-size:.8rem;background:#111;border:1px solid rgba(59,130,246,0.2);border-radius:6px;padding:9px;color:#93c5fd;">
            <button onclick="copiarTexto(this,'{_mp_cvu}')" style="background:rgba(59,130,246,0.15);color:#93c5fd;border:1px solid rgba(59,130,246,0.35);border-radius:6px;padding:0 12px;height:36px;font-weight:700;cursor:pointer;font-size:.8rem;white-space:nowrap;"><i class="fa-solid fa-copy"></i> Copiar</button>
          </div>
        </div>
      </div>
      <p style="font-size:.78rem;color:#71717a;margin:0 0 14px;line-height:1.5;padding:10px;background:rgba(0,0,0,0.4);border-radius:8px;border-left:2px solid rgba(59,130,246,0.5);">
        El monto en pesos es orientativo al dólar de hoy y puede variar al momento de transferir. Avisale a <strong style="color:#e2e8f0;">{titular}</strong> por WhatsApp cuando completes el pago. Tu plan se activa en cuanto Avanza confirma el pago (hasta 24hs hábiles).
      </p>
      <div style="display:flex;gap:10px;">
        <button class="btn-cancel" onclick="volverAPaso1()">← Volver</button>
        <a href="https://wa.me/{wa_contacto}" id="modal-mp-wa-btn"
           target="_blank"
           style="flex:1;padding:12px;border-radius:8px;border:none;background:#25d366;color:#fff;cursor:pointer;font-weight:700;font-size:.95rem;text-decoration:none;text-align:center;display:flex;align-items:center;justify-content:center;gap:6px;">
          <i class="fa-brands fa-whatsapp"></i> Confirmar por WhatsApp
        </a>
      </div>
    </div>
  </div>
</div>

<script>
  const _PLAN_PRECIOS = {{{_plan_precios_js}}};
  const _PLANES_MENSUALES = {{{_planes_mensuales_js}}};
  const _MP_DATOS = {{ titular: "{_mp_titular}", alias: "{_mp_alias}", cvu: "{_mp_cvu}" }};
  let _plan = \'\', _ref = \'\', _moneda = \'ars\';
  function abrirModal(plan, ref) {{
    _plan = plan; _ref = ref; _moneda = \'ars\';
    mostrarPaso(\'step-nombre\');
    document.getElementById(\'modal-overlay\').style.display = \'flex\';
    setTimeout(() => document.getElementById(\'modal-nombre\').focus(), 60);
  }}
  function cerrarModal() {{
    document.getElementById(\'modal-overlay\').style.display = \'none\';
    [\'modal-nombre\',\'modal-email\',\'modal-whatsapp\'].forEach(id => {{
      const el = document.getElementById(id);
      if (el) {{ el.value = \'\'; el.style.borderColor = \'#444\'; }}
    }});
  }}
  function onOverlayClick(e) {{
    if (e.target === document.getElementById(\'modal-overlay\')) cerrarModal();
  }}
  function mostrarPaso(id) {{
    [\'step-nombre\',\'step-moneda\',\'step-procesando\',\'step-usdt\',\'step-payoneer\',\'step-mp-mensual\'].forEach(s => {{
      const el = document.getElementById(s);
      if (el) el.classList.remove(\'active\');
    }});
    document.getElementById(id).classList.add(\'active\');
  }}
  function _marcarError(id) {{ const el = document.getElementById(id); el.style.borderColor = \'#ef4444\'; el.focus(); }}
  function _esEmailValido(s) {{ return /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(s); }}
  function _esWhatsappValido(s) {{ const limpio = s.replace(/[\\s\\-\\(\\)]/g, \'\'); return /^\\+?[0-9]{{8,15}}$/.test(limpio); }}
  function irAPaso2() {{
    const nombre = document.getElementById(\'modal-nombre\').value.trim();
    const email = document.getElementById(\'modal-email\').value.trim();
    const whatsapp = document.getElementById(\'modal-whatsapp\').value.trim();
    [\'modal-nombre\',\'modal-email\',\'modal-whatsapp\'].forEach(id => {{ document.getElementById(id).style.borderColor = \'#444\'; }});
    if (!nombre) {{ _marcarError(\'modal-nombre\'); return; }}
    if (!_esEmailValido(email)) {{ _marcarError(\'modal-email\'); return; }}
    if (!_esWhatsappValido(whatsapp)) {{ _marcarError(\'modal-whatsapp\'); return; }}
    mostrarPaso(\'step-moneda\');
  }}
  function volverAPaso1() {{ mostrarPaso(\'step-nombre\'); setTimeout(() => document.getElementById(\'modal-nombre\').focus(), 60); }}
  function seleccionarMoneda(m) {{
    _moneda = m;
    document.getElementById(\'opt-ars\').classList.toggle(\'selected\', m === \'ars\');
    const optUsdt = document.getElementById(\'opt-usdt\');
    if (optUsdt) optUsdt.classList.toggle(\'selected\', m === \'usdt\');
    const optPayoneer = document.getElementById(\'opt-payoneer\');
    if (optPayoneer) optPayoneer.classList.toggle(\'selected\', m === \'payoneer\');
    const info = document.getElementById(\'moneda-info\');
    if (m === \'ars\') {{
      info.style.background = \'rgba(59,130,246,0.08)\'; info.style.borderColor = \'rgba(59,130,246,0.2)\'; info.style.color = \'#93c5fd\';
      info.innerHTML = \'🏦 Pagarás en <strong>pesos argentinos</strong> a través de <strong>MercadoPago</strong>.\';
    }} else if (m === \'payoneer\') {{
      info.style.background = \'rgba(255,163,26,0.08)\'; info.style.borderColor = \'rgba(255,163,26,0.25)\'; info.style.color = \'#fbbf24\';
      info.innerHTML = \'💳 Pagarás en <strong>USD</strong> a través de <strong>Payoneer</strong>. Transferencia directa (confirmación manual en 24hs hábiles).\';
    }} else {{
      info.style.background = \'rgba(38,161,123,0.08)\'; info.style.borderColor = \'rgba(38,161,123,0.25)\'; info.style.color = \'#6ee7b7\';
      info.innerHTML = \'🪙 Pagarás en <strong>USDT/USDC</strong>. Transferencia directa a billetera cripto (confirmación manual en 24hs hábiles).\';
    }}
  }}
  function copiarDirUSDT() {{
    const el = document.getElementById(\'modal-usdt-dir\');
    if (!el) return;
    navigator.clipboard.writeText(el.value).then(() => {{
      el.style.borderColor = \'#26a17b\';
      setTimeout(() => el.style.borderColor = \'rgba(38,161,123,0.2)\', 1500);
    }}).catch(() => {{
      el.select(); document.execCommand(\'copy\');
    }});
  }}
  function copiarEmailPayoneer() {{
    const el = document.getElementById(\'modal-payoneer-email\');
    if (!el) return;
    navigator.clipboard.writeText(el.value).then(() => {{
      el.style.borderColor = \'#ffa31a\';
      setTimeout(() => el.style.borderColor = \'rgba(255,163,26,0.2)\', 1500);
    }}).catch(() => {{ el.select(); document.execCommand(\'copy\'); }});
  }}
  function copiarTexto(btn, txt) {{
    navigator.clipboard.writeText(txt).then(() => {{
      const _o = btn.innerHTML;
      btn.innerHTML = '<i class="fa-solid fa-check"></i>';
      setTimeout(() => btn.innerHTML = _o, 1200);
    }}).catch(() => {{}});
  }}
  async function confirmarContratacion() {{
    const nombre = document.getElementById(\'modal-nombre\').value.trim();
    const email = document.getElementById(\'modal-email\').value.trim();
    const whatsapp = document.getElementById(\'modal-whatsapp\').value.trim();
    if (!nombre || !email || !whatsapp) {{ volverAPaso1(); return; }}

    const esMensual = !!_PLANES_MENSUALES[_plan];

    // Flujo MercadoPago MANUAL — solo planes mensuales de mantenimiento.
    // Muestra Alias + CVU + titular y el monto convertido a pesos al dólar de hoy.
    if (esMensual && _moneda === \'ars\') {{
      const precioUsd = _PLAN_PRECIOS[_plan] || 0;
      const montoEl = document.getElementById(\'modal-mp-monto\');
      const refEl = document.getElementById(\'modal-mp-monto-ref\');
      if (montoEl) montoEl.textContent = \'Calculando…\';
      if (refEl) refEl.textContent = \'\';
      mostrarPaso(\'step-mp-mensual\');
      try {{
        const r = await fetch(\'/tipo-de-cambio\');
        const d = await r.json();
        const tc = parseFloat(d.venta) || 0;
        if (tc > 0) {{
          const ars = Math.round(precioUsd * tc);
          const arsFmt = ars.toLocaleString(\'es-AR\');
          if (montoEl) montoEl.textContent = \'$ \' + arsFmt + \' / mes\';
          if (refEl) refEl.textContent = \'USD \' + precioUsd + \' al dólar de hoy ($\' + tc.toLocaleString(\'es-AR\') + \')\';
        }} else {{
          if (montoEl) montoEl.textContent = \'USD \' + precioUsd + \' / mes\';
          if (refEl) refEl.textContent = \'Consultá el equivalente en pesos por WhatsApp.\';
        }}
      }} catch(_) {{
        if (montoEl) montoEl.textContent = \'USD \' + precioUsd + \' / mes\';
        if (refEl) refEl.textContent = \'Consultá el equivalente en pesos por WhatsApp.\';
      }}
      const waBtn = document.getElementById(\'modal-mp-wa-btn\');
      if (waBtn) {{
        const texto = encodeURIComponent(\'Hola \' + _MP_DATOS.titular + \', realicé la transferencia por MercadoPago para el \' + _plan + \' (mensual) de Avanza Digital. Mi nombre: \' + nombre + \', email: \' + email);
        waBtn.href = \'https://wa.me/{wa_contacto}?text=\' + texto;
      }}
      return;
    }}

    // Flujo Payoneer: mostrar instrucciones de transferencia
    if (_moneda === \'payoneer\') {{
      const precio = _PLAN_PRECIOS[_plan] || \'—\';
      document.getElementById(\'modal-payoneer-monto\').textContent = `USD $${{precio}}`;
      const waBtn = document.getElementById(\'modal-payoneer-wa-btn\');
      if (waBtn) {{
        const texto = encodeURIComponent(`Hola, realicé la transferencia de USD $${{precio}} por Payoneer para el ${{_plan}} de Avanza Digital. Mi nombre: ${{nombre}}, email: ${{email}}`);
        waBtn.href = `https://wa.me/{wa_contacto}?text=${{texto}}`;
      }}
      mostrarPaso(\'step-payoneer\');
      return;
    }}

    // Flujo USDT: mostrar instrucciones de transferencia sin checkout automático
    if (_moneda === \'usdt\') {{
      const precio = _PLAN_PRECIOS[_plan] || \'—\';
      document.getElementById(\'modal-usdt-monto\').textContent = `USD ${{precio}}`;
      // Actualizar link WA con el plan y monto
      const waBtn = document.getElementById(\'modal-usdt-wa-btn\');
      if (waBtn) {{
        const texto = encodeURIComponent(`Hola, realicé la transferencia de USD ${{precio}} en USDT para el ${{_plan}} de Avanza Digital. Mi nombre: ${{nombre}}, email: ${{email}}`);
        waBtn.href = `https://wa.me/{wa_contacto}?text=${{texto}}`;
      }}
      mostrarPaso(\'step-usdt\');
      return;
    }}

    mostrarPaso(\'step-procesando\');
    try {{
      const url = `/checkout/crear?plan=${{encodeURIComponent(_plan)}}&ref_code=${{_ref}}&nombre_cliente=${{encodeURIComponent(nombre)}}&moneda=${{_moneda}}&cliente_email=${{encodeURIComponent(email)}}&cliente_whatsapp=${{encodeURIComponent(whatsapp)}}`;
      const res = await fetch(url, {{method:\'POST\'}});
      let data;
      try {{ data = await res.json(); }} catch(_) {{ data = {{}}; }}
      if (!res.ok) {{
        alert(data.detail || \'Error al generar el link de pago. Intentá de nuevo.\');
        mostrarPaso(\'step-moneda\');
      }} else if (data.checkout_url) {{
        if (data.checkout_url.startsWith('tron:') || data.tipo === 'usdt') {{
          const dirEl = document.getElementById('modal-usdt-dir');
          const montoEl = document.getElementById('modal-usdt-monto');
          if (dirEl && data.direccion) dirEl.value = data.direccion;
          if (montoEl) montoEl.textContent = 'USD ' + (data.monto_usdt || '—');
          const waBtn = document.getElementById('modal-usdt-wa-btn');
          if (waBtn) {{
            const _nom = document.getElementById('modal-nombre').value;
            const _em  = document.getElementById('modal-email').value;
            const texto = encodeURIComponent('Hola, realicé la transferencia de USD ' + (data.monto_usdt||'') + ' en USDT para el ' + _plan + '. Mi nombre: ' + _nom + ', email: ' + _em);
            waBtn.href = 'https://wa.me/{wa_contacto}?text=' + texto;
          }}
          mostrarPaso('step-usdt');
        }} else {{
          window.location.href = data.checkout_url;
        }}
      }} else {{
        alert(\'Error al generar el link de pago. Intentá de nuevo.\');
        mostrarPaso(\'step-moneda\');
      }}
    }} catch(e) {{
      alert(\'Error de conexión. Revisá tu conexión e intentá de nuevo.\');
      mostrarPaso(\'step-moneda\');
    }}
  }}
</script>
</body></html>"""

    return HTMLResponse(html)



@router.patch("/aliados/{codigo}/portal-publico")
def configurar_portal_publico(codigo: str,
                              body: schemas.ActualizarPerfilIn | None = Body(default=None),
                              activo: bool = True,
                              titular: str = "",
                              bio: str = "",
                              db: Session = Depends(get_db),
                              _owner=Depends(verify_ownership_dep)):
    a = _get_aliado(codigo, db)
    if body is not None:
        if body.portal_publico_activo is not None:
            a.portal_publico_activo = body.portal_publico_activo
        if body.portal_publico_titular is not None:
            a.portal_publico_titular = body.portal_publico_titular[:120] or None
        if body.portal_publico_bio is not None:
            a.portal_publico_bio = body.portal_publico_bio[:500] or None
        if body.portal_publico_foto_url is not None:
            # Aceptar solo URLs https:// para evitar XSS
            url = body.portal_publico_foto_url.strip()
            if url.startswith("https://"):
                a.portal_publico_foto_url = url
            else:
                a.portal_publico_foto_url = None
    else:
        a.portal_publico_activo = activo
        if titular: a.portal_publico_titular = titular[:120]
        if bio:     a.portal_publico_bio = bio[:500]
    db.commit()
    return {
        "mensaje": "Portal público actualizado.",
        "url": f"/p/{a.ref_code}",
        "titular": a.portal_publico_titular,
        "bio": a.portal_publico_bio,
        "activo": a.portal_publico_activo,
        "foto_url": a.portal_publico_foto_url,
    }
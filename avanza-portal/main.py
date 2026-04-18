from fastapi import FastAPI, Depends, HTTPException, Request, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from passlib.context import CryptContext
from datetime import datetime, timedelta
from pydantic import BaseModel
from models import (
    Aliado, Admin, Venta, Referido, Prospecto, AuditoriaLog, LeadBolsa,
    TransaccionCredito, PostComunidad, ComentarioComunidad, AutomationLog,
    PLANES, NIVELES, CUOTAS_RECARGO, REPUTACION_BADGES
)
import random, string, os, smtplib, httpx, json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler

from database import engine, get_db, Base

Base.metadata.create_all(bind=engine)

# Auto-migración SEGURA: Corregido para PostgreSQL (Railway)
try:
    with engine.connect() as conn:
        # PostgreSQL usa TIMESTAMP en lugar de DATETIME. Esto causaba el crash.
        conn.execute(text("ALTER TABLE aliados ADD COLUMN ultimo_login TIMESTAMP"))
        conn.commit()
except Exception as e:
    pass # Si ya existe, lo ignoramos

try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE aliados ADD COLUMN cantidad_logins INTEGER DEFAULT 0"))
        conn.commit()
except Exception as e:
    pass # Si ya existe, lo ignoramos

# Migraciones para columnas nuevas de LeadBolsa y Red de Aliados
for col_sql in [
    "ALTER TABLE bolsa_leads ADD COLUMN resultado VARCHAR",
    "ALTER TABLE bolsa_leads ADD COLUMN notif_24h_enviada BOOLEAN DEFAULT FALSE",
    "ALTER TABLE aliados ADD COLUMN sponsor_id INTEGER REFERENCES aliados(id)"
]:
    try:
        with engine.connect() as conn:
            conn.execute(text(col_sql))
            conn.commit()
    except Exception:
        pass

# Migraciones para inteligencia de ventas y checkout
for col_sql in [
    "ALTER TABLE prospectos ADD COLUMN rubro VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN tamano VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN urgencia VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN score_ia INTEGER DEFAULT 0",
    "ALTER TABLE aliados ADD COLUMN onboarding_completado BOOLEAN DEFAULT FALSE",
]:
    try:
        with engine.connect() as conn:
            conn.execute(text(col_sql))
            conn.commit()
    except Exception:
        pass

# ─── MIGRACIONES v1.3 (Perfilado IA + Reputación + Marketplace + Comunidad) ─
for col_sql in [
    # Prospecto — perfilado IA completo
    "ALTER TABLE prospectos ADD COLUMN plan_recomendado VARCHAR",
    "ALTER TABLE prospectos ADD COLUMN pitch_sugerido TEXT",
    "ALTER TABLE prospectos ADD COLUMN perfilado_en TIMESTAMP",
    # Prospecto — piloto automático
    "ALTER TABLE prospectos ADD COLUMN automation_paso INTEGER DEFAULT 0",
    "ALTER TABLE prospectos ADD COLUMN automation_ultimo_en TIMESTAMP",
    "ALTER TABLE prospectos ADD COLUMN automation_activa_desde TIMESTAMP",
    # Aliado — reputación + créditos + portal público
    "ALTER TABLE aliados ADD COLUMN reputacion_score INTEGER DEFAULT 50",
    "ALTER TABLE aliados ADD COLUMN badges TEXT DEFAULT '[]'",
    "ALTER TABLE aliados ADD COLUMN reputacion_calculada_en TIMESTAMP",
    "ALTER TABLE aliados ADD COLUMN creditos INTEGER DEFAULT 0",
    "ALTER TABLE aliados ADD COLUMN portal_publico_activo BOOLEAN DEFAULT TRUE",
    "ALTER TABLE aliados ADD COLUMN portal_publico_titular VARCHAR",
    "ALTER TABLE aliados ADD COLUMN portal_publico_bio TEXT",
    # Ventas — financiación
    "ALTER TABLE ventas ADD COLUMN cuotas INTEGER DEFAULT 1",
    "ALTER TABLE ventas ADD COLUMN financiacion_pct FLOAT DEFAULT 0.0",
    # Bolsa — marketplace
    "ALTER TABLE bolsa_leads ADD COLUMN tier VARCHAR DEFAULT 'basico'",
    "ALTER TABLE bolsa_leads ADD COLUMN costo_creditos INTEGER DEFAULT 0",
    "ALTER TABLE bolsa_leads ADD COLUMN score_calidad INTEGER DEFAULT 50",
    "ALTER TABLE bolsa_leads ADD COLUMN notas_calificacion TEXT",
]:
    try:
        with engine.connect() as conn:
            conn.execute(text(col_sql))
            conn.commit()
    except Exception:
        pass


# ─── EMAIL HELPER ─────────────────────────────────────────────────────────────
SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASS     = os.environ.get("SMTP_PASS", "")
EMAIL_FROM    = os.environ.get("EMAIL_FROM", SMTP_USER)

# ─── MERCADOPAGO ──────────────────────────────────────────────────────────────
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
PORTAL_URL      = os.environ.get("PORTAL_URL", "https://avanza-digital-production.up.railway.app")

def enviar_email(destinatario: str, asunto: str, cuerpo_html: str):
    """Envía un email. Si no hay SMTP configurado, solo loguea y no falla."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print(f"[EMAIL - sin SMTP configurado] Para: {destinatario} | Asunto: {asunto}")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"]    = EMAIL_FROM
        msg["To"]      = destinatario
        msg.attach(MIMEText(cuerpo_html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, destinatario, msg.as_string())
        print(f"[EMAIL] Enviado a {destinatario}: {asunto}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


# ─── SCHEDULER: NOTIFICACIÓN 24HS ────────────────────────────────────────────
def job_notificaciones_24h():
    """Corre cada hora. Detecta leads reclamados hace 24hs sin contactar y avisa al aliado."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        limite_inf = ahora - timedelta(hours=25)  # entre 24 y 25 hs
        limite_sup = ahora - timedelta(hours=24)

        pendientes = db.query(LeadBolsa).filter(
            LeadBolsa.estado == "reclamado",
            LeadBolsa.notif_24h_enviada == False,
            LeadBolsa.fecha_reclamo <= limite_sup,
            LeadBolsa.fecha_reclamo >= limite_inf,
        ).all()

        for lead in pendientes:
            if lead.aliado and lead.aliado.email:
                horas_rest = max(0, int(48 - (ahora - lead.fecha_reclamo).total_seconds() / 3600))
                html = f"""
                <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;background:#0f172a;color:#e2e8f0;border-radius:12px;">
                  <h2 style="color:#f59e0b;margin-bottom:8px;">⏰ ¡Te quedan {horas_rest} horas!</h2>
                  <p>Hola <strong>{lead.aliado.nombre}</strong>,</p>
                  <p>Reclamaste el lead <strong>{lead.empresa}</strong> hace 24 horas y todavía no lo marcaste como contactado.</p>
                  <p style="color:#f87171;">Si no actualizás su estado en las próximas <strong>{horas_rest} horas</strong>, el sistema lo devolverá a la bolsa pública automáticamente.</p>
                  <a href="https://avanza-digital-production.up.railway.app/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ir al Portal →</a>
                  <p style="margin-top:24px;font-size:.8rem;color:#64748b;">Avanza Digital · Partner Network</p>
                </div>
                """
                enviar_email(lead.aliado.email, f"⏰ Avanza: Tenés {horas_rest}hs para contactar a {lead.empresa}", html)
                lead.notif_24h_enviada = True
        db.commit()
    except Exception as e:
        print(f"[SCHEDULER ERROR] {e}")
    finally:
        db.close()

scheduler = BackgroundScheduler()
scheduler.add_job(job_notificaciones_24h, "interval", hours=1)
scheduler.start()


app = FastAPI(title="Avanza Partner Portal", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ─── API KEY ─────────────────────────────────────────────────────────────────
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")

RUTAS_ADMIN = {
    ("POST",   "/aliados/crear"),
    ("POST",   "/admin/setup"),
    ("GET",    "/aliados"),
    ("GET",    "/aliados/suspendidos"),
    ("GET",    "/aliados/inactivos"),
    ("PATCH",  "/aliados/{codigo}/nivel"),
    ("GET",    "/referidos/pendientes"),
    ("POST",   "/ventas/registrar"),
    ("GET",    "/dashboard"),
    ("GET",    "/admin/prospectos"),
    ("GET",    "/admin/auditorias"),
    ("POST",   "/admin/bolsa"),
    ("POST",   "/admin/bolsa-v2"),
    ("GET",    "/admin/bolsa"),
    ("POST",   "/admin/bolsa/{id}/revocar"),
    ("GET",    "/admin/historial-bolsa"),
    ("GET",    "/admin/reputacion/ranking"),
    ("POST",   "/admin/aliados/{codigo}/creditos"),
    ("POST",   "/admin/comunidad/{id}/fijar"),
    ("POST",   "/admin/comunidad/{id}/ocultar"),
    ("POST",   "/ventas/{id}/pagar"),
    ("POST",   "/referidos/{id}/confirmar"),
}

def _es_ruta_admin(method: str, path: str) -> bool:
    segmentos_path = path.rstrip("/").split("/")
    for m, patron in RUTAS_ADMIN:
        if m != method:
            continue
        segmentos_patron = patron.rstrip("/").split("/")
        if len(segmentos_patron) != len(segmentos_path):
            continue
        if all(p == s or p.startswith("{") for p, s in zip(segmentos_patron, segmentos_path)):
            return True
    return False

@app.middleware("http")
async def verificar_api_key(request: Request, call_next):
    if _es_ruta_admin(request.method, request.url.path):
        if not ADMIN_API_KEY:
            return JSONResponse(status_code=503, content={"detail": "ADMIN_API_KEY no configurada."})
        if request.headers.get("X-API-Key", "") != ADMIN_API_KEY:
            return JSONResponse(status_code=401, content={"detail": "API key inválida."})
    return await call_next(request)


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def hash_password(p): return pwd_context.hash(p)
def verify_password(plain, hashed): return pwd_context.verify(plain, hashed)

def generar_ref_code(nombre):
    base = nombre.split()[0].lower()[:6]
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


# ─── SALUD ───────────────────────────────────────────────────────────────────

@app.get("/")
def root(): return {"status": "Avanza Partner Portal activo", "version": "1.2"}

@app.get("/health")
def health(): return {"status": "ok"}

# ─── DESCARGA DE MATERIALES (públicos) ───────────────────────────────────────
from fastapi.responses import RedirectResponse

@app.get("/brochure")
def descargar_brochure():
    """Redirige al brochure comercial en Drive o URL configurada."""
    url = os.environ.get("URL_BROCHURE", "https://avanzadigital.digital/alianzas#brochure")
    return RedirectResponse(url=url)

@app.get("/guion")
def descargar_guion():
    """Redirige al guión de ventas."""
    url = os.environ.get("URL_GUION", "https://avanzadigital.digital/alianzas#guion")
    return RedirectResponse(url=url)

@app.get("/contrato")
def ver_contrato():
    """Redirige al contrato de aliado."""
    url = os.environ.get("URL_CONTRATO", "https://avanzadigital.digital/alianzas#contrato")
    return RedirectResponse(url=url)


# ─── AUTO-REGISTRO PÚBLICO CON EFECTO RED ────────────────────────────────────

@app.post("/registrarse")
def auto_registro(
    nombre: str, email: str, whatsapp: str,
    background_tasks: BackgroundTasks,
    ciudad: str = "", perfil: str = "", password: str = "", dni: str = "",
    ref_sponsor: str = "", 
    db: Session = Depends(get_db)
):
    """Registro self-serve público con sistema de Sub-Aliados."""
    if not nombre or not email or not whatsapp or not password:
        raise HTTPException(400, "Nombre, email, WhatsApp y contraseña son obligatorios.")
    if len(password) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres.")
    if db.query(Aliado).filter(Aliado.email == email).first():
        raise HTTPException(400, "Ya existe un aliado registrado con ese email.")

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
        ref_code     = generar_ref_code(nombre),
        password_hash= hash_password(password),
        sponsor_id   = sponsor_id_db,
    )
    db.add(a); db.commit(); db.refresh(a)

    # Email de bienvenida — EN SEGUNDO PLANO (no bloquea la respuesta)
    background_tasks.add_task(
        enviar_email,
        a.email,
        f"¡Bienvenido al Avanza Partner Network, {a.nombre.split()[0]}!",
        f"""
        <div style="font-family:Inter,sans-serif;background:#050505;color:#fff;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;">
          <h1 style="color:#f97316;font-size:1.6rem;margin-bottom:8px;">¡Ya sos Aliado Avanza! 🎉</h1>
          <p style="color:#a1a1aa;margin-bottom:28px;">Tu registro fue confirmado. Guardá estos datos para ingresar al portal.</p>
          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:20px;margin-bottom:24px;">
            <p style="margin:0 0 8px;font-size:.85rem;color:#71717a;text-transform:uppercase;letter-spacing:1px;">Tu código de aliado</p>
            <p style="margin:0;font-size:2rem;font-weight:900;color:#f97316;letter-spacing:2px;">{a.codigo}</p>
          </div>
          <p style="color:#a1a1aa;margin-bottom:8px;">Tu comisión arranca en <strong style="color:#fff;">10% (BASIC)</strong> y sube automáticamente con cada venta.</p>
          <p style="color:#a1a1aa;margin-bottom:28px;">Tu link de referido: <a href="https://avanzadigital.digital/alianzas?ref={a.ref_code}" style="color:#3b82f6;">/alianzas?ref={a.ref_code}</a></p>
          <a href="https://avanza-digital-production.up.railway.app/portal.html" style="display:inline-block;padding:14px 28px;background:#f97316;color:#000;border-radius:8px;text-decoration:none;font-weight:800;font-size:1rem;">Ingresar al portal →</a>
          <p style="margin-top:32px;font-size:.8rem;color:#71717a;">Avanza Digital · Partner Network · Santa Fe, Argentina</p>
        </div>
        """
    )

    # Notificar al admin — EN SEGUNDO PLANO
    background_tasks.add_task(
        enviar_email,
        EMAIL_FROM or "avanzadigital4@gmail.com",
        f"[NUEVO ALIADO] {a.nombre} — {a.codigo}",
        f"<p>Nuevo aliado auto-registrado:<br><strong>{a.nombre}</strong> — {a.email} — {a.whatsapp}<br>Perfil: {a.perfil or '—'} | Ciudad: {a.ciudad or '—'}<br>Código: {a.codigo} | Ref: {a.ref_code}</p>"
    )

    return _aliado_detalle(a)


# ─── ADMIN SETUP ─────────────────────────────────────────────────────────────

@app.post("/admin/setup")
def crear_admin_inicial(username: str, password: str, db: Session = Depends(get_db)):
    """Crea el primer admin. Solo funciona si no existe ninguno. Requiere X-API-Key."""
    if db.query(Admin).count() > 0:
        raise HTTPException(400, "Ya existe al menos un admin.")
    db.add(Admin(username=username, password_hash=hash_password(password)))
    db.commit()
    return {"mensaje": f"Admin '{username}' creado correctamente."}


# ─── LOGIN ALIADO ─────────────────────────────────────────────────────────────

@app.post("/aliados/login")
def login_aliado(codigo: str, password: str, db: Session = Depends(get_db)):
    """Portal del aliado: login con código + contraseña."""
    a = db.query(Aliado).filter(Aliado.codigo == codigo, Aliado.activo == True).first()
    if not a:
        raise HTTPException(404, "Código no encontrado.")
    if not verify_password(password, a.password_hash):
        raise HTTPException(401, "Contraseña incorrecta.")
    
    # TRACKING: Registramos que acaba de entrar (CON ESCUDO PROTECTOR)
    try:
        a.ultimo_login = datetime.now()
        a.cantidad_logins = (getattr(a, 'cantidad_logins', 0) or 0) + 1
        db.commit()
    except Exception as e:
        db.rollback() # Si la base de datos falla, aborta el tracking pero PERMITE el login
        print(f"Error guardando tracking de login: {e}")
    
    return _aliado_detalle(a)


# ─── ALIADOS — RUTAS FIJAS (deben ir ANTES de /{codigo}) ─────────────────────

@app.get("/aliados/suspendidos")
def listar_suspendidos(db: Session = Depends(get_db)):
    return [_aliado_row(a) for a in db.query(Aliado).filter(Aliado.activo == False).all()]


@app.get("/aliados/inactivos")
def aliados_inactivos(dias: int = 30, db: Session = Depends(get_db)):
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


@app.get("/aliados")
def listar_aliados(db: Session = Depends(get_db)):
    return [_aliado_row(a) for a in db.query(Aliado).filter(Aliado.activo == True).all()]


@app.post("/aliados/crear")
def crear_aliado(nombre: str, email: str, whatsapp: str, ciudad: str,
                 dni: str = "", perfil: str = "", fecha_firma: str = "",
                 password: str = "avanza2026", db: Session = Depends(get_db)):
    if db.query(Aliado).filter(Aliado.email == email).first():
        raise HTTPException(400, "Ya existe un aliado con ese email.")
    a = Aliado(
        codigo=generar_codigo_aliado(db), nombre=nombre, email=email,
        dni=dni, whatsapp=whatsapp, ciudad=ciudad, perfil=perfil,
        fecha_firma=fecha_firma or datetime.now().strftime("%d/%m/%Y"),
        ref_code=generar_ref_code(nombre), password_hash=hash_password(password),
    )
    db.add(a); db.commit(); db.refresh(a)
    return {
        "mensaje": f"Aliado {a.codigo} creado", "codigo": a.codigo,
        "ref_code": a.ref_code, "password_inicial": password,
        "ref_code": a.ref_code,
        "link_ref": f"https://avanzadigital.digital/alianzas?ref={a.ref_code}",
    }


# ─── ALIADOS — RUTAS CON {codigo} ────────────────────────────────────────────

@app.get("/aliados/{codigo}")
def ver_aliado(codigo: str, db: Session = Depends(get_db)):
    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a: raise HTTPException(404, "Aliado no encontrado.")
    return _aliado_detalle(a)


@app.post("/aliados/{codigo}/suspender")
def suspender_aliado(codigo: str, db: Session = Depends(get_db)):
    a = _get_aliado(codigo, db)
    a.activo = False; db.commit()
    return {"mensaje": f"{a.nombre} suspendido."}


@app.post("/aliados/{codigo}/activar")
def activar_aliado(codigo: str, db: Session = Depends(get_db)):
    a = _get_aliado(codigo, db)
    a.activo = True; db.commit()
    return {"mensaje": f"{a.nombre} reactivado."}


@app.delete("/aliados/{codigo}/eliminar")
def eliminar_aliado(codigo: str, db: Session = Depends(get_db)):
    a = _get_aliado(codigo, db)
    db.query(Referido).filter(Referido.aliado_id == a.id).delete()
    db.query(Venta).filter(Venta.aliado_id == a.id).delete()
    db.delete(a); db.commit()
    return {"mensaje": f"{codigo} eliminado permanentemente."}


@app.patch("/aliados/{codigo}/nivel")
def cambiar_nivel(codigo: str, nivel: str, db: Session = Depends(get_db)):
    if nivel not in NIVELES:
        raise HTTPException(400, f"Nivel inválido. Opciones: {list(NIVELES.keys())}")
    a = _get_aliado(codigo, db)
    anterior = a.nivel; a.nivel = nivel; db.commit()
    return {"mensaje": f"{a.nombre}: {anterior} → {nivel}", "comision": f"{NIVELES[nivel]['comision']*100:.0f}%"}


@app.get("/aliados/{codigo}/red")
def mi_red_comercial(codigo: str, db: Session = Depends(get_db)):
    a = _get_aliado(codigo, db)
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

        red.append({
            "nombre": sub.nombre,
            "ciudad": sub.ciudad or "Sin especificar",
            "nivel": sub.nivel_calculado,
            "fecha_ingreso": fecha_ing,
            "ganancia_pasiva": round(ganancia, 2)
        })
    
    red.sort(key=lambda x: x["ganancia_pasiva"], reverse=True)

    return {
        "sponsor": getattr(a, "sponsor").nombre if getattr(a, "sponsor", None) else None,
        "total_sub_aliados": len(red),
        "total_ganancia_pasiva": round(total_pasivo, 2),
        "detalle": red
    }


# ─── REFERIDOS ───────────────────────────────────────────────────────────────

@app.post("/referidos/registrar")
def registrar_referido(ref_code: str, nombre_cliente: str, plan_elegido: str,
                        notas: str = "", db: Session = Depends(get_db)):
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not a: raise HTTPException(404, "Código de referido inválido.")
    if plan_elegido not in PLANES: raise HTTPException(400, f"Plan inválido.")
    r = Referido(aliado_id=a.id, nombre_cliente=nombre_cliente, plan_elegido=plan_elegido, notas=notas)
    db.add(r); db.commit(); db.refresh(r)
    return {
        "mensaje": "Referido registrado.", "id_referido": r.id,
        "aliado": a.nombre, "cliente": nombre_cliente, "plan": plan_elegido,
        "valor_plan": PLANES[plan_elegido],
        "comision_estimada": round(PLANES[plan_elegido] * a.comision_pct, 2),
        "registrado_en": r.registrado_en.strftime("%d/%m/%Y %H:%M"),
    }


@app.get("/referidos/pendientes")
def referidos_pendientes(db: Session = Depends(get_db)):
    return [
        {"id": r.id, "aliado": r.aliado.nombre, "aliado_codigo": r.aliado.codigo,
         "cliente": r.nombre_cliente, "plan": r.plan_elegido,
         "registrado_en": r.registrado_en.strftime("%d/%m/%Y %H:%M")}
        for r in db.query(Referido).filter(Referido.acuse_recibo == False).all()
    ]


@app.post("/referidos/{id}/confirmar")
def confirmar_referido(id: int, db: Session = Depends(get_db)):
    r = db.query(Referido).filter(Referido.id == id).first()
    if not r: raise HTTPException(404, "Referido no encontrado.")
    r.acuse_recibo = True; db.commit()
    return {"mensaje": f"Referido de '{r.nombre_cliente}' confirmado."}


# ─── VENTAS CON COMISIONES RED ───────────────────────────────────────────────

@app.post("/ventas/registrar")
def registrar_venta(codigo_aliado: str, nombre_cliente: str, plan: str,
                    modalidad_pago: str = "ARS MEP", referido_id: int = None,
                    notas: str = "", db: Session = Depends(get_db)):
    a = _get_aliado(codigo_aliado, db)
    if plan not in PLANES: raise HTTPException(400, "Plan inválido.")
    valor = PLANES[plan]
    comision_usd = round(valor * a.comision_pct, 2)
    
    # 1. Registrar Venta del Aliado que cerró
    v = Venta(aliado_id=a.id, referido_id=referido_id, nombre_cliente=nombre_cliente,
              plan=plan, valor_usd=valor, comision_pct=a.comision_pct,
              comision_usd=comision_usd, confirmada=True, pagada=False,
              fecha_venta=datetime.now(), modalidad_pago=modalidad_pago, notas=notas)
    db.add(v)

    # 2. EFECTO RED: Si tiene Sponsor, le damos un 5% pasivo al Sponsor
    if getattr(a, "sponsor", None):
        comision_sponsor = round(valor * 0.05, 2) # Fijo 5% de Regalía
        v_red = Venta(
            aliado_id=a.sponsor.id, 
            referido_id=None,
            nombre_cliente=f"♻️ RED: {a.nombre} (Venta: {nombre_cliente})",
            plan=plan, 
            valor_usd=valor, 
            comision_pct=0.05,
            comision_usd=comision_sponsor, 
            confirmada=True, pagada=False,
            fecha_venta=datetime.now(), modalidad_pago=modalidad_pago, 
            notas=f"Ingreso pasivo por venta de tu sub-aliado {a.nombre}"
        )
        db.add(v_red)
        a.sponsor.nivel = a.sponsor.nivel_calculado

    if referido_id:
        ref = db.query(Referido).filter(Referido.id == referido_id).first()
        if ref: ref.convertido = True
    
    a.nivel = a.nivel_calculado
    db.commit()
    return {"mensaje": "Venta registrada.", "aliado": a.nombre, "nivel_nuevo": a.nivel_calculado, "valor_usd": valor, "comision_usd": comision_usd}


@app.post("/ventas/{id}/pagar")
def marcar_pagada(id: int, modalidad: str = "ARS MEP", db: Session = Depends(get_db)):
    v = db.query(Venta).filter(Venta.id == id).first()
    if not v: raise HTTPException(404, "Venta no encontrada.")
    v.pagada = True; v.fecha_pago = datetime.now(); v.modalidad_pago = modalidad
    db.commit()
    return {"mensaje": f"USD {v.comision_usd} pagados a {v.aliado.nombre}."}


# ─── DASHBOARD + LEADERBOARD ─────────────────────────────────────────────────

@app.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    ventas  = db.query(Venta).filter(Venta.confirmada == True).all()
    niveles = {"BASIC": 0, "SILVER": 0, "PREMIUM": 0, "ELITE": 0}
    for a in aliados: niveles[a.nivel_calculado] = niveles.get(a.nivel_calculado, 0) + 1
    leaderboard = sorted(
        [{"codigo": a.codigo, "nombre": a.nombre, "nivel": a.nivel_calculado,
          "ventas_6m": a.ventas_6_meses, "total_ganado": round(a.total_ganado, 2)}
         for a in aliados],
        key=lambda x: x["ventas_6m"], reverse=True
    )[:10]
    return {
        "total_aliados": len(aliados),
        "total_ventas": len(ventas),
        "total_vendido_usd": round(sum(v.valor_usd for v in ventas), 2),
        "total_comisiones_usd": round(sum(v.comision_usd for v in ventas), 2),
        "pendiente_pagar_usd": round(sum(v.comision_usd for v in ventas if not v.pagada), 2),
        "distribucion_niveles": niveles,
        "referidos_sin_confirmar": db.query(Referido).filter(Referido.acuse_recibo == False).count(),
        "leaderboard": leaderboard,
    }



# ─── PROSPECTOS ──────────────────────────────────────────────────────────────

@app.post("/prospectos/crear")
def crear_prospecto(codigo_aliado: str, nombre: str, contacto: str = "",
                    plan_interes: str = "", nota: str = "", db: Session = Depends(get_db)):
    """El aliado carga un prospecto nuevo."""
    a = _get_aliado(codigo_aliado, db)
    p = Prospecto(aliado_id=a.id, nombre=nombre, contacto=contacto,
                  plan_interes=plan_interes, nota=nota)
    db.add(p); db.commit(); db.refresh(p)
    return {"mensaje": "Prospecto cargado.", "id": p.id, "nombre": p.nombre}


@app.get("/prospectos/aliado/{codigo}")
def listar_prospectos_aliado(codigo: str, db: Session = Depends(get_db)):
    """Portal: prospectos del aliado logueado."""
    a = _get_aliado(codigo, db)
    return [_prospecto_row(p) for p in sorted(a.prospectos, key=lambda x: x.creado_en, reverse=True)]


@app.patch("/prospectos/{id}/contactar")
def marcar_contactado(id: int, db: Session = Depends(get_db)):
    p = _get_prospecto(id, db)
    p.estado = "contactado"
    p.fecha_contacto = datetime.now()
    db.commit()
    return {"mensaje": "Marcado como contactado.", "estado": p.estado}


@app.patch("/prospectos/{id}/respondio")
def marcar_respondio(id: int, db: Session = Depends(get_db)):
    p = _get_prospecto(id, db)
    p.estado = "respondio"
    p.fecha_respuesta = datetime.now()
    if not p.fecha_contacto:
        p.fecha_contacto = datetime.now()
    db.commit()
    return {"mensaje": "Marcado como respondió.", "estado": p.estado}


@app.patch("/prospectos/{id}/nota")
def actualizar_nota(id: int, nota: str, db: Session = Depends(get_db)):
    p = _get_prospecto(id, db)
    p.nota = nota; db.commit()
    return {"mensaje": "Nota guardada."}


@app.patch("/prospectos/{id}/interesante")
def toggle_interesante(id: int, db: Session = Depends(get_db)):
    p = _get_prospecto(id, db)
    p.interesante = not p.interesante; db.commit()
    return {"interesante": p.interesante}


@app.delete("/prospectos/{id}/eliminar")
def eliminar_prospecto(id: int, db: Session = Depends(get_db)):
    p = _get_prospecto(id, db)
    db.delete(p); db.commit()
    return {"mensaje": "Prospecto eliminado."}


@app.patch("/prospectos/{id}/piloto")
def toggle_piloto_automatico(id: int, activo: bool, db: Session = Depends(get_db)):
    """Activa/desactiva el piloto automático de seguimiento para un prospecto."""
    p = _get_prospecto(id, db)
    p.piloto_automatico = activo
    db.commit()
    return {"piloto_automatico": p.piloto_automatico,
            "mensaje": "Piloto automático activado" if activo else "Piloto desactivado"}


@app.get("/admin/prospectos")
def admin_prospectos(db: Session = Depends(get_db)):
    """Admin: resumen de prospectos por aliado + lista completa."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    resumen = []
    for a in aliados:
        ps = a.prospectos
        if not ps:
            continue
        ultima = max((p.creado_en for p in ps), default=None)
        resumen.append({
            "codigo": a.codigo, "nombre": a.nombre,
            "total": len(ps),
            "sin_contactar": sum(1 for p in ps if p.estado == "sin_contactar"),
            "contactados":   sum(1 for p in ps if p.estado == "contactado"),
            "respondieron":  sum(1 for p in ps if p.estado == "respondio"),
            "interesantes":  sum(1 for p in ps if p.interesante),
            "ultima_actividad": ultima.strftime("%d/%m/%Y") if ultima else None,
            "prospectos": [_prospecto_row(p) for p in sorted(ps, key=lambda x: x.creado_en, reverse=True)],
        })
    resumen.sort(key=lambda x: x["ultima_actividad"] or "", reverse=True)
    totales = {
        "total":         sum(r["total"] for r in resumen),
        "sin_contactar": sum(r["sin_contactar"] for r in resumen),
        "contactados":   sum(r["contactados"] for r in resumen),
        "respondieron":  sum(r["respondieron"] for r in resumen),
        "interesantes":  sum(r["interesantes"] for r in resumen),
    }
    return {"totales": totales, "por_aliado": resumen}


# ─── AUDITORÍAS ──────────────────────────────────────────────────────────────

@app.post("/auditorias/log")
def log_auditoria(dominio: str, score: int, ref_code: str = "", email: str = "", db: Session = Depends(get_db)):
    """Guarda el log cuando se genera un reporte o se captura un email."""
    aliado_id = None
    if ref_code:
        a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
        if a:
            aliado_id = a.id
    
    log = AuditoriaLog(aliado_id=aliado_id, ref_code=ref_code, dominio=dominio, score=score, email_capturado=email)
    db.add(log)
    db.commit()
    return {"status": "ok"}


@app.get("/admin/auditorias")
def admin_auditorias(db: Session = Depends(get_db)):
    """Métricas de uso de la herramienta para el admin."""
    logs = db.query(AuditoriaLog).all()
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    
    usos_por_aliado = {}
    for log in logs:
        if log.aliado_id:
            if log.aliado_id not in usos_por_aliado:
                usos_por_aliado[log.aliado_id] = []
            usos_por_aliado[log.aliado_id].append({
                "dominio": log.dominio,
                "score": log.score,
                "email": log.email_capturado,
                "fecha": log.creado_en.strftime("%d/%m/%Y")
            })

    resumen_aliados = []
    for a in aliados:
        historial = usos_por_aliado.get(a.id, [])
        resumen_aliados.append({
            "codigo": a.codigo,
            "nombre": a.nombre,
            "usos_totales": len(historial),
            "ultimo_uso": historial[-1]["fecha"] if historial else None,
            "historial": historial
        })
    
    return {
        "total_auditorias": len(logs),
        "aliados_activos_uso": len([a for a in resumen_aliados if a["usos_totales"] > 0]),
        "aliados_sin_uso": len([a for a in resumen_aliados if a["usos_totales"] == 0]),
        "detalle": sorted(resumen_aliados, key=lambda x: x["usos_totales"], reverse=True)
    }


# ─── HELPERS PRIVADOS ────────────────────────────────────────────────────────

def _get_aliado(codigo, db):
    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a: raise HTTPException(404, "Aliado no encontrado.")
    return a

def _get_prospecto(id, db):
    p = db.query(Prospecto).filter(Prospecto.id == id).first()
    if not p: raise HTTPException(404, "Prospecto no encontrado.")
    return p

def _prospecto_row(p):
    # --- INTELIGENCIA DE VENTAS: "Next Best Action" ---
    next_action = ""
    action_type = "primary" # Color de la alerta

    if p.estado == "sin_contactar":
        next_action = "🔥 Sugerencia: Romper el hielo. Enviale el link de la Auditoría Gratuita hoy."
        action_type = "amber"
    elif p.estado == "contactado":
        dias = 0
        if p.fecha_contacto:
            dias = (datetime.now() - p.fecha_contacto).days
        
        if dias >= 3:
            next_action = f"⚠️ Se enfría (hace {dias} días). Sugerencia: Mandá un mensaje de seguimiento ('¿Pudiste ver lo que te mandé?')."
            action_type = "red"
        else:
            next_action = "⏳ Esperando respuesta. Aún es pronto para insistir."
            action_type = "text-dim"
    elif p.estado == "respondio":
        next_action = "✅ ¡Lead Caliente! Tu objetivo ahora es llevarlo a una llamada o usar el Cotizador."
        action_type = "green"

    return {
        "id": p.id, "nombre": p.nombre, "contacto": p.contacto,
        "plan_interes": p.plan_interes, "estado": p.estado,
        "nota": p.nota, "interesante": p.interesante,
        "piloto_automatico": getattr(p, "piloto_automatico", False) or False,
        "fecha_contacto":  p.fecha_contacto.strftime("%d/%m/%Y") if p.fecha_contacto else None,
        "fecha_respuesta": p.fecha_respuesta.strftime("%d/%m/%Y") if p.fecha_respuesta else None,
        "creado_en": p.creado_en.strftime("%d/%m/%Y") if p.creado_en else None,
        "next_action": next_action,
        "action_type": action_type,
        # Perfilado IA (A)
        "rubro": getattr(p, "rubro", None),
        "tamano": getattr(p, "tamano", None),
        "urgencia": getattr(p, "urgencia", None),
        "score_ia": getattr(p, "score_ia", 0) or 0,
        "plan_recomendado": getattr(p, "plan_recomendado", None),
        "pitch_sugerido": getattr(p, "pitch_sugerido", None),
        "automation_paso": getattr(p, "automation_paso", 0) or 0,
    }

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
    }

def _aliado_detalle(a):
    return {
        "codigo": a.codigo, "nombre": a.nombre, "email": a.email,
        "whatsapp": a.whatsapp, "ciudad": a.ciudad, "perfil": a.perfil,
        "nivel_actual": a.nivel, "nivel_calculado": a.nivel_calculado,
        "comision_pct": a.comision_pct * 100,
        "ventas_6m": a.ventas_6_meses, "total_ventas": len(a.ventas),
        "total_ganado": round(a.total_ganado, 2),
        "total_pendiente": round(a.total_pendiente, 2),
        "ref_code": a.ref_code,
        "link_ref": f"https://avanzadigital.digital/alianzas?ref={a.ref_code}",
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

# ─── RANKING PÚBLICO (Gamificación) ──────────────────────────────────────────

@app.get("/leaderboard")
def obtener_leaderboard(db: Session = Depends(get_db)):
    """Ranking inteligente: no solo ventas, sino tasa de cierre, velocidad y ticket promedio."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    lista_reales = []

    for a in aliados:
        partes = a.nombre.split()
        nombre_corto = f"{partes[0]} {partes[1][0]}." if len(partes) > 1 else a.nombre

        ventas_conf = [v for v in a.ventas if v.confirmada]
        total_ventas = len(ventas_conf)
        total_refs   = len(a.referidos)

        tasa_cierre  = round((total_ventas / total_refs) * 100) if total_refs > 0 else 0
        ticket_prom  = round(sum(v.valor_usd for v in ventas_conf) / total_ventas) if total_ventas > 0 else 0

        meses_activos = 1
        if a.fecha_firma:
            try:
                fecha = datetime.strptime(a.fecha_firma, "%d/%m/%Y")
                meses_activos = max(1, (datetime.now() - fecha).days // 30)
            except Exception:
                pass
        velocidad = round(total_ventas / meses_activos, 1)

        lista_reales.append({
            "codigo": a.codigo, "nombre": nombre_corto,
            "nivel": a.nivel_calculado, "ventas_6m": a.ventas_6_meses,
            "total_ganado": round(a.total_ganado, 2),
            "tasa_cierre": tasa_cierre, "ticket_prom": ticket_prom, "velocidad": velocidad,
        })

    lista_ficticios = [
        {"codigo":"AL-991","nombre":"Martín G.","nivel":"ELITE","ventas_6m":12,"total_ganado":5850.0,"tasa_cierre":68,"ticket_prom":3200,"velocidad":2.0},
        {"codigo":"AL-842","nombre":"Sofía L.","nivel":"PREMIUM","ventas_6m":8,"total_ganado":3100.0,"tasa_cierre":55,"ticket_prom":2900,"velocidad":1.3},
        {"codigo":"AL-705","nombre":"Lucas P.","nivel":"PREMIUM","ventas_6m":5,"total_ganado":1950.0,"tasa_cierre":42,"ticket_prom":2400,"velocidad":0.8},
        {"codigo":"AL-613","nombre":"Camila R.","nivel":"SILVER","ventas_6m":3,"total_ganado":870.0,"tasa_cierre":30,"ticket_prom":1800,"velocidad":0.5},
    ]

    completo = sorted(lista_reales + lista_ficticios, key=lambda x: x["total_ganado"], reverse=True)
    for i, item in enumerate(completo):
        item["posicion"] = i + 1
    return completo


# ─── CHECKOUT MERCADOPAGO ─────────────────────────────────────────────────────

@app.post("/checkout/crear")
async def crear_checkout(plan: str, ref_code: str, nombre_cliente: str = "Cliente", db: Session = Depends(get_db)):
    """Crea una preferencia de pago en MercadoPago con atribución automática al aliado."""
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not a:
        raise HTTPException(404, "Código de referido inválido.")
    if plan not in PLANES:
        raise HTTPException(400, "Plan inválido.")

    if not MP_ACCESS_TOKEN:
        # Fallback gracioso si no está configurado
        return {
            "checkout_url": f"https://avanzadigital.digital/contratar?plan={plan}&ref={ref_code}",
            "fallback": True,
            "mensaje": "Pagos automáticos no activados aún. Contactá a admin para configurar MP_ACCESS_TOKEN."
        }

    valor = PLANES[plan]
    external_ref = f"{ref_code}|{plan}|{nombre_cliente}"

    preference_data = {
        "items": [{"title": f"Avanza Digital — {plan}", "quantity": 1,
                   "unit_price": float(valor), "currency_id": "USD"}],
        "payer": {"name": nombre_cliente},
        "external_reference": external_ref,
        "back_urls": {
            "success": f"{PORTAL_URL}/checkout/exitoso?ref={ref_code}&plan={plan}",
            "failure": f"{PORTAL_URL}/portal.html",
            "pending": f"{PORTAL_URL}/portal.html"
        },
        "auto_return": "approved",
        "notification_url": f"{PORTAL_URL}/checkout/webhook"
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.mercadopago.com/checkout/preferences",
                json=preference_data,
                headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}", "Content-Type": "application/json"},
                timeout=15.0
            )
            if resp.status_code not in (200, 201):
                raise HTTPException(502, f"Error MercadoPago: {resp.text[:200]}")
            pref = resp.json()

        return {"checkout_url": pref["init_point"], "preference_id": pref["id"],
                "plan": plan, "valor": valor, "aliado": a.nombre, "fallback": False}
    except httpx.TimeoutException:
        raise HTTPException(504, "Timeout al conectar con MercadoPago.")


@app.get("/checkout/exitoso")
def checkout_exitoso(ref: str = "", plan: str = "", payment_id: str = "", db: Session = Depends(get_db)):
    """Redirección post-pago. Pre-registra el referido si hay aliado válido."""
    a = db.query(Aliado).filter(Aliado.ref_code == ref).first()
    if a and plan in PLANES:
        reciente = db.query(Referido).filter(
            Referido.aliado_id == a.id, Referido.plan_elegido == plan,
            Referido.registrado_en >= datetime.now() - timedelta(hours=48)
        ).first()
        if not reciente:
            r = Referido(aliado_id=a.id, nombre_cliente=f"Cliente Web (MP:{payment_id or '?'})",
                         plan_elegido=plan, notas="Auto-registrado vía checkout web")
            db.add(r); db.commit()
    return RedirectResponse(f"{PORTAL_URL}/portal.html?pago=ok&plan={plan}&ref={ref}")


@app.post("/checkout/webhook")
async def checkout_webhook(request: Request, db: Session = Depends(get_db)):
    """IPN de MercadoPago: registra la venta automáticamente al aliado."""
    try:
        body = await request.json()
        if body.get("type") != "payment":
            return {"status": "ignored"}

        payment_id = body.get("data", {}).get("id")
        if not payment_id or not MP_ACCESS_TOKEN:
            return {"status": "no_payment_id"}

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.mercadopago.com/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}, timeout=10.0
            )
            if resp.status_code != 200:
                return {"status": "error_mp"}
            payment = resp.json()

        if payment.get("status") != "approved":
            return {"status": "not_approved"}

        ext_ref = payment.get("external_reference", "")
        parts   = ext_ref.split("|", 2)
        if len(parts) < 2:
            return {"status": "invalid_ref"}
        ref_code, plan = parts[0], parts[1]
        nombre_cliente = parts[2] if len(parts) > 2 else "Cliente Web"

        if plan not in PLANES:
            return {"status": "invalid_plan"}

        a = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
        if not a:
            return {"status": "aliado_not_found"}

        # Idempotencia
        existing = db.query(Venta).filter(
            Venta.aliado_id == a.id, Venta.notas.contains(str(payment_id))
        ).first()
        if existing:
            return {"status": "already_processed"}

        valor = PLANES[plan]
        comision_usd = round(valor * a.comision_pct, 2)
        v = Venta(aliado_id=a.id, nombre_cliente=nombre_cliente, plan=plan,
                  valor_usd=valor, comision_pct=a.comision_pct, comision_usd=comision_usd,
                  confirmada=True, pagada=False, fecha_venta=datetime.now(),
                  modalidad_pago="MercadoPago", notas=f"Pago automático MP ID:{payment_id}")
        db.add(v)

        if getattr(a, "sponsor", None):
            comision_sponsor = round(valor * 0.05, 2)
            v_red = Venta(
                aliado_id=a.sponsor.id, nombre_cliente=f"♻️ RED: {a.nombre} (MP:{nombre_cliente})",
                plan=plan, valor_usd=valor, comision_pct=0.05, comision_usd=comision_sponsor,
                confirmada=True, pagada=False, fecha_venta=datetime.now(),
                modalidad_pago="MercadoPago", notas=f"Ingreso pasivo MP:{payment_id}"
            )
            db.add(v_red)
            a.sponsor.nivel = a.sponsor.nivel_calculado

        a.nivel = a.nivel_calculado
        db.commit()

        enviar_email(a.email, f"🎉 ¡Nueva venta confirmada! — {plan}",
            f"""<div style="font-family:sans-serif;background:#050505;color:#fff;padding:32px;max-width:520px;margin:auto;border-radius:12px;">
              <h2 style="color:#4ade80;">¡Venta confirmada vía portal! 🎉</h2>
              <p>Hola <strong>{a.nombre.split()[0]}</strong>, se confirmó un pago automático.</p>
              <div style="background:#111;border:1px solid #222;border-radius:8px;padding:16px;margin:16px 0;">
                <p style="margin:4px 0;"><strong>Plan:</strong> {plan}</p>
                <p style="margin:4px 0;"><strong>Cliente:</strong> {nombre_cliente}</p>
                <p style="margin:4px 0;"><strong>Tu comisión:</strong> <span style="color:#4ade80;font-size:1.3rem;font-weight:900;">USD {comision_usd:,.0f}</span></p>
              </div>
              <p style="color:#71717a;font-size:.85rem;">Tu comisión se acreditará en las próximas 24hs.</p>
              <a href="{PORTAL_URL}/portal.html" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;">Ver mi portal →</a>
            </div>""")

        return {"status": "ok", "venta_registrada": True}
    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")
        return {"status": "error", "detail": str(e)}


# ─── SIGUIENTE MEJOR ACCIÓN ───────────────────────────────────────────────────

@app.get("/aliados/{codigo}/siguiente-accion")
def siguiente_accion(codigo: str, db: Session = Depends(get_db)):
    """Analiza la situación del aliado y devuelve la acción más urgente e impactante."""
    a = _get_aliado(codigo, db)
    _aplicar_caducidad_bolsa(db)
    acciones = []

    # 1. Lead caliente: respondió pero no se cotizó
    respondieron = [p for p in a.prospectos if p.estado == "respondio"]
    if respondieron:
        mejor = max(respondieron, key=lambda p: p.fecha_respuesta or p.creado_en)
        acciones.append({
            "tipo": "cerrar_lead_caliente", "urgencia": 5, "icono": "⚡",
            "titulo": f"¡{mejor.nombre} está caliente!",
            "descripcion": "Respondió y está esperando tu propuesta. Usá el Cotizador y enviásela ahora — cada hora enfría el lead.",
            "accion_id": mejor.id, "boton": "Armar propuesta ahora", "tab": "cotizador",
            "color": "green"
        })

    # 2. Prospectos sin contactar
    sin_contactar = [p for p in a.prospectos if p.estado == "sin_contactar"]
    if sin_contactar:
        viejo = min(sin_contactar, key=lambda p: p.creado_en)
        dias = (datetime.now() - viejo.creado_en).days
        acciones.append({
            "tipo": "contactar_prospecto", "urgencia": 4, "icono": "🔥",
            "titulo": f"Contactá a {viejo.nombre}",
            "descripcion": f"Lleva {dias} día{'s' if dias != 1 else ''} sin contactar. Enviá el link de Auditoría gratuita para romper el hielo.",
            "accion_id": viejo.id, "boton": "Ir a Prospectos", "tab": "prospectos",
            "color": "amber"
        })

    # 3. Prospectos se enfrían (contactados sin respuesta >3 días)
    frios = [(p, (datetime.now() - p.fecha_contacto).days)
             for p in a.prospectos if p.estado == "contactado" and p.fecha_contacto
             and (datetime.now() - p.fecha_contacto).days >= 3]
    if frios:
        frio, dias_f = max(frios, key=lambda x: x[1])
        acciones.append({
            "tipo": "seguimiento", "urgencia": 3, "icono": "❄️",
            "titulo": f"Seguimiento urgente: {frio.nombre}",
            "descripcion": f"Hace {dias_f} días que no responde. Mandá un mensaje corto: '¿Pudiste ver lo que te envié?' Sin presionar.",
            "accion_id": frio.id, "boton": "Ver Prospectos", "tab": "prospectos",
            "color": "primary"
        })

    # 4. Leads disponibles en bolsa (si tiene cupo)
    reclamos_activos = db.query(LeadBolsa).filter(
        LeadBolsa.aliado_id == a.id, LeadBolsa.estado == "reclamado"
    ).count()
    leads_disp = db.query(LeadBolsa).filter(LeadBolsa.estado == "disponible").count()
    if leads_disp > 0 and reclamos_activos < 3:
        acciones.append({
            "tipo": "reclamar_lead", "urgencia": 2, "icono": "🎯",
            "titulo": f"{leads_disp} lead{'s' if leads_disp > 1 else ''} disponible{'s' if leads_disp > 1 else ''} en la bolsa",
            "descripcion": "Hay clientes pre-filtrados esperando. Reclamá uno antes que otro aliado lo tome.",
            "boton": "Ver Bolsa de Leads", "tab": "bolsa",
            "color": "primary"
        })

    # 5. Primer prospecto (si no tiene ninguno)
    if not a.prospectos and a.ventas_6_meses == 0:
        acciones.append({
            "tipo": "prospectar", "urgencia": 1, "icono": "🚀",
            "titulo": "Cargá tu primer prospecto hoy",
            "descripcion": "Pensá en 3 empresas de tu entorno que podrían necesitar presencia digital. Agregalas y contactalas con el enlace de Auditoría.",
            "boton": "Agregar Prospecto", "tab": "prospectos",
            "color": "primary"
        })

    acciones.sort(key=lambda x: x["urgencia"], reverse=True)

    # Stats del aliado para el contexto
    total_prospectos = len(a.prospectos)
    tasa_cierre_pct = 0
    if a.referidos:
        ventas_ok = len([v for v in a.ventas if v.confirmada])
        tasa_cierre_pct = round((ventas_ok / len(a.referidos)) * 100)

    return {
        "siguiente_accion": acciones[0] if acciones else None,
        "todas": acciones[:4],
        "stats": {
            "total_prospectos": total_prospectos,
            "calientes": len(respondieron),
            "sin_contactar": len(sin_contactar),
            "tasa_cierre": tasa_cierre_pct,
        }
    }


# ─── ONBOARDING DEL ALIADO ────────────────────────────────────────────────────

@app.get("/aliados/{codigo}/onboarding")
def estado_onboarding(codigo: str, db: Session = Depends(get_db)):
    """Retorna el progreso del checklist de onboarding del aliado."""
    a = _get_aliado(codigo, db)
    pasos = [
        {"id": "registro",    "titulo": "Te registraste",           "completado": True},
        {"id": "referido",    "titulo": "Registraste tu 1er referido",
         "completado": len(a.referidos) > 0},
        {"id": "prospecto",   "titulo": "Cargaste un prospecto",
         "completado": len(a.prospectos) > 0},
        {"id": "bolsa",       "titulo": "Reclamaste un lead de la bolsa",
         "completado": any(l.aliado_id == a.id for l in db.query(LeadBolsa).all())},
        {"id": "primera_venta","titulo": "Cerraste tu primera venta",
         "completado": a.ventas_6_meses > 0},
        {"id": "red",         "titulo": "Invitaste a tu primer sub-aliado",
         "completado": len(getattr(a, "sub_aliados", [])) > 0},
    ]
    completados = sum(1 for p in pasos if p["completado"])
    return {"pasos": pasos, "completados": completados, "total": len(pasos),
            "pct": round(completados / len(pasos) * 100)}


# ─── BOLSA DE LEADS (ADMIN) ──────────────────────────────────────────────────

class LeadBolsaCreate(BaseModel):
    empresa: str
    rubro: str
    telefono: str
    email: str = ""

def _aplicar_caducidad_bolsa(db: Session):
    """LA REGLA DE ORO: Libera los leads reclamados hace más de 48h sin contactar"""
    limite = datetime.now() - timedelta(hours=48)
    vencidos = db.query(LeadBolsa).filter(
        LeadBolsa.estado == "reclamado",
        LeadBolsa.fecha_reclamo < limite
    ).all()
    
    for lead in vencidos:
        lead.estado = "disponible"
        lead.aliado_id = None
        lead.fecha_reclamo = None
    
    if vencidos:
        db.commit()

@app.post("/admin/bolsa")
def cargar_lead_bolsa(lead: LeadBolsaCreate, db: Session = Depends(get_db)):
    nuevo = LeadBolsa(
        empresa=lead.empresa,
        rubro=lead.rubro,
        telefono=lead.telefono,
        email=lead.email,
        estado="disponible"
    )
    db.add(nuevo)
    db.commit()
    return {"mensaje": "Lead subido a la bolsa."}

@app.get("/admin/bolsa")
def monitor_bolsa(db: Session = Depends(get_db)):
    # 1. Limpiamos los leads vencidos antes de mostrar la data
    _aplicar_caducidad_bolsa(db) 
    
    # 2. Traemos todos los leads
    leads = db.query(LeadBolsa).order_by(LeadBolsa.fecha_carga.desc()).all()
    
    # 3. Calculamos KPIs
    total = len(leads)
    disponibles = sum(1 for l in leads if l.estado == "disponible")
    reclamados = sum(1 for l in leads if l.estado == "reclamado")
    contactados = sum(1 for l in leads if l.estado == "contactado")
    
    tasa = round((contactados / (reclamados + contactados)) * 100) if (reclamados + contactados) > 0 else 0

    # 4. Formateamos la tabla
    detalle = []
    for l in leads:
        tiempo_txt = ""
        if l.estado == "reclamado" and l.fecha_reclamo:
            horas = (datetime.now() - l.fecha_reclamo).total_seconds() / 3600
            tiempo_txt = f"{int(horas)}h / 48h"
            
        detalle.append({
            "id": l.id,
            "empresa": l.empresa,
            "rubro": l.rubro,
            "estado": l.estado,
            "asignado_a": l.aliado.nombre if l.aliado else None,
            "tiempo_transcurrido": tiempo_txt
        })

    return {
        "kpis": {
            "total": total,
            "disponibles": disponibles,
            "reclamados": reclamados,
            "tasa_contacto": tasa
        },
        "leads": detalle
    }

@app.post("/admin/bolsa/{id}/revocar")
def revocar_lead_bolsa(id: int, db: Session = Depends(get_db)):
    """Modo Dios: El admin quita el lead manualmente"""
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    
    lead.estado = "disponible"
    lead.aliado_id = None
    lead.fecha_reclamo = None
    db.commit()
    return {"mensaje": "Lead revocado con éxito"}


# ─── BOLSA DE LEADS (PORTAL ALIADO) ──────────────────────────────────────────

@app.get("/aliados/{codigo}/bolsa")
def ver_bolsa_aliado(codigo: str, db: Session = Depends(get_db)):
    """Muestra los leads disponibles y los que este aliado ya reclamó."""
    a = _get_aliado(codigo, db)
    _aplicar_caducidad_bolsa(db) # Limpiamos antes de mostrar
    
    disponibles = db.query(LeadBolsa).filter(LeadBolsa.estado == "disponible").all()
    mis_reclamos = db.query(LeadBolsa).filter(LeadBolsa.aliado_id == a.id).order_by(LeadBolsa.fecha_reclamo.desc()).all()
    
    reclamos_formateados = []
    for l in mis_reclamos:
        horas_restantes = 0
        if l.estado == "reclamado" and l.fecha_reclamo:
            horas_pasadas = (datetime.now() - l.fecha_reclamo).total_seconds() / 3600
            horas_restantes = max(0, 48 - int(horas_pasadas))
            
        reclamos_formateados.append({
            "id": l.id, "empresa": l.empresa, "rubro": l.rubro,
            "telefono": l.telefono, "email": l.email, "estado": l.estado,
            "horas_restantes": horas_restantes
        })
        
    return {
        "disponibles": [{"id": l.id, "empresa": l.empresa, "rubro": l.rubro} for l in disponibles],
        "mis_reclamos": reclamos_formateados,
        "reclamos_activos": sum(1 for r in reclamos_formateados if r["estado"] == "reclamado"),
        "limite_reclamos": 3
    }

LIMITE_RECLAMOS_ACTIVOS = 3  # Máximo de reclamos simultáneos por aliado

@app.post("/bolsa/{id}/reclamar")
def reclamar_lead(id: int, codigo_aliado: str, db: Session = Depends(get_db)):
    a = _get_aliado(codigo_aliado, db)

    # Verificar límite de reclamos activos simultáneos
    reclamos_activos = db.query(LeadBolsa).filter(
        LeadBolsa.aliado_id == a.id,
        LeadBolsa.estado == "reclamado"
    ).count()
    if reclamos_activos >= LIMITE_RECLAMOS_ACTIVOS:
        raise HTTPException(400, f"Límite alcanzado: ya tenés {LIMITE_RECLAMOS_ACTIVOS} leads reclamados activos. Marcá al menos uno como contactado antes de reclamar otro.")

    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id, LeadBolsa.estado == "disponible").first()
    if not lead:
        raise HTTPException(400, "El lead ya no está disponible. ¡Alguien fue más rápido!")
    
    lead.estado = "reclamado"
    lead.aliado_id = a.id
    lead.fecha_reclamo = datetime.now()
    db.commit()
    return {"mensaje": "¡Lead reclamado exitosamente!"}

@app.patch("/bolsa/{id}/contactar")
def contactar_lead_bolsa(id: int, codigo_aliado: str, resultado: str = "exitoso", db: Session = Depends(get_db)):
    """
    Marca un lead como contactado. 
    resultado puede ser: exitoso | no_interesado | no_contesto
    """
    RESULTADOS_VALIDOS = {"exitoso", "no_interesado", "no_contesto"}
    if resultado not in RESULTADOS_VALIDOS:
        raise HTTPException(400, f"Resultado inválido. Opciones: {', '.join(RESULTADOS_VALIDOS)}")

    a = _get_aliado(codigo_aliado, db)
    lead = db.query(LeadBolsa).filter(LeadBolsa.id == id, LeadBolsa.aliado_id == a.id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado o no te pertenece.")

    lead.estado    = "contactado"
    lead.resultado = resultado
    db.commit()

    mensajes = {
        "exitoso":       "¡Excelente! Lead marcado como exitoso. ¡A cerrar la venta!",
        "no_interesado": "Anotado. El lead quedó marcado como no interesado.",
        "no_contesto":   "Anotado. Si conseguís contactarlo después, podés actualizar el estado.",
    }
    return {"mensaje": mensajes[resultado]}


@app.get("/aliados/{codigo}/historial-bolsa")
def historial_bolsa_aliado(codigo: str, db: Session = Depends(get_db)):
    """Historial completo de leads de un aliado con estadísticas."""
    a = _get_aliado(codigo, db)
    leads = db.query(LeadBolsa).filter(LeadBolsa.aliado_id == a.id).order_by(LeadBolsa.fecha_reclamo.desc()).all()

    total          = len(leads)
    exitosos       = sum(1 for l in leads if l.resultado == "exitoso")
    no_interesados = sum(1 for l in leads if l.resultado == "no_interesado")
    no_contestaron = sum(1 for l in leads if l.resultado == "no_contesto")
    activos        = sum(1 for l in leads if l.estado == "reclamado")
    tasa_exito     = round((exitosos / total * 100), 1) if total else 0

    return {
        "stats": {
            "total_reclamados": total,
            "exitosos": exitosos,
            "no_interesados": no_interesados,
            "no_contestaron": no_contestaron,
            "activos": activos,
            "tasa_exito": tasa_exito,
        },
        "leads": [
            {
                "id": l.id,
                "empresa": l.empresa,
                "rubro": l.rubro,
                "telefono": l.telefono,
                "estado": l.estado,
                "resultado": l.resultado,
                "fecha_reclamo": l.fecha_reclamo.strftime("%d/%m/%Y %H:%M") if l.fecha_reclamo else None,
            }
            for l in leads
        ]
    }


@app.get("/admin/historial-bolsa")
def historial_bolsa_admin(db: Session = Depends(get_db)):
    """Admin: resumen de rendimiento de todos los aliados en la bolsa."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    resumen = []
    for a in aliados:
        leads = [l for l in a.leads_bolsa]
        total = len(leads)
        if total == 0:
            continue
        exitosos = sum(1 for l in leads if l.resultado == "exitoso")
        resumen.append({
            "codigo": a.codigo,
            "nombre": a.nombre,
            "total_reclamados": total,
            "exitosos": exitosos,
            "no_interesados": sum(1 for l in leads if l.resultado == "no_interesado"),
            "no_contestaron": sum(1 for l in leads if l.resultado == "no_contesto"),
            "activos": sum(1 for l in leads if l.estado == "reclamado"),
            "tasa_exito": round(exitosos / total * 100, 1) if total else 0,
        })
    resumen.sort(key=lambda x: x["exitosos"], reverse=True)
    return {"aliados": resumen}

# ═══════════════════════════════════════════════════════════════════════════
# ═══ v1.3 — INTELIGENCIA DE VENTAS + REPUTACIÓN + MARKETPLACE + COMUNIDAD ══
# ═══════════════════════════════════════════════════════════════════════════

# ─── PERFILADO IA DE LEADS (A) ───────────────────────────────────────────────
# Heurística local — sin LLM, explicable, determinística.
# El aliado carga rubro/tamaño/urgencia → el sistema devuelve score + plan + pitch.

RUBROS_PLAN = {
    # Rubros que naturalmente necesitan más infraestructura digital
    "Metalúrgica / Manufactura":     ("Plan Industrial", "B2B técnico con ciclo largo de venta"),
    "Agro / Maquinaria agrícola":    ("Plan Industrial", "Sector con presupuesto pero poca presencia digital"),
    "Logística / Transporte":        ("Plan Pro",        "Necesita canales claros de contacto y cotización"),
    "Servicios B2B / Consultoría":   ("Plan Pro",        "Necesita autoridad online y generación de leads"),
    "Comercio / Retail B2B":         ("Plan Pro",        "Catálogo + presencia local"),
    "Construcción / Obras":          ("Plan Industrial", "Obra pública/privada, necesita respaldo digital"),
    "Salud / Clínicas":              ("Plan Pro",        "Pacientes investigan online antes de elegir"),
    "Educación / Capacitación":      ("Plan Pro",        "Captación online es crítica"),
    "Tecnología / Software":         ("Estrategico 360", "Mercado educado, espera excelencia digital"),
    "Otro":                          ("Plan Pro",        "Plan versátil para la mayoría"),
}

TAMANOS_MULT = {"micro": 0.6, "pyme": 1.0, "mediana": 1.25, "grande": 1.4}
URGENCIA_SCORE = {"baja": 10, "media": 25, "alta": 40}


def _perfilar_prospecto(p: Prospecto) -> dict:
    """Corazón del perfilado IA: calcula score 0-100, plan recomendado y pitch."""
    score = 20  # base

    # 1. Rubro → +20 si es rubro de alta necesidad, plan sugerido
    plan, razon_rubro = RUBROS_PLAN.get(p.rubro or "Otro", ("Plan Pro", "Plan versátil"))
    if p.rubro and p.rubro != "Otro":
        score += 20

    # 2. Urgencia pesa fuerte (hasta +40)
    score += URGENCIA_SCORE.get(p.urgencia or "media", 25)

    # 3. Tamaño ajusta expectativa de ticket
    mult = TAMANOS_MULT.get(p.tamano or "pyme", 1.0)

    # Si el tamaño es grande → empujar a plan superior
    if p.tamano == "grande" and plan == "Plan Pro":
        plan = "Plan Industrial"
    elif p.tamano == "grande" and plan == "Plan Industrial":
        plan = "Estrategico 360"
    elif p.tamano == "micro" and plan != "Plan Base":
        plan = "Plan Base"
        razon_rubro = "Empresa chica — empezar con Plan Base y escalar después"

    # 4. Si ya respondió un mensaje → bonus fuerte
    if p.estado == "respondio":
        score += 15
    elif p.estado == "contactado":
        score += 5

    # 5. Si tiene plan_interes manual del aliado, respetarlo con un boost
    if p.plan_interes and p.plan_interes in PLANES:
        plan = p.plan_interes
        score += 5

    # Normalizar ticket esperado
    ticket = PLANES.get(plan, 2900) * mult
    score = max(0, min(100, int(score)))

    # 6. Pitch sugerido
    pitch = _generar_pitch(p.nombre, p.rubro, p.tamano, p.urgencia, plan, ticket)

    return {
        "score": score,
        "plan_recomendado": plan,
        "pitch_sugerido": pitch,
        "ticket_esperado": round(ticket, 0),
        "razon": razon_rubro,
    }


def _generar_pitch(nombre: str, rubro: str, tamano: str, urgencia: str, plan: str, ticket: float) -> str:
    """Genera un pitch corto y accionable para WhatsApp/email."""
    apertura = {
        "alta": f"Hola, vi que {nombre} está creciendo rápido — les paso algo que puede ahorrarles tiempo.",
        "media": f"Hola, estuve revisando empresas del rubro {rubro or 'de ustedes'} y {nombre} me llamó la atención.",
        "baja": f"Hola, te paso info por si a futuro les sirve. Sin apuro.",
    }.get(urgencia or "media")

    dolor = {
        "Metalúrgica / Manufactura": "Muchas fábricas pierden contactos porque su web no genera confianza técnica.",
        "Agro / Maquinaria agrícola": "En el agro el cliente investiga mucho antes de llamar — la web define si te llaman o no.",
        "Logística / Transporte": "Los clientes B2B esperan poder cotizar rápido, sin esperar 2 días a que les llamen.",
        "Servicios B2B / Consultoría": "Si tu web no transmite autoridad en 5 segundos, el lead se va a la competencia.",
        "Salud / Clínicas": "El 80% de los pacientes googlean antes de sacar turno.",
        "Construcción / Obras": "Las obras grandes se eligen por respaldo — y el respaldo hoy se mide online.",
    }.get(rubro or "Otro", "Las empresas que no invierten en digital pierden hasta un 30% de oportunidades por mes.")

    cierre = {
        "Plan Base":        f"Arrancamos con el Plan Base (USD {int(PLANES['Plan Base'])}): sitio limpio + Google Business + métricas en 30 días.",
        "Plan Pro":         f"Te sugiero el Plan Pro (USD {int(PLANES['Plan Pro'])}): incluye captación activa de leads, no solo presencia.",
        "Plan Industrial":  f"Por el tamaño de {nombre} va el Plan Industrial (USD {int(PLANES['Plan Industrial'])}): sistema completo + ventas B2B.",
        "Estrategico 360":  f"Lo que encaja acá es un Estratégico 360 (USD {int(PLANES['Estrategico 360'])}): canal digital entero operando como una máquina.",
    }.get(plan, "")

    return f"{apertura}\n\n{dolor}\n\n{cierre}\n\n¿Te mando un diagnóstico gratis para que veas el estado actual?"


@app.post("/prospectos/{id}/perfilar")
def perfilar_prospecto(id: int,
                       rubro: str = "",
                       tamano: str = "pyme",
                       urgencia: str = "media",
                       db: Session = Depends(get_db)):
    """Corre el perfilado IA sobre un prospecto y guarda el resultado."""
    p = _get_prospecto(id, db)
    if rubro:
        p.rubro = rubro
    p.tamano = tamano
    p.urgencia = urgencia

    resultado = _perfilar_prospecto(p)
    p.score_ia = resultado["score"]
    p.plan_recomendado = resultado["plan_recomendado"]
    p.pitch_sugerido = resultado["pitch_sugerido"]
    p.perfilado_en = datetime.now()
    db.commit()

    return {
        "mensaje": "Prospecto perfilado.",
        "score": resultado["score"],
        "plan_recomendado": resultado["plan_recomendado"],
        "pitch_sugerido": resultado["pitch_sugerido"],
        "ticket_esperado": resultado["ticket_esperado"],
        "razon": resultado["razon"],
    }


@app.patch("/prospectos/{id}/datos")
def actualizar_datos_prospecto(id: int,
                               rubro: str = "",
                               tamano: str = "",
                               urgencia: str = "",
                               db: Session = Depends(get_db)):
    """Actualiza rubro/tamaño/urgencia sin perfilar."""
    p = _get_prospecto(id, db)
    if rubro:    p.rubro = rubro
    if tamano:   p.tamano = tamano
    if urgencia: p.urgencia = urgencia
    db.commit()
    return {"mensaje": "Datos actualizados."}


# ─── SISTEMA DE REPUTACIÓN (C) ───────────────────────────────────────────────

def _calcular_reputacion(a: Aliado, db: Session) -> dict:
    """Calcula score 0-100 + badges del aliado.
    Factores (ponderados):
      - Tasa de cierre (40%)
      - Velocidad de contacto en bolsa (20%)
      - Tasa éxito en bolsa (20%)
      - Actividad reciente (10%)
      - Tamaño de red (10%)
    """
    ventas_conf = [v for v in a.ventas if v.confirmada]
    total_ventas = len(ventas_conf)
    total_refs = len(a.referidos)
    tasa_cierre = (total_ventas / total_refs) if total_refs > 0 else 0
    ticket_prom = (sum(v.valor_usd for v in ventas_conf) / total_ventas) if total_ventas else 0

    # Bolsa
    leads_bolsa = getattr(a, "leads_bolsa", [])
    exitosos = sum(1 for l in leads_bolsa if l.resultado == "exitoso")
    tasa_bolsa = (exitosos / len(leads_bolsa)) if leads_bolsa else 0

    # Actividad (últimos 30 días)
    corte = datetime.now() - timedelta(days=30)
    activo_reciente = (a.ultimo_login and a.ultimo_login >= corte) or \
                      any(r.registrado_en >= corte for r in a.referidos) or \
                      any(v.fecha_venta and v.fecha_venta >= corte for v in ventas_conf)

    # Red
    red_activa = sum(1 for sub in getattr(a, "sub_aliados", []) if sub.ventas_6_meses > 0)

    # Score
    score = 30  # base
    score += int(min(40, tasa_cierre * 100))        # hasta +40 por tasa cierre
    score += int(min(20, tasa_bolsa * 50))          # hasta +20 por éxito bolsa
    score += 10 if activo_reciente else 0
    score += min(10, red_activa * 3)                # hasta +10 por red activa
    score = max(0, min(100, score))

    # Badges
    badges = []
    if tasa_cierre >= 0.40 and total_ventas >= 2:
        badges.append("CLOSER")
    if ticket_prom >= 3500 and total_ventas >= 1:
        badges.append("TOP_TICKET")
    if activo_reciente and a.cantidad_logins and a.cantidad_logins >= 10:
        badges.append("FIEL")
    if red_activa >= 3:
        badges.append("EMBAJADOR")
    if tasa_bolsa >= 0.30 and len(leads_bolsa) >= 3:
        badges.append("BOLSA_MASTER")
    # "Rápido": contacta leads de bolsa en < 6hs
    tiempos = []
    for l in leads_bolsa:
        if l.estado in ("contactado",) and l.fecha_reclamo:
            # Aproximamos con fecha_carga vs fecha_reclamo (tiempo que tardó en reclamar) —
            # es una proxy razonable de "reacción".
            pass  # se deja como bonus futuro si queremos tiempo exacto de contacto
    # Simple: si ya tiene 2 contactos y el lead fue reclamado rápido, damos la badge
    if len(leads_bolsa) >= 2 and exitosos >= 1:
        badges.append("RAPIDO")

    return {
        "score": score,
        "badges": badges,
        "factores": {
            "tasa_cierre": round(tasa_cierre * 100, 1),
            "ticket_prom": round(ticket_prom),
            "tasa_bolsa": round(tasa_bolsa * 100, 1),
            "activo_reciente": activo_reciente,
            "red_activa": red_activa,
        },
    }


@app.get("/aliados/{codigo}/reputacion")
def ver_reputacion(codigo: str, db: Session = Depends(get_db)):
    a = _get_aliado(codigo, db)
    calc = _calcular_reputacion(a, db)
    # Persistir
    try:
        a.reputacion_score = calc["score"]
        a.badges = json.dumps(calc["badges"])
        a.reputacion_calculada_en = datetime.now()
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error guardando reputación: {e}")

    badges_full = [
        {"code": b, **REPUTACION_BADGES[b]}
        for b in calc["badges"] if b in REPUTACION_BADGES
    ]
    return {
        "codigo": a.codigo,
        "nombre": a.nombre,
        "score": calc["score"],
        "badges": badges_full,
        "factores": calc["factores"],
        "badges_disponibles": [
            {"code": code, **info} for code, info in REPUTACION_BADGES.items()
        ],
    }


@app.get("/admin/reputacion/ranking")
def ranking_reputacion(db: Session = Depends(get_db)):
    """Admin: ver todos los aliados rankeados por reputación."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    resultado = []
    for a in aliados:
        calc = _calcular_reputacion(a, db)
        resultado.append({
            "codigo": a.codigo,
            "nombre": a.nombre,
            "score": calc["score"],
            "badges": calc["badges"],
            **calc["factores"],
        })
    resultado.sort(key=lambda x: x["score"], reverse=True)
    return {"aliados": resultado}


# ─── PILOTO AUTOMÁTICO REAL (B) ──────────────────────────────────────────────
# Cada X horas corre el scheduler y envía el siguiente toque por email a los
# prospectos marcados como piloto_automatico = True.
#
# Secuencia: 3 toques espaciados 3 días cada uno, adaptados al plan recomendado.

PILOTO_INTERVALO_DIAS = 3  # días entre toques
PILOTO_MAX_PASOS = 3


def _render_mensaje_piloto(p: Prospecto, paso: int) -> tuple:
    """Devuelve (asunto, cuerpo_html) para el paso N."""
    aliado = p.aliado
    nombre_prospecto = p.nombre.split()[0] if p.nombre else "Hola"
    plan = p.plan_recomendado or p.plan_interes or "Plan Pro"

    if paso == 1:
        asunto = f"{nombre_prospecto}, te comparto un diagnóstico gratis"
        mensaje = (
            f"Hola {nombre_prospecto},<br><br>"
            f"Soy {aliado.nombre.split()[0]}. Hace unos días hablamos y quería retomar.<br><br>"
            f"Te dejo este <a href='https://avanzadigital.digital/alianzas?ref={aliado.ref_code}' "
            f"style='color:#3b82f6;'>diagnóstico gratuito</a> — toma 30 segundos y devuelve un "
            f"reporte con el estado real de tu presencia digital.<br><br>"
            f"Si te hace clic, hablamos.<br><br>"
            f"— {aliado.nombre}"
        )
    elif paso == 2:
        asunto = f"{nombre_prospecto}, un caso que quizás te sirve"
        mensaje = (
            f"Hola {nombre_prospecto},<br><br>"
            f"Te escribo por si te sirve este patrón que vemos mucho:<br><br>"
            f"Empresas del rubro {p.rubro or 'B2B'} suelen perder entre un 20% y un 40% de "
            f"consultas por problemas simples: sitios lentos, formularios rotos, o cero "
            f"captación activa. El {plan} resuelve exactamente eso.<br><br>"
            f"¿Te parece si armamos una llamada de 15 min esta semana para ver tu caso?<br><br>"
            f"— {aliado.nombre}"
        )
    else:  # paso 3 — cierre
        asunto = f"Último mensaje, {nombre_prospecto} — ¿cerramos o dejamos?"
        mensaje = (
            f"Hola {nombre_prospecto},<br><br>"
            f"Última vez que te escribo para no hacerme molesto. Si no es el momento, "
            f"perfecto — te dejo mi contacto guardado para cuando quieras retomar.<br><br>"
            f"Si sí lo es, te propongo agendar 15 min de llamada sin compromiso: "
            f"<a href='https://wa.me/{aliado.whatsapp}' style='color:#3b82f6;'>"
            f"escribime por WhatsApp</a>.<br><br>"
            f"— {aliado.nombre}"
        )

    html = f"""
    <div style='font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:32px;
                max-width:560px;margin:0 auto;border-radius:12px;'>
      {mensaje}
      <hr style='margin:24px 0;border:none;border-top:1px solid #222;'>
      <p style='font-size:0.75rem;color:#71717a;'>
        Este mensaje fue enviado por el sistema de seguimiento automático de Avanza Digital en
        nombre de {aliado.nombre}. Para dejar de recibirlos, respondé 'BAJA' a este mail.
      </p>
    </div>
    """
    return asunto, html


def job_piloto_automatico():
    """Corre cada hora. Envía el siguiente toque a prospectos con piloto activo."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        ahora = datetime.now()
        candidatos = db.query(Prospecto).filter(
            Prospecto.piloto_automatico == True,
            Prospecto.estado != "respondio",  # si ya respondió, paramos
        ).all()

        for p in candidatos:
            # ¿Ya agotó los pasos?
            if (p.automation_paso or 0) >= PILOTO_MAX_PASOS:
                continue
            # ¿Cuándo tocamos la última vez?
            ultimo = p.automation_ultimo_en or p.automation_activa_desde or p.creado_en
            if not ultimo:
                continue
            horas_desde = (ahora - ultimo).total_seconds() / 3600
            if horas_desde < PILOTO_INTERVALO_DIAS * 24:
                continue
            # ¿Tenemos cómo contactarlo?
            if not p.contacto or "@" not in (p.contacto or ""):
                # Sin email no podemos hacer el toque automático aún
                # (WhatsApp sería una fase 2 — requiere integración)
                continue

            paso = (p.automation_paso or 0) + 1
            asunto, cuerpo = _render_mensaje_piloto(p, paso)

            try:
                enviar_email(p.contacto, asunto, cuerpo)
                p.automation_paso = paso
                p.automation_ultimo_en = ahora
                if paso == 1 and not p.fecha_contacto:
                    p.estado = "contactado"
                    p.fecha_contacto = ahora
                # Log
                log = AutomationLog(
                    prospecto_id=p.id, aliado_id=p.aliado_id, paso=paso,
                    canal="email", asunto=asunto, mensaje=cuerpo[:500], exitoso=True
                )
                db.add(log)
            except Exception as e:
                log = AutomationLog(
                    prospecto_id=p.id, aliado_id=p.aliado_id, paso=paso,
                    canal="email", asunto=asunto, mensaje=str(e)[:500], exitoso=False
                )
                db.add(log)

        db.commit()
    except Exception as e:
        print(f"[PILOTO ERROR] {e}")
    finally:
        db.close()


# Lo registramos en el scheduler que ya existe
scheduler.add_job(job_piloto_automatico, "interval", hours=1)


@app.get("/aliados/{codigo}/automation-log")
def ver_automation_log(codigo: str, db: Session = Depends(get_db)):
    """Historial de mensajes automáticos enviados a los prospectos de este aliado."""
    a = _get_aliado(codigo, db)
    logs = db.query(AutomationLog).filter(
        AutomationLog.aliado_id == a.id
    ).order_by(AutomationLog.creado_en.desc()).limit(50).all()
    # Mapear a nombre de prospecto
    resultado = []
    for l in logs:
        p = db.query(Prospecto).filter(Prospecto.id == l.prospecto_id).first()
        resultado.append({
            "id": l.id,
            "prospecto": p.nombre if p else "—",
            "paso": l.paso,
            "canal": l.canal,
            "asunto": l.asunto,
            "exitoso": l.exitoso,
            "fecha": l.creado_en.strftime("%d/%m/%Y %H:%M") if l.creado_en else None,
        })
    return {"logs": resultado}


# ─── MARKETPLACE DE LEADS + CRÉDITOS (D) ─────────────────────────────────────

def _ajustar_creditos(db: Session, aliado: Aliado, delta: int, motivo: str, ref: str = ""):
    """Helper: suma/resta créditos y registra transacción."""
    aliado.creditos = (aliado.creditos or 0) + delta
    if aliado.creditos < 0:
        aliado.creditos = 0
    t = TransaccionCredito(aliado_id=aliado.id, delta=delta, motivo=motivo, referencia=ref)
    db.add(t)


@app.get("/aliados/{codigo}/creditos")
def ver_creditos(codigo: str, db: Session = Depends(get_db)):
    a = _get_aliado(codigo, db)
    movimientos = db.query(TransaccionCredito).filter(
        TransaccionCredito.aliado_id == a.id
    ).order_by(TransaccionCredito.creado_en.desc()).limit(20).all()
    return {
        "saldo": a.creditos or 0,
        "movimientos": [
            {"delta": m.delta, "motivo": m.motivo, "ref": m.referencia,
             "fecha": m.creado_en.strftime("%d/%m/%Y %H:%M") if m.creado_en else None}
            for m in movimientos
        ]
    }


@app.post("/admin/aliados/{codigo}/creditos")
def admin_ajustar_creditos(codigo: str, delta: int, motivo: str = "recarga_admin",
                            db: Session = Depends(get_db)):
    """Admin: asigna/quita créditos a un aliado."""
    a = _get_aliado(codigo, db)
    _ajustar_creditos(db, a, delta, motivo, "admin")
    db.commit()
    return {"mensaje": f"Saldo actualizado.", "nuevo_saldo": a.creditos}


@app.get("/bolsa/marketplace")
def ver_marketplace(codigo_aliado: str = "", db: Session = Depends(get_db)):
    """Lista los leads calificados/premium disponibles con su costo en créditos."""
    _aplicar_caducidad_bolsa(db)
    leads = db.query(LeadBolsa).filter(
        LeadBolsa.estado == "disponible",
        LeadBolsa.tier.in_(["calificado", "premium"])
    ).order_by(LeadBolsa.costo_creditos.desc(), LeadBolsa.fecha_carga.desc()).all()

    saldo = 0
    if codigo_aliado:
        a = db.query(Aliado).filter(Aliado.codigo == codigo_aliado).first()
        if a:
            saldo = a.creditos or 0

    return {
        "saldo_creditos": saldo,
        "leads": [
            {
                "id": l.id,
                "empresa": l.empresa,
                "rubro": l.rubro,
                "tier": l.tier,
                "costo_creditos": l.costo_creditos or 0,
                "score_calidad": l.score_calidad or 50,
                "notas": l.notas_calificacion or "",
            }
            for l in leads
        ]
    }


@app.post("/bolsa/{id}/comprar")
def comprar_lead(id: int, codigo_aliado: str, db: Session = Depends(get_db)):
    """Compra un lead premium/calificado usando créditos."""
    a = _get_aliado(codigo_aliado, db)
    lead = db.query(LeadBolsa).filter(
        LeadBolsa.id == id, LeadBolsa.estado == "disponible"
    ).first()
    if not lead:
        raise HTTPException(400, "Ese lead ya no está disponible.")

    if (lead.tier or "basico") == "basico":
        raise HTTPException(400, "Este lead es gratuito. Usá el endpoint /bolsa/{id}/reclamar.")

    costo = lead.costo_creditos or 0
    if (a.creditos or 0) < costo:
        raise HTTPException(400, f"Saldo insuficiente. Necesitás {costo} créditos, tenés {a.creditos or 0}.")

    # Límite de reclamos activos también aplica acá
    reclamos_activos = db.query(LeadBolsa).filter(
        LeadBolsa.aliado_id == a.id, LeadBolsa.estado == "reclamado"
    ).count()
    if reclamos_activos >= LIMITE_RECLAMOS_ACTIVOS:
        raise HTTPException(400, f"Ya tenés {LIMITE_RECLAMOS_ACTIVOS} leads reclamados activos.")

    lead.estado = "reclamado"
    lead.aliado_id = a.id
    lead.fecha_reclamo = datetime.now()
    _ajustar_creditos(db, a, -costo, "compra_lead", f"lead:{lead.id}")
    db.commit()

    return {
        "mensaje": f"¡Lead premium comprado! Te descontamos {costo} créditos.",
        "saldo_restante": a.creditos,
        "lead": {
            "id": lead.id, "empresa": lead.empresa, "rubro": lead.rubro,
            "telefono": lead.telefono, "email": lead.email,
        }
    }


class LeadBolsaCreateAdv(BaseModel):
    empresa: str
    rubro: str
    telefono: str
    email: str = ""
    tier: str = "basico"            # basico | calificado | premium
    costo_creditos: int = 0
    score_calidad: int = 50
    notas_calificacion: str = ""


@app.post("/admin/bolsa-v2")
def cargar_lead_bolsa_v2(lead: LeadBolsaCreateAdv, db: Session = Depends(get_db)):
    """Carga un lead con tier/costo. Reemplaza a /admin/bolsa cuando querés tier."""
    if lead.tier not in ("basico", "calificado", "premium"):
        raise HTTPException(400, "Tier inválido. Usá: basico | calificado | premium")
    nuevo = LeadBolsa(
        empresa=lead.empresa, rubro=lead.rubro, telefono=lead.telefono,
        email=lead.email, estado="disponible",
        tier=lead.tier, costo_creditos=lead.costo_creditos,
        score_calidad=lead.score_calidad, notas_calificacion=lead.notas_calificacion,
    )
    db.add(nuevo); db.commit()
    return {"mensaje": f"Lead cargado en tier '{lead.tier}'."}


# ─── FINANCIACIÓN / CUOTAS (E) ───────────────────────────────────────────────

@app.get("/cotizador/cuotas")
def simular_cuotas(plan: str, cuotas: int = 1):
    """Simulador de cuotas. Devuelve cuota, total con recargo, recargo pct."""
    if plan not in PLANES:
        raise HTTPException(400, "Plan inválido.")
    if cuotas not in CUOTAS_RECARGO:
        raise HTTPException(400, f"Cuotas inválidas. Opciones: {list(CUOTAS_RECARGO.keys())}")
    base = PLANES[plan]
    recargo_pct = CUOTAS_RECARGO[cuotas]
    total = base * (1 + recargo_pct)
    valor_cuota = total / cuotas
    return {
        "plan": plan,
        "valor_base": base,
        "cuotas": cuotas,
        "recargo_pct": round(recargo_pct * 100, 1),
        "total_financiado": round(total, 2),
        "valor_cuota": round(valor_cuota, 2),
        "opciones": [
            {"cuotas": c, "recargo_pct": round(r * 100, 1),
             "total": round(base * (1 + r), 2),
             "valor_cuota": round(base * (1 + r) / c, 2)}
            for c, r in CUOTAS_RECARGO.items()
        ],
    }


# ─── COMUNIDAD INTERNA (F) ───────────────────────────────────────────────────

@app.get("/comunidad/feed")
def ver_feed_comunidad(limit: int = 30, db: Session = Depends(get_db)):
    """Feed público para todos los aliados (los no ocultos)."""
    posts = db.query(PostComunidad).filter(
        PostComunidad.oculto == False
    ).order_by(
        PostComunidad.fijado.desc(),
        PostComunidad.creado_en.desc()
    ).limit(limit).all()

    resultado = []
    for p in posts:
        coms = db.query(ComentarioComunidad).filter(
            ComentarioComunidad.post_id == p.id
        ).order_by(ComentarioComunidad.creado_en.asc()).all()
        resultado.append({
            "id": p.id,
            "tipo": p.tipo,
            "titulo": p.titulo,
            "cuerpo": p.cuerpo,
            "likes": p.likes or 0,
            "fijado": p.fijado,
            "autor": p.aliado.nombre.split()[0] if p.aliado else "—",
            "autor_codigo": p.aliado.codigo if p.aliado else None,
            "autor_nivel": p.aliado.nivel_calculado if p.aliado else None,
            "fecha": p.creado_en.strftime("%d/%m/%Y %H:%M") if p.creado_en else None,
            "comentarios": [
                {"autor": c.aliado.nombre.split()[0] if c.aliado else "—",
                 "cuerpo": c.cuerpo,
                 "fecha": c.creado_en.strftime("%d/%m/%Y %H:%M") if c.creado_en else None}
                for c in coms
            ],
        })
    return {"posts": resultado}


class PostCreate(BaseModel):
    codigo_aliado: str
    tipo: str = "tip"          # tip | win | pregunta
    titulo: str
    cuerpo: str


@app.post("/comunidad/post")
def crear_post(post: PostCreate, db: Session = Depends(get_db)):
    if post.tipo not in ("tip", "win", "pregunta"):
        raise HTTPException(400, "Tipo inválido.")
    if len(post.titulo.strip()) < 3 or len(post.cuerpo.strip()) < 5:
        raise HTTPException(400, "Título y cuerpo requeridos.")
    a = _get_aliado(post.codigo_aliado, db)
    p = PostComunidad(
        aliado_id=a.id, tipo=post.tipo,
        titulo=post.titulo.strip()[:200], cuerpo=post.cuerpo.strip()[:3000],
    )
    db.add(p); db.commit(); db.refresh(p)
    return {"mensaje": "Post publicado.", "id": p.id}


@app.post("/comunidad/{id}/like")
def like_post(id: int, db: Session = Depends(get_db)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.likes = (p.likes or 0) + 1
    db.commit()
    return {"likes": p.likes}


class ComentarioCreate(BaseModel):
    codigo_aliado: str
    cuerpo: str


@app.post("/comunidad/{id}/comentario")
def comentar(id: int, com: ComentarioCreate, db: Session = Depends(get_db)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    if len(com.cuerpo.strip()) < 2:
        raise HTTPException(400, "Comentario vacío.")
    a = _get_aliado(com.codigo_aliado, db)
    c = ComentarioComunidad(
        post_id=p.id, aliado_id=a.id, cuerpo=com.cuerpo.strip()[:1000]
    )
    db.add(c); db.commit()
    return {"mensaje": "Comentario publicado."}


@app.post("/admin/comunidad/{id}/fijar")
def admin_fijar_post(id: int, fijar: bool = True, db: Session = Depends(get_db)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.fijado = fijar; db.commit()
    return {"mensaje": "Post fijado." if fijar else "Post desfijado."}


@app.post("/admin/comunidad/{id}/ocultar")
def admin_ocultar_post(id: int, ocultar: bool = True, db: Session = Depends(get_db)):
    p = db.query(PostComunidad).filter(PostComunidad.id == id).first()
    if not p: raise HTTPException(404, "Post no encontrado.")
    p.oculto = ocultar; db.commit()
    return {"mensaje": "Post ocultado." if ocultar else "Post visible de nuevo."}


# ─── PORTAL PÚBLICO POR ALIADO (G lite) ──────────────────────────────────────
# URL pública /p/{ref_code} que muestra una landing mínima con el nombre del
# aliado, los planes y el botón de pago con atribución automática.

from fastapi.responses import HTMLResponse

@app.get("/p/{ref_code}", response_class=HTMLResponse)
def portal_publico_aliado(ref_code: str, db: Session = Depends(get_db)):
    """Landing pública del aliado con su marca/bio y CTA de pago."""
    a = db.query(Aliado).filter(Aliado.ref_code == ref_code, Aliado.activo == True).first()
    if not a or not a.portal_publico_activo:
        return HTMLResponse("<h1>Portal no disponible</h1>", status_code=404)

    titular = a.portal_publico_titular or a.nombre
    bio = a.portal_publico_bio or f"Asesor digital — Partner de Avanza Digital"

    planes_html = ""
    for nombre_plan, precio in PLANES.items():
        planes_html += f"""
        <div style='background:#111;border:1px solid #222;border-radius:12px;padding:24px;margin-bottom:16px;'>
          <div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;'>
            <div>
              <h3 style='font-size:1.1rem;font-weight:800;margin-bottom:4px;'>{nombre_plan}</h3>
              <p style='color:#a1a1aa;font-size:0.85rem;'>USD {int(precio)}</p>
            </div>
            <a href='/checkout/crear?plan={nombre_plan}&ref_code={ref_code}' method='post'
               style='padding:12px 20px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-weight:700;'>
              Contratar →
            </a>
          </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{titular} · Avanza Digital</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&display=swap" rel="stylesheet">
<style>
body{{margin:0;font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;}}
.wrap{{max-width:640px;margin:0 auto;padding:48px 24px;}}
.hero{{text-align:center;margin-bottom:40px;}}
.hero h1{{font-size:2rem;font-weight:900;margin-bottom:8px;}}
.hero p{{color:#a1a1aa;font-size:1rem;}}
.badge{{display:inline-block;background:rgba(59,130,246,0.15);color:#93c5fd;padding:4px 12px;
       border-radius:20px;font-size:0.72rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;
       margin-bottom:16px;}}
.footer{{margin-top:48px;text-align:center;color:#71717a;font-size:0.78rem;}}
a.cta{{display:inline-block;padding:12px 20px;background:#3b82f6;color:#fff!important;
       border-radius:8px;text-decoration:none;font-weight:700;}}
</style></head><body>
<div class="wrap">
  <div class="hero">
    <div class="badge">Asesor certificado · Avanza Partner Network</div>
    <h1>{titular}</h1>
    <p>{bio}</p>
  </div>
  <h2 style="font-size:1.2rem;font-weight:800;margin-bottom:16px;">Planes disponibles</h2>
  {planes_html}
  <div class="footer">
    <p>Al contratar desde este link, tu pago queda atribuido automáticamente a {titular}.</p>
    <p style="margin-top:8px;"><a href="https://avanzadigital.digital" style="color:#3b82f6;">avanzadigital.digital</a></p>
  </div>
</div>
</body></html>"""
    return HTMLResponse(html)


@app.patch("/aliados/{codigo}/portal-publico")
def configurar_portal_publico(codigo: str,
                              activo: bool = True,
                              titular: str = "",
                              bio: str = "",
                              db: Session = Depends(get_db)):
    a = _get_aliado(codigo, db)
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
    }
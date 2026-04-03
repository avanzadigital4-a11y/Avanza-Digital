from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from datetime import datetime, timedelta
import random, string, os

from database import engine, get_db, Base
from models import Aliado, Admin, Venta, Referido, Prospecto, AuditoriaLog, PLANES, NIVELES

Base.metadata.create_all(bind=engine)

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
    return f"AL-{str(db.query(Aliado).count() + 1).zfill(3)}"


# ─── SALUD ───────────────────────────────────────────────────────────────────

@app.get("/")
def root(): return {"status": "Avanza Partner Portal activo", "version": "1.1"}

@app.get("/health")
def health(): return {"status": "ok"}


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


# ─── VENTAS ──────────────────────────────────────────────────────────────────

@app.post("/ventas/registrar")
def registrar_venta(codigo_aliado: str, nombre_cliente: str, plan: str,
                    modalidad_pago: str = "ARS MEP", referido_id: int = None,
                    notas: str = "", db: Session = Depends(get_db)):
    a = _get_aliado(codigo_aliado, db)
    if plan not in PLANES: raise HTTPException(400, "Plan inválido.")
    valor = PLANES[plan]
    comision_usd = round(valor * a.comision_pct, 2)
    v = Venta(aliado_id=a.id, referido_id=referido_id, nombre_cliente=nombre_cliente,
              plan=plan, valor_usd=valor, comision_pct=a.comision_pct,
              comision_usd=comision_usd, confirmada=True, pagada=False,
              fecha_venta=datetime.now(), modalidad_pago=modalidad_pago, notas=notas)
    db.add(v)
    if referido_id:
        ref = db.query(Referido).filter(Referido.id == referido_id).first()
        if ref: ref.convertido = True
    a.nivel = a.nivel_calculado
    db.commit()
    return {"mensaje": "Venta registrada.", "aliado": a.nombre, "nivel_nuevo": a.nivel_calculado,
            "valor_usd": valor, "comision_usd": comision_usd}


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
    return {
        "id": p.id, "nombre": p.nombre, "contacto": p.contacto,
        "plan_interes": p.plan_interes, "estado": p.estado,
        "nota": p.nota, "interesante": p.interesante,
        "fecha_contacto":  p.fecha_contacto.strftime("%d/%m/%Y") if p.fecha_contacto else None,
        "fecha_respuesta": p.fecha_respuesta.strftime("%d/%m/%Y") if p.fecha_respuesta else None,
        "creado_en": p.creado_en.strftime("%d/%m/%Y") if p.creado_en else None,
    }

def _aliado_row(a):
    return {
        "codigo": a.codigo, "nombre": a.nombre, "email": a.email,
        "whatsapp": a.whatsapp, "ciudad": a.ciudad, "perfil": a.perfil,
        "nivel": a.nivel_calculado, "ventas_6m": a.ventas_6_meses,
        "total_ganado": round(a.total_ganado, 2),
        "total_pendiente": round(a.total_pendiente, 2),
        "ref_code": a.ref_code, "fecha_firma": a.fecha_firma,
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
    """Ranking de aliados (reales + ficticios para motivación)."""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    lista_reales = []
    
    for a in aliados:
        # Ocultar apellidos completos por privacidad (Ej: Juan P.)
        partes = a.nombre.split()
        nombre_corto = a.nombre
        if len(partes) > 1:
            nombre_corto = f"{partes[0]} {partes[1][0]}."
            
        lista_reales.append({
            "codigo": a.codigo,
            "nombre": nombre_corto,
            "nivel": a.nivel_calculado,
            "ventas_6m": a.ventas_6_meses,
            "total_ganado": round(a.total_ganado, 2)
        })
        
    # Aquí configuras a tus "Top Aliados" ficticios
    lista_ficticios = [
        {"codigo": "AL-991", "nombre": "Martín G.", "nivel": "ELITE", "ventas_6m": 12, "total_ganado": 5850.0},
        {"codigo": "AL-842", "nombre": "Sofía L.", "nivel": "PREMIUM", "ventas_6m": 8, "total_ganado": 3100.0},
        {"codigo": "AL-705", "nombre": "Lucas P.", "nivel": "PREMIUM", "ventas_6m": 5, "total_ganado": 1950.0},
        {"codigo": "AL-613", "nombre": "Camila R.", "nivel": "SILVER", "ventas_6m": 3, "total_ganado": 870.0},
    ]
    
    # Mezclamos todos y los ordenamos por quién ganó más plata
    completo = sorted(lista_reales + lista_ficticios, key=lambda x: x["total_ganado"], reverse=True)
    
    # Asignamos el número de posición
    for i, item in enumerate(completo):
        item["posicion"] = i + 1
        
    # DEVOLVEMOS LA LISTA COMPLETA, SIN CORTARLA
    return completo
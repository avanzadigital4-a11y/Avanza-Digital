from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from datetime import datetime
import random, string

from database import engine, get_db, Base
from models import Aliado, Admin, Venta, Referido, PLANES, NIVELES

# Crear todas las tablas
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Avanza Partner Portal", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def generar_ref_code(nombre: str) -> str:
    base = nombre.split()[0].lower()[:6]
    sufijo = ''.join(random.choices(string.digits, k=4))
    return f"{base}{sufijo}"

def generar_codigo_aliado(db: Session) -> str:
    total = db.query(Aliado).count()
    return f"AL-{str(total + 1).zfill(3)}"


# ─── ENDPOINTS DE SALUD ─────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "Avanza Partner Portal activo", "version": "1.0"}

@app.get("/health")
def health():
    return {"status": "ok"}


# ─── ENDPOINTS DE ALIADOS ────────────────────────────────────────────────────

@app.post("/aliados/crear")
def crear_aliado(
    nombre: str,
    email: str,
    whatsapp: str,
    ciudad: str,
    dni: str = "",
    perfil: str = "",
    fecha_firma: str = "",
    password: str = "avanza2026",
    db: Session = Depends(get_db)
):
    """Crea un nuevo aliado (solo admin)"""
    existe = db.query(Aliado).filter(Aliado.email == email).first()
    if existe:
        raise HTTPException(status_code=400, detail="Ya existe un aliado con ese email")

    aliado = Aliado(
        codigo=generar_codigo_aliado(db),
        nombre=nombre,
        email=email,
        dni=dni,
        whatsapp=whatsapp,
        ciudad=ciudad,
        perfil=perfil,
        fecha_firma=fecha_firma or datetime.now().strftime("%d/%m/%Y"),
        ref_code=generar_ref_code(nombre),
        password_hash=hash_password(password),
    )
    db.add(aliado)
    db.commit()
    db.refresh(aliado)
    return {
        "mensaje": f"Aliado {aliado.codigo} creado exitosamente",
        "codigo": aliado.codigo,
        "ref_code": aliado.ref_code,
        "link_ref": f"https://avanzadigital.digital/alianzas?ref={aliado.ref_code}"
    }


@app.get("/aliados")
def listar_aliados(db: Session = Depends(get_db)):
    """Lista todos los aliados con su estado actual"""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    return [
        {
            "codigo": a.codigo,
            "nombre": a.nombre,
            "email": a.email,
            "whatsapp": a.whatsapp,
            "ciudad": a.ciudad,
            "perfil": a.perfil,
            "nivel": a.nivel_calculado,
            "ventas_6m": a.ventas_6_meses,
            "total_ganado": round(a.total_ganado, 2),
            "total_pendiente": round(a.total_pendiente, 2),
            "ref_code": a.ref_code,
            "fecha_firma": a.fecha_firma,
        }
        for a in aliados
    ]


@app.get("/aliados/{codigo}")
def ver_aliado(codigo: str, db: Session = Depends(get_db)):
    """Detalle completo de un aliado"""
    aliado = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not aliado:
        raise HTTPException(status_code=404, detail="Aliado no encontrado")
    return {
        "codigo": aliado.codigo,
        "nombre": aliado.nombre,
        "email": aliado.email,
        "whatsapp": aliado.whatsapp,
        "ciudad": aliado.ciudad,
        "perfil": aliado.perfil,
        "nivel_actual": aliado.nivel,
        "nivel_calculado": aliado.nivel_calculado,
        "comision_pct": aliado.comision_pct * 100,
        "ventas_6m": aliado.ventas_6_meses,
        "total_ventas": len(aliado.ventas),
        "total_ganado": round(aliado.total_ganado, 2),
        "total_pendiente": round(aliado.total_pendiente, 2),
        "link_ref": f"https://avanzadigital.digital/alianzas?ref={aliado.ref_code}",
        "referidos": [
            {
                "cliente": r.nombre_cliente,
                "plan": r.plan_elegido,
                "fecha": r.registrado_en.strftime("%d/%m/%Y"),
                "confirmado": r.acuse_recibo,
                "convertido": r.convertido,
            }
            for r in aliado.referidos
        ],
        "ventas": [
            {
                "cliente": v.nombre_cliente,
                "plan": v.plan,
                "valor": v.valor_usd,
                "comision": v.comision_usd,
                "pagada": v.pagada,
                "fecha": v.fecha_venta.strftime("%d/%m/%Y") if v.fecha_venta else None,
            }
            for v in aliado.ventas if v.confirmada
        ]
    }


# ─── ENDPOINTS DE ALIADOS — SUSPENDER / ACTIVAR / ELIMINAR ──────────────────

@app.post("/aliados/{codigo}/suspender")
def suspender_aliado(codigo: str, db: Session = Depends(get_db)):
    """Admin suspende un aliado — no puede entrar al portal"""
    aliado = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not aliado:
        raise HTTPException(status_code=404, detail="Aliado no encontrado")
    aliado.activo = False
    db.commit()
    return {"mensaje": f"Aliado {aliado.nombre} suspendido"}

@app.post("/aliados/{codigo}/activar")
def activar_aliado(codigo: str, db: Session = Depends(get_db)):
    """Admin reactiva un aliado suspendido"""
    aliado = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not aliado:
        raise HTTPException(status_code=404, detail="Aliado no encontrado")
    aliado.activo = True
    db.commit()
    return {"mensaje": f"Aliado {aliado.nombre} reactivado"}

@app.delete("/aliados/{codigo}/eliminar")
def eliminar_aliado(codigo: str, db: Session = Depends(get_db)):
    """Admin elimina permanentemente un aliado — libera el código para reusar"""
    aliado = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not aliado:
        raise HTTPException(status_code=404, detail="Aliado no encontrado")
    # Eliminar referidos y ventas asociadas primero
    db.query(Referido).filter(Referido.aliado_id == aliado.id).delete()
    db.query(Venta).filter(Venta.aliado_id == aliado.id).delete()
    db.delete(aliado)
    db.commit()
    return {"mensaje": f"Aliado {codigo} eliminado permanentemente"}

@app.get("/aliados/suspendidos")
def listar_suspendidos(db: Session = Depends(get_db)):
    """Lista aliados suspendidos"""
    aliados = db.query(Aliado).filter(Aliado.activo == False).all()
    return [
        {
            "codigo": a.codigo,
            "nombre": a.nombre,
            "email": a.email,
            "whatsapp": a.whatsapp,
            "ciudad": a.ciudad,
            "nivel": a.nivel_calculado,
            "ventas_6m": a.ventas_6_meses,
            "total_ganado": round(a.total_ganado, 2),
            "ref_code": a.ref_code,
            "fecha_firma": a.fecha_firma,
        }
        for a in aliados
    ]

# ─── ENDPOINTS DE REFERIDOS ──────────────────────────────────────────────────

@app.post("/referidos/registrar")
def registrar_referido(
    ref_code: str,
    nombre_cliente: str,
    plan_elegido: str,
    notas: str = "",
    db: Session = Depends(get_db)
):
    """El aliado registra un prospecto ANTES de que pague"""
    aliado = db.query(Aliado).filter(Aliado.ref_code == ref_code).first()
    if not aliado:
        raise HTTPException(status_code=404, detail="Código de referido inválido")

    if plan_elegido not in PLANES:
        raise HTTPException(status_code=400, detail=f"Plan inválido. Opciones: {list(PLANES.keys())}")

    referido = Referido(
        aliado_id=aliado.id,
        nombre_cliente=nombre_cliente,
        plan_elegido=plan_elegido,
        notas=notas,
    )
    db.add(referido)
    db.commit()
    db.refresh(referido)
    return {
        "mensaje": "Referido registrado. Avanza Digital fue notificado.",
        "id_referido": referido.id,
        "aliado": aliado.nombre,
        "cliente": nombre_cliente,
        "plan": plan_elegido,
        "valor_plan": PLANES[plan_elegido],
        "comision_estimada": round(PLANES[plan_elegido] * aliado.comision_pct, 2),
        "registrado_en": referido.registrado_en.strftime("%d/%m/%Y %H:%M"),
    }


@app.get("/referidos/pendientes")
def referidos_pendientes(db: Session = Depends(get_db)):
    """Lista referidos sin acuse de recibo — para admin"""
    pendientes = db.query(Referido).filter(Referido.acuse_recibo == False).all()
    return [
        {
            "id": r.id,
            "aliado": r.aliado.nombre,
            "cliente": r.nombre_cliente,
            "plan": r.plan_elegido,
            "registrado_en": r.registrado_en.strftime("%d/%m/%Y %H:%M"),
        }
        for r in pendientes
    ]


@app.post("/referidos/{id}/confirmar")
def confirmar_referido(id: int, db: Session = Depends(get_db)):
    """Admin confirma que recibió el aviso del aliado"""
    referido = db.query(Referido).filter(Referido.id == id).first()
    if not referido:
        raise HTTPException(status_code=404, detail="Referido no encontrado")
    referido.acuse_recibo = True
    db.commit()
    return {"mensaje": f"Referido de '{referido.nombre_cliente}' confirmado"}


# ─── ENDPOINTS DE VENTAS ─────────────────────────────────────────────────────

@app.post("/ventas/registrar")
def registrar_venta(
    codigo_aliado: str,
    nombre_cliente: str,
    plan: str,
    modalidad_pago: str = "ARS MEP",
    referido_id: int = None,
    notas: str = "",
    db: Session = Depends(get_db)
):
    """Admin registra una venta cerrada"""
    aliado = db.query(Aliado).filter(Aliado.codigo == codigo_aliado).first()
    if not aliado:
        raise HTTPException(status_code=404, detail="Aliado no encontrado")

    if plan not in PLANES:
        raise HTTPException(status_code=400, detail=f"Plan inválido. Opciones: {list(PLANES.keys())}")

    valor = PLANES[plan]
    comision_pct = aliado.comision_pct
    comision_usd = round(valor * comision_pct, 2)

    venta = Venta(
        aliado_id=aliado.id,
        referido_id=referido_id,
        nombre_cliente=nombre_cliente,
        plan=plan,
        valor_usd=valor,
        comision_pct=comision_pct,
        comision_usd=comision_usd,
        confirmada=True,
        pagada=False,
        fecha_venta=datetime.now(),
        modalidad_pago=modalidad_pago,
        notas=notas,
    )
    db.add(venta)

    if referido_id:
        ref = db.query(Referido).filter(Referido.id == referido_id).first()
        if ref:
            ref.convertido = True

    aliado.nivel = aliado.nivel_calculado
    db.commit()

    return {
        "mensaje": "Venta registrada exitosamente",
        "aliado": aliado.nombre,
        "nivel_nuevo": aliado.nivel_calculado,
        "cliente": nombre_cliente,
        "plan": plan,
        "valor_usd": valor,
        "comision_usd": comision_usd,
    }


@app.post("/ventas/{id}/pagar")
def marcar_pagada(id: int, modalidad: str = "ARS MEP", db: Session = Depends(get_db)):
    """Admin marca una comisión como pagada"""
    venta = db.query(Venta).filter(Venta.id == id).first()
    if not venta:
        raise HTTPException(status_code=404, detail="Venta no encontrada")
    venta.pagada = True
    venta.fecha_pago = datetime.now()
    venta.modalidad_pago = modalidad
    db.commit()
    return {"mensaje": f"Comisión de USD {venta.comision_usd} marcada como pagada a {venta.aliado.nombre}"}


# ─── RESUMEN GENERAL ─────────────────────────────────────────────────────────

@app.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    """KPIs generales del programa — vista admin"""
    aliados = db.query(Aliado).filter(Aliado.activo == True).all()
    ventas = db.query(Venta).filter(Venta.confirmada == True).all()

    total_vendido = sum(v.valor_usd for v in ventas)
    total_comisiones = sum(v.comision_usd for v in ventas)
    pendiente_pagar = sum(v.comision_usd for v in ventas if not v.pagada)

    niveles = {"BASIC": 0, "SILVER": 0, "PREMIUM": 0, "ELITE": 0}
    for a in aliados:
        niveles[a.nivel_calculado] = niveles.get(a.nivel_calculado, 0) + 1

    return {
        "total_aliados": len(aliados),
        "total_ventas": len(ventas),
        "total_vendido_usd": round(total_vendido, 2),
        "total_comisiones_usd": round(total_comisiones, 2),
        "pendiente_pagar_usd": round(pendiente_pagar, 2),
        "distribucion_niveles": niveles,
        "referidos_sin_confirmar": db.query(Referido).filter(Referido.acuse_recibo == False).count(),
    }
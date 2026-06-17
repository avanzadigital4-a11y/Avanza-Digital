"""
comisiones.py — Comisiones del aliado y Planes de Continuidad (DINERO).

Duodécimo router migrado de main.py (tramo 7 del split — sub-dominio
comisiones/continuidad). Migrado con máxima cautela: acá se decide qué
cobra cada aliado. Contiene:
  - Comisiones: listado del aliado (por JWT y por código con ownership),
    listado admin con datos de cobro, y "abonar" (exige CBU/alias cargado
    salvo confirmar_sin_cbu=true — spec §15). Los dos endpoints admin que
    delegaban solo en el middleware ahora llevan
    Depends(current_admin_required) explícito (criterio de tramos previos).
  - Planes de Continuidad (v1.5): alta/baja/precio/listado admin y
    auto-servicio del aliado (alta con notificación, listado, baja).
  - El motor de comisiones recurrentes: _crear_comisiones_recurrentes_para_plan
    (titular 10% + sponsor 5%, idempotente por aliado/cliente/plan/mes/año) y
    _generar_comisiones_recurrentes_del_mes. Los usan:
      · checkout.py → primera comisión al confirmar el pago del primer mes,
      · el job mensual del scheduler (vive en main, importa diferido),
      · POST /admin/continuidad/generar-comisiones-mes (corrida manual).
    CUIDADO: la idempotencia por etiqueta "(recurrente)" + mes/año es lo que
    evita comisiones duplicadas entre el alta, el cron y la corrida manual.

Helpers compartidos de main (_get_aliado) se acceden por puente diferido.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import current_aliado_required, current_admin_required, verify_ownership_dep
from database import get_db
from models import (
    Aliado, Comision, PlanContinuidadActivo, Venta,
    COMISION_RECURRENTE_PCT, PLANES_CONTINUIDAD,
)
from notificaciones import enviar_email, notificar_aliado

router = APIRouter(tags=["comisiones"])


# ── Puente diferido a helpers de main (evita import circular) ────────────────

def _get_aliado(codigo, db):
    from main import _get_aliado as f
    return f(codigo, db)


def _comision_row(c: Comision, cliente_fallback: str = ""):
    return {
        "id": c.id,
        "cliente": c.nombre_cliente or cliente_fallback or "—",
        "plan": c.plan,
        "monto_plan_usd": c.monto_plan_usd,
        "comision_usd": c.comision_usd,
        "comision_pct": c.comision_pct,
        "estado": c.estado,
        "processor": c.processor,
        "fecha_pago": c.fecha_pago.isoformat() if c.fecha_pago else None,
        "fecha_abono": c.fecha_abono.isoformat() if c.fecha_abono else None,
    }


@router.get("/aliado/comisiones")
def listar_comisiones_por_token(aliado: Aliado = Depends(current_aliado_required),
                                 db: Session = Depends(get_db)):
    """Comisiones del aliado autenticado.

    SECURITY (rev): la versión anterior tomaba el código directamente del header
    `Authorization: Bearer <codigo>` SIN validar firma — eso permitía a cualquiera
    listar comisiones ajenas con solo conocer el código. Ahora valida JWT firmado
    con HS256 contra JWT_SECRET y resuelve el aliado del subject del token.
    """
    comisiones = db.query(Comision).filter(Comision.aliado_id == aliado.id)\
        .order_by(Comision.fecha_pago.desc().nullslast() if hasattr(Comision.fecha_pago, "desc") else Comision.id.desc()).all()
    return [_comision_row(c) for c in comisiones]


@router.get("/aliados/{codigo}/comisiones")
def listar_comisiones_aliado(codigo: str, db: Session = Depends(get_db), _owner=Depends(verify_ownership_dep)):
    """Devuelve todas las comisiones del aliado (pendientes + abonadas)
    con totales agregados. Es la vista del panel de comisiones del portal.

    v1.5 — Suma:
      * mrr_recurrente_usd: USD/mes que está cobrando ahora (10% sobre planes
        de continuidad activos de sus clientes).
      * clientes_continuidad_activos: detalle por cliente activo.
    """
    a = _get_aliado(codigo, db)
    comisiones = db.query(Comision).filter(Comision.aliado_id == a.id)\
        .order_by(Comision.fecha_pago.desc().nullslast() if hasattr(Comision.fecha_pago, "desc") else Comision.id.desc()).all()

    items = [_comision_row(c) for c in comisiones]
    total_pendiente = round(sum(c.comision_usd for c in comisiones if c.estado == "pendiente"), 2)
    total_abonado   = round(sum(c.comision_usd for c in comisiones if c.estado == "abonada"), 2)

    # MRR recurrente — planes de continuidad activos de este aliado
    activos = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.aliado_id == a.id,
        PlanContinuidadActivo.fecha_baja.is_(None),
    ).order_by(PlanContinuidadActivo.fecha_alta.desc()).all()

    mrr = round(sum(p.comision_mensual_usd for p in activos), 2)
    clientes_continuidad = [{
        "id": p.id,
        "cliente": p.nombre_cliente,
        "plan": p.plan_continuidad,
        "precio_mensual": round(float(p.precio_mensual_usd), 2),
        "comision_mensual": p.comision_mensual_usd,
        "fecha_alta": p.fecha_alta.strftime("%d/%m/%y") if p.fecha_alta else "—",
    } for p in activos]

    return {
        "aliado": a.nombre,
        "codigo": a.codigo,
        "cbu_alias": a.cbu_alias,
        "total_pendiente_usd": total_pendiente,
        "total_abonado_usd":   total_abonado,
        "mrr_recurrente_usd":  mrr,
        "clientes_continuidad_activos": clientes_continuidad,
        "comisiones": items,
    }


# ─── COMISIONES — ADMIN (spec §12, §15) ──────────────────────────────────────

@router.get("/admin/comisiones")
def admin_listar_comisiones(estado: str = "", db: Session = Depends(get_db),
                            _admin=Depends(current_admin_required)):
    """Lista todas las comisiones del sistema, con datos del aliado para facilitar
    la transferencia. `estado` opcional: 'pendiente' | 'abonada'."""
    q = db.query(Comision)
    if estado in ("pendiente", "abonada"):
        q = q.filter(Comision.estado == estado)
    comisiones = q.order_by(Comision.fecha_pago.desc().nullslast() if hasattr(Comision.fecha_pago, "desc") else Comision.id.desc()).all()

    out = []
    for c in comisiones:
        aliado = c.aliado
        out.append({
            **_comision_row(c),
            "aliado_codigo": aliado.codigo if aliado else None,
            "aliado_nombre": aliado.nombre if aliado else "(aliado eliminado)",
            "aliado_email":  aliado.email if aliado else None,
            "aliado_cbu":    aliado.cbu_alias if aliado else None,
        })
    return out


@router.post("/admin/comisiones/{id}/abonar")
def admin_marcar_comision_abonada(id: int,
                                   confirmar_sin_cbu: bool = False,
                                   db: Session = Depends(get_db),
                                   _admin=Depends(current_admin_required)):
    """Marca una comisión como abonada. Si el aliado no tiene CBU cargado, falla
    salvo que se pase `confirmar_sin_cbu=true` (spec §15)."""
    c = db.query(Comision).filter(Comision.id == id).first()
    if not c:
        raise HTTPException(404, "Comisión no encontrada.")
    if c.estado == "abonada":
        raise HTTPException(400, "Esta comisión ya está marcada como abonada.")

    aliado = c.aliado
    if not aliado:
        raise HTTPException(404, "Aliado asociado no encontrado.")

    # Spec §15: bloquear si no hay CBU, salvo override explícito
    if not aliado.cbu_alias and not confirmar_sin_cbu:
        raise HTTPException(
            400,
            f"El aliado {aliado.nombre} no tiene CBU/alias cargado. "
            "Pedile que lo cargue antes de abonar, o pasá confirmar_sin_cbu=true para forzar."
        )

    c.estado = "abonada"
    c.fecha_abono = datetime.now()

    # También marcar la venta correspondiente como pagada (si existe)
    try:
        venta = db.query(Venta).filter(
            Venta.aliado_id == aliado.id,
            Venta.plan == c.plan,
            Venta.nombre_cliente == c.nombre_cliente,
            Venta.pagada == False,
        ).order_by(Venta.fecha_venta.desc()).first()
        if venta:
            venta.pagada = True
            venta.fecha_pago = datetime.now()
    except Exception as e:
        print(f"[ADMIN ABONAR] No pude sincronizar venta: {e}")

    db.commit()

    # Notificar al aliado
    enviar_email(
        aliado.email,
        f"✅ Tu comisión de USD {c.comision_usd:,.0f} fue abonada",
        f"""<div style="font-family:sans-serif;background:#050505;color:#fff;padding:32px;max-width:520px;margin:auto;border-radius:12px;">
          <h2 style="color:#4ade80;">¡Comisión abonada! 💸</h2>
          <p>Hola <strong>{aliado.nombre.split()[0]}</strong>,</p>
          <p>Se transfirió tu comisión al CBU/alias registrado.</p>
          <div style="background:#111;border:1px solid #222;border-radius:8px;padding:16px;margin:16px 0;">
            <p style="margin:4px 0;"><strong>Plan:</strong> {c.plan}</p>
            <p style="margin:4px 0;"><strong>Cliente:</strong> {c.nombre_cliente or '—'}</p>
            <p style="margin:4px 0;"><strong>Monto:</strong> <span style="color:#4ade80;font-size:1.3rem;font-weight:900;">USD {c.comision_usd:,.0f}</span></p>
            <p style="margin:4px 0;font-size:.85rem;color:#71717a;"><strong>Transferido a:</strong> {aliado.cbu_alias or '(marcado como abonado sin CBU registrado)'}</p>
          </div>
        </div>"""
    )

    return {"mensaje": "Comisión marcada como abonada.",
            "id": c.id, "estado": c.estado,
            "fecha_abono": c.fecha_abono.isoformat()}


# ─── PLANES DE CONTINUIDAD (v1.5) ────────────────────────────────────────────
# Suscripciones recurrentes mensuales de los clientes. Cada plan activo le
# genera al aliado correspondiente un 10% de comisión mensual mientras esté
# vivo. La generación de comisiones mensuales se dispara desde un job (ver
# `generar_comisiones_recurrentes_del_mes`) — admin puede invocarla manual
# o vía scheduler.

@router.post("/admin/continuidad/alta")
def admin_alta_continuidad(payload: dict, db: Session = Depends(get_db),
                           _admin=Depends(current_admin_required)):
    """Da de alta un Plan de Continuidad para un cliente, asociado a un aliado.

    payload esperado:
      - aliado_codigo: str (ej. 'AL-123')
      - nombre_cliente: str
      - cliente_email: str (opcional)
      - plan_continuidad: str (debe estar en PLANES_CONTINUIDAD)
      - precio_mensual_usd: float (opcional — default = precio del plan)
      - notas: str (opcional)
    """
    codigo = (payload.get("aliado_codigo") or "").strip()
    nombre = (payload.get("nombre_cliente") or "").strip()
    plan = (payload.get("plan_continuidad") or "").strip()

    if not codigo or not nombre or not plan:
        raise HTTPException(400, "Faltan campos obligatorios: aliado_codigo, nombre_cliente, plan_continuidad.")
    if plan not in PLANES_CONTINUIDAD:
        raise HTTPException(400, f"Plan inválido. Debe ser uno de: {list(PLANES_CONTINUIDAD.keys())}.")

    a = db.query(Aliado).filter(Aliado.codigo == codigo).first()
    if not a:
        raise HTTPException(404, f"Aliado {codigo} no encontrado.")

    precio = float(payload.get("precio_mensual_usd") or PLANES_CONTINUIDAD[plan])

    activo = PlanContinuidadActivo(
        aliado_id=a.id,
        nombre_cliente=nombre,
        cliente_email=(payload.get("cliente_email") or None),
        plan_continuidad=plan,
        precio_mensual_usd=precio,
        comision_pct=COMISION_RECURRENTE_PCT,
        notas=(payload.get("notas") or None),
    )
    db.add(activo)
    db.commit()
    db.refresh(activo)

    return {
        "mensaje": f"Plan {plan} activado para {nombre} bajo {a.nombre} ({a.codigo}).",
        "id": activo.id,
        "comision_mensual_usd": activo.comision_mensual_usd,
    }


@router.post("/admin/continuidad/{plan_id}/baja")
def admin_baja_continuidad(plan_id: int, payload: dict = None,
                           db: Session = Depends(get_db),
                           _admin=Depends(current_admin_required)):
    """Marca un plan de continuidad como dado de baja.

    Esto detiene la generación de comisiones recurrentes para ese cliente
    en próximos ciclos. Las comisiones ya generadas no se afectan.
    """
    p = db.query(PlanContinuidadActivo).filter(PlanContinuidadActivo.id == plan_id).first()
    if not p:
        raise HTTPException(404, "Plan de continuidad no encontrado.")
    if p.fecha_baja is not None:
        raise HTTPException(400, "Este plan ya está dado de baja.")

    p.fecha_baja = datetime.utcnow()
    p.motivo_baja = (payload or {}).get("motivo_baja") or None
    db.commit()
    return {"mensaje": "Plan dado de baja.", "id": p.id, "fecha_baja": p.fecha_baja.isoformat()}


@router.post("/admin/continuidad/{plan_id}/precio")
def admin_actualizar_precio_continuidad(plan_id: int, payload: dict,
                                        db: Session = Depends(get_db),
                                        _admin=Depends(current_admin_required)):
    """Actualiza el precio mensual de un PlanContinuidadActivo puntual.

    Útil cuando el cliente renegocia o cuando se sube de plan dentro del
    mismo registro (ej: pasa de Cuidado a Crecimiento sin cambiar el alta).
    El nuevo precio se aplica desde la PRÓXIMA comisión generada — las
    comisiones ya creadas en meses anteriores NO se modifican (mantiene
    trazabilidad del histórico).

    payload:
      - precio_mensual_usd: float (obligatorio, > 0)
      - plan_continuidad: str (opcional — si pasa, debe estar en PLANES_CONTINUIDAD)
      - motivo: str (opcional, queda en notas)
    """
    p = db.query(PlanContinuidadActivo).filter(PlanContinuidadActivo.id == plan_id).first()
    if not p:
        raise HTTPException(404, "Plan de continuidad no encontrado.")
    if p.fecha_baja is not None:
        raise HTTPException(400, "Plan dado de baja — no se puede actualizar precio.")

    nuevo_precio = payload.get("precio_mensual_usd")
    if nuevo_precio is None:
        raise HTTPException(400, "Falta precio_mensual_usd.")
    try:
        nuevo_precio = float(nuevo_precio)
    except (TypeError, ValueError):
        raise HTTPException(400, "precio_mensual_usd debe ser numérico.")
    if nuevo_precio <= 0:
        raise HTTPException(400, "precio_mensual_usd debe ser mayor a 0.")

    nuevo_plan = (payload.get("plan_continuidad") or "").strip()
    if nuevo_plan and nuevo_plan not in PLANES_CONTINUIDAD:
        raise HTTPException(400, f"Plan inválido. Debe ser uno de: {list(PLANES_CONTINUIDAD.keys())}.")

    precio_anterior = float(p.precio_mensual_usd)
    plan_anterior = p.plan_continuidad
    p.precio_mensual_usd = nuevo_precio
    if nuevo_plan:
        p.plan_continuidad = nuevo_plan

    # Anexar al histórico (sin pisar las notas existentes)
    motivo = (payload.get("motivo") or "").strip()
    sello = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    cambio = (f"[{sello}] precio: USD {precio_anterior:,.2f} → USD {nuevo_precio:,.2f}"
              + (f" · plan: {plan_anterior} → {nuevo_plan}" if nuevo_plan and nuevo_plan != plan_anterior else "")
              + (f" · motivo: {motivo}" if motivo else ""))
    p.notas = (p.notas + "\n" + cambio) if p.notas else cambio
    db.commit()
    return {
        "mensaje": "Precio actualizado. Aplica desde la próxima comisión generada.",
        "id": p.id,
        "plan": p.plan_continuidad,
        "precio_mensual_usd": round(nuevo_precio, 2),
        "comision_mensual_usd": p.comision_mensual_usd,
    }


@router.get("/admin/continuidad")
def admin_listar_continuidad(activos: bool = True, db: Session = Depends(get_db),
                             _admin=Depends(current_admin_required)):
    """Lista todos los planes de continuidad. Por default solo los activos."""
    q = db.query(PlanContinuidadActivo)
    if activos:
        q = q.filter(PlanContinuidadActivo.fecha_baja.is_(None))
    items = q.order_by(PlanContinuidadActivo.fecha_alta.desc()).all()
    out = []
    for p in items:
        out.append({
            "id": p.id,
            "aliado_codigo": p.aliado.codigo if p.aliado else None,
            "aliado_nombre": p.aliado.nombre if p.aliado else None,
            "cliente": p.nombre_cliente,
            "plan": p.plan_continuidad,
            "precio_mensual_usd": round(float(p.precio_mensual_usd), 2),
            "comision_mensual_usd": p.comision_mensual_usd,
            "fecha_alta": p.fecha_alta.strftime("%d/%m/%Y") if p.fecha_alta else "—",
            "fecha_baja": p.fecha_baja.strftime("%d/%m/%Y") if p.fecha_baja else None,
            "activo": p.activo,
        })
    return {"total": len(out), "items": out}


# ─── PLAN DE CONTINUIDAD — ENDPOINTS DEL ALIADO (auto-servicio) ──────────────
# v1.5 — el aliado puede dar de alta sus propias ventas de Plan de Continuidad
# desde el portal (sin esperar al admin) y darlas de baja cuando el cliente
# cancela. El alta dispara automáticamente la primera comisión del mes en curso.

@router.post("/aliado/continuidad/alta")
def aliado_alta_continuidad(payload: dict,
                            aliado: Aliado = Depends(current_aliado_required),
                            db: Session = Depends(get_db)):
    """El propio aliado da de alta un Plan de Continuidad para un cliente que
    cerró por fuera del checkout (transferencia directa, efectivo, otro medio).

    payload esperado:
      - nombre_cliente: str (obligatorio)
      - plan_continuidad: str (debe estar en PLANES_CONTINUIDAD)
      - cliente_email: str (opcional)
      - precio_mensual_usd: float (opcional — default = precio del plan)
      - notas: str (opcional)

    Crea automáticamente la primera comisión del mes en curso (10% titular +
    5% sponsor si tiene), igual que el flujo de pago automático. Cada 1ro del
    mes siguiente el cron acumula otra.
    """
    nombre = (payload.get("nombre_cliente") or "").strip()
    plan = (payload.get("plan_continuidad") or "").strip()

    if not nombre or not plan:
        raise HTTPException(400, "Faltan campos obligatorios: nombre_cliente, plan_continuidad.")
    if plan not in PLANES_CONTINUIDAD:
        raise HTTPException(400, f"Plan inválido. Debe ser uno de: {list(PLANES_CONTINUIDAD.keys())}.")

    precio = float(payload.get("precio_mensual_usd") or PLANES_CONTINUIDAD[plan])
    if precio <= 0:
        raise HTTPException(400, "Precio mensual inválido.")

    # Anti-duplicado defensivo: si ya hay un plan activo de este aliado para
    # este cliente y este plan, devolvemos el existente. Evita altas dobles
    # por doble-click o reintento.
    existente = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.aliado_id == aliado.id,
        PlanContinuidadActivo.nombre_cliente == nombre,
        PlanContinuidadActivo.plan_continuidad == plan,
        PlanContinuidadActivo.fecha_baja.is_(None),
    ).first()
    if existente:
        return {
            "status": "already_active",
            "id": existente.id,
            "comision_mensual_usd": existente.comision_mensual_usd,
            "mensaje": f"Ya tenés un {plan} activo para {nombre}.",
        }

    p = PlanContinuidadActivo(
        aliado_id=aliado.id,
        nombre_cliente=nombre,
        cliente_email=(payload.get("cliente_email") or None),
        plan_continuidad=plan,
        precio_mensual_usd=precio,
        comision_pct=COMISION_RECURRENTE_PCT,
        notas=(payload.get("notas") or "Alta directa desde portal del aliado"),
    )
    db.add(p)
    db.flush()

    # Atribucion de equipo: si el cierre vino de un lead handed-off, stampear el setter.
    _stampear_setter_desde_lead(db, p, payload.get("lead_id"), aliado.id)

    ahora = datetime.utcnow()
    creado = _crear_comisiones_recurrentes_para_plan(
        db, p, ahora.month, ahora.year, ahora,
    )
    db.commit()

    return {
        "status": "ok",
        "id": p.id,
        "plan": p.plan_continuidad,
        "cliente": p.nombre_cliente,
        "precio_mensual_usd": round(precio, 2),
        "comision_mensual_usd": p.comision_mensual_usd,
        "primera_comision_creada": creado["titular"],
        "comision_sponsor_creada": creado["sponsor"],
        "mensaje": f"{plan} activado para {nombre}. Cobrás USD {p.comision_mensual_usd:,.0f}/mes mientras esté activo.",
    }


@router.get("/aliado/continuidad")
def aliado_listar_continuidad(incluir_bajas: bool = False,
                              aliado: Aliado = Depends(current_aliado_required),
                              db: Session = Depends(get_db)):
    """Lista los planes de continuidad del aliado autenticado. Por default
    solo los activos. Si incluir_bajas=true, incluye los dados de baja."""
    q = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.aliado_id == aliado.id,
    )
    if not incluir_bajas:
        q = q.filter(PlanContinuidadActivo.fecha_baja.is_(None))
    items = q.order_by(PlanContinuidadActivo.fecha_alta.desc()).all()
    out = []
    for p in items:
        out.append({
            "id": p.id,
            "cliente": p.nombre_cliente,
            "cliente_email": p.cliente_email,
            "plan": p.plan_continuidad,
            "precio_mensual_usd": round(float(p.precio_mensual_usd), 2),
            "comision_mensual_usd": p.comision_mensual_usd,
            "fecha_alta": p.fecha_alta.strftime("%d/%m/%Y") if p.fecha_alta else "—",
            "fecha_baja": p.fecha_baja.strftime("%d/%m/%Y") if p.fecha_baja else None,
            "activo": p.activo,
        })
    mrr = round(sum(p.comision_mensual_usd for p in items if p.activo), 2)
    return {"total": len(out), "items": out, "mrr_recurrente_usd": mrr}


@router.post("/aliado/continuidad/{plan_id}/baja")
def aliado_baja_continuidad(plan_id: int,
                            payload: dict = None,
                            aliado: Aliado = Depends(current_aliado_required),
                            db: Session = Depends(get_db)):
    """El aliado da de baja un plan suyo (cuando su cliente cancela).
    Detiene la generación de comisiones recurrentes para próximos meses.
    Las comisiones ya generadas no se afectan (se siguen pudiendo cobrar).
    """
    p = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.id == plan_id,
        PlanContinuidadActivo.aliado_id == aliado.id,  # <-- el aliado solo da de baja lo suyo
    ).first()
    if not p:
        raise HTTPException(404, "Plan de continuidad no encontrado o no te pertenece.")
    if p.fecha_baja is not None:
        raise HTTPException(400, "Este plan ya está dado de baja.")

    p.fecha_baja = datetime.utcnow()
    p.motivo_baja = (payload or {}).get("motivo_baja") or "Baja reportada por el aliado"
    db.commit()
    return {
        "status": "ok",
        "id": p.id,
        "fecha_baja": p.fecha_baja.isoformat(),
        "mensaje": f"{p.plan_continuidad} de {p.nombre_cliente} dado de baja. No se generan más comisiones recurrentes a partir del próximo mes.",
    }


# ─── HELPER: generar comisiones recurrentes de un mes ────────────────────────
# v1.5 — extraído del endpoint admin para que el scheduler y el flujo de alta
# (primera comisión al firmar) puedan reutilizar la misma lógica de creación
# idempotente. Toda creación de Comision recurrente en el sistema pasa por acá.
def _repartir_comision_titular_equipo(db, p, c_closer, plan_label, mes, anio, fecha_pago):
    """Si el plan vino de un handoff de equipo (setter->closer), reparte la comision
    TITULAR: el setter cobra su % pactado y el closer el resto. El total NO cambia (se
    reparte, no se suma) y el 5% del sponsor queda intacto. Idempotente por
    (setter_id, "EQUIPO: cliente", plan, mes, anio)."""
    from sqlalchemy import extract
    setter_id = getattr(p, "setter_id", None)
    split = getattr(p, "setter_split_pct", None)
    if not setter_id or not split or setter_id == p.aliado_id:
        return
    bruto = float(c_closer.comision_usd)
    parte_setter = round(bruto * float(split), 2)
    if parte_setter <= 0:
        return
    cliente_eq = "EQUIPO: " + str(p.nombre_cliente)
    ya = db.query(Comision).filter(
        Comision.aliado_id == setter_id,
        Comision.nombre_cliente == cliente_eq,
        Comision.plan == plan_label,
        extract('month', Comision.fecha_pago) == mes,
        extract('year',  Comision.fecha_pago) == anio,
    ).first()
    if ya:
        return
    # Reducir la del closer y crear la del setter (suman exactamente el bruto).
    c_closer.comision_usd = round(bruto - parte_setter, 2)
    c_setter = Comision(
        aliado_id=setter_id, plan=plan_label,
        monto_plan_usd=c_closer.monto_plan_usd,
        comision_pct=round(float(p.comision_pct) * float(split), 4),
        comision_usd=parte_setter, nombre_cliente=cliente_eq,
        estado="pendiente", fecha_pago=fecha_pago,
    )
    db.add(c_setter)
    notificar_aliado(
        db, setter_id, "comision",
        "Comision de equipo: USD %s" % format(parte_setter, ",.2f"),
        "Tu closer cerro %s. Te toca tu parte como setter del deal." % p.nombre_cliente,
        tab="comisiones",
    )


def _stampear_setter_desde_lead(db, p, lead_id, closer_id):
    """Copia la atribucion setter->closer del lead handed-off al plan, para que el
    split corra al generar comision. Solo si el lead es del closer y trae setter."""
    if not lead_id:
        return
    from models import LeadBolsa
    try:
        lead = db.query(LeadBolsa).filter(LeadBolsa.id == int(lead_id)).first()
    except (TypeError, ValueError):
        return
    if not lead or lead.aliado_id != closer_id:
        return
    if not getattr(lead, "setter_id", None) or not getattr(lead, "setter_split_pct", None):
        return
    if lead.setter_id == closer_id:
        return
    p.setter_id = lead.setter_id
    p.setter_split_pct = lead.setter_split_pct


def _crear_comisiones_recurrentes_para_plan(db: Session,
                                            p: PlanContinuidadActivo,
                                            mes: int,
                                            anio: int,
                                            fecha_pago: datetime) -> dict:
    """Crea (si no existen) la comisión recurrente del aliado titular y la del
    sponsor (5%) para el plan de continuidad `p` en el mes/año dados.

    Idempotente por (aliado_id, nombre_cliente, plan_label, mes, anio).

    Devuelve {'titular': bool, 'sponsor': bool} indicando qué se creó.
    NO hace commit — el caller decide cuándo commitear.
    """
    from sqlalchemy import extract
    plan_label = f"{p.plan_continuidad} (recurrente)"
    creado = {"titular": False, "sponsor": False}

    # 1. Comisión del aliado que vendió (10%)
    ya_titular = db.query(Comision).filter(
        Comision.aliado_id == p.aliado_id,
        Comision.nombre_cliente == p.nombre_cliente,
        Comision.plan == plan_label,
        extract('month', Comision.fecha_pago) == mes,
        extract('year',  Comision.fecha_pago) == anio,
    ).first()
    if not ya_titular:
        c = Comision(
            aliado_id=p.aliado_id,
            plan=plan_label,
            monto_plan_usd=float(p.precio_mensual_usd),
            comision_pct=float(p.comision_pct),
            comision_usd=p.comision_mensual_usd,
            nombre_cliente=p.nombre_cliente,
            estado="pendiente",
            fecha_pago=fecha_pago,
        )
        db.add(c)
        creado["titular"] = True
        _repartir_comision_titular_equipo(db, p, c, plan_label, mes, anio, fecha_pago)
        notificar_aliado(
            db, p.aliado_id, "comision",
            f"🔁 Comisión recurrente: USD {c.comision_usd:,.2f}",
            f"Se generó tu comisión mensual de {p.nombre_cliente} ({p.plan_continuidad}).",
            tab="comisiones",
        )

    # 2. Comisión pasiva 5% al sponsor (RED) — paralelo al one-shot.
    # nombre_cliente lleva prefijo "RED:" para distinguirlo, igual que en las
    # ventas one-shot (ver registrar_venta y _procesar_pago_confirmado).
    aliado = p.aliado
    sponsor = getattr(aliado, "sponsor", None) if aliado else None
    if sponsor:
        cliente_red = f"RED: {aliado.nombre} ({p.nombre_cliente})"
        ya_sponsor = db.query(Comision).filter(
            Comision.aliado_id == sponsor.id,
            Comision.nombre_cliente == cliente_red,
            Comision.plan == plan_label,
            extract('month', Comision.fecha_pago) == mes,
            extract('year',  Comision.fecha_pago) == anio,
        ).first()
        if not ya_sponsor:
            comision_sponsor_usd = round(float(p.precio_mensual_usd) * 0.05, 2)
            c_red = Comision(
                aliado_id=sponsor.id,
                plan=plan_label,
                monto_plan_usd=float(p.precio_mensual_usd),
                comision_pct=0.05,
                comision_usd=comision_sponsor_usd,
                nombre_cliente=cliente_red,
                estado="pendiente",
                fecha_pago=fecha_pago,
            )
            db.add(c_red)
            creado["sponsor"] = True

    return creado


def _generar_comisiones_recurrentes_del_mes(db: Session, mes: int, anio: int) -> dict:
    """Itera todos los planes de continuidad activos y genera la comisión del
    mes/año dado para cada uno (titular + sponsor si corresponde).
    Idempotente. Hace commit antes de devolver.
    """
    if not (1 <= mes <= 12):
        raise ValueError(f"Mes inválido: {mes} (debe ser 1..12)")

    activos = db.query(PlanContinuidadActivo).filter(
        PlanContinuidadActivo.fecha_baja.is_(None),
    ).all()

    ahora = datetime.utcnow()
    fecha_pago = datetime(
        anio, mes,
        ahora.day if (mes == ahora.month and anio == ahora.year) else 1,
    )

    creadas_titular = 0
    creadas_sponsor = 0
    saltadas_idempotencia = 0
    detalle = []

    for p in activos:
        creado = _crear_comisiones_recurrentes_para_plan(db, p, mes, anio, fecha_pago)
        if creado["titular"]:
            creadas_titular += 1
        else:
            saltadas_idempotencia += 1
        if creado["sponsor"]:
            creadas_sponsor += 1
        detalle.append({
            "aliado_codigo": p.aliado.codigo if p.aliado else None,
            "cliente": p.nombre_cliente,
            "plan": p.plan_continuidad,
            "comision_titular_usd": p.comision_mensual_usd,
            "comision_sponsor_usd": (round(float(p.precio_mensual_usd) * 0.05, 2)
                                     if (p.aliado and getattr(p.aliado, "sponsor", None))
                                     else 0.0),
            "creado_titular": creado["titular"],
            "creado_sponsor": creado["sponsor"],
        })

    db.commit()
    return {
        "mensaje": f"Generación recurrente {mes:02d}/{anio} OK.",
        "creadas_titular": creadas_titular,
        "creadas_sponsor": creadas_sponsor,
        "saltadas_por_idempotencia": saltadas_idempotencia,
        "detalle": detalle,
    }


@router.post("/admin/continuidad/generar-comisiones-mes")
def admin_generar_comisiones_recurrentes(payload: dict = None,
                                         db: Session = Depends(get_db),
                                         _admin=Depends(current_admin_required)):
    """Genera las comisiones del mes para todos los planes de continuidad activos.

    Idempotente por mes: si ya existe una Comision con plan='<plan> (recurrente)'
    y fecha_pago dentro del mismo mes/año para el mismo aliado y cliente, NO
    crea otra. Pensado para correrse 1 vez al mes (cron / scheduler / manual).

    Genera además el 5% pasivo al sponsor (si tiene), como en las ventas one-shot.

    Body opcional:
      - mes: int 1..12 (default = mes actual UTC)
      - anio: int (default = año actual UTC)
    """
    payload = payload or {}
    ahora = datetime.utcnow()
    mes  = int(payload.get("mes")  or ahora.month)
    anio = int(payload.get("anio") or ahora.year)
    if not (1 <= mes <= 12):
        raise HTTPException(400, "Mes inválido (debe ser 1..12).")
    return _generar_comisiones_recurrentes_del_mes(db, mes, anio)
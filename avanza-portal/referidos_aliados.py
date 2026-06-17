"""
referidos_aliados.py — Hueco 2: loop de reclutamiento aliado → aliado
================================================================================

QUÉ YA EXISTÍA (no lo tocamos)
------------------------------
El portal YA tiene toda la plomería económica del referido entre aliados:
  - Cada aliado tiene `ref_code`.
  - El registro público (/registrarse) acepta `ref_sponsor` y setea `sponsor_id`.
  - comisiones.py paga 5% pasivo al sponsor sobre las ventas del referido.
El problema: ese loop estaba INVISIBLE (el aliado ni se entera de que puede
reclutar y ganar override) y SIN recompensa inmediata (el 5% recién llega
cuando el referido vende, semanas después). Por eso nadie lo usaba.

QUÉ AGREGA ESTE MÓDULO
----------------------
1. RECOMPENSA DE ACTIVACIÓN (el gancho que faltaba): cuando un referido se
   activa de verdad (primer login), su sponsor recibe un bono de créditos al
   instante + aviso por campanita/push/email. El override del 5% sigue corriendo
   por debajo como recompensa de largo plazo. Idempotente vía TransaccionCredito.
2. VISIBILIDAD: endpoint /aliados/{codigo}/red con el link de reclutamiento,
   un mensaje listo para compartir y las métricas del aliado (a cuántos invitó,
   cuántos se activaron, cuánto override lleva ganado, cuántos créditos cobró).

Costo: $0. Reusa créditos, comisiones y notificaciones que ya existen.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from auth import verify_ownership_dep
from models import Aliado, Comision, TransaccionCredito

router = APIRouter(tags=["red-aliados"])

PORTAL_URL = os.environ.get(
    "PORTAL_URL", os.environ.get("BACKEND_PUBLIC_URL", "https://avanzadigital.com")
).strip().rstrip("/")

# Créditos que recibe el sponsor cuando su referido se activa (primer login).
# Es el incentivo inmediato; el 5% de comisión pasiva es el de largo plazo.
BONUS_ACTIVACION = 75

# Motivo/anti-duplicado en transacciones_credito (mismo patrón que estipendio).
MOTIVO_REF = "referido_activacion"

# Días sin ingresar tras los cuales un referido deja de contar como "activado"
# en el contador X/Y de Mi Red. Es una ventana MÓVIL: si el invitado entra,
# vuelve a contar; si pasa este lapso sin entrar, el contador baja (4/4 → 3/4).
# OJO: esto es solo el contador de actividad reciente. El BONO de activación
# (job_referidos_activacion) es un pago de una sola vez por la PRIMERA
# activación y NO se revierte si el referido después se enfría.
DIAS_VENTANA_ACTIVO = int(os.environ.get("DIAS_VENTANA_ACTIVO", "7"))


def _get_aliado(codigo, db):
    """Puente diferido al helper de main (evita ciclo de import al cargar)."""
    from main import _get_aliado as f
    return f(codigo, db)


def _ref_str(sub_id: int) -> str:
    return f"sub:{sub_id}"


# ─── ENDPOINT: PANEL "MI RED" DEL ALIADO ─────────────────────────────────────

@router.get("/aliados/{codigo}/invitacion")
def mi_invitacion(codigo: str, db: Session = Depends(get_db),
                  _owner=Depends(verify_ownership_dep)):
    """Panel de RECLUTAMIENTO del aliado: su link de invitación, un mensaje
    listo para compartir, el bono por activación y sus métricas de invitados.

    Complementa (no reemplaza) a GET /aliados/{codigo}/red, que ya muestra el
    detalle de la red y la ganancia pasiva. Este se enfoca en la ACCIÓN de
    invitar y está disponible para todos los canales."""
    a = _get_aliado(codigo, db)

    invitados = db.query(func.count(Aliado.id)).filter(Aliado.sponsor_id == a.id).scalar() or 0

    # "Activados" = invitados que INGRESARON en los últimos DIAS_VENTANA_ACTIVO
    # días (ventana móvil). Si dejan de entrar ese lapso, el contador baja solo.
    corte_activo = datetime.now() - timedelta(days=DIAS_VENTANA_ACTIVO)
    activados = (db.query(func.count(Aliado.id))
                 .filter(Aliado.sponsor_id == a.id,
                         Aliado.ultimo_login.isnot(None),
                         Aliado.ultimo_login >= corte_activo)
                 .scalar() or 0)

    # Cuántos ingresaron alguna vez (histórico, no baja nunca). Sirve de referencia
    # y para distinguir "nunca entró" de "entró pero se enfrió".
    activados_alguna_vez = (db.query(func.count(Aliado.id))
                            .filter(Aliado.sponsor_id == a.id,
                                    ((Aliado.ultimo_login.isnot(None)) |
                                     (Aliado.cantidad_logins >= 1)))
                            .scalar() or 0)

    # Override de red: comisiones del aliado al 5% (las de sponsor; las propias
    # arrancan en 10%). Es una aproximación robusta sin tocar el modelo.
    override_usd = (db.query(func.coalesce(func.sum(Comision.comision_usd), 0.0))
                    .filter(Comision.aliado_id == a.id,
                            Comision.comision_pct == 0.05)
                    .scalar() or 0.0)

    creditos_ganados = (db.query(func.coalesce(func.sum(TransaccionCredito.delta), 0))
                        .filter(TransaccionCredito.aliado_id == a.id,
                                TransaccionCredito.motivo == MOTIVO_REF)
                        .scalar() or 0)

    link = f"{PORTAL_URL}/alianzas?ref={a.ref_code}"
    nombre_corto = (a.nombre or "").split()[0] if a.nombre else "Avanza"

    mensaje = (
        "Hola, te comparto el programa de aliados de Avanza Digital. "
        "Es para closers/setters que quieran cerrar sistemas comerciales para "
        "PyMEs industriales, con leads ya cargados y comisión por venta. "
        f"Si te interesa, registrate con mi link: {link}"
    )

    return {
        "ref_code": a.ref_code,
        "invite_link": link,
        "mensaje_para_compartir": mensaje,
        "bono_por_activacion": BONUS_ACTIVACION,
        "override_pct": 5,
        "stats": {
            "invitados": invitados,
            "activados": activados,
            "activados_alguna_vez": activados_alguna_vez,
            "ventana_dias": DIAS_VENTANA_ACTIVO,
            "pendientes_activar": max(0, invitados - activados),
            "override_usd_ganado": round(float(override_usd), 2),
            "creditos_por_referidos": int(creditos_ganados),
        },
    }


# ─── JOB: RECOMPENSA DE ACTIVACIÓN ───────────────────────────────────────────

def job_referidos_activacion():
    """Corre 1x/día. Busca referidos que ya se activaron (al menos un login) y
    cuyo sponsor todavía no cobró el bono, y se lo acredita una sola vez.

    Idempotente: usa TransaccionCredito(motivo='referido_activacion',
    referencia='sub:<id>') como anti-duplicado, igual que el estipendio. No
    rompe el flujo: cualquier error en un referido se loguea y sigue.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        # Import diferido: estos viven en main/notificaciones y se cargan después.
        from main import _ajustar_creditos
        from notificaciones import enviar_email, enviar_push_a_aliado, notificar_aliado

        # Referidos activados (tienen sponsor + al menos un login).
        referidos = (db.query(Aliado)
                     .filter(Aliado.sponsor_id.isnot(None),
                             Aliado.cantidad_logins >= 1)
                     .all())

        otorgados = 0
        for sub in referidos:
            ref = _ref_str(sub.id)

            ya = (db.query(TransaccionCredito)
                  .filter(TransaccionCredito.motivo == MOTIVO_REF,
                          TransaccionCredito.referencia == ref)
                  .first())
            if ya:
                continue

            sponsor = sub.sponsor or db.query(Aliado).filter(Aliado.id == sub.sponsor_id).first()
            if not sponsor:
                continue

            try:
                _ajustar_creditos(db, sponsor, BONUS_ACTIVACION, MOTIVO_REF, ref)
            except Exception as e:
                print(f"[REFERIDO ERROR] sponsor={getattr(sponsor,'codigo','?')} sub={sub.id}: {e}")
                continue

            nombre_ref = (sub.nombre or "Tu referido").split()[0]
            # Campanita in-app (no commitea; lo hace el commit de abajo).
            try:
                notificar_aliado(
                    db, sponsor.id, "referido",
                    f"¡{nombre_ref} se activó! +{BONUS_ACTIVACION} créditos",
                    f"Tu referido {nombre_ref} entró por primera vez al portal. "
                    f"Te acreditamos {BONUS_ACTIVACION} créditos y vas a cobrar 5% "
                    f"de override sobre sus ventas.",
                    tab="red",
                )
            except Exception:
                pass
            try:
                enviar_push_a_aliado(
                    db, sponsor.id, "Referido activado",
                    f"{nombre_ref} se activó. +{BONUS_ACTIVACION} créditos para vos.", "/")
            except Exception:
                pass

            db.commit()
            otorgados += 1

            # Email fuera del commit crítico; trackeado para la analítica (Hueco 1).
            if sponsor.email:
                spn = (sponsor.nombre or "Aliado").split()[0]
                html = f"""
                <div style="font-family:Inter,sans-serif;background:#050505;color:#e2e8f0;padding:40px;max-width:600px;margin:0 auto;border-radius:12px;border:1px solid #1e1e1e;">
                  <div style="margin-bottom:20px;">
                    <span style="background:#0f1d12;color:#86efac;font-size:.75rem;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;">Referido activado</span>
                  </div>
                  <h2 style="margin:0 0 12px;font-size:1.4rem;color:#4ade80;">{spn}, {nombre_ref} se sumó por tu link</h2>
                  <p style="color:#a1a1aa;line-height:1.6;">Tu referido acaba de activarse en el portal. Como gracias:</p>
                  <div style="background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.25);border-radius:8px;padding:18px;margin:18px 0;">
                    <p style="margin:0 0 6px;color:#c084fc;font-weight:700;">+{BONUS_ACTIVACION} créditos acreditados</p>
                    <p style="margin:0;color:#a1a1aa;font-size:.9rem;">Y vas a cobrar <strong style="color:#fff;">5% de override</strong> sobre cada venta que cierre.</p>
                  </div>
                  <a href="{PORTAL_URL}/portal.html" style="display:inline-block;padding:14px 28px;background:#22c55e;color:#000;border-radius:8px;text-decoration:none;font-weight:800;margin-top:6px;">Ver mi red →</a>
                  <p style="margin-top:26px;font-size:.75rem;color:#3f3f46;">Avanza Digital · Partner Network · Seguí invitando closers desde la solapa Mi Red.</p>
                </div>
                """
                try:
                    enviar_email(sponsor.email,
                                 f"{spn}, {nombre_ref} se activó por tu link — +{BONUS_ACTIVACION} créditos",
                                 html, campania="referido_activacion", aliado_id=sponsor.id)
                except Exception as e:
                    print(f"[REFERIDO email] {e}")

        if otorgados:
            print(f"[REFERIDOS] Bonos de activación otorgados: {otorgados}")
    except Exception as e:
        print(f"[REFERIDOS JOB ERROR] {e}")
    finally:
        db.close()
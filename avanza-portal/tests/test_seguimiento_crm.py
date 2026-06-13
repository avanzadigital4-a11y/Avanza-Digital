"""
Tests de POST /prospectos/{id}/seguimiento — el "Hice el seguimiento" en un paso:
  1. Cierra todas las tareas abiertas (pendientes y vencidas) y deja una nota.
  2. Si se agenda próxima acción, crea la tarea y setea proxima_accion_en.
  3. Sin próxima acción, proxima_accion_en queda en None (no quedan vencidas).
  4. No se puede registrar seguimiento sobre un prospecto ajeno.
"""
import sys, os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import Aliado, Prospecto, ActividadProspecto
from passlib.context import CryptContext

AUTH = lambda tok: {"Authorization": f"Bearer {tok}"}
_pwd = CryptContext(schemes=["sha256_crypt"], deprecated="auto")


def _prospecto(db, aliado, estado="contactado"):
    p = Prospecto(aliado_id=aliado.id, nombre="Depósito Jimenez",
                  estado=estado, fecha_contacto=datetime.now() - timedelta(days=4))
    db.add(p); db.commit(); db.refresh(p)
    return p


def _tarea(db, p, *, dias):
    """Crea una tarea con vencimiento a `dias` (negativo = vencida)."""
    t = ActividadProspecto(prospecto_id=p.id, aliado_id=p.aliado_id, tipo="tarea",
                           descripcion="Tocar de nuevo", completada=False,
                           vence_en=datetime.now() + timedelta(days=dias))
    db.add(t); db.commit(); db.refresh(t)
    return t


def _tareas_abiertas(db, p):
    return db.query(ActividadProspecto).filter(
        ActividadProspecto.prospecto_id == p.id,
        ActividadProspecto.tipo == "tarea",
        ActividadProspecto.completada == False,
    ).count()


def test_cierra_tareas_y_agenda_proxima(client, db, aliado, token_aliado):
    p = _prospecto(db, aliado)
    _tarea(db, p, dias=-2)   # vencida
    _tarea(db, p, dias=0)    # pendiente hoy

    r = client.post(f"/prospectos/{p.id}/seguimiento",
                    params={"detalle": "2do toque por LinkedIn",
                            "proxima_accion": "Reescribir si no responde",
                            "vence_en": "2026-06-20"},
                    headers=AUTH(token_aliado))
    assert r.status_code == 200
    data = r.json()
    assert data["tareas_cerradas"] == 2
    assert data["tareas_pendientes"] == 1          # solo la nueva queda abierta
    assert data["proxima_accion_en"].startswith("2026-06-20")

    # Las dos viejas quedaron completadas
    cerradas = db.query(ActividadProspecto).filter(
        ActividadProspecto.prospecto_id == p.id,
        ActividadProspecto.tipo == "tarea",
        ActividadProspecto.completada == True,
    ).count()
    assert cerradas == 2

    # Quedó la nota de lo que hizo en el timeline
    nota = db.query(ActividadProspecto).filter(
        ActividadProspecto.prospecto_id == p.id,
        ActividadProspecto.tipo == "nota",
    ).first()
    assert nota is not None and "LinkedIn" in (nota.descripcion or "")

    db.refresh(p)
    assert p.proxima_accion_en is not None


def test_sin_proxima_accion_no_deja_vencidas(client, db, aliado, token_aliado):
    p = _prospecto(db, aliado)
    _tarea(db, p, dias=-5)   # vencida

    r = client.post(f"/prospectos/{p.id}/seguimiento",
                    params={"detalle": "Llamé, no atendió"},
                    headers=AUTH(token_aliado))
    assert r.status_code == 200
    data = r.json()
    assert data["tareas_cerradas"] == 1
    assert data["tareas_pendientes"] == 0
    assert data["proxima_accion_en"] is None
    assert _tareas_abiertas(db, p) == 0


def test_detalle_vacio_usa_texto_por_defecto(client, db, aliado, token_aliado):
    p = _prospecto(db, aliado)
    r = client.post(f"/prospectos/{p.id}/seguimiento", params={},
                    headers=AUTH(token_aliado))
    assert r.status_code == 200
    nota = db.query(ActividadProspecto).filter(
        ActividadProspecto.prospecto_id == p.id,
        ActividadProspecto.tipo == "nota",
    ).first()
    assert nota is not None and (nota.descripcion or "").strip() != ""


def test_no_se_puede_seguir_prospecto_ajeno(client, db, aliado, token_aliado):
    # Prospecto de OTRO aliado
    otro = Aliado(codigo="AL-OTRO", nombre="Otro", email="otro@test.com",
                  ref_code="otro", password_hash=_pwd.hash("x"), activo=True)
    db.add(otro); db.commit(); db.refresh(otro)
    p_ajeno = _prospecto(db, otro)

    r = client.post(f"/prospectos/{p_ajeno.id}/seguimiento",
                    params={"detalle": "intruso"}, headers=AUTH(token_aliado))
    assert r.status_code == 404
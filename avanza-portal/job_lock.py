"""
job_lock.py — Lock distribuido para jobs del scheduler (anti-duplicación)
=========================================================================

PROBLEMA QUE RESUELVE:
  APScheduler corre EN PROCESO. Si alguna vez hay más de una instancia del
  backend (uvicorn --workers 2, dos servicios en Render/Railway, un deploy
  con overlap), cada instancia registra y dispara los mismos jobs. Para los
  jobs que crean dinero (estipendio mensual, comisiones recurrentes) o
  mandan emails, eso significa comisiones dobles y correos duplicados.

  Ya existe la primera línea de defensa: ENABLE_SCHEDULER=0 en las
  instancias secundarias. Este módulo es la segunda línea ("defensa en
  profundidad"): aunque dos schedulers estén activos por error, la base de
  datos garantiza que cada job corre UNA sola vez por ventana.

CÓMO FUNCIONA:
  - Tabla `job_runs` con UNIQUE(job_name, period_key).
  - period_key = floor(epoch_actual / ventana_segundos). Todas las
    instancias que disparen el job dentro de la misma ventana calculan el
    MISMO period_key.
  - Antes de ejecutar, cada instancia intenta INSERTAR su fila. El UNIQUE
    de la BD decide: la primera inserta y ejecuta; las demás chocan con
    IntegrityError y se saltan la corrida en silencio.
  - Funciona igual en SQLite (dev/tests) y Postgres (producción) — no usa
    advisory locks ni features específicos de un motor.

NOTA SOBRE INTERVALOS:
  Con ventana == intervalo del job, las corridas consecutivas de UNA misma
  instancia caen siempre en buckets consecutivos (no se saltean corridas
  legítimas): floor((t0 + n·i)/i) crece exactamente en 1 por corrida.

USO:
  scheduler.add_job(con_lock(job_x, "job_x", 3600), "interval", hours=1)

  Los tests que llaman job_x() directamente NO pasan por el lock (el wrap
  se aplica solo en la registración del scheduler), así que siguen siendo
  determinísticos.
"""
from __future__ import annotations

import functools
import time
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from database import Base, SessionLocal


class JobRun(Base):
    """Registro de 'este job ya corrió (o está corriendo) en esta ventana'."""
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("job_name", "period_key", name="uq_job_runs_name_period"),
    )

    id          = Column(Integer, primary_key=True)
    job_name    = Column(String(120), nullable=False, index=True)
    period_key  = Column(String(40), nullable=False)
    iniciado_en = Column(DateTime, default=datetime.utcnow)


# Retención de filas históricas (auditoría liviana de qué corrió y cuándo).
_RETENCION_DIAS = 90


def _adquirir_lock(job_name: str, period_key: str) -> bool:
    """Intenta reclamar la ventana (job_name, period_key) para esta instancia.

    Devuelve True si esta instancia ganó el lock y debe ejecutar el job.
    Devuelve False si otra instancia ya lo reclamó en esta ventana.

    FAIL-OPEN deliberado: si la BD está caída o la tabla todavía no existe
    (primer deploy a mitad de migración), devolvemos True y el job corre.
    Razón: preferimos el riesgo conocido de una posible corrida duplicada
    (los jobs de dinero ya tienen idempotencia propia por referencia) antes
    que el riesgo de que NINGUNA instancia ejecute jamás los jobs porque el
    lock no puede escribirse.
    """
    db = SessionLocal()
    try:
        db.add(JobRun(job_name=job_name, period_key=period_key))
        db.commit()
        return True
    except IntegrityError:
        # Otra instancia ya reclamó esta ventana — comportamiento esperado.
        db.rollback()
        return False
    except (OperationalError, ProgrammingError) as e:
        # BD caída o tabla inexistente → fail-open con log.
        db.rollback()
        print(f"[JOB LOCK] No se pudo verificar lock de '{job_name}' "
              f"({type(e).__name__}) — ejecutando igual (fail-open).")
        return True
    finally:
        db.close()


def _purgar_historico(ventana_segundos: int) -> None:
    """Borra filas de job_runs más viejas que la retención.

    Solo lo hacen los jobs diarios o más lentos (ventana >= 1 día), para no
    sumar un DELETE a cada corrida del polling de 30 segundos.
    Best-effort: cualquier error se ignora.
    """
    if ventana_segundos < 86400:
        return
    db = SessionLocal()
    try:
        limite = datetime.utcnow() - timedelta(days=_RETENCION_DIAS)
        db.query(JobRun).filter(JobRun.iniciado_en < limite).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def con_lock(fn, nombre: str, ventana_segundos: int):
    """Envuelve un job del scheduler con el lock de ventana.

    `nombre`            → identificador estable del job (no cambiar entre deploys).
    `ventana_segundos`  → debe coincidir con el intervalo de ejecución del job
                          (job cada 1h → 3600; cada 30 min → 1800; diario → 86400).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        period_key = str(int(time.time() // ventana_segundos))
        if not _adquirir_lock(nombre, period_key):
            # Otra instancia ya está corriendo este job en esta ventana.
            return None
        _purgar_historico(ventana_segundos)
        try:
            return fn(*args, **kwargs)
        except Exception:
            # Los jobs corren en threads del scheduler: sus excepciones no llegan
            # solas a Sentry (la integración FastAPI solo cubre requests HTTP).
            # Reportamos acá con el nombre del job como contexto y re-lanzamos
            # para no alterar el comportamiento previo (APScheduler ya loggea).
            try:
                import sentry_sdk
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("job", nombre)
                    scope.set_context("scheduler_job", {"nombre": nombre,
                                                         "ventana_segundos": ventana_segundos})
                    sentry_sdk.capture_exception()
            except Exception:
                pass  # sin sentry_sdk o sin init: seguimos como siempre
            raise

    return wrapper
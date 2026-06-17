"""
backfill_activacion.py
----------------------
Corrige aliados que figuran "sin activar" en Mi Red (cantidad_logins = 0/NULL)
pero que en realidad YA trabajaron en el portal. Son cuentas anteriores al
arreglo que marca la activacion en el alta, asi que su contador nunca se seteo.

Que considera "actividad real" (cualquiera de estas alcanza):
  - Reclamo al menos un lead de la bolsa (LeadBolsa con su aliado_id)
  - Tiene ultimo_login seteado (ingreso en algun momento)
  - Tiene al menos un Prospecto, Venta o Comision a su nombre

Que hace al aplicar:
  - Pone cantidad_logins = 1 (los marca como activados)
  - Si ultimo_login esta vacio, lo completa con la fecha mas temprana de
    actividad disponible (creado_en / fecha_reclamo) para que Mi Red no muestre
    "Nunca".

SEGURIDAD:
  - Por defecto corre en DRY-RUN: solo imprime a quien cambiaria, NO escribe nada.
  - Para aplicar de verdad:   python backfill_activacion.py --apply
  - Es idempotente: si lo corres dos veces, la segunda no cambia nada (los que ya
    tienen cantidad_logins >= 1 quedan afuera del filtro).

OJO (importante): al pasar un referido de 0 -> 1, el job diario de activacion
(job_referidos_activacion) le va a acreditar al SPONSOR su bono de 75 creditos
en la proxima corrida. Para referidos legitimamente activos eso es correcto
(es un bono que se le debia y no se le habia pagado), pero tenelo presente:
vas a ver una tanda de bonos en la proxima corrida diaria.
"""

import sys
from datetime import datetime

from database import SessionLocal
from models import Aliado, LeadBolsa, Prospecto, Venta, Comision

APPLY = "--apply" in sys.argv


def _ids_con_actividad(db):
    """Devuelve el set de aliado_id que tienen alguna actividad dura."""
    ids = set()
    for modelo in (LeadBolsa, Prospecto, Venta, Comision):
        rows = db.query(modelo.aliado_id).filter(modelo.aliado_id.isnot(None)).distinct().all()
        ids.update(r[0] for r in rows if r[0] is not None)
    return ids


def _fecha_mas_temprana(db, a):
    """Mejor fecha disponible para completar ultimo_login si esta vacio."""
    candidatas = []
    if getattr(a, "creado_en", None):
        candidatas.append(a.creado_en)
    primer_reclamo = (db.query(LeadBolsa.fecha_reclamo)
                      .filter(LeadBolsa.aliado_id == a.id,
                              LeadBolsa.fecha_reclamo.isnot(None))
                      .order_by(LeadBolsa.fecha_reclamo.asc())
                      .first())
    if primer_reclamo and primer_reclamo[0]:
        candidatas.append(primer_reclamo[0])
    return min(candidatas) if candidatas else datetime.now()


def main():
    db = SessionLocal()
    try:
        activos_ids = _ids_con_actividad(db)

        # Candidatos: cantidad_logins en (0, NULL) Y (tiene actividad O ya logueo alguna vez)
        candidatos = (db.query(Aliado)
                      .filter(
                          ((Aliado.cantidad_logins == 0) | (Aliado.cantidad_logins.is_(None))),
                      )
                      .all())

        a_corregir = []
        for a in candidatos:
            tiene_actividad = a.id in activos_ids
            logueo_alguna_vez = getattr(a, "ultimo_login", None) is not None
            if tiene_actividad or logueo_alguna_vez:
                a_corregir.append(a)

        modo = "APLICANDO CAMBIOS" if APPLY else "SIMULACION (dry-run, no escribe nada)"
        print(f"\n=== Backfill de activacion  [{modo}] ===")
        print(f"Aliados con falso 'sin activar' detectados: {len(a_corregir)}\n")

        if not a_corregir:
            print("No hay nada que corregir. Todos los activos ya estan marcados.")
            return

        print(f"{'ID':>5}  {'CODIGO':<10} {'NOMBRE':<28} {'EVIDENCIA':<22} ULTIMO_LOGIN")
        print("-" * 90)
        for a in a_corregir:
            evidencia = "lead/prospecto/venta" if a.id in activos_ids else "tiene ultimo_login"
            ult = getattr(a, "ultimo_login", None)
            ult_str = ult.strftime("%d/%m/%Y") if ult else "(vacio)"
            nombre = (a.nombre or "")[:27]
            print(f"{a.id:>5}  {str(a.codigo or ''):<10} {nombre:<28} {evidencia:<22} {ult_str}")

            if APPLY:
                a.cantidad_logins = 1
                if getattr(a, "ultimo_login", None) is None:
                    a.ultimo_login = _fecha_mas_temprana(db, a)

        if APPLY:
            db.commit()
            print(f"\nOK. Se marcaron {len(a_corregir)} aliados como activados.")
            print("Recorda: el job diario de activacion acreditara los bonos de 75")
            print("creditos a los sponsors correspondientes en su proxima corrida.")
        else:
            print(f"\nDRY-RUN: NO se modifico nada. {len(a_corregir)} quedarian corregidos.")
            print("Para aplicar de verdad:  python backfill_activacion.py --apply")

    except Exception as e:
        db.rollback()
        print(f"[ERROR] No se aplico ningun cambio: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
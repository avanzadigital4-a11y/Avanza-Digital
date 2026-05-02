"""seed_creditos_bienvenida.py
================================

Script idempotente que asigna 30 créditos de "bienvenida" a los aliados
que todavía no los recibieron.

Para correr una sola vez sobre los aliados existentes (los que se
registraron antes de que el endpoint /registrarse incluyera la lógica
automática de créditos de bienvenida).

Idempotencia
------------
El script busca, para cada aliado, si ya existe una `TransaccionCredito`
con motivo == "bienvenida". Si existe, lo saltea. Si no, le suma 30
créditos y registra la transacción. Eso significa que se puede correr
las veces que se quiera sin duplicar.

Uso
---
    cd avanza-portal
    python seed_creditos_bienvenida.py            # corre normal
    python seed_creditos_bienvenida.py --dry-run  # solo muestra qué haría
    python seed_creditos_bienvenida.py --monto 50 # otro monto

Salida
------
Imprime una línea por aliado (asignado / ya tenía / saltado), y al final
un resumen con el total de aliados procesados y créditos otorgados.
"""

from __future__ import annotations

import argparse
import sys

from database import SessionLocal
from models import Aliado, TransaccionCredito

MOTIVO = "bienvenida"
REFERENCIA = "backfill"
MONTO_DEFAULT = 30


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--monto",
        type=int,
        default=MONTO_DEFAULT,
        help=f"Créditos a asignar (default: {MONTO_DEFAULT}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No modifica la DB — solo lista lo que haría.",
    )
    args = parser.parse_args()

    if args.monto <= 0:
        print(f"❌ El monto debe ser positivo (recibido: {args.monto}).")
        return 2

    db = SessionLocal()
    try:
        aliados = db.query(Aliado).order_by(Aliado.id.asc()).all()
        if not aliados:
            print("ℹ️  No hay aliados en la base.")
            return 0

        asignados = 0
        ya_tenian = 0
        nuevos_creditos_total = 0

        print(f"🔍 Procesando {len(aliados)} aliados…")
        print("─" * 60)

        for a in aliados:
            ya = (
                db.query(TransaccionCredito)
                .filter(
                    TransaccionCredito.aliado_id == a.id,
                    TransaccionCredito.motivo == MOTIVO,
                )
                .first()
            )
            if ya:
                ya_tenian += 1
                print(
                    f"  ⏭️  {a.codigo} · {a.nombre[:30]:30s} "
                    f"ya tenía bienvenida (#{ya.id})"
                )
                continue

            saldo_previo = a.creditos or 0
            if not args.dry_run:
                a.creditos = saldo_previo + args.monto
                t = TransaccionCredito(
                    aliado_id=a.id,
                    delta=args.monto,
                    motivo=MOTIVO,
                    referencia=REFERENCIA,
                )
                db.add(t)

            asignados += 1
            nuevos_creditos_total += args.monto
            marca = "🧪 [dry-run]" if args.dry_run else "✅"
            print(
                f"  {marca} {a.codigo} · {a.nombre[:30]:30s} "
                f"{saldo_previo} → {saldo_previo + args.monto}"
            )

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

        print("─" * 60)
        print(
            f"📊 Total: {len(aliados)} aliados · "
            f"{asignados} asignados · {ya_tenian} ya tenían"
        )
        if asignados:
            print(
                f"   Créditos otorgados: {nuevos_creditos_total} "
                f"({args.monto} × {asignados})"
            )
        if args.dry_run:
            print("⚠️  DRY-RUN: no se modificó la base. Corré sin --dry-run para aplicar.")
        elif asignados:
            print("✨ Listo.")
        return 0
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
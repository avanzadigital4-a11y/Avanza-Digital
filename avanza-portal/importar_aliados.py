"""
importar_aliados.py — One-shot script para cargar aliados desde un CSV.

USO:
    cd avanza-portal/scripts
    python importar_aliados.py --csv aliados.csv --api https://avanza-digital.onrender.com

El CSV debe tener encabezado con las columnas:
    nombre, dni, email, whatsapp, ciudad, perfil, fecha_firma

IMPORTANTE — POR QUÉ NO HARDCODEAR DATOS PERSONALES:
La versión anterior de este archivo tenía los 15 aliados del primer batch con
nombre, DNI, email y WhatsApp escritos directamente en el código. Si el repo se
volviera público en algún momento, eso filtra datos personales de personas reales
sin su consentimiento (LPDP en Argentina, GDPR si hay alguno europeo).

Mantené el CSV fuera del repo (.gitignore ya cubre *.csv). Si lo necesitás otra
vez, generalo desde el Excel original y borralo cuando termines de importar.
"""
import argparse
import csv
import sys
import time

import requests


def importar(csv_path: str, api_base: str, admin_key: str = "", dry_run: bool = False) -> None:
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        filas = list(reader)

    print(f"Importando {len(filas)} aliados desde {csv_path} -> {api_base}")
    if dry_run:
        print("(modo --dry-run: no se mandan requests)")

    ok = 0
    fail = 0
    headers = {"Content-Type": "application/json"}
    if admin_key:
        headers["X-API-Key"] = admin_key

    for i, fila in enumerate(filas, start=1):
        payload = {
            "nombre":      (fila.get("nombre") or "").strip(),
            "dni":         (fila.get("dni") or "").strip(),
            "email":       (fila.get("email") or "").strip().lower(),
            "whatsapp":    (fila.get("whatsapp") or "").strip(),
            "ciudad":      (fila.get("ciudad") or "").strip(),
            "perfil":      (fila.get("perfil") or "").strip(),
            "fecha_firma": (fila.get("fecha_firma") or "").strip(),
        }
        if not payload["nombre"] or not payload["email"]:
            print(f"  [{i}] x fila sin nombre o email, salteo")
            fail += 1
            continue

        if dry_run:
            print(f"  [{i}] ~ {payload['nombre']} <{payload['email']}>")
            ok += 1
            continue

        try:
            r = requests.post(f"{api_base}/aliados", json=payload, headers=headers, timeout=15)
            if 200 <= r.status_code < 300:
                print(f"  [{i}] OK {payload['nombre']}")
                ok += 1
            else:
                print(f"  [{i}] x {payload['nombre']} - HTTP {r.status_code}: {r.text[:200]}")
                fail += 1
        except requests.RequestException as e:
            print(f"  [{i}] x {payload['nombre']} - error de red: {e}")
            fail += 1
        # Throttle suave para no martillar la API
        time.sleep(0.3)

    print(f"\nResumen: {ok} OK, {fail} fail.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Importar aliados desde CSV.")
    p.add_argument("--csv", required=True, help="Path al CSV con los aliados.")
    p.add_argument("--api", required=True, help="Base URL de la API (sin slash final).")
    p.add_argument("--admin-key", default="", help="ADMIN_API_KEY si el endpoint la requiere.")
    p.add_argument("--dry-run", action="store_true", help="Solo imprime, no hace requests.")
    args = p.parse_args()

    if args.api.endswith("/"):
        args.api = args.api[:-1]

    try:
        importar(args.csv, args.api, args.admin_key, args.dry_run)
    except FileNotFoundError:
        print(f"CSV no encontrado: {args.csv}", file=sys.stderr)
        sys.exit(1)
"""
test_venta_directa.py

Tests del flujo de VENTA DIRECTA (ref_code == "directo"): la rama nueva que
permite a un cliente contratar desde contratar.html sin pasar por un aliado.

Cubre:
  1. Venta directa exitosa: crea Venta con aliado_id=None, SIN Comision.
  2. Idempotencia: mismo payment_id dos veces -> una sola Venta.
  3. Plan de continuidad (mensual) -> rechazado, no soportado aún.
  4. Plan inválido -> invalid_plan, no crea nada.
  5. /checkout/manual con ref_code=directo crea un LinkPago sin aliado_id.
  6. admin_confirmar_pago_manual sobre un LinkPago de venta directa NO explota
     (antes de esta rama, lp.aliado=None rompía la función) y no genera comisión.
  7. /checkout/crear con ref_code=directo y un plan de continuidad -> 400.
  8. /checkout/crear con ref_code=directo y plan one-shot válido -> no exige aliado
     (usa el fallback porque MP_ACCESS_TOKEN está vacío en tests).
"""

import pytest

from models import Venta, Comision, LinkPago
from main import _procesar_pago_confirmado, PLANES, PLANES_CONTINUIDAD
from checkout import REF_CODE_DIRECTO

PLAN_VALIDO = list(PLANES.keys())[0]
PLAN_CONTINUIDAD = list(PLANES_CONTINUIDAD.keys())[0]


def _count_ventas_directas(db):
    return db.query(Venta).filter(Venta.aliado_id.is_(None)).count()


class TestVentaDirectaConfirmada:

    def test_venta_directa_crea_venta_sin_comision(self, db):
        resultado = _procesar_pago_confirmado(
            db,
            ref_code=REF_CODE_DIRECTO,
            plan=PLAN_VALIDO,
            nombre_cliente="Cliente Directo",
            processor="mercadopago",
            payment_id="DIRECTO-001",
        )
        assert resultado["status"] == "ok"
        assert resultado["venta_registrada"] is True
        assert resultado["comision_id"] is None
        assert resultado["comision_usd"] == 0

        venta = db.query(Venta).filter(Venta.notas.contains("[PID:DIRECTO-001]")).first()
        assert venta is not None
        assert venta.aliado_id is None
        assert venta.confirmada is True
        assert venta.pagada is True
        assert venta.comision_usd == 0

        # No debe haberse creado ninguna comisión para esta venta
        assert db.query(Comision).count() == 0

    def test_venta_directa_no_requiere_aliado_existente(self, db):
        """A diferencia del flujo normal, ref_code='directo' nunca busca un
        Aliado en la base -- no debe devolver aliado_not_found."""
        resultado = _procesar_pago_confirmado(
            db,
            ref_code=REF_CODE_DIRECTO,
            plan=PLAN_VALIDO,
            nombre_cliente="Cliente Directo",
            processor="usdt",
            payment_id="DIRECTO-002",
        )
        assert resultado["status"] == "ok"

    def test_mismo_payment_id_no_duplica_venta_directa(self, db):
        kwargs = dict(
            ref_code=REF_CODE_DIRECTO,
            plan=PLAN_VALIDO,
            nombre_cliente="Cliente Directo",
            processor="mercadopago",
            payment_id="DIRECTO-DUP",
        )
        res1 = _procesar_pago_confirmado(db, **kwargs)
        res2 = _procesar_pago_confirmado(db, **kwargs)

        assert res1["status"] == "ok"
        assert res2["status"] == "already_processed"
        assert _count_ventas_directas(db) == 1

    def test_payment_ids_distintos_crean_ventas_directas_distintas(self, db):
        _procesar_pago_confirmado(
            db, ref_code=REF_CODE_DIRECTO, plan=PLAN_VALIDO,
            nombre_cliente="Cliente A", processor="mercadopago", payment_id="DIRECTO-A",
        )
        _procesar_pago_confirmado(
            db, ref_code=REF_CODE_DIRECTO, plan=PLAN_VALIDO,
            nombre_cliente="Cliente B", processor="mercadopago", payment_id="DIRECTO-B",
        )
        assert _count_ventas_directas(db) == 2

    def test_plan_invalido_no_crea_venta_directa(self, db):
        resultado = _procesar_pago_confirmado(
            db, ref_code=REF_CODE_DIRECTO, plan="Plan Inventado XYZ",
            nombre_cliente="Alguien", processor="mercadopago", payment_id="DIRECTO-BAD",
        )
        assert resultado["status"] == "invalid_plan"
        assert _count_ventas_directas(db) == 0

    def test_plan_continuidad_no_soportado_via_procesar_pago(self, db):
        """PLANES_CONTINUIDAD no está en PLANES: la rama directa lo rechaza
        como invalid_plan (el motor de continuidad no está integrado)."""
        resultado = _procesar_pago_confirmado(
            db, ref_code=REF_CODE_DIRECTO, plan=PLAN_CONTINUIDAD,
            nombre_cliente="Alguien", processor="mercadopago", payment_id="DIRECTO-CONT",
        )
        assert resultado["status"] == "invalid_plan"
        assert _count_ventas_directas(db) == 0


class TestEndpointsVentaDirecta:

    def test_checkout_manual_directo_crea_link_sin_aliado(self, client, db):
        resp = client.post(
            "/checkout/manual",
            params={"plan": PLAN_VALIDO, "ref_code": REF_CODE_DIRECTO,
                    "nombre_cliente": "Cliente Manual", "metodo": "usdt"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reusado"] is False
        lp = db.query(LinkPago).filter(LinkPago.id == data["link_id"]).first()
        assert lp is not None
        assert lp.aliado_id is None
        assert lp.external_ref.startswith(f"{REF_CODE_DIRECTO}|{PLAN_VALIDO}|")

    def test_admin_confirmar_pago_manual_directo_no_explota(self, client, db, token_aliado=None):
        # Crear el link manual de venta directa
        resp = client.post(
            "/checkout/manual",
            params={"plan": PLAN_VALIDO, "ref_code": REF_CODE_DIRECTO,
                    "nombre_cliente": "Cliente Manual 2", "metodo": "payoneer"},
        )
        link_id = resp.json()["link_id"]

        # Confirmar como admin (antes de este fix, lp.aliado=None rompía acá)
        import main as main_mod
        app = main_mod.app
        # Reusamos la dependencia de admin ya presente en la app de test;
        # la sobreescribimos directamente para no depender de login real.
        from auth import current_admin_required
        app.dependency_overrides[current_admin_required] = lambda: object()

        resp2 = client.post(f"/admin/pagos/{link_id}/confirmar")
        app.dependency_overrides.pop(current_admin_required, None)

        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["status"] == "ok"

        lp = db.query(LinkPago).filter(LinkPago.id == link_id).first()
        assert lp.estado == "pagado"

        venta = db.query(Venta).filter(Venta.aliado_id.is_(None),
                                        Venta.notas.contains(f"[PID:manual-{link_id}]")).first()
        assert venta is not None
        # Ninguna comisión debe haberse generado a partir de este link (venta directa)
        assert db.query(Comision).filter(Comision.link_pago_id == link_id).count() == 0
        assert db.query(Comision).count() == 0

    def test_checkout_crear_directo_rechaza_plan_continuidad(self, client):
        resp = client.post(
            "/checkout/crear",
            params={"plan": PLAN_CONTINUIDAD, "ref_code": REF_CODE_DIRECTO,
                    "nombre_cliente": "Cliente", "moneda": "ars"},
        )
        assert resp.status_code == 400

    def test_checkout_crear_directo_no_exige_aliado(self, client):
        """Sin MP_ACCESS_TOKEN (así están los tests), el endpoint debe devolver
        el fallback en vez de 404 'Código de referido inválido' -- prueba de que
        ref_code=directo nunca busca un Aliado."""
        resp = client.post(
            "/checkout/crear",
            params={"plan": PLAN_VALIDO, "ref_code": REF_CODE_DIRECTO,
                    "nombre_cliente": "Cliente", "moneda": "ars"},
        )
        assert resp.status_code == 200
        assert resp.json().get("fallback") is True
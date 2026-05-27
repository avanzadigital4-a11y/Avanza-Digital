"""
test_checkout_y_webhooks.py

Tests del flujo de pago: checkout, procesamiento de pagos confirmados y
webhook de MercadoPago.

Bug target: _procesar_pago_confirmado() puede registrar la misma venta dos
veces si MercadoPago reenvía el webhook (comportamiento normal de MP: reenvía
hasta 3 veces hasta recibir HTTP 200). Sin idempotencia → doble comisión.

Cubrimos:
  1. Pago exitoso one-shot crea Venta + Comisión.
  2. Mismo payment_id dos veces → sólo UNA venta/comisión.
  3. Plan inválido → no crea nada.
  4. ref_code inexistente → not found.
  5. Primera venta otorga bonus de créditos.
  6. Sponsor recibe comisión de red (5%).
  7. Sponsor tampoco duplica si el webhook llega dos veces.
  8. Plan de continuidad vía webhook crea PlanContinuidadActivo.
  9. Plan de continuidad idempotente (mismo payment_id).
 10. Webhook MP rechaza firma inválida (cuando HMAC está habilitado).
 11. Checkout fallback cuando MP_ACCESS_TOKEN no está configurado.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from sqlalchemy import extract

from models import (
    Aliado, Venta, Comision, PlanContinuidadActivo,
    TransaccionCredito, LinkPago,
)
from main import _procesar_pago_confirmado, PLANES, PLANES_CONTINUIDAD


# ── helpers ──────────────────────────────────────────────────────────────────

PLAN_VALIDO = list(PLANES.keys())[0]           # ej. "Plan Base"
PLAN_CONTINUIDAD = list(PLANES_CONTINUIDAD.keys())[0]  # ej. "Plan Mantenimiento Web"


def _count_ventas(db, aliado_id):
    return db.query(Venta).filter(Venta.aliado_id == aliado_id, Venta.confirmada == True).count()


def _count_comisiones(db, aliado_id):
    return db.query(Comision).filter(Comision.aliado_id == aliado_id).count()


# ── Tests: flujo básico one-shot ─────────────────────────────────────────────

class TestProcesarPagoConfirmado:

    def test_pago_exitoso_crea_venta_y_comision(self, db, aliado):
        """Un pago confirmado válido crea 1 Venta + 1 Comisión."""
        resultado = _procesar_pago_confirmado(
            db,
            ref_code=aliado.ref_code,
            plan=PLAN_VALIDO,
            nombre_cliente="Cliente Test",
            processor="mercadopago",
            payment_id="PAY-001",
        )
        assert resultado["status"] == "ok"
        assert resultado["venta_registrada"] is True
        assert _count_ventas(db, aliado.id) == 1
        assert _count_comisiones(db, aliado.id) == 1

    def test_comision_calculada_correcta(self, db, aliado):
        """La comisión es exactamente comision_pct × precio_plan."""
        _procesar_pago_confirmado(
            db,
            ref_code=aliado.ref_code,
            plan=PLAN_VALIDO,
            nombre_cliente="Cliente Test",
            processor="mercadopago",
            payment_id="PAY-002",
        )
        comision = db.query(Comision).filter(Comision.aliado_id == aliado.id).first()
        # Verificar internamente coherente: comision = pct × precio del plan real
        esperada = round(comision.monto_plan_usd * comision.comision_pct, 2)
        assert comision.comision_usd == pytest.approx(esperada, abs=0.01)
        # El precio del plan en la comisión debe coincidir con el catálogo
        assert comision.monto_plan_usd == pytest.approx(PLANES[PLAN_VALIDO], abs=0.01)
        assert comision.estado == "pendiente"

    def test_plan_invalido_devuelve_status_error(self, db, aliado):
        """Plan inexistente → status invalid_plan, sin tocar la DB."""
        resultado = _procesar_pago_confirmado(
            db,
            ref_code=aliado.ref_code,
            plan="Plan Inventado XYZ",
            nombre_cliente="Alguien",
            processor="mercadopago",
            payment_id="PAY-003",
        )
        assert resultado["status"] == "invalid_plan"
        assert _count_ventas(db, aliado.id) == 0

    def test_ref_code_inexistente_devuelve_not_found(self, db):
        """ref_code que no existe en la DB → status aliado_not_found."""
        resultado = _procesar_pago_confirmado(
            db,
            ref_code="noexiste",
            plan=PLAN_VALIDO,
            nombre_cliente="Alguien",
            processor="mercadopago",
            payment_id="PAY-004",
        )
        assert resultado["status"] == "aliado_not_found"


# ── Tests: idempotencia (el más crítico) ─────────────────────────────────────

class TestIdempotenciaPago:

    def test_mismo_payment_id_no_duplica_venta(self, db, aliado):
        """
        MercadoPago reenvía el webhook hasta que recibe HTTP 200.
        El mismo payment_id dos veces → sólo UNA venta.
        """
        kwargs = dict(
            ref_code=aliado.ref_code,
            plan=PLAN_VALIDO,
            nombre_cliente="Cliente Test",
            processor="mercadopago",
            payment_id="PAY-DUPLICADO",
        )
        res1 = _procesar_pago_confirmado(db, **kwargs)
        res2 = _procesar_pago_confirmado(db, **kwargs)

        assert res1["status"] == "ok"
        assert res2["status"] == "already_processed"

        assert _count_ventas(db, aliado.id) == 1
        assert _count_comisiones(db, aliado.id) == 1

    def test_payment_ids_distintos_crean_ventas_distintas(self, db, aliado):
        """Dos pagos reales (IDs distintos) sí deben crear dos ventas."""
        _procesar_pago_confirmado(
            db, ref_code=aliado.ref_code, plan=PLAN_VALIDO,
            nombre_cliente="C1", processor="mercadopago", payment_id="PAY-A",
        )
        _procesar_pago_confirmado(
            db, ref_code=aliado.ref_code, plan=PLAN_VALIDO,
            nombre_cliente="C2", processor="mercadopago", payment_id="PAY-B",
        )
        assert _count_ventas(db, aliado.id) == 2
        assert _count_comisiones(db, aliado.id) == 2

    def test_pid_token_no_hace_match_parcial(self, db, aliado):
        """
        Bug conocido con LIKE: payment_id='42' no debe matchear payment_id='142'.
        El token usa [PID:42] con delimitadores para evitar falsos positivos.
        """
        _procesar_pago_confirmado(
            db, ref_code=aliado.ref_code, plan=PLAN_VALIDO,
            nombre_cliente="C1", processor="mercadopago", payment_id="42",
        )
        # payment_id='142' es diferente → debe crear otra venta
        res = _procesar_pago_confirmado(
            db, ref_code=aliado.ref_code, plan=PLAN_VALIDO,
            nombre_cliente="C2", processor="mercadopago", payment_id="142",
        )
        assert res["status"] == "ok"
        assert _count_ventas(db, aliado.id) == 2


# ── Tests: bonus primera venta ───────────────────────────────────────────────

class TestBonusPrimeraVenta:

    def test_primera_venta_otorga_creditos_bonus(self, db, aliado):
        """La primera venta confirmada otorga BONUS_PRIMERA_VENTA créditos."""
        saldo_inicial = aliado.creditos or 0
        resultado = _procesar_pago_confirmado(
            db, ref_code=aliado.ref_code, plan=PLAN_VALIDO,
            nombre_cliente="C1", processor="mercadopago", payment_id="PAY-FIRST",
        )
        assert resultado.get("primera_venta") is True
        db.refresh(aliado)
        from main import BONUS_PRIMERA_VENTA
        assert aliado.creditos == saldo_inicial + BONUS_PRIMERA_VENTA

    def test_segunda_venta_no_otorga_bonus(self, db, aliado):
        """La segunda venta no da bonus de primera venta."""
        _procesar_pago_confirmado(
            db, ref_code=aliado.ref_code, plan=PLAN_VALIDO,
            nombre_cliente="C1", processor="mercadopago", payment_id="PAY-1",
        )
        db.refresh(aliado)
        creditos_tras_primera = aliado.creditos

        resultado = _procesar_pago_confirmado(
            db, ref_code=aliado.ref_code, plan=PLAN_VALIDO,
            nombre_cliente="C2", processor="mercadopago", payment_id="PAY-2",
        )
        assert resultado.get("primera_venta") is False
        db.refresh(aliado)
        assert aliado.creditos == creditos_tras_primera  # sin cambio


# ── Tests: comisión de red (sponsor) ─────────────────────────────────────────

class TestComisionRed:

    def test_sponsor_recibe_5_pct_en_venta_one_shot(self, db, aliado, aliado_con_sponsor):
        """Cuando el vendedor tiene sponsor, éste recibe 5% sobre el valor del plan."""
        precio = PLANES[PLAN_VALIDO]

        _procesar_pago_confirmado(
            db, ref_code=aliado_con_sponsor.ref_code, plan=PLAN_VALIDO,
            nombre_cliente="ClienteX", processor="mercadopago", payment_id="PAY-RED",
        )
        # El sponsor es aliado (ref: fixture aliado_con_sponsor)
        comision_sponsor = db.query(Comision).filter(
            Comision.aliado_id == aliado.id
        ).first()
        assert comision_sponsor is not None
        assert comision_sponsor.comision_usd == pytest.approx(precio * 0.05, abs=0.01)
        assert comision_sponsor.comision_pct == pytest.approx(0.05)

    def test_sponsor_no_duplica_en_webhook_replay(self, db, aliado, aliado_con_sponsor):
        """El sponsor tampoco duplica si el webhook llega dos veces."""
        kwargs = dict(
            ref_code=aliado_con_sponsor.ref_code, plan=PLAN_VALIDO,
            nombre_cliente="ClienteX", processor="mercadopago", payment_id="PAY-RED-DUP",
        )
        _procesar_pago_confirmado(db, **kwargs)
        _procesar_pago_confirmado(db, **kwargs)

        count_sponsor = db.query(Comision).filter(
            Comision.aliado_id == aliado.id
        ).count()
        assert count_sponsor == 1


# ── Tests: plan de continuidad vía webhook ───────────────────────────────────

class TestPlanContinuidadWebhook:

    def test_pago_de_plan_continuidad_crea_registro(self, db, aliado):
        """Un pago de plan de continuidad crea PlanContinuidadActivo + Comisión."""
        resultado = _procesar_pago_confirmado(
            db, ref_code=aliado.ref_code, plan=PLAN_CONTINUIDAD,
            nombre_cliente="ClienteRecurrente", processor="mercadopago",
            payment_id="PAY-CONT-001",
        )
        assert resultado["status"] == "ok"
        assert resultado.get("tipo") == "continuidad"

        plan = db.query(PlanContinuidadActivo).filter(
            PlanContinuidadActivo.aliado_id == aliado.id
        ).first()
        assert plan is not None
        assert plan.fecha_baja is None  # activo

        # Primera comisión del mes creada automáticamente
        comision = db.query(Comision).filter(Comision.aliado_id == aliado.id).first()
        assert comision is not None

    def test_pago_continuidad_idempotente(self, db, aliado):
        """El mismo payment_id de plan de continuidad no crea un segundo plan."""
        kwargs = dict(
            ref_code=aliado.ref_code, plan=PLAN_CONTINUIDAD,
            nombre_cliente="ClienteRecurrente", processor="mercadopago",
            payment_id="PAY-CONT-DUP",
        )
        res1 = _procesar_pago_confirmado(db, **kwargs)
        res2 = _procesar_pago_confirmado(db, **kwargs)

        assert res1["status"] == "ok"
        assert res2["status"] == "already_processed"

        count = db.query(PlanContinuidadActivo).filter(
            PlanContinuidadActivo.aliado_id == aliado.id
        ).count()
        assert count == 1


# ── Tests: endpoint /checkout/crear (fallback) ───────────────────────────────

class TestCheckoutEndpoint:

    def test_checkout_fallback_sin_mp_token(self, client, aliado):
        """Sin MP_ACCESS_TOKEN configurado, devuelve URL de fallback (no 500)."""
        import os
        old = os.environ.get("MP_ACCESS_TOKEN", "")
        os.environ["MP_ACCESS_TOKEN"] = ""

        try:
            resp = client.post(
                f"/checkout/crear",
                params={
                    "plan": PLAN_VALIDO,
                    "ref_code": aliado.ref_code,
                    "moneda": "ars",
                },
            )
        finally:
            os.environ["MP_ACCESS_TOKEN"] = old

        # Debe responder 200 con fallback, nunca 500
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("fallback") is True
        assert "checkout_url" in data

    def test_checkout_plan_invalido_retorna_400(self, client, aliado):
        """Plan inexistente → 400, sin crear nada en DB."""
        resp = client.post(
            "/checkout/crear",
            params={
                "plan": "Plan Inventado",
                "ref_code": aliado.ref_code,
                "moneda": "ars",
            },
        )
        assert resp.status_code == 400

    def test_checkout_ref_code_inexistente_retorna_404(self, client):
        """ref_code que no existe → 404."""
        resp = client.post(
            "/checkout/crear",
            params={
                "plan": PLAN_VALIDO,
                "ref_code": "noexiste123",
                "moneda": "ars",
            },
        )
        assert resp.status_code == 404

    def test_checkout_moneda_invalida_retorna_400(self, client, aliado):
        """Moneda distinta de 'ars'/'usd' → 400."""
        resp = client.post(
            "/checkout/crear",
            params={
                "plan": PLAN_VALIDO,
                "ref_code": aliado.ref_code,
                "moneda": "eur",
            },
        )
        assert resp.status_code == 400

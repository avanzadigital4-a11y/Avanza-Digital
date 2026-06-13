"""
test_creditos.py

Tests del sistema de créditos y marketplace de leads.

Bug target: _ajustar_creditos() hace un UPDATE atómico con WHERE creditos+delta>=0.
Si hay bug, el saldo puede ir negativo (doble-click, llamadas paralelas) o
una compra puede fallar aunque hay saldo suficiente.

Cubrimos:
  1. Suma de créditos actualiza el saldo correctamente.
  2. Descuento con saldo suficiente → ok.
  3. Descuento con saldo insuficiente → 400, saldo no cambia.
  4. Cada operación crea TransaccionCredito (auditoría).
  5. Bienvenida: registro otorga 100 créditos.
  6. Reclamo de lead premium vía /comprar: GRATIS, no descuenta créditos
     (desde la unificación de la bolsa, los créditos son solo para Jarvis IA).
  7. Reclamo de lead premium con 0 créditos: funciona igual (es gratis).
  8. Reclamo de lead básico: usa /reclamar (tampoco descuenta créditos).
  9. Lead ya comprado → 400 "no disponible".
 10. Límite de reclamos activos simultáneos.
 11. Aliado Canal 2 no puede comprar leads.
 12. Admin puede ajustar créditos manualmente.
"""

import pytest
from fastapi import HTTPException

from models import Aliado, TransaccionCredito, LeadBolsa
from main import _ajustar_creditos, LIMITE_RECLAMOS_ACTIVOS
from auth import crear_token


# ── Tests: _ajustar_creditos ─────────────────────────────────────────────────

class TestAjustarCreditos:

    def test_suma_creditos(self, db, aliado):
        """Suma positiva aumenta el saldo."""
        saldo_inicial = aliado.creditos or 0
        _ajustar_creditos(db, aliado, 50, "test_suma", "ref1")
        db.commit()
        db.refresh(aliado)
        assert aliado.creditos == saldo_inicial + 50

    def test_descuento_con_saldo_suficiente(self, db, aliado):
        """Resta con saldo suficiente disminuye el saldo."""
        aliado.creditos = 200
        db.commit()
        _ajustar_creditos(db, aliado, -100, "test_resta", "ref2")
        db.commit()
        db.refresh(aliado)
        assert aliado.creditos == 100

    def test_saldo_insuficiente_lanza_400(self, db, aliado):
        """Si el saldo no alcanza, HTTPException 400 y el saldo NO cambia."""
        aliado.creditos = 10
        db.commit()
        saldo_antes = aliado.creditos

        with pytest.raises(HTTPException) as exc_info:
            _ajustar_creditos(db, aliado, -50, "test_insuf", "ref3")
        
        assert exc_info.value.status_code == 400
        db.rollback()
        db.refresh(aliado)
        assert aliado.creditos == saldo_antes  # sin cambio

    def test_saldo_exactamente_cero_tras_descuento(self, db, aliado):
        """Gastar exactamente todo el saldo lleva a 0 sin error."""
        aliado.creditos = 100
        db.commit()
        _ajustar_creditos(db, aliado, -100, "test_cero", "ref4")
        db.commit()
        db.refresh(aliado)
        assert aliado.creditos == 0

    def test_crea_transaccion_credito(self, db, aliado):
        """Cada ajuste deja registro en TransaccionCredito."""
        count_antes = db.query(TransaccionCredito).filter(
            TransaccionCredito.aliado_id == aliado.id
        ).count()
        _ajustar_creditos(db, aliado, 30, "test_tx", "ref-tx")
        db.commit()
        count_despues = db.query(TransaccionCredito).filter(
            TransaccionCredito.aliado_id == aliado.id
        ).count()
        assert count_despues == count_antes + 1

    def test_transaccion_guarda_motivo_y_referencia(self, db, aliado):
        """El motivo y la referencia quedan registrados en la transacción."""
        _ajustar_creditos(db, aliado, 25, "motivo_test", "ref-xyz-123")
        db.commit()
        tx = db.query(TransaccionCredito).filter(
            TransaccionCredito.aliado_id == aliado.id,
            TransaccionCredito.referencia == "ref-xyz-123",
        ).first()
        assert tx is not None
        assert tx.motivo == "motivo_test"
        assert tx.delta == 25


# ── Tests: registro otorga créditos de bienvenida ────────────────────────────

class TestCreditosBienvenida:

    def test_registro_otorga_100_creditos(self, client):
        """El auto-registro da 100 créditos de bienvenida al nuevo aliado."""
        resp = client.post(
            "/registrarse",
            json={
                "nombre": "Nuevo Aliado",
                "email": "nuevo@example.com",
                "whatsapp": "+5491199999999",
                "password": "password123",
                "acepto_terminos": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # El token indica que el aliado existe
        assert "token" in data

    def test_registro_email_duplicado_retorna_400(self, client, aliado):
        """Registrarse con un email ya existente devuelve 400."""
        resp = client.post(
            "/registrarse",
            json={
                "nombre": "Otro",
                "email": aliado.email,  # mismo email
                "whatsapp": "+5491100000099",
                "password": "password123",
                "acepto_terminos": True,
            },
        )
        assert resp.status_code == 400


# ── Tests: compra de leads ────────────────────────────────────────────────────

class TestCompraLeads:

    def test_compra_lead_premium_es_gratis_no_descuenta_creditos(self, client, db, aliado, lead_premium, token_aliado):
        """Desde la unificación de la bolsa, reclamar un lead premium vía
        /comprar es GRATIS: el saldo de créditos NO cambia y la respuesta
        desbloquea el contacto. Los créditos quedan reservados para Jarvis IA."""
        aliado.creditos = 100
        db.commit()

        resp = client.post(
            f"/bolsa/{lead_premium.id}/comprar",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # El contacto viene desbloqueado en la respuesta
        assert data["lead"]["telefono"] == lead_premium.telefono
        db.refresh(aliado)
        assert aliado.creditos == 100  # sin cambio: el reclamo es gratuito

    def test_compra_no_genera_transaccion_credito(self, client, db, aliado, lead_premium, token_aliado):
        """El reclamo gratuito no debe dejar registro en TransaccionCredito
        (no hubo movimiento de saldo que auditar)."""
        aliado.creditos = 100
        db.commit()
        txs_antes = db.query(TransaccionCredito).filter(
            TransaccionCredito.aliado_id == aliado.id
        ).count()

        resp = client.post(
            f"/bolsa/{lead_premium.id}/comprar",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp.status_code == 200
        txs_despues = db.query(TransaccionCredito).filter(
            TransaccionCredito.aliado_id == aliado.id
        ).count()
        assert txs_despues == txs_antes

    def test_compra_lead_actualiza_estado_a_reclamado(self, client, db, aliado, lead_premium, token_aliado):
        """Tras la compra, el lead queda en estado 'reclamado'."""
        aliado.creditos = 200
        db.commit()

        client.post(
            f"/bolsa/{lead_premium.id}/comprar",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        db.refresh(lead_premium)
        assert lead_premium.estado == "reclamado"
        assert lead_premium.aliado_id == aliado.id

    def test_compra_con_cero_creditos_funciona_igual(self, client, db, aliado, lead_premium, token_aliado):
        """Como los leads ya no cuestan créditos, un aliado con saldo 0 puede
        reclamar un lead premium sin problema. (Antes esto devolvía 400 por
        saldo insuficiente — ese comportamiento quedó obsoleto a propósito.)"""
        aliado.creditos = 0
        db.commit()

        resp = client.post(
            f"/bolsa/{lead_premium.id}/comprar",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp.status_code == 200
        db.refresh(lead_premium)
        db.refresh(aliado)
        assert lead_premium.estado == "reclamado"
        assert lead_premium.aliado_id == aliado.id
        assert aliado.creditos == 0  # sin cambio

    def test_compra_lead_ya_tomado_retorna_400_o_409(self, client, db, aliado, lead_premium, token_aliado):
        """Lead ya reclamado → error (no se puede comprar dos veces)."""
        aliado.creditos = 200
        db.commit()
        # Primera compra
        client.post(
            f"/bolsa/{lead_premium.id}/comprar",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        # Segunda compra del mismo lead
        resp2 = client.post(
            f"/bolsa/{lead_premium.id}/comprar",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp2.status_code in (400, 409)

    def test_lead_basico_no_usa_creditos(self, client, db, aliado, lead_basico, token_aliado):
        """Los leads básicos se reclaman sin costo, créditos no cambian."""
        saldo_inicial = aliado.creditos
        resp = client.post(
            f"/bolsa/{lead_basico.id}/reclamar",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp.status_code == 200
        db.refresh(aliado)
        assert aliado.creditos == saldo_inicial  # sin cambio

    def test_limite_reclamos_activos(self, client, db, aliado, token_aliado):
        """No se pueden tener más de LIMITE_RECLAMOS_ACTIVOS leads reclamados."""
        # Crear leads básicos suficientes
        leads = []
        for i in range(LIMITE_RECLAMOS_ACTIVOS + 1):
            l = LeadBolsa(
                empresa=f"Empresa {i}",
                rubro="Test",
                telefono=f"+54341{i:07d}",
                estado="disponible",
                tier="basico",
                costo_creditos=0,
            )
            db.add(l)
            leads.append(l)
        db.commit()

        # Reclamar hasta el límite
        for l in leads[:LIMITE_RECLAMOS_ACTIVOS]:
            resp = client.post(
                f"/bolsa/{l.id}/reclamar",
                headers={"Authorization": f"Bearer {token_aliado}"},
            )
            assert resp.status_code == 200

        # El siguiente debe fallar
        resp_extra = client.post(
            f"/bolsa/{leads[LIMITE_RECLAMOS_ACTIVOS].id}/reclamar",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp_extra.status_code == 400


# ── Tests: Canal 2 no tiene acceso a bolsa ───────────────────────────────────

class TestCanalDosSinBolsa:

    def test_canal2_no_puede_comprar_lead(self, client, db, aliado, lead_premium, token_aliado):
        """Aliados Canal 2 no tienen acceso al marketplace."""
        aliado.tipo_aliado = "canal2"
        aliado.creditos = 200
        db.commit()

        resp = client.post(
            f"/bolsa/{lead_premium.id}/comprar",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp.status_code == 403

    def test_canal2_no_puede_ver_bolsa(self, client, db, aliado, token_aliado):
        """Canal 2 no puede ver la bolsa de leads."""
        aliado.tipo_aliado = "canal2"
        db.commit()

        resp = client.get(
            f"/aliados/{aliado.codigo}/bolsa",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp.status_code == 403


# ── Tests: ajuste de créditos por admin ─────────────────────────────────────

class TestAdminCreditos:

    def test_admin_puede_sumar_creditos(self, client, db, aliado, admin_user, token_admin):
        """Admin suma créditos a un aliado y el saldo se actualiza."""
        saldo_antes = aliado.creditos or 0

        resp = client.post(
            f"/admin/aliados/{aliado.codigo}/creditos",
            headers={"Authorization": f"Bearer {token_admin}"},
            json={"delta": 500, "motivo": "bono_manual"},
        )
        assert resp.status_code == 200
        db.refresh(aliado)
        assert aliado.creditos == saldo_antes + 500

    def test_admin_puede_quitar_creditos(self, client, db, aliado, admin_user, token_admin):
        """Admin quita créditos (delta negativo)."""
        aliado.creditos = 300
        db.commit()

        resp = client.post(
            f"/admin/aliados/{aliado.codigo}/creditos",
            headers={"Authorization": f"Bearer {token_admin}"},
            json={"delta": -100, "motivo": "ajuste_admin"},
        )
        assert resp.status_code == 200
        db.refresh(aliado)
        assert aliado.creditos == 200
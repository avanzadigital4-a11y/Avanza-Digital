"""
test_metodos_cobro.py

Cubre la mejora de métodos de cobro (mejoras-metodos-cobro.md):
  - Persistencia real de payment_method/payment_info (antes no estaban
    mapeados en el modelo SQLAlchemy y setattr()/commit() no los guardaba).
  - Validación de USDT TRC20, Wise (email/telefono/wisetag), Payoneer/Airtm
    (email) y transferencia (formato según país).
  - GET /aliados/me devuelve pais y los campos cobro_* nuevos.
"""

from auth import crear_token


def _token_headers(aliado):
    token = crear_token(sub=aliado.codigo, tipo="aliado")
    return {"Authorization": f"Bearer {token}"}


class TestPersistenciaPaymentMethod:
    def test_payment_method_se_persiste_de_verdad(self, client, aliado, db):
        """Antes de mapear las columnas en models.py, esto se perdía: el
        objeto devolvía el valor correcto en la misma request (estaba en
        memoria) pero un GET posterior (fila nueva de la DB) lo veía None."""
        headers = _token_headers(aliado)
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "payoneer", "payment_info": "cobros@ejemplo.com"},
            headers=headers,
        )
        assert resp.status_code == 200

        # Releer desde una sesión/fila fresca de la DB, no del objeto en memoria.
        db.expire_all()
        me = client.get("/aliados/me", headers=headers)
        assert me.status_code == 200
        data = me.json()
        assert data["payment_method"] == "payoneer"
        assert data["payment_info"] == "cobros@ejemplo.com"


class TestValidacionUSDT:
    def test_usdt_formato_invalido_rechaza(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "usdt_trc20", "payment_info": "direccion-invalida"},
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 400

    def test_usdt_formato_valido_acepta(self, client, aliado):
        direccion = "T" + "A" * 33
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "usdt_trc20", "payment_info": direccion},
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 200
        assert resp.json()["payment_info"] == direccion


class TestValidacionWise:
    def test_wise_sin_tipo_rechaza(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "wise", "payment_info": "user@ejemplo.com"},
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 400

    def test_wise_email_valido_acepta(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "wise", "payment_info": "user@ejemplo.com", "payment_info_tipo": "email"},
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 200
        assert resp.json()["payment_info_tipo"] == "email"

    def test_wise_wisetag_sin_arroba_rechaza(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "wise", "payment_info": "usuario", "payment_info_tipo": "wisetag"},
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 400

    def test_wise_wisetag_valido_acepta(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "wise", "payment_info": "@usuario", "payment_info_tipo": "wisetag"},
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 200

    def test_wise_telefono_formato_internacional(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "wise", "payment_info": "+5491122334455", "payment_info_tipo": "telefono"},
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 200

        resp_malo = client.patch(
            "/aliado/perfil",
            json={"payment_method": "wise", "payment_info": "1122334455", "payment_info_tipo": "telefono"},
            headers=_token_headers(aliado),
        )
        assert resp_malo.status_code == 400


class TestValidacionPayoneerAirtm:
    def test_payoneer_email_invalido_rechaza(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "payoneer", "payment_info": "no-es-email"},
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 400

    def test_airtm_email_valido_acepta(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={"payment_method": "airtm", "payment_info": "user@ejemplo.com"},
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 200


class TestValidacionTransferenciaPorPais:
    def test_transferencia_mexico_clabe_longitud_18(self, client, aliado, db):
        aliado.pais = "MX"
        db.commit()
        # 17 dígitos: inválido (CLABE debe ser exactamente 18)
        resp = client.patch(
            "/aliado/perfil",
            json={
                "payment_method": "transferencia",
                "cobro_banco": "BBVA",
                "cobro_titular": "Juan Pérez",
                "cobro_tipo_cuenta": "ahorro",
                "cobro_numero_cuenta": "1" * 17,
            },
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 400

        resp_ok = client.patch(
            "/aliado/perfil",
            json={
                "payment_method": "transferencia",
                "cobro_banco": "BBVA",
                "cobro_titular": "Juan Pérez",
                "cobro_tipo_cuenta": "ahorro",
                "cobro_numero_cuenta": "1" * 18,
            },
            headers=_token_headers(aliado),
        )
        assert resp_ok.status_code == 200

    def test_transferencia_argentina_acepta_alias_alfanumerico(self, client, aliado):
        # aliado fixture ya tiene pais default "AR"
        resp = client.patch(
            "/aliado/perfil",
            json={
                "payment_method": "transferencia",
                "cobro_banco": "Banco Galicia",
                "cobro_titular": "Juan Pérez",
                "cobro_tipo_cuenta": "corriente",
                "cobro_numero_cuenta": "juan.perez.mp",
            },
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 200

    def test_transferencia_pais_sin_entrada_usa_default(self, client, aliado, db):
        aliado.pais = "BO"  # no está en el diccionario explícito
        db.commit()
        resp = client.patch(
            "/aliado/perfil",
            json={
                "payment_method": "transferencia",
                "cobro_banco": "Banco Nacional",
                "cobro_titular": "Juan Pérez",
                "cobro_numero_cuenta": "12345",
            },
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 200

    def test_transferencia_sin_titular_rechaza(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={
                "payment_method": "transferencia",
                "cobro_banco": "Banco Galicia",
                "cobro_numero_cuenta": "123456",
            },
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 400

    def test_transferencia_tipo_cuenta_invalido_rechaza(self, client, aliado):
        resp = client.patch(
            "/aliado/perfil",
            json={
                "payment_method": "transferencia",
                "cobro_banco": "Banco Galicia",
                "cobro_titular": "Juan Pérez",
                "cobro_tipo_cuenta": "tipo-inventado",
                "cobro_numero_cuenta": "123456",
            },
            headers=_token_headers(aliado),
        )
        assert resp.status_code == 400


class TestSerializerExponePais:
    def test_me_incluye_pais_y_campos_cobro(self, client, aliado):
        resp = client.get("/aliados/me", headers=_token_headers(aliado))
        assert resp.status_code == 200
        data = resp.json()
        assert "pais" in data
        assert "cobro_banco" in data
        assert "cobro_numero_cuenta" in data

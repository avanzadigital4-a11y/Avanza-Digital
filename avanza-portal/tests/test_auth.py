"""
test_auth.py

Tests de autenticación, login y protección de endpoints.

Cubrimos:
  1. Login exitoso con código.
  2. Login exitoso con email.
  3. Contraseña incorrecta → 401.
  4. Aliado inexistente → 401 (misma respuesta, no leak).
  5. Aliado inactivo no puede loguearse.
  6. Token válido accede a /aliados/me.
  7. Sin token → 401/403 en endpoints protegidos.
  8. Token de aliado A no accede a datos de aliado B.
  9. Admin login exitoso.
 10. Endpoint admin rechaza token de aliado.
"""

import pytest
from auth import crear_token


class TestLoginAliado:

    def test_login_exitoso_con_codigo(self, client, aliado):
        resp = client.post(
            "/aliados/login",
            json={"codigo": aliado.codigo, "password": "password123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["codigo"] == aliado.codigo

    def test_login_exitoso_con_email(self, client, aliado):
        resp = client.post(
            "/aliados/login",
            json={"codigo": aliado.email, "password": "password123"},
        )
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_password_incorrecto_retorna_401(self, client, aliado):
        resp = client.post(
            "/aliados/login",
            json={"codigo": aliado.codigo, "password": "wrongpassword"},
        )
        assert resp.status_code == 401

    def test_codigo_inexistente_retorna_401(self, client):
        """No debe leakear si el código existe o no."""
        resp = client.post(
            "/aliados/login",
            json={"codigo": "AL-NOEXISTE", "password": "cualquiera"},
        )
        assert resp.status_code == 401

    def test_aliado_inactivo_no_puede_loguearse(self, client, db, aliado):
        aliado.activo = False
        db.commit()

        resp = client.post(
            "/aliados/login",
            json={"codigo": aliado.codigo, "password": "password123"},
        )
        assert resp.status_code == 401

    def test_login_actualiza_ultimo_login(self, client, db, aliado):
        assert aliado.ultimo_login is None  # nunca entró
        client.post(
            "/aliados/login",
            json={"codigo": aliado.codigo, "password": "password123"},
        )
        db.refresh(aliado)
        assert aliado.ultimo_login is not None


class TestProteccionEndpoints:

    def test_me_con_token_valido(self, client, aliado, token_aliado):
        resp = client.get(
            "/aliados/me",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp.status_code == 200
        assert resp.json()["codigo"] == aliado.codigo

    def test_me_sin_token_retorna_401_o_403(self, client):
        resp = client.get("/aliados/me")
        assert resp.status_code in (401, 403)

    def test_token_aliado_a_no_accede_a_aliado_b(self, client, db, aliado, aliado_con_sponsor):
        """Token del sub-aliado no puede ver datos del aliado principal."""
        token_sub = crear_token(sub=aliado_con_sponsor.codigo, tipo="aliado")

        resp = client.get(
            f"/aliados/{aliado.codigo}",
            headers={"Authorization": f"Bearer {token_sub}"},
        )
        # Debe ser 403 (no es el dueño) — no 200
        assert resp.status_code in (403, 404)

    def test_endpoint_admin_rechaza_token_aliado(self, client, aliado, token_aliado):
        """Un JWT de aliado no puede acceder a endpoints admin."""
        resp = client.get(
            "/aliados",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp.status_code in (401, 403)

    def test_dashboard_requiere_admin(self, client, token_aliado):
        resp = client.get(
            "/dashboard",
            headers={"Authorization": f"Bearer {token_aliado}"},
        )
        assert resp.status_code in (401, 403)


class TestLoginAdmin:

    def test_admin_login_exitoso(self, client, admin_user):
        resp = client.post(
            "/admin/login",
            json={"username": "admin-test", "password": "adminpass"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["tipo"] == "admin"

    def test_admin_credenciales_incorrectas(self, client, admin_user):
        resp = client.post(
            "/admin/login",
            json={"username": "admin-test", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_admin_token_accede_a_dashboard(self, client, admin_user, token_admin):
        resp = client.get(
            "/dashboard",
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert resp.status_code == 200

    def test_admin_token_accede_a_lista_aliados(self, client, admin_user, token_admin):
        resp = client.get(
            "/aliados",
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestRecuperacionPassword:

    def test_recuperar_password_email_valido(self, client, aliado):
        """Siempre 200 aunque exista o no el email (anti-enumeración)."""
        resp = client.post(
            "/auth/recuperar",
            json={"email": aliado.email},
        )
        assert resp.status_code == 200

    def test_recuperar_password_email_inexistente(self, client):
        """Email inexistente → 200 igual (no revelar si existe)."""
        resp = client.post(
            "/auth/recuperar",
            json={"email": "noexiste@nowhere.com"},
        )
        assert resp.status_code == 200

    def test_resetear_token_invalido_retorna_400(self, client):
        resp = client.post(
            "/auth/resetear",
            json={"token": "tokenfalso", "nueva_password": "nueva123"},
        )
        assert resp.status_code == 400

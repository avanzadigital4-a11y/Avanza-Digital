"""
test_tron_xpub.py — Tests de la derivación de direcciones TRON por xpub.

Los vectores están CONGELADOS: provienen de la mnemónica de prueba estándar
BIP39 ("abandon abandon ... about" — pública y sin fondos, nunca usarla con
plata real) derivada por el camino legacy (mnemónica → privkey → dirección)
del propio main.py. Si alguna vez una modificación de tron_xpub.py hace que
las direcciones dejen de coincidir con este vector, estos tests fallan ANTES
de que un link de pago apunte a una dirección equivocada.
"""
import pytest

import tron_xpub

# xpub de la cuenta m/44'/195'/0' de la mnemónica de prueba BIP39 estándar.
XPUB_TEST = ("xpub6D1AabNHCupeiLM65ZR9UStMhJ1vCpyV4XbZdyhMZBiJXALQtmn9p42"
             "VTQckoHVn8WNqS7dqnJokZHAHcHGoaQgmv8D45oNUKx6DZMNZBCd")

# (índice del link de pago, dirección esperada en m/44'/195'/0'/0/{índice})
# Verificadas contra la derivación legacy por mnemónica de main.py.
VECTORES = [
    (0,    "TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH"),
    (1,    "TSeJkUh4Qv67VNFwY8LaAxERygNdy6NQZK"),
    (2,    "TYJPRrdB5APNeRs4R7fYZSwW3TcrTKw2gx"),
    (42,   "TEnzFm6jmsVnizS7RSuBr7H6zzn4e7H7Pb"),
    (9999, "TT2ABWoi5YLmcP9JGtyr6CrDdbTbxRZHEY"),
]


class TestDireccionDesdeXpub:

    @pytest.mark.parametrize("indice,esperada", VECTORES)
    def test_deriva_la_misma_direccion_que_el_camino_legacy(self, indice, esperada):
        assert tron_xpub.direccion_desde_xpub(XPUB_TEST, indice) == esperada

    def test_indice_negativo_rechazado(self):
        with pytest.raises(ValueError):
            tron_xpub.direccion_desde_xpub(XPUB_TEST, -1)

    def test_indice_hardened_rechazado(self):
        with pytest.raises(ValueError):
            tron_xpub.direccion_desde_xpub(XPUB_TEST, 0x80000000)


class TestValidacionXpub:

    def test_xpub_valida(self):
        assert tron_xpub.validar_xpub(XPUB_TEST) is True

    def test_xpub_corrupta_rechazada(self):
        rota = XPUB_TEST[:-1] + ("2" if XPUB_TEST[-1] != "2" else "3")
        assert tron_xpub.validar_xpub(rota) is False

    def test_basura_rechazada(self):
        assert tron_xpub.validar_xpub("no-soy-una-xpub") is False
        assert tron_xpub.validar_xpub("") is False
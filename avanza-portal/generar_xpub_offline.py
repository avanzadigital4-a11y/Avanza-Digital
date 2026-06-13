"""
generar_xpub_offline.py — Herramienta OFFLINE para generar la TRON_XPUB
=======================================================================

⚠️  CORRER SOLO EN TU MÁQUINA LOCAL, NUNCA EN EL SERVIDOR.
    Idealmente con el WiFi apagado mientras pegás la mnemónica.

QUÉ HACE:
  1. Te pide la mnemónica de la wallet TRON (no se guarda en ningún lado,
     no queda en el historial de la terminal porque se ingresa oculta).
  2. Deriva la cuenta m/44'/195'/0' (el mismo path que usa el portal).
  3. Imprime la TRON_XPUB para pegar en las variables de entorno del server.
  4. Imprime las primeras 3 direcciones derivadas para que verifiques contra
     tu wallet que todo coincide ANTES de borrar TRON_MNEMONIC del server.

DESPUÉS DE CORRERLO:
  - En Render/Railway: agregar TRON_XPUB=<valor impreso>.
  - Verificar que un link de pago USDT nuevo genera una dirección de tu wallet.
  - Recién entonces, BORRAR la variable TRON_MNEMONIC del servidor.

USO:
  pip install mnemonic ecdsa tronpy   (si no las tenés localmente)
  python generar_xpub_offline.py
"""
import getpass
import hashlib
import hmac
import struct
import sys

try:
    import ecdsa
    from mnemonic import Mnemonic
except ImportError:
    print("Faltan dependencias. Instalá con: pip install mnemonic ecdsa tronpy")
    sys.exit(1)

from tron_xpub import b58encode_check, direccion_desde_xpub

_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def _pub_comprimida(priv32: bytes) -> bytes:
    sk = ecdsa.SigningKey.from_string(priv32, curve=ecdsa.SECP256k1)
    p = sk.get_verifying_key().pubkey.point
    return (b"\x02" if p.y() % 2 == 0 else b"\x03") + p.x().to_bytes(32, "big")


def _ckd_priv(key: bytes, chain: bytes, indice: int, hardened: bool):
    i = indice + (0x80000000 if hardened else 0)
    data = (b"\x00" + key if hardened else _pub_comprimida(key)) + struct.pack(">I", i)
    I = hmac.new(chain, data, hashlib.sha512).digest()
    hijo = (int.from_bytes(I[:32], "big") + int.from_bytes(key, "big")) % _ORDER
    return hijo.to_bytes(32, "big"), I[32:], i


def main():
    print("=" * 64)
    print(" GENERADOR DE TRON_XPUB — correr OFFLINE, nunca en el servidor")
    print("=" * 64)
    frase = getpass.getpass("Pegá la mnemónica (no se muestra al tipear): ").strip()
    palabras = frase.split()
    if len(palabras) not in (12, 15, 18, 21, 24):
        print(f"❌ La mnemónica tiene {len(palabras)} palabras — esperadas 12/15/18/21/24.")
        sys.exit(1)
    if not Mnemonic("english").check(frase):
        print("❌ Mnemónica inválida (checksum BIP39 no cierra). Revisá las palabras.")
        sys.exit(1)

    # Master key desde la semilla (BIP32)
    seed = Mnemonic("english").to_seed(frase)
    I = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    key, chain = I[:32], I[32:]

    # Derivar hasta el nivel de cuenta: m/44'/195'/0'
    fingerprint_padre = b"\x00\x00\x00\x00"
    child_number = 0
    for idx, hard in [(44, True), (195, True), (0, True)]:
        # fingerprint del padre = primeros 4 bytes de hash160 de su pubkey
        pub_padre = _pub_comprimida(key)
        h160 = hashlib.new("ripemd160", hashlib.sha256(pub_padre).digest()).digest()
        fingerprint_padre = h160[:4]
        key, chain, child_number = _ckd_priv(key, chain, idx, hard)

    # Serializar la XPUB de la cuenta (versión estándar 0x0488B21E, depth 3)
    pub_cuenta = _pub_comprimida(key)
    payload = (
        bytes.fromhex("0488B21E")          # versión xpub
        + bytes([3])                        # depth: m/44'/195'/0' = 3 niveles
        + fingerprint_padre                 # fingerprint del padre (m/44'/195')
        + struct.pack(">I", child_number)   # child number (0' = 0x80000000)
        + chain
        + pub_cuenta
    )
    xpub = b58encode_check(payload)

    print("\n✅ XPUB generada. Pegá esto en las env vars del servidor:\n")
    print(f"TRON_XPUB={xpub}\n")
    print("Direcciones de verificación (compará con tu wallet, path m/44'/195'/0'/0/i):")
    for i in range(3):
        print(f"  índice {i}: {direccion_desde_xpub(xpub, i)}")
    print("\nSi coinciden con tu wallet:")
    print("  1. Agregá TRON_XPUB al servidor.")
    print("  2. Generá un link USDT de prueba y verificá la dirección.")
    print("  3. BORRÁ TRON_MNEMONIC del servidor. La semilla queda solo offline.")


if __name__ == "__main__":
    main()
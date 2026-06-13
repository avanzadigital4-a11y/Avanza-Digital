"""
tron_xpub.py — Derivación de direcciones TRON usando SOLO la clave pública (xpub)
=================================================================================

POR QUÉ EXISTE ESTE MÓDULO:
  Antes, el servidor derivaba cada dirección de cobro USDT a partir de
  TRON_MNEMONIC (la semilla completa de la wallet) en cada request. Eso
  significaba que un compromiso del servidor web = acceso total a TODOS los
  fondos, pasados y futuros. El servidor solo necesita GENERAR direcciones
  para recibir pagos — nunca firma transacciones (el barrido de fondos lo
  hace Iván manualmente desde su wallet).

  Con este módulo, el servidor guarda únicamente la XPUB de la cuenta
  (m/44'/195'/0'). Desde una xpub se pueden derivar todas las direcciones
  de recepción (derivación pública no-hardened BIP32), pero es
  MATEMÁTICAMENTE IMPOSIBLE obtener las claves privadas. Si alguien roba la
  xpub, lo peor que puede hacer es ver las direcciones — no mover fondos.

CÓMO GENERAR LA XPUB (una sola vez, OFFLINE):
  Correr `python generar_xpub_offline.py` en tu máquina local (idealmente
  sin red), pegar la mnemónica cuando la pida, y copiar el valor TRON_XPUB
  resultante a las variables de entorno de Render/Railway. Después BORRAR
  TRON_MNEMONIC del servidor.

COMPATIBILIDAD:
  El path completo sigue siendo m/44'/195'/0'/0/{indice} — exactamente el
  mismo que usaba la derivación por mnemónica. Las direcciones generadas
  son idénticas, así que los links de pago históricos siguen siendo
  consistentes con la misma wallet.

DEPENDENCIAS: ecdsa (ya presente vía python-jose) y tronpy (ya en
requirements para la conversión a dirección base58check).
"""
from __future__ import annotations

import hashlib
import hmac
import struct

import ecdsa

# Orden del grupo secp256k1 (BIP32)
_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# ─── BASE58CHECK ──────────────────────────────────────────────────────────────

def _b58decode_check(s: str) -> bytes:
    """Decodifica base58check y valida el checksum. Lanza ValueError si falla."""
    num = 0
    for ch in s:
        idx = _B58_ALPHABET.find(ch)
        if idx == -1:
            raise ValueError(f"Carácter inválido en base58: {ch!r}")
        num = num * 58 + idx
    # Reconstruir bytes (con ceros a la izquierda por cada '1' inicial)
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big")
    pad = len(s) - len(s.lstrip("1"))
    raw = b"\x00" * pad + raw
    if len(raw) < 5:
        raise ValueError("Cadena base58check demasiado corta.")
    payload, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        raise ValueError("Checksum base58check inválido — la xpub está corrupta o mal copiada.")
    return payload


def b58encode_check(payload: bytes) -> str:
    """Codifica payload + checksum en base58 (usado por el script offline)."""
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    raw = payload + checksum
    num = int.from_bytes(raw, "big")
    out = ""
    while num > 0:
        num, rem = divmod(num, 58)
        out = _B58_ALPHABET[rem] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + out


# ─── PARSEO DE XPUB (BIP32, serialización estándar de 78 bytes) ──────────────

def parsear_xpub(xpub: str) -> tuple[bytes, bytes]:
    """Devuelve (pubkey_comprimida_33B, chain_code_32B) de una xpub estándar."""
    data = _b58decode_check(xpub.strip())
    if len(data) != 78:
        raise ValueError(f"xpub con longitud inesperada: {len(data)} bytes (esperados 78).")
    chain_code = data[13:45]
    pubkey     = data[45:78]
    if pubkey[0] not in (0x02, 0x03):
        raise ValueError("La clave de la xpub no es una clave PÚBLICA comprimida. "
                         "¿Pegaste una xprv por error? NUNCA pongas la xprv en el servidor.")
    return pubkey, chain_code


# ─── DERIVACIÓN PÚBLICA (CKDpub — BIP32 no-hardened) ─────────────────────────

def _punto_desde_comprimida(pub33: bytes) -> ecdsa.ellipticcurve.PointJacobi:
    vk = ecdsa.VerifyingKey.from_string(pub33, curve=ecdsa.SECP256k1)
    return vk.pubkey.point


def _comprimir_punto(point) -> bytes:
    x = point.x().to_bytes(32, "big")
    prefijo = b"\x02" if point.y() % 2 == 0 else b"\x03"
    return prefijo + x


def _ckd_pub(pub33: bytes, chain: bytes, indice: int) -> tuple[bytes, bytes]:
    """Un paso de derivación pública no-hardened: (Kpar, cpar, i) → (Ki, ci)."""
    if indice >= 0x80000000:
        raise ValueError("La derivación pública no admite índices hardened.")
    data = pub33 + struct.pack(">I", indice)
    I = hmac.new(chain, data, hashlib.sha512).digest()
    il = int.from_bytes(I[:32], "big")
    if il >= _ORDER:
        raise ValueError("IL fuera de rango (probabilidad ~2^-127) — reintentar con otro índice.")
    punto_padre = _punto_desde_comprimida(pub33)
    punto_hijo  = il * ecdsa.SECP256k1.generator + punto_padre
    return _comprimir_punto(punto_hijo), I[32:]


# ─── DIRECCIÓN TRON ──────────────────────────────────────────────────────────

def _direccion_tron(pub33: bytes) -> str:
    """Convierte una clave pública comprimida en dirección TRON base58 (T...).

    Usa tronpy (ya en requirements) para keccak + base58check, igual que la
    derivación vieja por mnemónica — garantiza direcciones idénticas.
    """
    from tronpy.keys import PublicKey
    vk = ecdsa.VerifyingKey.from_string(pub33, curve=ecdsa.SECP256k1)
    pub64 = vk.to_string()  # 64 bytes sin comprimir (x || y), formato que espera tronpy
    return PublicKey(pub64).to_base58check_address()


# ─── API PÚBLICA DEL MÓDULO ──────────────────────────────────────────────────

def direccion_desde_xpub(xpub: str, indice: int) -> str:
    """Deriva la dirección TRON para el índice dado desde la xpub de cuenta.

    La xpub debe corresponder al nivel de cuenta m/44'/195'/0'. Este módulo
    deriva los dos pasos públicos restantes del path: 0/{indice} (cadena
    externa / dirección de recepción). El path completo resultante es
    m/44'/195'/0'/0/{indice} — idéntico al esquema histórico por mnemónica.
    """
    if indice < 0 or indice >= 0x80000000:
        raise ValueError(f"Índice fuera de rango: {indice}")
    pub, chain = parsear_xpub(xpub)
    pub, chain = _ckd_pub(pub, chain, 0)        # cadena externa (recepción)
    pub, chain = _ckd_pub(pub, chain, indice)   # índice del link de pago
    return _direccion_tron(pub)


def validar_xpub(xpub: str) -> bool:
    """True si la xpub parsea y deriva sin errores (para healthchecks/arranque)."""
    try:
        direccion_desde_xpub(xpub, 0)
        return True
    except Exception:
        return False
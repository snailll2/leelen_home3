"""Primitives for the Leelen iotlan LEEL JSON protocol.

These are direct Python ports of the Java classes that live in
`com.leelen.smarthouse.iotlan.ctrl` inside the official Leelen APK:

- ``ProtocolUtils``      → CRC8/CDMA2000 table + frame codec (``crypto`` + ``protocol``)
- ``LanEncryptUtils``    → AES-128-CBC/PKCS5Padding with a fixed IV
- ``HmacMD5Utils``       → HMAC-MD5 derived AES key

The encryption primitives are kept here; frame codec lives next door in
``protocol.py`` so reviewers can map each piece back to its Java origin.
"""
from __future__ import annotations

import hashlib
import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

# Fixed IV used by LanEncryptUtils (ProtocolUtils.<clinit>).
IV = b"nwALi4=fjXM4nk#N"

# HMAC key prefix prepended to the server random during login (HmacMD5Utils).
HMAC_KEY_PREFIX = "@9jHaGa]"

# CRC8/CDMA2000 lookup table (ProtocolUtils.<clinit>).
_CRC8_CDMA2000_TABLE = bytes([
    0x00, 0xFE, 0xFF, 0x01, 0xFD, 0x03, 0x02, 0xFC, 0xF9, 0x07, 0x06, 0xF8, 0x04, 0xFA, 0xFB, 0x05,
    0xF1, 0x0F, 0x0E, 0xF0, 0x0C, 0xF2, 0xF3, 0x0D, 0x08, 0xF6, 0xF7, 0x09, 0xF5, 0x0B, 0x0A, 0xF4,
    0xE1, 0x1F, 0x1E, 0xE0, 0x1C, 0xE2, 0xE3, 0x1D, 0x18, 0xE6, 0xE7, 0x19, 0xE5, 0x1B, 0x1A, 0xE4,
    0x10, 0xEE, 0xEF, 0x11, 0xED, 0x13, 0x12, 0xEC, 0xE9, 0x17, 0x16, 0xE8, 0x14, 0xEA, 0xEB, 0x15,
    0xC1, 0x3F, 0x3E, 0xC0, 0x3C, 0xC2, 0xC3, 0x3D, 0x38, 0xC6, 0xC7, 0x39, 0xC5, 0x3B, 0x3A, 0xC4,
    0x30, 0xCE, 0xCF, 0x31, 0xCD, 0x33, 0x32, 0xCC, 0xC9, 0x37, 0x36, 0xC8, 0x34, 0xCA, 0xCB, 0x35,
    0x20, 0xDE, 0xDF, 0x21, 0xDD, 0x23, 0x22, 0xDC, 0xD9, 0x27, 0x26, 0xD8, 0x24, 0xDA, 0xDB, 0x25,
    0xD1, 0x2F, 0x2E, 0xD0, 0x2C, 0xD2, 0xD3, 0x2D, 0x28, 0xD6, 0xD7, 0x29, 0xD5, 0x2B, 0x2A, 0xD4,
    0x81, 0x7F, 0x7E, 0x80, 0x7C, 0x82, 0x83, 0x7D, 0x78, 0x86, 0x87, 0x79, 0x85, 0x7B, 0x7A, 0x84,
    0x70, 0x8E, 0x8F, 0x71, 0x8D, 0x73, 0x72, 0x8C, 0x89, 0x77, 0x76, 0x88, 0x74, 0x8A, 0x8B, 0x75,
    0x60, 0x9E, 0x9F, 0x61, 0x9D, 0x63, 0x62, 0x9C, 0x99, 0x67, 0x66, 0x98, 0x64, 0x9A, 0x9B, 0x65,
    0x91, 0x6F, 0x6E, 0x90, 0x6C, 0x92, 0x93, 0x6D, 0x68, 0x96, 0x97, 0x69, 0x95, 0x6B, 0x6A, 0x94,
    0x40, 0xBE, 0xBF, 0x41, 0xBD, 0x43, 0x42, 0xBC, 0xB9, 0x47, 0x46, 0xB8, 0x44, 0xBA, 0xBB, 0x45,
    0xB1, 0x4F, 0x4E, 0xB0, 0x4C, 0xB2, 0xB3, 0x4D, 0x48, 0xB6, 0xB7, 0x49, 0xB5, 0x4B, 0x4A, 0xB4,
    0xA1, 0x5F, 0x5E, 0xA0, 0x5C, 0xA2, 0xA3, 0x5D, 0x58, 0xA6, 0xA7, 0x59, 0xA5, 0x5B, 0x5A, 0xA4,
    0x50, 0xAE, 0xAF, 0x51, 0xAD, 0x53, 0x52, 0xAC, 0xA9, 0x57, 0x56, 0xA8, 0x54, 0xAA, 0xAB, 0x55,
])


def crc8(data: bytes) -> int:
    """ProtocolUtils.crc8Cdma2000: table-driven CRC8/CDMA2000."""
    crc = 0
    for byte in data:
        crc = _CRC8_CDMA2000_TABLE[(crc ^ byte) & 0xFF]
    return crc


def aes_encrypt(plain: bytes, key: bytes) -> bytes:
    """LanEncryptUtils.encrypt: AES/CBC/PKCS5Padding with the fixed IV."""
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def aes_decrypt(cipher_text: bytes, key: bytes) -> bytes:
    """LanEncryptUtils.decrypt: NoPadding decrypt + extractJsonBytes, fallback PKCS5.

    The official client does not trust PKCS5 unpadding; it scans the decrypted
    plaintext for the first complete ``{ ... }`` substring.  This matches the
    Java implementation byte-for-byte.
    """
    decryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).decryptor()
    padded = decryptor.update(cipher_text) + decryptor.finalize()
    try:
        return _extract_json_bytes(padded)
    except ValueError:
        unpadder = PKCS7(algorithms.AES.block_size).unpadder()
        return unpadder.update(padded) + unpadder.finalize()


def _extract_json_bytes(data: bytes) -> bytes:
    """LanEncryptUtils.extractJsonBytes: return the first complete ``{ ... }`` slice."""
    size = len(data)
    index = 0
    depth = 0
    start = -1
    while index < size:
        char = chr(data[index])
        if char == "{":
            if start == -1:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start != -1:
                return data[start:index + 1]
        index += 1
    raise ValueError("No complete JSON object found")


def compute_password(random_str: str, group_id: str) -> str:
    """HmacMD5Utils: HMAC-MD5(key=prefix+random, msg=groupId) → 32 uppercase hex.

    The resulting hex string is then fed through ``hex_to_bytes`` to produce
    the 16-byte AES key used for the encrypted half of the protocol.
    """
    key = (HMAC_KEY_PREFIX + random_str).encode("utf-8")
    msg = group_id.encode("utf-8")
    digest = hmac.new(key, msg, hashlib.md5).digest()
    return "".join(f"{b:02X}" for b in digest)


def hex_to_bytes(hex_str: str) -> bytes:
    """HmacMD5Utils.hexToBytes: case-insensitive hex → 16-byte AES key."""
    return bytes.fromhex(hex_str)


def derive_aes_key(random_str: str, group_id: str) -> bytes:
    """Convenience: compute_password → hex_to_bytes in one call."""
    return hex_to_bytes(compute_password(random_str, group_id))

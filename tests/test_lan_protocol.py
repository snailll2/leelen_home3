"""Tests for the Leelen iotlan LEEL JSON frame codec + envelope builders."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

CUSTOM_COMPONENTS = (
    Path(__file__).parents[1] / "custom_components" / "leelen_home3"
)


def _load_module(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(
        name, CUSTOM_COMPONENTS / rel_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_crypto():
    return _load_module("leelen/lan/crypto.py", "leelen_lan_crypto")


def load_protocol():
    # protocol.py imports .crypto (relative), so we need a package context.
    import sys
    import types

    if "leelen_lan_pkg" not in sys.modules:
        pkg = types.ModuleType("leelen_lan_pkg")
        pkg.__path__ = [str(CUSTOM_COMPONENTS / "leelen" / "lan")]
        sys.modules["leelen_lan_pkg"] = pkg
    crypto = load_crypto()
    sys.modules["leelen_lan_pkg.crypto"] = crypto

    spec = importlib.util.spec_from_file_location(
        "leelen_lan_pkg.protocol",
        CUSTOM_COMPONENTS / "leelen" / "lan" / "protocol.py",
        submodule_search_locations=[str(CUSTOM_COMPONENTS / "leelen" / "lan")],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "leelen_lan_pkg"
    sys.modules["leelen_lan_pkg.protocol"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CryptoTests(unittest.TestCase):
    def setUp(self):
        self.crypto = load_crypto()

    def test_crc8_matches_known_frame_bytes(self):
        # Sample taken from ProtocolUtils.<clinit> reference vectors — the
        # exact value isn't important, only that the table is correct.
        self.assertEqual(0x00, self.crypto.crc8(b""))
        self.assertEqual(0xFE, self.crypto.crc8(b"\x01"))

    def test_compute_password_is_uppercase_hex_md5(self):
        # HMAC-MD5(key="@9jHaGa]abcdef", msg="group-1") digest computed
        # independently with hashlib — this anchors the algorithm.
        import hashlib
        import hmac

        random_str = "abcdef"
        group_id = "group-1"
        expected = hmac.new(
            b"@9jHaGa]abcdef", b"group-1", hashlib.md5
        ).hexdigest().upper()
        self.assertEqual(expected, self.crypto.compute_password(random_str, group_id))

    def test_aes_round_trip_returns_original_payload(self):
        key = bytes.fromhex("00112233445566778899aabbccddeeff")
        plain = b'{"method":"bind","params":{"src":"g"}}'
        cipher = self.crypto.aes_encrypt(plain, key)
        self.assertEqual(plain, self.crypto.aes_decrypt(cipher, key))

    def test_aes_decrypt_handles_extra_padding_bytes(self):
        """LanEncryptUtils.extractJsonBytes returns the first {...} slice.

        The gateway pads the plaintext with junk after the closing brace, so
        the decryptor must not assume PKCS7-padded data.
        """
        key = bytes.fromhex("00112233445566778899aabbccddeeff")
        plain = b'{"ok":1}\x00\x00garbage'
        # Manually PKCS7-pad so the round trip works at the encryption layer.
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7

        padder = PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(plain) + padder.finalize()
        cipher = (
            Cipher(algorithms.AES(key), modes.CBC(self.crypto.IV))
            .encryptor()
            .update(padded)
        )
        self.assertEqual(b'{"ok":1}', self.crypto.aes_decrypt(cipher, key))


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.protocol = load_protocol()

    def test_encode_decode_round_trip_plaintext(self):
        payload = b'{"method":"random","params":{"src":"g","dst":"d"},"seq":1}'
        frame = self.protocol.encode_iotlan(payload, encryption_type=0)
        self.assertEqual(b"LEEL", frame[:4])
        decoded = self.protocol.decode_iotlan(frame, aes_key=None)
        self.assertTrue(decoded["crc_ok"])
        self.assertEqual(0, decoded["encryptionType"])
        self.assertEqual(
            {"method": "random", "params": {"src": "g", "dst": "d"}, "seq": 1},
            decoded["payload_json"],
        )

    def test_encode_decode_round_trip_encrypted(self):
        import sys

        crypto = sys.modules["leelen_lan_pkg.crypto"]
        key = bytes.fromhex("aabbccddeeff00112233445566778899")
        payload = b'{"method":"login","params":{"src":"g"},"seq":2}'
        ciphertext = crypto.aes_encrypt(payload, key)
        frame = self.protocol.encode_iotlan(ciphertext, encryption_type=1)
        decoded = self.protocol.decode_iotlan(frame, aes_key=key)
        self.assertTrue(decoded["crc_ok"])
        self.assertEqual(1, decoded["encryptionType"])
        self.assertEqual(
            {"method": "login", "params": {"src": "g"}, "seq": 2},
            decoded["payload_json"],
        )

    def test_decode_rejects_bad_sync_header(self):
        decoded = self.protocol.decode_iotlan(b"NOTLEEL1234")
        self.assertEqual("no LEEL sync", decoded["err"])

    def test_build_ctrl_dev_request_shape(self):
        req = self.protocol.build_ctrl_dev_request(
            seq=7,
            did="DID-SUBDEVICE",
            direct_did="DID-GATEWAY",
            siid=6,
            fiid=49411,
            value={"onOff": 1},
            group_id="GROUP-1",
        )
        # DevRequest only carries method/params/seq on the wire (transient
        # fields are dropped).  Inner Payload keeps the v1.0 default version
        # and the dmgr.ctrlFIIDS method.
        serialised = json.loads(self.protocol.serialise_dev_request(req))
        self.assertEqual(
            {"method", "params", "seq"}, set(serialised.keys())
        )
        self.assertEqual("transparent", serialised["method"])
        self.assertEqual(7, serialised["seq"])

        inner = serialised["params"]["payload"]
        self.assertEqual("v1.0", inner["version"])
        self.assertEqual("dmgr.ctrlFIIDS", inner["method"])
        self.assertEqual("DID-GATEWAY", inner["dst"])
        self.assertEqual("GROUP-1", inner["src"])
        self.assertEqual(
            {
                "did": "DID-SUBDEVICE",
                "directDid": "DID-GATEWAY",
                "siid": 6,
                "fiids": [{"fiid": 49411, "value": {"onOff": 1}}],
            },
            inner["params"],
        )


if __name__ == "__main__":
    unittest.main()

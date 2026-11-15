"""LEEL JSON frame codec and envelope builders.

Direct port of ``com.leelen.smarthouse.iotlan.ctrl.ProtocolUtils`` (frame
encode/decode, CRC8) plus the three Kotlin data classes that wrap a control
request:

- ``DevRequest``  — top-level envelope. ``dst``, ``encryptionType`` and
                    ``mainGateway`` are ``@Transient`` and never serialised
                    by Gson, so the on-wire object only contains
                    ``method``, ``params`` and ``seq``.
- ``DevPayload``  — wraps ``(topic, payload)``. ``topic`` has a hard-coded
                    default that the official app never overrides in practice.
- ``Payload``     — actual business message. ``version`` defaults to
                    ``"v1.0"`` via Kotlin's default-constructor mask.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

from . import crypto

SYNC_HEADER = b"LEEL"

# DevPayload.topic default (DevPayload.<init> default mask=1).
# This is a literal string baked into the Leelen APK's DevPayload Kotlin class
# (not a user device id) — the gateway does not validate it, but it must be
# present on the wire for transparent control to be accepted.
DEVPAYLOAD_DEFAULT_TOPIC = "lliot/fiids_report/0001200d943469fffe71799e/2"

# Payload.version default (Payload.<init> default mask=2).
PAYLOAD_DEFAULT_VERSION = "v1.0"


def encode_iotlan(
    payload: bytes,
    *,
    encryption_type: int = 0,
    version: float = 1.0,
) -> bytes:
    """Encode a single LEEL JSON frame.

    Frame layout::

        "LEEL" + length(4 LE) + [version(2 LE short) + encType(1) + payload] + CRC8(1)

    where ``length`` covers the version/encType/payload bytes plus the
    trailing CRC8 byte.  ``payload`` is either a UTF-8 JSON document
    (``encryption_type=0``) or AES-CBC ciphertext (``encryption_type=1``).
    """
    body = struct.pack("<h", int(version)) + bytes([encryption_type]) + payload
    length = len(body) + 1  # +1 for the trailing CRC8 byte
    frame = SYNC_HEADER + struct.pack("<I", length) + body
    return frame + bytes([crypto.crc8(frame)])


def decode_iotlan(data: bytes, aes_key: bytes | None = None) -> dict[str, Any]:
    """Decode a single LEEL JSON frame.

    Returns a dict with ``length``, ``encryptionType``, ``crc_ok`` and either
    ``payload_json`` (already parsed) or one of ``payload_str``/``payload_hex``
    /``decrypt_err`` depending on what was decodable.
    """
    if len(data) < 9 or data[:4] != SYNC_HEADER:
        return {"err": "no LEEL sync", "hex_head": data[:32].hex()}

    length = struct.unpack("<I", data[4:8])[0]
    if len(data) < 8 + length:
        return {"err": "truncated", "hex": data.hex()}

    body = data[8:8 + length - 1]
    crc_recv = data[8 + length - 1]
    crc_calc = crypto.crc8(data[:8 + length - 1])
    if len(body) < 3:
        return {
            "length": length,
            "crc_ok": crc_recv == crc_calc,
            "err": "body short",
            "body_hex": body.hex(),
        }

    enc = body[2]
    payload_bytes = body[3:]
    info: dict[str, Any] = {
        "length": length,
        "encryptionType": enc,
        "crc_ok": crc_recv == crc_calc,
    }

    if enc == 0:
        try:
            info["payload_json"] = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            info["payload_str"] = payload_bytes.decode("utf-8", errors="replace")
    elif enc == 1 and aes_key:
        try:
            plain = crypto.aes_decrypt(payload_bytes, aes_key)
            info["payload_str"] = plain.decode("utf-8", errors="replace")
            try:
                info["payload_json"] = json.loads(info["payload_str"])
            except json.JSONDecodeError:
                pass
        except Exception as exc:  # noqa: BLE001 - protocol decoder must not raise
            info["decrypt_err"] = f"{type(exc).__name__}: {exc}"
            info["payload_hex"] = payload_bytes.hex()
    else:
        info["payload_hex"] = payload_bytes.hex()
    return info


@dataclass
class Payload:
    """The inner business message (Kotlin ``Payload``)."""

    seq: int
    method: str
    params: dict[str, Any]
    dst: str
    src: str
    version: str = PAYLOAD_DEFAULT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "version": self.version,
            "method": self.method,
            "params": self.params,
            "dst": self.dst,
            "src": self.src,
        }


@dataclass
class DevPayload:
    """Middle wrapper (Kotlin ``DevPayload``)."""

    payload: Payload
    topic: str = DEVPAYLOAD_DEFAULT_TOPIC

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "payload": self.payload.to_dict()}


@dataclass
class DevRequest:
    """Top-level envelope (Kotlin ``DevRequest``).

    ``method`` is always ``"transparent"`` for control flows; ``params`` is a
    ``DevPayload``; ``seq`` is a per-connection counter.  The Java class
    carries three additional ``@Transient`` fields (``dst``,
    ``encryptionType``, ``mainGateway``) that are deliberately omitted from
    the serialised JSON.
    """

    seq: int
    params: DevPayload
    method: str = "transparent"

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "params": self.params.to_dict(),
            "seq": self.seq,
        }


def build_ctrl_params(
    *,
    did: str,
    direct_did: str,
    siid: int,
    fiid: int,
    value: dict[str, Any],
) -> dict[str, Any]:
    """Build the ``dmgr.ctrlFIIDS`` params dict.

    Mirrors ``IotLanRTUtil.controlIotDeviceSwitch``: the value is a HashMap
    (e.g. ``{"onOff": 1}``) wrapped in a single-element ``fiids`` list.
    """
    return {
        "did": did,
        "directDid": direct_did,
        "siid": siid,
        "fiids": [{"fiid": fiid, "value": value}],
    }


def build_ctrl_dev_request(
    *,
    seq: int,
    did: str,
    direct_did: str,
    siid: int,
    fiid: int,
    value: dict[str, Any],
    group_id: str,
) -> DevRequest:
    """Build a complete ``dmgr.ctrlFIIDS`` ``DevRequest`` ready for encoding."""
    params = build_ctrl_params(
        did=did,
        direct_did=direct_did,
        siid=siid,
        fiid=fiid,
        value=value,
    )
    payload = Payload(
        seq=seq,
        method="dmgr.ctrlFIIDS",
        params=params,
        dst=direct_did,
        src=group_id,
    )
    dev_payload = DevPayload(payload=payload)
    return DevRequest(seq=seq, params=dev_payload)


def serialise_dev_request(request: DevRequest) -> bytes:
    """Serialise a ``DevRequest`` to compact JSON bytes (Gson-equivalent)."""
    return json.dumps(
        request.to_dict(),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

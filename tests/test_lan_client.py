"""Tests for LeelenLanClient push/ack dispatch logic.

These tests exercise ``_dispatch_parsed`` directly with hand-crafted decoded
frames.  They do not touch the network, the TLS handshake, or any asyncio
primitives beyond ``Future`` objects.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

CUSTOM_COMPONENTS = (
    Path(__file__).parents[1] / "custom_components" / "leelen_home3"
)


def _ensure_lan_package():
    if "leelen_lan_pkg" in sys.modules:
        return sys.modules["leelen_lan_pkg"]

    pkg = types.ModuleType("leelen_lan_pkg")
    pkg.__path__ = [str(CUSTOM_COMPONENTS / "leelen" / "lan")]
    sys.modules["leelen_lan_pkg"] = pkg

    crypto_spec = importlib.util.spec_from_file_location(
        "leelen_lan_pkg.crypto",
        CUSTOM_COMPONENTS / "leelen" / "lan" / "crypto.py",
    )
    crypto = importlib.util.module_from_spec(crypto_spec)
    sys.modules["leelen_lan_pkg.crypto"] = crypto
    assert crypto_spec.loader is not None
    crypto_spec.loader.exec_module(crypto)

    proto_spec = importlib.util.spec_from_file_location(
        "leelen_lan_pkg.protocol",
        CUSTOM_COMPONENTS / "leelen" / "lan" / "protocol.py",
    )
    proto = importlib.util.module_from_spec(proto_spec)
    sys.modules["leelen_lan_pkg.protocol"] = proto
    assert proto_spec.loader is not None
    proto_spec.loader.exec_module(proto)

    client_spec = importlib.util.spec_from_file_location(
        "leelen_lan_pkg.client",
        CUSTOM_COMPONENTS / "leelen" / "lan" / "client.py",
    )
    client = importlib.util.module_from_spec(client_spec)
    sys.modules["leelen_lan_pkg.client"] = client
    assert client_spec.loader is not None
    client_spec.loader.exec_module(client)

    pkg.crypto = crypto
    pkg.protocol = proto
    pkg.client = client
    return pkg


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.pkg = _ensure_lan_package()
        self.client = self.pkg.client.LeelenLanClient(
            self.pkg.client.GatewayInfo(
                host="127.0.0.1", port=49853, did="DID-1", group_id="G-1"
            )
        )
        self.pushes: list[dict] = []
        self.client._on_push = self.pushes.append

    def test_transparent_push_unwraps_to_inner_payload(self):
        """dmgr.notifyFIIDS inside transparent → on_push(inner)."""
        parsed = {
            "payload_json": {
                "method": "transparent",
                "seq": 11,
                "params": {
                    "topic": "lliot/fiids_report/DID/2",
                    "payload": {
                        "seq": 11,
                        "version": "v1.0",
                        "method": "dmgr.notifyFIIDS",
                        "params": {"did": "D", "siid": 2, "fiids": []},
                        "dst": "D",
                        "src": "G",
                    },
                },
            }
        }
        self.client._dispatch_parsed(parsed)
        self.assertEqual(1, len(self.pushes))
        self.assertEqual("dmgr.notifyFIIDS", self.pushes[0]["method"])
        # No future was registered → no pending leak.
        self.assertEqual({}, self.client._pending)

    def test_transparent_ack_routes_to_pending_future(self):
        """transparent wrapping dmgr.ctrlFIIDS → resolves the waiting future."""
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            future: asyncio.Future = loop.create_future()
            self.client._pending[42] = future

            parsed = {
                "payload_json": {
                    "method": "transparent",
                    "seq": 42,
                    "params": {
                        "topic": "lliot/fiids_report/DID/2",
                        "payload": {
                            "seq": 42,
                            "version": "v1.0",
                            "method": "dmgr.ctrlFIIDS",
                            "params": {"did": "D", "siid": 2, "fiids": []},
                            "dst": "D",
                            "src": "G",
                        },
                    },
                }
            }
            self.client._dispatch_parsed(parsed)
            self.assertTrue(future.done())
            self.assertIs(parsed, future.result())
            self.assertEqual({}, self.client._pending)
            # Push callback was not invoked for the ack.
            self.assertEqual(0, len(self.pushes))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_unknown_method_is_silently_dropped(self):
        parsed = {
            "payload_json": {
                "method": "unknown",
                "seq": 99,
                "params": {},
            }
        }
        self.client._dispatch_parsed(parsed)
        self.assertEqual(0, len(self.pushes))
        self.assertEqual({}, self.client._pending)

    def test_int_group_id_is_coerced_to_str(self):
        """HA storage may persist groupId as an int; the wire format needs str.

        Regression guard: a bare int drops the JSON quotes around ``src`` and
        ``groupId``, shrinking the bind payload by 4 bytes and getting the
        bind rejected with ``ack=0``.
        """
        client = self.pkg.client.LeelenLanClient(
            self.pkg.client.GatewayInfo(
                host="127.0.0.1",
                port=49853,
                did=12345,
                group_id=123456789012345678,
            )
        )
        self.assertEqual("123456789012345678", client._group_id)
        self.assertEqual("12345", client._did)

    def test_ctrl_response_with_no_top_level_method_is_accepted(self):
        """Regression: real transparent responses have no top-level method.

        The original implementation checked ``payload["method"] == "transparent"``
        which never matched the gateway's actual response shape::

            {"ack": 1, "seq": N, "params": {"payload": {"method":
            "dmgr.ctrlFIIDS", "result": 1, ...}}}

        Every LAN control was therefore misclassified as failed and fell back
        to the cloud.  This test confirms the dispatch path treats the
        response as a normal ack (no ``method`` field at the top level).
        """
        # The reader dispatch only inspects payload_json fields it needs;
        # a missing ``method`` is the normal case for acks (vs pushes which
        # carry ``method: transparent``).  Confirm dispatch does not crash
        # and routes the response to the pending future.
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            future: asyncio.Future = loop.create_future()
            self.client._pending[44] = future
            parsed = {
                "payload_json": {
                    "ack": 1,
                    "seq": 44,
                    "params": {
                        "topic": "lliot/receiver/G",
                        "payload": {
                            "seq": 44,
                            "version": "v1.0",
                            "method": "dmgr.ctrlFIIDS",
                            "src": "D",
                            "dst": "G",
                            "result": 1,
                        },
                    },
                }
            }
            self.client._dispatch_parsed(parsed)
            self.assertTrue(future.done())
            self.assertIs(parsed, future.result())
        finally:
            loop.close()
            asyncio.set_event_loop(None)


if __name__ == "__main__":
    unittest.main()

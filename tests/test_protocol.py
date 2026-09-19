"""Tests for Leelen cloud protocol response handling."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "leelen_home3"
    / "leelen"
    / "api"
    / "protocol.py"
)


def load_protocol_module():
    spec = importlib.util.spec_from_file_location("leelen_protocol", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.protocol = load_protocol_module()

    def test_retries_a_pending_realtime_read_after_server_wait(self):
        pending = {
            "result": 1,
            "waitNum": 1,
            "waitTime": 10000,
            "params": [{"fiids": []}],
        }
        complete = {
            "result": 1,
            "waitNum": 0,
            "waitTime": 0,
            "params": [{"fiids": [{"fiid": 49415, "value": {"curTemp": 24}}]}],
        }

        self.assertEqual(10.0, self.protocol.pending_read_delay(pending))
        self.assertIsNone(self.protocol.pending_read_delay(complete))

    def test_caps_an_untrusted_server_wait(self):
        response = {"result": 1, "waitNum": 1, "waitTime": 60000}

        self.assertEqual(10.0, self.protocol.pending_read_delay(response))


if __name__ == "__main__":
    unittest.main()

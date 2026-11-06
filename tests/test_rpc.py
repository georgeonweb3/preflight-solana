import json
import unittest
from unittest import mock

from preflight_core.rpc import Rpc, _tx


class TestTxBuilder(unittest.TestCase):
    """eth_call and eth_estimateGas used to build their call dicts
    twice over; _tx is the single builder now (2026-11-06 review,
    Standards finding 4)."""

    def test_minimal_call_has_no_from_or_value(self):
        self.assertEqual(_tx("0xabc", "0x00"), {"to": "0xabc", "data": "0x00"})

    def test_from_and_value_are_included_when_set(self):
        tx = _tx("0xabc", "0x00", from_addr="0xdef", value_wei=255)
        self.assertEqual(tx, {"to": "0xabc", "data": "0x00",
                              "from": "0xdef", "value": "0xff"})


class TestAllowlist(unittest.TestCase):
    def test_send_method_is_refused_before_any_network(self):
        with self.assertRaises(ValueError):
            Rpc([]).call("eth_sendTransaction", [])


class _Body:
    """Minimal urlopen response stand-in: context manager + read()."""

    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestGetCodeOutcomes(unittest.TestCase):
    """eth_getCode's two empty-looking outcomes must stay distinct at
    the RPC boundary (DEPLOYMENT_UNKNOWN fix, 2026-11-06): a
    successful read of an address with no code returns the string
    "0x"; a failed read - including a response body truncated
    mid-string, the failure mode observed live on BSC endpoints -
    returns None after every endpoint has been tried."""

    ADDR = "0x0000000000000000000000000000000000000001"

    def _call_with_body(self, body):
        with mock.patch("urllib.request.urlopen",
                        return_value=_Body(body)):
            return Rpc(["https://endpoint.invalid"]).call(
                "eth_getCode", [self.ADDR, "latest"])

    def test_successful_empty_read_returns_0x(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x"}).encode()
        self.assertEqual(self._call_with_body(body), "0x")

    def test_truncated_body_is_a_failed_read(self):
        full = json.dumps({"jsonrpc": "2.0", "id": 1,
                           "result": "0x608060405234801561001057600080fd5b50"}).encode()
        truncated = full[:len(full) // 2]  # cut mid-string: never parses
        self.assertIsNone(self._call_with_body(truncated))

    def test_transport_exception_is_a_failed_read(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=OSError("connection reset")):
            self.assertIsNone(Rpc(["https://endpoint.invalid"]).call(
                "eth_getCode", [self.ADDR, "latest"]))


if __name__ == "__main__":
    unittest.main()

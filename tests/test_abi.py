import unittest

from preflight_core.abi import arg_types, encode_call, wei_to_native
from preflight_core.keccak import selector


class TestArgTypes(unittest.TestCase):
    """Signature parsing used to live in two places (abi.encode_call
    and the probes simulation loop); arg_types is the one parser now
    (2026-11-06 review, Standards finding 5)."""

    def test_no_args(self):
        self.assertEqual(arg_types("mint()"), [])

    def test_typed_args(self):
        self.assertEqual(arg_types("mint(address,uint256)"),
                         ["address", "uint256"])

    def test_encode_call_uses_the_same_parsing(self):
        data = encode_call("mint(uint256)", [1])
        self.assertTrue(data.startswith("0x" + selector("mint(uint256)")))
        self.assertTrue(data.endswith("%064x" % 1))


class TestWeiToNative(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(wei_to_native(0), "0")
        self.assertEqual(wei_to_native(10 ** 18), "1")
        self.assertEqual(wei_to_native(None), "unknown")


if __name__ == "__main__":
    unittest.main()

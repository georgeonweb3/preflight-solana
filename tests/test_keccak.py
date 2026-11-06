import unittest

from preflight_core.keccak import keccak256, selector, selftest


class TestKeccak(unittest.TestCase):
    def test_empty_hash(self):
        self.assertEqual(
            keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")

    def test_known_selectors(self):
        self.assertEqual(selector("name()"), "06fdde03")
        self.assertEqual(selector("symbol()"), "95d89b41")
        self.assertEqual(selector("balanceOf(address)"), "70a08231")
        self.assertEqual(selector("transfer(address,uint256)"), "a9059cbb")
        self.assertEqual(selector("totalSupply()"), "18160ddd")
        self.assertEqual(selector("supportsInterface(bytes4)"), "01ffc9a7")

    def test_selftest(self):
        self.assertTrue(selftest())


if __name__ == "__main__":
    unittest.main()

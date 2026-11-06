import unittest

from preflight_core.resolve import resolve_input

ACAT = "0x28a38A372639E8C481aD41B9B16E52c3c6366ACC"


class TestResolve(unittest.TestCase):
    def test_bare_address(self):
        addr, note, cands = resolve_input(ACAT)
        self.assertEqual(addr, ACAT)
        self.assertEqual(cands, [ACAT])

    def test_address_in_text(self):
        addr, note, _ = resolve_input(f"yo check this mint: {ACAT} looks free")
        self.assertEqual(addr, ACAT)
        self.assertIn("text", note)

    def test_address_inside_url_skips_fetch(self):
        addr, note, _ = resolve_input(f"https://bscscan.com/token/{ACAT}")
        self.assertEqual(addr, ACAT)
        self.assertIn("URL itself", note)

    def test_garbage_text_unresolved(self):
        addr, note, cands = resolve_input("mint is totally free trust me bro")
        self.assertIsNone(addr)
        self.assertEqual(cands, [])


if __name__ == "__main__":
    unittest.main()

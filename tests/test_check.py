import unittest

from preflight_core.check import run_check

CHECKS_KEYS = {"deployment", "verification", "identity", "supply", "price",
               "open_status", "wallet", "simulation", "gas"}


class TestUnresolvedSkeleton(unittest.TestCase):
    """Regression test for the 2026-11-06 review (Spec finding 1): the
    UNRESOLVED_INPUT report omitted checks / library_matches /
    selector_fingerprint, breaking the preflight.verdict/1 schema,
    which promises one shape for every report."""

    def test_unresolved_report_carries_the_full_skeleton(self):
        # Text input with no address in it: resolution fails locally,
        # no network is touched.
        report, code = run_check("definitely not an address or a URL")
        self.assertEqual(code, 2)
        self.assertEqual(report["verdict"], "UNRESOLVED_INPUT")
        self.assertIsNone(report["contract"])
        self.assertEqual(set(report["checks"]), CHECKS_KEYS)
        self.assertTrue(all(v is None for v in report["checks"].values()))
        self.assertEqual(report["library_matches"], [])
        self.assertEqual(report["selector_fingerprint"], [])


if __name__ == "__main__":
    unittest.main()

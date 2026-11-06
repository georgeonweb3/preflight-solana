import unittest

from preflight_core import verdict as verdict_module
from preflight_core.library import SHAPE_THRESHOLD, match_library
from preflight_core.verdict import evaluate

ADDR = "0x28a38A372639E8C481aD41B9B16E52c3c6366ACC"
OTHER = "0x1111111111111111111111111111111111111111"


def entry(**kw):
    e = {"project": "ACAT", "chain": "BSC", "contract": ADDR,
         "outcome": "success_free_mint_executed", "failure_reason": None,
         "mint_method": None, "selectors": []}
    e.update(kw)
    return e


class TestChainScoping(unittest.TestCase):
    """Regression tests for the 2026-11-06 review addendum: library
    matching ignored the entry's chain field, so the same address on
    another chain inherited this chain's exact-contract history."""

    def test_exact_match_same_chain(self):
        m = match_library([entry()], ADDR, set(), chain="bsc")
        self.assertEqual(m[0]["match_type"], "exact_contract")

    def test_chain_comparison_is_case_insensitive(self):
        m = match_library([entry(chain="bsc")], ADDR, set(), chain="BSC")
        self.assertEqual(m[0]["match_type"], "exact_contract")

    def test_same_address_other_chain_is_not_exact(self):
        # No selectors on the entry, so there is no shape fallback
        # either: a cross-chain twin must produce no match at all.
        m = match_library([entry()], ADDR, set(), chain="base")
        self.assertEqual(m, [])

    def test_cross_chain_twin_can_only_shape_match(self):
        sels = {"a0712d68", "70a08231"}
        m = match_library([entry(selectors=sorted(sels))], ADDR, sels,
                          chain="base")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["match_type"], "abi_shape")

    def test_entry_without_chain_still_matches_exact(self):
        e = entry()
        del e["chain"]
        m = match_library([e], ADDR, set(), chain="base")
        self.assertEqual(m[0]["match_type"], "exact_contract")

    def test_no_chain_argument_keeps_legacy_behaviour(self):
        m = match_library([entry()], ADDR, set())
        self.assertEqual(m[0]["match_type"], "exact_contract")


def _clean_checks():
    return {
        "deployment": {"is_contract": True, "is_proxy_eip1967": False},
        "identity": {}, "supply": {}, "price": {},
        "open_status": {"probes": {"mintOpen()": True}},
        "wallet": {},
        "simulation": {"attempts": [{"function": "mint()", "value_wei": 0,
                                     "result": "success", "reason": None}],
                       "candidate_functions": ["mint()"],
                       "any_success": True, "free_success": True},
    }


class TestShapeThreshold(unittest.TestCase):
    """One threshold story (2026-11-06 review, Spec finding 6 tail):
    the matcher used to admit ABI shapes at similarity >= 0.25 while
    the verdict only surfaced them at >= 0.5, so "match" meant two
    different things in one report. SHAPE_THRESHOLD is now the single
    value every consumer uses."""

    def test_shape_at_threshold_matches(self):
        # intersection 2, union 4 -> similarity exactly 0.5
        e = entry(selectors=["aaaaaaaa", "bbbbbbbb", "cccccccc"])
        m = match_library([e], OTHER, {"aaaaaaaa", "bbbbbbbb", "dddddddd"},
                          chain="bsc")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["match_type"], "abi_shape")
        self.assertEqual(m[0]["similarity"], 0.5)

    def test_shape_below_threshold_does_not_match(self):
        # intersection 4, union 10 -> similarity 0.4: shared boilerplate
        # selectors, not a relation - no match is returned at all.
        e = entry(selectors=["aaaaaaaa", "bbbbbbbb", "cccccccc",
                             "dddddddd", "eeeeeeee", "ffffffff"])
        m = match_library([e], OTHER,
                          {"aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd",
                           "11111111", "22222222", "33333333", "44444444"},
                          chain="bsc")
        self.assertEqual(m, [])

    def test_threshold_is_one_shared_value(self):
        self.assertEqual(SHAPE_THRESHOLD, 0.5)
        self.assertIs(verdict_module.SHAPE_THRESHOLD, SHAPE_THRESHOLD)

    def test_verdict_ignores_sub_threshold_shape_match(self):
        # Even if a sub-threshold match were handed to the verdict
        # directly, it must not surface as a history reason.
        matches = [{"project": "BoilerplateTwin", "similarity": 0.4,
                    "match_type": "abi_shape", "outcome": "sold_out",
                    "failure_reason": "supply exhausted", "mint_method": None}]
        _, reasons = evaluate(_clean_checks(), {"status": "exact_match"},
                              matches, {})
        codes = {r["code"] for r in reasons}
        self.assertNotIn("HISTORY_SHAPE_FAILURE", codes)
        self.assertNotIn("HISTORY_SHAPE_MATCH", codes)


if __name__ == "__main__":
    unittest.main()

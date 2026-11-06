import unittest

from preflight_core.gas import band_for
from preflight_core.probes import wei_to_native
from preflight_core.verdict import evaluate


def base_checks():
    """A clean, verified, free, open mint - the ACAT shape."""
    return {
        "deployment": {"is_contract": True, "is_proxy_eip1967": False,
                       "implementation": None},
        "identity": {"type": "ERC721", "name": "ACAT", "symbol": "ACAT",
                     "owner": None, "paused": False},
        "supply": {"total_supply": 1087, "max_supply": 10000, "remaining": 8913},
        "price": {"wei": 0, "native": "0", "via_function": "mintPrice()",
                  "all_probes_native": {"mintPrice()": "0"}, "probes_disagree": False},
        "open_status": {"probes": {"mintOpen()": True}, "open": True, "paused": False},
        "wallet": {"wallet": "0x0", "balance_of": 0, "minted_counts": {}, "flags": {}},
        "simulation": {"attempts": [{"function": "mint()", "value_wei": 0,
                                     "result": "success", "reason": None}],
                       "candidate_functions": ["mint()"],
                       "any_success": True, "free_success": True},
    }


VERIFIED = {"status": "exact_match", "source": "sourcify"}
CHEAP_GAS = {"ok": True, "gwei_median": 0.08, "band": "CHEAP", "native_usd": 883.9}


def codes(reasons):
    return {r["code"] for r in reasons}


class TestVerdict(unittest.TestCase):
    def test_clean_free_verified_mint_is_clear(self):
        verdict, reasons = evaluate(base_checks(), VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "CLEAR")
        self.assertNotIn("block", {r["level"] for r in reasons})
        self.assertNotIn("caution", {r["level"] for r in reasons})

    def test_unverified_and_reverting_is_blocked(self):
        # the BNB JIAYOU shape
        checks = base_checks()
        checks["simulation"] = {
            "attempts": [{"function": "mint(uint256)", "value_wei": 0,
                          "result": "revert", "reason": "execution reverted"}],
            "candidate_functions": ["mint(uint256)"],
            "any_success": False, "free_success": False}
        checks["open_status"] = {"probes": {}, "open": None, "paused": None}
        checks["price"] = {"wei": None, "native": "unknown", "via_function": None,
                           "all_probes_native": {}, "probes_disagree": False}
        verdict, reasons = evaluate(checks, {"status": "none"}, [], CHEAP_GAS)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("UNVERIFIED_AND_REVERTING", codes(reasons))
        self.assertIn("SIMULATION_REVERTS", codes(reasons))

    def test_paid_mint_is_caution_with_amount(self):
        checks = base_checks()
        checks["price"] = {"wei": 670000000000000, "native": "0.00067",
                           "via_function": "mintPrice()",
                           "all_probes_native": {"mintPrice()": "0.00067"},
                           "probes_disagree": False}
        checks["simulation"]["attempts"] = [
            {"function": "mint()", "value_wei": 670000000000000,
             "result": "success", "reason": None}]
        checks["simulation"]["free_success"] = False
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("PAID_MINT", codes(reasons))

    def test_sold_out_is_blocked(self):
        checks = base_checks()
        checks["supply"] = {"total_supply": 3333, "max_supply": 3333, "remaining": 0}
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("SOLD_OUT", codes(reasons))

    def test_not_a_contract_is_blocked(self):
        checks = base_checks()
        checks["deployment"]["is_contract"] = False
        verdict, reasons = evaluate(checks, {"status": "unknown"}, [], {})
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("NOT_A_CONTRACT", codes(reasons))

    def test_proxy_is_caution_not_block(self):
        checks = base_checks()
        checks["deployment"]["is_proxy_eip1967"] = True
        checks["deployment"]["implementation"] = "0x0000000000000000000000000000000000000001"
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("UPGRADEABLE_PROXY", codes(reasons))

    def test_no_mint_function_is_caution_not_block(self):
        # the commit/reveal shape (Agent Periodic Lab lesson)
        checks = base_checks()
        checks["simulation"] = {"attempts": [], "candidate_functions": [],
                                "any_success": False, "free_success": False}
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("NO_KNOWN_MINT_FUNCTION", codes(reasons))

    def test_partial_match_is_info_not_caution(self):
        verdict, reasons = evaluate(base_checks(),
                                    {"status": "partial_match"}, [], CHEAP_GAS)
        self.assertEqual(verdict, "CLEAR")
        self.assertIn("SOURCE_VERIFIED_PARTIAL", codes(reasons))

    def test_exact_history_failure_is_caution(self):
        matches = [{"project": "OldDrop", "similarity": 1.0,
                    "match_type": "exact_contract", "outcome": "sold_out",
                    "failure_reason": "supply exhausted", "mint_method": None}]
        verdict, reasons = evaluate(base_checks(), VERIFIED, matches, CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("HISTORY_EXACT_FAILURE", codes(reasons))


class TestSimulationEvidence(unittest.TestCase):
    """Regression tests for the 2026-11-06 review's Critical finding:
    a transport failure is not a revert and must never manufacture a
    SIMULATION_REVERTS / UNVERIFIED_AND_REVERTING block."""

    def _set_sim(self, checks, attempts):
        checks["simulation"] = {
            "attempts": attempts,
            "candidate_functions": sorted({a["function"] for a in attempts}),
            "any_success": any(a["result"] == "success" for a in attempts),
            "free_success": False}

    def test_all_error_attempts_are_caution_not_block(self):
        checks = base_checks()
        self._set_sim(checks, [{"function": "mint()", "value_wei": 0,
                                "result": "error",
                                "reason": "all endpoints unreachable"}])
        verdict, reasons = evaluate(checks, {"status": "none"}, [], CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("SIMULATION_UNKNOWN", codes(reasons))
        self.assertNotIn("SIMULATION_REVERTS", codes(reasons))
        self.assertNotIn("UNVERIFIED_AND_REVERTING", codes(reasons))

    def test_mixed_revert_and_error_is_not_all_reverted(self):
        checks = base_checks()
        self._set_sim(checks, [
            {"function": "mint()", "value_wei": 0, "result": "revert",
             "reason": "execution reverted"},
            {"function": "claim()", "value_wei": 0, "result": "error",
             "reason": "endpoint timeout"},
        ])
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertNotIn("SIMULATION_REVERTS", codes(reasons))
        self.assertIn("SIMULATION_UNKNOWN", codes(reasons))


class TestDeploymentEvidence(unittest.TestCase):
    """Regression tests for the DEPLOYMENT_UNKNOWN fix (2026-11-06):
    a failed eth_getCode read (transport failure / truncation) is a
    caution that decides nothing on its own - it can neither
    manufacture a NOT_A_CONTRACT block nor soften an independent one."""

    def _unknown_checks(self):
        # What probe_contract emits when the code read failed: a
        # deployment section carrying the failure, and no
        # contract-dependent sections at all.
        return {"deployment": {"is_contract": None, "code_read_failed": True,
                               "is_proxy_eip1967": False, "implementation": None}}

    def test_deployment_unknown_is_caution_not_block(self):
        verdict, reasons = evaluate(self._unknown_checks(), VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("DEPLOYMENT_UNKNOWN", codes(reasons))
        self.assertNotIn("NOT_A_CONTRACT", codes(reasons))

    def test_deployment_unknown_suppresses_absence_readings(self):
        # With the bytecode never read, "no price answered" and "no
        # mint function found" would only restate the failure - the
        # absence of evidence must not be dressed up as two more
        # contract facts next to it.
        _, reasons = evaluate(self._unknown_checks(), VERIFIED, [], CHEAP_GAS)
        self.assertNotIn("PRICE_UNKNOWN", codes(reasons))
        self.assertNotIn("NO_KNOWN_MINT_FUNCTION", codes(reasons))
        self.assertNotIn("SIMULATION_NOT_POSSIBLE", codes(reasons))

    def test_deployment_unknown_does_not_mask_an_independent_block(self):
        checks = self._unknown_checks()
        checks["supply"] = {"total_supply": 100, "max_supply": 100, "remaining": 0}
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("SOLD_OUT", codes(reasons))
        self.assertIn("DEPLOYMENT_UNKNOWN", codes(reasons))

    def test_genuine_empty_read_still_blocks(self):
        checks = base_checks()
        checks["deployment"] = {"is_contract": False, "code_read_failed": False,
                                "is_proxy_eip1967": False, "implementation": None}
        verdict, reasons = evaluate(checks, {"status": "unknown"}, [], {})
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("NOT_A_CONTRACT", codes(reasons))
        self.assertNotIn("DEPLOYMENT_UNKNOWN", codes(reasons))

    def test_clean_read_has_no_deployment_unknown(self):
        verdict, reasons = evaluate(base_checks(), VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "CLEAR")
        self.assertNotIn("DEPLOYMENT_UNKNOWN", codes(reasons))


class TestHistoryClassification(unittest.TestCase):
    def test_unsuccessful_is_not_a_success(self):
        # Regression (coordinator addendum): outcome classification was
        # substring-based, so "unsuccessful" read as a success and an
        # otherwise-clean check came back CLEAR.
        matches = [{"project": "OldDrop", "similarity": 1.0,
                    "match_type": "exact_contract", "outcome": "unsuccessful",
                    "failure_reason": "mint reverted", "mint_method": None}]
        verdict, reasons = evaluate(base_checks(), VERIFIED, matches, CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("HISTORY_EXACT_FAILURE", codes(reasons))
        self.assertNotIn("HISTORY_EXACT_SUCCESS", codes(reasons))

    def test_success_outcome_tokens_still_info(self):
        matches = [{"project": "ACAT", "similarity": 1.0,
                    "match_type": "exact_contract",
                    "outcome": "success_free_mint_executed",
                    "failure_reason": None, "mint_method": None}]
        verdict, reasons = evaluate(base_checks(), VERIFIED, matches, CHEAP_GAS)
        self.assertEqual(verdict, "CLEAR")
        self.assertIn("HISTORY_EXACT_SUCCESS", codes(reasons))

    def test_shape_match_failure_is_caution(self):
        # Spec fix: the caution rule is "a matching past failure in the
        # experience library" - not limited to exact contract matches.
        matches = [{"project": "BLOB-like", "similarity": 0.8,
                    "match_type": "abi_shape", "outcome": "sold_out",
                    "failure_reason": "supply exhausted", "mint_method": None}]
        verdict, reasons = evaluate(base_checks(), VERIFIED, matches, CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("HISTORY_SHAPE_FAILURE", codes(reasons))

    def test_shape_match_success_stays_info(self):
        matches = [{"project": "AGENT MAO-like", "similarity": 0.9,
                    "match_type": "abi_shape", "outcome": "success_user_minted",
                    "failure_reason": None, "mint_method": None}]
        verdict, reasons = evaluate(base_checks(), VERIFIED, matches, CHEAP_GAS)
        self.assertEqual(verdict, "CLEAR")
        self.assertIn("HISTORY_SHAPE_MATCH", codes(reasons))


class TestGasHelpers(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(band_for(0.08), "CHEAP")
        self.assertEqual(band_for(0.143), "NORMAL")
        self.assertEqual(band_for(0.335), "EXPENSIVE")
        self.assertEqual(band_for(0.5), "VERY_EXPENSIVE")

    def test_wei_to_native(self):
        self.assertEqual(wei_to_native(0), "0")
        self.assertEqual(wei_to_native(10 ** 18), "1")
        self.assertEqual(wei_to_native(None), "unknown")


if __name__ == "__main__":
    unittest.main()

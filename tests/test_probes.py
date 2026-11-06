import unittest

from preflight_core.keccak import selector
from preflight_core.probes import MINT_CANDIDATES, probe_contract
from preflight_core.verdict import evaluate

VERIFIED = {"status": "exact_match", "source": "sourcify"}
CHEAP_GAS = {"ok": True, "gwei_median": 0.08, "band": "CHEAP", "native_usd": 883.9}


def code_with(*sigs):
    """Fake runtime bytecode whose hex contains the given selectors."""
    return "".join(selector(s) for s in sigs)


class FakeRpc:
    """Stand-in for rpc.Rpc: canned bytecode, scripted eth_call results.

    call_result may be a dict returned for every eth_call, or a callable
    (to, data, from_addr, value_wei) -> result for per-call scripting.
    """

    def __init__(self, code_hex, call_result=None):
        # code_hex=None models a failed code read: rpc.Rpc returns
        # None for eth_getCode when every endpoint failed in transport.
        self._code = None if code_hex is None else "0x" + code_hex
        self._call_result = call_result

    def call(self, method, params):
        if method == "eth_getCode":
            return self._code
        if method == "eth_getStorageAt":
            return "0x" + "00" * 32
        if method == "eth_blockNumber":
            return "0x100"
        return None

    def eth_call(self, to, data, from_addr=None, value_wei=0):
        if callable(self._call_result):
            return self._call_result(to, data, from_addr, value_wei)
        return self._call_result


def codes(reasons):
    return {r["code"] for r in reasons}


class TestSimulationEvidenceRecording(unittest.TestCase):
    """Regression tests for the 2026-11-06 review: probes recorded both
    RPC outcome kinds (__revert__ and __error__) as result "revert", so
    a network outage manufactured a BLOCKED verdict."""

    def test_transport_error_is_recorded_as_error_not_revert(self):
        rpc = FakeRpc(code_with("mint()"),
                      {"__error__": "all endpoints unreachable"})
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        attempts = checks["simulation"]["attempts"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["result"], "error")
        self.assertTrue(checks["simulation"]["any_error"])
        self.assertFalse(checks["simulation"]["any_revert"])

    def test_outage_does_not_produce_a_blocked_verdict(self):
        rpc = FakeRpc(code_with("mint()"),
                      {"__error__": "all endpoints unreachable"})
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        verdict, reasons = evaluate(checks, {"status": "none"}, [], CHEAP_GAS)
        self.assertNotEqual(verdict, "BLOCKED")
        self.assertIn("SIMULATION_UNKNOWN", codes(reasons))
        self.assertNotIn("SIMULATION_REVERTS", codes(reasons))
        self.assertNotIn("UNVERIFIED_AND_REVERTING", codes(reasons))

    def test_deterministic_revert_is_still_recorded_as_revert(self):
        rpc = FakeRpc(code_with("mint()"),
                      {"__revert__": "execution reverted: not open"})
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        attempts = checks["simulation"]["attempts"]
        self.assertEqual(attempts[0]["result"], "revert")
        verdict, reasons = evaluate(checks, {"status": "none"}, [], CHEAP_GAS)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("UNVERIFIED_AND_REVERTING", codes(reasons))

    def test_mixed_revert_and_error_is_not_all_reverted(self):
        def handler(to, data, from_addr, value_wei):
            if data.startswith("0x" + selector("mint()")):
                return {"__revert__": "execution reverted"}
            return {"__error__": "endpoint timeout"}

        rpc = FakeRpc(code_with("mint()", "claim()"), handler)
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        results = {a["function"]: a["result"]
                   for a in checks["simulation"]["attempts"]}
        self.assertEqual(results, {"mint()": "revert", "claim()": "error"})
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertNotIn("SIMULATION_REVERTS", codes(reasons))
        self.assertIn("SIMULATION_UNKNOWN", codes(reasons))


def word(n):
    return "0x" + "%064x" % n


def scripted(answers):
    """Build a FakeRpc eth_call handler from {signature: result}."""
    def handler(to, data, from_addr, value_wei):
        for sig, res in answers.items():
            if data.startswith("0x" + selector(sig)):
                return res
        return None
    return handler


class TestTotalMintedSupply(unittest.TestCase):
    """Regression tests for the 2026-11-06 review (Spec finding 2):
    totalMinted() sat in the selector fingerprint but was never probed,
    so contracts exposing only totalMinted() reported supply unknown
    and could never trigger SOLD_OUT."""

    def test_total_minted_fallback_judges_sold_out(self):
        rpc = FakeRpc(
            code_with("totalSupply()", "totalMinted()", "maxSupply()"),
            scripted({
                "totalSupply()": {"__revert__": "execution reverted"},
                "totalMinted()": word(10000),
                "maxSupply()": word(10000),
            }))
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        supply = checks["supply"]
        self.assertEqual(supply["total_supply"], 10000)
        self.assertEqual(supply["total_minted"], 10000)
        self.assertEqual(supply["total_supply_via"], "totalMinted()")
        self.assertEqual(supply["remaining"], 0)
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("SOLD_OUT", codes(reasons))

    def test_total_supply_stays_primary_when_it_answers(self):
        rpc = FakeRpc(
            code_with("totalSupply()", "totalMinted()", "maxSupply()"),
            scripted({
                "totalSupply()": word(500),
                "totalMinted()": word(700),
                "maxSupply()": word(10000),
            }))
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        supply = checks["supply"]
        self.assertEqual(supply["total_supply"], 500)
        self.assertEqual(supply["total_minted"], 700)
        self.assertEqual(supply["total_supply_via"], "totalSupply()")
        self.assertEqual(supply["remaining"], 9500)


class TestDeploymentEvidence(unittest.TestCase):
    """Regression tests for the DEPLOYMENT_UNKNOWN fix (2026-11-06):
    probe_contract used to collapse a total eth_getCode transport
    failure (Rpc returns None) into "no code", so an RPC outage -
    e.g. endpoints truncating large bytecode responses - manufactured
    a NOT_A_CONTRACT block for contracts that were never actually
    read. The deployment sibling of the SIMULATION_UNKNOWN fix."""

    ADDR = "0x0000000000000000000000000000000000000001"

    def test_failed_code_read_is_recorded_as_unknown(self):
        checks = probe_contract(FakeRpc(None), self.ADDR)
        deployment = checks["deployment"]
        self.assertTrue(deployment["code_read_failed"])
        self.assertIsNone(deployment["is_contract"])
        self.assertIsNone(deployment["code_size_bytes"])
        # Contract-dependent probes degrade by not running at all:
        # nothing downstream may fabricate sections from unread code.
        self.assertEqual(checks["selectors"], [])
        self.assertNotIn("simulation", checks)
        self.assertNotIn("identity", checks)

    def test_failed_code_read_is_caution_not_not_a_contract(self):
        checks = probe_contract(FakeRpc(None), self.ADDR)
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("DEPLOYMENT_UNKNOWN", codes(reasons))
        self.assertNotIn("NOT_A_CONTRACT", codes(reasons))

    def test_failed_code_read_with_unverified_source_still_caution(self):
        checks = probe_contract(FakeRpc(None), self.ADDR)
        verdict, reasons = evaluate(checks, {"status": "none"}, [], CHEAP_GAS)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("DEPLOYMENT_UNKNOWN", codes(reasons))
        self.assertIn("UNVERIFIED_SOURCE", codes(reasons))
        self.assertNotIn("UNVERIFIED_AND_REVERTING", codes(reasons))

    def test_genuine_empty_code_still_blocks_as_not_a_contract(self):
        checks = probe_contract(FakeRpc(""), self.ADDR)
        deployment = checks["deployment"]
        self.assertFalse(deployment["code_read_failed"])
        self.assertFalse(deployment["is_contract"])
        self.assertEqual(deployment["code_size_bytes"], 0)
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("NOT_A_CONTRACT", codes(reasons))
        self.assertNotIn("DEPLOYMENT_UNKNOWN", codes(reasons))

    def test_bytecode_present_records_a_clean_read(self):
        checks = probe_contract(FakeRpc(code_with("mint()"),
                                        {"__revert__": "execution reverted"}),
                                self.ADDR)
        deployment = checks["deployment"]
        self.assertFalse(deployment["code_read_failed"])
        self.assertTrue(deployment["is_contract"])
        self.assertGreater(deployment["code_size_bytes"], 0)


class TestCommitRevealHonesty(unittest.TestCase):
    """Regression test for the review's Spec High finding: reveal-only
    (commit/reveal) contracts must land on the NO_KNOWN_MINT_FUNCTION
    caution, not be simulated into a revert-driven BLOCKED."""

    def test_reveal_is_not_a_mint_candidate(self):
        self.assertNotIn("reveal(address)", MINT_CANDIDATES)

    def test_reveal_only_contract_gets_the_caution(self):
        rpc = FakeRpc(code_with("reveal(address)"),
                      {"__revert__": "execution reverted"})
        checks = probe_contract(rpc, "0x0000000000000000000000000000000000000001")
        self.assertEqual(checks["simulation"]["candidate_functions"], [])
        self.assertEqual(checks["simulation"]["attempts"], [])
        verdict, reasons = evaluate(checks, VERIFIED, [], CHEAP_GAS)
        self.assertIn("NO_KNOWN_MINT_FUNCTION", codes(reasons))
        self.assertNotEqual(verdict, "BLOCKED")


if __name__ == "__main__":
    unittest.main()

"""Elysium (chain ID 99901) adapter tests.

The chain was registered on 2026-11-08, the day before Kinetiq's
mainnet launch, with the chain ID official but no public RPC
published. Two states matter and both are pinned here:

- pre-launch (no RPC configured): a check must return CAUTION with
  the gap named (RPC_NOT_PUBLISHED) and the verdict-schema skeleton -
  never a crash, never a fake pass, never a contract verdict built on
  reads that never happened;
- activated (PREFLIGHT_ELYSIUM_RPC set): the normal EVM pipeline runs,
  and the verification stage reports Sourcify's "not_indexed" for the
  chain instead of a checked-and-unverified "none".

The activation test points the RPC at a refused localhost port, so
every read fails at the transport level instantly and no external
network is touched; the verdict discipline for that state
(DEPLOYMENT_UNKNOWN, a caution) is exactly what it pins.
"""
import os
import unittest

from preflight_core.chains import BASE, BSC, CHAINS, ELYSIUM
from preflight_core.check import effective_rpcs, rpc_env_var, run_check
from preflight_core.cli import render_text
from preflight_core.verdict import evaluate

ADDR = "0x28a38A372639E8C481aD41B9B16E52c3c6366ACC"
CHECKS_KEYS = {"deployment", "verification", "identity", "supply", "price",
               "open_status", "wallet", "simulation", "gas"}


def _codes(report):
    return {r["code"] for r in report["reasons"]}


class _EnvGuard(unittest.TestCase):
    """Keep PREFLIGHT_ELYSIUM_RPC under the test's control."""

    def setUp(self):
        self._saved = os.environ.pop("PREFLIGHT_ELYSIUM_RPC", None)

    def tearDown(self):
        os.environ.pop("PREFLIGHT_ELYSIUM_RPC", None)
        if self._saved is not None:
            os.environ["PREFLIGHT_ELYSIUM_RPC"] = self._saved


class TestRegistration(_EnvGuard):
    def test_elysium_registered_with_official_chain_id(self):
        self.assertIs(CHAINS["elysium"], ELYSIUM)
        self.assertEqual(ELYSIUM.chain_id, 99901)
        self.assertEqual(ELYSIUM.kind, "evm")
        self.assertEqual(ELYSIUM.native_symbol, "HYPE")

    def test_no_rpc_published_yet_and_sourcify_not_indexed(self):
        # The adapter must never ship a guessed endpoint.
        self.assertEqual(ELYSIUM.rpcs, ())
        self.assertFalse(ELYSIUM.sourcify_indexed)

    def test_existing_chains_keep_sourcify_default(self):
        self.assertTrue(BSC.sourcify_indexed)
        self.assertTrue(BASE.sourcify_indexed)


class TestEffectiveRpcs(_EnvGuard):
    def test_env_var_name(self):
        self.assertEqual(rpc_env_var(ELYSIUM), "PREFLIGHT_ELYSIUM_RPC")
        self.assertEqual(rpc_env_var(BSC), "PREFLIGHT_BSC_RPC")

    def test_no_env_falls_back_to_chain_list(self):
        self.assertEqual(effective_rpcs(ELYSIUM), ())
        self.assertEqual(effective_rpcs(BSC), BSC.rpcs)

    def test_env_single_and_comma_separated(self):
        os.environ["PREFLIGHT_ELYSIUM_RPC"] = "https://rpc.example.invalid"
        self.assertEqual(effective_rpcs(ELYSIUM), ("https://rpc.example.invalid",))
        os.environ["PREFLIGHT_ELYSIUM_RPC"] = "https://a.invalid, https://b.invalid"
        self.assertEqual(effective_rpcs(ELYSIUM),
                         ("https://a.invalid", "https://b.invalid"))


class TestPreLaunchState(_EnvGuard):
    def test_check_is_honest_caution_with_named_gap(self):
        report, code = run_check(ADDR, chain_key="elysium")
        self.assertEqual(code, 0)
        self.assertEqual(report["verdict"], "CAUTION")
        self.assertEqual(report["contract"], ADDR)
        self.assertEqual(report["chain"]["chain_id"], 99901)
        self.assertIn("RPC_NOT_PUBLISHED", _codes(report))
        reason = report["reasons"][0]
        self.assertEqual(reason["level"], "caution")
        self.assertIn("PREFLIGHT_ELYSIUM_RPC", reason["message"])
        # No evidence was gathered, and the schema says so: the full
        # skeleton with every section null, no matches, no fingerprint.
        self.assertEqual(set(report["checks"]), CHECKS_KEYS)
        self.assertTrue(all(v is None for v in report["checks"].values()))
        self.assertEqual(report["library_matches"], [])
        self.assertEqual(report["selector_fingerprint"], [])

    def test_card_renders_the_gap_not_empty_findings(self):
        report, _ = run_check(ADDR, chain_key="elysium")
        card = render_text(report)
        self.assertIn("Elysium", card)
        self.assertIn("VERDICT    CAUTION", card)
        self.assertIn("PREFLIGHT_ELYSIUM_RPC", card)
        self.assertNotIn("unknown name", card)


class TestActivatedState(_EnvGuard):
    def test_pipeline_runs_and_verification_is_not_indexed(self):
        # Refused port: every read fails at transport level, instantly.
        os.environ["PREFLIGHT_ELYSIUM_RPC"] = "http://127.0.0.1:9"
        report, code = run_check(ADDR, chain_key="elysium")
        self.assertEqual(code, 0)
        self.assertNotIn("RPC_NOT_PUBLISHED", _codes(report))
        # A failed read is DEPLOYMENT_UNKNOWN - a caution about the
        # network, never a contract verdict.
        self.assertIn("DEPLOYMENT_UNKNOWN", _codes(report))
        self.assertEqual(report["verdict"], "CAUTION")
        self.assertEqual(report["checks"]["verification"]["status"], "not_indexed")
        self.assertIn("SOURCE_NOT_INDEXED", _codes(report))


class TestNotIndexedVerdict(unittest.TestCase):
    def test_not_indexed_is_its_own_caution(self):
        checks = {
            "deployment": {"is_contract": True, "is_proxy_eip1967": False,
                           "implementation": None},
            "identity": {"type": "ERC721", "name": "X", "symbol": "X",
                         "owner": None, "paused": False},
            "supply": {"total_supply": 10, "max_supply": 100, "remaining": 90},
            "price": {"wei": 0, "native": "0", "via_function": "mintPrice()",
                      "all_probes_native": {"mintPrice()": "0"},
                      "probes_disagree": False},
            "open_status": {"probes": {"mintOpen()": True}, "open": True,
                            "paused": False},
            "wallet": {"wallet": "0x0", "balance_of": 0, "minted_counts": {},
                       "flags": {}},
            "simulation": {"attempts": [{"function": "mint()", "value_wei": 0,
                                         "result": "success", "reason": None}],
                           "candidate_functions": ["mint()"],
                           "any_success": True, "free_success": True},
        }
        gas = {"ok": True, "gwei_median": 0.08, "band": None, "native_usd": None}
        verdict, reasons = evaluate(checks, {"status": "not_indexed"}, [], gas)
        codes = {r["code"] for r in reasons}
        self.assertIn("SOURCE_NOT_INDEXED", codes)
        self.assertNotIn("UNVERIFIED_SOURCE", codes)
        self.assertNotIn("VERIFICATION_UNCHECKED", codes)
        self.assertEqual(verdict, "CAUTION")


if __name__ == "__main__":
    unittest.main()

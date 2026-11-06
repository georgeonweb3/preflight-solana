import unittest
from unittest import mock

from preflight_core import cli

ADDR = "0x28a38A372639E8C481aD41B9B16E52c3c6366ACC"


class TestWalletValidation(unittest.TestCase):
    """Regression tests for the 2026-11-06 review (Spec finding 2):
    --wallet was never address-validated, so a typo silently degraded
    every wallet-specific answer in the report. It is now a usage
    error, raised before any check runs."""

    def test_short_wallet_is_a_usage_error(self):
        with mock.patch.object(cli, "run_check") as rc:
            with self.assertRaises(SystemExit) as cm:
                cli.main(["check", ADDR, "--wallet", "0x1234"])
        self.assertEqual(cm.exception.code, 2)
        rc.assert_not_called()

    def test_non_hex_wallet_is_a_usage_error(self):
        with mock.patch.object(cli, "run_check") as rc:
            with self.assertRaises(SystemExit) as cm:
                cli.main(["check", ADDR, "--wallet", "0x" + "zz" * 20])
        self.assertEqual(cm.exception.code, 2)
        rc.assert_not_called()

    def test_valid_wallet_reaches_run_check(self):
        fake = ({"verdict": "UNRESOLVED_INPUT"}, 2)
        with mock.patch.object(cli, "run_check", return_value=fake) as rc:
            code = cli.main(["check", ADDR, "--wallet", ADDR, "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(rc.call_args.kwargs["wallet"], ADDR)


def _not_a_contract_report():
    """The partial-checks shape probe_contract returns when the
    address has no code: probing stops at the bytecode, so only
    deployment / verification / gas sections exist."""
    return {
        "version": "0.1.2", "chain": {"name": "BNB Smart Chain",
                                      "native_symbol": "BNB"},
        "wallet": None, "verdict": "BLOCKED",
        "resolution": {"contract": ADDR, "note": "bare address"},
        "checks": {
            "deployment": {"is_contract": False, "code_size_bytes": 0,
                           "is_proxy_eip1967": False,
                           "implementation": None, "block_at_check": 123},
            "verification": {"status": "none", "source": "sourcify"},
            "gas": {"snapshot": {"ok": False}, "mint_estimate": None},
        },
        "reasons": [{"code": "NOT_A_CONTRACT", "level": "block",
                     "message": "address has no contract code"}],
        "identification_path": "unfamiliar contract shape",
        "total_seconds": 1.0,
    }


class TestRenderText(unittest.TestCase):
    """Regression test (found during the v0.1.2 live re-checks): the
    text card indexed every checks section directly and crashed with
    KeyError on a not-a-contract report - the default output mode
    could not render one of the six block verdicts."""

    def test_not_a_contract_report_renders(self):
        card = cli.render_text(_not_a_contract_report())
        self.assertIn("VERDICT    BLOCKED", card)
        self.assertIn("address has no contract code", card)


if __name__ == "__main__":
    unittest.main()

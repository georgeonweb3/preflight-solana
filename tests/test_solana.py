"""Unit tests for the Solana adapter: parsing and verdict mapping.

No network: every payload comes from tests/fixtures/. The USDC and
burncoin fixtures were recorded live on 2026-11-06 (USDC mint
EPjFWdd5... and the Token-2022 memecoin 8P8BNixE..., lightly trimmed:
GoPlus pool/holder lists truncated). The largest-accounts fixture
follows the documented getTokenLargestAccounts response shape - the
public endpoint rate-limited that method throughout the recording
session (HTTP 429 / method blocked on every free endpoint tried),
which is itself covered here as the unavailable path.
"""
import json
import unittest
from pathlib import Path

from unittest.mock import patch

from preflight_core.solana import (
    is_solana_address, parse_goplus, parse_largest_accounts,
    parse_mint_account, parse_rugcheck, probe_solana, resolve_solana_input,
)
from preflight_core.solana_verdict import evaluate_solana

FIXTURES = Path(__file__).parent / "fixtures"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BURNCOIN = "8P8BNixECEJw33Xg2WmqFX2YjcMFVa2PkvaU7Y1Spump"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def account_value(name):
    return load(name)["result"]["value"]


def codes(reasons):
    return {r["code"] for r in reasons}


def checks_from(deployment_status="ok", mint_authority=None, freeze_authority=None,
                extensions=None, holders=None, rugcheck=None, goplus=None,
                supply=1_000_000, wallet=None):
    """A clean Solana checks dict; tests poke one hole at a time."""
    return {
        "deployment": {"status": deployment_status,
                       "is_mint": deployment_status == "ok"},
        "identity": {"name": "Test", "symbol": "TST", "type": "SPL token (legacy Token program)"},
        "supply": {"total_supply": supply, "supply_ui": float(supply), "decimals": 6},
        "wallet": wallet,
        "solana": {
            "authorities": {"mint_authority": mint_authority,
                            "freeze_authority": freeze_authority},
            "extensions": extensions or [],
            "holders": holders or {"status": "ok", "source": "rpc",
                                   "top1_pct": 3.0, "top10_pct": 15.0},
            "rugcheck": rugcheck if rugcheck is not None else
                        {"status": "ok", "risks": [], "score": 100,
                         "score_normalised": 5, "rugged": False},
            "goplus": goplus if goplus is not None else
                      {"status": "ok", "mintable": False, "freezable": False,
                       "closable": False, "balance_mutable": False,
                       "metadata_mutable": False, "non_transferable": False,
                       "transfer_fee_rate": None, "transfer_hook": False},
        },
    }


class TestAddressValidation(unittest.TestCase):
    def test_real_mints_are_valid(self):
        self.assertTrue(is_solana_address(USDC))
        self.assertTrue(is_solana_address(BURNCOIN))

    def test_evm_address_is_not_solana(self):
        self.assertFalse(is_solana_address("0x28a38A372639E8C481aD41B9B16E52c3c6366ACC"))
        self.assertFalse(is_solana_address("0x0000000000000000000000000000000000000000"))

    def test_non_base58_and_short_inputs_rejected(self):
        self.assertFalse(is_solana_address("0OIl-not-base58"))
        self.assertFalse(is_solana_address("abc"))
        self.assertFalse(is_solana_address(""))
        self.assertFalse(is_solana_address(None))

    def test_resolve_rejects_evm_with_a_clear_note(self):
        mint, note, _ = resolve_solana_input("0x28a38A372639E8C481aD41B9B16E52c3c6366ACC")
        self.assertIsNone(mint)
        self.assertIn("EVM", note)

    def test_resolve_rejects_garbage(self):
        mint, note, _ = resolve_solana_input("definitely not an address!!")
        self.assertIsNone(mint)
        self.assertIn("base58", note)

    def test_resolve_accepts_bare_mint(self):
        mint, note, candidates = resolve_solana_input(f"  {USDC}  ")
        self.assertEqual(mint, USDC)
        self.assertEqual(candidates, [USDC])


class TestMintAccountParsing(unittest.TestCase):
    def test_usdc_spl_token(self):
        info = parse_mint_account(account_value("solana_usdc_account.json"))
        self.assertEqual(info["status"], "ok")
        self.assertEqual(info["token_program"], "spl-token")
        self.assertEqual(info["decimals"], 6)
        self.assertEqual(info["supply_raw"], 8846707979439730)
        # Circle keeps both authorities on USDC - the recorded truth.
        self.assertEqual(info["mint_authority"], "BJE5MMbqXjVwjAF7oxwPYXnTXDyspzZyt4vwenNw5ruG")
        self.assertEqual(info["freeze_authority"], "7dGbd2QZcCKcTndnHcTL8q7SMVXAkp688NTQYwrRCrar")
        self.assertEqual(info["extensions"], [])

    def test_burncoin_token2022_extensions(self):
        info = parse_mint_account(account_value("solana_burncoin_account.json"))
        self.assertEqual(info["status"], "ok")
        self.assertEqual(info["token_program"], "token-2022")
        self.assertIsNone(info["mint_authority"])
        self.assertIsNone(info["freeze_authority"])
        names = {e["name"] for e in info["extensions"]}
        self.assertEqual(names, {"metadataPointer", "tokenMetadata"})

    def test_missing_account_is_not_found(self):
        self.assertEqual(parse_mint_account(None)["status"], "read_failed")

    def test_non_mint_account(self):
        value = {"owner": "11111111111111111111111111111111",
                 "data": {"parsed": {"type": "account", "info": {}}}}
        parsed = parse_mint_account(value)
        self.assertEqual(parsed["status"], "not_a_mint")
        self.assertEqual(parsed["account_type"], "account")


class TestLargestAccounts(unittest.TestCase):
    def test_top_shares_math(self):
        value = load("solana_largest_accounts.json")["result"]["value"]
        holders = parse_largest_accounts(value, 1_000_000)
        self.assertEqual(holders["status"], "ok")
        self.assertEqual(holders["top1_pct"], 40.0)
        self.assertEqual(holders["top10_pct"], 62.0)

    def test_empty_or_missing_is_unavailable(self):
        self.assertEqual(parse_largest_accounts(None, 100)["status"], "unavailable")
        self.assertEqual(parse_largest_accounts([], 100)["status"], "unavailable")
        self.assertEqual(parse_largest_accounts([{"amount": "5"}], 0)["status"], "unavailable")


class TestRugCheckParsing(unittest.TestCase):
    def test_usdc_clean(self):
        rc = parse_rugcheck(load("solana_usdc_rugcheck_summary.json"))
        self.assertEqual(rc["status"], "ok")
        self.assertEqual(rc["risks"], [])
        self.assertEqual(rc["score_normalised"], 1)

    def test_burncoin_danger(self):
        rc = parse_rugcheck(load("solana_burncoin_rugcheck_summary.json"))
        self.assertEqual(rc["status"], "ok")
        danger = [r for r in rc["risks"] if r["level"] == "danger"]
        self.assertEqual(len(danger), 1)
        self.assertEqual(danger[0]["name"], "Creator history of rugged tokens")

    def test_error_bodies_are_unavailable_not_clean(self):
        self.assertEqual(parse_rugcheck(None)["status"], "unavailable")
        self.assertEqual(parse_rugcheck({"error": "not found"})["status"], "unavailable")
        self.assertEqual(parse_rugcheck({})["status"], "unavailable")


class TestGoPlusParsing(unittest.TestCase):
    def test_usdc_flags_and_metadata(self):
        gp = parse_goplus(load("solana_usdc_goplus.json"), USDC)
        self.assertEqual(gp["status"], "ok")
        self.assertEqual(gp["name"], "USD Coin")
        self.assertEqual(gp["symbol"], "USDC")
        self.assertIs(gp["mintable"], True)
        self.assertIs(gp["freezable"], True)
        self.assertIs(gp["closable"], False)
        self.assertIs(gp["non_transferable"], False)
        self.assertEqual(gp["holder_count"], 9095715)

    def test_usdc_holders_from_goplus(self):
        gp = parse_goplus(load("solana_usdc_goplus.json"), USDC)
        holders = gp["holders"]
        self.assertIsNotNone(holders)
        self.assertEqual(holders["source"], "goplus")
        # Recorded top holder: 941,845,580 of 7,757,717,865 = 12.14%.
        self.assertAlmostEqual(holders["top1_pct"], 12.14, places=1)
        self.assertGreater(holders["top10_pct"], holders["top1_pct"])

    def test_burncoin_revoked(self):
        gp = parse_goplus(load("solana_burncoin_goplus.json"), BURNCOIN)
        self.assertEqual(gp["status"], "ok")
        self.assertIs(gp["mintable"], False)
        self.assertIs(gp["freezable"], False)
        self.assertIsNone(gp["holders"])  # GoPlus carried no holder table

    def test_failures_are_unavailable(self):
        self.assertEqual(parse_goplus(None, USDC)["status"], "unavailable")
        self.assertEqual(parse_goplus({"code": 2, "result": {}}, USDC)["status"], "unavailable")
        self.assertEqual(parse_goplus({"code": 1, "result": {}}, USDC)["status"], "unavailable")
        self.assertEqual(parse_goplus({"code": 1, "result": {USDC: {}}}, USDC)["status"], "unavailable")


class TestSolanaVerdict(unittest.TestCase):
    def test_clean_revoked_mint_is_clear(self):
        verdict, reasons = evaluate_solana(checks_from())
        self.assertEqual(verdict, "CLEAR")
        self.assertIn("AUTHORITIES_REVOKED", codes(reasons))

    def test_usdc_shape_is_caution_not_blocked(self):
        """Live authorities on an otherwise clean, corroborated token:
        stated as cautions, never a block - the USDC calibration."""
        checks = checks_from(
            mint_authority="BJE5MMbqXjVwjAF7oxwPYXnTXDyspzZyt4vwenNw5ruG",
            freeze_authority="7dGbd2QZcCKcTndnHcTL8q7SMVXAkp688NTQYwrRCrar",
            holders={"status": "ok", "source": "goplus",
                     "top1_pct": 12.14, "top10_pct": 23.5},
            goplus={"status": "ok", "mintable": True, "freezable": True,
                    "closable": False, "balance_mutable": False,
                    "metadata_mutable": True, "non_transferable": False,
                    "transfer_fee_rate": None, "transfer_hook": False})
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("MINT_AUTHORITY_LIVE", codes(reasons))
        self.assertIn("FREEZE_AUTHORITY_LIVE", codes(reasons))
        self.assertNotIn("block", {r["level"] for r in reasons})

    def test_rugcheck_danger_blocks(self):
        checks = checks_from(rugcheck={
            "status": "ok", "score": 93601, "score_normalised": 78, "rugged": False,
            "risks": [{"name": "Creator history of rugged tokens", "level": "danger",
                       "description": "Creator has a history of rugging tokens.", "score": 93600}]})
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("RUGCHECK_DANGER", codes(reasons))

    def test_rugged_flag_blocks(self):
        checks = checks_from(rugcheck={
            "status": "ok", "risks": [], "score": 1, "score_normalised": 1, "rugged": True})
        verdict, _ = evaluate_solana(checks)
        self.assertEqual(verdict, "BLOCKED")

    def test_not_a_mint_blocks(self):
        verdict, reasons = evaluate_solana(checks_from(deployment_status="not_a_mint"))
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("NOT_A_TOKEN_MINT", codes(reasons))
        verdict, _ = evaluate_solana(checks_from(deployment_status="not_found"))
        self.assertEqual(verdict, "BLOCKED")

    def test_failed_read_is_caution_never_clear_or_block(self):
        checks = checks_from(deployment_status="read_failed",
                             holders={"status": "unavailable", "source": "rpc"},
                             rugcheck={"status": "unavailable"},
                             goplus={"status": "unavailable"})
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("MINT_READ_FAILED", codes(reasons))

    def test_unavailable_sources_cap_at_caution(self):
        checks = checks_from(rugcheck={"status": "unavailable"},
                             goplus={"status": "unavailable"})
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("RUGCHECK_UNAVAILABLE", codes(reasons))
        self.assertIn("GOPLUS_UNAVAILABLE", codes(reasons))

    def test_mint_authority_plus_whale_blocks(self):
        checks = checks_from(
            mint_authority="SomeMintAuthority111111111111111111111111",
            holders={"status": "ok", "source": "rpc", "top1_pct": 30.0, "top10_pct": 45.0})
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("MINT_AUTHORITY_CONCENTRATED", codes(reasons))

    def test_single_holder_majority_blocks(self):
        checks = checks_from(holders={"status": "ok", "source": "rpc",
                                      "top1_pct": 55.0, "top10_pct": 70.0})
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("SINGLE_HOLDER_MAJORITY", codes(reasons))

    def test_permanent_delegate_blocks(self):
        checks = checks_from(extensions=[
            {"name": "permanentDelegate",
             "state": {"authority": "SomeDelegate11111111111111111111111111111"}}])
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("PERMANENT_DELEGATE", codes(reasons))

    def test_transfer_fee_and_hook_are_cautions(self):
        checks = checks_from(extensions=[
            {"name": "transferFeeConfig",
             "state": {"newerTransferFee": {"transferFeeBasisPoints": 250}}},
            {"name": "transferHook",
             "state": {"programId": "HookProgram111111111111111111111111111111"}}])
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("TRANSFER_FEE", codes(reasons))
        self.assertIn("TRANSFER_HOOK", codes(reasons))

    def test_warn_risk_is_caution(self):
        checks = checks_from(rugcheck={
            "status": "ok", "score": 500, "score_normalised": 40, "rugged": False,
            "risks": [{"name": "Mutable metadata", "level": "warn",
                       "description": "", "score": 500}]})
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("RUGCHECK_WARN", codes(reasons))

    def test_authority_disagreement_is_caution(self):
        checks = checks_from(goplus={
            "status": "ok", "mintable": True, "freezable": False, "closable": False,
            "balance_mutable": False, "metadata_mutable": False,
            "non_transferable": False, "transfer_fee_rate": None, "transfer_hook": False})
        verdict, reasons = evaluate_solana(checks)
        self.assertEqual(verdict, "CAUTION")
        self.assertIn("AUTHORITY_SOURCES_DISAGREE", codes(reasons))


class _StubRpc:
    """Stands in for SolanaRpc: returns one canned result per call."""

    def __init__(self, result):
        self._result = result
        self.errors = []

    def call(self, method, params):
        return self._result


def _probe_with(account_result):
    with patch("preflight_core.solana.SolanaRpc",
               lambda endpoints: _StubRpc(account_result)), \
         patch("preflight_core.solana.fetch_rugcheck",
               lambda mint: ({"status": "unavailable"}, None)), \
         patch("preflight_core.solana.fetch_goplus",
               lambda mint: {"status": "unavailable", "holders": None}):
        return probe_solana(["https://example.invalid"], USDC)


class TestProbeExistenceSemantics(unittest.TestCase):
    """An answered null (account does not exist) and an unanswered
    call (transport failure) are different facts and must stay
    different all the way to the verdict - regression test for a live
    run that reported a nonexistent mint as a network failure."""

    def test_answered_null_is_not_found(self):
        out = _probe_with({"context": {"slot": 7}, "value": None})
        self.assertEqual(out["mint"]["status"], "not_found")
        self.assertEqual(out["slot"], 7)

    def test_silence_is_read_failed(self):
        out = _probe_with(None)
        self.assertEqual(out["mint"]["status"], "read_failed")


if __name__ == "__main__":
    unittest.main()

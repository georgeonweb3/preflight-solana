"""One full Preflight check for a Solana mint.

A separate pipeline from the EVM one in check.py, because the account
model is different - but the report it returns has the same shape
(schema preflight.verdict/1): the EVM-only sections (verification,
price, open status, simulation, gas) are present and null, which is
the schema's way of saying "this stage does not apply here", and the
Solana evidence lives under checks["solana"] plus the shared
deployment / identity / supply / wallet sections.

Read-only, like everything in Preflight: Solana JSON-RPC reads and
two keyless public safety APIs. Nothing is signed or sent.
"""
import time
from datetime import datetime, timezone

from . import __version__
from .solana import probe_solana, resolve_solana_input
from .solana_verdict import evaluate_solana

SCHEMA = "preflight.verdict/1"

_CHECK_KEYS = ("deployment", "verification", "identity", "supply", "price",
               "open_status", "wallet", "simulation", "gas")


class _Timer:
    def __init__(self):
        self.steps = {}
        self._t = None
        self._name = None

    def start(self, name):
        self.stop()
        self._name, self._t = name, time.perf_counter()

    def stop(self):
        if self._t is not None:
            self.steps[self._name] = round(time.perf_counter() - self._t, 3)
            self._t = self._name = None


def _supply_ui(raw, decimals):
    if raw is None or decimals is None:
        return None
    return raw / (10 ** decimals)


def run_solana_check(target, chain, wallet=None):
    """Returns (report_dict, exit_code). Exit 2 = input unresolved."""
    timer = _Timer()
    t0 = time.perf_counter()
    report = {
        "tool": "preflight",
        "version": __version__,
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": target,
        "chain": {"key": chain.key, "name": chain.name,
                  "chain_id": chain.chain_id, "native_symbol": chain.native_symbol},
        "wallet": wallet,
        "mode": "READ-ONLY: Solana JSON-RPC reads + public safety APIs (RugCheck, GoPlus) - no signing, no approvals, no payments",
    }

    timer.start("resolve_input")
    mint, note, candidates = resolve_solana_input(target)
    timer.stop()
    report["resolution"] = {"contract": mint, "note": note, "all_candidates": candidates}
    if not mint:
        report["verdict"] = "UNRESOLVED_INPUT"
        report["reasons"] = [{
            "code": "UNRESOLVED_INPUT", "level": "block",
            "message": f"could not resolve a Solana mint address from the input ({note}) - nothing was guessed",
        }]
        report["contract"] = None
        report["checks"] = {k: None for k in _CHECK_KEYS}
        report["checks"]["solana"] = None
        report["library_matches"] = []
        report["selector_fingerprint"] = []
        report["identification_path"] = "input unresolved - nothing was probed"
        report["timings_seconds"] = timer.steps
        report["total_seconds"] = round(time.perf_counter() - t0, 3)
        return report, 2
    report["contract"] = mint

    timer.start("probe_solana")
    probe = probe_solana(chain.rpcs, mint, wallet)
    timer.stop()

    mint_info = probe["mint"]
    goplus = probe["goplus"]
    status = mint_info.get("status")
    decimals = mint_info.get("decimals")
    supply_raw = mint_info.get("supply_raw")

    checks = {k: None for k in _CHECK_KEYS}
    checks["deployment"] = {
        "status": status,
        "account_exists": status in ("ok", "not_a_mint"),
        "is_mint": status == "ok",
        "account_type": mint_info.get("account_type"),
        "owner": mint_info.get("owner"),
        "token_program": mint_info.get("token_program"),
        "slot": probe.get("slot"),
    }
    program_label = {"spl-token": "SPL token (legacy Token program)",
                     "token-2022": "Token-2022"}.get(mint_info.get("token_program"))
    checks["identity"] = {
        "name": goplus.get("name"),
        "symbol": goplus.get("symbol"),
        "type": program_label,
    }
    checks["supply"] = {
        "total_supply": supply_raw,
        "supply_ui": _supply_ui(supply_raw, decimals),
        "decimals": decimals,
        "max_supply": None,
        "remaining": None,
    }
    checks["wallet"] = probe.get("wallet")
    checks["solana"] = {
        "current_epoch": probe.get("current_epoch"),
        "authorities": {
            "mint_authority": mint_info.get("mint_authority"),
            "freeze_authority": mint_info.get("freeze_authority"),
        },
        "extensions": mint_info.get("extensions") or [],
        "holders": probe["holders"],
        "rugcheck": probe["rugcheck"],
        "goplus": {k: v for k, v in goplus.items() if k != "holders"},
        "rpc_errors": probe.get("rpc_errors") or [],
    }

    verdict, reasons = evaluate_solana(checks)
    report["checks"] = checks
    report["verdict"] = verdict
    report["reasons"] = reasons
    report["library_matches"] = []
    report["selector_fingerprint"] = []
    report["identification_path"] = (
        "experience library covers EVM mint contracts only - not consulted "
        "for a Solana mint; this verdict rests on live RPC reads plus the "
        "RugCheck and GoPlus reports")
    report["timings_seconds"] = timer.steps
    report["total_seconds"] = round(time.perf_counter() - t0, 3)
    return report, 0

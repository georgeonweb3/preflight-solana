"""One full Preflight check: resolve -> probe -> verify -> gas -> verdict.

This is the shared core every "door" uses. The mint-precheck CLI is the
first door; agent-seatbelt will call this same pipeline on a transaction
an agent proposes, instead of on a contract a human pasted.

All chain interaction is read-only. Timings are recorded per step because
the parent skill taught us a slow check is a check people stop running.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import __version__
from .chains import CHAINS
from .gas import estimate_call_cost, snapshot as gas_snapshot
from .library import SHAPE_THRESHOLD, load_library, match_library
from .probes import ZERO_ADDRESS, probe_contract
from .resolve import resolve_input
from .rpc import Rpc
from .sourcify import check_verification
from .verdict import evaluate

SCHEMA = "preflight.verdict/1"


def rpc_env_var(chain) -> str:
    """The environment variable that overrides a chain's RPC list."""
    return f"PREFLIGHT_{chain.key.upper()}_RPC"


def effective_rpcs(chain) -> tuple:
    """The chain's RPC endpoints after the environment override.

    PREFLIGHT_<CHAIN>_RPC (one URL, or several comma-separated)
    replaces the built-in list with no code change. It exists for
    chains whose public RPC is unpublished when the adapter ships -
    Elysium at its 2026-11-09 launch: the adapter registers the chain
    with an empty list, and setting PREFLIGHT_ELYSIUM_RPC to the
    official endpoint the moment Kinetiq publishes it activates the
    full pipeline.
    """
    raw = os.environ.get(rpc_env_var(chain), "")
    urls = tuple(u.strip() for u in raw.split(",") if u.strip())
    return urls or chain.rpcs


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


def run_check(target, chain_key="bsc", wallet=None, library_path=None):
    """Returns (report_dict, exit_code). Exit 2 = input unresolved."""
    chain = CHAINS[chain_key]
    if chain.kind == "solana":
        # Different account model: the EVM stages below (eth_call
        # probes, Sourcify, gas bands, selector fingerprints) do not
        # exist there. The Solana pipeline returns the same report
        # shape with those sections null.
        from .solana_check import run_solana_check
        return run_solana_check(target, chain, wallet=wallet)
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
        "mode": "READ-ONLY: eth_call / eth_getCode / eth_estimateGas only - no signing, no approvals, no payments",
    }

    timer.start("resolve_input")
    address, note, candidates = resolve_input(target, chain.explorer_domains)
    timer.stop()
    report["resolution"] = {"contract": address, "note": note, "all_candidates": candidates}
    if not address:
        report["verdict"] = "UNRESOLVED_INPUT"
        report["reasons"] = [{
            "code": "UNRESOLVED_INPUT", "level": "block",
            "message": f"could not resolve a contract address from the input ({note}) - nothing was guessed",
        }]
        # The verdict schema (preflight.verdict/1) has one shape for
        # every report, resolved or not: an unresolved report carries
        # the full skeleton with the evidence sections null (nothing
        # was probed) and the match/fingerprint lists empty, so a
        # consumer never has to guess which keys exist.
        report["contract"] = None
        report["checks"] = {k: None for k in (
            "deployment", "verification", "identity", "supply", "price",
            "open_status", "wallet", "simulation", "gas")}
        report["library_matches"] = []
        report["selector_fingerprint"] = []
        report["timings_seconds"] = timer.steps
        report["total_seconds"] = round(time.perf_counter() - t0, 3)
        return report, 2
    contract = address
    report["contract"] = contract

    rpcs = effective_rpcs(chain)
    if not rpcs:
        # Pre-launch state: the chain is registered but no RPC is
        # configured (for Elysium, Kinetiq has not published one yet).
        # Nothing on-chain was read, so there is no contract verdict
        # to give: CAUTION with the gap named, per the discipline that
        # an unanswered read is neither a pass nor a finding. The
        # report keeps the verdict-schema skeleton, evidence null.
        report["checks"] = {k: None for k in (
            "deployment", "verification", "identity", "supply", "price",
            "open_status", "wallet", "simulation", "gas")}
        report["library_matches"] = []
        report["selector_fingerprint"] = []
        report["identification_path"] = (
            "chain registered but not reachable yet - nothing was probed, "
            "so there is no live evidence and no history match for this contract")
        report["verdict"] = "CAUTION"
        report["reasons"] = [{
            "code": "RPC_NOT_PUBLISHED", "level": "caution",
            "message": (f"{chain.name} (chain ID {chain.chain_id}) is registered in Preflight, "
                        "but no RPC endpoint is configured - the chain operator has not "
                        "published a public RPC yet, so nothing on-chain could be read and "
                        "this contract was NOT checked. This is a gap in the chain's launch "
                        f"state, not a verdict on the contract. When the RPC is published, "
                        f"set {rpc_env_var(chain)}=<url> and re-run - no code change needed."),
        }]
        report["timings_seconds"] = timer.steps
        report["total_seconds"] = round(time.perf_counter() - t0, 3)
        return report, 0

    rpc = Rpc(rpcs)

    # Probes and the Sourcify lookup are independent: run them together.
    timer.start("probe_and_verify")
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_probe = ex.submit(probe_contract, rpc, contract, wallet)
        if chain.sourcify_indexed:
            f_verify = ex.submit(check_verification, chain.chain_id, contract)
            verification = f_verify.result()
        else:
            # Sourcify has no index for this chain: do not ask it a
            # question it cannot answer. "not_indexed" is its own
            # status, distinct from "none" (checked, no match) and
            # from "unknown" (Sourcify unreachable).
            verification = {
                "status": "not_indexed", "source": "sourcify",
                "detail": (f"Sourcify does not index {chain.name} (chain {chain.chain_id}), "
                           "so source verification cannot be checked there - not the same "
                           "as a contract that was checked and found unverified"),
            }
        checks = f_probe.result()
    timer.stop()

    selectors = set(checks.pop("selectors", []))
    report["selector_fingerprint"] = sorted(selectors)
    checks["verification"] = verification

    # Gas: snapshot always; an exact estimate when a simulation succeeded.
    timer.start("gas")
    snap = gas_snapshot(chain, rpc)
    mint_estimate = None
    sim = checks.get("simulation", {})
    winning = next((a for a in sim.get("attempts", []) if a["result"] == "success"), None)
    if winning:
        mint_estimate = estimate_call_cost(
            chain, rpc, contract, winning["calldata"],
            wallet or ZERO_ADDRESS,
            winning["value_wei"], snap=snap)
    checks["gas"] = {"snapshot": snap, "mint_estimate": mint_estimate}
    timer.stop()

    # Experience library: a hint layer, never a substitute for live checks.
    timer.start("library_match")
    matches = match_library(load_library(library_path), contract, selectors,
                            chain=chain.key)
    report["library_matches"] = matches
    best = matches[0] if matches else None
    report["identification_path"] = (
        f"known shape: '{best['project']}' (similarity {best['similarity']}, {best['match_type']}) - "
        "address, price and parameters were still re-verified live in this run"
        if best and best["similarity"] >= SHAPE_THRESHOLD else
        "unfamiliar contract shape - read the reasons carefully, this is where human judgement earns its keep")
    timer.stop()

    verdict, reasons = evaluate(checks, verification, matches, snap)
    report["checks"] = checks
    report["verdict"] = verdict
    report["reasons"] = reasons
    report["timings_seconds"] = timer.steps
    report["total_seconds"] = round(time.perf_counter() - t0, 3)
    return report, 0

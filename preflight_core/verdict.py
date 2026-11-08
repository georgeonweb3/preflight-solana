"""The verdict engine: checks in, CLEAR / CAUTION / BLOCKED out.

Pure function, no I/O - the same inputs always give the same verdict,
which is what makes it unit-testable and worth trusting. Every verdict
comes with itemised reasons, each tagged block / caution / info, so the
CLI and the JSON schema can show their working instead of asking anyone
to trust a bare label.

Rule sources, all learned the expensive way in production (2026-11-03/04):
- a "free claim" can hide an anti-Sybil fee (Nomio) or a creator fee
  (CodexNFT) -> any price above zero is a caution with the amount shown;
- a mint contract can be unverified while its token is verified, and a
  reverting simulation on an unverified contract is a hard stop
  (BNB JIAYOU) -> unverified + reverting escalates to a block;
- commit/reveal mints expose no standard mint() selector (Agent Periodic
  Lab) -> "no known mint function" is a caution, not a block;
- gas can spike 4x intraday (BBF batch) -> expensive gas is a caution
  with the band named.
"""
import re
from decimal import Decimal

from .library import SHAPE_THRESHOLD

__all__ = ["evaluate", "SHAPE_THRESHOLD"]


def _reason(code, level, message):
    return {"code": code, "level": level, "message": message}


# The static verification reasons, keyed by Sourcify status. Anything
# outside this vocabulary (including a missing status) falls through
# to VERIFICATION_UNCHECKED below: "could not check" is its own state,
# never silently promoted to a checked one.
_VERIFICATION_REASONS = {
    "exact_match": ("SOURCE_VERIFIED", "info",
                    "source code verified on Sourcify (exact match)"),
    "partial_match": ("SOURCE_VERIFIED_PARTIAL", "info",
                      "source code verified on Sourcify (partial match: same code, different metadata)"),
    "none": ("UNVERIFIED_SOURCE", "caution",
             "source code is NOT verified on Sourcify - nobody outside the project can read what this contract does"),
    "not_indexed": ("SOURCE_NOT_INDEXED", "caution",
                    "source verification could not be checked: Sourcify does not index this chain, "
                    "so there is no verified-source record to consult - treat the source as unread "
                    "(not the same as a contract that was checked and found unverified)"),
}


def _outcome_is_success(outcome):
    """Classify an experience-library outcome by whole tokens, never by
    substring: "success_free_mint_executed" is a success, "unsuccessful"
    is not (a substring test cannot tell those apart)."""
    tokens = set(re.split(r"[^a-z0-9]+", (outcome or "").lower()))
    return bool(tokens & {"success", "executed"})


def evaluate(checks: dict, verification: dict, library_matches: list, gas_snapshot: dict):
    deployment = checks.get("deployment", {})
    identity = checks.get("identity", {})
    supply = checks.get("supply", {})
    price = checks.get("price", {})
    open_status = checks.get("open_status", {})
    wallet = checks.get("wallet", {})
    sim = checks.get("simulation", {})
    reasons = []

    deployment_unknown = bool(deployment.get("code_read_failed"))

    # ---- deployment evidence ------------------------------------------
    # The deployment read has the same three-outcome discipline as the
    # simulation below: bytecode answers the question, a successful
    # read that came back empty blocks as NOT_A_CONTRACT, and a failed
    # read is DEPLOYMENT_UNKNOWN - a caution, never a block, never a
    # clear. Unlike NOT_A_CONTRACT it does NOT return early: the rest
    # of the evidence is still evaluated, so an independent finding
    # (a sold-out supply, a paused flag) stands or falls on its own
    # and the failed read itself decides nothing either way.
    if deployment_unknown:
        reasons.append(_reason("DEPLOYMENT_UNKNOWN", "caution",
                          "the eth_getCode deployment read failed at the transport level "
                          "(every RPC endpoint failed, timed out, or returned a truncated/malformed response), "
                          "so whether a contract exists at this address could not be determined. "
                          "That is evidence about the network, not about this address - "
                          "re-run the check when the RPC path is healthy"))
    elif not deployment.get("is_contract"):
        reasons.append(_reason("NOT_A_CONTRACT", "block",
                          "address has no contract code on this chain (wrong address or wrong chain)"))
        return "BLOCKED", reasons

    # ---- hard blocks -------------------------------------------------

    if supply.get("remaining") is not None and supply["remaining"] <= 0:
        reasons.append(_reason("SOLD_OUT", "block",
                          f"supply exhausted: {supply.get('total_supply')} of {supply.get('max_supply')} minted, 0 remaining"))

    if identity.get("paused") is True or open_status.get("paused") is True:
        reasons.append(_reason("PAUSED", "block", "contract is paused"))

    attempts = sim.get("attempts") or []
    # Only genuine reverts count. An attempt that ended in a transport
    # error says nothing about the contract, so it can neither satisfy
    # nor soften this block - it surfaces as SIMULATION_UNKNOWN below.
    sim_all_reverted = bool(attempts) and \
        all(a.get("result") == "revert" for a in attempts)
    if sim_all_reverted:
        first = next((a for a in attempts if a.get("reason")), attempts[0])
        reasons.append(_reason("SIMULATION_REVERTS", "block",
                          f"every mint/claim function reverted in eth_call simulation "
                          f"(e.g. {first['function']}: {first.get('reason') or 'bare revert'}). "
                          "Closed, sold out, ineligible wallet or wrong parameters are all possible - do not sign blind"))

    opens = open_status.get("probes") or {}
    if opens and not any(opens.values()) and not sim.get("any_success"):
        reasons.append(_reason("NOT_OPEN", "block",
                          "every open-status probe that answered says the mint/claim is not open"))

    vstatus = (verification or {}).get("status", "unknown")
    if vstatus == "none" and sim_all_reverted:
        reasons.append(_reason("UNVERIFIED_AND_REVERTING", "block",
                          "source code is NOT verified and the mint simulation reverts - "
                          "this is the exact shape of the BNB JIAYOU block: nothing about this contract can be trusted"))

    # ---- verification ------------------------------------------------
    v = _VERIFICATION_REASONS.get(vstatus)
    if v:
        reasons.append(_reason(*v))
    else:
        reasons.append(_reason("VERIFICATION_UNCHECKED", "caution",
                          "verification status could not be checked (Sourcify unreachable) - treat as unverified until confirmed"))

    # ---- contract shape ----------------------------------------------
    if deployment.get("is_proxy_eip1967"):
        reasons.append(_reason("UPGRADEABLE_PROXY", "caution",
                          f"upgradeable proxy: the implementation ({deployment.get('implementation')}) can be swapped after this check - re-verify at signing time"))

    if identity.get("owner"):
        reasons.append(_reason("OWNER_PRESENT", "info",
                          f"contract has an owner ({identity['owner']}) - check what the owner can change before trusting long-term"))

    # ---- price ---------------------------------------------------------
    price_wei = price.get("wei")
    native_usd = (gas_snapshot or {}).get("native_usd")
    if price_wei is not None and price_wei > 0:
        usd = ""
        if native_usd:
            usd_val = (Decimal(price_wei) / Decimal(10 ** 18)) * Decimal(str(native_usd))
            usd = f" (~${usd_val:.2f})"
        reasons.append(_reason("PAID_MINT", "caution",
                          f"not free: price is {price.get('native')} (native) via {price.get('via_function')}{usd} - plus gas"))
    elif price_wei == 0:
        reasons.append(_reason("FREE_PRICE", "info", "price probes read 0 - the mint itself is free, gas still applies"))
    if price.get("probes_disagree"):
        reasons.append(_reason("PRICE_PROBES_DISAGREE", "caution",
                          f"price functions disagree with each other: {price.get('all_probes_native')} - trust the simulation value, not the label"))
    if price_wei is None and not deployment_unknown:
        # Suppressed when the deployment read failed: there, "no price
        # function answered" only restates that nothing could be read.
        reasons.append(_reason("PRICE_UNKNOWN", "caution",
                          "no price function answered - the real cost is only visible at simulation/signing time, watch for hidden fees"))

    # ---- open status / simulation shape --------------------------------
    if deployment_unknown:
        # No candidates and no attempts here say nothing about the
        # contract: the bytecode was never read, so there was nothing
        # to fingerprint or simulate. DEPLOYMENT_UNKNOWN above is the
        # whole story for this section.
        pass
    elif not (sim.get("candidate_functions") or []):
        reasons.append(_reason("NO_KNOWN_MINT_FUNCTION", "caution",
                          "no known mint/claim function found in the bytecode - commit/reveal drops work this way (their first step is project-specific), so check the project's official guide before concluding anything"))
    elif not attempts:
        reasons.append(_reason("SIMULATION_NOT_POSSIBLE", "caution",
                          "mint functions exist but none could be simulated with standard arguments"))
    sim_errors = [a for a in attempts if a.get("result") == "error"]
    if sim_errors and not sim.get("any_success"):
        first_err = sim_errors[0]
        reasons.append(_reason("SIMULATION_UNKNOWN", "caution",
                          f"simulation could not be completed: {len(sim_errors)} of {len(attempts)} "
                          f"eth_call attempt(s) failed at the transport level "
                          f"(e.g. {first_err['function']}: {first_err.get('reason') or 'RPC unreachable'}). "
                          "That is evidence about the network, not about this contract - "
                          "re-run the check when the RPC path is healthy"))
    if not opens and sim.get("candidate_functions"):
        reasons.append(_reason("OPEN_UNCONFIRMED", "caution",
                          "no open-status function answered - openness is inferred from the simulation only"))
    if opens and not any(opens.values()) and sim.get("any_success"):
        reasons.append(_reason("FLAGS_VS_SIMULATION", "info",
                          "open-status flags say closed but a simulation succeeded from this wallet - the simulation is the stronger signal"))

    if sim.get("free_success"):
        reasons.append(_reason("SIMULATION_OK", "info", "a mint/claim simulation succeeded at value 0 (eth_call, nothing signed)"))
    elif sim.get("any_success"):
        reasons.append(_reason("SIMULATION_OK_PAID", "info", "a mint/claim simulation succeeded at the probed price (eth_call, nothing signed)"))

    # ---- wallet --------------------------------------------------------
    flags = wallet.get("flags") or {}
    if flags.get("hasMinted(address)") is True or flags.get("hasClaimed(address)") is True:
        reasons.append(_reason("ALREADY_MINTED_OR_CLAIMED", "caution",
                          "this wallet has already minted/claimed according to the contract - a second one may be blocked or paid"))
    if any(v is False for k, v in flags.items() if "whitelist" in k.lower() or "allowlist" in k.lower()):
        reasons.append(_reason("WALLET_NOT_WHITELISTED", "caution",
                          "whitelist/allowlist probes answered False for this wallet"))
    if wallet.get("balance_of"):
        reasons.append(_reason("ALREADY_HOLDS", "info",
                          f"wallet already holds {wallet['balance_of']} of this contract's token/NFT"))

    # ---- gas -------------------------------------------------------------
    band = (gas_snapshot or {}).get("band")
    if band == "EXPENSIVE":
        reasons.append(_reason("GAS_EXPENSIVE", "caution",
                          f"gas is EXPENSIVE right now ({(gas_snapshot or {}).get('gwei_median')} gwei) - if this is not time-sensitive, waiting is cheaper"))
    elif band == "VERY_EXPENSIVE":
        reasons.append(_reason("GAS_VERY_EXPENSIVE", "caution",
                          f"gas is VERY EXPENSIVE right now ({(gas_snapshot or {}).get('gwei_median')} gwei) - do not batch anything at this price"))

    # ---- history ---------------------------------------------------------
    for m in library_matches or []:
        outcome = (m.get("outcome") or "")
        if m.get("match_type") == "exact_contract":
            if _outcome_is_success(outcome):
                reasons.append(_reason("HISTORY_EXACT_SUCCESS", "info",
                                  f"this exact contract is in the experience library: {m.get('project')} - outcome {outcome}"))
            elif outcome:
                reasons.append(_reason("HISTORY_EXACT_FAILURE", "caution",
                                  f"this exact contract is in the experience library: {m.get('project')} - outcome {outcome}"
                                  + (f" ({m['failure_reason']})" if m.get("failure_reason") else "")))
        elif m.get("similarity", 0) >= SHAPE_THRESHOLD:
            if outcome and not _outcome_is_success(outcome):
                # The spec's caution rule is "a matching past failure in
                # the experience library" - it is not limited to exact
                # contract matches. A shape that failed before is a
                # caution with the reason attached, not a neutral note.
                reasons.append(_reason("HISTORY_SHAPE_FAILURE", "caution",
                                  f"ABI shape matches '{m.get('project')}' (similarity {m.get('similarity')}) "
                                  f"and that shape failed before - outcome {outcome}"
                                  + (f" ({m['failure_reason']})" if m.get("failure_reason") else "")
                                  + " - everything still re-verified live, but read the live reasons with this in mind"))
            else:
                reasons.append(_reason("HISTORY_SHAPE_MATCH", "info",
                                  f"ABI shape matches '{m.get('project')}' (similarity {m.get('similarity')}) - identification path reused, everything still re-verified live"))

    # ---- roll up ---------------------------------------------------------
    levels = {r["level"] for r in reasons}
    if "block" in levels:
        return "BLOCKED", reasons
    if "caution" in levels:
        return "CAUTION", reasons
    return "CLEAR", reasons

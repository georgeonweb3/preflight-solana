"""Experience library: past mint outcomes, matched against new contracts.

One JSON object per line: project, contract, outcome, failure reason and
the contract's selector fingerprint. Matching is exact-contract first,
then ABI-shape similarity (Jaccard over the selector set).

The rule from the parent skill stands: history is a hint, never a
substitute. A match changes what we tell the user ("this shape failed
before, here is why") - it never skips a live check, and addresses,
prices and parameters are always re-verified in the same run.

Exact-contract matching is chain-scoped: entries record a chain, and
the same address on a different chain (common with deterministic
deployers) is a different contract with a different history. ABI-shape
matching stays chain-agnostic - a selector shape means the same thing
on any EVM chain - but a cross-chain address twin can only ever
surface as a shape match, never as this contract's own history.

There is exactly ONE shape-match threshold, SHAPE_THRESHOLD, and it is
shared by every consumer: this matcher, the verdict engine's history
reasons, and the identification path in check.py. Below it, two
contracts that merely share boilerplate selectors (the ERC721/ERC1155
surface almost every NFT mint carries) are treated as unrelated; the
library used to admit shapes at 0.25 while the verdict only surfaced
them at 0.5, which made "match" mean two different things in one
report.
"""
import json
from pathlib import Path

DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / "data" / "experience_library.jsonl"

# The single Jaccard similarity at or above which an ABI shape counts
# as a match - here, in the verdict's history reasons, and in the
# identification path. One threshold, one meaning of "shape match".
SHAPE_THRESHOLD = 0.5


def load_library(path=None):
    path = Path(path) if path else DEFAULT_LIBRARY
    entries = []
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def match_library(entries, contract, selector_set, chain=None):
    """Up to 3 matches: exact contract (same chain), then selector-set
    similarity >= SHAPE_THRESHOLD. `chain` is the chain key being checked (e.g.
    "bsc"); when an entry records a different chain, its exact-contract
    history does not transfer - it may still shape-match. Entries that
    record no chain cannot be scoped and match as before."""
    out = []
    want_chain = (chain or "").strip().lower() or None
    for e in entries:
        entry_chain = (e.get("chain") or "").strip().lower() or None
        same_chain = want_chain is None or entry_chain is None or entry_chain == want_chain
        if same_chain and e.get("contract") and contract and e["contract"].lower() == contract.lower():
            out.append({"project": e.get("project"), "similarity": 1.0,
                        "match_type": "exact_contract", "outcome": e.get("outcome"),
                        "failure_reason": e.get("failure_reason"),
                        "mint_method": e.get("mint_method")})
            continue
        sels = set(e.get("selectors") or [])
        if sels and selector_set:
            inter = len(sels & selector_set)
            union = len(sels | selector_set)
            sim = inter / union if union else 0.0
            if sim >= SHAPE_THRESHOLD:
                out.append({"project": e.get("project"), "similarity": round(sim, 3),
                            "match_type": "abi_shape", "outcome": e.get("outcome"),
                            "failure_reason": e.get("failure_reason"),
                            "mint_method": e.get("mint_method")})
    out.sort(key=lambda x: -x["similarity"])
    return out[:3]

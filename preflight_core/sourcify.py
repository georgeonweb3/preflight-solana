"""Source-code verification check via Sourcify (sourcify.dev).

The parent bsc-mint-precheck skill deliberately did NOT cover this - its
report told the user to check verification separately before executing.
Preflight folds it in, because "is the source verified" is the single
question that separated George's clean mints from his blocked ones
(ACAT: verified exact match, minted fine. BNB JIAYOU minter: no match,
and its mint simulation reverted - blocked).

Status vocabulary (normalised):
- "exact_match"   - deployed bytecode matches verified source exactly
- "partial_match" - source matches except metadata (still real source)
- "none"          - Sourcify has no verified match for this address
- "unknown"       - Sourcify could not be reached; NOT the same as "none",
                    and the verdict engine treats it differently

Uses the Sourcify API v2 only. (The v1 check-by-addresses endpoint was
removed upstream - it returns a 404 removal notice - so there is no v1
fallback to lean on. v2 semantics: 200 with match fields for verified
contracts, 404 with null match fields for everything else.)

The mapping is deliberately strict, because the verdict escalates on
"none" but not on "unknown": a 200 only counts when its match value is
in the known vocabulary, and a 404 only counts as "none" when the body
positively carries null match fields. Any other shape is unrecognised
and the caller reports "unknown" - "we could not check" must never
harden into "definitely unverified".
"""
import json
import urllib.error
import urllib.request

_V2 = "https://sourcify.dev/server/v2/contract/{chain_id}/{address}"


def interpret(status_code, body):
    """Map a v2 response (HTTP status + parsed JSON body) to a status.

    Pure function so the mapping is unit-testable without a network.
    Returns (status, detail) or None if the response is unrecognisable.
    """
    if not isinstance(body, dict):
        return None
    if status_code == 200:
        match = body.get("match") or body.get("runtimeMatch") or body.get("creationMatch")
        if match in ("exact_match", "perfect"):
            return "exact_match", "Sourcify v2: exact match"
        if match in ("partial_match", "partial"):
            return "partial_match", "Sourcify v2: partial match"
        # Any other 200 (no match value, or a value outside the known
        # vocabulary) is unrecognisable - never promote it to a match.
        return None
    if status_code == 404:
        # v2 answers unknown/unverified contracts with a 404 whose body
        # still names the chain and address and carries null match
        # fields. "none" requires that positive evidence: at least one
        # match field present, every match field null, and the body
        # naming the chain or address it is answering for. A bare or
        # foreign-shaped 404 (an error page, a removal notice) carries
        # no such evidence and stays unrecognised -> "unknown".
        match_keys = ("match", "runtimeMatch", "creationMatch")
        named = body.get("chainId") is not None or body.get("address") is not None
        has_match_field = any(k in body for k in match_keys)
        all_null = all(body.get(k) is None for k in match_keys)
        if named and has_match_field and all_null:
            return "none", "Sourcify v2: no verified match (404)"
        return None
    return None


def _fetch(url, timeout=15):
    """(status_code, parsed_json_or_None); transport errors raise."""
    req = urllib.request.Request(url, headers={"User-Agent": "preflight/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None


def check_verification(chain_id: int, address: str) -> dict:
    """Return {"status": ..., "source": "sourcify", "detail": ...}."""
    url = _V2.format(chain_id=chain_id, address=address)
    last_err = None
    for _attempt in range(2):  # one retry: Sourcify can be slow under load
        try:
            code, body = _fetch(url)
        except Exception as e:
            last_err = e
            continue
        got = interpret(code, body)
        if got:
            return {"status": got[0], "source": "sourcify", "detail": got[1]}
        return {"status": "unknown", "source": "sourcify",
                "detail": f"Sourcify v2 returned an unrecognised response (HTTP {code})"}
    return {"status": "unknown", "source": "sourcify",
            "detail": f"Sourcify unreachable: {last_err}"}

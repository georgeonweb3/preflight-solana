"""Solana reads: mint account, holder concentration, safety reports.

Solana is not an EVM chain, so the EVM pipeline (eth_call probes,
Sourcify, gas bands) cannot ask its questions there. The questions a
buyer actually needs answered before touching a Solana mint are
different, and they are all read-only:

- does the mint exist, and who still controls it - a live *mint
  authority* can print new supply, and a live *freeze authority* is
  the Solana honeypot equivalent: the holder of it can freeze a
  buyer's token account so the tokens can never be sold or moved;
- is supply concentrated in a few accounts (getTokenLargestAccounts);
- what do the two keyless public safety reports say - RugCheck's named
  risks and GoPlus's Solana token security flags - used as a
  cross-check on the raw RPC reads, never as a substitute for them.

Transport discipline matches rpc.py: an allowlist of read-only
methods, endpoint failover, and a hard separation between "the chain
answered X" and "the read failed". The public mainnet endpoint
rate-limits getTokenLargestAccounts aggressively (HTTP 429 in live
testing), so holder concentration falls back to the GoPlus holder
list, then to RugCheck's top-holder table, with the source recorded -
and if none of them answer, the section is "unavailable", which the
verdict engine treats as a gap to state, never as a pass.
"""
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

__all__ = [
    "BASE58_RE", "TOKEN_PROGRAM", "TOKEN_2022_PROGRAM",
    "is_solana_address", "resolve_solana_input", "SolanaRpc",
    "parse_mint_account", "parse_largest_accounts", "parse_goplus_holders",
    "parse_rugcheck", "parse_goplus", "probe_solana",
]

# Base58 alphabet (no 0, O, I, l). Solana addresses are 32 bytes, which
# encode to 32-44 base58 characters.
BASE58_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

READ_ONLY_METHODS = {
    "getAccountInfo",
    "getTokenLargestAccounts",
    "getTokenAccountsByOwner",
    "getBalance",
    "getSlot",
    "getEpochInfo",
}

RUGCHECK_BASE = "https://api.rugcheck.xyz/v1/tokens"
GOPLUS_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"


def is_solana_address(text) -> bool:
    return bool(text) and bool(BASE58_RE.fullmatch(text.strip()))


def resolve_solana_input(target: str):
    """Returns (mint_or_None, source_note, all_candidates).

    Solana checks take a bare base58 mint address. URLs are not
    resolved (there is no explorer-page convention to lean on yet),
    and an EVM address is called out by name - pasting a 0x address
    with --chain solana is the most likely mistake, so the message
    says exactly what happened instead of a bare "not found".
    """
    t = (target or "").strip()
    if t.startswith("0x") or re.fullmatch(r"0x[a-fA-F0-9]{40}", t):
        return None, ("input looks like an EVM (0x) address - Solana mint "
                      "addresses are base58 (32-44 characters, no 0x prefix)"), []
    if is_solana_address(t):
        return t, "input was a bare Solana mint address", [t]
    return None, ("no Solana mint address found in the input - paste the bare "
                  "base58 mint address (project URLs are not resolved for Solana)"), []


def _http_json(url, payload=None, timeout=20, user_agent="preflight/0.1"):
    """One JSON HTTP round-trip. Returns the parsed body, or None on
    any transport/HTTP/parse failure - callers treat None as
    "did not answer", never as an empty answer."""
    try:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


class SolanaRpc:
    """Read-only Solana JSON-RPC with endpoint failover.

    Mirrors rpc.Rpc: methods outside the allowlist are refused in
    code, and a JSON-RPC error body (rate limit, method blocked) is a
    failed read - the next endpoint is tried, and if every endpoint
    fails the call returns None ("could not read"), never a fabricated
    empty result.
    """

    def __init__(self, endpoints, user_agent="preflight/0.1"):
        self.endpoints = list(endpoints)
        self.user_agent = user_agent
        self.errors = []

    def call(self, method, params):
        if method not in READ_ONLY_METHODS:
            raise ValueError(f"refusing non-read-only RPC method: {method}")
        for ep in self.endpoints:
            for attempt in range(2):
                body = _http_json(ep, {"jsonrpc": "2.0", "id": 1,
                                       "method": method, "params": params},
                                  user_agent=self.user_agent)
                if body is None:
                    self.errors.append(f"{ep}: transport failure")
                    break  # dead endpoint - move on
                if "error" in body:
                    msg = (body["error"] or {}).get("message", str(body["error"]))
                    self.errors.append(f"{ep}: {msg}")
                    if attempt == 0:
                        time.sleep(1.2)  # 429s clear; one retry, then fail over
                        continue
                    break
                return body.get("result")
        return None


# ---------------------------------------------------------------------
# Parsers. Pure functions over decoded JSON - these are what the unit
# tests pin down with recorded fixtures.
# ---------------------------------------------------------------------

def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_from_fraction(raw):
    """Holder share as a percentage. GoPlus reports a fraction string
    ("0.1214" = 12.14%, verified against a live USDC response); a
    value above 1 can only already be a percentage, so it is taken
    as-is rather than multiplied into nonsense."""
    v = _to_float(raw)
    if v is None:
        return None
    return v * 100 if v <= 1 else v


def parse_mint_account(value):
    """Parse a getAccountInfo result *value* (jsonParsed encoding).

    Returns a dict with status one of:
    - "ok": a token mint; supply/decimals/authorities/extensions set;
    - "not_a_mint": the account exists but is not a token mint;
    - "read_failed": value was None because the read never answered
      (handled by the caller, which passes None through).
    A null account value from an RPC that DID answer means the
    account does not exist; probe_solana assigns that "not_found"
    itself, since only it can tell an answered null from silence.
    """
    if value is None:
        return {"status": "read_failed"}
    data = (value.get("data") or {})
    parsed = data.get("parsed") or {}
    if parsed.get("type") != "mint":
        return {"status": "not_a_mint",
                "account_type": parsed.get("type"),
                "owner": value.get("owner")}
    info = parsed.get("info") or {}
    extensions = []
    for ext in info.get("extensions") or []:
        extensions.append({"name": ext.get("extension"),
                           "state": ext.get("state") or {}})
    owner = value.get("owner")
    return {
        "status": "ok",
        "owner": owner,
        "token_program": ("token-2022" if owner == TOKEN_2022_PROGRAM
                          else "spl-token" if owner == TOKEN_PROGRAM else owner),
        "supply_raw": _to_int(info.get("supply")),
        "decimals": _to_int(info.get("decimals")),
        "is_initialized": bool(info.get("isInitialized")),
        "mint_authority": info.get("mintAuthority"),
        "freeze_authority": info.get("freezeAuthority"),
        "extensions": extensions,
    }


def parse_largest_accounts(value, supply_raw, source="rpc"):
    """Top-1 / top-10 share of supply from a getTokenLargestAccounts
    result value (a list of up to 20 accounts, largest first)."""
    if not value or not supply_raw:
        return {"status": "unavailable", "source": source}
    amounts = []
    for entry in value:
        amt = _to_int((entry or {}).get("amount"))
        if amt is not None:
            amounts.append(amt)
    if not amounts:
        return {"status": "unavailable", "source": source}
    return {
        "status": "ok",
        "source": source,
        "accounts_seen": len(amounts),
        "top1_pct": round(amounts[0] / supply_raw * 100, 4),
        "top10_pct": round(sum(amounts[:10]) / supply_raw * 100, 4),
    }


def parse_goplus_holders(entry):
    """Holder concentration from a GoPlus token entry's holder list.
    Returns None when GoPlus carried no holder data."""
    holders = (entry or {}).get("holders") or []
    pcts = []
    for h in holders:
        p = _pct_from_fraction((h or {}).get("percent"))
        if p is not None:
            pcts.append(p)
    if not pcts:
        return None
    pcts.sort(reverse=True)
    return {
        "status": "ok",
        "source": "goplus",
        "accounts_seen": len(pcts),
        "top1_pct": round(pcts[0], 4),
        "top10_pct": round(sum(pcts[:10]), 4),
    }


def parse_rugcheck(payload, source="summary"):
    """Parse a RugCheck /report/summary (or /report) body.

    An HTTP error body or a payload without the report keys is an
    unavailable report, not a clean one."""
    if not isinstance(payload, dict) or "risks" not in payload:
        return {"status": "unavailable", "source": source}
    risks = []
    for r in payload.get("risks") or []:
        r = r or {}
        risks.append({
            "name": r.get("name") or "unnamed risk",
            "level": str(r.get("level") or "").lower(),
            "description": r.get("description") or "",
            "score": r.get("score"),
        })
    return {
        "status": "ok",
        "source": source,
        "score": payload.get("score"),
        "score_normalised": payload.get("score_normalised"),
        "rugged": bool(payload.get("rugged")) if payload.get("rugged") is not None else None,
        "lp_locked_pct": payload.get("lpLockedPct"),
        "risks": risks,
    }


def parse_rugcheck_holders(payload):
    """Top holders from a RugCheck full /report body (pct is already
    a percentage there). Tertiary concentration source."""
    holders = (payload or {}).get("topHolders") or []
    pcts = []
    for h in holders:
        p = _to_float((h or {}).get("pct"))
        if p is not None:
            pcts.append(p)
    if not pcts:
        return None
    pcts.sort(reverse=True)
    return {
        "status": "ok",
        "source": "rugcheck",
        "accounts_seen": len(pcts),
        "top1_pct": round(pcts[0], 4),
        "top10_pct": round(sum(pcts[:10]), 4),
    }


def _flag_status(entry, key):
    """A GoPlus {status: "1"/"0", authority: [...]} flag -> True/False/None."""
    flag = (entry or {}).get(key)
    if not isinstance(flag, dict) or "status" not in flag:
        return None
    return str(flag.get("status")) == "1"


def parse_goplus(payload, mint):
    """Parse a GoPlus Solana token-security body for one mint.

    code != 1, a missing mint entry, or an empty entry all mean
    GoPlus has no usable answer for this mint - "unavailable", which
    the verdict states as a gap rather than reading it as clean."""
    if not isinstance(payload, dict) or payload.get("code") != 1:
        return {"status": "unavailable"}
    entry = (payload.get("result") or {}).get(mint)
    if not entry:
        return {"status": "unavailable"}
    metadata = entry.get("metadata") or {}
    transfer_fee = entry.get("transfer_fee")
    fee_rate = None
    if isinstance(transfer_fee, dict) and transfer_fee:
        fee_rate = transfer_fee.get("rate")
    return {
        "status": "ok",
        "name": metadata.get("name") or None,
        "symbol": metadata.get("symbol") or None,
        "mintable": _flag_status(entry, "mintable"),
        "freezable": _flag_status(entry, "freezable"),
        "closable": _flag_status(entry, "closable"),
        "balance_mutable": _flag_status(entry, "balance_mutable_authority"),
        "metadata_mutable": _flag_status(entry, "metadata_mutable"),
        "non_transferable": (str(entry.get("non_transferable")) == "1"
                             if entry.get("non_transferable") is not None else None),
        "transfer_fee_rate": fee_rate,
        "transfer_hook": bool(entry.get("transfer_hook")) if entry.get("transfer_hook") is not None else None,
        "holder_count": _to_int(entry.get("holder_count")),
        "holders": parse_goplus_holders(entry),
    }


def _http_json_retry(url):
    """API reads get one retry: the probe fires several requests in
    parallel, and the public APIs occasionally drop one under the
    burst (observed live with GoPlus). A dropped read retried once is
    still a read; a dropped read never retried is a fake gap."""
    body = _http_json(url)
    if body is None:
        time.sleep(1.5)
        body = _http_json(url)
    return body


# GoPlus's edge intermittently resets connections from urllib's
# default TLS profile (observed live, 2026-11-06: 0-byte reads and
# RemoteDisconnected from the plain profile while curl/requests to
# the same URL succeeded; a cipher profile matching common Python
# HTTP stacks gets responses through, with retries covering the
# remaining flakiness). A body counts only when fully read and
# parsed - anything less is "unavailable", never a partial answer.
_GOPLUS_CIPHERS = (
    "ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:ECDH+AESGCM:"
    "DH+AESGCM:ECDH+AES:DH+AES:RSA+AESGCM:RSA+AES:!aNULL:!eNULL:!MD5:!DSS:!RC4")


def _goplus_json(url, attempts=3):
    import gzip
    import ssl
    ctx = ssl.create_default_context()
    try:
        ctx.set_ciphers(_GOPLUS_CIPHERS)
    except ssl.SSLError:
        pass  # a stricter local OpenSSL policy wins; default profile it is
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx))
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "preflight/0.1",
                "Accept": "*/*",
                "Accept-Encoding": "gzip",
            })
            with opener.open(req, timeout=20) as r:
                raw = r.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return json.loads(raw)
        except Exception:
            if i < attempts - 1:
                time.sleep(1.2)
    return None


def fetch_rugcheck(mint):
    body = _http_json_retry(f"{RUGCHECK_BASE}/{mint}/report/summary")
    parsed = parse_rugcheck(body, source="summary")
    if parsed["status"] == "ok":
        return parsed, body
    body = _http_json_retry(f"{RUGCHECK_BASE}/{mint}/report")
    return parse_rugcheck(body, source="report"), body


def fetch_goplus(mint):
    body = _goplus_json(f"{GOPLUS_URL}?contract_addresses={mint}")
    return parse_goplus(body, mint)


# ---------------------------------------------------------------------
# Probe assembly.
# ---------------------------------------------------------------------

def probe_solana(rpcs, mint, wallet=None):
    """Run every Solana read for a mint and assemble check sections.

    Independent reads run in parallel. Holder concentration prefers
    the RPC largest-accounts table; the public endpoint throttles that
    method hard, so GoPlus holders and then RugCheck's table stand in,
    with the winning source recorded in the section.
    """
    rpc = SolanaRpc(rpcs)
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_account = ex.submit(rpc.call, "getAccountInfo",
                              [mint, {"encoding": "jsonParsed"}])
        f_largest = ex.submit(rpc.call, "getTokenLargestAccounts", [mint])
        f_rugcheck = ex.submit(fetch_rugcheck, mint)
        f_goplus = ex.submit(fetch_goplus, mint)
        f_epoch = ex.submit(rpc.call, "getEpochInfo", [])
        account_result = f_account.result()
        largest_result = f_largest.result()
        rugcheck, rugcheck_body = f_rugcheck.result()
        goplus = f_goplus.result()
        epoch_result = f_epoch.result()

    # The current epoch decides which Token-2022 transfer fee schedule is
    # active (older vs newer). A failed read stays None: the verdict then
    # falls back to the worst case instead of guessing a schedule.
    current_epoch = None
    if isinstance(epoch_result, dict):
        epoch_value = epoch_result.get("epoch")
        if isinstance(epoch_value, int):
            current_epoch = epoch_value

    slot = None
    account_value = None
    if isinstance(account_result, dict):
        slot = (account_result.get("context") or {}).get("slot")
        account_value = account_result.get("value")
        # The RPC answered: a null value here is the chain saying the
        # account does not exist - a verdict about the target, not a
        # network gap. Only a call that never answered (account_result
        # is None) may be reported as a failed read.
        mint_info = (parse_mint_account(account_value)
                     if account_value is not None else {"status": "not_found"})
    else:
        mint_info = {"status": "read_failed"}

    supply_raw = mint_info.get("supply_raw")
    holders = parse_largest_accounts(
        (largest_result or {}).get("value") if isinstance(largest_result, dict) else None,
        supply_raw, source="rpc")
    if holders["status"] != "ok" and goplus.get("holders"):
        holders = goplus["holders"]
    if holders["status"] != "ok" and rugcheck_body:
        rc_holders = parse_rugcheck_holders(rugcheck_body)
        if rc_holders:
            holders = rc_holders

    wallet_section = None
    if wallet:
        wallet_section = {"address": wallet, "holds_ui": None, "accounts": 0}
        res = rpc.call("getTokenAccountsByOwner",
                       [wallet, {"mint": mint}, {"encoding": "jsonParsed"}])
        if isinstance(res, dict) and isinstance(res.get("value"), list):
            total = 0.0
            seen = 0
            for acct in res["value"]:
                info = (((acct or {}).get("account") or {}).get("data") or {}).get("parsed", {}).get("info") or {}
                amt = (info.get("tokenAmount") or {}).get("uiAmount")
                if amt is not None:
                    total += float(amt)
                    seen += 1
            wallet_section = {"address": wallet, "holds_ui": total, "accounts": seen}

    return {
        "slot": slot,
        "current_epoch": current_epoch,
        "mint": mint_info,
        "holders": holders,
        "rugcheck": rugcheck,
        "goplus": goplus,
        "wallet": wallet_section,
        "rpc_errors": rpc.errors,
    }

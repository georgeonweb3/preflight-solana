"""The Solana verdict engine: checks in, CLEAR / CAUTION / BLOCKED out.

Pure function, no I/O, same discipline as verdict.py: every verdict
carries itemised reasons tagged block / caution / info, and a read
that failed is its own state - it can neither pass nor condemn a
mint, it caps the verdict at CAUTION and says why.

Calibration notes (learned from live data, 2026-11-06):
- A live freeze authority is the Solana honeypot *shape*, but it is
  not by itself a block: Circle's USDC keeps both its mint and freeze
  authorities live, and RugCheck scores it clean (normalised score 1,
  no named risks). So authorities alone are strong cautions here, and
  they escalate to blocks only with corroboration: a RugCheck danger
  risk, extreme holder concentration, or both authorities live over a
  concentrated holder base - the full rug kit.
- RugCheck's danger/warn levels are trusted as assigned: for a
  typical memecoin RugCheck itself rates a live freeze authority as a
  danger risk; for an established issuer token it does not. The raw
  RPC reads are always shown alongside, so the human sees the same
  evidence either way.
"""
__all__ = ["evaluate_solana"]

# Concentration thresholds, in percent of supply.
TOP1_BLOCK_PCT = 50.0            # one account is the majority of supply
MINT_LIVE_TOP1_BLOCK_PCT = 25.0  # mint authority live + a whale this size
FULL_CONTROL_TOP10_PCT = 50.0    # both authorities live + top-10 majority
TOP10_CAUTION_PCT = 40.0
TOP1_CAUTION_PCT = 20.0


def _reason(code, level, message):
    return {"code": code, "level": level, "message": message}


def _short(addr, head=8, tail=6):
    if not addr or len(addr) <= head + tail + 3:
        return addr
    return f"{addr[:head]}...{addr[-tail:]}"


def _ext_state(extensions, name):
    for ext in extensions or []:
        if ext.get("name") == name:
            return ext.get("state") or {}
    return None


def evaluate_solana(checks: dict):
    deployment = checks.get("deployment") or {}
    sol = checks.get("solana") or {}
    authorities = sol.get("authorities") or {}
    extensions = sol.get("extensions") or []
    holders = sol.get("holders") or {}
    rugcheck = sol.get("rugcheck") or {}
    goplus = sol.get("goplus") or {}
    supply = checks.get("supply") or {}
    wallet = checks.get("wallet") or {}
    reasons = []

    # ---- existence ----------------------------------------------------
    status = deployment.get("status")
    if status == "not_found":
        reasons.append(_reason("NOT_A_TOKEN_MINT", "block",
                          "no account exists at this address on Solana mainnet "
                          "(wrong address, or the mint has not been created)"))
        return "BLOCKED", reasons
    if status == "not_a_mint":
        reasons.append(_reason("NOT_A_TOKEN_MINT", "block",
                          f"the account at this address is not a token mint "
                          f"(account type: {deployment.get('account_type') or 'unknown'}) - "
                          "a wallet or program account cannot be minted from"))
        return "BLOCKED", reasons
    if status == "read_failed":
        reasons.append(_reason("MINT_READ_FAILED", "caution",
                          "the Solana RPC mint read failed at the transport level "
                          "(every endpoint failed or rate-limited), so the mint's "
                          "authorities and supply could not be determined. That is "
                          "evidence about the network, not about this mint - re-run "
                          "the check when the RPC path is healthy"))

    mint_authority = authorities.get("mint_authority")
    freeze_authority = authorities.get("freeze_authority")
    authorities_known = status == "ok"
    top1 = holders.get("top1_pct")
    top10 = holders.get("top10_pct")
    holders_known = holders.get("status") == "ok" and top1 is not None

    # ---- external danger verdicts --------------------------------------
    if rugcheck.get("rugged") is True:
        reasons.append(_reason("RUGGED", "block",
                          "RugCheck marks this token as rugged"))
    danger_risks = [r for r in rugcheck.get("risks") or [] if r.get("level") == "danger"]
    for r in danger_risks:
        reasons.append(_reason("RUGCHECK_DANGER", "block",
                          f"RugCheck danger risk: {r['name']}"
                          + (f" - {r['description']}" if r.get("description") else "")))

    # ---- Token-2022 total-control extensions ---------------------------
    delegate = _ext_state(extensions, "permanentDelegate")
    if delegate and (delegate.get("authority") or delegate.get("delegate")):
        reasons.append(_reason("PERMANENT_DELEGATE", "block",
                          f"Token-2022 permanent delegate is set ({_short(delegate.get('authority') or delegate.get('delegate'))}): "
                          "that account can transfer or burn ANY holder's tokens at will"))
    pausable = _ext_state(extensions, "pausableConfig")
    if pausable and pausable.get("paused") is True:
        reasons.append(_reason("TRANSFERS_PAUSED", "block",
                          "Token-2022 pausable extension: transfers are currently paused by the authority"))

    # ---- concentration blocks ------------------------------------------
    if holders_known and top1 >= TOP1_BLOCK_PCT:
        reasons.append(_reason("SINGLE_HOLDER_MAJORITY", "block",
                          f"a single account holds {top1}% of the supply - one wallet is the market"))
    if authorities_known and mint_authority and holders_known \
            and top1 >= MINT_LIVE_TOP1_BLOCK_PCT and top1 < TOP1_BLOCK_PCT:
        reasons.append(_reason("MINT_AUTHORITY_CONCENTRATED", "block",
                          f"mint authority is still live and the largest holder already holds {top1}% "
                          "of supply - new supply can be printed into a concentrated market"))
    if authorities_known and mint_authority and freeze_authority and holders_known \
            and top10 >= FULL_CONTROL_TOP10_PCT:
        reasons.append(_reason("FULL_CONTROL_CONCENTRATED", "block",
                          f"mint AND freeze authorities are both still live and the top 10 accounts "
                          f"hold {top10}% of supply - the full rug kit: print, freeze, and a concentrated base"))

    # ---- authorities -----------------------------------------------------
    if authorities_known and mint_authority:
        reasons.append(_reason("MINT_AUTHORITY_LIVE", "caution",
                          f"mint authority is still set ({_short(mint_authority)}) - new supply can be "
                          "created at will, diluting holders. Legitimate issuers (e.g. USDC) keep this; "
                          "an anonymous memecoin keeping it is a standing rug risk"))
    if authorities_known and freeze_authority:
        reasons.append(_reason("FREEZE_AUTHORITY_LIVE", "caution",
                          f"freeze authority is still set ({_short(freeze_authority)}) - the Solana "
                          "honeypot equivalent: that account can freeze any holder's token account so "
                          "the tokens cannot be sold or moved"))
    if authorities_known and not mint_authority and not freeze_authority:
        reasons.append(_reason("AUTHORITIES_REVOKED", "info",
                          "mint and freeze authorities are both revoked - supply is fixed and no "
                          "authority can freeze holders"))

    # ---- cross-check disagreements ---------------------------------------
    if goplus.get("status") == "ok" and authorities_known:
        gp_mint = goplus.get("mintable")
        gp_freeze = goplus.get("freezable")
        if (gp_mint is not None and bool(mint_authority) != gp_mint) or \
           (gp_freeze is not None and bool(freeze_authority) != gp_freeze):
            reasons.append(_reason("AUTHORITY_SOURCES_DISAGREE", "caution",
                          "GoPlus and the direct RPC read disagree about this mint's authorities "
                          "(one source is stale) - trust the RPC read shown here and re-check before signing"))

    # ---- Token-2022 extension cautions -----------------------------------
    fee_cfg = _ext_state(extensions, "transferFeeConfig")
    fee_bps = None
    if fee_cfg:
        fee = fee_cfg.get("newerTransferFee") or fee_cfg.get("olderTransferFee") or {}
        fee_bps = fee.get("transferFeeBasisPoints")
    if fee_bps:
        reasons.append(_reason("TRANSFER_FEE", "caution",
                          f"Token-2022 transfer fee of {fee_bps} basis points "
                          f"({fee_bps / 100:g}%) is taken on every transfer"))
    elif not fee_bps and goplus.get("transfer_fee_rate"):
        reasons.append(_reason("TRANSFER_FEE", "caution",
                          f"GoPlus reports a transfer fee (rate {goplus['transfer_fee_rate']}) on this token"))
    hook = _ext_state(extensions, "transferHook")
    if (hook and hook.get("programId")) or goplus.get("transfer_hook"):
        reasons.append(_reason("TRANSFER_HOOK", "caution",
                          "a transfer hook program runs custom code on every transfer of this token - "
                          "it can block or tax transfers in ways this check cannot see"))
    if _ext_state(extensions, "nonTransferable") is not None or goplus.get("non_transferable"):
        reasons.append(_reason("NON_TRANSFERABLE", "caution",
                          "this token is non-transferable (soulbound): once it is in a wallet it can "
                          "never be sold or moved"))
    if goplus.get("closable"):
        reasons.append(_reason("CLOSABLE_ACCOUNTS", "caution",
                          "GoPlus flags this mint as closable: an authority can force-close holder accounts"))
    if goplus.get("balance_mutable"):
        reasons.append(_reason("BALANCE_MUTABLE", "caution",
                          "GoPlus flags a balance-mutable authority: holder balances can be edited by an authority"))
    if pausable and pausable.get("paused") is not True and \
            (pausable.get("authority") or pausable.get("pausableConfigAuthority")):
        reasons.append(_reason("PAUSABLE_AUTHORITY", "caution",
                          "Token-2022 pausable extension has a live authority that can pause all transfers"))
    close_auth = _ext_state(extensions, "mintCloseAuthority")
    if close_auth and close_auth.get("authority"):
        reasons.append(_reason("MINT_CLOSE_AUTHORITY", "caution",
                          "a mint close authority is set - the mint account itself can be closed by that account"))
    default_state = _ext_state(extensions, "defaultAccountState")
    if default_state and str(default_state.get("defaultAccountState")).lower() == "frozen":
        reasons.append(_reason("DEFAULT_ACCOUNT_FROZEN", "caution",
                          "new token accounts for this mint are created frozen by default and must be "
                          "thawed by the freeze authority before the tokens can move"))

    # ---- holder concentration --------------------------------------------
    if not holders_known and status == "ok":
        reasons.append(_reason("HOLDERS_UNKNOWN", "caution",
                          "holder concentration could not be read (RPC largest-accounts, GoPlus and "
                          "RugCheck holder tables all failed or carried no data) - treat distribution as unverified"))
    if holders_known:
        if top10 >= TOP10_CAUTION_PCT and top10 < FULL_CONTROL_TOP10_PCT:
            reasons.append(_reason("HIGH_CONCENTRATION", "caution",
                          f"the top 10 accounts hold {top10}% of supply (via {holders.get('source')})"))
        if top1 >= TOP1_CAUTION_PCT and top1 < MINT_LIVE_TOP1_BLOCK_PCT:
            reasons.append(_reason("TOP_HOLDER_HEAVY", "caution",
                          f"the largest single account holds {top1}% of supply (via {holders.get('source')})"))

    # ---- external reports: gaps and warnings ------------------------------
    if rugcheck.get("status") != "ok":
        reasons.append(_reason("RUGCHECK_UNAVAILABLE", "caution",
                          "RugCheck did not return a report for this mint - its named-risk screen "
                          "could not be applied, so this verdict rests on the RPC reads and GoPlus alone"))
    else:
        for r in rugcheck.get("risks") or []:
            if r.get("level") == "warn":
                reasons.append(_reason("RUGCHECK_WARN", "caution",
                                  f"RugCheck warning: {r['name']}"
                                  + (f" - {r['description']}" if r.get("description") else "")))
        if not (rugcheck.get("risks") or []):
            reasons.append(_reason("RUGCHECK_CLEAN", "info",
                          f"RugCheck reports no named risks for this mint "
                          f"(score {rugcheck.get('score')}, normalised {rugcheck.get('score_normalised')}; lower is safer)"))
    if goplus.get("status") != "ok":
        reasons.append(_reason("GOPLUS_UNAVAILABLE", "caution",
                          "GoPlus returned no Solana security data for this mint - its cross-check "
                          "could not be applied"))
    elif goplus.get("mintable") is False and goplus.get("freezable") is False:
        reasons.append(_reason("GOPLUS_AGREES_REVOKED", "info",
                          "GoPlus agrees with the RPC read: not mintable, not freezable"))
    if goplus.get("metadata_mutable"):
        reasons.append(_reason("METADATA_MUTABLE", "info",
                          "token metadata is mutable - the name/symbol/image can be changed after this check"))

    # ---- supply / wallet ---------------------------------------------------
    if supply.get("total_supply") == 0:
        reasons.append(_reason("SUPPLY_ZERO", "caution",
                          "the mint exists but its supply is 0 - nothing has been issued to hold or trade"))
    if wallet:
        if wallet.get("holds_ui") is None:
            reasons.append(_reason("WALLET_READ_FAILED", "caution",
                          "the wallet's token accounts for this mint could not be read"))
        elif wallet.get("holds_ui"):
            reasons.append(_reason("ALREADY_HOLDS", "info",
                          f"wallet already holds {wallet['holds_ui']} of this token"))

    # ---- roll up -----------------------------------------------------------
    levels = {r["level"] for r in reasons}
    if "block" in levels:
        return "BLOCKED", reasons
    if "caution" in levels:
        return "CAUTION", reasons
    return "CLEAR", reasons

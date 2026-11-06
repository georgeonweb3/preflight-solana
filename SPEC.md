# Preflight - Product Spec (v1)

## One-line pitch

Check before you sign: paste a mint or claim contract (or its project
page) and get a CLEAR, CAUTION or BLOCKED verdict with the receipts,
before any wallet touches it.

Tagline: **Check before you sign. Guard before your agent spends.**

## Who it is for

- Airdrop and mint hunters who get contract addresses and mint links
  from X posts, Discord and roundups, and currently have to trust them.
- Small Web3 teams running mint/claim automations who want a read-only
  gate in front of any signing step.
- (Next door, same core) builders of AI agents that can spend or sign,
  who need a policy check between "the agent wants to" and "it did".

## The problem it answers

Free-mint and claim links are one of the easiest ways to lose money in
Web3. The failure shapes repeat: the "free" claim hides a fee, the mint
contract is unverified while the token contract is verified, the drop
sold out hours ago but the post still says open, the simulation reverts
for reasons nobody checked, gas spikes 4x between the quote and the
signature. Every one of those is visible on-chain, for free, before
signing. Nobody assembles it into one answer. Preflight does.

## Architecture: one core, three doors

```
                    +----------------------+
  pasted CA / URL   |   preflight_core     |
 -----------------> |  resolve -> probe -> |
                    |  verify -> gas ->    |
  agent-proposed    |  verdict             |
  transaction  ---> |                      |
 (door 2, later)    +----------+-----------+
                               |
                    standard verdict (JSON)
                    CLEAR / CAUTION / BLOCKED
                    + itemised reasons
```

### preflight_core (built, v1)

- **Chain adapters** (`chains.py`): pure data per chain - RPC endpoints,
  Sourcify chain id, explorer domains, native token and price tickers.
  All probing is plain EVM eth_call, so a new chain is a new config
  entry, not new code. BSC is first-class (calibrated in production);
  Base ships as the second adapter. Gas bands are BSC-calibrated and
  are only reported on chains flagged as calibrated.
- **Input resolution** (`resolve.py`): bare address, address in text,
  address inside a URL, or fetch the page and rank candidate addresses
  (explorer links first, then addresses near contract/mint keywords,
  then page order). Unresolvable input returns UNRESOLVED_INPUT.
  Addresses are never guessed.
- **Probing** (`probes.py`): a fixed battery of read-only eth_call
  probes run in parallel - deployment and code size, EIP-1967 proxy
  detection (identification follows the implementation), identity
  (ERC-165 type, name, symbol, owner, paused), supply vs max supply
  (`totalSupply()`, falling back to `totalMinted()` for contracts that
  expose only the minted count, so sold-out is still judged and the
  report records which function the number came from), a table of
  known price functions, a table of open-status functions, wallet
  eligibility (holdings, minted counts, whitelist flags). The
  deployment read itself has three outcomes, kept distinct like the
  simulation outcomes below: bytecode present; a successful read that
  came back empty (genuinely not a contract - a block); and a failed
  read (every endpoint failed, timed out, or returned a truncated or
  malformed body). A failed read is evidence about the network, not
  about the address: the deployment section records it
  (`code_read_failed`, with `is_contract` null), downstream probes do
  not run, and the verdict surfaces it as the DEPLOYMENT_UNKNOWN
  caution - it can neither manufacture a NOT_A_CONTRACT block nor
  clear one, and independent findings are still evaluated on their
  own.
- **Simulation** (`probes.py`): every known mint/claim function found
  in the bytecode is eth_call'd from the check wallet, first at value
  0 and, when a price was probed, again at value = price. Revert
  reasons are captured verbatim. Each attempt records one of three
  outcomes: success, revert (the contract answered and refused), or
  error (every RPC endpoint failed at the transport level, so the
  contract never answered). An error is evidence about the network,
  not about the contract: the verdict surfaces it as a caution
  (SIMULATION_UNKNOWN) and never lets it feed a block. This is a
  simulation only; nothing is signed or broadcast, ever.
- **Verification** (`sourcify.py`): Sourcify API v2 match status
  (exact_match / partial_match / none / unknown). "unknown" (Sourcify
  unreachable) is kept distinct from "none" (no match) because they
  mean different things and the verdict treats them differently. The
  mapping is strict: a 200 only counts when its match value is in the
  known vocabulary, and a 404 only counts as "none" when the body
  positively carries null match fields - anything unrecognised is
  "unknown", so "could not check" can never harden into "definitely
  unverified".
- **Gas** (`gas.py`): median eth_gasPrice across endpoints, BSC band
  (CHEAP / NORMAL / EXPENSIVE / VERY_EXPENSIVE), rolling 24h local
  baseline (samples are appended to `data/gas_history.jsonl` - the
  one local write the tool makes, disclosed in SECURITY.md),
  native-token USD price, and an eth_estimateGas cost for the exact
  mint call when a simulation succeeded.
- **Experience library** (`library.py`): past outcomes as JSONL,
  matched exact-contract first, then by ABI shape (Jaccard over the
  selector fingerprint). Exact-contract matching is chain-scoped: the
  same address on a different chain is a different contract and does
  not inherit this chain's history. A selector shape counts as a
  match at Jaccard similarity >= 0.5 - one threshold (`SHAPE_THRESHOLD`
  in `library.py`), used by the matcher, the verdict's history
  reasons and the identification path alike; below it, two contracts
  merely sharing boilerplate selectors are treated as unrelated. A
  match surfaces history ("this shape failed before, here is why")
  but never skips a live check.
- **Verdict engine** (`verdict.py`): a pure function from checks to
  verdict + itemised reasons. Deterministic and unit-tested.

### The standard verdict schema (`preflight.verdict/1`)

Every check emits the same JSON shape, whoever ran it:

- `verdict`: CLEAR | CAUTION | BLOCKED | UNRESOLVED_INPUT
- `reasons[]`: `{code, level, message}` where level is
  block | caution | info. A BLOCKED verdict always names its blocks;
  a CLEAR verdict means zero blocks and zero cautions were found.
- `checks`: deployment, verification, identity, supply, price,
  open_status, wallet, simulation, gas - the raw evidence.
- `library_matches`, `selector_fingerprint`, per-step `timings_seconds`.

An UNRESOLVED_INPUT report carries the same skeleton: the `checks`
sections are present but null (nothing was probed), `library_matches`
and `selector_fingerprint` are empty lists, and `contract` is null.
The shape never changes, so a consumer never has to guess which keys
exist.

Block rules (v1): no contract code (on a successful code read);
supply exhausted; contract paused;
every simulation reverted; open-status probes unanimously closed with
no successful simulation; source unverified AND simulation reverting.
Caution rules include: unverified or unchecked source, upgradeable
proxy, any price above zero (amount shown), disagreeing price probes,
unknown price, no known mint function (commit/reveal shapes), open
status unconfirmed, deployment not checked (the eth_getCode read
itself failed in transport - DEPLOYMENT_UNKNOWN), simulation not
completed (RPC/transport failure),
expensive gas, whitelist answering false, a matching past failure in
the experience library (exact contract or matching ABI shape).

### The three doors

1. **mint-precheck (built, v1)** - the CLI in this repo:
   `preflight check <address-or-url> --chain bsc [--wallet 0x...]`,
   human card on stdout, `--json` for the schema, `--out` to save it.
   `--wallet` must be a full 0x address and is validated before
   anything runs (a malformed wallet is a usage error, not a silently
   degraded report). `--library <path>` swaps in a different
   experience-library file. Two utility flags live on the top-level
   command: `--selftest` (verify the built-in Keccak-256 and exit)
   and `--version`.
2. **agent-seatbelt (next, spec only)** - the same core wrapped as a
   gate in front of an AI agent's signing/spending step. Input is a
   proposed transaction instead of a pasted link; the core adds policy
   on top of the verdict (spend caps, approval blocking, quote sanity,
   human-approval mode). This is the Aegis / tgp-guard lineage productised
   as middleware other builders can drop in. No code in v1.
3. **testnet-ops (parked)** - a phone-first workflow for testnet tasks
   (burner wallets, faucet tracking, queued contract calls), reusing
   the same precheck before each call. Parked deliberately: it earns
   its place only after doors 1 and 2 have real users asking for it.
   One paragraph is all it gets until then.

## Security model

- Read-only by construction. The RPC client refuses, in code, any
  method outside an allowlist of reads (eth_call, eth_estimateGas,
  eth_getCode, eth_getStorageAt, eth_blockNumber, eth_chainId,
  eth_gasPrice, eth_getBalance). There is no signing code path, no key
  handling, no wallet import, nothing to steal.
- It never asks for a private key, seed phrase or signature. The
  optional `--wallet` is a public address used as the eth_call sender
  so eligibility answers are wallet-specific.
- Page content fetched during URL resolution is treated as untrusted
  data: it is only scanned for address-shaped strings, never executed
  or followed as instructions.
- Zero third-party dependencies: pure Python 3.9+ stdlib.

## Honest limits

- A simulation is eth_call against current state. State can change
  between the check and a signature; a passing check is not a promise.
- Selector fingerprinting sees only functions whose selectors appear
  in the bytecode. Custom mint flows (commit/reveal, off-chain
  signatures, separate gate contracts) can be missed; the verdict
  says "no known mint function" instead of pretending otherwise.
- Verification status comes from Sourcify only in v1. A contract can
  be verified on an explorer and absent from Sourcify.
- Price probes read the functions contracts usually expose. A fee can
  hide in the transaction flow itself (the CodexNFT lesson); the
  verdict flags what it cannot see rather than claiming "free".
- It does not audit token honeypot/tax behaviour, and it is not
  financial advice. It answers "what does this contract look like
  right now", not "should you mint".

## v1 scope vs later

In v1: BSC + Base adapters, CLI door, Sourcify verification, gas
snapshot + mint estimate, experience library with seeded history,
JSON schema, unit tests, two live example reports.
Later: agent-seatbelt package, more chains, explorer verification as a
second source, library recording CLI, hosted version if demand shows.

# Build notes - Preflight v1 (built 2026-11-06, local only)

Status: built and proven locally in `~/workspace/preflight/`. NOT pushed
anywhere. George gives the final go for the public GitHub push after
reviewing this.

## What was reused from the existing skills (not reinvented)

From `~/workspace/custom-skills/bsc-mint-precheck/`:
- Pure-Python Keccak-256 + selector computation (ported into
  `preflight_core/keccak.py`; see the bug note below).
- Minimal ABI encode/decode helpers (`abi.py`).
- The whole probe design: probe tables for price / open-status /
  wallet-count / wallet-bool / mint candidates, EIP-1967 proxy slots,
  selector fingerprinting of the runtime code (and of the
  implementation when proxied), supply vs max-supply math, parallel
  probing with a thread pool.
- Simulation flow: eth_call each bytecode-present mint candidate from
  the check wallet, value 0 first, then value = probed price, revert
  reasons captured.
- URL resolution ranking: explorer links, then addresses near
  contract/mint keywords, then page order. UNRESOLVED_INPUT when
  nothing resolves, no guessing.
- Experience library format and matching (exact contract, then Jaccard
  ABI-shape similarity), including the "history is a hint, live checks
  always re-run" rule. Seed data in `data/experience_library.jsonl`
  carries five real entries from the skill's library (ACAT, BNB
  JIAYOU, BLOB Miner, Nomio, AGENT MAO), with ACAT/JIAYOU selector
  fingerprints filled in from the live example runs.

From `~/workspace/custom-skills/bsc-gas-guard/`:
- Gas approach: median eth_gasPrice across endpoints, band table,
  rolling 24h local baseline, USD cost table, eth_estimateGas for an
  exact call. One adaptation: the native price comes from
  data-api.binance.vision first (api.binance.com is geo-blocked from
  some networks), CoinGecko and DexScreener as fallbacks.

## What is new in Preflight

- Package layout (`preflight_core/`) with the pipeline split into
  single-job modules: chains, rpc, probes, sourcify, gas, resolve,
  library, verdict, check, cli.
- Chain adapters as data (`chains.py`): BSC first-class, Base included
  because the EVM probe logic generalises cleanly. Gas bands are only
  reported where the config says they are calibrated (BSC), rather
  than pretending Base bands exist.
- Sourcify verification (`sourcify.py`): the parent skill explicitly
  left this out; Preflight folds it in. API v2 only (see bug note).
- The verdict engine (`verdict.py`): CLEAR / CAUTION / BLOCKED with
  itemised, level-tagged reasons, as a pure function (unit-tested on
  synthetic checks, including the ACAT and JIAYOU shapes).
- The standard JSON schema `preflight.verdict/1` and the
  screenshot-friendly text card.

## Bugs found while building (all fixed, all instructive)

1. Two Keccak round constants were mistyped during the port
   (indices 21-22). The built-in `--selftest` caught it before
   anything else ran; constants now match the production skill
   byte-for-byte and the self-test passes. This is why the self-test
   ships in the CLI.
2. Sourcify's v1 API has been removed upstream (it returns a 404
   removal notice). Worse, v2 answers unverified contracts with an
   HTTP 404 whose body carries the verdict (null match fields), and
   urllib raises on 404, so the first live JIAYOU run reported
   verification "unknown" instead of "none" and the signature
   UNVERIFIED_AND_REVERTING block did not fire. Fixed: v2 only, the
   error body is parsed, 404 + null matches = "none", one retry on
   transport failure, and the response mapping is unit-tested
   (`tests/test_sourcify.py`). Re-ran both examples after the fix.
3. The text card printed "holds None" when a contract does not answer
   balanceOf. Fixed to "holds unknown"; example cards re-rendered
   from their saved JSON so text and JSON stay consistent.

## Post-review fixes (v0.1.1, 2026-11-06)

The two-axis code review of v0.1.0 (Matt Pocock `code-review` skill:
Standards + Spec, plus a coordinator addendum) found one Critical and
a cluster of same-family defects, all in the evidence pipeline: ways
for "we could not check" to harden into block-grade evidence. Fixes
1-4 below are the review's ranked fix list; 5-6 are the addendum
items. Every fix ships with regression tests that fail on the v0.1.0
behaviour (verified: 17 of the 21 new tests fail or error against the
old code; the other 4 are guards that pass on both versions).

1. **Transport failure is not a revert** (was Spec Critical).
   `probes.py` recorded both RPC outcome kinds (`__revert__`,
   `__error__`) as `result: "revert"`, so an RPC outage produced
   SIMULATION_REVERTS and UNVERIFIED_AND_REVERTING blocks. Attempts
   now record `result: "error"` for transport failures; the verdict
   engine's revert blocks require every attempt to be a genuine
   revert, and an incomplete simulation surfaces as the new
   SIMULATION_UNKNOWN caution. `SPEC.md` documents the three-outcome
   simulation evidence (success / revert / error).
2. **`reveal(address)` removed from the mint candidates** (was Spec
   High). Commit/reveal contracts were simulated on their reveal half
   and predictably blocked; per the spec's own honesty rule they now
   land on the NO_KNOWN_MINT_FUNCTION caution.
3. **Sourcify classification is strict** (was Standards High / Spec
   Medium). A 200 with a match value outside the known vocabulary is
   unrecognised, and a 404 counts as "none" only when the body
   positively carries null match fields (plus the chain/address it
   answers for). Anything else is "unknown".
4. **Shape-matched past failure is a caution** (was Spec Medium).
   `verdict.py` emitted HISTORY_SHAPE_MATCH as info regardless of the
   matched outcome; a shape match whose recorded outcome is a failure
   now emits HISTORY_SHAPE_FAILURE (caution) with the failure reason,
   per the spec's caution rule. Success shapes stay info.
5. **Outcome classification is token-based** (addendum). Exact-history
   classification used substring tests, so outcome "unsuccessful"
   matched "success" and returned CLEAR. Classification now splits
   the outcome into whole tokens (`_outcome_is_success`).
6. **Library exact matching is chain-scoped** (addendum).
   `match_library` takes the chain being checked; an entry recorded
   on a different chain can no longer produce an exact-contract match
   (it may still ABI-shape match). `check.py` passes the chain key.

## Post-review fixes, round 2 (v0.1.2, 2026-11-06)

Everything from the same two-axis review that v0.1.1 deliberately
left behind, worked through finding by finding. Dispositions for the
items NOT fixed are recorded at the end, with reasons - same honesty
rule as the fixes.

Fixed:

1. **Gas-history write disclosed** (was Standards Medium).
   `gas.snapshot()` appends one line per check to
   `data/gas_history.jsonl`; README.md and SECURITY.md now say exactly
   what is written (timestamp, chain, gwei - no addresses, no
   verdicts), where, and how to clear it (delete the file). There is
   still no opt-out switch, and the docs now say that plainly instead
   of letting "read-only" imply it.
2. **UNRESOLVED_INPUT carries the full schema skeleton** (was Spec
   Medium). The unresolved report previously omitted `checks`,
   `library_matches` and `selector_fingerprint`. It now emits all
   nine `checks` sections (null - nothing was probed), empty
   `library_matches` / `selector_fingerprint`, and `contract: null`,
   so `preflight.verdict/1` has one shape for every report. SPEC.md
   documents this.
3. **`totalMinted()` is probed** (was Spec Low). It sat in the
   selector fingerprint but was never called, so contracts exposing
   only a minted count reported supply unknown and could never be
   SOLD_OUT. It is now a view probe and the supply fallback when
   `totalSupply()` does not answer; the supply section records
   `total_minted` and `total_supply_via` so the number's source is
   never ambiguous.
4. **`--wallet` is validated** (was Spec Low). A malformed wallet used
   to flow straight into the check and silently degrade every
   eligibility answer. The CLI now rejects anything that is not a
   full 0x address before anything runs (argparse usage error,
   exit 2).
5. **One shape-match threshold** (was the open tail of Spec Medium
   #6). The library admitted ABI shapes at similarity >= 0.25 while
   the verdict surfaced them only at >= 0.5. `SHAPE_THRESHOLD = 0.5`
   in `library.py` is now the single value used by the matcher, the
   verdict's history reasons and check.py's identification path.
   Below 0.5, shared boilerplate selectors are treated as no
   relation. Note: reports therefore no longer list sub-0.5 shape
   matches under `library_matches`. SPEC.md documents the threshold.
6. **CLI surface documented in the spec** (was Spec Low, scope
   creep). `--library`, `--selftest` and `--version` are deliberate
   (the selftest is the Keccak guard from bug #1 above); SPEC.md's
   door-1 definition now lists them instead of the code quietly
   exceeding the spec.
7. **Duplicated probe/call construction extracted** (was Standards
   Medium smell, ranked fix 4). The four probe shapes in `probes.py`
   were near-identical try/dict/decode blocks; they are now one
   `_read` helper with the shapes as thin wrappers. `rpc.py`'s
   eth_call / eth_estimateGas duplicated call-dict construction; both
   now use one `_tx()` builder. Signature argument parsing lived in
   both `abi.encode_call` and the simulation loop; `abi.arg_types`
   is the one parser now.
8. **Names and misplaced helpers** (was Standards Low). `verdict._r`
   is now `_reason`; the CLI's `_W` is `_CARD_WIDTH`; check.py reuses
   `probes.ZERO_ADDRESS` instead of a hardcoded literal; `wei_to_native`
   moved out of `probes.py` into `abi.py` next to the other decoders
   (still re-exported from probes for compatibility). The verdict's
   verification if-cascade is a status -> reason lookup table.

Not fixed, deliberately:

- **Typed dataclasses for verdict inputs** (Standards Medium smell,
  ranked fix 5). `evaluate()`'s inputs are the report's `checks`
  evidence, serialised verbatim into the `preflight.verdict/1` JSON.
  Typing them means maintaining a parallel dataclass mirror of the
  public schema, or changing the report shape - real risk for zero
  behaviour gain while the schema is the contract. The parts of the
  finding with teeth (duplicated signature parsing, repeated
  cascades) are fixed above; the dicts themselves stay, documented
  here as a considered choice.
- **Splitting probes.py** (Standards Low smell). Helper extraction
  removed the duplication that made the file hurt; the module paths
  are load-bearing (tests, BUILD_NOTES' single-job layout, pip
  installs), and splitting deployment/supply/simulation into
  submodules in a patch release risks more than a Low judgement-call
  smell is worth.
- **Speculative generality items** (Standards Low smell).
  `eth_chainId` / `eth_getBalance` stay in the RPC allowlist because
  SPEC.md's security model names the allowlist exactly - removing
  them would contradict the spec, not satisfy it. `mint_method`
  (library matches) and `cost_table_usd` (gas snapshot) stay because
  both are fields of the emitted verdict JSON: the schema's external
  consumers are their consumers.

Tests: 62 unit tests, OK (43 at v0.1.1; +19: shape-threshold
boundaries and threshold coherence, totalMinted fallback + primacy,
UNRESOLVED skeleton, --wallet validation, _tx builder + allowlist
refusal, arg_types, partial-match guard, not-a-contract card
rendering). `--selftest` OK.

Found while verifying v0.1.2 (not review findings):

- The text card crashed with KeyError on a not-a-contract report:
  `render_text` indexed every checks section directly, but probing
  stops at the bytecode for that verdict, so most sections do not
  exist. The card now reads sections defensively and renders for
  every verdict shape (regression test in tests/test_cli.py).
- KNOWN ISSUE at the time (fixed in round 3 below, same evening):
  `probe_contract` collapses a total
  eth_getCode transport failure (Rpc returns None after every
  endpoint fails) into "no code", so an RPC outage can manufacture a
  NOT_A_CONTRACT block - the deployment-probe sibling of the
  simulation defect v0.1.1 fixed. Diagnosed 2026-11-06 when this
  VM's egress began truncating every BSC endpoint's getCode
  response: both live example checks degraded to NOT_A_CONTRACT
  while eth_blockNumber still answered. The honest fix mirrors
  SIMULATION_UNKNOWN (a DEPLOYMENT_UNKNOWN caution when the code
  read itself failed); it changes verdict semantics, so it waits
  for an explicit decision rather than riding along in this pass.
  Consequence for this release's verification: the JIAYOU live
  re-run DID complete once the egress fluctuated back - BLOCKED,
  reasons SIMULATION_REVERTS + UNVERIFIED_AND_REVERTING +
  UNVERIFIED_SOURCE, exactly as in v0.1.1. The ACAT re-run could
  not be completed: its (larger) bytecode response was truncated
  by this VM's egress on every attempt across all configured
  endpoints, so the run kept degrading to the known issue above.
  ACAT verdict equivalence is covered by the unit suite instead
  (ACAT-shape verdict tests, FakeRpc end-to-end probes).

## Post-review fix, round 3 (v0.1.2, 2026-11-06)

The one known issue round 2 deliberately left behind, fixed the same
evening on George's explicit go ("fix the deployment-unknown gap,
then push"). It ships as part of v0.1.2 - the version was never
published without it.

**DEPLOYMENT_UNKNOWN.** `probe_contract` collapsed a total
eth_getCode transport failure into "no code", so an RPC outage could
manufacture a NOT_A_CONTRACT block - the deployment-probe sibling of
the simulation defect v0.1.1 fixed, and a failure mode with a real
victim on record (the Dronegent contract's false NOT_A_CONTRACT
verdicts on 2026-11-04, from BSC RPC truncation). The deployment read
now has three outcomes, kept distinct end to end:

- bytecode present -> normal flow, unchanged;
- a successful read that came back empty ("0x") -> genuinely not a
  contract: NOT_A_CONTRACT block, unchanged;
- a failed read (Rpc returns None - every endpoint down, timed out,
  or a truncated/malformed body that never parsed; any non-string
  result counts as malformed) -> `deployment` records
  `code_read_failed: true` with `is_contract` and `code_size_bytes`
  null, downstream contract-dependent probes do not run, and the
  verdict engine emits the new DEPLOYMENT_UNKNOWN caution. The
  verdict lands at CAUTION on deployment alone: never BLOCKED by the
  failed read, never CLEAR through it. Absence-based readings that
  would merely restate the failure (PRICE_UNKNOWN,
  NO_KNOWN_MINT_FUNCTION, SIMULATION_NOT_POSSIBLE) are suppressed,
  and the engine does not return early, so independent findings
  still stand on their own (a sold-out supply alongside a failed
  code read still blocks, with DEPLOYMENT_UNKNOWN named next to
  SOLD_OUT). SPEC.md documents the three outcomes alongside
  SIMULATION_UNKNOWN.

Tests: 75 unit tests, OK (62 at the start of this round; +13:
failed/genuine/bytecode reads at the probe level, caution-not-block
and independent-block behaviour at the verdict level, and the RPC
boundary itself - a truncated response body is a failed read, a
healthy "0x" is a successful empty one). `--selftest` OK.

Live verification, run the same evening under the same hostile
egress that exposed the issue (this VM's BSC eth_getCode responses
were still being truncated, so no full read completed):

- JIAYOU minter (2 runs): CAUTION - DEPLOYMENT_UNKNOWN +
  UNVERIFIED_SOURCE + HISTORY_EXACT_FAILURE. The historical BLOCKED
  needs a successful code read to reproduce live and could not be
  re-run under tonight's truncation; the blocked shape is covered
  by the unit suite (JIAYOU-shape verdict and probe tests). What
  matters tonight: v0.1.2 without this fix answered these same runs
  with a false NOT_A_CONTRACT block; v0.1.2 with it answers
  honestly that the deployment question is open.
- ACAT (2 runs): CAUTION - DEPLOYMENT_UNKNOWN + SOURCE_VERIFIED +
  HISTORY_EXACT_SUCCESS. Exactly the prescribed behaviour: the
  independent evidence is favourable and is reported as such, the
  verdict refuses to be CLEAR on an unread contract, and nothing
  was manufactured.

Pushed to github.com/georgeonweb3/preflight (main) as the v0.1.2
snapshot on 2026-11-06; the commit SHA is recorded in the adjacent
sync record (`~/workspace/preflight.muse-github-snapshot-sync.json`),
not here, because a file cannot contain the hash of the commit that
carries it.

## Cut from v1, and why

- Library recording (`--record-outcome` in the skill): the library is
  read-only in v1 plus seed data. Keeps the public surface small;
  recording returns when there are outside users whose outcomes are
  worth recording.
- Base gas bands: not calibrated, so not shown. Honesty over coverage.
- Explorer (BscScan/BaseScan) verification as a second source:
  Sourcify only in v1; explorer APIs need keys and vary per chain.
- Commit/reveal guided probing: those flows need each project's own
  guide (the Agent Periodic Lab lesson). v1 flags NO_KNOWN_MINT_FUNCTION
  as a caution instead of pretending to check them.
- Batch mode, hosted UI, agent-seatbelt code: doors 2 and 3 are spec
  only, per the ship order.

## Known rough edge

A full check took ~28s wall-clock on public BSC RPCs during the
example runs (parallel probes, some endpoints slow to fail over). Fine
for a pre-sign gate; endpoint/timeout tuning is the first optimisation
if it ever annoys.

## Test results

- `python3 -m preflight_core.cli --selftest`: OK.
- `python3 -m unittest discover -s tests`: 43 tests, OK (keccak
  vectors, input resolution, verdict rules incl. both historical
  shapes, Sourcify response mapping, gas bands; v0.1.1 adds fake-RPC
  probe tests, simulation-evidence tests, history classification
  tests and library chain-scoping tests).
- Live runs: see `examples/` (ACAT and JIAYOU, 2026-11-06), plus
  URL-mode and UNRESOLVED_INPUT paths exercised live. Both examples
  re-run after the v0.1.1 fixes: verdicts unchanged (ACAT BLOCKED on
  state with verified source; JIAYOU BLOCKED on trust).

## Remaining steps to publish (nothing here is done yet)

1. George reviews this build and says go.
2. Create the repo: suggested name `preflight` under
   github.com/georgeonweb3 (check the name is free at creation time).
3. `git init` in `~/workspace/preflight/`, first commit. `.gitignore`
   already excludes `__pycache__` and the runtime gas history.
4. Topics/tags: bnb-chain, bsc, base, nft, mint, security, precheck,
   web3, python, ai-agents.
5. Tag `v0.1.0` and cut a GitHub release summarising the two example
   verdicts (they are the best demo the project has).
6. Add a verdict-card screenshot as the repo's social preview image.
7. Launch: X thread + Farcaster cast built from the JIAYOU example
   ("flag said open, price said free, still blocked"), then awesome
   list submissions once there is traction to point at.

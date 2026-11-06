# Preflight for Solana

**Check a Solana mint before you buy, claim or sign. Plain-words verdict: CLEAR, CAUTION or BLOCKED.**

This repo is the Solana home of [Preflight](https://github.com/georgeonweb3/preflight).
The full Preflight tool lives there and stays the source of truth; this repo mirrors the
complete tool with the Solana adapter front and centre, so Solana devs and traders can find
and run it on its own. Pure Python 3.9+ stdlib. No dependencies, nothing to build, never
signs anything, never asks for a key.

## Run it

```bash
python3 -m preflight_core.cli check <MINT_ADDRESS> --chain solana
python3 -m preflight_core.cli check EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v --chain solana
```

## What the Solana adapter checks

On Solana the trap is not hidden contract code, it is live authorities and quiet token
extensions. The adapter reads the chain directly and cross-checks two independent sources:

- **Mint authority** still set: the issuer can print more supply.
- **Freeze authority** still set: the Solana honeypot equivalent; your tokens can be
  frozen in place so you can never sell or move them.
- **Token-2022 extensions**: transfer fees, transfer hooks, non-transferable, closable,
  balance-mutable, permanent delegate, pausable transfers.
- **Holder concentration**: top-1 and top-10 share of supply (RPC largest accounts first,
  GoPlus and RugCheck tables as fallbacks).
- **RugCheck report**: named risks, danger level drives BLOCKED, warn level flags.
- **GoPlus Solana token security**: mintable/freezable cross-check, disagreement with the
  RPC read is itself flagged.

Verdict calibration: an authority on its own is a CAUTION, not a block (Circle's USDC keeps
both authorities live with a clean RugCheck score, and the adapter says so honestly). An
authority escalates to BLOCKED with corroboration: a danger-level risk, a rugged flag, or
extreme holder concentration. A source that does not answer is reported as a gap and caps
the verdict at CAUTION; silence is never dressed up as a pass.

## Live examples (2026-11-06)

- **USDC** `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` -> **CAUTION**: both authorities
  live (Circle's real addresses, flagged as warnings), RugCheck clean, top-10 holders 31.96%.
- **Leonida (pump.fun)** `BWJfBNeXpapG7uoqByp5KSwWgCQba8QcDmA2DQiEpump` -> **CAUTION**:
  authorities revoked, RugCheck clean, capped only because holder concentration was
  unreadable from every source that run, and the report says exactly that.
- A random address with no account -> **BLOCKED**: no account exists at this address.
- An EVM `0x...` address -> rejected with an explanation, not a crash.

The same tool also checks EVM contracts (`--chain bsc`, `--chain base`): Sourcify source
verification, open-status and pause flags, live mint simulation, gas, and the same
CLEAR / CAUTION / BLOCKED verdict card. Full documentation in the docs below.

---

# Preflight

**Check before you sign. Guard before your agent spends.**

Somebody drops a "free mint" link in the group chat. The site says open,
the price says zero, the vibes say hurry. Ten minutes later your wallet
is paying for a contract nobody could read, in a drop that sold out
yesterday. Preflight exists so that story ends at the check, not at the
signature.

Paste a contract address or a project link. Preflight reads the chain
and tells you, in plain words: **CLEAR**, **CAUTION** or **BLOCKED**,
with every reason listed. It never signs anything. It never asks for a
key. It just reads, checks, and shows its work.

## Real output (BNB JIAYOU minter, checked live)

This contract's own page said the first share was free, and its
open-status flag still reads `True` today. Here is what Preflight found
(trimmed, full output in [examples/](examples/)):

```text
Contract   0xc9b4d42abe8966496aa68a736938bf2d508f7a91
Source     Sourcify: none
Open       mintOpen()=True
Simulate   mint(uint256) @ 0 BNB -> REVERT - execution reverted: 0x
Simulate   mint(address,uint256) @ 0 BNB -> REVERT - execution reverted: 0x
------------------------------------------------------------------------
VERDICT    BLOCKED
  [BLOCK] every mint/claim function reverted in eth_call simulation.
          Closed, sold out, ineligible wallet or wrong parameters are
          all possible - do not sign blind
  [BLOCK] source code is NOT verified and the mint simulation reverts.
          Nothing about this contract can be trusted
  [WARN ] source code is NOT verified on Sourcify - nobody outside the
          project can read what this contract does
```

Flag says open. Price looked free. Still blocked, for reasons that were
all sitting on-chain, free to read. That is the whole point.

## Install and run (under two minutes)

No dependencies. Pure Python 3.9+ stdlib. Nothing to build.

```bash
git clone https://github.com/georgeonweb3/preflight.git
cd preflight
python3 -m preflight_core.cli check 0x28a38A372639E8C481aD41B9B16E52c3c6366ACC --chain bsc
```

Or install it as a command:

```bash
pip install .
preflight check <contract-address-or-project-url> --chain bsc --wallet 0xYourPublicAddress
```

- `--wallet` is a public address only. It makes eligibility and the
  simulation run as that wallet. Skip it and the check runs from the
  zero address, which the report tells you. (On Solana it simply
  reports that wallet's balance of the mint.)
- `--json` prints the full machine-readable verdict (schema
  `preflight.verdict/1`). `--out report.json` saves it to a file.
- `--chain` is `bsc` today; `base` and `solana` also work. On Solana
  the target is a token mint address (base58) - see Chains below.
- Exit codes: 0 = check done (BLOCKED is a successful check), 2 = the
  input could not be resolved to a contract address, nothing guessed.

## What it checks

- **Deployment**: is there actually a contract there, how big, what block
- **Proxy**: EIP-1967 upgradeable proxy detection. If the code can be
  swapped later, you hear about it
- **Source verification**: Sourcify match (exact, partial, none, or
  honestly "could not check")
- **Identity**: ERC721 / ERC1155 / token, name, symbol, owner, paused
- **Supply**: minted vs max, how much is actually left
- **Price**: every price function these contracts usually expose,
  including the "free claim" fee traps (`claimFee`, `fee`, friends).
  Price probes that disagree with each other get flagged
- **Open status**: the usual open/active flags, cross-checked against
  the simulation instead of trusted blindly
- **Wallet eligibility**: holdings, minted counts, whitelist answers
  for your wallet
- **Simulation**: the mint/claim call itself, run as `eth_call` at
  value 0 and at the probed price. Revert reasons included
- **Gas**: live gas price with a band (CHEAP to VERY_EXPENSIVE on BSC),
  a 24h baseline, and the estimated USD cost of the actual mint call
- **History**: an experience library of past mints. If this contract, or
  this contract shape, burned someone before, the report says so. Live
  checks always re-run anyway

## What it writes locally (one file, honestly)

Preflight is read-only on-chain, but it keeps one small local log.
Every check appends a single line - unix timestamp, chain, median gas
price in gwei, nothing else - to `data/gas_history.jsonl` inside the
Preflight folder. That log is the rolling 24h gas baseline the report
compares the live price against. It contains no contract addresses,
no wallet addresses and no check results.

There is no switch to turn the write off in this version. To clear
it, delete the file: the baseline simply reports "not enough samples"
until it rebuilds, and the file is recreated on the next check.
Nothing else is written anywhere.

## What it does NOT do

- Not a wallet. It holds no keys, signs nothing, sends nothing. The RPC
  layer refuses non-read-only methods in code.
- Not an audit. It does not check token honeypot/tax behaviour, and a
  CLEAR verdict is "nothing we check tripped", not "this is safe".
- Not financial advice. It tells you what the contract looks like right
  now. Whether to mint is still your call.
- Simulations are best-effort. Chain state moves between the check and
  any signature. Some mint flows (commit/reveal, off-chain signatures)
  expose no standard mint function; Preflight says so instead of
  faking a verdict.

## Chains

BSC is first-class: the probes and gas bands were calibrated on live
mint hunting there. Base works through the same adapter shape; its gas
bands are not calibrated yet, so you get the price without a band name.
New EVM chains are config, not code. PRs welcome.

Solana (`--chain solana`) is its own adapter, because nothing about
the EVM pipeline applies there: no source verification, no eth_call.
The check takes a mint address and reads it three ways - direct RPC
(mint and freeze authorities, supply, Token-2022 extensions such as
transfer fees/hooks and permanent delegates), holder concentration
(largest accounts, with GoPlus and RugCheck holder tables as
fallbacks), plus the RugCheck risk report and the GoPlus Solana token
security entry as cross-checks. The verdict words are the same -
CLEAR / CAUTION / BLOCKED - and the honesty rule is the same: a
source that does not answer is reported as a gap and caps the verdict
at CAUTION, never read as a pass. A live freeze authority is treated
as the Solana honeypot equivalent; live authorities on their own are
cautions (legitimate issuers like USDC keep them), and they escalate
to BLOCKED only with corroboration - a RugCheck danger risk, an
extreme holder concentration, or both authorities over a concentrated
base.

## Roadmap

- **agent-seatbelt** (next): the same core as a gate in front of an AI
  agent that wants to sign or spend. Proposed transaction in, verdict
  plus policy out: spend caps, approval blocking, human-approval mode.
- **testnet-ops** (parked): phone-first testnet workflows reusing the
  same precheck. It ships when users of the first two doors ask for it.

Full design in [SPEC.md](SPEC.md). Security model in
[SECURITY.md](SECURITY.md).

## Tests

```bash
python3 -m unittest discover -s tests
python3 -m preflight_core.cli --selftest
```

## License

MIT. See [LICENSE](LICENSE).

Built by [George Agbe](https://github.com/georgeonweb3), a Web3 builder
working from a phone in Lagos. The checks in here were learned the
expensive way, mint by mint.

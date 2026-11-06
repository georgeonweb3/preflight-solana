# Live example runs

Generated 2026-11-06 by running the CLI in this repo against two real
BSC contracts from Preflight's own hunting history, using the same
wallet that took part in that history
(`0xA79E2C85D5456Fa3d9115d86398896a390159D1c`). Commands:

```bash
python3 -m preflight_core.cli check 0x28a38A372639E8C481aD41B9B16E52c3c6366ACC --chain bsc --wallet 0xA79E...59D1c --out examples/acat-bsc.json
python3 -m preflight_core.cli check 0xc9b4d42abe8966496aa68a736938bf2d508f7a91 --chain bsc --wallet 0xA79E...59D1c --out examples/jiayou-bsc.json
```

Each case has the human card (`.txt`) and the full JSON verdict
(`.json`, schema `preflight.verdict/1`). The two verdicts are both
BLOCKED, but for opposite reasons, and that difference is the product:

## ACAT (acat-bsc) - blocked by state, clean on trust

- History (2026-11-04): Sourcify-verified ERC721, genuinely free mint.
  This wallet minted ACAT #1087 at value 0; gas was 0.00001862 BNB.
  Back then this check would have been CLEAR-shaped: verified, free,
  open, simulation passing.
- This run (2026-11-06, block 131924390): supply is 5,999 of 5,999,
  remaining 0. `mintOpen()` reads False, both simulations revert.
  Verdict BLOCKED on SOLD_OUT / NOT_OPEN / SIMULATION_REVERTS.
  Verification is still an exact Sourcify match, and the library match
  still shows the past success. Zero trust objections; the drop is
  simply over.
- Drift worth knowing: `mintedBy(wallet)` still reads 1 (the mint is
  on-chain history), but `balanceOf` reads 0, so the NFT itself no
  longer sits in that wallet at check time.

Same contract, same code, different day, opposite verdict. A verdict
is a statement about state right now, not a badge a contract earns once.

## BNB JIAYOU minter (jiayou-bsc) - blocked by trust

- History (2026-11-04): the minter contract's source was NOT verified
  (the project's own FAQ admitted it; the separate token contract was
  verified). On-chain reads said the first share was free
  (`quoteFor` = 0, one free slot left of 10,000), but the mint
  simulation reverted bare and the wallet preview failed too. It was
  never executed.
- This run (2026-11-06, block 131924453): Sourcify still has no match
  (`none`). `mintOpen()` still reads True. Both mint simulations still
  revert with an empty reason. Verdict BLOCKED, including the explicit
  UNVERIFIED_AND_REVERTING block: unverified source plus a reverting
  simulation is a hard stop, however friendly the flags look.
- Drift: none in the trust picture. One honest gap: this minter
  answers no supply functions, so the old "one share left" state
  cannot be re-confirmed from the contract itself. A contract you
  cannot read is a contract you cannot clear.

Gas at both runs: 0.05 gwei (CHEAP band), BNB around $694.

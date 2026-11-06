# Security

## The model

Preflight is read-only, and that is enforced in code, not just promised
in docs:

- The RPC client (`preflight_core/rpc.py`) keeps an allowlist of
  read-only JSON-RPC methods: `eth_call`, `eth_estimateGas`,
  `eth_getCode`, `eth_getStorageAt`, `eth_blockNumber`, `eth_chainId`,
  `eth_gasPrice`, `eth_getBalance`. Any other method raises before a
  network call is made. There is no code path that can sign or
  broadcast a transaction.
- Preflight never asks for, reads, or stores a private key, seed
  phrase, or signature. The optional `--wallet` argument is a public
  address used as the `eth_call` sender so eligibility answers are
  wallet-specific.
- It has zero third-party dependencies (Python stdlib only), so there
  is no supply chain beyond Python itself.
- Project pages fetched during URL resolution are treated as untrusted
  input. They are scanned for address-shaped strings only. Nothing in
  a fetched page is executed, and no instruction found in page content
  is followed.

## Local writes

One honest exception to "reads only": each check appends a single line
(unix timestamp, chain key, median gas price in gwei) to
`data/gas_history.jsonl` in the install directory. That file is the
rolling 24h gas baseline, and nothing else is stored - it holds no
contract addresses, no wallet addresses and no verdicts. The write is
best-effort: failing to write it never fails a check. There is no
opt-out flag in this version; delete the file to clear it (it is
recreated on the next check, and the baseline reports as unavailable
until enough samples collect).

## What a verdict is, and is not

A CLEAR verdict means none of Preflight's checks tripped at check
time. It is not a guarantee of safety, not an audit, and not financial
advice. Chain state can change between a check and a signature;
re-run the check at signing time for anything that matters.

## Reporting a problem

Found a case where Preflight said CLEAR and the contract was bad, or
BLOCKED a mint that was fine? That is exactly the data this tool gets
better from. Open an issue at
https://github.com/georgeonweb3/preflight/issues with the contract
address, the chain, and the saved `--out` JSON if you have it.

For anything sensitive (a bug that could make the tool vouch for a
malicious contract), contact George directly via the profile at
https://github.com/georgeonweb3 before posting details publicly.

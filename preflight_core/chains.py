"""Chain configuration.

A chain adapter is data, not code: RPC endpoints, the Sourcify chain id,
the block-explorer domains used when resolving a project URL, the native
token symbol and its price ticker. Every probe in Preflight is plain EVM
eth_call, so a new chain is a new entry here, not a new code path.

BSC is first-class: the gas bands and probe behaviour were calibrated on
it in production. Base ships as a second adapter because the probe logic
generalises cleanly; its gas bands are NOT calibrated yet, so the gas
module reports price without a band there rather than faking one.

Solana is the exception that proves the rule: it is not an EVM chain,
so it is an adapter entry here (kind="solana") plus its own pipeline
(solana_check.py) - base58 mints, mint/freeze authorities and Token-2022
extensions instead of eth_call probes, and RugCheck/GoPlus reports
instead of Sourcify.

Elysium is the pre-launch case: Kinetiq has published the mainnet
chain id (99901) ahead of the 2026-11-09 launch but not a public RPC
or explorer, so the adapter ships with an empty RPC list and check.py
reports that gap honestly until PREFLIGHT_ELYSIUM_RPC activates it
(see effective_rpcs in check.py). Sourcify does not index the chain,
which the sourcify_indexed flag records - "not indexed" is a
different fact from "checked and unverified".
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Chain:
    key: str                      # CLI name, e.g. "bsc"
    name: str                     # display name, e.g. "BNB Chain"
    chain_id: int
    rpcs: tuple
    native_symbol: str            # "BNB", "ETH"
    price_symbol: str             # Binance ticker stem, e.g. "BNBUSDT"
    coingecko_id: str             # price fallback id
    explorer_domains: tuple       # used to rank addresses found on a page
    gas_bands_calibrated: bool = False
    # False when Sourcify has no index for this chain at all: the
    # verification stage then reports "not_indexed" instead of asking
    # Sourcify a question it cannot answer (its reply would otherwise
    # be indistinguishable from a checked contract, or from an outage).
    sourcify_indexed: bool = True
    # "evm" chains run the eth_call pipeline in check.py. "solana" is
    # a different account model entirely (base58 mints, authorities
    # instead of owner functions, no Sourcify/eth_call) and dispatches
    # to solana_check.py; the fields above that only make sense for
    # EVM (chain_id, Sourcify, gas bands) are inert for it.
    kind: str = "evm"


BSC = Chain(
    key="bsc",
    name="BNB Chain (BSC)",
    chain_id=56,
    rpcs=(
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.bnbchain.org",
        "https://bsc-dataseed2.bnbchain.org",
        "https://bsc-dataseed1.defibit.io",
        "https://bsc-rpc.publicnode.com",
    ),
    native_symbol="BNB",
    price_symbol="BNBUSDT",
    coingecko_id="binancecoin",
    explorer_domains=("bscscan.com", "bscscan.io"),
    gas_bands_calibrated=True,
)

BASE = Chain(
    key="base",
    name="Base",
    chain_id=8453,
    rpcs=(
        "https://mainnet.base.org",
        "https://base-rpc.publicnode.com",
        "https://base.drpc.org",
    ),
    native_symbol="ETH",
    price_symbol="ETHUSDT",
    coingecko_id="ethereum",
    explorer_domains=("basescan.org",),
    gas_bands_calibrated=False,
)

ELYSIUM = Chain(
    key="elysium",
    name="Elysium",
    chain_id=99901,
    # Kinetiq published the chain id in its docs on 2026-11-07 ahead of
    # the 2026-11-09 mainnet launch, but no public RPC yet ("Coming
    # soon"). The list stays empty - never guess an endpoint - until
    # the official RPC is published; PREFLIGHT_ELYSIUM_RPC then
    # activates the chain with no code change (check.py).
    rpcs=(),
    native_symbol="HYPE",
    price_symbol="HYPEUSDT",
    coingecko_id="hyperliquid",
    explorer_domains=(),  # explorer not published yet either
    gas_bands_calibrated=False,
    sourcify_indexed=False,
)

SOLANA = Chain(
    key="solana",
    name="Solana",
    chain_id=None,  # no EVM chain id; Solana is not an EVM chain
    rpcs=(
        "https://api.mainnet-beta.solana.com",
        "https://solana-rpc.publicnode.com",
    ),
    native_symbol="SOL",
    price_symbol="SOLUSDT",
    coingecko_id="solana",
    explorer_domains=("solscan.io",),
    kind="solana",
)

CHAINS = {c.key: c for c in (BSC, BASE, ELYSIUM, SOLANA)}

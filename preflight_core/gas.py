"""Gas snapshot and USD cost estimation.

Approach reused from George's bsc-gas-guard skill, built after a 2026-11-04
batch mint paid $0.303 in gas during a 4x intraday spike that nobody had
measured: sample eth_gasPrice from several RPCs and take the median, name
the band, compare against a rolling 24h local baseline, and convert to USD
so "how much will this mint actually cost" is a number, not a vibe.

Bands are calibrated on BSC observations (2026-11-04: morning mints settled
at 0.078-0.085 gwei, a midday transfer at 0.143, an afternoon batch at
0.335). On chains whose config says the bands are not calibrated, we
report the price and skip the band rather than pretending it transfers.

One adaptation from the skill: the native-token USD price is fetched from
the data-api.binance.vision mirror first (api.binance.com is geo-blocked
from some networks), with CoinGecko and DexScreener as fallbacks.

Read-only: eth_gasPrice / eth_estimateGas only. The history file is a
local convenience; failing to write it never fails a check.
"""
import json
import statistics
import time
import urllib.request
from pathlib import Path

from .rpc import Rpc

_HISTORY = Path(__file__).resolve().parent.parent / "data" / "gas_history.jsonl"
_STANDARD_UNITS = (21000, 50000, 100000, 150000, 250000, 500000, 1000000)


def band_for(gwei: float):
    """BSC-calibrated bands, in gwei."""
    if gwei < 0.10:
        return "CHEAP"
    if gwei < 0.20:
        return "NORMAL"
    if gwei < 0.35:
        return "EXPENSIVE"
    return "VERY_EXPENSIVE"


def _get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "preflight/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def native_usd(chain) -> tuple:
    """(price, source) for the chain's native token, or (None, None)."""
    try:
        j = _get_json(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={chain.price_symbol}")
        return float(j["price"]), "binance.vision ticker"
    except Exception:
        pass
    try:
        j = _get_json(f"https://api.coingecko.com/api/v3/simple/price?ids={chain.coingecko_id}&vs_currencies=usd")
        return float(j[chain.coingecko_id]["usd"]), "coingecko"
    except Exception:
        pass
    try:
        # DexScreener: take the most liquid pair for the ticker stem.
        q = chain.price_symbol.replace("USDT", "")
        j = _get_json(f"https://api.dexscreener.com/latest/dex/search?q={q}/USDT")
        pairs = j.get("pairs") or []
        if pairs:
            best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
            return float(best["priceUsd"]), "dexscreener"
    except Exception:
        pass
    return None, None


def _record_history(chain_key, gwei):
    try:
        _HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with open(_HISTORY, "a") as f:
            f.write(json.dumps({"ts": int(time.time()), "chain": chain_key,
                                "gwei": round(gwei, 4)}) + "\n")
    except Exception:
        pass


def _baseline_24h(chain_key):
    try:
        now = int(time.time())
        vals = []
        with open(_HISTORY) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("chain", "bsc") == chain_key and now - d["ts"] <= 86400:
                        vals.append(d["gwei"])
                except Exception:
                    pass
        if len(vals) >= 5:
            return statistics.median(vals), len(vals)
    except Exception:
        pass
    return None, 0


def snapshot(chain, rpc: Rpc = None) -> dict:
    """Median gas price + band + USD cost table for standard call sizes."""
    rpc = rpc or Rpc(chain.rpcs)
    samples = []
    # ask several endpoints independently; Rpc fails over within a call,
    # so rebuild per endpoint to get genuine independent samples.
    for ep in chain.rpcs[:4]:
        r = Rpc([ep]).gas_price_wei()
        if r is not None:
            samples.append(r / 1e9)
    if not samples:
        single = rpc.gas_price_wei()
        if single is not None:
            samples = [single / 1e9]
    if not samples:
        return {"ok": False, "error": "no RPC returned eth_gasPrice"}
    gwei = statistics.median(samples)
    _record_history(chain.key, gwei)
    base, n = _baseline_24h(chain.key)
    price, src = native_usd(chain)
    out = {
        "ok": True,
        "gwei_median": round(gwei, 4),
        "gwei_samples": [round(s, 4) for s in samples],
        "band": band_for(gwei) if chain.gas_bands_calibrated else None,
        "band_note": (None if chain.gas_bands_calibrated else
                      "bands are calibrated on BSC only in v1; price shown without a band"),
        "baseline_24h_median_gwei": (round(base, 4) if base else None),
        "baseline_samples": n,
        "vs_baseline_x": (round(gwei / base, 2) if base else None),
        "native_usd": price,
        "native_usd_source": src,
        "cost_table_usd": {},
    }
    if price:
        for units in _STANDARD_UNITS:
            out["cost_table_usd"][str(units)] = round(units * gwei * 1e-9 * price, 4)
    return out


def estimate_call_cost(chain, rpc: Rpc, contract, calldata, from_addr, value_wei, snap=None) -> dict:
    """eth_estimateGas on one exact call + USD cost at the live gas price."""
    snap = snap or snapshot(chain, rpc)
    units = rpc.estimate_gas(contract, calldata, from_addr=from_addr, value_wei=value_wei)
    if not isinstance(units, int):
        reason = None
        if isinstance(units, dict):
            reason = (units.get("__revert__") or units.get("__error__") or "")[:160]
        return {"ok": False, "error": "eth_estimateGas failed (call likely reverts)",
                "reason": reason, "snapshot": snap}
    gwei = snap.get("gwei_median")
    price = snap.get("native_usd")
    cost_native = units * gwei * 1e-9 if gwei else None
    return {
        "ok": True,
        "gas_units": units,
        "gwei": gwei,
        "band": snap.get("band"),
        "est_cost_native": (round(cost_native, 8) if cost_native is not None else None),
        "est_cost_usd": (round(cost_native * price, 4) if (cost_native is not None and price) else None),
        "note": "estimate is against current chain state; state changes before broadcast change the real cost",
    }

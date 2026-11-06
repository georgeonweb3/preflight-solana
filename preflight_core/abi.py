"""Minimal ABI encode/decode for the types Preflight probes need.

Supports encoding address / uint / int / bool / bytes4 arguments and
decoding uint / bool / address / string return values. That is all the
probe tables use; anything fancier belongs in a full ABI library, and
pulling one in would break the zero-dependency rule.

Adapted from George's bsc-mint-precheck skill (same author).
"""
from decimal import Decimal

from .keccak import selector


def arg_types(sig: str) -> list:
    """The argument types of a signature: "mint(address,uint256)" ->
    ["address", "uint256"], "mint()" -> []. The one place signature
    parsing lives - encoding and simulation both use it."""
    inner = sig[sig.index("(") + 1:sig.rindex(")")] if "(" in sig else ""
    return inner.split(",") if inner else []


def wei_to_native(wei) -> str:
    """Format a wei amount as a native-token decimal string, or
    "unknown" when the amount itself is unknown."""
    if wei is None:
        return "unknown"
    return str((Decimal(wei) / Decimal(10 ** 18)).normalize())


def enc_uint(v: int) -> str:
    return "%064x" % v


def enc_address(a: str) -> str:
    return "000000000000000000000000" + a.lower().replace("0x", "")


def enc_bool(v: bool) -> str:
    return enc_uint(1 if v else 0)


def enc_bytes4(hex4: str) -> str:
    # bytes4 is left-aligned, right-padded
    return hex4.lower().replace("0x", "") + "0" * 56


def encode_call(sig: str, args: list) -> str:
    """Encode a function call as 0x-prefixed calldata hex."""
    types = arg_types(sig)
    data = selector(sig)
    for t, v in zip(types, args):
        if t == "address":
            data += enc_address(v)
        elif t.startswith("uint") or t.startswith("int"):
            data += enc_uint(int(v))
        elif t == "bool":
            data += enc_bool(bool(v))
        elif t == "bytes4":
            data += enc_bytes4(v)
        else:
            raise ValueError(f"unsupported arg type {t}")
    return "0x" + data


def dec_uint(hexdata: str):
    if not hexdata or hexdata == "0x":
        return None
    return int(hexdata[2:66], 16)


def dec_bool(hexdata: str):
    v = dec_uint(hexdata)
    return None if v is None else bool(v)


def dec_address(hexdata: str):
    if not hexdata or len(hexdata) < 66:
        return None
    return "0x" + hexdata[26:66]


def dec_string(hexdata: str):
    if not hexdata or hexdata == "0x":
        return None
    try:
        raw = bytes.fromhex(hexdata[2:])
        if len(raw) >= 64:
            length = int.from_bytes(raw[32:64], "big")
            return raw[64:64 + length].decode("utf-8", "replace")
        return raw.rstrip(b"\x00").decode("utf-8", "replace")
    except Exception:
        return None

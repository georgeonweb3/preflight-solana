"""Pure-Python Keccak-256.

Ethereum function selectors need real Keccak-256. hashlib.sha3_256 is the
NIST variant and produces WRONG selectors, so it must not be substituted.
This implementation is stdlib-only so Preflight runs anywhere Python 3 does,
with nothing to pip install.

Adapted from George's bsc-mint-precheck skill (same author), where it has
been running in production probes since 2026-11-03.
"""

_MASK64 = (1 << 64) - 1
_KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
# rotation offsets by lane index (x + 5*y)
_KECCAK_ROT = [
    0, 1, 62, 28, 27,
    36, 44, 6, 55, 20,
    3, 10, 43, 25, 39,
    41, 45, 15, 21, 8,
    18, 2, 61, 56, 14,
]


def _rol64(v, n):
    if n == 0:
        return v & _MASK64
    return ((v << n) | (v >> (64 - n))) & _MASK64


def _keccak_f1600(a):
    for rnd in range(24):
        # theta
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        d = [c[(x + 4) % 5] ^ _rol64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] ^= d[x]
        # rho + pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol64(a[x + 5 * y], _KECCAK_ROT[x + 5 * y])
        # chi
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
        # iota
        a[0] ^= _KECCAK_RC[rnd]
    return a


def keccak256(data: bytes) -> bytes:
    rate = 136  # 1088-bit rate for 256-bit output
    padded = bytearray(data)
    padded.append(0x01)  # Keccak (not SHA3) domain byte
    while len(padded) % rate != rate - 1:
        padded.append(0x00)
    padded.append(0x80)
    state = [0] * 25
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        state = _keccak_f1600(state)
    out = bytearray()
    for i in range(4):
        out += state[i].to_bytes(8, "little")
    return bytes(out[:32])


def selector(sig: str) -> str:
    """4-byte function selector as 8 hex chars, no 0x prefix."""
    return keccak256(sig.encode()).hex()[:8]


def selftest() -> bool:
    """Known-answer checks. Returns True when the implementation is sound."""
    if keccak256(b"").hex() != "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470":
        return False
    if selector("name()") != "06fdde03":
        return False
    if selector("transfer(address,uint256)") != "a9059cbb":
        return False
    if selector("balanceOf(address)") != "70a08231":
        return False
    return True

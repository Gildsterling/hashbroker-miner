"""HashBroker proof and calldata, independent of the Hashcats protocol."""

import hashlib

CHAIN_ID = 4663
CONTRACT = "0x4272D6f51771839F596082eF48fa84D35239Bab3"
SELECTORS = {
    "challenge": "0xd2ef7398",
    "difficulty": "0x5c062d6c",
    "price": "0x6817c76c",
    "supply": "0x18160ddd",
    "mine": "0xe43e322c",
}
MAX_SUPPLY = 4444
# The official WebGPU shader only searches a 64-bit nonce. The contract hashes a
# full uint256 nonce, but proofs produced here stay inside the 64-bit space so
# that GPU and CPU validators always agree.
NONCE_LIMIT = 2**64


def hex_bytes(value: str, size: int) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 2 + size * 2:
        raise ValueError(f"expected {size}-byte hex value")
    return bytes.fromhex(value[2:])


def proof_hash(wallet: str, nonce: int, challenge: str) -> bytes:
    if not 0 <= nonce < 2**256:
        raise ValueError("nonce must be uint256")
    return hashlib.sha256(
        hex_bytes(wallet, 20) + nonce.to_bytes(32, "big") + hex_bytes(challenge, 32)
    ).digest()


def checked_nonce(nonce) -> int:
    if not isinstance(nonce, int) or isinstance(nonce, bool) or not 0 <= nonce < NONCE_LIMIT:
        raise ValueError("nonce must be an unsigned 64-bit integer")
    return nonce


def leading_zero_bits(digest: bytes) -> int:
    return 256 - int.from_bytes(digest, "big").bit_length()


def mine_calldata(nonce: int, challenge: str) -> str:
    if not 0 <= nonce < 2**256:
        raise ValueError("nonce must be uint256")
    hex_bytes(challenge, 32)
    return SELECTORS["mine"] + f"{nonce:064x}" + challenge[2:]


def job_id(challenge: str, difficulty: int) -> str:
    hex_bytes(challenge, 32)
    if not 1 <= difficulty <= 256:
        raise ValueError("invalid difficulty")
    return f"{challenge.lower()}:{difficulty}"


def block2_schedule(challenge: str) -> list[int]:
    """SHA-256 message schedule of the constant second block.

    The second block is ``challenge[12:32] || 0x80 || zeros || 672`` and never
    depends on the nonce, so the GPU can read it instead of recomputing it.
    """
    challenge_words = [int.from_bytes(word, "big")
                       for word in [hex_bytes(challenge, 32)[i:i + 4] for i in range(0, 32, 4)]]
    block = challenge_words[3:8] + [0x80000000] + [0] * 9 + [672]
    w = list(block)
    for i in range(16, 64):
        x, y = w[i - 15], w[i - 2]
        s0 = ((x >> 7) | (x << 25)) ^ ((x >> 18) | (x << 14)) ^ (x >> 3)
        s1 = ((y >> 17) | (y << 15)) ^ ((y >> 19) | (y << 13)) ^ (y >> 10)
        w.append((s0 + w[i - 7] + s1 + w[i - 16]) & 0xFFFFFFFF)
    return w


_RC = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]


def _rotr(x: int, n: int) -> int:
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


def _compress(words: list[int], state: list[int]) -> list[int]:
    a, b, c, d, e, f, g, h = state
    for i in range(64):
        wi = words[i]
        s1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)
        ch = (e & f) ^ (~e & g)
        t1 = (h + s1 + ch + _RC[i] + wi) & 0xFFFFFFFF
        s0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)
        maj = (a & b) ^ (a & c) ^ (b & c)
        t2 = (s0 + maj) & 0xFFFFFFFF
        h, g, f, e, d, c, b, a = g, f, e, (d + t1) & 0xFFFFFFFF, c, b, a, (t1 + t2) & 0xFFFFFFFF
    return [(x + y) & 0xFFFFFFFF for x, y in zip(state, [a, b, c, d, e, f, g, h])]


def _schedule(words: list[int]) -> list[int]:
    w = list(words)
    for i in range(16, 64):
        x, y = w[i - 15], w[i - 2]
        s0 = _rotr(x, 7) ^ _rotr(x, 18) ^ (x >> 3)
        s1 = _rotr(y, 17) ^ _rotr(y, 19) ^ (y >> 10)
        w.append((s0 + w[i - 7] + s1 + w[i - 16]) & 0xFFFFFFFF)
    return w


def reference_split_hash(wallet: str, nonce: int, challenge: str) -> bytes:
    """SHA-256 computed the same way the GPU splits it: nonce block plus tail block."""
    raw = hex_bytes(wallet, 20) + nonce.to_bytes(32, "big") + hex_bytes(challenge, 32)[:12]
    block1 = [int.from_bytes(raw[i:i + 4], "big") for i in range(0, 64, 4)]
    state = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
             0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
    state = _compress(_schedule(block1), state)
    state = _compress(block2_schedule(challenge), state)
    return b"".join(word.to_bytes(4, "big") for word in state)

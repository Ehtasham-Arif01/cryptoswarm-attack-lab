"""Reduced-keyspace DES.

Real DES has a 64-bit key, but the lowest bit of every byte is a parity bit
that the cipher ignores, leaving 56 effective bits. For the demo we make most
of the key public (a random "leaked" prefix) and only keep `bits` effective
bits secret, so the swarm can search the whole space during a class demo.

The secret bits are packed into the 7 effective (high) bits of the last
bytes of the key. Using the parity bit would create duplicate keys, because
two keys that differ only in parity bits encrypt identically.
"""

from Crypto.Cipher import DES

KNOWN_PLAINTEXT = b"CRYPTOSW"  # 8 bytes = one DES block (known-plaintext attack)
MAX_BITS = 56


def index_to_key(i: int, bits: int, prefix: bytes) -> bytes:
    """Map a search index in [0, 2**bits) to a full 8-byte DES key."""
    if not 0 < bits <= MAX_BITS:
        raise ValueError(f"bits must be in 1..{MAX_BITS}")
    if not 0 <= i < (1 << bits):
        raise ValueError("index out of range")
    key = bytearray(prefix)
    nbytes = (bits + 6) // 7
    for j in range(7, 7 - nbytes, -1):
        key[j] = (key[j] & 0x01) | ((i & 0x7F) << 1)
        i >>= 7
    return bytes(key)


def des_encrypt(key: bytes, block: bytes) -> bytes:
    return DES.new(key, DES.MODE_ECB).encrypt(block)

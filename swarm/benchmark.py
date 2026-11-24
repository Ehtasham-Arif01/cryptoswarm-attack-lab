"""Single-core speed of the primitives behind each attack, for the cost-to-crack calculator.

Each number is "operations per second on one full CPU core" using the same
Python code paths as the workers, so the costs it gives are for *our* code.
Optimized C, GPUs or ASICs are much faster. The dashboard lets the user apply
an assumed speedup for that.
"""

import hashlib
import os
import time

import bcrypt
from Crypto.Cipher import AES, DES

from .toycrypto.des import index_to_key

DURATION = 0.3  # seconds per measurement


def _rate(fn) -> float:
    n, start = 0, time.perf_counter()
    while True:
        fn(n)
        n += 1
        if n % 64 == 0 and time.perf_counter() - start > DURATION:
            return n / (time.perf_counter() - start)


def run() -> dict:
    prefix = os.urandom(8)
    pt = b"CRYPTOSW"
    block16 = bytes(16)
    des_new, des_ecb = DES.new, DES.MODE_ECB
    aes_new, aes_ecb = AES.new, AES.MODE_ECB
    keys128 = [os.urandom(16) for _ in range(256)]
    keys256 = [os.urandom(32) for _ in range(256)]
    salt10 = bcrypt.gensalt(rounds=10)

    rates = {
        # one DES key trial, exactly like the brute-force worker
        "des": _rate(lambda i: des_new(index_to_key(i & 0xFFFFF, 20, prefix), des_ecb).encrypt(pt)),
        "aes128": _rate(lambda i: aes_new(keys128[i & 255], aes_ecb).encrypt(block16)),
        "aes256": _rate(lambda i: aes_new(keys256[i & 255], aes_ecb).encrypt(block16)),
        "md5": _rate(lambda i: hashlib.md5(b"pw%d" % i).digest()),
        "sha256": _rate(lambda i: hashlib.sha256(b"msg%d" % i).digest()),
    }
    start, n = time.perf_counter(), 0
    while n < 2 or time.perf_counter() - start < DURATION:
        bcrypt.hashpw(b"password", salt10)
        n += 1
    rates["bcrypt10"] = n / (time.perf_counter() - start)
    return {k: round(v, 2) for k, v in rates.items()}

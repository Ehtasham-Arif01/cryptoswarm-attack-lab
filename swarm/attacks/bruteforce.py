"""Attack 1: known-plaintext brute force against reduced-key DES.

The attacker knows one plaintext block and its ciphertext and tries every
possible key until one encrypts the plaintext to that ciphertext.
"""

import os
import secrets

from Crypto.Cipher import DES

from ..toycrypto.des import KNOWN_PLAINTEXT, des_encrypt, index_to_key
from .base import Attack, ChunkContext, Plan

TARGET_CHUNKS = 256
REPORT_EVERY = 2048


class BruteForceDES(Attack):
    name = "bruteforce"
    title = "Brute-force DES (reduced key)"
    unit = "keys"
    params_schema = {"bits": {"type": "int", "min": 12, "max": 32, "default": 20}}

    def plan(self, params: dict) -> Plan:
        bits = self.clean_params(params)["bits"]
        prefix = os.urandom(8)
        secret_index = secrets.randbelow(1 << bits)
        key = index_to_key(secret_index, bits, prefix)
        ciphertext = des_encrypt(key, KNOWN_PLAINTEXT)

        total = 1 << bits
        n_chunks = min(TARGET_CHUNKS, total // 256)
        size = total // n_chunks
        chunks = [{"id": c, "start": c * size, "end": (c + 1) * size} for c in range(n_chunks)]

        meta = {
            "bits": bits,
            "prefix": prefix.hex(),
            "plaintext": KNOWN_PLAINTEXT.hex(),
            "ciphertext": ciphertext.hex(),
        }
        summary = f"Brute-force DES with {bits} secret bits: {total:,} keys in {n_chunks} chunks"
        return Plan(meta, chunks, total, {"key": key.hex(), "index": secret_index}, summary)

    def run_chunk(self, meta: dict, chunk: dict, ctx: ChunkContext) -> dict:
        bits = meta["bits"]
        prefix = bytes.fromhex(meta["prefix"])
        plaintext = bytes.fromhex(meta["plaintext"])
        target = bytes.fromhex(meta["ciphertext"])
        new, mode = DES.new, DES.MODE_ECB

        batch = 0
        for i in range(chunk["start"], chunk["end"]):
            key = index_to_key(i, bits, prefix)
            batch += 1
            if new(key, mode).encrypt(plaintext) == target:
                ctx.progress(batch)
                return {"found": True, "key": key.hex(), "index": i}
            if batch == REPORT_EVERY:
                if not ctx.progress(batch):
                    return {"aborted": True}
                batch = 0
        if batch:
            ctx.progress(batch)
        return {}

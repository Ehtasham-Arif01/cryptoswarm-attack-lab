"""Attack 2: meet-in-the-middle against Double DES (Stallings ch. 6).

Double DES:   C = E(k2, E(k1, P))
With two n-bit keys a naive brute force needs 2^(2n) tries. Meet-in-the-middle
instead uses the fact that the middle value M = E(k1, P) = D(k2, C):

  Phase 1 (build): for every k1, store E(k1, P1) -> k1 in a shared table   (2^n work, 2^n memory)
  Phase 2 (match): for every k2, look up D(k2, C1) in the table              (2^n work)
  Each hit is checked against a second known pair (P2, C2) to rule out false matches.

Total ≈ 2·2^n operations instead of 2^(2n): Double DES gives barely one
extra bit of security. That's why 3DES (three encryptions) is used instead.
"""

import os
import secrets

from Crypto.Cipher import DES

from .. import keys as K
from ..toycrypto.des import index_to_key
from .base import Attack, ChunkContext, Plan

PLAINTEXTS = (b"MEET-MID", b"2nd PAIR")
TARGET_CHUNKS = 128  # per phase
BATCH = 2048
TAG_HEX = 12  # store a 48-bit tag of the middle value to save Redis memory


def double_des_encrypt(k1: bytes, k2: bytes, block: bytes) -> bytes:
    return DES.new(k2, DES.MODE_ECB).encrypt(DES.new(k1, DES.MODE_ECB).encrypt(block))


class MeetInTheMiddle(Attack):
    name = "mitm"
    title = "Meet-in-the-middle on Double DES"
    unit = "ops"
    # 20 bits = 1M table entries (~80 MB in Redis). Keep it there for an 8 GB laptop.
    params_schema = {"bits": {"type": "int", "min": 12, "max": 20, "default": 18}}

    def plan(self, params: dict) -> Plan:
        bits = self.clean_params(params)["bits"]
        prefix1, prefix2 = os.urandom(8), os.urandom(8)
        i1, i2 = secrets.randbelow(1 << bits), secrets.randbelow(1 << bits)
        k1, k2 = index_to_key(i1, bits, prefix1), index_to_key(i2, bits, prefix2)
        c1, c2 = (double_des_encrypt(k1, k2, p) for p in PLAINTEXTS)

        space = 1 << bits
        n = min(TARGET_CHUNKS, space // 256)
        size = space // n
        build = [{"id": c, "phase": 0, "start": c * size, "end": (c + 1) * size} for c in range(n)]
        match = [{"id": n + c, "phase": 1, "start": c * size, "end": (c + 1) * size} for c in range(n)]

        meta = {
            "bits": bits,
            "prefix1": prefix1.hex(),
            "prefix2": prefix2.hex(),
            "p1": PLAINTEXTS[0].hex(),
            "c1": c1.hex(),
            "p2": PLAINTEXTS[1].hex(),
            "c2": c2.hex(),
        }
        secret = {"k1": k1.hex(), "k2": k2.hex(), "i1": i1, "i2": i2}
        summary = (
            f"Meet-in-the-middle on Double DES, 2 × {bits}-bit keys: naive needs 2^{2 * bits} tries, "
            f"MITM needs ~{2 * space:,} ops"
        )
        return Plan(meta, [build, match], 2 * space, secret, summary, ["build table", "match"])

    def summarize(self, job: dict) -> str:
        if job["status"] == "found":
            naive = 2 ** (2 * job["meta"]["bits"])
            less = naive / max(job["tried"], 1)
            return f"both keys recovered with {job['tried']:,} ops ({less:,.0f}× less than naive)"
        return super().summarize(job)

    def run_chunk(self, meta: dict, chunk: dict, ctx: ChunkContext) -> dict:
        bits = meta["bits"]
        new, mode = DES.new, DES.MODE_ECB
        table = K.job_table(ctx.job_id)
        start, end = chunk["start"], chunk["end"]

        if chunk["phase"] == 0:  # ---- build: E(k1, P1) -> k1
            prefix, p1 = bytes.fromhex(meta["prefix1"]), bytes.fromhex(meta["p1"])
            for lo in range(start, end, BATCH):
                hi = min(lo + BATCH, end)
                mapping = {
                    new(index_to_key(i, bits, prefix), mode).encrypt(p1).hex()[:TAG_HEX]: i for i in range(lo, hi)
                }
                ctx.r.hset(table, mapping=mapping)
                if not ctx.progress(hi - lo):
                    return {"aborted": True}
            return {}

        # ---- match: look up D(k2, C1) in the table
        prefix1, prefix2 = bytes.fromhex(meta["prefix1"]), bytes.fromhex(meta["prefix2"])
        c1 = bytes.fromhex(meta["c1"])
        p2, c2 = bytes.fromhex(meta["p2"]), bytes.fromhex(meta["c2"])
        for lo in range(start, end, BATCH):
            hi = min(lo + BATCH, end)
            k2s = [index_to_key(i, bits, prefix2) for i in range(lo, hi)]
            tags = [new(k2, mode).decrypt(c1).hex()[:TAG_HEX] for k2 in k2s]
            hits = ctx.r.hmget(table, tags)
            for offset, hit in enumerate(hits):
                if hit is None:
                    continue
                k1 = index_to_key(int(hit), bits, prefix1)
                k2 = k2s[offset]
                if double_des_encrypt(k1, k2, p2) == c2:  # confirm with the second pair
                    ctx.progress(offset + 1)
                    return {"found": True, "k1": k1.hex(), "k2": k2.hex(), "i1": int(hit), "i2": lo + offset}
            if not ctx.progress(hi - lo):
                return {"aborted": True}
        return {}

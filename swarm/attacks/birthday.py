"""Attack 3: birthday attack on a truncated hash (Yuval's attack, Stallings ch. 11).

Story: Bob wants Alice to sign a contract for $100, then claim she signed one
for $100,000. He writes both contracts in 2^24 slightly different wordings
(each variant picks one of two phrasings at 24 places) and hashes them all
until a fair variant and a fraudulent variant have the same hash. Alice signs
the hash of the fair one, and the signature is equally valid for the fraud.

For an n-bit hash there are N = 2^n possible values, but a collision between
the two sets is expected after only about sqrt(N) = 2^(n/2) variants each:
the birthday paradox. We truncate SHA-256 to n bits so the swarm can do it live.

Workers share one Redis table: tag -> "L<i>" (legit) or "F<i>" (fraud).
HSETNX is atomic, so if a tag is already taken by the *other* set, it's a collision.
"""

import hashlib
import math

from .. import keys as K
from .base import Attack, ChunkContext, Plan

# 24 places where the wording can change without changing the meaning.
SLOTS = [
    ("I, Alice,", "I, Alice Smith,"),
    ("agree", "hereby agree"),
    ("to pay", "to transfer"),
    ("Bob", "Mr. Bob Jones"),
    ("for the", "in exchange for the"),
    ("old car", "used car"),
    ("he sold me", "that he sold to me"),
    ("today.", "on this day."),
    ("The payment", "This payment"),
    ("will", "shall"),
    ("be made", "be completed"),
    ("within", "in no more than"),
    ("30 days", "thirty days"),
    ("by bank transfer.", "via bank transfer."),
    ("This", "The present"),
    ("agreement", "contract"),
    ("is", "shall be"),
    ("final", "binding"),
    ("and", "and also"),
    ("cannot be", "may not be"),
    ("changed", "modified"),
    ("later.", "afterwards."),
    ("Signed,", "Signed by"),
    ("Alice.", "Alice S."),
]
AMOUNT_AFTER_SLOT = 3  # the amount goes right after "Bob"
LEGIT_AMOUNT = "$100"
FRAUD_AMOUNT = "$100,000"
SEARCH_FACTOR = 8  # search up to 8·sqrt(N) variants: P(no collision) ≈ e^-64
BATCH = 1024
TARGET_CHUNKS = 256


def contract(i: int, amount: str) -> str:
    words = []
    for b, (a, c) in enumerate(SLOTS):
        words.append(c if (i >> b) & 1 else a)
        if b == AMOUNT_AFTER_SLOT:
            words.append(amount)
    return " ".join(words)


def truncated_hash(message: str, bits: int) -> int:
    digest = hashlib.sha256(message.encode()).digest()
    return int.from_bytes(digest[:8], "big") >> (64 - bits)


class BirthdayAttack(Attack):
    name = "birthday"
    title = "Birthday attack on truncated SHA-256"
    unit = "hashes"
    # 38 bits: ~2^20 table entries expected (~80 MB in Redis), fine on an 8 GB laptop
    params_schema = {"bits": {"type": "int", "min": 20, "max": 38, "default": 34}}

    def plan(self, params: dict) -> Plan:
        bits = self.clean_params(params)["bits"]
        root = 1 << math.ceil(bits / 2)  # ≈ sqrt(N)
        variants = SEARCH_FACTOR * root  # per set
        n = min(TARGET_CHUNKS, variants // 64)
        size = variants // n
        chunks = [{"id": c, "start": c * size, "end": (c + 1) * size} for c in range(n)]
        meta = {
            "bits": bits,
            "legit_amount": LEGIT_AMOUNT,
            "fraud_amount": FRAUD_AMOUNT,
            "expected_hashes": round(2 * math.sqrt(math.pi / 4 * 2**bits)),  # 2 sets × E[k] for a cross collision
        }
        summary = (
            f"Birthday attack on SHA-256 truncated to {bits} bits: 2^{bits} = {2**bits:,} possible hashes, "
            f"collision expected after ~{meta['expected_hashes']:,}"
        )
        return Plan(meta, [chunks], 2 * variants, {}, summary, ["hash variants"])

    def run_chunk(self, meta: dict, chunk: dict, ctx: ChunkContext) -> dict:
        bits = meta["bits"]
        table = K.job_table(ctx.job_id)
        legit, fraud = meta["legit_amount"], meta["fraud_amount"]
        for lo in range(chunk["start"], chunk["end"], BATCH):
            hi = min(lo + BATCH, chunk["end"])
            entries = []
            for i in range(lo, hi):
                entries.append((format(truncated_hash(contract(i, legit), bits), "x"), f"L{i}"))
                entries.append((format(truncated_hash(contract(i, fraud), bits), "x"), f"F{i}"))
            pipe = ctx.r.pipeline(transaction=False)
            for tag, who in entries:
                pipe.hsetnx(table, tag, who)
            added = pipe.execute()

            clashes = [entries[k] for k, ok in enumerate(added) if not ok]
            if clashes:
                owners = ctx.r.hmget(table, [tag for tag, _ in clashes])
                for (tag, who), owner in zip(clashes, owners):
                    if owner and owner[0] != who[0]:  # legit vs fraud: the collision we want
                        li = int(who[1:] if who[0] == "L" else owner[1:])
                        fi = int(who[1:] if who[0] == "F" else owner[1:])
                        ctx.progress(2 * (hi - lo))
                        return {
                            "found": True,
                            "hash": tag,
                            "legit_index": li,
                            "fraud_index": fi,
                            "legit": contract(li, legit),
                            "fraud": contract(fi, fraud),
                        }
            if not ctx.progress(2 * (hi - lo)):
                return {"aborted": True}
        return {}

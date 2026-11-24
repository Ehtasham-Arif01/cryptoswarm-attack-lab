import math

from swarm.attacks import ATTACKS
from swarm.attacks.birthday import SLOTS, contract, truncated_hash


class FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def hsetnx(self, name, key, value):
        self.ops.append((name, key, value))

    def execute(self):
        out = []
        for name, key, value in self.ops:
            table = self.r.h.setdefault(name, {})
            out.append(key not in table)
            table.setdefault(key, value)
        return out


class FakeRedis:
    def __init__(self):
        self.h = {}

    def pipeline(self, transaction=True):
        return FakePipe(self)

    def hmget(self, name, keys):
        return [self.h.get(name, {}).get(k) for k in keys]


class FakeCtx:
    def __init__(self):
        self.r = FakeRedis()
        self.job_id = "test"
        self.count = 0

    def progress(self, n):
        self.count += n
        return True


def test_contract_variants_are_distinct_and_keep_amount():
    legit = {contract(i, "$100") for i in range(4096)}
    assert len(legit) == 4096
    assert all("Bob $100 " in m or "Jones $100 " in m for m in list(legit)[:50])
    assert len(SLOTS) == 24


def test_birthday_finds_a_real_collision_near_sqrt_n():
    attack = ATTACKS["birthday"]
    plan = attack.plan({"bits": 24})
    ctx = FakeCtx()
    found = None
    for chunk in plan.phases[0]:
        result = attack.run_chunk(plan.meta, chunk, ctx)
        if result.get("found"):
            found = result
            break

    assert found is not None
    assert "$100,000" in found["fraud"] and "$100,000" not in found["legit"]
    h1, h2 = truncated_hash(found["legit"], 24), truncated_hash(found["fraud"], 24)
    assert h1 == h2 == int(found["hash"], 16)
    # Far fewer than 2^24 = 16.7M hashes: within a few × sqrt(N)
    assert ctx.count < 16 * math.sqrt(2**24)

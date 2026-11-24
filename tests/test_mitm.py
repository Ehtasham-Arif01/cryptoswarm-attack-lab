from swarm.attacks import ATTACKS
from swarm.attacks.mitm import double_des_encrypt


class FakeRedis:
    """Just the two hash commands the MITM attack uses."""

    def __init__(self):
        self.h = {}

    def hset(self, name, mapping):
        self.h.setdefault(name, {}).update({k: str(v) for k, v in mapping.items()})

    def hmget(self, name, keys):
        table = self.h.get(name, {})
        return [table.get(k) for k in keys]


class FakeCtx:
    def __init__(self, r):
        self.r = r
        self.job_id = "test"
        self.count = 0

    def progress(self, n):
        self.count += n
        return True


def test_plan_has_build_then_match_phase():
    plan = ATTACKS["mitm"].plan({"bits": 12})
    build, match = plan.phases
    assert {c["phase"] for c in build} == {0} and {c["phase"] for c in match} == {1}
    assert plan.phase_ends == [len(build), len(build) + len(match)]
    assert sorted(c["id"] for c in plan.chunks) == list(range(len(plan.chunks)))
    assert plan.total == 2 * 4096


def test_mitm_recovers_both_keys():
    attack = ATTACKS["mitm"]
    plan = attack.plan({"bits": 12})
    ctx = FakeCtx(FakeRedis())

    for chunk in plan.phases[0]:
        assert attack.run_chunk(plan.meta, chunk, ctx) == {}

    found = None
    for chunk in plan.phases[1]:
        result = attack.run_chunk(plan.meta, chunk, ctx)
        if result.get("found"):
            found = result
            break

    assert found is not None
    assert found["k1"] == plan.secret["k1"]
    assert found["k2"] == plan.secret["k2"]
    # Far less work than the naive 2^(2·12) = 16.7M tries
    assert ctx.count <= plan.total


def test_double_des_is_two_single_des():
    from Crypto.Cipher import DES

    k1, k2, p = b"key-one!", b"key-two!", b"block!!!"
    inner = DES.new(k1, DES.MODE_ECB).encrypt(p)
    assert double_des_encrypt(k1, k2, p) == DES.new(k2, DES.MODE_ECB).encrypt(inner)

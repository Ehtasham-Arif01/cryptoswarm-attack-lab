import pytest

import swarm.attacks.passwords as pw
from swarm.attacks import ATTACKS


class FakeRedis:
    def __init__(self):
        self.h = {}

    def hkeys(self, name):
        return list(self.h.get(name, {}))

    def hsetnx(self, name, key, value):
        table = self.h.setdefault(name, {})
        if key in table:
            return 0
        table[key] = value
        return 1


class FakeCtx:
    def __init__(self):
        self.r = FakeRedis()
        self.job_id = "test"
        self.count = 0

    def progress(self, n):
        self.count += n
        return True


@pytest.fixture(autouse=True)
def no_events(monkeypatch):
    monkeypatch.setattr(pw, "push_event", lambda *a, **k: None)


def run(plan, chunks=None):
    ctx = FakeCtx()
    for chunk in chunks or plan.phases[0]:
        if ATTACKS["passwords"].run_chunk(plan.meta, chunk, ctx).get("found"):
            break
    return set(ctx.r.h.get("swarm:job:test:cracked", {})), ctx


def test_candidates_are_unique_and_ordered_easy_first():
    n = pw.num_candidates()
    # l33t leaves words without a/e/i/o/s/t unchanged ("lucky"), so a few guesses repeat
    assert len({pw.candidate(i) for i in range(n)}) > 0.99 * n
    assert pw.candidate(0) == pw.WORDS[0]
    assert pw.candidate(len(pw.WORDS)) == pw.WORDS[0].capitalize()


def test_chunks_cover_every_candidate_once():
    plan = ATTACKS["passwords"].plan({"scheme": "md5"})
    covered = sum(c["end"] - c["start"] for c in plan.phases[0])
    assert covered == pw.num_candidates()


@pytest.mark.parametrize("scheme", ["md5", "md5_salted"])
def test_md5_cracks_all_dictionary_passwords_but_not_random_ones(scheme):
    plan = ATTACKS["passwords"].plan({"scheme": scheme})
    secret = plan.secret["passwords"]
    weak = {u for u, p in secret.items() if p in {pw.candidate(i) for i in range(pw.num_candidates())}}
    cracked, _ = run(plan)
    assert cracked == weak
    assert len(weak) >= 9  # 9 dictionary passwords, 3 strong random ones


def test_unsalted_md5_leaks_equal_passwords():
    plan = ATTACKS["passwords"].plan({"scheme": "md5"})
    hashes = [u["hash"] for u in plan.meta["users"]]
    assert len(set(hashes)) < len(hashes)  # two users share a password -> same hash


def test_salt_hides_equal_passwords():
    plan = ATTACKS["passwords"].plan({"scheme": "md5_salted"})
    hashes = [u["hash"] for u in plan.meta["users"]]
    assert len(set(hashes)) == len(hashes)


def test_bcrypt_still_falls_for_plain_words():
    plan = ATTACKS["passwords"].plan({"scheme": "bcrypt", "cost": 4})
    secret = plan.secret["passwords"]
    plain_users = {u for u, p in secret.items() if p in pw.WORDS}
    cracked, ctx = run(plan, [{"id": 0, "start": 0, "end": len(pw.WORDS)}])
    assert cracked == plain_users and len(plain_users) >= 3
    assert ctx.count == len(pw.WORDS) * len(pw.USERS)

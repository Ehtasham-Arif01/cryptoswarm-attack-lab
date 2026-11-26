import json

import pytest

from swarm.attacks import ATTACKS
from swarm.attacks.classical import (
    ENGLISH_IOC,
    PLAINTEXT,
    ioc,
    letters,
    pick_length,
    recovered_key,
    shift_text,
    shortest_period,
)


class FakeRedis:
    def __init__(self):
        self.h = {}

    def hset(self, name, key, value):
        self.h.setdefault(name, {})[key] = value

    def hgetall(self, name):
        return dict(self.h.get(name, {}))


class FakeCtx:
    def __init__(self):
        self.r = FakeRedis()
        self.job_id = "test"

    def progress(self, n):
        return True


def run(plan):
    ctx = FakeCtx()
    attack = ATTACKS["classical"]
    for phase in plan.phases:
        for chunk in phase:
            attack.run_chunk(plan.meta, chunk, ctx)
    live = {k: json.loads(v) for k, v in ctx.r.h["swarm:job:test:live"].items()}
    return recovered_key(live), live


def test_english_text_has_english_ioc():
    assert abs(ioc(letters(PLAINTEXT)) - ENGLISH_IOC) < 0.01


def test_shift_roundtrip():
    assert shift_text(shift_text("HELLOWORLD", [3, 7], +1), [3, 7], -1) == "HELLOWORLD"


@pytest.mark.parametrize("cipher,key_len", [("caesar", 1), ("vigenere", 4), ("vigenere", 6), ("vigenere", 9)])
def test_frequency_analysis_recovers_key(cipher, key_len):
    plan = ATTACKS["classical"].plan({"cipher": cipher, "key_len": key_len})
    key, _ = run(plan)
    assert key == shortest_period(plan.secret["key"])


def test_partly_repeating_key_regression():
    """CI once failed on key UGUK: length 2 looked almost as English as length 4."""
    for key in ("UGUK", "ABAC", "KXKXKY"):
        c = shift_text(letters(PLAINTEXT), [ord(k) - 65 for k in key], +1)
        iocs = {n: sum(ioc(c[x::n]) for x in range(n)) / n for n in range(1, 17)}
        assert pick_length(iocs) == len(key), key


def test_shortest_period():
    assert shortest_period("ABAB") == "AB" and shortest_period("ABC") == "ABC" and shortest_period("Q") == "Q"


def test_one_time_pad_gives_nothing():
    plan = ATTACKS["classical"].plan({"cipher": "otp"})
    key, live = run(plan)
    best_ioc = max(v["ioc"] for k, v in live.items() if k.startswith("len:"))
    assert best_ioc < 0.05  # no key length makes the text look like English
    recovered = shift_text(plan.meta["ciphertext"], [ord(c) - 65 for c in key], -1) if key else ""
    assert recovered != plan.secret["plaintext"]

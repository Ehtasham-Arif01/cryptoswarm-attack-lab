import json

import pytest

from swarm.attacks import ATTACKS
from swarm.attacks.classical import ENGLISH_IOC, PLAINTEXT, ioc, letters, shift_text


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
    cols = sorted((int(k[4:]), v) for k, v in live.items() if k.startswith("col:"))
    return "".join(v["letter"] for _, v in cols), live


def test_english_text_has_english_ioc():
    assert abs(ioc(letters(PLAINTEXT)) - ENGLISH_IOC) < 0.01


def test_shift_roundtrip():
    assert shift_text(shift_text("HELLOWORLD", [3, 7], +1), [3, 7], -1) == "HELLOWORLD"


@pytest.mark.parametrize("cipher,key_len", [("caesar", 1), ("vigenere", 4), ("vigenere", 6), ("vigenere", 9)])
def test_frequency_analysis_recovers_key(cipher, key_len):
    plan = ATTACKS["classical"].plan({"cipher": cipher, "key_len": key_len})
    key, _ = run(plan)
    assert key == plan.secret["key"]


def test_one_time_pad_gives_nothing():
    plan = ATTACKS["classical"].plan({"cipher": "otp"})
    key, live = run(plan)
    best_ioc = max(v["ioc"] for k, v in live.items() if k.startswith("len:"))
    assert best_ioc < 0.05  # no key length makes the text look like English
    recovered = shift_text(plan.meta["ciphertext"], [ord(c) - 65 for c in key], -1)
    assert recovered != plan.secret["plaintext"]

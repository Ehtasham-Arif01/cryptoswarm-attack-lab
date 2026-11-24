from Crypto.Cipher import DES

from swarm.attacks import ATTACKS
from swarm.toycrypto.des import index_to_key


class FakeCtx:
    def __init__(self):
        self.count = 0

    def progress(self, n):
        self.count += n
        return True


def effective(key: bytes) -> bytes:
    """Strip parity bits: what DES actually uses."""
    return bytes(b & 0xFE for b in key)


def test_index_to_key_gives_distinct_effective_keys():
    prefix = bytes(range(8))
    keys = {effective(index_to_key(i, 14, prefix)) for i in range(1 << 14)}
    assert len(keys) == 1 << 14


def test_index_to_key_keeps_prefix_bytes():
    prefix = bytes.fromhex("a1b2c3d4e5f60718")
    key = index_to_key(5, 20, prefix)  # 20 bits -> last 3 bytes change
    assert key[:5] == prefix[:5]


def test_parity_bits_are_really_ignored_by_des():
    """Explains why we never use the parity bit for secret bits."""
    pt = b"CRYPTOSW"
    k1 = bytes.fromhex("0123456789abcdef")
    k2 = bytes(b ^ 0x01 for b in k1)
    assert DES.new(k1, DES.MODE_ECB).encrypt(pt) == DES.new(k2, DES.MODE_ECB).encrypt(pt)


def test_bruteforce_finds_the_secret_key():
    attack = ATTACKS["bruteforce"]
    plan = attack.plan({"bits": 12})
    assert sum(c["end"] - c["start"] for c in plan.chunks) == plan.total == 4096

    found = None
    for chunk in plan.chunks:
        result = attack.run_chunk(plan.meta, chunk, FakeCtx())
        if result.get("found"):
            found = result
            break
    assert found is not None
    assert found["key"] == plan.secret["key"]
    assert found["index"] == plan.secret["index"]


def test_params_are_clamped():
    attack = ATTACKS["bruteforce"]
    assert attack.clean_params({"bits": 99})["bits"] == 32
    assert attack.clean_params({})["bits"] == 20

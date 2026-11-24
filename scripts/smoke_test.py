#!/usr/bin/env python3
"""End-to-end smoke test: run every attack against the live stack and check the result.

Usage (stack must be running):   python3 scripts/smoke_test.py [http://localhost:8090]
Uses only the standard library, so it runs anywhere (CI, laptop) without a venv.
"""

import json
import sys
import time
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8090").rstrip("/")


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def wait_ready(timeout: float = 90) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            call("GET", "/api/health")
            workers = len(call("GET", "/api/state")["workers"])
            if workers:
                return workers
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit("stack not ready: no healthy coordinator or no workers")


def run(attack: str, params: dict, timeout: float = 180) -> dict:
    jid = call("POST", "/api/jobs", {"attack": attack, "params": params})["id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = call("GET", "/api/state")["job"]
        if job["id"] == jid and job["status"] != "running":
            return job
        time.sleep(1)
    raise AssertionError(f"{attack} did not finish within {timeout}s")


def check_bruteforce(job):
    assert job["status"] == "found", job["status"]
    assert job["result"]["key"] == job["secret"]["key"]


def check_mitm(job):
    assert job["status"] == "found", job["status"]
    assert (job["result"]["k1"], job["result"]["k2"]) == (job["secret"]["k1"], job["secret"]["k2"])


def check_birthday(job):
    assert job["status"] == "found", job["status"]
    assert job["result"]["legit"] != job["result"]["fraud"]


def check_passwords(job):
    assert len(job["cracked"]) == 9, f"cracked {len(job['cracked'])}/12"
    for user, info in job["cracked"].items():
        assert job["secret"]["passwords"][user] == info["password"]


def check_padding_oracle(job):
    blocks = sorted(job["live"].items(), key=lambda kv: int(kv[0]))
    assert all(b["status"] == "done" for _, b in blocks), [b["status"] for _, b in blocks]
    plain = b"".join(bytes.fromhex(b["plain"]) for _, b in blocks)
    assert plain[: -plain[-1]].decode() == job["secret"]["plaintext"]


def check_padding_oracle_fixed(job):
    assert all(b["status"] == "failed" for b in job["live"].values())


def check_classical(job):
    cols = sorted((int(k[4:]), v["letter"]) for k, v in job["live"].items() if k.startswith("col:"))
    assert "".join(c for _, c in cols) == job["secret"]["key"]


CASES = [
    ("bruteforce", {"bits": 16}, check_bruteforce),
    ("mitm", {"bits": 14}, check_mitm),
    ("birthday", {"bits": 26}, check_birthday),
    ("passwords", {"scheme": "md5"}, check_passwords),
    ("padding_oracle", {"mode": "vulnerable"}, check_padding_oracle),
    ("padding_oracle", {"mode": "fixed"}, check_padding_oracle_fixed),
    ("classical", {"cipher": "vigenere", "key_len": 6}, check_classical),
]


def main() -> int:
    print(f"stack ready with {wait_ready()} worker(s) at {BASE}")
    failures = 0
    for attack, params, check in CASES:
        start = time.time()
        try:
            check(run(attack, params))
            print(f"  ✅ {attack:15} {json.dumps(params):40} {time.time() - start:6.1f}s")
        except AssertionError as exc:
            failures += 1
            print(f"  ❌ {attack:15} {json.dumps(params):40} {exc}")
    history = call("GET", "/api/history?limit=20")
    print(f"history has {len(history)} recent runs")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

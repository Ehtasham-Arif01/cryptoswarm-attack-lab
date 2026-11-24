import importlib

from swarm.attacks import ATTACKS


def test_history_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HISTORY_DB", str(tmp_path / "h.db"))
    import coordinator.history as history

    history = importlib.reload(history)
    run = {"id": "abc", "attack": "bruteforce", "title": "t", "status": "found", "params": {"bits": 20},
           "outcome": "key found", "created": 1.0, "elapsed": 2.0, "tried": 10, "total": 20, "unit": "keys",
           "workers": 8, "vcpus": 2.0, "rate": 5.0, "samples": {"8": [5.0, 3]}}
    history.record(run)
    history.record(run)  # recording twice is harmless
    assert history.known_ids(["abc", "zzz"]) == {"abc"}
    rows = history.list_runs()
    assert len(rows) == 1 and rows[0]["params"] == {"bits": 20}
    assert rows[0]["samples"] == {"8": [5.0, 3]}
    assert history.list_runs("mitm") == []
    assert history.clear() == 1


def test_every_attack_can_summarize_a_cancelled_job():
    job = {"status": "cancelled", "tried": 0, "total": 1, "meta": {}, "live": {}, "cracked": {}, "result": None}
    for name, attack in ATTACKS.items():
        if name == "passwords":
            job["meta"] = {"scheme": "md5", "cost": 8, "users": []}
        elif name == "classical":
            job["meta"] = {"cipher": "vigenere"}
        elif name == "padding_oracle":
            job["meta"] = {"n_blocks": 6, "mode": "vulnerable"}
        assert isinstance(attack.summarize(job), str)

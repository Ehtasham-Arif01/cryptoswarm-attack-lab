"""CryptoSwarm worker.

Loop: take a chunk from the queue -> run the attack plugin on it -> report.
Scale it with:  docker compose up -d --scale worker=8
"""

import json
import os
import signal
import socket
import threading
import time
import traceback

import redis

from swarm import keys as K
from swarm.attacks import ATTACKS
from swarm.events import FINISH_JOB_LUA, push_event

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
FLUSH_EVERY = 0.25  # seconds between progress reports to Redis


class ChunkContext:
    """Passed to Attack.run_chunk(); batches progress updates to Redis."""

    def __init__(self, worker: "Worker", job_id: str, chunk_id: int):
        self.w = worker
        self.job_id = job_id
        self.chunk_id = chunk_id
        self.pending = 0
        self.chunk_tried = 0
        self.last = time.monotonic()
        self.running = True

    def progress(self, n: int) -> bool:
        self.pending += n
        now = time.monotonic()
        if now - self.last >= FLUSH_EVERY:
            self.flush(now)
        return self.running and not self.w.stopping

    def flush(self, now: float | None = None) -> None:
        now = now or time.monotonic()
        n, dt = self.pending, now - self.last
        self.chunk_tried += n
        pipe = self.w.r.pipeline()
        if n:
            pipe.hincrby(K.job(self.job_id), "tried", n)
            pipe.hset(K.chunk_progress(self.job_id), self.chunk_id, self.chunk_tried)
        pipe.hget(K.job(self.job_id), "status")
        self.running = pipe.execute()[-1] == "running"
        with self.w.lock:
            self.w.state["rate"] = n / dt if dt > 0 else 0.0
            self.w.state["tried_total"] += n
        self.pending = 0
        self.last = now


class Worker:
    def __init__(self) -> None:
        self.id = os.environ.get("WORKER_ID") or socket.gethostname()[:12]
        self.r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.finish_job = self.r.register_script(FINISH_JOB_LUA)
        self.lock = threading.Lock()
        self.stopping = False
        self.state = {
            "id": self.id,
            "lang": "python",
            "status": "idle",
            "job": None,
            "chunk": None,
            "rate": 0.0,
            "tried_total": 0,
            "chunks_done": 0,
            "started": time.time(),
        }

    # ---------- heartbeat ----------
    def heartbeat_loop(self) -> None:
        while not self.stopping:
            with self.lock:
                if self.state["status"] == "idle":
                    self.state["rate"] = 0.0
                payload = json.dumps(self.state)
            try:
                self.r.set(K.worker(self.id), payload, ex=K.HEARTBEAT_TTL)
            except redis.RedisError:
                pass
            time.sleep(1)

    def set_state(self, **kw) -> None:
        with self.lock:
            self.state.update(kw)

    # ---------- queue handling ----------
    def requeue(self, raw: str, task: dict) -> None:
        """Hand an unfinished chunk back to the queue (used on graceful shutdown)."""
        jid, cid = task["job_id"], task["chunk"]["id"]
        prog = int(self.r.hget(K.chunk_progress(jid), cid) or 0)
        pipe = self.r.pipeline()
        pipe.lrem(K.processing(self.id), 1, raw)
        pipe.hincrby(K.job(jid), "tried", -prog)
        pipe.hdel(K.chunk_progress(jid), cid)
        pipe.hdel(K.chunks_inflight(jid), cid)
        pipe.rpush(K.QUEUE, raw)  # right end = picked up next
        pipe.execute()

    def finish_chunk(self, raw: str, task: dict, result: dict) -> None:
        jid, cid = task["job_id"], task["chunk"]["id"]
        pipe = self.r.pipeline()
        pipe.lrem(K.processing(self.id), 1, raw)
        pipe.hdel(K.chunks_inflight(jid), cid)
        pipe.hdel(K.chunk_progress(jid), cid)
        pipe.sadd(K.chunks_done(jid), cid)
        pipe.scard(K.chunks_done(jid))
        pipe.hget(K.job(jid), "nchunks")
        *_, n_done, n_chunks = pipe.execute()
        with self.lock:
            self.state["chunks_done"] += 1

        now = str(time.time())
        if result.get("found"):
            result["worker"] = self.id
            if self.finish_job(keys=[K.job(jid)], args=["found", json.dumps(result), now]):
                push_event(self.r, f"🔓 Worker {self.id} cracked job {jid}! {self.describe(result)}", "success")
        elif int(n_done) >= int(n_chunks or 0):
            if self.finish_job(keys=[K.job(jid)], args=["exhausted", json.dumps({}), now]):
                push_event(self.r, f"Job {jid}: search space exhausted, nothing found", "warn")

    @staticmethod
    def describe(result: dict) -> str:
        return f"key = 0x{result['key']}" if "key" in result else ""

    def handle(self, raw: str) -> None:
        task = json.loads(raw)
        jid, chunk = task["job_id"], task["chunk"]
        job = self.r.hgetall(K.job(jid))
        if job.get("status") != "running":  # stale chunk from a finished/cancelled job
            self.r.lrem(K.processing(self.id), 1, raw)
            return
        attack = ATTACKS[job["attack"]]
        meta = json.loads(job["meta"])

        self.r.hset(K.chunks_inflight(jid), chunk["id"], self.id)
        self.set_state(status="working", job=jid, chunk=chunk["id"])
        ctx = ChunkContext(self, jid, chunk["id"])
        try:
            result = attack.run_chunk(meta, chunk, ctx)
            ctx.flush()
        except Exception:
            traceback.print_exc()
            push_event(self.r, f"Worker {self.id} crashed on chunk {chunk['id']} of job {jid}", "error")
            result = {}

        if result.get("aborted"):
            if self.stopping and ctx.running:
                self.requeue(raw, task)  # job still needs it: give it back
            else:
                self.r.lrem(K.processing(self.id), 1, raw)
                self.r.hdel(K.chunks_inflight(jid), chunk["id"])
        else:
            self.finish_chunk(raw, task, result)
        self.set_state(status="idle", job=None, chunk=None)

    # ---------- main ----------
    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        print(f"[worker {self.id}] online, redis={REDIS_URL}", flush=True)
        while not self.stopping:
            try:
                raw = self.r.blmove(K.QUEUE, K.processing(self.id), timeout=1, src="RIGHT", dest="LEFT")
                if raw:
                    self.handle(raw)
            except redis.ConnectionError:
                print(f"[worker {self.id}] redis unavailable, retrying", flush=True)
                time.sleep(1)
        self.r.delete(K.worker(self.id))
        print(f"[worker {self.id}] stopped", flush=True)

    def stop(self, *_):
        self.stopping = True


if __name__ == "__main__":
    Worker().run()

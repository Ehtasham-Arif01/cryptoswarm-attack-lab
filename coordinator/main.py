"""CryptoSwarm coordinator.

- REST API to start/cancel attacks
- WebSocket that streams a live snapshot of the swarm twice a second
- Reaper: detects dead workers (missing heartbeat) and requeues their chunks
"""

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from coordinator import history
from swarm import benchmark
from swarm import keys as K
from swarm.attacks import ATTACKS
from swarm.events import MAX_EVENTS, make_event

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
SNAPSHOT_INTERVAL = 0.5

r = aioredis.from_url(REDIS_URL, decode_responses=True)


async def event(msg: str, level: str = "info") -> None:
    pipe = r.pipeline()
    pipe.lpush(K.EVENTS, make_event(msg, level))
    pipe.ltrim(K.EVENTS, 0, MAX_EVENTS - 1)
    await pipe.execute()


# ---------------------------------------------------------------- reaper
async def requeue_processing(wid: str) -> list[str]:
    """Move all unfinished tasks of a dead worker back onto the queue."""
    plist = K.processing(wid)
    requeued = []
    for raw in await r.lrange(plist, 0, -1):
        task = json.loads(raw)
        jid, cid = task["job_id"], task["chunk"]["id"]
        prog = int(await r.hget(K.chunk_progress(jid), cid) or 0)
        pipe = r.pipeline()
        pipe.lrem(plist, 1, raw)
        pipe.hincrby(K.job(jid), "tried", -prog)  # its partial work is lost, so un-count it
        pipe.hdel(K.chunk_progress(jid), cid)
        pipe.hdel(K.chunks_inflight(jid), cid)
        pipe.rpush(K.QUEUE, raw)  # right end = picked up next
        await pipe.execute()
        if await r.hget(K.job(jid), "status") == "running":
            requeued.append(f"#{cid}")
    return requeued


async def track_job_workers() -> None:
    """Record the most workers/vCPUs online during the running job (workers may join after it starts)."""
    jid = await r.get(K.CURRENT_JOB)
    if not jid or await r.hget(K.job(jid), "status") != "running":
        return
    workers = await load_workers()
    vcpus = sum(w.get("cpus") or 1 for w in workers)
    old_w, old_v = await r.hmget(K.job(jid), "workers", "vcpus")
    if len(workers) > int(old_w or 0):
        await r.hset(K.job(jid), mapping={"workers": len(workers), "vcpus": max(vcpus, float(old_v or 0))})
    # one speed sample per second, grouped by how many workers are busy right now
    busy = [w for w in workers if w["status"] == "working"]
    if busy:
        pipe = r.pipeline()
        pipe.hincrbyfloat(K.job(jid), f"s_rate:{len(busy)}", sum(w["rate"] for w in workers))
        pipe.hincrby(K.job(jid), f"s_cnt:{len(busy)}", 1)
        await pipe.execute()


async def reaper() -> None:
    known: set[str] = set()
    while True:
        try:
            alive = {K.id_from_key(k) async for k in r.scan_iter(K.worker("*"))}
            for wid in sorted(alive - known):
                await event(f"Worker {wid} joined the swarm")
            for wid in sorted(known - alive):
                await event(f"Worker {wid} went offline", "warn")
            async for plist in r.scan_iter(K.processing("*")):
                wid = K.id_from_key(plist)
                if wid not in alive:
                    chunks = await requeue_processing(wid)
                    if chunks:
                        await event(
                            f"💀 Worker {wid} died mid-chunk. Requeued chunk(s) {', '.join(chunks)}", "error"
                        )
            known = alive
            await track_job_workers()
        except Exception as exc:  # keep the reaper alive no matter what
            print(f"[reaper] {exc!r}", flush=True)
        await asyncio.sleep(1)


# ---------------------------------------------------------------- history
def run_record(job: dict, h: dict) -> dict:
    attack = ATTACKS[job["attack"]]
    try:
        outcome = attack.summarize(job)
    except Exception as exc:  # a summary must never break recording
        outcome = f"{job['status']} ({exc!r})"
    if job["status"] == "cancelled":
        pct = 100 * job["tried"] / max(job["total"], 1)
        outcome = f"cancelled at {pct:.0f}%" + ("" if outcome == "cancelled" else f": {outcome}")
    return {
        "id": job["id"], "attack": job["attack"], "title": job["title"], "status": job["status"],
        "params": job["params"], "outcome": outcome, "created": job["created"], "elapsed": job["elapsed"],
        "tried": job["tried"], "total": job["total"], "unit": job["unit"],
        "workers": int(h["workers"]) if h.get("workers") else None,
        "vcpus": float(h["vcpus"]) if h.get("vcpus") else None,
        "rate": job["tried"] / job["elapsed"] if job["elapsed"] > 0 else None,
        # {busy workers: [average swarm speed, number of 1-second samples]}
        "samples": {
            k[7:]: [float(h[k]) / int(h["s_cnt:" + k[7:]]), int(h["s_cnt:" + k[7:]])]
            for k in h if k.startswith("s_rate:") and int(h.get("s_cnt:" + k[7:], 0))
        },
    }


async def history_recorder() -> None:
    """Save every job that has stopped running. Also backfills jobs still in Redis after a restart."""
    while True:
        try:
            jids = await r.lrange(K.JOBS, 0, 200)
            missing = [j for j in jids if j not in await asyncio.to_thread(history.known_ids, jids)]
            for jid in missing:
                h = await r.hgetall(K.job(jid))
                if not h or h.get("status") == "running" or h.get("attack") not in ATTACKS:
                    continue
                job = await load_job(jid)
                job["secret"] = None
                await asyncio.to_thread(history.record, run_record(job, h))
        except Exception as exc:
            print(f"[history] {exc!r}", flush=True)
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = [asyncio.create_task(reaper()), asyncio.create_task(history_recorder())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="CryptoSwarm Coordinator", lifespan=lifespan)


# ---------------------------------------------------------------- snapshot
async def load_workers() -> list[dict]:
    wkeys = [k async for k in r.scan_iter(K.worker("*"))]
    raws = await r.mget(wkeys) if wkeys else []
    return sorted((json.loads(x) for x in raws if x), key=lambda w: w["started"])


async def load_job(jid: str) -> dict | None:
    h = await r.hgetall(K.job(jid))
    if not h:
        return None
    nchunks = int(h["nchunks"])
    done = await r.smembers(K.chunks_done(jid))
    grid = [0] * nchunks
    for c in done:
        grid[int(c)] = 1
    status = h["status"]
    finished = float(h["finished"]) if h.get("finished") else None
    job = {
        "id": jid,
        "attack": h["attack"],
        "title": ATTACKS[h["attack"]].title,
        "unit": ATTACKS[h["attack"]].unit,
        "status": status,
        "params": json.loads(h["params"]),
        "meta": json.loads(h["meta"]),
        "total": int(h["total"]),
        "tried": int(h["tried"]),
        "nchunks": nchunks,
        "chunks_done": grid,
        "inflight": await r.hgetall(K.chunks_inflight(jid)),
        "created": float(h["created"]),
        "elapsed": (finished or time.time()) - float(h["created"]),
        "result": json.loads(h["result"]) if h.get("result") else None,
        "secret": None,
        "phase": int(h.get("phase", 0)),
        "phase_ends": json.loads(h.get("phase_ends", f"[{nchunks}]")),
        "phase_names": json.loads(h.get("phase_names", '["search"]')),
        "table_size": await r.hlen(K.job_table(jid)),
        "cracked": {u: json.loads(v) for u, v in (await r.hgetall(K.job_cracked(jid))).items()},
        "live": {c: json.loads(v) for c, v in (await r.hgetall(K.job_live(jid))).items()},
    }
    if status != "running":  # only reveal the real answer after the attack ends
        secret = await r.get(K.job_secret(jid))
        job["secret"] = json.loads(secret) if secret else None
    return job


async def snapshot() -> dict:
    workers = await load_workers()
    jid = await r.get(K.CURRENT_JOB)
    return {
        "t": time.time(),
        "workers": workers,
        "swarm_rate": sum(w["rate"] for w in workers),
        "queue_depth": await r.llen(K.QUEUE),
        "job": await load_job(jid) if jid else None,
        "events": [json.loads(e) for e in await r.lrange(K.EVENTS, 0, 29)],
    }


# ---------------------------------------------------------------- API
class JobRequest(BaseModel):
    attack: str
    params: dict = {}


async def cancel_current() -> str | None:
    jid = await r.get(K.CURRENT_JOB)
    if jid and await r.hget(K.job(jid), "status") == "running":
        await r.hset(K.job(jid), mapping={"status": "cancelled", "finished": time.time()})
        await r.delete(K.QUEUE)
        await event(f"Job {jid} cancelled", "warn")
        return jid
    return None


@app.get("/api/health")
async def health():
    await r.ping()
    return {"ok": True}


@app.get("/api/attacks")
async def list_attacks():
    return [a.describe() for a in ATTACKS.values()]


@app.get("/api/state")
async def state():
    return await snapshot()


@app.post("/api/jobs")
async def create_job(req: JobRequest):
    attack = ATTACKS.get(req.attack)
    if not attack:
        raise HTTPException(404, f"unknown attack '{req.attack}'")
    await cancel_current()
    await r.delete(K.QUEUE)
    old = await r.get(K.CURRENT_JOB)
    if old:  # free the previous job's lookup table (can be ~80 MB)
        await r.delete(K.job_table(old))

    params = attack.clean_params(req.params)
    workers = await load_workers()
    vcpus = sum(w.get("cpus") or 1 for w in workers)  # an uncapped worker counts as 1 vCPU
    try:
        plan = await asyncio.to_thread(attack.plan, params)
    except OSError as exc:  # e.g. the oracle container isn't running
        raise HTTPException(503, f"could not prepare attack: {exc}") from exc
    jid = uuid.uuid4().hex[:8]

    pipe = r.pipeline()
    pipe.hset(
        K.job(jid),
        mapping={
            "attack": attack.name,
            "status": "running",
            "params": json.dumps(params),
            "meta": json.dumps(plan.meta),
            "total": plan.total,
            "tried": 0,
            "nchunks": len(plan.chunks),
            "phase": 0,
            "phase_ends": json.dumps(plan.phase_ends),
            "phase_names": json.dumps(plan.phase_names),
            "created": time.time(),
            "workers": len(workers),
            "vcpus": vcpus,
        },
    )
    pipe.set(K.job_secret(jid), json.dumps(plan.secret))
    pipe.set(K.CURRENT_JOB, jid)
    pipe.lpush(K.JOBS, jid)
    pipe.ltrim(K.JOBS, 0, 499)
    task = lambda c: json.dumps({"job_id": jid, "chunk": c})  # noqa: E731
    # LPUSH in order, so chunk 0 sits at the right end and is popped first
    pipe.lpush(K.QUEUE, *[task(c) for c in plan.phases[0]])
    for i, phase in enumerate(plan.phases[1:], start=1):  # later phases wait until the previous one ends
        pipe.rpush(K.job_phase(jid, i), *[task(c) for c in phase])
    await pipe.execute()
    await event(f"▶ Job {jid} started. {plan.summary}")
    return {"id": jid}


@app.post("/api/jobs/cancel")
async def cancel():
    return {"cancelled": await cancel_current()}


BENCH_KEY = f"{K.PREFIX}:benchmark"
BENCH_TTL = 600


async def last_bruteforce_run() -> dict | None:
    """Real speed of the swarm in the most recent finished brute-force job."""
    for jid in await r.lrange(K.JOBS, 0, 100):
        h = await r.hgetall(K.job(jid))
        if h.get("attack") != "bruteforce" or not h.get("finished"):
            continue
        elapsed = float(h["finished"]) - float(h["created"])
        if elapsed < 1 or int(h["tried"]) < 10_000:
            continue
        rate = int(h["tried"]) / elapsed
        vcpus = float(h.get("vcpus") or 0) or None
        return {"job": jid, "rate": rate, "workers": int(h.get("workers") or 0), "vcpus": vcpus,
                "per_vcpu": rate / vcpus if vcpus else None}
    return None


@app.get("/api/benchmark")
async def get_benchmark(refresh: bool = False):
    """Single-core speeds (cached for 10 min) + the measured speed of the real swarm."""
    cached = None if refresh else await r.get(BENCH_KEY)
    if cached:
        bench = json.loads(cached)
    else:
        jid = await r.get(K.CURRENT_JOB)
        busy = bool(jid) and await r.hget(K.job(jid), "status") == "running"
        bench = {"rates": await asyncio.to_thread(benchmark.run), "t": time.time(), "busy": busy}
        if not busy:  # a measurement taken while workers use the CPU is too low: don't keep it
            await r.set(BENCH_KEY, json.dumps(bench), ex=BENCH_TTL)
    bench["swarm"] = await last_bruteforce_run()
    return bench


@app.get("/api/history")
async def get_history(attack: str | None = None, limit: int = 500):
    return await asyncio.to_thread(history.list_runs, attack, min(limit, 2000))


@app.delete("/api/history")
async def clear_history():
    # Forget old jobs in Redis too, or the recorder would import them again.
    current = await r.get(K.CURRENT_JOB)
    running = current and await r.hget(K.job(current), "status") == "running"
    await r.delete(K.JOBS)
    if running:
        await r.lpush(K.JOBS, current)
    return {"deleted": await asyncio.to_thread(history.clear)}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(await snapshot())
            await asyncio.sleep(SNAPSHOT_INTERVAL)
    except (WebSocketDisconnect, RuntimeError):
        pass

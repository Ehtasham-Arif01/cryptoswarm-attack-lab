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
        except Exception as exc:  # keep the reaper alive no matter what
            print(f"[reaper] {exc!r}", flush=True)
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(reaper())
    yield
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
    plan = attack.plan(params)
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
        },
    )
    pipe.set(K.job_secret(jid), json.dumps(plan.secret))
    pipe.set(K.CURRENT_JOB, jid)
    pipe.lpush(K.JOBS, jid)
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


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(await snapshot())
            await asyncio.sleep(SNAPSHOT_INTERVAL)
    except (WebSocketDisconnect, RuntimeError):
        pass

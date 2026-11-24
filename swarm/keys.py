"""Redis key layout shared by the coordinator and workers.

Queue model ("reliable queue"):
  - The coordinator LPUSHes chunk tasks onto QUEUE.
  - A worker atomically moves a task from QUEUE into its own processing list
    (BLMOVE), so a task is never lost if the worker dies mid-chunk.
  - If a worker's heartbeat expires, the coordinator moves every task in that
    worker's processing list back onto QUEUE (fault tolerance).
"""

PREFIX = "swarm"

QUEUE = f"{PREFIX}:queue"
EVENTS = f"{PREFIX}:events"
JOBS = f"{PREFIX}:jobs"
CURRENT_JOB = f"{PREFIX}:current_job"

HEARTBEAT_TTL = 4  # seconds without a heartbeat before a worker counts as dead


def worker(wid: str) -> str:
    """JSON heartbeat/status of one worker (expires after HEARTBEAT_TTL)."""
    return f"{PREFIX}:worker:{wid}"


def processing(wid: str) -> str:
    """Tasks a worker has taken but not finished."""
    return f"{PREFIX}:processing:{wid}"


def job(jid: str) -> str:
    """Job hash: attack, status, params, meta, total, tried, nchunks, created, finished, result."""
    return f"{PREFIX}:job:{jid}"


def job_secret(jid: str) -> str:
    """The real answer, kept away from workers; revealed on the dashboard after the job ends."""
    return f"{PREFIX}:job:{jid}:secret"


def chunks_done(jid: str) -> str:
    return f"{PREFIX}:job:{jid}:done"


def chunks_inflight(jid: str) -> str:
    """Hash chunk_id -> worker_id."""
    return f"{PREFIX}:job:{jid}:inflight"


def chunk_progress(jid: str) -> str:
    """Hash chunk_id -> work units already counted, so a requeued chunk can be subtracted."""
    return f"{PREFIX}:job:{jid}:progress"


def job_phase(jid: str, phase: int) -> str:
    """Tasks of a later phase, waiting until the previous phase is finished."""
    return f"{PREFIX}:job:{jid}:phase:{phase}"


def job_table(jid: str) -> str:
    """Shared lookup table for attacks that need one (MITM, birthday)."""
    return f"{PREFIX}:job:{jid}:table"


def job_cracked(jid: str) -> str:
    """Hash user -> json, for attacks that find several answers (passwords)."""
    return f"{PREFIX}:job:{jid}:cracked"


def id_from_key(key: str) -> str:
    """'swarm:worker:abc' -> 'abc'."""
    return key.split(":", 2)[2]

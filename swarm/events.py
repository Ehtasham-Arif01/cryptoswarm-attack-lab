import json
import time

from . import keys as K

MAX_EVENTS = 100


def make_event(msg: str, level: str = "info") -> str:
    return json.dumps({"t": time.time(), "msg": msg, "level": level})


def push_event(r, msg: str, level: str = "info") -> None:
    """Sync version, for workers. The coordinator has an async copy."""
    pipe = r.pipeline()
    pipe.lpush(K.EVENTS, make_event(msg, level))
    pipe.ltrim(K.EVENTS, 0, MAX_EVENTS - 1)
    pipe.execute()


# Finish a job exactly once: only the first worker to report a result wins.
# KEYS[1] = job hash, ARGV = new status, result json, finished timestamp
FINISH_JOB_LUA = """
if redis.call('HGET', KEYS[1], 'status') == 'running' then
  redis.call('HSET', KEYS[1], 'status', ARGV[1], 'result', ARGV[2], 'finished', ARGV[3])
  return 1
end
return 0
"""

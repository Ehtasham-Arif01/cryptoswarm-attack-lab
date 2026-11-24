"""Attack 4: dictionary + rules attack on a leaked password table.

A website's user table has leaked. The swarm tries common passwords with
mangling rules (Capitalize, UPPER, l33t, digits/years/symbols appended)
against it. The storage scheme decides how much that costs:

  md5         unsalted MD5. Each guess is hashed ONCE and checked against all
              users at the same time. Equal passwords give equal hashes, and
              precomputed (rainbow) tables work.
  md5_salted  MD5(salt || password) with a random salt per user. Each guess has
              to be hashed again for every user, and precomputed tables are useless.
  bcrypt      salted AND deliberately slow (cost factor: 2^cost rounds).
              Every guess costs ~100,000× more than MD5.

Candidates are ordered from most to least likely, so even bcrypt falls quickly
for users whose password is a plain dictionary word. Slow hashing helps, but
strong passwords are still needed.
"""

import hashlib
import json
import os
import secrets
import time

import bcrypt

from .. import keys as K
from ..events import push_event
from .base import Attack, ChunkContext, Plan

WORDS = """password dragon monkey football baseball letmein shadow master sunshine princess
qwerty welcome login admin flower hello freedom whatever trustno1 superman batman starwars
iloveyou charlie michael jennifer jordan hunter ranger buster soccer hockey killer george
summer winter autumn spring thomas robert daniel pepper ginger cookie chocolate banana orange
cheese coffee secret silver golden diamond tiger lion eagle falcon phoenix wizard matrix
access mustang ferrari porsche harley corvette yankees cowboys lakers chelsea arsenal
liverpool london paris berlin tokyo pakistan lahore karachi islamabad cricket pokemon naruto
minecraft fortnite gaming hacker computer internet network server system linux windows apple
samsung google android iphone nothing forever love angel heaven family friend happy smile
lucky magic music guitar piano rock metal purple yellow blue green black white red""".split()

TRANSFORMS = ["plain", "capitalize", "upper", "leet"]
SUFFIXES = (
    [""]
    + [str(d) for d in range(10)]
    + ["123", "1234", "12345", "!", "!!", "@", "#", "123!", "@123", "1!"]
    + [f"{d:02d}" for d in range(100)]
    + [str(y) for y in range(1970, 2026)]
)
LEET = str.maketrans({"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7"})
USERS = ["alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi", "ivan", "judy", "mallory", "trent"]
SCHEMES = ["md5", "md5_salted", "bcrypt"]


def num_candidates() -> int:
    return len(WORDS) * len(TRANSFORMS) * len(SUFFIXES)


def candidate(i: int) -> str:
    """Index -> guess. Order: all plain words first, then transforms, then suffixes."""
    word = WORDS[i % len(WORDS)]
    rest = i // len(WORDS)
    t = TRANSFORMS[rest % len(TRANSFORMS)]
    suffix = SUFFIXES[rest // len(TRANSFORMS)]
    if t == "capitalize":
        word = word.capitalize()
    elif t == "upper":
        word = word.upper()
    elif t == "leet":
        word = word.translate(LEET)
    return word + suffix


def store(password: str, scheme: str, cost: int) -> dict:
    """How the website stores a password."""
    if scheme == "md5":
        return {"hash": hashlib.md5(password.encode()).hexdigest()}
    if scheme == "md5_salted":
        salt = os.urandom(8).hex()
        return {"salt": salt, "hash": hashlib.md5((salt + password).encode()).hexdigest()}
    return {"hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=cost)).decode()}


def benchmark(cost: int) -> dict:
    """Hashes per second on one full core, so the dashboard can compare schemes fairly."""
    t = time.perf_counter()
    for i in range(20000):
        hashlib.md5(b"bench%d" % i).digest()
    md5 = 20000 / (time.perf_counter() - t)
    salt = bcrypt.gensalt(rounds=cost)
    t, n = time.perf_counter(), 0
    while n < 2 or time.perf_counter() - t < 0.2:
        bcrypt.hashpw(b"bench", salt)
        n += 1
    return {"md5": round(md5), "bcrypt": round(n / (time.perf_counter() - t), 2)}


class PasswordCracking(Attack):
    name = "passwords"
    title = "Dictionary attack on a leaked password table"
    unit = "hashes"
    params_schema = {
        "scheme": {"type": "choice", "options": SCHEMES, "default": "md5"},
        "cost": {"type": "int", "min": 4, "max": 12, "default": 8},
    }

    def plan(self, params: dict) -> Plan:
        p = self.clean_params(params)
        scheme, cost = p["scheme"], p["cost"]
        n_cand = num_candidates()
        plain = len(WORDS) * len(TRANSFORMS)  # no suffix: the easiest guesses

        # Choose the users' passwords: a realistic mix of weak and strong.
        shared = candidate(secrets.randbelow(len(WORDS)))  # plain word, used by 2 users
        picks = [
            shared,
            shared,
            candidate(secrets.randbelow(len(WORDS))),  # another plain word
            candidate(secrets.randbelow(plain)),  # e.g. Dragon, MONKEY, sh4d0w
            candidate(plain + secrets.randbelow(len(WORDS) * len(TRANSFORMS) * 20)),  # short suffixes
            candidate(secrets.randbelow(n_cand)),
            candidate(secrets.randbelow(n_cand)),
            candidate(secrets.randbelow(n_cand)),
            candidate(secrets.randbelow(n_cand)),
            secrets.token_urlsafe(9),  # strong random passwords: not in any dictionary
            secrets.token_urlsafe(9),
            secrets.token_urlsafe(9),
        ]
        order = list(range(len(USERS)))
        secrets.SystemRandom().shuffle(order)
        passwords = {USERS[order[k]]: pw for k, pw in enumerate(picks)}
        users = [{"user": u, **store(passwords[u], scheme, cost)} for u in USERS]

        per_guess = 1 if scheme == "md5" else len(USERS)  # unsalted: one hash covers every user
        n_chunks = 128
        size = -(-n_cand // n_chunks)
        chunks = [
            {"id": c, "start": c * size, "end": min((c + 1) * size, n_cand)}
            for c in range(n_chunks)
            if c * size < n_cand
        ]
        meta = {
            "scheme": scheme,
            "cost": cost,
            "users": users,
            "candidates": n_cand,
            "words": len(WORDS),
            "rules": len(TRANSFORMS) * len(SUFFIXES),
            "per_guess": per_guess,
            "bench": benchmark(cost),
        }
        label = {"md5": "MD5 (no salt)", "md5_salted": "MD5 + salt", "bcrypt": f"bcrypt (cost {cost})"}[scheme]
        summary = (
            f"Dictionary attack on {len(USERS)} leaked {label} hashes: "
            f"{len(WORDS)} words × {meta['rules']} rules = {n_cand:,} guesses"
        )
        return Plan(meta, [chunks], n_cand * per_guess, {"passwords": passwords}, summary, ["guess"])

    def summarize(self, job: dict) -> str:
        m = job["meta"]
        label = {"md5": "MD5", "md5_salted": "MD5 + salt", "bcrypt": f"bcrypt cost {m['cost']}"}[m["scheme"]]
        return f"cracked {len(job['cracked'])}/{len(m['users'])} accounts ({label})"

    # ------------------------------------------------------------ worker side
    def run_chunk(self, meta: dict, chunk: dict, ctx: ChunkContext) -> dict:
        cracked_key = K.job_cracked(ctx.job_id)
        users = meta["users"]
        cracked = set(ctx.r.hkeys(cracked_key))
        last_sync = time.monotonic()

        def crack(user: str, password: str) -> bool:
            info = json.dumps({"password": password, "t": time.time()})
            if ctx.r.hsetnx(cracked_key, user, info):
                push_event(ctx.r, f"Cracked {user}: \"{password}\" ({meta['scheme']})", "success")
            cracked.add(user)
            return len(cracked) == len(users)

        if meta["scheme"] == "md5":  # one hash per guess, compare against everyone
            by_hash: dict[str, list[str]] = {}
            for u in users:
                by_hash.setdefault(u["hash"], []).append(u["user"])
            batch = 0
            for i in range(chunk["start"], chunk["end"]):
                guess = candidate(i)
                hit = by_hash.get(hashlib.md5(guess.encode()).hexdigest())
                if hit:
                    for user in hit:
                        if crack(user, guess):
                            ctx.progress(batch + 1)
                            return {"found": True, "all_cracked": True}
                batch += 1
                if batch == 1024:
                    if not ctx.progress(batch):
                        return {"aborted": True}
                    batch = 0
            if batch:
                ctx.progress(batch)
            return {}

        # salted schemes: every guess must be hashed separately for every user
        for i in range(chunk["start"], chunk["end"]):
            if time.monotonic() - last_sync > 0.5:  # skip users other workers already cracked
                cracked |= set(ctx.r.hkeys(cracked_key))
                last_sync = time.monotonic()
            guess = candidate(i)
            g = guess.encode()
            todo = [u for u in users if u["user"] not in cracked]
            if meta["scheme"] == "bcrypt" and len(todo) < len(users):
                ctx.progress(len(users) - len(todo))  # already-cracked users need no work
            for u in todo:
                if meta["scheme"] == "md5_salted":
                    ok = hashlib.md5(u["salt"].encode() + g).hexdigest() == u["hash"]
                else:
                    ok = bcrypt.checkpw(g, u["hash"].encode())
                if ok and crack(u["user"], guess):
                    ctx.progress(len(users))
                    return {"found": True, "all_cracked": True}
                if meta["scheme"] == "bcrypt" and not ctx.progress(1):
                    return {"aborted": True}
            if meta["scheme"] == "md5_salted" and not ctx.progress(len(users)):
                return {"aborted": True}
        return {}

"""Attack 6: breaking classical ciphers with frequency analysis (Stallings ch. 3).

Caesar shifts every letter by the same amount. Vigenère uses a repeating key
of length L, so letter i is shifted by key[i mod L]. Both leak the uneven
letter frequencies of English (E ≈ 12.7%, Z ≈ 0.07%):

  Phase 1 (key length): for each candidate length L, split the ciphertext into
      L columns and measure the index of coincidence (IoC). English text has
      IoC ≈ 0.066, uniformly random letters ≈ 0.038. The right L (and its
      multiples) makes every column look like English.
  Phase 2 (columns): each column is a plain Caesar cipher. Try all 26 shifts
      and keep the one whose letter frequencies best match English (chi-squared).

No brute force over 26^L keys is needed. The fix is the one-time pad: a truly
random key as long as the message leaves no frequencies to analyse.
"""

import json
import secrets
import string

from .. import keys as K
from .base import Attack, ChunkContext, Plan

ALPHA = string.ascii_uppercase
MAX_LEN = 16
ENGLISH_IOC = 0.0667
RANDOM_IOC = 1 / 26

# Relative letter frequencies of English text (A..Z), in percent.
ENGLISH_FREQ = [
    8.17, 1.49, 2.78, 4.25, 12.70, 2.23, 2.02, 6.09, 6.97, 0.15, 0.77, 4.03, 2.41,
    6.75, 7.51, 1.93, 0.10, 5.99, 6.33, 9.06, 2.76, 0.98, 2.36, 0.15, 1.97, 0.07,
]

# Original text written for this project.
PLAINTEXT = (
    "For three hundred years the Vigenere cipher was called the indecipherable cipher. "
    "Diplomats, generals and spies trusted it because a single letter of the message could be "
    "hidden behind many different letters of the ciphertext, which seemed to defeat the frequency "
    "counting that had broken simple substitution ciphers since the ninth century. The weakness was "
    "the repeating key. Once an analyst guesses the length of the key, the message splits into "
    "columns, and every column is nothing more than a Caesar cipher with its own small shift. "
    "Each column still carries the fingerprint of the language: the letter E appears most often, "
    "followed by T, A and O, while Q, X and Z are rare. Matching that fingerprint reveals the shift "
    "of each column, the shifts spell out the key, and the whole message falls in a moment. "
    "Charles Babbage and Friedrich Kasiski found this method in the nineteenth century. The lesson "
    "still holds today: a cipher must hide every pattern of the plaintext, not only its letters."
)


def letters(text: str) -> str:
    return "".join(c for c in text.upper() if c in ALPHA)


def shift_text(text: str, key: list[int], sign: int) -> str:
    return "".join(ALPHA[(ALPHA.index(c) + sign * key[i % len(key)]) % 26] for i, c in enumerate(text))


def ioc(text: str) -> float:
    n = len(text)
    if n < 2:
        return 0.0
    counts = [text.count(c) for c in ALPHA]
    return sum(c * (c - 1) for c in counts) / (n * (n - 1))


def chi_squared(text: str) -> float:
    n = len(text) or 1
    return sum((text.count(c) - n * f / 100) ** 2 / (n * f / 100) for c, f in zip(ALPHA, ENGLISH_FREQ, strict=False))


def pick_length(iocs: dict[int, float]) -> int:
    """Smallest key length whose IoC is within 5% of the best one.

    Multiples of the true length (2L, 3L, ...) score just as high, and a divisor
    only looks partly English, so "smallest near the max" is the right choice.
    5%, not 10%: a key that partly repeats (e.g. UGUK) makes the divisor score
    close (failed 5 in 2,000 random keys at 10%; 0 in 10,000 at 5%).
    """
    best = max(iocs.values())
    return min(length for length, value in iocs.items() if value >= 0.95 * best)


def shortest_period(key: str) -> str:
    """'ABAB' -> 'AB': both keys encrypt identically, so compare keys by their shortest period."""
    for p in range(1, len(key) + 1):
        if len(key) % p == 0 and key == key[:p] * (len(key) // p):
            return key[:p]
    return key


def recovered_key(live: dict) -> str:
    """Assemble the key from the per-column results of a job (parsed `live` hash)."""
    cols = sorted((int(k[4:]), v["letter"]) for k, v in live.items() if k.startswith("col:"))
    return shortest_period("".join(c for _, c in cols))


class ClassicalCiphers(Attack):
    name = "classical"
    title = "Frequency analysis of classical ciphers"
    unit = "letters"
    params_schema = {
        "cipher": {"type": "choice", "options": ["caesar", "vigenere", "otp"], "default": "vigenere"},
        "key_len": {"type": "int", "min": 2, "max": 10, "default": 6},
    }

    def plan(self, params: dict) -> Plan:
        p = self.clean_params(params)
        plain = letters(PLAINTEXT)
        n = len(plain)
        key_len = {"caesar": 1, "vigenere": p["key_len"], "otp": n}[p["cipher"]]
        key = [secrets.randbelow(26) for _ in range(key_len)]
        if p["cipher"] == "caesar":
            key = [1 + secrets.randbelow(25)]  # shift 0 would be no encryption
        cipher = shift_text(plain, key, +1)

        lengths = [{"id": i, "phase": 0, "length": i + 1} for i in range(MAX_LEN)]
        columns = [{"id": MAX_LEN + c, "phase": 1, "column": c} for c in range(MAX_LEN)]
        meta = {"cipher": p["cipher"], "key_len_param": p["key_len"], "ciphertext": cipher, "n": n,
                "english_freq": ENGLISH_FREQ, "english_ioc": ENGLISH_IOC, "random_ioc": RANDOM_IOC}
        key_text = "".join(ALPHA[k] for k in key)
        secret = {"key": key_text if p["cipher"] != "otp" else key_text[:24] + "…", "plaintext": plain}
        names = {"caesar": "Caesar", "vigenere": f"Vigenère (key length {key_len})", "otp": "one-time pad"}
        summary = f"Frequency analysis of a {n}-letter {names[p['cipher']]} ciphertext"
        return Plan(meta, [lengths, columns], MAX_LEN * n + n, secret, summary, ["key length", "columns"])

    def summarize(self, job: dict) -> str:
        live = job["live"]
        best = max((v["ioc"] for k, v in live.items() if k.startswith("len:")), default=0)
        if best < 0.05:
            return f"no pattern found (best IoC {best:.3f}): {job['meta']['cipher']} holds"
        return f"{job['meta']['cipher']} key {recovered_key(live)} recovered by frequency analysis"

    def run_chunk(self, meta: dict, chunk: dict, ctx: ChunkContext) -> dict:
        text = meta["ciphertext"]
        live = K.job_live(ctx.job_id)

        if chunk["phase"] == 0:  # ---- average IoC of the columns for one key length
            length = chunk["length"]
            value = sum(ioc(text[c::length]) for c in range(length)) / length
            ctx.r.hset(live, f"len:{length}", json.dumps({"ioc": round(value, 5)}))
            ctx.progress(len(text))
            return {}

        # ---- phase 2: crack one column as a Caesar cipher
        raw = ctx.r.hgetall(live)
        iocs = {int(k[4:]): json.loads(v)["ioc"] for k, v in raw.items() if k.startswith("len:")}
        length = pick_length(iocs)
        c = chunk["column"]
        if c >= length:
            return {}  # this key length has fewer columns
        column = text[c::length]
        scores = [chi_squared(shift_text(column, [s], -1)) for s in range(26)]
        best = min(range(26), key=scores.__getitem__)
        ctx.r.hset(live, f"col:{c}", json.dumps({"shift": best, "letter": ALPHA[best], "chi": round(scores[best], 1),
                                                  "length": length}))
        ctx.progress(len(column))
        return {}

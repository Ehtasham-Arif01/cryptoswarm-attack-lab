"""A deliberately vulnerable web server: the padding oracle.

It issues AES-128-CBC encrypted session tokens and accepts them back at
POST /api/session. The key never leaves this container.

mode = "vulnerable": decrypt first, then check padding. A bad padding gives a
    different answer (HTTP 500 "padding error") than a good padding with bad
    content (HTTP 400/200). That one bit of information lets an attacker decrypt
    any token without the key.

mode = "fixed" (Encrypt-then-MAC): the token carries an HMAC-SHA256 tag over
    IV || ciphertext. The tag is checked FIRST, in constant time. Any tampered
    token gets the same answer (HTTP 403) and is never decrypted, so there's no
    oracle.

For the demo only: /setup lets the coordinator pick the mode and message.
"""

import hashlib
import hmac
import json
import os

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

TAG_LEN = 32
app = FastAPI(title="Vulnerable session server (padding oracle)")
state = {"mode": "vulnerable", "key": os.urandom(16), "mac_key": os.urandom(32), "queries": 0}


class Setup(BaseModel):
    mode: str
    message: str


class Session(BaseModel):
    token: str  # hex


def tag(data: bytes) -> bytes:
    return hmac.new(state["mac_key"], data, hashlib.sha256).digest()


@app.post("/setup")
def setup(req: Setup):
    """New keys + a fresh session token for the given message."""
    state.update(mode=req.mode, key=os.urandom(16), mac_key=os.urandom(32), queries=0)
    iv = os.urandom(16)
    ct = AES.new(state["key"], AES.MODE_CBC, iv).encrypt(pad(req.message.encode(), 16))
    token = iv + ct
    if req.mode == "fixed":
        token += tag(token)
    return {"token": token.hex(), "mode": req.mode}


@app.post("/api/session")
def session(req: Session):
    state["queries"] += 1
    try:
        data = bytes.fromhex(req.token)
    except ValueError:
        return JSONResponse({"error": "invalid session"}, status_code=403)

    if state["mode"] == "fixed":
        body, mac = data[:-TAG_LEN], data[-TAG_LEN:]
        if len(data) < 32 + TAG_LEN or not hmac.compare_digest(mac, tag(body)):
            return JSONResponse({"error": "invalid session"}, status_code=403)  # same answer for everything
        data = body

    if len(data) < 32 or len(data) % 16:
        return JSONResponse({"error": "invalid session"}, status_code=403)
    iv, ct = data[:16], data[16:]
    plain = AES.new(state["key"], AES.MODE_CBC, iv).decrypt(ct)
    try:
        plain = unpad(plain, 16)
    except ValueError:
        return JSONResponse({"error": "padding error"}, status_code=500)  # <-- the leak
    try:
        user = json.loads(plain)
    except ValueError:
        return JSONResponse({"error": "bad session data"}, status_code=400)
    return {"welcome": user.get("user", "?")}


@app.get("/stats")
def stats():
    return {"mode": state["mode"], "queries": state["queries"]}

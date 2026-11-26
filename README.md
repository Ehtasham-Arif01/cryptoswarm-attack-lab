# ⬢ CryptoSwarm: Distributed Crypto Attack Lab

> Break it, then fix it. A group ("swarm") of Docker worker containers attacks weakened
> versions of real cryptography, live on a web dashboard. Each attack comes with its countermeasure.

Semester project for **Information Security (CS3002)**, based on Stallings, *Cryptography and Network Security*.

## Quick start

Requirements: Docker + Docker Compose.

```bash
docker compose up -d --build          # redis, coordinator, 1 worker, dashboard
open http://localhost:8090            # dashboard (change with DASHBOARD_PORT=xxxx)
```

Demo commands:

```bash
docker compose up -d --scale worker=8         # add workers live
docker compose up -d --scale worker-rust=4    # add fast Rust workers (brute force, ~40× a Python worker)
docker compose ps -q worker | head -1 | xargs docker kill   # kill one mid-attack: its chunk is requeued
docker compose down                           # stop everything
```

## Demo

```bash
./scripts/demo.sh --check     # build, start, run every attack once, open the dashboard
```

Then follow **[DEMO.md](DEMO.md)**, the minute-by-minute run sheet. Press **P** in the dashboard for presenter mode.

## Kubernetes (autoscaling)

```bash
./scripts/k8s-up.sh           # minikube cluster + KEDA + CryptoSwarm, prints the dashboard URL
./scripts/k8s-down.sh         # delete the cluster
```

Worker pods are scaled by **KEDA** between 0 and 8 from the coordinator's `/api/scaling` metric
(queued + in-progress chunks): no attack means no workers, and an attack brings up to 8 within seconds.
Manifests: `k8s/` (non-root pods, dropped capabilities, read-only root filesystem for workers).

## Architecture

```
 Dashboard (nginx + JS + Chart.js) ◄──WebSocket/REST──► Coordinator (FastAPI)
                                                              │
                                                            Redis
                                      job queue · shared tables · heartbeats · events
                                                              │
                                       ┌──────────┬───────────┴──────────┐
                                    worker 1   worker 2      ...      worker N
```

- **Coordinator** (`coordinator/main.py`): splits an attack into chunks, streams live state to the dashboard
  twice a second, and runs a *reaper* that requeues chunks of workers whose heartbeat stopped.
- **Workers** (`worker/worker.py`): take chunks with a reliable-queue pattern (`BLMOVE` into a
  per-worker processing list), run the attack plugin, report progress, and stop cleanly on `SIGTERM`.
- **Attack plugins** (`swarm/attacks/`): each attack implements `plan()` (coordinator side) and
  `run_chunk()` (worker side). Adding an attack doesn't change the platform.

## Attacks

| # | Attack | Break | Fix | Status |
|---|---|---|---|---|
| 1 | Brute-force DES (reduced key) | Known-plaintext key search | Bigger keys (DES-56 → AES-128) | ✅ |
| 2 | Meet-in-the-middle | Double DES ≈ single DES | 3DES | ✅ |
| 3 | Birthday attack | Truncated SHA-256 collision in ~√N | Full-length hash | ✅ |
| 4 | Password cracking | MD5 + rainbow tables | bcrypt + salt | ✅ |
| 5 | Padding oracle | Decrypt AES-CBC byte by byte | Encrypt-then-MAC | ✅ |

Full roadmap and progress: **[BRAIN.md](BRAIN.md)**.

## How the reduced DES works

DES keys are 64 bits, but the lowest bit of each byte is parity and is ignored, which leaves 56 effective bits.
We publish a random "leaked" key prefix and keep only `bits` effective bits secret, packed into the
high 7 bits of the last bytes, so every candidate is a different key. Each run picks a fresh random
key and the dashboard verifies the cracked key against it.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .                 # lint
.venv/bin/python -m pytest -q          # unit tests
python3 scripts/smoke_test.py          # every attack end-to-end against the running stack
```

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`) on every push and pull request:

1. **Test**: ruff, pytest, dashboard JS syntax check
2. **Security**: Trivy scans dependencies, secrets and Dockerfiles
3. **Build + end-to-end**: builds the images, Trivy-scans them, starts the stack and runs every attack
4. **Deliver** (main only): pushes the images to GitHub Container Registry

Any HIGH/CRITICAL vulnerability that has a fix available fails the pipeline. All containers run as a non-root user.

## Team

2-person project. See BRAIN.md for the task split.

# CryptoSwarm: Distributed Cryptographic Attack Lab

[![CI/CD](https://github.com/Ehtasham-Arif01/cryptoswarm-attack-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/Ehtasham-Arif01/cryptoswarm-attack-lab/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB)
![Rust](https://img.shields.io/badge/rust-2021-B7410E)
![Docker](https://img.shields.io/badge/docker-compose-2496ED)
![Kubernetes](https://img.shields.io/badge/kubernetes-KEDA%20autoscaling-326CE5)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**CryptoSwarm** is a distributed, fault-tolerant compute platform that breaks deliberately weakened
versions of real-world cryptography in real time, and then demonstrates the countermeasure that
defeats each attack. A coordinator splits every attack into work units, a horizontally scalable swarm of
Python and Rust workers processes them through a reliable Redis queue, and a live web dashboard
visualises progress, throughput, worker health and the final result.

Every module follows a **Break, then Fix** model: the weakness is exploited live on real code paths,
and the defence is shown with measured numbers, not just described.

Developed for the **Information Security (CS3002)** course, based on William Stallings,
*Cryptography and Network Security*.

---

## Table of contents

- [Highlights](#highlights)
- [Attack modules](#attack-modules)
- [Architecture](#architecture)
- [The Rust worker](#the-rust-worker)
- [Measured performance](#measured-performance)
- [Getting started](#getting-started)
- [Kubernetes and autoscaling](#kubernetes-and-autoscaling)
- [Security engineering and CI/CD](#security-engineering-and-cicd)
- [Project structure](#project-structure)
- [Extending the platform](#extending-the-platform)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## Highlights

- **Six complete attack modules** covering symmetric ciphers, hash functions, password storage,
  authenticated encryption and classical cryptanalysis, each paired with its countermeasure.
- **Distributed by design:** a coordinator/worker architecture over Redis with a reliable-queue
  pattern (`BLMOVE` into per-worker processing lists), heartbeats, and an automatic reaper that
  requeues the work of any worker that dies mid-task. No work is ever lost.
- **Polyglot swarm:** a Python worker that runs every attack, plus a high-performance **Rust worker**
  that speaks the identical wire protocol and is roughly **40x faster** per CPU share on DES key search.
- **Multi-phase jobs:** attacks such as meet-in-the-middle build a shared table in phase 1 and query it
  in phase 2; phases are advanced atomically with server-side Lua scripts.
- **Live dashboard:** WebSocket-driven UI showing the worker grid, chunk heatmap,
  throughput and ETA, per-attack visualisations, a cost-to-crack calculator and full attack history.
- **Cloud-native operations:** Docker Compose for local runs, Kubernetes manifests with **KEDA**
  scale-to-zero autoscaling driven by the coordinator's own queue metric.
- **Hardened supply chain:** every container runs as non-root; a GitHub Actions pipeline lints, tests,
  scans dependencies, secrets, Dockerfiles and built images with Trivy, runs every attack end-to-end,
  and publishes images to GitHub Container Registry.
- **Fully offline:** all JavaScript libraries and fonts are vendored, so the lab runs without internet access.

---

## Attack modules

| # | Attack | Break | Fix |
|---|---|---|---|
| 1 | **Brute-force DES** (reduced key space) | Distributed known-plaintext key search over the effective key bits | Larger key sizes: DES-56 to AES-128, shown with the swarm's measured speed |
| 2 | **Meet-in-the-middle** on Double DES | Shared lookup table reduces 2^2k work to about 2^(k+1) | Triple DES; cost table computed at the swarm's real rate |
| 3 | **Birthday attack** on truncated SHA-256 | Yuval's contract forgery: a fair and a fraudulent contract with the same hash tag, in about sqrt(N) work | Full-length hash output |
| 4 | **Password cracking** | Rule-based dictionary attack (86k guesses) against a leaked MD5 table; identical hashes expose shared passwords | Per-user salt and bcrypt with a tunable cost factor |
| 5 | **Padding oracle** on AES-CBC | Decrypts a token byte by byte against a live vulnerable server, never touching the key | Encrypt-then-MAC (HMAC-SHA256, constant-time verification) |
| 6 | **Classical ciphers** | Caesar and Vigenere broken by index of coincidence and chi-squared frequency analysis | One-time pad, which shows no statistical pattern |

### Implementation notes

- **Reduced DES done correctly.** DES uses 64-bit keys with one parity bit per byte, leaving 56 effective
  bits. CryptoSwarm publishes a random "leaked" key prefix and keeps only `bits` effective bits secret,
  packed into the high 7 bits of the trailing bytes, so every candidate is a distinct key. Each run draws
  a fresh random key and the result is independently verified.
- **Meet-in-the-middle.** Phase 1 stores `E(k1, P1) -> k1` in a shared Redis table (48-bit tags); phase 2
  looks up `D(k2, C1)` in batches and confirms candidates with a second plaintext/ciphertext pair.
- **Birthday attack.** 2^24 wordings each of a $100 and a $100,000 contract are hashed into a shared table
  with atomic `HSETNX`. A collision between the two sets is a forgery; the result is verified separately
  and plotted against the theoretical curve `1 - e^(-k^2 / 4N)`.
- **Padding oracle.** The oracle is a separate container whose key never leaves it. One chunk per ciphertext
  block lets the swarm decrypt all blocks in parallel. Workers calibrate first: if the server never
  reveals a padding error, it is not an oracle and the attack stops. With the fix enabled, tampered tokens
  are rejected before decryption.
- **Vigenere key length.** The chosen length is the smallest L whose average column IoC reaches 95% of the
  best score, followed by reduction of the key to its shortest period. Validated on 10,000 random keys.

---

## Architecture

| Component | Location | Responsibility |
|---|---|---|
| Coordinator | `coordinator/main.py` | REST and WebSocket API, splits attacks into chunks, streams state twice per second, reaps dead workers, records history, exposes the autoscaling metric |
| Python worker | `worker/worker.py` | Reliable-queue consumer, heartbeat, progress reporting, retry up to 3 attempts, graceful `SIGTERM` handling |
| Rust worker | `worker-rust/src/main.rs` | High-throughput DES key search on the same Redis protocol |
| Attack plugins | `swarm/attacks/` | Each attack implements `plan()` (coordinator side) and `run_chunk()` (worker side) |
| Oracle | `oracle/server.py` | Deliberately vulnerable AES-CBC service, with a switchable Encrypt-then-MAC fix |
| Dashboard | `dashboard/` | Static single-page application served by unprivileged nginx |
| History | `coordinator/history.py` | Persistent SQLite store of every job, survives restarts |

### Reliability model

1. The coordinator `LPUSH`es chunks onto `swarm:queue`.
2. A worker atomically moves a chunk into its own `swarm:processing:<id>` list with `BLMOVE`.
3. The worker publishes a heartbeat that expires after a few seconds.
4. If the heartbeat disappears, the coordinator's reaper moves the worker's in-flight chunks back to the queue.
5. Job completion is decided by a Lua script, so a result is accepted exactly once even with many concurrent workers.

### REST API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness check |
| `GET` | `/api/attacks` | Available attack plugins and their parameters |
| `GET` | `/api/state` | Current job, workers and progress |
| `POST` | `/api/jobs` | Start an attack |
| `POST` | `/api/jobs/cancel` | Cancel the running attack |
| `GET` | `/api/benchmark` | Single-core speed of DES, AES, MD5, SHA-256 and bcrypt |
| `GET` | `/api/scaling` | Pending work, consumed by the KEDA autoscaler |
| `GET` / `DELETE` | `/api/history` | Read or clear attack history |
| `WS` | `/ws` | Live state stream |

---

## The Rust worker

The Rust worker is a central part of the project: it demonstrates how much the choice of implementation
matters to an attacker, and why security margins must assume optimised, compiled attack code rather than
a reference implementation.

- **Protocol-compatible.** It implements the exact Redis protocol of the Python worker: reliable queue,
  heartbeat (reported as `lang: rust` on the dashboard), progress accounting, the same exactly-once Lua
  finish script and `SIGTERM` requeue. Python and Rust workers join the same swarm and share the same job.
- **Cooperative.** It handles brute-force DES chunks and hands chunks of other attacks back to the queue
  for Python workers.
- **Fast.** At the same 0.25 CPU share, a Rust worker searches about **330k to 470k keys per second**,
  against 7.5k to 11.5k for a Python worker: **38 to 44 times faster per worker**.
- **Minimal attack surface.** Built as a statically linked musl binary and shipped in a `scratch` image
  with no OS, shell or package manager. The final image is **1.95 MB**, compared with 257 MB for the
  Python worker, and runs as a non-root user.
- **Verified against the reference.** `cargo test` checks key derivation and ciphertexts against test
  vectors produced by the Python implementation, and runs inside every Docker build.
- **Fault tolerant.** Killing a Rust worker mid-chunk requeues its work; the key is still found and verified.

The dashboard includes a dedicated Python vs Rust comparison panel, and the cost-to-crack calculator can
project attack cost using the Rust worker's measured speed.

---

## Measured performance

Measured on a 2-core Intel i5-6300U laptop. By default each worker is capped at 0.25 CPU to emulate a
small rented cloud instance, which makes horizontal scaling visible on a single machine.

| Scenario | Result |
|---|---|
| Brute-force DES, 1 to 8 Python workers | about 3.7k to 57k keys/s (around 15x) |
| Brute-force DES, 20-bit key | ETA about 5 minutes with 1 worker; cracked in about 20 s with 8 |
| Rust vs Python worker, same CPU share | about 40x faster per worker |
| Meet-in-the-middle, 2 x 18-bit keys, 8 workers | both keys in about 12 s (about 0.5M operations instead of 68.7 billion) |
| Birthday attack, 34-bit tag, 8 workers | collision in about 4 s (about 180k hashes instead of 17 billion) |
| Passwords, unsalted MD5 | 9 of 12 accounts in 0.4 s |
| Passwords, bcrypt cost 8 | 1 of 12 accounts after 45 s; a full run would take about 3.7 h |
| Padding oracle, vulnerable server | 6 of 6 blocks in 24 to 29 s, about 120 queries per byte |
| Padding oracle, Encrypt-then-MAC | attack blocked after 24 queries |
| Vigenere key recovery | under 150 ms |
| KEDA autoscaling | 0 to 8 worker pods within 30 s of a job starting; back to 0 after it ends |

---

## Getting started

### Prerequisites

- Docker with Docker Compose v2
- Python 3.12 (only for running the tests and the end-to-end check locally)
- Optional: minikube and kubectl for the Kubernetes deployment

No local Rust toolchain is needed; the Rust worker is compiled inside Docker.

### Run with Docker Compose

```bash
git clone https://github.com/Ehtasham-Arif01/cryptoswarm-attack-lab.git
cd cryptoswarm-attack-lab
docker compose up -d --build
```

Open the dashboard at **http://localhost:8090** (override with `DASHBOARD_PORT`).

### Operating the swarm

```bash
docker compose up -d --scale worker=8                        # scale Python workers live
docker compose up -d --scale worker-rust=4                   # add Rust workers
docker kill $(docker compose ps -q worker | head -1)         # kill a worker mid-attack: its chunk is requeued
WORKER_CPUS=0 docker compose up -d                           # remove the per-worker CPU cap
docker compose down                                          # stop everything
```

### One-command setup and verification

```bash
./scripts/demo.sh            # build, start with one worker, open the dashboard
./scripts/demo.sh --check    # same, and first run every attack end-to-end
./scripts/demo.sh --stop     # stop the stack
```

Individual tabs can be opened directly, for example `http://localhost:8090/#mitm` or `#cost`.
Press **P** in the dashboard to toggle presenter mode.

---

## Kubernetes and autoscaling

```bash
./scripts/k8s-up.sh      # minikube cluster + KEDA + CryptoSwarm; prints the dashboard URL
./scripts/k8s-down.sh    # delete the cluster
```

- Kustomize manifests in `k8s/` for Redis, coordinator (with a persistent volume for history), oracle,
  dashboard (NodePort 30080) and both worker deployments.
- **KEDA** scales workers between **0 and 8** pods using a metrics-API trigger on the coordinator's
  `/api/scaling` endpoint (queued plus in-progress chunks, so busy workers are never scaled away).
  With no attack running there are no workers; starting an attack brings them up within seconds.
- Pods run as non-root with all Linux capabilities dropped; worker pods use a read-only root filesystem.

---

## Security engineering and CI/CD

The GitHub Actions pipeline (`.github/workflows/ci.yml`) runs on every push and pull request:

1. **Test:** `ruff` lint, `pytest` unit tests and a syntax check of the dashboard JavaScript.
2. **Security:** Trivy scans the repository for vulnerable dependencies, leaked secrets and Dockerfile
   misconfigurations.
3. **Build and end-to-end:** builds all five images (including `cargo test` for the Rust worker),
   scans each image with Trivy, starts the full stack with Python and Rust workers, and runs every
   attack against it with `scripts/smoke_test.py`.
4. **Deliver:** on `main`, pushes versioned images to GitHub Container Registry.

Any HIGH or CRITICAL vulnerability with an available fix fails the build. Issues found and fixed through
this pipeline include containers running as root, a transitive Starlette dependency with three HIGH CVEs
(now pinned), an oracle crash on forged tokens, and failed chunks being incorrectly marked as done.

---

## Project structure

```
cryptoswarm-attack-lab/
├── coordinator/          FastAPI coordinator, reaper, history store
├── worker/               Python worker (all attacks)
├── worker-rust/          Rust worker (high-performance DES key search)
├── oracle/               Deliberately vulnerable AES-CBC server
├── swarm/                Shared library
│   ├── attacks/          Attack plugins (one file per attack)
│   ├── toycrypto/        Reduced-key DES helpers
│   ├── keys.py           Redis key layout
│   ├── events.py         Event log and Lua scripts
│   └── benchmark.py      Primitive speed benchmarks
├── dashboard/            Web UI, nginx config, vendored Chart.js and fonts
├── k8s/                  Kubernetes manifests and KEDA scaler
├── scripts/              Demo, Kubernetes and end-to-end test scripts
├── tests/                Unit tests
├── docker-compose.yml
└── .github/workflows/    CI/CD pipeline
```

---

## Extending the platform

Attacks are plugins, so adding one does not require changes to the coordinator, workers or queue.

1. Create a module in `swarm/attacks/` that subclasses `Attack` from `swarm/attacks/base.py`.
2. Implement `plan()` to split the job into chunks (and phases, if needed), `run_chunk()` to process
   one chunk on a worker, and `summarize()` for the one-line history entry.
3. Register the class in `swarm/attacks/__init__.py`.
4. Add unit tests in `tests/` and a case in `scripts/smoke_test.py`.
5. Pair the attack with its countermeasure, so every module keeps the Break, then Fix structure.

---

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .                  # lint
.venv/bin/python -m pytest -q           # unit tests
python3 scripts/smoke_test.py           # every attack end-to-end against a running stack
```

---

## Troubleshooting

| Symptom | Resolution |
|---|---|
| Dashboard shows "reconnecting" | Check `docker compose ps`; restart with `docker compose up -d` |
| No workers on the grid | `docker compose up -d --scale worker=4` |
| An attack appears stuck | Cancel and start again; each run uses a new random key |
| Stale page after an update | Hard reload with Ctrl+Shift+R |
| Host is slow | Use fewer workers, for example `--scale worker=4` |
| Permission error on the history volume after upgrading | `docker compose run --rm --no-deps --user root --entrypoint chown coordinator -R 10001:10001 /data` |

---

## Disclaimer

CryptoSwarm is an educational project. All attacks target deliberately weakened parameters or
intentionally vulnerable services that run inside the lab itself. Do not use any part of this
project against systems you do not own or are not authorised to test.

## License

Released under the [MIT License](LICENSE).

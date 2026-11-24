# 🧠 BRAIN.md: CryptoSwarm project tracker

The single source of truth for **what we're building, what's done, and what's left**.
Update this file whenever a task is finished or the plan changes.

Legend: ✅ done · 🚧 in progress · ⬜ to do · ❌ dropped

---

## 1. Project in one paragraph

**CryptoSwarm: Distributed Crypto Attack Lab.** A live web dashboard that controls a
group of Docker/Kubernetes worker containers to break **weakened versions of real crypto**
(DES, Double DES, truncated hashes, MD5 passwords, AES-CBC via padding oracle, classical
ciphers). Every attack has a **Break → Fix** toggle that shows the countermeasure.
Course: Information Security (CS3002), Stallings ch. 1–20. Team: 2 people.

## 2. Architecture (short)

```
Dashboard (nginx + JS/Chart.js) ⇄ WebSocket/REST ⇄ Coordinator (FastAPI)
                                                       │
                                                     Redis  (job queue, shared tables, heartbeats, events)
                                                       │
                                         Workers ×N (Python / Rust), docker compose --scale
```

- **Plugin model:** every attack is a class in `swarm/attacks/` with `plan()` (coordinator side)
  and `run_chunk()` (worker side). Register it in `swarm/attacks/__init__.py`.
- **Reliable queue:** coordinator `LPUSH`es chunks → worker `BLMOVE`s into its own processing
  list → on heartbeat loss the coordinator requeues them (fault tolerance).
- **Ports:** dashboard on **http://localhost:8090** (8080/8000 are taken on the dev machine).

## 3. Tech stack

Python 3.12 (FastAPI, redis-py, pycryptodome, bcrypt) · JavaScript + Chart.js · Rust (fast worker) ·
Redis · Docker Compose · Kubernetes (kind/minikube + HPA) · GitHub Actions + Trivy · SQLite (history).

## 4. Repo

- GitHub: `Ehtasham-Arif01/cryptoswarm-attack-lab` (**private**)
- Local: `/mnt/hdd/Documents/7th-Semester/Information Security/cryptoswarm-attack-lab`

---

## 5. Roadmap and status

### Week 1: Platform + Brute force
- ✅ Repo folder + git init
- ✅ Shared package `swarm/`: Redis key layout, events, Lua "finish once" script
- ✅ Reduced-key DES helpers (`swarm/toycrypto/des.py`), with the parity bit handled correctly
- ✅ Attack plugin interface (`swarm/attacks/base.py`)
- ✅ Brute-force DES plugin (`swarm/attacks/bruteforce.py`)
- ✅ Worker: reliable queue, heartbeat, progress reporting, graceful SIGTERM
- ✅ Coordinator: REST API, WebSocket snapshots, reaper (requeue dead workers' chunks)
- ✅ Dashboard: worker grid, chunk heatmap, speed chart, ETA, DES-56/AES-128 reality check, event log
- ✅ docker-compose (redis, coordinator, worker, dashboard), Dockerfiles, nginx proxy
- ✅ Unit tests (DES key mapping, parity bits, brute force finds key): 5 passing
- ✅ End-to-end test: 22-bit key cracked + verified; `docker kill` of a worker → chunk requeued, job still finished
- ✅ README with run instructions
- ✅ Create private GitHub repo + first push
- ⬜ Visual check of the dashboard in a browser (only tested through the API so far)
- ⬜ Faster Python DES path (DES.new() is ~85% of per-key cost), or bring the Rust worker forward

### Week 2: More attacks + Break→Fix
- ⬜ Attack 2: Meet-in-the-middle on Double DES (2 phases, shared Redis table) + naive vs MITM race chart
- ⬜ Attack 3: Birthday attack on truncated SHA-256 + √N curve and probability chart
- ⬜ Attack 4: Password cracking MD5 vs bcrypt, salt vs rainbow table
- ⬜ Break→Fix toggle on each attack (bigger key / 3DES / full hash / bcrypt+salt)

### Week 3: Advanced attacks + platform features
- ⬜ Attack 5: Padding oracle on AES-CBC (vulnerable server container) → fix with Encrypt-then-MAC
- ⬜ Attack 6: Classical cipher breaker (Caesar/Vigenère frequency analysis)
- ⬜ Fault-tolerance demo polish (dead worker tile turns red, requeue shown in event log)
- ⬜ Cost-to-crack calculator (keys/s → AWS $ for DES vs AES)
- ⬜ Attack history (SQLite) + PDF report export

### Week 4: DevOps + polish
- ⬜ Kubernetes manifests + HPA autoscaling on queue depth
- ⬜ Rust worker + Python vs Rust benchmark tab
- ⬜ GitHub Actions: tests, image build, Trivy scan
- ⬜ Demo script rehearsal, slides, report

### Optional (Tier 2, if time allows)
- ⬜ Class Swarm: browser workers via QR code
- ⬜ Secure the swarm: TLS + HMAC-signed jobs/results, rogue worker demo
- ⬜ Weak PRNG attack (time-seeded tokens)
- ⬜ Prometheus + Grafana

---

## 6. Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-11-24 | Dropped the "RSA breaker" idea | Too close to a classmate's quantum/RSA-factoring project |
| 2026-11-24 | Project = CryptoSwarm (Tier 1 + Tier 3 scope) | Combines course crypto with DevOps tooling |
| 2026-11-24 | Dashboard in plain JS + Chart.js (no React build step for now) | Faster to build, no build step; can move to React later |
| 2026-11-24 | Chart.js stored in the repo (`dashboard/vendor/`) | Demo must work without classroom internet |
| 2026-11-24 | Reduced DES keys use only effective bits (not parity bits) | Parity bits are ignored by DES and would create duplicate keys |

## 7. Notes / gotchas
- **Demo laptop = the only machine:** Intel i5-6300U (2 cores / 4 threads), 8 GB RAM, 128 GB SSD.
  RAM is not a problem (worker ≈ 23 MB). CPU is the limit.
- **Uncapped workers** (WORKER_CPUS=0): 1 → 52k keys/s · 2 → 86k · 4 → 94k · 8 → 88k. Stops at 2 cores.
- **Solution (default): each worker is capped to ¼ core** (`cpus: ${WORKER_CPUS:-0.25}` in compose), like a
  small rented cloud VM. Measured: 1 → 3.7k · 2 → 9.4k · 4 → 16k · 8 → **57k keys/s (~15×)**.
  With ½ core: 1 → 9.4k · 2 → 25k · 4 → 73k · 8 → 90k.
  Scaling is slightly *better* than linear because the laptop lowers its CPU clock when the load is
  light. Mention this honestly in the report.
- **Demo timing (20 bits, ¼ core):** 1 worker shows ETA ≈ 5 min → `--scale worker=8` → cracked in ≈ 20 s.
- Optional later: teammate's laptop as extra workers over Wi-Fi (Redis password + LAN), or the Rust worker.
- The bottleneck is `DES.new()` object creation (~14 µs per key); pycryptodome's raw C API isn't
  easy to call directly (encryption goes through function pointers in its state struct).
- Default brute-force size = **20 bits** (≈1M keys ≈ 10–20 s). Slider range 12–32.
- One job runs at a time; starting a new job cancels the current one and clears the queue.
- nginx must re-resolve `coordinator` (Docker DNS `resolver 127.0.0.11`), otherwise rebuilding
  the coordinator gives 502 errors.
- Ports 8000/8080 are in use on the dev machine, so the dashboard runs on **8090**.

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
                                         Oracle (vulnerable AES-CBC server) ⇠ workers (padding oracle only)
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

Python 3.12 (FastAPI, redis-py, pycryptodome, bcrypt, requests) · Rust (des, redis) · JavaScript + Chart.js · Rust (fast worker) ·
Redis · Docker Compose · Kubernetes (kind/minikube + HPA) · GitHub Actions + Trivy + ruff · SQLite (history).

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
- ✅ Visual check of the dashboard (user checked in browser; headless Chrome screenshots)
- ⬜ Faster Python DES path (DES.new() is ~85% of per-key cost), or bring the Rust worker forward

### Week 2: More attacks + Break→Fix ✅ (done 2026-11-24)
- ✅ Platform: multi-phase attacks (later phases queued only when the previous phase is done; Lua `ADVANCE_PHASE`)
- ✅ Attack 2: Meet-in-the-middle on Double DES (`swarm/attacks/mitm.py`): phase 1 builds the shared Redis table
  E(k1,P1)→k1 (48-bit tags), phase 2 looks up D(k2,C1) in batches (HMGET) and confirms with a 2nd pair.
  Tested: 18-bit keys, 8 workers → both keys in 12 s; 19-bit, 4 workers → 34 s. Unit tests: 3.
- ✅ Dashboard tabs (brute force + MITM), flow diagram, naive-vs-MITM race chart (log scale), 3DES fix table
- ⬜ Check the race chart and speed chart fill in properly during a real-time run (headless screenshots can't show this)
- ✅ Attack 3: Birthday attack (`swarm/attacks/birthday.py`), Yuval's contract-forgery version:
  2^24 wordings of a $100 and a $100,000 contract, SHA-256 truncated to 20–38 bits, shared Redis table with
  atomic HSETNX ("L<i>"/"F<i>"), collision = fair and fraud wordings share a tag.
  Tested with 8 workers: 30 bits 2.5 s, 34 bits 4.2 s, 36 bits 23 s; collision verified independently.
  Dashboard: contracts side by side with amounts highlighted, theory curve 1−e^(−k²/4N) with the swarm's point, fix table.
- ✅ Attack 4: Password cracking (`swarm/attacks/passwords.py`): leaked table of 12 users (alice…trent),
  9 dictionary passwords (2 users share one) + 3 strong random ones. Dictionary = 122 words × 708 rules
  (case/l33t × digits/years/symbols) = 86k guesses, ordered easiest first. Schemes: MD5, MD5+salt, bcrypt (cost 4–12).
  Several answers per job → `swarm:job:<id>:cracked` hash; job ends "complete" when all chunks are done.
  Measured (8 workers): MD5 9/12 in 0.4 s · MD5+salt 9/12 in 1.2 s (12× work) · bcrypt cost 8: 1/12 after 45 s
  (78 checks/s; full run ≈ 3.7 h). 1-core benchmark: MD5 ≈ 840k/s, bcrypt cost 10 ≈ 9/s.
  Dashboard: leaked-table view with "same hash as…" warnings, cracked passwords, fix table using measured speed.
- ✅ Break→Fix on each attack: brute force ✅ (key-size table), MITM ✅ (3DES table), birthday ✅ (full-hash table), passwords ✅ (salt/bcrypt/strong-password table)

### Week 3: Advanced attacks + platform features (all 6 attacks done 2026-11-24)
- ✅ Attack 5: Padding oracle on AES-CBC (`swarm/attacks/padding_oracle.py` + `oracle/server.py` container).
  Oracle = vulnerable FastAPI server (key never leaves it): `500 padding error` vs `400`/`200`. Fixed mode =
  Encrypt-then-MAC (HMAC-SHA256 over IV‖C checked first, constant time → always `403`).
  One chunk per ciphertext block (blocks are independent) → 6 blocks decrypted in parallel.
  Worker calibrates with 4 random blocks; if none give a padding error, the server isn't an oracle → block "failed".
  Live per-block progress in `swarm:job:<id>:live`. Measured (8 workers): vulnerable 6/6 blocks in 24–29 s,
  ~11.7k queries (~122/byte); fixed: blocked after 24 queries.
  Dashboard: live block grid (right to left, padding shown as hex), decrypted token, Encrypt-then-MAC explanation.
- ✅ Attack 6: Classical ciphers (`swarm/attacks/classical.py`): Caesar / Vigenère (key length 2–10) / one-time pad
  on an original 809-letter text. Phase 1: one chunk per key length 1–16 (average IoC of the columns).
  Phase 2: one chunk per column, Caesar by chi-squared. Key length rule = smallest L with IoC ≥ **95%** of the best (90% failed 5/2,000 on partly repeating keys like UGUK, found by a flaky CI run; 95% + reducing the key to its shortest period: 0/10,000)
  (a fixed 0.055 threshold failed: half of a 10-letter key already scores 0.056). Checked: 300/300 lengths,
  500/500 full keys correct. Swarm runtime ≈ 50–150 ms. OTP: best IoC ≈ 0.040 → "no pattern".
  Dashboard: IoC-per-length chart (peaks at L, 2L), letter frequency chart (cipher vs recovered vs English), fix table.
- ⬜ Fault-tolerance demo polish (dead worker tile turns red, requeue shown in event log)
- ✅ Cost-to-crack tab (`#cost`): `GET /api/benchmark` = 1-core speeds of the workers' own Python code
  (DES, AES-128/256, MD5, SHA-256, bcrypt), cached 10 min, not cached if measured during an attack ("busy").
  DES uses the swarm's real speed per vCPU from the last brute-force job (jobs now store `workers` + `vcpus`).
  Inputs: $/vCPU-hour (default 0.045 ≈ AWS on-demand, marked approximate), fleet 1–10M vCPUs, assumed speedup
  (1×/100×/10⁴×/10⁶×). Output per target: time, $ cost, comparison (coffee … × world GDP).
  Example (1,000 vCPUs, our Python): DES 53 years / ~$21M; AES-128 ≈ 3.4×10¹⁴ × world GDP.
- ✅ Tabs can be opened from the URL: `http://localhost:8090/#mitm`, `#cost`, …
- ✅ Attack history (`coordinator/history.py`, SQLite on the `history` Docker volume, survives restarts).
  Recorder task saves every job once it stops running (and backfills jobs still in Redis). Each plugin has
  `summarize(job)` for a one-line outcome. `GET/DELETE /api/history` (clearing also empties Redis's job list,
  otherwise old jobs get re-imported). History tab (`#history`): filter, stats tiles, table.
  Scaling chart uses **1-second samples grouped by busy workers** (`s_rate:<n>`/`s_cnt:<n>` on the job hash), so it's
  correct when workers are scaled mid-attack. Measured in one run: 1 → 2.9k, 2 → 4.7k, 4 → 13.3k, 8 → 40k keys/s.
- ❌ PDF report export: dropped (user doesn't need to submit a report)

### Week 4: DevOps + polish
- ⬜ Kubernetes manifests + HPA autoscaling on queue depth
- ✅ Rust worker (`worker-rust/`, brute-force DES only). Same Redis protocol as the Python worker (reliable queue,
  heartbeat with `lang: "rust"`, progress, FINISH Lua, SIGTERM requeue); hands other attacks' chunks back to the queue.
  Static musl binary on `scratch`, non-root: **1.95 MB image** (Python worker: 257 MB). Crates: des 0.8, redis 0.27,
  serde_json, signal-hook. `cargo test` checks keys/ciphertexts against vectors from the Python code (runs in docker build).
  Compose service `worker-rust` has `scale: 0`: start with `docker compose up -d --scale worker-rust=4`.
  Measured at 0.25 CPU each: Python ≈ 7.5–11.5k keys/s, Rust ≈ 330–470k keys/s → **38–44× per worker**.
  Fault tolerance checked: killed a Rust worker mid-chunk → requeued → key found + verified.
  Dashboard: 🦀 Python vs Rust panel (Brute Force tab), orange RUST badge, cost tab option "Our Rust worker (measured)".
  CI: Rust image built (incl. cargo test), Trivy-scanned, e2e runs with 4 Python + 2 Rust workers, pushed to GHCR.
- ✅ CI/CD (`.github/workflows/ci.yml`, checked with actionlint): **test** (ruff + pytest + `node --check`),
  **security** (Trivy fs: deps, secrets, Dockerfile misconfig), **e2e** (build → Trivy image scan → stack with
  4 uncapped workers → `scripts/smoke_test.py` runs all 7 attack cases → on main push images to
  `ghcr.io/<owner>/cryptoswarm-attack-lab/{coordinator,worker,oracle}:<sha>|latest`). HIGH/CRITICAL with a fix fails the build.
  Findings fixed while setting it up: containers ran as root (AVD-DS-0002) → `USER app` (uid 10001);
  Starlette 0.41.3 had 3 HIGH CVEs (missed by the fs scan because it's a transitive dep) → FastAPI 0.165 + Starlette 1.10 pinned.
  Smoke test also found 2 bugs: oracle crashed (500) on forged tokens decrypting to non-object JSON; worker marked
  chunks that raised as *done* → now retried up to 3 times (`MAX_ATTEMPTS`).
  First green run: 2026-11-24 (~3.5 min). E2E on the runner: all 7 cases pass (padding oracle 7 s with 4 uncapped workers).
  GHCR push needed the `org.opencontainers.image.source` label (otherwise `denied`). Packages are private.
  Actions pinned to current majors (checkout v7, setup-python v7, login-action v4) to avoid the Node 20 deprecation.
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
| 2026-11-24 | Workers capped at 0.25 CPU each by default | Only machine is a 2-core i5-6300U; the cap makes scaling visible (≈15× from 1→8) |
| 2026-11-24 | MITM 3DES fix shown as a computed table, not a running attack | An attack that can't finish isn't a useful demo; the table shows the times at the swarm's real speed |
| 2026-11-24 | No Claude co-author lines in commits | User's request |
| 2026-11-24 | No PDF reports, history only | User doesn't need to submit a report |
| 2026-11-24 | Scaling measured from per-second samples, not per-run averages | New workers take seconds to boot on ¼ core; per-run averages and start-time worker counts were wrong |

## 7. Notes / gotchas
- A job's `workers`/`vcpus` = the most seen while it ran (reaper updates them every second).
- Speedup factors on the cost tab are assumptions, labelled as such. Don't present them as measured.
- No local Rust toolchain needed: everything builds in Docker (`rust:1-alpine`). First build ≈ 3 min.
- Run `python3 scripts/smoke_test.py` before a demo: it runs every attack against the live stack (~2 min).
- After switching containers to non-root, an old `history` volume needs: `docker compose run --rm --no-deps --user root --entrypoint chown coordinator -R 10001:10001 /data`.
- Padding oracle test sends ~8k requests in-process, so it only decrypts the first + last block (~35 s).
- `attack.plan()` runs in a thread in the coordinator (padding oracle makes an HTTP call to the oracle).
- Passwords: candidate generator has ~0.2% duplicates (l33t doesn't change words without a/e/i/o/s/t). Harmless.
- Start buttons send the params of the inputs listed in `data-inputs` (each input has `data-param`).
- Birthday: expected work ≈ √(πN) hashes for 2 sets; search capped at 8·√N per set. Max 38 bits (~80 MB table).
- Dashboard hides another attack's result when a different tab is open (it showed the brute-force result on the MITM tab).
- MITM max is 20 bits per key (1M table entries ≈ 80 MB in Redis). 18 bits ≈ 16 MB. Old tables are deleted when a new job starts.
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

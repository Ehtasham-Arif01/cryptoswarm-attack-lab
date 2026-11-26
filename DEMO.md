# CryptoSwarm: class demo run sheet

About **15 minutes**. Timings are measured on the demo laptop (Intel i5-6300U, 2 cores, 8 GB RAM),
with every worker limited to a quarter of a CPU. All commands run from the project folder:

```bash
cd "/mnt/hdd/Documents/7th-Semester/Information Security/cryptoswarm-attack-lab"
```

## Before class (10 minutes before)

1. Close heavy apps and stop other Docker projects if you can (more free CPU means faster cracks).
2. Start and check everything:
   ```bash
   ./scripts/demo.sh --check
   ```
   All 6 checks must pass. It ends with 1 worker running and the dashboard open.
3. The dashboard opens in **presenter mode** (text and charts scale with the screen width;
   **P** or the Presenter button toggles it, `PRESENTER=0 ./scripts/demo.sh` opens normal mode).
   Press **F11** for full screen. Test on the projector itself: it is tuned for 1366 px and wider.
4. Keep a terminal open next to the browser, already in the project folder.

## The run

| Time | Tab / action | What to say |
|---|---|---|
| 0:00 | **Brute Force DES**, header + architecture | "A coordinator splits an attack into chunks; workers in Docker containers take chunks from a Redis queue. Every attack here breaks something real, then shows the fix." |
| 0:45 | Start attack (20 bits). **ETA ≈ 5 min** with 1 worker | "One worker, a quarter of a CPU: about 3,700 keys per second." |
| 1:15 | `docker compose up -d --scale worker=8` | Workers appear; ETA drops. Cracked in **≈ 20 s**. "Same laptop, more workers: roughly 14× faster. Attackers rent workers by the hour." |
| 2:00 | During the next run: `docker kill $(docker compose ps -q worker \| head -1)` | Tile turns red, event log: *died mid-chunk, requeued*. "Losing a worker loses no work." |
| 2:45 | **Fix** table on the right | "56-bit DES at this speed: thousands of years. AES-128: far beyond the age of the universe. Every extra bit doubles the work." |
| 3:15 | `docker compose up -d --scale worker-rust=4` (during a run) | Python vs Rust panel: **≈ 40× per worker**. "Same algorithm, compiled code. Real attackers use optimized code and GPUs." |
| 4:00 | **Meet-in-the-Middle**, 18 bits, 8 workers | Cracked in **≈ 12 s**. Race chart: naive line barely moves. "Double DES gives about one extra bit, not double: that's why we use Triple DES." |
| 5:30 | **Birthday Attack**, 34 bits | Collision in **≈ 5 s**, about 180 k hashes instead of 17 billion. Show the two contracts. "Alice signs \$100, Bob shows \$100,000 with the same signature. Fix: full-length hash." |
| 7:00 | **Passwords**: MD5 | 9/12 accounts in **< 1 s**. Point at *same hash as …* rows. |
| 7:45 | Passwords: **bcrypt, cost 6** | Only plain-word passwords fall, slowly. "Slow hashing helps; random passwords are never found." |
| 8:45 | **Padding Oracle**, Vulnerable | Blocks fill right to left, decrypted in **≈ 25 s** with 8 workers. "We never had the key: one leaked bit, padding error or not." |
| 10:00 | Padding Oracle, **Fixed** | Blocked after 24 queries. "Encrypt-then-MAC: tampered tokens are rejected before decryption." |
| 11:45 | **Cost to crack** | Fleet 1,000 vCPUs: DES ≈ \$20 M with our Python, switch to *GPU cluster* or *Rust*: DES becomes cheap, AES-128 stays impossible. |
| 12:45 | **History** | Scaling chart from this session's real runs. |
| 13:15 | GitHub (browser): **Actions** tab | Pipeline: tests, Trivy scans, builds, end-to-end attacks on every push. "Trivy found root containers and 3 vulnerable library versions; the pipeline blocks them." |
| 14:00 | Optional: Kubernetes | See below. Otherwise show `k8s/keda.yaml`: "0 workers when idle, up to 8 when work arrives." |
| 14:30 | Questions | |

## Optional: Kubernetes autoscaling live

Only if there's time, and start it **before** class (it uses ~3 GB RAM):

```bash
./scripts/k8s-up.sh                                  # ~3 min if the cluster exists, ~10 min the first time
kubectl --context cryptoswarm -n cryptoswarm get pods -w   # second terminal
```

Open the printed URL, start a brute-force attack: worker pods go from **0 to 8** within seconds
and back to 0 about 20 s after the attack ends. Afterwards: `./scripts/k8s-down.sh`, then
`./scripts/demo.sh` to return to Docker Compose.

## If something goes wrong

| Problem | Fix |
|---|---|
| Dashboard says *reconnecting* | `docker compose ps`; if the coordinator is down: `docker compose up -d` |
| Dashboard stuck on *connecting* or buttons do nothing | Close the other CryptoSwarm tabs (each `demo.sh` run opens one), then hard-reload with Ctrl+Shift+R |
| No workers on the grid | `docker compose up -d --scale worker=4` |
| An attack seems stuck | Press **Cancel**, start again (each run uses a new random key) |
| Page looks old after an update | Hard reload: Ctrl+Shift+R |
| Laptop very slow | Fewer workers (`--scale worker=4`), close the browser's other tabs |
| Everything broken | `./scripts/demo.sh --stop` then `./scripts/demo.sh` |

## Numbers to remember

- 1 → 8 Python workers: ~3.7 k → ~57 k keys/s (2-core laptop, ¼ CPU each).
- Rust worker: ~40× a Python worker at the same CPU share; image 2 MB vs 257 MB.
- MITM, 2 × 18-bit keys: ~0.5 M operations instead of 68.7 billion.
- Birthday, 34-bit hash: collision after ~180 k hashes instead of 17 billion.
- Padding oracle: ~120 queries per byte, never touches the key.
- CI: 3 jobs, ~4–5 min per push; images pushed to GitHub Container Registry.

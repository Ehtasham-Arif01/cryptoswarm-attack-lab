//! CryptoSwarm worker in Rust: brute-force DES, many times faster than the Python worker.
//!
//! It speaks exactly the same Redis protocol as `worker/worker.py`, so it joins the same swarm:
//!   - reliable queue: BLMOVE swarm:queue -> swarm:processing:<id>, requeued by the coordinator if we die
//!   - heartbeat: swarm:worker:<id> (JSON, expires after 4 s), shown on the dashboard with lang = "rust"
//!   - progress: HINCRBY swarm:job:<id> tried, per-chunk progress for requeue accounting
//!   - the same Lua script to finish a job exactly once
//!
//! It only implements the brute-force attack. Chunks of other attacks are handed back to the queue
//! for the Python workers.

use std::collections::{HashMap, HashSet};
use std::env;
use std::error::Error;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use des::cipher::generic_array::GenericArray;
use des::cipher::{BlockEncrypt, KeyInit};
use des::Des;
use redis::{Connection, Script};
use serde_json::{json, Value};

type Res<T> = Result<T, Box<dyn Error>>;

const QUEUE: &str = "swarm:queue";
const EVENTS: &str = "swarm:events";
const HEARTBEAT_TTL: u64 = 4;
const FLUSH_EVERY: Duration = Duration::from_millis(250);
const CHECK_EVERY: u64 = 8192; // keys between clock checks

// Same scripts as swarm/events.py
const FINISH_JOB_LUA: &str = r"
if redis.call('HGET', KEYS[1], 'status') == 'running' then
  redis.call('HSET', KEYS[1], 'status', ARGV[1], 'result', ARGV[2], 'finished', ARGV[3])
  return 1
end
return 0
";

// ------------------------------------------------------------------ crypto (mirrors swarm/toycrypto/des.py)

/// Map a search index in [0, 2^bits) to a DES key: secret bits go into the 7 effective (high)
/// bits of the last bytes; the parity bit (LSB) is ignored by DES, so using it would create duplicates.
pub fn index_to_key(mut i: u64, bits: u32, prefix: &[u8; 8]) -> [u8; 8] {
    let mut key = *prefix;
    let nbytes = ((bits + 6) / 7) as usize;
    for j in (8 - nbytes..8).rev() {
        key[j] = (key[j] & 0x01) | (((i & 0x7F) as u8) << 1);
        i >>= 7;
    }
    key
}

pub fn des_encrypt(key: &[u8; 8], block: &[u8; 8]) -> [u8; 8] {
    let cipher = Des::new(GenericArray::from_slice(key));
    let mut b = GenericArray::clone_from_slice(block);
    cipher.encrypt_block(&mut b);
    b.into()
}

fn hex_decode<const N: usize>(s: &str) -> Res<[u8; N]> {
    if s.len() != 2 * N {
        return Err(format!("expected {} hex chars, got {}", 2 * N, s.len()).into());
    }
    let mut out = [0u8; N];
    for (k, byte) in out.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&s[2 * k..2 * k + 2], 16)?;
    }
    Ok(out)
}

fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

// ------------------------------------------------------------------ worker state

struct State {
    id: String,
    cpus: f64,
    status: &'static str,
    job: Option<String>,
    chunk: Option<i64>,
    rate: f64,
    tried_total: u64,
    chunks_done: u64,
    started: f64,
}

impl State {
    fn to_json(&self) -> String {
        json!({
            "id": self.id, "lang": "rust", "cpus": self.cpus, "status": self.status,
            "job": self.job, "chunk": self.chunk, "rate": self.rate,
            "tried_total": self.tried_total, "chunks_done": self.chunks_done, "started": self.started,
        })
        .to_string()
    }
}

struct Worker {
    id: String,
    con: Connection,
    state: Arc<Mutex<State>>,
    stop: Arc<AtomicBool>,
    finish: Script,
    warned_attacks: HashSet<String>,
}

fn key_worker(id: &str) -> String { format!("swarm:worker:{id}") }
fn key_processing(id: &str) -> String { format!("swarm:processing:{id}") }
fn key_job(j: &str) -> String { format!("swarm:job:{j}") }
fn key_done(j: &str) -> String { format!("swarm:job:{j}:done") }
fn key_inflight(j: &str) -> String { format!("swarm:job:{j}:inflight") }
fn key_progress(j: &str) -> String { format!("swarm:job:{j}:progress") }

fn push_event(con: &mut Connection, msg: &str, level: &str) -> Res<()> {
    let ev = json!({"t": now(), "msg": msg, "level": level}).to_string();
    redis::pipe().lpush(EVENTS, ev).ltrim(EVENTS, 0, 99).query::<()>(con)?;
    Ok(())
}

enum Outcome {
    Found { key: [u8; 8], index: u64 },
    Done,
    Aborted { job_still_running: bool },
}

impl Worker {
    fn set(&self, f: impl FnOnce(&mut State)) {
        if let Ok(mut s) = self.state.lock() {
            f(&mut s);
        }
    }

    /// Main loop: take a chunk, run it, report. Returns when asked to stop.
    fn run(&mut self) -> Res<()> {
        let plist = key_processing(&self.id);
        while !self.stop.load(Ordering::Relaxed) {
            let raw: Option<String> = redis::cmd("BLMOVE")
                .arg(QUEUE).arg(&plist).arg("RIGHT").arg("LEFT").arg(1)
                .query(&mut self.con)?;
            if let Some(raw) = raw {
                self.handle(&raw)?;
                self.set(|s| { s.status = "idle"; s.job = None; s.chunk = None; });
            }
        }
        Ok(())
    }

    fn handle(&mut self, raw: &str) -> Res<()> {
        let plist = key_processing(&self.id);
        let task: Value = serde_json::from_str(raw)?;
        let jid = task["job_id"].as_str().ok_or("task without job_id")?.to_string();
        let chunk = &task["chunk"];
        let cid = chunk["id"].as_i64().ok_or("chunk without id")?;
        let job: HashMap<String, String> = redis::cmd("HGETALL").arg(key_job(&jid)).query(&mut self.con)?;

        if job.get("status").map(String::as_str) != Some("running") {
            redis::cmd("LREM").arg(&plist).arg(1).arg(raw).query::<()>(&mut self.con)?;
            return Ok(());
        }
        let attack = job.get("attack").cloned().unwrap_or_default();
        if attack != "bruteforce" {
            // Not ours: give it back (front of the queue) so a Python worker picks it up.
            redis::pipe().lrem(&plist, 1, raw).rpush(QUEUE, raw).query::<()>(&mut self.con)?;
            if self.warned_attacks.insert(attack.clone()) {  // once per attack type, not per job
                push_event(&mut self.con,
                    &format!("Rust worker {} only does brute force: leaving {attack} chunks to Python workers", self.id),
                    "info")?;
            }
            thread::sleep(Duration::from_millis(300));
            return Ok(());
        }

        let meta: Value = serde_json::from_str(job.get("meta").ok_or("job without meta")?)?;
        let bits = meta["bits"].as_u64().ok_or("meta.bits")? as u32;
        let prefix: [u8; 8] = hex_decode(meta["prefix"].as_str().ok_or("meta.prefix")?)?;
        let plaintext: [u8; 8] = hex_decode(meta["plaintext"].as_str().ok_or("meta.plaintext")?)?;
        let target: [u8; 8] = hex_decode(meta["ciphertext"].as_str().ok_or("meta.ciphertext")?)?;
        let start = chunk["start"].as_u64().ok_or("chunk.start")?;
        let end = chunk["end"].as_u64().ok_or("chunk.end")?;

        redis::cmd("HSET").arg(key_inflight(&jid)).arg(cid).arg(&self.id).query::<()>(&mut self.con)?;
        self.set(|s| { s.status = "working"; s.job = Some(jid.clone()); s.chunk = Some(cid); });

        let outcome = self.search(&jid, cid, bits, &prefix, &plaintext, &target, start, end)?;
        match outcome {
            Outcome::Aborted { job_still_running: true } => self.requeue(raw, &jid, cid),
            Outcome::Aborted { .. } => {
                redis::pipe().lrem(&plist, 1, raw).hdel(key_inflight(&jid), cid).query::<()>(&mut self.con)?;
                Ok(())
            }
            other => self.finish_chunk(raw, &jid, cid, other),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn search(&mut self, jid: &str, cid: i64, bits: u32, prefix: &[u8; 8], pt: &[u8; 8], target: &[u8; 8],
              start: u64, end: u64) -> Res<Outcome> {
        let pt_block = GenericArray::clone_from_slice(pt);
        let (mut pending, mut chunk_tried) = (0u64, 0u64);
        let mut last = Instant::now();
        let mut i = start;
        while i < end {
            let key = index_to_key(i, bits, prefix);
            let mut block = pt_block;
            Des::new(GenericArray::from_slice(&key)).encrypt_block(&mut block);
            pending += 1;
            if block.as_slice() == target {
                self.flush(jid, cid, &mut pending, &mut chunk_tried, &mut last)?;
                return Ok(Outcome::Found { key, index: i });
            }
            if pending % CHECK_EVERY == 0 && last.elapsed() >= FLUSH_EVERY {
                let running = self.flush(jid, cid, &mut pending, &mut chunk_tried, &mut last)?;
                if !running || self.stop.load(Ordering::Relaxed) {
                    return Ok(Outcome::Aborted { job_still_running: running });
                }
            }
            i += 1;
        }
        self.flush(jid, cid, &mut pending, &mut chunk_tried, &mut last)?;
        Ok(Outcome::Done)
    }

    /// Report progress; returns whether the job is still running.
    fn flush(&mut self, jid: &str, cid: i64, pending: &mut u64, chunk_tried: &mut u64, last: &mut Instant)
             -> Res<bool> {
        let n = *pending;
        let dt = last.elapsed().as_secs_f64();
        *chunk_tried += n;
        let mut pipe = redis::pipe();
        if n > 0 {
            pipe.cmd("HINCRBY").arg(key_job(jid)).arg("tried").arg(n).ignore();
            pipe.cmd("HSET").arg(key_progress(jid)).arg(cid).arg(*chunk_tried).ignore();
        }
        pipe.cmd("HGET").arg(key_job(jid)).arg("status");
        let (status,): (Option<String>,) = pipe.query(&mut self.con)?;
        self.set(|s| {
            s.rate = if dt > 0.0 { n as f64 / dt } else { 0.0 };
            s.tried_total += n;
        });
        *pending = 0;
        *last = Instant::now();
        Ok(status.as_deref() == Some("running"))
    }

    /// Give an unfinished chunk back (graceful shutdown), un-counting its partial work.
    fn requeue(&mut self, raw: &str, jid: &str, cid: i64) -> Res<()> {
        let prog: Option<i64> = redis::cmd("HGET").arg(key_progress(jid)).arg(cid).query(&mut self.con)?;
        redis::pipe()
            .lrem(key_processing(&self.id), 1, raw)
            .cmd("HINCRBY").arg(key_job(jid)).arg("tried").arg(-prog.unwrap_or(0))
            .hdel(key_progress(jid), cid)
            .hdel(key_inflight(jid), cid)
            .rpush(QUEUE, raw)
            .query::<()>(&mut self.con)?;
        Ok(())
    }

    fn finish_chunk(&mut self, raw: &str, jid: &str, cid: i64, outcome: Outcome) -> Res<()> {
        let (_, _, _, _, n_done, ends): ((), (), (), (), i64, Option<String>) = redis::pipe()
            .lrem(key_processing(&self.id), 1, raw)
            .hdel(key_inflight(jid), cid)
            .hdel(key_progress(jid), cid)
            .sadd(key_done(jid), cid)
            .scard(key_done(jid))
            .hget(key_job(jid), "phase_ends")
            .query(&mut self.con)?;
        self.set(|s| s.chunks_done += 1);
        let total_chunks = ends
            .and_then(|e| serde_json::from_str::<Vec<i64>>(&e).ok())
            .and_then(|v| v.last().copied())
            .unwrap_or(i64::MAX);

        if let Outcome::Found { key, index } = outcome {
            let result = json!({"found": true, "key": hex_encode(&key), "index": index,
                                "worker": self.id, "chunk": cid}).to_string();
            let won: i64 = self.finish.key(key_job(jid)).arg("found").arg(&result).arg(now().to_string())
                .invoke(&mut self.con)?;
            if won == 1 {
                push_event(&mut self.con,
                    &format!("Worker {} (rust) cracked job {jid}: key = 0x{}", self.id, hex_encode(&key)),
                    "success")?;
            }
        } else if n_done >= total_chunks {
            let won: i64 = self.finish.key(key_job(jid)).arg("exhausted").arg("{}").arg(now().to_string())
                .invoke(&mut self.con)?;
            if won == 1 {
                push_event(&mut self.con, &format!("Job {jid} complete: all {total_chunks} chunks done"), "info")?;
            }
        }
        Ok(())
    }
}

fn heartbeat(url: String, state: Arc<Mutex<State>>, stop: Arc<AtomicBool>) {
    let mut con: Option<Connection> = None;
    while !stop.load(Ordering::Relaxed) {
        if con.is_none() {
            con = redis::Client::open(url.as_str()).and_then(|c| c.get_connection()).ok();
        }
        let (id, payload) = match state.lock() {
            Ok(mut s) => {
                if s.status == "idle" {
                    s.rate = 0.0;
                }
                (s.id.clone(), s.to_json())
            }
            Err(_) => return,
        };
        if let Some(c) = con.as_mut() {
            let ok: redis::RedisResult<()> =
                redis::cmd("SET").arg(key_worker(&id)).arg(payload).arg("EX").arg(HEARTBEAT_TTL).query(c);
            if ok.is_err() {
                con = None;
            }
        }
        thread::sleep(Duration::from_secs(1));
    }
}

/// Docker: 12-char container id. Kubernetes: pod "<deployment>-<replicaset hash>-<suffix>"
/// becomes "<deployment>-<suffix>" (unique, same rule as the Python worker).
fn worker_id() -> String {
    if let Ok(id) = env::var("WORKER_ID") {
        return id;
    }
    let host = env::var("HOSTNAME").unwrap_or_else(|_| format!("rust-{}", std::process::id()));
    let parts: Vec<&str> = host.split('-').collect();
    if parts.len() >= 3 {
        let mut keep: Vec<&str> = parts[..parts.len() - 2].to_vec();
        keep.push(parts[parts.len() - 1]);
        return keep.join("-");
    }
    host.chars().take(12).collect()
}

fn main() -> Res<()> {
    let url = env::var("REDIS_URL").unwrap_or_else(|_| "redis://localhost:6379/0".into());
    let id = worker_id();
    let cpus: f64 = env::var("WORKER_CPUS").ok().and_then(|v| v.parse().ok()).unwrap_or(0.0);

    let stop = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(signal_hook::consts::SIGTERM, Arc::clone(&stop))?;
    signal_hook::flag::register(signal_hook::consts::SIGINT, Arc::clone(&stop))?;

    let state = Arc::new(Mutex::new(State {
        id: id.clone(), cpus, status: "idle", job: None, chunk: None,
        rate: 0.0, tried_total: 0, chunks_done: 0, started: now(),
    }));
    {
        let (u, s, st) = (url.clone(), Arc::clone(&state), Arc::clone(&stop));
        thread::spawn(move || heartbeat(u, s, st));
    }
    println!("[rust worker {id}] online, redis={url}");

    while !stop.load(Ordering::Relaxed) {
        let con = match redis::Client::open(url.as_str()).and_then(|c| c.get_connection()) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[rust worker {id}] redis unavailable ({e}), retrying");
                thread::sleep(Duration::from_secs(1));
                continue;
            }
        };
        let mut w = Worker {
            id: id.clone(), con, state: Arc::clone(&state), stop: Arc::clone(&stop),
            finish: Script::new(FINISH_JOB_LUA), warned_attacks: HashSet::new(),
        };
        if let Err(e) = w.run() {
            eprintln!("[rust worker {id}] error: {e}; reconnecting");
            thread::sleep(Duration::from_secs(1));
        }
    }
    if let Ok(c) = redis::Client::open(url.as_str()).and_then(|c| c.get_connection()) {
        let mut c = c;
        let _: redis::RedisResult<()> = redis::cmd("DEL").arg(key_worker(&id)).query(&mut c);
    }
    println!("[rust worker {id}] stopped");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const PREFIX: [u8; 8] = [0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef];
    const PT: [u8; 8] = *b"CRYPTOSW";

    /// Vectors generated with the Python implementation (swarm/toycrypto/des.py):
    /// the Rust worker must produce exactly the same keys and ciphertexts.
    #[test]
    fn matches_python_reference() {
        let cases: [(u64, u32, &str, &str); 5] = [
            (12345, 20, "012345678901c173", "cc67b93a5fbc065e"),
            (0, 20, "0123456789010101", "e995dcf554ba9a50"),
            (1_048_575, 20, "01234567897fffff", "bed441e2647ca676"),
            (987_654_321, 32, "01234507adf3a363", "a44e03562f885e34"),
            (5, 12, "0123456789ab010b", "3271c7de907a51dc"),
        ];
        for (i, bits, key_hex, ct_hex) in cases {
            let key = index_to_key(i, bits, &PREFIX);
            assert_eq!(hex_encode(&key), key_hex, "key for i={i}, bits={bits}");
            assert_eq!(hex_encode(&des_encrypt(&key, &PT)), ct_hex, "ciphertext for i={i}");
        }
    }

    #[test]
    fn keys_are_unique_ignoring_parity() {
        let mut seen = HashSet::new();
        for i in 0..(1u64 << 14) {
            let k = index_to_key(i, 14, &PREFIX);
            assert!(seen.insert(k.map(|b| b & 0xFE)), "duplicate effective key at {i}");
        }
    }

    #[test]
    fn hex_roundtrip() {
        let b: [u8; 8] = hex_decode("0123456789abcdef").unwrap();
        assert_eq!(hex_encode(&b), "0123456789abcdef");
        assert!(hex_decode::<8>("abc").is_err());
    }
}

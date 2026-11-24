/* CryptoSwarm dashboard: renders the live snapshot that the coordinator streams over /ws. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------------------------------------------------------------- formatting
function fmtNum(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " G";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + " M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + " k";
  return Math.round(n).toString();
}

function sci(x) {
  const e = Math.floor(Math.log10(x));
  const m = x / 10 ** e;
  const sup = String(e).split("").map((d) => "⁰¹²³⁴⁵⁶⁷⁸⁹"[d] ?? "⁻").join("");
  return `${m.toFixed(1)} × 10${sup}`;
}

function fmtDuration(sec) {
  if (!isFinite(sec) || sec < 0) return "n/a";
  if (sec < 1) return (sec * 1000).toFixed(0) + " ms";
  if (sec < 60) return sec.toFixed(1) + " s";
  if (sec < 3600) return (sec / 60).toFixed(1) + " min";
  if (sec < 86400) return (sec / 3600).toFixed(1) + " h";
  const days = sec / 86400;
  if (days < 365) return days.toFixed(1) + " days";
  const years = days / 365.25;
  if (years < 1e6) return fmtNum(years).replace(" ", "") + " years";
  return sci(years) + " years";
}

function hue(id) {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}
const color = (id, l = 60) => `hsl(${hue(id)} 75% ${l}%)`;

// ---------------------------------------------------------------- controls
const bits = $("bits");
function updateBits() {
  const b = Number(bits.value);
  $("bits-value").textContent = b;
  $("bits-label").textContent = b;
  $("bits-space").textContent = (2 ** b).toLocaleString();
}
bits.addEventListener("input", updateBits);
updateBits();

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) alertBox(`Request failed: ${res.status}`);
  return res.json().catch(() => ({}));
}
function alertBox(msg) { console.error(msg); }

$("start").addEventListener("click", () => {
  speedChart.data.labels = [];
  speedChart.data.datasets[0].data = [];
  post("/api/jobs", { attack: "bruteforce", params: { bits: Number(bits.value) } });
});
$("cancel").addEventListener("click", () => post("/api/jobs/cancel"));

// ---------------------------------------------------------------- chart
const speedChart = new Chart($("speed-chart"), {
  type: "line",
  data: { labels: [], datasets: [{ data: [], borderColor: "#36d399", backgroundColor: "rgba(54,211,153,.12)", fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 }] },
  options: {
    animation: false,
    maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => fmtNum(c.parsed.y) + " keys/s" } } },
    scales: {
      x: { ticks: { color: "#8a96ad", maxTicksLimit: 6 }, grid: { color: "#1c2538" } },
      y: { beginAtZero: true, ticks: { color: "#8a96ad", callback: (v) => fmtNum(v) }, grid: { color: "#1c2538" } },
    },
  },
});

// ---------------------------------------------------------------- state
let smoothRate = 0;
const lastSeen = new Map(); // worker id -> {worker, t}
const DEAD_SHOW_SECONDS = 15;

function render(s) {
  smoothRate = smoothRate * 0.6 + s.swarm_rate * 0.4;
  $("top-workers").textContent = s.workers.length;
  $("top-rate").textContent = fmtNum(smoothRate) + "/s";
  $("top-queue").textContent = s.queue_depth;

  renderJob(s.job);
  renderWorkers(s);
  renderReality(s.job);
  renderEvents(s.events);

  const t = new Date(s.t * 1000).toLocaleTimeString([], { minute: "2-digit", second: "2-digit" });
  const d = speedChart.data;
  if (s.job && s.job.status === "running") {
    d.labels.push(t);
    d.datasets[0].data.push(s.swarm_rate);
    if (d.labels.length > 120) { d.labels.shift(); d.datasets[0].data.shift(); }
    speedChart.update();
  }
}

function renderJob(job) {
  if (!job) {
    $("t-status").textContent = "idle";
    $("heatmap").innerHTML = '<span class="empty">No attack yet. Press Start.</span>';
    return;
  }
  const pct = Math.min(100, (100 * job.tried) / job.total);
  $("t-status").textContent = job.status;
  $("t-status").className = "value status-" + job.status;
  $("t-progress").textContent = pct.toFixed(1) + "%";
  $("t-bar").style.width = pct + "%";
  $("t-tried").textContent = fmtNum(job.tried);
  $("t-elapsed").textContent = fmtDuration(job.elapsed);
  $("t-eta").textContent = job.status === "running" && smoothRate > 0 ? fmtDuration((job.total - job.tried) / smoothRate) : "n/a";

  const m = job.meta;
  $("job-meta").innerHTML = `
    <dt>Job</dt><dd>${esc(job.id)}</dd>
    <dt>Plaintext</dt><dd>0x${esc(m.plaintext)} ("${esc(hexToAscii(m.plaintext))}")</dd>
    <dt>Ciphertext</dt><dd>0x${esc(m.ciphertext)}</dd>
    <dt>Known key prefix</dt><dd>0x${esc(m.prefix)}</dd>
    <dt>Secret bits</dt><dd>${m.bits} (${(2 ** m.bits).toLocaleString()} keys)</dd>`;

  renderHeatmap(job);
  renderResult(job);
}

function hexToAscii(h) {
  return h.match(/../g).map((b) => String.fromCharCode(parseInt(b, 16))).join("");
}

function renderHeatmap(job) {
  const el = $("heatmap");
  const n = job.nchunks;
  el.style.gridTemplateColumns = `repeat(${Math.min(32, n)}, 1fr)`;
  if (el.childElementCount !== n || el.dataset.job !== job.id) {
    el.innerHTML = "";
    el.dataset.job = job.id;
    for (let i = 0; i < n; i++) {
      const c = document.createElement("div");
      c.className = "cell";
      el.appendChild(c);
    }
  }
  const size = job.total / n;
  const foundChunk = job.result && job.result.found ? Math.floor(job.result.index / size) : -1;
  [...el.children].forEach((c, i) => {
    const wid = job.inflight[i];
    if (i === foundChunk) {
      c.className = "cell found";
      c.style.background = "";
    } else if (wid && job.status === "running") {
      c.className = "cell inflight";
      c.style.background = color(wid);
    } else if (job.chunks_done[i]) {
      c.className = "cell done";
      c.style.background = "";
    } else {
      c.className = "cell";
      c.style.background = "";
    }
    c.title = `chunk #${i}${wid ? " · " + wid : ""}`;
  });
}

function renderResult(job) {
  const el = $("result");
  if (job.status === "found") {
    const ok = job.secret && job.secret.key === job.result.key;
    el.classList.remove("hidden");
    el.innerHTML = `
      <h2>🔓 Key cracked in ${fmtDuration(job.elapsed)}</h2>
      <p class="big">0x${esc(job.result.key)}</p>
      <p class="muted">Found by worker <b style="color:${color(job.result.worker)}">${esc(job.result.worker)}</b>
        after ${fmtNum(job.tried)} of ${fmtNum(job.total)} keys.
        ${ok ? "✅ Verified against the real secret key." : "⚠️ Does not match the stored secret."}</p>
      <p class="muted small">🔒 Fix: use enough key bits. Every extra bit doubles the work; see the reality check below.</p>`;
  } else if (job.status === "exhausted") {
    el.classList.remove("hidden");
    el.innerHTML = `<h2>Search space exhausted without a match</h2>`;
  } else {
    el.classList.add("hidden");
  }
}

function renderWorkers(s) {
  const now = Date.now() / 1000;
  const live = new Set(s.workers.map((w) => w.id));
  s.workers.forEach((w) => lastSeen.set(w.id, { worker: w, t: now }));
  const dead = [...lastSeen.values()].filter((x) => !live.has(x.worker.id) && now - x.t < DEAD_SHOW_SECONDS);
  for (const [id, x] of lastSeen) if (now - x.t >= DEAD_SHOW_SECONDS) lastSeen.delete(id);

  const grid = $("worker-grid");
  if (!s.workers.length && !dead.length) {
    grid.innerHTML = '<span class="empty">No workers online. Run: docker compose up -d --scale worker=4</span>';
    return;
  }
  const card = (w, isDead) => `
    <div class="wcard ${isDead ? "dead" : w.status}" style="--c:${color(w.id)}">
      <span class="lang">${esc(w.lang)}${w.cpus ? " · " + w.cpus + " cpu" : ""}</span>
      <div class="wid">${esc(w.id)}</div>
      <div class="wrate">${isDead ? "💀 offline" : fmtNum(w.rate) + "/s"}</div>
      <div class="muted">${isDead ? "chunk requeued" : w.status === "working" ? "chunk #" + w.chunk : "idle"}
        · ${w.chunks_done} done</div>
    </div>`;
  grid.innerHTML = s.workers.map((w) => card(w, false)).join("") + dead.map((x) => card(x.worker, true)).join("");
}

function renderReality(job) {
  const rate = smoothRate > 0 ? smoothRate : null;
  const rows = [];
  if (job && job.meta.bits) rows.push({ name: "This demo", bits: job.meta.bits, current: true });
  rows.push(
    { name: "DES (real)", bits: 56 },
    { name: "3DES (112-bit security)", bits: 112 },
    { name: "AES-128", bits: 128 },
    { name: "AES-256", bits: 256 },
  );
  const AGE_UNIVERSE_S = 1.38e10 * 365.25 * 86400;
  $("reality-body").innerHTML = rows.map((r) => {
    const secs = rate ? 2 ** (r.bits - 1) / rate : NaN;
    const danger = secs > AGE_UNIVERSE_S;
    return `<tr class="${r.current ? "current" : ""}">
      <td>${esc(r.name)}</td><td>${r.bits}</td><td>2^${r.bits}</td>
      <td class="${danger ? "danger" : ""}">${rate ? fmtDuration(secs) + (danger ? " 🌌" : "") : "start an attack"}</td></tr>`;
  }).join("");
}

function renderEvents(events) {
  $("events").innerHTML = events.map((e) => {
    const t = new Date(e.t * 1000).toLocaleTimeString();
    return `<li class="${esc(e.level)}"><span class="time">${t}</span><span>${esc(e.msg)}</span></li>`;
  }).join("");
}

// ---------------------------------------------------------------- websocket
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { $("conn-dot").className = "dot on"; $("conn-text").textContent = "live"; };
  ws.onmessage = (m) => render(JSON.parse(m.data));
  ws.onclose = () => {
    $("conn-dot").className = "dot off";
    $("conn-text").textContent = "reconnecting…";
    setTimeout(connect, 1000);
  };
}
connect();

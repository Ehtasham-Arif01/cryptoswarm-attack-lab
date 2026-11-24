/* CryptoSwarm dashboard: renders the live snapshot that the coordinator streams over /ws. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ================================================================ formatting
function fmtNum(n) {
  if (n >= 1e12) return (n / 1e12).toFixed(2) + " T";
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

const AGE_UNIVERSE_S = 1.38e10 * 365.25 * 86400;
const timeCell = (secs) => {
  if (!isFinite(secs)) return "<td>start an attack</td>";
  const danger = secs > AGE_UNIVERSE_S;
  return `<td class="${danger ? "safe" : ""}">${fmtDuration(secs)}${danger ? " 🌌" : ""}</td>`;
};

function hue(id) {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}
const color = (id, l = 60) => `hsl(${hue(id)} 75% ${l}%)`;
const hexToAscii = (h) => h.match(/../g).map((b) => String.fromCharCode(parseInt(b, 16))).join("");

// ================================================================ tabs
let activeTab = "bruteforce";
let autoTabbedJob = null;

function showTab(name) {
  activeTab = name;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
  if (lastState) render(lastState);
}
document.querySelectorAll(".tab[data-tab]").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

// ================================================================ controls
function updateSliders() {
  document.querySelectorAll(".bits-out").forEach((el) => (el.textContent = $(el.dataset.for).value));
  const bf = Number($("bf-bits").value);
  $("bf-space").textContent = (2 ** bf).toLocaleString();
  const m = Number($("mitm-bits").value);
  $("mitm-naive-bits").textContent = 2 * m;
  $("mitm-naive").textContent = fmtNum(2 ** (2 * m));
  $("mitm-ops").textContent = fmtNum(2 ** (m + 1));
  const b = Number($("bd-bits").value);
  $("bd-space").textContent = fmtNum(2 ** b);
  $("bd-expected").textContent = fmtNum(Math.sqrt(Math.PI * 2 ** b));
  $("pw-cost-sup").textContent = $("pw-cost").value;
  $("pw-cost-row").classList.toggle("hidden", $("pw-scheme").value !== "bcrypt");
  if (lastState) render(lastState);
}
document.querySelectorAll("input[type=range], select").forEach((s) => s.addEventListener("input", updateSliders));

async function post(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  if (!res.ok) console.error(`Request failed: ${res.status}`);
  return res.json().catch(() => ({}));
}
function readParams(button) {
  const params = {};
  for (const id of button.dataset.inputs.split(",")) {
    const el = $(id);
    params[el.dataset.param] = el.type === "range" ? Number(el.value) : el.value;
  }
  return params;
}
document.querySelectorAll(".start").forEach((b) =>
  b.addEventListener("click", () => post("/api/jobs", { attack: b.dataset.attack, params: readParams(b) })),
);
document.querySelectorAll(".cancel").forEach((b) => b.addEventListener("click", () => post("/api/jobs/cancel")));

// ================================================================ charts
const axis = { ticks: { color: "#8a96ad", maxTicksLimit: 6 }, grid: { color: "#1c2538" } };
const speedChart = new Chart($("speed-chart"), {
  type: "line",
  data: { labels: [], datasets: [{ data: [], borderColor: "#36d399", backgroundColor: "rgba(54,211,153,.12)", fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 }] },
  options: {
    animation: false, maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => fmtNum(c.parsed.y) + " ops/s" } } },
    scales: { x: axis, y: { ...axis, beginAtZero: true, suggestedMax: 1000, ticks: { ...axis.ticks, callback: (v) => fmtNum(v) } } },
  },
});

const raceChart = new Chart($("race-chart"), {
  type: "line",
  data: {
    labels: [],
    datasets: [
      { label: "Meet-in-the-middle", data: [], borderColor: "#36d399", pointRadius: 0, borderWidth: 2, tension: 0.2 },
      { label: "Naive brute force (same speed)", data: [], borderColor: "#f0616d", pointRadius: 0, borderWidth: 2, tension: 0.2 },
    ],
  },
  options: {
    animation: false, maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: "#e6ebf5", boxWidth: 12 } },
      tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y.toPrecision(3)}%` } },
    },
    scales: {
      x: { ...axis, title: { display: true, text: "seconds", color: "#8a96ad" } },
      y: { ...axis, type: "logarithmic", min: 1e-7, max: 100, ticks: { ...axis.ticks, callback: (v) => ([100, 1, 1e-2, 1e-4, 1e-6].includes(v) ? v + "%" : "") } },
    },
  },
});

const bdProb = (k, N) => 1 - Math.exp(-(k * k) / (4 * N));
const bdChart = new Chart($("bd-chart"), {
  type: "scatter",
  data: {
    datasets: [
      { label: "Theory", data: [], showLine: true, borderColor: "#4f8cff", pointRadius: 0, borderWidth: 2 },
      { label: "Swarm", data: [], borderColor: "#36d399", backgroundColor: "#36d399", pointRadius: 7, pointStyle: "circle" },
      { label: "Expected ≈ √(πN)", data: [], showLine: true, borderColor: "#f5b041", borderDash: [5, 4], pointRadius: 0, borderWidth: 1.5 },
    ],
  },
  options: {
    animation: false, maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: "#e6ebf5", boxWidth: 12 } },
      tooltip: { callbacks: { label: (c) => `${fmtNum(c.parsed.x)} hashes → ${(100 * c.parsed.y).toFixed(1)}%` } },
    },
    scales: {
      x: { ...axis, type: "linear", min: 0, title: { display: true, text: "hashes computed", color: "#8a96ad" }, ticks: { ...axis.ticks, callback: (v) => fmtNum(v) } },
      y: { ...axis, min: 0, max: 1, ticks: { ...axis.ticks, callback: (v) => Math.round(v * 100) + "%" } },
    },
  },
});

function renderBirthdayChart(job) {
  const bits = job && job.attack === "birthday" ? job.meta.bits : Number($("bd-bits").value);
  const N = 2 ** bits;
  const xmax = Math.max(4 * Math.sqrt(N), job && job.attack === "birthday" ? job.tried * 1.1 : 0);
  const theory = [];
  for (let i = 0; i <= 60; i++) theory.push({ x: (xmax * i) / 60, y: bdProb((xmax * i) / 60, N) });
  const exp = Math.sqrt(Math.PI * N);
  bdChart.data.datasets[0].data = theory;
  bdChart.data.datasets[2].data = [{ x: exp, y: 0 }, { x: exp, y: 1 }];
  bdChart.data.datasets[1].data = job && job.attack === "birthday" ? [{ x: job.tried, y: bdProb(job.tried, N) }] : [];
  bdChart.data.datasets[1].pointStyle = job && job.status === "found" ? "star" : "circle";
  bdChart.data.datasets[1].pointRadius = job && job.status === "found" ? 12 : 7;
  bdChart.options.scales.x.max = xmax;
  bdChart.update();
}

function renderBirthdayFix(job) {
  const rate = lastRate || NaN;
  const bits = job && job.attack === "birthday" ? job.meta.bits : Number($("bd-bits").value);
  const rows = [
    { name: "This demo (truncated)", bits, broken: true },
    { name: "MD5", bits: 128 },
    { name: "SHA-1", bits: 160 },
    { name: "SHA-256 (full)", bits: 256, fix: true },
  ];
  $("bd-fix-body").innerHTML = rows.map((r) => `<tr class="${r.broken ? "broken" : r.fix ? "fixed" : ""}">
      <td>${esc(r.name)}${r.broken ? " ⚠️" : r.fix ? " ✅" : ""}</td><td>${r.bits}</td><td>2^${r.bits / 2}</td>
      ${timeCell(Math.sqrt(Math.PI * 2 ** r.bits) / rate)}</tr>`).join("");
}

let pwCores = null; // swarm speed expressed in "full CPU cores", measured during a passwords run
const seenCracked = new Set();

function renderLeak(job) {
  if (!job || job.attack !== "passwords") return;
  const m = job.meta;
  const owners = {};
  m.users.forEach((u) => (owners[u.hash] = owners[u.hash] ? [...owners[u.hash], u.user] : [u.user]));
  const secret = job.secret ? job.secret.passwords : null;
  $("leak-body").innerHTML = m.users.map((u) => {
    const c = job.cracked[u.user];
    const key = job.id + u.user;
    const isNew = c && !seenCracked.has(key);
    if (c) seenCracked.add(key);
    const twins = owners[u.hash].filter((x) => x !== u.user);
    const shown = m.scheme === "bcrypt" ? u.hash.slice(0, 29) + "…" : (u.salt ? u.salt.slice(0, 6) + "…:" : "") + u.hash;
    const pwCell = c
      ? `<td class="cracked">🔓 ${esc(c.password)}</td>`
      : job.status === "running"
        ? '<td class="locked">🔒 ••••••</td>'
        : `<td class="locked">🔒 not in dictionary${secret ? ` <span class="muted small">(${esc(secret[u.user])})</span>` : ""}</td>`;
    return `<tr class="${isNew ? "new" : ""}"><td>${esc(u.user)}</td>
      <td class="hash mono" title="${esc(u.hash)}">${esc(shown)}${twins.length ? `<span class="dup">⚠ same hash as ${esc(twins.join(", "))}</span>` : ""}</td>${pwCell}</tr>`;
  }).join("");
}

function renderPwFix(job) {
  const isPw = job && job.attack === "passwords";
  if (isPw && job.status === "running" && smoothRate > 0) {
    const perCore = job.meta.scheme === "bcrypt" ? job.meta.bench.bcrypt : job.meta.bench.md5;
    pwCores = smoothRate / perCore;
  } else if (isPw && !pwCores && job.elapsed > 0.5) {
    const perCore = job.meta.scheme === "bcrypt" ? job.meta.bench.bcrypt : job.meta.bench.md5;
    pwCores = job.tried / job.elapsed / perCore;
  }
  if (!isPw) {
    $("pw-fix-body").innerHTML = '<tr><td colspan="3" class="muted">Run an attack to measure this swarm.</td></tr>';
    return;
  }
  const m = job.meta, C = m.candidates, U = m.users.length;
  const bcryptAt = (cost) => m.bench.bcrypt * 2 ** (m.cost - cost); // per-core speed at another cost
  const t = (work, perCore) => (pwCores ? work / (pwCores * perCore) : NaN);
  const rows = [
    { name: "MD5 + rainbow table", work: "precomputed: just a lookup", time: "<td>≈ 0 s</td>", broken: true },
    { name: "MD5, no salt", work: fmtNum(C), time: timeCell(t(C, m.bench.md5)), broken: true },
    { name: "MD5 + salt", work: fmtNum(C * U), time: timeCell(t(C * U, m.bench.md5)) },
    { name: "bcrypt, cost 8", work: fmtNum(C * U), time: timeCell(t(C * U, bcryptAt(8))) },
    { name: "bcrypt, cost 12", work: fmtNum(C * U), time: timeCell(t(C * U, bcryptAt(12))), fix: true },
    { name: "bcrypt 12 + random 12-char password", work: "2^71 per user", time: timeCell(t(2 ** 71, bcryptAt(12))), fix: true },
  ];
  $("pw-fix-body").innerHTML = rows.map((r) => `<tr class="${r.broken ? "broken" : r.fix ? "fixed" : ""}">
    <td>${esc(r.name)}${r.broken ? " ⚠️" : r.fix ? " ✅" : ""}</td><td>${r.work}</td>${r.time}</tr>`).join("");
}

const markAmount = (text, amount, cls) => esc(text).replace(esc(amount), `<mark class="${cls}">${esc(amount)}</mark>`);

let chartJob = null;
function resetCharts(jobId) {
  chartJob = jobId;
  for (const ch of [speedChart, raceChart]) {
    ch.data.labels = [];
    ch.data.datasets.forEach((d) => (d.data = []));
  }
}

// ================================================================ per-attack UI
const ATTACK_UI = {
  bruteforce: {
    triedLabel: "Keys tried",
    meta: (m) => `
      <dt>Plaintext</dt><dd>0x${esc(m.plaintext)} ("${esc(hexToAscii(m.plaintext))}")</dd>
      <dt>Ciphertext</dt><dd>0x${esc(m.ciphertext)}</dd>
      <dt>Known key prefix</dt><dd>0x${esc(m.prefix)}</dd>
      <dt>Secret bits</dt><dd>${m.bits} (${(2 ** m.bits).toLocaleString()} keys)</dd>`,
    result: (job) => {
      const ok = job.secret && job.secret.key === job.result.key;
      return `
        <h2>🔓 Key cracked in ${fmtDuration(job.elapsed)}</h2>
        <p class="big">0x${esc(job.result.key)}</p>
        <p class="muted">Found by worker ${workerTag(job.result.worker)} after ${fmtNum(job.tried)} of ${fmtNum(job.total)} keys.
          ${ok ? "✅ Verified against the real secret key." : "⚠️ Does not match the stored secret."}</p>
        <p class="muted small">🔒 Fix: use enough key bits. Every extra bit doubles the work; see the table on the right.</p>`;
    },
  },
  mitm: {
    triedLabel: "Operations",
    meta: (m) => `
      <dt>Pair 1</dt><dd>P1 = "${esc(hexToAscii(m.p1))}" → C1 = 0x${esc(m.c1)}</dd>
      <dt>Pair 2</dt><dd>P2 = "${esc(hexToAscii(m.p2))}" → C2 = 0x${esc(m.c2)}</dd>
      <dt>k1 prefix</dt><dd>0x${esc(m.prefix1)}</dd>
      <dt>k2 prefix</dt><dd>0x${esc(m.prefix2)}</dd>
      <dt>Key bits</dt><dd>2 × ${m.bits} = ${2 * m.bits} (naive keyspace ${fmtNum(2 ** (2 * m.bits))})</dd>`,
    result: (job) => {
      const s = job.secret, r = job.result;
      const ok = s && s.k1 === r.k1 && s.k2 === r.k2;
      const naive = 2 ** (2 * job.meta.bits);
      return `
        <h2>🔓 Both Double-DES keys recovered in ${fmtDuration(job.elapsed)}</h2>
        <p class="big">k1 = 0x${esc(r.k1)} &nbsp; k2 = 0x${esc(r.k2)}</p>
        <p class="muted">Found by worker ${workerTag(r.worker)} with ${fmtNum(job.tried)} operations instead of
          up to ${fmtNum(naive)} naive tries, <b>${fmtNum(naive / job.tried)}× less work</b>.
          Confirmed with the second plaintext/ciphertext pair.
          ${ok ? "✅ Matches the real secret keys." : "⚠️ Does not match the stored secret."}</p>
        <p class="muted small">🔒 Fix: Triple DES. A third encryption pushes meet-in-the-middle back to 2<sup>2n</sup> work.</p>`;
    },
  },
  birthday: {
    triedLabel: "Hashes computed",
    meta: (m) => `
      <dt>Hash</dt><dd>SHA-256 truncated to ${m.bits} bits (N = ${fmtNum(2 ** m.bits)})</dd>
      <dt>Fair contract</dt><dd>pays Bob ${esc(m.legit_amount)}, 2^24 wordings</dd>
      <dt>Fraud contract</dt><dd>pays Bob ${esc(m.fraud_amount)}, 2^24 wordings</dd>
      <dt>Expected work</dt><dd>≈ ${fmtNum(m.expected_hashes)} hashes (√(πN)) vs N = ${fmtNum(2 ** m.bits)}</dd>`,
    result: (job) => {
      const r = job.result, m = job.meta;
      return `
        <h2>🔓 Collision found in ${fmtDuration(job.elapsed)}: two contracts, one hash</h2>
        <div class="contracts">
          <div class="contract ok"><div class="label">Fair contract #${r.legit_index} (Alice signs this)</div>
            <p>${markAmount(r.legit, m.legit_amount, "ok")}</p></div>
          <div class="contract bad"><div class="label">Fraud contract #${r.fraud_index} (Bob shows this in court)</div>
            <p>${markAmount(r.fraud, m.fraud_amount, "bad")}</p></div>
        </div>
        <p class="big">hash<sub>${m.bits}</sub> = 0x${esc(r.hash)} for both</p>
        <p class="muted">Found by worker ${workerTag(r.worker)} after ${fmtNum(job.tried)} hashes, about
          ${(job.tried / m.expected_hashes).toFixed(1)}× the birthday estimate and
          <b>${fmtNum(2 ** m.bits / job.tried)}× fewer</b> than the ${fmtNum(2 ** m.bits)} a naive search would need.</p>
        <p class="muted small">🔒 Fix: use the full 256-bit SHA-256. Its birthday bound is 2<sup>128</sup>.</p>`;
    },
  },
  passwords: {
    triedLabel: "Hashes computed",
    statusLabel: { exhausted: "complete" },
    extra: (job) => ["Accounts cracked", `${Object.keys(job.cracked).length} / ${job.meta.users.length}`],
    meta: (m) => `
      <dt>Storage</dt><dd>${esc(schemeLabel(m))}</dd>
      <dt>Dictionary</dt><dd>${m.words} words × ${m.rules} rules = ${fmtNum(m.candidates)} guesses</dd>
      <dt>Work</dt><dd>${m.per_guess === 1 ? "1 hash per guess (checks all users at once)" : `${m.per_guess} hashes per guess (one per user)`}</dd>
      <dt>1-core speed</dt><dd>MD5 ${fmtNum(m.bench.md5)}/s · bcrypt(cost ${m.cost}) ${m.bench.bcrypt}/s</dd>`,
    result: (job) => passwordsSummary(job),
    complete: (job) => passwordsSummary(job),
  },
};

const schemeLabel = (m) => ({ md5: "MD5, no salt", md5_salted: "MD5 + per-user salt", bcrypt: `bcrypt, cost ${m.cost}` }[m.scheme]);

function passwordsSummary(job) {
  const m = job.meta, n = Object.keys(job.cracked).length;
  const lesson = {
    md5: "Unsalted fast hashes fall in seconds, and two users with the same password had the same hash. Precomputed (rainbow) tables would make it even faster.",
    md5_salted: `Salt removed the shared hashes and made precomputed tables useless, but the work only grew ${m.users.length}× and MD5 is still fast.`,
    bcrypt: "bcrypt makes every guess expensive. Only the most obvious passwords fall in reasonable time.",
  }[m.scheme];
  return `
    <h2>${n ? "🔓" : "🔒"} Cracked ${n} of ${m.users.length} accounts in ${fmtDuration(job.elapsed)} (${esc(schemeLabel(m))})</h2>
    <p class="muted">${lesson} The random passwords were never in the dictionary, so they survived even MD5.</p>
    <p class="muted small">🔒 Fix: salt + a slow hash (bcrypt / scrypt / Argon2) <b>and</b> long random passwords.</p>`;
}

const workerTag = (wid) => `<b style="color:${color(wid)}">${esc(wid)}</b>`;

// ================================================================ render
let smoothRate = 0;
let lastRate = 0; // last real speed, so the fix tables still work when idle
let lastState = null;
const lastSeen = new Map();
const DEAD_SHOW_SECONDS = 15;

function render(s) {
  lastState = s;
  const job = s.job;
  smoothRate = smoothRate * 0.6 + s.swarm_rate * 0.4;
  if (job && job.status === "running" && smoothRate > 100) lastRate = smoothRate;
  else if (!lastRate && job && job.elapsed > 0.5) lastRate = job.tried / job.elapsed; // page opened after a run

  $("top-workers").textContent = s.workers.length;
  $("top-rate").textContent = fmtNum(smoothRate) + "/s";
  $("top-queue").textContent = s.queue_depth;

  if (job && autoTabbedJob !== job.id) { // jump to the tab of a newly started job
    autoTabbedJob = job.id;
    if (job.attack !== activeTab && ATTACK_UI[job.attack]) return showTab(job.attack);
  }
  if (job && chartJob !== job.id) resetCharts(job.id);

  renderJob(job);
  renderWorkers(s);
  renderBruteforceFix(job);
  renderMitmFix(job);
  renderBirthdayFix(job);
  renderLeak(job);
  renderPwFix(job);
  if (activeTab === "birthday") renderBirthdayChart(job);
  renderEvents(s.events);
  if (job && job.status === "running") updateCharts(job, s);
}

function renderJob(job) {
  const ui = job && ATTACK_UI[job.attack];
  if (!job || !ui) {
    $("t-status").textContent = "idle";
    $("heatmap").innerHTML = '<span class="empty">No attack yet. Press Start.</span>';
    return;
  }
  const pct = Math.min(100, (100 * job.tried) / job.total);
  $("t-status").textContent = (ui.statusLabel && ui.statusLabel[job.status]) || job.status;
  $("t-status").className = "value status-" + job.status;
  $("t-progress").textContent = pct.toFixed(1) + "%";
  $("t-bar").style.width = pct + "%";
  $("t-tried-label").textContent = ui.triedLabel;
  $("t-tried").textContent = fmtNum(job.tried);
  $("t-elapsed").textContent = fmtDuration(job.elapsed);
  $("t-eta").textContent = job.status === "running" && smoothRate > 0 ? fmtDuration((job.total - job.tried) / smoothRate) : "n/a";

  const multiPhase = job.phase_ends.length > 1;
  const [extraLabel, extraValue] = ui.extra
    ? ui.extra(job)
    : multiPhase
      ? ["Phase · table size", `${job.phase + 1}/${job.phase_ends.length} · ${fmtNum(job.table_size)}`]
      : ["Chunks", `${job.chunks_done.filter(Boolean).length}/${job.nchunks}`];
  $("t-extra-label").textContent = extraLabel;
  $("t-extra").textContent = extraValue;

  const otherTab = job.attack !== activeTab;
  $("job-meta").innerHTML = otherTab
    ? `<dt>Last job</dt><dd>${esc(job.title)} (${esc(job.status)}). Open its tab to see details, or press Start here.</dd>`
    : `<dt>Job</dt><dd>${esc(job.id)} · ${esc(job.title)}</dd>` + ui.meta(job.meta);

  renderHeatmap(job);
  const el = $("result");
  if (otherTab) {
    el.classList.add("hidden");
  } else if (job.status === "found") {
    el.classList.remove("hidden");
    el.innerHTML = ui.result(job);
  } else if (job.status === "exhausted") {
    el.classList.remove("hidden");
    el.innerHTML = ui.complete ? ui.complete(job) : "<h2>Search space exhausted without a match</h2>";
  } else {
    el.classList.add("hidden");
  }
}

function renderHeatmap(job) {
  const el = $("heatmap");
  const n = job.nchunks;
  const ends = job.phase_ends;
  el.style.gridTemplateColumns = `repeat(${Math.min(32, n)}, 1fr)`;
  if (el.childElementCount !== n || el.dataset.job !== job.id) {
    el.innerHTML = "";
    el.dataset.job = job.id;
    for (let i = 0; i < n; i++) el.appendChild(document.createElement("div"));
  }
  $("heat-legend").textContent = ends.length > 1
    ? `(${job.phase_names.map((p, i) => `${i ? "lower" : "upper"} half = ${p}`).join(", ")}; each cell is one chunk)`
    : "(each cell is one chunk, colored by the worker searching it)";
  const foundChunk = job.result && job.result.found ? job.result.chunk : -1;
  [...el.children].forEach((c, i) => {
    const wid = job.inflight[i];
    const phaseCls = i >= ends[0] ? " p2" : "";
    if (i === foundChunk) {
      c.className = "cell found";
      c.style.background = "";
    } else if (wid && job.status === "running") {
      c.className = "cell inflight";
      c.style.background = color(wid);
    } else {
      c.className = "cell" + phaseCls + (job.chunks_done[i] ? " done" : "");
      c.style.background = "";
    }
    c.title = `chunk #${i} · ${job.phase_names[ends.findIndex((e) => i < e)]}${wid ? " · " + wid : ""}`;
  });
}

function updateCharts(job, s) {
  const t = job.elapsed.toFixed(0);
  speedChart.data.labels.push(new Date(s.t * 1000).toLocaleTimeString([], { minute: "2-digit", second: "2-digit" }));
  speedChart.data.datasets[0].data.push(s.swarm_rate);
  if (speedChart.data.labels.length > 120) {
    speedChart.data.labels.shift();
    speedChart.data.datasets[0].data.shift();
  }
  speedChart.update();

  if (job.attack === "mitm") {
    const naiveSpace = 2 ** (2 * job.meta.bits);
    raceChart.data.labels.push(t);
    raceChart.data.datasets[0].data.push(Math.max(1e-7, (100 * job.tried) / job.total));
    raceChart.data.datasets[1].data.push(Math.max(1e-7, (100 * job.tried) / naiveSpace));
    raceChart.update();
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
      <div class="wid">${esc(w.id)}</div>
      <div class="lang">${esc(w.lang)}${w.cpus ? " · " + w.cpus + " cpu" : ""}</div>
      <div class="wrate">${isDead ? "💀 offline" : fmtNum(w.rate) + "/s"}</div>
      <div class="muted">${isDead ? "chunk requeued" : w.status === "working" ? "chunk #" + w.chunk : "idle"} · ${w.chunks_done} done</div>
    </div>`;
  grid.innerHTML = s.workers.map((w) => card(w, false)).join("") + dead.map((x) => card(x.worker, true)).join("");
}

function renderBruteforceFix(job) {
  const rate = lastRate || NaN;
  const bits = job && job.attack === "bruteforce" ? job.meta.bits : Number($("bf-bits").value);
  const rows = [
    { name: "This demo", bits, current: true },
    { name: "DES (real)", bits: 56 },
    { name: "3DES (112-bit security)", bits: 112 },
    { name: "AES-128", bits: 128 },
    { name: "AES-256", bits: 256 },
  ];
  $("reality-body").innerHTML = rows.map((r) => `<tr class="${r.current ? "current" : ""}">
      <td>${esc(r.name)}</td><td>${r.bits}</td><td>2^${r.bits}</td>${timeCell(2 ** (r.bits - 1) / rate)}</tr>`).join("");
}

function renderMitmFix(job) {
  const rate = lastRate || NaN;
  const n = job && job.attack === "mitm" ? job.meta.bits : Number($("mitm-bits").value);
  const rows = [
    { name: `Single DES (${n}-bit key)`, attack: "brute force", exp: n },
    { name: `Double DES (2 × ${n})`, attack: "naive brute force", exp: 2 * n },
    { name: `Double DES (2 × ${n})`, attack: "meet-in-the-middle", exp: n + 1, broken: true },
    { name: `Triple DES (3 × ${n})`, attack: "meet-in-the-middle", exp: 2 * n, fix: true },
    { name: "Real 3DES (3 × 56)", attack: "meet-in-the-middle", exp: 112, fix: true },
  ];
  $("mitm-fix-body").innerHTML = rows.map((r) => `<tr class="${r.broken ? "broken" : r.fix ? "fixed" : ""}">
      <td>${esc(r.name)}${r.broken ? " ⚠️" : r.fix ? " ✅" : ""}</td><td>${r.attack}</td><td>2^${r.exp}</td>${timeCell(2 ** r.exp / rate)}</tr>`).join("");
}

function renderEvents(events) {
  $("events").innerHTML = events.map((e) => {
    const t = new Date(e.t * 1000).toLocaleTimeString();
    return `<li class="${esc(e.level)}"><span class="time">${t}</span><span>${esc(e.msg)}</span></li>`;
  }).join("");
}

// ================================================================ websocket
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
updateSliders();
connect();

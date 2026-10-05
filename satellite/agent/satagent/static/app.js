"use strict";
// Satellite web UI. Plain JS: status polling, meters, server-sent events.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
let status = null, meters = null, tab = "overview";

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.json !== undefined) { o.method = o.method || "POST"; o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || r.statusText);
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 2600);
}
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function debounce(fn, ms) { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; }
const fmt = (v, d = 1, unit = "") => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toFixed(d) + unit;

// ------------------------------------------------------------------ tabs
$$("#tabs button").forEach(b => b.onclick = () => {
  tab = b.dataset.tab;
  $$("#tabs button").forEach(x => x.classList.toggle("on", x === b));
  $$(".tab").forEach(s => s.classList.toggle("on", s.id === "tab-" + tab));
  if (tab === "wakeword") loadWakewords();
  if (tab === "system") loadLogs();
  history.replaceState(null, "", "#" + tab);
});
if (location.hash) { const b = $(`#tabs button[data-tab="${location.hash.slice(1)}"]`); if (b) b.click(); }

// ------------------------------------------------------------------ status
async function refresh() {
  try { status = await api("/api/status"); } catch (e) { return; }
  const s = status;
  $("#dev-name").textContent = s.device.name;
  $("#dev-room").textContent = (s.device.room || "no room") + " · " + s.device.device_id;
  document.title = s.device.name + " · satellite";
  setState(s.state, s.muted);
  const lp = $("#pill-link"); lp.textContent = s.link.status; lp.className = "pill " + s.link.status;
  if (document.activeElement !== $("#vol")) { $("#vol").value = s.settings.volume; $("#vol-val").textContent = Math.round(s.settings.volume); }
  const mb = $("#btn-mute"); mb.setAttribute("aria-pressed", s.muted); mb.textContent = s.muted ? "Mic muted" : "Mic on";
  if (s.transcript) $("#last-transcript").textContent = s.transcript;
  if (s.reply) $("#last-reply").textContent = s.reply;
  fillForms();
  $("#link-kv").innerHTML = kv({
    Status: s.link.status, "Connected to": s.link.url || "—", Detail: s.link.detail || "—", "Round trip": fmt(s.link.rtt_ms, 1, " ms"),
    "Clock offset": fmt(s.link.clock_offset_ms, 1, " ms"), "Device ID": s.device.device_id,
    Engine: s.engine.connected ? "running " + (s.engine.version || "") : "not running",
  });
  const sy = s.system;
  $("#sys-kv").innerHTML = kv({
    Version: s.version, "Engine": s.engine.version || "—", "CPU temperature": fmt(sy.cpu_temp, 1, " °C"),
    "CPU (all cores)": fmt(sy.cpu, 0, " %"), Load: fmt(sy.load, 2), Memory: sy.mem ? `${sy.mem.available_mb} / ${sy.mem.total_mb} MB free` : "—",
    Uptime: sy.uptime ? fmtDur(sy.uptime) : "—", Button: s.button.error || s.button.device || "—",
    Turns: s.counters.turns, Wakes: `${s.counters.wakes} (${s.counters.rejected} rejected by the server)`,
  });
  if (!$("#d-name").dataset.filled) {
    $("#d-name").value = s.device.name; $("#d-room").value = s.device.room; $("#d-server").value = s.device.server_url;
    $("#d-name").dataset.filled = 1;
  }
  renderPlugins();
}
function kv(o) { return Object.entries(o).map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join(""); }
function fmtDur(s) { const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + "d " : "") + h + "h " + m + "m"; }
function setState(state, muted) {
  const p = $("#pill-state");
  p.textContent = muted && state === "idle" ? "muted" : state;
  p.className = "pill " + (muted && state === "idle" ? "muted" : state);
}

// ------------------------------------------------------------------ events
function logEvent(text) {
  const li = document.createElement("li");
  li.innerHTML = `<time>${new Date().toLocaleTimeString()}</time><span>${esc(text)}</span>`;
  const ol = $("#events"); ol.prepend(li);
  while (ol.children.length > 60) ol.lastChild.remove();
}
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = e => {
    const m = JSON.parse(e.data);
    switch (m.event) {
      case "state": setState(m.state, m.muted); logEvent(`${m.state}${m.reason ? " (" + m.reason + ")" : ""}`); break;
      case "wake": logEvent(`wake: ${m.source}${m.keyword ? " '" + m.keyword + "'" : ""} from ${Math.round(m.doa)}°, score ${fmt(m.score, 2)}`); break;
      case "transcript": $("#last-transcript").textContent = m.text; logEvent(`heard: ${m.text}`); break;
      case "reply": $("#last-reply").textContent = m.text; break;
      case "link": logEvent(`server ${m.status}`); refresh(); break;
      case "kws_loaded": logEvent(`wake words loaded: ${m.templates} samples, suggested threshold ${fmt(m.suggested_threshold, 3)}`); if (tab === "wakeword") loadWakewords(); break;
      case "error": logEvent(`error: ${m.reason}`); break;
      case "volume": logEvent(`volume ${m.volume}`); break;
      case "mute": logEvent(m.muted ? "microphones muted" : "microphones on"); break;
      case "alarm": logEvent(m.on ? "alarm ringing" : "alarm stopped"); break;
    }
  };
}

// ------------------------------------------------------------------ meters
async function pollMeters() {
  if (tab === "overview" || tab === "wakeword") {
    try { meters = await api("/api/meters"); drawMeters(); } catch (e) { }
  }
  setTimeout(pollMeters, tab === "overview" || tab === "wakeword" ? 100 : 1000);
}
function bar(label, frac, value, cls = "", line = null) {
  return `<div class="bar"><span>${label}</span><div class="meter"><i class="${cls}" style="width:${clamp(frac, 0, 1) * 100}%"></i>${line !== null ? `<em style="left:${clamp(line, 0, 1) * 100}%"></em>` : ""}</div><b>${value}</b></div>`;
}
function drawMeters() {
  const m = meters; if (!m || !m.mic_db) return;
  const lv = db => (db + 70) / 60;
  $("#mic-bars").innerHTML = m.mic_db.map((d, i) => bar("MIC" + (i + 1), lv(d), fmt(d, 0, " dB"), d > -30 ? "hot" : "")).join("")
    + bar("Loopback", lv(m.ref_db), fmt(m.ref_db, 0, " dB")) + bar("Output", lv(m.out_db), fmt(m.out_db, 0, " dB"));
  $("#speech-stats").innerHTML = [
    ["Voice", m.speech ? "yes" : "no", m.speech], ["VAD", fmt(m.vad, 2)], ["SNR", fmt(m.snr_db, 0, " dB")],
    ["Noise", fmt(m.noise_db, 0, " dB")], ["Echo cancel.", fmt(m.erle_db, 0, " dB")], ["AGC", fmt(m.agc_db, 0, " dB")],
  ].map(([k, v, on]) => `<div class="stat ${on ? "on" : ""}"><span>${k}</span><b>${v}</b></div>`).join("");
  const thr = status ? status.settings.kws_threshold : 0.42;
  const cost = c => clamp(1 - c / 0.9, 0, 1);
  if (status && status.keywords.length) {
    $("#kws-bars").innerHTML = (m.kws || []).map((c, i) => bar(`${Math.round(i * 360 / m.kws.length)}°`, cost(c), c >= 9 ? "—" : fmt(c, 2), c < thr ? "hot" : "", cost(thr))).join("");
  } else {
    $("#kws-bars").innerHTML = `<p class="hint">No wake word yet: enroll one in the Wake word tab. The button and the Talk button work meanwhile.</p>`;
  }
  const cpu = m.cpu || {};
  $("#cpu-stats").innerHTML = [
    ["Frame", fmt(cpu.frame_us / 1000, 1, " ms")], ["Budget", fmt(cpu.budget_us / 1000, 0, " ms")],
    ["Worst", fmt(cpu.max_us / 1000, 1, " ms")], ["AEC", fmt(cpu.aec_us / 1000, 1, " ms")],
    ["Beams", fmt(cpu.bf_us / 1000, 1, " ms")], ["Xruns", `${m.xruns.capture}/${m.xruns.playback}`],
  ].map(([k, v]) => `<div class="stat"><span>${k}</span><b>${v}</b></div>`).join("");
  const best = Math.min(...(m.kws || [9]));
  $("#kw-live").style.width = (best >= 9 ? 0 : cost(best) * 100) + "%";
  $("#kw-live").className = best < thr ? "hot" : "";
  $("#kw-line").style.left = cost(thr) * 100 + "%";
  drawCompass();
}

const MICS = [[-23.2, 40.1], [23.2, 40.1], [46.3, 0], [23.2, -40.1], [-23.2, -40.1], [-46.3, 0]];
function drawCompass() {
  const cv = $("#compass"), g = cv.getContext("2d"), m = meters;
  const W = cv.width, c = W / 2, R = W * 0.42;
  const css = getComputedStyle(document.documentElement);
  const col = n => css.getPropertyValue(n).trim();
  g.clearRect(0, 0, W, W);
  g.strokeStyle = col("--line"); g.lineWidth = 1;
  for (const r of [0.33, 0.66, 1]) { g.beginPath(); g.arc(c, c, R * r, 0, 2 * Math.PI); g.stroke(); }
  g.fillStyle = col("--muted"); g.font = "12px system-ui"; g.textAlign = "center"; g.textBaseline = "middle";
  [["0°", 0], ["90°", 90], ["180°", 180], ["270°", 270]].forEach(([t, a]) => {
    const r = a * Math.PI / 180; g.fillText(t, c + Math.sin(r) * (R + 16), c - Math.cos(r) * (R + 16));
  });
  const pt = (az, rr) => { const r = az * Math.PI / 180; return [c + Math.sin(r) * rr, c - Math.cos(r) * rr]; };
  // static interferers
  if (m.static) {
    const n = m.static.length;
    m.static.forEach((v, i) => {
      if (v < 0.15) return;
      g.fillStyle = `rgba(217,119,6,${Math.min(0.55, v * 0.6)})`;
      g.beginPath(); g.moveTo(c, c);
      g.arc(c, c, R, (i * 360 / n - 90 - 180 / n) * Math.PI / 180, (i * 360 / n - 90 + 180 / n) * Math.PI / 180); g.fill();
    });
  }
  // SRP polar
  if (m.srp) {
    const n = m.srp.length, mx = Math.max(...m.srp, 1e-6), mn = Math.min(...m.srp);
    g.beginPath();
    m.srp.forEach((v, i) => { const [x, y] = pt(i * 360 / n, R * (0.12 + 0.88 * (v - mn) / (mx - mn + 1e-6))); i ? g.lineTo(x, y) : g.moveTo(x, y); });
    g.closePath(); g.fillStyle = col("--accent-2") + "33"; g.fill(); g.strokeStyle = col("--accent-2"); g.lineWidth = 1.5; g.stroke();
  }
  // board + mics
  g.strokeStyle = col("--muted"); g.lineWidth = 1;
  g.beginPath(); g.arc(c, c, R * 0.22, 0, 2 * Math.PI); g.stroke();
  MICS.forEach(([x, y], i) => {
    const px = c + x / 46.3 * R * 0.22, py = c - y / 46.3 * R * 0.22;
    const lv = m.mic_db ? clamp((m.mic_db[i] + 70) / 50, 0, 1) : 0;
    g.fillStyle = `hsl(${140 - lv * 140} 80% ${35 + lv * 25}%)`;
    g.beginPath(); g.arc(px, py, 4.5, 0, 2 * Math.PI); g.fill();
  });
  // tracker
  if (m.track_valid) {
    const [x, y] = pt(m.track, R * 0.98);
    g.strokeStyle = col("--accent"); g.lineWidth = 3; g.lineCap = "round";
    g.beginPath(); g.moveTo(...pt(m.track, R * 0.3)); g.lineTo(x, y); g.stroke();
    g.fillStyle = col("--accent"); g.beginPath(); g.arc(x, y, m.speech ? 9 : 6, 0, 2 * Math.PI); g.fill();
  }
}

// ------------------------------------------------------------------ controls
$("#btn-talk").onclick = () => api("/api/action", { json: { action: "wake" } }).catch(e => toast(e.message));
$("#btn-stop").onclick = () => api("/api/action", { json: { action: "stop" } });
$("#btn-mute").onclick = () => setEngine({ mic_muted: !(status && status.muted) }).then(refresh);
$("#vol").oninput = e => { $("#vol-val").textContent = e.target.value; volSave(+e.target.value); };
const volSave = debounce(v => setEngine({ volume: v }), 150);
async function setEngine(changes) {
  try { const s = await api("/api/engine", { json: changes }); if (status) status.settings = s; return s; }
  catch (e) { toast(e.message); }
}

// ------------------------------------------------------------------ settings forms
const AUDIO_SCHEMA = [
  ["Output", [
    ["volume", "Volume", 0, 100, 1, ""], ["speaker_muted", "Speaker muted", "bool"],
    ["listen_duck_db", "Media level while listening to you", -60, 0, 1, " dB"],
    ["duck_db", "Media level while answering", -40, 0, 1, " dB"],
    ["tts_gain_db", "Voice gain", -20, 12, 0.5, " dB"], ["media_gain_db", "Media gain", -20, 12, 0.5, " dB"],
    ["alarm_gain_db", "Alarm gain", -20, 12, 0.5, " dB"], ["earcon_gain_db", "Earcon gain", -20, 12, 0.5, " dB"],
    ["earcons", "Earcons", "bool"], ["earcon_wake", "Chime on wake", "bool"], ["earcon_end", "Chime at end of speech", "bool"],
  ]],
  ["Speaker voicing", [
    ["eq_low_db", "Bass (150 Hz)", -12, 12, 0.5, " dB"], ["eq_mid_db", "Mid (1.2 kHz)", -12, 12, 0.5, " dB"],
    ["eq_high_db", "Treble (6 kHz)", -12, 12, 0.5, " dB"], ["hpf_hz", "High-pass", 0, 300, 5, " Hz"],
    ["drc_threshold_db", "Compressor threshold", -40, 0, 1, " dB"], ["drc_ratio", "Compressor ratio", 1, 10, 0.5, ":1"],
    ["limiter_db", "Limiter ceiling", -12, 0, 0.5, " dB"],
  ]],
  ["Capture", [
    ["capture_gain_db", "Input gain", -20, 30, 0.5, " dB"],
    ["aec_enabled", "Echo cancellation", "bool"], ["aec_tail_ms", "Echo tail", 32, 256, 16, " ms"],
    ["res_enabled", "Residual echo suppression", "bool"], ["res_strength", "Residual echo strength", 0.5, 3, 0.1, "×"],
    ["res_floor_db", "Residual echo max reduction", -30, 0, 1, " dB"],
    ["bf_mode", "Beamformer", ["mvdr", "superdirective", "das", "mic1"]],
    ["ns_enabled", "Noise suppression", "bool"], ["ns_floor_db", "Max noise reduction", -30, 0, 1, " dB"],
    ["agc_enabled", "Speech AGC", "bool"], ["agc_target_db", "AGC target", -35, -6, 1, " dB"],
    ["agc_max_gain_db", "AGC max gain", 0, 36, 1, " dB"],
  ]],
  ["Turn taking", [
    ["vad_threshold_db", "Voice detection threshold", 2, 20, 0.5, " dB"],
    ["eos_silence_ms", "End of speech after", 300, 3000, 50, " ms"],
    ["listen_timeout_ms", "Give up if silent for", 2000, 15000, 500, " ms"],
    ["max_listen_ms", "Longest request", 4000, 40000, 1000, " ms"],
    ["preroll_ms", "Pre-roll sent with a wake", 500, 2400, 100, " ms"],
  ]],
];
const KW_SCHEMA = [["", [
  ["kws_enabled", "Wake word detection", "bool"], ["kws_barge_in", "Interrupt answers with the wake word", "bool"],
  ["kws_min_matches", "Samples that must agree (0 = auto)", 0, 5, 1, ""],
  ["kws_neg_margin", "Margin over learned false wakes", 0, 0.2, 0.005, ""],
  ["kws_playback_margin", "More lenient while music plays", -0.1, 0.2, 0.005, ""],
  ["kws_tts_threshold", "Strictest threshold while it speaks", 0.3, 0.6, 0.005, ""],
]]];

function renderForm(container, schema) {
  container.innerHTML = schema.map(([title, fields]) => `<div class="card">${title ? `<h2>${title}</h2>` : ""}${fields.map(([k, label, a, b, step, unit]) => {
    if (a === "bool") return `<label class="switch"><input type="checkbox" data-k="${k}"><span>${label}</span></label>`;
    if (Array.isArray(a)) return `<label class="field"><span>${label}</span><select data-k="${k}">${a.map(o => `<option>${o}</option>`).join("")}</select></label>`;
    return `<label class="field"><span>${label} <b data-v="${k}" data-unit="${unit}"></b></span><input type="range" data-k="${k}" min="${a}" max="${b}" step="${step}"></label>`;
  }).join("")}</div>`).join("");
  $$("[data-k]", container).forEach(el => {
    const k = el.dataset.k;
    const save = debounce(v => setEngine({ [k]: v }), 250);
    el.addEventListener(el.type === "range" ? "input" : "change", () => {
      const v = el.type === "checkbox" ? el.checked : el.type === "range" ? +el.value : el.value;
      const out = $(`[data-v="${k}"]`, container); if (out) out.textContent = v + out.dataset.unit;
      save(v);
    });
  });
}
function fillForms() {
  if (!status) return;
  $$("[data-k]").forEach(el => {
    if (el === document.activeElement) return;
    const v = status.settings[el.dataset.k]; if (v === undefined) return;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
    const out = $(`[data-v="${el.dataset.k}"]`); if (out) out.textContent = v + out.dataset.unit;
  });
  if (document.activeElement !== $("#kw-thr")) { $("#kw-thr").value = status.settings.kws_threshold; $("#kw-thr-val").textContent = Number(status.settings.kws_threshold).toFixed(3); }
  $("#kw-auto").checked = !!status.wakeword.auto_threshold;
  $("#d-button").checked = true;
}
renderForm($("#audio-forms"), AUDIO_SCHEMA);
renderForm($("#kw-forms"), KW_SCHEMA);
$("#kw-forms").firstElementChild.classList.remove("card");

// ------------------------------------------------------------------ wake word
async function loadWakewords() {
  const d = await api("/api/wakewords");
  const loaded = Object.fromEntries((d.loaded || []).map(k => [k.name, k.templates]));
  $("#kw-list").innerHTML = d.words.length ? d.words.map(w => `
    <div class="kw"><div class="kw-head"><b>${esc(w.name.replace(/_/g, " "))}</b>
      <span class="hint">${loaded[w.name] || 0} of ${w.samples.length} samples usable${w.negatives ? ` · learned from ${w.negatives} false wakes <button class="small" data-clearneg="${esc(w.name)}">forget</button>` : ""}</span>
      <button class="danger" data-delkw="${esc(w.name)}">Delete</button></div>
      <div class="samples">${w.samples.map((s, i) => `<span class="sample">#${i + 1}
        <button data-play="${esc(w.name)}/${esc(s)}" aria-label="Play">▶</button>
        <button data-del="${esc(w.name)}/${esc(s)}" aria-label="Delete">✕</button></span>`).join("")}</div>
      <div class="row"><button data-more="${esc(w.name)}">Add a sample</button></div></div>`).join("")
    : `<p class="hint">No wake word enrolled yet.</p>`;
}
$("#kw-list").onclick = async e => {
  const t = e.target;
  if (t.dataset.play) new Audio("/api/wakewords/" + t.dataset.play).play();
  else if (t.dataset.del) { await api("/api/wakewords/" + t.dataset.del, { method: "DELETE" }); loadWakewords(); }
  else if (t.dataset.delkw && confirm("Delete this wake word and its samples?")) { await api("/api/wakewords/" + t.dataset.delkw, { method: "DELETE" }); loadWakewords(); }
  else if (t.dataset.clearneg) { await api(`/api/wakewords/${t.dataset.clearneg}/clear_negatives`, { method: "POST" }); loadWakewords(); }
  else if (t.dataset.more) { $("#kw-name").value = t.dataset.more.replace(/_/g, " "); record(); }
};
async function record() {
  const name = $("#kw-name").value.trim();
  if (!name) { toast("Type the wake word first"); return; }
  const st = $("#record-status"), btn = $("#btn-record");
  btn.disabled = true; st.className = "record-status rec"; st.textContent = "Get ready… say it right after the beep";
  try {
    const r = api(`/api/wakewords/${encodeURIComponent(name)}/record`, { json: { ms: 2500 } });
    setTimeout(() => { st.textContent = "● Recording — say it now"; }, 450);
    const res = await r;
    st.className = "record-status";
    if (res.sample) st.textContent = "Good sample" + (res.report.cost != null && res.report.cost < 9 ? ` (distance to the others ${res.report.cost.toFixed(2)})` : "") + ". Record 3 to 5 in total.";
    else { st.className = "record-status rec"; st.textContent = "Not kept: " + res.report.advice; }
    setTimeout(loadWakewords, 600);
  } catch (e) { st.className = "record-status"; st.textContent = e.message; }
  btn.disabled = false;
}
$("#btn-record").onclick = record;
$("#kw-thr").oninput = e => { $("#kw-thr-val").textContent = (+e.target.value).toFixed(3); thrSave(+e.target.value); };
const thrSave = debounce(v => { api("/api/wakeword_settings", { json: { auto_threshold: false } }); setEngine({ kws_threshold: v }); }, 250);
$("#kw-auto").onchange = e => api("/api/wakeword_settings", { json: { auto_threshold: e.target.checked } }).then(refresh);

// ------------------------------------------------------------------ plugins
window.SatPlugins = window.SatPlugins || {};
const mounted = new Set();
function renderPlugins() {
  if (!status) return;
  const loaded = Object.fromEntries(status.plugins.map(p => [p.name, p]));
  $("#plugin-list").innerHTML = status.available_plugins.map(n => {
    const p = loaded[n];
    return `<div class="plugin"><div><b>${esc(p ? p.title : n)}</b><p>${esc(p ? p.description : "not running")}</p></div>
      <label class="switch"><input type="checkbox" data-plugin="${esc(n)}" ${p ? "checked" : ""}><span>${p ? "on" : "off"}</span></label></div>`;
  }).join("") || `<p class="hint">No plugins installed.</p>`;
  for (const p of status.plugins) {
    if (!p.panel || mounted.has(p.name)) continue;
    mounted.add(p.name);
    const card = document.createElement("div"); card.className = "card"; card.innerHTML = `<h2>${esc(p.title)}</h2><div></div>`;
    $("#plugin-panels").append(card);
    const s = document.createElement("script"); s.src = `/plugins/${p.name}/static/panel.js`;
    s.onload = () => window.SatPlugins[p.name] && window.SatPlugins[p.name].mount(card.lastChild, {
      api: (act, data) => api(`/api/plugins/${p.name}/api/${act}`, data ? { json: data } : {}),
      settings: () => api(`/api/plugins/${p.name}/settings`),
      save: data => api(`/api/plugins/${p.name}/settings`, { json: data }),
      toast,
    });
    document.body.append(s);
  }
}
$("#plugin-list").onchange = async e => {
  const n = e.target.dataset.plugin; if (!n) return;
  await api("/api/action", { json: { action: "plugin_enable", name: n, enabled: e.target.checked } });
  toast("Saved: restart the agent (System tab) to apply");
};

// ------------------------------------------------------------------ device / system
$("#btn-save-device").onclick = async () => {
  try {
    await api("/api/device", { json: { name: $("#d-name").value, room: $("#d-room").value, server_url: $("#d-server").value } });
    $("#d-saved").textContent = "Saved, reconnecting…"; refresh();
  } catch (e) { toast(e.message); }
};
$("#btn-save-access").onclick = async () => {
  const data = { button_enabled: $("#d-button").checked };
  if ($("#d-password").value || confirm("Remove the web UI password?")) data.password = $("#d-password").value;
  await api("/api/device", { json: data }); toast("Saved");
};
$("#btn-reconnect").onclick = () => api("/api/action", { json: { action: "reconnect" } });
$("#btn-new-token").onclick = () => confirm("Generate a new device token? The server will have to approve this device again.") && api("/api/action", { json: { action: "new_token" } });
$("#btn-restart-engine").onclick = () => api("/api/action", { json: { action: "restart_engine" } }).then(() => toast("Engine restarting")).catch(e => toast(e.message));
$("#btn-restart-agent").onclick = () => api("/api/action", { json: { action: "restart_agent" } }).catch(() => toast("Agent restarting"));
$("#btn-reset-noise").onclick = () => api("/api/action", { json: { action: "reset_noise" } }).then(() => toast("Noise model reset"));
$$("[data-earcon]").forEach(b => b.onclick = () => api("/api/action", { json: { action: "earcon", name: b.dataset.earcon } }));
async function loadLogs() { $("#logs").textContent = await api("/api/logs?unit=" + $("#log-unit").value).catch(e => e.message); }
$("#btn-logs").onclick = loadLogs; $("#log-unit").onchange = loadLogs;

// ------------------------------------------------------------------ monitor (listen in the browser)
let monitor = null;
$("#btn-monitor").onclick = async () => {
  const b = $("#btn-monitor");
  if (monitor) { monitor.abort(); monitor = null; b.textContent = "Listen to the beam"; return; }
  monitor = new AbortController(); b.textContent = "Stop listening";
  const ac = new AudioContext({ sampleRate: 16000 }); let t = ac.currentTime + 0.25, rest = new Uint8Array(0);
  try {
    const r = await fetch("/api/monitor", { signal: monitor.signal }), rd = r.body.getReader();
    for (;;) {
      const { value, done } = await rd.read(); if (done) break;
      const buf = new Uint8Array(rest.length + value.length); buf.set(rest); buf.set(value, rest.length);
      const n = buf.length >> 1; rest = buf.slice(n * 2);
      const pcm = new Int16Array(buf.buffer, 0, n), ab = ac.createBuffer(1, n, 16000), ch = ab.getChannelData(0);
      for (let i = 0; i < n; i++) ch[i] = pcm[i] / 32768;
      const src = ac.createBufferSource(); src.buffer = ab; src.connect(ac.destination);
      t = Math.max(t, ac.currentTime + 0.05); src.start(t); t += n / 16000;
    }
  } catch (e) { }
  ac.close(); monitor = null; b.textContent = "Listen to the beam";
};

refresh(); setInterval(refresh, 2500); pollMeters(); connectEvents();

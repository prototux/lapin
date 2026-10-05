"use strict";
// Assistant server admin UI (vanilla JS).

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtMs = ms => ms == null ? "—" : ms >= 1000 ? (ms / 1000).toFixed(2) + " s" : Math.round(ms) + " ms";
const ago = ts => { const s = Date.now() / 1000 - ts; return s < 60 ? Math.round(s) + "s ago" : s < 3600 ? Math.round(s / 60) + " min ago" : s < 86400 ? Math.round(s / 3600) + " h ago" : new Date(ts * 1000).toLocaleDateString(); };
const md = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|\s)\*(\S.*?)\*/g, "$1<i>$2</i>")
  .replace(/`([^`]+)`/g, "<code>$1</code>").replace(/^\s*[-•] /gm, "• ");
const plain = s => String(s ?? "").replace(/\*\*|__|`/g, "").replace(/^\s*[-•] /gm, "");
const clock = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
let page = "dashboard", ov = null;

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.json !== undefined) { o.method = o.method || "POST"; o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const d = ct.includes("json") ? await r.json() : ct.includes("audio") ? await r.blob() : await r.text();
  if (!r.ok) throw new Error((d && d.error) || r.statusText);
  return d;
}
function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.add("show"); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 2800); }

// ------------------------------------------------------------------ navigation
function go(p) {
  page = p;
  $$("#nav a").forEach(a => a.classList.toggle("on", a.dataset.page === p));
  $$(".page").forEach(s => s.classList.toggle("on", s.id === "p-" + p));
  $("#side").classList.remove("open");
  history.replaceState(null, "", "#" + p);
  ({ dashboard: loadOverview, devices: loadDevices, conversations: () => loadTurns(true), talk: loadChat, timers: loadTimers,
     media: loadMedia, people: loadPeople, skills: loadSkills, settings: loadSettings, logs: loadLogs,
     integrations: loadChannels })[p]?.();
}
$$("#nav a").forEach(a => a.onclick = () => go(a.dataset.page));
$("#menu").onclick = () => $("#side").classList.toggle("open");

// ------------------------------------------------------------------ dashboard
const STATE_LABEL = { idle: "idle", listening: "listening", thinking: "thinking", speaking: "speaking" };
function devRing(d) { return !d.online ? "offline" : d.mic_muted && d.state === "idle" ? "muted" : (d.state || "idle"); }
async function loadOverview() {
  ov = await api("/api/overview").catch(() => null);
  if (!ov) return;
  $("#srv-name").textContent = ov.name;
  $("#srv-sub").textContent = "v" + ov.version + " · up " + Math.round(ov.uptime / 3600) + " h";
  const H = ov.health, hcard = (name, h, extra) => `<div class="card health"><i class="dot ${h && h.ok ? "ok" : h && h.ok === false ? "bad" : ""}"></i><div><b>${name}</b><small>${h && h.ok ? fmtMs(h.ms) + (extra || "") : h && h.error ? esc(h.error.slice(0, 60)) : "checking…"}</small></div></div>`;
  $("#health").innerHTML = hcard("Speech recognition", H.stt) + hcard("Speech synthesis", H.tts) + hcard("Language model", H.llm)
    + `<div class="card health"><i class="dot ${Object.values(H.channels).some(s => String(s).startsWith("error")) ? "bad" : "ok"}"></i><div><b>Messaging</b><small>${esc(Object.entries(H.channels).filter(([n, s]) => s !== "disabled").map(([n, s]) => n + ": " + s).join(" · ") || "web chat only")}</small></div></div>`;
  renderDashDevices();
  const pend = ov.devices.filter(d => d.status === "pending").length;
  $("#pending-badge").hidden = !pend; $("#pending-badge").textContent = pend;
  $("#dash-turns").innerHTML = ov.turns.map(turnRow).join("") || `<p class="hint">Nothing yet. Say the wake word, or use Talk & chat.</p>`;
  const nt = ov.next_timer;
  $("#dash-now").innerHTML = kv({
    "Active turns": ov.active.map(a => `${a.device}: ${a.status}`).join(", ") || "none",
    "Timers running": ov.timers + (nt ? ` (next: ${nt.label || nt.kind} in ${fmtLeft(nt.due - Date.now() / 1000)})` : ""),
    "Playing": Object.entries(ov.media).map(([d, m]) => `${m.station} on ${d}`).join(", ") || "nothing",
    "Device gateway": `ws://${location.hostname}:${ov.gateway_port}/v1/device`,
  });
  $("#gw-url").textContent = `ws://${location.hostname}:${ov.gateway_port}/v1/device`;
}
function renderDashDevices() {
  const devs = (ov && ov.devices || []).filter(d => d.status === "approved");
  $("#dash-devices").innerHTML = devs.map(d => `<div class="dev"><i class="ring ${devRing(d)}"></i><div style="flex:1"><b>${esc(d.name)}</b>
    <small>${esc(d.room || "no room")} · ${d.online ? STATE_LABEL[d.state] || d.state : "offline"}${d.online && d.volume != null ? " · vol " + d.volume : ""}</small></div>
    ${d.telemetry && d.telemetry.system ? `<small>${Math.round(d.telemetry.system.cpu_temp || 0)}°C</small>` : ""}</div>`).join("")
    || `<p class="hint">No satellite yet.</p>`;
}
function kv(o) { return Object.entries(o).map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join(""); }
function fmtLeft(s) { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); }
function latency(t) {
  const m = t.trace && t.trace.marks || {};
  const end = m.eot ?? null, first = m.playback_started ?? m.tts_first_audio ?? m.first_sentence ?? null;
  return end != null && first != null ? first - end : first;
}
function turnRow(t) {
  const lat = latency(t);
  const st = { done: "ok", rejected: "warn", arbitration: "", no_speech: "", empty: "", barge_in: "warn", error: "bad", cancelled: "warn" }[t.status] ?? "";
  return `<div class="turn" data-id="${t.id}"><div class="q">${esc(t.transcript || "(" + t.status + ")")}</div>
    <div class="meta"><span class="pill ${st}">${esc(t.status)}</span>${lat != null ? `<span class="pill acc">${fmtMs(lat)}</span>` : ""}</div>
    <div class="a">${esc(plain(t.reply))}</div>
    <div class="meta" style="grid-column:1/-1;justify-content:flex-start">${esc(deviceName(t.device_id) || t.channel)} · ${ago(t.ts)} · ${esc(t.route || "")}</div></div>`;
}
function deviceName(id) { const d = ov && ov.devices.find(x => x.id === id); return d ? d.name : id; }

// live events
const feed = [];
function addFeed(text) {
  const li = document.createElement("li");
  li.innerHTML = `<time>${new Date().toLocaleTimeString()}</time><span>${text}</span>`;
  $("#feed").prepend(li);
  while ($("#feed").children.length > 80) $("#feed").lastChild.remove();
}
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = e => {
    const m = JSON.parse(e.data);
    switch (m.topic) {
      case "device_state":
        if (ov) { const d = ov.devices.find(x => x.id === m.device); if (d) { Object.assign(d, { state: m.state ?? d.state, mic_muted: m.muted ?? d.mic_muted, volume: m.volume ?? d.volume }); renderDashDevices(); } }
        break;
      case "device":
        addFeed(`<b>${esc(m.device.name || m.device.id)}</b> ${esc(m.event)}`);
        loadOverview(); if (page === "devices") loadDevices();
        if (m.event === "registered" && m.device.status === "pending") toast("New device waiting for approval");
        break;
      case "turn":
        if (m.partial) addFeed(`<b>${esc(m.device)}</b> … ${esc(m.partial)}`);
        else if (m.transcript && m.status === "thinking") addFeed(`<b>${esc(m.device)}</b> heard: “${esc(m.transcript)}”`);
        else if (m.trace) { addFeed(`<b>${esc(m.device)}</b> ${m.reply ? "→ " + esc(m.reply) : esc(m.status)}`); if (page === "dashboard") loadOverview(); if (page === "conversations") loadTurns(true); }
        else if (m.status === "listening" && m.source) addFeed(`<b>${esc(m.device)}</b> wake (${esc(m.source)})`);
        break;
      case "notify": addFeed(`🔔 ${esc(m.text)}`); if (page === "timers") loadTimers(); break;
      case "announce": addFeed(`📢 “${esc(m.text)}” on ${esc(m.devices.join(", "))}`); break;
      case "media": addFeed(`🎵 ${esc(m.event)} ${esc(m.station || "")} ${esc((m.devices || [m.device]).join(", "))}`); if (page === "media") loadMedia(); break;
      case "chat": if (page === "talk") loadChat(); break;
      case "log": if (page === "logs") appendLog(m); break;
      case "channel_unknown": toast(`${m.channel}: message from someone not linked yet (${m.name || m.sender}) — see Integrations`); $("#unknown-badge").hidden = false; $("#unknown-badge").textContent = "!"; if (page === "integrations") loadChannels(); break;
    }
  };
}

// ------------------------------------------------------------------ devices
async function loadDevices() {
  const [devs, users] = await Promise.all([api("/api/devices"), api("/api/users")]);
  const pend = devs.filter(d => d.status === "pending");
  $("#pending").innerHTML = pend.map(d => `<div class="card" style="border-color:var(--warn)"><h2>New device waiting</h2>
    <div class="row"><b>${esc(d.name)}</b><code>${esc(d.id)}</code><span class="hint">${esc(d.kind || "satellite")}${d.user ? ", " + esc(d.user) + "'s" : ""}, from ${esc(d.info.ip || "?")}, version ${esc(d.info.version || "?")}</span></div>
    <div class="row"><button class="primary" data-approve="${d.id}">Approve</button><button class="danger" data-reject="${d.id}">Reject</button></div></div>`).join("");
  const userOpts = sel => users.users.map(u => `<option ${u.name === sel ? "selected" : ""}>${esc(u.name)}</option>`).join("");
  $("#device-list").innerHTML = devs.filter(d => d.status !== "pending").map(d => {
    const t = d.telemetry || {}, sy = t.system || {}, en = t.engine || {};
    return `<div class="card" data-dev="${d.id}">
      <h2><span><i class="dot ${d.online ? "ok" : ""}" style="display:inline-block;margin-right:8px"></i>${esc(d.name)}</span>
      <span class="pill ${d.status === "blocked" ? "bad" : d.online ? "ok" : ""}">${d.status === "blocked" ? "blocked" : d.online ? esc(d.state || "online") : "offline"}</span></h2>
      <div class="grid two" style="margin:0;gap:10px">
        <label class="field"><span>Name</span><input data-f="name" value="${esc(d.name)}"></label>
        <label class="field"><span>Room</span><input data-f="room" value="${esc(d.room || "")}" placeholder="kitchen"></label>
      </div>
      <label class="field"><span>${["phone", "desktop", "tv"].includes(d.kind) ? "Owner (personal device)" : "Speaks as user"}</span><select data-f="user"><option value="">household</option>${userOpts(d.user)}</select></label>
      ${d.online ? `<label class="field"><span>Volume <b>${d.volume ?? "?"}</b></span><input type="range" min="0" max="100" value="${d.volume ?? 50}" data-vol style="width:100%"></label>
      <div class="row"><input data-say placeholder="Say something on it…"><button data-act="say">Say</button></div>
      <div class="row"><button data-act="listen">Start listening</button><button data-act="earcon">Chime</button><button data-act="ring">Test alarm</button>
        <button data-act="led" data-pattern="success">LED</button><button data-act="mute">${d.mic_muted ? "Unmute mic" : "Mute mic"}</button><button data-act="stop">Stop</button></div>` : ""}
      <div class="kv" style="margin-top:10px">${kv(Object.fromEntries(Object.entries({
        "Device id": d.id, Type: d.kind || "satellite", Version: d.version || (d.info || {}).version, Address: d.remote || (d.info || {}).ip,
        "Its own actions": (d.tools || []).join(", ").replace(/_/g, " ") || null,
        "Wake words": d.kind && d.kind !== "satellite" ? null : (d.wake_words || []).join(", ").replace(/_/g, " ") || "none enrolled",
        "Board": sy.cpu_temp != null ? `${Math.round(sy.cpu_temp)}°C, CPU ${sy.cpu ?? "?"}%, ${sy.mem ? sy.mem.available_mb + " MB free" : ""}` : null,
        "Audio engine": en.cpu ? `${(en.cpu.frame_us / 1000).toFixed(1)} of ${(en.cpu.budget_us / 1000).toFixed(0)} ms per frame, echo cancel. ${Math.round(en.erle_db || 0)} dB` : null,
        "Link": t.rtt_ms != null ? `${t.rtt_ms.toFixed(1)} ms round trip` : null,
        "Last seen": d.online ? "now" : d.last_seen ? ago(d.last_seen) : "never",
      }).filter(([k, v]) => v)))}</div>
      ${d.id.startsWith("korvo-") ? `<div class="row"><button data-devlog>Device log</button>${d.online ? `<button data-ota>Update firmware</button>` : ""}<span class="hint" data-ota-state></span></div>
      <pre class="logs" data-logbox hidden style="max-height:320px;overflow:auto;font-size:12px"></pre>` : ""}
      <div class="row"><button class="primary" data-save>Save</button>${d.status === "blocked" ? `<button data-approve="${d.id}">Unblock</button>` : ""}<button class="danger" data-del>Remove</button></div>
    </div>`;
  }).join("") || `<p class="hint">No device yet.</p>`;
}
$("#p-devices").onclick = async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.approve) { await api(`/api/devices/${b.dataset.approve}/approve`, { json: { approved: true } }); toast("Approved"); return setTimeout(loadDevices, 800); }
  if (b.dataset.reject) { await api(`/api/devices/${b.dataset.reject}/approve`, { json: { approved: false } }); return loadDevices(); }
  const card = b.closest("[data-dev]"); if (!card) return;
  const id = card.dataset.dev;
  if (b.hasAttribute("data-devlog")) {
    const box = $("[data-logbox]", card), l = await api(`/api/devices/${id}/log`);
    box.textContent = (l.reset_reason ? `last restart: ${l.reset_reason}, boot #${l.boot_count}, firmware ${l.version || "?"}\n\n` : "") +
      (l.lines.length ? l.lines.join("\n") : "No log received yet (the device sends it when it connects).");
    box.hidden = false; box.scrollTop = box.scrollHeight;
    return;
  }
  if (b.hasAttribute("data-ota")) {
    const fw = await api("/api/firmware");
    if (!fw.version) return toast("No firmware image in korvo/dist");
    if (!confirm(`Send firmware ${fw.version} (${Math.round(fw.size / 1024)} KB) to this device? It restarts at the end, and goes back to the previous version by itself if the new one can't reach the server.`)) return;
    const r = await api(`/api/devices/${id}/ota`, { json: {} });
    if (r.error) return toast(r.error);
    const st = $("[data-ota-state]", card);
    const poll = async () => {
      const j = await api(`/api/devices/${id}/ota`);
      st.textContent = j.state === "sending" ? `sending ${Math.round(100 * (j.acked || 0) / j.size)} %…`
        : j.state === "done" ? `firmware ${j.version} sent: the device restarts` : j.state === "error" ? `update failed: ${j.error}` : "";
      if (j.state === "sending") setTimeout(poll, 1000);
    };
    poll();
    return;
  }
  if (b.hasAttribute("data-save")) {
    const f = Object.fromEntries($$("[data-f]", card).map(i => [i.dataset.f, i.value]));
    await api(`/api/devices/${id}`, { json: f }); toast("Saved"); loadDevices();
  } else if (b.hasAttribute("data-del")) {
    if (confirm("Remove this device? It will have to be approved again.")) { await api(`/api/devices/${id}`, { method: "DELETE" }); loadDevices(); }
  } else if (b.dataset.act) {
    const a = b.dataset.act, data = { action: a };
    if (a === "say") data.text = $("[data-say]", card).value || "Hello!";
    if (a === "led") data.pattern = b.dataset.pattern;
    if (a === "mute") data.muted = b.textContent.startsWith("Mute");
    await api(`/api/devices/${id}/action`, { json: data }).catch(err => toast(err.message));
    if (a === "mute") setTimeout(loadDevices, 500);
  }
};
$("#p-devices").addEventListener("change", async e => {
  if (!e.target.hasAttribute("data-vol")) return;
  const id = e.target.closest("[data-dev]").dataset.dev;
  await api(`/api/devices/${id}/action`, { json: { action: "volume", volume: +e.target.value } });
  e.target.previousElementSibling.querySelector("b").textContent = e.target.value;
});

// ------------------------------------------------------------------ conversations
let turns = [], selTurn = null;
async function loadTurns(reset) {
  if (!ov) await loadOverview();
  const before = !reset && turns.length ? "&before=" + turns[turns.length - 1].ts : "";
  const more = await api("/api/turns?limit=40" + before);
  turns = reset ? more : turns.concat(more);
  $("#turn-list").innerHTML = turns.map(turnRow).join("") || `<p class="hint">No turns yet.</p>`;
  if (selTurn) { const el = $(`.turn[data-id="${selTurn}"]`, $("#turn-list")); if (el) el.classList.add("sel"); }
}
$("#more-turns").onclick = () => loadTurns(false);
document.addEventListener("click", e => {
  const t = e.target.closest(".turn"); if (!t) return;
  const turn = turns.find(x => x.id === t.dataset.id) || (ov && ov.turns.find(x => x.id === t.dataset.id));
  if (!turn) return;
  if (page !== "conversations") { go("conversations"); setTimeout(() => showTurn(turn), 300); } else showTurn(turn);
});
const MARKS = [["wake", "Wake"], ["accepted", "Accepted (arbitration)"], ["preroll_received", "Pre-roll received"], ["verified", "Wake verified"],
  ["eot", "End of speech"], ["asr_done", "Transcribed"], ["reply_ready", "Fast-path answer"], ["llm_first_token", "First token"], ["first_sentence", "First sentence"],
  ["tts_first_audio", "First speech audio"], ["playback_started", "Playing on device"], ["done", "Done"]];
function showTurn(t) {
  selTurn = t.id;
  $$(".turn").forEach(x => x.classList.toggle("sel", x.dataset.id === t.id));
  const tr = t.trace || {}, m = tr.marks || {}, spans = tr.spans || [];
  const total = Math.max(1, ...Object.values(m), ...spans.map(s => s.end));
  const lat = latency(t);
  let rows = MARKS.filter(([k]) => m[k] != null).map(([k, label]) =>
    `<div class="wf-row"><span>${label}</span><div class="wf-track"><i class="wf-bar mark" style="left:${m[k] / total * 100}%"></i></div><b>${fmtMs(m[k])}</b></div>`);
  rows = rows.concat(spans.map(s => `<div class="wf-row"><span>${esc(s.name)}${s.info && s.info.tools && s.info.tools.length ? " → " + esc(s.info.tools.join(", ")) : ""}</span>
    <div class="wf-track"><i class="wf-bar" style="left:${s.start / total * 100}%;width:${(s.end - s.start) / total * 100}%"></i></div><b>${fmtMs(s.end - s.start)}</b></div>`));
  const ev = (tr.events || []).map(e => `<div><span>${fmtMs(e.ms)}</span><b>${esc(e.kind)}</b><code>${esc(JSON.stringify(e.data))}</code></div>`).join("");
  $("#turn-detail").innerHTML = `<h2>${esc(deviceName(t.device_id) || t.channel)} · ${new Date(t.ts * 1000).toLocaleString()} <span class="pill">${esc(t.status)}</span></h2>
    <div class="kv">${kv({ Heard: t.transcript || "—", Answer: t.reply || "—", Route: t.route || "—" })}</div>
    ${lat != null ? `<p><span class="big-latency">${fmtMs(lat)}</span> <span class="hint">from the end of speech to the answer playing</span></p>` : ""}
    <h3>Timeline</h3><div class="wf">${rows.join("")}</div>
    ${t.audio ? `<audio controls src="/api/turns/${t.id}/audio" style="width:100%"></audio>` : ""}
    <h3>Events</h3><div class="events-list">${ev}</div>`;
}

// ------------------------------------------------------------------ chat
const CHAT_ID = "household";
async function loadChat() {
  const h = await api("/api/chat/history?chat_id=" + CHAT_ID);
  $("#chat").innerHTML = h.map(m => `<div class="msg ${m.from === "user" ? "user" : "bot"}">${m.from === "user" ? esc(m.text) : md(m.text)}</div>`).join("") || `<p class="hint">Type a question, or ask for something: “set a 10 minute timer”, “what's the weather tomorrow?”, “remember that the wifi password is on the fridge”.</p>`;
  $("#chat").scrollTop = 1e9;
}
$("#chat-form").onsubmit = async e => {
  e.preventDefault();
  const text = $("#chat-input").value.trim(); if (!text) return;
  $("#chat-input").value = "";
  $("#chat").insertAdjacentHTML("beforeend", `<div class="msg user">${esc(text)}</div><div class="msg bot" id="typing">…</div>`);
  $("#chat").scrollTop = 1e9;
  try {
    const r = await api("/api/chat", { json: { text, chat_id: CHAT_ID } });
    $("#typing").outerHTML = `<div class="msg bot">${md(r.reply)}<small>${fmtMs(r.ms)}</small></div>`;
  } catch (err) { $("#typing").outerHTML = `<div class="msg bot">⚠ ${esc(err.message)}</div>`; }
  $("#chat").scrollTop = 1e9;
};
$("#chat-reset").onclick = async () => { await api("/api/chat/reset", { json: { chat_id: CHAT_ID } }); loadChat(); };

// ------------------------------------------------------------------ push to talk (browser endpoint)
const ptt = { ws: null, ctx: null, node: null, stream: null, wakeId: 0, state: "idle", playT: 0, rate: 24000 };
function pttState(s, hint) {
  ptt.state = s;
  $("#ptt").className = "ptt " + s;
  $("#ptt-state").textContent = { idle: "Tap to talk", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…", connecting: "Connecting…" }[s] || s;
  if (hint) $("#ptt-hint").textContent = hint;
}
async function pttConnect() {
  if (ptt.ws && ptt.ws.readyState === 1) return;
  const t = await api("/api/browser_token");
  await new Promise((res, rej) => {
    const ws = new WebSocket(t.url); ws.binaryType = "arraybuffer";
    ws.onopen = () => ws.send(JSON.stringify({ type: "hello", device_id: "browser-" + Math.random().toString(36).slice(2, 8), token: t.token, kind: "browser",
      name: "Browser", capabilities: { audio_in: { rate: 16000 }, audio_out: { rates: [24000, 48000] } } }));
    ws.onmessage = ev => {
      if (typeof ev.data !== "string") return pttAudio(ev.data);
      const m = JSON.parse(ev.data);
      if (m.type === "welcome") { ptt.ws = ws; res(); }
      else if (m.type === "error") rej(new Error(m.message));
      else pttMsg(m);
    };
    ws.onerror = () => rej(new Error("cannot reach the device gateway at " + t.url));
    ws.onclose = () => { ptt.ws = null; pttStopMic(); pttState("idle"); };
  });
}
function pttMsg(m) {
  switch (m.type) {
    case "eot": pttStopMic(); pttState("thinking"); break;
    case "transcript": $("#ptt-heard").textContent = m.text; break;
    case "reply": $("#ptt-reply").textContent = m.text; break;
    case "stream_open": ptt.rate = m.rate; ptt.channels = m.channels; ptt.playT = 0; pttState("speaking"); break;
    case "cancel": pttStopMic(); pttState("idle", m.reason === "no_speech" ? "I didn't hear anything." : "Cancelled: " + m.reason); break;
    case "session_end": {
      const wait = Math.max(0, (ptt.playT - (ptt.ctx ? ptt.ctx.currentTime : 0)) * 1000);
      setTimeout(() => { if (m.follow_up) pttStart("followup"); else pttState("idle"); }, wait + 150);
      break;
    }
    case "stop": if (ptt.ctx) { ptt.ctx.close(); ptt.ctx = null; } pttState("idle"); break;
  }
}
function pttAudio(buf) {
  const b = new Uint8Array(buf); if (b[0] !== 2) return;
  const pcm = new Int16Array(buf.slice(5)), ch = ptt.channels || 1, n = pcm.length / ch;
  if (!ptt.ctx) ptt.ctx = new AudioContext();
  const ab = ptt.ctx.createBuffer(1, n, ptt.rate), out = ab.getChannelData(0);
  for (let i = 0; i < n; i++) out[i] = pcm[i * ch] / 32768;
  const src = ptt.ctx.createBufferSource(); src.buffer = ab; src.connect(ptt.ctx.destination);
  ptt.playT = Math.max(ptt.playT, ptt.ctx.currentTime + 0.08); src.start(ptt.playT); ptt.playT += n / ptt.rate;
}
const WORKLET = `class Down extends AudioWorkletProcessor {
  constructor() { super(); this.acc = 0; this.buf = []; this.ratio = sampleRate / 16000; }
  process(inputs) { const x = inputs[0][0]; if (!x) return true;
    for (let i = 0; i < x.length; i++) { this.acc += 1; if (this.acc >= this.ratio) { this.acc -= this.ratio; this.buf.push(Math.max(-1, Math.min(1, x[i]))); } }
    if (this.buf.length >= 320) { const o = new Int16Array(this.buf.length); this.buf.forEach((v, i) => o[i] = v * 32767); this.port.postMessage(o.buffer, [o.buffer]); this.buf = []; }
    return true; } }
registerProcessor("down", Down);`;
async function pttStart(source = "button") {
  try {
    pttState("connecting");
    await pttConnect();
    if (!ptt.ctx) ptt.ctx = new AudioContext();
    if (ptt.ctx.state === "suspended") await ptt.ctx.resume();
    ptt.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    await ptt.ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" })));
    const srcNode = ptt.ctx.createMediaStreamSource(ptt.stream);
    ptt.node = new AudioWorkletNode(ptt.ctx, "down");
    ptt.node.port.onmessage = e => { if (ptt.ws && ptt.state === "listening") { const b = new Uint8Array(e.data.byteLength + 1); b[0] = 1; b.set(new Uint8Array(e.data), 1); ptt.ws.send(b); } };
    srcNode.connect(ptt.node);
    ptt.wakeId += 1;
    ptt.ws.send(JSON.stringify({ type: "wake", wake_id: ptt.wakeId, source, score: 1, preroll_ms: 0 }));
    pttState("listening", source === "followup" ? "Follow-up: answer without the wake word." : "Speak now; it stops by itself when you're done.");
  } catch (err) { pttStopMic(); pttState("idle", err.message.includes("secure") || !window.isSecureContext ? "The microphone needs https or localhost." : err.message); }
}
function pttStopMic() {
  if (ptt.node) { ptt.node.disconnect(); ptt.node = null; }
  if (ptt.stream) { ptt.stream.getTracks().forEach(t => t.stop()); ptt.stream = null; }
}
$("#ptt").onclick = () => {
  if (ptt.state === "idle") pttStart();
  else if (ptt.state === "listening") { ptt.ws.send(JSON.stringify({ type: "audio_end", wake_id: ptt.wakeId })); pttStopMic(); pttState("thinking"); }
  else if (ptt.state === "speaking") { if (ptt.ctx) { ptt.ctx.close(); ptt.ctx = null; } pttState("idle"); }
};

// ------------------------------------------------------------------ timers
let timerTick = null;
async function loadTimers() {
  const [ts, devs] = await Promise.all([api("/api/timers"), api("/api/devices")]);
  const t0 = Date.now() / 1000;
  $("#timer-list").innerHTML = ts.length ? `<table><tr><th>Kind</th><th>Label</th><th>When</th><th>Left</th><th>Where</th><th></th></tr>${ts.map(t => `<tr>
    <td><span class="pill">${t.kind}</span></td><td>${esc(t.label || "—")}</td><td>${new Date(t.due * 1000).toLocaleString()}${t.repeat ? " (" + t.repeat + ")" : ""}</td>
    <td data-due="${t.due}">${fmtLeft(t.due - t0)}</td><td>${esc(t.device || t.channel)}</td><td><button class="small danger" data-cancel="${t.id}">Cancel</button></td></tr>`).join("")}</table>`
    : `<p class="hint">Nothing scheduled.</p>`;
  $("#t-dev").innerHTML = devs.filter(d => d.online).map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join("");
  clearInterval(timerTick);
  timerTick = setInterval(() => { if (page !== "timers") return clearInterval(timerTick); $$("[data-due]").forEach(td => td.textContent = fmtLeft(+td.dataset.due - Date.now() / 1000)); }, 1000);
}
$("#timer-list").onclick = async e => { const id = e.target.dataset.cancel; if (id) { await api("/api/timers/" + id, { method: "DELETE" }); loadTimers(); } };
$("#timer-form").onsubmit = async e => {
  e.preventDefault();
  await api("/api/timers", { json: { kind: "timer", label: $("#t-label").value, seconds: +$("#t-min").value * 60, device_id: $("#t-dev").value } });
  loadTimers();
};

// ------------------------------------------------------------------ media
async function loadMedia() {
  const [m, devs] = await Promise.all([api("/api/media"), api("/api/devices")]);
  $("#m-station").innerHTML = m.stations.map(s => `<option>${esc(s.name)}</option>`).join("");
  $("#m-devices").innerHTML = devs.filter(d => d.online && d.kind !== "browser").map(d => `<label><input type="checkbox" value="${d.id}" checked> ${esc(d.name)}</label>`).join("") || `<span class="hint">No satellite online.</span>`;
  $("#m-now").innerHTML = kv(Object.fromEntries(Object.entries(m.playing).map(([d, p]) => [d,
    `${p.paused ? "⏸ " : ""}${p.track && p.track.count > 1 ? `${p.track.artist} – ${p.track.title} (${p.track.position}/${p.track.count}, ${p.station})` : p.station} · ${ago(p.since)}`]))) || `<p class="hint">Nothing playing.</p>`;
}
const pickedDevices = () => $$("#m-devices input:checked").map(i => i.value);
$("#m-play").onclick = () => api("/api/media/play", { json: { station: $("#m-station").value, devices: pickedDevices() } }).then(() => setTimeout(loadMedia, 800)).catch(e => toast(e.message));
$("#m-stop").onclick = () => api("/api/media/stop", { json: { devices: pickedDevices() } }).then(() => setTimeout(loadMedia, 500));

// ------------------------------------------------------------------ people & memory
async function loadPeople() {
  const [u, f] = await Promise.all([api("/api/users"), api("/api/memory")]);
  $("#users").innerHTML = `<table>${u.users.map(x => `<tr><td><b>${esc(x.name)}</b></td><td><span class="pill">${x.role}</span></td>
    <td>${x.services.map(s => `<span class="pill ok">${esc(s)}</span>`).join(" ")}</td>
    <td>${x.name !== "household" ? `<button class="small danger" data-deluser="${esc(x.name)}">Remove</button>` : ""}</td></tr>`).join("")}</table>`;
  const sel = $("#acct-user"), cur = sel.value;
  sel.innerHTML = u.users.map(x => `<option ${x.name === cur ? "selected" : ""}>${esc(x.name)}</option>`).join("");
  loadAccounts();
  $("#facts").innerHTML = f.length ? `<table>${f.map(x => `<tr><td>${esc(x.text)}</td><td><span class="hint">${esc(x.user || "everyone")} · ${ago(x.created)}</span></td>
    <td><button class="small danger" data-delfact="${x.id}">Forget</button></td></tr>`).join("")}</table>` : `<p class="hint">Nothing remembered yet. Say “remember that…”.</p>`;
}
async function loadAccounts() {
  const user = $("#acct-user").value || "household";
  const d = await api(`/api/users/${encodeURIComponent(user)}/services`);
  $("#accounts").innerHTML = Object.entries(d.services).map(([svc, def]) => {
    const v = d.values[svc] || {};
    return `<form class="acct" data-svc="${svc}"><h3>${esc(def.title)}</h3>${def.fields.map(([k, label]) => {
      const secret = /secret/.test(label);
      return `<label class="field"><span>${esc(label.replace(" (secret)", ""))}</span><input name="${k}" ${secret ? 'type="password" autocomplete="new-password"' : ""} value="${esc(v[k] || "")}"></label>`;
    }).join("")}<div class="row"><button class="primary">Save</button><button type="button" data-test="${svc}">Test</button><span class="hint" data-result></span></div></form>`;
  }).join("");
}
$("#acct-user").onchange = loadAccounts;
$("#accounts").addEventListener("submit", async e => {
  e.preventDefault();
  const form = e.target, svc = form.dataset.svc, user = $("#acct-user").value;
  const data = Object.fromEntries([...new FormData(form)]);
  await api(`/api/users/${encodeURIComponent(user)}/services`, { json: { [svc]: data } });
  $("[data-result]", form).textContent = "Saved"; loadPeople();
});
$("#accounts").addEventListener("click", async e => {
  const svc = e.target.dataset.test; if (!svc) return;
  const form = e.target.closest("form"), user = $("#acct-user").value;
  $("[data-result]", form).textContent = "Testing…";
  try { const r = await api(`/api/users/${encodeURIComponent(user)}/services/${svc}/test`, { json: {} }); $("[data-result]", form).textContent = "✓ " + r.message; }
  catch (err) { $("[data-result]", form).textContent = "✗ " + err.message; }
});
$("#p-people").onclick = async e => {
  const d = e.target.dataset;
  if (d.deluser) await api("/api/users/" + d.deluser, { method: "DELETE" });
  else if (d.delfact) await api("/api/memory/" + d.delfact, { method: "DELETE" });
  else return;
  loadPeople();
};
$("#user-form").onsubmit = async e => { e.preventDefault(); await api("/api/users", { json: { name: $("#u-name").value, role: $("#u-role").value } }).catch(err => toast(err.message)); $("#u-name").value = ""; loadPeople(); };
$("#fact-form").onsubmit = async e => { e.preventDefault(); await api("/api/memory", { json: { text: $("#fact-text").value } }); $("#fact-text").value = ""; loadPeople(); };

// ------------------------------------------------------------------ integrations (messaging channels)
let channelUsers = [];
async function loadChannels() {
  const [chs, u] = await Promise.all([api("/api/channels"), api("/api/users")]);
  channelUsers = u.users.map(x => x.name);
  const opts = sel => channelUsers.map(n => `<option ${n === sel ? "selected" : ""}>${esc(n)}</option>`).join("");
  $("#unknown-badge").hidden = !chs.some(c => c.unknown.length);
  $("#channel-list").innerHTML = chs.map(c => `<div class="card" data-ch="${c.name}">
    <h2><span>${esc(c.title)}</span><label class="switch" style="margin:0"><input type="checkbox" data-enable ${c.enabled ? "checked" : ""}><span>${c.enabled ? "on" : "off"}</span></label></h2>
    <p class="hint" style="margin-top:-6px">${esc(c.description)}</p>
    <div class="kv"><div><span>Status</span><b>${esc(c.status)}</b></div></div>
    ${c.fields.length ? `<form data-chform>${c.fields.map(f => f.type === "bool"
      ? `<label class="switch"><input type="checkbox" name="${f.key}" ${f.value ? "checked" : ""}><span>${esc(f.label)}</span></label>`
      : f.type === "readonly" ? (f.value ? `<div class="kv"><div><span>${esc(f.label)}</span><b>${f.key.endsWith("link") ? `<a href="${esc(f.value)}" target="_blank">${esc(f.value)}</a>` : esc(f.value)}</b></div></div>` : "")
      : `<label class="field"><span>${esc(f.label)}</span><input name="${f.key}" ${f.type === "secret" ? 'type="password"' : ""} value="${esc(f.value)}"></label>`).join("")}
      <div class="row"><button class="primary">Save</button></div></form>` : ""}
    ${c.name === "signal" ? signalSetup(c) : ""}
    ${c.name !== "web" ? `<h3>Linked people</h3>${Object.entries(c.users).map(([id, user]) => `<div class="row"><code>${esc(id)}</code> → <b>${esc(user)}</b><button class="small danger" data-unlink="${esc(id)}">Unlink</button></div>`).join("") || '<p class="hint">Nobody linked yet.</p>'}
    ${c.unknown.map(x => `<div class="row" style="background:var(--panel-2);padding:8px;border-radius:10px"><span>Unknown: <b>${esc(x.name || "?")}</b> <code>${esc(x.id)}</code> “${esc(x.text)}”</span><select data-linkuser>${opts()}</select><button class="small primary" data-link="${esc(x.id)}">Link</button></div>`).join("")}
    <form class="row" data-manual><input name="id" placeholder="${c.name === "telegram" ? "Telegram user id" : "Signal UUID or number"}"><select name="user">${opts()}</select><button>Link</button></form>` : ""}
  </div>`).join("");
}
function signalSetup(c) {
  const v = Object.fromEntries(c.fields.map(f => [f.key, f.value]));
  return `<h3>Set up the assistant's Signal account</h3>
  <ol class="hint" style="line-height:1.7;margin:6px 0 10px;padding-left:18px">
    <li>Run the gateway: <code>docker run -d --name signal-api -p 8080:8080 -e MODE=json-rpc -v signal-data:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api</code>, put its URL above, Save, then <button class="small" data-sig="check">Check</button></li>
    <li>Signal needs a phone number once (SMS or voice call: a landline or prepaid SIM works; it is hidden afterwards). Solve the <a href="https://signalcaptchas.org/registration/generate.html" target="_blank">captcha</a> and copy the <code>signalcaptcha://…</code> link.</li>
  </ol>
  <div class="row"><input data-sig-number placeholder="+33…" value="${esc(v.number || "")}"><input data-sig-captcha placeholder="signalcaptcha://…"></div>
  <div class="row"><label class="switch" style="margin:0"><input type="checkbox" data-sig-voice><span>Voice call instead of SMS (landline)</span></label><button class="small" data-sig="register">Send the code</button></div>
  <div class="row"><input data-sig-code placeholder="Verification code"><button class="small" data-sig="verify">Verify</button></div>
  <div class="row"><input data-sig-base placeholder="Username prefix (e.g. lapin)" value="lapin"><button class="small" data-sig="username">Create a private username</button><button class="small" data-sig="privacy">Hide the number</button></div>
  <p class="hint" data-sig-result>${v.username ? `Members add <b>${esc(v.username)}</b> as a contact (Signal → New chat → Find by username), or open the contact link.` : ""}</p>`;
}
$("#channel-list").addEventListener("change", async e => {
  if (!e.target.hasAttribute("data-enable")) return;
  const ch = e.target.closest("[data-ch]").dataset.ch;
  await api(`/api/channels/${ch}`, { json: { enabled: e.target.checked } }); setTimeout(loadChannels, 600);
});
$("#channel-list").addEventListener("submit", async e => {
  e.preventDefault();
  const card = e.target.closest("[data-ch]"), ch = card.dataset.ch;
  if (e.target.hasAttribute("data-chform")) {
    const data = {};
    $$("input", e.target).forEach(i => data[i.name] = i.type === "checkbox" ? i.checked : i.value);
    await api(`/api/channels/${ch}`, { json: data }); toast("Saved");
  } else if (e.target.hasAttribute("data-manual")) {
    const fd = Object.fromEntries([...new FormData(e.target)]);
    await api(`/api/channels/${ch}/link`, { json: fd });
  }
  loadChannels();
});
$("#channel-list").addEventListener("click", async e => {
  const b = e.target, card = b.closest("[data-ch]"); if (!card) return;
  const ch = card.dataset.ch;
  if (b.dataset.unlink) { await api(`/api/channels/${ch}/link`, { json: { id: b.dataset.unlink, user: "" } }); return loadChannels(); }
  if (b.dataset.link) { await api(`/api/channels/${ch}/link`, { json: { id: b.dataset.link, user: b.previousElementSibling.value } }); return loadChannels(); }
  if (b.dataset.sig) {
    const out = $("[data-sig-result]", card);
    out.textContent = "…";
    const data = { number: $("[data-sig-number]", card).value, captcha: $("[data-sig-captcha]", card).value,
      use_voice: $("[data-sig-voice]", card).checked, code: $("[data-sig-code]", card).value, base: $("[data-sig-base]", card).value };
    try {
      const r = await api(`/api/channels/${ch}/action/${b.dataset.sig}`, { json: data });
      out.textContent = r.error ? "✗ " + r.error : "✓ " + (r.message || (r.username ? `Username: ${r.username} ${r.link || ""}` : JSON.stringify(r).slice(0, 200)));
      if (r.username) setTimeout(loadChannels, 800);
    } catch (err) { out.textContent = "✗ " + err.message; }
  }
});

// ------------------------------------------------------------------ skills
async function loadSkills() {
  const sk = await api("/api/skills");
  $("#skill-list").innerHTML = sk.map(s => `<div class="card skill" data-sk="${s.name}"><h2><span>${esc(s.name)}</span>
    <label class="switch" style="margin:0"><input type="checkbox" data-skill="${s.name}" ${s.enabled ? "checked" : ""}><span>${s.enabled ? "on" : "off"}</span></label></h2>
    <p class="skill-desc">${esc(s.description || "")}</p>
    ${!s.available ? `<p class="hint">Not configured (see Settings).</p>` : ""}
    ${(s.examples.en || s.examples.fr) ? `<button class="small ex-toggle" data-ex="${s.name}">Show examples</button>
    <div class="examples" hidden>${["en", "fr"].filter(l => s.examples[l]).map(l => `<div><b>${l === "fr" ? "Français" : "English"}</b><ul>${s.examples[l].map(e => `<li><a href="#" data-try="${esc(e)}" title="Try it in the chat">“${esc(e)}”</a></li>`).join("")}</ul></div>`).join("")}</div>` : ""}
    <div class="tools">${s.tools.map(t => `<div class="tool"><code>${t.name}</code>${t.risk === "confirm" ? '<span class="pill warn">asks first</span>' : ""}<span class="hint" style="margin:0">${esc(t.description)}</span></div>`).join("")}</div></div>`).join("");
}
$("#skill-list").onclick = e => {
  const b = e.target.closest("[data-ex]");
  if (b) { const box = b.nextElementSibling; box.hidden = !box.hidden; b.textContent = box.hidden ? "Show examples" : "Hide examples"; return; }
  const a = e.target.closest("[data-try]");
  if (a) { e.preventDefault(); go("talk"); $("#chat-input").value = a.dataset.try; $("#chat-input").focus(); }
};
$("#skill-list").onchange = async e => { const n = e.target.dataset.skill; if (n) { await api("/api/skills", { json: { name: n, enabled: e.target.checked } }); loadSkills(); } };

// ------------------------------------------------------------------ settings
let settings = null;
const S = [
  ["Assistant", [["assistant.name", "Name"],
    ["assistant.language", "Reply language", ["auto", "fr", "en"]], ["assistant.default_language", "Language of alarms and announcements", ["fr", "en"]], ["assistant.persona", "Persona / system prompt", "textarea"], ["assistant.location", "Home location (weather)"],
    ["assistant.timezone", "Time zone (e.g. Europe/Paris; empty = server's)"], ["assistant.household", "About the household (given to the model)", "textarea"],
    ["assistant.follow_up", "Follow-up mode (no wake word to answer a question)", "bool"], ["assistant.follow_up_ms", "Follow-up window (ms)", "number"], ["assistant.max_misses", "Tries before giving up when it does not understand", "number"],
    ["assistant.conversation_timeout_s", "Conversation memory (seconds)", "number"]]],
  ["Speech recognition", [["stt.url", "URL"], ["stt.token", "Token", "secret"], ["stt.model", "Model"]]],
  ["Speech synthesis", [["tts.url", "URL"], ["tts.token", "Token", "secret"], ["tts.model", "Model"], ["tts.voice", "Voice", "voice"], ["tts.voice_fr", "Voice for French (empty: same)", "voice"],
    ["tts.instructions", "Delivery instructions (sent with every request)", "textarea"],
    ["tts.stream", "Streaming", "bool"], ["tts.cache_phrases", "Cache short phrases", "bool"]]],
  ["Language model", [["llm.url", "URL"], ["llm.token", "Token", "secret"], ["llm.model", "Model"], ["llm.temperature", "Temperature", "number"],
    ["llm.max_tokens", "Max tokens per answer", "number"], ["llm.disable_thinking", "Disable reasoning (faster)", "bool"],
    ["llm.max_tool_rounds", "Max tool rounds", "number"], ["llm.history_turns", "Turns of history", "number"]]],
  ["Wake word", [["wake.verify", "Verify wake words on the server (stage 2)", "bool"], ["wake.phrases", "Wake phrases (comma separated)", "list"],
    ["wake.threshold", "Verification threshold (0-1)", "number"], ["wake.aliases", "Learned spellings", "aliases"],
    ["wake.learn_aliases", "Learn spellings from enrollment samples", "bool"], ["wake.arbitration_ms", "Arbitration window (ms)", "number"]]],
  ["Turn taking", [["endpoint.fast_silence_ms", "Quick end of turn after (ms, if the sentence reads complete)", "number"],
    ["endpoint.slow_silence_ms", "End of turn after (ms, otherwise)", "number"], ["endpoint.semantic", "Semantic end of turn", "bool"],
    ["endpoint.partials", "Live partial transcripts", "bool"], ["endpoint.max_turn_s", "Longest request (s)", "number"],
    ["enhance.denoise", "Extra server-side noise reduction", "bool"]]],
  ["Privacy", [["privacy.store_transcripts", "Keep transcripts", "bool"], ["privacy.store_audio", "Keep request audio", "bool"],
    ["privacy.retention_days", "Delete history after (days)", "number"]]],
  ["Quiet hours", [["quiet_hours.enabled", "Enabled (reminders softer or sent as messages)", "bool"], ["quiet_hours.start", "From (HH:MM)"], ["quiet_hours.end", "To (HH:MM)"]]],
  ["Media", [["media.stations", "Radio stations (one per line: Name | URL)", "stations"], ["media.default_volume_db", "Music level (dB)", "number"]]],
  ["Home Assistant", [["homeassistant.url", "URL (e.g. http://homeassistant.local:8123)"], ["homeassistant.token", "Long-lived access token", "secret"]]],
  ["Web search", [["search.url", "OpenSERP URL"], ["search.engines", "Engines, in order (comma separated: duck, bing, yandex, google...)", "list"],
    ["search.results", "Results per search", "number"]]],
  ["Server", [["server.name", "Server name"], ["server.auto_approve", "Approve new devices automatically", "bool"], ["server.web_password", "Web UI password", "secret"],
    ["guardrails.rate_per_minute", "Max requests per user per minute", "number"]]],
];
const getp = (o, p) => p.split(".").reduce((a, k) => a == null ? a : a[k], o);
const setp = (o, p, v) => { const ks = p.split("."); let a = o; ks.slice(0, -1).forEach(k => a = a[k] = a[k] || {}); a[ks[ks.length - 1]] = v; };
async function loadSettings() {
  settings = await api("/api/settings");
  const voices = await api("/api/voices").catch(() => []);
  $("#settings-forms").innerHTML = S.map(([title, fields]) => `<div class="card"><h2>${title}</h2>${fields.map(([p, label, type]) => {
    const v = getp(settings, p);
    if (type === "bool") return `<label class="switch"><input type="checkbox" data-p="${p}" data-t="bool" ${v ? "checked" : ""}><span>${label}</span></label>`;
    if (type === "textarea") return `<label class="field"><span>${label}</span><textarea data-p="${p}">${esc(v)}</textarea></label>`;
    if (type === "list") return `<label class="field"><span>${label}</span><input data-p="${p}" data-t="list" value="${esc((v || []).join(", "))}"></label>`;
    if (type === "stations") return `<label class="field"><span>${label}</span><textarea data-p="${p}" data-t="stations" rows="6">${esc((v || []).map(s => s.name + " | " + s.url).join("\n"))}</textarea></label>`;
    if (Array.isArray(type)) return `<label class="field"><span>${label}</span><select data-p="${p}">${type.map(x => `<option ${x === v ? "selected" : ""}>${x}</option>`).join("")}</select></label>`;
    if (type === "voice") return `<label class="field"><span>${label}</span><div class="row" style="margin:0"><select data-p="${p}">${p.endsWith("_fr") ? `<option value="">(same)</option>` : ""}${(voices.length ? voices : [v]).map(x => `<option ${x === v ? "selected" : ""}>${esc(x)}</option>`).join("")}</select><button type="button" data-voice-test="${p}">Listen</button></div></label>`;
    if (type === "aliases") return `<div class="field"><span>${label}</span><div id="aliases">${Object.entries(v || {}).flatMap(([kw, list]) => list.map(a => `<span class="alias">${esc(a)} <small>(${esc(kw.replace(/_/g, " "))})</small><button type="button" data-alias="${esc(kw)}|${esc(a)}">✕</button></span>`)).join("") || '<span class="hint">None yet: enroll a wake word on a satellite.</span>'}</div></div>`;
    return `<label class="field"><span>${label}</span><input data-p="${p}" data-t="${type || "text"}" type="${type === "secret" ? "password" : type === "number" ? "number" : "text"}" step="any" value="${esc(v ?? "")}"></label>`;
  }).join("")}</div>`).join("");
  $$("[data-voice-test]").forEach(b => b.onclick = async () => {
    const fr = b.dataset.voiceTest.endsWith("_fr");
    const voice = $(`[data-p="${b.dataset.voiceTest}"]`).value || $('[data-p="tts.voice"]').value;
    const text = fr ? "Bonjour ! Voilà ma voix. Je mets un minuteur ?" : "Hi! This is how I sound. Shall I set a timer?";
    new Audio(URL.createObjectURL(await api("/api/tts", { json: { text, voice } }))).play();
  });
}
$("#settings-forms").onclick = e => {
  const a = e.target.dataset.alias; if (!a) return;
  const [kw, al] = a.split("|"); const list = (settings.wake.aliases[kw] || []).filter(x => x !== al);
  settings.wake.aliases[kw] = list; e.target.parentElement.remove(); settings._aliasesChanged = true;
};
$("#settings-save").onclick = async () => {
  const out = {};
  $$("[data-p]").forEach(el => {
    const t = el.dataset.t; let v;
    if (t === "bool") v = el.checked;
    else if (t === "number") v = el.value === "" ? null : +el.value;
    else if (t === "list") v = el.value.split(",").map(s => s.trim()).filter(Boolean);
    else if (t === "stations") v = el.value.split("\n").map(l => l.split("|").map(s => s.trim())).filter(x => x[0] && x[1]).map(([name, url]) => ({ name, url }));
    else v = el.value;
    if (v !== null) setp(out, el.dataset.p, v);
  });
  if (settings._aliasesChanged) setp(out, "wake.aliases", settings.wake.aliases);
  try { await api("/api/settings", { json: out }); $("#settings-status").textContent = "Saved " + new Date().toLocaleTimeString(); toast("Settings saved"); loadSettings(); }
  catch (e) { toast(e.message); }
};

// ------------------------------------------------------------------ logs
async function loadLogs() { const l = await api("/api/logs"); $("#logs").innerHTML = ""; l.forEach(appendLog); }
function appendLog(m) {
  const el = $("#logs"), atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  el.insertAdjacentHTML("beforeend", `<span class="${m.level[0]}">${clock(m.ts)} ${m.level[0]} ${esc(m.name)}: ${esc(m.msg)}</span>\n`);
  if (atEnd) el.scrollTop = el.scrollHeight;
}

// ------------------------------------------------------------------ start
connectEvents();
loadOverview().then(() => go(location.hash ? location.hash.slice(1) : "dashboard"));
setInterval(() => { if (page === "dashboard") loadOverview(); }, 10000);

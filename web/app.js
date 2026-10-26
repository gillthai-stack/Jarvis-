// Jarvis dashboard: chat, voice, live activity log and settings.

const $ = (id) => document.getElementById(id);
const els = {
  orb: $("orb"), status: $("status"), model: $("model"), effort: $("effort"), reset: $("reset"),
  messages: $("messages"), empty: $("empty"), form: $("composer"), input: $("input"), send: $("send"),
  mic: $("mic"), handsfree: $("handsfree"), speak: $("speak"), heard: $("heard"),
  activity: $("activity"), toolinfo: $("toolinfo"),
  requests: $("stat-requests"), tokIn: $("stat-in"), tokOut: $("stat-out"),
};

let socket;
let busy = false;
let current = null;          // the Jarvis message bubble being streamed
let replyText = "";
let thinkingItem = null;
const toolItems = new Map();
const stats = { requests: 0, in: 0, out: 0 };

function savePref(key, value) { try { localStorage.setItem("jarvis." + key, value); } catch {} }
function loadPref(key) { try { return localStorage.getItem("jarvis." + key); } catch { return null; } }

// ---------- state & status ----------

function setState(state, text) {
  els.orb.className = "orb " + state;
  els.status.textContent = text;
}

function idleStatus() {
  if (listening && handsFree) setState("listening", 'Listening for "Jarvis"…');
  else setState("idle", "Ready");
}

// ---------- connection ----------

async function loadConfig() {
  const cfg = await (await fetch("/api/config")).json();
  els.model.innerHTML = Object.entries(cfg.models)
    .map(([id, label]) => `<option value="${id}">${label}</option>`).join("");
  els.effort.innerHTML = cfg.efforts.map((e) => `<option>${e}</option>`).join("");
  els.model.value = cfg.model;
  els.effort.value = cfg.effort;
  els.toolinfo.innerHTML =
    `<p><strong>Tools:</strong> ${cfg.tools.join(", ")}</p>` +
    `<p><strong>Folders Jarvis can read:</strong><br>${cfg.search_dirs.join("<br>")}</p>`;
  const savedModel = loadPref("model"), savedEffort = loadPref("effort");
  if (savedModel && savedModel in cfg.models && savedModel !== cfg.model) { els.model.value = savedModel; send({ type: "set_model", model: savedModel }); }
  if (savedEffort && cfg.efforts.includes(savedEffort) && savedEffort !== cfg.effort) { els.effort.value = savedEffort; send({ type: "set_effort", effort: savedEffort }); }
}

function connect() {
  socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  socket.onopen = () => { idleStatus(); loadConfig(); };
  socket.onclose = () => { setState("idle", "Disconnected, retrying…"); setTimeout(connect, 2000); };
  socket.onmessage = (e) => handle(JSON.parse(e.data));
}

function send(obj) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(obj));
}

// ---------- chat ----------

function addMessage(cls, text) {
  els.empty.style.display = "none";
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  els.messages.appendChild(div);
  els.messages.scrollTop = els.messages.scrollHeight;
  return div;
}

function ask(text) {
  text = text.trim();
  if (!text || busy) return;
  busy = true;
  els.send.disabled = true;
  stopSpeaking();
  addMessage("user", text);
  current = null;
  replyText = "";
  thinkingItem = null;
  setState("thinking", "Thinking…");
  send({ type: "ask", text });
}

function handle(ev) {
  switch (ev.type) {
    case "text":
      if (!current) current = addMessage("jarvis streaming", "");
      replyText += ev.text;
      current.textContent = replyText;
      els.messages.scrollTop = els.messages.scrollHeight;
      setState("thinking", "Answering…");
      break;
    case "thinking":
      if (!thinkingItem) thinkingItem = logItem("think", "Thinking", "");
      thinkingItem.querySelector(".detail").textContent += ev.text;
      break;
    case "tool_start": {
      thinkingItem = null;
      const item = logItem("tool", toolLabel(ev.name), ev.server ? "running on Anthropic's servers…" : "running…");
      toolItems.set(ev.id, item);
      setState("thinking", toolLabel(ev.name) + "…");
      // Text after a tool call starts a new paragraph.
      if (current) { replyText += "\n\n"; }
      break;
    }
    case "tool_input": {
      const item = toolItems.get(ev.id);
      if (item && ev.input !== "{}") item.querySelector(".detail").textContent = ev.input;
      break;
    }
    case "tool_result": {
      const item = toolItems.get(ev.id) || logItem("tool", toolLabel(ev.name), "");
      item.className = ev.ok ? "ok" : "fail";
      item.querySelector(".detail").textContent = ev.output;
      break;
    }
    case "usage":
      stats.requests += 1; stats.in += ev.input; stats.out += ev.output;
      els.requests.textContent = stats.requests;
      els.tokIn.textContent = stats.in.toLocaleString();
      els.tokOut.textContent = stats.out.toLocaleString();
      break;
    case "error":
      addMessage("error", ev.message);
      break;
    case "done":
      busy = false;
      loadToday();
      els.send.disabled = false;
      if (current) current.classList.remove("streaming");
      current = null;
      if (replyText.trim() && els.speak.checked) speak(replyText);
      else { idleStatus(); resumeListening(); }
      break;
    case "settings":
      els.model.value = ev.model; els.effort.value = ev.effort;
      logItem("", "Settings", `Brain: ${ev.model}, effort: ${ev.effort}`);
      break;
    case "reset":
      els.messages.querySelectorAll(".msg").forEach((m) => m.remove());
      els.empty.style.display = "";
      els.activity.innerHTML = "";
      break;
  }
}

const LABELS = {
  get_time: "Checking the time", search_files: "Searching files", read_file: "Reading a file",
  open_url: "Opening a web page", open_app: "Opening an app", remember: "Saving to memory",
  recall: "Checking memory", web_search: "Searching the web", web_fetch: "Reading a web page",
  gmail_search: "Searching Gmail", gmail_read: "Reading an email", gmail_draft: "Writing a Gmail draft",
  calendar_events: "Checking the calendar", calendar_create: "Adding a calendar event",
  look_at_screen: "Looking at your screen", browser_tab: "Checking your browser", media_control: "Controlling music",
  set_volume: "Setting the volume", create_reminder: "Adding a reminder", create_note: "Creating a note",
  notify: "Showing a notification", list_shortcuts: "Listing Shortcuts", run_shortcut: "Running a Shortcut",
  web_search_tool_result: "Searching the web", web_fetch_tool_result: "Reading a web page",
};
const toolLabel = (n) => LABELS[n] || n;

function logItem(cls, name, detail) {
  const li = document.createElement("li");
  li.className = cls;
  const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  li.innerHTML = `<div><span class="name"></span> <small>${time}</small></div><div class="detail"></div>`;
  li.querySelector(".name").textContent = name;
  li.querySelector(".detail").textContent = detail;
  els.activity.appendChild(li);
  els.activity.scrollTop = els.activity.scrollHeight;
  return li;
}

// ---------- speech output ----------

function speak(text) {
  if (!("speechSynthesis" in window)) { idleStatus(); return; }
  const plain = text.replace(/```[\s\S]*?```/g, " (code omitted) ").replace(/[*_#`>|]/g, "").replace(/\[(.*?)\]\(.*?\)/g, "$1");
  const utter = new SpeechSynthesisUtterance(plain);
  const voices = speechSynthesis.getVoices();
  const preferred = voices.find((v) => /Daniel|Arthur|Oliver/.test(v.name) && v.lang.startsWith("en"))
    || voices.find((v) => v.lang === "en-GB") || voices.find((v) => v.lang.startsWith("en"));
  if (preferred) utter.voice = preferred;
  utter.rate = 1.05;
  pauseListening();
  setState("speaking", "Speaking…");
  utter.onend = utter.onerror = () => { idleStatus(); resumeListening(); };
  speechSynthesis.speak(utter);
}

function stopSpeaking() { if ("speechSynthesis" in window) speechSynthesis.cancel(); }

// ---------- speech input ----------

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;
let listening = false;     // recognizer currently running
let handsFree = false;
let pushToTalk = false;
let awaitingCommand = false;
let pttText = "";

function makeRecognizer() {
  const r = new Recognition();
  r.lang = navigator.language || "en-US";
  r.interimResults = true;
  r.continuous = true;
  r.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      if (e.results[i].isFinal) onFinal(t); else interim += t;
    }
    els.heard.textContent = interim || els.heard.textContent;
  };
  r.onend = () => {
    listening = false;
    if (pushToTalk) return;
    if (handsFree && !busy && !speechSynthesis.speaking) startListening();
    else if (!busy) idleStatus();
  };
  r.onerror = (e) => {
    if (e.error === "not-allowed") {
      els.heard.textContent = "Microphone permission was denied.";
      handsFree = false; els.handsfree.checked = false;
    }
  };
  return r;
}

function onFinal(text) {
  text = text.trim();
  els.heard.textContent = text;
  if (pushToTalk) { pttText += " " + text; return; }
  if (!handsFree || busy) return;
  const m = text.match(/\bjarvis\b[,.!?]?\s*(.*)$/i);
  if (m) {
    if (m[1]) { awaitingCommand = false; ask(m[1]); }
    else { awaitingCommand = true; setState("listening", "Yes?"); }
  } else if (awaitingCommand) {
    awaitingCommand = false;
    ask(text);
  }
}

function startListening() {
  if (!Recognition || listening) return;
  recognizer = recognizer || makeRecognizer();
  try { recognizer.start(); listening = true; } catch { /* already started */ }
  if (handsFree && !busy) idleStatus();
}

function pauseListening() { if (recognizer && listening) recognizer.abort(); }
function resumeListening() { if (handsFree) startListening(); }

els.handsfree.addEventListener("change", () => {
  handsFree = els.handsfree.checked;
  savePref("handsfree", handsFree ? "1" : "0");
  if (handsFree) startListening(); else { pauseListening(); idleStatus(); }
});
els.speak.addEventListener("change", () => { savePref("speak", els.speak.checked ? "1" : "0"); if (!els.speak.checked) stopSpeaking(); });

function pttStart(e) {
  e.preventDefault();
  if (!Recognition) { els.heard.textContent = "Voice needs Chrome or Safari."; return; }
  stopSpeaking();
  pushToTalk = true; pttText = "";
  els.mic.classList.add("on");
  setState("listening", "Listening…");
  if (listening) recognizer.abort();
  setTimeout(() => startListening(), 50);
}
function pttEnd() {
  if (!pushToTalk) return;
  els.mic.classList.remove("on");
  // Give the recognizer a moment to deliver its last words.
  setTimeout(() => {
    pushToTalk = false;
    if (recognizer) recognizer.stop();
    if (pttText.trim()) ask(pttText); else idleStatus();
  }, 500);
}
els.mic.addEventListener("pointerdown", pttStart);
els.mic.addEventListener("pointerup", pttEnd);
els.mic.addEventListener("pointerleave", pttEnd);

// ---------- Today panel (Google) ----------

const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function fmtTime(ev) {
  if (ev.all_day) return "All day";
  return new Date(ev.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function loadToday() {
  const box = $("today");
  let data;
  try { data = await (await fetch("/api/today")).json(); } catch { return; }
  if (!data.connected) {
    const status = await (await fetch("/api/google/status")).json();
    box.innerHTML = status.has_credentials
      ? `<span class="muted">Connect Google to see your calendar and email here.</span>
         <a class="connect" href="/auth/google"><button>Connect Google</button></a>`
      : `<span class="muted">Gmail and Calendar aren't set up yet. Follow <code>docs/google-setup.md</code>,
         then put the file at:</span><code class="muted">${esc(status.credentials_path)}</code>`;
    return;
  }
  if (data.error) { box.innerHTML = `<span class="error">${esc(data.error)}</span>`; return; }
  const now = Date.now();
  const events = data.events.map((ev) => {
    const past = !ev.all_day && new Date(ev.end).getTime() < now;
    return `<li class="${past ? "past" : ""}"><time>${fmtTime(ev)}</time><span>${esc(ev.title)}</span></li>`;
  }).join("");
  box.innerHTML = `
    <div class="unread"><span>Unread in inbox</span><strong>${data.unread}</strong></div>
    ${events ? `<ul>${events}</ul>` : `<span class="muted">Nothing else on the calendar today.</span>`}`;
}

function googleNotice() {
  const params = new URLSearchParams(location.search);
  if (params.get("google") === "connected") logItem("ok", "Google connected", "Jarvis can now read Gmail and your calendar.");
  if (params.get("google_error")) addMessage("error", "Google sign-in failed: " + params.get("google_error"));
  if (params.has("google") || params.has("google_error")) history.replaceState(null, "", "/");
}

// ---------- views ----------

function showView(view) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  document.querySelector(".chat").hidden = view !== "chat";
  $("graphview").hidden = view !== "graph";
  savePref("view", view);
  if (view === "graph" && window.openGraph) window.openGraph();
}
window.jarvisAsk = (text) => { showView("chat"); ask(text); };

// ---------- wiring ----------

els.form.addEventListener("submit", (e) => { e.preventDefault(); ask(els.input.value); els.input.value = ""; });
els.empty.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
els.model.addEventListener("change", () => { savePref("model", els.model.value); send({ type: "set_model", model: els.model.value }); });
els.effort.addEventListener("change", () => { savePref("effort", els.effort.value); send({ type: "set_effort", effort: els.effort.value }); });
els.reset.addEventListener("click", () => { stopSpeaking(); send({ type: "reset" }); });
if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => {};

if (loadPref("speak") === "0") els.speak.checked = false;
if (!Recognition) { els.handsfree.disabled = true; els.heard.textContent = "Voice input needs Chrome or Safari."; }

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
$("briefing").addEventListener("click", () => window.jarvisAsk("Give me my morning briefing."));

googleNotice();
loadToday();
setInterval(loadToday, 120000);
if (loadPref("view") === "graph") showView("graph");
connect();

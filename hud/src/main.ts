/** HUD entry point: wires the WebSocket events to the Three.js scene, the canvas instruments and the DOM panels,
 *  runs the "attention" behaviour of the compact overlay (dim while idle → unfold on "Hey Jarvis" → settle), applies
 *  the user's preferences, drives the animated mode switches and talks to the Qt shell through `bridge`. */

import { bridge } from "./bridge";
import { ReactorScene } from "./scene";
import { initSettings, type Action, type PrefPatch } from "./settings";
import { switchMode as animateSwitch } from "./transitions";
import { Instruments } from "./widgets";
import { JarvisSocket, type Corner, type JarvisState, type Mode, type ServerEvent, type TileKind, type UiCommand, type UiPrefs } from "./ws";

const $ = <T extends HTMLElement>(sel: string): T => document.querySelector(sel) as T;

// ---- URL options -------------------------------------------------------------------------------
// ?mode=compact|panel|cinematic  layout (the Qt shell passes its mode)   ?corner= ?margin=  docking (ditto)
// ?bg=1 opaque background for browser dev.
const params = new URLSearchParams(location.search);
const MODES: Mode[] = ["compact", "panel", "cinematic"];
const CORNERS: Corner[] = ["bottom-right", "bottom-left", "top-right", "top-left"];
const urlMode = MODES.find((m) => m === params.get("mode")) ?? null;
const urlCorner = CORNERS.find((c) => c === params.get("corner")) ?? null;
if (params.get("bg") === "1") document.body.classList.add("bg");
document.documentElement.style.setProperty("--dock-margin", `${Number(params.get("margin") ?? 24) || 0}px`);

const scene = new ReactorScene($("#scene"));
const inst = new Instruments();
const sock = new JarvisSocket();

const el = {
  stateText: $("#state-text"),
  stateDot: $("#state-dot"),
  head: $("#head"),
  hbSettings: $<HTMLButtonElement>("#hb-settings"),
  hbExpand: $<HTMLButtonElement>("#hb-expand"),
  hbClose: $<HTMLButtonElement>("#hb-close"),
  transcript: $("#transcript"),
  tools: $("#tools"),
  confirm: $("#confirm"),
  confirmText: $("#confirm-text"),
  confirmYes: $<HTMLButtonElement>("#confirm-yes"),
  confirmNo: $<HTMLButtonElement>("#confirm-no"),
  form: $<HTMLFormElement>("#input-form"),
  input: $<HTMLInputElement>("#input"),
  metrics: $("#metrics"),
  dropHint: $("#drop-hint"),
  imagePreview: $("#image-preview"),
  imageClear: $<HTMLButtonElement>("#image-clear"),
  boot: $("#boot"),
  bootLines: $(".boot-lines"),
};
const body = document.body;

let userTitle = "sir";
let model = "";
let state: JarvisState = "booting";
let mode: Mode = "panel";
let confirmId: string | null = null;
let streaming: HTMLDivElement | null = null; // the assistant bubble currently receiving tokens
let streamNode: Text | null = null; // its text node (kept separate from the blinking cursor span)
let streamText = "";
let pendingImage: { b64: string; mime: string } | null = null;
const toolChips = new Map<string, HTMLSpanElement>();
let hotkeys = { text_input: "<ctrl>+<shift>+j", toggle_hud: "<ctrl>+<shift>+h" };

// ---- preferences --------------------------------------------------------------------------------
// Defaults mirror UICfg (config.py); the backend's hello carries the real ones (settings.yaml overlaid by data/ui.json).
const TILE_ORDER: TileKind[] = ["wave", "spectrum", "system", "radar", "clock", "globe", "pipeline"]; // = CSS `order` in panel
const PANEL_MAX_TILES = 3;
let prefs: UiPrefs = {
  corner: urlCorner ?? "bottom-right",
  idle_opacity: 0.55,
  linger_s: 8,
  reactor_size: 300,
  show_convo: true,
  show_status: true,
  auto_hide: false,
  widgets: true,
  tiles: { wave: true, spectrum: true, system: true, radar: true, clock: true, globe: true, pipeline: true },
  cinematic_dim: 0.45,
  scanlines: true,
};

function merge(patch: PrefPatch): UiPrefs {
  const { tiles, ...rest } = patch;
  return { ...prefs, ...rest, tiles: { ...prefs.tiles, ...(tiles ?? {}) } };
}

/** Which instrument tiles are laid out: the enabled ones; panel shows at most three (in TILE_ORDER). */
function layoutTiles(): void {
  let shown = 0;
  for (const k of TILE_ORDER) {
    const on = prefs.widgets && prefs.tiles[k] !== false && (mode === "cinematic" || shown < PANEL_MAX_TILES);
    document.getElementById(`t-${k}`)!.classList.toggle("off", !on);
    if (on) shown++;
  }
}

function applyPrefs(p: UiPrefs, { local = false } = {}): void {
  prefs = p;
  const root = document.documentElement.style;
  root.setProperty("--idle-opacity", String(p.idle_opacity));
  root.setProperty("--cinematic-dim", String(p.cinematic_dim));
  root.setProperty("--reactor-size", `${p.reactor_size}px`);
  for (const c of CORNERS) body.classList.toggle(`corner-${c}`, p.corner === c);
  body.classList.toggle("no-widgets", !p.widgets);
  body.classList.toggle("no-convo", !p.show_convo);
  body.classList.toggle("no-status", !p.show_status);
  body.classList.toggle("no-scanlines", !p.scanlines);
  layoutTiles();
  if (!local) bridge.applyPrefs({ corner: p.corner, auto_hide: p.auto_hide }); // the window docks / auto-hides accordingly
  // already resting in the corner: apply the auto-hide choice now rather than after the next turn
  if (mode === "compact" && state === "idle" && body.classList.contains("resting")) bridge.setVisible(!p.auto_hide);
  settings.refresh();
}

// ---- modes --------------------------------------------------------------------------------------
function setMode(m: Mode): void {
  mode = m;
  for (const x of MODES) body.classList.toggle(x, x === m);
  layoutTiles();
  el.hbExpand.textContent = m === "cinematic" ? "⤡" : "⤢";
  el.hbExpand.title = m === "cinematic" ? "Panel" : "Full interface  (Ctrl+Shift+M)";
  if (m !== "compact") bridge.setVisible(true);
  else if (state === "idle") settle(); // back in the corner: rest (and auto-hide) again after linger_s
}

/** Run the mode-switch animation (transitions.ts). The folded-away conversation of a resting compact HUD is laid out
 *  but invisible, so it must not be "revealed". */
function animateTo(m: Mode): Promise<void> {
  settings.close();
  window.clearTimeout(lingerTimer); // a pending "rest" must not auto-hide the window mid-switch
  lingerTimer = undefined;
  return animateSwitch(m, setMode, { skip: (el) => el.id === "convo" && mode === "compact" && !body.classList.contains("engaged") });
}

/** Page-initiated switch (buttons, drawer, Esc, voice). In the Qt shell the window grows first and then calls
 *  window.switchMode (below); in a plain browser tab we just animate in place. */
function requestMode(m: Mode): void {
  if (m === mode) return;
  settings.close();
  if (!bridge.setMode(m)) void animateTo(m);
}

// ---- attention (compact mode) -----------------------------------------------------------------
// engaged: full brightness, last lines of the conversation shown beside the reactor.
// resting: dimmed to --idle-opacity, conversation folded away (and with ui.auto_hide the window is hidden).
// Any activity engages; `linger_s` after the turn ends (state back to idle) the HUD settles back to resting.
// Panel/cinematic ignore the classes (see style.css).
let lingerTimer: number | undefined;
function engage(): void {
  window.clearTimeout(lingerTimer);
  lingerTimer = undefined;
  body.classList.add("engaged");
  body.classList.remove("resting");
  if (prefs.auto_hide && mode === "compact") bridge.setVisible(true);
}
function settle(): void {
  window.clearTimeout(lingerTimer);
  lingerTimer = window.setTimeout(() => {
    body.classList.remove("engaged");
    body.classList.add("resting");
    if (prefs.auto_hide && mode === "compact") bridge.setVisible(false);
  }, Math.max(0, prefs.linger_s) * 1000);
}
/** Something happened (reminder, notice, tool...): show it, and if nothing else is going on, settle again later. */
function touch(): void {
  engage();
  if (state === "idle") settle();
}

// ---- state ------------------------------------------------------------------------------------
const STATE_LABEL: Record<JarvisState, string> = {
  booting: "BOOTING",
  idle: "STANDBY",
  listening: "LISTENING",
  thinking: "PROCESSING",
  speaking: "SPEAKING",
  confirming: "AWAITING CONFIRMATION",
  error: "ERROR",
};
const STATE_HEX: Record<JarvisState, string> = {
  booting: "#34e4f5",
  idle: "#34e4f5",
  listening: "#ffb648",
  thinking: "#c6ecff",
  speaking: "#34e4f5",
  confirming: "#ffb648",
  error: "#ff5a5a",
};

function setAccent(hex: string): void {
  const n = parseInt(hex.slice(1), 16);
  document.documentElement.style.setProperty("--accent", hex);
  document.documentElement.style.setProperty("--accent-rgb", `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`);
  inst.setAccent(hex);
}

function setState(s: JarvisState): void {
  state = s;
  scene.setState(s);
  inst.setState(s);
  bridge.setState(s); // tray icon colour
  el.stateText.textContent = STATE_LABEL[s] ?? s.toUpperCase();
  setAccent(STATE_HEX[s] ?? STATE_HEX.idle);
  body.dataset.state = s;
  if (s !== "speaking" && s !== "thinking") {
    scene.setSpeechLevel(0);
    inst.pushLevel("out", 0);
  }
  if (s !== "listening" && s !== "confirming") {
    scene.setMicLevel(0);
    inst.pushLevel("mic", 0);
  }
  // The pipeline speaks the question (SPEAKING) before entering CONFIRMING, so only drop the card once the turn is over.
  if (s === "idle" || s === "listening") hideConfirm();
  if (s === "idle" && el.boot && !el.boot.classList.contains("done")) finishBoot();
  if (s === "idle") settle();
  else if (s !== "booting") engage();
}

// ---- transcript -------------------------------------------------------------------------------
function addMsg(kind: "user" | "assistant" | "notice" | "reminder", text: string): HTMLDivElement {
  const d = document.createElement("div");
  d.className = `msg ${kind}`;
  d.textContent = text;
  el.transcript.appendChild(d);
  while (el.transcript.children.length > 60) el.transcript.removeChild(el.transcript.firstChild!);
  el.transcript.scrollTop = el.transcript.scrollHeight;
  return d;
}

function onToken(t: string): void {
  if (!streaming) {
    streaming = addMsg("assistant", "");
    streamText = "";
    streamNode = document.createTextNode("");
    streaming.appendChild(streamNode);
    const c = document.createElement("span");
    c.className = "cursor";
    streaming.appendChild(c);
  }
  streamText += t;
  streamNode!.data = streamText;
  el.transcript.scrollTop = el.transcript.scrollHeight;
}

function onFinal(text: string): void {
  if (streaming) {
    streaming.textContent = text || streamText;
    streaming = null;
    streamNode = null;
  } else if (text) {
    addMsg("assistant", text);
  }
  streamText = "";
}

// ---- tool chips -------------------------------------------------------------------------------
function argSummary(args: Record<string, unknown>): string {
  const vals = Object.values(args).filter((v) => typeof v === "string" || typeof v === "number") as (string | number)[];
  const s = vals.map(String).join(", ");
  return s.length > 42 ? s.slice(0, 40) + "…" : s;
}

function onToolCall(id: string, name: string, args: Record<string, unknown>): void {
  const chip = document.createElement("span");
  chip.className = "chip running";
  const a = argSummary(args);
  chip.textContent = `▸ ${name}${a ? ": " + a : ""}`;
  el.tools.appendChild(chip);
  toolChips.set(id, chip);
  while (el.tools.children.length > 6) {
    const first = el.tools.firstChild as HTMLSpanElement;
    el.tools.removeChild(first);
  }
}

function onToolResult(id: string, name: string, ok: boolean): void {
  let chip = toolChips.get(id);
  if (!chip) {
    chip = document.createElement("span");
    chip.textContent = `▸ ${name}`;
    el.tools.appendChild(chip);
  }
  chip.className = `chip ${ok ? "ok" : "err"}`;
  chip.textContent = chip.textContent!.replace(/ [✓✗]$/, "") + (ok ? " ✓" : " ✗");
  toolChips.delete(id);
  const c = chip;
  setTimeout(() => {
    c.style.transition = "opacity 0.6s";
    c.style.opacity = "0";
    setTimeout(() => c.remove(), 600);
  }, 12000);
}

// ---- confirm ----------------------------------------------------------------------------------
function showConfirm(id: string, prompt: string): void {
  confirmId = id;
  el.confirmText.textContent = prompt;
  el.confirm.classList.remove("hidden");
}
function hideConfirm(): void {
  confirmId = null;
  el.confirm.classList.add("hidden");
}
el.confirmYes.onclick = () => confirmId && sock.send({ type: "confirm", id: confirmId, accepted: true });
el.confirmNo.onclick = () => confirmId && sock.send({ type: "confirm", id: confirmId, accepted: false });

// ---- settings drawer + header buttons + shutdown -----------------------------------------------
function shutdown(): void {
  // In the Qt shell the window quits and its launcher stops the backend; a bare browser tab asks the backend directly.
  if (!bridge.quit()) void fetch("/shutdown", { method: "POST" }).catch(() => undefined);
}

const settings = initSettings($("#settings"), {
  prefs: () => prefs,
  preview: (patch) => applyPrefs(merge(patch), { local: true }),
  change: (patch) => {
    applyPrefs(merge(patch));
    sock.send({ type: "ui.prefs", prefs: patch }); // the hub validates + persists, then broadcasts the full set back
  },
  action: (a: Action) => {
    settings.close();
    if (a === "hide") bridge.hide();
    else if (a === "shutdown") shutdown();
    else requestMode(a);
  },
  hotkeys: () => hotkeys,
});
el.hbSettings.onclick = () => settings.toggle();
el.hbExpand.onclick = () => requestMode(mode === "cinematic" ? "panel" : "cinematic");
el.hbClose.onclick = () => requestMode("compact");
applyPrefs(prefs, { local: true });
setMode(urlMode ?? "panel");
// drag the panel by its header (the Qt window has no title bar)
el.head.addEventListener("mousedown", (e) => {
  if (e.button === 0 && mode === "panel" && !(e.target as HTMLElement).closest("button")) bridge.beginDrag();
});

// ---- input ------------------------------------------------------------------------------------
el.form.onsubmit = (e) => {
  e.preventDefault();
  const text = el.input.value.trim();
  if (!text && !pendingImage) return;
  if (pendingImage) {
    sock.send({ type: "image", text, image_b64: pendingImage.b64, mime: pendingImage.mime });
    clearImage();
  } else {
    sock.send({ type: "text", text });
  }
  el.input.value = "";
};
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    // close what's open first; interrupt if he's busy; otherwise fold the full interface back into the corner
    if (settings.isOpen()) settings.close();
    else if (confirmId) sock.send({ type: "confirm", id: confirmId, accepted: false });
    else if (state === "speaking" || state === "thinking" || state === "listening") sock.send({ type: "interrupt" });
    else if (mode !== "compact") requestMode("compact");
  }
  if (e.key === "/" && document.activeElement !== el.input) {
    e.preventDefault();
    el.input.focus();
  }
});

// The Qt shell (ui/window.py) calls these via runJavaScript.
Object.assign(window as unknown as Record<string, unknown>, {
  focusInput: () => {
    el.input.focus();
    el.input.select();
  },
  setMode,
  switchMode: (to: Mode) => animateTo(to),
  openSettings: () => settings.open(),
});

// ---- image drop / paste -----------------------------------------------------------------------
function setImage(file: File): void {
  const r = new FileReader();
  r.onload = () => {
    const url = r.result as string;
    const [head, b64] = url.split(",", 2);
    const mime = /data:(.*?);/.exec(head)?.[1] ?? "image/png";
    pendingImage = { b64, mime };
    el.imagePreview.querySelector("img")!.src = url;
    el.imagePreview.classList.remove("hidden");
    el.input.placeholder = "Ask about the image (or press Enter)";
    el.input.focus();
  };
  r.readAsDataURL(file);
}
function clearImage(): void {
  pendingImage = null;
  el.imagePreview.classList.add("hidden");
  el.input.placeholder = "Type a command, sir — or drop an image";
}
el.imageClear.onclick = clearImage;
let dragDepth = 0;
window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; el.dropHint.classList.remove("hidden"); });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; el.dropHint.classList.add("hidden"); } });
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  el.dropHint.classList.add("hidden");
  const f = e.dataTransfer?.files?.[0];
  if (f && f.type.startsWith("image/")) setImage(f);
});
window.addEventListener("paste", (e) => {
  const item = Array.from(e.clipboardData?.items ?? []).find((i) => i.type.startsWith("image/"));
  const f = item?.getAsFile();
  if (f) setImage(f);
});

// click the reactor = wake
$("#scene").addEventListener("click", () => sock.send({ type: "wake" }));

// ---- boot sequence ----------------------------------------------------------------------------
const BOOT_LINES = ["INITIALISING CORE", "LOADING NEURAL MODELS", "CALIBRATING AUDIO ARRAY", "LINKING SYSTEM CONTROL", "ESTABLISHING UPLINK"];
let bootDone = false;
function startBoot(): void {
  el.bootLines.innerHTML = "";
  BOOT_LINES.forEach((l, i) => {
    const d = document.createElement("div");
    d.textContent = `> ${l}`;
    d.style.animationDelay = `${i * 0.35}s`;
    el.bootLines.appendChild(d);
  });
  body.classList.add("pre-boot");
}
function finishBoot(): void {
  if (bootDone) return;
  bootDone = true;
  const d = document.createElement("div");
  d.textContent = `> ALL SYSTEMS NOMINAL. WELCOME BACK, ${userTitle.toUpperCase()}.`;
  d.style.animationDelay = "0s";
  el.bootLines.appendChild(d);
  setTimeout(() => {
    el.boot.classList.add("done");
    body.classList.remove("pre-boot");
  }, 900);
}
startBoot();

// ---- metrics ----------------------------------------------------------------------------------
function onMetrics(m: Extract<ServerEvent, { type: "metrics" }>): void {
  const parts = [m.model.replace(/^ollama\//, "")];
  if (m.stt_ms != null) parts.push(`STT ${m.stt_ms}ms`);
  if (m.llm_first_token_ms != null) parts.push(`LLM ${m.llm_first_token_ms}ms`);
  if (m.tts_first_audio_ms != null) parts.push(`TTS ${m.tts_first_audio_ms}ms`);
  if (m.total_ms != null) parts.push(`TOTAL ${m.total_ms}ms`);
  el.metrics.textContent = parts.join("  ·  ");
  inst.setLatency({ stt_ms: m.stt_ms ?? null, llm_first_token_ms: m.llm_first_token_ms ?? null, tts_first_audio_ms: m.tts_first_audio_ms ?? null, total_ms: m.total_ms ?? null });
}

// ---- commands from the backend (voice tools, other clients) ------------------------------------
function onCommand(ev: UiCommand): void {
  switch (ev.action) {
    case "show":
      bridge.show();
      break;
    case "hide":
      bridge.hide();
      break;
    case "mode":
      if (ev.mode) requestMode(ev.mode);
      break;
    case "shutdown":
      if (!bridge.quit()) addMsg("notice", "POWERING DOWN"); // the backend stops itself when no window is attached
      break;
  }
}

// ---- event routing ----------------------------------------------------------------------------
function handle(ev: ServerEvent): void {
  switch (ev.type) {
    case "hello":
      userTitle = ev.user_title;
      model = ev.model;
      el.metrics.textContent = model.replace(/^ollama\//, "");
      if (ev.hotkeys) hotkeys = ev.hotkeys;
      if (!urlMode && MODES.includes(ev.mode as Mode)) setMode(ev.mode as Mode);
      if (ev.ui) applyPrefs(ev.ui);
      el.transcript.innerHTML = ""; // history replay replaces whatever a previous connection showed
      el.tools.innerHTML = "";
      streaming = null;
      for (const h of ev.history) if (h.type !== "hello") handle(h);
      setState(ev.state);
      if (ev.pending_confirm) showConfirm(ev.pending_confirm.id, ev.pending_confirm.prompt);
      if (ev.state !== "booting") finishBoot();
      break;
    case "state":
      setState(ev.state);
      break;
    case "wake":
      scene.pulse();
      engage();
      break;
    case "transcript.final":
      addMsg("user", ev.text);
      touch();
      break;
    case "assistant.token":
      onToken(ev.text);
      break;
    case "assistant.final":
      onFinal(ev.text);
      touch();
      break;
    case "audio.level":
      scene.setSpeechLevel(ev.level);
      inst.pushLevel("out", ev.level);
      break;
    case "mic.level":
      scene.setMicLevel(ev.level);
      inst.pushLevel("mic", ev.level);
      break;
    case "spectrum":
      scene.setSpectrum(ev.source, ev.bands);
      inst.setSpectrum(ev.source, ev.bands);
      break;
    case "system.stats":
      inst.setStats(ev);
      break;
    case "tool.call":
      onToolCall(ev.id, ev.name, ev.args);
      touch();
      break;
    case "tool.result":
      onToolResult(ev.id, ev.name, ev.ok);
      break;
    case "confirm.request":
      showConfirm(ev.id, ev.prompt);
      engage();
      break;
    case "confirm.resolved":
      hideConfirm();
      break;
    case "notice":
      addMsg("notice", ev.text.toUpperCase());
      touch();
      break;
    case "reminder.due":
      addMsg("reminder", `${ev.kind === "timer" ? "Timer" : "Reminder"}: ${ev.text}`);
      touch();
      break;
    case "metrics":
      onMetrics(ev);
      break;
    case "ui.prefs":
      applyPrefs(ev.prefs);
      break;
    case "ui.command":
      onCommand(ev);
      break;
  }
}

/** Tell the hub what kind of client this is (the window, or a dev browser tab) — voice shutdown defers to the window. */
function announce(): void {
  sock.send({ type: "client", kind: bridge.present ? "window" : "browser" });
}
bridge.onReady(announce);

sock.on(handle);
sock.onConnection = (up) => {
  if (!up) {
    el.stateText.textContent = "LINK LOST — RECONNECTING";
    el.stateDot.style.background = "var(--red)";
    scene.setState("error");
    bridge.setState("error");
    engage();
  } else {
    el.stateDot.style.background = "";
    announce();
  }
};
sock.connect();

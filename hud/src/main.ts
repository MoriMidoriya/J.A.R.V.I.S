/** HUD entry point: wires the WebSocket events to the Three.js scene and the DOM panels. */

import { ReactorScene } from "./scene";
import { JarvisSocket, type JarvisState, type ServerEvent } from "./ws";

const $ = <T extends HTMLElement>(sel: string): T => document.querySelector(sel) as T;

// ---- URL options -------------------------------------------------------------------------------
// ?mode=compact|panel|cinematic  layout (the Qt shell passes its mode); ?bg=1 opaque background for browser dev.
const params = new URLSearchParams(location.search);
const urlMode = params.get("mode");
if (params.get("bg") === "1") document.body.classList.add("bg");

const scene = new ReactorScene($("#scene"));
const sock = new JarvisSocket();

const el = {
  stateText: $("#state-text"),
  stateDot: $("#state-dot"),
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
  hud: $("#hud"),
};

let userTitle = "sir";
let model = "";
let confirmId: string | null = null;
let streaming: HTMLDivElement | null = null; // the assistant bubble currently receiving tokens
let streamNode: Text | null = null; // its text node (kept separate from the blinking cursor span)
let streamText = "";
let pendingImage: { b64: string; mime: string } | null = null;
const toolChips = new Map<string, HTMLSpanElement>();

function setMode(mode: string): void {
  document.body.classList.remove("compact", "panel", "cinematic");
  document.body.classList.add(mode);
}
setMode(urlMode ?? "panel");

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
const STATE_CSS: Record<JarvisState, string> = {
  booting: "var(--cyan)",
  idle: "var(--cyan)",
  listening: "var(--amber)",
  thinking: "var(--white)",
  speaking: "var(--cyan)",
  confirming: "var(--amber)",
  error: "var(--red)",
};

function setState(s: JarvisState): void {
  scene.setState(s);
  el.stateText.textContent = STATE_LABEL[s] ?? s.toUpperCase();
  document.documentElement.style.setProperty("--accent", STATE_CSS[s] ?? "var(--cyan)");
  document.body.dataset.state = s;
  if (s !== "speaking" && s !== "thinking") scene.setSpeechLevel(0);
  if (s !== "listening" && s !== "confirming") scene.setMicLevel(0);
  // The pipeline speaks the question (SPEAKING) before entering CONFIRMING, so only drop the card once the turn is over.
  if (s === "idle" || s === "listening") hideConfirm();
  if (s === "idle" && el.boot && !el.boot.classList.contains("done")) finishBoot();
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
    if (confirmId) sock.send({ type: "confirm", id: confirmId, accepted: false });
    else sock.send({ type: "interrupt" });
  }
  if (e.key === "/" && document.activeElement !== el.input) {
    e.preventDefault();
    el.input.focus();
  }
});
// The Qt shell calls window.focusInput() on the Ctrl+Shift+J hotkey.
(window as unknown as { focusInput: () => void }).focusInput = () => {
  el.input.focus();
  el.input.select();
};
(window as unknown as { setMode: (m: string) => void }).setMode = setMode;

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
  el.hud.style.opacity = "0";
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
    el.hud.style.opacity = "1";
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
}

// ---- event routing ----------------------------------------------------------------------------
function handle(ev: ServerEvent): void {
  switch (ev.type) {
    case "hello":
      userTitle = ev.user_title;
      model = ev.model;
      el.metrics.textContent = model.replace(/^ollama\//, "");
      if (!urlMode && ev.mode) setMode(ev.mode);
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
      break;
    case "transcript.final":
      addMsg("user", ev.text);
      break;
    case "assistant.token":
      onToken(ev.text);
      break;
    case "assistant.final":
      onFinal(ev.text);
      break;
    case "audio.level":
      scene.setSpeechLevel(ev.level);
      break;
    case "mic.level":
      scene.setMicLevel(ev.level);
      break;
    case "tool.call":
      onToolCall(ev.id, ev.name, ev.args);
      break;
    case "tool.result":
      onToolResult(ev.id, ev.name, ev.ok);
      break;
    case "confirm.request":
      showConfirm(ev.id, ev.prompt);
      break;
    case "confirm.resolved":
      hideConfirm();
      break;
    case "notice":
      addMsg("notice", ev.text.toUpperCase());
      break;
    case "reminder.due":
      addMsg("reminder", `⏰ ${ev.kind === "timer" ? "Timer" : "Reminder"}: ${ev.text}`);
      break;
    case "metrics":
      onMetrics(ev);
      break;
  }
}

sock.on(handle);
sock.onConnection = (up) => {
  if (!up) {
    el.stateText.textContent = "LINK LOST — RECONNECTING";
    el.stateDot.style.background = "var(--red)";
    scene.setState("error");
  } else {
    el.stateDot.style.background = "";
  }
};
sock.connect();

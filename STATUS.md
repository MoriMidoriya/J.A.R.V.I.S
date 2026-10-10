# J.A.R.V.I.S. — Build Status & Engineering Log

Voice-first, local-first Windows assistant with full PC control and an Iron Man HUD.
Design: [`PLAN.md`](PLAN.md) · Setup/usage: [`README.md`](README.md) · Root: `C:\Users\Mori\jarvis`

**Last updated: 2026-10-10 (session 3 — lighter-model evaluation, §10; all planned phases complete).** This file is the single
source of truth for *what exists, how it works, what was measured, and why decisions were made*. It is updated at the end of every phase.

---

## 0. At a glance

| Phase | Scope | State |
|---|---|---|
| 0 | Environment, config, `jarvis doctor` | ✅ done |
| 1 | Text brain: LLM router, agent loop, tool registry, first PC tools, `jarvis chat` | ✅ done |
| 2 | Voice loop: wake word → VAD → Whisper → intents/LLM → Kokoro, barge-in, hot window | ✅ done, verified live |
| 3 | Full PC-control tool suite (47 tools) + confirmations + reminders/memory + vision | ✅ done, 22/22 LLM test |
| 4 | Iron Man HUD: FastAPI/WebSocket hub + Three.js reactor + PySide6 transparent overlay, text & image input | ✅ done, verified live |
| 5 | Conversation persistence across restarts, start-with-Windows, two-process launcher, boot-time fix | ✅ done, verified live |
| — | Lighter-model evaluation: Liquid LFM2.5 (vs Qwen3 / Qwen2.5-VL) and Parakeet-TDT (vs Whisper turbo) | ✅ done 2026-10-10 — **kept the current models** (D20, §10) |

Run it today:

```powershell
cd C:\Users\Mori\jarvis
uv run jarvis doctor        # health check
uv run jarvis run           # FULL: HUD overlay + voice backend (tray icon; Ctrl+Shift+J to type; Ctrl+Shift+H hide/show)
uv run jarvis run --no-ui   # backend only (voice + HUD server) — HUD still available at http://127.0.0.1:8765/
uv run jarvis hud           # window only, attaches to a running backend
uv run jarvis chat          # text REPL with full PC control
uv run jarvis say "Good evening, sir."   # hear the voice
uv run jarvis autostart on  # start at Windows sign-in (no console window); `off` / `status`
```

First time only: `cd hud; npm install; npm run build` (the HUD is a Vite build; `jarvis doctor` checks for it).

**Open for your decision (nothing blocks on these):** (1) run `jarvis autostart on` if you want him at sign-in — I left it
OFF; (2) `memory.resume_hours: 6` is how long a conversation survives a restart — raise/lower to taste; (3) voice is still
`bm_george`; try `uv run jarvis say "..."` after editing `tts.voice` (bm_lewis, bf_emma, am_michael, …); (4) `qwen3:14b`
(9.3 GB) is still in Ollama but unused since D2 — `ollama rm qwen3:14b` if you want the space (the evaluation's own 16 GB was
cleaned up on request, §10.6).

---

## 1. Decisions log (what was chosen, and why)

| # | Decision | Reasoning / data |
|---|---|---|
| D1 | **Local-first LLM via Ollama** (you approved) | Free, private. Cloud providers remain one config line away via LiteLLM. |
| D2 | **Default local model: `qwen3:8b`** (was 14b in the plan; decided 2026-10-09) | Measured on 12 identical commands with tools: **8b 11/12 correct, 0.3–0.6 s/turn, 5 GB VRAM**; **14b 11/12 correct, 0.5–0.9 s/turn, 10 GB VRAM**. With 14b resident, Whisper+Kokoro+HUD push VRAM to the 12 GB limit and Ollama may spill layers to CPU (very slow). With 8b, everything fits with 3.8 GB spare (measured 8.4/12.2 GB used with all models loaded). The 8b miss was answering a battery question from context instead of calling `system_status` (defensible); the 14b miss was opening a Google search for "who wrote the Iliad" (worse). Switch back anytime: `llm.primary: ollama/qwen3:14b`. |
| D3 | **Qwen3 thinking mode OFF** (`think=false`) | With thinking on, the first greeting took **70 s**; off it is 0.2–0.8 s. Thinking adds nothing for PC commands. |
| D4 | **Wake word: openWakeWord `hey_jarvis`** | Free/offline ONNX; Porcupine dropped its free tier (June 2026). Scores 0.92–1.00 on clear speech, 0.58 via a robotic synthetic voice through the room. |
| D5 | **STT: faster-whisper `large-v3-turbo` int8_float16 on CUDA** | 130–230 ms per utterance on your GPU; ~1.5 GB VRAM. Hallucination filter + wake-word stripping in `stt/base.py`. |
| D6 | **TTS: Kokoro-82M, voice `bm_george`** (you chose Kokoro; voice is still yours to pick) | 73–114 ms to first audio per sentence (warm). Samples to compare: `data/voice_bm_george.wav`, `..._lewis`, `..._daniel`, `..._fable`. Change `tts.voice` in `config/settings.yaml`. **If you don't say otherwise, `bm_george` stays.** |
| D7 | **HUD: Node + Vite + TypeScript + Three.js inside a PySide6 transparent window** (you approved) | pywebview cannot do transparent windows on Windows; Qt can. Web tech gives the best visuals. |
| D8 | **File search: Everything SDK, Windows Search fallback** | Instant NTFS index. Note: the Everything *service* alone has no IPC; Jarvis auto-starts the user-session `Everything.exe -startup` on first search. |
| D9 | **Shell access ON, always behind confirmation** (you approved) | `run_powershell` is tier `dangerous`: confirm + audit log; disable with `tools.allow_shell: false`. |
| D10 | **Address: "sir"** (you approved) | `assistant.user_title`. |
| D11 | **Fast-path intents bypass the LLM** | "daddy's home", "stop", "thanks", wake-only → deterministic templated replies in ~0.15 s processing. Movie-style greetings vary by time of day. |
| D12 | **Boot does not wait for the LLM** (2026-10-09) | After a reboot Ollama needs 60–90 s to load a model from a cold disk, then ~12 s one-off warmup. Jarvis now says "Systems online" as soon as audio is ready (~30 s), fast-path greetings work immediately, and if you ask something before the model is up he says "my language model is still loading" and answers when it is. |
| D13 | **Jarvis starts Ollama itself if it isn't running** (2026-10-09) | Ollama was not in your Windows startup list after the reboot. `llm/ollama.py` launches the tray app and waits for the API. A keep-alive ping every 10 min keeps the model in VRAM while Jarvis runs. |
| D14 | **Live context goes in the user message, not the system prompt** | Keeps the system prompt + tool schemas byte-identical across turns so Ollama's prompt cache hits: 1,550 prompt tokens are re-used in 0.03 s instead of re-evaluated in 0.5 s. |
| D15 | **Native Ollama client for local models; LiteLLM only for cloud** (2026-10-09) | Root-caused a recurring ~2.1 s first-token stall: LiteLLM rebuilds its HTTP client whenever its pooled connection has been idle > 5 s (slow on Windows), and imports in 2.5 s. Direct `/api/chat` over one persistent `httpx` connection: **41–100 ms** to first token for text, 230–430 ms for tool calls (Ollama emits the call once its JSON is complete), 75–85 tok/s generation, plus server-side stats (`prompt_eval`, `load_duration`) in the debug log. `llm/ollama.py` → `OllamaClient`; conversions for tool-call arguments (object vs string), tool results and images live in `to_ollama_messages()`. |
| D16 | **HUD talks to the backend only over the local WebSocket** (2026-10-10) | QtWebEngine must own the main thread, so the backend cannot share it; rather than share objects across threads/processes, the window is just another WebSocket client. Result: the same HUD runs in a browser tab, `jarvis hud` can attach to a running `jarvis run --no-ui`, and scripts get `/chat` for free. (Originally the backend was a *thread* of the window process — superseded by D18.) |
| D17 | **Transparency via an alpha-from-brightness post pass instead of dropping bloom** (2026-10-10) | Bloom is what makes it look like the films, but Three's bloom pass writes alpha = 1 everywhere. Since the whole scene is light on black, brightness is a faithful coverage value; one 6-line shader restores transparency and keeps the glow. |
| D18 | **The voice backend is a child *process* of the HUD window, not a thread** (2026-10-10) | Measured: with the backend inside the Qt process every model load was ~1.6× slower (audio stack 16–18 s vs 10 s). Root cause, isolated by five controlled runs (§9): a *visible* Chromium view raises the Windows timer resolution to 1 ms (per-process on Win 11) and Qt/Chromium threads then share the process with Python's GIL, so the four model-loading threads get force-switched 3× more often. Window hidden → fast; window in another process → fast; `--no-ui` with `timeBeginPeriod(1)` forced → slow. `ui/launcher.py` now spawns `python -m jarvis run --no-ui` inside a Job Object (kill-on-close: the backend can never outlive the window), supervises it (restart ×3 on crash), and stops it gracefully via `POST /shutdown`. Bonus: a Chromium/GPU-driver crash in the window no longer kills the assistant. |
| D19 | **Conversation persistence = replay the last N turns from SQLite if the last one is < `resume_hours` old** (2026-10-10) | Simplest thing that makes "what did I just ask?" work after a restart (or an autostart at sign-in resuming last night's thread) without summarisation machinery. Only user/assistant text is stored, never tool payloads; a `system` marker row written by `/reset` is a hard boundary. 6 h default: long enough for a reboot, short enough that yesterday's context doesn't confuse today's first question. |
| D20 | **Keep Qwen3-8B, Whisper large-v3-turbo and Qwen2.5-VL-7B; do not switch to Liquid LFM2.5 or Parakeet** (2026-10-10, you asked: "replace if same or just a little worse") | Measured head-to-head on the real stack (§10). **Brain:** qwen3:8b passes the 44-call tool suite 44/44 at 0.63 s/turn; the best Liquid model (LFM2.5-2.6B) reaches 36/44 at 1.9 s/turn with thinking on, or 29/44 at 1.2 s with thinking suppressed, and the 8B-A1B 33/44 at 2.2 s — all of them sometimes say "Done, sir." *without* calling the tool. Not a little worse: a voice assistant that bluffs about having done something is unusable. **Ears:** Parakeet-TDT-0.6B-v3 equals Whisper on clean speech (LibriSpeech WER 3.5 % both; 100 % vs 99 % of command keywords kept) and is 2.5× faster (60 vs 152 ms) with far fewer hallucinations on noise (1/4 vs 4/4 probes), **but** its WER doubles in noise (11 % vs 5 % at 10 dB; 19 % vs 9 % at 5 dB), one-word answers like "Yes." sometimes come out in Cyrillic (no language forcing in the model), it cannot take the Jarvis vocabulary prompt, and the GPU build is *heavier*, not lighter: 3.6 GB VRAM / 2.4 GB disk vs 1.2 GB / 1.5 GB (the 0.6 GB int8 build runs on CPU: slower than Whisper and worse). The premise "more lightweight" does not hold for the variant that performs. **Eyes:** on screenshots with text at real on-screen size, Qwen2.5-VL-7B answers 18/20 (its two misses are defensible) vs LFM2.5-VL-3B 12/20 at the production 1280 px — with confident misreads (error code `D800700E`, "Bluetooth is on" when it was off, wrong speaker). At 1920 px the small model recovers to 15–17/20 but is then no faster. Its 3.2 GB *would* fit beside qwen3:8b and save the ~5 s model swap, but only with ~0.6 GB of VRAM to spare on the full stack — too fragile. Kept as an opt-in (§10.5). |

### Questions I had for you — answered by default, change if you disagree
1. **Voice**: kept `bm_george`. Listen to the four `data/voice_bm_*.wav` samples and tell me if you prefer another.
2. **Default model**: `qwen3:8b` per D2. Say the word if you want 14b (or a cloud model) as primary.
3. **Wake sensitivity**: threshold 0.5. If Jarvis misses you from across the room, set `assistant.wake_threshold: 0.4`; if he wakes on TV audio, set 0.6.
4. **Your git commit `f0b0d5b` "Phase 0 and partial Phase 1"** actually contains the Phase 2 voice code + STATUS.md. I left it as-is (no history rewriting).

---

## 2. Your machine (verified 2026-10-09 after reboot)

| Item | Value |
|---|---|
| Laptop | ASUS ROG Zephyrus G14 GA403WR — Ryzen AI 9 HX 370 (12C/24T), 31 GB RAM |
| GPU | **RTX 5070 Ti Laptop, 12 GB** (Blackwell `sm_120`), driver 616.92 |
| OS | Windows 11 Home 24H2 |
| Python | 3.12.14 in `.venv` (managed by uv 0.12.12) — torch 2.11.0+cu128, CTranslate2 4.8.2 CUDA, onnxruntime 1.29 |
| Audio | Mic: Realtek Microphone Array · Speakers: Realtek · 15 inputs / 21 outputs enumerated |
| Tooling | Node v24.19.0 · Ollama 0.33.3 · Everything 1.4.1 · eSpeak NG · git 2.54 |
| Models on disk | Ollama: `qwen3:8b` 5.2 GB, `qwen3:14b` 9.3 GB, `qwen2.5vl:7b` 6 GB · HF cache: Whisper large-v3-turbo, Kokoro-82M · `models/openwakeword/*.onnx` · `models/everything/Everything64.dll` |
| Power plan | "Turbo" (good — GPU clocks were 2.4 GHz under load) |

`uv run jarvis doctor -v` reports **"All systems nominal, sir."**

---

## 3. Architecture (how the pieces fit)

```
 mic (16 kHz, 80 ms frames)                                   speakers (24 kHz)
      │                                                             ▲
      ▼                                                             │ RMS level → HUD pulse
 openWakeWord ──"hey jarvis"──► Silero VAD ──utterance──► faster-whisper ──text──┐        AudioPlayer
   (every state; barge-in)      (600 ms silence = end)      (~150 ms, CUDA)      │             ▲
                                                                                 ▼             │ sentences
                                                     ┌─── fast-path intents ─────┤       Kokoro TTS (GPU)
                                                     │  greeting/stop/thanks     │             ▲
                                                     │  (no LLM, ~0.15 s)        ▼             │ streamed text
                                                     └── Agent loop ── router ── Ollama qwen3:8b (native) ──┘
                                                          │   ▲                └─ LiteLLM → Claude/GPT/Gemini/Groq (key)
                                                          ▼   │ results
                                                       Tool registry (apps, files, browser, system, …)
                                                          │ confirm? ──► spoken yes/no  (or HUD buttons)
                                              EventBus → Hub → WebSocket 127.0.0.1:8765 → HUD (Qt overlay / browser)
                                                                      ◄── typed text, images, confirm, wake, interrupt

 Processes (D18):   [window: pythonw/python -m jarvis run]  ──spawns, Job Object, POST /shutdown──►  [backend: python -m jarvis run --no-ui]
                     PySide6 + QtWebEngine, tray, hotkeys        ◄──────────── WebSocket ────────────   everything drawn above
                     logs/hud.log                                                                        logs/jarvis.log, data/jarvis.db
```

### Source map (`src/jarvis/`)

| File | Role | Key ideas |
|---|---|---|
| `__main__.py` | CLI (`doctor`, `chat`, `run`, `hud`, `autostart`, `say`) | Forces UTF-8 console (Windows cp1252 crashed on `▸`). |
| `config.py` | Typed settings (pydantic) from `config/settings.yaml` + `permissions.yaml`; env overrides `JARVIS__SECTION__KEY` | `load_persona()` fills `config/persona.md`. |
| `events.py` | Typed events + `EventBus` (async pub/sub) | `state`, `wake`, `transcript.*`, `assistant.*`, `audio.level`, `mic.level`, `tool.*`, `confirm.*`, `metrics`. The HUD consumes these verbatim. |
| `logging_setup.py` | loguru console (skipped under `pythonw`) + rotating file `logs/jarvis.log` (`logs/hud.log` for the window process) | `reapply()` exists because **kokoro calls `logger.remove()` on import** and wipes all sinks. |
| `doctor.py` | Health checks | GPU kernel smoke test (not just "is CUDA visible"); checks the HUD build exists. |
| `app.py` | Boot wiring of the **backend process**; `main()` is also the launcher entry | Loads TTS/STT/wake/VAD in parallel threads; LLM warms in background; keep-alive; `_spawn()` logs background-task failures instead of letting them die silently; `request_stop()` for `POST /shutdown`. |
| `ui/launcher.py` | `BackendProcess`: spawn `jarvis run --no-ui` as a child, Job Object kill-on-close, supervise/restart, graceful stop (D18) | Picks `python.exe` even when the window runs under `pythonw.exe`; no console window for the child when autostarted. |
| `ui/autostart.py` | Start-with-Windows via a Startup-folder shortcut to `pythonw.exe -m jarvis run` | No admin, no Task Scheduler; `enable/disable/status`. |
| `voice.py` | **The state machine** (IDLE→LISTENING→THINKING→SPEAKING→hot window) | Frame loop never blocks on speech; drains stale mic frames so Jarvis never transcribes himself; wake word works in every state (barge-in). |
| `audio/input.py` | `sounddevice` capture → asyncio queue; 400 ms preroll ring | |
| `audio/wakeword.py` | openWakeWord ONNX wrapper; downloads models to `models/openwakeword/` | 2 s cooldown after a hit. |
| `audio/vad.py` | Silero VAD end-pointing | Utterance = 320 ms lead-in + speech + 600 ms tail; blips < 250 ms dropped; max 15 s. |
| `audio/output.py` | Playback thread with cancel + RMS meter | 20 ms blocks; `cancel()` stops within ~20 ms. |
| `stt/` | `faster_whisper.py` (CUDA), `cloud.py` (OpenAI/Groq), `base.py` sanitiser | Strips leading "hey jarvis", drops Whisper hallucinations ("Thank you.", "you", …). |
| `tts/` | `kokoro.py`, `openai_tts.py`, `elevenlabs.py`, `speaker.py` | `Speaker` streams sentence-by-sentence; `clean_for_speech()` fixes "PC", "%", paths, URLs for pronunciation. |
| `llm/router.py` | Model router: `ollama/*` → native `OllamaClient`; anything else → LiteLLM | Strips `<think>` blocks; rescues tool calls emitted as JSON text; per-call timeout; fallback model; per-model `think`/temperature via `llm.model_opts` (§10.5). |
| `llm/ollama.py` | Ollama lifecycle (`ensure_running`, `/api/ps`) + `OllamaClient` streaming `/api/chat` | Persistent keep-alive connection, `think: false` (or omitted for always-reasoning models), `keep_alive`, `num_ctx`, server timing stats (D15). |
| `llm/agent.py` | Tool-calling loop; vision turns; conversation persistence (D19) | **Bluff guard**: if the model says "Opening X…" without calling a tool, the text is held back and the model is nudged once to actually call it. `_restore_history()` on start, `_persist()` per turn, `reset()` writes a boundary marker. |
| `llm/intents.py` | Fast-path regexes + greeting templates | 4 day-parts × 3–4 variants; yes/no classifier for confirmations. |
| `llm/chunker.py` | Sentence splitter for streaming TTS | Handles "3.5", "p.m.", abbreviations. |
| `llm/context.py` | Live context (time, battery, active window) | Stable part → system prompt; volatile part → user message (D14). |
| `tools/registry.py` | `@tool` decorator → JSON schema, validation, tiers (safe/confirm/dangerous), execution, events | Pydantic models generated from type hints; `ToolError` for clean user-facing failures. |
| `tools/apps.py` | Index Start Menu + UWP via `Get-StartApps` (+ `.lnk` scan), fuzzy launch/close | 176–179 apps indexed; cache `data/apps_index.json` (6 h TTL); 40+ spoken aliases ("chrome", "vs code", "settings"…). |
| `tools/files.py` | `search_files`, `open_path`, `open_folder`, `reveal_in_explorer`, `play_media` | Query relaxation: exact → drop filler words → OR terms. Allow-listed roots. |
| `tools/everything.py` | ctypes binding to Everything SDK; auto-start IPC instance | |
| `tools/browser.py` | `open_url` (20 site aliases), `web_search` (8 engines incl. YouTube) | |
| `tools/system.py` | `system_status` (CPU/RAM/GPU/disk/battery), `get_datetime` | |
| `tools/media.py`, `windows.py`, `power.py`, `network.py`, `inputs.py`, `screen.py`, `shell.py`, `web.py`, `memory.py`, `reminders.py` | Phase 3 control suite — see §7 | |
| `memory/store.py`, `memory/scheduler.py` | SQLite facts/reminders/messages; 1 s poll → `reminder.due` events | |
| `server/hub.py` | WebSocket hub: EventBus → all HUD clients; HUD commands → pipeline | Per-client queue + writer task (ordering, back-pressure); `hello` with history + pending confirm. |
| `server/api.py` | FastAPI app: `/ws`, `/health`, `/chat`, `/confirm`, `/interrupt`, `/shutdown`, static `hud/dist` | uvicorn runs inside the backend's asyncio loop (`loop="none"`, `log_config=None` — its default logging config crashes under `pythonw`). |
| `ui/window.py` | PySide6 shell: frameless/transparent/on-top `QWebEngineView`, tray, hotkeys, modes; 2 s supervision tick | Compact mode is click-through via Win32 `WS_EX_TRANSPARENT`; drag to move in panel mode. |
| `hud/src/scene.ts` | Three.js reactor: particle sphere shader, rings, ticks, core, waveform ring, bloom + alpha pass | `setState / setSpeechLevel / setMicLevel / pulse`. |
| `hud/src/main.ts`, `ws.ts`, `style.css` | DOM panels, typed WS client with reconnect/backoff, state colours, modes | `?mode=` and `?bg=1` (opaque background for browser dev) URL options. |

---

## 4. Phase 0 — Environment (done)

- Installed via winget: uv, Node LTS, Ollama, Everything, eSpeak NG. Python 3.12 via `uv python install`.
- `pyproject.toml`: 40+ deps; **`environments = ["sys_platform == 'win32'"]`** was required because `openwakeword` pulls a Linux-only `tflite-runtime` that has no 3.12 wheel and broke resolution. Torch comes from the `cu128` index (Blackwell needs CUDA ≥ 12.8).
- Config files: `settings.yaml` (every knob), `persona.md` (JARVIS character), `permissions.yaml` (tiers, allowed/denied folders).
- `jarvis doctor` verifies GPU kernels actually execute on `sm_120`, CTranslate2 sees CUDA, audio devices, Ollama models, Everything, Node, model caches.

## 5. Phase 1 — Text brain + PC tools (done)

**What it does:** `jarvis chat` is a REPL where typed commands control the PC through 12 tools, with any LLM.

**Verified live (2026-09-10):** "open calculator" → launched; "find the plan document for the jarvis project" → `C:\Users\Mori\jarvis\PLAN.md`; "close calculator" → confirmation prompt, decline respected; time/battery answered from live context.

**Latency with `qwen3:8b` (steady state, native path):** tool-call round 0.25–0.45 s, follow-up round ("Done, sir.") 0.1–0.25 s → a tool command ≈ **0.5 s** plus tool time.

**Bugs found & fixed in Phase 1**
- Qwen3 "thinking" → 70 s first reply → `think=false` (D3).
- Local model *announcing* actions without calling tools → bluff guard + persona rule "You act ONLY through your tools" (`llm/agent.py`, `config/persona.md`).
- "plan document jarvis project" found nothing → query relaxation in `tools/files.py`.
- Everything service ≠ IPC instance → auto-start (D8).
- Rich crashed printing `▸` on cp1252 → UTF-8 reconfigure in `__main__.py`.

## 6. Phase 2 — Voice loop (done, verified live 2026-10-09)

### How a voice turn works
1. `MicInput` delivers 80 ms int16 frames to the asyncio loop (preroll ring of 400 ms).
2. `WakeWord.detect()` scores every frame (~1 ms CPU). ≥ 0.5 → `wake` event, current speech/turn cancelled (barge-in), state → LISTENING. 150 ms of preroll is kept so the first syllable after "Jarvis" isn't lost.
3. `SileroVAD.feed()` accumulates frames; 600 ms of silence after speech → `Utterance` (320 ms lead-in + speech + tail). If nothing is said for 1.6 s Jarvis asks "Yes, sir?" **once**; after 7 s he goes back to IDLE.
4. `faster-whisper` transcribes (130–230 ms). Leading "hey jarvis" is stripped; hallucinations dropped. Empty → keep listening.
5. `intents.match()`: greeting / stop / thanks → templated reply, no LLM. Otherwise `Agent.respond()` streams tokens.
6. `Speaker` cuts the stream into sentences (`SentenceChunker`) and synthesises each with Kokoro in a single worker thread while the LLM is still generating; `AudioPlayer` plays them back-to-back and publishes RMS levels (`audio.level`) for the HUD.
7. After the reply: **hot window** (8 s, `assistant.hot_window_seconds`) — you can follow up without the wake word. Then IDLE.
8. Confirmations: a `confirm`/`dangerous` tool makes Jarvis speak the question and listen for yes/no (two attempts; silence or interruption = no).

### Test results

**Offline component test (synthetic speech, 2026-09-10):** wake score 1.00 on "Hey Jarvis…"; STT 141–225 ms; "Daddy's home." → greeting intent; chunker splits correctly.

**End-to-end integration test with injected audio (2026-10-09, `18/18 checks passed`, run 4× during the day)** — real wake word, VAD, Whisper, LLM, Kokoro and speakers; only the microphone was replaced by a feeder pushing synthetic speech (Kokoro `am_adam` voice) in real time. Final run after all fixes:

| Scenario | Result |
|---|---|
| A — "Hey Jarvis, daddy's home." | wake 0.93 → STT 145 ms `"It's daddy's home."` (the "-vis" tail leaks in as "It's"; now tolerated) → greeting *"Welcome home, sir. Burning the midnight oil again, I see. How can I help?"* **1.78 s after the wake word** (≈1.0 s of that is you finishing the sentence + 0.6 s end-of-speech silence; processing ≈ 0.15 s); first audio 79 ms; `audio.level` pulses; hot window → IDLE. |
| B — "Hey Jarvis, open calculator." | STT 139 ms → `open_app(Calculator)` ✓ → *"Done, sir."* **LLM turn 488 ms (two rounds), first audio 574 ms after the LLM started.** Before D15 this was 2.5–4.1 s. |
| C — long answer, then "Hey Jarvis" mid-sentence | first token **61 ms** (was 2135 ms) → speaking → barge-in detected while speaking (0.86) → playback cancelled → LISTENING → silence → *"Yes, sir?"* exactly once → IDLE after 7 s. |

**Agent-level command test (text, native path, 2026-10-09):**

| Command | Result | Turn |
|---|---|---|
| "Open Calculator." | launched → *"Done, sir."* | 0.54 s |
| "What time is it right now?" | `get_datetime` → *"It is 11:32 PM, sir."* | 0.52 s |
| "Close Calculator." (confirmation declined) | *"Very well, sir, leaving it open."* (before fix #9 the model claimed it had closed it) | 0.50 s |
| "Find the plan document for the jarvis project." | `search_files("jarvis plan")` → `C:\Users\Mori\jarvis\PLAN.md` → offers to open it | 1.37 s |

**Live boot with the real microphone (2026-10-09):** audio stack ready in **30 s** on the first boot after the reboot (12 s on later boots — OS file cache warm), LLM warm in 7 s in parallel (0.3 s once Ollama has the model resident), *"Systems online. Good evening, sir."* spoken, Realtek mic opened. Then an **acoustic loopback** (Windows' Microsoft David voice through the laptop speakers, into the real mic):

| Spoken through speakers | Outcome |
|---|---|
| "Hey Jarvis. Daddy's home." | **missed** (first utterance after silence; robotic voice; see D4) |
| "Hey Jarvis. What time is it right now?" | wake 0.58 → STT 146 ms → `get_datetime` → spoken answer, first audio 2.9 s after LLM start |
| "Hey Jarvis. Open notepad." | Notepad launched → "Done, sir." (the "Hey Jarvis." fragment arrived in the hot window, was transcribed, stripped to empty and ignored — correct) |

A real human voice scores far higher than SAPI's David (0.92–1.00 in every injected test), so expect reliable wakes; tune `wake_threshold` per §1 Q3.

**VRAM with everything loaded:** 8.4 GB / 12.2 GB (Ollama 8b 6.3 GB incl. 8k KV cache; Whisper + Kokoro ≈ 1.9 GB).

### Bugs found & fixed in Phase 2 (all before the first live run, by reading the code; then confirmed by tests)
1. "Yes, sir?" would repeat forever — `_start_listening` reset the `prompted` flag. Fixed with an explicit `prompted` parameter.
2. Fast-path greetings blocked the frame loop (`await speaker.say()` inside frame handling) → no barge-in during greetings, and mic frames captured *while Jarvis spoke* were later fed to the VAD. Now everything that speaks runs in a separate task and the mic queue is drained when listening starts.
3. Fast-path replies emitted no `assistant.final` event → the HUD would never have shown them.
4. Kokoro wipes loguru sinks on import → `logs/jarvis.log` was empty and `--debug` showed nothing. `logging_setup.reapply()` after import.
5. Utterances contained all silence since listening began (7 s sent to Whisper for a 1 s phrase) → trimmed to 320 ms lead-in.
6. Ollama cold-start exceeded the 60 s request timeout → boot no longer waits for the LLM; warmup uses a 300 s timeout (D12).
7. Audio-out thread crashed if the stream closed mid-write → caught; player goes idle.
8. **Recurring ~2.1 s first-token stall** (every text answer; also the first call after any pause). Investigation: Ollama direct = 40–100 ms; a forced full prompt-cache miss = 0.5 s; no model reloads in Ollama's log; LiteLLM = 2.1 s whenever its connection had been idle > 5 s. Fix: native client (D15).
9. Declined confirmation → the model told the user *"Calculator is closed, sir."* The tool result said only "The user declined"; now it says explicitly `NOT DONE … was NOT executed and nothing changed`.
10. The 8b model read "the plan **document**" as "search the Documents folder" and found nothing. `search_files` now widens to the whole PC when a folder-restricted search is empty, and the parameter descriptions steer the model towards distinctive words ("jarvis plan").
11. First file search after a reboot took 5 s (starting Everything's user instance) and the first app launch 1.1 s (index scan) → both are now warmed in a background thread during boot (176 apps indexed in 2 ms from cache).
12. "Hey Jarvis, daddy's home" sometimes transcribes as *"It's daddy's home"* (Whisper renders the end of "Jarvis" as a word) → greeting regex tolerates leading fragments (`it's`, `is`, `his`, `service`…).
13. Lines logged *during* Kokoro's 5 s import were lost from the file → `sinks_frozen()` neutralises Kokoro's `logger.remove()/add()` for the duration of the import.

### Known limitations (Phase 2)
- Barge-in requires the wake word ("Hey Jarvis"), not just talking over him. Half-duplex: while he speaks, only the wake word is detected.
- Latency budget for a spoken command, measured: you stop talking → 0.6 s end-of-speech wait → 0.15 s STT → 0.3–0.5 s LLM (two rounds for a tool) → 0.08 s first audio ≈ **1.1–1.3 s to the first spoken word**. The 0.6 s endpoint is the biggest fixed cost; `audio.vad_silence_ms` trades it against cutting you off.
- No acoustic echo cancellation; relies on Windows/Realtek/AMD noise suppression. If Jarvis wakes himself up, raise `wake_threshold`.

---

## 7. Phase 3 — Full PC control, memory, reminders (done 2026-10-10)

**47 tools** are now registered (was 12). New in Phase 3:

| Module | Tools | How it works |
|---|---|---|
| `tools/media.py` | `set_volume`, `change_volume`, `set_mute`, `get_volume`, `set_brightness`, `change_brightness`, `media_control` | Volume via Windows Core Audio (pycaw `EndpointVolume`; COM initialised per worker thread). Brightness via WMI (`screen_brightness_control`). Media keys are synthesised `VK_MEDIA_*` presses — reach Spotify/YouTube/VLC without focus. |
| `tools/windows.py` | `list_windows`, `focus_window`, `window_action` (minimize/maximize/restore/close/snap_left/snap_right), `show_desktop` | Win32 `EnumWindows`, filtered (visible, titled, not cloaked UWP shells). Fuzzy match on title **and** process name ("switch to chrome" works though Chrome's title is the page). Foreground-lock bypass via `AttachThreadInput` + ALT tap. |
| `tools/power.py` | `lock_pc`, `sleep_pc`, `shutdown_pc(delay_s)`, `restart_pc(delay_s)`, `cancel_shutdown` | `LockWorkStation`, `SetSuspendState(0,1,0)` (true sleep, not hibernate), `shutdown /s /t`. All but cancel are tier *confirm*. |
| `tools/network.py` | `set_wifi`, `set_bluetooth` (confirm), `network_status` | Radios through WinRT `Windows.Devices.Radios` from PowerShell 5.1 — **no admin needed** (unlike `netsh`). Status = `netsh wlan show interfaces` + Microsoft's connectivity probe. |
| `tools/inputs.py` | `get_clipboard`, `set_clipboard`, `type_text`, `press_keys` | pyperclip + pynput; `press_keys("ctrl+shift+esc")` style combos with aliases (win→cmd, esc, f1–f12…). |
| `tools/screen.py` | `take_screenshot`, `describe_screen(question)` | mss capture → JPEG ≤1280 px → `llm.vision` (qwen2.5vl:7b). **Model swap**: the chat model is evicted first (and we wait for VRAM to actually free), the answer is spoken *directly from the tool* (`speak_result=True`, no second LLM round), and the chat model is re-warmed in the background. |
| `tools/shell.py` | `run_powershell` | Tier *dangerous*: spoken confirmation + `logs/audit.log` entry; 30 s timeout; output capped at 4 k chars. Hidden from the model entirely if `tools.allow_shell: false`. |
| `tools/web.py` | `get_weather(city?)`, `fetch_page(url)` | Open-Meteo (free, no key; geocoding cached) — city, `assistant.home_city`, or IP geolocation. Page text via trafilatura. |
| `tools/memory.py` + `memory/store.py` | `remember`, `recall`, `forget` | SQLite `data/jarvis.db`. The 20 most recent facts are injected into the **system prompt**, so Jarvis just knows them ("Where did I park?" → "Level 3, sir"). |
| `tools/reminders.py` + `memory/scheduler.py` | `set_reminder(text, when)`, `set_timer(duration, label)`, `list_reminders`, `cancel_reminder` | Times are parsed **in code** ("in 20 minutes", "at 7 pm", "tomorrow at 9", "18:30", "tonight") — the model only relays your words; it is bad at clock arithmetic. A 1 s poll loop fires `reminder.due`; the voice pipeline speaks it immediately when idle, or right after the current turn. Reminders survive restarts (they live in the DB); ones missed while offline are announced once as "missed". Pending reminders appear in the live context so "what's next?" works. |

**Permissions (`config/permissions.yaml`)** — confirm: lock/sleep/shutdown/restart, Wi-Fi/Bluetooth, close_app, delete_path. dangerous: run_powershell. Everything else runs immediately, including `type_text`/`press_keys`/`window_action(close)` — deliberate, since you asked for full control; flip any of them to `confirm` by adding the name to the list.

### Test results

**Direct tool test (46 checks, 2026-10-09):** all pass after two fixes — volume/mute/brightness round-trips (80–460 ms), window list/focus, clipboard, screenshot (174 ms), network status, weather for Pune (1.2 s), Wikipedia page read (0.6 s), remember/recall/forget, timer fired by the scheduler, PowerShell with confirmation + audit entry, `describe_screen` correct ("Visual Studio Code… 'Jarvis PC assistant project'").

**LLM test — 22 natural commands through the real agent (qwen3:8b): 22/22 correct**, e.g. "Turn the volume up a bit" → `change_volume(+15)`; "Shut down the PC in 10 minutes" → `shutdown_pc(600)` + confirmation asked; "Remind me to call mum at 7 pm" → `set_reminder("call mum", "at 7 pm")`; "Where did I park the car?" → recalled "level 3"; "Press alt F4" → `press_keys("alt+F4")`; "How many processes are running? Use PowerShell" → `run_powershell(Get-Process | Measure-Object …)` + confirmation. Typical turn **0.5–0.7 s**; weather 3.2 s (network); "what's on my screen" **6.3 s**.

### Bugs found & fixed in Phase 3
1. `cancel_reminder("test reminder")` cancelled a timer named "test" — partial-ratio ties. Score now blends full and partial similarity.
2. "at 7" asked at 23:40 resolved to 19:00 tomorrow; now picks whichever of 07:00/19:00 comes first (07:00 tomorrow).
3. `describe_screen` took **65 s**: the 7B vision model was loaded *next to* qwen3:8b and half of it spilled to CPU (prompt eval 26 s). Even after adding an explicit unload it was 14 s, because Ollama's unload returns before VRAM is free. Now we unload, **wait for `/api/ps` to drop the model**, then load vision → 3 s load + 1.3 s image encode. The answer is spoken straight from the tool.
4. `warm()`/keep-alive loaded the model with Ollama's default `num_ctx` (4096) while chat uses 8192 → Ollama saw a different configuration and **reloaded the model on the next turn** (+3–4 s). All loads now pass the same `num_ctx`. (This would have bitten every 10 minutes via the keep-alive.)
5. `network_status` said "internet unreachable" while weather worked — HEAD against an https-less probe URL. Fixed to GET `http://www.msftconnecttest.com/connecttest.txt`.
6. After a declined confirmation the model parroted the example phrase ("leaving it open") even for *lock*. Example made neutral.

### Vision model comparison (2026-10-10, 1280-px screenshot, after the swap fix)
| Model | Load (page cache) | Image + prompt eval | Verdict |
|---|---|---|---|
| **qwen2.5vl:7b** (6 GB) | 3.0 s | **1.2–1.3 s** (1,376 tokens) | Correct, reads title text. **Kept as default.** |
| gemma3:4b (3.3 GB) | — | 21 s (its SigLIP encoder runs slowly in Ollama) | Rejected; removed again. |

Image width 1024 px would save ~0.5 s per look but loses small text; kept 1280. (LFM2.5-VL-3B was evaluated later with
ground-truth screenshots — see §10.4.)

### Known limitations (Phase 3)
- Vision costs ~5–6 s because the two models must swap in 12 GB; the chat model is back ~3 s later (hidden unless you ask something immediately).
- `set_wifi`/`set_bluetooth` depend on Windows granting radio access to desktop apps (worked here).
- No AEC: "stop" while music plays through the speakers must be louder than the music.

## 8. Phase 4 — Iron Man HUD (done 2026-10-10, verified live)

Three parts, each independently useful:

| Part | Files | What it is |
|---|---|---|
| **Server** | `server/hub.py`, `server/api.py` | FastAPI + WebSocket on `127.0.0.1:8765` (loopback only). Every `EventBus` event is fanned out to connected HUDs as JSON; HUD commands come back the same way. Also serves the built HUD as static files and a tiny REST surface for scripts. |
| **HUD** | `hud/` (Vite + TypeScript + Three.js) → `hud/dist/` | The arc reactor scene + panels (transcript, tool chips, confirm card, input box, metrics, boot sequence). Runs in any Chromium: the Qt window, or a browser tab at `http://127.0.0.1:8765/`. |
| **Shell** | `ui/window.py` (PySide6) | Frameless, transparent, always-on-top `QWebEngineView` that loads the HUD, with tray icon, global hotkeys and three layout modes. |

### How it fits together (and why Qt owns the main thread)

```
 jarvis run ── window process: Qt event loop → HudWindow (QWebEngineView → http://127.0.0.1:8765/?mode=compact)
      │                           ▲ tray menu / hotkeys (pynput thread → Qt Signal)      logs/hud.log
      └─ spawns ── backend process: `python -m jarvis run --no-ui`  (asyncio loop)       logs/jarvis.log
                   ├─ uvicorn (FastAPI) ── /ws ──► Hub ── per-client asyncio.Queue ── writer task ──► HUD
                   │                        ◄── {"type":"text"|"image"|"confirm"|"wake"|"interrupt"}
                   ├─ EventBus.on(None, hub._on_event)   # every event → JSON → all clients
                   └─ VoicePipeline / Agent / tools (unchanged from Phases 2–3)
```

QtWebEngine insists on living in the main thread, so the backend cannot share the window's main thread. In Phase 4 it
ran as a daemon *thread* of the window process; Phase 5 measurements showed that slowed every model load ~1.6×, so it
is now a child *process* (D18, details in §9). The two halves talk **only** through the local WebSocket/HTTP server —
a clean seam that also means you can run them separately: `jarvis run --no-ui` + `jarvis hud` (or a browser tab),
which is how the HUD was developed. Quitting from the tray → `POST /shutdown` → `JarvisApp.request_stop()` (cancels
all backend tasks → mic/TTS close) → backend exits in ~0.1 s.

### WebSocket protocol (`ws://127.0.0.1:8765/ws`)

Server → client: every event from `events.py` exactly as `model_dump()` (`state`, `wake`, `transcript.final`,
`assistant.token`, `assistant.final`, `audio.level`, `mic.level`, `tool.call`, `tool.result`, `confirm.request`,
`confirm.resolved`, `notice`, `reminder.due`, `metrics`) plus one `hello` on connect carrying the current state, the
model name, the layout mode, the **last 40 transcript/tool events** (so a HUD that opens or reconnects late shows the
conversation) and any **pending confirmation** (so the buttons reappear after a reconnect).

Client → server: `text` (typed command — goes through exactly the same `handle_text()` as speech: intents, agent,
spoken reply), `image` (base64 + mime → vision turn), `confirm` (`id`, `accepted`), `wake` (click the reactor), `interrupt` (Esc).

Delivery rules in `Hub`: one outbound queue + one writer task per client → strict ordering; if a client falls more than
256 messages behind, only `audio.level`/`mic.level` samples are dropped, never state/transcript/tool events. A
command may run for seconds (a whole spoken turn), so each is spawned as its own task and never blocks the socket.

REST (for scripts/automations): `GET /health`, `POST /chat {"text":..., "speak":true|false}` (speak=false → silent
agent reply as JSON), `POST /confirm`, `POST /interrupt`.

### The scene (`hud/src/scene.ts`) — what pulses and how

| Layer | Driven by | Notes |
|---|---|---|
| Particle sphere (6 000 points, Fibonacci-distributed) | `audio.level` (Jarvis' voice) | GLSL vertex shader displaces each point along its normal by `noise(t)·(idle breath + level)`; fast ripple term + slow drift term. At full level the sphere grows ≈0.5 and stays inside the inner ring. |
| 3 gyroscopic rings + 72 tick marks | state colour, spin speed ∝ level | Rings spin faster while thinking; opacity rises with level. |
| Core disc + halo | level | Opacity 0.2 → 0.7. |
| Waveform ring (128 bars) | `mic.level` while LISTENING/CONFIRMING | History buffer; fades in/out with state. |
| UnrealBloomPass | level | Strength 0.4 → 0.8. |

State colours: cyan idle/speaking, **amber** listening/confirming, white-blue thinking (rings race, sphere breathes
deeper), red on link loss. Levels are smoothed per frame with fast attack / slow release so the ~18 Hz level events
feel continuous at 60 fps. DPR capped at 1.5 (the 2880×1800 panel would otherwise render 4× the pixels);
rendering pauses when the tab/window is hidden.

Calibration (measured live, Kokoro `bm_george`, 268 samples over one reply): `audio.level` p50 **0.36**, p90 0.53,
max 0.66 at ~18 Hz (the `AudioPlayer` already compresses RMS as `min(1,(rms·4)^0.7)` at 40 Hz and de-duplicates
changes < 0.02). HUD gain is 1.1 so normal speech sits around 0.4 and peaks near 0.75 without clipping.

### Layout modes (`ui.mode`, tray menu, or `Ctrl+Shift+M` to cycle)

| Mode | Size / place | Shows | Mouse |
|---|---|---|---|
| `compact` (default) | 340×340, bottom-right | reactor + status word | **click-through** (Win32 `WS_EX_TRANSPARENT`) — it never steals a click |
| `panel` | 520×760, bottom-right, draggable | reactor, transcript, tool chips, confirm card, input, metrics | normal |
| `cinematic` | full screen overlay | everything, big type, centred | normal |

Hotkeys (global, pynput): `Ctrl+Shift+J` show + focus the text box (switches compact → panel), `Ctrl+Shift+H`
show/hide, `Ctrl+Shift+M` cycle mode. In the HUD itself: `/` focuses the input, `Esc` cancels a confirmation or
interrupts speech, clicking the reactor = "Hey Jarvis". Images: drag-drop or paste (Ctrl+V) → thumbnail → type a
question (or just Enter) → vision turn.

### Test results

**Server smoke test** (temp script, fake pipeline, 14 checks): health, index served, silent `/chat`, `hello`
payload, text → `transcript.final`/`state`/`tool.call`/`tool.result`/`assistant.token`/`audio.level`/`assistant.final`
in order, `source:"text"`, image → agent with mime, confirm buttons → `resolve_confirm_external`, wake, interrupt,
`confirm.request` broadcast, history replay to a late client, client cleanup on disconnect, 200 level events
delivered in **4 ms**. **14/14.** Plus a connect/disconnect leak test (4 scenarios, 0 leaked clients).

**Visual test** (demo server replaying scripted turns into the built HUD in a browser, screenshots): idle reactor;
SPEAKING with the sphere rippling; LISTENING amber with the waveform ring; AWAITING CONFIRMATION amber with the card;
transcript bubbles + typewriter streaming + cursor; tool chip `▸ open_app: Spotify ✓`; metrics line.

**Live test** (`jarvis run`, real models, real Kokoro):
- Boot: Qt window up **0.4–0.5 s** after launch, server 0.4 s later, HUD connected ~1 s after that (boot sequence
  plays while models load). Audio stack ready in **29–33 s** warm (68 s the first time after reboot — Kokoro 49 s cold).
  *Phase 5 note: these boots ran the backend inside the Qt process; after D18 the same warm boot is **10.6 s**.*
- Typed "Tell me about the arc reactor in three sentences." → spoken + streamed on the HUD: first token 112–134 ms,
  turn 2.0–2.7 s; the compact reactor pulsed with the real voice.
- Image drop (142 KB JPEG screenshot of the desktop) → "The screen displays a text editor with a project titled
  'Jarvis PC assistant project'… a web browser with the J.A.R.V.I.S. interface, and a terminal…" — correct.
  Timeline: chat model unloaded in 181 ms → vision first token **8.2 s** (model loaded from disk; ~4.6 s once cached)
  → spoken → chat model re-warmed in the background in 5.2 s.
- Desktop capture confirmed: frameless, transparent (desktop visible through the window), always on top, compact mode
  click-through, tray icon present, hotkeys registered.

### Bugs found & fixed in Phase 4

1. **Opaque black window.** Three's `UnrealBloomPass` blur shader writes `alpha = 1` for every pixel, so the
   transparent canvas became solid black and the "transparent" Qt window showed a black square. Fixed with a final
   `ShaderPass` that derives alpha from brightness (`alpha = max(r,g,b)`, colour un-premultiplied) — everything in
   the scene is glow on black, so brightness *is* coverage. A soft threshold (`smoothstep(0.02, 0.14)`) removes the
   faint haze the widest bloom mip spreads over the whole frame. Also set an 8-bit alpha `QSurfaceFormat` on the Qt side.
2. **Over-exposure.** First render was a white blob: 6 000 additive points × bloom 1.1. Rebalanced particle alpha
   (0.85 → 0.28), point size, core opacity, bloom (strength 0.45, threshold 0.25). Then with the *real* voice the sphere
   still washed out — measured the actual level distribution (p50 0.36, not the 0.05–0.3 I had assumed) and set the
   gain from data (1.1). Displacement/size/glow multipliers per level were also cut so a full-scale level stays inside the rings.
3. **Compact window saturated** while the panel looked right: a 340-px canvas has ~7× fewer pixels for the same 6 000
   points. `resize()` now scales point size and alpha with the canvas size.
4. **Confirm card never appeared.** `setState()` hid the card on any non-`confirming` state, but the pipeline speaks
   the question first (`confirm.request` → `speaking` → `confirming`). Now the card only hides on `confirm.resolved`
   or when the turn ends (idle/listening); plus `hello` carries `pending_confirm` for reconnects.
5. **Streaming bubble 7 px wide, one word per line.** `addMsg("assistant", "")` creates no text node, so tokens were
   written into the blinking-cursor `<span>` (width 7 px). Fixed with an explicit `Text` node.
6. **Hub fan-out used one `ensure_future(ws.send_text)` per event** — concurrent sends on one socket with no ordering
   guarantee. Replaced by a per-client queue + single writer (also gives back-pressure with level-sample dropping).
7. **Typed image turns would have taken ~65 s**: `agent.respond(image_b64=…)` switched to the vision model without
   the VRAM swap that `describe_screen` does. Factored the swap into `LLMRouter.vision_swap()` (async context manager)
   and gave the agent a dedicated tool-free `_vision_turn()`; both the tool and the HUD path use it.
8. **Ctrl+C ignored with the HUD** (Qt's C++ loop never returns to Python): `SIG_DFL` + a 250 ms no-op `QTimer`.
9. **Three mystery `wake word (1.00)` hits** in the live log turned out to be reactor clicks from a HUD tab (score 1.0
   is what `wake_external()` passes). Now logged as "wake requested by HUD" so it is never mistaken for a false trigger.

### Known limitations (Phase 4)
- The Qt window loads the HUD once; after `npm run build` use tray → *Reload HUD* (or restart). No hot reload in Qt
  (use `npm run dev` + a browser tab for HUD development; Vite proxies `/ws` to the backend).
- Hotkeys were registered but not exercised by an automated test (I did not inject keystrokes into your session).
- Confirm buttons were tested against the WS→pipeline wiring; the full spoken-question → button path was not driven
  live because every confirm-tier tool has a real side effect (lock/sleep/close…). The button calls the same
  `resolve_confirm_external()` the voice path uses.
- In `cinematic` mode the window covers the whole screen and is *not* click-through — use it for show, `compact` for work.
- ~~Between "Kokoro loaded" and "audio stack ready" there is a 16 s gap on warm boots~~ — investigated and fixed in Phase 5
  (it was two things: a one-off first-synthesis cost, and the in-process Qt slowdown — see §9).

## 9. Phase 5 — Persistence, autostart, two-process launcher (done 2026-10-10, verified live)

### What was built

| Part | Where | What it does |
|---|---|---|
| **Conversation persistence** | `llm/agent.py` (`_restore_history`, `_persist`, `reset`), `memory/store.py` (`messages` table), `config.py` (`memory.resume_hours`) | Every completed turn (typed, spoken or image) is written to SQLite as a `user` + `assistant` row (the assistant row's `meta` holds the model name). On start the agent reloads the last `history_turns × 2` rows **if the newest is younger than `resume_hours` (6 h)**, so Jarvis can answer "what was the codeword I gave you?" after a restart or sign-in. `/reset` in `jarvis chat` writes a `system` marker row and nothing before it is ever restored. Tool calls/results are not persisted (they are large, and stale tool output after a restart would mislead the model). |
| **Start with Windows** | `ui/autostart.py`, CLI `jarvis autostart on|off|status` | Creates `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\JARVIS.lnk` → `.venv\Scripts\pythonw.exe -m jarvis run [--mode X]`, working dir = repo. `pythonw` = no console window; the backend child then also runs windowless (`CREATE_NO_WINDOW`) and logs to file only. No admin rights, no Task Scheduler; delete the shortcut (or `autostart off`) to undo. **Left OFF** — your call. |
| **Two-process launcher** | `ui/launcher.py`, `app.main`, `server/api.py` (`POST /shutdown`), `hub.on_shutdown` | `jarvis run` is now the *window* process; it spawns `python -m jarvis run --no-ui` as the *backend*. Job Object with `KILL_ON_JOB_CLOSE` → the backend dies with the window no matter how the window dies (Task Manager included; verified). 2 s supervision tick restarts a crashed backend (3 s delay, max 3). Tray *Quit* → `POST /shutdown` → clean stop in ~0.1 s, else terminate after 5 s. Window logs to `logs/hud.log`, backend to `logs/jarvis.log` (two processes rotating one file would corrupt it). |
| **pythonw crash fixes** | `server/api.py`, `app.py` (`_spawn`), `logging_setup.py` | Under `pythonw.exe` `sys.stderr is None`; uvicorn's default logging config then raises `ValueError: Unable to configure formatter 'default'` and — because it was a bare `create_task` — the HUD server silently never started (the HUD showed "disconnected" forever while voice still worked). Fixed with `log_config=None`, and every background task now goes through `_spawn()`, which logs a traceback if a task dies. |
| **Boot-time measurement** | `tts/base.py` (warm-up timing log) | `tts warmup synthesis (incl. model load) took N s` in the debug log, so the Kokoro cost is visible per boot. |

### The boot-time investigation (why the backend is a process now)

The Phase 4 log always showed a 13–16 s gap between "Kokoro loaded" and "audio stack ready". Reproducing it in a
standalone script gave a surprise: Kokoro's first synthesis took **10 s** twice in a row… and then 1.7–2.8 s in every
later run (with or without `HF_HUB_OFFLINE=1` — the voice-pack check is 0.24 s and not the culprit). Torch has native
sm_120 kernels, so it is not a PTX JIT either; the first-use cost is almost certainly cuDNN/cuBLAS libraries (~1 GB of
DLLs) being paged in after large model loads evicted them — a one-off per session, not something to engineer around.

That left a second, consistent effect, isolated with five controlled runs (warm caches, same minute, same browser tab
connected throughout):

| Run | Window | Audio stack ready | Kokoro load |
|---|---|---|---|
| `jarvis run --no-ui` | none | **10.8 s** | 7.9 s |
| `jarvis run` (Phase 4: backend thread inside the Qt process), window visible | in-process | **16.3 s** (pythonw: 17.9 s) | 13.3 s |
| same, but `win.show()` skipped (Qt + page loaded, nothing on screen) | in-process, hidden | 9.7 s | 7.9 s |
| `jarvis hud` in another process + `jarvis run --no-ui` | separate process, visible | 9.8 s | 7.9 s |
| `--no-ui` + `timeBeginPeriod(1)` forced via ctypes (no window at all) | none | 12.9 s | 10.0 s |
| in-process visible + `sys.setswitchinterval(0.05)` | in-process | 13.3 s | 10.7 s |

Reading: it is not GPU contention (a visible window in *another* process costs nothing), and it is not Python callbacks
from Qt (none fire at frame rate — PySide caches "no override" per virtual). A **visible** Chromium view requests a 1 ms
Windows timer resolution; on Windows 11 that is per-process, and it alone costs +2–3 s (row 5) because Python's GIL
switch timeout (5 ms) is honoured 3× more often, forcing the four model-loading threads to hand the GIL around far more.
The rest is Qt/Chromium browser-side threads competing in the same process. Moving the backend out of the window
process removes both at once → **10.6 s with the window visible** (and 11.2 s under `pythonw`), from 16–18 s.

### Test results (all on the real stack, 2026-10-10 01:10–01:40)

| Test | Result |
|---|---|
| Persist: typed turn via WS → `messages` table | rows `user` / `assistant ("Done, sir.")`, `meta = ollama/qwen3:8b` |
| Resume: restart, ask "what was the codeword?" | log `resumed conversation: 2 messages, last one 0 min ago`; answer **"Pelican, sir."**; new rows persisted (ids 3–4), again after the 2nd restart (5–6) |
| Restore/reset logic (scratch copy of the DB, incl. WAL) | 6 restored → `reset()` → new agent restores 0 → one fresh turn → restores 2 ✓ |
| `pythonw.exe -m jarvis run --mode compact` (autostart path) | window up 0.33–0.56 s; backend in `python.exe` with no console; audio stack 11.2 s; typed turn answered + persisted; desktop capture shows the compact reactor over the browser HUD |
| `POST /shutdown` | backend exits code 0 in 0.1 s; launcher logs *"backend exited… restarting in 3 s"* and restarts it (supervisor path) |
| Kill the window process (`Stop-Process`) | whole backend tree gone within 4 s (Job Object, including the uv trampoline's grandchild `python.exe`); port 8765 free |
| `autostart on/off/status` | shortcut created with the right target/args/working dir, removed cleanly; status reports correctly |
| Lint | `ruff check` clean on all touched files (project ignores aside) |

### Bugs found & fixed in Phase 5
1. **HUD server never started under `pythonw`** (autostart would have shipped a HUD that says "disconnected" forever) — uvicorn's logging config needs a stderr. `log_config=None`.
2. **Background task failures were invisible** — `create_task` without a done-callback. `_spawn()` logs them.
3. **`/reset` did not survive restarts** — in-memory clear only; now writes a `system` boundary row that `_restore_history` honours.
4. **Model loads 1.6× slower with the HUD** — root-caused and fixed by the process split (above).
5. **Two processes, one log file** — avoided up front: the window process logs to `logs/hud.log`.

### Known limitations (Phase 5)
- The uv venv's `python.exe` is a trampoline that spawns the real interpreter, so the backend is two processes
  (trampoline + interpreter); the Job Object covers both. If a future uv changes this, nothing breaks.
- `/shutdown` is unauthenticated — fine because the server binds to 127.0.0.1 only; do not change `ui.host` to
  `0.0.0.0` without adding a token.
- Persistence stores plain text in `data/jarvis.db`; anything you say to Jarvis is on disk until you delete the DB
  (or set `memory.resume_hours: 0` to stop restoring — rows are still written, so you keep a searchable transcript).
- `jarvis hud` attached to a backend you started yourself does **not** stop that backend when the window quits (by design).

---

## 10. Lighter-model evaluation — Liquid LFM2.5 and NVIDIA Parakeet (2026-10-10)

You asked whether the [Liquid AI LFM2.5 family](https://www.liquid.ai/models) could replace Qwen (brain and eyes) and
NVIDIA Parakeet could replace Whisper turbo (ears), "if they are the same or just a little worse", because they are smaller.
Everything below was measured on this laptop, on the real JARVIS code paths, one model at a time on the GPU. **Result: all three
stay as they are (D20).** The one code change that remains is a small, default-off config feature (§10.5).

### 10.1 Candidates and what they are

| Role | Current | Candidate(s) tried | Notes |
|---|---|---|---|
| Brain (tools + chat) | `qwen3:8b` (Q4_K_M, 5.2 GB disk, 6.19 GB VRAM at 8k ctx) | `lfm2.5:8b` = LFM2.5-8B-A1B (MoE, 8.3B total / 1.5B active, 5.2 GB disk, 5.37 GB VRAM) · `LiquidAI/lfm2.5-2.6b` (1.7 GB, ~1.8 GB VRAM) · `LiquidAI/lfm2.5-1.2b-instruct` (0.73 GB, 0.84 GB VRAM) | The 8B-A1B and 2.6B are *reasoning* models: every answer starts with a chain of thought. Ollama's `think: false` (what we send to Qwen3) is ignored by LFM — the `<think>` text leaked into the reply and hit the token cap, so the first smoke test returned empty replies. Handled three ways (§10.5): let it think (`on`), or pre-fill an empty `<think></think>` block so it skips straight to the answer (`prefill`; works for 2.6B, not for 8B which then stops calling tools). Liquid's recommended sampling (T 0.1–0.2, top-k 50–80, rep 1.05–1.1) was used. |
| Eyes (screenshots) | `qwen2.5vl:7b` (6.0 GB disk, 5.87 GB VRAM) | `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q8_0` (3.5 GB disk, 3.2 GB VRAM) | Not in the Ollama library; the HF GGUF loads fine in Ollama 0.34 (an older "missing tensor output_norm" bug was fixed before 0.14.4). The 450M/1.6B VL variants were skipped — the 3B is already the smallest that could plausibly read a screen. |
| Ears (STT) | faster-whisper `large-v3-turbo` int8_float16, beam 1, English forced, Jarvis vocabulary in `initial_prompt` (1.55 GB disk, **1.23 GB VRAM**) | `nvidia/parakeet-tdt-0.6b-v3` (FastConformer-TDT, 600M params, 25 European languages, punctuation + casing, CC-BY-4.0) via `onnx-asr` 0.12 on onnxruntime-gpu 1.24.4 (CUDA 12 build). fp32 encoder: 2.43 GB disk, **3.6 GB VRAM**; int8 encoder: 0.64 GB disk, 0.42 GB VRAM but its quantised ops run on the CPU | NeMo (NVIDIA's own runtime) is a 2+ GB install and pins its own torch, so the ONNX export was the realistic way in. Ran in a throwaway venv so the project venv stayed untouched; CUDA DLLs were borrowed from the project's torch (`os.add_dll_directory` + `onnxruntime.preload_dlls`). Gotchas found: onnxruntime ≥ 1.24 refuses the Hugging Face blob-cache layout for the 2.3 GB external-data file ("escapes model directory") → download with `local_dir=`; the very first inference ever took **48 s** (PTX JIT, cached by the driver afterwards: 0.75 s on the next process start, then 50–90 ms). onnxruntime-gpu ≥ 1.27 is CUDA 13 — it would clash with torch's cu128 in one process, so the pin would have to be `<1.27`. |

### 10.2 Brain — tool-calling suite (same 22 commands as Phase 3, ×2 runs, plus persona and multi-step checks)

Method: `agent.respond()` end-to-end with the full 47-tool registry, scratch SQLite (your real conversation/memory DB was
never touched), confirm-tier tools auto-declined, side-effect tools (`press_keys`, `open_url`, `web_search`,
`describe_screen`) stubbed. A case passes if the expected tool is called with sensible arguments (judged the same way for every
model). VRAM from Ollama's `/api/ps` at 8k context.

| Model | Thinking | T | Tool suite | Turn p50 / p90 | First visible token p50 | Gen speed | Tokens generated per turn (median) | Replied without calling a tool ("Done, sir.") | Persona answers (words; asked for ≤ 2 sentences) | 2-step commands |
|---|---|---|---|---|---|---|---|---|---|---|
| **qwen3:8b** (current) | off | 0.6 | **44/44** | **626 / 2017 ms** | 313 ms | 78 tok/s | 31 | **0** | 13–40 | 2/2¹ |
| lfm2.5:8b (8B-A1B) | on | 0.2 | 33/44 (75 %) | 2166 / 3883 ms | 835 ms | 234 tok/s | 392 (≈ 450–520 chars of thinking) | 7 | 12–42 | 2/2 |
| lfm2.5-2.6b | on | 0.1 | 36/44 (82 %) | 1904 / 3123 ms | 1055 ms | 174 tok/s | 206 | 4 | 26–128 | 0/2 |
| lfm2.5-2.6b | prefill (no thinking) | 0.1 | 29/44 (66 %) | 1219 / 4382 ms | **151 ms** | 178 tok/s | 139 | 1 | 82–133 | 2/2 |
| lfm2.5-1.2b-instruct | off (non-reasoning) | 0.1 | 18/44 (41 %) | 622 / 822 ms | 164 ms | 341 tok/s | 52 | 1 | 23–56 | 0/2 |

¹ qwen3 did "Open YouTube and search for lo-fi beats" as a single `web_search(engine=youtube)` — correct, just not the two calls
the strict judge first expected.

What the failures look like (this is why "a little worse" does not describe them):
- **Bluffing.** LFM 8B-A1B answered "Done, sir." to *Mute the speakers*, *Pause the music*, *Switch to Discord*, *Minimise
  everything* … with **no tool call** (7 of 44). The 2.6B did it 4 times with thinking on, and on both 2-step commands.
- **Wrong arguments.** 2.6B (prefill): *Mute* → `set_mute(state="off")`; *Dim the screen a little* → `set_brightness(30)`
  without reading the current level; *Pause* → `media_control("stop")`; *Press alt F4* → `window_action("close")`.
- **Confusion.** 1.2B: *Take a screenshot* → `get_volume` + `set_volume`; *What reminders do I have?* → `remember(...)`;
  *Shut down in 10 minutes* → `cancel_shutdown`.
- **Persona.** Thinking-suppressed 2.6B wrote 82–133-word essays where the persona asks for two sentences; none of the Liquid
  models said "sir" reliably (qwen3 does when it fits).
- **Latency.** The Liquid models *generate* 2–4× faster per token, but the reasoning ones spend 140–390 tokens thinking before
  the tool call, so the user waits 2–3× longer; and the fast one (1.2B) is wrong 59 % of the time.

The 8B-A1B's 5.37 GB VRAM is also not meaningfully lighter than qwen3:8b's 6.19 GB. Verdict: **keep `qwen3:8b`.**

For fairness: qwen3:8b is not immune to bluffing. In the post-change smoke test (`jarvis chat`) it answered *"remember that my
test keyword is pelican-7"* with "I have noted that…" **without calling `remember`** (the suite's "Remember that my car is parked
on level 3" passes every time; the first-person "my test keyword" phrasing did not). Pre-existing behaviour, not caused by this
change (the request body is byte-identical) — a persona nudge ("when asked to remember something, always call `remember`") is the
likely fix and is left for you to decide.

### 10.3 Ears — Whisper large-v3-turbo vs Parakeet-TDT-0.6B-v3

Test set (737 clips, 46 min, built by `jarvis_stt_testset.py` in `%TEMP%\jarvis_stt_set`):
- **libri** — 73 real human utterances (LibriSpeech dev-clean sample, 16 kHz read speech) with reference transcripts;
- **cmd** — 44 Jarvis commands ("Hey Jarvis, set the volume to forty percent", "… open VS Code", hot-window follow-ups like
  "Yes." / "Make it louder.") × 5 Kokoro voices (am_adam, af_heart, bm_george, bf_emma, am_michael at 1.3× speed), with the
  0.3/0.4 s of silence the VAD leaves around speech;
- **cmd_n10 / cmd_n5** — the same 220 commands under pink noise at 10 dB and 5 dB SNR (fan / loud room);
- **probe** — 1 s silence, 2 s pink noise, a click train, a breath-like burst: should transcribe to *nothing*.

Whisper ran through the production `FasterWhisperSTT.transcribe()` (so with its no-speech gate and `sanitize()`); Parakeet
through `onnx-asr` with the same `sanitize()` applied. WER uses Whisper's English normaliser (so "forty percent" = "40%").
"Command keyword kept" checks the word(s) the tool call actually depends on (app name, number, "mute", "cancel", …).

| | **Whisper turbo (current)** | Parakeet v3 fp32 (GPU) | Parakeet v3 int8 (CPU ops) |
|---|---|---|---|
| WER libri (real speech) | **3.5 %** | **3.5 %** | 4.6 % |
| WER commands, clean | **1.7 %** | 2.3 % | 4.8 % |
| WER commands, 10 dB noise | **5.1 %** | 11.2 % | 12.2 % |
| WER commands, 5 dB noise | **9.1 %** | 19.2 % | 23.4 % |
| Command keyword kept — clean | 218/220 (99 %) | **219/220 (100 %)** | 217/220 |
| Command keyword kept — 10 dB | **212/220 (96 %)** | 204/220 (93 %) | 205/220 |
| Command keyword kept — 5 dB | **205/220 (93 %)** | 196/220 (89 %) | 185/220 (84 %) |
| Non-speech probes that produced text | 4/4 ✗ ("Closed Captioning by…", "I'll see you next time", …) | **1/4** ("Yeah.") | 1/4 |
| Non-English output on English audio | **0** | 3 ("Yes." → "Я жду.", "Яс.") | — |
| Latency per command (p50 / p90) | 152 / 169 ms | **60 / 65 ms** | 223 / 260 ms |
| Latency, 5–20 s clips (p50 / p90) | 170 / 233 ms | **69 / 91 ms** | 285 / 488 ms |
| Model load | 6.4 s | 4.0 s | 2.6 s |
| VRAM | **1.23 GB** | 3.6 GB | 0.42 GB |
| Disk | **1.55 GB** | 2.43 GB (+ 0.33 GB onnxruntime-gpu) | 0.64 GB (+ 0.33 GB) |
| Vocabulary biasing (`initial_prompt`: "Spotify, Interstellar, …") | yes | no (architecture has no prompt) | no |
| Your own voice / accent | in daily use | **untested** — Parakeet v3 is trained on European languages only; Whisper on 680k h multilingual | |

Per-voice clean WER was within 1 % of each other for every voice (0.5–3.2 % Whisper, 1.1–4.3 % Parakeet); both engines made
the same two real mistakes on the fast am_michael voice ("thinks that is all", "type a low world").

Reading: on clean speech they are the same. Parakeet is genuinely faster (saves ~90 ms of a ~1.5 s voice turn) and much
better behaved on non-speech. But in noise its word error rate roughly **doubles**, the Cyrillic "Yes." is a real hazard for
one-word confirmations in the hot window, it cannot be primed with your app/film names, and — decisive for your premise — the
GPU build needs **3× the VRAM and 1.6× the disk** of Whisper turbo. The small int8 build is lighter but slower *and* worse.
Verdict: **keep Whisper turbo.** Two things learned that are worth doing anyway (not done, your call): tighten Whisper's
no-speech gate (it let all four probes through — the Silero VAD in front of it is what saves us in practice), and if you want
to try Parakeet on *your* voice, the throwaway venv and model are still in `%TEMP%` (§10.6).

### 10.4 Eyes — Qwen2.5-VL-7B vs LFM2.5-VL-3B

Method (`jarvis_vision_bench2.py`): ten synthetic Windows screens with known content (Premiere error dialog with code
`0x8007000E`, Spotify "Blinding Lights", Explorer with five file names, BBC headline, VS Code traceback at line 42, Settings
toggles, Outlook mail, Discord chat, npm ERESOLVE terminal, Spotify + Chrome side by side) and one real capture. Same system
prompt, 300-token cap, T 0.2 and 1280-px JPEG as `describe_screen`. Pass = every ground-truth keyword present. Two rounds:
**standard** (1920×1080 render, 18–22 px text) and **dense** (text scaled to the size it really has after your
2880×1800 @ 175 % screen is shrunk to 1280 px — about 9–11 px). Each case asked twice.

| | **qwen2.5vl:7b** (current) | LFM2.5-VL-3B @ 1280 px | LFM2.5-VL-3B @ 1920 px | LFM2.5-VL-3B @ 2880 px |
|---|---|---|---|---|
| Standard round | 18/20 | **20/20** | — | — |
| **Dense round (realistic)** | **18/20** | 12/20 | 15/20 | 17/20 |
| Image tokens | 1261 | 835 | 2377 | 2377 (capped) |
| Prompt eval, new image | 0.8–0.9 s | 0.3 s | 0.8 s | 0.8 s |
| Warm answer p50 / p90 | 0.9 / 1.4 s | 0.5 / 0.8 s | 0.5 / 1.2 s | 0.5 / 1.2 s |
| VRAM | 5.87 GB | 3.2 GB | 3.2 GB | 3.2 GB |
| Load from disk cache | 3–5 s | 2.7–3.8 s | | |

Qwen's two misses are the same case twice: it said the ZeroDivisionError is "on line 44" (the call site shown in the
traceback; line 42 is the division) — defensible. LFM's dense-round misses are confident misreads: error code **`D800700E`**,
files "Investor_March_2026.pdf" / "tres_final_v1.docx", **"Bluetooth is on"** when the toggle was off, and attributing another
user's message to Rohan. At 1920 px it recovers most of the text but then costs the same prompt-eval time as Qwen, still
misattributes the chat message in some runs, and misses the dependency name in the npm error. Both models called the real
capture "Visual Studio Code"-like with the right tab title (no "Cursor" text was visible; not scored).

The genuinely attractive property of the 3B is VRAM: measured with `qwen3:8b` resident, it loaded **beside** it (both 100 %
GPU) and answered in **0.2 s warm**, versus Qwen2.5-VL's production path of unload chat → describe → re-warm chat (**4.9 s to
the answer, chat back after 8.3 s**; LFM through the same swap path: 3.9 s / 5.7 s). But the full stack already holds
qwen3:8b + Whisper + Kokoro + the HUD's Chromium at ~8.4 of 12.2 GB; adding 3.2 GB leaves ~0.6 GB, and when Ollama's
scheduler judges the margin too small it offloads layers to the CPU, which is the 65-second failure mode fixed in Phase 3.
Not worth a 10–30-point accuracy drop on the thing you'd use it for (reading an error). Verdict: **keep `qwen2.5vl:7b`.**

### 10.5 What changed in the code (small, default-off)

To test the reasoning models fairly, Jarvis needed per-model control over Ollama's thinking flag and temperature:

- `config.py`: `ModelOpts(think: off | on | prefill, temperature)` and `llm.model_opts: {model-string: ModelOpts}`;
  `LLMCfg.opts_for(model)` matches the exact string, then a prefix (so `ollama/lfm2.5` covers every tag).
- `llm/router.py`: `stream()` applies `opts_for(model)`; `prefill` appends an assistant message `<think>\n\n</think>` that
  Ollama continues (so the model skips its reasoning); `on` omits the `think` flag so Ollama returns the chain of thought in
  the separate `thinking` field, which Jarvis ignores.
- `llm/ollama.py`: `chat_stream(think: bool | None)` — `None` omits the key.

With no `model_opts` in `settings.yaml` the request body is byte-identical to before (`think: false`), and the qwen3:8b baseline
above (44/44) ran through the new path. The feature does not depend on any particular model being installed (the Liquid models
were removed again, §10.6). If you ever want to try one anyway, `ollama pull` it first, then:

```yaml
llm:
  primary: ollama/LiquidAI/lfm2.5-2.6b
  model_opts:
    ollama/LiquidAI/lfm2.5-2.6b: { think: prefill, temperature: 0.1 }   # or think: on for better tool accuracy, slower
    ollama/lfm2.5: { think: on, temperature: 0.2 }                      # 8B-A1B: prefill breaks its tool calling
  # vision: ollama/hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q8_0   # and raise VISION_MAX_W in tools/screen.py to 1920
```

Parakeet was **not** wired into `stt/` (it would have needed `stt/parakeet.py`, an `onnx-asr` + `onnxruntime-gpu<1.27`
dependency swap, and a `doctor` check). The recipe that worked, should you want to try it on your own voice one day:
`uv venv <dir> --python 3.12`, `uv pip install "onnx-asr[hub]" "onnxruntime-gpu<1.27" soundfile`, download
`istupakov/parakeet-tdt-0.6b-v3-onnx` with `snapshot_download(local_dir=...)`, then `os.add_dll_directory(<.venv>/Lib/site-packages/torch/lib)`
+ `onnxruntime.preload_dlls(directory=that)` before `onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", path=local_dir, providers=["CUDAExecutionProvider"])`;
expect a one-off ~48 s first inference while the driver JIT-compiles.

### 10.6 Cleanup (done 2026-10-10 12:16, on your request)

Nothing in the repo or in `config/settings.yaml` needed changing — the defaults are what won. Everything the evaluation
had added was removed again; the project was re-verified afterwards (`jarvis doctor` all OK, main venv imports fine):

| Removed | Size |
|---|---|
| Ollama `lfm2.5:8b`, `LiquidAI/lfm2.5-2.6b`, `LiquidAI/lfm2.5-1.2b-instruct`, `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q8_0` | 10.3 GB (`~/.ollama/models` 29.3 → 19.1 GB) |
| Parakeet model dir + its throwaway venv (`%TEMP%\jarvis_parakeet_*`), 46-min STT test set, vision screenshots, benchmark JSON/scratch DB | 3.6 GB |
| Hugging Face cache: shared-blob copy of Parakeet (`hub/blobs`, `models--istupakov--…`) + LibriSpeech sample dataset | 2.4 GB |
| uv cache entries for `onnxruntime-gpu`, `onnx-asr`, `jiwer`, `pyarrow` | 0.4 GB |
| Test scripts tied to those models (`jarvis_llm_bench.py`, `jarvis_lfm_*.py`, `jarvis_parakeet_probe.py`, `jarvis_stt_*.py`, `jarvis_vision_bench2.py`, `jarvis_libri_extract.py`) | — |
| **Total** | **16.2 GB** (C: free 528.9 → 545.1 GB) |

Kept: `qwen3:8b`, `qwen2.5vl:7b`, the Whisper and Kokoro caches, the generic `llm.model_opts` code (§10.5 — works without
those models, default-off), and `qwen3:14b` (9.3 GB, not part of the evaluation; unused since D2 — `ollama rm qwen3:14b` is
the one remaining big saving, your call).

---

## 11. Tuning cheat-sheet (`config/settings.yaml`)

| Want to… | Change |
|---|---|
| Use Claude / GPT / Gemini / Groq | put the key in `.env`, set `llm.primary: anthropic/claude-sonnet-4-5` (any LiteLLM string) |
| Smarter local model | `llm.primary: ollama/qwen3:14b` (slower, 10 GB VRAM) |
| Try a reasoning model that ignores `think: false` (LFM2.5, DeepSeek-R1…) | `llm.model_opts: { ollama/<model>: { think: on \| prefill, temperature: 0.1 } }` — see §10.5 |
| Different voice | `tts.voice: bm_lewis` (or `bm_daniel`, `bm_fable`, American `am_adam`, `af_heart`); `tts.speed: 1.1` |
| Jarvis misses / over-triggers the wake word | `assistant.wake_threshold: 0.4` / `0.6` |
| Longer / shorter follow-up window | `assistant.hot_window_seconds` |
| He cuts you off mid-sentence | `audio.vad_silence_ms: 800` |
| Pick a specific mic / speaker | `audio.input_device` / `output_device` = index or name from `jarvis doctor -v` |
| Disable PowerShell tool | `tools.allow_shell: false` |
| Allow another drive for file search | add to `allowed_roots` in `config/permissions.yaml` |
| Verbose logs | `uv run jarvis run --debug` (file log is always DEBUG: `logs/jarvis.log`) |
| HUD starts in panel / cinematic mode | `ui.mode: panel` (or `cinematic`); cycle live with `Ctrl+Shift+M` |
| Different hotkeys | `ui.hotkey_text_input`, `ui.hotkey_toggle_hud` (pynput syntax, e.g. `<ctrl>+<alt>+j`) |
| HUD too bright / too subtle | `hud/src/scene.ts`: `setSpeechLevel` gain (1.1), bloom strength (`0.4 + level*0.4`), particle `uAlpha` (0.28); then `cd hud; npm run build` + tray → Reload HUD |
| Develop the HUD with hot reload | `uv run jarvis run --no-ui` in one terminal, `cd hud; npm run dev` in another → http://localhost:5173/?bg=1 |
| Start Jarvis at Windows sign-in | `uv run jarvis autostart on` (`--mode panel` optional); `off` removes the shortcut; `status` shows it |
| Forget conversations faster / never resume | `memory.resume_hours: 1` (or `0` = always start fresh); `/reset` in `jarvis chat` also draws a hard line |
| More / less conversation memory per turn | `memory.history_turns: 12` (pairs of messages kept in the prompt and restored on start) |
| Window-side logs (tray, hotkeys, backend restarts) | `logs/hud.log` (backend: `logs/jarvis.log`) |

---

## 12. Git history

```
18ebba9  Model evaluation: Liquid LFM2.5 vs Qwen, Parakeet vs Whisper — kept current models (D20); llm.model_opts; docs
fc6b5f3  STATUS: record Phase 5 commit hash
18e257c  Phase 5: conversation persistence, autostart, two-process launcher (D18), pythonw fixes, docs
9be730b  Phase 4: WebSocket hub + FastAPI, Three.js HUD, PySide6 overlay, vision_swap, docs
d09ed55  Phase 3: full PC control, memory, reminders, vision
a4a2600  Phase 2 complete: native Ollama client, voice fixes, boot order, docs
f0b0d5b  (your commit, titled "Phase 0 and partial Phase 1") — actually Phase 2 voice code + STATUS.md
90b4cc6  Phase 1: LiteLLM router, agent loop, tool registry, apps/files/browser/system tools, chat REPL
272277b  Phase 0: plan, environment, config, doctor
```

Gitignored (local only): `.venv/`, `models/`, `data/`, `logs/`, `.env`.

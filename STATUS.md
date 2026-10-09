# J.A.R.V.I.S. — Build Status & Engineering Log

Voice-first, local-first Windows assistant with full PC control and an Iron Man HUD.
Design: [`PLAN.md`](PLAN.md) · Setup/usage: [`README.md`](README.md) · Root: `C:\Users\Mori\jarvis`

**Last updated: 2026-10-10 (session 2, Phase 4 done).** This file is the single source of truth for *what exists, how it works,
what was measured, and why decisions were made*. It is updated at the end of every phase.

---

## 0. At a glance

| Phase | Scope | State |
|---|---|---|
| 0 | Environment, config, `jarvis doctor` | ✅ done |
| 1 | Text brain: LLM router, agent loop, tool registry, first PC tools, `jarvis chat` | ✅ done |
| 2 | Voice loop: wake word → VAD → Whisper → intents/LLM → Kokoro, barge-in, hot window | ✅ done, verified live |
| 3 | Full PC-control tool suite (47 tools) + confirmations + reminders/memory + vision | ✅ done, 22/22 LLM test |
| 4 | Iron Man HUD: FastAPI/WebSocket hub + Three.js reactor + PySide6 transparent overlay, text & image input | ✅ done, verified live |
| 5 | Persistence, autostart, polish | 🔄 in progress |

Run it today:

```powershell
cd C:\Users\Mori\jarvis
uv run jarvis doctor        # health check
uv run jarvis run           # FULL: voice + HUD overlay (tray icon; Ctrl+Shift+J to type; Ctrl+Shift+H hide/show)
uv run jarvis run --no-ui   # voice only, no window — HUD still available at http://127.0.0.1:8765/
uv run jarvis hud           # window only, attaches to a running backend
uv run jarvis chat          # text REPL with full PC control
uv run jarvis say "Good evening, sir."   # hear the voice
```

First time only: `cd hud; npm install; npm run build` (the HUD is a Vite build; `jarvis doctor` checks for it).

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
| D16 | **HUD talks to the backend only over the local WebSocket** (2026-10-10) | QtWebEngine must own the main thread, so the asyncio backend runs in a thread; rather than share objects across threads, the window is just another WebSocket client. Result: the same HUD runs in a browser tab, `jarvis hud` can attach to a running `jarvis run --no-ui`, and scripts get `/chat` for free. |
| D17 | **Transparency via an alpha-from-brightness post pass instead of dropping bloom** (2026-10-10) | Bloom is what makes it look like the films, but Three's bloom pass writes alpha = 1 everywhere. Since the whole scene is light on black, brightness is a faithful coverage value; one 6-line shader restores transparency and keeps the glow. |
| D15 | **Native Ollama client for local models; LiteLLM only for cloud** (2026-10-09) | Root-caused a recurring ~2.1 s first-token stall: LiteLLM rebuilds its HTTP client whenever its pooled connection has been idle > 5 s (slow on Windows), and imports in 2.5 s. Direct `/api/chat` over one persistent `httpx` connection: **41–100 ms** to first token for text, 230–430 ms for tool calls (Ollama emits the call once its JSON is complete), 75–85 tok/s generation, plus server-side stats (`prompt_eval`, `load_duration`) in the debug log. `llm/ollama.py` → `OllamaClient`; conversions for tool-call arguments (object vs string), tool results and images live in `to_ollama_messages()`. |

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
```

### Source map (`src/jarvis/`)

| File | Role | Key ideas |
|---|---|---|
| `__main__.py` | CLI (`doctor`, `chat`, `run`, `say`) | Forces UTF-8 console (Windows cp1252 crashed on `▸`). |
| `config.py` | Typed settings (pydantic) from `config/settings.yaml` + `permissions.yaml`; env overrides `JARVIS__SECTION__KEY` | `load_persona()` fills `config/persona.md`. |
| `events.py` | Typed events + `EventBus` (async pub/sub) | `state`, `wake`, `transcript.*`, `assistant.*`, `audio.level`, `mic.level`, `tool.*`, `confirm.*`, `metrics`. The HUD consumes these verbatim. |
| `logging_setup.py` | loguru console + rotating file `logs/jarvis.log` | `reapply()` exists because **kokoro calls `logger.remove()` on import** and wipes all sinks. |
| `doctor.py` | Health checks | GPU kernel smoke test (not just "is CUDA visible"). |
| `app.py` | Boot wiring | Loads TTS/STT/wake/VAD in parallel threads; LLM warms in background; keep-alive. |
| `voice.py` | **The state machine** (IDLE→LISTENING→THINKING→SPEAKING→hot window) | Frame loop never blocks on speech; drains stale mic frames so Jarvis never transcribes himself; wake word works in every state (barge-in). |
| `audio/input.py` | `sounddevice` capture → asyncio queue; 400 ms preroll ring | |
| `audio/wakeword.py` | openWakeWord ONNX wrapper; downloads models to `models/openwakeword/` | 2 s cooldown after a hit. |
| `audio/vad.py` | Silero VAD end-pointing | Utterance = 320 ms lead-in + speech + 600 ms tail; blips < 250 ms dropped; max 15 s. |
| `audio/output.py` | Playback thread with cancel + RMS meter | 20 ms blocks; `cancel()` stops within ~20 ms. |
| `stt/` | `faster_whisper.py` (CUDA), `cloud.py` (OpenAI/Groq), `base.py` sanitiser | Strips leading "hey jarvis", drops Whisper hallucinations ("Thank you.", "you", …). |
| `tts/` | `kokoro.py`, `openai_tts.py`, `elevenlabs.py`, `speaker.py` | `Speaker` streams sentence-by-sentence; `clean_for_speech()` fixes "PC", "%", paths, URLs for pronunciation. |
| `llm/router.py` | Model router: `ollama/*` → native `OllamaClient`; anything else → LiteLLM | Strips `<think>` blocks; rescues tool calls emitted as JSON text; per-call timeout; fallback model. |
| `llm/ollama.py` | Ollama lifecycle (`ensure_running`, `/api/ps`) + `OllamaClient` streaming `/api/chat` | Persistent keep-alive connection, `think:false`, `keep_alive`, `num_ctx`, server timing stats (D15). |
| `llm/agent.py` | Tool-calling loop | **Bluff guard**: if the model says "Opening X…" without calling a tool, the text is held back and the model is nudged once to actually call it. |
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
| `server/api.py` | FastAPI app: `/ws`, `/health`, `/chat`, `/confirm`, `/interrupt`, static `hud/dist` | uvicorn runs inside the backend's asyncio loop (`loop="none"`). |
| `ui/window.py` | PySide6 shell: frameless/transparent/on-top `QWebEngineView`, tray, hotkeys, modes | Compact mode is click-through via Win32 `WS_EX_TRANSPARENT`; drag to move in panel mode. |
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

Image width 1024 px would save ~0.5 s per look but loses small text; kept 1280.

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
 jarvis run ─┬─ main thread: Qt event loop → HudWindow (QWebEngineView → http://127.0.0.1:8765/?mode=compact)
             │                                  ▲ tray menu / hotkeys (pynput thread → Qt Signal)
             └─ "jarvis-backend" thread: asyncio loop
                   ├─ uvicorn (FastAPI) ── /ws ──► Hub ── per-client asyncio.Queue ── writer task ──► HUD
                   │                        ◄── {"type":"text"|"image"|"confirm"|"wake"|"interrupt"}
                   ├─ EventBus.on(None, hub._on_event)   # every event → JSON → all clients
                   └─ VoicePipeline / Agent / tools (unchanged from Phases 2–3)
```

QtWebEngine insists on living in the main thread, so with the HUD enabled the whole Phase 2–3 backend runs in a
daemon thread (`app.main`). The two halves talk **only** through the local WebSocket/HTTP server — a clean seam that
also means you can run them separately: `jarvis run --no-ui` + `jarvis hud` (or a browser tab), which is how the
HUD was developed. Quitting from the tray calls `JarvisApp.request_stop()` (cancels all backend tasks → mic/TTS close).

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
- Between "Kokoro loaded" and "audio stack ready" there is a 16 s gap on warm boots (HF Hub voice check + warm-up synth); a Phase 5 item.

## 9. Phase 5 — Persistence, autostart, polish
*(pending)*

---

## 10. Tuning cheat-sheet (`config/settings.yaml`)

| Want to… | Change |
|---|---|
| Use Claude / GPT / Gemini / Groq | put the key in `.env`, set `llm.primary: anthropic/claude-sonnet-4-5` (any LiteLLM string) |
| Smarter local model | `llm.primary: ollama/qwen3:14b` (slower, 10 GB VRAM) |
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

---

## 11. Git history

```
(working tree)  Phase 4: WebSocket hub + FastAPI, Three.js HUD, PySide6 overlay, vision_swap, docs
d09ed55  Phase 3: full PC control, memory, reminders, vision
a4a2600  Phase 2 complete: native Ollama client, voice fixes, boot order, docs
f0b0d5b  (your commit, titled "Phase 0 and partial Phase 1") — actually Phase 2 voice code + STATUS.md
90b4cc6  Phase 1: LiteLLM router, agent loop, tool registry, apps/files/browser/system tools, chat REPL
272277b  Phase 0: plan, environment, config, doctor
```

Gitignored (local only): `.venv/`, `models/`, `data/`, `logs/`, `.env`.

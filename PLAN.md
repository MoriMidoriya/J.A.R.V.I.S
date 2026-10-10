# J.A.R.V.I.S. — Project Plan

> "Just A Rather Very Intelligent System"
> A voice-first, local-first AI assistant with full control over this PC, an Iron-Man-style HUD, and the ability to swap in any LLM (local Ollama, Claude, OpenAI, Gemini, Groq, OpenRouter…) by editing one config line.

Status: **APPROVED — Phase 0 in progress**
Owner: Mori
Location: `C:\Users\Mori\jarvis`
Last updated: 2026-09-10

---

## 0. TL;DR

| Layer | Choice | Why | Cost |
|---|---|---|---|
| Language | Python 3.12 (backend) + TypeScript/Three.js (HUD) | Your preference; best AI ecosystem; web tech gives the best visuals | Free |
| Wake word | **openWakeWord** (`hey_jarvis` pre-trained model) | Free, offline, ONNX, ships a "Hey Jarvis" model. Porcupine's free tier was discontinued June 2026. | Free |
| VAD (speech end-pointing) | **Silero VAD** | Tiny, accurate, industry default | Free |
| STT | **faster-whisper** (`large-v3-turbo` or `distil-large-v3`) on CUDA | Your 12 GB RTX 5070 Ti makes this near-instant; 99 languages. Alt: Moonshine v2 streaming (MIT) | Free |
| LLM (local) | **Ollama** → `qwen3:14b` (Q4_K_M, ~9.3 GB) | Best reasoning + tool calling that fits 12 GB. `qwen3:8b` as the fast option | Free |
| LLM (cloud, optional) | **LiteLLM** router → Anthropic / OpenAI / Gemini / Groq / OpenRouter | One `completion()` API with tool-calling across all providers; switch by config | Your API key |
| Vision | `qwen2.5vl:7b` (Ollama) or Claude/GPT-4o via key | "What's on my screen?", image drag-and-drop | Free / key |
| TTS | **Kokoro-82M** (British male voice, e.g. `bm_george`/`bm_lewis`) on CUDA | Apache 2.0, ~50 ms first-audio on GPU, top of TTS Arena for its size. Alt: ElevenLabs / OpenAI TTS via key | Free |
| Agent core | Custom asyncio agent loop (tools registry, streaming, barge-in) | Full control over latency + sentence-level TTS streaming | — |
| UI shell | **PySide6** transparent, frameless, always-on-top window hosting a **QWebEngineView** | Python-only shell; transparency works on Windows (pywebview does not); web tech inside for the HUD | Free |
| HUD | **Vite + TypeScript + Three.js + Web Audio** | Arc-reactor core, particle sphere that pulses to Jarvis' voice, HUD rings, transcript, waveform | Free |
| Backend ↔ UI | **FastAPI** + WebSocket on `127.0.0.1` | Streams state, transcript, audio amplitude, tool events | Free |
| File search | **Everything** (voidtools) SDK, fallback to Windows Search index | Instant NTFS-indexed search of every file/movie/folder | Free |
| Storage | SQLite (conversations, memory, reminders) + optional sqlite-vec | Zero-config | Free |

Target end-to-end latency for "Hey Jarvis, daddy's home" → spoken reply: **≈1.0–1.5 s** (fast-path greeting skips the LLM; general questions ≈2–3 s with a local model, less with Groq/Claude Haiku).

---

## 1. Your Machine (verified)

| Item | Value | Implication |
|---|---|---|
| Laptop | ASUS ROG Zephyrus G14 (GA403WR) | Great mic array, but a laptop mic → we will add noise-robust VAD thresholds |
| CPU | AMD Ryzen AI 9 HX 370 (12C/24T) + NPU | Plenty for audio pipeline on CPU while GPU does LLM |
| GPU | NVIDIA RTX 5070 Ti Laptop, **12 GB VRAM**, driver 616.92 (Blackwell) | Fits Qwen3-14B Q4 + Whisper + Kokoro simultaneously if we budget (~9.3 + 1.5 + 0.5 GB). Blackwell needs CUDA ≥ 12.8 builds — verify in Phase 0 |
| RAM | 31 GB | Comfortable |
| OS | Windows 11 Home 24H2 | Win32 automation via `pywin32`/`pywinauto`; UWP apps via `Get-StartApps` |
| Python | 3.10.11 (default) and 3.14.5 | **We'll install 3.12 via `uv`** — best wheel coverage for torch/onnx/PySide6 (3.10 EOL Oct 2026; 3.14 too new for some ML wheels) |
| Node / Ollama / uv / Everything | Not installed | Installed in Phase 0 (all via `winget`) |
| Git | 2.54 | ✓ |

---

## 2. Experience Goals (what "done" feels like)

1. **Wake & greet** — You walk in and say *"Hey Jarvis, daddy's home."* The HUD wakes (reactor glows, rings spin up), and within ~1 s Jarvis replies in a calm British voice: *"Good evening, sir. Welcome back. It's 7:42 and you have two reminders pending. What can I do for you?"* — greeting is time-of-day aware and varies each time.
2. **Full PC control by voice** — *"Open Spotify."* *"Play Interstellar."* *"Find that PDF about taxes from last month and open it."* *"Open my Downloads folder."* *"Search YouTube for lo-fi beats."* *"Mute."* *"Set brightness to 40%."* *"Lock the PC in 10 minutes."* *"Close Chrome."* *"Take a screenshot."* *"What's on my screen right now?"*
3. **Conversational** — General Q&A, follow-ups with context, interrupts ("Jarvis, stop") mid-sentence, remembers facts you tell it ("remember my gym is at 6 pm").
4. **Multi-modal input** — Voice (primary), typed text (global hotkey `Ctrl+Shift+J` opens the input), images (drag-drop / paste into HUD, or "look at my screen").
5. **Swap brains** — `config/settings.yaml`: `llm.provider: ollama/qwen3:14b` → `anthropic/claude-sonnet-4-5` → done. Same for STT/TTS.
6. **Iron Man HUD** — Transparent, always-on-top overlay in a corner (or full-screen mode). Arc reactor core pulses with Jarvis' actual audio amplitude; particle sphere breathes when idle, tightens when listening, orbits when thinking, ripples when speaking. Transcript + tool-call log fade in as HUD panels. Cyan/orange Stark palette, scanlines, glitch-in animations.
7. **Safe** — Destructive actions (delete, shutdown, running arbitrary shell) require a spoken/clicked confirmation; an allow-list governs which folders Jarvis may touch.

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                               HUD (PySide6 window)                            │
│  QWebEngineView → Vite/TS/Three.js app  ── WebSocket ──►  ws://127.0.0.1:8765 │
│  • reactor/particles react to `audio.level` events                           │
│  • transcript, tool log, text box, image drop, state (idle/listen/think/speak)│
└──────────────────────────────────────────────────────────────────────────────┘
                                        ▲  events / commands (JSON)
┌───────────────────────────────────────┴──────────────────────────────────────┐
│                          jarvis backend (Python, asyncio)                     │
│                                                                              │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────┐   ┌──────────────┐  │
│  │ Audio Input  │→→ │ Wake Word     │→→ │ VAD + STT    │→→ │  Agent Core  │  │
│  │ sounddevice  │   │ openWakeWord  │   │ Silero +     │   │  (LLM loop)  │  │
│  │ 16 kHz mono  │   │ "hey jarvis"  │   │ faster-whisper│  │              │  │
│  └──────────────┘   └───────────────┘   └──────────────┘   └──────┬───────┘  │
│                                                                   │          │
│         ┌─────────────────────────────────────────────────────────┼───────┐  │
│         │  Intent fast-path (regex/keyword: greetings, stop, mute) │       │  │
│         │  LLM Router (LiteLLM): ollama | anthropic | openai | …   ◄       │  │
│         │  Tool Registry (pydantic-typed tools, permission tiers)  │       │  │
│         │  Memory (SQLite: history, facts, reminders)              │       │  │
│         └─────────────────────────────────────────────────────────┼───────┘  │
│                                                                   ▼          │
│  ┌──────────────┐   ┌───────────────┐   ┌───────────────────────────────┐    │
│  │ TTS (Kokoro) │←← │ Sentence      │←← │ Tools: apps, files, browser,  │    │
│  │ stream → out │   │ chunker       │   │ system, windows, media, vision│    │
│  │ + RMS levels │   │ (barge-in)    │   │ shell (confirm), reminders…   │    │
│  └──────────────┘   └───────────────┘   └───────────────────────────────┘    │
│                                                                              │
│  FastAPI (REST + WS)  •  Event Bus  •  Config (YAML + .env)  •  Logging       │
└──────────────────────────────────────────────────────────────────────────────┘
        ▲                         ▲                          ▲
   Ollama (local)         Anthropic / OpenAI API        Everything.exe (file index)
```

### 3.1 Core data flow — "Hey Jarvis, daddy's home"

1. `AudioInput` streams 16 kHz frames (80 ms) into a ring buffer.
2. `openWakeWord` scores each frame; `hey_jarvis` > 0.5 → emit `wake` → HUD state `listening`, play a short chime (optional).
3. `VAD` collects speech until 600 ms silence (or 12 s max) → hands PCM to `STT`.
4. `faster-whisper` transcribes (~150–300 ms on GPU) → `"daddy's home"`.
5. `IntentRouter` fast-path matches a *greeting* pattern → builds a templated greeting with time-of-day, pending reminders, weather (if enabled) → **skips LLM** for sub-second reply. (Non-matching utterances go to the LLM with tools.)
6. `TTS` synthesizes sentence-by-sentence, starts playback on the first sentence; each 20 ms audio chunk publishes an RMS level → HUD reactor pulses.
7. Session stays "hot" for 8 s after Jarvis finishes so you can continue without re-saying the wake word (configurable).

### 3.2 Agent loop (LLM path)

```
messages = [system(persona + time + facts), …history, user(text | text+image)]
loop:
    stream = litellm.completion(model, messages, tools=registry.schemas(), stream=True)
    for token in stream: → HUD transcript; → sentence chunker → TTS (speak while thinking)
    if tool_calls:
        for call in tool_calls:
            if tool.tier == DANGEROUS and not confirmed: ask user (voice yes/no)  
            result = await registry.run(call)   → HUD tool log
        messages += tool results; continue
    else: break
```

Barge-in: if the wake word or speech is detected while speaking → cancel TTS + LLM stream immediately.

---

## 4. Repository Layout

```
jarvis/
├── PLAN.md                     ← this file
├── README.md                   ← setup & usage (written in Phase 1)
├── pyproject.toml              ← uv-managed project, Python 3.12
├── .env.example                ← API keys template (never commit .env)
├── config/
│   ├── settings.yaml           ← providers, models, voice, hotkeys, HUD prefs
│   ├── persona.md              ← JARVIS system prompt
│   └── permissions.yaml        ← allowed folders, dangerous-tool policy
├── src/jarvis/
│   ├── __main__.py             ← `python -m jarvis` (starts backend + HUD)
│   ├── app.py                  ← wiring, lifecycle, event bus
│   ├── config.py               ← pydantic-settings loader
│   ├── events.py               ← typed events (Wake, Transcript, State, AudioLevel…)
│   ├── audio/
│   │   ├── input.py            ← sounddevice capture, ring buffer, device select
│   │   ├── wakeword.py         ← openWakeWord wrapper
│   │   ├── vad.py              ← Silero VAD end-pointing
│   │   └── output.py           ← playback queue, RMS meter, barge-in cancel
│   ├── stt/
│   │   ├── base.py             ← STT interface
│   │   ├── faster_whisper.py
│   │   ├── moonshine.py        ← optional streaming alt
│   │   └── openai_stt.py       ← cloud alt (whisper-1 / gpt-4o-transcribe)
│   ├── tts/
│   │   ├── base.py
│   │   ├── kokoro.py
│   │   ├── elevenlabs.py
│   │   └── openai_tts.py
│   ├── llm/
│   │   ├── router.py           ← LiteLLM wrapper, provider switching, streaming
│   │   ├── agent.py            ← the loop above
│   │   ├── chunker.py          ← sentence splitter for streaming TTS
│   │   └── intents.py          ← fast-path intents (greeting, stop, mute…)
│   ├── tools/
│   │   ├── registry.py         ← @tool decorator, JSON schemas, permission tiers
│   │   ├── apps.py             ← index Start Menu + UWP, fuzzy launch/close
│   │   ├── files.py            ← Everything/Windows Search, open, reveal, recent
│   │   ├── browser.py          ← open URL, web search, YouTube, site shortcuts
│   │   ├── system.py           ← volume, brightness, lock, sleep, shutdown, wifi, bt
│   │   ├── windows.py          ← focus/minimize/maximize/move/list windows
│   │   ├── media.py            ← play/pause/next, now-playing (Spotify/YouTube)
│   │   ├── screen.py           ← screenshot, screen-to-vision Q&A
│   │   ├── clipboard.py
│   │   ├── shell.py            ← PowerShell exec (DANGEROUS tier)
│   │   ├── reminders.py        ← timers, alarms, reminders (APScheduler)
│   │   ├── memory.py           ← remember/recall facts
│   │   └── web.py              ← fetch/summarize a page, weather, quick facts
│   ├── memory/
│   │   ├── store.py            ← SQLite schema, conversations, facts
│   │   └── embeddings.py       ← optional semantic recall (sqlite-vec)
│   ├── server/
│   │   ├── api.py              ← FastAPI REST (/chat, /state, /config)
│   │   └── ws.py               ← WebSocket hub for HUD
│   └── ui/
│       ├── window.py           ← PySide6 transparent overlay, tray icon, hotkeys
│       └── bridge.py           ← Qt ↔ web bridge (drag-drop, window controls)
├── hud/                        ← Vite + TypeScript + Three.js
│   ├── index.html
│   ├── src/
│   │   ├── main.ts
│   │   ├── ws.ts               ← typed WebSocket client
│   │   ├── scene/              ← reactor, particle sphere, rings, shaders
│   │   ├── panels/             ← transcript, tool log, input, image drop
│   │   └── theme.css           ← Stark palette, scanlines, glitch anims
│   └── package.json
├── models/                     ← downloaded weights (gitignored)
├── scripts/
│   ├── setup.ps1               ← one-shot installer (winget, uv, ollama pulls, models)
│   ├── doctor.py               ← checks GPU/CUDA/audio devices/models
│   └── train_wakeword.md       ← optional: custom wake word recipe
└── tests/
```

---

## 5. Component Details

### 5.1 Audio input
- `sounddevice` (PortAudio) → 16 kHz mono int16, 80 ms blocks, in a dedicated thread; pushes to an `asyncio.Queue`.
- Device selection in `settings.yaml` (`audio.input_device: default`), listed by `scripts/doctor.py`.
- Optional AEC/noise suppression: `noisereduce` or WebRTC APM if the laptop mic is noisy while Jarvis speaks (barge-in needs this; we start with simple "half-duplex" — ignore wake word while speaking unless level exceeds a threshold).

### 5.2 Wake word — openWakeWord
- Pre-trained `hey_jarvis_v0.1.onnx` model (ONNX runtime, CPU, <1 ms/frame).
- Threshold 0.5 default; noise-suppression flag for laptop mic.
- Stretch: train a personal model on your voice with a community trainer (`nibor1896/custom-wakeword-trainer`) for higher recall/lower false alarms.

### 5.3 STT
- **Primary:** `faster-whisper` `large-v3-turbo` int8_float16 on CUDA (~1.6 GB VRAM). Word-level confidence to reject garbage.
- **Fallback (no GPU / issues):** `distil-large-v3` on CPU int8, or Moonshine `medium-streaming` (CPU, MIT).
- **Cloud:** OpenAI `gpt-4o-transcribe`/`whisper-1`, Groq Whisper (very fast, free tier) via key.
- Risk: CTranslate2 wheels must support Blackwell (sm_120). Phase 0 verifies; if not, use `whisper` via PyTorch (torch 2.7+ supports Blackwell) or Moonshine.

### 5.4 LLM router — native Ollama client + LiteLLM for cloud
- One config string per provider: `ollama/qwen3:8b`, `anthropic/claude-sonnet-4-5`, `openai/gpt-4.1-mini`, `gemini/gemini-2.5-flash`, `groq/llama-3.3-70b-versatile`, `openrouter/…`.
- `llm.primary` + `llm.fallback` (e.g. local first, cloud fallback on timeout).
- Per-task routing: `llm.fast` (intent/short answers), `llm.smart` (complex/tool-heavy), `llm.vision`.
- **Revised 2026-10-09:** `ollama/*` models bypass LiteLLM and use our own streaming client (`llm/ollama.py`).
  Measured: LiteLLM added ~2.1 s to the first token whenever its connection had been idle > 5 s (client rebuild is
  slow on Windows) plus a 2.5 s import. Native path: 40–250 ms to first token. LiteLLM remains for cloud providers.
- Local model recommendations for 12 GB:
  - **`qwen3:8b` — default** (Q4_K_M, 5.2 GB; 6.3 GB resident with 8k context). Measured 11/12 commands correct, 0.3–0.6 s per tool round, 75–85 tok/s.
  - `qwen3:14b` — marginally better reasoning (11/12 on the same set), 10 GB resident → leaves no headroom for Whisper + Kokoro + HUD. Opt-in.
  - `qwen2.5vl:7b` — vision (loaded on demand, swapped by Ollama).
  - Thinking mode is disabled per request (`think: false`); Ollama flags `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` are optional extras.
- VRAM budget **as measured with everything resident**: qwen3:8b 6.3 GB + Whisper 1.5 GB + Kokoro 0.4 GB ≈ 8.4 GB of 12.2 GB → comfortable. Decision: `qwen3:8b` default (see STATUS.md D2).

### 5.5 Tools (the "full control over PC" part)

| Tool | Implementation | Tier |
|---|---|---|
| `open_app(name)` | Index `%ProgramData%\Microsoft\Windows\Start Menu`, `%AppData%\…\Start Menu`, `Get-StartApps` (UWP/AppX), Steam/Epic shortcuts; fuzzy match with `rapidfuzz`; launch via `os.startfile` / `explorer.exe shell:AppsFolder\<AUMID>` | Safe |
| `close_app(name)` / `focus_window(title)` / `minimize_all()` / `list_windows()` | `pywinauto`, `pygetwindow`, `psutil` | Safe |
| `search_files(query, type?, folder?, modified_after?)` | Everything SDK (`Everything64.dll` via ctypes) → instant. Fallback: Windows Search via `SELECT System.ItemPathDisplay FROM SYSTEMINDEX WHERE …` (ADO via `pywin32`) | Safe |
| `open_path(path)` / `reveal_in_explorer(path)` | `os.startfile` | Safe (allow-listed roots) |
| `play_media(query)` | Search video/audio extensions, open with default player (or VLC if installed) | Safe |
| `open_url(url)` / `web_search(q, engine)` / `youtube_search(q)` | `webbrowser`, default browser | Safe |
| `set_volume(pct)` / `mute()` / `set_brightness(pct)` | `pycaw`, `screen-brightness-control` | Safe |
| `media_control(play/pause/next/prev)` | virtual media keys (`pynput`) | Safe |
| `lock_pc()` / `sleep_pc()` / `shutdown(delay)` / `restart()` | `rundll32 user32.dll,LockWorkStation`, `shutdown /s /t` | **Confirm** |
| `wifi(on/off)` / `bluetooth(on/off)` / `airplane` | `netsh`, Windows Radio Management API / PowerShell | Confirm |
| `take_screenshot()` / `describe_screen(question)` | `mss` → PNG → vision LLM | Safe |
| `clipboard_get/set()` | `pyperclip` | Safe |
| `type_text(text)` / `press_keys(combo)` | `pynput` / `pyautogui` — lets Jarvis dictate into any app | Safe |
| `run_powershell(cmd)` | subprocess with timeout, output capture | **Dangerous** — always confirm; can be disabled |
| `set_reminder(when, text)` / `set_timer(dur)` / `list_reminders()` | APScheduler + SQLite; spoken when due | Safe |
| `remember(fact)` / `recall(query)` | SQLite facts table (+ optional embeddings) | Safe |
| `get_time()` / `get_weather(city)` / `fetch_page(url)` | `httpx`, Open-Meteo (free, no key), `trafilatura` for readable text | Safe |
| `system_status()` | CPU/GPU/RAM/battery/disk via `psutil`, `nvidia-smi` | Safe |

Permission tiers in `config/permissions.yaml`:
- `safe` — runs immediately.
- `confirm` — Jarvis asks *"Shall I shut down in 10 minutes, sir?"* → yes/no by voice or HUD click.
- `dangerous` — confirm + logged + can be disabled entirely.
- `allowed_roots` — folders Jarvis may open/search (default: user profile, Desktop, Documents, Downloads, Videos, Music, Pictures, plus any drives you add).

### 5.6 Persona (`config/persona.md`)
- British, dry wit, unfailingly polite, concise (1–2 sentences unless asked), addresses you as "sir" (configurable), never breaks character with "As an AI…".
- Injected context every turn: local time/date, day part, battery, active window title, pending reminders, last 3 facts remembered.
- Greeting templates for the fast path: morning/afternoon/evening/late-night variants × 5 each, randomly chosen, with slots for reminders/weather.
- Example: *"Good evening, sir. Welcome home. The forecast is clear, your battery is at 62%, and you asked me to remind you about the gym at six. What can I do for you?"*

### 5.7 TTS — Kokoro
- `kokoro` pip package + `misaki` G2P; voices: `bm_george` (deep British), `bm_lewis`, `bm_daniel`, `bm_fable`. We'll audition and pick; voice blending (e.g. 70% george + 30% lewis) is supported.
- Streams per sentence; audio chunks (24 kHz) → `sounddevice.OutputStream`; RMS per 20 ms → `audio.level` WS event → HUD pulse.
- Optional subtle post-processing: light EQ/reverb "comm-link" effect via `pedalboard` for the movie feel (toggle).
- Cloud alt: ElevenLabs (streaming, has excellent JARVIS-like voices), OpenAI `gpt-4o-mini-tts`.

### 5.8 HUD (Three.js in a transparent Qt window)
- **Window:** PySide6 `QWebEngineView`, `Qt.FramelessWindowHint | WindowStaysOnTopHint | Tool`, `WA_TranslucentBackground`, page background transparent. Modes: *compact* (reactor widget bottom-right, 320×320, click-through when idle), *panel* (reactor + transcript, 480×720), *cinematic* (full-screen overlay). System tray icon with toggle/exit. Global hotkeys via `pynput`/`keyboard`.
- **Scene:**
  - Arc reactor core: layered rings (SVG/Canvas) with additive glow (`UnrealBloomPass`), rotating segment arcs at different speeds.
  - Particle sphere (~6k points, custom GLSL shader): displacement driven by `audio.level`, noise for idle breathing, color by state (cyan idle → amber listening → white-blue thinking → pulsing cyan speaking).
  - Waveform ring around the core showing live mic input when listening.
  - HUD panels: monospaced transcript (typewriter effect), tool-call chips ("▸ open_app: Spotify ✓"), status line (model name, latency ms), text input (appears on hotkey), image drop zone.
  - Effects: scanlines, chromatic aberration on state change, glitch-in on wake, boot sequence on start ("J.A.R.V.I.S. online").
- **Reduced-motion / performance:** 60 fps target; DPR cap; pause rendering when hidden.

> **As built (2026-10-10):** sizes are 340×340 / 520×760; mode cycling added (`Ctrl+Shift+M`). Two deviations worth
> knowing: (1) QtWebEngine must own the main thread, so the backend cannot share it; the window is simply another
> WebSocket client of `127.0.0.1:8765` — nothing is shared (Phase 4 used a backend *thread*, Phase 5 made it a child
> *process* because the in-process Qt window slowed model loading 1.6× — STATUS.md D18); (2) `UnrealBloomPass`
> writes alpha = 1 everywhere, which would make the window an opaque black square, so a final shader pass derives
> alpha from brightness (glow-on-black ⇒ brightness = coverage). Chromatic aberration / glitch effects were left out
> (scanlines + boot sequence are in). Details, measurements and bugs in `STATUS.md` §8.

### 5.9 Text & image input
- Text: HUD input box (hotkey `Ctrl+Shift+J`), plus `/chat` REST for scripts, plus a CLI `jarvis say "..."`.
- Images: drag-drop or paste (Ctrl+V) in HUD → base64 → vision-capable model. "Jarvis, look at my screen — what's the error?" → `describe_screen` tool.

### 5.10 Memory
- SQLite: `conversations`, `messages`, `facts`, `reminders`, `tool_log`.
- Context window management: last N turns + summarized older history.
- Optional semantic recall with `sqlite-vec` + a small local embedding model (`nomic-embed-text` via Ollama).

> **As built (2026-10-10):** tables are `facts`, `reminders`, `messages` (no separate `conversations`/`tool_log` —
> the shell tool keeps its own audit log). Conversation continuity is the simple version: the last `history_turns`
> pairs are replayed from `messages` on start if the newest is under `memory.resume_hours` (6 h) old; a `system` marker
> row from `/reset` is a hard boundary. Summarised older history and semantic recall were **not** built — facts
> ("remember that…") cover the "long-term" need for v1, and the 32k context holds a long session as-is. STATUS.md D19.

---

## 6. Configuration (`config/settings.yaml` sketch)

```yaml
assistant:
  name: Jarvis
  user_title: sir
  wake_word: hey_jarvis          # openWakeWord model name or path
  wake_threshold: 0.5
  hot_window_seconds: 8          # follow-ups without wake word
  language: en

llm:
  primary: ollama/qwen3:14b      # any LiteLLM model string
  fast:    ollama/qwen3:8b
  vision:  ollama/qwen2.5vl:7b
  fallback: anthropic/claude-sonnet-4-5   # used if primary fails/times out
  temperature: 0.6
  max_tokens: 400
  ollama_base_url: http://127.0.0.1:11434

stt:
  engine: faster_whisper         # faster_whisper | moonshine | openai | groq
  model: large-v3-turbo
  device: cuda                   # cuda | cpu
  compute_type: int8_float16

tts:
  engine: kokoro                 # kokoro | elevenlabs | openai
  voice: bm_george
  speed: 1.0
  comm_link_fx: true

audio:
  input_device: default
  output_device: default
  vad_silence_ms: 600

ui:
  mode: compact                  # compact | panel | cinematic
  hotkey_text_input: ctrl+shift+j
  hotkey_toggle_hud: ctrl+shift+h
  start_with_windows: false

tools:
  everything_path: C:\Program Files\Everything\Everything.exe
  allow_shell: false
```

`.env`: `ANTHROPIC_API_KEY=`, `OPENAI_API_KEY=`, `GEMINI_API_KEY=`, `GROQ_API_KEY=`, `ELEVENLABS_API_KEY=`, `OPENROUTER_API_KEY=` — all optional.

---

## 7. Everything You Need to Install (Phase 0 checklist)

All free. `scripts/setup.ps1` will automate this; listed here so you can approve.

| # | What | How | Purpose |
|---|---|---|---|
| 1 | **uv** (Python manager) | `winget install astral-sh.uv` | Creates the Python 3.12 venv, installs deps fast |
| 2 | **Python 3.12** | `uv python install 3.12` | Backend runtime |
| 3 | **Node.js LTS** | `winget install OpenJS.NodeJS.LTS` | Build the HUD (Vite/TS/Three.js). Only needed at dev/build time |
| 4 | **Ollama** | `winget install Ollama.Ollama` then `ollama pull qwen3:14b qwen3:8b qwen2.5vl:7b` | Local LLMs (~20 GB disk total) |
| 5 | **Everything** (voidtools) | `winget install voidtools.Everything` | Instant file search index |
| 6 | **NVIDIA CUDA runtime libs** | pip wheels `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` (no full CUDA toolkit needed) | GPU for faster-whisper / Kokoro |
| 7 | **eSpeak NG** | `winget install eSpeak-NG.eSpeak-NG` | Fallback phonemizer for Kokoro's G2P (`misaki`) |
| 8 | **Microsoft Visual C++ Redist** | usually present; setup checks | onnxruntime / ctranslate2 |
| 9 | Python packages (via `uv sync`) | `fastapi uvicorn websockets pydantic-settings litellm openwakeword onnxruntime silero-vad faster-whisper kokoro soundfile sounddevice numpy torch (cu128) PySide6 PySide6-WebEngine pywin32 pywinauto pygetwindow psutil pycaw comtypes screen-brightness-control rapidfuzz mss pyperclip pynput apscheduler httpx trafilatura rich loguru` | Everything above |
| 10 | Model downloads (auto on first run) | openWakeWord models (~20 MB), Silero VAD (~2 MB), faster-whisper large-v3-turbo (~1.6 GB), Kokoro (~330 MB) | Cached in `models/` |
| 11 | Optional API keys | Anthropic / OpenAI / Gemini / Groq / ElevenLabs / OpenRouter in `.env` | Cloud brains/voices |
| 12 | Microphone permission | Windows Settings → Privacy → Microphone → allow desktop apps | Obviously |

Disk: ~25–30 GB with all local models. Nothing needs admin except winget installs.

---

## 8. Build Phases

Each phase ends with something you can run and test. I'll pause for your feedback at each checkpoint.

### Phase 0 — Environment & scaffolding (½ day)
- Run `setup.ps1`: install tools above, create venv, pull models.
- `scripts/doctor.py`: verifies CUDA visible to torch/ctranslate2 on Blackwell, lists audio devices, checks Ollama, Everything.
- Repo skeleton, `pyproject.toml`, config loading, logging, git init + `.gitignore`.
- **Checkpoint:** `python -m jarvis doctor` all green.

### Phase 1 — Brain: text agent with tools & provider switching (1 day)
- LiteLLM router, streaming, tool-calling loop, persona injection.
- Tool registry + first tools: `open_app`, `search_files`, `open_path`, `open_url`, `web_search`, `get_time`, `system_status`.
- CLI REPL: `python -m jarvis chat` → *"open spotify"* actually opens Spotify.
- Test with `ollama/qwen3:14b` and (if key present) Claude to prove switching.
- **Checkpoint:** typed commands control the PC; swapping `llm.primary` works.

### Phase 2 — Voice loop (1–2 days)
- Audio capture → openWakeWord → Silero VAD → faster-whisper → agent → Kokoro → speaker.
- Fast-path intents: greetings ("daddy's home", "I'm home", "good morning"), "stop/cancel", "thank you", "mute".
- Sentence-streaming TTS, hot window for follow-ups, barge-in v1 (wake word interrupts speech).
- Latency instrumentation (wake→speech-start ms in logs).
- **Checkpoint:** *"Hey Jarvis, daddy's home"* → spoken greeting in ≈1 s; *"open my downloads folder"* works by voice.

### Phase 3 — Full PC control tool suite (1–2 days)
- Remaining tools from §5.5: windows/focus/close, volume/brightness/media keys, lock/sleep/shutdown with confirmation flow, clipboard, type/press keys, reminders/timers, memory, weather, screenshot.
- App index with UWP support + fuzzy matching; Everything integration with Windows Search fallback; media playback ("play Interstellar").
- Permissions config + confirmation dialogue (voice yes/no).
- **Checkpoint:** the whole command list in §2.2 works by voice.

### Phase 4 — HUD (2–3 days)
- PySide6 transparent always-on-top shell + tray icon + global hotkeys + compact/panel/cinematic modes.
- Vite/TS/Three.js scene: reactor, particle sphere driven by real audio levels, state machine colors, waveform ring, transcript/tool panels, boot sequence.
- WebSocket protocol (`state`, `transcript.partial/final`, `assistant.token`, `audio.level`, `tool.call/result`, `confirm.request`).
- Text input + image drag-drop/paste.
- **Checkpoint:** the reactor pulses with Jarvis' voice; you can type or drop an image.

### Phase 5 — Vision, memory, polish (1–2 days)
- `describe_screen` with local VL model / Claude; image Q&A.
- Conversation memory + facts + semantic recall; reminders spoken when due.
- Comm-link voice FX, greeting variety, personality tuning, error handling ("I'm afraid Spotify isn't installed, sir").
- Start-with-Windows option, single-exe packaging option (PyInstaller) for later.
- **Checkpoint:** daily-driver quality.

> **As built (2026-10-10):** vision + facts + reminders landed early (Phase 3); Phase 5 delivered conversation
> persistence across restarts, `jarvis autostart on|off|status`, the two-process launcher (D18) that cut warm boot
> from 16–18 s to ~11 s, and the `pythonw` fixes autostart needed. Not done, deliberately: semantic recall and
> summarised history (see 5.10), comm-link voice FX (would need a DSP pass over Kokoro's output — a Phase 6 candidate),
> PyInstaller packaging (the uv venv + Startup shortcut already gives a one-click start).

### Phase 6 — Stretch (as desired)
- Streaming STT (Moonshine) for live captions while you talk.
- Custom wake word trained on your voice; speaker verification so only *you* can wake it.
- MCP client: plug in any MCP server (Gmail, calendar, Spotify API, Home Assistant) as tools.
- Voice clone (Chatterbox) for a more Bettany-esque voice; multi-language.
- Phone companion via LAN (browser HUD on phone), smart-home control, desktop automation macros ("start my work setup").

Total to a polished v1 (Phases 0–5): roughly 7–10 focused build days.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Blackwell (RTX 50xx) support in CTranslate2/onnxruntime-gpu wheels | Verified in Phase 0; fallbacks: PyTorch Whisper, Moonshine (CPU), CPU int8 faster-whisper (still ~0.5 s) |
| 12 GB VRAM contention (LLM + STT + TTS) | Default local model `qwen3:8b`, Whisper int8; measure; Ollama `keep_alive` tuning |
| Laptop mic false wakes / misses | Threshold tuning, openWakeWord noise suppression, optional custom-trained model |
| Jarvis hears itself while speaking | Half-duplex gating v1; later WebRTC AEC / echo-reference subtraction |
| Local LLM tool-call reliability | Strict pydantic schemas, retries with reformatted prompt, fast-path intents for common commands, cloud fallback |
| Safety of "full control" | Permission tiers, allow-listed roots, confirmation for destructive ops, full tool audit log, shell disabled by default |
| Windows focus/foreground restrictions when launching apps | `SetForegroundWindow` workarounds via `pywinauto`; `AttachThreadInput` |
| Transparent overlay click-through | Toggle `WindowTransparentForInput` when idle; hit-testing on reactor region |

---

## 10. Decisions (approved 2026-09-10)

1. **Default brain:** local-first — `ollama/qwen3:8b` primary and fast (revised from 14b on 2026-10-09 after measuring, see §5.4 and STATUS.md D2); cloud providers available via `.env` keys.
2. **HUD tooling:** Node.js installed; HUD built with Vite + TypeScript + Three.js.
3. **File search:** Everything installed; Windows Search index as fallback.
4. **Voice:** Kokoro British male (local, free); ElevenLabs/OpenAI TTS optional via key.
5. **Shell access:** `run_powershell` enabled behind mandatory confirmation.
6. **Address:** "sir".
7. **Lighter models evaluated and rejected (2026-10-10):** Liquid LFM2.5 (8B-A1B, 2.6B, 1.2B, VL-3B) and NVIDIA
   Parakeet-TDT-0.6B-v3 were benchmarked against Qwen3-8B / Qwen2.5-VL-7B / Whisper turbo on the real stack. None was "the same
   or just a little worse" (tool-call bluffing, doubled WER in noise, misread error codes), and the Parakeet GPU build is heavier
   than Whisper turbo. Defaults unchanged; `llm.model_opts` added for reasoning models. Details: STATUS.md D20 and §10.

---

## 11. Success Criteria for v1

- [ ] "Hey Jarvis, daddy's home" → context-aware spoken greeting in ≤1.5 s, 9/10 attempts from 2 m away.
- [ ] Opens any installed app by name (≥95% of Start Menu apps) by voice.
- [ ] Finds & opens files/movies/folders by fuzzy description in ≤2 s.
- [ ] Browser: open URLs, search web/YouTube.
- [ ] System: volume, brightness, media, lock/sleep/shutdown (with confirm), screenshots.
- [ ] Text and image input work from the HUD; "what's on my screen" works.
- [ ] Switching LLM provider is a one-line config change; works with Ollama, Anthropic, OpenAI.
- [ ] HUD reactor visibly pulses with Jarvis' speech; runs at 60 fps; stays on top; can be hidden with a hotkey.
- [ ] No destructive action without confirmation; all tool calls logged.

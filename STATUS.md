# J.A.R.V.I.S. — status as of 2026-09-10 (paused)

Voice-first, local-first Windows assistant with PC control and an Iron Man HUD.
**Work is paused here.** Full design: [`PLAN.md`](PLAN.md). Project root: `C:\Users\Mori\jarvis`.

---

## Decisions (you approved)

| Topic | Choice |
|---|---|
| Default brain | Local-first: Ollama `qwen3:14b` / `qwen3:8b` |
| HUD | Node + Vite + TypeScript + Three.js (not built yet) |
| Installs | Everything via winget (uv, Node, Ollama, Everything, eSpeak NG) |
| Voice | Kokoro British male (local). Samples in `data/voice_bm_*.wav` |
| Shell | PowerShell allowed, **always behind confirmation** |
| Address | "sir" |

---

## Your machine (verified)

- ASUS ROG Zephyrus G14 — Ryzen AI 9 HX 370, **RTX 5070 Ti 12 GB** (Blackwell `sm_120`), 31 GB RAM, Windows 11
- Python 3.12.14 via uv; torch **2.11.0+cu128** (CUDA kernels work on Blackwell)
- CTranslate2 CUDA OK (`int8_float16`) — Whisper on GPU
- Mic: Realtek array. Speakers: Realtek.
- Node v24.19.0, Ollama 0.33.3, Everything 1.4.1 (service + IPC), eSpeak NG

---

## What's installed (Phase 0)

| Tool | Status |
|---|---|
| uv 0.12.12 | installed |
| Python 3.12 venv (`.venv`) | installed, `uv.lock` committed |
| Node LTS | installed (HUD not started) |
| Ollama | `qwen3:8b` (5.2 GB), `qwen3:14b` (9.3 GB), `qwen2.5vl:7b` (6.0 GB) |
| Everything | installed; SDK dll auto-downloaded to `models/everything/` |
| eSpeak NG | installed |
| HuggingFace caches | Whisper `large-v3-turbo`, Kokoro-82M (first-use downloads done) |
| openWakeWord | `hey_jarvis` ONNX in `models/openwakeword/` |

The two early **background jobs that reported error** (Ollama pull + first `uv sync`) were superseded. Python deps resolved after pinning Windows-only (`openwakeword` → Linux `tflite-runtime` was blocking). Models all present now.

---

## Git

```
90b4cc6  Phase 1: LiteLLM router, agent loop, tool registry, apps/files/browser, chat REPL
272277b  Phase 0: plan, environment, config, doctor
```

**Uncommitted (Phase 2, working tree — pause left it here):**

```
src/jarvis/app.py
src/jarvis/voice.py
src/jarvis/audio/          input, output, vad, wakeword
src/jarvis/stt/            faster-whisper + OpenAI/Groq
src/jarvis/tts/            Kokoro + OpenAI + ElevenLabs + speaker
src/jarvis/llm/intents.py
src/jarvis/llm/chunker.py
```

---

## Phase 0 — environment (done, committed)

- `PLAN.md`, `README.md`, `pyproject.toml`, `.gitignore`, `.env.example`
- Config: `config/settings.yaml`, `config/persona.md`, `config/permissions.yaml`
- Typed settings (`src/jarvis/config.py`), event bus (`events.py`), logging, CLI (`jarvis doctor|chat|run|say`)
- `uv run jarvis doctor` — GPU/CUDA, audio devices, Ollama, Everything, packages
- Result: **All systems nominal** (warnings only for first-use model caches, now filled)

How to run:

```powershell
cd C:\Users\Mori\jarvis
uv run jarvis doctor -v
```

---

## Phase 1 — text brain + PC tools (done, committed)

You can already control the PC from text:

```powershell
uv run jarvis chat
# /model ollama/qwen3:8b
# /model anthropic/claude-sonnet-4-5   (needs ANTHROPIC_API_KEY in .env)
# /tools  /reset  /image path  /quit
```

### What works (live-tested)

| Command | Result |
|---|---|
| "hey jarvis, daddy's home" | Spoken-style greeting (text): "Welcome back, sir…" |
| "what time is it and how's the battery?" | Uses live context (time, 80% charging) |
| "open calculator" / "open notepad" | Actually launched |
| "find the plan document for the jarvis project…" | Found `C:\Users\Mori\jarvis\PLAN.md` |
| "close calculator" | Confirm prompt; decline respected |
| App resolve | Spotify, Chrome, Settings, VS Code, Calculator, Cursor, Notepad — 179 Start Menu apps indexed |
| File search | Everything SDK (instant). Windows Search fallback if IPC missing |
| LLM switch | LiteLLM strings: `ollama/qwen3:14b`, Anthropic/OpenAI/Gemini/Groq/OpenRouter when keys exist |

### Registered tools (12)

`open_app`, `close_app` (confirm), `list_apps`, `search_files`, `open_path`, `reveal_in_explorer`, `open_folder`, `play_media`, `open_url`, `web_search`, `system_status`, `get_datetime`

### Latency (local `qwen3:14b`, thinking **off**, GPU 100%)

| Turn | Time |
|---|---|
| First greeting (cold-ish) | ~6 s |
| Time/battery | ~2.7 s |
| Open calculator (tool) | ~0.8 s |
| File search + path | ~2.1 s |
| Confirm decline | ~1.3 s |

Thinking mode was ~70 s on the first greeting; `think=False` + `keep_alive=30m` fixed that.

### Fixes baked in

- Local models that **announce** "Opening X" without a tool call get **nudged** once to actually call the tool
- Search query relaxation (drop filler words, then OR) so "plan document jarvis project" still hits `PLAN.md`
- Everything: auto-start user-session IPC (service alone is not enough)
- UTF-8 stdout on Windows (Rich `▸` crash on cp1252)

---

## Phase 2 — voice (code written, **not committed**, not end-to-end on the mic yet)

Pipeline designed: **mic → openWakeWord ("hey jarvis") → Silero VAD → faster-whisper → fast-path intent or LLM → Kokoro TTS → speakers**, with barge-in and an 8 s follow-up window.

### Offline voice tests (synthetic speech, no live mic)

| Phrase | Wake | STT | Intent |
|---|---|---|---|
| "Hey Jarvis" | score **1.00** | (stripped empty) | wake-only → "At your service, sir." |
| "Hey Jarvis, daddy's home." | **1.00** | `"Daddy's home."` in **193 ms** | greeting → "Good afternoon, sir. Welcome home…" |
| "Hey Jarvis, open Spotify." | **1.00** | `"open Spotify."` in **141 ms** | goes to LLM |
| "Please open the downloads folder." | 0.00 (correct, no wake) | `"Please open the Downloads folder."` | LLM |

Kokoro (warm, CUDA): **73–114 ms** to first audio per sentence. First load ~28 s.

Voice samples for you to pick a default (`data/`, gitignored):

- `data/voice_bm_george.wav` (current default)
- `data/voice_bm_lewis.wav`
- `data/voice_bm_daniel.wav`
- `data/voice_bm_fable.wav`

### How it *would* run (not started — paused)

```powershell
uv run jarvis          # full voice loop (HUD later)
uv run jarvis --no-ui
uv run jarvis say "Good evening, sir."
```

Boot sequence loads TTS, Whisper, wake word, VAD, and warms the LLM in parallel, then speaks: *"Systems online. Good afternoon, sir."* Then waits for **"Hey Jarvis"**.

---

## Not started (paused before)

- **Phase 3** — volume/brightness/media keys, lock/sleep/shutdown, windows focus, clipboard, type/keys, reminders, memory, weather, screenshot
- **Phase 4** — PySide6 transparent overlay, Three.js HUD, reactor pulse from `audio.level`, WebSocket
- **Phase 5** — vision ("what's on my screen"), facts memory, polish, start-with-Windows
- HUD `hud/` folder does not exist yet

---

## Layout

```
jarvis/
├── PLAN.md                 full architecture
├── STATUS.md               this file
├── config/                 settings, persona, permissions
├── src/jarvis/             Python backend
│   ├── __main__.py         CLI
│   ├── doctor.py / cli_chat.py / app.py / voice.py
│   ├── llm/                router, agent, intents, chunker, context
│   ├── tools/              registry + apps/files/browser/system
│   ├── audio/              mic, wake, vad, playback
│   ├── stt/                faster-whisper, cloud
│   └── tts/                kokoro, openai, elevenlabs, speaker
├── models/                 wake word + Everything SDK (gitignored)
├── data/                   sqlite later; voice samples now (gitignored)
└── .venv/                  Python 3.12
```

Config switch (any LiteLLM string):

```yaml
# config/settings.yaml
llm:
  primary: ollama/qwen3:14b
  # primary: anthropic/claude-sonnet-4-5
tts:
  voice: bm_george          # or bm_lewis / bm_daniel / bm_fable
```

Optional keys in `.env` (copy from `.env.example`): Anthropic, OpenAI, Gemini, Groq, OpenRouter, ElevenLabs.

---

## When you resume

1. Listen to `data/voice_bm_*.wav` and pick a default voice if you want a change.
2. Commit Phase 2 (or discard — files are only on disk).
3. Live mic test: `uv run jarvis --no-ui` — say **"Hey Jarvis, daddy's home"**.
4. Then Phase 3 tools → Phase 4 HUD.

Calculator / Notepad may still be open from Phase 1 tests; close them if they are.

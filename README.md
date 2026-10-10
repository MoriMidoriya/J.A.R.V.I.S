# J.A.R.V.I.S.

Voice-first, local-first AI assistant for Windows with full PC control and an Iron Man style HUD that pulses with his voice.

- **Design:** [`PLAN.md`](PLAN.md)
- **Build log, measurements, decisions, tuning:** [`STATUS.md`](STATUS.md)

> "Hey Jarvis, daddy's home." → *"Good evening, sir. Welcome home. What can I do for you?"*

## What works today

| | |
|---|---|
| Wake word | "Hey Jarvis" (openWakeWord, offline) — works mid-sentence to interrupt him |
| Speech → text | faster-whisper `large-v3-turbo` on your GPU (~150 ms) |
| Brain | Ollama `qwen3:8b` locally by default; any Claude / GPT / Gemini / Groq model via API key |
| Voice | Kokoro-82M, British male (`bm_george`), streamed sentence by sentence |
| PC control (47 tools) | open/close/switch apps and windows, find & open files/folders/movies (Everything index), volume/mute/brightness, play-pause/skip, lock/sleep/shutdown/restart, Wi-Fi/Bluetooth, clipboard, type text & shortcuts, screenshots, PowerShell, open sites, web/YouTube search, system & network status |
| Sees the screen | "what's on my screen?" / "read that error" → local vision model (qwen2.5vl) |
| Remembers | "remember my car is on level 3" → persistent facts, injected into every conversation |
| Reminders & timers | "remind me to call mum at 7 pm", "set a 12 minute timer for the pasta" → spoken when due, survive restarts |
| Weather & web | Open-Meteo weather (no key), reads web pages aloud/summarises |
| Safety | risky actions ask for spoken confirmation; PowerShell only behind confirmation + audit log; file access limited to allow-listed folders |
| Follow-ups | after a reply you have 8 s to speak again without the wake word |
| HUD | transparent always-on-top arc reactor: concentric instrument rings whose bars lengthen with the spectrum of his voice (cyan idle/speaking, amber listening, racing rings while thinking); conversation unfolds beside it while he's engaged, then folds away; side instruments (audio, spectrum, system, clock, globe, latencies) in the bigger modes; tray icon; three layout modes |
| Type or drop images | `Ctrl+Shift+J` opens the text box — typed commands behave exactly like spoken ones; drag-drop / paste an image and ask about it |
| Picks up where you left off | conversations are saved; after a restart (within 6 h) he still knows what you were talking about |
| Starts with Windows | `uv run jarvis autostart on` — launches silently at sign-in, lives in the tray |

## Requirements

- Windows 11, NVIDIA GPU (tested: RTX 5070 Ti Laptop 12 GB; works on CPU too, slower)
- Microphone + speakers
- ~25 GB disk for models

## Install (one time)

```powershell
# 1. Tools (winget)
winget install astral-sh.uv OpenJS.NodeJS.LTS Ollama.Ollama voidtools.Everything eSpeak-NG.eSpeak-NG Git.Git

# 2. Local models
ollama pull qwen3:8b
ollama pull qwen2.5vl:7b          # vision ("what's on my screen?", image drops)

# 3. Python environment (Python 3.12 is downloaded automatically)
cd C:\Users\Mori\jarvis
uv sync

# 4. Build the HUD (Vite + Three.js → hud/dist, served by Jarvis himself)
cd hud; npm install; npm run build; cd ..

# 5. Health check — should end with "All systems nominal, sir."
uv run jarvis doctor -v
```

Whisper, Kokoro and the wake-word models download automatically on first run (a few minutes once).

Optional cloud keys go in `.env` (never committed):

```
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
GROQ_API_KEY=...
```

## Run

```powershell
uv run jarvis run                  # HUD overlay + voice backend (tray icon in the corner). Quit from the tray or Ctrl+C.
uv run jarvis run --no-ui          # backend only; the HUD is still served at http://127.0.0.1:8765/ for a browser tab
uv run jarvis hud                  # just the overlay window, attaching to a backend already running
uv run jarvis chat                 # text REPL with the same brain and tools
uv run jarvis say "Good evening, sir."
uv run jarvis doctor -v            # diagnostics
uv run jarvis run --debug          # verbose console; file logs are always at logs/jarvis.log (backend) + logs/hud.log (window)
uv run jarvis autostart on         # start at Windows sign-in, no console window (off | status)
```

Boot takes ~11 s with warm caches (loading models onto the GPU; up to a minute the first time after a reboot); the HUD
appears within a second and plays its boot sequence meanwhile. Jarvis says *"Systems online"* when ready. If Ollama
isn't running he starts it himself; the language model may take another minute after a cold reboot and he'll tell you
if you ask something before it's ready.

`jarvis run` is two processes: the window, and the voice backend it spawns (`jarvis run --no-ui`). The backend can
never outlive the window (Windows Job Object), is restarted if it crashes, and a crash in the window's Chromium never
takes your assistant down. Conversations are saved to `data/jarvis.db`; if you restart within `memory.resume_hours`
(6) he continues the thread ("what did I just ask you?" works). `/reset` in `jarvis chat` starts a clean slate.

### The HUD

Just say "Hey Jarvis" — nothing covers your screen unless you ask for it.

| | |
|---|---|
| **Compact** (default) | the reactor rests dimmed in the bottom-right corner, click-through — your mouse, keyboard and focus stay with whatever you're doing. On "Hey Jarvis" it brightens, goes amber while listening, and the last lines of the conversation unfold beside it while he answers; ~8 s after the turn (`ui.linger_s`) it folds and dims again |
| **Panel** | a framed console: reactor, audio/spectrum/system instruments, scrolling transcript, text box; drag it anywhere |
| **Cinematic** | full-screen takeover with the desktop dimmed (`ui.cinematic_dim`) and six instruments around a big reactor — only when you switch to it |
| `Ctrl+Shift+J` | show the HUD and focus the text box (switches compact → panel; typed commands are spoken back, like voice) |
| `Ctrl+Shift+H` / `Ctrl+Shift+M` | hide/show · cycle mode (also in the tray menu) |
| Click the reactor | same as saying "Hey Jarvis" (panel / cinematic — compact is click-through) |
| Drop / paste an image | thumbnail appears → ask a question → the vision model answers aloud |
| `Esc` | cancel a pending confirmation, or interrupt him |

The reactor tells you the state: cyan standby/speaking — the 96 bars around the core lengthen with the spectrum of his
voice (lows at the bottom) while the light itself only pulses gently; amber listening — the same bars follow your
voice; white-blue thinking — pulses chase around the ring; red if the link drops. A risky action brings an amber card:
**Confirm / Cancel** in panel/cinematic, "say yes or no" in compact.

## Talk to him

- "Hey Jarvis, daddy's home" / "good morning" → time-of-day greeting
- "Hey Jarvis, open Spotify" / "close Chrome" (asks first) / "open settings"
- "Hey Jarvis, find the tax PDF from last year" → lists matches → "open the first one"
- "Hey Jarvis, play Interstellar" → finds the biggest matching video and plays it
- "Hey Jarvis, open YouTube" / "search for weather in Pune" / "what time is it" / "how's the battery"
- "Hey Jarvis, volume to 40" / "mute" / "dim the screen" / "pause the music" / "switch to Discord" / "show the desktop"
- "Hey Jarvis, what's on my screen?" / "take a screenshot" / "copy that to the clipboard"
- "Hey Jarvis, remind me to call mum at 7 pm" / "set a timer for 12 minutes" / "what reminders do I have?"
- "Hey Jarvis, remember that my passport is in the top drawer" / "where's my passport?"
- "Hey Jarvis, lock the PC" / "shut down in 10 minutes" (asks you to confirm) / "cancel the shutdown"
- "Hey Jarvis, stop" interrupts speech; "thanks" ends the exchange

## Configure

Everything lives in `config/settings.yaml` (model, voice, wake sensitivity, hot window, devices…) and
`config/permissions.yaml` (what needs confirmation, which folders are reachable). Persona text is `config/persona.md`.
See the tuning cheat-sheet in [`STATUS.md`](STATUS.md#11-tuning-cheat-sheet-configsettingsyaml).

Why these models: Qwen3-8B, Whisper large-v3-turbo and Qwen2.5-VL-7B were benchmarked against the smaller Liquid LFM2.5 family
and NVIDIA Parakeet on this machine (tool-calling suite, 737-clip speech set, ground-truth screenshots) and kept — the numbers
and the opt-in recipes are in [`STATUS.md` §10](STATUS.md#10-lighter-model-evaluation--liquid-lfm25-and-nvidia-parakeet-2026-10-10).

Switch to a cloud brain:

```yaml
llm:
  primary: anthropic/claude-sonnet-4-5      # any LiteLLM model string
  fallback: ollama/qwen3:8b                 # used if the primary fails
```

## Project layout

```
config/        settings.yaml, permissions.yaml, persona.md
src/jarvis/    app.py (boot), voice.py (state machine), audio/, stt/, tts/, llm/, tools/, memory/,
               server/ (FastAPI + WebSocket hub), ui/ (PySide6 overlay window, backend launcher, autostart)
hud/           Vite + TypeScript + Three.js HUD → hud/dist (built once; npm run dev for hot reload)
models/        wake-word ONNX + Everything SDK dll (downloaded, gitignored)
data/          app index cache, voice samples, SQLite — facts, reminders, conversation (gitignored)
logs/          jarvis.log (backend) + hud.log (window), rotating, gitignored
```

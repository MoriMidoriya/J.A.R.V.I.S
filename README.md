# J.A.R.V.I.S.

Voice-first, local-first AI assistant for Windows with full PC control and (coming) an Iron Man style HUD.

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
| PC control | open/close apps (Start Menu + Store apps), find & open files/folders/movies (Everything index), open sites, web/YouTube search, system status, time |
| Safety | risky actions ask for spoken confirmation; PowerShell only behind confirmation; file access limited to allow-listed folders |
| Follow-ups | after a reply you have 8 s to speak again without the wake word |

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
ollama pull qwen2.5vl:7b          # vision (Phase 5)

# 3. Python environment (Python 3.12 is downloaded automatically)
cd C:\Users\Mori\jarvis
uv sync

# 4. Health check — should end with "All systems nominal, sir."
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
uv run jarvis run --no-ui          # voice assistant (HUD arrives in Phase 4). Ctrl+C to stop.
uv run jarvis chat                 # text REPL with the same brain and tools
uv run jarvis say "Good evening, sir."
uv run jarvis doctor -v            # diagnostics
uv run jarvis run --debug          # verbose console; file log is always at logs/jarvis.log
```

Boot takes ~30 s (loading models onto the GPU). Jarvis says *"Systems online"* when ready. If Ollama isn't running he
starts it himself; the language model may take another minute after a cold reboot and he'll tell you if you ask
something before it's ready.

## Talk to him

- "Hey Jarvis, daddy's home" / "good morning" → time-of-day greeting
- "Hey Jarvis, open Spotify" / "close Chrome" (asks first) / "open settings"
- "Hey Jarvis, find the tax PDF from last year" → lists matches → "open the first one"
- "Hey Jarvis, play Interstellar" → finds the biggest matching video and plays it
- "Hey Jarvis, open YouTube" / "search for weather in Pune" / "what time is it" / "how's the battery"
- "Hey Jarvis, stop" interrupts speech; "thanks" ends the exchange

## Configure

Everything lives in `config/settings.yaml` (model, voice, wake sensitivity, hot window, devices…) and
`config/permissions.yaml` (what needs confirmation, which folders are reachable). Persona text is `config/persona.md`.
See the tuning cheat-sheet in [`STATUS.md`](STATUS.md#10-tuning-cheat-sheet-configsettingsyaml).

Switch to a cloud brain:

```yaml
llm:
  primary: anthropic/claude-sonnet-4-5      # any LiteLLM model string
  fallback: ollama/qwen3:8b                 # used if the primary fails
```

## Project layout

```
config/        settings.yaml, permissions.yaml, persona.md
src/jarvis/    app.py (boot), voice.py (state machine), audio/, stt/, tts/, llm/, tools/
models/        wake-word ONNX + Everything SDK dll (downloaded, gitignored)
data/          app index cache, voice samples, SQLite (gitignored)
logs/          jarvis.log (rotating, gitignored)
hud/           Vite + Three.js HUD (Phase 4)
```

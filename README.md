# J.A.R.V.I.S.

Voice-first, local-first AI assistant for Windows with full PC control and an Iron Man style HUD.

See `PLAN.md` for the full design. Setup instructions are filled in as phases complete.

## Quick start (dev)

```powershell
uv sync                 # create .venv with Python 3.12 and all deps
uv run jarvis doctor    # check GPU / audio / Ollama / Everything
uv run jarvis chat      # text REPL (Phase 1)
uv run jarvis           # full voice + HUD (Phase 2+)
```

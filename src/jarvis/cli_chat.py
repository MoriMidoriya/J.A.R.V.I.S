"""`jarvis chat` — text REPL with full tool access. Type `/help` for commands."""

from __future__ import annotations

import asyncio
import base64
import sys
import warnings
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from jarvis.config import get_settings
from jarvis.events import ConfirmRequest, Event, EventBus, ToolCall, ToolResult
from jarvis.llm.agent import Agent
from jarvis.llm.router import LLMRouter
from jarvis.logging_setup import setup_logging
from jarvis.tools.all import registry

console = Console()

HELP = """\
/model <name>   switch model (e.g. ollama/qwen3:8b, anthropic/claude-sonnet-4-5)
/image <path>   attach an image to the next message
/tools          list registered tools
/reset          clear conversation history
/quit           exit
"""


async def _confirm(_id: str, prompt: str) -> bool:
    console.print(f"[bold yellow]Jarvis asks:[/] {prompt} [dim](y/n)[/]")
    ans = await asyncio.to_thread(input, "> ")
    return ans.strip().lower() in ("y", "yes", "yeah", "yep", "do it", "go ahead")


async def _run(model_override: str | None) -> None:
    s = get_settings()
    bus = EventBus()
    registry.bus = bus
    registry.confirm_handler = _confirm
    router = LLMRouter(s)
    agent = Agent(s, router, registry, bus)
    if model_override:
        agent.model_override = model_override
    if router.is_local(agent.model_override or s.llm.primary):
        from jarvis.llm import ollama

        if not await asyncio.to_thread(ollama.ensure_running, s.llm.ollama_base_url):
            console.print("[red]Ollama is not reachable and could not be started. Is it installed?[/]")

    def on_event(e: Event) -> None:
        if isinstance(e, ToolCall):
            console.print(f"  [dim cyan]▸ {e.name}({', '.join(f'{k}={v!r}' for k, v in e.args.items())})[/]")
        elif isinstance(e, ToolResult):
            console.print(f"  [dim {'green' if e.ok else 'red'}]{'✓' if e.ok else '✗'} {e.summary}[/]")
        elif isinstance(e, ConfirmRequest):
            pass

    bus.on(None, on_event)

    console.print(f"[bold cyan]J.A.R.V.I.S.[/] online. Model: [green]{agent.model_override or s.llm.primary}[/]. Tools: {len(registry.names())}. /help for commands.")
    pending_image: tuple[str, str] | None = None

    while True:
        try:
            text = await asyncio.to_thread(input, "\nyou> ")
        except (EOFError, KeyboardInterrupt):
            break
        text = text.strip()
        if not text:
            continue
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            if cmd in ("/quit", "/exit", "/q"):
                break
            if cmd == "/help":
                console.print(HELP)
            elif cmd == "/model":
                agent.model_override = arg.strip() or None
                console.print(f"model → {agent.model_override or s.llm.primary}")
            elif cmd == "/tools":
                console.print(", ".join(registry.names()))
            elif cmd == "/reset":
                agent.reset()
                console.print("history cleared")
            elif cmd == "/image":
                p = Path(arg.strip().strip('"'))
                if not p.exists():
                    console.print("[red]no such file[/]")
                else:
                    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
                    pending_image = (base64.b64encode(p.read_bytes()).decode(), mime)
                    console.print(f"attached {p.name}")
            else:
                console.print("unknown command; /help")
            continue

        console.print("[bold cyan]jarvis>[/] ", end="")
        printed = False

        def on_text(delta: str) -> None:
            nonlocal printed
            printed = True
            console.print(delta, end="", highlight=False, soft_wrap=True)

        try:
            img = pending_image
            pending_image = None
            reply = await agent.respond(text, image_b64=img[0] if img else None, image_mime=img[1] if img else "image/png", on_text=on_text)
            if not printed:
                console.print(reply, highlight=False)
            else:
                console.print()
        except Exception as e:  # noqa: BLE001
            console.print(f"\n[red]error:[/] {type(e).__name__}: {e}")


def main(model_override: str | None = None, debug: bool = False) -> None:
    warnings.filterwarnings("ignore", category=SyntaxWarning)
    setup_logging(get_settings(), console_level="DEBUG" if debug else "WARNING")
    try:
        asyncio.run(_run(model_override))
    except KeyboardInterrupt:
        pass
    console.print("\n[dim]Goodbye, sir.[/]")
    sys.exit(0)


if __name__ == "__main__":
    main()

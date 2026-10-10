"""CLI entry point: `jarvis [doctor|chat|run|say]`."""

from __future__ import annotations

import sys
import warnings

import typer

# Windows consoles default to a legacy codepage; make unicode glyphs safe everywhere (also when piped).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, AttributeError):
            pass
warnings.filterwarnings("ignore", category=SyntaxWarning)  # pywinauto ships an old regex literal

app = typer.Typer(help="J.A.R.V.I.S. — voice-first local AI assistant.", no_args_is_help=False, add_completion=False)


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """With no subcommand, launch the full assistant (voice + HUD)."""
    if ctx.invoked_subcommand is None:
        run()


@app.command()
def doctor(verbose: bool = typer.Option(False, "--verbose", "-v", help="List all audio devices")) -> None:
    """Check GPU, audio, Ollama, Everything, models."""
    from jarvis.doctor import run as run_doctor

    raise typer.Exit(run_doctor(verbose=verbose))


@app.command()
def chat(
    model: str | None = typer.Option(None, "--model", "-m", help="Override llm.primary, e.g. anthropic/claude-sonnet-4-5"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Text REPL — talk to Jarvis and let it control the PC, no voice."""
    from jarvis.cli_chat import main as chat_main

    chat_main(model_override=model, debug=debug)


@app.command()
def run(
    no_ui: bool = typer.Option(False, "--no-ui", help="Backend only (voice + HUD server), no window — `jarvis hud` can attach"),
    mode: str | None = typer.Option(None, "--mode", help="HUD mode: compact | panel | cinematic (overrides settings.yaml)"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Start the full assistant: wake word, voice loop, tools, HUD."""
    from jarvis.app import main as app_main

    if mode:
        from jarvis.config import get_settings

        get_settings().ui.mode = mode  # type: ignore[assignment]
    app_main(ui=not no_ui, debug=debug)


@app.command()
def hud(mode: str | None = typer.Option(None, "--mode", help="compact | panel | cinematic (overrides settings.yaml)")) -> None:
    """Open only the HUD window and attach to a backend already started with `jarvis run --no-ui`."""
    from jarvis.config import get_settings
    from jarvis.logging_setup import setup_logging
    from jarvis.ui.window import run_window

    s = get_settings()
    if mode:
        s.ui.mode = mode  # type: ignore[assignment]
    setup_logging(s, name="hud")  # logs/hud.log — never the backend's file (two processes, one rotating file = trouble)
    raise typer.Exit(run_window(s))


@app.command()
def autostart(
    action: str = typer.Argument("status", help="on | off | status"),
    mode: str | None = typer.Option(None, "--mode", help="HUD mode to start in (compact | panel | cinematic)"),
) -> None:
    """Start J.A.R.V.I.S. automatically when you sign in to Windows (Startup-folder shortcut, no admin needed)."""
    from jarvis.ui import autostart as auto

    if action == "on":
        lnk = auto.enable(mode)
        typer.echo(f"Autostart ON — {lnk}\nJarvis will start (no console window) at sign-in; quit him from the tray icon.")
    elif action == "off":
        typer.echo("Autostart OFF — shortcut removed." if auto.disable() else "Autostart was already off.")
    else:
        typer.echo(f"Autostart: {auto.status()}")


@app.command()
def shortcut(
    action: str = typer.Argument("create", help="create | remove | status"),
    desktop: bool = typer.Option(False, "--desktop", help="Also (or only, with `remove`) the Desktop shortcut; default is the Start Menu"),
    mode: str | None = typer.Option(None, "--mode", help="HUD mode to start in (compact | panel | cinematic)"),
) -> None:
    """Start Menu (and optionally Desktop) shortcut that starts J.A.R.V.I.S. without a terminal — search "JARVIS" in the Start Menu."""
    from jarvis.ui import shortcuts as sc

    targets = [sc.start_menu_shortcut()] + ([sc.desktop_shortcut()] if desktop else [])
    if action == "create":
        for lnk in targets:
            sc.create_shortcut(lnk, mode)
            typer.echo(f"Created {lnk}")
        typer.echo("Jarvis starts with no console window; the tray icon is the UI (quit from there, or say “power down”).")
    elif action == "remove":
        for lnk in targets:
            typer.echo(f"Removed {lnk}" if sc.remove_shortcut(lnk) else f"No shortcut at {lnk}")
    else:
        for lnk in [sc.start_menu_shortcut(), sc.desktop_shortcut()]:
            typer.echo(sc.describe_shortcut(lnk))


@app.command()
def say(text: str) -> None:
    """Speak a line with the configured TTS voice (for testing voices)."""
    from jarvis.tts import speak_blocking

    speak_blocking(text)


if __name__ == "__main__":
    app()

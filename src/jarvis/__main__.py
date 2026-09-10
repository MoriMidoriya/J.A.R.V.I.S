"""CLI entry point: `jarvis [doctor|chat|run|say]`."""

from __future__ import annotations

import typer

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
    no_ui: bool = typer.Option(False, "--no-ui", help="Voice only, no HUD window"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Start the full assistant: wake word, voice loop, tools, HUD."""
    from jarvis.app import main as app_main

    app_main(ui=not no_ui, debug=debug)


@app.command()
def say(text: str) -> None:
    """Speak a line with the configured TTS voice (for testing voices)."""
    from jarvis.tts import speak_blocking

    speak_blocking(text)


if __name__ == "__main__":
    app()

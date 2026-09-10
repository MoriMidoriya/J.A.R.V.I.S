"""Speech-to-text engines."""

from __future__ import annotations

from jarvis.config import Settings, get_settings
from jarvis.stt.base import STTEngine


def make_stt(settings: Settings | None = None) -> STTEngine:
    s = settings or get_settings()
    if s.stt.engine == "faster_whisper":
        from jarvis.stt.faster_whisper import FasterWhisperSTT

        return FasterWhisperSTT(model=s.stt.model, device=s.stt.device, compute_type=s.stt.compute_type, beam_size=s.stt.beam_size, language=s.assistant.language)
    if s.stt.engine in ("openai", "groq"):
        from jarvis.stt.cloud import CloudSTT

        return CloudSTT(provider=s.stt.engine, language=s.assistant.language)
    raise ValueError(f"unsupported stt engine {s.stt.engine}")

"""Text-to-speech engines."""

from __future__ import annotations

from jarvis.config import Settings, get_settings
from jarvis.tts.base import TTSEngine


def make_tts(settings: Settings | None = None) -> TTSEngine:
    s = settings or get_settings()
    if s.tts.engine == "kokoro":
        from jarvis.tts.kokoro import KokoroTTS

        return KokoroTTS(voice=s.tts.voice, speed=s.tts.speed)
    if s.tts.engine == "openai":
        from jarvis.tts.openai_tts import OpenAITTS

        return OpenAITTS(voice=s.tts.voice, speed=s.tts.speed)
    if s.tts.engine == "elevenlabs":
        from jarvis.tts.elevenlabs import ElevenLabsTTS

        return ElevenLabsTTS(voice=s.tts.voice)
    raise ValueError(f"unknown tts engine {s.tts.engine}")


def speak_blocking(text: str) -> None:
    """Synthesize and play a line synchronously (used by `jarvis say`)."""
    from jarvis.audio.output import AudioPlayer

    s = get_settings()
    tts = make_tts(s)
    player = AudioPlayer(sample_rate=tts.sample_rate, device=s.audio.output_device)
    for chunk in tts.synthesize(text):
        player.enqueue(chunk)
    import time

    time.sleep(0.1)
    player.wait_idle_blocking(timeout=60)
